#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { constants } from 'node:fs';
import { lstat, mkdir, open } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { backendFor, BridgeError, pathsForBackend, runtimeDirectoryForBackend, privateDirectory, readConfig } from '../bridge/runtime.mjs';
import { inspectRegistry, inspectStrongbox, inspectTunnel, parseBackendArgs, resolveTunnelHelper, strongboxPaths } from './tunnel-diagnostics.mjs';

export async function prepareStrongbox({ userHome = homedir(), uid = process.getuid(), expectedPort,
  inspectPointer = inspectStrongbox } = {}) {
  if (uid === 0) throw new BridgeError('run_as_owner', 'Run this command as your normal user, without an outer sudo.');
  const before = await inspectPointer(userHome, uid);
  if (before.state === 'invalid') throw new BridgeError('pointer_unsafe', 'Inspect the existing Strongbox ownership and permissions; no existing content was changed.');
  const { directories, pointer } = strongboxPaths(userHome);
  for (const directory of directories) {
    try { await mkdir(directory, { mode: 0o700 }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    const details = await lstat(directory);
    if (!details.isDirectory() || details.isSymbolicLink() || details.uid !== uid || (details.mode & 0o022)) {
      throw new BridgeError('pointer_unsafe', 'Strongbox must use ordinary-user directories without writable shared permissions or symbolic links.');
    }
  }
  try {
    const handle = await open(pointer, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await handle.close();
  } catch (error) { if (error.code !== 'EEXIST') throw error; }
  // Existing safe pointers are never written here. They may be root-owned
  // after an earlier helper run; the sudo helper can update them itself.
  const after = await inspectPointer(userHome, uid);
  if (after.state === 'invalid' || (after.port !== null && after.port !== expectedPort)) {
    throw new BridgeError('pointer_changed', 'The Strongbox pointer changed or is unsafe; inspect it before retrying.');
  }
  return after;
}
export function sudoInvocation({ helper, udid, port, userHome = homedir(), node = process.execPath }) {
  return { command: '/usr/bin/sudo',
    args: ['--preserve-env=HOME', '-p', 'iphone use administrator password: ', '--', node,
      helper.helperPath, '--udid', udid, '--tunnel-registry-port', String(port), '--reconnect-retries', '3'],
    options: { cwd: runtimeDirectoryForBackend(helper.backend), env: { HOME: userHome, PATH: '/usr/bin:/bin:/usr/sbin:/sbin', LANG: 'en_US.UTF-8' },
      stdio: ['inherit', 'pipe', 'pipe'] },
  };
}
export async function launchForeground({ helper, udid, port, userHome, directory = runtimeDirectoryForBackend(helper.backend),
  paths = pathsForBackend(process.env, helper.backend),
  spawnProcess = spawn, output = (text) => process.stdout.write(text), errorOutput = (text) => process.stderr.write(text),
  poll = async () => inspectRegistry(udid, await inspectStrongbox(userHome)) }) {
  const invocation = sudoInvocation({ helper, udid, port, userHome });
  invocation.options.cwd = directory;
  const logPath = join(dirname(paths.log), 'tunnel.log');
  await privateDirectory(dirname(logPath));
  const log = await open(logPath, constants.O_WRONLY | constants.O_APPEND | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
  const details = await log.stat();
  if (!details.isFile() || details.uid !== process.getuid()) { await log.close(); throw new BridgeError('unsafe_log', 'The private tunnel log must be a file owned by the current user.'); }
  await log.chmod(0o600);
  output('Starting the foreground tunnel. Enter the administrator password in this terminal when prompted. Keep this terminal open and USB unplugged.\n');
  output(`Private helper log: ${logPath}\n`);
  let child;
  try { child = spawnProcess(invocation.command, invocation.args, invocation.options); }
  catch (error) { await log.close(); throw error; }
  // Upstream logs contain every discovered device identifier. Consume them
  // privately and report only readiness, a generic failure, and sudo's prompt.
  let warned = false; let promptBuffer = ''; let lastState; let polling = false; let writes = Promise.resolve();
  const record = (chunk) => { writes = writes.then(() => log.write(chunk)).catch(() => {}); };
  child.stdout?.on('data', record);
  child.stderr?.on('data', (chunk) => {
    record(chunk);
    promptBuffer = (promptBuffer + String(chunk)).slice(-4096);
    const prompt = 'iphone use administrator password: ';
    if (promptBuffer.includes(prompt)) { errorOutput(prompt); promptBuffer = ''; }
    if (!warned && /(?:error|failed|sorry|denied)/i.test(String(chunk))) {
      warned = true;
      errorOutput('The foreground helper reported a problem. Check the registry result below; no device identifiers are shown.\n');
    }
  });
  const pollStatus = async () => {
    if (polling) return;
    polling = true;
    try {
      const registry = await poll();
      if (registry.state !== lastState && registry.catalog_ready) {
        lastState = registry.state;
        output(registry.state === 'Network'
          ? 'Network tunnel catalog is ready. Keep this terminal open; phone-session verification is still required.\n'
          : 'The tunnel is not confirmed as Network. Do not claim wireless readiness; inspect with --check.\n');
      }
    } catch { /* The bounded diagnostics command can explain an unavailable registry. */ }
    finally { polling = false; }
  };
  const timer = setInterval(() => { void pollStatus(); }, 1500);
  const handlers = new Map(['SIGINT', 'SIGTERM', 'SIGHUP'].map((signal) => [signal, () => {
    try { child.kill(signal); } catch { /* Forward only to this actual child, never a remembered PID. */ }
  }]));
  for (const [signal, handler] of handlers) process.on(signal, handler);
  try {
    return await new Promise((done, reject) => {
      child.once('error', () => reject(new BridgeError('sudo_start_failed', 'Could not start the foreground helper. Use a normal-user interactive terminal with sudo support.')));
      child.once('close', (code, signal) => done({ ok: code === 0, started: true, exit_code: code, signal, phone_session_created: false }));
    });
  } finally {
    clearInterval(timer);
    for (const [signal, handler] of handlers) process.removeListener(signal, handler);
    await writes;
    await log.close();
  }
}
export async function startTunnel(options = {}) {
  const { backend = backendFor(), checkOnly = false, paths = pathsForBackend(process.env, backend), userHome = homedir(), directory = runtimeDirectoryForBackend(backend),
    uid = process.getuid(), interactive = Boolean(process.stdin.isTTY && process.stderr.isTTY), launch = launchForeground } = options;
  const originalConfig = await readConfig(paths);
  const report = await inspectTunnel(options);
  if (checkOnly) return report;
  // Reuse is a no-op even if USB presence means full wireless acceptance is
  // still outstanding. Never replace an existing user's service or pointer.
  if (report.reusable) return { ...report, reused: true, started: false };
  if (!report.can_start) throw new BridgeError(report.blockers[0] || 'tunnel_not_ready', report.next_steps[0] || 'Inspect tunnel prerequisites with --check.');
  if (!interactive) throw new BridgeError('interactive_terminal_required', 'Run node scripts/start-tunnel.mjs in a normal-user terminal so sudo can prompt the owner.');
  await prepareStrongbox({ userHome, uid, expectedPort: report.tunnel_registry.port, inspectPointer: options.inspectPointer });
  // Check again after preparation: an existing service or a newly attached
  // USB cable must block this attempt before sudo starts the upstream helper.
  const current = await inspectTunnel(options);
  if (current.reusable) return { ...current, reused: true, started: false };
  if (!current.can_start || current.tunnel_registry.port !== report.tunnel_registry.port) {
    throw new BridgeError('prerequisites_changed', 'Tunnel prerequisites changed. Rerun --check; nothing was started.');
  }
  const config = await readConfig(paths);
  if (config.udid !== originalConfig.udid || config.wda_transport !== originalConfig.wda_transport ||
      config.use_preinstalled_wda !== originalConfig.use_preinstalled_wda) {
    throw new BridgeError('configuration_changed', 'Device configuration changed during preparation. Rerun --check; nothing was started.');
  }
  const helper = await resolveTunnelHelper(directory, backend);
  return launch({ helper, udid: config.udid, port: current.tunnel_registry.port, userHome, directory, paths });
}
export async function main(args = process.argv.slice(2)) {
  const json = args.includes('--json');
  try {
    const parsed = parseBackendArgs(args, ['--check', '--json']);
    if (json && !parsed.flags.includes('--check')) {
      throw new BridgeError('usage', 'Usage: node scripts/start-tunnel.mjs [--backend direct-wda|appium] [--check [--json]]');
    }
    const result = await startTunnel({ backend: parsed.backend, checkOnly: parsed.flags.includes('--check') });
    if (json) process.stdout.write(JSON.stringify(result) + '\n');
    else if (result.read_only) {
      const usbAttached = result.usb.connected === true ? 'yes' : result.usb.connected === false ? 'no' : 'unknown';
      process.stdout.write(`Registry: ${result.tunnel_registry.state}; USB attached: ${usbAttached}; device discovery: ${result.usb.state}.\n`);
      for (const step of result.next_steps) process.stdout.write(step + '\n');
      if (result.reused) process.stdout.write('Reusing the existing Network tunnel. No service or pointer was changed.\n');
    } else process.stdout.write(result.ok ? 'Foreground tunnel helper exited.\n' : 'Foreground helper exited unsuccessfully; run --check for prerequisites.\n');
    if (result.ok === false) process.exitCode = 1;
  } catch (error) {
    const result = { ok: false, error: { code: error.code || 'tunnel_setup_failed', message: error instanceof BridgeError ? error.message : 'Cannot prepare the foreground tunnel; inspect configuration and permissions with --check.' } };
    if (json) process.stdout.write(JSON.stringify(result) + '\n');
    else process.stderr.write(result.error.message + '\n');
    process.exitCode = 1;
  }
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) await main();

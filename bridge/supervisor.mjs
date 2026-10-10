import { spawn } from 'node:child_process';
import { timingSafeEqual } from 'node:crypto';
import { chmod, open, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  bridgeDirectory, childEnvironment, delay, pathsFor, prepareState,
  processIdentity, readConfig, readJson, writePrivateJson,
} from './runtime.mjs';

// Exported so process lifetime can be exercised with a harmless fake child.
export async function supervise({ paths, config, token, instance, runnerPath = join(bridgeDirectory, 'bridge-runner.cjs') }) {
  if (!/^[a-f0-9]{64}$/.test(token) || !/^[a-f0-9]{32}$/.test(instance)) throw new Error('Invalid supervisor identity');
  await prepareState(paths);
  const log = await open(paths.log, 'a', 0o600);
  await log.chmod(0o600);
  let child;
  let stopping = false;
  let closed = false;
  let exitObserved = false;
  let finishClosed;
  const guardians = new Map();
  const guardianRegistrations = new Set();
  const whenClosed = new Promise((r) => { finishClosed = r; });
  const server = createServer((socket) => {
    socket.setTimeout(1_000, () => socket.destroy());
    let text = '';
    socket.on('error', () => {});
    socket.on('data', (data) => {
      text += data.toString();
      if (text.length > 1_024) return socket.destroy();
      if (!text.includes('\n')) return;
      let request;
      try { request = JSON.parse(text.split('\n')[0]); } catch { return socket.destroy(); }
      if (typeof request.token !== 'string' || !/^[a-f0-9]{64}$/.test(request.token) ||
          !timingSafeEqual(Buffer.from(request.token), Buffer.from(token))) return socket.destroy();
      if (!['status', 'stop'].includes(request.action)) return socket.destroy();
      socket.end(JSON.stringify({ pid: process.pid, instance, port: config.port,
        child_pid: child?.pid ?? null, running: Boolean(child?.pid && !exitObserved), stopping }) + '\n');
      if (request.action === 'stop') void stop();
    });
  });
  // The control socket only accepts an authentication token from a 0600 file
  // inside a 0700 directory. The CLI never signals a remembered numeric PID.
  await new Promise((resolveListen, reject) => {
    server.once('error', reject);
    server.listen(paths.socket, resolveListen);
  });
  await chmod(paths.socket, 0o600);
  const owner = {
    format_version: 1, pid: process.pid, identity: await processIdentity(process.pid),
    script: join(bridgeDirectory, 'supervisor.mjs'), token, instance, socket: paths.socket,
    port: config.port, started_at: new Date().toISOString(),
  };
  async function cleanup() {
    if (closed) return;
    closed = true;
    server.close();
    const current = await readJson(paths.descriptor).catch(() => null);
    if (current?.instance === instance && current?.token === token) {
      await rm(paths.descriptor, { force: true });
      await rm(paths.socket, { force: true });
    }
    await log.close();
    finishClosed();
  }
  function signalChild(signal) {
    if (!child?.pid || exitObserved || child.exitCode !== null || child.signalCode !== null) return;
    try { child.kill(signal); } catch (e) { if (e.code !== 'ESRCH') throw e; }
  }
  async function stop() {
    if (stopping) return whenClosed;
    stopping = true;
    signalChild('SIGTERM');
    const deadline = Date.now() + 8_000;
    while (child?.pid && !exitObserved && Date.now() < deadline) await delay(50);
    signalChild('SIGKILL');
    const forceDeadline = Date.now() + 2_000;
    while (child?.pid && !exitObserved && Date.now() < forceDeadline) await delay(50);
    if (!exitObserved && child?.pid) {
      // Preserve the control endpoint and ownership evidence when exit cannot
      // be confirmed. It must not be reported as safely stopped.
      stopping = false;
      return;
    }
    await Promise.all(guardianRegistrations);
    const guardianDeadline = Date.now() + 3_000;
    while (guardians.size && Date.now() < guardianDeadline) {
      for (const [pid, identity] of guardians) {
        if (await processIdentity(pid) !== identity) guardians.delete(pid);
      }
      if (guardians.size) await delay(50);
    }
    if (guardians.size) {
      stopping = false;
      return; // Preserve evidence rather than claim descendant cleanup.
    }
    await cleanup();
  }
  const handlers = ['SIGTERM', 'SIGINT', 'SIGHUP'].map((signal) => {
    const handler = () => { void stop(); };
    process.on(signal, handler);
    return [signal, handler];
  });
  try {
    await writePrivateJson(paths.descriptor, owner);
    if (stopping || closed) {
      // A termination signal may arrive while the ownership file is being
      // published. Never launch Appium after that shutdown request.
      await rm(paths.descriptor, { force: true });
      await cleanup();
      return { owner, stop, whenClosed, child };
    }
    child = spawn(process.execPath, [runnerPath,
      '--address', '127.0.0.1', '--port', String(config.port),
      '--use-drivers', 'xcuitest', '--log-level', 'warn',
    ], { cwd: bridgeDirectory, env: childEnvironment(config), detached: true,
      stdio: ['ignore', log.fd, log.fd, 'ipc'] });
    child.on('message', (message) => {
      if (message?.type !== 'vico-xcode-guardian' || !Number.isSafeInteger(message.pid) || message.pid < 2) return;
      const registration = processIdentity(message.pid).then((identity) => {
        if (identity?.includes(join(bridgeDirectory, 'xcodebuild-guardian.cjs'))) guardians.set(message.pid, identity);
      });
      guardianRegistrations.add(registration);
      registration.finally(() => guardianRegistrations.delete(registration));
    });
    child.once('exit', () => { exitObserved = true; if (!stopping) void stop(); });
    child.once('error', () => { exitObserved = true; if (!stopping) void stop(); });
    if (exitObserved) await cleanup();
  } catch (e) { await stop(); throw e; }
  whenClosed.finally(() => { for (const [signal, handler] of handlers) process.removeListener(signal, handler); });
  return { owner, stop, whenClosed, child };
}

async function main() {
  const [home, token, instance] = process.argv.slice(2);
  if (!home || !token || !instance) throw new Error('Supervisor requires a private state directory and ownership identity');
  const paths = pathsFor({ VICO_IPHONE_HOME: home });
  const config = await readConfig(paths);
  const supervisor = await supervise({ paths, config, token, instance });
  await supervisor.whenClosed;
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch(() => { process.stderr.write('Standalone bridge supervisor failed.\n'); process.exitCode = 1; });
}

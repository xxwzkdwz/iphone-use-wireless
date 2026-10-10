#!/usr/bin/env node
import { execFile } from 'node:child_process';
import { constants } from 'node:fs';
import { access, lstat, open, readFile } from 'node:fs/promises';
import { get } from 'node:http';
import { isIP } from 'node:net';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { backendFor, childEnvironment, pathsForBackend, runtimeDirectoryForBackend, readConfig, readJson } from '../bridge/runtime.mjs';

const run = promisify(execFile);
export const DEFAULT_REGISTRY_PORT = 42314;
const remotePackage = 'node_modules/appium-ios-remotexpc';
const legacyRemotePackage = 'node_modules/appium-xcuitest-driver/node_modules/appium-ios-remotexpc';
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
export function strongboxPaths(userHome = homedir()) {
  const home = resolve(userHome);
  const directories = [home, join(home, 'Library'), join(home, 'Library/Application Support'),
    join(home, 'Library/Application Support/appium-xcuitest-driver-nodejs'),
    join(home, 'Library/Application Support/appium-xcuitest-driver-nodejs/strongbox')];
  return { directories, directory: directories.at(-1), pointer: join(directories.at(-1), 'tunnelRegistryPort') };
}
export async function inspectStrongbox(userHome = homedir(), uid = process.getuid(), { openFile = open } = {}) {
  const { directories, pointer } = strongboxPaths(userHome);
  try {
    for (const directory of directories) {
      const details = await lstat(directory);
      if (!details.isDirectory() || details.isSymbolicLink() || details.uid !== uid || (details.mode & 0o022)) {
        return { state: 'invalid', reason: 'unsafe_directory', port: null };
      }
    }
    const handle = await openFile(pointer, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const details = await handle.stat();
      if (!details.isFile() || ![uid, 0].includes(details.uid) || (details.mode & 0o022) || details.size > 16) {
        return { state: 'invalid', reason: 'unsafe_pointer', port: null };
      }
      await access(pointer, constants.R_OK);
      const writable = details.uid === uid && await access(pointer, constants.W_OK).then(() => true, () => false);
      const bytes = Buffer.alloc(17);
      const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
      const value = bytes.subarray(0, bytesRead).toString('utf8').trim();
      if (!value) return { state: 'empty', port: null, writable };
      if (!/^\d{1,5}$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
        return { state: 'invalid', reason: 'invalid_port', port: null };
      }
      return { state: 'valid', port: Number(value), writable };
    } finally { await handle.close(); }
  } catch (error) {
    return error.code === 'ENOENT' ? { state: 'missing', port: null }
      : { state: 'invalid', reason: 'unreadable_or_unsafe_pointer', port: null };
  }
}
export async function resolveTunnelHelper(directory = runtimeDirectoryForBackend(), backend = backendFor()) {
  runtimeDirectoryForBackend(backend);
  const root = resolve(directory);
  const packagePath = backend === 'appium' ? legacyRemotePackage : remotePackage;
  const lock = await readJson(join(root, 'package-lock.json'));
  const remoteRoot = join(root, packagePath);
  const pkg = await readJson(join(remoteRoot, 'package.json'));
  const expected = lock?.packages?.[packagePath]?.version;
  if (expected !== '5.18.9' || pkg?.name !== 'appium-ios-remotexpc' || pkg.version !== expected) {
    throw new Error('Install the pinned runtime dependencies for the selected backend in this plugin copy first.');
  }
  if (backend === 'appium') {
    const driver = await readJson(join(root, 'node_modules/appium-xcuitest-driver/package.json'));
    if (driver?.version !== lock?.packages?.['node_modules/appium-xcuitest-driver']?.version || driver?.version !== '12.11.0') {
      throw new Error('Install the pinned legacy Appium runtime first.');
    }
  }
  const helperPath = join(remoteRoot, 'scripts/tunnel-creation.mjs');
  const details = await lstat(helperPath);
  if (!details.isFile() || details.isSymbolicLink()) throw new Error('The pinned foreground helper is unavailable.');
  return { helperPath, entryPath: join(remoteRoot, 'build/src/index.js'), version: pkg.version, backend };
}
export function readRegistry(port, { timeout = 2_000, maxBytes = 256 * 1024 } = {}) {
  return new Promise((done) => {
    let settled = false;
    const finish = (value) => { if (!settled) { settled = true; clearTimeout(timer); done(value); } };
    const request = get({ hostname: '127.0.0.1', port, path: '/remotexpc/tunnels', agent: false }, (response) => {
      const chunks = []; let size = 0;
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > maxBytes) { finish({ kind: 'invalid', reason: 'oversized_response' }); request.destroy(); }
        else chunks.push(chunk);
      });
      response.on('error', () => finish({ kind: 'unreachable', reason: 'incomplete_response' }));
      response.on('end', () => {
        if (response.statusCode !== 200) return finish({ kind: 'invalid', reason: 'unexpected_http_status' });
        try {
          const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (!isObject(value) || !isObject(value.tunnels)) throw new Error();
          finish({ kind: 'registry', value });
        } catch { finish({ kind: 'invalid', reason: 'unexpected_registry_response' }); }
      });
    });
    const timer = setTimeout(() => { finish({ kind: 'unreachable', reason: 'timeout' }); request.destroy(); }, timeout);
    request.on('error', (error) => finish(error.code === 'ECONNREFUSED'
      ? { kind: 'absent', reason: 'connection_refused' } : { kind: 'unreachable', reason: 'connection_failed' }));
  });
}
export async function inspectRegistry(udid, pointer, request = readRegistry) {
  if (pointer.state === 'invalid') return { state: 'invalid', reason: pointer.reason, checked: false, port: null, port_available: false };
  const port = pointer.port ?? DEFAULT_REGISTRY_PORT;
  const response = await request(port);
  if (response.kind !== 'registry') return {
    state: response.kind === 'absent' ? (pointer.state === 'valid' ? 'unreachable' : 'missing') : response.kind,
    checked: true, reason: response.reason, port, port_available: response.kind === 'absent',
    registry_reachable: false, target_found: false, catalog_ready: false,
  };
  const registry = response.value.tunnels;
  const entry = registry[udid];
  const base = { checked: true, port, port_available: false, registry_reachable: true, target_found: Boolean(entry), catalog_ready: false };
  if (!entry) return { ...base, state: 'missing', reason: 'target_not_in_existing_registry' };
  if (!isObject(entry) || entry.udid !== udid || typeof entry.address !== 'string' || isIP(entry.address) !== 6 || !/^f[cd]/i.test(entry.address) ||
      !Number.isInteger(entry.rsdPort) || entry.rsdPort < 1 || entry.rsdPort > 65535 ||
      !isObject(entry.services) || !Object.keys(entry.services).length) {
    return { ...base, state: 'invalid', reason: 'target_catalog_not_ready' };
  }
  const state = ['Network', 'USB'].includes(entry.connectionType) ? entry.connectionType : 'unknown';
  return { ...base, state, catalog_ready: true, service_count: Object.keys(entry.services).length,
    ...(state === 'unknown' ? { reason: 'connection_type_unknown' } : {}) };
}
function summarizeUsb(devices, udid) {
  if (!Array.isArray(devices) || devices.some((device) => !isObject(device?.Properties) ||
      typeof device.Properties.SerialNumber !== 'string' || typeof device.Properties.ConnectionType !== 'string')) {
    return { checked: false, connected: null, state: 'unknown' };
  }
  const matches = devices.filter((device) => device.Properties.SerialNumber === udid);
  if (matches.some((device) => device.Properties.ConnectionType === 'USB')) return { checked: true, connected: true, state: 'USB' };
  if (matches.some((device) => !['USB', 'Network'].includes(device.Properties.ConnectionType))) return { checked: false, connected: null, state: 'unknown' };
  return { checked: true, connected: false, state: matches.length ? 'Network' : 'missing' };
}
export async function inspectUsb(udid, helper, listDevices) {
  try {
    if (listDevices) return summarizeUsb(await listDevices(), udid);
    // A bounded child keeps third-party logging and environment overrides out
    // of JSON diagnostics. ListDevices queries only the local usbmux daemon.
    const code = `const m=await import(process.argv[1]);let u;try{u=await m.createUsbmux({socketPath:'/var/run/usbmuxd',timeout:3000});const d=await u.listDevices(3000);console.log(JSON.stringify(d));}finally{await u?.close();}`;
    const { stdout } = await run(process.execPath, ['--input-type=module', '-e', code, pathToFileURL(helper.entryPath).href],
      { env: childEnvironment(), timeout: 7_000, maxBuffer: 256 * 1024 });
    return summarizeUsb(JSON.parse(stdout), udid);
  } catch { return { checked: false, connected: null, state: 'unknown' }; }
}
const guidance = {
  unsupported_platform: 'Use this foreground helper on macOS.',
  run_as_owner: 'Run node scripts/start-tunnel.mjs as your normal user, without an outer sudo.',
  configuration_required: 'Configure an explicit device UDID with coredevice and use_preinstalled_wda=true first.',
  runtime_missing: 'Run bash scripts/setup.sh --skip-plugin for the selected backend in this actual plugin copy first.',
  pointer_unsafe: 'Inspect the Strongbox directory and pointer ownership/permissions; do not overwrite or recursively repair shared state.',
  registry_pointer_missing: 'An existing registry is listening but its user pointer is missing. Identify its owner and restore the correct pointer before proceeding.',
  existing_registry: 'An existing registry or another listener occupies the selected port. Identify its owner; this command will not replace or stop it.',
  registry_unknown: 'The registry endpoint could not be verified. Inspect the foreground terminal or existing service before retrying.',
  usb_connected: 'After USB pairing, unplug the data cable and rerun --check; the upstream helper prefers USB whenever present.',
  usb_unknown: 'USB status could not be confirmed. Unlock the paired phone, check Xcode and the local usbmux daemon, then rerun --check.',
  network_missing: 'The target is not listed over Network. Unlock it on the same Wi-Fi and check Xcode wireless pairing before retrying.',
};
export async function inspectTunnel(options = {}) {
  const { backend = backendFor(), paths = pathsForBackend(process.env, backend), userHome = homedir(), directory = runtimeDirectoryForBackend(backend),
    uid = process.getuid(), platform = process.platform, listDevices, request = readRegistry,
    inspectPointer = inspectStrongbox } = options;
  const config = await readConfig(paths);
  const configured = typeof config.udid === 'string' && /^(?:[a-f\d]{8}-[a-f\d]{16}|[a-f\d]{40})$/i.test(config.udid) &&
    config.wda_transport === 'coredevice' && config.use_preinstalled_wda === true;
  const blockers = [];
  if (platform !== 'darwin') blockers.push('unsupported_platform');
  if (uid === 0) blockers.push('run_as_owner');
  if (!configured) blockers.push('configuration_required');
  let helper;
  try { helper = await resolveTunnelHelper(directory, backend); } catch { blockers.push('runtime_missing'); }
  const pointer = await inspectPointer(userHome, uid);
  const registry = await inspectRegistry(configured ? config.udid : '', pointer, request);
  const usb = configured && helper ? await inspectUsb(config.udid, helper, listDevices)
    : { checked: false, connected: null, state: 'unknown' };
  if (pointer.state === 'invalid') blockers.push('pointer_unsafe');
  const reusable = configured && pointer.state === 'valid' && registry.state === 'Network' && registry.catalog_ready;
  if (!reusable && pointer.state !== 'invalid') {
    if (registry.registry_reachable && pointer.state !== 'valid') blockers.push('registry_pointer_missing');
    else if (!registry.port_available) blockers.push(registry.state === 'unreachable' ? 'registry_unknown' : 'existing_registry');
  }
  if (usb.connected === true) blockers.push('usb_connected');
  else if (!usb.checked) blockers.push('usb_unknown');
  else if (!reusable && usb.state !== 'Network') blockers.push('network_missing');
  const result = { ok: true, read_only: true, backend, configured, configured_transport: config.wda_transport || 'default',
    use_preinstalled_wda: config.use_preinstalled_wda === true,
    helper: { available: Boolean(helper), ...(helper ? { version: helper.version } : {}) },
    pointer, tunnel_registry_checked: registry.checked, tunnel_registry: registry, usb,
    reusable, can_start: !reusable && blockers.length === 0,
    wireless_prerequisites_ready: reusable && usb.checked && usb.connected === false && blockers.length === 0,
    blockers, next_steps: [...new Set(blockers)].map((code) => code === 'runtime_missing'
      ? `Run bash scripts/setup.sh --skip-plugin --backend ${backend} in this actual plugin copy first.` : guidance[code]),
    privileged_tunnel_installed_by_plugin: false, device_reachability_verified: false, phone_session_created: false,
    preinstalled_remote_xpc_requires_separate_tunnel: true,
    limitation: 'Registry metadata and USB checks do not prove WDA health or a successful phone session.',
  };
  if (!blockers.length) result.next_steps = [reusable ? 'Reuse the existing Network tunnel; verify a new phone session and observation separately.'
    : `Run node scripts/start-tunnel.mjs --backend ${backend} in a normal-user terminal and keep it open.`];
  return result;
}
async function check(command, args) {
  try {
    const { stdout } = await run(command, args, { timeout: 5_000, maxBuffer: 16_384, env: childEnvironment() });
    return { available: true, summary: stdout.trim().slice(0, 2_000) };
  } catch { return { available: false }; }
}
export async function main(args = process.argv.slice(2)) {
  try {
    const parsed = parseBackendArgs(args, ['--json']);
    const [result, xcode, devicectl] = await Promise.all([inspectTunnel({ backend: parsed.backend }),
      check('/usr/bin/xcodebuild', ['-version']), check('/usr/bin/xcrun', ['--find', 'devicectl'])]);
    process.stdout.write(JSON.stringify({ ...result, xcode, devicectl }) + '\n');
  } catch {
    process.stdout.write(JSON.stringify({ ok: false, error: { code: 'diagnostic_failed', message: 'Cannot inspect tunnel prerequisites. Check private configuration and installed dependencies.' } }) + '\n');
    process.exitCode = 1;
  }
}
export function parseBackendArgs(args, flags) {
  const result = { backend: process.env.IPHONE_USE_BACKEND ?? 'direct-wda', flags: [] };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--backend' && ['direct-wda', 'appium'].includes(args[index + 1])) result.backend = args[++index];
    else if (flags.includes(arg)) result.flags.push(arg);
    else throw new Error('Use --backend direct-wda|appium with the supported command flags.');
  }
  backendFor({ IPHONE_USE_BACKEND: result.backend });
  return result;
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) await main();

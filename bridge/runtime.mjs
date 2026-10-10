import { randomBytes } from 'node:crypto';
import { execFile } from 'node:child_process';
import { chmod, lstat, mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir, userInfo } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createConnection, createServer } from 'node:net';
import { promisify } from 'node:util';
import { createRequire } from 'node:module';
import { applyIPhoneBridgePatches } from '../scripts/iphone-control-patches.mjs';

export const bridgeDirectory = dirname(fileURLToPath(import.meta.url));
export const directDirectory = join(bridgeDirectory, '../direct');
export const versions = Object.freeze({ appium: '3.7.0', xcuitest: '12.11.0' });
export const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const execFileAsync = promisify(execFile);
export class BridgeError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
export function pathsFor(env = process.env) {
  // Legacy bridge entry points deliberately ignore IPHONE_USE_BACKEND. An
  // Appium CLI command must never silently pick the default direct profile.
  const home = resolve(env.VICO_IPHONE_HOME || join(homedir(), '.local/share/vico-iphone'));
  return statePaths(home, 'appium');
}
export function backendFor(env = process.env) {
  const backend = env.IPHONE_USE_BACKEND ?? 'direct-wda';
  if (!['direct-wda', 'appium'].includes(backend)) throw new BridgeError('invalid_backend', 'Use direct-wda or appium as the backend.');
  return backend;
}
export function runtimeDirectoryForBackend(backend = backendFor()) {
  if (!['direct-wda', 'appium'].includes(backend)) throw new BridgeError('invalid_backend', 'Use direct-wda or appium as the backend.');
  return backend === 'appium' ? bridgeDirectory : directDirectory;
}
export function pathsForBackend(env = process.env, backend = backendFor(env)) {
  runtimeDirectoryForBackend(backend);
  const defaultHome = backend === 'appium' ? '.local/share/vico-iphone' : '.local/share/iphone-use-wireless-direct';
  return statePaths(resolve(env.VICO_IPHONE_HOME || join(homedir(), defaultHome)), backend);
}
function statePaths(home, backend) {
  const runtime = join(home, 'bridge');
  return {
    home, runtime, backend, defaultPort: backend === 'appium' ? 4737 : 18401,
    config: join(home, 'configuration/iphone-control.json'),
    descriptor: join(runtime, 'owner.json'), socket: join(runtime, 'control.sock'),
    lock: join(runtime, 'operation.lock'), log: join(home, 'logs/bridge.log'),
  };
}
export async function readJson(path, fallback = null) {
  try { return JSON.parse(await readFile(path, 'utf8')); }
  catch (e) { if (e.code === 'ENOENT') return fallback; throw e; }
}
export async function writePrivateJson(path, value) {
  const temporary = `${path}.${process.pid}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    await writeFile(temporary, JSON.stringify(value, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
    await rename(temporary, path);
  } finally { await rm(temporary, { force: true }); }
}
export async function privateDirectory(path) {
  await mkdir(path, { recursive: true, mode: 0o700 });
  const stat = await lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid()) {
    throw new BridgeError('unsafe_state_path', 'Bridge state must be a directory owned by this user.');
  }
  await chmod(path, 0o700);
}
export async function prepareState(paths) {
  for (const path of [paths.home, paths.runtime, dirname(paths.config), dirname(paths.log)]) {
    await privateDirectory(path);
  }
  if (Buffer.byteLength(paths.socket) > 100) {
    throw new BridgeError('state_path_too_long', 'Set VICO_IPHONE_HOME to a shorter private path (control socket path must fit 100 bytes).');
  }
}
export async function readConfig(paths) {
  let config;
  try { config = await readJson(paths.config, {}); }
  catch { throw new BridgeError('invalid_config', 'Cannot read valid JSON from the private iPhone configuration.'); }
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new BridgeError('invalid_config', 'iPhone configuration must be an object.');
  }
  const port = config.port ?? paths.defaultPort ?? 4737;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new BridgeError('invalid_port', 'The bridge port must be an integer between 1024 and 65535.');
  }
  if (config.appium_server_url && config.appium_server_url !== `http://127.0.0.1:${port}`) {
    throw new BridgeError('invalid_endpoint', 'This managed bridge supports only its configured 127.0.0.1 HTTP port.');
  }
  return { ...config, port };
}
export function childEnvironment(config = {}) {
  // Construct an allowlist; never inherit provider keys, npm auth, proxy auth,
  // NODE_OPTIONS or model-supplied executable/preload variables.
  const username = userInfo().username;
  return {
    HOME: homedir(), USER: username, LOGNAME: username, TMPDIR: tmpdir(),
    LANG: 'en_US.UTF-8', NO_COLOR: '1',
    PATH: `${dirname(process.execPath)}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin`,
    APPIUM_HOME: bridgeDirectory, APPIUM_XCUITEST_PREFER_DEVICECTL: '1',
    VICO_IPHONE_PRESERVE_WDA_APPS: '1',
    // This mode verifies a RemoteXPC tunnel before connecting. A paired phone
    // may still appear in usbmux even when its usbmux port is unreachable.
    ...(config.wda_transport === 'coredevice' && config.use_preinstalled_wda === true
      ? { VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING: '1' } : {}),
  };
}
export async function driverRegistrationStatus(directory = bridgeDirectory) {
  const root = resolve(directory);
  const manifest = join(root, 'node_modules/.cache/appium/extensions.yaml');
  try {
    // Use the pinned Appium dependency's YAML parser, without loading Appium
    // itself: status must not regenerate caches or start device discovery.
    const require = createRequire(join(root, 'node_modules/appium/package.json'));
    const data = require('yaml').parse(await readFile(manifest, 'utf8'));
    const pkg = await readJson(join(root, 'node_modules/appium-xcuitest-driver/package.json'));
    const driver = data?.drivers?.xcuitest;
    const registered = data?.schemaRev === 4 && pkg?.version === versions.xcuitest &&
      driver?.pkgName === 'appium-xcuitest-driver' && driver.version === versions.xcuitest &&
      driver.installPath === join(root, 'node_modules/appium-xcuitest-driver') &&
      driver.mainClass === pkg.appium?.mainClass && driver.automationName === 'XCUITest' &&
      JSON.stringify(driver.platformNames) === JSON.stringify(pkg.appium?.platformNames) &&
      driver.installType === 'npm' && driver.installSpec === `appium-xcuitest-driver@${versions.xcuitest}`;
    return { registered: Boolean(registered), ...(registered ? {} : { reason: 'stale_or_incompatible_manifest' }) };
  } catch (error) {
    return { registered: false, reason: error.code === 'ENOENT' ? 'missing_manifest' : 'unreadable_manifest' };
  }
}
export async function ensureDriverRegistration(directory = bridgeDirectory) {
  const root = resolve(directory);
  if ((await driverRegistrationStatus(root)).registered) return { registered: true, rebuilt: false };
  // extensions.yaml is a generated cache local to this isolated runtime. A
  // copied node_modules can retain an old installPath even when Appium's
  // package hash is unchanged. Let the official CLI rediscover the packages;
  // do not hand-write Appium's manifest or run driver install/npm update.
  await rm(join(root, 'node_modules/.cache/appium/extensions.yaml'), { force: true });
  try {
    const { stdout } = await execFileAsync(process.execPath,
      [join(root, 'node_modules/appium/index.js'), 'driver', 'list', '--installed', '--json'],
      { cwd: root, env: { ...childEnvironment(), APPIUM_HOME: root }, timeout: 30_000, maxBuffer: 1024 * 1024 });
    const driver = JSON.parse(stdout)?.xcuitest;
    if (driver?.installed !== true || driver.version !== versions.xcuitest ||
        driver.installPath !== join(root, 'node_modules/appium-xcuitest-driver') ||
        !(await driverRegistrationStatus(root)).registered) throw new Error('Invalid regenerated driver registration');
    return { registered: true, rebuilt: true };
  } catch {
    throw new BridgeError('driver_registration_failed', 'Appium could not register the pinned XCUITest driver in this runtime. Run node scripts/install-bridge.mjs --json.');
  }
}
export async function installationStatus(directory = bridgeDirectory) {
  try {
    const [appium, xcuitest] = await Promise.all([
      readJson(join(directory, 'node_modules/appium/package.json')),
      readJson(join(directory, 'node_modules/appium-xcuitest-driver/package.json')),
    ]);
    if (appium?.version !== versions.appium || xcuitest?.version !== versions.xcuitest) {
      return { installed: false, runtime_valid: false, reason: 'missing_or_incompatible_dependencies', versions };
    }
    const patches = await applyIPhoneBridgePatches(directory, { verifyOnly: true });
    const driver_registration = await driverRegistrationStatus(directory);
    return { installed: driver_registration.registered, runtime_valid: true,
      ...(!driver_registration.registered ? { reason: 'driver_registration_invalid' } : {}),
      versions, patches, driver_registration };
  } catch { return { installed: false, runtime_valid: false, reason: 'missing_or_invalid_compatibility_patches', versions }; }
}
export async function processIdentity(pid) {
  if (!Number.isSafeInteger(pid) || pid < 2) return null;
  try {
    const { stdout } = await execFileAsync('/bin/ps', ['-p', String(pid), '-o', 'lstart=', '-o', 'command='],
      { timeout: 2_000, maxBuffer: 16_384, env: childEnvironment() });
    return stdout.trim() || null;
  } catch (e) { if (e.code === 1) return null; throw e; }
}
export async function requestControl(paths, owner, action, timeout = 1_500) {
  if (!owner || typeof owner.token !== 'string' || !/^[a-f0-9]{64}$/.test(owner.token)) return null;
  return new Promise((resolveRequest) => {
    const socket = createConnection(paths.socket);
    let data = '';
    const finish = (result) => { socket.destroy(); resolveRequest(result); };
    socket.setTimeout(timeout, () => finish(null));
    socket.once('error', () => finish(null));
    socket.once('connect', () => socket.write(JSON.stringify({ token: owner.token, action }) + '\n'));
    socket.on('data', (chunk) => {
      data += chunk.toString();
      if (data.length > 16_384) return finish(null);
      if (data.includes('\n')) {
        try {
          const response = JSON.parse(data.split('\n')[0]);
          finish(response.pid === owner.pid && response.instance === owner.instance ? response : null);
        } catch { finish(null); }
      }
    });
    socket.once('end', () => finish(null));
  });
}
export async function readOwner(paths) {
  try {
    const stat = await lstat(paths.descriptor);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.uid !== process.getuid() || (stat.mode & 0o077)) return null;
    return await readJson(paths.descriptor);
  } catch (e) { if (e.code === 'ENOENT' || e instanceof SyntaxError) return null; throw e; }
}
export async function ownedSupervisor(paths) {
  const owner = await readOwner(paths);
  if (!owner || owner.format_version !== 1 || owner.socket !== paths.socket ||
      owner.script !== join(bridgeDirectory, 'supervisor.mjs')) return null;
  const identity = await processIdentity(owner.pid);
  if (!identity || identity !== owner.identity || !identity.includes(owner.script)) return null;
  const response = await requestControl(paths, owner, 'status');
  return response ? { owner, response } : null;
}
export async function portAvailable(port) {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once('error', (e) => e.code === 'EADDRINUSE' ? resolvePort(false) : reject(e));
    server.listen(port, '127.0.0.1', () => server.close(() => resolvePort(true)));
  });
}
export async function probeAppium(port) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/status`, { signal: AbortSignal.timeout(1_500), redirect: 'error' });
    if (!response.ok) return false;
    const value = await response.json();
    return value?.value?.ready === true && value?.value?.build?.version === versions.appium;
  } catch { return false; }
}
export async function withOperationLock(paths, callback) {
  await prepareState(paths);
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await mkdir(paths.lock, { mode: 0o700 });
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      const lockOwner = await readJson(join(paths.lock, 'owner.json'));
      // An incomplete lock may be in the brief publication window. Refuse it;
      // never assume an unknown process is stale or delete someone else's lock.
      if (!lockOwner || await processIdentity(lockOwner.pid)) {
        throw new BridgeError('operation_in_progress', 'Another bridge operation holds the private lock.');
      }
      await rm(paths.lock, { recursive: true, force: true });
      continue;
    }
    try {
      await writePrivateJson(join(paths.lock, 'owner.json'), { pid: process.pid, identity: await processIdentity(process.pid) });
      return await callback();
    } finally { await rm(paths.lock, { recursive: true, force: true }); }
  }
  throw new BridgeError('operation_in_progress', 'Another bridge operation is in progress.');
}
export const limitations = Object.freeze([
  'Appium readiness does not verify WDA, device reachability or a phone session.',
  'The default path uses Xcode/CoreDevice for a paired iPhone on USB or the same Wi-Fi.',
  'No privileged RemoteXPC tunnel daemon or login autostart is installed; preinstalled RemoteXPC and cross-network control need separate setup and acceptance.',
]);

import { constants } from 'node:fs';
import { lstat, open, readFile } from 'node:fs/promises';
import { get } from 'node:http';
import { isIP } from 'node:net';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const directDirectory = dirname(fileURLToPath(import.meta.url));
export const REMOTEXPC_VERSION = '5.18.9';
const WDA_PORT = 8100;
const delay = (ms) => new Promise((done) => setTimeout(done, ms));
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
export class DirectError extends Error {
  constructor(code, message, outcome = 'not_sent') { super(message); this.code = code; this.request_outcome = outcome; }
}
const error = (code, message, outcome) => new DirectError(code, message, outcome);

export async function readConfig(env = process.env) {
  if (!env.VICO_IPHONE_HOME || !env.VICO_IPHONE_HOME.startsWith('/')) {
    throw error('configuration_required', 'Set VICO_IPHONE_HOME to the private direct-WDA state directory.');
  }
  const home = resolve(env.VICO_IPHONE_HOME);
  const path = join(home, 'configuration/iphone-control.json');
  let handle;
  try {
    for (const directory of [home, dirname(path)]) {
      const details = await lstat(directory);
      if (!details.isDirectory() || details.isSymbolicLink() || details.uid !== process.getuid() || (details.mode & 0o022)) throw new Error();
    }
    handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const details = await handle.stat();
    if (!details.isFile() || details.uid !== process.getuid() || (details.mode & 0o077) || details.size > 64 * 1024) throw new Error();
    const config = JSON.parse(await handle.readFile('utf8'));
    if (!object(config) || config.enabled !== true ||
        !/^(?:[a-f\d]{8}-[a-f\d]{16}|[a-f\d]{40})$/i.test(config.udid ?? '') ||
        config.wda_transport !== 'coredevice' || config.use_preinstalled_wda !== true ||
        !Number.isInteger(config.port) || config.port < 1024 || config.port > 65535 ||
        !/^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(config.wda_bundle_id ?? '') || config.wda_url || config.wda_base_url) throw new Error();
    return { ...config, runnerBundleId: config.wda_bundle_id.endsWith('.xctrunner')
      ? config.wda_bundle_id : `${config.wda_bundle_id}.xctrunner` };
  } catch {
    throw error('configuration_required', 'Configure this private direct-WDA profile with an explicit device, loopback port, preinstalled WDA and coredevice transport.');
  } finally { await handle?.close(); }
}

export async function checkDependencies(directory = directDirectory) {
  try {
    const root = resolve(directory);
    const dependency = join(root, 'node_modules/appium-ios-remotexpc');
    const [pkg, lock, entry] = await Promise.all([
      readFile(join(dependency, 'package.json'), 'utf8').then(JSON.parse),
      readFile(join(root, 'package-lock.json'), 'utf8').then(JSON.parse),
      lstat(join(dependency, 'build/src/index.js')),
    ]);
    return pkg.name === 'appium-ios-remotexpc' && pkg.version === REMOTEXPC_VERSION &&
      lock.packages?.['node_modules/appium-ios-remotexpc']?.version === REMOTEXPC_VERSION && entry.isFile() && !entry.isSymbolicLink();
  } catch { return false; }
}

export async function inspectHost({ env = process.env, platform = process.platform, uid = process.getuid(), directory = directDirectory } = {}) {
  const configured = await readConfig(env).then(() => true, () => false);
  const dependencies_ready = await checkDependencies(directory);
  const blockers = [];
  if (platform !== 'darwin') blockers.push('unsupported_platform');
  if (uid === 0) blockers.push('run_as_owner');
  if (!configured) blockers.push('configuration_required');
  if (!dependencies_ready) blockers.push('runtime_missing');
  return { ok: blockers.length === 0, backend: 'direct-wda', configured, dependencies_ready,
    phone_verified: false, read_only: true, blockers };
}

export async function readRegistryPort(userHome = homedir()) {
  const directories = [userHome, join(userHome, 'Library'), join(userHome, 'Library/Application Support'),
    join(userHome, 'Library/Application Support/appium-xcuitest-driver-nodejs'),
    join(userHome, 'Library/Application Support/appium-xcuitest-driver-nodejs/strongbox')];
  let handle;
  try {
    for (const directory of directories) {
      const details = await lstat(directory);
      if (!details.isDirectory() || details.isSymbolicLink() || details.uid !== process.getuid() || (details.mode & 0o022)) throw new Error();
    }
    handle = await open(join(directories.at(-1), 'tunnelRegistryPort'), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const details = await handle.stat();
    if (!details.isFile() || ![0, process.getuid()].includes(details.uid) || (details.mode & 0o022) || details.size > 16) throw new Error();
    const value = (await handle.readFile('utf8')).trim();
    if (!/^\d{1,5}$/.test(value) || Number(value) < 1 || Number(value) > 65535) throw new Error();
    return Number(value);
  } catch {
    throw error('tunnel_required', 'An existing owner-configured wireless RemoteXPC tunnel is required. Inspect the tunnel terminal; this helper does not create or stop tunnels.');
  } finally { await handle?.close(); }
}

export function requestJson(port, path, timeout = 2_000) {
  return new Promise((done, fail) => {
    let settled = false;
    const finish = (err, value) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (err) fail(err); else done(value);
    };
    const req = get({ hostname: '127.0.0.1', port, path, agent: false }, (res) => {
      const chunks = []; let size = 0;
      res.on('data', (chunk) => {
        size += chunk.length;
        if (size > 512 * 1024) { finish(error('invalid_response', 'The local endpoint returned an oversized response.')); req.destroy(); }
        else chunks.push(chunk);
      });
      res.on('error', () => finish(error('endpoint_unreachable', 'The local endpoint response was interrupted.')));
      res.on('end', () => {
        if (res.statusCode !== 200) return finish(error('invalid_response', 'The local endpoint returned an unexpected HTTP response.'));
        try { finish(null, JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
        catch { finish(error('invalid_response', 'The local endpoint did not return valid JSON.')); }
      });
    });
    const timer = setTimeout(() => { finish(error('endpoint_unreachable', 'The local endpoint did not respond in time.')); req.destroy(); }, timeout);
    req.on('error', () => finish(error('endpoint_unreachable', 'The local endpoint is not reachable.')));
  });
}

export async function assertNetwork(config, registryPort, request = requestJson) {
  let value;
  try { value = await request(registryPort, '/remotexpc/tunnels'); }
  catch { throw error('tunnel_required', 'The existing wireless tunnel registry is not reachable.'); }
  const entry = value?.tunnels?.[config.udid];
  if (!object(entry) || entry.udid !== config.udid || entry.connectionType !== 'Network' ||
      typeof entry.address !== 'string' || isIP(entry.address) !== 6 || !/^f[cd]/i.test(entry.address) ||
      !Number.isInteger(entry.rsdPort) || entry.rsdPort < 1 || entry.rsdPort > 65535 ||
      !object(entry.services) || Object.keys(entry.services).length === 0) {
    throw error('wireless_required', 'The configured phone must have a ready Network tunnel. USB and unknown transports are not used.');
  }
  return entry;
}

export async function assertNoUsb(config, remote) {
  let usbmux;
  try {
    usbmux = await remote.createUsbmux({ socketPath: '/var/run/usbmuxd', timeout: 3000 });
    const devices = await usbmux.listDevices(3000);
    if (!Array.isArray(devices) || devices.some((device) => !object(device?.Properties) ||
        typeof device.Properties.SerialNumber !== 'string' || typeof device.Properties.ConnectionType !== 'string')) throw new Error();
    const matching = devices.filter((device) => device.Properties.SerialNumber === config.udid);
    if (matching.some((device) => device.Properties.ConnectionType === 'USB')) {
      throw error('usb_connected', 'Unplug the data cable before trying the wireless direct-WDA profile.');
    }
    if (matching.some((device) => device.Properties.ConnectionType !== 'Network')) throw new Error();
  } catch (cause) {
    if (cause instanceof DirectError) throw cause;
    throw error('usb_unknown', 'Cannot confirm that the configured phone has no USB connection. Check local device pairing.');
  } finally { await usbmux?.close().catch(() => {}); }
}

export function inspectWdaStatus(value, config) {
  if (!object(value) || !object(value.value) || value.value.ready !== true || !Object.hasOwn(value, 'sessionId')) {
    throw error('wda_status_invalid', 'An endpoint is present but it cannot be identified as a safe idle WDA server.');
  }
  if (value.sessionId !== null && typeof value.sessionId !== 'string') {
    throw error('wda_status_invalid', 'The running WDA did not provide a valid session state.');
  }
  if (value.sessionId || value.value.sessionId) throw error('wda_in_use', 'WDA already has an active session. Finish or disconnect its owner before using direct WDA.');
  const bundle = value.value.build?.productBundleIdentifier;
  if (![config.wda_bundle_id, config.runnerBundleId].includes(bundle)) {
    throw error('wda_identity_mismatch', 'The running WDA does not match the configured installed runner.');
  }
}

async function bounded(promise, ms, code, message, outcome = 'not_sent') {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(error(code, message, outcome)), ms); })]); }
  finally { clearTimeout(timer); }
}

/** Owns only one loopback forwarder and (if launched here) one verified runner PID. */
export class DirectRuntime {
  constructor(config, remote, { request = requestJson, readPort = readRegistryPort, wait = delay, startupMs = 45_000 } = {}) {
    this.config = config; this.remote = remote; this.request = request; this.readPort = readPort;
    this.wait = wait; this.startupMs = startupMs; this.closed = false; this.forwarder = null;
    this.dvt = null; this.pid = 0; this.launchPending = null; this.launchUnknown = false;
    this.wdaStarted = false; this.launchAttempted = false; this.closePromise = null;
  }
  assertOpen() { if (this.closed) throw error('cancelled', 'Direct WDA startup was cancelled.', this.launchAttempted ? 'unknown' : 'not_sent'); }
  async start() {
    try { return await this.startOwned(); }
    catch (cause) {
      // A launch can succeed before readiness parsing fails. Its effects are
      // uncertain until cleanup verifies the owned process has actually stopped.
      if (this.launchAttempted) {
        const failure = sanitizedFailure(cause);
        throw error(failure.error_code, failure.error.message, 'unknown');
      }
      throw cause;
    }
  }
  async startOwned() {
    this.assertOpen();
    const config = this.config;
    this.registryPort = await this.readPort();
    await assertNetwork(config, this.registryPort, this.request);
    await assertNoUsb(config, this.remote);
    this.assertOpen();
    this.forwarder = new this.remote.DevicePortForwarder(config.port, WDA_PORT, {
      host: '127.0.0.1',
      // No usbmux fallback, even when the wireless connection fails.
      primaryConnector: async () => {
        this.assertOpen();
        await assertNetwork(config, this.registryPort, this.request);
        return this.remote.connectViaTunnel(config.udid, WDA_PORT, 3000);
      },
    });
    this.forwarder.on('upstreamConnectError', () => {});
    try { await this.forwarder.start(); }
    catch { throw error('port_unavailable', 'The configured loopback port is occupied or unavailable. Existing listeners were not contacted or stopped.'); }
    this.assertOpen();
    let initial;
    try { initial = await this.request(config.port, '/status'); }
    catch (cause) {
      if (cause?.code !== 'endpoint_unreachable') throw cause;
    }
    this.assertOpen();
    if (initial) {
      inspectWdaStatus(initial, config);
      return this.readiness();
    }
    await assertNetwork(config, this.registryPort, this.request);
    this.dvt = await bounded(this.remote.Services.startDVTService(config.udid), 10_000,
      'wda_launch_failed', 'Could not open the installed runner control service.');
    this.assertOpen();
    const existingPid = await bounded(this.dvt.processControl.getPidForBundleIdentifier(config.runnerBundleId), 5000,
      'wda_state_unknown', 'Could not verify whether the configured runner is already running.');
    if (existingPid !== 0) throw error('wda_in_use', 'The configured WDA runner is already running but not ready. Its owner must resolve or stop it first.');
    this.assertOpen();
    await assertNetwork(config, this.registryPort, this.request);
    this.assertOpen();
    this.launchAttempted = true; this.launchUnknown = true;
    this.launchPending = this.dvt.processControl.launch({ bundleId: config.runnerBundleId,
      environment: { USE_PORT: String(WDA_PORT), WDA_PRODUCT_BUNDLE_IDENTIFIER: config.runnerBundleId },
      killExisting: false,
    }).then((pid) => {
      if (!Number.isInteger(pid) || pid <= 0) throw error('wda_launch_unknown', 'The runner launch did not return a valid owned process identifier.', 'unknown');
      this.pid = pid; this.wdaStarted = true; this.launchUnknown = false;
      return pid;
    });
    // A lost launch acknowledgement is never retried: the controller retains its lease.
    await bounded(this.launchPending, 10_000, 'wda_launch_unknown', 'The runner launch outcome is unknown; do not retry automatically.', 'unknown');
    this.assertOpen();
    const deadline = Date.now() + this.startupMs;
    while (Date.now() < deadline) {
      this.assertOpen();
      let value;
      try { value = await this.request(config.port, '/status', 1500); }
      catch (cause) { if (cause?.code !== 'endpoint_unreachable') throw cause; }
      if (value) { inspectWdaStatus(value, config); return this.readiness(); }
      await this.wait(250);
    }
    throw error('wda_start_timeout', 'The installed runner did not become ready. Check device unlock, developer trust and runner signing.', 'unknown');
  }
  readiness() {
    return { ready: true, endpoint: `http://127.0.0.1:${this.config.port}`, wda_started: this.wdaStarted };
  }
  close() {
    this.closed = true;
    this.closePromise ??= this.cleanup();
    return this.closePromise;
  }
  async cleanup() {
    let failure;
    if (this.launchPending && this.launchUnknown) {
      try { await bounded(this.launchPending, 5000, 'cleanup_unknown', 'Runner launch cleanup is unresolved.', 'unknown'); }
      catch { failure = error('cleanup_unknown', 'The runner launch outcome could not be resolved; device ownership must be retained.', 'unknown'); }
    }
    if (this.pid && this.dvt) {
      try {
        const stopped = await bounded(this.stopOwnedRunner(), 6000, 'cleanup_unknown', 'Could not verify that the owned runner stopped.', 'unknown');
        if (!stopped) throw new Error();
      } catch { failure = error('cleanup_unknown', 'Could not verify that the owned runner stopped; device ownership must be retained.', 'unknown'); }
    }
    try { if (this.dvt) await bounded(this.dvt.dvtService.close(), 2000, 'cleanup_unknown', 'The runner control connection could not be closed.', 'unknown'); }
    catch (cause) { failure ??= cause; }
    try { if (this.forwarder) await this.forwarder.stop(); }
    catch (cause) { if (cause?.code !== 'ERR_SERVER_NOT_RUNNING') failure ??= error('cleanup_unknown', 'The owned forwarder could not be closed.', 'unknown'); }
    if (failure) throw failure;
  }
  async stopOwnedRunner() {
    const { processControl, deviceInfo } = this.dvt;
    const running = await deviceInfo.isRunningPid(this.pid);
    if (running === false) return true;
    if (running !== true || await processControl.getPidForBundleIdentifier(this.config.runnerBundleId) !== this.pid) return false;
    await processControl.kill(this.pid);
    for (let attempt = 0; attempt < 10; attempt += 1) {
      const alive = await deviceInfo.isRunningPid(this.pid);
      if (alive === false) return true;
      if (alive !== true) return false;
      await this.wait(150);
    }
    return false;
  }
}

export function sanitizedFailure(cause) {
  const known = cause instanceof DirectError;
  return { ready: false, error_code: known ? cause.code : 'runtime_failed', error: known ? { code: cause.code, message: cause.message }
    : { code: 'runtime_failed', message: 'Direct WDA could not start. Inspect private runtime diagnostics and device readiness.' },
  request_outcome: known ? cause.request_outcome : 'unknown' };
}

/** Emitters must await this: no safe failure receipt exists before cleanup ends. */
export async function finalizeFailure(cause, runtime) {
  let cleanupVerified = false;
  let cleanupError = null;
  try {
    await runtime?.close();
    cleanupVerified = !runtime?.launchUnknown;
  } catch (failure) { cleanupError = sanitizedFailure(failure).error_code; }
  const receipt = sanitizedFailure(cause);
  // A safe-looking readiness/parser error must not let the caller release its
  // lease while an owned or unacknowledged process launch remains unresolved.
  if (!cleanupVerified && (runtime?.launchAttempted || runtime?.launchUnknown || runtime?.wdaStarted)) {
    receipt.request_outcome = 'unknown';
  }
  return { receipt: { ...receipt, cleanup_verified: cleanupVerified,
    wda_started: runtime?.wdaStarted === true,
    ...(cleanupError ? { cleanup_error_code: cleanupError } : {}) },
  exitCode: cleanupVerified ? 1 : 2 };
}

import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { DirectError, DirectRuntime, assertNetwork, assertNoUsb, inspectHost, inspectWdaStatus,
  readConfig, sanitizedFailure, finalizeFailure } from '../runtime.mjs';

const config = { enabled: true, udid: `${'A'.repeat(8)}-${'B'.repeat(16)}`, wda_transport: 'coredevice',
  use_preinstalled_wda: true, port: 18401, wda_bundle_id: 'org.example.WebDriverAgentRunner',
  runnerBundleId: 'org.example.WebDriverAgentRunner.xctrunner' };
const network = () => ({ tunnels: { [config.udid]: { udid: config.udid, address: 'fd00::1', rsdPort: 5000,
  connectionType: 'Network', services: { 'example.service': { port: 5050 } } } } });
const healthy = () => ({ sessionId: null, value: { ready: true, build: { productBundleIdentifier: config.runnerBundleId } } });
const unreachable = () => { throw new DirectError('endpoint_unreachable', 'No WDA'); };

function fixture({ existing = null, status = healthy, bindError = false, failKill = false, startPid = 0 } = {}) {
  const calls = []; let pid = startPid; let forwarded;
  const remote = {
    createUsbmux: async () => ({ listDevices: async () => { calls.push('usb_list'); return []; }, close: async () => calls.push('usb_close') }),
    connectViaTunnel: async (...args) => { calls.push(['tunnel_connect', ...args]); return 'socket'; },
    DevicePortForwarder: class {
      constructor(...args) { forwarded = args; calls.push('forward_construct'); }
      on() {}
      async start() { calls.push('forward_start'); if (bindError) throw Object.assign(new Error('occupied'), { code: 'EADDRINUSE' }); }
      async stop() { calls.push('forward_stop'); }
    },
    Services: { startDVTService: async () => {
      calls.push('dvt_start');
      return {
        processControl: {
          getPidForBundleIdentifier: async () => pid,
          launch: async (args) => { calls.push(['launch', args]); pid = 4321; return pid; },
          kill: async (target) => { calls.push(['kill', target]); if (failKill) throw new Error('connection gone'); pid = 0; },
        },
        deviceInfo: { isRunningPid: async (target) => target === pid },
        dvtService: { close: async () => calls.push('dvt_close') },
      };
    } },
  };
  let observations = 0;
  const request = async (_, path) => {
    if (path === '/remotexpc/tunnels') return network();
    calls.push('status'); observations += 1;
    if (observations === 1) return existing ?? unreachable();
    return status();
  };
  const runtime = new DirectRuntime(config, remote, { request, readPort: async () => 43000, wait: async () => {}, startupMs: 2 });
  return { runtime, remote, calls, forward: () => forwarded, setPid: (value) => { pid = value; } };
}

test('wireless validation rejects USB, missing, and malformed entries', async () => {
  for (const type of ['USB', 'unknown', undefined]) {
    const data = network(); data.tunnels[config.udid].connectionType = type;
    await assert.rejects(assertNetwork(config, 43000, async () => data), { code: 'wireless_required' });
  }
  await assert.rejects(assertNetwork(config, 43000, async () => ({ tunnels: {} })), { code: 'wireless_required' });
  const data = network(); data.tunnels[config.udid].udid = 'other';
  await assert.rejects(assertNetwork(config, 43000, async () => data), { code: 'wireless_required' });
});

test('connected USB and unknown usbmux response block wireless startup and close discovery', async () => {
  for (const devices of [null, [{ Properties: { SerialNumber: config.udid, ConnectionType: 'USB' } }],
    [{ Properties: { SerialNumber: config.udid, ConnectionType: 'unexpected' } }]]) {
    let closed = false;
    await assert.rejects(assertNoUsb(config, { createUsbmux: async () => ({ listDevices: async () => devices,
      close: async () => { closed = true; } }) }), (err) => ['usb_connected', 'usb_unknown'].includes(err.code));
    assert.equal(closed, true);
  }
});

test('forwarding has only tunnel connector, listens on loopback, never adds USB fallback', async () => {
  const f = fixture({ existing: healthy() });
  await f.runtime.start();
  const [local, remote, options] = f.forward();
  assert.equal(local, config.port); assert.equal(remote, 8100);
  assert.equal(options.host, '127.0.0.1'); assert.equal(Object.hasOwn(options, 'fallbackConnector'), false);
  assert.equal(await options.primaryConnector(), 'socket');
  await f.runtime.close();
});

test('occupied local port is refused without contacting listener or launching WDA', async () => {
  const f = fixture({ bindError: true });
  await assert.rejects(f.runtime.start(), { code: 'port_unavailable' });
  assert.equal(f.calls.includes('status'), false); assert.equal(f.calls.includes('dvt_start'), false);
  await f.runtime.close();
});

test('real port binder does not replace an existing loopback listener', async () => {
  const { DevicePortForwarder } = await import('appium-ios-remotexpc');
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  let upstreamCalls = 0;
  const forwarder = new DevicePortForwarder(server.address().port, 8100, {
    host: '127.0.0.1', primaryConnector: async () => { upstreamCalls += 1; throw new Error(); },
  });
  try { await assert.rejects(forwarder.start(), { code: 'EADDRINUSE' }); assert.equal(server.listening, true); assert.equal(upstreamCalls, 0); }
  finally { await forwarder.stop().catch(() => {}); await new Promise((resolve) => server.close(resolve)); }
});

test('healthy idle WDA is reused without DVT launch and is left running on cleanup', async () => {
  const f = fixture({ existing: healthy() });
  assert.deepEqual(await f.runtime.start(), { ready: true, endpoint: 'http://127.0.0.1:18401', wda_started: false });
  await f.runtime.close();
  assert.equal(f.calls.includes('dvt_start'), false);
  assert.equal(f.calls.includes('forward_stop'), true);
});

test('an existing WDA session cannot be taken over even if ready is true', async () => {
  const current = healthy(); current.sessionId = 'foreign-session';
  const f = fixture({ existing: current });
  await assert.rejects(f.runtime.start(), { code: 'wda_in_use' });
  await f.runtime.close();
  assert.equal(f.calls.includes('dvt_start'), false);
});

test('unknown WDA identity or missing session state cannot be adopted', () => {
  for (const value of [{ value: healthy().value }, { ...healthy(), sessionId: 0 },
    { sessionId: null, value: { ready: true, build: { productBundleIdentifier: 'other' } } }]) {
    assert.throws(() => inspectWdaStatus(value, config), (err) => ['wda_status_invalid', 'wda_identity_mismatch'].includes(err.code));
  }
});

test('unhealthy but already running runner is not relaunched or killed', async () => {
  const f = fixture({ startPid: 99 });
  await assert.rejects(f.runtime.start(), { code: 'wda_in_use' });
  await f.runtime.close();
  assert.equal(f.calls.some((call) => Array.isArray(call) && ['launch', 'kill'].includes(call[0])), false);
});

test('direct startup uses preinstalled runner, killExisting false, and cleans only owned PID', async () => {
  const f = fixture();
  assert.equal((await f.runtime.start()).wda_started, true);
  const launch = f.calls.find((call) => Array.isArray(call) && call[0] === 'launch')[1];
  assert.equal(launch.bundleId, config.runnerBundleId); assert.equal(launch.killExisting, false);
  assert.deepEqual(launch.environment, { USE_PORT: '8100', WDA_PRODUCT_BUNDLE_IDENTIFIER: config.runnerBundleId });
  await f.runtime.close(); await f.runtime.close();
  assert.deepEqual(f.calls.filter((call) => Array.isArray(call) && call[0] === 'kill'), [['kill', 4321]]);
  assert.equal(f.calls.filter((call) => call === 'forward_stop').length, 1);
});

test('readiness failure still cleans the owned runner and forwarder', async () => {
  const f = fixture({ status: () => ({ nonsense: true }) });
  await assert.rejects(f.runtime.start(), { code: 'wda_status_invalid', request_outcome: 'unknown' });
  await f.runtime.close();
  assert.ok(f.calls.some((call) => Array.isArray(call) && call[0] === 'kill'));
  assert.ok(f.calls.includes('forward_stop'));
});

test('startup failure receipt waits until cleanup is complete', async () => {
  let finishCleanup; let receiptAvailable = false;
  const runtime = { launchAttempted: true, launchUnknown: false, wdaStarted: true,
    close: () => new Promise((resolve) => { finishCleanup = resolve; }) };
  const pending = finalizeFailure(new DirectError('wda_status_invalid', 'Invalid status'), runtime)
    .then((value) => { receiptAvailable = true; return value; });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(receiptAvailable, false);
  finishCleanup();
  const final = await pending;
  assert.equal(final.receipt.cleanup_verified, true);
  assert.equal(final.exitCode, 1);
});

test('post-launch readiness failure plus failed cleanup never emits not_sent', async () => {
  const f = fixture({ status: () => ({ nonsense: true }), failKill: true });
  let cause;
  try { await f.runtime.start(); } catch (failure) { cause = failure; }
  assert.equal(cause.request_outcome, 'unknown');
  const final = await finalizeFailure(cause, f.runtime);
  assert.equal(final.receipt.request_outcome, 'unknown');
  assert.equal(final.receipt.cleanup_verified, false);
  assert.equal(final.receipt.wda_started, true);
  assert.equal(final.exitCode, 2);
  // Even an unrelated caller passing an incorrectly classified parser error
  // cannot override the runtime's tracked launch/cleanup uncertainty.
  const inconsistent = await finalizeFailure(new DirectError('wda_status_invalid', 'Invalid status'), f.runtime);
  assert.equal(inconsistent.receipt.request_outcome, 'unknown');
});

test('unacknowledged launch remains unknown even if close incorrectly resolves', async () => {
  const final = await finalizeFailure(new DirectError('cancelled', 'Cancelled'), {
    launchAttempted: true, launchUnknown: true, wdaStarted: false, close: async () => {},
  });
  assert.equal(final.receipt.request_outcome, 'unknown');
  assert.equal(final.receipt.cleanup_verified, false);
  assert.equal(final.exitCode, 2);
});

test('a prerequisite failure emits verified no-mutation receipt only after finalization', async () => {
  const final = await finalizeFailure(new DirectError('configuration_required', 'Missing configuration'));
  assert.equal(final.receipt.request_outcome, 'not_sent');
  assert.equal(final.receipt.cleanup_verified, true);
  assert.equal(final.receipt.wda_started, false);
  assert.equal(final.exitCode, 1);
});

test('cleanup cannot report success when owned PID termination fails', async () => {
  const f = fixture({ failKill: true }); await f.runtime.start();
  await assert.rejects(f.runtime.close(), { code: 'cleanup_unknown', request_outcome: 'unknown' });
  assert.ok(f.calls.includes('forward_stop'));
});

test('PID reuse does not authorize killing a different runner', async () => {
  const f = fixture(); await f.runtime.start();
  f.runtime.dvt.deviceInfo.isRunningPid = async () => true;
  f.setPid(5555);
  await assert.rejects(f.runtime.close(), { code: 'cleanup_unknown' });
  assert.equal(f.calls.some((call) => Array.isArray(call) && call[0] === 'kill'), false);
});

test('unknown launch errors remain unknown and cleanup cannot release ownership', async () => {
  const f = fixture();
  const original = f.remote.Services.startDVTService;
  f.remote.Services.startDVTService = async () => {
    const dvt = await original();
    dvt.processControl.launch = async () => { throw new Error('private path and device id'); };
    return dvt;
  };
  await assert.rejects(f.runtime.start());
  await assert.rejects(f.runtime.close(), { code: 'cleanup_unknown' });
  const result = sanitizedFailure(new Error('token-secret /private/path'));
  assert.equal(result.request_outcome, 'unknown');
  assert.equal(JSON.stringify(result).includes('token-secret'), false);
});

test('EOF-style cancellation before launch has no device mutation', async () => {
  const f = fixture(); f.runtime.closed = true;
  await assert.rejects(f.runtime.start(), { code: 'cancelled' });
  await f.runtime.close(); assert.deepEqual(f.calls, []);
});

test('configuration is private and normalized; --check never starts a phone', async () => {
  const home = await mkdtemp(join(tmpdir(), 'direct-wda-test-'));
  try {
    await mkdir(join(home, 'configuration'), { mode: 0o700 });
    const { runnerBundleId, ...stored } = config;
    await writeFile(join(home, 'configuration/iphone-control.json'), JSON.stringify(stored), { mode: 0o600 });
    assert.equal((await readConfig({ VICO_IPHONE_HOME: home })).runnerBundleId, runnerBundleId);
    const host = await inspectHost({ env: { VICO_IPHONE_HOME: home }, platform: 'darwin', uid: 501 });
    assert.equal(host.configured, true); assert.equal(host.phone_verified, false); assert.equal(host.dependencies_ready, true);
    const child = spawnSync(process.execPath, ['direct/runner.mjs', '--check'], {
      cwd: new URL('../..', import.meta.url), env: { ...process.env, VICO_IPHONE_HOME: home }, encoding: 'utf8', timeout: 5000,
    });
    assert.equal(child.stdout.trim().split('\n').length, 1);
    assert.equal(JSON.parse(child.stdout).read_only, true);
    assert.equal(JSON.parse(child.stdout).phone_verified, false);
  } finally { await rm(home, { recursive: true, force: true }); }
});

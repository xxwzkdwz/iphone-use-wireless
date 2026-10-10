import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { chmod, mkdir, mkdtemp, open, readFile, realpath, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { pathsFor } from '../bridge/runtime.mjs';
import { inspectRegistry, inspectStrongbox, inspectTunnel, inspectUsb, readRegistry, strongboxPaths } from '../scripts/tunnel-diagnostics.mjs';
import { launchForeground, prepareStrongbox, startTunnel, sudoInvocation } from '../scripts/start-tunnel.mjs';

const udid = ['00000000', '0000000000000001'].join('-');
const remote = 'node_modules/appium-xcuitest-driver/node_modules/appium-ios-remotexpc';
const device = (type = 'Network') => ({ Properties: { SerialNumber: udid, ConnectionType: type } });
const entry = (type = 'Network') => ({ udid, address: 'fd00::1', rsdPort: 12345, services: { test: { port: 12346 } }, connectionType: type });
const registry = (value = entry()) => ({ kind: 'registry', value: { tunnels: { [udid]: value } } });
async function fixture(t) {
  const root = await mkdtemp(join(await realpath('/tmp'), 'iu-tunnel-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const userHome = join(root, 'user');
  await mkdir(userHome, { mode: 0o700 });
  const directory = join(root, 'plugin/bridge');
  const paths = pathsFor({ VICO_IPHONE_HOME: join(root, 'state') });
  await mkdir(dirname(paths.config), { recursive: true });
  await writeFile(paths.config, JSON.stringify({ udid, wda_transport: 'coredevice', use_preinstalled_wda: true }));
  await mkdir(join(directory, remote, 'scripts'), { recursive: true });
  await writeFile(join(directory, remote, 'scripts/tunnel-creation.mjs'), '// fixture: never executed\n');
  await writeFile(join(directory, remote, 'package.json'), JSON.stringify({ name: 'appium-ios-remotexpc', version: '5.18.9' }));
  await writeFile(join(directory, 'node_modules/appium-xcuitest-driver/package.json'), JSON.stringify({ version: '12.11.0' }));
  await writeFile(join(directory, 'package-lock.json'), JSON.stringify({ packages: {
    [remote]: { version: '5.18.9' }, 'node_modules/appium-xcuitest-driver': { version: '12.11.0' },
  } }));
  return { root, userHome, directory, paths, platform: 'darwin', uid: process.getuid(),
    interactive: true, listDevices: async () => [device()], request: async () => ({ kind: 'absent', reason: 'connection_refused' }) };
}
async function pointer(options, content = '42314\n') {
  await prepareStrongbox({ userHome: options.userHome, uid: options.uid, expectedPort: 42314 });
  const path = strongboxPaths(options.userHome).pointer;
  await writeFile(path, content);
  return path;
}

test('--check is read-only, redacted, and can distinguish a missing tunnel', async (t) => {
  const options = await fixture(t);
  const result = await startTunnel({ ...options, checkOnly: true });
  assert.equal(result.tunnel_registry.state, 'missing');
  assert.equal(result.tunnel_registry_checked, true);
  assert.equal(result.can_start, true);
  assert.equal(result.usb.connected, false);
  assert.equal(result.phone_session_created, false);
  assert.equal(JSON.stringify(result).includes(udid), false);
  await assert.rejects(stat(join(options.userHome, 'Library')), { code: 'ENOENT' });
});

test('a ready target Network tunnel is reused without changing its pointer or spawning', async (t) => {
  const options = await fixture(t);
  const path = await pointer(options, '42314\n');
  const before = await readFile(path);
  let launched = false;
  const result = await startTunnel({ ...options, request: async () => registry(), launch: async () => { launched = true; } });
  assert.equal(result.reused, true);
  assert.equal(result.wireless_prerequisites_ready, true);
  assert.equal(result.started, false);
  assert.equal(launched, false);
  assert.deepEqual(await readFile(path), before);
  assert.equal(JSON.stringify(result).includes(udid), false);
});

test('a safe root-owned read-only pointer can reuse an existing Network registry', async (t) => {
  const options = await fixture(t);
  const path = await pointer(options);
  const inspectPointer = (home, uid) => inspectStrongbox(home, uid, { openFile: async (...args) => {
    const handle = await open(...args);
    return { close: () => handle.close(), read: (...values) => handle.read(...values),
      stat: async () => { const details = await handle.stat(); details.uid = 0; return details; } };
  } });
  const readOnly = await inspectPointer(options.userHome, options.uid);
  assert.equal(readOnly.state, 'valid');
  assert.equal(readOnly.writable, false);
  const result = await startTunnel({ ...options, inspectPointer, request: async () => registry(),
    launch: async () => { throw Error('must not start'); } });
  assert.equal(result.reused, true);
  assert.equal(result.wireless_prerequisites_ready, true);
  assert.equal(await readFile(path, 'utf8'), '42314\n');
  const inactive = await inspectTunnel({ ...options, inspectPointer });
  assert.equal(inactive.can_start, true);
  let launched = false;
  const restarted = await startTunnel({ ...options, inspectPointer,
    launch: async () => { launched = true; return { ok: true, started: true }; } });
  assert.equal(restarted.started, true);
  assert.equal(launched, true);
  assert.equal(await readFile(path, 'utf8'), '42314\n');
});

test('USB tunnels and attached USB cannot be mislabeled as wireless', async (t) => {
  const options = await fixture(t);
  await pointer(options);
  const usbTunnel = await inspectTunnel({ ...options, request: async () => registry(entry('USB')) });
  assert.equal(usbTunnel.tunnel_registry.state, 'USB');
  assert.equal(usbTunnel.can_start, false);
  assert.equal(usbTunnel.wireless_prerequisites_ready, false);
  const attached = { ...options, listDevices: async () => [device(), device('USB')] };
  await assert.rejects(startTunnel(attached), { code: 'usb_connected' });
  const reuse = await startTunnel({ ...attached, request: async () => registry() });
  assert.equal(reuse.reused, true);
  assert.equal(reuse.wireless_prerequisites_ready, false);
  assert.ok(reuse.blockers.includes('usb_connected'));
});

test('registry readiness validates target, private address, RSD port and catalog', async () => {
  for (const change of [{ udid: 'other' }, { address: '127.0.0.1' }, { address: '2001:db8::1' },
    { address: undefined }, { rsdPort: 0 }, { rsdPort: '123' }, { services: {} }, { services: [] }]) {
    const result = await inspectRegistry(udid, { state: 'valid', port: 42314 }, async () => registry({ ...entry(), ...change }));
    assert.equal(result.state, 'invalid');
    assert.equal(result.catalog_ready, false);
  }
  const unknown = await inspectRegistry(udid, { state: 'valid', port: 42314 }, async () => registry({ ...entry(), connectionType: 'Other' }));
  assert.equal(unknown.state, 'unknown');
});

test('existing registries and uncertain endpoints are never replaced', async (t) => {
  const options = await fixture(t);
  await pointer(options);
  for (const response of [
    { kind: 'registry', value: { tunnels: {} } },
    { kind: 'invalid', reason: 'unexpected_http_status' },
    { kind: 'unreachable', reason: 'timeout' },
  ]) {
    const result = await inspectTunnel({ ...options, request: async () => response });
    assert.equal(result.can_start, false);
    assert.equal(result.reusable, false);
    await assert.rejects(startTunnel({ ...options, request: async () => response }));
  }
  await rm(strongboxPaths(options.userHome).pointer);
  const orphan = await inspectTunnel({ ...options, request: async () => registry() });
  assert.ok(orphan.blockers.includes('registry_pointer_missing'));
  assert.equal(orphan.reusable, false);
});

test('Strongbox preparation creates only missing private items and preserves existing contents', async (t) => {
  const options = await fixture(t);
  await prepareStrongbox({ ...options, expectedPort: 42314 });
  const { directory, pointer: path } = strongboxPaths(options.userHome);
  assert.equal((await stat(directory)).mode & 0o777, 0o700);
  assert.equal((await stat(path)).mode & 0o777, 0o600);
  await writeFile(path, ' 42314\n');
  await prepareStrongbox({ ...options, expectedPort: 42314 });
  assert.equal(await readFile(path, 'utf8'), ' 42314\n');
  await writeFile(path, 'not-a-port');
  await assert.rejects(prepareStrongbox({ ...options, expectedPort: 42314 }), { code: 'pointer_unsafe' });
  assert.equal(await readFile(path, 'utf8'), 'not-a-port');
});

test('unsafe permissions, ownership and symlink pointers are rejected without repair', async (t) => {
  const options = await fixture(t);
  const path = await pointer(options);
  await chmod(path, 0o666);
  assert.equal((await inspectStrongbox(options.userHome)).state, 'invalid');
  const report = await inspectTunnel(options);
  assert.ok(report.blockers.includes('pointer_unsafe'));
  assert.equal(report.blockers.includes('existing_registry'), false);
  await assert.rejects(prepareStrongbox({ ...options, expectedPort: 42314 }));
  assert.equal((await stat(path)).mode & 0o777, 0o666);
  await chmod(path, 0o600);
  assert.equal((await inspectStrongbox(options.userHome, options.uid + 1)).state, 'invalid');
  await rm(path);
  const unrelated = join(options.root, 'unrelated');
  await writeFile(unrelated, '42314\n');
  await symlink(unrelated, path);
  assert.equal((await inspectStrongbox(options.userHome)).state, 'invalid');
  await assert.rejects(prepareStrongbox({ ...options, expectedPort: 42314 }));
  assert.equal(await readFile(unrelated, 'utf8'), '42314\n');
});

test('USB inspection resolves helper entries containing spaces and URL punctuation', async (t) => {
  const options = await fixture(t);
  const special = join(options.root, 'helper with # and %', 'fixture.mjs');
  await mkdir(dirname(special));
  await writeFile(special, `export async function createUsbmux(options) {
    if (options.socketPath !== '/var/run/usbmuxd') throw Error('Unexpected socket');
    return { listDevices: async () => ${JSON.stringify([device()])}, close: async () => {} };
  }`);
  assert.deepEqual(await inspectUsb(udid, { entryPath: special }), { checked: true, connected: false, state: 'Network' });
});

test('foreground start requires confirmed Network visibility and an interactive owner', async (t) => {
  const options = await fixture(t);
  for (const change of [{ interactive: false }, { listDevices: async () => [] },
    { listDevices: async () => { throw Error('unknown'); } }]) {
    await assert.rejects(startTunnel({ ...options, ...change }));
    await assert.rejects(stat(join(options.userHome, 'Library')), { code: 'ENOENT' });
  }
  let invocation;
  const result = await startTunnel({ ...options, launch: async (args) => { invocation = sudoInvocation(args); return { ok: true, started: true }; } });
  assert.equal(result.started, true);
  assert.equal(invocation.command, '/usr/bin/sudo');
  assert.equal(invocation.args[0], '--preserve-env=HOME');
  assert.ok(invocation.args.includes(join(options.directory, remote, 'scripts/tunnel-creation.mjs')));
  assert.deepEqual(invocation.args.slice(-6), ['--udid', udid, '--tunnel-registry-port', '42314', '--reconnect-retries', '3']);
  assert.deepEqual(Object.keys(invocation.options.env).sort(), ['HOME', 'LANG', 'PATH']);
  assert.equal(invocation.options.env.HOME, options.userHome);
  assert.equal(await readFile(strongboxPaths(options.userHome).pointer, 'utf8'), '');
});

test('a service appearing during preparation blocks the foreground spawn', async (t) => {
  const options = await fixture(t);
  let count = 0; let launched = false;
  await assert.rejects(startTunnel({ ...options,
    request: async () => ++count === 1 ? { kind: 'absent' } : { kind: 'registry', value: { tunnels: {} } },
    launch: async () => { launched = true; } }), { code: 'prerequisites_changed' });
  assert.equal(launched, false);
});

test('wrong configuration or a moved helper version blocks setup', async (t) => {
  const options = await fixture(t);
  await writeFile(options.paths.config, JSON.stringify({ udid, wda_transport: 'default', use_preinstalled_wda: false }));
  await assert.rejects(startTunnel(options), { code: 'configuration_required' });
  await writeFile(options.paths.config, JSON.stringify({ udid, wda_transport: 'coredevice', use_preinstalled_wda: true }));
  await writeFile(join(options.directory, remote, 'package.json'), JSON.stringify({ name: 'appium-ios-remotexpc', version: '999' }));
  await assert.rejects(startTunnel(options), { code: 'runtime_missing' });
});

test('local registry GET rejects redirects, oversized data and incomplete replies', async (t) => {
  let mode = 'valid'; let methods = [];
  const server = createServer((req, res) => {
    methods.push(req.method + ' ' + req.url);
    if (mode === 'redirect') { res.writeHead(302, { Location: 'http://example.invalid/' }); res.end(); }
    else if (mode === 'large') res.end('x'.repeat(1000));
    else if (mode === 'hang') { res.writeHead(200); res.write('{'); }
    else res.end(JSON.stringify(registry().value));
  });
  await new Promise((done) => server.listen(0, '127.0.0.1', done));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const port = server.address().port;
  assert.equal((await readRegistry(port)).kind, 'registry');
  mode = 'redirect'; assert.equal((await readRegistry(port)).kind, 'invalid');
  mode = 'large'; assert.equal((await readRegistry(port, { maxBytes: 40 })).reason, 'oversized_response');
  mode = 'hang'; assert.equal((await readRegistry(port, { timeout: 30 })).kind, 'unreachable');
  assert.ok(methods.every((value) => value === 'GET /remotexpc/tunnels'));
});

test('foreground sudo output hides identifiers, preserves its prompt and retains a private local log', async (t) => {
  const options = await fixture(t);
  const fake = new EventEmitter(); fake.stdout = new PassThrough(); fake.stderr = new PassThrough(); fake.kill = () => {};
  const output = []; const errors = [];
  let spawned;
  const ready = new Promise((done) => { spawned = done; });
  const result = launchForeground({ ...options, helper: { helperPath: '/fixture/helper.mjs' }, udid, port: 42314,
    spawnProcess: () => { spawned(); return fake; }, output: (text) => output.push(text), errorOutput: (text) => errors.push(text) });
  await ready;
  fake.stdout.write(`Discovered ${udid}\n`);
  fake.stderr.write('iphone use administrator ');
  fake.stderr.write('password: ');
  fake.stderr.write(`Failed for device ${udid}\n`);
  fake.emit('close', 1, null);
  assert.equal((await result).ok, false);
  assert.equal([...output, ...errors].join('').includes(udid), false);
  assert.ok(errors.includes('iphone use administrator password: '));
  const logPath = join(dirname(options.paths.log), 'tunnel.log');
  assert.equal((await stat(logPath)).mode & 0o777, 0o600);
  assert.ok((await readFile(logPath, 'utf8')).includes(`Failed for device ${udid}`));
});

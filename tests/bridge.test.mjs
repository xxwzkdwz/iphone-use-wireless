import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { chmod, copyFile, mkdir, mkdtemp, readFile, realpath, rename, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { runInNewContext } from 'node:vm';
import {
  bridgeDirectory, childEnvironment, delay, driverRegistrationStatus, ensureDriverRegistration,
  installationStatus, pathsFor, prepareState,
  readConfig, requestControl, withOperationLock, writePrivateJson,
} from '../bridge/runtime.mjs';
import { status, start, stop } from '../bridge/cli.mjs';
import { supervise } from '../bridge/supervisor.mjs';
import {
  applyIPhoneBridgePatches, patchIPhoneControlStartup, patchRemoteXPCFacade,
  wirelessCleanupPatch, remoteXPCTunnelForwardingPatch,
} from '../scripts/iphone-control-patches.mjs';

async function fixture(t) {
  const home = await mkdtemp(join(await realpath('/tmp'), 'vico-iphone-test-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const paths = pathsFor({ VICO_IPHONE_HOME: home });
  await prepareState(paths);
  return { home, paths };
}
async function waitFor(check, label, timeout = 5_000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await check()) return; await delay(30); }
  throw new Error('Timed out: ' + label);
}
function alive(pid) { try { process.kill(pid, 0); return true; } catch { return false; } }

async function runtimeFixture(t) {
  const { home, paths } = await fixture(t);
  const plugin = join(home, 'plugin');
  const directory = join(plugin, 'bridge');
  for (const name of ['runtime.mjs', 'cli.mjs', 'supervisor.mjs', 'bridge-runner.cjs', 'xcodebuild-spawn.cjs', 'xcodebuild-guardian.cjs', 'package.json']) {
    await mkdir(directory, { recursive: true });
    await copyFile(join(bridgeDirectory, name), join(directory, name));
  }
  await mkdir(join(plugin, 'scripts'));
  for (const name of ['iphone-control-patches.mjs', 'install-bridge.mjs']) {
    await copyFile(join(bridgeDirectory, '../scripts', name), join(plugin, 'scripts', name));
  }
  for (const name of ['node_modules/appium-xcuitest-driver/package.json', wirelessCleanupPatch.target, remoteXPCTunnelForwardingPatch.target]) {
    await mkdir(dirname(join(directory, name)), { recursive: true });
    await copyFile(join(bridgeDirectory, name), join(directory, name));
  }
  const require = createRequire(join(bridgeDirectory, 'node_modules/appium/package.json'));
  await symlink(dirname(require.resolve('yaml/package.json')), join(directory, 'node_modules/yaml'));
  await mkdir(join(directory, 'node_modules/appium'));
  await writeFile(join(directory, 'node_modules/appium/package.json'), JSON.stringify({ name: 'appium', version: '3.7.0', type: 'commonjs' }));
  await writeFile(join(directory, 'node_modules/appium/index.js'), `
const fs = require('node:fs'); const path = require('node:path');
exports.main = async () => {
  const root = process.cwd(); const args = process.argv.slice(2);
  const record = path.join(root, 'calls.jsonl');
  const log = (value) => fs.appendFileSync(record, JSON.stringify(value) + '\\n');
  if (args[0] === 'driver') {
    if (JSON.stringify(args) !== JSON.stringify(['driver','list','--installed','--json'])) throw Error('Unexpected extension command');
    if (process.env.APPIUM_HOME !== root || process.env.OPENAI_API_KEY || process.env.NODE_OPTIONS) throw Error('Unsafe child environment');
    log({kind:'registration', home:process.env.APPIUM_HOME});
    const mode = fs.existsSync(path.join(root, 'mode')) ? fs.readFileSync(path.join(root, 'mode'), 'utf8') : '';
    if (mode === 'fail') throw Error('Synthetic CLI failure');
    const pkg = JSON.parse(fs.readFileSync(path.join(root, 'node_modules/appium-xcuitest-driver/package.json')));
    const {driverName, ...meta} = pkg.appium;
    const driver = {...meta, pkgName:pkg.name, version:pkg.version, installType:'npm', installSpec:pkg.name+'@'+pkg.version,
      installPath:mode === 'stale' ? '/old/runtime/node_modules/appium-xcuitest-driver' : path.join(root, 'node_modules/appium-xcuitest-driver')};
    const manifest = path.join(root, 'node_modules/.cache/appium/extensions.yaml');
    fs.mkdirSync(path.dirname(manifest), {recursive:true});
    fs.writeFileSync(manifest, JSON.stringify({schemaRev:4,drivers:{xcuitest:driver},plugins:{}}));
    console.log(JSON.stringify({xcuitest:{...driver,installed:true}}));
    return;
  }
  log({kind:'server'});
  require('node:http').createServer((req,res) => {
    log({kind:'request',url:req.url});
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({value:{ready:true,build:{version:'3.7.0'}}}));
  }).listen(Number(args[args.indexOf('--port')+1]), '127.0.0.1');
};
if (require.main === module) exports.main().catch(() => { process.exitCode = 1; });
`);
  const manifest = join(directory, 'node_modules/.cache/appium/extensions.yaml');
  return { home, paths, plugin, directory, manifest };
}

test('driver registration is read-only until repaired by the official CLI in the current root', async (t) => {
  const { directory, manifest } = await runtimeFixture(t);
  assert.equal((await installationStatus(directory)).reason, 'driver_registration_invalid');
  await assert.rejects(stat(manifest), { code: 'ENOENT' });
  assert.deepEqual(await ensureDriverRegistration(directory), { registered: true, rebuilt: true });
  const before = await readFile(manifest, 'utf8');
  assert.deepEqual(await ensureDriverRegistration(directory), { registered: true, rebuilt: false });
  assert.equal(await readFile(manifest, 'utf8'), before);
  assert.equal((await installationStatus(directory)).installed, true);
  const calls = (await readFile(join(directory, 'calls.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(calls, [{ kind: 'registration', home: directory }]);
});

test('moving a complete runtime repairs its old registration before a fake Appium start', async (t) => {
  const { home, paths, plugin, directory } = await runtimeFixture(t);
  await ensureDriverRegistration(directory);
  const movedPlugin = join(home, 'moved');
  await rename(plugin, movedPlugin);
  const moved = join(movedPlugin, 'bridge');
  assert.equal((await installationStatus(moved)).reason, 'driver_registration_invalid');
  assert.equal((await driverRegistrationStatus(moved)).registered, false);
  const listener = createServer();
  await new Promise((r) => listener.listen(0, '127.0.0.1', r));
  const port = listener.address().port;
  await new Promise((r) => listener.close(r));
  await writePrivateJson(paths.config, { port });
  const cli = await import(pathToFileURL(join(moved, 'cli.mjs')));
  t.after(() => cli.stop(paths).catch(() => {}));
  const started = await cli.start(paths);
  assert.equal(started.ready, true);
  assert.equal(started.owned, true);
  const manifest = join(moved, 'node_modules/.cache/appium/extensions.yaml');
  const valid = await readFile(manifest, 'utf8');
  const stale = JSON.parse(valid);
  stale.drivers.xcuitest.installPath = join(directory, 'node_modules/appium-xcuitest-driver');
  await writeFile(manifest, JSON.stringify(stale));
  const unhealthy = await cli.status(paths);
  assert.equal(unhealthy.running, true);
  assert.equal(unhealthy.ready, false, 'an HTTP-ready server with invalid driver registration must fail readiness');
  assert.equal(unhealthy.reason, 'driver_registration_invalid');
  await assert.rejects(cli.start(paths), { code: 'runtime_verification_failed' });
  assert.equal((await driverRegistrationStatus(moved)).registered, false, 'never rewrite registration under a running bridge');
  assert.equal((await cli.stop(paths)).stopped, true);
  const calls = (await readFile(join(moved, 'calls.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(calls.filter((call) => call.kind === 'registration').map((call) => call.home), [directory, moved]);
  assert.equal(calls.filter((call) => call.kind === 'server').length, 1);
  assert.ok(calls.filter((call) => call.kind === 'request').every((call) => call.url === '/status'));
});

test('install repairs malformed registration without npm or an Appium server', async (t) => {
  const { paths, plugin, directory, manifest } = await runtimeFixture(t);
  await mkdir(dirname(manifest), { recursive: true });
  await writeFile(manifest, 'drivers: [invalid yaml');
  assert.equal((await installationStatus(directory)).runtime_valid, true);
  const { install } = await import(pathToFileURL(join(plugin, 'scripts/install-bridge.mjs')));
  const result = await install(paths);
  assert.equal(result.installed, true);
  assert.equal(result.registration_rebuilt, true);
  assert.equal(result.phone_session_created, false);
  assert.equal((await install(paths)).already_installed, true);
  const calls = (await readFile(join(directory, 'calls.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(calls.map((call) => call.kind), ['registration']);
});

test('failed or incorrect registry generation is not accepted as installed', async (t) => {
  const { directory } = await runtimeFixture(t);
  for (const mode of ['fail', 'stale']) {
    await writeFile(join(directory, 'mode'), mode);
    await assert.rejects(ensureDriverRegistration(directory), { code: 'driver_registration_failed' });
    assert.equal((await installationStatus(directory)).installed, false);
  }
});

test('private configuration defaults to isolated port and rejects public server URLs', async (t) => {
  const { paths } = await fixture(t);
  assert.equal((await readConfig(paths)).port, 4737);
  await writePrivateJson(paths.config, { port: 0 });
  await assert.rejects(readConfig(paths), { code: 'invalid_port' });
  await writePrivateJson(paths.config, { port: 4737, appium_server_url: 'http://192.168.0.1:4737' });
  await assert.rejects(readConfig(paths), { code: 'invalid_endpoint' });
  assert.equal((await stat(paths.home)).mode & 0o777, 0o700);
  assert.equal((await stat(paths.config)).mode & 0o777, 0o600);
});

test('child environment is an allowlist and does not inherit provider credentials or preloads', () => {
  process.env.OPENAI_API_KEY = 'fixture-key';
  process.env.NODE_OPTIONS = '--require=/untrusted/file';
  process.env.HTTPS_PROXY = 'https://user:secret@proxy.invalid';
  const previousTunnelFlag = process.env.VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING;
  process.env.VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING = '1';
  try {
    const env = childEnvironment();
    for (const key of ['OPENAI_API_KEY', 'NODE_OPTIONS', 'HTTPS_PROXY', 'VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING']) assert.equal(env[key], undefined);
    assert.equal(env.APPIUM_HOME, bridgeDirectory);
    assert.equal(env.APPIUM_XCUITEST_PREFER_DEVICECTL, '1');
    assert.equal(env.VICO_IPHONE_PRESERVE_WDA_APPS, '1');
  } finally {
    delete process.env.OPENAI_API_KEY; delete process.env.NODE_OPTIONS; delete process.env.HTTPS_PROXY;
    if (previousTunnelFlag === undefined) delete process.env.VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING;
    else process.env.VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING = previousTunnelFlag;
  }
});

test('unowned listeners are reported and neither start nor stop signals them', async (t) => {
  const { paths } = await fixture(t);
  const listener = createServer((socket) => socket.end('unrelated listener'));
  await new Promise((r) => listener.listen(0, '127.0.0.1', r));
  t.after(() => new Promise((r) => listener.close(r)));
  await writePrivateJson(paths.config, { port: listener.address().port });
  const initial = await status(paths);
  assert.equal(initial.state, 'external_listener');
  assert.equal(initial.ready, false);
  await assert.rejects(start(paths), { code: 'port_occupied' });
  await assert.rejects(stop(paths), { code: 'ownership_unverified' });
  assert.equal(listener.listening, true);
});

test('preinstalled CoreDevice configuration reaches the child as tunnel-only forwarding', async (t) => {
  const { home, paths } = await fixture(t);
  const fake = join(home, 'fake-runner.cjs');
  const record = join(home, 'forwarding.json');
  await writeFile(fake, `require('node:fs').writeFileSync(${JSON.stringify(record)},JSON.stringify({tunnel:process.env.VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING}));process.on('SIGTERM',()=>process.exit(0));setInterval(()=>{},1000);\n`);
  const supervisor = await supervise({ paths,
    config: { port: 4737, wda_transport: 'coredevice', use_preinstalled_wda: true },
    token: 'a'.repeat(64), instance: 'b'.repeat(32), runnerPath: fake });
  t.after(() => supervisor.stop());
  await waitFor(async () => { try { await stat(record); return true; } catch { return false; } }, 'child forwarding environment');
  assert.equal(JSON.parse(await readFile(record, 'utf8')).tunnel, '1');
  for (const config of [{}, { wda_transport: 'coredevice' },
    { wda_transport: 'default', use_preinstalled_wda: true },
    { wda_transport: 'coredevice', use_preinstalled_wda: false }]) {
    assert.equal(childEnvironment(config).VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING, undefined);
  }
});

test('a remembered live PID never grants stop authority', async (t) => {
  const { paths } = await fixture(t);
  await writePrivateJson(paths.descriptor, { pid: process.pid, token: 'a'.repeat(64), identity: 'wrong' });
  await assert.rejects(stop(paths), { code: 'ownership_unverified' });
  assert.equal(alive(process.pid), true);
});

test('operation locks refuse concurrency and clean up even when callback fails', async (t) => {
  const { paths } = await fixture(t);
  await withOperationLock(paths, async () => {
    await assert.rejects(withOperationLock(paths, async () => {}), { code: 'operation_in_progress' });
  });
  await assert.rejects(withOperationLock(paths, async () => { throw Object.assign(new Error('callback failure'), { code: 'EEXIST' }); }), /callback failure/);
  assert.equal(await withOperationLock(paths, async () => 'released'), 'released');
});

test('authenticated supervisor only stops its actual harmless child', async (t) => {
  const { home, paths } = await fixture(t);
  const fake = join(home, 'fake-runner.cjs');
  await writeFile(fake, "process.on('SIGTERM',()=>process.exit(0));setInterval(()=>{},1000);\n");
  const external = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore' });
  t.after(() => external.kill('SIGKILL'));
  const supervisor = await supervise({ paths, config: { port: 4737 }, token: 'a'.repeat(64), instance: 'b'.repeat(32), runnerPath: fake });
  t.after(() => supervisor.stop());
  await delay(100);
  assert.equal((await requestControl(paths, supervisor.owner, 'status')).running, true);
  assert.equal(await requestControl(paths, { ...supervisor.owner, token: 'c'.repeat(64) }, 'stop', 100), null);
  assert.equal(alive(supervisor.child.pid), true);
  assert.ok(await requestControl(paths, supervisor.owner, 'stop'));
  await supervisor.whenClosed;
  assert.equal(alive(supervisor.child.pid), false);
  assert.equal(alive(external.pid), true);
  await assert.rejects(stat(paths.descriptor), { code: 'ENOENT' });
});

test('supervisor confirms registered guardian cleanup before reporting closed', async (t) => {
  const { home, paths } = await fixture(t);
  const fakeXcode = join(home, 'xcodebuild');
  const childRecord = join(home, 'child.json');
  await writeFile(fakeXcode, `#!${process.execPath}\nrequire('node:fs').writeFileSync(${JSON.stringify(childRecord)},JSON.stringify({pid:process.pid}));process.on('SIGTERM',()=>{});setInterval(()=>{},1000);\n`);
  await chmod(fakeXcode, 0o700);
  const runner = join(home, 'fake-runner.cjs');
  await writeFile(runner, `require(${JSON.stringify(join(bridgeDirectory, 'xcodebuild-spawn.cjs'))}).installXcodebuildGuard();require('node:child_process').spawn(${JSON.stringify(fakeXcode)},[],{stdio:'ignore'});process.on('SIGTERM',()=>process.exit(0));\n`);
  const supervisor = await supervise({ paths, config: { port: 4737 }, token: 'a'.repeat(64), instance: 'b'.repeat(32), runnerPath: runner });
  t.after(() => supervisor.stop());
  await waitFor(async () => { try { await stat(childRecord); return true; } catch { return false; } }, 'owned xcode child');
  const { pid } = JSON.parse(await readFile(childRecord, 'utf8'));
  await supervisor.stop();
  await supervisor.whenClosed;
  assert.equal(alive(pid), false);
});

test('pinned patches are idempotent and reject mutation outside the replacement', async () => {
  const installation = await installationStatus();
  assert.equal(installation.installed, true, 'run scripts/install-bridge.mjs before runtime tests');
  for (const [patch, patcher] of [[wirelessCleanupPatch, patchIPhoneControlStartup], [remoteXPCTunnelForwardingPatch, patchRemoteXPCFacade]]) {
    const applied = await readFile(join(bridgeDirectory, patch.target), 'utf8');
    const clean = applied.replace(patch.replacement, patch.original);
    assert.equal(patcher(clean, '12.11.0'), applied);
    assert.equal(patcher(applied, '12.11.0'), applied);
    assert.throws(() => patcher(applied + '\n// unexpected mutation', '12.11.0'), /checksum/);
    assert.throws(() => patcher(clean, '12.12.0'), /requires/);
  }
  const first = await applyIPhoneBridgePatches(bridgeDirectory, { verifyOnly: true });
  const second = await applyIPhoneBridgePatches(bridgeDirectory, { verifyOnly: true });
  assert.deepEqual(first, second);
});

test('wireless cleanup validates all uninstall candidates before changing any app', async () => {
  const removed = [];
  const cleanup = runInNewContext('(' + wirelessCleanupPatch.replacement + ')', {
    process: { platform: 'darwin', env: { APPIUM_XCUITEST_PREFER_DEVICECTL: '1' } },
    WDA_CF_BUNDLE_NAME: 'WebDriverAgentRunner-Runner', isLocalHost: () => false,
  });
  const app = (id) => ({ name: 'WebDriverAgentRunner-Runner', bundleIdentifier: id, builtByDeveloper: true, removable: true, defaultApp: false, internalApp: false });
  const driver = { isRealDevice: () => true, wda: { wdaBaseUrl: 'http://192.168.1.4' }, log: { info() {} }, device: {
    devicectl: { listApps: async () => [app('com.test.old'), { ...app('com.apple.system'), defaultApp: true }], execute: async (args) => removed.push(args) },
  } };
  await assert.rejects(cleanup(driver, ['com.test.current']), /unverified/);
  assert.deepEqual(removed, []);
});

test('standalone cleanup preserves every existing WDA runner without device calls', async () => {
  let deviceCalls = 0;
  const cleanup = runInNewContext('(' + wirelessCleanupPatch.replacement + ')', {
    process: { platform: 'darwin', env: { VICO_IPHONE_PRESERVE_WDA_APPS: '1', APPIUM_XCUITEST_PREFER_DEVICECTL: '1' } },
  });
  const driver = { log: { info() {} }, device: new Proxy({}, { get() { deviceCalls++; throw new Error('Unexpected device call'); } }) };
  await cleanup(driver, ['com.vicoiphone.runner']);
  assert.equal(deviceCalls, 0);
});

test('guardian cleans only owned fake xcodebuild after an abrupt bridge death', async (t) => {
  const { home } = await fixture(t);
  for (const name of ['bridge-runner.cjs', 'xcodebuild-spawn.cjs', 'xcodebuild-guardian.cjs']) await copyFile(join(bridgeDirectory, name), join(home, name));
  const fake = join(home, 'xcodebuild');
  await writeFile(fake, `#!${process.execPath}\nconsole.log(JSON.stringify({kind:'xcode',pid:process.pid}));process.on('SIGTERM',()=>{});setInterval(()=>{},1000);\n`);
  await chmod(fake, 0o700);
  await mkdir(join(home, 'node_modules/appium'), { recursive: true });
  await writeFile(join(home, 'node_modules/appium/index.js'), `exports.main=async()=>{const {spawn}=await import('node:child_process');const c=spawn(${JSON.stringify(fake)},[],{detached:true,stdio:['ignore','pipe','pipe']});console.log(JSON.stringify({kind:'guardian',pid:c.pid}));c.stdout.pipe(process.stdout);c.stderr.pipe(process.stderr);process.once('SIGTERM',()=>process.exit(0));};\n`);
  const bridge = spawn(process.execPath, [join(home, 'bridge-runner.cjs')], { stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
  t.after(() => { if (bridge.exitCode === null && bridge.signalCode === null) bridge.kill('SIGKILL'); });
  let output = '';
  bridge.stdout.on('data', (data) => { output += data; });
  const events = () => output.split('\n').filter(Boolean).flatMap((line) => { try { return [JSON.parse(line)]; } catch { return []; } });
  await waitFor(() => events().some((e) => e.kind === 'xcode'), 'fake xcodebuild readiness');
  const xcode = events().find((e) => e.kind === 'xcode').pid;
  const guardian = events().find((e) => e.kind === 'guardian').pid;
  bridge.kill('SIGKILL');
  await waitFor(() => !alive(xcode) && !alive(guardian), 'owned process group exit');
});

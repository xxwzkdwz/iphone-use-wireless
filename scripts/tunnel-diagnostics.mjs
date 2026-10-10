#!/usr/bin/env node
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { childEnvironment, pathsFor, readConfig } from '../bridge/runtime.mjs';

const run = promisify(execFile);
async function check(command, args) {
  try {
    const { stdout } = await run(command, args, { timeout: 5_000, maxBuffer: 16_384, env: childEnvironment() });
    return { available: true, summary: stdout.trim().slice(0, 2_000) };
  } catch { return { available: false }; }
}
try {
  if (process.argv.slice(2).some((arg) => arg !== '--json')) throw new Error('Usage: node scripts/tunnel-diagnostics.mjs --json');
  const config = await readConfig(pathsFor());
  const [xcode, devicectl] = await Promise.all([
    check('/usr/bin/xcodebuild', ['-version']), check('/usr/bin/xcrun', ['--find', 'devicectl']),
  ]);
  process.stdout.write(JSON.stringify({ ok: true, read_only: true, xcode, devicectl,
    configured_transport: config.wda_transport || 'default',
    use_preinstalled_wda: config.use_preinstalled_wda === true,
    privileged_tunnel_installed_by_plugin: false,
    tunnel_registry_checked: false,
    preinstalled_remote_xpc_requires_separate_tunnel: true,
    limitation: 'This host check does not inspect tunnel health. For the tested preinstalled wireless path, prepare the separate foreground RemoteXPC tunnel described in docs/WIRELESS_SETUP.en.md; this plugin does not install a privileged background service.',
    device_reachability_verified: false, phone_session_created: false,
  }) + '\n');
} catch (error) {
  process.stdout.write(JSON.stringify({ ok: false, error: { code: 'diagnostic_failed', message: error.message } }) + '\n');
  process.exitCode = 1;
}

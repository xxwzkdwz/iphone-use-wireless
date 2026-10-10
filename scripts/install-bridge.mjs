#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { open } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  BridgeError, bridgeDirectory, childEnvironment, ensureDriverRegistration, installationStatus,
  ownedSupervisor, pathsFor, versions, withOperationLock,
} from '../bridge/runtime.mjs';
import { applyIPhoneBridgePatches } from './iphone-control-patches.mjs';

function assertNodeVersion() {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (!(major >= 24 || major === 22 && minor >= 12 || major === 20 && minor >= 19)) {
    throw new BridgeError('unsupported_node', 'Use Node 20.19+, Node 22.12+, or Node 24+ with npm 10+.');
  }
}
export async function install(paths = pathsFor()) {
  assertNodeVersion();
  return withOperationLock(paths, async () => {
    const installed = await installationStatus();
    if (installed.installed) return { ok: true, ...installed, already_installed: true, phone_session_created: false };
    if (await ownedSupervisor(paths)) throw new BridgeError('bridge_running', 'Stop this plugin bridge before installing its runtime.');
    // An exact older pinned patch can be upgraded without reinstalling npm
    // dependencies. Unknown checksums fall through to a clean lockfile install.
    try {
      await applyIPhoneBridgePatches(bridgeDirectory);
    } catch { /* A clean npm ci below verifies the original pinned artifacts. */ }
    const repaired = await installationStatus();
    if (repaired.runtime_valid) {
      const registration = await ensureDriverRegistration();
      const verified = await installationStatus();
      if (!verified.installed) throw new BridgeError('runtime_verification_failed', 'Pinned runtime verification failed after registration repair.');
      return { ok: true, ...verified, patches_updated: true,
        registration_rebuilt: registration.rebuilt, phone_session_created: false };
    }
    const logPath = join(dirname(paths.log), 'install.log');
    const log = await open(logPath, 'a', 0o600);
    await log.chmod(0o600);
    try {
      const executable = join(dirname(process.execPath), 'npm');
      const npm = spawn(executable, ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], {
        cwd: bridgeDirectory, env: { ...childEnvironment(), npm_config_cache: join(paths.home, 'npm-cache') },
        stdio: ['ignore', log.fd, log.fd],
      });
      await new Promise((resolveInstall, reject) => {
        npm.once('error', reject);
        npm.once('exit', (code) => code === 0 ? resolveInstall() : reject(new BridgeError('npm_install_failed', `npm ci failed; inspect ${logPath}`)));
      });
      await applyIPhoneBridgePatches(bridgeDirectory);
      const registration = await ensureDriverRegistration();
      const verified = await installationStatus();
      if (!verified.installed) throw new BridgeError('runtime_verification_failed', 'Pinned runtime verification failed after installation.');
      return { ok: true, ...verified, registration_rebuilt: registration.rebuilt,
        appium_home: bridgeDirectory, log_path: logPath, phone_session_created: false };
    } finally { await log.close(); }
  });
}
async function main() {
  try {
    if (process.argv.slice(2).some((option) => option !== '--json')) throw new BridgeError('usage', 'Usage: node scripts/install-bridge.mjs --json');
    process.stdout.write(JSON.stringify(await install()) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, versions, error: { code: error.code || 'install_failed', message: error.message } }) + '\n');
    process.exitCode = 1;
  }
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) await main();

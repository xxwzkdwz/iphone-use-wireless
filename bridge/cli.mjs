#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { open, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  BridgeError, bridgeDirectory, childEnvironment, delay, ensureDriverRegistration, installationStatus,
  limitations, ownedSupervisor, pathsFor, portAvailable, probeAppium,
  processIdentity, readConfig, readOwner, requestControl, withOperationLock,
} from './runtime.mjs';

export async function status(paths = pathsFor()) {
  const config = await readConfig(paths);
  const [installation, owned] = await Promise.all([installationStatus(), ownedSupervisor(paths)]);
  const port = owned?.response.port ?? config.port;
  const ready = Boolean(installation.installed && owned?.response.running && await probeAppium(port));
  const available = owned ? false : await portAvailable(port);
  return { ok: true, ...installation, running: Boolean(owned?.response.running),
    owned: Boolean(owned), ready, port, endpoint: `http://127.0.0.1:${port}`,
    configured_port: config.port, pid: owned?.owner.pid ?? null,
    child_pid: owned?.response.child_pid ?? null,
    state: owned ? ready ? 'ready' : 'starting_or_unhealthy' : available ? 'stopped' : 'external_listener',
    external_listener: !owned && !available,
    state_home: paths.home, config_path: paths.config, log_path: paths.log,
    appium_home: bridgeDirectory, limitations,
  };
}
export async function start(paths = pathsFor()) {
  return withOperationLock(paths, async () => {
    const current = await status(paths);
    if (current.owned) {
      if (current.port !== current.configured_port) throw new BridgeError('configuration_changed', 'Stop the owned bridge before changing its port.');
      if (!current.installed) throw new BridgeError('runtime_verification_failed', 'The running bridge runtime is invalid. Stop this owned bridge before reinstalling its runtime.');
      return { ...current, already_running: true };
    }
    if (current.external_listener) throw new BridgeError('port_occupied', 'The configured port is occupied by an unowned listener; nothing was stopped.');
    if (!current.runtime_valid) throw new BridgeError('runtime_not_installed', 'Run node scripts/install-bridge.mjs --json first.');
    const previous = await readOwner(paths);
    if (previous && await processIdentity(previous.pid)) {
      throw new BridgeError('ownership_unverified', 'A recorded process is still alive but ownership could not be verified; nothing was stopped.');
    }
    await ensureDriverRegistration();
    // Remove only dead state files at fixed paths in this private state home.
    await rm(paths.descriptor, { force: true });
    await rm(paths.socket, { force: true });
    const log = await open(paths.log, 'a', 0o600);
    await log.chmod(0o600);
    const token = randomBytes(32).toString('hex');
    const instance = randomBytes(16).toString('hex');
    let child;
    try {
      child = spawn(process.execPath, [join(bridgeDirectory, 'supervisor.mjs'), paths.home, token, instance],
        { cwd: bridgeDirectory, env: childEnvironment(), detached: true, stdio: ['ignore', log.fd, log.fd] });
      await new Promise((resolveSpawn, reject) => { child.once('spawn', resolveSpawn); child.once('error', reject); });
      child.unref();
    } finally { await log.close(); }
    const deadline = Date.now() + 25_000;
    while (Date.now() < deadline) {
      const result = await status(paths);
      if (result.ready) return { ...result, started: true };
      if (child.exitCode !== null || child.signalCode !== null) break;
      await delay(200);
    }
    const owned = await ownedSupervisor(paths);
    if (owned?.owner.instance === instance) await requestControl(paths, owned.owner, 'stop');
    throw new BridgeError('startup_failed', 'Appium did not become ready within 25 seconds. Inspect the private bridge log; no phone session was requested.');
  });
}
export async function stop(paths = pathsFor()) {
  return withOperationLock(paths, async () => {
    const owned = await ownedSupervisor(paths);
    if (!owned) {
      const result = await status(paths);
      if (result.external_listener) throw new BridgeError('ownership_unverified', 'The listener is not owned by this plugin; nothing was stopped.');
      const previous = await readOwner(paths);
      if (previous && await processIdentity(previous.pid)) {
        throw new BridgeError('ownership_unverified', 'The recorded process is alive but ownership could not be verified; nothing was stopped.');
      }
      return { ...result, stopped: true, had_owned_process: false };
    }
    const response = await requestControl(paths, owned.owner, 'stop');
    if (!response) throw new BridgeError('stop_unconfirmed', 'The owned supervisor did not acknowledge stop.');
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      if (!(await processIdentity(owned.owner.pid))) {
        return { ...(await status(paths)), running: false, owned: true, stopped: true, had_owned_process: true };
      }
      await delay(100);
    }
    throw new BridgeError('stop_unconfirmed', 'The owned supervisor did not exit within the shutdown deadline.');
  });
}
export async function main(args = process.argv.slice(2)) {
  try {
    const [command, ...options] = args;
    if (!['status', 'start', 'stop'].includes(command) || options.some((option) => option !== '--json')) {
      throw new BridgeError('usage', 'Usage: node bridge/cli.mjs status|start|stop --json');
    }
    const result = await ({ status, start, stop })[command]();
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: { code: error.code || 'bridge_error', message: error.message } }) + '\n');
    process.exitCode = 1;
  }
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) await main();

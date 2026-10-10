#!/usr/bin/env node
import { DirectError, DirectRuntime, finalizeFailure, inspectHost, readConfig } from './runtime.mjs';

// Reserve stdout exclusively for the single machine-readable readiness receipt.
// Upstream libraries may use console or process.stdout; keep their logs private.
const writeReceipt = process.stdout.write.bind(process.stdout);
process.stdout.write = process.stderr.write.bind(process.stderr);
const emit = (value) => writeReceipt(`${JSON.stringify(value)}\n`);
process.env.APPIUM_IOS_REMOTEXPC_LOG_LEVEL = 'error';

const args = process.argv.slice(2);
if (args.length === 1 && args[0] === '--check') {
  const status = await inspectHost();
  emit(status);
  process.exitCode = status.ok ? 0 : 1;
} else if (args.length) {
  const { receipt } = await finalizeFailure(new DirectError('invalid_arguments', 'Usage: node direct/runner.mjs [--check]'));
  emit(receipt);
  process.exitCode = 1;
} else {
  let runtime;
  let stopRequested = false;
  let receiptWritten = false;
  let resolveStop;
  const stop = new Promise((resolve) => { resolveStop = resolve; });
  const requestStop = () => { stopRequested = true; if (runtime) runtime.closed = true; resolveStop(); };
  process.stdin.on('end', requestStop);
  process.stdin.on('error', requestStop);
  process.stdin.resume();
  process.once('SIGTERM', requestStop);
  process.once('SIGINT', requestStop);
  // A final hard deadline prevents open third-party sockets from orphaning this
  // helper. A nonzero exit means the parent must retain uncertain device ownership.
  let shutdownTimer;
  stop.then(() => { shutdownTimer = setTimeout(() => process.exit(2), 15_000); shutdownTimer.unref(); });
  let exitCode = 0;
  let startupFailure;
  try {
    const host = await inspectHost();
    if (!host.ok) throw new DirectError('prerequisites_missing', `Direct-WDA prerequisites are incomplete: ${host.blockers.join(', ')}.`);
    const config = await readConfig();
    const remote = await import('appium-ios-remotexpc');
    if (stopRequested) throw new DirectError('cancelled', 'The owning controller disconnected before startup.');
    runtime = new DirectRuntime(config, remote);
    const readiness = await runtime.start();
    if (stopRequested) throw new DirectError('cancelled', 'The owning controller disconnected during startup.', 'unknown');
    emit(readiness); receiptWritten = true;
    await stop;
  } catch (cause) {
    startupFailure = cause;
    exitCode = 1;
  } finally {
    requestStop();
    if (startupFailure && !receiptWritten) {
      const final = await finalizeFailure(startupFailure, runtime);
      exitCode = final.exitCode;
      emit(final.receipt); receiptWritten = true;
    } else {
      try { await runtime?.close(); }
      catch { exitCode = 2; }
    }
    clearTimeout(shutdownTimer);
    process.stdin.pause();
    process.exit(exitCode);
  }
}

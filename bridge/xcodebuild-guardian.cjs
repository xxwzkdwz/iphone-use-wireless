"use strict";

const { spawn } = require("node:child_process");
const { basename } = require("node:path");

// This process is launched as a detached group leader with an IPC connection
// to Appium. Its xcodebuild child stays in this group. Appium's own exit (even
// SIGKILL) closes IPC, so a cached WDA cannot outlive its controlling bridge.
const [command, ...args] = process.argv.slice(2);
if (!process.connected || !command || basename(command) !== "xcodebuild") process.exit(2);

let stopping = false;
let child;
let forceTimer;
function killOwnedGroup(signal) {
  // Our own PID cannot be reused while this guardian is running. No lookup or
  // device-wide process matching is needed to identify the owned group.
  try { process.kill(-process.pid, signal); }
  catch (error) {
    if (error.code !== "ESRCH") process.exitCode = 1;
    if (child && child.exitCode === null && child.signalCode === null) child.kill(signal);
  }
}

function stop(signal = "SIGTERM") {
  if (stopping) return;
  stopping = true;
  // Keep the guardian alive for the bounded group cleanup even if xcodebuild
  // exits first; any remaining children still belong to this exact group.
  forceTimer = setTimeout(() => killOwnedGroup("SIGKILL"), 1000);
  killOwnedGroup(signal);
}

process.on("SIGTERM", () => stop("SIGTERM"));
process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGHUP", () => stop("SIGHUP"));
process.once("disconnect", () => stop());
// Parent death may close stdout/stderr before Node delivers IPC disconnect.
// Treat that case as shutdown instead of crashing and abandoning xcodebuild.
process.stdout.on("error", () => stop());
process.stderr.on("error", () => stop());

child = spawn(command, args, { detached: false, stdio: ["inherit", "pipe", "pipe"] });
child.stdout.pipe(process.stdout, { end: false });
child.stderr.pipe(process.stderr, { end: false });
child.once("error", () => {
  if (!stopping) process.exitCode = 1;
});
child.once("close", (code, signal) => {
  if (stopping) return;
  clearTimeout(forceTimer);
  if (signal) {
    process.removeAllListeners(signal);
    process.kill(process.pid, signal);
  } else {
    // Allow buffered xcodebuild output to drain before the guardian exits.
    process.exitCode = code ?? 1;
    process.channel?.unref();
  }
});

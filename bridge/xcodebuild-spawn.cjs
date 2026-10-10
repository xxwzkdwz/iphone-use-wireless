"use strict";

const childProcess = require("node:child_process");
const { syncBuiltinESMExports } = require("node:module");
const { basename, join } = require("node:path");

/** Guard only xcodebuild children actually created by this bridge process. */
function installXcodebuildGuard() {
  const originalSpawn = childProcess.spawn;
  const owned = new Set();
  childProcess.spawn = function guardedSpawn(command, args, options) {
    if (typeof command !== "string" || basename(command) !== "xcodebuild") {
      return originalSpawn.apply(this, arguments);
    }
    if (!Array.isArray(args)) {
      options = args;
      args = [];
    }
    options = options || {};
    const stdio = Array.isArray(options.stdio)
      ? options.stdio.slice()
      : Array(3).fill(options.stdio || "pipe");
    if (options.shell || stdio.length > 3 || stdio.includes("ipc")) {
      throw new Error("Unsupported xcodebuild spawn options for the managed iPhone bridge");
    }
    while (stdio.length < 3) stdio.push("pipe");
    const guardian = originalSpawn(process.execPath,
      [join(__dirname, "xcodebuild-guardian.cjs"), command, ...args], {
        ...options, detached: true, stdio: [...stdio, "ipc"],
      });
    owned.add(guardian);
    // The standalone supervisor observes these exact children at shutdown.
    // It never uses this notification to signal a remembered numeric PID.
    if (guardian.pid && process.connected && process.send) {
      process.send({ type: "vico-xcode-guardian", pid: guardian.pid }, () => {});
    }
    // Keep the ChildProcess identity until its observed exit. A numeric PID
    // discovered by scanning, or one remembered after exit, is never used.
    guardian.once("exit", () => owned.delete(guardian));
    guardian.once("error", () => { if (!guardian.pid) owned.delete(guardian); });
    guardian.kill = (signal = "SIGTERM") => {
      if (!owned.has(guardian) || !guardian.pid || guardian.exitCode !== null || guardian.signalCode !== null) return false;
      try {
        // The guardian is our live detached group leader. Include its actual
        // xcodebuild child when callers escalate directly to SIGKILL.
        process.kill(-guardian.pid, signal);
        return true;
      } catch (error) {
        if (error.code === "ESRCH") return false;
        throw error;
      }
    };
    return guardian;
  };
  // Appium/teen_process import the builtin's named ESM spawn export.
  syncBuiltinESMExports();
}

module.exports = { installXcodebuildGuard };

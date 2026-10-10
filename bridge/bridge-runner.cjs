#!/usr/bin/env node
"use strict";

require("./xcodebuild-spawn.cjs").installXcodebuildGuard();
// The standalone supervisor owns this IPC link. Its death must also stop
// Appium and the separately guarded xcodebuild descendants.
process.once("disconnect", () => {
  setTimeout(() => process.exit(1), 8_000).unref();
  process.kill(process.pid, "SIGTERM");
});
const { main } = require("./node_modules/appium/index.js");
Promise.resolve(main()).catch(() => {
  process.stderr.write("iphone use bridge failed to start.\n");
  process.exitCode = 1;
  process.disconnect?.();
});

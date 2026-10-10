import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const driverVersion = "12.11.0";
const cleanupTarget = "node_modules/appium-xcuitest-driver/build/lib/commands/wda/startup.js";
const cleanupBeforeSha256 = "c2b6b0e019075f18f778d791133836300a4523b14658f31b56b728120a138cd1";
const remoteXPCTarget = "node_modules/appium-xcuitest-driver/build/lib/device/remote-xpc/facade.js";
const remoteXPCBeforeSha256 = "d1b6659caf18f2785b897b49afad2afc57553d41b78442aec21a75c7d0fb928b";

const original = String.raw`async function cleanupApps(driver, keepBundleIds = []) {
    const installedBundleIds = await driver.device.getUserInstalledBundleIdsByBundleName(WDA_CF_BUNDLE_NAME);
    const keep = new Set(keepBundleIds.filter(Boolean));
    for (const bundleId of installedBundleIds.filter((id) => !keep.has(id))) {
        driver.log.info(\`Removing WebDriverAgent runner app '\${bundleId}' \` + \`(CFBundleName '\${WDA_CF_BUNDLE_NAME}')\`);
        try {
            await driver.device.removeApp(bundleId);
        }
        catch (e) {
            driver.log.warn(\`Failed to remove WebDriverAgent apps: \${e.message}\`);
        }
    }
}`.replaceAll("\\`", "`").replaceAll("\\${", "${");

// The driver otherwise asks InstallationProxy to enumerate old WDA runners
// before xcodebuild starts. A device visible only to CoreDevice cannot answer
// that usbmux request. Keep the original path for USB/local WDA sessions.
const networkBranch = String.raw`
    if (process.platform === "darwin" && driver.isRealDevice() &&
        ["1", "true", "yes"].includes(String(process.env.APPIUM_XCUITEST_PREFER_DEVICECTL ?? "").toLowerCase()) &&
        !isLocalHost(driver.wda.wdaBaseUrl)) {
        const apps = await driver.device.devicectl.listApps();
        if (!Array.isArray(apps) || apps.some((app) => !app ||
            typeof app.name !== "string" || app.name.length === 0 ||
            typeof app.bundleIdentifier !== "string" ||
            !/^[A-Za-z0-9_.-]+$/.test(app.bundleIdentifier))) {
            throw new Error("Invalid devicectl app list while preparing wireless WebDriverAgent");
        }
        if (!Array.isArray(keepBundleIds) || keepBundleIds.some((id) => typeof id !== "string")) {
            throw new Error("Invalid WebDriverAgent bundle preservation list");
        }
        const keep = new Set(keepBundleIds.filter(Boolean));
        const candidates = apps.filter((app) => app.name === WDA_CF_BUNDLE_NAME && !keep.has(app.bundleIdentifier));
        // Validate every candidate before the first uninstall. CoreDevice must
        // explicitly identify these as removable developer apps, never system apps.
        if (candidates.some((app) => app.builtByDeveloper !== true || app.removable !== true ||
            app.defaultApp !== false || app.internalApp !== false)) {
            throw new Error("Refusing to remove an unverified WebDriverAgent candidate");
        }
        for (const bundleId of new Set(candidates.map((app) => app.bundleIdentifier))) {
            driver.log.info("Removing obsolete wireless WebDriverAgent runner app '" + bundleId + "'");
            await driver.device.devicectl.execute(["device", "uninstall", "app", bundleId], {timeout: 30000});
        }
        return;
    }`;

const previousCleanupReplacement = original.replace("async function cleanupApps(driver, keepBundleIds = []) {",
  "async function cleanupApps(driver, keepBundleIds = []) {" + networkBranch);
// A separately installed plugin must never uninstall the main VICO app's WDA
// runner or any other owner's runner. The plugin always sets this flag. WDA
// signing/bundle isolation is configured separately by the owning controller.
const preserveBranch = String.raw`
    if (process.env.VICO_IPHONE_PRESERVE_WDA_APPS === "1") {
        driver.log.info("Preserving all installed WebDriverAgent runners for the standalone iPhone plugin");
        return;
    }`;

export const wirelessCleanupPatch = Object.freeze({
  id: "xcuitest-12.11.0-standalone-preserve-wda-cleanup-v2",
  target: cleanupTarget,
  before_sha256: cleanupBeforeSha256,
  original,
  replacement: original.replace("async function cleanupApps(driver, keepBundleIds = []) {",
    "async function cleanupApps(driver, keepBundleIds = []) {" + preserveBranch + networkBranch),
});

const remoteXPCOriginal =
  "        this.useUsbMuxPath = await isDeviceListedInUsbmux(loadedModule, this.udid, this.log);";

// The upstream driver prefers its usbmux connector whenever the device appears
// in usbmuxd, even when a healthy RemoteXPC tunnel is already registered. VICO's
// production bridge explicitly opts into deterministic tunnel forwarding so a
// listed-but-unreachable USB path cannot silently replace connectViaTunnel.
const remoteXPCReplacement = String.raw`        this.useUsbMuxPath = process.env.VICO_XCUITEST_REQUIRE_TUNNEL_FORWARDING !== "1" &&
            await isDeviceListedInUsbmux(loadedModule, this.udid, this.log);`;

export const remoteXPCTunnelForwardingPatch = Object.freeze({
  id: "xcuitest-12.11.0-require-remotexpc-tunnel-forwarding-v1",
  target: remoteXPCTarget,
  before_sha256: remoteXPCBeforeSha256,
  original: remoteXPCOriginal,
  replacement: remoteXPCReplacement,
});

export function patchIPhoneControlStartup(text, version) {
  if (version !== driverVersion) throw new Error("The wireless WDA patch requires XCUITest " + driverVersion);
  if (text.split(wirelessCleanupPatch.replacement).length === 2) {
    const clean = text.replace(wirelessCleanupPatch.replacement, original);
    if (hash(clean) === cleanupBeforeSha256) return text;
  }
  // Upgrade only the exact previous pinned patch, never a merely similar file.
  if (text.split(previousCleanupReplacement).length === 2) {
    const clean = text.replace(previousCleanupReplacement, original);
    if (hash(clean) === cleanupBeforeSha256) text = clean;
  }
  if (hash(text) !== cleanupBeforeSha256) throw new Error("The wireless WDA patch input checksum does not match the pinned driver");
  if (text.split(original).length !== 2) throw new Error("The wireless WDA cleanup patch must match exactly once");
  return text.replace(original, wirelessCleanupPatch.replacement);
}

export function patchRemoteXPCFacade(text, version) {
  if (version !== driverVersion) throw new Error("The RemoteXPC forwarding patch requires XCUITest " + driverVersion);
  if (text.split(remoteXPCReplacement).length === 2) {
    const clean = text.replace(remoteXPCReplacement, remoteXPCOriginal);
    if (hash(clean) === remoteXPCBeforeSha256) return text;
  }
  if (hash(text) !== remoteXPCBeforeSha256) {
    throw new Error("The RemoteXPC forwarding patch input checksum does not match the pinned driver");
  }
  if (text.split(remoteXPCOriginal).length !== 2) {
    throw new Error("The RemoteXPC forwarding patch must match exactly once");
  }
  return text.replace(remoteXPCOriginal, remoteXPCReplacement);
}

/** Accept clean or exactly patched pinned files; reject all dependency drift. */
export async function applyIPhoneBridgePatches(sourceDirectory, { verifyOnly = false } = {}) {
  const pkg = JSON.parse(await readFile(join(sourceDirectory, "node_modules/appium-xcuitest-driver/package.json"), "utf8"));
  const cleanupFile = join(sourceDirectory, cleanupTarget);
  const remoteXPCFile = join(sourceDirectory, remoteXPCTarget);
  // Validate every pinned input before writing either output. A dependency drift
  // therefore leaves the staging tree untouched instead of half-patched.
  const [cleanupSource, remoteXPCSource] = await Promise.all([
    readFile(cleanupFile, "utf8"), readFile(remoteXPCFile, "utf8"),
  ]);
  const cleanupPatched = patchIPhoneControlStartup(cleanupSource, pkg.version);
  const remoteXPCPatched = patchRemoteXPCFacade(remoteXPCSource, pkg.version);
  if (verifyOnly) {
    if (cleanupSource !== cleanupPatched || remoteXPCSource !== remoteXPCPatched) {
      throw new Error("Required iPhone bridge compatibility patches have not been applied");
    }
  } else {
    await Promise.all([
      writeFile(cleanupFile, cleanupPatched), writeFile(remoteXPCFile, remoteXPCPatched),
    ]);
  }
  return [
    { id: wirelessCleanupPatch.id, path: cleanupTarget,
      before_sha256: cleanupBeforeSha256, after_sha256: hash(cleanupPatched) },
    { id: remoteXPCTunnelForwardingPatch.id, path: remoteXPCTarget,
      before_sha256: remoteXPCBeforeSha256, after_sha256: hash(remoteXPCPatched) },
  ];
}

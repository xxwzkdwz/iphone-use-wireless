# Set up iphone-use-wireless from scratch

[Back to README](../README.en.md) · [中文](WIRELESS_SETUP.md)

After initial USB pairing and your own Apple signing, unplug the cable and operate your iPhone through a Mac on the same network. This guide uses **preinstalled WDA with CoreDevice / RemoteXPC**, requiring iOS 18+. Keep the Mac awake with Codex, Appium, and a foreground tunnel running, and keep the phone unlocked and reachable.

Give this guide to Codex running on your Mac. It can check the environment, help prepare software, and run installation and configuration commands. You provide the Mac, iPhone, and initial pairing cable, and complete Apple sign-in, phone trust and Developer Mode confirmations, and any required password entry locally. Full Xcode is required, but it can be prepared during setup; you do not need to configure every piece of software manually beforehand.

## 1. Get the source and prepare software

Run locally, or ask Codex to run:

```sh
git clone https://github.com/xxwzkdwz/iphone-use-wireless.git
cd iphone-use-wireless
bash scripts/setup.sh --check
```

`--check` inspects system software and plugin dependencies in this directory without installing, changing device configuration, or connecting the phone. Missing plugin dependencies are expected in a fresh clone. Resolve missing system software using the reported next steps:

- **Full Xcode**: Codex can inspect existing installations and guide download and installation. Launch Xcode once, complete component installation, and select it under **Settings → Locations → Command Line Tools**. Complete license or administrator prompts locally.
- **Runtime tools**: [uv](https://docs.astral.sh/uv/getting-started/installation/), [Node.js](https://nodejs.org/en/download), and npm 10+. uv prepares the required Python 3.11+ environment. Supported Node.js versions are `^20.19.0 || ^22.12.0 || >=24.0.0`. Codex can help install and check these tools.
- **Codex CLI**: `codex` must be available in the terminal. For another MCP client, use `--skip-plugin` without needing to install the Codex CLI.

Once system software is ready:

```sh
bash scripts/setup.sh
```

The script installs pinned Python and bridge dependencies, checks the environment, and registers the Codex plugin. It preserves device configuration and does not connect to or operate the phone. It does not install system software such as Xcode or Node.js; missing prerequisites produce a next step and a nonzero exit. `bash scripts/setup.sh --skip-plugin` prepares dependencies in the current copy without registering the plugin.

Run subsequent commands from the repository root unless a step explicitly refers to the installed Codex copy. Keep real device configuration, screenshots, and signing material in private local directories.

## 2. Sign in, trust, and pair over USB

Codex can guide these steps; you handle sign-in, the cable, and phone confirmations:

1. Sign in to your Apple Account under **Xcode Settings → Accounts**. Do not send passwords to Codex or export private keys.
2. Connect the unlocked iPhone by USB. Accept **Trust This Computer** and enter the passcode on the phone.
3. Open **Xcode Window → Devices and Simulators**; some newer versions use **Device Hub**. Select your phone, wait for pairing and development support preparation, and check your UDID and iOS version.
4. Enable **Settings → Privacy & Security → Developer Mode** on the phone and follow its restart and confirmation prompts. If the option is missing, initiate Xcode pairing first. See [Apple's Developer Mode guide](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device).
5. Enable **Show this iPhone when on Wi-Fi** in Finder's device page and apply the change. Enable **Connect via network** if Xcode exposes it; some versions no longer display a separate option.
6. Ensure the Mac and phone share a reachable network. Leave USB connected while preparing WDA.

See [Apple's wireless device guide](https://help.apple.com/xcode/mac/current/en.lproj/dev3e2f4ee6d.html). You do not need to arbitrarily switch the network currently in use.

## 3. Build and prepare WDA with your own team

WDA is a test helper installed on the phone. Use your own Apple signing configuration; you do not need third-party signing, someone else's signed app, certificates, or device settings. Xcode's free Personal Team can be used; see step 8 for signing expiration.

Open the project included with the installed dependencies:

```sh
open bridge/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj
```

Configure it in Xcode; Codex can help locate settings and troubleshoot build errors:

1. Select the **WebDriverAgentRunner** target, open **Signing & Capabilities**, enable **Automatically manage signing**, and select your Team.
2. Set a unique bundle identifier you can sign, such as `com.yourname.iphoneuse.WebDriverAgentRunner`, replacing `yourname`. Use that base ID for `--wda-bundle-id`; do not duplicate the `.xctrunner` suffix used for the test runner.
3. Resolve any signing errors for other affected targets using the same team.
4. Select the **WebDriverAgentRunner** scheme and your real phone as the destination, then run **Product → Test**. Complete developer trust or authorization prompts on the phone yourself.
5. Confirm that the runner built, installed, and started, then stop the Xcode test. Resolve any Xcode error before continuing.

This follows Appium's [manual signing workflow](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/full-manual-config/). For free-team bundle ID or provisioning errors, see [basic configuration](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/basic-manual-config/).

### Prepare the preinstalled copy with the script

Use **Show in Finder** on `WebDriverAgentRunner-Runner.app` in Xcode's products or build report to locate your original build. It is normally under that project's DerivedData `Build/Products/Debug-iphoneos/`.

List signing identities locally:

```sh
security find-identity -v -p codesigning
```

Select a valid **Apple Development** identity for the build's Xcode Team. Substitute its complete name or SHA-1 identifier below:

```sh
uv run --frozen python scripts/prepare-wda.py \
  --source '/absolute/path/to/original/WebDriverAgentRunner-Runner.app' \
  --identity '<YOUR_VALID_APPLE_DEVELOPMENT_IDENTITY>'
```

The source must already be signed and pass signature verification. The script copies it into a new private directory, removes only the copy's embedded `XC*` frameworks, signs nested XCTest bundles and the outer app, and checks the signature. It preserves the original and device configuration, following Appium's [preinstalled-WDA requirements](https://appium.github.io/appium-xcuitest-driver/latest/guides/run-preinstalled-wda/).

On success, `prepared_path` contains the absolute `.app` path needed below. The default location is a new directory under `${VICO_IPHONE_HOME:-~/.local/share/vico-iphone}/wda`; use `--output-root '/absolute/private/wda'` to choose another location. On signing failure, the script stops and removes its incomplete copy. This step does not install the app on the phone or establish a working phone connection.

## 4. Save device configuration

Inspect and reuse an existing working configuration. For first setup, replace the placeholders with your local values and use the previous step's `prepared_path`:

```sh
uv run --frozen iphone-use configure \
  --udid '<DEVICE_UDID>' \
  --team-id '<APPLE_TEAM_ID>' \
  --platform-version '<IOS_VERSION>' \
  --transport coredevice \
  --wda-bundle-id '<YOUR_WDA_BUNDLE_ID>' \
  --preinstalled-wda \
  --prebuilt-wda-path '/absolute/path/to/prepared/WebDriverAgentRunner-Runner.app'
uv run --frozen iphone-use doctor
```

`--preinstalled-wda` selects the preinstalled launch method. With `--prebuilt-wda-path`, Appium installs from the signed package when connecting, so retain that file. Omit the path only if the correct matching WDA is already installed. `doctor` checks the host and configuration; connect to the phone in step 7.

Private configuration is stored at `~/.local/share/vico-iphone/configuration/iphone-control.json`, or under `VICO_IPHONE_HOME`. Compatibility paths and the internal `iphone-use` CLI name remain unchanged. Appium defaults to `127.0.0.1:4737`.

## 5. Prepare the actual installed Codex copy

Step 1 registered the plugin. If the CLI directs you to install through the desktop app, select **iphone-use-wireless** in the local marketplace. Open a new chat, restarting the client if needed, and ask:

> Use the iphone-use-wireless setup skill to find the actual loaded plugin directory and prepare that copy with setup.sh --skip-plugin. Preserve my existing device configuration.

Codex may run a cached copy without the source checkout's dependencies. The setup skill resolves the actual plugin root from its own location and runs:

```sh
bash scripts/setup.sh --skip-plugin
```

This does not register the plugin again or alter device configuration. For a read-only check, use `bash scripts/setup.sh --check --skip-plugin`. Do not hard-code an old cache path. You can run the tunnel from the source checkout kept on your Mac.

## 6. Unplug and start the foreground wireless tunnel

1. End your existing wired session and Xcode WDA test. Stop a previous foreground tunnel you own with `Ctrl+C` in its terminal.
2. Unplug USB. Keep the phone unlocked and both devices on the same reachable network. Wait for Xcode to show the phone available wirelessly.
3. In a normal-user terminal at the source repository root, check readiness:

```sh
node scripts/start-tunnel.mjs --check --json
```

`--check` inspects configuration, network discovery, USB state, and actual tunnel registration. It creates no files and does not request sudo. Follow its next steps, then run:

```sh
node scripts/start-tunnel.mjs
```

The script reads device configuration and uses the project's pinned tunnel helper. It reports an existing usable Network tunnel for the target as reusable. Otherwise, it checks that USB is disconnected, the target is discoverable over the network, and the port is available before preparing registry files and starting a foreground tunnel.

**Run as your ordinary user; do not prefix the command with sudo.** The script invokes sudo when needed while preserving the correct HOME. Enter the administrator password directly in that terminal. Keep a newly started tunnel's terminal running and use `Ctrl+C` there when finished. The script installs no startup service and does not stop other programs.

From another terminal, check:

```sh
node scripts/tunnel-diagnostics.mjs --json
```

The target should have a **Network** registration with USB disconnected. Device discovery or a listening port alone does not establish tunnel readiness. Diagnostics do not create tunnels. The underlying helper prefers USB when USB and Network coexist, so end the wired session, unplug, and start a new wireless tunnel instead of relying on seamless switching. See [RemoteXPC wireless support](https://github.com/appium/appium-ios-remotexpc#iphone--ipad-over-wifi-usbmuxd-network-devices) and [Appium tunnel setup](https://appium.github.io/appium-xcuitest-driver/latest/guides/remotexpc-tunnels-real-devices/).

## 7. Connect and use the phone

Ask Codex:

> Use iphone-use-wireless to connect to my phone. USB is unplugged. Check the wireless tunnel, read the current UI components, and tell me whether the connection succeeded.

Codex checks status, starts the bridge if needed, and creates a device session. If connection returns pending with an `operation_id`, poll `connect_status` for that operation instead of connecting again. After success, use that session's `session_token` to read current components and perform your task.

Observe again after each action. Use screenshots only when components are unavailable or meaningful repeated attempts still cannot locate the target. Check uncertain outcomes before proceeding; do not repeat a send, tap, or purchase to test whether it worked. Disconnect your session when finished.

For daily use: unlock the phone on the same network → keep the Mac awake → run `node scripts/start-tunnel.mjs` → ask Codex to connect and act. Keep the tunnel running. Use the next section to recover after sleep, restart, network changes, or signing expiration.

## 8. Troubleshooting

| Symptom | Check or recovery |
| --- | --- |
| Setup reports missing Xcode or runtime tools | Follow the reported system-software steps. For missing plugin dependencies, run `bash scripts/setup.sh`; use `--skip-plugin` in a cache copy. |
| Phone disappears after unplugging | Check network reachability, Finder Wi-Fi visibility, Xcode pairing, and phone unlock. Guest-network isolation can block discovery; reconnect USB for pairing if needed. |
| Wired works, wireless disconnects | End your wired session and old tunnel, unplug, then run `start-tunnel.mjs` and check for Network registration. |
| Failure after sleep or network change | Wake the Mac, unlock the phone, check reachability, and restart your stale tunnel. |
| RemoteXPC unavailable | Run `start-tunnel.mjs --check --json` and follow the next step. Diagnostics do not create a tunnel. |
| Port 42314 or 4737 is occupied | Identify and coordinate with its owner; do not kill unrelated services. |
| Tunnel registry permission error | Inspect diagnostics, then check your Strongbox paths as described below. |
| WDA signing or launch failure | Check Developer Mode, team, base bundle ID, and provisioning. Repair the Xcode build and rerun `prepare-wda.py`. |
| Free-team app stops after several days | Personal Team device provisioning expires after seven days. Rebuild and prepare WDA, then update the configured copy path. See [Apple's account guide](https://developer.apple.com/help/account/basics/about-your-developer-account). |
| Prepared `.app` path no longer exists | End your session and rerun step 4 with a stable valid path. Do not bypass validation by editing JSON directly. |
| Plugin update reports missing dependencies | Run `bash scripts/setup.sh --skip-plugin` in the currently loaded copy, not an old cache directory. |
| Target moved or action result is uncertain | Observe again and inspect status. Do not replay uncertain actions or delete session locks. |

Before reconfiguration or dependency upgrades, end your device session and any tunnel using that directory. Preserve private configuration and your signed copy. Avoid simultaneous control by different controllers.

If tunnel startup fails, inspect the private log path printed in the terminal: `$VICO_IPHONE_HOME/logs/tunnel.log`, defaulting to `~/.local/share/vico-iphone/logs/tunnel.log`. Raw logs may contain device identifiers; do not paste them unchanged into public issues or documentation.

### Advanced: your own Strongbox permissions

The helper handles new registry files. A root-owned pointer that is safely readable by your ordinary user (for example, mode `0644`), inside a safe user-owned directory, is not an error. You can reuse an existing Network tunnel or restart when its registry port is free without chown; the sudo tunnel helper updates the existing pointer. Inspect permissions only when diagnostics report an unreadable pointer, unexpected directory owner, or a similar problem. Before a targeted repair, confirm that you created these paths, no other active service relies on them, and your own tunnel has stopped:

```sh
IPHONE_USE_REGISTRY_DIR="$HOME/Library/Application Support/appium-xcuitest-driver-nodejs/strongbox"
ls -ld "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
sudo chown "$(id -u):$(id -g)" "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
chmod 700 "$IPHONE_USE_REGISTRY_DIR"
chmod 600 "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
```

Do not recursively modify other Library or Appium paths. The ordinary-user plugin and sudo tunnel must share the registry under the same HOME; `start-tunnel.mjs` uses `sudo --preserve-env=HOME` to maintain this. Repeat step 6's read-only check and start command after repair.

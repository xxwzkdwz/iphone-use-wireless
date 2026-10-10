# iphone use

**After initial USB pairing and signing, let Codex operate your iPhone wirelessly through a Mac on the same network. You can disconnect the data cable for normal use.** Codex handles reasoning; Appium, XCUITest, and WebDriverAgent execute device operations. No VICO Core process or DeepSeek API key is required.

[Complete wireless setup guide](docs/WIRELESS_SETUP.en.md) · [中文](README.md) · [MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

The Mac must remain awake, the paired phone must be reachable, and an owner-started RemoteXPC tunnel must stay running. The plugin does not install a privileged tunnel daemon or include a cross-network relay. Follow the [complete guide](docs/WIRELESS_SETUP.en.md) for these prerequisites.

## Verified scope

Maintainer testing on **2026-10-10**, using **iPhone 16 / iOS 26.6.1 / Xcode 26.6**, recorded:

| Scenario | Result |
| --- | --- |
| Wireless session without USB data connection | Connected and read 51 UI components |
| Real app task | Opened an app and completed an add-to-cart flow |
| Targeting limitations | Two screenshot fallbacks; one unsuccessful action after a component moved |

This verifies one device environment. It does not establish universal app/version compatibility, entirely component-based operation, or a completed fresh-Mac onboarding test. New users must use their own signing and repeat the unplugged acceptance steps.

## Setup

Prepare macOS with full Xcode, Python 3.11+, uv, Node.js `^20.19.0 || ^22.12.0 || >=24.0.0`, npm 10+, and Codex with local MCP support. This guide's preinstalled-WDA wireless path requires iOS 18+. Follow the [complete guide](docs/WIRELESS_SETUP.en.md) for USB trust, Developer Mode, Apple signing, WDA preparation, and the foreground tunnel.

Get the source and dependencies:

```sh
git clone https://github.com/xxwzkdwz/iphone-use.git
cd iphone-use
uv sync --frozen
node scripts/install-bridge.mjs --json
```

After preparing your own signed WDA as described in the guide:

```sh
uv run --frozen iphone-use configure \
  --udid '<DEVICE_UDID>' \
  --team-id '<APPLE_TEAM_ID>' \
  --platform-version '<IOS_VERSION>' \
  --transport coredevice \
  --wda-bundle-id '<YOUR_WDA_BUNDLE_ID>' \
  --preinstalled-wda \
  --prebuilt-wda-path '/absolute/path/to/WebDriverAgentRunner-Runner.app'
uv run --frozen iphone-use doctor
bash scripts/install-codex.sh
```

Replace placeholders with your own device, signing team, and unique WDA bundle ID. The `.app` must be prepared and signed for your device; it is not a universal package shipped in this repository. If the correctly signed WDA is already installed, omit `--prebuilt-wda-path` while keeping `--preinstalled-wda`. Keep credentials and real device configuration out of source control.

The installer registers the local marketplace and installs the plugin when supported by your Codex CLI. Otherwise, install it through the desktop application's local marketplace. Start a new chat after installation and ask: “Use iphone use to check setup, connect my phone, and read the current screen.” The plugin includes `setup` and `iphone` skills.

The host may run a cached copy of the plugin. Dependencies installed in the source checkout do not prove that copy is ready. Run the setup skill after installation: it must resolve its actual plugin root, check dependencies there, and run that copy's `uv sync --frozen` and `node scripts/install-bridge.mjs --json` when needed. Private configuration is shared through `VICO_IPHONE_HOME`.

For direct stdio MCP registration:

```sh
codex mcp add iphone_use -- bash '/absolute/path/to/iphone-use/scripts/run-server.sh'
```

Use the actual absolute path, and avoid enabling both registrations. Other MCP clients can execute the same script. Starting MCP does not automatically connect or act on the phone.

## Operating contract

Tools: `iphone_status`, `iphone_setup`, `iphone_session`, `iphone_observe`, `iphone_act`, and `iphone_launch`.

An initial WDA build can take time. `iphone_session(action="connect")` may return pending with an `operation_id`; poll `action="connect_status"` with that same ID instead of reconnecting. Bounded calls avoid depending on unusually long host tool timeouts. Successful connection returns a `session_token`. Pass it unchanged to observation, action, launch, and disconnect calls. Do not share operation IDs or tokens between chats.

Actions also require a fresh `observation_id`. Observe components first, prefer current `element_ref` targets, perform one action, then observe again. Use screenshots only when components are unavailable or still fail after a meaningful repeated attempt. Never replay a mutation whose outcome is unknown.

Complete ordinary steps within the user's authorized task without repeated confirmation. Consequential actions require authorization covering that action; host approval policy still applies. Disconnect your session when finished.

## State and wireless use

Private state defaults to `~/.local/share/vico-iphone`; override it with `VICO_IPHONE_HOME`. Device configuration is `configuration/iphone-control.json` within that directory. The default Appium address is `127.0.0.1:4737`. The renamed plugin retains this private directory, environment variable, and existing WDA configuration for compatibility with established connections.

UI text and screenshots are returned to the current Codex conversation and follow the host's data policies. Never distribute state, screenshots, tokens, signing keys, or device configuration. Do not let another controller operate the same phone concurrently.

Wireless requires a previously paired, reachable device, an awake Mac, and the same reachable network. End your wired session, unplug USB, then start a fresh foreground tunnel using [step 6](docs/WIRELESS_SETUP.en.md#6-unplug-and-start-the-foreground-wireless-tunnel). Seamless switching during a live wired session is not guaranteed. Appium health and device discovery do not prove a working automation session.

```sh
node bridge/cli.mjs status --json
node scripts/tunnel-diagnostics.mjs --json
node bridge/cli.mjs start --json
node bridge/cli.mjs stop --json
```

Tunnel diagnostics are read-only and do not start the tunnel. Free Apple Personal Team provisioning expires after seven days and requires rebuilding/reinstalling; see [Apple's account guide](https://developer.apple.com/help/account/basics/about-your-developer-account). The [troubleshooting guide](docs/WIRELESS_SETUP.en.md#8-troubleshooting) covers this, sleep/disconnection, registry permissions, and stale cache or WDA paths.

## Packaging and distribution

```sh
uv run --frozen pytest -q
node --test tests/bridge.test.mjs
python3 scripts/package.py --check
python3 scripts/package.py
```

The allowlisted source ZIP includes tests and excludes installed dependencies, virtual environments, runtime state, secrets, screenshots, and Git history. Its checksum and embedded file inventory make the contents reviewable.

Portable manifests are `plugin.json` and `mcp.json`; the Codex compatibility manifests are `.codex-plugin/plugin.json` and `.mcp.json`. The repository marketplace points to `./` relative to the repository root. GitHub/local distribution is separate from publication in the official plugin directory; see [OpenAI's packaging guide](https://developers.openai.com/plugins/build/plugins). After updating a checkout, reinstall dependencies and refresh/reinstall the plugin because the host may run a cached copy.

For GitHub marketplace installation, run `codex plugin marketplace add xxwzkdwz/iphone-use --ref main`, then `codex plugin add iphone-use@iphone-use-local`, and complete the setup skill in the actual installed copy. See the [wireless guide](docs/WIRELESS_SETUP.en.md) for the full first-run procedure.

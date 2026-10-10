---
name: setup
description: Set up or troubleshoot iphone-use-wireless on a Mac, including software preparation, owner device pairing and signing, plugin cache dependencies, and wireless tunnel readiness. Use for first installation or connection failures.
---

# Set up iphone-use-wireless

Read the [wireless setup guide](../../docs/WIRELESS_SETUP.md), or its [English version](../../docs/WIRELESS_SETUP.en.md), for the relevant setup or recovery steps. Resolve the plugin root two directories above this skill's directory. An installed skill may run from a cache copy; do not substitute an old checkout or cache path.

The default backend connects directly to preinstalled WDA through CoreDevice / RemoteXPC. It does not run Appium Server or XCUITest Driver; Apple XCTest, WDA, and the `appium-ios-remotexpc` helper remain in use. Follow [upgrade and compatibility notes](../../docs/DIRECT_WDA_EXPERIMENT.md) for an older Appium installation.

Setup is a collaboration: Codex checks the environment and runs installation/configuration commands; the owner provides the Mac, iPhone, and first-pairing cable, and handles Apple authentication, phone trust and Developer Mode confirmations, and required passwords locally. Full Xcode is a runtime requirement that Codex can help the owner prepare during setup. Do not tell the owner that all software must be configured manually beforehand.

## Prepare software in the correct copy

1. Inspect existing configuration and status. Reuse working settings and establish the intended owner-authorized phone before changing device configuration.
2. In the actual installed root, run `bash scripts/setup.sh --check --skip-plugin`. It checks macOS, full initialized Xcode, uv, supported Node/npm, and that copy's runtime dependencies without changing files or connecting a phone. Missing runtime dependencies are normal in a fresh copy.
3. Help prepare missing system software using the script's next steps. `setup.sh` reports missing tools rather than installing Xcode, uv, or Node automatically. The owner completes installer/account/password prompts locally. uv supplies the project's Python 3.11+ environment; do not require a separate system-Python installation.
4. For the loaded plugin/cache copy, run `bash scripts/setup.sh --skip-plugin` to install dependencies without registering the plugin again. For a source checkout's first plugin installation, `bash scripts/setup.sh` also registers the plugin and requires Codex CLI on PATH. These commands preserve device configuration and do not connect or act on the phone.

For a requested plugin upgrade, prepare the updated source checkout with `bash scripts/setup.sh --skip-plugin`, then run `bash scripts/install-codex.sh --reinstall`. The installer uses official Codex plugin remove/add commands to refresh the cache and checks the installed version against the source manifest, leaving private device configuration intact. `setup.sh` does not forward `--reinstall`. Open a new chat and confirm the actual loaded copy's manifest version matches the intended release before using its setup instructions. A marketplace registration message or an unchanged cache does not confirm an upgrade. Manual MCP installations instead update their registered checkout and restart that MCP process.

Source-checkout dependencies may not be copied into the installed cache. Always resolve the loaded skill's root and prepare that copy. Default setup installs pinned dependencies under `direct/`; it does not install Appium Server or XCUITest Driver. `iphone_setup(start)` checks local readiness and does not install dependencies or start the phone runner; `iphone_session(connect)` starts the installed WDA and local forwarding. No VICO Core, separate model API, or old VICO tunnel service is required.

## Pair, sign, and configure

For a new phone, follow the guide's USB trust, Xcode pairing, and Developer Mode steps. Guide the owner through Apple login and phone confirmations; never request passwords, cookies, exported private keys, or certificates in chat.

Use the owner's Apple team and a unique signable WDA bundle ID. Open `direct/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj` (pinned WDA 16.12.5), then follow the guide's Xcode target/scheme steps to build and test WDA. Once the owner's signed build and matching local signing identity are known, prepare a copy:

```sh
uv run --frozen python scripts/prepare-wda.py \
  --source '/absolute/path/to/original/WebDriverAgentRunner-Runner.app' \
  --identity '<YOUR_VALID_APPLE_DEVELOPMENT_IDENTITY>'
```

The script validates the signed source, copies and prepares it, and verifies the new signature without changing the original or configuration. It does not choose a signing identity or install on the phone. Stop the owner's Xcode test, keep USB connected and the phone unlocked, and explicitly install the returned `prepared_path`:

```sh
xcrun devicectl device install app \
  --device '<DEVICE_UDID>' \
  '/absolute/path/from/prepared_path/WebDriverAgentRunner-Runner.app'
```

The owner completes any developer-trust/restart prompt. Then use the guide's `iphone-use configure` command with `--transport coredevice`, `--preinstalled-wda`, and the owner's base `--wda-bundle-id`. Do not pass `--prebuilt-wda-path` in direct mode or promise that connect installs the app: it only starts a matching WDA already on the phone. Do not copy device/team values from another project or reconfigure an existing working setup merely because the plugin was renamed or updated.

Default private state is `~/.local/share/iphone-use-wireless-direct`, with `configuration/iphone-control.json` and local forwarding port `18401`. `VICO_IPHONE_HOME` takes precedence. Preserve the prepared WDA at a stable private path outside the repository/cache.

For an existing Appium configuration, end its owned session and use `uv run --frozen iphone-use init-direct` once. This copies device configuration from `~/.local/share/vico-iphone`, changes the forwarding port, and does not copy sessions or overwrite existing direct configuration. Already migrated users should reuse their direct configuration rather than rerun migration. For custom directories use `--from-state` / `--state-dir` and set the matching `VICO_IPHONE_HOME` for later commands and MCP.

Only select legacy Appium when intended by the task: `bash scripts/setup.sh --backend appium --skip-plugin`, then set `IPHONE_USE_BACKEND=appium` for the MCP process and CLI. Legacy defaults remain `~/.local/share/vico-iphone` and port `4737`, with `VICO_IPHONE_HOME` still taking precedence. Its tunnel commands also use `--backend appium`. There is no automatic backend fallback.

## Start the wireless tunnel

End only the owner's wired session and owned foreground tunnel, then have the owner unplug USB. Keep the Mac awake, phone unlocked, and both devices on the same reachable network. The underlying helper prefers USB while connected; do not promise seamless cable removal during a live session or cross-network control.

Run `node scripts/start-tunnel.mjs --check --json` for a read-only configuration, USB, discovery, and registry check. It does not create a tunnel or request sudo. Follow its returned next step; a listening port or discovered device is not a usable target tunnel.

To start a tunnel, use `node scripts/start-tunnel.mjs` in the owner's normal-user terminal. Do not add an outer sudo. It reads the configured target, reuses a usable Network tunnel when present, or checks USB/network/port conditions before preparing registry files and invoking the pinned helper through sudo with HOME preserved. The owner enters any administrator password in that terminal and keeps the foreground process running. It installs no daemon and does not stop other services. Read the guide's targeted registry repair only if diagnostics identify that problem.

## Connect and verify

When the task includes connecting, check `iphone_status` / `iphone_setup(action="status")`, prepare missing dependencies or tunnel state, then call `iphone_session(action="connect")`. In default direct mode, setup start only checks readiness; do not wait for an Appium process or create one. With explicitly selected legacy Appium, use setup start to start its bridge if needed. If pending, keep its private `operation_id` and poll `iphone_session(action="connect_status", operation_id=...)` for the same attempt; do not start another connect.

Keep the successful `session_token` in this chat and pass it unchanged to observation, action, launch, and disconnect. Observe components first. Only report working wireless control after USB is unplugged, the target tunnel is Network, and a real session with a fresh observation succeeds. Host installation and tunnel readiness alone do not establish phone control. Use the `iphone` skill for daily tasks.

Continue authorized setup steps without repeated permission prompts. Owner-side interaction or essential missing values may require their input; setup does not authorize messages, purchases, or unrelated phone/network changes. Do not take over another task's session or stop its processes.

If a connection or action is unknown/uncertain, stop mutations and use supported status/observation recovery; do not replay it, repeatedly reconnect, or delete locks. For missing cache dependencies, run setup in the actual copy. For signature expiration or a missing installed WDA, end the owned session, repair the owner's build/prepared copy, and explicitly install it again. Update configuration only if device, team, or bundle ID changed. Disconnect the session when the requested verification is finished.

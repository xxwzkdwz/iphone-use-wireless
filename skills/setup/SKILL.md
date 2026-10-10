---
name: setup
description: Set up or diagnose iphone-use-wireless on a Mac, including USB pairing, owner Apple signing, prepared WDA, a foreground RemoteXPC tunnel, wireless operation after unplugging, and Codex cache dependencies. Use for initial setup or connection failures.
---

# iphone-use-wireless setup

iphone-use-wireless is an independent local stdio MCP server. Codex decides which actions to take; this package has no VICO Core or DeepSeek dependency. Read the repository `README.md` for commands. Resolve script paths from this skill's plugin root, two directories above this file's directory.

For first setup or wireless recovery, read the packaged [wireless setup guide](../../docs/WIRELESS_SETUP.md), or its [English version](../../docs/WIRELESS_SETUP.en.md). Follow its complete sequence: dependencies, USB trust/pairing and Developer Mode, owner signing, prepared WDA copy, configuration, actual installed runtime, foreground tunnel, and unplugged acceptance. Do not skip signing or assume an old VICO tunnel service exists.

The host may run an installed cache copy. Use the root resolved from this loaded skill, never assume a separately cloned source checkout is the runtime root. Neither source-checkout `.venv` nor `bridge/node_modules` is guaranteed to be copied during installation. Inspect and, when setup is authorized, install dependencies in the actual loaded plugin root. The external private state directory is independent of the plugin copy.

## Inspect before changing state

1. Call `iphone_status` and `iphone_setup(action="status")` if available. Otherwise run `uv run --project <plugin-root> iphone-use doctor`. These report different layers; a healthy Appium endpoint does not prove a working phone session.
2. Establish which Mac and owner-authorized iPhone are intended. If several devices are available, resolve the target before configuration. Never copy device identifiers or signing settings from another project, old screenshots, examples, or a different owner's setup.
3. Check macOS, Xcode, Python 3.11+, uv, supported Node.js and npm. The bridge supports Node.js 20.19+, 22.12+, or 24+ in the engine ranges declared by `bridge/package.json`, and npm 10+. The full Xcode app and its command-line tools are required.

## Prepare the device and dependencies

The initial device setup is owner-controlled: connect USB, trust the Mac, enable Developer Mode where required, pair through Xcode, and select the owner's Apple signing team. Direct the owner to Xcode for Apple authentication; never request passwords, session cookies, private signing keys, or certificates in chat.

When setup is authorized, install dependencies from the actual plugin root. If a working configuration already exists, inspect and preserve it; do not run a fresh configure command merely because the plugin was renamed or updated.

```sh
uv sync --frozen
node scripts/install-bridge.mjs --json
```

For a new wireless setup, guide the owner through Xcode signing and the guide's copied-app preparation first. Use a unique owner-signable WDA bundle ID. The `.app` must be the owner's correctly signed, prepared copy, with embedded XC* frameworks handled as documented and its signature verified; the repository supplies no universally signed WDA. Then configure:

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
```

Use actual owner-supplied values; the default bundle ID is not a universal signing identity. Preinstalled mode requires iOS 18+ and `coredevice`. Omit `--prebuilt-wda-path` only if the correct signed WDA is already installed; otherwise use a stable absolute path outside the repository/cache. Do not keep device values in release files. `VICO_IPHONE_HOME` selects private state, defaulting to `~/.local/share/vico-iphone`, with configuration under `configuration/iphone-control.json` and Appium port 4737. Preserve these compatibility paths and existing WDA configuration across the rename.

The dependency installer downloads packages and applies the package's local compatibility patches. `iphone_setup` manages the installed bridge and does not install dependencies. Starting MCP does not connect to a phone.

## Verify each boundary

1. For wireless acceptance, first end the owner's wired session and old foreground tunnel, unplug USB, and establish a fresh Network tunnel as described below. Then start the bridge with `iphone_setup(action="start")` or `node bridge/cli.mjs start --json`.
2. If the user authorized connecting, call `iphone_session(action="connect")`. A slow initial build may return a pending result and `operation_id`; poll `iphone_session(action="connect_status", operation_id=...)` until the operation completes, rather than calling connect again. Keep the successful result's `session_token` in this chat's working context. Pass it unchanged to observations, actions, launches, and disconnect. Never borrow another chat's operation ID or session token.
3. Observe components, and report whether a real device session and observation succeeded. Do not infer screenshot or action success from process health. Use the `iphone` skill for the subsequent task.
4. Disconnect your session when the requested verification or task is done. Do not stop a bridge or session owned by another task.

Once the task is authorized, proceed with ordinary steps without repeatedly asking permission. Ask only when essential information, owner-side Apple/device interaction, or an action beyond the authorized task is needed. Never use setup as authorization to send messages, make purchases, or alter unrelated device/network settings.

## Wireless and failures

Wireless use requires initial USB pairing/signing, a reachable iPhone, an awake Mac, and the same reachable network. The plugin does not provide a relay or install a privileged daemon. Use the pinned `appium-ios-remotexpc` foreground helper from the guide, with the owner entering sudo authentication directly in their terminal. Preserve only the current `HOME` with `sudo --preserve-env=HOME` so the tunnel and ordinary-user controller share the Strongbox registry. Prepare/check the user-owned readable registry pointer as documented; do not overwrite another task's active tunnel or recursively change Appium permissions.

The pinned helper prefers USB when both USB and Network entries exist, and lacks the newer `--watch-devices` flag. End the wired attempt first, unplug, then create a new wireless tunnel. Do not promise seamless cable removal during an active session. Keep the foreground terminal running; stop only that owned helper when appropriate.

Run `node scripts/tunnel-diagnostics.mjs --json` for read-only diagnostics. It does not start a tunnel. Confirm the intended registry entry is `Network`, USB is unplugged, a real session succeeds, and a fresh component observation succeeds. Do not expose registry identifiers in public logs. Do not switch networks or disable Wi-Fi as routine recovery.

For expiration, rebuild/reinstall and prepare WDA with the owner's team; free Personal Team provisioning expires after seven days. For moved/missing WDA paths, disconnect the owner's session and rerun the documented configure command with a valid stable path. For a new plugin cache, install dependencies in the currently loaded copy. The guide covers these steps.

If a result is marked uncertain or unknown, do not replay the action or clear the protective state to force progress. Inspect status and re-observe through the supported recovery path; report what is confirmed and what remains unknown. Do not kill unrelated Appium, Xcode, VICO, or phone processes.

The project records one successful unplugged device environment with component observations and real app operations, including screenshot fallbacks and an unsuccessful moved-component action. It does not establish compatibility with every new Mac, phone, network, or app. Verify the current owner's environment before claiming it works.

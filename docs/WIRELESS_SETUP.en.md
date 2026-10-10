# Set up iphone-use-wireless from scratch

[Back to README](../README.en.md) · [中文](WIRELESS_SETUP.md)

After initial USB pairing and your own Apple signing, disconnect the data cable and operate the iPhone through a Mac on the same reachable network. This guide uses **preinstalled WDA with CoreDevice / RemoteXPC**, requiring iOS 18+. The Mac still runs Codex, Appium, and a foreground tunnel; there is no cross-network relay.

Maintainers verified an unplugged session on iPhone 16 / iOS 26.6.1 / Xcode 26.6. A complete first-time setup on a separate fresh Mac has not been verified. Your Apple account, pairing, and signing remain local prerequisites.

## 1. Get the source and dependencies

Install full Xcode, launch it once, and complete component installation. Select it under **Xcode Settings → Locations → Command Line Tools**. Install Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/), [Node.js](https://nodejs.org/en/download), and npm 10+. Supported Node.js versions are `^20.19.0 || ^22.12.0 || >=24.0.0`.

```sh
git clone https://github.com/xxwzkdwz/iphone-use-wireless.git
cd iphone-use-wireless
uv sync --frozen
node scripts/install-bridge.mjs --json
uv run --frozen iphone-use doctor
```

At this stage, `configured: false` is expected. `full_xcode` should be true, and required tools should be present. Dependency installation does not connect the phone.

Commands below run from this repository root unless explicitly referring to the installed Codex copy. Keep device configuration, screenshots, and certificates outside the repository.

## 2. Sign in, trust, and pair over USB

1. Sign in to your own Apple Account in **Xcode Settings → Accounts**. Do not send passwords to Codex or export private keys.
2. Connect the unlocked iPhone by USB. Accept **Trust This Computer** on the phone and enter its passcode yourself.
3. Open Xcode's **Window → Devices and Simulators**; some newer versions use **Device Hub**. Select your phone and wait for pairing and development support preparation. Record your own UDID and iOS version.
4. Enable **Settings → Privacy & Security → Developer Mode** on the phone and follow its restart/confirmation prompts. If missing, initiate Xcode pairing first. See [Apple's Developer Mode guide](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device).
5. In Finder's device page, enable **Show this iPhone when on Wi-Fi**, then apply changes as prompted. Enable **Connect via network** if Xcode exposes that option; some versions no longer display it separately.
6. Put the Mac and phone on the same reachable network. Leave USB connected while preparing WDA.

See [Apple's wireless device guide](https://help.apple.com/xcode/mac/current/en.lproj/dev3e2f4ee6d.html). You do not need to disable or arbitrarily switch your existing network.

## 3. Build and install WDA with your own team

WDA is a device-side test helper. Use your own signing configuration rather than someone else's signed app, credentials, or device settings.

```sh
open bridge/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj
```

In Xcode:

1. Select the **WebDriverAgentRunner** target, open **Signing & Capabilities**, enable **Automatically manage signing**, and choose your Team.
2. Set a unique bundle identifier you can sign, such as `com.yourname.iphoneuse.WebDriverAgentRunner`, replacing `yourname`. Use this base identifier for `--wda-bundle-id`. Xcode normally adds `.xctrunner` for the test runner; do not add that suffix twice.
3. Resolve signing errors for any other affected targets with the same team.
4. Select the **WebDriverAgentRunner** scheme and your actual phone as the destination. Run **Product → Test**. Complete owner-side developer trust prompts on the phone.
5. Confirm the runner built, installed, and started. Stop this Xcode test before proceeding. If it fails, fix the Xcode error before retrying plugin connections.

This follows Appium's [manual signing workflow](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/full-manual-config/). For free-team provisioning errors, see its [basic manual configuration](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/basic-manual-config/).

### Prepare a signed copy for preinstalled mode

Find `WebDriverAgentRunner-Runner.app` through Xcode's products/build report and **Show in Finder**. It is normally under the project's DerivedData `Build/Products/Debug-iphoneos/`. Use your own actual build path.

For modern iOS, Appium's [preinstalled-WDA guidance](https://appium.github.io/appium-xcuitest-driver/latest/guides/run-preinstalled-wda/) calls for using the device's XCTest frameworks instead of embedded `XC*` frameworks. The successful maintainer setup used a copied app with those frameworks removed and its signature renewed. The following preparation procedure has **not been accepted end to end on a separate fresh Mac**; verify both the signature and your own device connection.

Copy your original build into a new private directory:

```sh
IPHONE_USE_WDA_SOURCE='/absolute/path/to/original/WebDriverAgentRunner-Runner.app'
IPHONE_USE_STATE_HOME="${VICO_IPHONE_HOME:-$HOME/.local/share/vico-iphone}"
mkdir -p "$IPHONE_USE_STATE_HOME/wda"
IPHONE_USE_WDA_DIR="$(mktemp -d "$IPHONE_USE_STATE_HOME/wda/prepared.XXXXXX")"
IPHONE_USE_WDA_APP="$IPHONE_USE_WDA_DIR/WebDriverAgentRunner-Runner.app"
ditto "$IPHONE_USE_WDA_SOURCE" "$IPHONE_USE_WDA_APP"
```

Remove only the direct `XC*` entries in the copy's `Frameworks` directory. Keep other frameworks and the original build:

```sh
python3 - "$IPHONE_USE_WDA_APP" <<'PY'
from pathlib import Path
import shutil
import sys

app = Path(sys.argv[1])
if app.name != 'WebDriverAgentRunner-Runner.app' or not app.is_dir():
    raise SystemExit('Expected the copied WDA .app directory')
for item in (app / 'Frameworks').glob('XC*'):
    if item.is_dir() and not item.is_symlink():
        shutil.rmtree(item)
    else:
        item.unlink()
    print('Removed from copy:', item.name)
PY
```

List available code-signing identities locally:

```sh
security find-identity -v -p codesigning
```

Select the valid **Apple Development** identity corresponding to your Xcode team. Insert its complete name or SHA-1 identifier below. Keep the list local; no private-key export is needed.

```sh
IPHONE_USE_SIGNING_IDENTITY='<YOUR_VALID_APPLE_DEVELOPMENT_IDENTITY>'
python3 - "$IPHONE_USE_WDA_APP" "$IPHONE_USE_SIGNING_IDENTITY" <<'PY'
from pathlib import Path
import subprocess
import sys

app = Path(sys.argv[1])
identity = sys.argv[2]
test_bundles = list((app / 'PlugIns').glob('*.xctest'))
if not test_bundles:
    raise SystemExit('No XCTest bundle found; check the original build product')
for bundle in [*test_bundles, app]:
    subprocess.run(['codesign', '--force', '--sign', identity,
                    '--preserve-metadata=identifier,entitlements', str(bundle)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)], check=True)
print('Prepared WDA:', app)
PY
```

This signs nested XCTest bundles before the outer app, preserving their identifiers and entitlements. Use the same signing team as the original provisioning profile. Stop on verification failure and repair the original build in Xcode. Keep the resulting absolute `.app` path for the next step; never commit the signed package or signing material.

## 4. Save your device configuration

Replace every placeholder with your local values:

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

`--preinstalled-wda` selects the preinstalled launch path. If a signed `.app` path is supplied, Appium installs from that file; keep it at a stable absolute location. Omit the path only when the correct WDA is already installed, with a matching bundle identifier.

Private configuration lives at `~/.local/share/vico-iphone/configuration/iphone-control.json`, or under `VICO_IPHONE_HOME`. These compatibility paths remain after the product rename. Appium defaults to `127.0.0.1:4737`. `doctor` checks the host; it does not verify a phone session.

## 5. Install the Codex plugin and prepare its actual copy

```sh
bash scripts/install-codex.sh
```

Open a new Codex chat; restart the desktop client if necessary. Ask:

> Use the iphone-use-wireless setup skill to find the actual loaded plugin directory and install missing dependencies there. Preserve my existing device configuration and do not operate the phone yet.

Codex may run a cached copy. Dependencies in the source checkout do not prove that copy is ready. Setup must resolve its root from the loaded `skills/setup/SKILL.md` and run these commands there:

```sh
uv sync --frozen
node scripts/install-bridge.mjs --json
uv run --frozen iphone-use doctor
node bridge/cli.mjs status --json
```

Do not hard-code another developer's cache path. Both copies read your private configuration, but only one controller should use the phone. You may run the tunnel from the stable source checkout kept on your Mac.

## 6. Unplug and start the foreground wireless tunnel

1. End your existing wired phone session and stop your Xcode WDA test. Stop a previous foreground tunnel in its own terminal with `Ctrl+C`.
2. Unplug the USB data cable. Keep the phone unlocked and both devices on the same reachable network. Wait for Xcode to show the phone available wirelessly.
3. Prepare the tunnel registry pointer as your normal user. If this location serves another active Appium tunnel, identify it before changing anything. The following creates only missing items and preserves existing contents:

```sh
python3 - <<'PY'
from pathlib import Path
import os
import stat

directory = Path.home() / 'Library/Application Support/appium-xcuitest-driver-nodejs/strongbox'
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
details = directory.lstat()
if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
    raise SystemExit('Registry directory must belong to the current user; inspect before repair')
pointer = directory / 'tunnelRegistryPort'
try:
    fd = os.open(pointer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    os.close(fd)
details = pointer.lstat()
if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
        or details.st_mode & 0o022 or not os.access(pointer, os.R_OK | os.W_OK)):
    raise SystemExit('Registry pointer must be user-owned, readable/writable, and not group/world writable')
print('Registry location prepared; existing contents were not overwritten')
PY
```

4. In a separate terminal at the source repository root, start the pinned upstream helper. Enter the administrator password directly in that terminal, never in chat:

```sh
IPHONE_USE_ROOT="$(pwd -P)"
sudo --preserve-env=HOME "$(command -v node)" \
  "$IPHONE_USE_ROOT/bridge/node_modules/appium-xcuitest-driver/node_modules/appium-ios-remotexpc/scripts/tunnel-creation.mjs" \
  --udid '<DEVICE_UDID>' \
  --tunnel-registry-port 42314 \
  --reconnect-retries 3
```

Keep this terminal running. It creates a TUN/TAP interface and a RemoteXPC tunnel as a separate foreground process. The plugin installs no privileged daemon or login service. Stop it with `Ctrl+C` in its own terminal, never by killing all Node/Appium processes.

Preserving **HOME** makes the privileged helper and ordinary-user plugin use the same Strongbox pointer. Default sudo behavior may otherwise write it under root's home. Only HOME needs preservation; do not preserve the whole environment. The required sudo option appears as `--preserve-env=list` in `sudo --help`.

These flags were checked against pinned `appium-ios-remotexpc 5.18.9`. It prefers USB when both USB and Network entries exist and lacks the newer `--watch-devices` flag, so restart it after unplugging. Its help still mentions USB, while upstream supports usbmuxd `ConnectionType: Network` devices. See [RemoteXPC wireless support](https://github.com/appium/appium-ios-remotexpc#iphone--ipad-over-wifi-usbmuxd-network-devices) and [Appium tunnel setup](https://appium.github.io/appium-xcuitest-driver/latest/guides/remotexpc-tunnels-real-devices/).

From another terminal:

```sh
node scripts/tunnel-diagnostics.mjs --json
curl --fail --silent --show-error http://127.0.0.1:42314/remotexpc/tunnels
```

Registry output can contain UDIDs and local addresses; keep it local. Confirm the intended entry has `connectionType: Network`. A missing entry or `USB` is not wireless acceptance. If port 42314 is already used, identify its owner instead of stopping another task's service.

## 7. Connect and verify unplugged operation

Ask Codex:

> Use iphone-use-wireless to connect my phone. USB is unplugged. Check the wireless tunnel, read the current UI components, and report whether both connection and observation succeeded.

Codex should check `iphone_status` and `iphone_setup(action="status")`, start the bridge if required, and call `iphone_session(action="connect")`. A pending connection returns an `operation_id`; poll `connect_status` for the same operation rather than reconnecting.

After success, keep the private `session_token` and call `iphone_observe(mode="components")`. A fresh observation is required; Appium readiness alone does not prove phone control. If action verification is desired, authorize a reversible action such as opening a named app, then observe its result. Screenshots are a fallback after unavailable components or repeated meaningful targeting attempts.

Disconnect your session and connect again to verify wireless reconnection. Acceptance requires **USB unplugged, a Network registry entry, a real session, and a new successful observation**. Never replay an uncertain action. Public reports need only device/version summaries, not private screenshots.

For daily use: unlock the phone, ensure the same reachable network, start the foreground tunnel, and ask Codex to connect. Sleep, restart, network changes, and signing expiration can require recovery.

## 8. Troubleshooting

| Symptom | Check or recovery |
| --- | --- |
| Phone disappears after unplugging | Verify pairing, Finder Wi-Fi visibility, reachability, and an unlocked device. Guest-network client isolation can block discovery. Reconnect USB for pairing if necessary, then repeat step 6. |
| Wired works, wireless disconnects | The tunnel may have selected USB. End your session and foreground tunnel, unplug, and create a new Network tunnel. |
| Failure after sleep or network change | Wake the Mac, unlock the phone, check reachability, and restart your stale foreground tunnel. |
| RemoteXPC unavailable | Check the foreground terminal and target registry entry. Diagnostics do not create a tunnel. |
| Port 42314 or 4737 is occupied | Identify and coordinate with its owning task; do not kill unrelated services. |
| Tunnel logs look healthy but plugin cannot find it | Preserve the current HOME and ensure the ordinary user can read the Strongbox pointer. See the targeted repair below. |
| WDA launch/signature failure | Check Developer Mode, team, base bundle ID, provisioning, and the renewed signature on the prepared copy. |
| Free-team app stops working after several days | Apple Personal Team provisioning expires after seven days. Rebuild, reinstall, and prepare WDA again. See [Apple's account guide](https://developer.apple.com/help/account/basics/about-your-developer-account). |
| Prebuilt WDA path no longer exists | After ending your session, rerun the complete configure command with a stable valid `.app` path. Moving a checkout or cleaning DerivedData can invalidate old paths. |
| Plugin update reports missing dependencies | Run setup against the current loaded cache copy, not an old version's directory. |
| Element reference or position changed | Observe again and select a fresh target. Do not reuse old coordinates after UI changes. |
| Unknown/uncertain result | Stop mutations and inspect status/UI through supported recovery. Do not replay the action, reconnect repeatedly, or delete locks. |

End your session before reconfiguration. Preserve private state and your signed WDA copy. Do not import another controller's session, locks, or certificates. Before dependency upgrades, stop your phone session and any foreground tunnel using that dependency directory.

### Repair only your own Strongbox permissions

Use this only after confirming that these items were created for your setup, no other active tunnel depends on them, and your foreground tunnel has stopped. If sudo made them root-owned, repair these two exact paths without recursively modifying Library or Appium state:

```sh
IPHONE_USE_REGISTRY_DIR="$HOME/Library/Application Support/appium-xcuitest-driver-nodejs/strongbox"
ls -ld "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
sudo chown "$(id -u):$(id -g)" "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
chmod 700 "$IPHONE_USE_REGISTRY_DIR"
chmod 600 "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
```

Repeat step 6 and run diagnostics as the ordinary user. This repairs your registry pointer; it does not install a daemon or depend on an old VICO tunnel service.

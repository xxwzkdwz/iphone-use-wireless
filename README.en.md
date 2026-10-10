# iphone-use-wireless

**Use Wi-Fi to let Codex get things done on your iPhone.**

Ask Codex to open apps, find information, enter text, tap, and swipe. After initial USB pairing and signing setup, unplug the cable and use your iPhone wirelessly on the same network as your Mac.

[中文](README.md) · [Setup guide](docs/WIRELESS_SETUP.en.md)

## What you can do

- **Operate wirelessly**: connect to a paired iPhone through your Mac, without a data cable during everyday use.
- **Read the screen**: inspect text and UI components, using screenshots when needed.
- **Work inside apps**: launch apps, search, fill in text, tap controls, and scroll.
- **Handle multiple steps**: describe a goal and let Codex observe, act, and check the result.
- **Use your preferred client**: install the Codex plugin or connect through standard MCP.

## Get started

You need a Mac with full Xcode, an iPhone running iOS 18+, a cable for initial pairing, and your own Apple signing account. Keep the Mac awake and the phone unlocked on the same network while operating wirelessly.

Send this to Codex for guided setup:

```text
Help me set up iphone-use-wireless to control my own iPhone over Wi-Fi.
Repository: https://github.com/xxwzkdwz/iphone-use-wireless

Follow the wireless setup guide to prepare dependencies, install the plugin,
pair the phone, configure WDA, and prepare the wireless tunnel.
Reuse my existing configuration where possible. When I need to sign in to
Apple, confirm a phone prompt, or enter an administrator password, explain
what I should do locally. Finish by connecting and reading the screen with USB unplugged.
```

First-time preparation includes pairing, WDA signing, and a wireless tunnel. The [complete setup guide](docs/WIRELESS_SETUP.en.md) walks through each step. The plugin includes a `setup` skill for configuration and an `iphone` skill for everyday tasks.

<details>
<summary>Manual installation</summary>

Make sure the Codex CLI is installed and `codex` is on PATH. Install Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 22.12+ (22.x) or 24+, and npm 10+, then run:

```sh
git clone https://github.com/xxwzkdwz/iphone-use-wireless.git
cd iphone-use-wireless
uv sync --frozen
node scripts/install-bridge.mjs --json
bash scripts/install-codex.sh
```

Complete signing, device configuration, and tunnel preparation in the [setup guide](docs/WIRELESS_SETUP.en.md). Open a new Codex chat and run `setup` to check the actual installed plugin directory. Installation commands retain the identifier `iphone-use`; the display name is **iphone-use-wireless**.

Other MCP clients can launch this project's `scripts/run-server.sh` with `bash`. For direct registration in the Codex CLI:

```sh
codex mcp add iphone_use -- bash '/absolute/path/to/iphone-use-wireless/scripts/run-server.sh'
```

Choose either plugin installation or direct MCP registration.

</details>

## Try a task

> Connect to my phone with iphone-use-wireless and tell me which screen is open.

> Search for nearby coffee shops in Maps and summarize a few places shown on the screen.

> Open Notes and write a packing list for a weekend trip.

Keep the wireless tunnel running while you work, then give Codex a task. Ask it to disconnect the phone when you are finished.

## More

[Setup and troubleshooting](docs/WIRELESS_SETUP.en.md) · [Changelog](CHANGELOG.md) · [MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

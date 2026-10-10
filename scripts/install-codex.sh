#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/install-codex.sh [--register-only | --reinstall]

Registers this checkout as the iphone-use-local Codex marketplace, then
installs iphone-use@iphone-use-local. --register-only skips plugin installation.
--reinstall replaces this plugin's installed cache using Codex's remove/add
commands. Close chats using the plugin first; saved device configuration outside
the plugin cache is retained. It can also move the iphone-use-local marketplace
from an older local checkout, restoring that source if registration fails.
An ordinary install verifies the cached version
and reports stale caches instead of claiming an upgrade succeeded.
This command modifies Codex plugin configuration. It does not install device
dependencies, connect to an iPhone, or perform phone actions.
EOF
}

register_only=false
reinstall=false
case "${1:-}" in
  "") ;;
  --register-only) register_only=true ;;
  --reinstall) reinstall=true ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi

plugin_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if ! command -v codex >/dev/null 2>&1; then
  printf '%s\n' 'codex is not on PATH. Install the Codex CLI or use this repo marketplace in the desktop app.' >&2
  exit 127
fi

if [[ "$register_only" == false ]]; then
  if ! codex plugin add --help >/dev/null 2>&1 || ! codex plugin list --help >/dev/null 2>&1; then
    printf '%s\n' 'This Codex CLI lacks plugin add/list; install iphone-use-wireless from the local marketplace in the desktop app.' >&2
    exit 1
  fi
  if ! command -v node >/dev/null 2>&1; then
    printf '%s\n' 'Node.js is required to verify the installed plugin version. Run setup first.' >&2
    exit 1
  fi
  expected_version="$(node -e 'const fs=require("node:fs"); const p=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); if(p.name!=="iphone-use" || typeof p.version!=="string" || !/^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$/.test(p.version)) process.exit(1); process.stdout.write(p.version)' "$plugin_root/.codex-plugin/plugin.json")"
  installer_temp="$(mktemp -d "${TMPDIR:-/tmp}/iphone-use-install.XXXXXX")"
  trap 'rm -rf -- "$installer_temp"' EXIT
fi

old_marketplace_source=""
if [[ "$reinstall" == true ]]; then
  codex plugin marketplace list --json > "$installer_temp/marketplaces.json"
  old_marketplace_source="$(node - "$installer_temp/marketplaces.json" "$plugin_root" <<'JS'
const fs = require('node:fs');
const path = require('node:path');
const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (!Array.isArray(data.marketplaces)) process.exit(1);
const matches = data.marketplaces.filter(m => m.name === 'iphone-use-local');
if (matches.length > 1) process.exit(1);
if (matches.length) {
  const current = matches[0];
  const source = current.marketplaceSource;
  if (source?.sourceType !== 'local' || typeof source.source !== 'string' || !path.isAbsolute(source.source) ||
      source.source.includes('\0') || typeof current.root !== 'string' || !path.isAbsolute(current.root)) {
    console.error('Cannot migrate iphone-use-local: its registered source is not a verified local directory.');
    process.exit(1);
  }
  const canonical = p => { try { return fs.realpathSync(p); } catch { return path.resolve(p); } };
  if (canonical(current.root) !== canonical(source.source)) process.exit(1);
  if (canonical(source.source) !== canonical(process.argv[3])) process.stdout.write(source.source);
}
JS
)"
fi
if [[ -n "$old_marketplace_source" ]]; then
  codex plugin marketplace remove iphone-use-local --json
  if codex plugin marketplace add "$plugin_root"; then
    printf '%s\n' 'Moved the iphone-use-local marketplace to this checkout.'
  else
    registration_status=$?
    if codex plugin marketplace add "$old_marketplace_source"; then
      printf '%s\n' 'New marketplace registration failed; restored the previous local source. The installed plugin was not removed.' >&2
    else
      printf '%s\n' 'New marketplace registration and restoring its old local source both failed. Inspect iphone-use-local before retrying; the installed plugin was not removed.' >&2
    fi
    exit "$registration_status"
  fi
else
  codex plugin marketplace add "$plugin_root"
fi

if [[ "$register_only" == false ]]; then
  installed_version() {
    codex plugin list --marketplace iphone-use-local --json > "$installer_temp/plugins.json"
    node -e 'const fs=require("node:fs"); const data=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); if(!Array.isArray(data.installed)) process.exit(1); const matches=data.installed.filter(p=>p.pluginId==="iphone-use@iphone-use-local" && p.installed===true); if(matches.length>1) process.exit(1); const version=matches[0]?.version??""; if(typeof version!=="string") process.exit(1); process.stdout.write(version)' "$installer_temp/plugins.json"
  }
  if [[ "$reinstall" == true ]]; then
    prior_version="$(installed_version)"
    if [[ -n "$prior_version" ]]; then
      codex plugin remove iphone-use@iphone-use-local --json
    fi
  fi
  codex plugin add iphone-use@iphone-use-local --json
  actual_version="$(installed_version)"
  if [[ "$actual_version" != "$expected_version" ]]; then
    printf 'Plugin cache version did not update: expected %s, installed %s.\n' "$expected_version" "${actual_version:-none}" >&2
    printf '%s\n' 'Close chats using this plugin, then run bash scripts/install-codex.sh --reinstall to replace its cache. Saved device configuration is retained.' >&2
    exit 1
  fi
  printf 'Verified installed iphone-use-wireless version %s.\n' "$actual_version"
fi
printf '%s\n' 'Open a new Codex chat after installation. Run the iphone-use-wireless setup skill before connecting a phone.'
printf '%s\n' 'The host may run a cached plugin copy. Setup must check and install dependencies in that actual plugin directory.'

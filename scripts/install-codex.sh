#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/install-codex.sh [--register-only]

Registers this checkout as the iphone-use-local Codex marketplace, then
installs iphone-use@iphone-use-local. --register-only skips plugin installation.
This command modifies Codex plugin configuration. It does not install device
dependencies, connect to an iPhone, or perform phone actions.
EOF
}

register_only=false
case "${1:-}" in
  "") ;;
  --register-only) register_only=true ;;
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

codex plugin marketplace add "$plugin_root"
if [[ "$register_only" == false ]]; then
  if codex plugin add --help >/dev/null 2>&1; then
    codex plugin add iphone-use@iphone-use-local
  else
    printf '%s\n' 'Marketplace registered. This Codex CLI lacks plugin add; install iphone use from the local marketplace in the desktop app.' >&2
  fi
fi
printf '%s\n' 'Open a new Codex chat after installation. Run the iphone use setup skill before connecting a phone.'
printf '%s\n' 'The host may run a cached plugin copy. Setup must check and install dependencies in that actual plugin directory.'

#!/usr/bin/env bash
set -euo pipefail

plugin_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export IPHONE_USE_BACKEND="${IPHONE_USE_BACKEND-direct-wda}"
case "$IPHONE_USE_BACKEND" in
  direct-wda) default_state="$HOME/.local/share/iphone-use-wireless-direct" ;;
  appium) default_state="$HOME/.local/share/vico-iphone" ;;
  *) printf '%s\n' 'IPHONE_USE_BACKEND must be direct-wda or appium; no automatic fallback is supported.' >&2; exit 2 ;;
esac
export VICO_IPHONE_HOME="${VICO_IPHONE_HOME:-$default_state}"
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'iphone use requires uv on PATH. Install uv, then run uv sync --frozen in the plugin directory.' >&2
  exit 127
fi

# All ordinary output must stay off stdout: stdout carries the MCP protocol.
exec uv run --frozen --project "$plugin_root" iphone-use-mcp "$@"

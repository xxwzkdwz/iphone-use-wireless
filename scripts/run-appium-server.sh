#!/usr/bin/env bash
set -euo pipefail
plugin_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export IPHONE_USE_BACKEND=appium
export VICO_IPHONE_HOME="${VICO_IPHONE_HOME:-$HOME/.local/share/vico-iphone}"
exec bash "$plugin_root/scripts/run-server.sh" "$@"

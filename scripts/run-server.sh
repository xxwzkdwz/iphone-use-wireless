#!/usr/bin/env bash
set -euo pipefail

plugin_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'iphone use requires uv on PATH. Install uv, then run uv sync --frozen in the plugin directory.' >&2
  exit 127
fi

# All ordinary output must stay off stdout: stdout carries the MCP protocol.
exec uv run --frozen --project "$plugin_root" iphone-use-mcp "$@"

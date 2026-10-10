#!/usr/bin/env bash
# Prepare this exact checkout/cache. Device configuration is intentionally retained.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/setup.sh [--check] [--skip-plugin] [--backend direct-wda|appium]

Check Mac prerequisites, install this copy's pinned Python/direct-WDA dependencies,
and register the Codex plugin. --skip-plugin prepares runtime dependencies only
(use it inside an already installed plugin cache or with another MCP client).
--check inspects prerequisites and installed dependencies without changing them.
The default backend is direct-wda. --backend appium (or IPHONE_USE_BACKEND=appium)
prepares the optional legacy Appium bridge instead.

Xcode, uv, Node.js and npm must be available first; missing prerequisites are
reported with the next step. This does not sign WDA, pair/connect an iPhone,
start a tunnel, change existing device configuration, or install system software.
EOF
}

check_only=false
skip_plugin=false
backend="${IPHONE_USE_BACKEND-direct-wda}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) check_only=true; shift ;;
    --skip-plugin) skip_plugin=true; shift ;;
    --backend) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; backend="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
case "$backend" in direct-wda|appium) ;; *) usage >&2; exit 2 ;; esac
export IPHONE_USE_BACKEND="$backend"
plugin_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$plugin_root"
missing=0
pass() { printf '[OK] %s\n' "$1"; }
need() { printf '[NEEDS ACTION] %s\n' "$1" >&2; missing=$((missing + 1)); }

printf '%s\n' 'Checking this copy of iphone-use-wireless...'
if [[ "$(uname -s)" != Darwin ]]; then
  need 'Use a Mac for iPhone control; the Apple device tools require macOS.'
else
  pass 'macOS'
  xcode_path="$(xcode-select -p 2>/dev/null || true)"
  if [[ "$xcode_path" != *'.app/Contents/Developer' ]]; then
    need 'Install and open the full Xcode app, then select it in Xcode Settings > Locations > Command Line Tools.'
  elif ! xcodebuild -checkFirstLaunchStatus >/dev/null 2>&1; then
    need 'Open Xcode and finish its license/component installation, then rerun setup.'
  elif ! xcrun --find devicectl >/dev/null 2>&1; then
    need 'The selected Xcode does not provide devicectl. Update/open full Xcode and select its command-line tools.'
  else
    pass 'Full Xcode and device tools'
  fi
fi

if command -v uv >/dev/null 2>&1; then
  pass 'uv'
else
  need 'Install uv: https://docs.astral.sh/uv/getting-started/installation/ (Codex can help), then rerun setup.'
fi
if ! command -v node >/dev/null 2>&1; then
  need 'Install Node.js 22.12+ (22.x) or 24+ and npm 10+: https://nodejs.org/en/download'
elif ! node -e 'const [a,b] = process.versions.node.split(".").map(Number); process.exit((a === 20 && b >= 19) || (a === 22 && b >= 12) || a >= 24 ? 0 : 1)' >/dev/null 2>&1; then
  need 'Unsupported Node.js. Use 20.19+ (20.x), 22.12+ (22.x), or 24+; Node 21/23 are not supported.'
else
  pass 'Supported Node.js'
fi
if ! command -v npm >/dev/null 2>&1; then
  need 'npm is missing. Install npm 10+ with a supported Node.js release.'
else
  npm_version="$(npm --version 2>/dev/null || true)"
  npm_major="${npm_version%%.*}"
  case "$npm_major" in
    ''|*[!0-9]*) need 'Cannot read a valid npm version. Install npm 10+.' ;;
    *) if [[ "$npm_major" -lt 10 ]]; then need 'npm 10+ is required.'; else pass 'npm 10+'; fi ;;
  esac
fi
if [[ "$skip_plugin" == false ]]; then
  if command -v codex >/dev/null 2>&1; then
    pass 'Codex CLI on PATH'
  else
    need 'Make the Codex CLI available on PATH. For an installed plugin cache or other MCP client, use --skip-plugin.'
  fi
fi
if [[ "$missing" -gt 0 ]]; then
  printf '%s\n' 'No installation was attempted. Resolve the items above, then run this command again.' >&2
  exit 1
fi

if [[ "$check_only" == true ]]; then
  if [[ -x "$plugin_root/.venv/bin/python" ]] &&
     PYTHONDONTWRITEBYTECODE=1 "$plugin_root/.venv/bin/python" -c 'import sys, httpx, pydantic, mcp, vico_iphone; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    pass 'Python 3.11+ environment and runtime imports'
  else
    need 'Python runtime is missing or incomplete in this copy. Run setup without --check; uv will select a compatible Python.'
  fi
  if node --input-type=module - "$plugin_root" "$backend" <<'JS'
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { readFile, stat } from 'node:fs/promises';
const root = process.argv[2];
if (process.argv[3] === 'appium') {
  const { installationStatus } = await import(pathToFileURL(join(root, 'bridge/runtime.mjs')));
  const result = await installationStatus();
  if (!result.installed) {
    console.error('Bridge check:', result.reason || 'dependencies_not_ready');
    process.exitCode = 1;
  }
} else {
  const { checkDependencies } = await import(pathToFileURL(join(root, 'direct/runtime.mjs')));
  const directory = join(root, 'direct');
  try {
    const pkg = JSON.parse(await readFile(join(directory, 'node_modules/appium-webdriveragent/package.json'), 'utf8'));
    const lock = JSON.parse(await readFile(join(directory, 'package-lock.json'), 'utf8'));
    const project = await stat(join(directory, 'node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj/project.pbxproj'));
    if (!(await checkDependencies(directory)) || pkg.name !== 'appium-webdriveragent' || pkg.version !== '16.12.5' ||
        lock.packages?.['node_modules/appium-webdriveragent']?.version !== '16.12.5' || !project.isFile()) throw Error();
  } catch {
    console.error('Direct WDA check: pinned runtime or WDA build source is missing.');
    process.exitCode = 1;
  }
}
JS
  then
    pass "Pinned $backend dependencies and build source"
  else
    need "The $backend runtime is missing or incomplete in this copy. Run setup without --check for this backend."
  fi
  if [[ "$missing" -gt 0 ]]; then exit 1; fi
  printf '%s\n' 'Runtime checks passed. Device pairing, WDA signing and the wireless tunnel are separate setup steps.'
  exit 0
fi

printf '\n%s\n' 'Installing Python dependencies (uv selects Python 3.11+)...'
uv sync --frozen
if [[ "$backend" == appium ]]; then
  printf '\n%s\n' 'Installing and checking the pinned legacy Appium bridge...'
  node "$plugin_root/scripts/install-bridge.mjs" --json
else
  printf '\n%s\n' 'Installing the pinned direct-WDA runtime and WDA build source...'
  npm ci --prefix "$plugin_root/direct" --omit=dev --ignore-scripts --no-audit --no-fund
fi
if [[ "$skip_plugin" == false ]]; then
  printf '\n%s\n' 'Registering the Codex plugin...'
  bash "$plugin_root/scripts/install-codex.sh"
fi
printf '\n%s\n' 'Checking the saved host configuration...'
uv run --frozen iphone-use doctor
printf '\n%s\n' 'Runtime preparation complete. Existing device configuration was retained.'
printf '%s\n' 'Next: follow docs/WIRELESS_SETUP.md for first pairing/signing, then run node scripts/start-tunnel.mjs.'
if [[ "$skip_plugin" == false ]]; then
  printf '%s\n' 'Open a new Codex chat and run setup in its actual plugin cache with --skip-plugin before connecting.'
fi

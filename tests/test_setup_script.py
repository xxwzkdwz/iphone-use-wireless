"""Exercise the installer command boundary without installing or touching a phone."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/setup.sh"


@pytest.fixture
def setup_host(tmp_path):
    root = tmp_path / "plugin with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SOURCE, scripts / "setup.sh")
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls"
    state = tmp_path / "private-state"
    state.mkdir()
    config = state / "device.json"
    config.write_text('{"keep":"existing config"}')
    env = {
        **os.environ,
        "PATH": str(binary) + ":/usr/bin:/bin",
        "SETUP_TEST_LOG": str(log),
        "VICO_IPHONE_HOME": str(state),
    }

    def command(name, body):
        path = binary / name
        path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    command("uname", "printf 'Darwin\\n'")
    command("xcode-select", "printf '/Applications/Xcode.app/Contents/Developer\\n'")
    command("xcodebuild", 'exit "${SETUP_TEST_XCODE_STATUS:-0}"')
    command("xcrun", "printf '/Applications/Xcode.app/Contents/Developer/usr/bin/devicectl\\n'")
    command("npm", "printf '10.9.0\\n'")
    command("codex", "exit 0")
    command("uv", 'printf "uv %s\\n" "$*" >> "$SETUP_TEST_LOG"; exit "${SETUP_TEST_UV_STATUS:-0}"')
    command("node", '''
case "$1" in
  -e) exit "${SETUP_TEST_NODE_STATUS:-0}" ;;
  --input-type=module) cat >/dev/null; exit "${SETUP_TEST_BRIDGE_STATUS:-0}" ;;
  *) printf 'node %s\n' "$*" >> "$SETUP_TEST_LOG" ;;
esac''')
    (scripts / "install-codex.sh").write_text('printf "register plugin\\n" >> "$SETUP_TEST_LOG"\n')
    runtime_python = root / ".venv/bin/python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("#!/bin/bash\nexit 0\n")
    runtime_python.chmod(0o755)

    def run(*args):
        return subprocess.run(
            ["/bin/bash", str(scripts / "setup.sh"), *args], cwd=tmp_path,
            env=env, text=True, capture_output=True, timeout=10,
        )

    return run, env, log, root, binary, config


def test_setup_preserves_config_and_uses_exact_copy(setup_host):
    run, _, log, root, _, config = setup_host
    result = run()
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "uv sync --frozen",
        f"node {root}/scripts/install-bridge.mjs --json",
        "register plugin",
        "uv run --frozen iphone-use doctor",
    ]
    assert config.read_text() == '{"keep":"existing config"}'
    assert "Runtime preparation complete" in result.stdout


def test_missing_xcode_initialization_stops_before_any_install(setup_host):
    run, env, log, _, _, _ = setup_host
    env["SETUP_TEST_XCODE_STATUS"] = "1"
    result = run()
    assert result.returncode == 1
    assert "license/component" in result.stderr
    assert not log.exists()


def test_cache_preparation_does_not_require_or_register_codex(setup_host):
    run, _, log, _, binary, _ = setup_host
    (binary / "codex").unlink()
    assert run().returncode == 1
    assert not log.exists()
    result = run("--skip-plugin")
    assert result.returncode == 0, result.stderr
    assert "register plugin" not in log.read_text()


def test_check_does_not_install_or_create_state(setup_host):
    run, _, log, root, _, config = setup_host
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    result = run("--check")
    assert result.returncode == 0, result.stderr
    assert not log.exists()
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before
    assert config.read_text() == '{"keep":"existing config"}'


def test_check_detects_incomplete_runtime_without_repair(setup_host):
    run, env, log, root, _, _ = setup_host
    (root / ".venv/bin/python").unlink()
    env["SETUP_TEST_BRIDGE_STATUS"] = "1"
    result = run("--check")
    assert result.returncode == 1
    assert "Python runtime is missing" in result.stderr
    assert "Bridge runtime is missing" in result.stderr
    assert not log.exists()


def test_failed_dependency_install_does_not_register_plugin(setup_host):
    run, env, log, _, _, _ = setup_host
    env["SETUP_TEST_UV_STATUS"] = "7"
    result = run()
    assert result.returncode == 7
    assert log.read_text().splitlines() == ["uv sync --frozen"]
    assert "complete" not in result.stdout.lower()


def test_invalid_arguments_do_not_install(setup_host):
    run, _, log, _, _, _ = setup_host
    assert run("--unknown").returncode == 2
    assert not log.exists()

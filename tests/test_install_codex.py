"""Verify public Codex commands and version checks without changing Codex state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def installer(tmp_path):
    root = tmp_path / "plugin with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / ".codex-plugin").mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts/install-codex.sh"
    shutil.copy2(source, root / "scripts/install-codex.sh")
    (root / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "iphone-use", "version": "0.2.0"}))
    binary = tmp_path / "bin"
    binary.mkdir()
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for installer JSON verification")
    (binary / "node").symlink_to(node)
    log = tmp_path / "calls.jsonl"
    version = tmp_path / "installed-version"
    marketplaces = tmp_path / "marketplaces.json"
    marketplaces.write_text("[]")
    fake = binary / "codex"
    fake.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
if '--help' in args:
    sys.exit(0)
with open(os.environ['INSTALL_TEST_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
version = pathlib.Path(os.environ['INSTALL_TEST_VERSION'])
marketplaces = pathlib.Path(os.environ['INSTALL_TEST_MARKETPLACES'])
entries = json.loads(marketplaces.read_text())
if args[:3] == ['plugin', 'marketplace', 'list']:
    print(json.dumps({'marketplaces': entries}))
elif args[:3] == ['plugin', 'marketplace', 'remove']:
    marketplaces.write_text(json.dumps([m for m in entries if m['name'] != args[3]]))
elif args[:3] == ['plugin', 'marketplace', 'add']:
    new_source = args[3]
    if new_source == os.environ.get('INSTALL_TEST_MARKETPLACE_ADD_FAIL'):
        sys.exit(8)
    target = [m for m in entries if m['name'] == 'iphone-use-local']
    if target and target[0]['marketplaceSource']['source'] != new_source:
        sys.exit(9)
    if not target:
        entries.append({'name': 'iphone-use-local', 'root': new_source,
                        'marketplaceSource': {'sourceType': 'local', 'source': new_source}})
        marketplaces.write_text(json.dumps(entries))
elif args[:2] == ['plugin', 'list']:
    installed = [{'pluginId': 'iphone-use@iphone-use-local', 'version': version.read_text(), 'installed': True}] if version.exists() else []
    print(json.dumps({'installed': installed, 'available': []}))
elif args[:2] == ['plugin', 'remove']:
    version.unlink()
elif args[:2] == ['plugin', 'add']:
    if os.environ.get('INSTALL_TEST_ADD_FAIL'):
        sys.exit(7)
    if not version.exists():
        version.write_text('0.2.0')
''')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{binary}:/usr/bin:/bin", "INSTALL_TEST_LOG": str(log), "INSTALL_TEST_VERSION": str(version),
           "INSTALL_TEST_MARKETPLACES": str(marketplaces)}

    def run(*args):
        return subprocess.run(["/bin/bash", str(root / "scripts/install-codex.sh"), *args], env=env,
                              text=True, capture_output=True, timeout=10)

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return run, env, version, calls, root


def test_fresh_install_uses_official_add_then_verifies_version(installer):
    run, _, version, calls, root = installer
    result = run()
    assert result.returncode == 0, result.stderr
    assert version.read_text() == "0.2.0"
    assert calls() == [
        ["plugin", "marketplace", "add", str(root)],
        ["plugin", "add", "iphone-use@iphone-use-local", "--json"],
        ["plugin", "list", "--marketplace", "iphone-use-local", "--json"],
    ]
    assert "Verified installed iphone-use-wireless version 0.2.0" in result.stdout


def test_stale_cache_is_reported_without_implicit_uninstall(installer):
    run, _, version, calls, _ = installer
    version.write_text("0.1.0")
    result = run()
    assert result.returncode == 1
    assert "expected 0.2.0, installed 0.1.0" in result.stderr
    assert "--reinstall" in result.stderr
    assert not any(call[:2] == ["plugin", "remove"] for call in calls())
    assert version.read_text() == "0.1.0"


def test_explicit_reinstall_uses_remove_add_for_cache_upgrade(installer):
    run, _, version, calls, _ = installer
    version.write_text("0.1.0")
    result = run("--reinstall")
    assert result.returncode == 0, result.stderr
    assert version.read_text() == "0.2.0"
    operations = [call[1] for call in calls()]
    assert operations == ["marketplace", "marketplace", "list", "remove", "add", "list"]


def test_reinstall_without_existing_plugin_skips_remove(installer):
    run, _, _, calls, _ = installer
    assert run("--reinstall").returncode == 0
    assert not any(call[:2] == ["plugin", "remove"] for call in calls())


def test_register_only_does_not_install_or_claim_verification(installer):
    run, _, version, calls, root = installer
    result = run("--register-only")
    assert result.returncode == 0
    assert calls() == [["plugin", "marketplace", "add", str(root)]]
    assert not version.exists()
    assert "Verified installed" not in result.stdout


def test_add_failure_is_not_reported_as_installed(installer):
    run, env, _, calls, _ = installer
    env["INSTALL_TEST_ADD_FAIL"] = "1"
    result = run()
    assert result.returncode == 7
    assert "Verified installed" not in result.stdout
    assert not any(call[:2] == ["plugin", "list"] for call in calls())


def test_invalid_reinstall_arguments_do_not_change_codex(installer):
    run, _, _, calls, _ = installer
    assert run("--reinstall", "--register-only").returncode == 2
    assert calls() == []


def set_old_marketplace(env, source="/old checkout", source_type="local"):
    entries = [
        {"name": "unrelated", "root": "/other", "marketplaceSource": {"sourceType": "local", "source": "/other"}},
        {"name": "iphone-use-local", "root": source, "marketplaceSource": {"sourceType": source_type, "source": source}},
    ]
    Path(env["INSTALL_TEST_MARKETPLACES"]).write_text(json.dumps(entries))
    return entries


def test_explicit_reinstall_moves_only_its_old_local_marketplace(installer):
    run, env, version, calls, root = installer
    before = set_old_marketplace(env)
    version.write_text("0.1.0")
    result = run("--reinstall")
    assert result.returncode == 0, result.stderr
    assert version.read_text() == "0.2.0"
    entries = json.loads(Path(env["INSTALL_TEST_MARKETPLACES"]).read_text())
    assert entries[0] == before[0]
    assert entries[1]["marketplaceSource"]["source"] == str(root)
    assert calls()[:3] == [
        ["plugin", "marketplace", "list", "--json"],
        ["plugin", "marketplace", "remove", "iphone-use-local", "--json"],
        ["plugin", "marketplace", "add", str(root)],
    ]


def test_failed_marketplace_migration_restores_source_without_removing_plugin(installer):
    run, env, version, calls, root = installer
    before = set_old_marketplace(env)
    version.write_text("0.1.0")
    env["INSTALL_TEST_MARKETPLACE_ADD_FAIL"] = str(root)
    result = run("--reinstall")
    assert result.returncode == 8
    assert "restored the previous local source" in result.stderr
    assert json.loads(Path(env["INSTALL_TEST_MARKETPLACES"]).read_text()) == before
    assert version.read_text() == "0.1.0"
    assert calls()[-1] == ["plugin", "marketplace", "add", "/old checkout"]
    assert not any(call[:2] == ["plugin", "remove"] for call in calls())


def test_ordinary_install_does_not_migrate_marketplaces(installer):
    run, env, version, calls, _ = installer
    before = set_old_marketplace(env)
    version.write_text("0.1.0")
    result = run()
    assert result.returncode == 9
    assert json.loads(Path(env["INSTALL_TEST_MARKETPLACES"]).read_text()) == before
    assert version.read_text() == "0.1.0"
    assert len(calls()) == 1


def test_reinstall_refuses_nonlocal_registered_source(installer):
    run, env, _, calls, _ = installer
    before = set_old_marketplace(env, source_type="git")
    result = run("--reinstall")
    assert result.returncode == 1
    assert json.loads(Path(env["INSTALL_TEST_MARKETPLACES"]).read_text()) == before
    assert calls() == [["plugin", "marketplace", "list", "--json"]]

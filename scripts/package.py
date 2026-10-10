#!/usr/bin/env python3
"""Build a reproducible, allowlisted source ZIP without local device state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PNG_ASSETS = {"assets/iphone-use-wireless-hero.png"}
REQUIRED_FILES = {
    ".agents/plugins/marketplace.json",
    ".codex-plugin/plugin.json",
    ".gitignore",
    ".mcp.json",
    "LICENSE",
    "CHANGELOG.md",
    "README.md",
    "README.en.md",
    "docs/WIRELESS_SETUP.md",
    "docs/WIRELESS_SETUP.en.md",
    "docs/DIRECT_WDA_EXPERIMENT.md",
    "docs/BENCHMARK_PROTOCOL.md",
    "THIRD_PARTY_NOTICES.md",
    "licenses/Apache-2.0.txt",
    "licenses/WebDriverAgent-LICENSE.txt",
    "plugin.json",
    "mcp.json",
    "pyproject.toml",
    "uv.lock",
    "bridge/package.json",
    "bridge/package-lock.json",
    "bridge/cli.mjs",
    "bridge/runtime.mjs",
    "bridge/supervisor.mjs",
    "bridge/bridge-runner.cjs",
    "bridge/xcodebuild-spawn.cjs",
    "bridge/xcodebuild-guardian.cjs",
    "direct/package.json",
    "direct/package-lock.json",
    "direct/runtime.mjs",
    "direct/runner.mjs",
    "direct/test/runtime.test.mjs",
    "scripts/install-bridge.mjs",
    "scripts/iphone-control-patches.mjs",
    "scripts/tunnel-diagnostics.mjs",
    "scripts/install-codex.sh",
    "scripts/setup.sh",
    "scripts/prepare-wda.py",
    "scripts/start-tunnel.mjs",
    "scripts/run-server.sh",
    "scripts/run-direct-server.sh",
    "scripts/package.py",
    "scripts/benchmark-backends.py",
    "skills/setup/SKILL.md",
    "skills/iphone/SKILL.md",
    "tests/test_controller.py",
    "tests/test_cli.py",
    "tests/test_discovery.py",
    "tests/test_service.py",
    "tests/test_stdio.py",
    "tests/bridge.test.mjs",
    "tests/test_setup_script.py",
    "tests/test_prepare_wda.py",
    "tests/tunnel-setup.test.mjs",
    "tests/test_direct_wda.py",
    "tests/test_direct_integration.py",
    "tests/test_benchmark.py",
} | PNG_ASSETS
REQUIRED_SOURCE = {"src/vico_iphone/__init__.py", "src/vico_iphone/cli.py", "src/vico_iphone/server.py",
                   "src/vico_iphone/direct_wda.py"}
FORBIDDEN_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "configuration", "runtime", "state", "data", "logs", "screenshots",
    "observations", "DerivedData", ".vico-iphone",
}
PRIVATE_MARKERS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "device identifier": re.compile(rb"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{16}\b"),
    "personal home path": re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
    "OpenAI API key": re.compile(rb"\bsk-(?:proj-|svcacct-)[A-Za-z0-9_-]{24,}"),
}


def collect_sources(root: Path) -> dict[str, bytes]:
    missing = sorted(name for name in REQUIRED_FILES | REQUIRED_SOURCE if not (root / name).is_file())
    if missing:
        raise ValueError("Required release files are missing: " + ", ".join(missing))

    names = set(REQUIRED_FILES)
    # The only recursive inclusion is Python source. No arbitrary docs, fixtures,
    # assets, hidden files, state directories, or installed dependencies are copied.
    names.update(path.relative_to(root).as_posix() for path in (root / "src/vico_iphone").rglob("*.py"))
    files: dict[str, bytes] = {}
    for name in sorted(names):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or FORBIDDEN_PARTS.intersection(relative.parts):
            raise ValueError(f"Disallowed package path: {name}")
        path = root / relative
        if any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise ValueError(f"Symlinks are not distributable: {name}")
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Invalid source file: {name}")
        content = path.read_bytes()
        if len(content) > 5 * 1024 * 1024:
            raise ValueError(f"Unexpectedly large source file: {name}")
        if name in PNG_ASSETS:
            if not content.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"):
                raise ValueError(f"Expected a PNG image: {name}")
        elif b"\x00" in content:
            raise ValueError(f"Binary content is not allowed: {name}")
        for label, pattern in PRIVATE_MARKERS.items():
            if pattern.search(content):
                # Report the file and category, never the sensitive match.
                raise ValueError(f"Potential {label} in {name}; review before packaging")
        files[name] = content
    return files


def validate_manifests(files: dict[str, bytes]) -> tuple[str, str]:
    manifest = json.loads(files["plugin.json"])
    legacy = json.loads(files[".codex-plugin/plugin.json"])
    portable_mcp = json.loads(files["mcp.json"])
    legacy_mcp = json.loads(files[".mcp.json"])
    marketplace = json.loads(files[".agents/plugins/marketplace.json"])
    name, version = manifest["name"], manifest["version"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("Plugin name must be kebab-case")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("Plugin version must be a safe semantic version")
    if (legacy.get("name"), legacy.get("version")) != (name, version):
        raise ValueError("Portable and Codex compatibility identities differ")
    if legacy.get("mcpServers") != "./.mcp.json" or legacy.get("skills") != "./skills/":
        raise ValueError("Codex compatibility paths are incorrect")
    if set(portable_mcp.get("mcpServers", {})) != set(legacy_mcp.get("mcpServers", {})):
        raise ValueError("MCP server names differ between formats")
    server = portable_mcp["mcpServers"].get("iphone_use", {})
    if server != {"type": "stdio", "command": "bash", "args": ["${PLUGIN_ROOT}/scripts/run-server.sh"], "cwd": "${PLUGIN_ROOT}"}:
        raise ValueError("Portable stdio launch configuration changed; review packaging")
    entry = marketplace.get("plugins", [{}])[0]
    if entry.get("name") != name or entry.get("source") != {"source": "local", "path": "./"}:
        raise ValueError("Marketplace must resolve this plugin from the repository root")
    onboarding = manifest.get("extensions", {}).get("com.openai", {}).get("onboardingSkill")
    if onboarding != "./skills/setup/SKILL.md":
        raise ValueError("Onboarding must point to the packaged setup skill")
    return name, version


def build(root: Path, output: Path | None = None, check_only: bool = False) -> dict:
    files = collect_sources(root)
    name, version = validate_manifests(files)
    inventory = [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                 for path, data in files.items()]
    if check_only:
        return {"ok": True, "name": name, "version": version, "file_count": len(files), "files": inventory}

    output = output or root / "dist" / f"{name}-{version}.zip"
    if output.suffix != ".zip":
        raise ValueError("Output filename must end in .zip")
    if output.is_symlink() or any(parent.is_symlink() for parent in output.parents):
        raise ValueError("Output path may not use symlinks")
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory_bytes = (json.dumps({"name": name, "version": version, "files": inventory}, indent=2) + "\n").encode()
    temporary = output.with_name(output.name + ".tmp")
    if temporary.is_symlink():
        raise ValueError("Temporary output may not be a symlink")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, data in {**files, "PACKAGE_CONTENTS.json": inventory_bytes}.items():
                info = zipfile.ZipInfo(f"{name}/{path}", (2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                executable = path in {"scripts/run-server.sh", "scripts/run-direct-server.sh", "scripts/install-codex.sh", "scripts/setup.sh", "scripts/prepare-wda.py", "scripts/package.py"}
                info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        temporary.replace(output)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum = output.with_name(output.name + ".sha256")
    if checksum.is_symlink():
        raise ValueError("Checksum output may not be a symlink")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return {"ok": True, "archive": str(output), "sha256": digest, "checksum": str(checksum),
            "file_count": len(files) + 1, "bytes": output.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate and print the allowlisted inventory without writing an archive")
    parser.add_argument("--output", type=Path, help="ZIP output path (default: dist/iphone-use-VERSION.zip)")
    args = parser.parse_args()
    try:
        result = build(ROOT, args.output, args.check)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

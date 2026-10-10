#!/usr/bin/env python3
"""Prepare a fresh, signed WebDriverAgent copy for preinstalled wireless use.

Uses only Python's standard library and macOS codesign. The owner must first
build and sign WDA in Xcode, then provide the exact local signing identity.
This command does not discover identities, download certificates, update phone
configuration, install the app, or connect to a phone.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable


WDA_APP_NAME = "WebDriverAgentRunner-Runner.app"
CODESIGN = "/usr/bin/codesign"
CODESIGN_TIMEOUT_SECONDS = 120


class PrepareError(RuntimeError):
    """A local preparation failed; no configured app is replaced."""


def default_output_root() -> Path:
    # Keep the standalone, standard-library-only helper aligned with state_home.
    backend = os.environ.get("IPHONE_USE_BACKEND", "direct-wda")
    if backend not in {"direct-wda", "appium"}:
        raise PrepareError("IPHONE_USE_BACKEND must be direct-wda or appium; no automatic fallback is supported.")
    directory = "iphone-use-wireless-direct" if backend == "direct-wda" else "vico-iphone"
    state = Path(os.environ.get("VICO_IPHONE_HOME", str(Path.home() / ".local/share" / directory)))
    return state.expanduser().resolve() / "wda"


def absolute_path(value: Path | str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise PrepareError(f"{label} must be an absolute path without '..' components.")
    if path.is_symlink():
        raise PrepareError(f"{label} must not be a symlink.")
    # macOS may expose system ancestors such as /var through a symlink. Resolve
    # these before checking containment; never follow a source/output leaf link.
    return path.resolve()


def validate_bundle(app: Path) -> list[Path]:
    if app.name != WDA_APP_NAME or not app.is_dir() or app.is_symlink():
        raise PrepareError(f"Source must be an existing {WDA_APP_NAME} directory, not a symlink.")
    plugins = app / "PlugIns"
    frameworks = app / "Frameworks"
    if plugins.is_symlink() or not plugins.is_dir():
        raise PrepareError("WDA must contain a real PlugIns directory, not a symlink.")
    if frameworks.is_symlink() or frameworks.exists() and not frameworks.is_dir():
        raise PrepareError("WDA Frameworks must be a real directory when present.")
    bundles = sorted(plugins.glob("*.xctest"))
    if not bundles or any(path.is_symlink() or not path.is_dir() for path in bundles):
        raise PrepareError("WDA must contain real PlugIns/*.xctest directories, not symlinks.")
    # copytree preserves relative, internal framework links. Reject external or
    # absolute links so later codesign can only visit this fresh copy, not the
    # original bundle or another location. Do not traverse directory symlinks.
    for directory, names, files in os.walk(app, followlinks=False):
        for name in [*names, *files]:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = path.readlink()
                try:
                    contained = not target.is_absolute() and path.resolve(strict=True).is_relative_to(app)
                except (OSError, RuntimeError):
                    contained = False
                if not contained:
                    raise PrepareError("WDA contains an absolute, external, broken, or cyclic symlink; use a self-contained Xcode build.")
            elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise PrepareError("WDA contains a special file; use a regular Xcode app bundle.")
    return bundles


def run_codesign(arguments: list[str], phase: str, runner: Callable) -> None:
    try:
        completed = runner(
            [CODESIGN, *arguments], check=False, capture_output=True, text=True,
            timeout=CODESIGN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrepareError(f"{phase} timed out; no configuration was changed.") from exc
    except OSError as exc:
        raise PrepareError(f"Cannot run macOS codesign during {phase}; check that Xcode command-line tools are installed.") from exc
    if completed.returncode:
        # codesign diagnostics can include the owner's identity or entitlements.
        # Keep them out of machine-readable/chat output; inspect details locally.
        raise PrepareError(f"{phase} failed (codesign exit {completed.returncode}); check the original Xcode signing and the exact local identity.")


def prepare_wda(
    source: Path | str,
    identity: str,
    output_root: Path | str | None = None,
    *,
    runner: Callable = subprocess.run,
) -> dict:
    """Prepare and verify a unique copy; subprocess injection supports offline tests."""
    if (not isinstance(identity, str) or not identity.strip() or identity.startswith("-")
            or identity.strip().startswith("<") or any(ord(char) < 32 or ord(char) == 127 for char in identity)):
        raise PrepareError("Provide the exact existing Apple Development identity name or SHA-1; ad-hoc signing and placeholders are not accepted.")
    source = absolute_path(source, "Source")
    validate_bundle(source)
    output_root = absolute_path(output_root if output_root is not None else default_output_root(), "Output root")
    if output_root.is_relative_to(source):
        raise PrepareError("Output root must not be the source bundle or a directory inside it.")
    if output_root.exists() and not output_root.is_dir():
        raise PrepareError("Output root must be a directory.")
    verify = ["--verify", "--deep", "--strict", "--verbose=2"]
    run_codesign([*verify, str(source)], "Source signature verification", runner)

    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdtemp creates a private 0700 directory, even if the selected parent is
    # shared. Every run uses a new directory; existing builds are never replaced.
    staging = Path(tempfile.mkdtemp(prefix="prepared.", dir=output_root))
    prepared = staging / WDA_APP_NAME
    try:
        shutil.copytree(source, prepared, symlinks=True)
        bundles = validate_bundle(prepared)
        removed = []
        for item in sorted((prepared / "Frameworks").glob("XC*")):
            if item.is_symlink() or item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)
            removed.append(item.name)
        for bundle in [*bundles, prepared]:
            phase = "WDA app signing" if bundle == prepared else "Nested XCTest bundle signing"
            run_codesign([
                "--force", "--sign", identity,
                "--preserve-metadata=identifier,entitlements", str(bundle),
            ], phase, runner)
        run_codesign([*verify, str(prepared)], "Prepared signature verification", runner)
        return {"ok": True, "prepared_path": str(prepared), "removed_frameworks": removed,
                "signature_verified": True, "phone_verified": False,
                "configuration_changed": False}
    except BaseException as exc:
        try:
            shutil.rmtree(staging)
        except OSError:
            raise PrepareError(f"Preparation failed; an incomplete private copy remains at {staging}. No configuration was changed.") from exc
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Success prints JSON with prepared_path. Install that signed app on the iPhone before configuring the default direct-wda backend; this command does not install it. Only the explicit appium backend accepts configure --prebuilt-wda-path. Failures remove only this run's incomplete copy; the original and current configuration remain unchanged. Never pass a certificate password as --identity.",
    )
    parser.add_argument("--source", required=True, type=Path,
                        help="Absolute path to your existing, validly signed WebDriverAgentRunner-Runner.app built in Xcode")
    parser.add_argument("--identity", required=True,
                        help="Exact existing local Apple Development signing identity name or SHA-1; no automatic identity selection")
    parser.add_argument("--output-root", type=Path,
                        help="Absolute parent for a new private prepared.* directory (default: $VICO_IPHONE_HOME/wda or ~/.local/share/iphone-use-wireless-direct/wda; explicit appium uses ~/.local/share/vico-iphone/wda)")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "darwin":
            raise PrepareError("WDA signing preparation requires macOS and its codesign tool.")
        value = prepare_wda(args.source, args.identity, args.output_root)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except (PrepareError, OSError, shutil.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "configuration_changed": False}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Preparation cancelled; no configuration was changed.",
                          "configuration_changed": False}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

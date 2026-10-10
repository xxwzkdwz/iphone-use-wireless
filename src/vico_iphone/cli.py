"""Owner-controlled setup. Credentials and device configuration stay outside Git."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .iphone_control import PhoneConfig
from .state import control_backend, default_port, operation_lock, read_json, state_home, write_json


def validate_backend_config(config: PhoneConfig, backend: str) -> None:
    if backend == "direct-wda":
        if config.wda_transport != "coredevice" or not config.use_preinstalled_wda:
            raise ValueError("Direct WDA requires coredevice transport and an already signed, installed WDA.")
        if config.prebuilt_wda_path:
            raise ValueError("Direct WDA does not install apps on connect. Install the signed WDA first, then configure without --prebuilt-wda-path.")


def doctor() -> dict:
    backend = control_backend()
    home = state_home(backend)
    config = read_json(home / "configuration/iphone-control.json")
    errors = []
    if config is not None:
        try:
            validated = PhoneConfig.model_validate(config)
        except ValueError:
            errors.append("Invalid local phone configuration; run configure again.")
        else:
            try:
                validate_backend_config(validated, backend)
            except ValueError as exc:
                errors.append(str(exc))
    xcode = None
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                xcode = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"platform": platform.system(), "macos_required": True, "backend": backend,
            "appium_server_required": backend == "appium",
            "default_port": default_port(backend),
            "tools": {name: bool(shutil.which(name)) for name in ("node", "npm", "uv", "codex", "xcrun")},
            "full_xcode": bool(xcode and ".app/Contents/Developer" in xcode),
            "configured": bool(config and config.get("enabled") and not errors),
            "transport": (config or {}).get("wda_transport"),
            "use_preinstalled_wda": (config or {}).get("use_preinstalled_wda", False),
            "state_directory": str(home), "errors": errors,
            "phone_verified": False,
            "note": ("Read-only host check. Direct WDA requires a signed, installed WDA and a prepared wireless tunnel; no Appium Server or automatic fallback."
                     if backend == "direct-wda" else
                     "Read-only host check for the explicitly selected Appium backend. A running bridge does not prove WDA can control the phone.")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent iPhone control for Codex")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Read-only host prerequisites; no phone actions")
    direct = commands.add_parser("init-direct", help="Explicitly copy an existing Appium profile into separate direct-WDA state without overwriting either profile")
    direct.add_argument("--from-state", type=Path, default=Path.home() / ".local/share/vico-iphone")
    direct.add_argument("--state-dir", type=Path, default=None,
                        help="Target profile; defaults to VICO_IPHONE_HOME or the separate direct-WDA state directory")
    direct.add_argument("--port", type=int, default=18401)
    configure = commands.add_parser("configure", help="Save your own device/signing setup in private local state")
    configure.add_argument("--udid", required=True)
    configure.add_argument("--team-id", required=True)
    configure.add_argument("--platform-version", required=True)
    configure.add_argument("--transport", choices=["coredevice", "default"], default="coredevice")
    configure.add_argument("--port", type=int, default=None,
                           help="Local control port; defaults to 18401 for direct WDA or 4737 for explicit Appium")
    configure.add_argument("--wda-bundle-id", default="com.vico.iphone.WebDriverAgentRunner")
    configure.add_argument("--preinstalled-wda", action=argparse.BooleanOptionalAction, default=None,
                           help="Use an already signed/installed WDA with a prepared wireless tunnel (default and required for direct WDA; iOS 18+, coredevice)")
    configure.add_argument("--prebuilt-wda-path", default="",
                           help="Appium only: optional absolute signed .app path to install on connect; requires --preinstalled-wda")
    args = parser.parse_args()
    try:
        backend = control_backend()
        if args.command == "doctor":
            result = doctor()
        elif args.command == "init-direct":
            source = args.from_state.expanduser().resolve()
            home = (args.state_dir or state_home("direct-wda")).expanduser().resolve()
            if source == home:
                raise ValueError("Direct WDA must use a different state directory; keep the original configuration.")
            existing = read_json(source / "configuration/iphone-control.json")
            if existing is None:
                raise ValueError("No existing independent phone configuration found at --from-state.")
            config = PhoneConfig.model_validate(existing)
            if not config.enabled or config.wda_transport != "coredevice" or not config.use_preinstalled_wda:
                raise ValueError("Migration requires an already configured wireless, preinstalled WDA setup.")
            if args.port == config.port:
                raise ValueError("Use a separate loopback port for the direct-WDA forwarder.")
            # Reuse device identity, not session/runtime state or Appium's installation path.
            config = PhoneConfig.model_validate({**config.model_dump(), "port": args.port,
                                                "prebuilt_wda_path": ""})
            path = home / "configuration/iphone-control.json"
            with operation_lock(home):
                if path.exists() or (home / "session.json").exists():
                    raise ValueError("Direct-WDA state already exists; it was not overwritten.")
                write_json(path, config.model_dump())
            result = {"configured": True, "backend": "direct-wda", "state_directory": str(home),
                      "port": config.port, "original_preserved": True, "phone_verified": False}
        else:
            home = state_home(backend)
            with operation_lock(home):
                if read_json(home / "session.json"):
                    raise ValueError("Disconnect or recover the existing phone session before changing configuration.")
                port = default_port(backend) if args.port is None else args.port
                preinstalled = backend == "direct-wda" if args.preinstalled_wda is None else args.preinstalled_wda
                config = PhoneConfig(enabled=True, port=port, udid=args.udid,
                                     team_id=args.team_id, platform_version=args.platform_version,
                                     wda_transport=args.transport, wda_bundle_id=args.wda_bundle_id,
                                     use_preinstalled_wda=preinstalled,
                                     prebuilt_wda_path=args.prebuilt_wda_path,
                                     control_scope="device", allowed_bundles=[])
                if not config.udid or config.udid.lower() == "auto" or not config.team_id or not config.platform_version:
                    raise ValueError("An explicit device ID, signing team and iOS version are required.")
                validate_backend_config(config, backend)
                path = home / "configuration/iphone-control.json"
                write_json(path, config.model_dump())
                result = {"configured": True, "backend": backend, "path": str(path), "transport": args.transport,
                          "use_preinstalled_wda": config.use_preinstalled_wda,
                          "port": config.port, "phone_verified": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

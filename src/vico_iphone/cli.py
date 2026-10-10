"""Owner-controlled setup. Credentials and device configuration stay outside Git."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys

from .iphone_control import PhoneConfig
from .state import operation_lock, read_json, state_home, write_json


def doctor() -> dict:
    home = state_home()
    config = read_json(home / "configuration/iphone-control.json")
    errors = []
    if config is not None:
        try:
            PhoneConfig.model_validate(config)
        except ValueError:
            errors.append("Invalid local phone configuration; run configure again.")
    xcode = None
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                xcode = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"platform": platform.system(), "macos_required": True,
            "tools": {name: bool(shutil.which(name)) for name in ("node", "npm", "uv", "codex", "xcrun")},
            "full_xcode": bool(xcode and ".app/Contents/Developer" in xcode),
            "configured": bool(config and config.get("enabled") and not errors),
            "transport": (config or {}).get("wda_transport"),
            "use_preinstalled_wda": (config or {}).get("use_preinstalled_wda", False),
            "state_directory": str(home), "errors": errors,
            "phone_verified": False,
            "note": "Read-only host check. A paired device or running bridge does not prove WDA can control the phone."}


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent iPhone control for Codex")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Read-only host prerequisites; no phone actions")
    configure = commands.add_parser("configure", help="Save your own device/signing setup in private local state")
    configure.add_argument("--udid", required=True)
    configure.add_argument("--team-id", required=True)
    configure.add_argument("--platform-version", required=True)
    configure.add_argument("--transport", choices=["coredevice", "default"], default="coredevice")
    configure.add_argument("--port", type=int, default=4737)
    configure.add_argument("--wda-bundle-id", default="com.vico.iphone.WebDriverAgentRunner")
    configure.add_argument("--preinstalled-wda", action="store_true",
                           help="Use an already signed/installed WDA with a separately running RemoteXPC tunnel (iOS 18+, coredevice)")
    configure.add_argument("--prebuilt-wda-path", default="",
                           help="Optional absolute signed .app path to install on connect; requires --preinstalled-wda")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = doctor()
        else:
            home = state_home()
            with operation_lock(home):
                if read_json(home / "session.json"):
                    raise ValueError("Disconnect or recover the existing phone session before changing configuration.")
                config = PhoneConfig(enabled=True, port=args.port, udid=args.udid,
                                     team_id=args.team_id, platform_version=args.platform_version,
                                     wda_transport=args.transport, wda_bundle_id=args.wda_bundle_id,
                                     use_preinstalled_wda=args.preinstalled_wda,
                                     prebuilt_wda_path=args.prebuilt_wda_path,
                                     control_scope="device", allowed_bundles=[])
                if not config.udid or config.udid.lower() == "auto" or not config.team_id or not config.platform_version:
                    raise ValueError("An explicit device ID, signing team and iOS version are required.")
                path = home / "configuration/iphone-control.json"
                write_json(path, config.model_dump())
                result = {"configured": True, "path": str(path), "transport": args.transport,
                          "use_preinstalled_wda": config.use_preinstalled_wda,
                          "port": args.port, "phone_verified": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

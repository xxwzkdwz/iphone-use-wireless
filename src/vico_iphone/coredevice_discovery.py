"""Read one configured device's live Apple development tunnel, without phone actions."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import signal
import stat
import tempfile
from pathlib import Path


QUERY_TIMEOUT_SECONDS = 10
MAX_DETAILS_BYTES = 128 * 1024


class CoreDeviceDiscoveryError(RuntimeError):
    """A discovery failure before any Appium session request has been submitted."""


def coredevice_environment(temporary_directory: Path) -> dict[str, str]:
    # Pairing/Xcode need the real home directory. Never forward model keys,
    # proxies, Python/Node preload options, or caller-supplied executable paths.
    return {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(temporary_directory), "LANG": "en_US.UTF-8"}


def tunnel_url_from_details(payload: object, udid: str) -> str:
    if (not isinstance(payload, dict) or "error" in payload
            or not isinstance(payload.get("info"), dict) or payload["info"].get("outcome") != "success"):
        raise CoreDeviceDiscoveryError("Apple 设备查询没有返回有效结果")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CoreDeviceDiscoveryError("Apple 设备查询没有返回有效结果")
    hardware = result.get("hardwareProperties")
    connection = result.get("connectionProperties")
    if not isinstance(hardware, dict) or hardware.get("udid") != udid:
        raise CoreDeviceDiscoveryError("Apple 设备查询结果与本地配置的设备不一致")
    if (not isinstance(connection, dict) or connection.get("pairingState") != "paired"
            or connection.get("tunnelState") != "connected"
            or connection.get("transportType") not in ("localNetwork", "wired")):
        raise CoreDeviceDiscoveryError("Apple 开发隧道尚未连接，请检查设备配对与连接状态")
    value = connection.get("tunnelIPAddress")
    try:
        if not isinstance(value, str) or "%" in value:
            raise ValueError()
        address = ipaddress.IPv6Address(value)
        if address not in ipaddress.IPv6Network("fc00::/7"):
            raise ValueError()
    except ValueError:
        raise CoreDeviceDiscoveryError("Apple 开发隧道没有返回有效的 IPv6 ULA 地址") from None
    return f"http://[{address.compressed}]:8100"


def _read_details(path: Path, udid: str) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DETAILS_BYTES:
            raise CoreDeviceDiscoveryError("Apple 设备查询结果过大或格式无效")
        data = stream.read(MAX_DETAILS_BYTES + 1)
    if len(data) > MAX_DETAILS_BYTES:
        raise CoreDeviceDiscoveryError("Apple 设备查询结果过大或格式无效")
    return tunnel_url_from_details(json.loads(data), udid)


async def _stop_query(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    # Only this query's detached process group is ours; no discovery/port kills.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await asyncio.wait_for(process.wait(), timeout=1)


async def discover_coredevice_wda_url(udid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", udid) or udid.lower() == "auto":
        raise CoreDeviceDiscoveryError("Apple 开发隧道需要明确的本地设备 UDID")
    try:
        with tempfile.TemporaryDirectory(prefix="vico-iphone-coredevice-") as directory:
            root = Path(directory)
            details = root / "details.json"
            descriptor = os.open(details, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            process = None
            # Shield process creation so cancellation cannot lose an already
            # spawned child before we obtain the handle needed to clean it up.
            creation = asyncio.create_task(asyncio.create_subprocess_exec(
                "/usr/bin/xcrun", "devicectl", "device", "info", "details",
                "--device", udid, "--timeout", "8", "--quiet", "--json-output", str(details),
                cwd=root, env=coredevice_environment(root), start_new_session=True,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            ))
            try:
                process = await asyncio.shield(creation)
                await asyncio.wait_for(process.wait(), timeout=QUERY_TIMEOUT_SECONDS)
                if process.returncode != 0:
                    raise CoreDeviceDiscoveryError("Apple 设备查询失败，请检查本地开发工具与设备连接")
                return _read_details(details, udid)
            finally:
                if process is None:
                    process = await creation
                if process.returncode is None:
                    await asyncio.shield(_stop_query(process))
    except asyncio.CancelledError:
        raise
    except CoreDeviceDiscoveryError:
        raise
    except asyncio.TimeoutError:
        raise CoreDeviceDiscoveryError("Apple 设备查询超时，本次尚未创建手机控制会话") from None
    except (OSError, ValueError, TypeError, RecursionError):
        raise CoreDeviceDiscoveryError("Apple 设备查询不可用或结果无效，本次尚未创建手机控制会话") from None

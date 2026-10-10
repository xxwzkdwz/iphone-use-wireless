"""Opt-in, local Appium bridge. No arbitrary WebDriver commands are model exposed."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import stat
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .coredevice_discovery import CoreDeviceDiscoveryError, discover_coredevice_wda_url


BRIDGE_STATUS_MAX_BYTES = 16 * 1024
BRIDGE_STATUS_MAX_AGE_SECONDS = 30
REMOTEXPC_REGISTRY_MAX_BYTES = 256 * 1024
REMOTEXPC_REGISTRY_TIMEOUT_SECONDS = 4
REMOTEXPC_REGISTRY_PORT_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "appium-xcuitest-driver-nodejs"
    / "strongbox"
    / "tunnelRegistryPort"
)
# A first xcodebuild may compile WDA's native sources. Leave room for Appium's
# reply and CoreDevice discovery outside that one attempt; never retry creation.
WDA_LAUNCH_TIMEOUT_MS = 300_000
PREINSTALLED_WDA_LAUNCH_TIMEOUT_MS = 60_000
WDA_STARTUP_ATTEMPTS = 1
PHONE_SESSION_REQUEST_TIMEOUT_SECONDS = WDA_LAUNCH_TIMEOUT_MS // 1000 + 60
PHONE_CONNECT_TOOL_TIMEOUT_SECONDS = PHONE_SESSION_REQUEST_TIMEOUT_SECONDS + 30
# Shared across this plugin's MCP processes, independent of an individual
# workspace/data directory. This does not coordinate with a running VICO app.
DEVICE_LEASE_DIRECTORY = Path.home() / ".local" / "state" / "vico-iphone" / "device-leases"
DEVICE_MARKER_MAX_BYTES = 8 * 1024


class PhoneError(RuntimeError):
    pass


class PhoneObservationRequired(PhoneError):
    """Read again before deciding on a new action; never replay a mutation."""


class PhoneSessionRequired(PhoneError):
    """No task owns a known or uncertain session; an explicit connect is needed."""


class PhoneBridgeError(PhoneError):
    """Distinguish an unsubmitted request from an unknown remote outcome."""

    def __init__(self, message: str, *, code: str | None = None,
                 request_outcome: Literal["not_sent", "rejected", "unknown"] = "unknown"):
        super().__init__(message)
        self.code = code
        self.request_outcome = request_outcome


class RemoteXPCTunnelError(RuntimeError):
    """The local Appium RemoteXPC registry cannot prove this device is ready."""


def device_owner_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _device_marker_path(udid: str) -> Path:
    key = hashlib.sha256(udid.encode("utf-8")).hexdigest()
    return DEVICE_LEASE_DIRECTORY / f"{key}.session.json"


def _read_device_marker(path: Path) -> dict | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
            raise PhoneError("共享设备会话记录无效，请检查本地状态")
        raw = stream.read(DEVICE_MARKER_MAX_BYTES + 1)
    if len(raw) > DEVICE_MARKER_MAX_BYTES:
        raise PhoneError("共享设备会话记录超过大小上限")
    try:
        marker = json.loads(raw)
        if (not isinstance(marker, dict) or marker.get("schema_version") != 1
                or type(marker.get("owner_pid")) is not int or marker["owner_pid"] <= 0
                or not isinstance(marker.get("owner_id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", marker["owner_id"])
                or not isinstance(marker.get("state_directory"), str)
                or not Path(marker["state_directory"]).is_absolute()
                or type(marker.get("bridge_port")) is not int
                or not 1024 <= marker["bridge_port"] <= 65535
                or marker.get("phase") not in {"connecting", "connected"}
                or marker.get("session_id") is not None
                and (not isinstance(marker["session_id"], str)
                     or not re.fullmatch(r"[A-Za-z0-9-]+", marker["session_id"]))):
            raise ValueError()
        return marker
    except (ValueError, TypeError, RecursionError):
        raise PhoneError("共享设备会话记录无效，请检查本地状态") from None


def validate_wda_url(value: str) -> str:
    """Only explicit loopback/private/CGNAT IPs; no DNS or URL credentials."""
    if not value:
        return ""
    message = "WDA 地址必须是无凭据、路径或查询参数的 HTTP(S) 回环、私有或 CGNAT IP 地址"
    if any(ord(char) < 33 or ord(char) > 126 for char in value) or any(char in value for char in "\\@%?#"):
        raise ValueError(message)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.path not in {"", "/"} or parsed.netloc.endswith(":")):
            raise ValueError(message)
        address = ipaddress.ip_address(parsed.hostname)
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError(message)
        if isinstance(address, ipaddress.IPv4Address):
            # CGNAT permits owner-configured Tailscale peers. Accepting an IP
            # does not establish or verify a VPN route or a running remote WDA.
            allowed = address.is_loopback or any(address in ipaddress.ip_network(cidr)
                                                  for cidr in ("10.0.0.0/8", "172.16.0.0/12",
                                                               "192.168.0.0/16", "100.64.0.0/10"))
            host = str(address)
        else:
            allowed = address == ipaddress.IPv6Address("::1") or address in ipaddress.ip_network("fc00::/7")
            host = f"[{address.compressed}]"
        if not allowed:
            raise ValueError(message)
        return f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")
    except ValueError:
        raise ValueError(message) from None


class PhoneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    # A fixed loopback address avoids exposing phone control to the LAN or model.
    port: int = Field(default=4737, ge=1024, le=65535)
    udid: str = Field(default="", pattern=r"^[A-Za-z0-9-]*$")
    team_id: str = Field(default="", pattern=r"^[A-Z0-9]*$")
    wda_bundle_id: str = Field(default="com.vico.iphone.WebDriverAgentRunner",
                               pattern=r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
    platform_version: str = Field(default="", pattern=r"^(?:[0-9]+(?:\.[0-9]+){0,2})?$")
    # Both empty preserves USB-managed WDA. wda_url attaches to a WDA the owner
    # already started; wda_base_url retains Appium's WDA startup lifecycle.
    # Neither target may be supplied through model tool arguments.
    wda_url: str = Field(default="", max_length=2048)
    wda_base_url: str = Field(default="", max_length=2048)
    wda_transport: Literal["default", "coredevice"] = "default"
    use_preinstalled_wda: bool = False
    prebuilt_wda_path: str = Field(default="", max_length=4096)
    allow_provisioning_registration: bool = False
    show_xcode_log: bool = False
    allowed_bundles: list[str] = Field(default_factory=list)
    control_scope: Literal["apps", "device"] = "device"

    @model_validator(mode="after")
    def validate_wireless_configuration(self) -> PhoneConfig:
        self.wda_url = validate_wda_url(self.wda_url)
        self.wda_base_url = validate_wda_url(self.wda_base_url)
        if self.prebuilt_wda_path:
            path = Path(self.prebuilt_wda_path)
            if (not path.is_absolute() or path.suffix != ".app"
                    or any(ord(char) < 32 for char in self.prebuilt_wda_path)):
                raise ValueError("prebuilt_wda_path 必须是无控制字符的绝对 .app 路径")
        if self.wda_base_url:
            base = urlsplit(self.wda_base_url)
            # The pinned driver ignores wdaBaseUrl's port and starts WDA using
            # plain HTTP on 8100. Reject addresses it would silently reinterpret.
            if base.scheme != "http" or base.port not in (None, 8100):
                raise ValueError("wda_base_url 自动启动模式只允许 HTTP，端口须省略或为 8100")
        if self.wda_url and self.wda_base_url:
            raise ValueError("wda_url 与 wda_base_url 不能同时配置")
        if self.wda_transport == "coredevice" and (self.wda_url or self.wda_base_url):
            raise ValueError("coredevice 传输不能与固定 WDA 地址同时配置")
        if ((self.wda_url or self.wda_base_url or self.wda_transport == "coredevice")
                and (not self.platform_version or not self.udid or self.udid.lower() == "auto")):
            raise ValueError("WDA 地址模式需要明确的 platform_version 和非 auto 的设备 udid")
        if self.prebuilt_wda_path and not self.use_preinstalled_wda:
            raise ValueError("prebuilt_wda_path 只能用于预安装 WDA 模式")
        if self.use_preinstalled_wda:
            try:
                platform_major = int(self.platform_version.split(".", 1)[0])
            except (TypeError, ValueError):
                platform_major = 0
            if self.wda_transport != "coredevice":
                raise ValueError("预安装 WDA 模式需要 coredevice 传输")
            if self.wda_url or self.wda_base_url:
                raise ValueError("预安装 WDA 模式不能与固定 WDA 地址同时配置")
            if (not self.udid or self.udid.lower() == "auto" or not self.platform_version
                    or platform_major < 18):
                raise ValueError("预安装 WDA 模式需要 iOS 18+、明确版本和非 auto 的设备 udid")
        return self


def _read_remotexpc_registry_port(path: Path = REMOTEXPC_REGISTRY_PORT_PATH) -> int:
    """Read Appium's local Strongbox pointer without following substituted files."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            details = os.fstat(source.fileno())
            if (not stat.S_ISREG(details.st_mode)
                    or details.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
                raise RemoteXPCTunnelError("RemoteXPC 注册表端口文件无效")
            raw = source.read(17)
    except FileNotFoundError as exc:
        raise RemoteXPCTunnelError("RemoteXPC 隧道尚未启动") from exc
    except OSError as exc:
        raise RemoteXPCTunnelError("无法读取 RemoteXPC 隧道状态") from exc
    try:
        text = raw.decode("ascii").strip()
        if not re.fullmatch(r"[0-9]{1,5}", text):
            raise ValueError
        port = int(text)
        if not 1 <= port <= 65535:
            raise ValueError
        return port
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteXPCTunnelError("RemoteXPC 注册表端口无效") from exc


async def require_remotexpc_tunnel(
    udid: str,
    *,
    registry_port_path: Path = REMOTEXPC_REGISTRY_PORT_PATH,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Prove a ready tunnel for this exact device before Appium receives POST /session."""
    port = _read_remotexpc_registry_port(registry_port_path)
    url = f"http://127.0.0.1:{port}/remotexpc/tunnels/{udid}"
    try:
        async with httpx.AsyncClient(
            timeout=REMOTEXPC_REGISTRY_TIMEOUT_SECONDS,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise RemoteXPCTunnelError("当前 iPhone 没有可用的 RemoteXPC 隧道")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > REMOTEXPC_REGISTRY_MAX_BYTES:
                        raise RemoteXPCTunnelError("RemoteXPC 隧道状态超过大小上限")
                    chunks.append(chunk)
        entry = json.loads(b"".join(chunks))
        if not isinstance(entry, dict) or entry.get("udid") != udid:
            raise RemoteXPCTunnelError("RemoteXPC 隧道设备不匹配")
        address = ipaddress.ip_address(entry.get("address", ""))
        rsd_port, services = entry.get("rsdPort"), entry.get("services")
        if (not isinstance(address, ipaddress.IPv6Address)
                or address not in ipaddress.ip_network("fc00::/7")
                or type(rsd_port) is not int or not 1 <= rsd_port <= 65535
                or not isinstance(services, dict) or not services):
            raise RemoteXPCTunnelError("RemoteXPC 隧道尚未就绪")
        return {"address": address.compressed, "rsd_port": rsd_port,
                "service_count": len(services)}
    except asyncio.CancelledError:
        raise
    except RemoteXPCTunnelError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, RecursionError) as exc:
        raise RemoteXPCTunnelError("RemoteXPC 隧道不可用") from exc


class PhoneAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    action: Literal["tap", "type", "swipe", "home"]
    element_ref: str | None = None
    x: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    y: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    direction: Literal["up", "down", "left", "right"] | None = None
    text: str | None = Field(default=None, max_length=2000)
    target_description: str | None = Field(default=None, max_length=160)


MAX_SOURCE_NODES = 10_000
ELEMENT_TEXT_LIMIT = 160


def parse_elements(
    source: str,
    *,
    full_values: bool = True,
    limit: int | None = None,
) -> tuple[list[dict], dict[str, str], dict[str, int | bool]]:
    if limit is not None and (type(limit) is not int or limit < 1):
        raise PhoneError("组件数量限制必须是正整数或空值")
    if len(source.encode()) > 2_000_000 or re.search(r"<!\s*(DOCTYPE|ENTITY)", source, re.I):
        raise PhoneError("手机界面树过大或格式不安全")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise PhoneError("无法解析手机界面树") from exc
    # Appium's page source adds a display-only scope wrapper. WDA's XPath
    # queries start at the application itself, not at this wrapper.
    if root.tag == "AppiumAUT":
        if len(root) != 1:
            raise PhoneError("界面树包含多个应用根，不能可靠定位")
        root = root[0]
    elements, paths = [], {}
    visible_meaningful_total = 0
    # Iterative traversal also bounds depth/size of untrusted UI data.
    stack = [(root, "/*[1]", 0, False)]
    visited = 0
    while stack:
        node, path, depth, keyboard_ancestor = stack.pop()
        visited += 1
        if visited > MAX_SOURCE_NODES or depth > 100:
            raise PhoneError("手机界面树超出读取上限")
        meaningful = (node.tag not in {"XCUIElementTypeOther", "XCUIElementTypeWindow", "XCUIElementTypeApplication"}
                      or any(node.get(k) for k in ("name", "label", "value")))
        in_keyboard = keyboard_ancestor or node.tag == "XCUIElementTypeKeyboard"
        if node.get("visible") == "true" and meaningful:
            visible_meaningful_total += 1
        if (node.get("visible") == "true" and meaningful
                and (limit is None or len(elements) < limit)):
            ref = f"e{len(elements) + 1}"
            item = {key: node.get(key, "") for key in (
                "type", "name", "label", "value", "enabled", "visible", "accessible", "traits",
                "x", "y", "width", "height",
            )}
            item["type"] = item["type"] or node.tag
            item["keyboard_ancestor"] = "true" if keyboard_ancestor else "false"
            if not full_values:
                item = {key: value[:ELEMENT_TEXT_LIMIT] for key, value in item.items()}
            # Never expose a secure field's value, even when a driver returns one.
            if "SecureTextField" in node.tag:
                item["value"] = "[redacted]"
            item["ref"] = ref
            elements.append(item)
            paths[ref] = path
        children = list(node)
        stack.extend((child, f"{path}/*[{index + 1}]", depth + 1, in_keyboard)
                     for index, child in reversed(list(enumerate(children))))
    return elements, paths, {
        "total": visible_meaningful_total,
        "retained": len(elements),
        "truncated": visible_meaningful_total > len(elements),
    }


def xpath_literal(value: str) -> str:
    """Quote a UI-provided value as data, including values containing both quotes."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in value.split("'")) + ")"


def component_locator(item: dict) -> str:
    """Resolve this observed target semantically, never by its old sibling index.

    An unrelated clock/list update must not invalidate every component. A small
    two-point layout tolerance permits rounding/settling, while changed target
    identity, usability, or significant movement requires another observation.
    """
    kind = item.get("type", "")
    if (not re.fullmatch(r"XCUIElementType[A-Za-z0-9]+", kind)
            or item.get("enabled") != "true" or item.get("visible") != "true"):
        raise PhoneObservationRequired("组件不可见或不可操作，请重新观察或按截图定位")
    try:
        rect = {key: float(item[key]) for key in ("x", "y", "width", "height")}
        if (any(not math.isfinite(value) or abs(value) > 10000 for value in rect.values())
                or rect["width"] <= 0 or rect["height"] <= 0):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise PhoneObservationRequired("组件位置无效，请重新观察或按截图定位") from None
    conditions = [f"self::{kind}", "@visible = 'true'", "@enabled = 'true'"]
    identity = ["name", "label"]
    # Text entry changes a field's value without changing its identity. Secure
    # values are never retained or used as locators. Other anonymous controls may
    # expose their only identifying text as value.
    if not item.get("name") and not item.get("label") and kind not in {
        "XCUIElementTypeTextField", "XCUIElementTypeTextView", "XCUIElementTypeSearchField",
        "XCUIElementTypeSecureTextField",
    }:
        identity.append("value")
    for key in identity:
        value = item.get(key, "")
        if len(value) > 2048:
            raise PhoneObservationRequired("组件标识过长，请按截图定位或重新观察")
        conditions.append(f"string(@{key}) = {xpath_literal(value)}")
    for key, value in rect.items():
        conditions.extend((f"number(@{key}) >= {value - 2:.6f}",
                           f"number(@{key}) <= {value + 2:.6f}"))
    return "//*[" + " and ".join(conditions) + "]"


class IPhoneController:
    def __init__(self, data_directory: Path, *, config: PhoneConfig | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.data_directory = data_directory
        self.config_override = config
        self.transport = transport
        self.session_id: str | None = None
        self.owner: str | None = None
        self.bundle: str | None = None
        self.observation: dict | None = None
        self.uncertain = False
        self.lock = asyncio.Lock()
        self.lease = None
        self.config = PhoneConfig()
        self.full_access_session = False
        self.last_diagnostic: dict | None = None
        # Unlike task identifiers, this nonce can be stored without tying device
        # state to any conversation. It also prevents a same-PID takeover.
        self.device_owner_id = uuid.uuid4().hex

    @property
    def control_scope(self) -> str:
        return "device" if self.full_access_session else self.config.control_scope

    def load_config(self) -> PhoneConfig:
        try:
            if self.config_override is not None:
                # model_copy(update=...) does not validate updates. Revalidate the
                # same boundary used for disk configuration before any request.
                config = PhoneConfig.model_validate(self.config_override.model_dump())
            else:
                path = self.data_directory / "configuration" / "iphone-control.json"
                if not path.exists():
                    return PhoneConfig()
                config = PhoneConfig.model_validate_json(path.read_text())
        except (ValueError, OSError) as exc:
            raise PhoneError("iPhone 控制配置无效，请由用户检查本地配置") from exc
        if any(not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", b)
               for b in config.allowed_bundles):
            raise PhoneError("允许的 App bundle ID 无效")
        return config

    def bridge_status(self, config: PhoneConfig) -> dict:
        """Read the bridge service's bounded health snapshot; never probe or start a bridge."""
        note = "这是本地服务最近一次控制桥检查快照，不证明手机在线、解锁或 WDA 可用。"

        def unavailable(code: str, message: str) -> dict:
            return {"state": "unknown", "port": config.port, "fresh": False,
                    "error_code": code, "error": message, "note": note}

        path = self.data_directory / "runtime" / "iphone-bridge-status.json"
        try:
            # Do not block on a FIFO or follow a substituted status-file symlink.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    return unavailable("bridge_status_invalid", "本地控制桥状态文件无效。")
                raw = source.read(BRIDGE_STATUS_MAX_BYTES + 1)
            if len(raw) > BRIDGE_STATUS_MAX_BYTES:
                return unavailable("bridge_status_invalid", "本地控制桥状态文件超过大小上限。")
            status = json.loads(raw)
            states = {"disabled", "starting", "ready", "external", "failed", "stopped"}
            if (not isinstance(status, dict) or type(status.get("schema_version")) is not int
                    or status["schema_version"] != 1 or status.get("state") not in states
                    or type(status.get("port")) is not int or type(status.get("managed")) is not bool):
                return unavailable("bridge_status_invalid", "本地控制桥状态格式无效。")
            if status["port"] != config.port:
                return unavailable("bridge_status_port_mismatch", "本地控制桥状态对应的端口与当前配置不同。")
            instance, pid = status.get("instance_id"), status.get("pid")
            updated_at, code, message = status.get("updated_at"), status.get("error_code"), status.get("error")
            if (instance is not None and (not isinstance(instance, str)
                                         or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", instance))
                    or pid is not None and (type(pid) is not int or pid <= 0)
                    or not isinstance(updated_at, str) or len(updated_at) > 64
                    or code is not None and (not isinstance(code, str)
                                             or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", code))
                    or message is not None and (not isinstance(message, str) or len(message) > 600)):
                return unavailable("bridge_status_invalid", "本地控制桥状态字段无效。")
            if (status["state"] == "ready" and (not status["managed"] or instance is None)
                    or status["state"] == "external" and status["managed"]):
                return unavailable("bridge_status_invalid", "本地控制桥状态与进程归属不一致。")
            updated = datetime.fromisoformat(updated_at)
            if updated.tzinfo is None:
                return unavailable("bridge_status_invalid", "本地控制桥状态缺少有效更新时间。")
            age = time.time() - updated.timestamp()
            if age < -5:
                return unavailable("bridge_status_invalid", "本地控制桥状态更新时间无效。")
            if message:
                for private in (config.udid, config.team_id):
                    if private:
                        message = message.replace(private, "[redacted]")
            result = {"schema_version": 1, "state": status["state"], "port": status["port"],
                      "managed": status["managed"], "instance_id": instance, "pid": pid,
                      "updated_at": updated_at, "error_code": code, "error": message,
                      "fresh": age <= BRIDGE_STATUS_MAX_AGE_SECONDS, "note": note}
            if not result["fresh"]:
                result.update(state="unknown", reported_state=status["state"],
                              error_code="bridge_status_stale", error="本地控制桥状态已过期，当前是否可用尚未确认。")
            return result
        except FileNotFoundError:
            return unavailable("bridge_status_missing", "尚未收到本地控制桥状态。")
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            return unavailable("bridge_status_invalid", "无法读取有效的本地控制桥状态。")

    async def request(self, method: str, path: str, body=None, timeout=30):
        # No retries: a lost HTTP reply does not imply a tap was not executed.
        diagnostic = {"phase": "bridge", "method": method, "path": path}
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{self.config.port}", timeout=timeout,
                trust_env=False, follow_redirects=False, transport=self.transport,
            ) as client:
                async with client.stream(method, path, json=body) as response:
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 24 * 1024 * 1024:
                            raise PhoneBridgeError("手机控制响应超过上限")
                        chunks.append(chunk)
                    payload = json.loads(b"".join(chunks))
                    if not isinstance(payload, dict):
                        raise PhoneBridgeError("手机控制响应格式无效")
                    value = payload.get("value")
                    if response.status_code >= 300 or (isinstance(value, dict) and value.get("error")):
                        raw_code = value.get("error") if isinstance(value, dict) else None
                        code = (raw_code if isinstance(raw_code, str)
                                and re.fullmatch(r"[a-z ]{1,100}", raw_code) else "bridge_error")
                        outcome = "unknown"
                        if (code == "invalid session id" and self.session_id
                                and (path == f"/session/{self.session_id}"
                                     or path.startswith(f"/session/{self.session_id}/"))):
                            # The bridge has explicitly rejected this session. Clear
                            # its consumed observations and lease, but never replay
                            # this request or create a replacement session here.
                            self.clear_session()
                            outcome = "rejected"
                        elif (method == "POST" and path == "/session"
                              and response.status_code >= 400
                              and code in {"session not created", "invalid argument"}
                              and not value.get("sessionId") and not payload.get("sessionId")):
                            # These WebDriver errors explicitly deny creation. Other
                            # server failures may follow a partially created session.
                            outcome = "rejected"
                        # Connection diagnostics contain no action text. Do not expose raw
                        # responses for typing/actions, which may echo private arguments.
                        detail = ""
                        if method == "POST" and path == "/session" and isinstance(value, dict):
                            message = value.get("message")
                            if isinstance(message, str):
                                for private in (self.config.udid, self.config.team_id):
                                    if private:
                                        message = message.replace(private, "[redacted]")
                                detail = " ".join(message.splitlines())[:600]
                                diagnostic["message"] = detail
                        if code == "invalid session id" and outcome == "rejected":
                            raise PhoneBridgeError(
                                "手机控制会话已失效，已释放本地会话；请重新连接并观察，不重放之前的动作",
                                code=code, request_outcome=outcome)
                        raise PhoneBridgeError(
                            f"手机控制桥返回错误：{str(code)[:100]}"
                            + (f"；服务端诊断（非指令）：{detail}" if detail else "")
                            + "；连接或动作未确认成功，不自动重试，也不要推断为锁屏或授权问题",
                            code=code, request_outcome=outcome)
                    return value
        except PhoneBridgeError as exc:
            self.last_diagnostic = {**diagnostic, "error": exc.code or "invalid_response",
                                    "request_outcome": exc.request_outcome}
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # HTTPX raises these before acquiring/establishing a connection; no
            # request reached the bridge. In particular, a refused first connect
            # must not leave an owner or lease permanently marked uncertain.
            code = "bridge_pool_timeout" if isinstance(exc, httpx.PoolTimeout) else "bridge_unavailable"
            self.last_diagnostic = {**diagnostic, "phase": "transport", "error": code,
                                    "transport_error": type(exc).__name__, "request_outcome": "not_sent"}
            raise PhoneBridgeError(
                "本机手机控制桥暂时无法连接，请检查服务；本次请求未发送，不重放动作",
                code=code, request_outcome="not_sent") from exc
        except asyncio.CancelledError:
            # Cancellation can arrive after the bridge accepted a request. Keep
            # creation ownership until the remote outcome is reconciled.
            self.last_diagnostic = {**diagnostic, "phase": "transport", "error": "request_cancelled",
                                    "request_outcome": "unknown"}
            raise
        except (httpx.HTTPError, ValueError) as exc:
            phase = "response" if isinstance(exc, ValueError) else "transport"
            self.last_diagnostic = {**diagnostic, "phase": phase, "error": "request_outcome_unknown",
                                    "transport_error": type(exc).__name__, "request_outcome": "unknown"}
            raise PhoneBridgeError("手机控制响应丢失或无效；请求结果未知，请先核验，勿自动重放",
                                   code="request_outcome_unknown") from exc

    async def mobile(self, name: str, args: dict | None = None):
        return await self.request("POST", f"/session/{self.session_id}/execute/sync",
                                  {"script": f"mobile: {name}", "args": [args or {}]})

    def require_owner(self, task_id: str):
        if not self.session_id and self.owner is None and not self.uncertain:
            raise PhoneSessionRequired("当前没有手机控制会话；请先 iphone_session(connect)，然后重新观察")
        if not self.session_id or self.owner != task_id or self.uncertain:
            raise PhoneError("当前任务没有手机控制会话；请先连接，不能接管其他任务的会话")
        latest = self.load_config()
        if latest != self.config or not latest.enabled:
            self.observation = None
            raise PhoneError("手机控制配置已改变或停用，请断开会话后重新连接")

    def release_lease(self):
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    def acquire_lease(self):
        """Take a per-device OS lease before any session creation request."""
        DEVICE_LEASE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = hashlib.sha256(self.config.udid.encode("utf-8")).hexdigest()
        path = DEVICE_LEASE_DIRECTORY / f"{key}.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.lease = os.fdopen(descriptor, "a+")
        try:
            details = os.fstat(self.lease.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
                raise PhoneError("手机控制锁文件无效")
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if _read_device_marker(_device_marker_path(self.config.udid)) is not None:
                raise PhoneError("这台手机留有未清理的会话记录；请在原配置目录核验并重置所属控制桥，不自动新建或重放")
        except BlockingIOError as exc:
            self.release_lease()
            raise PhoneError("另一个 iphone-use 进程正在控制这台手机") from exc
        except BaseException:
            self.release_lease()
            raise

    def _write_device_marker(self, phase: str) -> None:
        """Write before POST /session; a process crash must not erase ownership."""
        if self.lease is None:
            raise PhoneError("缺少设备控制锁，不能记录会话")
        path = _device_marker_path(self.config.udid)
        marker = {"schema_version": 1, "owner_pid": os.getpid(),
                  "owner_id": self.device_owner_id,
                  "state_directory": str(self.data_directory.resolve()),
                  "bridge_port": self.config.port, "phase": phase,
                  "session_id": self.session_id}
        descriptor, temporary = tempfile.mkstemp(prefix=".device-session-", dir=DEVICE_LEASE_DIRECTORY)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(marker, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_descriptor = os.open(DEVICE_LEASE_DIRECTORY, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _clear_owned_device_marker(self) -> None:
        if not self.config.udid or self.lease is None:
            return
        path = _device_marker_path(self.config.udid)
        marker = _read_device_marker(path)
        if marker and marker["owner_id"] == self.device_owner_id:
            path.unlink()

    def clear_abandoned_lease_after_bridge_reset(self) -> bool:
        """After a verified owned-bridge reset, clear only this home's stale marker.

        The caller must first prove that the plugin-owned Appium bridge stopped.
        A matching live owner can be cleared only when this very controller owns
        an unknown connection with no session ID. This method does not clear the
        controller's memory; the caller can then call clear_session().
        """
        config = self.load_config()
        if not config.udid:
            return False
        DEVICE_LEASE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = _device_marker_path(config.udid)
        lock_path = path.with_name(path.name.removesuffix(".session.json") + ".lock")
        borrowed_descriptor = None
        try:
            if self.lease is None:
                borrowed_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                try:
                    fcntl.flock(borrowed_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
            elif self.config.udid != config.udid:
                return False
            marker = _read_device_marker(path)
            if marker is None:
                return True
            if (marker["state_directory"] != str(self.data_directory.resolve())
                    or marker["bridge_port"] != config.port):
                return False
            own_unknown = (marker["owner_id"] == self.device_owner_id
                           and self.uncertain and self.session_id is None)
            if device_owner_alive(marker["owner_pid"]) and not own_unknown:
                return False
            path.unlink()
            return True
        finally:
            if borrowed_descriptor is not None:
                os.close(borrowed_descriptor)

    def clear_session(self):
        """Only after confirmed deletion/expiry, or a request that did not create a session."""
        try:
            self._clear_owned_device_marker()
        finally:
            self.session_id = self.owner = self.bundle = self.observation = None
            self.uncertain = False
            self.full_access_session = False
            self.release_lease()

    async def session(self, task_id: str, action: str, bundle: str | None = None, *, full_access: bool = False) -> dict:
        async with self.lock:
            if action not in {"connect", "disconnect", "status"}:
                raise PhoneError("未知会话操作")
            if action == "status":
                config = self.load_config()
                return {"enabled": config.enabled, "connected": self.session_id is not None,
                        "owned_by_this_task": self.owner == task_id,
                        "connection_outcome_unknown": self.uncertain,
                        "last_diagnostic": self.last_diagnostic,
                        "allowed_bundles": config.allowed_bundles,
                        "control_scope": self.control_scope if self.owner == task_id else config.control_scope,
                        "configured_control_scope": config.control_scope,
                        "bridge": self.bridge_status(config),
                        "note": "状态不是手机在线证明；observe 才读取真机。"}
            if action == "disconnect":
                if self.owner not in (None, task_id):
                    raise PhoneError("只能由持有会话的任务断开")
                self.observation = None
                if self.uncertain:
                    raise PhoneError("连接结果未知；请先核验本地 Appium 会话并完成显式恢复，不能自动重试连接")
                if self.session_id:
                    try:
                        await self.request("DELETE", f"/session/{self.session_id}", timeout=10)
                    except PhoneBridgeError as exc:
                        if exc.code != "invalid session id":
                            raise
                self.clear_session()
                return {"connected": False}
            if self.session_id or self.uncertain:
                raise PhoneError("已有会话或连接结果未知，请先检查状态，不能自动新建重试")
            self.config = self.load_config()
            if not self.config.enabled or not self.config.udid or not self.config.team_id:
                raise PhoneError("iPhone 控制尚未配置启用；需要 Mac 配对、开发者模式和官方 UI Automation 授权")
            if not full_access and self.config.control_scope == "apps":
                if not bundle or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle):
                    raise PhoneError("指定 App 模式需要有效的初始 App bundle ID，不要猜测应用标识")
                if bundle not in self.config.allowed_bundles:
                    raise PhoneError("这个 App 不在用户配置的允许列表内")
            else:
                # Device sessions attach to the phone, not an app. Ignore even a
                # stale/hallucinated bundle from an older tool context. Never launch,
                # install or verify a target app as a side effect of connecting.
                bundle = None
            wda_base_url = self.config.wda_base_url
            if self.config.wda_transport == "coredevice":
                try:
                    discovered_wda_url = await discover_coredevice_wda_url(self.config.udid)
                except asyncio.CancelledError:
                    self.last_diagnostic = {"phase": "coredevice_discovery",
                                            "error": "discovery_cancelled", "request_outcome": "not_sent"}
                    raise
                except CoreDeviceDiscoveryError as exc:
                    self.last_diagnostic = {"phase": "coredevice_discovery",
                                            "error": "coredevice_unavailable", "request_outcome": "not_sent"}
                    raise PhoneBridgeError(str(exc), code="coredevice_unavailable",
                                           request_outcome="not_sent") from exc
                if self.config.use_preinstalled_wda:
                    # Discovery proves this exact paired device is connected. The
                    # preinstalled path deliberately does not pass its ULA as
                    # wdaBaseUrl: Appium owns a local :8100 RemoteXPC forwarder.
                    try:
                        await require_remotexpc_tunnel(self.config.udid)
                    except asyncio.CancelledError:
                        self.last_diagnostic = {"phase": "remotexpc_tunnel",
                                                "error": "tunnel_check_cancelled",
                                                "request_outcome": "not_sent"}
                        raise
                    except RemoteXPCTunnelError as exc:
                        self.last_diagnostic = {"phase": "remotexpc_tunnel",
                                                "error": "remotexpc_tunnel_unavailable",
                                                "request_outcome": "not_sent"}
                        raise PhoneBridgeError(
                            str(exc), code="remotexpc_tunnel_unavailable",
                            request_outcome="not_sent",
                        ) from exc
                else:
                    wda_base_url = discovered_wda_url
            self.acquire_lease()
            self.owner, self.bundle, self.uncertain = task_id, bundle, True
            self.full_access_session = full_access
            self.last_diagnostic = None
            try:
                self._write_device_marker("connecting")
            except (OSError, PhoneError):
                # The session request has not been submitted yet.
                self.clear_session()
                raise
            preinstalled = self.config.use_preinstalled_wda
            capabilities = {"alwaysMatch": {
                "platformName": "iOS", "appium:automationName": "XCUITest",
                "appium:udid": self.config.udid,
                **({"appium:bundleId": bundle} if bundle else {}),
                "appium:updatedWDABundleId": self.config.wda_bundle_id,
                "appium:noReset": True, "appium:autoLaunch": False,
                "appium:shouldTerminateApp": False,
                "appium:newCommandTimeout": 180,
                "appium:autoAcceptAlerts": False, "appium:autoDismissAlerts": False,
                **({"appium:xcodeOrgId": self.config.team_id,
                    "appium:xcodeSigningId": "Apple Development",
                    "appium:allowProvisioningDeviceRegistration": self.config.allow_provisioning_registration,
                    "appium:showXcodeLog": self.config.show_xcode_log} if not preinstalled else {}),
                **({"appium:usePreinstalledWDA": True,
                    **({"appium:prebuiltWDAPath": self.config.prebuilt_wda_path}
                       if self.config.prebuilt_wda_path else {})} if preinstalled else {}),
                **({"appium:wdaLaunchTimeout": (PREINSTALLED_WDA_LAUNCH_TIMEOUT_MS
                                                 if preinstalled else WDA_LAUNCH_TIMEOUT_MS),
                    "appium:wdaStartupRetries": WDA_STARTUP_ATTEMPTS} if not self.config.wda_url else {}),
                **({"appium:webDriverAgentUrl": self.config.wda_url, "appium:skipLogCapture": True}
                   if self.config.wda_url else {}),
                **({"appium:wdaBaseUrl": wda_base_url, "appium:skipLogCapture": True}
                   if wda_base_url else {}),
                **({"appium:platformVersion": self.config.platform_version}
                   if self.config.platform_version else {}),
            }}
            try:
                value = await self.request("POST", "/session", {"capabilities": capabilities},
                                           timeout=PHONE_SESSION_REQUEST_TIMEOUT_SECONDS)
            except PhoneBridgeError as exc:
                if exc.request_outcome in {"not_sent", "rejected"}:
                    self.clear_session()
                raise
            sid = value.get("sessionId") if isinstance(value, dict) else None
            if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9-]+", sid):
                self.last_diagnostic = {"phase": "bridge", "method": "POST", "path": "/session",
                                        "error": "invalid_session_response", "request_outcome": "unknown"}
                raise PhoneBridgeError("控制桥没有返回有效会话；请检查服务，不自动重复连接",
                                       code="invalid_session_response")
            self.session_id, self.uncertain = sid, False
            self._write_device_marker("connected")
            return {"connected": True, "bundle_id": bundle, "control_scope": self.control_scope,
                    "configured_control_scope": self.config.control_scope,
                    "lifetime": "mcp_session",
                    "cleanup": "请显式 disconnect 释放会话；MCP 服务停止时会尝试清理，但不保证远端会话已释放。",
                    "next": ("直接 iphone_observe 读取当前屏幕；可显式启动已知 bundle ID 的 App，"
                             "或按截图点击图标，需要时 home 返回主屏幕。"
                             if self.control_scope == "device" else
                             "请用户把授权目标 App 放到前台并保持解锁，然后 iphone_observe。")}

    async def finish(self, task_id: str | None = None) -> dict | None:
        """Best-effort explicit/service-shutdown cleanup; never taps or launches apps."""
        if self.owner is None or (task_id is not None and self.owner != task_id):
            return None
        try:
            return await self.session(self.owner, "disconnect")
        except (PhoneError, OSError) as exc:
            # Retain ownership on uncertain cleanup: do not hand an active device
            # to another task or pretend that automation has stopped.
            self.observation = None
            previous = self.last_diagnostic
            self.last_diagnostic = {"phase": "disconnect", "message": str(exc)}
            if previous:
                # Keep the original connection/transport evidence through repeated
                # cleanup attempts without recursively growing diagnostics.
                self.last_diagnostic["cause"] = previous.get("cause", previous)
            return {"connected": self.session_id is not None, "needs_attention": True,
                    "message": str(exc)}

    async def foreground(self, *, for_action: bool = False, expected: str | None = None):
        expected = expected if self.control_scope == "device" else self.bundle
        seen = []
        for attempt in range(3):
            if await self.mobile("isLocked") is not False:
                raise PhoneError("手机锁定或锁屏状态未知，请用户解锁，不自动解锁")
            try:
                app = await self.mobile("activeAppInfo")
                actual = app.get("bundleId") if isinstance(app, dict) else None
            except PhoneBridgeError as exc:
                if exc.code not in {"stale element reference", "no such element"}:
                    raise
                # XCTest's accessibility application handle can expire while an
                # app settles. Refresh this read, not the user's last action.
                actual = f"read_error:{exc.code}"
            seen.append(str(actual)[:160])
            valid = isinstance(actual, str) and re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", actual)
            if valid and (expected is None or actual == expected):
                if attempt:
                    self.last_diagnostic = {"phase": "foreground", "expected_bundle": expected,
                                            "observed_bundles": seen, "recovered": True}
                return actual
            if attempt < 2:
                await asyncio.sleep(0.2)  # Only read-only checks are retried.
        self.last_diagnostic = {"phase": "foreground", "expected_bundle": expected,
                                "observed_bundles": seen, "recovered": False}
        raise PhoneObservationRequired(
            f"暂未确认当前界面（期望 {expected or '可识别的前台 App'}，检测到 {', '.join(seen)}）；"
            "未执行动作。可重新观察；持续异常再请用户检查，不推断用户切换了 App"
        )

    async def window_rect(self):
        # Physical screen coordinates must not depend on the target app's AX
        # window snapshot, which can be stale even while screenshots work.
        info = await self.mobile("deviceScreenInfo")
        size = info.get("screenSize", {}) if isinstance(info, dict) else {}
        rect = {"x": 0, "y": 0, "width": size.get("width"), "height": size.get("height")}
        if not isinstance(rect, dict) or any(not isinstance(rect.get(k), (int, float)) or not math.isfinite(rect[k])
            for k in ("x", "y", "width", "height")) or not (0 < rect["width"] < 10000 and 0 < rect["height"] < 10000):
            raise PhoneError("手机坐标范围无效")
        return rect

    async def observe(self, task_id: str, *, include_elements: bool = True,
                      capture_image: bool = True, element_limit: int | None = None) -> tuple[dict, bytes]:
        """Return one stable observation, absorbing brief notification/app jitter."""

        last_error: PhoneObservationRequired | None = None
        for attempt in range(3):
            try:
                return await self._observe_once(
                    task_id,
                    include_elements=include_elements,
                    capture_image=capture_image,
                    element_limit=element_limit,
                )
            except PhoneObservationRequired as exc:
                last_error = exc
                # Only transient foreground-app reads are retried here. A real
                # orientation/geometry change, lock, or configuration change
                # remains an immediate safety boundary.
                if "暂未确认当前界面" not in str(exc):
                    raise
                if attempt < 2:
                    await asyncio.sleep(0.08)
        assert last_error is not None
        raise last_error

    async def _observe_once(self, task_id: str, *, include_elements: bool = True,
                            capture_image: bool = True,
                            element_limit: int | None = None) -> tuple[dict, bytes]:
        async with self.lock:
            self.require_owner(task_id)
            self.observation = None
            if not include_elements and not capture_image:
                raise PhoneError("观察需要组件或截图至少一种；未获取任何界面信息")
            observed_bundle = await self.foreground()
            rect = await self.window_rect()
            elements = []
            elements_metadata: dict[str, int | bool] = {
                "total": 0,
                "retained": 0,
                "truncated": False,
            }
            components_status = "not_requested"
            if include_elements:
                try:
                    # One best-effort AX read, optionally followed by a fresh PNG.
                    # No full-tree byte hash: components are revalidated individually
                    # at action time, so unrelated updates need not block observation.
                    source = await self.request("GET", f"/session/{self.session_id}/source")
                    if not isinstance(source, str):
                        raise PhoneError("手机界面树无效")
                    elements, _, elements_metadata = parse_elements(source, full_values=True, limit=element_limit)
                    components_status = "available"
                except PhoneError as exc:
                    # A failed read cannot authorize a lost/unknown session. Do not
                    # catch cancellation, lock/config/app checks, or PNG failures.
                    if (isinstance(exc, PhoneBridgeError)
                            and exc.code not in {"stale element reference", "no such element"}):
                        raise
                    self.require_owner(task_id)
                    elements = []
                    elements_metadata = {"total": 0, "retained": 0, "truncated": False}
                    components_status = "unavailable"
            if not capture_image and components_status != "available":
                raise PhoneObservationRequired("组件读取失败，未获取截图；请重新观察或显式请求截图")
            png = b""
            width, height = None, None
            if capture_image:
                encoded = await self.request("GET", f"/session/{self.session_id}/screenshot")
                try:
                    png = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as exc:
                    raise PhoneError("手机截图格式无效") from exc
                if len(png) > 16 * 1024 * 1024 or len(png) < 24 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise PhoneError("手机截图不是有效大小的 PNG")
                width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
                if not (0 < width <= 10000 and 0 < height <= 10000):
                    raise PhoneError("手机截图尺寸无效")
            await self.foreground(expected=observed_bundle)
            after_rect = await self.window_rect()
            if rect != after_rect:
                raise PhoneObservationRequired("观察期间手机尺寸或方向变化，请重新观察")
            self.require_owner(task_id)
            token = uuid.uuid4().hex
            self.observation = {"id": token, "time": time.monotonic(),
                                "rect": rect, "elements": elements, "bundle": observed_bundle}
            return {"observation_id": token, "bundle_id": observed_bundle,
                    "expires_in_seconds": 60,
                    "elements": elements,
                    "include_elements": components_status == "available",
                    "capture_image": capture_image,
                    "components_status": components_status,
                    **({"components_note": "组件暂不可用，已返回原始截图和新的坐标观察凭证；可按截图操作，需要输入框时再读取组件。"}
                       if components_status == "unavailable" else {}),
                    "elements_limit": element_limit,
                    "element_text_limit": None,
                    "elements_total": elements_metadata["total"],
                    "elements_retained": elements_metadata["retained"],
                    "elements_truncated": elements_metadata["truncated"],
                    "coordinate_space": "normalized 0..1 of screen",
                    "window_rect": rect, "width": width, "height": height}, png

    async def launch(self, task_id: str, bundle_id: str) -> dict:
        """Explicitly activate an installed app; never infer or install a bundle."""
        async with self.lock:
            self.require_owner(task_id)
            if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle_id):
                raise PhoneError("启动 App 需要已知且有效的 bundle ID，不要猜测应用标识")
            if self.control_scope == "apps" and bundle_id != self.bundle:
                raise PhoneError("指定 App 模式只能启动当前会话绑定的 App")
            self.observation = None
            if await self.mobile("isLocked") is not False:
                raise PhoneError("手机锁定或锁屏状态未知，请用户解锁，不自动解锁")
            self.require_owner(task_id)
            await self.mobile("activateApp", {"bundleId": bundle_id})
            return {"action": "launch", "bundle_id": bundle_id,
                    "action_dispatched": True, "verified": False,
                    "requires_observation": True,
                    "note": "启动指令返回成功；请重新观察核验前台 App，超时不重放。"}

    async def act(self, task_id: str, args: PhoneAction) -> dict:
        async with self.lock:
            self.require_owner(task_id)
            observation, self.observation = self.observation, None
            if not observation or observation["id"] != args.observation_id or time.monotonic() - observation["time"] > 60:
                raise PhoneError("观察已过期或已被使用，请重新 iphone_observe")
            await self.foreground(for_action=True, expected=observation["bundle"])
            rect = await self.window_rect()
            if rect != observation["rect"]:
                raise PhoneObservationRequired("手机尺寸或方向已变化，取消动作；请重新观察")
            element_id = None
            screen_point = None
            if args.element_ref:
                item = next((e for e in observation["elements"] if e["ref"] == args.element_ref), None)
                if not item:
                    raise PhoneObservationRequired("组件引用不可用，请用 include_elements=true 重新观察")
                if args.action == "type" and item["type"] not in {"XCUIElementTypeTextField", "XCUIElementTypeTextView", "XCUIElementTypeSearchField", "XCUIElementTypeSecureTextField"}:
                    raise PhoneError("只能向已观察的文本输入框输入")
                matches = await self.request("POST", f"/session/{self.session_id}/elements",
                                             {"using": "xpath", "value": component_locator(item)})
                if not isinstance(matches, list) or len(matches) != 1:
                    raise PhoneObservationRequired("目标组件已变化或定位不唯一，请重新观察或按截图定位")
                if not isinstance(matches[0], dict):
                    raise PhoneError("组件引用无效")
                element_id = matches[0].get("element-6066-11e4-a52e-4f735466cecf")
                if not isinstance(element_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", element_id):
                    raise PhoneError("组件引用无效")
            await self.foreground(for_action=True, expected=observation["bundle"])
            self.require_owner(task_id)
            if await self.window_rect() != rect:
                raise PhoneObservationRequired("手机尺寸或方向已变化，取消动作；请重新观察")
            if args.action == "tap":
                if element_id:
                    await self.request("POST", f"/session/{self.session_id}/element/{element_id}/click", {})
                elif args.x is not None and args.y is not None:
                    screen_point = {"x": rect["x"] + args.x * (rect["width"] - 1),
                                    "y": rect["y"] + args.y * (rect["height"] - 1)}
                    await self.mobile("tap", screen_point)
                else:
                    raise PhoneError("点击需要组件 ref 或归一化 x/y 坐标")
            elif args.action == "type":
                if not element_id or args.text is None:
                    raise PhoneError("输入需要已观察的文本输入框 ref 和 text；换行可能触发提交")
                if any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 or 0xE000 <= ord(c) <= 0xF8FF for c in args.text):
                    raise PhoneError("文本输入不接受非法控制字符或 WebDriver 特殊按键编码")
                await self.request("POST", f"/session/{self.session_id}/element/{element_id}/value", {"text": args.text})
            elif args.action == "swipe":
                if args.direction is None:
                    raise PhoneError("滑动需要 direction")
                await self.mobile("swipe", {"direction": args.direction})
            elif args.action == "home":
                if self.control_scope != "device":
                    raise PhoneError("返回主屏幕需要用户开启整台手机控制范围")
                await self.mobile("pressButton", {"name": "home"})
            return {"action": args.action, "action_dispatched": True, "verified": False, "requires_observation": True,
                    **({"target_description": args.target_description} if args.target_description is not None else {}),
                    **({"normalized_point": {"x": args.x, "y": args.y}, "screen_point": screen_point}
                       if screen_point is not None else {}),
                    "note": "仅证明控制指令返回成功，不代表业务完成。必须再次观察核验；超时不重放。"}

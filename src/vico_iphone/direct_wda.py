"""Wireless WDA client; no Appium server or action retries.

Device ownership, observation validation, and action policy stay in the shared
controller. Only the transport and the small set of native command mappings
change. The helper owns the wireless TCP forward and (when needed) XCTest.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import shutil
import stat
from pathlib import Path

import httpx

from .iphone_control import IPhoneController, PhoneBridgeError, PhoneConfig, PhoneError
from .state import DEFAULT_DIRECT_PORT


MAX_RESPONSE_BYTES = 24 * 1024 * 1024
HELPER_READY_TIMEOUT = 90
HELPER_LINE_LIMIT = 16 * 1024

# Match the installed XCUITest driver's commands/wda/startup.ts for this
# plugin's noReset=true, autoLaunch=false session. Undefined Appium options are
# omitted, so WDA retains the same native defaults (including idle timeout).
APPIUM_WDA_CAPABILITIES = {
    "arguments": [], "environment": {}, "eventloopIdleDelaySec": 0,
    "shouldWaitForQuiescence": True, "maxTypingFrequency": 60,
    "shouldUseSingletonTestManager": True, "shouldTerminateApp": False,
    "forceAppLaunch": False, "useNativeCachingStrategy": True,
    "forceSimulatorSoftwareKeyboardPresence": True,
}


class DirectWDARuntime:
    """One supervised, process-local helper, never a global service restart."""

    def __init__(self, data_directory: Path, *, runner_path: Path | None = None):
        self.data_directory = data_directory
        self.runner_path = runner_path or Path(__file__).resolve().parents[2] / "direct" / "runner.mjs"
        self.process: asyncio.subprocess.Process | None = None
        self.ready: dict | None = None
        self.failure_receipt: dict | None = None
        self.last_stop: dict | None = None

    def status(self) -> dict:
        running = self.process is not None and self.process.returncode is None
        return {"running": running, "ready": running and self.ready is not None,
                "owned": self.process is not None,
                "wda_started": bool(self.ready and self.ready.get("wda_started")),
                "needs_attention": bool(self.last_stop and not self.last_stop["stopped"])}

    async def start(self, config: PhoneConfig) -> dict:
        if self.process is not None or self.last_stop and not self.last_stop["stopped"]:
            # A new POST /session on WDA destroys its current session. Even a
            # formerly ready helper must be explicitly retired before another
            # connection; its startup check is not proof of current ownership.
            raise PhoneBridgeError("无线 WDA 启动进程状态不明，请先显式停止并核验",
                                   code="direct_runtime_pending", request_outcome="unknown")
        self.last_stop = self.failure_receipt = None
        node = shutil.which("node")
        if not node or not self.runner_path.is_file():
            raise PhoneBridgeError("无线 WDA 运行依赖未准备好，请先完成安装配置",
                                   code="direct_runtime_missing", request_outcome="not_sent")
        log_directory = self.data_directory / "runtime"
        log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(log_directory / "direct-wda.stderr.log",
                             os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
                raise PhoneError("无线 WDA 私有日志文件无效")
            os.fchmod(descriptor, 0o600)
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                node, str(self.runner_path), cwd=str(self.runner_path.parent),
                env={**os.environ, "VICO_IPHONE_HOME": str(self.data_directory.resolve())},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=descriptor, limit=HELPER_LINE_LIMIT,
            ))
            try:
                self.process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                # Acquire the child handle even when cancellation races OS spawn;
                # otherwise stdin EOF supervision cannot reliably clean it up.
                self.process = await spawning
                await self.stop()
                raise
        except OSError as exc:
            raise PhoneBridgeError("无法启动无线 WDA 运行进程",
                                   code="direct_runtime_unavailable", request_outcome="not_sent") from exc
        finally:
            os.close(descriptor)
        try:
            assert self.process.stdout is not None
            line = await asyncio.wait_for(self.process.stdout.readline(), HELPER_READY_TIMEOUT)
            if not line or len(line) > HELPER_LINE_LIMIT:
                raise ValueError("missing or oversized helper response")
            ready = json.loads(line)
            if (not isinstance(ready, dict) or ready.get("ready") is not True
                    or ready.get("endpoint") != f"http://127.0.0.1:{config.port}"
                    or type(ready.get("wda_started")) is not bool):
                raw_code = ready.get("error_code") if isinstance(ready, dict) else None
                code = raw_code if isinstance(raw_code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", raw_code) else "direct_runtime_failed"
                if isinstance(ready, dict) and ready.get("ready") is False:
                    self.failure_receipt = ready
                outcome = ("not_sent" if self.failure_receipt
                           and ready.get("request_outcome") == "not_sent"
                           and ready.get("cleanup_verified") is True else "unknown")
                raise PhoneBridgeError("无线 WDA 未就绪；请查看本地运行诊断",
                                       code=code, request_outcome=outcome)
            self.ready = ready
            return ready
        except asyncio.CancelledError:
            await self.stop()
            raise
        except (ValueError, TimeoutError, PhoneBridgeError) as exc:
            stopped = await self.stop()
            if isinstance(exc, PhoneBridgeError):
                if exc.request_outcome != "unknown" and not stopped["stopped"]:
                    raise PhoneBridgeError("无线 WDA 启动失败，且清理结果尚未确认；保留设备占用记录",
                                           code=exc.code, request_outcome="unknown") from exc
                raise
            raise PhoneBridgeError("无线 WDA 启动未返回有效就绪信息；启动结果尚未确认",
                                   code="direct_runtime_failed", request_outcome="unknown") from exc

    async def stop(self) -> dict:
        process, ready = self.process, self.ready
        if process is None:
            return self.last_stop or {"owned": False, "stopped": True, "wda_started": False, "forced": False}
        forced = False
        if process.returncode is None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                forced = True
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
        # A forced OS process exit proves neither remote XCTest nor its WDA
        # session stopped. Never use it as evidence to release an uncertain lease.
        # Startup errors use exit 1 even after successful cleanup. Only their
        # post-cleanup receipt can establish that this normal error exit is safe.
        verified_failure = (process.returncode == 1 and self.failure_receipt is not None
                            and self.failure_receipt.get("cleanup_verified") is True)
        normal_shutdown = process.returncode == 0 and self.failure_receipt is None
        result = {"owned": True, "stopped": (normal_shutdown or verified_failure) and not forced,
                  "wda_started": (ready or self.failure_receipt or {}).get("wda_started") is True, "forced": forced}
        self.process = None
        self.ready = None
        self.last_stop = result
        return result


class DirectWDAController(IPhoneController):
    def __init__(self, data_directory: Path, *, config: PhoneConfig | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 runtime: DirectWDARuntime | None = None):
        super().__init__(data_directory, config=config, transport=transport)
        self.runtime = runtime or DirectWDARuntime(data_directory)
        self.client: httpx.AsyncClient | None = None
        self._screen_info_cache: tuple[str, dict] | None = None

    def clear_session(self):
        self._screen_info_cache = None
        super().clear_session()

    def load_config(self) -> PhoneConfig:
        if self.config_override is None and not (self.data_directory / "configuration/iphone-control.json").exists():
            return PhoneConfig(port=DEFAULT_DIRECT_PORT)
        config = super().load_config()
        if config.enabled and (config.wda_transport != "coredevice" or not config.use_preinstalled_wda):
            raise PhoneError("无线直连需要 coredevice 和已安装的签名 WDA，请先完成配置")
        if config.prebuilt_wda_path:
            raise PhoneError("无线直连不会在连接时安装 WDA；请先安装签名 WDA，并清空 prebuilt_wda_path")
        return config

    def runtime_status(self) -> dict:
        status = self.runtime.status()
        needs_attention = self.uncertain or status.get("needs_attention", False)
        return {"backend": "direct-wda", "state": "unknown" if needs_attention else "ready" if status["ready"] else "stopped",
                "managed": status["owned"], "running": status["running"],
                "wda_started": status["wda_started"], "needs_attention": needs_attention,
                "note": "进程状态不证明手机当前可操作；请通过观察读取真机。"}

    def bridge_status(self, config: PhoneConfig) -> dict:
        return {**self.runtime_status(), "port": config.port}

    async def _close_client(self):
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def _stop_helper(self) -> dict:
        await self._close_client()
        return await self.runtime.stop()

    async def session(self, task_id: str, action: str, bundle: str | None = None,
                      *, full_access: bool = False) -> dict:
        if action == "disconnect":
            async with self.lock:
                if self.owner not in (None, task_id):
                    raise PhoneError("只能由持有会话的任务断开")
                self._screen_info_cache = None
                self.observation = None
                if self.uncertain:
                    raise PhoneError("无线 WDA 连接结果未知；请显式停止所属运行进程并核验，不能自动重试")
                if self.session_id:
                    try:
                        await self.request("DELETE", f"/session/{self.session_id}", timeout=10)
                    except PhoneBridgeError as exc:
                        if exc.code != "invalid session id":
                            raise
                stopped = await self._stop_helper()
                if stopped["stopped"]:
                    self.clear_session()
                else:
                    # DELETE confirmed this session ended, but the owned device
                    # process did not cleanly stop. Do not hand it to a new owner.
                    self.session_id = None
                    self.uncertain = True
                return {"connected": False, "backend": "direct-wda", "runtime": stopped,
                        "needs_attention": not stopped["stopped"],
                        **({"message": "控制会话已断开，但无线运行进程清理未确认；保留设备占用记录。"}
                           if not stopped["stopped"] else {})}
        result = await super().session(task_id, action, bundle, full_access=full_access)
        return {**result, "backend": "direct-wda"}

    async def stop_runtime(self) -> dict:
        """Explicit recovery only; a dead local forwarder is not a deleted session."""
        self._screen_info_cache = None
        error = None
        if self.session_id and self.owner and not self.uncertain:
            try:
                return await self.session(self.owner, "disconnect")
            except (PhoneError, OSError) as exc:
                error = str(exc)
        async with self.lock:
            self.observation = None
            stopped = await self._stop_helper()
            remote_stopped = stopped["owned"] and stopped["stopped"] and stopped["wda_started"]
            if remote_stopped:
                self.clear_session()
            needs_attention = self.owner is not None or self.uncertain or not stopped["stopped"]
            return {"backend": "direct-wda", "runtime": stopped,
                    "connected": self.session_id is not None, "needs_attention": needs_attention,
                    **({"message": error or "远端会话停止尚未确认；保留设备占用记录，请显式核验。"}
                       if needs_attention else {})}

    async def finish(self, task_id: str | None = None) -> dict | None:
        if task_id is not None and self.owner not in (None, task_id):
            return None
        return await self.stop_runtime()

    async def request(self, method: str, path: str, body=None, timeout=30):
        # Startup is reached only after the inherited connect path has obtained
        # the cross-process device lease and written its durable owner marker.
        creating = method == "POST" and path == "/session"
        if creating:
            if self.lease is None or self.owner is None:
                raise PhoneBridgeError("缺少设备控制锁，不能启动无线 WDA",
                                       code="direct_lease_required", request_outcome="not_sent")
            self._screen_info_cache = None
            try:
                await self.runtime.start(self.config)
            except asyncio.CancelledError:
                self.last_diagnostic = {"phase": "direct_runtime", "error": "startup_cancelled",
                                        "request_outcome": "unknown"}
                # No HTTP session was sent, but a cancelled device-process
                # launch may still be in flight. Keep ownership until verified.
                raise
            except PhoneBridgeError as exc:
                stopped = await self._stop_helper()
                if exc.request_outcome != "unknown" and not stopped["stopped"]:
                    exc = PhoneBridgeError("无线 WDA 启动失败，清理未确认；保留设备占用记录",
                                           code=exc.code, request_outcome="unknown")
                self.last_diagnostic = {"phase": "direct_runtime", "error": exc.code,
                                        "request_outcome": exc.request_outcome}
                raise exc
            # WDA's bundleId launches/activates an app even with forceAppLaunch
            # false. Always omit it: the shared layer enforces app-only scope.
            body = {"capabilities": {"alwaysMatch": {},
                                      "firstMatch": [copy.deepcopy(APPIUM_WDA_CAPABILITIES)]}}
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.config.port}",
                                            trust_env=False, follow_redirects=False,
                                            transport=self.transport)
        original_path = path
        window_rect = path.endswith("/window/rect")
        if window_rect:
            path = path.removesuffix("/rect") + "/size"
        if method == "GET" and path.endswith("/source"):
            # Match XCUITest's mobileGetSource XML scope; this is a wrapper tag,
            # not a filter which drops parts of the accessibility tree.
            path += "?format=xml&scope=AppiumAUT"
        if (method == "POST" and re.fullmatch(r"/session/[A-Za-z0-9-]+/element/[A-Za-z0-9-]+/value", path)
                and isinstance(body, dict) and isinstance(body.get("text"), str)):
            # XCUITest setValue uses [...String(text)], i.e. Unicode code points,
            # rather than UTF-16 halves. Shared action validation already rejects
            # WebDriver special-key codes; ordinary newlines are kept verbatim.
            body = {"value": list(body["text"])}
        diagnostic = {"phase": "direct_wda", "method": method, "path": original_path}
        try:
            async with self.client.stream(method, path, json=body, timeout=timeout) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise PhoneBridgeError("无线 WDA 响应超过大小上限")
                    chunks.append(chunk)
                payload = json.loads(b"".join(chunks))
                if not isinstance(payload, dict) or "value" not in payload:
                    raise PhoneBridgeError("无线 WDA 响应格式无效")
                value = payload["value"]
                if response.status_code >= 300 or isinstance(value, dict) and value.get("error"):
                    raw_code = value.get("error") if isinstance(value, dict) else None
                    code = raw_code if isinstance(raw_code, str) and re.fullmatch(r"[a-z ]{1,100}", raw_code) else "wda_error"
                    outcome = "unknown"
                    if (code == "invalid session id" and self.session_id
                            and (original_path == f"/session/{self.session_id}"
                                 or original_path.startswith(f"/session/{self.session_id}/"))):
                        stopped = await self._stop_helper()
                        if stopped["stopped"]:
                            self.clear_session()
                            outcome = "rejected"
                        else:
                            self.session_id = None
                            self.uncertain = True
                    elif (creating and response.status_code >= 400
                          and code in {"session not created", "invalid argument"}
                          and not payload.get("sessionId") and not value.get("sessionId")):
                        outcome = "rejected"
                    # Never echo remote text: WDA errors can contain typed text,
                    # device identity, application contents, or XML selectors.
                    raise PhoneBridgeError(f"无线 WDA 返回错误：{code}；未确认成功，不自动重放动作",
                                           code=code, request_outcome=outcome)
                if creating and isinstance(value, dict) and not value.get("sessionId") and payload.get("sessionId"):
                    value = {**value, "sessionId": payload["sessionId"]}
                if window_rect and isinstance(value, dict):
                    value = {"x": 0, "y": 0, **value}
                return value
        except PhoneBridgeError as exc:
            if creating and exc.request_outcome in {"not_sent", "rejected"}:
                stopped = await self._stop_helper()
                if not stopped["stopped"]:
                    exc = PhoneBridgeError("无线 WDA 连接失败，清理未确认；保留设备占用记录",
                                           code=exc.code, request_outcome="unknown")
            self.last_diagnostic = {**diagnostic, "error": exc.code or "invalid_response",
                                    "request_outcome": exc.request_outcome}
            raise exc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            outcome = "not_sent"
            if creating and not (await self._stop_helper())["stopped"]:
                outcome = "unknown"
            self.last_diagnostic = {**diagnostic, "error": "direct_wda_unavailable",
                                    "transport_error": type(exc).__name__, "request_outcome": outcome}
            raise PhoneBridgeError("无线 WDA 暂时无法连接；不重放动作"
                                   + ("，运行进程清理未确认，保留设备占用记录" if outcome == "unknown" else "，本次请求未发送"),
                                   code="direct_wda_unavailable", request_outcome=outcome) from exc
        except asyncio.CancelledError:
            self.last_diagnostic = {**diagnostic, "error": "request_cancelled", "request_outcome": "unknown"}
            raise
        except (httpx.HTTPError, ValueError, RecursionError) as exc:
            self.last_diagnostic = {**diagnostic, "error": "request_outcome_unknown",
                                    "transport_error": type(exc).__name__, "request_outcome": "unknown"}
            raise PhoneBridgeError("无线 WDA 响应丢失或无效；结果未知，请先核验，勿自动重放",
                                   code="request_outcome_unknown") from exc

    async def mobile(self, name: str, args: dict | None = None):
        if (name == "deviceScreenInfo" and self.session_id and self._screen_info_cache
                and self._screen_info_cache[0] == self.session_id):
            return copy.deepcopy(self._screen_info_cache[1])
        # Routes verified against WDA FB{Custom,Element,Session}Commands.m.
        routes = {"isLocked": ("GET", "/wda/locked"),
                  "activeAppInfo": ("GET", "/wda/activeAppInfo"),
                  "deviceScreenInfo": ("GET", "/wda/screen"),
                  "tap": ("POST", "/wda/tap"),
                  "swipe": ("POST", "/wda/swipe"),
                  "pressButton": ("POST", "/wda/pressButton"),
                  "activateApp": ("POST", "/wda/apps/activate")}
        if name not in routes:
            raise PhoneError("无线直连未实现该内部命令")
        method, suffix = routes[name]
        value = await self.request(method, f"/session/{self.session_id}{suffix}",
                                   (args or {}) if method == "POST" else None)
        if name == "deviceScreenInfo" and self.session_id and isinstance(value, dict):
            size = value.get("screenSize")
            if isinstance(size, dict) and all(
                type(size.get(axis)) in (int, float) and math.isfinite(size[axis])
                and 0 < size[axis] < 10000 for axis in ("width", "height")
            ):
                # XCUITest memoizes getScreenInfo per driver/session. Matching
                # that policy removes redundant wireless reads, but it also means
                # mid-session rotation is not detected by geometry checks alone.
                # Reconnect after rotating; cached data never crosses sessions.
                self._screen_info_cache = (self.session_id, copy.deepcopy(value))
        return value

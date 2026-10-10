"""Stdio MCP adapter. Codex plans; this process only connects and executes tools."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from . import __version__
from .iphone_control import IPhoneController, PhoneAction, PhoneBridgeError, PhoneError
from .state import operation_lock, process_alive, read_json, state_home, write_json

ROOT = Path(__file__).resolve().parents[2]
CONNECT_POLL_WAIT_SECONDS = 20.0
INSTRUCTIONS = """Control the owner's paired iPhone using components first. Codex makes the decisions;
this server does not call another model. Start with iphone_status / iphone_setup. A running Appium
bridge is not proof the phone is connected. iphone_session(connect) starts exactly one connection
attempt. It waits at most 20 seconds; a pending result gives a private operation_id. Poll that same
attempt with iphone_session(action=connect_status, operation_id=...) until completed. Never resend
connect because an attempt is pending or a reply timed out. Success gives a private session_token:
retain it for this task and pass it to subsequent tools; never take over another task's session.
iphone_observe defaults to components and does not capture or send an image. Use fresh element_ref
values from that observation. Actions consume observation_id once and normally return the next
component observation. Reuse that result. An accepted command is not proof the user's task is done.
Read the actual final state. Keep task progress and tool results; do not replay a timed-out action.
Use screenshots only when repeated component attempts cannot progress, or when the user requests
visual inspection. Screenshot coordinates for iphone_act are normalized x/y in [0,1]; images are
returned as image blocks, which the host must forward to the model. Do not mistake metadata for
seeing an image. Observe after user intervention or a changed page. Disconnect when finished.
Network mode targets same-network Apple development tunnels, not public remote control. Initial
pairing/signing may require USB, Xcode, an unlocked device and the owner's Apple account.
"""


def obj(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def string(description: str, **extra) -> dict:
    return {"type": "string", "description": description, **extra}


TOKEN = string("Exact private session_token returned by this task's connect.", minLength=32, maxLength=128)
OPERATION_ID = string("Exact private operation_id returned by this task's pending connect.",
                      minLength=48, maxLength=48, pattern="^[a-f0-9]{48}$")
TOOLS = [
    types.Tool(name="iphone_status", description="Read-only configuration, session and local bridge status. Does not prove the phone is reachable; no model calls.",
               inputSchema=obj({}), annotations=types.ToolAnnotations(readOnlyHint=True)),
    types.Tool(name="iphone_setup", description="Inspect, start or stop this plugin's isolated local Appium bridge. Never installs software or touches an unrelated listener. No phone actions. Dependencies must first be installed with the packaged setup instructions.",
               inputSchema=obj({"action": string("Bridge operation", enum=["status", "start", "stop"])}, ("action",))),
    types.Tool(name="iphone_session", description="Start one connection, poll its connect_status, or disconnect this task's session. Connect/status waits at most 20 seconds; pending returns a private operation_id for polling the same attempt without replay. Connect may build/start WDA using owner-configured signing. Success returns session_token for all phone tools. Connect does not open an app.",
               inputSchema=obj({"action": string("Session operation", enum=["connect", "connect_status", "disconnect"]),
                                "bundle_id": string("Optional initial app for configured app-scoped sessions.", maxLength=255),
                                "operation_id": OPERATION_ID,
                                "session_token": TOKEN}, ("action",))),
    types.Tool(name="iphone_observe", description="Read current components without capturing an image by default. Returns full visible component fields and a single-use observation_id. Explicit screenshot/both mode returns an actual PNG image; screenshot mode skips the component tree.",
               inputSchema=obj({"session_token": TOKEN, "mode": string("Default components; visual fallback only when needed.", enum=["components", "screenshot", "both"])}, ("session_token",)),
               annotations=types.ToolAnnotations(readOnlyHint=True)),
    types.Tool(name="iphone_act", description="Execute one action from a fresh observation; then return the new component observation by default. Tap/type prefer element_ref. Coordinate taps use normalized x/y [0,1]. Type appends text; newline may submit. Never repeat an unknown-outcome action. Reuse the returned observation for the next step.",
               inputSchema=obj({"session_token": TOKEN,
                                "observation_id": string("Fresh, unused observation_id", minLength=1, maxLength=128),
                                "action": string("Action", enum=["tap", "type", "swipe", "home"]),
                                "element_ref": string("Element ref from current observation", maxLength=128),
                                "text": string("Complete intended input; newline can submit.", maxLength=2000),
                                "x": {"type": "number", "minimum": 0, "maximum": 1},
                                "y": {"type": "number", "minimum": 0, "maximum": 1},
                                "direction": string("Finger swipe direction", enum=["up", "down", "left", "right"]),
                                "observe_after": {"type": "boolean", "description": "Default true. Return components after this action; never automatically request a screenshot."}},
                               ("session_token", "observation_id", "action"))),
    types.Tool(name="iphone_launch", description="Activate one explicitly known installed app by bundle_id. Invalidates the prior observation; returns new components by default. No app installation or guessed catalog lookup.",
               inputSchema=obj({"session_token": TOKEN, "bundle_id": string("Known app bundle identifier", minLength=3, maxLength=255),
                                "observe_after": {"type": "boolean", "default": True}}, ("session_token", "bundle_id"))),
]


def result(data: dict, image: bytes = b"", *, error: bool = False) -> types.CallToolResult:
    blocks = [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]
    if image:
        blocks.append(types.ImageContent(type="image", data=base64.b64encode(image).decode("ascii"), mimeType="image/png"))
    # Image content stays in content, not only structuredContent, for hosts that otherwise drop it.
    return types.CallToolResult(content=blocks, isError=error)


async def bridge_command(home: Path, action: str) -> dict:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is missing; follow the setup skill.")
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "DEVELOPER_DIR") if key in os.environ}
    env["VICO_IPHONE_HOME"] = str(home)
    process = await asyncio.create_subprocess_exec(node, str(ROOT / "bridge/cli.mjs"), action, "--json",
                                                  cwd=ROOT, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if len(output) > 256 * 1024:
        raise RuntimeError("Bridge response is too large.")
    try:
        value = json.loads(output)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Bridge did not return valid JSON; inspect its private log.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Invalid bridge response.")
    if process.returncode or value.get("ok") is False:
        error = value.get("error", {})
        raise RuntimeError(error.get("message", "Local bridge failed") if isinstance(error, dict) else str(error))
    return value


class PhoneService:
    def __init__(self, home: Path | None = None, controller=None, bridge=bridge_command,
                 *, connect_wait_seconds: float = CONNECT_POLL_WAIT_SECONDS):
        self.home = home or state_home()
        self.controller = controller or IPhoneController(self.home)
        self.bridge = bridge
        self.owner = "codex-" + secrets.token_hex(16)
        self.token: str | None = None
        self.mutex = asyncio.Lock()
        self.journal = self.home / "session.json"
        self.connect_wait_seconds = min(25.0, max(0.0, connect_wait_seconds))
        self.connect_task: asyncio.Task[types.CallToolResult] | None = None
        self.connect_operation_id: str | None = None
        self.closing = False

    def save_session(self, phase: str) -> None:
        write_json(self.journal, {"owner": self.owner, "pid": os.getpid(), "phase": phase,
                                 "appium_session_id": self.controller.session_id})

    def clear_journal(self) -> None:
        previous = read_json(self.journal)
        if previous and previous.get("owner") == self.owner:
            self.journal.unlink(missing_ok=True)

    def require_token(self, token: str | None) -> None:
        if not self.token or not token or not secrets.compare_digest(self.token, token):
            raise PhoneError("Session token does not belong to this task. Connect your own session; do not take over another task.")

    async def observe(self, mode: str = "components") -> tuple[dict, bytes]:
        value, image = await self.controller.observe(self.owner, include_elements=mode != "screenshot", capture_image=mode != "components")
        self.save_session("connected")
        return value, image

    async def after_action(self, action: dict, observe_after: bool) -> types.CallToolResult:
        self.save_session("connected")
        if not observe_after:
            return result({"ok": True, "action": action, "next": "iphone_observe"})
        try:
            observation, _ = await self.observe()
            return result({"ok": True, "action": action, "observation": observation})
        except PhoneError as exc:
            return result({"ok": True, "action": action, "observation_error": str(exc),
                           "next": "The command already returned. Read current state with iphone_observe; do not replay the action."})

    @staticmethod
    def failure(exc: Exception) -> types.CallToolResult:
        if isinstance(exc, ValidationError):
            message = json.dumps(exc.errors(include_input=False, include_context=False))
        else:
            message = str(exc)
        info = {"ok": False, "error": {"code": getattr(exc, "code", type(exc).__name__), "message": message},
                "replay_action": False}
        if isinstance(exc, PhoneBridgeError):
            info["request_outcome"] = exc.request_outcome
        return result(info, error=True)

    @staticmethod
    def busy(name: str) -> types.CallToolResult:
        # Status never reveals either private operation or session credentials.
        return result({"ok": name == "iphone_status", "busy": True,
                       "message": "A phone tool is still running; poll your existing connection operation or wait for its result."},
                      error=name != "iphone_status")

    def forget_connection_operation(self) -> None:
        self.connect_task = None
        self.connect_operation_id = None

    async def run_connection(self, args: dict) -> types.CallToolResult:
        # The background task, rather than any one bounded MCP call, owns both
        # locks for the entire POST /session attempt. No other tool can mutate.
        try:
            async with self.mutex:
                with operation_lock(self.home):
                    return await self._call("iphone_session", args)
        except (PhoneError, OSError, ValueError, RuntimeError) as exc:
            return self.failure(exc)

    async def poll_connection(self, operation_id: str | None) -> types.CallToolResult:
        if (not self.connect_operation_id or not isinstance(operation_id, str)
                or not operation_id.isascii()
                or not secrets.compare_digest(self.connect_operation_id, operation_id)
                or self.connect_task is None):
            raise PhoneError("Connection operation does not belong to this task; use the exact operation_id returned by your pending connect.")
        task = self.connect_task
        # asyncio.wait does not cancel the operation on timeout or caller
        # cancellation. Only MCP shutdown deliberately cancels the background task.
        await asyncio.wait({task}, timeout=self.connect_wait_seconds)
        if task.done():
            if task.cancelled():
                return result({"ok": False, "pending": False, "request_outcome": "unknown",
                               "replay_action": False,
                               "error": {"code": "connection_cancelled", "message": "The connection attempt was cancelled. Inspect status and recover explicitly; do not reconnect automatically."}}, error=True)
            return task.result()
        return result({"ok": True, "pending": True, "operation_id": operation_id,
                       "next": "Call iphone_session(action=connect_status, operation_id=...) with this same operation_id; do not call connect again."})

    async def call(self, name: str, args: dict) -> types.CallToolResult:
        try:
            if self.closing:
                raise PhoneError("The MCP service is shutting down; inspect persisted session state before any later reconnect.")
            if name == "iphone_session" and args.get("action") == "connect_status":
                return await self.poll_connection(args.get("operation_id"))
            if self.mutex.locked() or self.connect_task is not None and not self.connect_task.done():
                return self.busy(name)
            if name == "iphone_session" and args.get("action") == "connect":
                if self.connect_task is not None and (self.token or self.controller.owner
                                                     or self.controller.uncertain or read_json(self.journal)):
                    raise PhoneError("This connection attempt already completed or needs recovery. Poll its original operation_id or inspect status; do not send another connect.")
                self.connect_operation_id = secrets.token_hex(24)
                self.connect_task = asyncio.create_task(self.run_connection(dict(args)))
                return await self.poll_connection(self.connect_operation_id)
            async with self.mutex:
                with operation_lock(self.home):
                    return await self._call(name, args)
        except (PhoneError, OSError, ValueError, RuntimeError) as exc:
            return self.failure(exc)

    async def _call(self, name: str, args: dict) -> types.CallToolResult:
        if name == "iphone_status":
            state = await self.controller.session(self.owner, "status")
            saved = read_json(self.journal)
            state["session_recovery"] = ({"required": saved.get("owner") != self.owner or self.controller.uncertain,
                                           "owner_process_active": process_alive(saved.get("pid")),
                                           "pending_outcome": saved.get("phase") in {"connecting", "action_in_flight"},
                                           "phase": saved.get("phase")} if saved else None)
            state["bridge_runtime"] = await self.bridge(self.home, "status")
            return result({"ok": True, **state})
        if name == "iphone_setup":
            action = args["action"]
            saved = read_json(self.journal)
            if action != "status" and saved:
                own_unknown_connect = (action == "stop" and saved.get("owner") == self.owner
                                       and self.controller.uncertain and not self.controller.session_id)
                if not own_unknown_connect and (self.controller.owner or process_alive(saved.get("pid"))):
                    raise PhoneError("A phone session is active. Its owner must disconnect before changing the bridge.")
                if action == "start":
                    raise PhoneError("The previous MCP session ended without cleanup. Inspect status, then stop the owned bridge before starting again; do not replay its last action.")
            value = await self.bridge(self.home, action)
            if action == "stop" and saved:
                # The CLI verifies an owned stop or an already stopped, unoccupied endpoint.
                if value.get("stopped") is True and value.get("running") is False:
                    if self.controller.clear_abandoned_lease_after_bridge_reset():
                        self.controller.clear_session()
                        self.token = None
                        self.journal.unlink(missing_ok=True)
                        self.forget_connection_operation()
                    else:
                        value["session_recovery_required"] = True
                        value["note"] = "The bridge stopped, but its device record could not be safely cleared. Session records are retained; inspect their owner and configuration."
                else:
                    value["session_recovery_required"] = True
                    value["note"] = "No owned bridge reset was confirmed; the abandoned session record is retained."
            return result({"ok": True, **value})
        if name == "iphone_session":
            action = args["action"]
            if action == "connect":
                if read_json(self.journal):
                    raise PhoneError("A session already owns this state directory or needs recovery. Inspect iphone_status; do not start another session.")
                self.save_session("connecting")
                try:
                    value = await self.controller.session(self.owner, "connect", args.get("bundle_id"))
                except BaseException:
                    if self.controller.owner is None and not self.controller.uncertain:
                        self.clear_journal()
                    raise
                self.token = secrets.token_hex(24)
                self.save_session("connected")
                return result({"ok": True, **value, "session_token": self.token,
                               "next": "iphone_observe(mode=components) with this session_token"})
            self.require_token(args.get("session_token"))
            value = await self.controller.session(self.owner, "disconnect")
            self.clear_journal()
            self.token = None
            self.forget_connection_operation()
            return result({"ok": True, **value})
        self.require_token(args.get("session_token"))
        if name == "iphone_observe":
            value, image = await self.observe(args.get("mode", "components"))
            return result({"ok": True, **value}, image)
        if name == "iphone_act":
            parameters = {key: value for key, value in args.items() if key not in {"session_token", "observe_after"}}
            action = PhoneAction.model_validate(parameters)
            self.save_session("action_in_flight")
            value = await self.controller.act(self.owner, action)
            return await self.after_action(value, args.get("observe_after", True))
        if name == "iphone_launch":
            self.save_session("action_in_flight")
            value = await self.controller.launch(self.owner, args["bundle_id"])
            return await self.after_action(value, args.get("observe_after", True))
        raise ValueError("Unknown tool")

    async def close(self) -> None:
        self.closing = True
        if self.connect_task is not None and not self.connect_task.done():
            self.connect_task.cancel()
            try:
                await self.connect_task
            except asyncio.CancelledError:
                pass
        async with self.mutex:
            if self.controller.owner == self.owner and not self.controller.uncertain:
                try:
                    with operation_lock(self.home):
                        await asyncio.wait_for(self.controller.finish(self.owner), timeout=15)
                        if self.controller.owner is None:
                            self.clear_journal()
                except (OSError, ValueError, RuntimeError, asyncio.TimeoutError):
                    # The durable record remains for the next MCP process to diagnose.
                    pass


def create_server(service: PhoneService) -> Server:
    @asynccontextmanager
    async def lifespan(_: Server):
        try:
            yield {}
        finally:
            with anyio.CancelScope(shield=True):
                await service.close()

    server = Server("iphone-use", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        return await service.call(name, arguments)

    return server


async def run() -> None:
    server = create_server(PhoneService())
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

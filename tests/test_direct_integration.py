import json
import os
import stat
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from vico_iphone import cli
from vico_iphone.iphone_control import PhoneConfig
from vico_iphone.server import PhoneService
from vico_iphone.state import write_json


def test_init_direct_preserves_original_and_uses_private_separate_state(tmp_path, monkeypatch, capsys):
    source, target = tmp_path / "old", tmp_path / "new"
    config = PhoneConfig(enabled=True, udid="test-device", team_id="TESTTEAM01", platform_version="26.0",
                         wda_transport="coredevice", use_preinstalled_wda=True)
    path = source / "configuration/iphone-control.json"
    write_json(path, config.model_dump())
    original = path.read_bytes()
    write_json(source / "session.json", {"owner": "old-session"})
    monkeypatch.setattr(sys, "argv", ["iphone-use", "init-direct", "--from-state", str(source), "--state-dir", str(target)])
    cli.main()
    copied = target / "configuration/iphone-control.json"
    assert path.read_bytes() == original
    assert not (target / "session.json").exists()
    assert json.loads(copied.read_text())["port"] == 18401
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600
    assert json.loads(capsys.readouterr().out)["original_preserved"] is True
    before = copied.read_bytes()
    with pytest.raises(SystemExit):
        cli.main()
    assert copied.read_bytes() == before


def test_init_direct_rejects_original_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["iphone-use", "init-direct", "--from-state", str(tmp_path), "--state-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main()
    assert not (tmp_path / "configuration").exists()


def test_unknown_backend_does_not_fall_back_to_appium(tmp_path):
    with pytest.raises(ValueError, match="BACKEND"):
        PhoneService(tmp_path, backend="direct-typo")


async def test_direct_stop_retains_abandoned_journal_without_remote_cleanup_proof(tmp_path, monkeypatch):
    journal = tmp_path / "session.json"
    write_json(journal, {"owner": "previous-mcp-owner", "pid": 12345,
                         "phase": "connecting", "appium_session_id": None})
    original = journal.read_bytes()
    monkeypatch.setattr("vico_iphone.server.process_alive", lambda pid: False)
    service = PhoneService(tmp_path, backend="direct-wda")

    reply = await service.call("iphone_setup", {"action": "stop"})
    assert not reply.isError
    value = json.loads(reply.content[0].text)
    assert value["runtime"]["owned"] is False
    assert value["runtime"]["stopped"] is True
    assert value["session_recovery_required"] is True
    assert value["needs_attention"] is True
    assert journal.read_bytes() == original
    assert service.controller.runtime.process is None


async def test_direct_disconnect_preserves_cleanup_journal_and_token_until_explicit_verified_stop(tmp_path):
    class CleanupPendingController:
        def __init__(self):
            self.owner = None
            self.session_id = None
            self.uncertain = False
            self.cleanup_verified = False
            self.calls = []

        async def session(self, owner, action, bundle=None):
            self.calls.append(action)
            if action == "connect":
                self.owner, self.session_id = owner, "direct-test-session"
                return {"connected": True, "backend": "direct-wda"}
            assert action == "disconnect" and self.owner == owner
            # The HTTP DELETE succeeded, but helper cleanup remains uncertain.
            self.session_id = None
            self.uncertain = True
            return {"connected": False, "needs_attention": True,
                    "runtime": {"owned": True, "stopped": False, "wda_started": True}}

        async def stop_runtime(self):
            self.calls.append("stop")
            if self.cleanup_verified:
                self.owner, self.uncertain = None, False
            return {"connected": False, "needs_attention": not self.cleanup_verified,
                    "runtime": {"owned": True, "stopped": self.cleanup_verified, "wda_started": True}}

    controller = CleanupPendingController()

    async def bridge(home, action):
        assert home == tmp_path and action == "stop"
        return await controller.stop_runtime()

    service = PhoneService(tmp_path, controller=controller, bridge=bridge, backend="direct-wda")
    connected = await service.call("iphone_session", {"action": "connect"})
    assert not connected.isError
    token = json.loads(connected.content[0].text)["session_token"]
    operation_id = service.connect_operation_id

    disconnected = await service.call("iphone_session", {"action": "disconnect", "session_token": token})
    value = json.loads(disconnected.content[0].text)
    assert disconnected.isError and value["needs_attention"] and value["session_recovery_required"]
    journal = json.loads(service.journal.read_text())
    assert journal["phase"] == "cleanup_pending" and journal["owner"] == service.owner
    assert journal["appium_session_id"] is None
    assert service.token == token and service.connect_operation_id == operation_id

    retry = await service.call("iphone_session", {"action": "connect"})
    assert retry.isError and controller.calls == ["connect", "disconnect"]
    unverified = await service.call("iphone_setup", {"action": "stop"})
    assert json.loads(unverified.content[0].text)["needs_attention"]
    assert json.loads(service.journal.read_text()) == journal
    assert service.token == token and service.connect_operation_id == operation_id

    controller.cleanup_verified = True
    recovered = await service.call("iphone_setup", {"action": "stop"})
    assert not recovered.isError
    assert not json.loads(recovered.content[0].text)["needs_attention"]
    assert not service.journal.exists() and service.token is None
    assert service.connect_operation_id is None and service.connect_task is None
    assert controller.calls == ["connect", "disconnect", "stop", "stop"]


async def test_direct_stdio_is_distinct_and_status_does_not_launch_phone(tmp_path):
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    env.update(VICO_IPHONE_HOME=str(tmp_path), IPHONE_USE_BACKEND="direct-wda")
    params = StdioServerParameters(command=sys.executable, args=["-m", "vico_iphone.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            hello = await session.initialize()
            assert hello.serverInfo.name == "iphone-use-wireless-direct"
            tools = await session.list_tools()
            assert len(tools.tools) == 6
            assert all("direct-WDA" in tool.description for tool in tools.tools)
            status = await session.call_tool("iphone_status", {})
            assert not status.isError
            value = json.loads(status.content[0].text)
            assert value["connected"] is False
            assert value["bridge_runtime"]["backend"] == "direct-wda"
            assert value["bridge_runtime"]["phone_verified"] is False
    assert not (tmp_path / "session.json").exists()

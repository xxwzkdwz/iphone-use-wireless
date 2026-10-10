from __future__ import annotations

import asyncio
import json
import signal
import stat
from pathlib import Path

import pytest

from vico_iphone import coredevice_discovery as discovery
from vico_iphone.coredevice_discovery import CoreDeviceDiscoveryError


def details(**connection):
    return {"info": {"outcome": "success"}, "result": {
        "hardwareProperties": {"udid": "test-device"},
        "connectionProperties": {"pairingState": "paired", "tunnelState": "connected",
                                 "transportType": "localNetwork", "tunnelIPAddress": "fd12:3456::1",
                                 **connection},
    }}


class FakeProcess:
    pid = 987654

    def __init__(self, *, blocked=False, returncode=0):
        self.returncode = None if blocked else returncode
        self.release = asyncio.Event()

    async def wait(self):
        if self.returncode is None:
            await self.release.wait()
        return self.returncode


def install_process(monkeypatch, *, payload=None, raw=None, blocked=False, returncode=0):
    process = FakeProcess(blocked=blocked, returncode=returncode)
    calls, signals = [], []

    async def spawn(*args, **kwargs):
        output = Path(args[args.index("--json-output") + 1])
        calls.append((args, kwargs, output))
        assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        output.write_bytes(raw if raw is not None else json.dumps(payload or details()).encode())
        return process

    def kill(pid, sig):
        assert pid == process.pid
        signals.append(sig)
        process.returncode = -sig
        process.release.set()

    monkeypatch.setattr(discovery.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(discovery.os, "killpg", kill)
    return process, calls, signals


@pytest.mark.parametrize("transport", ["localNetwork", "wired"])
async def test_fixed_read_only_query_has_private_output_minimal_environment_and_cleanup(monkeypatch, transport):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    monkeypatch.setenv("HTTPS_PROXY", "do-not-inherit")
    monkeypatch.setenv("NODE_OPTIONS", "do-not-inherit")
    _, calls, signals = install_process(monkeypatch, payload=details(transportType=transport))
    assert await discovery.discover_coredevice_wda_url("test-device") == "http://[fd12:3456::1]:8100"
    args, options, output = calls[0]
    assert args == ("/usr/bin/xcrun", "devicectl", "device", "info", "details", "--device", "test-device",
                    "--timeout", "8", "--quiet", "--json-output", str(output))
    assert set(options["env"]) == {"HOME", "PATH", "TMPDIR", "LANG"}
    assert options["env"]["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert options["start_new_session"] is True
    assert options["stdin"] == options["stdout"] == options["stderr"] == asyncio.subprocess.DEVNULL
    assert not output.parent.exists()
    assert not signals


@pytest.mark.parametrize("connection", [
    {"pairingState": "unpaired"}, {"tunnelState": "disconnected"}, {"transportType": "unknown"},
    {"transportType": {}}, {"tunnelIPAddress": "192.168.1.2"}, {"tunnelIPAddress": "::1"},
    {"tunnelIPAddress": "fe80::1"}, {"tunnelIPAddress": "2001:4860::1"},
    {"tunnelIPAddress": "fd12::1%en0"}, {"tunnelIPAddress": "fd12::1\n"},
    {"tunnelIPAddress": "[fd12::1]:8100"}, {"tunnelIPAddress": "phone.local"},
    {"tunnelIPAddress": None}, {"tunnelIPAddress": {}},
])
async def test_invalid_connection_or_address_never_becomes_a_target(monkeypatch, connection):
    _, calls, _ = install_process(monkeypatch, payload=details(**connection))
    with pytest.raises(CoreDeviceDiscoveryError):
        await discovery.discover_coredevice_wda_url("test-device")
    assert not calls[0][2].parent.exists()


@pytest.mark.parametrize("case", ["wrong-device", "missing-hardware", "failed-outcome", "top-error"])
async def test_query_must_succeed_for_exact_configured_device(monkeypatch, case):
    payload = details()
    if case == "wrong-device":
        payload["result"]["hardwareProperties"]["udid"] = "another-device"
    elif case == "missing-hardware":
        del payload["result"]["hardwareProperties"]
    elif case == "failed-outcome":
        payload["info"]["outcome"] = "failed"
    else:
        payload["error"] = {"message": "private diagnostic"}
    install_process(monkeypatch, payload=payload)
    with pytest.raises(CoreDeviceDiscoveryError) as captured:
        await discovery.discover_coredevice_wda_url("test-device")
    assert "another-device" not in str(captured.value)
    assert "private diagnostic" not in str(captured.value)


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b"{}", b"\xff", b"[" * 2000 + b"]" * 2000,
                                 b"x" * (discovery.MAX_DETAILS_BYTES + 1)])
async def test_invalid_or_oversized_json_is_rejected_and_deleted(monkeypatch, raw):
    _, calls, _ = install_process(monkeypatch, raw=raw)
    with pytest.raises(CoreDeviceDiscoveryError):
        await discovery.discover_coredevice_wda_url("test-device")
    assert not calls[0][2].parent.exists()


async def test_query_failure_does_not_use_a_success_looking_output(monkeypatch):
    install_process(monkeypatch, returncode=1)
    with pytest.raises(CoreDeviceDiscoveryError, match="查询失败"):
        await discovery.discover_coredevice_wda_url("test-device")


async def test_timeout_terminates_only_own_query_and_removes_temporary_output(monkeypatch):
    _, calls, signals = install_process(monkeypatch, blocked=True)
    monkeypatch.setattr(discovery, "QUERY_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(CoreDeviceDiscoveryError, match="超时"):
        await discovery.discover_coredevice_wda_url("test-device")
    assert signals == [signal.SIGTERM]
    assert not calls[0][2].parent.exists()


async def test_cancellation_terminates_query_preserves_cancellation_and_removes_output(monkeypatch):
    _, calls, signals = install_process(monkeypatch, blocked=True)
    task = asyncio.create_task(discovery.discover_coredevice_wda_url("test-device"))
    while not calls:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signals == [signal.SIGTERM]
    assert not calls[0][2].parent.exists()


async def test_cancellation_during_creation_still_collects_and_terminates_spawned_query(monkeypatch):
    process, calls, signals = install_process(monkeypatch, blocked=True)
    spawn = discovery.asyncio.create_subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(discovery.asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(discovery.discover_coredevice_wda_url("test-device"))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode is not None and signals == [signal.SIGTERM]
    assert not calls[0][2].parent.exists()


async def test_result_symlink_is_rejected_without_removing_its_target(monkeypatch, tmp_path):
    target = tmp_path / "unrelated.json"
    target.write_text(json.dumps(details()))
    process = FakeProcess()

    async def spawn(*args, **kwargs):
        output = Path(args[args.index("--json-output") + 1])
        output.unlink()
        output.symlink_to(target)
        return process

    monkeypatch.setattr(discovery.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(CoreDeviceDiscoveryError):
        await discovery.discover_coredevice_wda_url("test-device")
    assert target.exists()


@pytest.mark.parametrize("udid", ["auto", "--help", "device name", "", "device;other"])
async def test_invalid_udid_never_spawns_a_command(monkeypatch, udid):
    _, calls, _ = install_process(monkeypatch)
    with pytest.raises(CoreDeviceDiscoveryError):
        await discovery.discover_coredevice_wda_url(udid)
    assert not calls

import asyncio
import base64
import json
import os

import pytest

from vico_iphone.iphone_control import PhoneBridgeError, PhoneError
from vico_iphone.server import PhoneService
from vico_iphone.state import read_json, write_json


class Controller:
    def __init__(self):
        self.session_id = self.owner = None
        self.uncertain = False
        self.calls = []
        self.observation_failure = False
        self.connect_failure = None
        self.action_failure = None

    async def session(self, owner, action, bundle=None):
        self.calls.append(("session", action))
        if action == "connect":
            if self.connect_failure:
                self.owner, self.uncertain = owner, True
                raise self.connect_failure
            self.owner, self.session_id = owner, "wda-session"
            return {"connected": True}
        if action == "disconnect":
            self.clear_session()
            return {"connected": False}
        return {"connected": bool(self.session_id)}

    def clear_session(self):
        self.owner = self.session_id = None
        self.uncertain = False

    def clear_abandoned_lease_after_bridge_reset(self):
        return True

    async def observe(self, owner, **kwargs):
        self.calls.append(("observe", kwargs))
        if self.observation_failure:
            raise PhoneError("components temporarily unavailable")
        return {"observation_id": "fresh", "elements": [{"ref": "e1", "label": "Settings"}]}, (b"PNG" if kwargs["capture_image"] else b"")

    async def act(self, owner, args):
        self.calls.append(("act", args.action))
        if self.action_failure:
            raise self.action_failure
        return {"action_dispatched": True, "verified": False}

    async def launch(self, owner, bundle):
        self.calls.append(("launch", bundle))
        return {"action_dispatched": True}

    async def finish(self, owner):
        return await self.session(owner, "disconnect")


async def bridge(home, action):
    return {"ok": True, "running": action == "start", "stopped": action == "stop", "owned": True}


def data(result):
    return json.loads(result.content[0].text)


@pytest.fixture
def service(tmp_path):
    return PhoneService(tmp_path, Controller(), bridge, backend="appium")


async def connect(service):
    value = await service.call("iphone_session", {"action": "connect"})
    assert not value.isError
    return data(value)["session_token"]


async def test_token_is_required_and_not_leaked_in_status(service):
    token = await connect(service)
    status = data(await service.call("iphone_status", {}))
    assert token not in json.dumps(status)
    failure = await service.call("iphone_observe", {"session_token": "wrong"})
    assert failure.isError
    assert not any(c[0] == "observe" for c in service.controller.calls)


async def test_components_never_capture_or_emit_image(service):
    token = await connect(service)
    value = await service.call("iphone_observe", {"session_token": token})
    assert [block.type for block in value.content] == ["text"]
    assert service.controller.calls[-1] == ("observe", {"include_elements": True, "capture_image": False})


async def test_screenshot_returns_image_and_skips_components(service):
    token = await connect(service)
    value = await service.call("iphone_observe", {"session_token": token, "mode": "screenshot"})
    assert [block.type for block in value.content] == ["text", "image"]
    assert base64.b64decode(value.content[1].data) == b"PNG"
    assert service.controller.calls[-1][1] == {"include_elements": False, "capture_image": True}


async def test_action_returns_next_components_in_same_call(service):
    token = await connect(service)
    value = data(await service.call("iphone_act", {"session_token": token, "observation_id": "old", "action": "tap", "element_ref": "e1"}))
    assert value["action"]["action_dispatched"] is True
    assert value["observation"]["observation_id"] == "fresh"
    assert service.controller.calls[-1][1]["capture_image"] is False


async def test_failed_post_observation_does_not_erase_action_receipt(service):
    token = await connect(service)
    service.controller.observation_failure = True
    value = await service.call("iphone_act", {"session_token": token, "observation_id": "old", "action": "home"})
    assert not value.isError
    assert data(value)["action"]["action_dispatched"]
    assert "observation_error" in data(value)
    assert service.controller.calls.count(("act", "home")) == 1


async def test_unknown_action_not_replayed_and_journal_retained(service):
    token = await connect(service)
    service.controller.action_failure = PhoneBridgeError("timeout", request_outcome="unknown")
    value = await service.call("iphone_act", {"session_token": token, "observation_id": "old", "action": "home"})
    assert value.isError and data(value)["request_outcome"] == "unknown"
    assert not data(value)["replay_action"]
    assert service.controller.calls.count(("act", "home")) == 1
    assert read_json(service.journal)["phase"] == "action_in_flight"


async def test_second_process_cannot_take_over_or_stop(service):
    await connect(service)
    other = PhoneService(service.home, Controller(), bridge, backend="appium")
    assert (await other.call("iphone_session", {"action": "connect"})).isError
    assert (await other.call("iphone_setup", {"action": "stop"})).isError


async def test_crash_journal_prevents_silent_reconnect_and_reset_is_explicit(service):
    write_json(service.journal, {"owner": "dead-client", "pid": -1, "phase": "action_in_flight"})
    assert (await service.call("iphone_session", {"action": "connect"})).isError
    assert (await service.call("iphone_setup", {"action": "start"})).isError
    assert not (await service.call("iphone_setup", {"action": "stop"})).isError
    assert not service.journal.exists()


async def test_unconfirmed_stop_retains_abandoned_session(service):
    write_json(service.journal, {"owner": "dead-client", "pid": -1, "phase": "connecting"})
    async def no_owned_bridge(home, action):
        return {"running": False, "stopped": False}
    service.bridge = no_owned_bridge
    response = data(await service.call("iphone_setup", {"action": "stop"}))
    assert response["session_recovery_required"]
    assert service.journal.exists()


async def test_failed_unknown_connect_can_stop_owned_bridge(service):
    service.controller.connect_failure = PhoneBridgeError("connect response lost", request_outcome="unknown")
    assert (await service.call("iphone_session", {"action": "connect"})).isError
    assert read_json(service.journal)["phase"] == "connecting"
    assert not (await service.call("iphone_setup", {"action": "stop"})).isError
    assert service.controller.owner is None
    assert not service.journal.exists()


async def test_disconnect_and_stdio_close_clean_known_sessions(service):
    token = await connect(service)
    assert not (await service.call("iphone_session", {"action": "disconnect", "session_token": token})).isError
    assert not service.journal.exists()
    await connect(service)
    await service.close()
    assert service.controller.session_id is None
    assert not service.journal.exists()


async def test_session_journal_private_permissions(service):
    await connect(service)
    assert service.journal.stat().st_mode & 0o777 == 0o600
    assert service.home.stat().st_mode & 0o777 == 0o700


async def test_busy_calls_do_not_queue_mutations(service):
    async with service.mutex:
        value = await service.call("iphone_act", {})
        assert value.isError and data(value)["busy"]
        assert not service.controller.calls


class SlowController(Controller):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.complete = asyncio.Event()
        self.cancelled = False

    async def session(self, owner, action, bundle=None):
        if action != 'connect':
            return await super().session(owner, action, bundle)
        self.calls.append(('session', 'connect'))
        self.owner, self.uncertain = owner, True
        self.started.set()
        try:
            await self.complete.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.connect_failure:
            raise self.connect_failure
        self.session_id, self.uncertain = 'wda-session', False
        return {'connected': True, 'bundle_id': bundle}


@pytest.fixture
def slow_service(tmp_path):
    return PhoneService(tmp_path, SlowController(), bridge, connect_wait_seconds=0.001, backend="appium")


async def pending_connect(service):
    response = await service.call('iphone_session', {'action': 'connect'})
    payload = data(response)
    assert not response.isError and payload['pending']
    assert len(payload['operation_id']) == 48 and 'session_token' not in payload
    return payload['operation_id']


async def test_pending_connect_keeps_both_locks_without_repeating_request(slow_service):
    from vico_iphone.state import operation_lock

    operation_id = await pending_connect(slow_service)
    assert slow_service.mutex.locked()
    assert read_json(slow_service.journal)['phase'] == 'connecting'
    with pytest.raises(RuntimeError, match='in progress'):
        with operation_lock(slow_service.home):
            pytest.fail('background connection must retain the process lock')
    pending = data(await slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id}))
    assert pending['pending'] and pending['operation_id'] == operation_id
    assert slow_service.controller.calls.count(('session', 'connect')) == 1
    assert not slow_service.controller.cancelled
    slow_service.controller.complete.set()
    finished = await slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id})
    assert data(finished)['connected'] and data(finished)['session_token']
    assert not slow_service.mutex.locked()
    with operation_lock(slow_service.home):
        pass
    await slow_service.close()


async def test_connection_poll_requires_private_operation_and_caches_original_result(slow_service):
    operation_id = await pending_connect(slow_service)
    for private_id in (None, 'f' * 48, '非' * 48):
        response = await slow_service.call('iphone_session', {
            'action': 'connect_status', 'operation_id': private_id})
        assert response.isError and operation_id not in str(response)
    slow_service.controller.complete.set()
    arguments = {'action': 'connect_status', 'operation_id': operation_id}
    first = await slow_service.call('iphone_session', arguments)
    second = await slow_service.call('iphone_session', arguments)
    assert first == second and data(first)['session_token']
    assert slow_service.controller.calls.count(('session', 'connect')) == 1
    await slow_service.close()


async def test_pending_busy_and_status_never_reveal_operation_or_session_credentials(slow_service):
    operation_id = await pending_connect(slow_service)
    for name, args in (
        ('iphone_status', {}), ('iphone_setup', {'action': 'stop'}),
        ('iphone_session', {'action': 'connect'}), ('iphone_act', {}),
    ):
        response = await slow_service.call(name, args)
        assert data(response)['busy']
        assert 'operation_id' not in data(response) and operation_id not in str(response)
        assert 'session_token' not in data(response)
    slow_service.controller.complete.set()
    finished = await slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id})
    token = data(finished)['session_token']
    status = await slow_service.call('iphone_status', {})
    assert token not in str(status) and operation_id not in str(status)
    duplicate = await slow_service.call('iphone_session', {'action': 'connect'})
    assert duplicate.isError
    assert token not in str(duplicate) and operation_id not in str(duplicate)
    assert slow_service.controller.calls.count(('session', 'connect')) == 1
    await slow_service.close()


async def test_cancelled_poll_does_not_cancel_connection_or_resend(slow_service):
    operation_id = await pending_connect(slow_service)
    slow_service.connect_wait_seconds = 1
    poll = asyncio.create_task(slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id}))
    await asyncio.sleep(0)
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poll
    assert not slow_service.controller.cancelled and not slow_service.connect_task.done()
    slow_service.controller.complete.set()
    finished = await slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id})
    assert data(finished)['connected']
    assert slow_service.controller.calls.count(('session', 'connect')) == 1
    await slow_service.close()


async def test_cancelled_first_wait_does_not_cancel_background_connect(slow_service):
    slow_service.connect_wait_seconds = 1
    first_wait = asyncio.create_task(slow_service.call('iphone_session', {'action': 'connect'}))
    await slow_service.controller.started.wait()
    first_wait.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_wait
    assert not slow_service.controller.cancelled and slow_service.mutex.locked()
    await slow_service.close()
    assert read_json(slow_service.journal)['phase'] == 'connecting'


async def test_shutdown_cancels_background_connect_and_preserves_unknown_state(slow_service):
    operation_id = await pending_connect(slow_service)
    await asyncio.wait_for(slow_service.close(), timeout=0.5)
    assert slow_service.controller.cancelled and slow_service.connect_task.cancelled()
    assert slow_service.controller.uncertain
    assert read_json(slow_service.journal)['phase'] == 'connecting'
    assert ('session', 'disconnect') not in slow_service.controller.calls
    assert not slow_service.mutex.locked()
    assert (await slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id})).isError
    replacement = PhoneService(slow_service.home, Controller(), bridge, backend="appium")
    assert (await replacement.call('iphone_session', {'action': 'connect'})).isError
    assert not replacement.controller.calls


async def test_unknown_connection_failure_is_returned_by_poll_without_retry(slow_service):
    slow_service.controller.connect_failure = PhoneBridgeError('reply lost', request_outcome='unknown')
    operation_id = await pending_connect(slow_service)
    slow_service.controller.complete.set()
    arguments = {'action': 'connect_status', 'operation_id': operation_id}
    failed = await slow_service.call('iphone_session', arguments)
    assert failed.isError and data(failed)['request_outcome'] == 'unknown'
    assert await slow_service.call('iphone_session', arguments) == failed
    assert slow_service.controller.calls.count(('session', 'connect')) == 1
    assert read_json(slow_service.journal)['phase'] == 'connecting'
    await slow_service.close()
    assert slow_service.journal.exists()


async def test_disconnect_invalidates_completed_connection_operation(slow_service):
    operation_id = await pending_connect(slow_service)
    slow_service.controller.complete.set()
    finished = await slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id})
    token = data(finished)['session_token']
    assert not (await slow_service.call('iphone_session', {
        'action': 'disconnect', 'session_token': token})).isError
    assert (await slow_service.call('iphone_session', {
        'action': 'connect_status', 'operation_id': operation_id})).isError


@pytest.mark.parametrize('own_unknown', [False, True])
async def test_bridge_stop_keeps_journal_and_controller_if_device_marker_cannot_clear(service, own_unknown):
    if own_unknown:
        service.controller.owner = service.owner
        service.controller.uncertain = True
    write_json(service.journal, {'owner': service.owner if own_unknown else 'dead-client',
                                'pid': os.getpid() if own_unknown else -1, 'phase': 'connecting'})
    service.controller.clear_abandoned_lease_after_bridge_reset = lambda: False
    original = read_json(service.journal)
    response = await service.call('iphone_setup', {'action': 'stop'})
    assert data(response)['stopped'] and data(response)['session_recovery_required']
    assert read_json(service.journal) == original
    assert service.controller.uncertain == own_unknown
    if own_unknown:
        assert service.controller.owner == service.owner


def test_connection_poll_keeps_six_tools_and_defaults_to_bounded_wait():
    from vico_iphone.server import CONNECT_POLL_WAIT_SECONDS, TOOLS

    assert 20 <= CONNECT_POLL_WAIT_SECONDS <= 25
    assert len(TOOLS) == 6
    session_tool = next(tool for tool in TOOLS if tool.name == 'iphone_session')
    assert 'connect_status' in session_tool.inputSchema['properties']['action']['enum']
    assert 'operation_id' in session_tool.inputSchema['properties']

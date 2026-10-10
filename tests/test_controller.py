from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest
from pydantic import ValidationError

from vico_iphone import iphone_control as control
from vico_iphone.iphone_control import (
    IPhoneController, PhoneAction, PhoneBridgeError, PhoneConfig,
    PhoneError, PhoneObservationRequired, parse_elements,
)

SOURCE = ('<AppiumAUT><XCUIElementTypeApplication>'
          '<XCUIElementTypeTextField name="search" visible="true" enabled="true" '
          'x="1" y="2" width="100" height="30" />'
          '</XCUIElementTypeApplication></AppiumAUT>')
PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR'
       + (390).to_bytes(4, 'big') + (844).to_bytes(4, 'big'))


class FakePhone:
    def __init__(self):
        self.calls = []
        self.source = SOURCE
        self.bundle = 'com.apple.springboard'
        self.locked = False
        self.fail_mutation = False
        self.matches = [{'element-6066-11e4-a52e-4f735466cecf': 'element-1'}]
        self.screen = {'width': 390, 'height': 844}

    def __call__(self, request):
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.calls.append((request.method, path, body))
        value = None
        if path == '/session':
            value = {'sessionId': 'session-1'}
        elif path.endswith('/source'):
            value = self.source
        elif path.endswith('/screenshot'):
            value = base64.b64encode(PNG).decode()
        elif path.endswith('/elements'):
            value = self.matches
        elif path.endswith('/execute/sync'):
            script = body['script']
            if script == 'mobile: isLocked':
                value = self.locked
            elif script == 'mobile: activeAppInfo':
                value = {'bundleId': self.bundle}
            elif script == 'mobile: deviceScreenInfo':
                value = {'screenSize': self.screen, 'scale': 3}
            elif self.fail_mutation:
                raise httpx.ReadTimeout('reply lost', request=request)
            elif script == 'mobile: activateApp':
                self.bundle = body['args'][0]['bundleId']
        elif self.fail_mutation:
            raise httpx.ReadTimeout('reply lost', request=request)
        return httpx.Response(200, json={'value': value})

    def mutations(self):
        return [call for call in self.calls if (
            call[1].endswith(('/click', '/value'))
            or call[2].get('script') in {
                'mobile: tap', 'mobile: swipe', 'mobile: pressButton', 'mobile: activateApp'})]


@pytest.fixture(autouse=True)
def isolated_device_leases(monkeypatch, tmp_path):
    monkeypatch.setattr(control, 'DEVICE_LEASE_DIRECTORY', tmp_path / 'shared-leases')


@pytest.fixture
def phone(tmp_path):
    fake = FakePhone()
    controller = IPhoneController(
        tmp_path / 'data',
        config=PhoneConfig(enabled=True, udid='test-device', team_id='TEST'),
        transport=httpx.MockTransport(fake),
    )
    yield controller, fake
    controller.release_lease()


async def connected(phone):
    controller, fake = phone
    await controller.session('task', 'connect')
    return controller, fake


def test_standalone_defaults_and_no_financial_intent_field():
    config = PhoneConfig()
    assert config.port == 4737
    assert config.control_scope == 'device' and config.allowed_bundles == []
    assert config.wda_bundle_id == 'com.vico.iphone.WebDriverAgentRunner'
    assert 'financial_commit' not in PhoneAction.model_fields


async def test_disabled_configuration_does_not_contact_phone(tmp_path):
    fake = FakePhone()
    controller = IPhoneController(tmp_path, transport=httpx.MockTransport(fake))
    with pytest.raises(PhoneError, match='尚未配置'):
        await controller.session('task', 'connect')
    assert not fake.calls


@pytest.mark.parametrize('bundle', [None, 'com.example.app', '应用名称'])
async def test_connect_has_no_app_launch_side_effect(phone, bundle):
    controller, fake = phone
    result = await controller.session('task', 'connect', bundle)
    assert len(fake.calls) == 1
    caps = fake.calls[0][2]['capabilities']['alwaysMatch']
    assert 'appium:bundleId' not in caps and 'appium:app' not in caps
    assert caps['appium:autoLaunch'] is False and caps['appium:shouldTerminateApp'] is False
    assert result['bundle_id'] is None and result['lifetime'] == 'mcp_session'


async def test_config_from_independent_data_directory(tmp_path):
    fake = FakePhone()
    configuration = tmp_path / 'configuration'
    configuration.mkdir()
    configuration.joinpath('iphone-control.json').write_text(PhoneConfig(
        enabled=True, udid='local-device', team_id='TEAM').model_dump_json())
    controller = IPhoneController(tmp_path, transport=httpx.MockTransport(fake))
    try:
        await controller.session('task', 'connect')
        assert fake.calls[0][2]['capabilities']['alwaysMatch']['appium:udid'] == 'local-device'
    finally:
        controller.release_lease()


@pytest.mark.parametrize('field,capability', [
    ('wda_url', 'appium:webDriverAgentUrl'),
    ('wda_base_url', 'appium:wdaBaseUrl'),
])
async def test_configured_wireless_capabilities(phone, field, capability):
    controller, fake = phone
    controller.config_override = controller.config_override.model_copy(update={
        field: 'http://192.168.1.10:8100', 'platform_version': '26.1',
        'wda_bundle_id': 'com.example.control.WebDriverAgentRunner',
    })
    await controller.session('task', 'connect')
    caps = fake.calls[0][2]['capabilities']['alwaysMatch']
    assert caps[capability] == 'http://192.168.1.10:8100'
    assert caps['appium:updatedWDABundleId'] == 'com.example.control.WebDriverAgentRunner'
    assert caps['appium:platformVersion'] == '26.1'
    assert caps['appium:autoLaunch'] is False
    assert caps['appium:skipLogCapture'] is True
    assert ('appium:wdaLaunchTimeout' in caps) == (field == 'wda_base_url')


@pytest.mark.parametrize('url', [
    'https://example.com:8100', 'http://user@127.0.0.1:8100',
    'http://8.8.8.8:8100', 'http://127.0.0.1:8100/path',
    'http://127.0.0.1:8100?key=private',
])
def test_wda_rejects_unapproved_targets(url):
    with pytest.raises(ValidationError):
        PhoneConfig(wda_url=url, udid='device', platform_version='26.1')


async def test_coredevice_address_is_fresh_each_connection(phone, monkeypatch):
    controller, fake = phone
    controller.config_override = controller.config_override.model_copy(update={
        'wda_transport': 'coredevice', 'platform_version': '26.1'})
    addresses = iter(['http://[fd12::1]:8100', 'http://[fd12::2]:8100'])

    async def discover(udid):
        assert udid == 'test-device'
        return next(addresses)

    monkeypatch.setattr(control, 'discover_coredevice_wda_url', discover)
    await controller.session('task', 'connect')
    await controller.session('task', 'disconnect')
    await controller.session('task', 'connect')
    capabilities = [body['capabilities']['alwaysMatch'] for method, path, body in fake.calls if path == '/session']
    assert [caps['appium:wdaBaseUrl'] for caps in capabilities] == ['http://[fd12::1]:8100', 'http://[fd12::2]:8100']
    assert controller.config.wda_base_url == ''


async def test_preinstalled_wda_requires_ready_remotexpc_without_passing_ula(phone, monkeypatch):
    controller, fake = phone
    controller.config_override = controller.config_override.model_copy(update={
        'wda_transport': 'coredevice', 'platform_version': '26.1',
        'use_preinstalled_wda': True, 'prebuilt_wda_path': '/example/Runner.app'})
    checks = []

    async def discover(udid):
        checks.append(('device', udid))
        return 'http://[fd12::1]:8100'

    async def ready(udid):
        checks.append(('tunnel', udid))
        return {'address': 'fd12::1', 'rsd_port': 1234, 'service_count': 1}

    monkeypatch.setattr(control, 'discover_coredevice_wda_url', discover)
    monkeypatch.setattr(control, 'require_remotexpc_tunnel', ready)
    await controller.session('task', 'connect')
    caps = fake.calls[0][2]['capabilities']['alwaysMatch']
    assert checks == [('device', 'test-device'), ('tunnel', 'test-device')]
    assert caps['appium:usePreinstalledWDA'] is True
    assert caps['appium:prebuiltWDAPath'] == '/example/Runner.app'
    assert 'appium:wdaBaseUrl' not in caps and 'appium:xcodeOrgId' not in caps


async def test_observe_can_avoid_screenshot_acquisition_completely(phone):
    controller, fake = await connected(phone)
    result, image = await controller.observe('task', capture_image=False)
    assert result['components_status'] == 'available' and len(result['elements']) == 1
    assert result['capture_image'] is False and image == b''
    assert result['width'] is None and result['height'] is None
    assert not any(path.endswith('/screenshot') for _, path, _ in fake.calls)
    action = await controller.act('task', PhoneAction(
        observation_id=result['observation_id'], action='type', element_ref='e1', text='hello'))
    assert action['action_dispatched'] is True
    assert fake.mutations()[0][1].endswith('/element/element-1/value')


async def test_default_observe_provides_original_image_and_components(phone):
    controller, _ = await connected(phone)
    result, image = await controller.observe('task')
    assert image == PNG and result['width'] == 390 and result['height'] == 844
    assert result['elements_retained'] == result['elements_total'] == 1
    assert result['capture_image'] and result['include_elements']


async def test_component_only_failure_does_not_return_empty_action_token_or_screenshot(phone):
    controller, fake = await connected(phone)
    fake.source = '<invalid'
    with pytest.raises(PhoneObservationRequired, match='组件读取失败'):
        await controller.observe('task', capture_image=False)
    assert controller.observation is None
    assert not any(path.endswith('/screenshot') for _, path, _ in fake.calls)


async def test_source_failure_can_fall_back_to_requested_image(phone):
    controller, fake = await connected(phone)
    fake.source = '<invalid'
    result, image = await controller.observe('task')
    assert result['components_status'] == 'unavailable' and image == PNG
    assert result['observation_id']


async def test_action_revalidates_component_and_consumes_token(phone):
    controller, fake = await connected(phone)
    result, _ = await controller.observe('task', capture_image=False)
    args = PhoneAction(observation_id=result['observation_id'], action='tap', element_ref='e1')
    await controller.act('task', args)
    assert len(fake.mutations()) == 1
    assert any(path.endswith('/elements') and body['using'] == 'xpath' for _, path, body in fake.calls)
    with pytest.raises(PhoneError, match='观察已过期'):
        await controller.act('task', args)
    assert len(fake.mutations()) == 1


@pytest.mark.parametrize('change', ['expired', 'replaced', 'duplicate-component', 'moved-screen'])
async def test_stale_observation_never_dispatches_an_action(phone, change):
    controller, fake = await connected(phone)
    result, _ = await controller.observe('task', capture_image=False)
    if change == 'expired':
        controller.observation['time'] = time.monotonic() - 61
    elif change == 'replaced':
        await controller.observe('task', capture_image=False)
    elif change == 'duplicate-component':
        fake.matches *= 2
    else:
        fake.screen = {'width': 844, 'height': 390}
    with pytest.raises(PhoneError):
        await controller.act('task', PhoneAction(
            observation_id=result['observation_id'], action='tap', element_ref='e1'))
    assert not fake.mutations() and controller.observation is None


async def test_unknown_action_outcome_is_never_replayed(phone):
    controller, fake = await connected(phone)
    result, _ = await controller.observe('task', capture_image=False)
    args = PhoneAction(observation_id=result['observation_id'], action='tap', x=0.5, y=0.5)
    fake.fail_mutation = True
    with pytest.raises(PhoneBridgeError) as error:
        await controller.act('task', args)
    assert error.value.request_outcome == 'unknown'
    assert len(fake.mutations()) == 1 and controller.observation is None
    with pytest.raises(PhoneError, match='观察已过期'):
        await controller.act('task', args)
    assert len(fake.mutations()) == 1
    fake.fail_mutation = False
    result, _ = await controller.observe('task', capture_image=False)
    assert result['observation_id'] != args.observation_id
    assert len(fake.mutations()) == 1


async def test_known_unsubmitted_connection_releases_owner_and_lease(phone):
    controller, fake = phone

    def refused(request):
        raise httpx.ConnectError('refused', request=request)

    controller.transport = httpx.MockTransport(refused)
    with pytest.raises(PhoneBridgeError) as error:
        await controller.session('task', 'connect')
    assert error.value.request_outcome == 'not_sent'
    assert controller.owner is None and controller.lease is None and not controller.uncertain
    controller.transport = httpx.MockTransport(fake)
    await controller.session('task', 'connect')
    assert controller.session_id == 'session-1'


async def test_unknown_connection_retains_lease_and_blocks_retry(phone):
    controller, _ = phone
    attempts = []

    def lost(request):
        attempts.append(request.url.path)
        raise httpx.ReadTimeout('reply lost', request=request)

    controller.transport = httpx.MockTransport(lost)
    with pytest.raises(PhoneBridgeError):
        await controller.session('task', 'connect')
    assert controller.uncertain and controller.lease is not None
    with pytest.raises(PhoneError, match='已有会话'):
        await controller.session('task', 'connect')
    assert attempts == ['/session']


async def test_launch_is_explicit_invalidates_observation_and_requires_verification(phone):
    controller, fake = await connected(phone)
    await controller.observe('task', capture_image=False)
    result = await controller.launch('task', 'com.example.target')
    assert result['action_dispatched'] and result['verified'] is False
    assert controller.observation is None
    assert fake.mutations() == [('POST', '/session/session-1/execute/sync', {
        'script': 'mobile: activateApp', 'args': [{'bundleId': 'com.example.target'}]})]
    observed, _ = await controller.observe('task', capture_image=False)
    assert observed['bundle_id'] == 'com.example.target'


async def test_locked_phone_rejects_launch_without_unlocking(phone):
    controller, fake = await connected(phone)
    fake.locked = True
    with pytest.raises(PhoneError, match='锁定'):
        await controller.launch('task', 'com.example.target')
    assert not fake.mutations()


async def test_unknown_launch_has_one_attempt_and_no_usable_old_observation(phone):
    controller, fake = await connected(phone)
    await controller.observe('task', capture_image=False)
    fake.fail_mutation = True
    with pytest.raises(PhoneBridgeError) as error:
        await controller.launch('task', 'com.example.target')
    assert error.value.request_outcome == 'unknown'
    assert len(fake.mutations()) == 1 and controller.observation is None


async def test_app_scope_and_session_ownership_are_enforced(phone):
    controller, fake = phone
    controller.config_override = controller.config_override.model_copy(update={
        'control_scope': 'apps', 'allowed_bundles': ['com.example.target']})
    with pytest.raises(PhoneError, match='允许列表'):
        await controller.session('task', 'connect', 'com.other.app')
    assert not fake.calls
    await controller.session('task', 'connect', 'com.example.target')
    with pytest.raises(PhoneError, match='当前会话绑定'):
        await controller.launch('task', 'com.other.app')
    with pytest.raises(PhoneError, match='没有手机控制会话'):
        await controller.observe('other')
    with pytest.raises(PhoneError, match='持有会话'):
        await controller.session('other', 'disconnect')
    assert not fake.mutations()


async def test_device_lease_is_shared_across_different_data_directories(phone, tmp_path):
    first, fake = await connected(phone)
    second = IPhoneController(tmp_path / 'different-data', config=first.config_override,
                              transport=httpx.MockTransport(fake))
    try:
        with pytest.raises(PhoneError, match='另一个 iphone-use'):
            await second.session('other-task', 'connect')
        assert sum(path == '/session' for _, path, _ in fake.calls) == 1
        await first.session('task', 'disconnect')
        await second.session('other-task', 'connect')
    finally:
        second.release_lease()


async def test_different_device_has_independent_lease(phone, tmp_path):
    first, fake = await connected(phone)
    second = IPhoneController(tmp_path / 'different-data', config=first.config_override.model_copy(
        update={'udid': 'other-device'}), transport=httpx.MockTransport(fake))
    try:
        await second.session('other-task', 'connect')
        assert second.session_id
    finally:
        second.release_lease()


async def test_full_component_count_and_text_are_retained_by_default(phone):
    controller, fake = await connected(phone)
    long_label = 'Visible text ' * 100
    fake.source = '<XCUIElementTypeApplication>' + ''.join(
        f'<XCUIElementTypeButton name="item-{i}" label="{long_label}" '
        f'visible="true" enabled="true" x="1" y="{i}" width="100" height="1" />'
        for i in range(401)) + '</XCUIElementTypeApplication>'
    result, _ = await controller.observe('task', capture_image=False)
    assert result['elements_total'] == result['elements_retained'] == len(result['elements']) == 401
    assert not result['elements_truncated'] and result['elements_limit'] is None
    assert result['elements'][-1]['label'] == long_label
    assert result['elements'][-1]['ref'] == 'e401'


def test_optional_component_limit_reports_full_count():
    source = '<XCUIElementTypeApplication>' + ''.join(
        '<XCUIElementTypeButton visible="true" />' for _ in range(5)) + '</XCUIElementTypeApplication>'
    elements, _, metadata = parse_elements(source, limit=2)
    assert len(elements) == 2 and metadata == {'total': 5, 'retained': 2, 'truncated': True}


def test_secure_field_value_is_redacted():
    source = '<XCUIElementTypeSecureTextField visible="true" value="private-value" />'
    elements, _, _ = parse_elements(source)
    assert elements[0]['value'] == '[redacted]'


@pytest.mark.parametrize('source', ['<!DOCTYPE foo><root />', '<!ENTITY secret "x"><root />', '<invalid'])
def test_invalid_xml_is_rejected(source):
    with pytest.raises(PhoneError):
        parse_elements(source)


async def test_device_lease_blocks_another_process(phone):
    import subprocess
    import sys

    controller, _ = await connected(phone)
    path = next(control.DEVICE_LEASE_DIRECTORY.glob('*.lock'))
    probe = '''import fcntl, sys
with open(sys.argv[1], 'a') as lease:
    try:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(42)
'''
    assert subprocess.run([sys.executable, '-c', probe, str(path)], check=False).returncode == 42
    await controller.session('task', 'disconnect')
    assert subprocess.run([sys.executable, '-c', probe, str(path)], check=False).returncode == 0


async def test_cancelled_action_consumes_observation_without_replaying(phone):
    controller, fake = await connected(phone)
    observed, _ = await controller.observe('task', capture_image=False)
    mutation_started = asyncio.Event()
    original_handler = fake
    attempts = []

    async def handler(request):
        body = json.loads(request.content) if request.content else {}
        if body.get('script') == 'mobile: tap':
            attempts.append(body)
            mutation_started.set()
            await asyncio.Event().wait()
        return original_handler(request)

    controller.transport = httpx.MockTransport(handler)
    args = PhoneAction(observation_id=observed['observation_id'], action='tap', x=0.5, y=0.5)
    task = asyncio.create_task(controller.act('task', args))
    await mutation_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert controller.observation is None and len(attempts) == 1
    assert controller.last_diagnostic['request_outcome'] == 'unknown'
    with pytest.raises(PhoneError, match='观察已过期'):
        await controller.act('task', args)
    assert len(attempts) == 1


async def test_disconnect_unknown_keeps_lease_for_reconciliation(phone):
    controller, fake = await connected(phone)
    fake.fail_mutation = True
    result = await controller.finish('task')
    assert result['needs_attention'] and controller.session_id == 'session-1'
    assert controller.owner == 'task' and controller.lease is not None
    assert controller.observation is None


async def test_action_error_does_not_echo_sensitive_text(phone):
    controller, _ = await connected(phone)
    controller.transport = httpx.MockTransport(lambda request: httpx.Response(500, json={
        'value': {'error': 'unknown error', 'message': 'private user input'}}))
    with pytest.raises(PhoneBridgeError) as error:
        await controller.request('POST', '/session/session-1/element/1/value', {'text': 'private user input'})
    assert 'private user input' not in str(error.value)
    assert 'private user input' not in str(controller.last_diagnostic)


async def test_launch_rechecks_configuration_immediately_before_dispatch(phone):
    controller, fake = await connected(phone)
    original = controller.mobile

    async def mobile(name, args=None):
        value = await original(name, args)
        if name == 'isLocked':
            controller.config_override = controller.config_override.model_copy(update={'enabled': False})
        return value

    controller.mobile = mobile
    with pytest.raises(PhoneError, match='配置已改变'):
        await controller.launch('task', 'com.example.target')
    assert not fake.mutations()


def marker_files():
    return list(control.DEVICE_LEASE_DIRECTORY.glob('*.session.json'))


@pytest.mark.parametrize('failure', ['not_sent', 'rejected'])
async def test_device_marker_not_sent_cleanup(phone, failure):
    controller, _ = phone

    def fail(request):
        markers = marker_files()
        assert len(markers) == 1
        marker = json.loads(markers[0].read_text())
        assert marker['phase'] == 'connecting' and marker['session_id'] is None
        assert marker['state_directory'] == str(controller.data_directory.resolve())
        if failure == 'not_sent':
            raise httpx.ConnectError('not sent', request=request)
        return httpx.Response(500, json={'value': {'error': 'session not created'}})

    controller.transport = httpx.MockTransport(fail)
    with pytest.raises(PhoneBridgeError) as error:
        await controller.session('task', 'connect')
    assert error.value.request_outcome == failure
    assert not marker_files() and controller.lease is None


async def test_device_marker_unknown_crosshome_blocks_after_process_lease_ends(phone, tmp_path):
    first, fake = phone
    attempts = []

    def unknown(request):
        attempts.append(request.url.path)
        raise httpx.ReadTimeout('reply lost', request=request)

    first.transport = httpx.MockTransport(unknown)
    with pytest.raises(PhoneBridgeError):
        await first.session('task', 'connect')
    marker = json.loads(marker_files()[0].read_text())
    assert marker['phase'] == 'connecting' and marker['session_id'] is None
    assert set(marker) == {'schema_version', 'owner_pid', 'owner_id', 'state_directory',
                           'bridge_port', 'phase', 'session_id'}
    first.release_lease()  # Models process exit; durable marker must remain.
    second = IPhoneController(tmp_path / 'other-home', config=first.config_override,
                              transport=httpx.MockTransport(fake))
    try:
        with pytest.raises(PhoneError, match='未清理的会话记录'):
            await second.session('other', 'connect')
        assert not fake.calls and attempts == ['/session']
        assert len(marker_files()) == 1
        assert second.owner is None and second.lease is None
    finally:
        second.release_lease()


async def test_device_marker_updates_on_connect_and_clears_after_confirmed_disconnect(phone):
    controller, _ = await connected(phone)
    marker = json.loads(marker_files()[0].read_text())
    assert marker['phase'] == 'connected' and marker['session_id'] == 'session-1'
    await controller.session('task', 'disconnect')
    assert not marker_files()


async def test_owned_bridge_reset_clears_only_matching_abandoned_device_marker(phone, tmp_path, monkeypatch):
    first, fake = await connected(phone)
    first.release_lease()  # Models an owner that exited without confirmed cleanup.
    monkeypatch.setattr(control, 'device_owner_alive', lambda pid: False)
    foreign = IPhoneController(tmp_path / 'other-home', config=first.config_override,
                               transport=httpx.MockTransport(fake))
    assert foreign.clear_abandoned_lease_after_bridge_reset() is False
    assert len(marker_files()) == 1
    wrong_port = IPhoneController(first.data_directory, config=first.config_override.model_copy(
        update={'port': 4738}), transport=httpx.MockTransport(fake))
    assert wrong_port.clear_abandoned_lease_after_bridge_reset() is False
    assert len(marker_files()) == 1
    replacement = IPhoneController(first.data_directory, config=first.config_override,
                                   transport=httpx.MockTransport(fake))
    assert replacement.clear_abandoned_lease_after_bridge_reset() is True
    assert not marker_files()
    try:
        await replacement.session('new-owner', 'connect')
        assert replacement.session_id == 'session-1'
    finally:
        replacement.clear_session()


async def test_bridge_reset_never_clears_marker_of_another_live_controller(phone):
    first, fake = await connected(phone)
    first.release_lease()  # Marker PID still belongs to this live test process.
    replacement = IPhoneController(first.data_directory, config=first.config_override,
                                   transport=httpx.MockTransport(fake))
    assert replacement.clear_abandoned_lease_after_bridge_reset() is False
    assert len(marker_files()) == 1


async def test_owned_bridge_reset_can_clear_own_unknown_connection(phone):
    controller, _ = phone

    def lost(request):
        raise httpx.ReadTimeout('reply lost', request=request)

    controller.transport = httpx.MockTransport(lost)
    with pytest.raises(PhoneBridgeError):
        await controller.session('task', 'connect')
    assert controller.uncertain and controller.session_id is None
    assert controller.clear_abandoned_lease_after_bridge_reset() is True
    assert not marker_files()
    controller.clear_session()
    assert controller.owner is None and controller.lease is None

from __future__ import annotations

import asyncio
import base64
import json
import shutil

import httpx
import pytest

from vico_iphone import iphone_control as control
from vico_iphone.direct_wda import DirectWDAController, DirectWDARuntime
from vico_iphone.iphone_control import PhoneAction, PhoneBridgeError, PhoneConfig, PhoneError


SOURCE = ('<AppiumAUT><XCUIElementTypeApplication>'
          '<XCUIElementTypeTextField name="search" visible="true" enabled="true" '
          'x="1" y="2" width="100" height="30" />'
          '</XCUIElementTypeApplication></AppiumAUT>')
PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR'
       + (390).to_bytes(4, 'big') + (844).to_bytes(4, 'big'))


class FakeRuntime:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.running = False
        self.wda_started = True
        self.stopped = True
        self.failure = None
        self.last_stop = None
        self.before_start = lambda: None

    async def start(self, config):
        self.before_start()
        self.starts += 1
        self.running = True
        if self.failure:
            raise self.failure
        return {'ready': True, 'endpoint': f'http://127.0.0.1:{config.port}',
                'wda_started': self.wda_started}

    async def stop(self):
        self.stops += 1
        if not self.running and self.last_stop:
            return self.last_stop
        was_running = self.running
        self.running = False
        self.last_stop = {'owned': was_running, 'stopped': self.stopped,
                          'wda_started': self.wda_started and was_running, 'forced': False}
        return self.last_stop

    def status(self):
        return {'running': self.running, 'ready': self.running,
                'owned': self.running, 'wda_started': self.wda_started and self.running}


class FakeWDA:
    def __init__(self):
        self.calls = []
        self.urls = []
        self.failure_path = None
        self.failure = None
        self.bundle = 'com.apple.springboard'
        self.screen = {'width': 390, 'height': 844}
        self.locked = False
        self.elements = [{'element-6066-11e4-a52e-4f735466cecf': 'element-1'}]

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        self.urls.append(request.url)
        if self.failure_path and path.endswith(self.failure_path):
            if isinstance(self.failure, Exception) or isinstance(self.failure, asyncio.CancelledError):
                raise self.failure
            return self.failure
        value = None
        if path == '/session':
            value = {'sessionId': 'wda-session-1', 'capabilities': {}}
        elif path.endswith('/source'):
            value = SOURCE
        elif path.endswith('/screenshot'):
            value = base64.b64encode(PNG).decode()
        elif path.endswith('/wda/screen'):
            value = {'screenSize': self.screen, 'scale': 3}
        elif path.endswith('/window/size'):
            value = self.screen
        elif path.endswith('/wda/activeAppInfo'):
            value = {'bundleId': self.bundle}
        elif path.endswith('/wda/locked'):
            value = self.locked
        elif path.endswith('/elements'):
            value = self.elements
        elif path.endswith('/wda/apps/activate'):
            self.bundle = body['bundleId']
        return httpx.Response(200, json={'value': value})


@pytest.fixture(autouse=True)
def prerequisites(monkeypatch, tmp_path):
    monkeypatch.setattr(control, 'DEVICE_LEASE_DIRECTORY', tmp_path / 'leases')
    async def discovery(udid):
        return 'http://[fd00::1]:8100'
    async def tunnel(udid):
        return {'ready': True}
    monkeypatch.setattr(control, 'discover_coredevice_wda_url', discovery)
    monkeypatch.setattr(control, 'require_remotexpc_tunnel', tunnel)


@pytest.fixture
async def phone(tmp_path):
    runtime, fake = FakeRuntime(), FakeWDA()
    config = PhoneConfig(enabled=True, udid='test-device', team_id='TEST',
                         platform_version='26.1', port=4738,
                         wda_transport='coredevice', use_preinstalled_wda=True)
    instance = DirectWDAController(tmp_path, config=config, runtime=runtime,
                                    transport=httpx.MockTransport(fake))
    yield instance, fake, runtime
    await instance._close_client()
    instance.release_lease()


async def test_status_never_starts_helper_or_contacts_phone(phone):
    controller, fake, runtime = phone
    result = await controller.session('task', 'status')
    assert result['backend'] == 'direct-wda'
    assert result['bridge']['state'] == 'stopped'
    assert not fake.calls and runtime.starts == 0


async def test_connect_leases_before_helper_and_never_launches_target_app(phone):
    controller, fake, runtime = phone
    def check_lease():
        assert controller.lease is not None and controller.owner == 'task'
        assert control._read_device_marker(control._device_marker_path('test-device'))['phase'] == 'connecting'
    runtime.before_start = check_lease
    await controller.session('task', 'connect', 'com.example.ignored')
    assert fake.calls == [('POST', '/session', {'capabilities': {
        'alwaysMatch': {}, 'firstMatch': [{
            'arguments': [], 'environment': {}, 'eventloopIdleDelaySec': 0,
            'shouldWaitForQuiescence': True, 'maxTypingFrequency': 60,
            'shouldUseSingletonTestManager': True, 'shouldTerminateApp': False,
            'forceAppLaunch': False, 'useNativeCachingStrategy': True,
            'forceSimulatorSoftwareKeyboardPresence': True,
        }]}})]
    assert runtime.starts == 1
    assert controller.session_id == 'wda-session-1'
    assert not controller.uncertain


async def test_app_scope_retained_without_wda_bundle_launch(phone):
    controller, fake, _ = phone
    controller.config_override = controller.config_override.model_copy(update={
        'control_scope': 'apps', 'allowed_bundles': ['com.example.allowed']})
    await controller.session('task', 'connect', 'com.example.allowed')
    assert controller.bundle == 'com.example.allowed'
    assert 'bundleId' not in str(fake.calls[0][2])
    with pytest.raises(PhoneError, match='当前会话绑定'):
        await controller.launch('task', 'com.example.other')


async def test_raw_wda_observation_uses_persistent_client_and_optional_screenshot(phone):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    client = controller.client
    observed, png = await controller.observe('task', capture_image=False)
    assert observed['elements'] and not png
    assert controller.client is client
    assert not any('/execute/' in path or path.endswith('/screenshot') for _, path, _ in fake.calls)
    observed, png = await controller.observe('task', capture_image=True)
    assert png == PNG and observed['width'] == 390
    assert controller.client is client
    assert sum(path.endswith('/wda/screen') for _, path, _ in fake.calls) == 1
    source_urls = [url for url in fake.urls if url.path.endswith('/source')]
    assert len(source_urls) == 2
    assert all(dict(url.params) == {'format': 'xml', 'scope': 'AppiumAUT'} for url in source_urls)


async def test_screen_info_cache_returns_independent_copies(phone):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    original = await controller.mobile('deviceScreenInfo')
    original['screenSize']['width'] = 99
    cached = await controller.mobile('deviceScreenInfo')
    assert cached['screenSize'] == {'width': 390, 'height': 844}
    cached['screenSize']['height'] = 77
    assert (await controller.mobile('deviceScreenInfo'))['screenSize']['height'] == 844
    assert sum(path.endswith('/wda/screen') for _, path, _ in fake.calls) == 1


@pytest.mark.parametrize('failure', [
    httpx.ReadTimeout('screen response lost'),
    httpx.Response(200, json={'value': {'screenSize': {'width': 'bad', 'height': 844}}}),
])
async def test_screen_info_failures_and_invalid_geometry_are_not_cached(phone, failure):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    fake.failure_path, fake.failure = '/wda/screen', failure
    with pytest.raises(PhoneError):
        await controller.window_rect()
    assert controller._screen_info_cache is None
    fake.failure_path = None
    assert (await controller.window_rect())['width'] == 390
    assert sum(path.endswith('/wda/screen') for _, path, _ in fake.calls) == 2


async def test_screen_cache_is_cleared_even_when_disconnect_cleanup_fails(phone):
    controller, _, runtime = phone
    await controller.session('task', 'connect')
    await controller.mobile('deviceScreenInfo')
    runtime.stopped = False
    assert (await controller.session('task', 'disconnect'))['needs_attention']
    assert controller._screen_info_cache is None


async def test_screen_cache_does_not_cross_disconnect_clear_session_or_new_connect(phone):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    await controller.mobile('deviceScreenInfo')
    await controller.session('task', 'disconnect')
    assert controller._screen_info_cache is None
    fake.screen = {'width': 844, 'height': 390}
    await controller.session('task', 'connect')
    assert (await controller.window_rect())['width'] == 844
    controller.clear_session()
    assert controller._screen_info_cache is None
    # A new connection must not reuse cached data even if WDA reuses its ID.
    controller._screen_info_cache = ('wda-session-1', {'screenSize': {'width': 1, 'height': 1}})
    await controller.session('task', 'connect')
    assert controller._screen_info_cache is None
    assert (await controller.window_rect())['width'] == 844
    assert sum(path.endswith('/wda/screen') for _, path, _ in fake.calls) == 3


async def test_screen_cache_matches_appium_session_rotation_limitation(phone):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    assert (await controller.window_rect())['width'] == 390
    fake.screen = {'width': 844, 'height': 390}
    assert (await controller.window_rect())['width'] == 390
    assert sum(path.endswith('/wda/screen') for _, path, _ in fake.calls) == 1


@pytest.mark.parametrize('action,kwargs,suffix', [
    ('tap', {'x': 0.5, 'y': 0.25}, '/wda/tap'),
    ('swipe', {'direction': 'up'}, '/wda/swipe'),
    ('home', {}, '/wda/pressButton'),
])
async def test_actions_map_to_native_routes_once_and_consume_observation(phone, action, kwargs, suffix):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    observed, _ = await controller.observe('task', capture_image=False)
    args = PhoneAction(action=action, observation_id=observed['observation_id'], **kwargs)
    result = await controller.act('task', args)
    assert result['action_dispatched'] and not result['verified']
    actions = [call for call in fake.calls if call[1].endswith(suffix)]
    assert len(actions) == 1
    if action == 'tap':
        assert actions[0][2] == {'x': 194.5, 'y': 210.75}
    with pytest.raises(PhoneError, match='观察已过期'):
        await controller.act('task', args)
    assert len([call for call in fake.calls if call[1].endswith(suffix)]) == 1


@pytest.mark.parametrize('text', ['hello', '你好😀\n下一行'])
async def test_component_xpath_and_text_match_appium_native_wda_payload(phone, text):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    observed, _ = await controller.observe('task', capture_image=False)
    await controller.act('task', PhoneAction(action='type', observation_id=observed['observation_id'],
                                             element_ref=observed['elements'][0]['ref'], text=text))
    query = next(body for _, path, body in fake.calls if path.endswith('/elements'))
    assert query['using'] == 'xpath' and 'search' in query['value']
    assert fake.calls[-1] == ('POST', '/session/wda-session-1/element/element-1/value', {'value': list(text)})
    if '😀' in text:
        assert fake.calls[-1][2]['value'][2] == '😀'
        assert len(fake.calls[-1][2]['value']) == 7


async def test_explicit_launch_uses_native_activate_and_invalidates_observation(phone):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    await controller.observe('task', capture_image=False)
    result = await controller.launch('task', 'com.apple.mobilesafari')
    assert result['requires_observation'] and controller.observation is None
    assert fake.calls[-1][1:] == ('/session/wda-session-1/wda/apps/activate', {'bundleId': 'com.apple.mobilesafari'})


async def test_lost_mutation_reply_is_unknown_and_never_replayed(phone):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    observed, _ = await controller.observe('task', capture_image=False)
    fake.failure_path, fake.failure = '/wda/tap', httpx.ReadTimeout('lost reply containing private text')
    args = PhoneAction(action='tap', observation_id=observed['observation_id'], x=0.5, y=0.5)
    with pytest.raises(PhoneBridgeError) as caught:
        await controller.act('task', args)
    assert caught.value.request_outcome == 'unknown'
    assert 'private' not in str(caught.value)
    with pytest.raises(PhoneError, match='观察已过期'):
        await controller.act('task', args)
    assert sum(path.endswith('/wda/tap') for _, path, _ in fake.calls) == 1


@pytest.mark.parametrize('failure,outcome', [
    (httpx.ConnectError('refused'), 'not_sent'),
    (httpx.ReadTimeout('reply lost'), 'unknown'),
    (httpx.Response(500, json={'value': {'error': 'session not created'}}), 'rejected'),
    (httpx.Response(200, content=b'{bad'), 'unknown'),
])
async def test_create_failure_preserves_only_uncertain_ownership(phone, failure, outcome):
    controller, fake, runtime = phone
    fake.failure_path, fake.failure = '/session', failure
    with pytest.raises(PhoneBridgeError) as caught:
        await controller.session('task', 'connect')
    assert caught.value.request_outcome == outcome
    assert controller.uncertain is (outcome == 'unknown')
    assert (controller.lease is not None) is (outcome == 'unknown')
    assert len(fake.calls) == 1
    if outcome == 'unknown':
        with pytest.raises(PhoneError, match='已有会话'):
            await controller.session('task', 'connect')
        assert runtime.starts == 1
    else:
        assert runtime.stops == 1


@pytest.mark.parametrize('outcome', ['not_sent', 'unknown'])
async def test_helper_failure_does_not_send_session_and_classifies_ownership(phone, outcome):
    controller, fake, runtime = phone
    runtime.failure = PhoneBridgeError('helper failed', code='test_failure', request_outcome=outcome)
    with pytest.raises(PhoneBridgeError):
        await controller.session('task', 'connect')
    assert not fake.calls
    assert controller.uncertain is (outcome == 'unknown')


async def test_cancelled_http_creation_keeps_lease_and_no_automatic_retry(phone):
    controller, fake, runtime = phone
    fake.failure_path, fake.failure = '/session', asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await controller.session('task', 'connect')
    assert controller.uncertain and controller.lease is not None
    assert controller.last_diagnostic['request_outcome'] == 'unknown'
    assert runtime.stops == 0 and len(fake.calls) == 1


async def test_session_expiry_releases_lease_without_replaying_read(phone):
    controller, fake, runtime = phone
    await controller.session('task', 'connect')
    fake.failure_path = '/wda/locked'
    fake.failure = httpx.Response(404, json={'value': {'error': 'invalid session id', 'message': 'private'}})
    with pytest.raises(PhoneBridgeError) as caught:
        await controller.observe('task', capture_image=False)
    assert caught.value.request_outcome == 'rejected'
    assert controller.session_id is None and controller.lease is None
    assert sum(path.endswith('/wda/locked') for _, path, _ in fake.calls) == 1
    await controller.finish()
    assert runtime.stops == 2 and controller.client is None


async def test_disconnect_deletes_before_stopping_helper_and_releases_client(phone):
    controller, fake, runtime = phone
    await controller.session('task', 'connect')
    result = await controller.session('task', 'disconnect')
    assert result['connected'] is False
    assert fake.calls[-1] == ('DELETE', '/session/wda-session-1', None)
    assert runtime.stops == 1 and controller.client is None and controller.lease is None


@pytest.mark.parametrize('wda_started,stopped,needs_attention', [
    (True, True, False), (False, True, True), (True, False, True),
])
async def test_explicit_stop_only_releases_unknown_session_if_own_wda_confirmed_stopped(phone, wda_started, stopped, needs_attention):
    controller, fake, runtime = phone
    runtime.wda_started, runtime.stopped = wda_started, stopped
    fake.failure_path, fake.failure = '/session', httpx.ReadTimeout('unknown create')
    with pytest.raises(PhoneBridgeError):
        await controller.session('task', 'connect')
    result = await controller.stop_runtime()
    assert result['needs_attention'] is needs_attention
    assert (controller.lease is not None) is needs_attention
    assert controller.uncertain is needs_attention
    assert len(fake.calls) == 1


async def test_existing_device_owner_blocks_helper_start(phone, tmp_path):
    controller, _, _ = phone
    await controller.session('task', 'connect')
    runtime = FakeRuntime()
    other = DirectWDAController(tmp_path / 'other', config=controller.config_override, runtime=runtime)
    with pytest.raises(PhoneError, match='另一个'):
        await other.session('other-task', 'connect')
    assert runtime.starts == 0


async def test_appium_config_is_rejected_before_helper_or_phone(phone):
    controller, fake, runtime = phone
    controller.config_override = PhoneConfig(enabled=True, udid='test-device', team_id='TEST')
    with pytest.raises(PhoneError, match='coredevice'):
        await controller.session('task', 'connect')
    assert not fake.calls and runtime.starts == 0


async def test_window_rect_compatibility_maps_to_size(phone):
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    rect = await controller.request('GET', '/session/wda-session-1/window/rect')
    assert rect == {'x': 0, 'y': 0, 'width': 390, 'height': 844}
    assert fake.calls[-1][1].endswith('/window/size')


async def test_response_size_is_bounded_without_logging_content(phone, monkeypatch):
    from vico_iphone import direct_wda
    controller, fake, _ = phone
    await controller.session('task', 'connect')
    monkeypatch.setattr(direct_wda, 'MAX_RESPONSE_BYTES', 8)
    with pytest.raises(PhoneBridgeError, match='大小上限'):
        await controller.observe('task', capture_image=False)
    assert controller.last_diagnostic['request_outcome'] == 'unknown'


async def test_stop_without_owned_process_cannot_prove_remote_wda_terminated(tmp_path):
    runtime = DirectWDARuntime(tmp_path)
    assert await runtime.stop() == {'owned': False, 'stopped': True, 'wda_started': False, 'forced': False}


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed for harmless helper lifecycle test')
async def test_helper_real_process_is_started_once_stops_on_eof_and_logs_privately(tmp_path):
    runner = tmp_path / 'runner.mjs'
    runner.write_text('process.stdout.write(JSON.stringify({ready:true,endpoint:"http://127.0.0.1:4738",wda_started:true})+"\\n");\n'
                      'process.stdin.resume(); process.stdin.on("end",()=>process.exit(0));\n')
    runtime = DirectWDARuntime(tmp_path / 'state', runner_path=runner)
    config = PhoneConfig(port=4738)
    try:
        assert (await runtime.start(config))['ready']
        assert runtime.status()['running']
        with pytest.raises(PhoneBridgeError, match='状态不明'):
            await runtime.start(config)
    finally:
        result = await runtime.stop()
    assert result == {'owned': True, 'stopped': True, 'wda_started': True, 'forced': False}
    assert not runtime.status()['running']
    assert (tmp_path / 'state/runtime/direct-wda.stderr.log').stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed for harmless helper lifecycle test')
@pytest.mark.parametrize('payload,outcome', [
    ({'ready': False, 'error_code': 'wireless_not_ready', 'request_outcome': 'not_sent', 'cleanup_verified': True}, 'not_sent'),
    ({'ready': False, 'error_code': 'launch_unknown', 'request_outcome': 'unknown'}, 'unknown'),
    ({'ready': True, 'endpoint': 'http://192.168.1.12:4738', 'wda_started': True}, 'unknown'),
])
async def test_helper_rejects_untrusted_ready_endpoint_and_honors_unknown_launch(tmp_path, payload, outcome):
    runner = tmp_path / 'runner.mjs'
    exit_code = 1 if payload.get('ready') is False else 0
    runner.write_text('process.stdout.write(' + json.dumps(json.dumps(payload) + '\n') + ');\n'
                      f'process.stdin.resume(); process.stdin.on("end",()=>process.exit({exit_code}));\n')
    runtime = DirectWDARuntime(tmp_path / 'state', runner_path=runner)
    with pytest.raises(PhoneBridgeError) as caught:
        await runtime.start(PhoneConfig(port=4738))
    assert caught.value.request_outcome == outcome
    assert not runtime.status()['running']


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed for harmless helper lifecycle test')
@pytest.mark.parametrize('cleanup_verified,exit_code', [(False, 1), (True, 2), (False, 0)])
async def test_not_sent_failure_with_unverified_cleanup_never_releases_device(phone, tmp_path, cleanup_verified, exit_code):
    controller, fake, _ = phone
    payload = {'ready': False, 'error_code': 'startup_failed', 'request_outcome': 'not_sent',
               'cleanup_verified': cleanup_verified, 'wda_started': True}
    runner = tmp_path / 'failed-runner.mjs'
    runner.write_text('process.stdout.write(' + json.dumps(json.dumps(payload) + '\n') + ');\n'
                      f'process.stdin.resume(); process.stdin.on("end",()=>process.exit({exit_code}));\n')
    controller.runtime = DirectWDARuntime(tmp_path / 'runtime-state', runner_path=runner)
    with pytest.raises(PhoneBridgeError) as caught:
        await controller.session('task', 'connect')
    assert caught.value.request_outcome == 'unknown'
    assert controller.uncertain and controller.lease is not None and not fake.calls
    assert (await controller.finish())['needs_attention']
    assert (await controller.finish())['needs_attention']
    assert controller.lease is not None
    assert controller.runtime_status()['state'] == 'unknown'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed for harmless helper lifecycle test')
async def test_post_cleanup_start_failure_retains_proof_for_explicit_finish(phone, tmp_path):
    controller, fake, _ = phone
    payload = {'ready': False, 'error_code': 'startup_failed', 'request_outcome': 'unknown',
               'cleanup_verified': True, 'wda_started': True}
    runner = tmp_path / 'failed-runner.mjs'
    runner.write_text('process.stdout.write(' + json.dumps(json.dumps(payload) + '\n') + ');\n'
                      'process.stdin.resume(); process.stdin.on("end",()=>process.exit(1));\n')
    controller.runtime = DirectWDARuntime(tmp_path / 'runtime-state', runner_path=runner)
    with pytest.raises(PhoneBridgeError):
        await controller.session('task', 'connect')
    assert controller.uncertain and controller.lease is not None
    assert not (await controller.finish())['needs_attention']
    assert controller.lease is None and controller.owner is None and not fake.calls


@pytest.mark.parametrize('failure', [
    httpx.ConnectError('refused'),
    httpx.Response(500, json={'value': {'error': 'session not created'}}),
])
async def test_http_create_not_sent_or_rejected_upgraded_when_runtime_cleanup_fails(phone, failure):
    controller, fake, runtime = phone
    runtime.stopped = False
    fake.failure_path, fake.failure = '/session', failure
    with pytest.raises(PhoneBridgeError) as caught:
        await controller.session('task', 'connect')
    assert caught.value.request_outcome == 'unknown'
    assert controller.uncertain and controller.lease is not None
    assert (await controller.finish())['needs_attention']
    assert len(fake.calls) == 1


async def test_confirmed_disconnect_with_cleanup_failure_keeps_lease_and_attention(phone):
    controller, fake, runtime = phone
    await controller.session('task', 'connect')
    runtime.stopped = False
    result = await controller.session('task', 'disconnect')
    assert result['connected'] is False and result['needs_attention']
    assert controller.session_id is None and controller.uncertain and controller.lease is not None
    assert (await controller.finish())['needs_attention']
    assert sum(method == 'DELETE' for method, _, _ in fake.calls) == 1


async def test_expired_session_with_cleanup_failure_preserves_ownership(phone):
    controller, fake, runtime = phone
    await controller.session('task', 'connect')
    runtime.stopped = False
    fake.failure_path = '/wda/locked'
    fake.failure = httpx.Response(404, json={'value': {'error': 'invalid session id'}})
    with pytest.raises(PhoneBridgeError) as caught:
        await controller.observe('task', capture_image=False)
    assert caught.value.request_outcome == 'unknown'
    assert controller.session_id is None and controller.uncertain and controller.lease is not None
    assert (await controller.finish())['needs_attention']


async def test_finish_after_unknown_create_never_stops_foreign_wda_or_sends_delete(phone):
    controller, fake, runtime = phone
    runtime.wda_started = False
    fake.failure_path, fake.failure = '/session', httpx.ReadTimeout('lost create response')
    with pytest.raises(PhoneBridgeError):
        await controller.session('task', 'connect')
    assert (await controller.finish())['needs_attention']
    assert (await controller.finish())['needs_attention']
    assert controller.lease is not None
    assert len(fake.calls) == 1 and fake.calls[0][0] == 'POST'

"""Backend selection must preserve the legacy profile and never start a phone."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vico_iphone.direct_wda import DirectWDAController
from vico_iphone.iphone_control import IPhoneController, PhoneConfig
from vico_iphone.server import PhoneService
from vico_iphone.state import control_backend, default_port, state_home, write_json


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VICO_IPHONE_HOME", raising=False)
    monkeypatch.delenv("IPHONE_USE_BACKEND", raising=False)


def test_default_profile_does_not_read_or_modify_existing_appium_profile(tmp_path):
    old_home = tmp_path / ".local/share/vico-iphone"
    old_config = old_home / "configuration/iphone-control.json"
    write_json(old_config, PhoneConfig(enabled=True, udid="test-device", team_id="TEST").model_dump())
    before = old_config.read_bytes()
    service = PhoneService()
    assert service.backend == "direct-wda" and type(service.controller) is DirectWDAController
    assert service.home == tmp_path / ".local/share/iphone-use-wireless-direct"
    assert service.controller.load_config().enabled is False
    assert service.controller.load_config().port == 18401
    assert old_config.read_bytes() == before and not service.home.exists()
    assert service.controller.runtime.process is None


def test_explicit_legacy_backend_uses_original_profile_even_when_env_is_direct(monkeypatch, tmp_path):
    monkeypatch.setenv("IPHONE_USE_BACKEND", "direct-wda")
    service = PhoneService(backend="appium")
    assert type(service.controller) is IPhoneController
    assert service.home == tmp_path / ".local/share/vico-iphone"
    assert service.controller.load_config().port == 4737


@pytest.mark.parametrize('backend', ['direct-wda', 'appium'])
def test_explicit_state_home_wins_for_both_backends(monkeypatch, tmp_path, backend):
    custom = tmp_path / "custom"
    monkeypatch.setenv("VICO_IPHONE_HOME", str(custom))
    monkeypatch.setenv("IPHONE_USE_BACKEND", backend)
    assert state_home() == custom
    assert PhoneService().home == custom
    assert control_backend() == backend
    assert default_port() == (18401 if backend == 'direct-wda' else 4737)


def test_invalid_backend_is_rejected_even_with_an_explicit_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("VICO_IPHONE_HOME", str(tmp_path))
    monkeypatch.setenv("IPHONE_USE_BACKEND", "typo")
    with pytest.raises(ValueError, match="no automatic fallback"):
        state_home()
    with pytest.raises(ValueError, match="no automatic fallback"):
        PhoneService()


@pytest.mark.parametrize('script,backend,expected', [
    ('run-server.sh', None, 'direct-wda'),
    ('run-server.sh', 'appium', 'appium'),
    ('run-direct-server.sh', 'appium', 'direct-wda'),
    ('run-appium-server.sh', 'direct-wda', 'appium'),
])
@pytest.mark.parametrize('custom_home', [False, True])
def test_launchers_select_explicit_backend_and_isolated_home(tmp_path, script, backend, expected, custom_home):
    commands = tmp_path / 'bin'
    commands.mkdir()
    uv = commands / 'uv'
    uv.write_text('#!/bin/bash\nprintf "%s\\n" "$IPHONE_USE_BACKEND" "$VICO_IPHONE_HOME" "$*"\n')
    uv.chmod(0o700)
    env = {'HOME': str(tmp_path), 'PATH': f'{commands}:/usr/bin:/bin'}
    if backend:
        env['IPHONE_USE_BACKEND'] = backend
    if custom_home:
        env['VICO_IPHONE_HOME'] = str(tmp_path / 'custom')
    path = Path(__file__).resolve().parents[1] / 'scripts' / script
    result = subprocess.run(['bash', str(path), '--sentinel'], env=env, cwd=tmp_path,
                            text=True, capture_output=True, timeout=5, check=True)
    observed_backend, observed_home, args = result.stdout.splitlines()
    assert observed_backend == expected
    expected_home = (tmp_path / 'custom' if custom_home else tmp_path / '.local/share' /
                     ('iphone-use-wireless-direct' if expected == 'direct-wda' else 'vico-iphone'))
    assert observed_home == str(expected_home)
    assert args.startswith('run --frozen --project ') and args.endswith('iphone-use-mcp --sentinel')


@pytest.mark.parametrize('backend', ['typo', ''])
def test_launcher_invalid_backend_never_runs_uv(tmp_path, backend):
    env = {'HOME': str(tmp_path), 'PATH': '/usr/bin:/bin', 'IPHONE_USE_BACKEND': backend}
    path = Path(__file__).resolve().parents[1] / 'scripts/run-server.sh'
    result = subprocess.run(['bash', str(path)], env=env, cwd=tmp_path, text=True,
                            capture_output=True, timeout=5)
    assert result.returncode == 2 and result.stdout == ''
    assert 'no automatic fallback' in result.stderr

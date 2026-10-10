from __future__ import annotations

import json
import sys

import pytest

from vico_iphone import cli


@pytest.fixture(autouse=True)
def clear_backend(monkeypatch):
    monkeypatch.delenv("IPHONE_USE_BACKEND", raising=False)


def configure(monkeypatch, home, *extra):
    monkeypatch.setenv("VICO_IPHONE_HOME", str(home))
    monkeypatch.setattr(sys, "argv", [
        "iphone-use", "configure", "--udid", "test-device",
        "--team-id", "TESTTEAM01", "--platform-version", "26.0",
        "--wda-bundle-id", "org.example.phone.WebDriverAgentRunner", *extra,
    ])
    cli.main()


def test_wireless_preinstalled_configuration_is_saved(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path, "--preinstalled-wda")
    saved = json.loads((tmp_path / "configuration/iphone-control.json").read_text())
    assert saved["wda_transport"] == "coredevice"
    assert saved["use_preinstalled_wda"] is True
    assert saved["prebuilt_wda_path"] == ""
    assert saved["wda_bundle_id"] == "org.example.phone.WebDriverAgentRunner"
    assert json.loads(capsys.readouterr().out)["phone_verified"] is False
    assert cli.doctor()["use_preinstalled_wda"] is True


def test_prebuilt_path_requires_preinstalled_mode_and_does_not_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IPHONE_USE_BACKEND", "appium")
    with pytest.raises(SystemExit) as error:
        configure(monkeypatch, tmp_path, "--prebuilt-wda-path", str(tmp_path / "Runner.app"))
    assert error.value.code == 1
    assert not (tmp_path / "configuration/iphone-control.json").exists()
    assert json.loads(capsys.readouterr().err)["ok"] is False


def test_configuration_does_not_replace_an_active_session(monkeypatch, tmp_path, capsys):
    (tmp_path / "session.json").write_text('{"session_id": "existing"}')
    with pytest.raises(SystemExit) as error:
        configure(monkeypatch, tmp_path, "--preinstalled-wda")
    assert error.value.code == 1
    assert not (tmp_path / "configuration/iphone-control.json").exists()
    assert "Disconnect" in json.loads(capsys.readouterr().err)["error"]


def test_default_configure_selects_direct_port_and_preinstalled_wda(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path)
    saved = json.loads((tmp_path / "configuration/iphone-control.json").read_text())
    result = json.loads(capsys.readouterr().out)
    assert result["backend"] == "direct-wda" and saved["port"] == 18401
    assert saved["wda_transport"] == "coredevice" and saved["use_preinstalled_wda"] is True
    report = cli.doctor()
    assert report["configured"] and report["backend"] == "direct-wda"
    assert report["appium_server_required"] is False and report["default_port"] == 18401


def test_explicit_appium_configure_preserves_legacy_defaults(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IPHONE_USE_BACKEND", "appium")
    configure(monkeypatch, tmp_path)
    saved = json.loads((tmp_path / "configuration/iphone-control.json").read_text())
    assert saved["port"] == 4737 and saved["use_preinstalled_wda"] is False
    assert json.loads(capsys.readouterr().out)["backend"] == "appium"
    assert cli.doctor()["appium_server_required"] is True


@pytest.mark.parametrize('extra', [
    ('--no-preinstalled-wda',), ('--transport', 'default'),
    ('--prebuilt-wda-path', '/example/Signed.app'),
])
def test_direct_configure_rejects_incompatible_modes_without_writing(monkeypatch, tmp_path, extra, capsys):
    with pytest.raises(SystemExit) as caught:
        configure(monkeypatch, tmp_path, *extra)
    assert caught.value.code == 1
    assert not (tmp_path / "configuration/iphone-control.json").exists()
    assert json.loads(capsys.readouterr().err)["ok"] is False


def test_direct_doctor_rejects_legacy_config_without_changing_it(monkeypatch, tmp_path):
    from vico_iphone.iphone_control import PhoneConfig
    from vico_iphone.state import write_json
    path = tmp_path / "configuration/iphone-control.json"
    write_json(path, PhoneConfig(enabled=True, udid="test-device", team_id="TEAM").model_dump())
    before = path.read_bytes()
    monkeypatch.setenv("VICO_IPHONE_HOME", str(tmp_path))
    result = cli.doctor()
    assert result["configured"] is False and result["errors"]
    assert path.read_bytes() == before


def test_unknown_backend_reports_error_without_fallback(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IPHONE_USE_BACKEND", "typo")
    with pytest.raises(SystemExit) as caught:
        configure(monkeypatch, tmp_path)
    assert caught.value.code == 1
    assert "no automatic fallback" in json.loads(capsys.readouterr().err)["error"]
    assert not (tmp_path / "configuration/iphone-control.json").exists()

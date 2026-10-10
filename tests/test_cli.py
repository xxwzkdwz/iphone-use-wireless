from __future__ import annotations

import json
import sys

import pytest

from vico_iphone import cli


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

"""Offline WDA preparation tests: no real codesign, identities, or phone access."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location(
    "prepare_wda_script", Path(__file__).parents[1] / "scripts" / "prepare-wda.py"
)
assert SPEC is not None and SPEC.loader is not None
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)
IDENTITY = "Apple Development: Test Fixture"


@pytest.fixture
def source(tmp_path):
    app = tmp_path / "original" / prepare.WDA_APP_NAME
    contents = {
        "Info.plist": "fixture-info",
        "PlugIns/WebDriverAgentRunner.xctest/runner": "original-runner",
        "Frameworks/XCTest.framework/XCTest": "remove-test-framework",
        "Frameworks/XCUIAutomation.framework/XCUIAutomation": "remove-ui-framework",
        "Frameworks/Testing.framework/Testing": "keep-testing",
        "Frameworks/Testing.framework/Frameworks/XCKeep.framework/XCKeep": "keep-nested",
        "Frameworks/libXCTestSwiftSupport.dylib": "keep-support",
    }
    for name, value in contents.items():
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
    return app


class Codesign:
    def __init__(self, fail_at=None, exception=None):
        self.calls = []
        self.fail_at = fail_at
        self.exception = exception

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if len(self.calls) == self.fail_at:
            if self.exception is not None:
                raise self.exception
            return subprocess.CompletedProcess(command, 1, "", "private-identity-diagnostic")
        return subprocess.CompletedProcess(command, 0, "", "")


def snapshot(path):
    return {str(file.relative_to(path)): file.read_bytes() for file in path.rglob("*") if file.is_file()}


def test_prepare_copies_removes_only_direct_xc_and_signs_inside_out(source, tmp_path):
    before = snapshot(source)
    run = Codesign()
    result = prepare.prepare_wda(source, IDENTITY, tmp_path / "output", runner=run)
    output = Path(result["prepared_path"])
    assert result == {
        "ok": True,
        "prepared_path": str(output),
        "removed_frameworks": ["XCTest.framework", "XCUIAutomation.framework"],
        "signature_verified": True,
        "phone_verified": False,
        "configuration_changed": False,
    }
    assert output.parent.parent == tmp_path / "output"
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    assert snapshot(source) == before
    assert not (output / "Frameworks/XCTest.framework").exists()
    assert not (output / "Frameworks/XCUIAutomation.framework").exists()
    assert (output / "Frameworks/Testing.framework/Testing").read_text() == "keep-testing"
    assert (output / "Frameworks/Testing.framework/Frameworks/XCKeep.framework/XCKeep").read_text() == "keep-nested"
    assert (output / "Frameworks/libXCTestSwiftSupport.dylib").read_text() == "keep-support"
    verify = [prepare.CODESIGN, "--verify", "--deep", "--strict", "--verbose=2"]
    signing = [prepare.CODESIGN, "--force", "--sign", IDENTITY,
               "--preserve-metadata=identifier,entitlements"]
    assert [call for call, _ in run.calls] == [
        [*verify, str(source)],
        [*signing, str(output / "PlugIns/WebDriverAgentRunner.xctest")],
        [*signing, str(output)],
        [*verify, str(output)],
    ]
    assert all(options == {"check": False, "capture_output": True, "text": True,
                           "timeout": 120} for _, options in run.calls)


def test_each_run_is_fresh_and_configuration_is_untouched(source, tmp_path, monkeypatch):
    home = tmp_path / "private-state"
    config = home / "configuration/iphone-control.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"prebuilt_wda_path":"old-prepared-build"}')
    monkeypatch.setenv("VICO_IPHONE_HOME", str(home))
    first = prepare.prepare_wda(source, IDENTITY, runner=Codesign())
    second = prepare.prepare_wda(source, IDENTITY, runner=Codesign())
    first_path, second_path = Path(first["prepared_path"]), Path(second["prepared_path"])
    assert first_path != second_path
    assert first_path.parent.parent == second_path.parent.parent == home / "wda"
    assert first_path.is_dir() and second_path.is_dir()
    assert config.read_text() == '{"prebuilt_wda_path":"old-prepared-build"}'


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4])
def test_codesign_failures_preserve_source_and_remove_only_partial_run(source, tmp_path, fail_at):
    output = tmp_path / "output"
    prior = output / "previous" / "keep.txt"
    prior.parent.mkdir(parents=True)
    prior.write_text("existing-build")
    before = snapshot(source)
    run = Codesign(fail_at=fail_at)
    with pytest.raises(prepare.PrepareError) as error:
        prepare.prepare_wda(source, IDENTITY, output, runner=run)
    assert "private-identity-diagnostic" not in str(error.value)
    assert len(run.calls) == fail_at
    assert list(output.iterdir()) == [prior.parent]
    assert prior.read_text() == "existing-build"
    assert snapshot(source) == before


def test_source_verify_failure_creates_no_output(source, tmp_path):
    output = tmp_path / "not-created"
    with pytest.raises(prepare.PrepareError, match="Source signature verification failed"):
        prepare.prepare_wda(source, IDENTITY, output, runner=Codesign(fail_at=1))
    assert not output.exists()


@pytest.mark.parametrize("exception", [
    subprocess.TimeoutExpired("codesign", 120), OSError("codesign unavailable"), KeyboardInterrupt(),
])
def test_execution_error_or_interrupt_cleans_copy(source, tmp_path, exception):
    output = tmp_path / "output"
    expected = KeyboardInterrupt if isinstance(exception, KeyboardInterrupt) else prepare.PrepareError
    with pytest.raises(expected):
        prepare.prepare_wda(source, IDENTITY, output,
                            runner=Codesign(fail_at=2, exception=exception))
    assert list(output.iterdir()) == []
    assert (source / "Frameworks/XCTest.framework/XCTest").is_file()


def test_copy_failure_cleans_partial_copy(source, tmp_path, monkeypatch):
    def fail_copy(src, dest, **kwargs):
        dest.mkdir()
        (dest / "partial").write_text("partial")
        raise OSError("simulated copy failure")
    monkeypatch.setattr(prepare.shutil, "copytree", fail_copy)
    output = tmp_path / "output"
    with pytest.raises(OSError, match="simulated copy failure"):
        prepare.prepare_wda(source, IDENTITY, output, runner=Codesign())
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("identity", ["", " ", "-", "--sign", "<local identity>", "name\nvalue"])
def test_rejects_invalid_identity_without_subprocess(source, tmp_path, identity):
    run = Codesign()
    with pytest.raises(prepare.PrepareError, match="exact existing"):
        prepare.prepare_wda(source, identity, tmp_path / "output", runner=run)
    assert run.calls == []


@pytest.mark.parametrize("boundary", [
    "relative_source", "source_symlink", "wrong_app_name", "no_xctest", "plugins_symlink",
    "frameworks_symlink", "xctest_symlink", "output_inside_source", "output_equals_source",
    "output_symlink", "output_file", "relative_output", "external_link", "absolute_link",
    "broken_link", "special_file",
])
def test_rejects_unsafe_or_invalid_paths_before_codesign(source, tmp_path, boundary):
    output = tmp_path / "output"
    outside = tmp_path / "outside"
    outside.mkdir()
    if boundary == "relative_source":
        source = Path(prepare.WDA_APP_NAME)
    elif boundary == "source_symlink":
        link = tmp_path / prepare.WDA_APP_NAME
        link.symlink_to(source, target_is_directory=True)
        source = link
    elif boundary == "wrong_app_name":
        renamed = source.with_name("Other.app")
        source.rename(renamed)
        source = renamed
    elif boundary == "no_xctest":
        shutil.rmtree(source / "PlugIns/WebDriverAgentRunner.xctest")
    elif boundary in {"plugins_symlink", "frameworks_symlink", "xctest_symlink"}:
        target = source / {"plugins_symlink": "PlugIns", "frameworks_symlink": "Frameworks",
                           "xctest_symlink": "PlugIns/WebDriverAgentRunner.xctest"}[boundary]
        shutil.rmtree(target)
        target.symlink_to(outside, target_is_directory=True)
    elif boundary == "output_inside_source":
        output = source / "nested-output"
    elif boundary == "output_equals_source":
        output = source
    elif boundary == "output_symlink":
        output.symlink_to(outside, target_is_directory=True)
    elif boundary == "output_file":
        output.write_text("not-a-directory")
    elif boundary == "relative_output":
        output = Path("relative-output")
    elif boundary == "external_link":
        (source / "external").symlink_to("../../outside")
    elif boundary == "absolute_link":
        (source / "absolute").symlink_to(source / "Info.plist")
    elif boundary == "broken_link":
        (source / "broken").symlink_to("missing")
    elif boundary == "special_file":
        prepare.os.mkfifo(source / "special")
    run = Codesign()
    with pytest.raises(prepare.PrepareError):
        prepare.prepare_wda(source, IDENTITY, output, runner=run)
    assert run.calls == []
    assert list(outside.iterdir()) == []


def test_internal_relative_symlink_is_preserved_inside_copy(source, tmp_path):
    link = source / "Frameworks/Testing.framework/Current"
    link.symlink_to("Testing")
    result = prepare.prepare_wda(source, IDENTITY, tmp_path / "output", runner=Codesign())
    output = Path(result["prepared_path"])
    copied = output / "Frameworks/Testing.framework/Current"
    assert copied.is_symlink()
    assert copied.resolve().is_relative_to(output)
    assert copied.read_text() == "keep-testing"
    assert link.readlink() == Path("Testing")


def test_cli_success_json_and_error_json(source, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prepare.sys, "platform", "darwin")
    original = prepare.prepare_wda
    monkeypatch.setattr(prepare, "prepare_wda", lambda src, identity, out: original(src, identity, out, runner=Codesign()))
    args = ["--source", str(source), "--identity", IDENTITY, "--output-root", str(tmp_path / "output")]
    assert prepare.main(args) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert Path(result["prepared_path"]).is_dir()
    assert result["configuration_changed"] is False
    monkeypatch.setattr(prepare.sys, "platform", "linux")
    assert prepare.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["ok"] is False and "macOS" in error["error"]


def test_help_requires_no_identity_or_platform_and_explains_boundary(capsys):
    with pytest.raises(SystemExit) as exit_result:
        prepare.main(["--help"])
    assert exit_result.value.code == 0
    help_text = capsys.readouterr().out
    assert "--source" in help_text and "--identity" in help_text and "--output-root" in help_text
    assert "configuration remain unchanged" in help_text
    assert "certificate password" in help_text

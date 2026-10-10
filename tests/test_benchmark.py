import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


spec = importlib.util.spec_from_file_location("phone_benchmark", Path(__file__).parents[1] / "scripts/benchmark-backends.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def component_observation():
    return {"bundle_id": benchmark.TARGET_APP, "components_status": "available", "capture_image": False,
            "window_rect": {"x": 0, "y": 0, "width": 393, "height": 852}, "elements_total": 1,
            "elements_truncated": False, "elements": [{"ref": "e1", "type": "XCUIElementTypeSearchField",
                                                       "name": "private text", "x": "0", "enabled": "true", "visible": "true"}]}


def snapshot(values=None):
    defaults = {"waitForIdleTimeout": 10, "animationCoolOffTimeout": 2, "snapshotMaxDepth": 50, "screenshotQuality": 3}
    return {**benchmark.settings_snapshot(defaults if values is None else values), "all_scalar_values_fingerprint": "identical"}


def block(backend="appium", settings=None):
    return {"success": True, "backend": backend, "settings": {"before": settings or snapshot(), "after": settings or snapshot()},
            "configuration": {"device_fingerprint": "same"}, "shared_source_sha256": {"controller": "same"},
            "dependencies": {"httpx": "same"}, "wda": {"version": "same"}, "samples": []}


def test_sequence_balances_order_without_random_reassignment():
    assert benchmark.sequence(4) == ["appium", "direct-wda", "direct-wda", "appium"]
    assert benchmark.sequence(8) == benchmark.sequence(4) * 2
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.sequence(3)


def test_summary_uses_successes_for_latency_and_retains_failures():
    samples = [{"operation": "components", "success": success, "seconds": seconds}
               for success, seconds in ((True, 1), (True, 3), (False, 60))]
    result = benchmark.summarize(samples)["components"]
    assert result == {"attempts": 3, "successes": 2, "failures": 1, "median_seconds": 2, "p90_seconds": 2.8}


def test_private_json_never_creates_world_readable_reports(tmp_path):
    path = tmp_path / "report.json"
    benchmark.private_json(path, {"safe": True})
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {"safe": True}


def test_routes_strip_session_and_element_identifiers_and_do_not_record_body():
    assert benchmark.route_label("GET", "/session/private-session/element/private-element/value", None) == "GET /session/{session}/element/{element}/value"
    route = benchmark.route_label("POST", "/session/private/execute/sync", {"script": "mobile: isLocked", "args": [{"token": "secret"}]})
    assert route == "POST /session/{session}/execute/sync [mobile: isLocked]"
    assert "secret" not in route


def test_component_evidence_checks_contract_without_persisting_text():
    evidence = benchmark.observation_evidence(component_observation(), [], "components", b"key")
    assert evidence["element_count"] == 1
    assert "private text" not in json.dumps(evidence)
    wrong = component_observation()
    wrong["bundle_id"] = "org.other.app"
    with pytest.raises(benchmark.BenchmarkError, match="unexpected_foreground_app"):
        benchmark.observation_evidence(wrong, [], "components", b"key")


def test_component_evidence_rejects_empty_truncated_or_image_substitutes():
    for update in ({"elements": []}, {"elements_truncated": True}, {"capture_image": True}):
        with pytest.raises(benchmark.BenchmarkError):
            benchmark.observation_evidence({**component_observation(), **update}, [], "components", b"key")


def test_fingerprints_ignore_ephemeral_refs_but_detect_actual_ui_changes():
    original = component_observation()
    first = benchmark.observation_evidence(original, [], "components", b"key")
    original["elements"][0]["ref"] = "e990"
    second = benchmark.observation_evidence(original, [], "components", b"key")
    assert first["content_fingerprint"] == second["content_fingerprint"]
    original["elements"][0]["name"] = "different"
    third = benchmark.observation_evidence(original, [], "components", b"key")
    assert first["content_fingerprint"] != third["content_fingerprint"]
    assert first["structure_fingerprint"] == third["structure_fingerprint"]


def test_unique_action_targets_never_guess_between_duplicates():
    observed = component_observation()
    assert benchmark.unique_element(observed, lambda item: True)["ref"] == "e1"
    observed["elements"].append(dict(observed["elements"][0]))
    with pytest.raises(benchmark.BenchmarkError, match="action_target_not_unique"):
        benchmark.unique_element(observed, lambda item: True)


def test_comparison_rejects_effective_setting_differences_and_missing_values():
    assert benchmark.comparison([block(), block("direct-wda")])["comparable"] is True
    other = snapshot()
    other["values"]["waitForIdleTimeout"] = 0
    assert benchmark.comparison([block(), block("direct-wda", other)])["settings_value_differences"] == ["waitForIdleTimeout"]
    assert benchmark.comparison([block(), block("direct-wda", snapshot({}))])["comparable"] is False


def test_comparison_rejects_unlisted_setting_changes_via_complete_scalar_hash():
    other = snapshot()
    other["all_scalar_values_fingerprint"] = "changed_unknown_field"
    result = benchmark.comparison([block(), block("direct-wda", other)])
    assert result["comparable"] is False
    assert "settings_not_aligned" in result["reasons"]


def test_comparison_rejects_different_devices_sources_or_wda_builds():
    for key in ("configuration", "shared_source_sha256", "dependencies", "wda"):
        other = block("direct-wda")
        other[key] = {"different": True}
        assert key + "_mismatch" in benchmark.comparison([block(), other])["reasons"]


def test_comparison_checks_element_counts_orientation_and_action_structures():
    first, second = block(), block("direct-wda")
    first["samples"] = [{"operation": "components", "success": True, "warmup": False,
                         "evidence": {"element_count": 20, "orientation": "portrait"}},
                        {"operation": "action_swipe_up", "success": True,
                         "evidence": {"structure_fingerprint": "page-a"}}]
    second["samples"] = [{"operation": "components", "success": True, "warmup": False,
                          "evidence": {"element_count": 19, "orientation": "landscape"}},
                         {"operation": "action_swipe_up", "success": True,
                          "evidence": {"structure_fingerprint": "page-b"}}]
    result = benchmark.comparison([first, second])
    assert result["comparable"] is False
    assert {"component_count_changed", "orientation_changed", "action_structures_not_aligned"} <= set(result["reasons"])
    assert result["action_structure_differences"] == ["action_swipe_up"]


@pytest.mark.asyncio
async def test_recorder_counts_request_boundary_and_records_failures_without_retry():
    calls = 0
    async def request(method, path, body=None, timeout=30):
        nonlocal calls
        calls += 1
        raise benchmark.BenchmarkError("fixture_failure")
    controller = SimpleNamespace(request=request)
    recorder = benchmark.Recorder(controller)
    with pytest.raises(benchmark.BenchmarkError):
        await recorder.measure("components", lambda: controller.request("GET", "/session/secret/source"))
    assert calls == 1
    assert recorder.samples[0]["success"] is False
    assert recorder.samples[0]["request_count"] == 1
    assert recorder.samples[0]["requests"][0]["route"] == "GET /session/{session}/source"


@pytest.mark.asyncio
async def test_audit_reads_actual_wda_session_not_appium_session_or_local_settings():
    paths = []
    def respond(request):
        paths.append((request.url.port, request.url.path))
        if request.url.path == "/status":
            return httpx.Response(200, json={"sessionId": "owned-wda-session", "value": {"ready": True,
                "build": {"productBundleIdentifier": "org.example.WDA.xctrunner", "version": "1.0"}, "os": {"version": "26"}}})
        assert request.url.path == "/session/owned-wda-session/appium/settings"
        return httpx.Response(200, json={"value": {**snapshot()["values"], "customPrivateSetting": "not-for-report"}})
    config = SimpleNamespace(port=18401, wda_bundle_id="org.example.WDA")
    records = []
    result = await benchmark.wda_settings_audit(config, "appium", 8100, b"key", transport=httpx.MockTransport(respond), request_records=records)
    assert paths == [(8100, "/status"), (8100, "/session/owned-wda-session/appium/settings")]
    assert result["source"] == "owned_wda_http_endpoint"
    assert "customPrivateSetting" in result["all_keys"]
    assert "not-for-report" not in json.dumps(result)
    assert all(record["boundary"] == "wda_audit_http" for record in records)
    assert "owned-wda-session" not in json.dumps(records)


@pytest.mark.asyncio
async def test_audit_refuses_idle_or_wrong_runner_before_requesting_settings():
    config = SimpleNamespace(port=18401, wda_bundle_id="org.example.WDA")
    for sid, bundle in ((None, "org.example.WDA.xctrunner"), ("sid", "org.other.WDA")):
        calls = []
        def respond(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={"sessionId": sid, "value": {"ready": True, "build": {"productBundleIdentifier": bundle}}})
        with pytest.raises(benchmark.BenchmarkError, match="wda_audit_identity_or_session_mismatch"):
            await benchmark.wda_settings_audit(config, "direct-wda", 8100, b"key", transport=httpx.MockTransport(respond))
        assert calls == ["/status"]

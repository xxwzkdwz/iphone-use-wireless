#!/usr/bin/env python3
"""Explicit, sequential device benchmark. Importing this file never contacts a device."""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
TARGET_APP = "com.apple.Preferences"
SHARED_FILES = ("src/vico_iphone/iphone_control.py", "src/vico_iphone/coredevice_discovery.py", "src/vico_iphone/state.py")
SETTINGS_FIELDS = (
    "waitForIdleTimeout", "animationCoolOffTimeout", "snapshotMaxDepth", "customSnapshotTimeout",
    "screenshotQuality", "screenshotOrientation", "useFirstMatch", "boundElementsByIndex",
    "includeNonModalElements", "shouldUseCompactResponses", "elementResponseAttributes",
    "shouldUseTestManagerForVisibilityDetection", "reduceMotion", "activeAppDetectionPoint",
    "defaultActiveApplication", "useJSONSource", "maxTypingFrequency",
)
REQUIRED_SETTINGS = ("waitForIdleTimeout", "animationCoolOffTimeout", "snapshotMaxDepth", "screenshotQuality")


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    os.replace(temporary, path)


def digest(value, key: bytes) -> str:
    return hmac.new(key, json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def safe_code(value) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ .:-]{0,99}", value) else "benchmark_error"


class BenchmarkError(RuntimeError):
    def __init__(self, code):
        self.code = safe_code(code)
        super().__init__(self.code)


def route_label(method: str, path: str, body) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"(/session/)[^/]+", r"\1{session}", path)
    path = re.sub(r"(/element/)[^/]+", r"\1{element}", path)
    if path.endswith("/execute/sync") and isinstance(body, dict):
        script = body.get("script")
        if script in {"mobile: isLocked", "mobile: activeAppInfo", "mobile: deviceScreenInfo", "mobile: activateApp",
                      "mobile: swipe", "mobile: pressButton", "mobile: tap", "mobile: keys"}:
            path += " [" + script + "]"
    return f"{method.upper()} {path}"


def parse_result(result):
    text = [block.text for block in result.content if block.type == "text"]
    if len(text) != 1:
        raise BenchmarkError("invalid_tool_payload")
    value = json.loads(text[0])
    if not isinstance(value, dict) or result.isError or value.get("ok") is not True:
        raise BenchmarkError(value.get("error", {}).get("code", "tool_failed") if isinstance(value, dict) else "tool_failed")
    return value


def observation_evidence(value: dict, image_blocks, mode: str, key: bytes) -> dict:
    if value.get("bundle_id") != TARGET_APP:
        raise BenchmarkError("unexpected_foreground_app")
    rect = value.get("window_rect")
    if not isinstance(rect, dict) or not all(isinstance(rect.get(k), (int, float)) and math.isfinite(rect[k]) for k in ("x", "y", "width", "height")):
        raise BenchmarkError("invalid_window_rect")
    evidence = {"expected_app": True, "window_rect": rect, "mode": mode,
                "orientation": "portrait" if rect["height"] >= rect["width"] else "landscape"}
    if mode == "components":
        elements = value.get("elements")
        if value.get("components_status") != "available" or not isinstance(elements, list) or not elements:
            raise BenchmarkError("components_missing")
        if value.get("capture_image") is not False or image_blocks:
            raise BenchmarkError("unexpected_components_screenshot")
        normalized = [{k: v for k, v in item.items() if k != "ref"} for item in elements]
        shape = [{k: v for k, v in item.items() if k in {"type", "x", "y", "width", "height", "enabled", "visible"}} for item in elements]
        evidence.update(element_count=len(elements), elements_total=value.get("elements_total"),
                        truncated=value.get("elements_truncated"), content_fingerprint=digest(normalized, key),
                        structure_fingerprint=digest(shape, key))
        if value.get("elements_truncated") is not False:
            raise BenchmarkError("components_truncated")
    else:
        if value.get("capture_image") is not True or value.get("include_elements") is not False or len(image_blocks) != 1:
            raise BenchmarkError("screenshot_contract_mismatch")
        image = image_blocks[0]
        if image.mimeType != "image/png":
            raise BenchmarkError("screenshot_not_png")
        png = base64.b64decode(image.data, validate=True)
        if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
            raise BenchmarkError("invalid_png")
        width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
        if (width, height) != (value.get("width"), value.get("height")) or not (0 < width <= 10000 and 0 < height <= 10000):
            raise BenchmarkError("png_dimensions_mismatch")
        evidence.update(png_width=width, png_height=height, png_bytes=len(png), png_fingerprint=hmac.new(key, png, hashlib.sha256).hexdigest())
    return evidence


def settings_snapshot(value) -> dict:
    if not isinstance(value, dict):
        raise BenchmarkError("invalid_settings_response")
    scalars = {key: val for key, val in value.items() if isinstance(key, str) and (val is None or isinstance(val, (str, int, float, bool)))}
    values = {key: scalars[key] for key in SETTINGS_FIELDS if key in scalars}
    return {"values": values, "all_keys": sorted(value), "scalar_keys": sorted(scalars),
            "missing_required": [key for key in REQUIRED_SETTINGS if key not in values]}


async def wda_settings_audit(config, backend, appium_wda_port, key, *, request_records=None, transport=None) -> dict:
    """Read the actual owned WDA, never Appium's potentially local settings cache."""
    import httpx
    port = appium_wda_port if backend == "appium" else config.port
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                                 follow_redirects=False, transport=transport, timeout=8) as client:
        async def get(path):
            record = {"route": route_label("GET", path, None), "boundary": "wda_audit_http", "success": False}
            start = time.perf_counter()
            try:
                response = await client.get(path)
                if response.status_code != 200 or len(response.content) > 512 * 1024:
                    raise BenchmarkError("wda_audit_http_failed")
                value = response.json()
                if not isinstance(value, dict) or "value" not in value:
                    raise BenchmarkError("wda_audit_invalid_response")
                record["success"] = True
                return value
            finally:
                record["seconds"] = time.perf_counter() - start
                if request_records is not None:
                    request_records.append(record)
        status = await get("/status")
        sid = status.get("sessionId")
        value = status.get("value", {})
        bundle = value.get("build", {}).get("productBundleIdentifier")
        allowed = {config.wda_bundle_id, config.wda_bundle_id.removesuffix(".xctrunner") + ".xctrunner"}
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", sid) or value.get("ready") is not True or bundle not in allowed:
            raise BenchmarkError("wda_audit_identity_or_session_mismatch")
        raw = await get(f"/session/{sid}/appium/settings")
        snapshot = settings_snapshot(raw["value"])
        snapshot["values_fingerprint"] = digest(snapshot["values"], key)
        all_scalars = {name: val for name, val in raw["value"].items()
                       if val is None or isinstance(val, (str, int, float, bool))}
        snapshot["all_scalar_values_fingerprint"] = digest(all_scalars, key)
        snapshot["source"] = "owned_wda_http_endpoint"
        snapshot["wda"] = {"version": value.get("build", {}).get("version"), "built_at": value.get("build", {}).get("time"),
                           "os_version": value.get("os", {}).get("version")}
        return snapshot


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    items = sorted(values)
    position = (len(items) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return items[lower] + (items[upper] - items[lower]) * (position - lower)


def summarize(samples: list[dict]) -> dict:
    output = {}
    for operation in sorted({item["operation"] for item in samples}):
        selected = [item for item in samples if item["operation"] == operation]
        successful = [item["seconds"] for item in selected if item["success"]]
        output[operation] = {"attempts": len(selected), "successes": len(successful), "failures": len(selected) - len(successful),
                             "median_seconds": statistics.median(successful) if successful else None,
                             "p90_seconds": percentile(successful, .9)}
    return output


class Recorder:
    def __init__(self, controller):
        self.samples = []
        self.active_routes = None
        original = controller.request

        async def traced(method, path, body=None, timeout=30):
            routes = self.active_routes
            record = {"route": route_label(method, path, body), "success": False}
            start = time.perf_counter()
            try:
                value = await original(method, path, body, timeout)
                record["success"] = True
                return value
            except BaseException as exc:
                record["error_code"] = safe_code(getattr(exc, "code", type(exc).__name__))
                raise
            finally:
                record["seconds"] = time.perf_counter() - start
                if routes is not None:
                    routes.append(record)
        controller.request = traced

    async def measure(self, operation, call, validate=None, *, warmup=False):
        record = {"operation": operation, "warmup": warmup, "started_utc": utc(), "success": False,
                  "request_boundary": "python_controller.request", "requests": []}
        self.active_routes = record["requests"]
        start = time.perf_counter()
        try:
            value = await call()
            record["seconds"] = time.perf_counter() - start
            if validate:
                record["evidence"] = validate(value)
            record["success"] = True
            return value
        except BaseException as exc:
            record.setdefault("seconds", time.perf_counter() - start)
            record["error_code"] = safe_code(getattr(exc, "code", type(exc).__name__))
            raise
        finally:
            self.active_routes = None
            record["request_count"] = len(record["requests"])
            self.samples.append(record)


def shared_hashes(source: Path) -> dict:
    return {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in SHARED_FILES}


def unique_element(value, predicate):
    elements = [item for item in value.get("elements", []) if item.get("enabled") == "true" and item.get("visible") == "true" and predicate(item)]
    if len(elements) != 1 or not elements[0].get("ref"):
        raise BenchmarkError("action_target_not_unique")
    return elements[0]


async def worker(args) -> int:
    sys.path.insert(0, str(args.source / "src"))
    os.environ["VICO_IPHONE_HOME"] = str(args.home)
    os.environ["IPHONE_USE_BACKEND"] = args.backend
    from vico_iphone.server import PhoneService
    key = bytes.fromhex(os.environ["IPHONE_BENCHMARK_HASH_KEY"])
    service = PhoneService(home=args.home, connect_wait_seconds=20)
    recorder = Recorder(service.controller)
    result = {"backend": args.backend, "block": args.block, "started_utc": utc(), "success": False,
              "shared_source_sha256": shared_hashes(args.source), "samples": recorder.samples,
              "scope": "PhoneService tool API; model and MCP stdio transport excluded",
              "dependencies": {name: importlib.metadata.version(name) for name in ("httpx", "pydantic", "mcp")},
              "settings": {}}
    token = None

    async def tool(name, params):
        response = await service.call(name, params)
        return parse_result(response), response

    async def observe(mode):
        return await tool("iphone_observe", {"session_token": token, "mode": mode})

    def validate_observation(pair, mode):
        value, response = pair
        return observation_evidence(value, [item for item in response.content if item.type == "image"], mode, key)

    async def connect():
        pair = await tool("iphone_session", {"action": "connect"})
        while pair[0].get("pending"):
            pair = await tool("iphone_session", {"action": "connect_status", "operation_id": pair[0]["operation_id"]})
        if not pair[0].get("session_token"):
            raise BenchmarkError("session_token_missing")
        return pair

    async def settings():
        if not service.controller.session_id or service.controller.owner != service.owner:
            raise BenchmarkError("wda_audit_requires_owned_session")
        return await wda_settings_audit(config, args.backend, args.appium_wda_port, key,
                                        request_records=recorder.active_routes)

    async def actions(cycle):
        pair = await recorder.measure("action_prepare", lambda: observe("components"), lambda p: validate_observation(p, "components"))
        current = pair[0]
        start_structure = recorder.samples[-1]["evidence"]["structure_fingerprint"]

        async def action(label, parameters, check=None):
            nonlocal current
            def validate(pair):
                value, response = pair
                observed = value.get("observation")
                if not isinstance(observed, dict):
                    raise BenchmarkError("action_observation_missing")
                evidence = observation_evidence(observed, [], "components", key)
                if check:
                    check(observed)
                evidence["postcondition_checked"] = check is not None
                return evidence
            pair = await recorder.measure(f"action_{label}", lambda: tool("iphone_act", {
                "session_token": token, "observation_id": current["observation_id"], "observe_after": True, **parameters}), validate)
            recorder.samples[-1]["cycle"] = cycle
            current = pair[0]["observation"]

        search = lambda item: item.get("type") == "XCUIElementTypeSearchField"
        field = unique_element(current, search)
        await action("focus_search", {"action": "tap", "element_ref": field["ref"]}, lambda obs: unique_element(obs, search))
        field = unique_element(current, search)
        def entered(obs):
            if unique_element(obs, search).get("value") != "通用":
                raise BenchmarkError("typed_text_not_verified")
        await action("type_search", {"action": "type", "element_ref": field["ref"], "text": "通用"}, entered)
        button = lambda labels: lambda item: item.get("type") == "XCUIElementTypeButton" and any(item.get(k) in labels for k in ("name", "label"))
        clear = unique_element(current, button({"清除文本", "Clear text"}))
        def cleared(obs):
            if "通用" in unique_element(obs, search).get("value", ""):
                raise BenchmarkError("search_clear_not_verified")
        await action("clear_search", {"action": "tap", "element_ref": clear["ref"]}, cleared)
        close_labels = {"关闭", "取消", "Cancel", "Close"}
        close = unique_element(current, button(close_labels))
        def closed(obs):
            if any(item.get("keyboard_ancestor") == "true" for item in obs.get("elements", [])):
                raise BenchmarkError("keyboard_still_visible")
            if any(item.get("type") == "XCUIElementTypeButton" and any(item.get(k) in close_labels for k in ("name", "label"))
                   for item in obs.get("elements", [])):
                raise BenchmarkError("search_still_open")
            unique_element(obs, search)
        await action("close_search", {"action": "tap", "element_ref": close["ref"]}, closed)
        await action("swipe_up", {"action": "swipe", "direction": "up"})
        def restored(obs):
            closed(obs)
            if observation_evidence(obs, [], "components", key)["structure_fingerprint"] != start_structure:
                raise BenchmarkError("settings_home_not_restored")
        await action("swipe_down", {"action": "swipe", "direction": "down"}, restored)

    try:
        config = service.controller.load_config()
        if config.wda_transport != "coredevice" or config.use_preinstalled_wda is not True:
            raise BenchmarkError("wireless_preinstalled_profile_required")
        result["configuration"] = {"device_fingerprint": digest(config.udid, key), "wda_bundle_fingerprint": digest(config.wda_bundle_id, key),
            "platform_version": config.platform_version, "wda_transport": config.wda_transport,
            "use_preinstalled_wda": config.use_preinstalled_wda, "control_scope": config.control_scope}
        status = await recorder.measure("prepare_status", lambda: tool("iphone_setup", {"action": "status"}))
        if args.backend == "appium" and (status[0].get("running") is not True or status[0].get("ready") is not True):
            raise BenchmarkError("baseline_bridge_not_ready")
        if args.backend == "direct-wda" and status[0].get("dependencies_ready") is not True:
            raise BenchmarkError("candidate_runtime_not_ready")
        pair = await recorder.measure("connect", connect)
        token = pair[0]["session_token"]
        result["settings"]["before"] = await recorder.measure("settings_before", settings)
        initial_settings = result["settings"]["before"]
        result["wda"] = initial_settings["wda"]
        if initial_settings["missing_required"]:
            raise BenchmarkError("required_wda_settings_missing")
        if args.reference_block:
            reference = json.loads(args.reference_block.read_text())["settings"]["before"]
            if (reference["values"] != initial_settings["values"] or
                    reference["all_scalar_values_fingerprint"] != initial_settings["all_scalar_values_fingerprint"]):
                raise BenchmarkError("wda_settings_not_aligned")
        await recorder.measure("prepare_settings_app", lambda: tool("iphone_launch", {
            "session_token": token, "bundle_id": TARGET_APP, "observe_after": False}))
        for mode, count in (("components", 2), ("screenshot", 1)):
            for _ in range(count):
                await recorder.measure(mode, lambda mode=mode: observe(mode), lambda pair, mode=mode: validate_observation(pair, mode), warmup=True)
        for mode, count in (("components", args.samples), ("screenshot", args.screenshots)):
            for _ in range(count):
                await recorder.measure(mode, lambda mode=mode: observe(mode), lambda pair, mode=mode: validate_observation(pair, mode))
        for cycle in range(args.action_cycles):
            await actions(cycle + 1)
        result["settings"]["after"] = await recorder.measure("settings_after", settings)
        if (result["settings"]["after"]["values"] != initial_settings["values"] or
                result["settings"]["after"]["all_scalar_values_fingerprint"] != initial_settings["all_scalar_values_fingerprint"]):
            raise BenchmarkError("wda_settings_changed_during_block")
        result["success"] = True
    except BaseException as exc:
        result["failure_code"] = safe_code(getattr(exc, "code", type(exc).__name__))
    finally:
        if token:
            try:
                await recorder.measure("disconnect", lambda: tool("iphone_session", {"action": "disconnect", "session_token": token}))
            except BaseException as exc:
                result["success"] = False
                result["cleanup_failure_code"] = safe_code(getattr(exc, "code", type(exc).__name__))
        try:
            await asyncio.wait_for(service.close(), timeout=40)
        except BaseException:
            result["success"] = False
            result["cleanup_failure_code"] = "service_close_unverified"
        if service.controller.owner is not None or service.controller.uncertain:
            result["success"] = False
            result["cleanup_failure_code"] = "device_ownership_retained"
        if service.journal.exists():
            result["success"] = False
            result["cleanup_failure_code"] = "session_journal_retained"
        result["cleanup_verified"] = (service.controller.owner is None and not service.controller.uncertain
                                      and not service.journal.exists() and "cleanup_failure_code" not in result)
        result["finished_utc"] = utc()
        result["summary"] = summarize([item for item in recorder.samples if not item["warmup"]])
        private_json(args.worker_output, result)
    return 0 if result["success"] else 1


def comparison(blocks: list[dict]) -> dict:
    reasons = []
    if not blocks or not all(block.get("success") for block in blocks):
        reasons.append("one_or_more_blocks_failed_or_missing")
    settings = [block.get("settings", {}).get(phase) for block in blocks for phase in ("before", "after")]
    settings = [value for value in settings if value is not None]
    differing = [key for key in SETTINGS_FIELDS if len({json.dumps(value["values"].get(key, "<missing>"), sort_keys=True) for value in settings}) > 1]
    missing = sorted({key for value in settings for key in value["missing_required"]})
    if (len(settings) != len(blocks) * 2 or differing or missing or
            len({value.get("all_scalar_values_fingerprint") for value in settings}) > 1):
        reasons.append("settings_not_aligned")
    for field in ("configuration", "shared_source_sha256", "dependencies", "wda"):
        if len({json.dumps(block.get(field), sort_keys=True) for block in blocks}) > 1:
            reasons.append(field + "_mismatch")
    evidence = [sample["evidence"] for block in blocks for sample in block.get("samples", [])
                if sample.get("success") and sample["operation"] in {"components", "screenshot"} and "evidence" in sample and not sample["warmup"]]
    for signature, condition in (("structure_fingerprint", "component_structure_changed"), ("element_count", "component_count_changed"),
                                 ("window_rect", "window_dimensions_changed"), ("orientation", "orientation_changed")):
        if len({json.dumps(item[signature], sort_keys=True) for item in evidence if signature in item}) > 1:
            reasons.append(condition)
    dimensions = {(item["png_width"], item["png_height"]) for item in evidence if "png_width" in item}
    if len(dimensions) > 1:
        reasons.append("png_dimensions_changed")
    actions = {}
    for current in blocks:
        for sample in current.get("samples", []):
            if sample["operation"].startswith("action_") and sample.get("success") and "evidence" in sample:
                actions.setdefault(sample["operation"], set()).add(sample["evidence"].get("structure_fingerprint"))
    action_differences = sorted(name for name, signatures in actions.items() if len(signatures) > 1)
    if action_differences:
        reasons.append("action_structures_not_aligned")
    key_sets = [set(value["all_keys"]) for value in settings]
    key_differences = sorted(set.union(*key_sets) - set.intersection(*key_sets)) if key_sets else []
    return {"comparable": not reasons, "reasons": reasons, "settings_value_differences": differing,
            "settings_missing_required": missing, "settings_key_differences": key_differences,
            "action_structure_differences": action_differences,
            "content_hashes_identical": len({item["content_fingerprint"] for item in evidence if "content_fingerprint" in item}) <= 1,
            "png_hashes_identical": len({item["png_fingerprint"] for item in evidence if "png_fingerprint" in item}) <= 1,
            "note": "Controller-boundary timings; request counts do not reveal Appium internal WDA calls or cache hits. Pixel/text hashes may vary with the clock and battery."}


def sequence(blocks: int) -> list[str]:
    if blocks < 4 or blocks % 4:
        raise BenchmarkError("blocks_must_be_multiple_of_four")
    return ["appium", "direct-wda", "direct-wda", "appium"] * (blocks // 4)


def main(args) -> int:
    if args.worker:
        return asyncio.run(worker(args))
    order = sequence(args.blocks)
    sources = {"appium": args.baseline_source.resolve(), "direct-wda": args.candidate_source.resolve()}
    homes = {"appium": args.baseline_home.resolve(), "direct-wda": args.candidate_home.resolve()}
    if sources["appium"] == sources["direct-wda"] or homes["appium"] == homes["direct-wda"]:
        raise BenchmarkError("separate_sources_and_profiles_required")
    if shared_hashes(sources["appium"]) != shared_hashes(sources["direct-wda"]):
        raise BenchmarkError("shared_controller_source_mismatch")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.is_symlink() or output.stat().st_uid != os.getuid() or output.stat().st_mode & 0o077 or any(output.iterdir()):
        raise BenchmarkError("output_must_be_empty_private_owned_directory")
    key = secrets.token_hex(32)
    report = {"protocol_version": 1, "started_utc": utc(), "planned_order": order, "planned_samples_per_block": args.samples,
              "planned_screenshots_per_block": args.screenshots, "action_cycles_per_block": args.action_cycles,
              "warmup_per_block": {"components": 2, "screenshot": 1}, "blocks": [],
              "scope": "Same PhoneService tool API; excludes model inference and MCP stdio transport; no unmeasured action retries"}
    for index, backend in enumerate(order, 1):
        block_output = output / f"block-{index:02d}-{backend}.json"
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--source", str(sources[backend]),
                   "--home", str(homes[backend]), "--backend", backend, "--block", str(index),
                   "--worker-output", str(block_output), "--samples", str(args.samples), "--screenshots", str(args.screenshots),
                   "--action-cycles", str(args.action_cycles), "--appium-wda-port", str(args.appium_wda_port)]
        if index > 1:
            command += ["--reference-block", str(output / "block-01-appium.json")]
        env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "DEVELOPER_DIR") if key in os.environ}
        env["IPHONE_BENCHMARK_HASH_KEY"] = key
        process = subprocess.Popen(command, cwd=sources[backend], env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        timed_out = False
        try:
            process.wait(timeout=args.block_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=50)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if block_output.is_file():
            block = json.loads(block_output.read_text())
        else:
            block = {"block": index, "backend": backend, "success": False, "failure_code": "worker_receipt_missing", "samples": []}
        if timed_out or process.returncode != 0:
            block["success"] = False
            block["worker_exit_code"] = process.returncode
            block["worker_timed_out"] = timed_out
        report["blocks"].append(block)
        report["comparison"] = comparison(report["blocks"])
        report["completed"] = len(report["blocks"]) == len(order) and all(value["success"] for value in report["blocks"])
        if not report["completed"]:
            report["comparison"]["comparable"] = False
        private_json(output / "benchmark.json", report)
        print(json.dumps({"block": index, "backend": backend, "success": block["success"], "summary": block.get("summary", {}),
                          "failure_code": block.get("failure_code"), "cleanup_failure_code": block.get("cleanup_failure_code")}), flush=True)
        if not block["success"]:
            break
    report["finished_utc"] = utc()
    private_json(output / "benchmark.json", report)
    return 0 if report["completed"] and report["comparison"]["comparable"] else 1


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-source", type=Path)
    p.add_argument("--candidate-source", type=Path, default=ROOT)
    p.add_argument("--baseline-home", type=Path)
    p.add_argument("--candidate-home", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--blocks", type=int, default=4)
    p.add_argument("--samples", type=int, default=8)
    p.add_argument("--screenshots", type=int, default=4)
    p.add_argument("--action-cycles", type=int, default=0)
    p.add_argument("--block-timeout", type=int, default=300)
    p.add_argument("--appium-wda-port", type=int, default=8100)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--backend", choices=("appium", "direct-wda"), help=argparse.SUPPRESS)
    p.add_argument("--block", type=int, help=argparse.SUPPRESS)
    p.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--reference-block", type=Path, help=argparse.SUPPRESS)
    return p


if __name__ == "__main__":
    arguments = parser().parse_args()
    if arguments.samples < 1 or arguments.screenshots < 1 or arguments.action_cycles < 0:
        raise SystemExit("Sample counts must be positive; action cycles must be nonnegative.")
    required = ("source", "home", "backend", "block", "worker_output") if arguments.worker else ("baseline_source", "baseline_home", "candidate_home", "output")
    if any(getattr(arguments, name) is None for name in required):
        raise SystemExit("Provide separate baseline/candidate source and profile paths, and an empty private --output directory.")
    try:
        raise SystemExit(main(arguments))
    except BenchmarkError as exc:
        print(json.dumps({"success": False, "error_code": exc.code}))
        raise SystemExit(1)

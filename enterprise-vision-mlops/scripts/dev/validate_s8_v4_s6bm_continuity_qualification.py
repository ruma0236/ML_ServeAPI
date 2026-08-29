from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.s6bm_causal import (  # noqa: E402
    S6BMCausalError,
    project_run_clock_offset_envelope,
    project_switch_fence_commit_interval,
    validate_causal_bundle,
)
from evm.scale_validation.s6bm_runtime import (  # noqa: E402
    S6BMConfig,
    S6BMRuntimeError,
    canonical,
    canonical_sha256,
    project_continuity_contract,
    project_success_attempt,
    sha256_file,
)


CASE_CONTRACT = (
    (
        "bridge_server_start_after_switch_fence",
        "s6bm_v4_continuity_server_after_switch",
    ),
    (
        "bridge_controller_start_after_switch_fence",
        "s6bm_v4_continuity_controller_after_switch",
    ),
    (
        "pre_switch_terminal_effect_after_switch_fence",
        "s6bm_v4_continuity_terminal_fence",
    ),
    (
        "pre_switch_terminal_effect_missing",
        "s6bm_v4_continuity_terminal_durable_binding",
    ),
    ("normal_request_bound_to_old_blue", "s6bm_continuity_role_binding"),
    (
        "callback_before_required_actor_start",
        "s6bm_v4_continuity_actor_receipt_commit_readback",
    ),
    (
        "bridge_actor_receipt_readback_absent",
        "s6bm_v4_continuity_actor_receipt_commit_readback",
    ),
    (
        "bridge_actor_receipt_commit_after_switch",
        "s6bm_v4_continuity_actor_receipt_commit_readback",
    ),
    ("bridge_required_event_missing", "s6bm_v4_event_type_set"),
    ("pre_switch_terminal_set_missing", "s6bm_continuity_pre_switch_terminal_set"),
    ("pre_switch_terminal_set_extra", "s6bm_continuity_pre_switch_terminal_set"),
    (
        "pre_switch_terminal_identity_mismatch",
        "s6bm_continuity_pre_switch_terminal_binding",
    ),
    ("all_submitted_last_request_green_routed", "s6bm_continuity_role_binding"),
    ("pending_crossover_set_missing", "s6bm_continuity_pending_crossover_gate"),
    ("pending_crossover_set_extra", "s6bm_continuity_pending_crossover_gate"),
    ("one_crossover_not_released", "s6bm_continuity_crossover_release_binding"),
    ("release_before_route_applied", "s6bm_continuity_crossover_release_order"),
    ("release_before_receipt_gate", "s6bm_continuity_crossover_release_order"),
    ("crossover_timeout_cleanup_residue", "s6bm_success_cleanup"),
    (
        "pre_switch_online_wrong_response_id",
        "s6bm_continuity_pre_switch_terminal_set",
    ),
    (
        "pre_switch_online_stale_generation",
        "s6bm_continuity_pre_switch_terminal_binding",
    ),
    (
        "pre_switch_online_wrong_artifact_version",
        "s6bm_continuity_pre_switch_terminal_binding",
    ),
    (
        "pre_switch_online_durable_readback_absent",
        "s6bm_continuity_pre_switch_terminal_binding",
    ),
    (
        "pre_switch_terminal_fence_hash_mismatch",
        "s6bm_continuity_pre_switch_terminal_set",
    ),
)
CASE_CONTRACT_SHA256 = "9613d11a2f68b801780d88fd9fe7197ce48685a5879e3e47c45d21c00a432500"
SUPERSEDED_CASE_CONTRACT = (
    CASE_CONTRACT[:2]
    + (
        (
            "bridge_model_start_after_switch_fence",
            "s6bm_v4_continuity_model_after_switch",
        ),
        (
            "bridge_compute_start_after_switch_fence",
            "s6bm_v4_continuity_compute_after_switch",
        ),
    )
    + CASE_CONTRACT[4:]
)
SUPERSEDED_CASE_CONTRACT_SHA256 = "d75b6bbc0e396151dc581b05b86fffc5f59ae4681f34a42b60e560f99c85d886"
HISTORICAL_CASE_CONTRACT = SUPERSEDED_CASE_CONTRACT[:9]
HISTORICAL_CASE_CONTRACT_SHA256 = "c42a6245d1e48152d06c6f1bd31c7fca8de58f201129c99bb7c59abe9356d4d7"
PREVIOUS_CASE_CONTRACT = SUPERSEDED_CASE_CONTRACT[:19]
PREVIOUS_CASE_CONTRACT_SHA256 = "230ff21035dace5eb498d649d69a1bb063c377c95808d8d9bcae5903edd13a6e"


class ContinuityQualificationError(RuntimeError):
    pass


Mutation = Callable[[Path, dict[str, Any], S6BMConfig], None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the exact-1000 S6B-M continuity qualification"
    )
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_s6bm_blue_green_v4.toml",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise ContinuityQualificationError(f"noncanonical_json:{path.name}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ContinuityQualificationError(f"json_object_required:{path.name}")
    return payload


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")


def git_text(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise ContinuityQualificationError(f"git_command:{' '.join(arguments)}")
    return result.stdout.strip()


def reference_path(root: Path, reference: dict[str, Any]) -> Path:
    relative = Path(str(reference.get("path", "")))
    if relative.is_absolute() or ".." in relative.parts:
        raise ContinuityQualificationError("continuity_reference_path")
    return root / relative


def refresh(reference: dict[str, Any], path: Path) -> None:
    reference["bytes"] = path.stat().st_size
    reference["sha256"] = sha256_file(path)


def rewrite_reference(
    root: Path,
    reference: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    path = reference_path(root, reference)
    payload = read_json(path)
    mutate(payload)
    canonical_write(path, payload)
    refresh(reference, path)
    return payload


def proof(raw: dict[str, Any]) -> dict[str, Any]:
    return dict(raw["causal_proof"])


def required_bridge_id(raw: dict[str, Any]) -> str:
    items = [
        dict(item)
        for item in raw["traffic_plan"]["roles"]["bridge"]
        if item.get("actor_receipt_required") is True
    ]
    if len(items) != 4:
        raise ContinuityQualificationError("required_bridge_exact_set")
    return str(items[0]["request_id"])


def record_for(raw: dict[str, Any], request_id: str) -> dict[str, Any]:
    matches = [item for item in raw["request_records"] if item.get("request_id") == request_id]
    if len(matches) != 1:
        raise ContinuityQualificationError("request_record_identity")
    return matches[0]


def span_entry(payload: dict[str, Any], *, trace_id: str, name: str) -> dict[str, Any]:
    matches = [
        entry
        for entry in payload["entries"]
        if entry["span"].get("traceId") == trace_id and entry["span"].get("name") == name
    ]
    if len(matches) != 1:
        raise ContinuityQualificationError(f"span_identity:{name}:{len(matches)}")
    return matches[0]


def after_switch_unix_ns(raw: dict[str, Any], config: S6BMConfig) -> tuple[int, int]:
    hold_id = str(raw["traffic_plan"]["roles"]["causal_hold"][0]["request_id"])
    hold = record_for(raw, hold_id)
    switch = project_switch_fence_commit_interval(
        proof(raw)["route_transition_receipt"],
        hold["durable_effect"],
        config,
    )
    clock = project_run_clock_offset_envelope(raw, config)
    fence_upper = int(switch["fence_commit_interval_ns"][1])
    offset_high = int(clock["offset_envelope_ns"][1])
    target_monotonic = fence_upper + 1_000_000
    return target_monotonic + offset_high, target_monotonic


def observed_transition_from_switch(switch_event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(switch_event["payload"])
    return {
        "schema_version": "evm.s6bm.observed_transition.v1",
        "transition_id": str(payload["transition_id"]),
        "fence_id": str(payload["fence_id"]),
        "fence_sequence": int(switch_event["causal_sequence"]),
        "fence_transaction_id": str(switch_event["transaction_id"]),
        "fence_payload_sha256": str(switch_event["payload_sha256"]),
        "attempt_id": str(switch_event["attempt_id"]),
        "run_id": str(switch_event["run_id"]),
        "request_id": str(switch_event["request_id"]),
        "old_route_generation": int(payload["old_route_generation"]),
        "new_route_generation": int(payload["new_route_generation"]),
        "source_payload_sha256": str(payload["source_payload_sha256"]),
        "cell_id": str(payload["cell_id"]),
        "replica_id": str(payload["replica_id"]),
        "database_recorded_at": str(switch_event["database_recorded_at"]),
    }


def refresh_crossover_effect_bindings(
    root: Path,
    raw: dict[str, Any],
    causal_export: dict[str, Any],
) -> None:
    """Rebind both post-switch crossover effects to the rewritten fence."""
    switch_event = next(
        item
        for item in causal_export["events"]
        if item.get("event_type") == "blue_to_green_switch_commit"
    )
    observed_transition = observed_transition_from_switch(switch_event)
    effect_events = [
        item
        for item in causal_export["events"]
        if item.get("event_type") == "durable_terminal_effect_commit"
        and dict(item.get("payload", {})).get("requires_switch_before_effect") is True
    ]
    eligible_ids = sorted(
        str(value) for value in switch_event["payload"].get("pending_crossover_request_ids", [])
    )
    event_ids = sorted(str(item["request_id"]) for item in effect_events)
    if event_ids != eligible_ids or len(event_ids) != 2:
        raise ContinuityQualificationError("crossover_effect_exact_set")
    records = {str(item["request_id"]): item for item in raw["request_records"]}
    effects_reference = proof(raw)["durable_effect_export"]
    effects_path = reference_path(root, effects_reference)
    effects_export = read_json(effects_path)
    effects = {
        str(item.get("idempotency_key", "")): item for item in effects_export.get("effects", [])
    }
    if not set(event_ids).issubset(records) or not set(event_ids).issubset(effects):
        raise ContinuityQualificationError("crossover_effect_artifact_set")
    for event in effect_events:
        request_id = str(event["request_id"])
        event["payload"]["observed_transition"] = copy.deepcopy(observed_transition)
        event["payload_sha256"] = canonical_sha256(event["payload"])
        receipt = records[request_id]["durable_effect"]
        receipt["observed_transition"] = copy.deepcopy(observed_transition)
        receipt["causal_payload_sha256"] = event["payload_sha256"]
        stored = effects[request_id]["payload"]
        stored["observed_transition"] = copy.deepcopy(observed_transition)
        stored["durable_commit"]["observed_transition"] = copy.deepcopy(observed_transition)
        stored["durable_commit"]["causal_payload_sha256"] = event["payload_sha256"]
        receipt["stored_payload_sha256"] = canonical_sha256(stored)
    canonical_write(effects_path, effects_export)
    refresh(effects_reference, effects_path)


def refresh_switch_receipt_binding(
    root: Path,
    raw: dict[str, Any],
    *,
    request_id: str,
    stage: str,
) -> None:
    """Keep the immutable switch/readback chain coherent with a mutated receipt."""
    causal_reference = proof(raw)["causal_event_export"]
    causal_path = reference_path(root, causal_reference)
    causal_export = read_json(causal_path)
    start_event = next(
        item
        for item in causal_export["events"]
        if item.get("request_id") == request_id and item.get("event_type") == stage
    )
    switch_event = next(
        item
        for item in causal_export["events"]
        if item.get("event_type") == "blue_to_green_switch_commit"
    )
    switch_payload = switch_event["payload"]
    switch_payload["continuity_receipt_sequences"][request_id][stage] = start_event[
        "causal_sequence"
    ]
    switch_payload["continuity_receipt_payload_sha256"][request_id][stage] = start_event[
        "payload_sha256"
    ]
    switch_payload["continuity_receipt_transaction_ids"][request_id][stage] = start_event[
        "transaction_id"
    ]
    switch_event["payload_sha256"] = canonical_sha256(switch_payload)
    refresh_crossover_effect_bindings(root, raw, causal_export)
    canonical_write(causal_path, causal_export)
    refresh(causal_reference, causal_path)

    transition = proof(raw)["route_transition_receipt"]
    fence_receipt = transition["fence_receipt"]
    fence_receipt["payload"] = copy.deepcopy(switch_payload)
    fence_receipt["payload_sha256"] = switch_event["payload_sha256"]
    fence_receipt["fence_payload_sha256"] = switch_event["payload_sha256"]
    transition["fence_receipt_sha256"] = canonical_sha256(fence_receipt)
    transition["fence_payload_sha256"] = switch_event["payload_sha256"]


def rewrite_bridge_start_event(
    root: Path,
    raw: dict[str, Any],
    *,
    request_id: str,
    stage: str,
    actor_start_unix_ns: int,
    actor_start_monotonic_ns: int,
) -> dict[str, Any]:
    selected: dict[str, Any] = {}

    def mutate(payload: dict[str, Any]) -> None:
        nonlocal selected
        matches = [
            item
            for item in payload["events"]
            if item.get("request_id") == request_id and item.get("event_type") == stage
        ]
        if len(matches) != 1:
            raise ContinuityQualificationError("bridge_start_event_identity")
        selected = matches[0]
        selected["payload"]["actor_start_unix_ns"] = actor_start_unix_ns
        if stage != "triton_backend_compute_entry":
            selected["payload"]["monotonic_before_ns"] = actor_start_monotonic_ns
            selected["payload"]["monotonic_after_ns"] = actor_start_monotonic_ns + 500
        selected["payload_sha256"] = canonical_sha256(selected["payload"])

    gate_bundle = raw["continuity_execution"]["bridge_actor_receipt_gate"]
    rewrite_reference(root, proof(raw)["causal_event_export"], mutate)
    rewrite_reference(root, gate_bundle["raw_readback_export"], mutate)
    gate_events = gate_bundle["events"]
    gate = next(
        item
        for item in gate_events
        if item.get("request_id") == request_id and item.get("event_type") == stage
    )
    gate["payload_sha256"] = selected["payload_sha256"]
    gate_bundle["selected_event_set_sha256"] = canonical_sha256(gate_events)
    refresh_switch_receipt_binding(
        root,
        raw,
        request_id=request_id,
        stage=stage,
    )
    return selected


def move_bridge_span_and_receipt(
    root: Path,
    raw: dict[str, Any],
    config: S6BMConfig,
    *,
    span_name: str,
    stage: str | None,
) -> None:
    request_id = required_bridge_id(raw)
    record = record_for(raw, request_id)
    target_unix_ns, target_monotonic_ns = after_switch_unix_ns(raw, config)

    def mutate_trace(payload: dict[str, Any]) -> None:
        entry = span_entry(payload, trace_id=str(record["trace_id"]), name=span_name)
        span = entry["span"]
        duration = max(
            1_000,
            int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]),
        )
        span["startTimeUnixNano"] = str(target_unix_ns)
        span["endTimeUnixNano"] = str(target_unix_ns + duration)

    rewrite_reference(root, raw["observability"]["artifacts"]["trace_export"], mutate_trace)
    if stage is not None:
        rewrite_bridge_start_event(
            root,
            raw,
            request_id=request_id,
            stage=stage,
            actor_start_unix_ns=target_unix_ns,
            actor_start_monotonic_ns=target_monotonic_ns,
        )


def mutate_server(root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    move_bridge_span_and_receipt(
        root,
        raw,
        config,
        span_name="POST /control-panel/v1/scenario-workloads/triton-blue-green/predict",
        stage="api_server_handler_entry",
    )


def mutate_controller(root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    move_bridge_span_and_receipt(
        root,
        raw,
        config,
        span_name="s6bm.controller.predict",
        stage="controller_entry",
    )


def sync_bridge_collector_trace(
    root: Path,
    raw: dict[str, Any],
    config: S6BMConfig,
    *,
    update_model: bool,
    update_compute: bool,
) -> None:
    request_id = required_bridge_id(raw)
    record = record_for(raw, request_id)
    trace_payload = read_json(
        reference_path(root, raw["observability"]["artifacts"]["trace_export"])
    )
    model_entry = span_entry(
        trace_payload, trace_id=str(record["trace_id"]), name=config.blue.model_name
    )
    compute_entry = span_entry(trace_payload, trace_id=str(record["trace_id"]), name="compute")
    collectors = raw["continuity_execution"]["bridge_triton_start_receipts"]
    collector = next(item for item in collectors if item.get("request_id") == request_id)
    raw_trace_path = reference_path(root, collector["raw_trace"])
    bridge_trace = read_json(raw_trace_path)
    if update_model:
        bridge_trace["raw_model_entry"] = copy.deepcopy(model_entry)
    if update_compute:
        bridge_trace["compute_start_unix_ns"] = int(compute_entry["span"]["startTimeUnixNano"])
        bridge_trace["raw_compute_entry"] = copy.deepcopy(compute_entry)
    canonical_write(raw_trace_path, bridge_trace)
    refresh(collector["raw_trace"], raw_trace_path)
    raw_trace_record_sha = canonical_sha256(bridge_trace)
    selected_event: dict[str, Any] = {}

    def mutate_event(payload: dict[str, Any]) -> None:
        nonlocal selected_event
        selected_event = next(
            item
            for item in payload["events"]
            if item.get("request_id") == request_id
            and item.get("event_type") == "triton_backend_compute_entry"
        )
        selected_event["payload"]["raw_trace_artifact_sha256"] = collector["raw_trace"]["sha256"]
        selected_event["payload"]["raw_trace_record_sha256"] = raw_trace_record_sha
        selected_event["payload_sha256"] = canonical_sha256(selected_event["payload"])

    gate_bundle = raw["continuity_execution"]["bridge_actor_receipt_gate"]
    rewrite_reference(root, proof(raw)["causal_event_export"], mutate_event)
    rewrite_reference(root, gate_bundle["raw_readback_export"], mutate_event)
    gate = next(
        item
        for item in gate_bundle["events"]
        if item.get("request_id") == request_id
        and item.get("event_type") == "triton_backend_compute_entry"
    )
    gate["payload_sha256"] = selected_event["payload_sha256"]
    gate_bundle["selected_event_set_sha256"] = canonical_sha256(gate_bundle["events"])
    collector["raw_trace_sha256"] = collector["raw_trace"]["sha256"]
    collector["raw_record_sha256"] = raw_trace_record_sha
    collector["receipt"]["payload"] = copy.deepcopy(selected_event["payload"])
    collector["receipt"]["payload_sha256"] = selected_event["payload_sha256"]
    result_payload = {
        key: value
        for key, value in collector.items()
        if key not in {"spec", "result", "raw_trace", "stdout", "stderr"}
    }
    result_path = reference_path(root, collector["result"])
    canonical_write(result_path, result_payload)
    refresh(collector["result"], result_path)
    refresh_switch_receipt_binding(
        root,
        raw,
        request_id=request_id,
        stage="triton_backend_compute_entry",
    )


def mutate_model(root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    move_bridge_span_and_receipt(
        root,
        raw,
        config,
        span_name=config.blue.model_name,
        stage=None,
    )
    sync_bridge_collector_trace(root, raw, config, update_model=True, update_compute=False)


def mutate_compute(root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    move_bridge_span_and_receipt(
        root,
        raw,
        config,
        span_name="compute",
        stage="triton_backend_compute_entry",
    )
    sync_bridge_collector_trace(root, raw, config, update_model=False, update_compute=True)


def mutate_terminal_effect_after_switch(
    root: Path, raw: dict[str, Any], _config: S6BMConfig
) -> None:
    gate = raw["continuity_execution"]["pre_switch_terminal_gate"]
    request_id = str(gate["expected_terminal_request_ids"][0])
    switch_sequence = int(proof(raw)["route_transition_receipt"]["fence_sequence"])

    def mutate_effect(payload: dict[str, Any]) -> None:
        effect = next(
            item for item in payload["effects"] if item.get("idempotency_key") == request_id
        )
        effect["payload"]["durable_commit"]["causal_sequence"] = switch_sequence + 1

    def mutate_event(payload: dict[str, Any]) -> None:
        event = next(
            item
            for item in payload["events"]
            if item.get("request_id") == request_id
            and item.get("event_type") == "durable_terminal_effect_commit"
        )
        event["causal_sequence"] = switch_sequence + 1

    rewrite_reference(root, gate["raw_effect_export"], mutate_effect)
    rewrite_reference(root, gate["raw_event_export"], mutate_event)


def mutate_terminal_effect_missing(root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    gate = raw["continuity_execution"]["pre_switch_terminal_gate"]
    request_id = str(gate["expected_terminal_request_ids"][0])

    def mutate(payload: dict[str, Any]) -> None:
        payload["effects"] = [
            item for item in payload["effects"] if item.get("idempotency_key") != request_id
        ]
        payload["effect_count"] = len(payload["effects"])

    rewrite_reference(root, gate["raw_effect_export"], mutate)


def mutate_normal_old_blue(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    request_id = str(raw["traffic_plan"]["roles"]["normal"][0]["request_id"])
    record = record_for(raw, request_id)
    transition = proof(raw)["route_transition_receipt"]
    record["model_role"] = "blue"
    record["route_generation"] = int(transition["old_route_generation"])


def mutate_callback_before_start(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    execution = raw["continuity_execution"]
    execution["bridge_actor_receipt_gate"]["gate_satisfied_monotonic"] = execution[
        "causal_gate_started_monotonic"
    ]


def mutate_receipt_readback_absent(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    raw["continuity_execution"]["bridge_actor_receipt_gate"]["events"][0]["readback_visible"] = (
        False
    )


def unix_ns_iso(value: int) -> str:
    return (
        datetime.fromtimestamp(value / 1_000_000_000, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def mutate_receipt_after_switch(root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    request_id = required_bridge_id(raw)
    stage = "api_server_handler_entry"
    target_unix_ns, _target_monotonic_ns = after_switch_unix_ns(raw, config)
    timestamp = unix_ns_iso(target_unix_ns)

    def mutate(payload: dict[str, Any]) -> None:
        event = next(
            item
            for item in payload["events"]
            if item.get("request_id") == request_id and item.get("event_type") == stage
        )
        event["database_recorded_at"] = timestamp
        event["captured_at"] = timestamp

    gate_bundle = raw["continuity_execution"]["bridge_actor_receipt_gate"]
    rewrite_reference(root, proof(raw)["causal_event_export"], mutate)
    rewrite_reference(root, gate_bundle["raw_readback_export"], mutate)
    gate = next(
        item
        for item in gate_bundle["events"]
        if item.get("request_id") == request_id and item.get("event_type") == stage
    )
    gate["database_recorded_at"] = timestamp
    gate["readback_at"] = timestamp
    gate_bundle["selected_event_set_sha256"] = canonical_sha256(gate_bundle["events"])


def mutate_required_event_missing(root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    request_id = required_bridge_id(raw)
    stage = "controller_entry"
    gate = raw["continuity_execution"]["bridge_actor_receipt_gate"]
    final_reference = proof(raw)["causal_event_export"]
    pre_switch_reference = gate["raw_readback_export"]
    final_count_before = int(read_json(reference_path(root, final_reference))["event_count"])
    pre_switch_count_before = int(
        read_json(reference_path(root, pre_switch_reference))["event_count"]
    )

    def mutate(payload: dict[str, Any]) -> None:
        payload["events"] = [
            item
            for item in payload["events"]
            if not (item.get("request_id") == request_id and item.get("event_type") == stage)
        ]
        payload["event_count"] = len(payload["events"])

    final_export = rewrite_reference(root, final_reference, mutate)
    pre_switch_export = rewrite_reference(root, pre_switch_reference, mutate)
    gate["events"] = [
        item
        for item in gate["events"]
        if not (item.get("request_id") == request_id and item.get("event_type") == stage)
    ]
    gate["visible_event_count"] = len(gate["events"])
    gate["maximum_visible_causal_sequence"] = max(
        (int(item["causal_sequence"]) for item in gate["events"]),
        default=0,
    )
    gate["selected_event_set_sha256"] = canonical_sha256(gate["events"])
    gate["raw_readback_event_count"] = int(pre_switch_export["event_count"])
    if (
        int(final_export["event_count"]) != final_count_before - 1
        or int(pre_switch_export["event_count"]) != pre_switch_count_before - 1
    ):
        raise ContinuityQualificationError("coherent_event_export_count")


def mutate_terminal_set_missing(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    raw["continuity_execution"]["pre_switch_terminal_gate"]["terminal_records"].pop()


def mutate_terminal_set_extra(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    crossover_id = str(raw["traffic_plan"]["bridge_subsets"]["crossover"]["request_ids"][0])
    crossover = record_for(raw, crossover_id)
    raw["continuity_execution"]["pre_switch_terminal_gate"]["terminal_records"].append(
        {
            "request_id": crossover_id,
            "attempt_id": crossover["attempt_id"],
            "run_id": crossover["run_id"],
        }
    )


def mutate_terminal_identity(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    gate = raw["continuity_execution"]["pre_switch_terminal_gate"]
    gate["terminal_records"][0]["model_role"] = "green"
    gate["terminal_records_sha256"] = canonical_sha256(gate["terminal_records"])
    proof(raw)["route_transition_receipt"]["continuity_terminal_records_sha256"] = gate[
        "terminal_records_sha256"
    ]


def mutate_last_request_green(_root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    gate = raw["continuity_execution"]["pre_switch_terminal_gate"]
    request_id = str(gate["expected_terminal_request_ids"][-1])
    record = record_for(raw, request_id)
    record.update(
        {
            "model_role": "green",
            "model_name": config.green.model_name,
            "model_version": config.green.model_version,
            "artifact_sha256": config.green.artifact_sha256,
            "offered_identity": {
                "model_role": "green",
                "model_name": config.green.model_name,
                "model_version": config.green.model_version,
                "artifact_sha256": config.green.artifact_sha256,
            },
            "output": list(config.green.expected_output),
            "route_generation": int(proof(raw)["route_transition_receipt"]["new_route_generation"]),
        }
    )
    gate_record = next(
        item for item in gate["terminal_records"] if item["request_id"] == request_id
    )
    for key in (
        "model_role",
        "model_name",
        "model_version",
        "artifact_sha256",
        "route_generation",
    ):
        gate_record[key] = record[key]
    gate["terminal_records_sha256"] = canonical_sha256(gate["terminal_records"])


def mutate_pending_missing(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    raw["continuity_execution"]["pre_switch_state"]["pending_crossover_request_ids"].pop()


def mutate_pending_extra(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    raw["continuity_execution"]["pre_switch_state"]["pending_crossover_request_ids"].append(
        "unexpected-crossover"
    )


def mutate_one_not_released(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    proof(raw)["route_transition_receipt"]["released_crossover_request_ids"].pop()


def mutate_release_before_route(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    transition = proof(raw)["route_transition_receipt"]
    transition["crossover_release_monotonic_ns"] = int(transition["route_applied_monotonic_ns"]) - 1


def mutate_release_before_receipts(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    gate = raw["continuity_execution"]["bridge_actor_receipt_gate"]
    proof(raw)["route_transition_receipt"]["crossover_release_monotonic_ns"] = (
        int(float(gate["gate_satisfied_monotonic"]) * 1e9) - 1
    )


def mutate_timeout_cleanup(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    raw["cleanup"]["controller_pending_crossovers_zero"] = False


def _online_records(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gate = raw["continuity_execution"]["pre_switch_terminal_gate"]
    records = gate["online_response_records"]
    if len(records) != 39:
        raise ContinuityQualificationError("online_terminal_exact_set")
    return gate, records


def _refresh_online_records(gate: dict[str, Any], records: list[dict[str, Any]]) -> None:
    gate["online_response_records_sha256"] = canonical_sha256(records)


def mutate_online_wrong_response_id(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    gate, records = _online_records(raw)
    records[0]["request_id"] = "attacker-substituted-request"
    _refresh_online_records(gate, records)


def mutate_online_stale_generation(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    gate, records = _online_records(raw)
    records[0]["route_generation"] = int(records[0]["route_generation"]) - 1
    _refresh_online_records(gate, records)


def mutate_online_wrong_artifact_version(
    _root: Path, raw: dict[str, Any], _config: S6BMConfig
) -> None:
    gate, records = _online_records(raw)
    records[0]["model_version"] = "attacker-version"
    records[0]["artifact_sha256"] = "f" * 64
    _refresh_online_records(gate, records)


def mutate_online_durable_readback_absent(
    _root: Path, raw: dict[str, Any], _config: S6BMConfig
) -> None:
    gate, records = _online_records(raw)
    records[0]["durable_effect"]["readback_visible"] = False
    _refresh_online_records(gate, records)


def mutate_terminal_fence_hash(_root: Path, raw: dict[str, Any], _config: S6BMConfig) -> None:
    proof(raw)["route_transition_receipt"]["continuity_terminal_records_sha256"] = "f" * 64


MUTATIONS: dict[str, tuple[str, Mutation, str]] = {
    "bridge_server_start_after_switch_fence": (
        CASE_CONTRACT[0][1],
        mutate_server,
        "causal",
    ),
    "bridge_controller_start_after_switch_fence": (
        CASE_CONTRACT[1][1],
        mutate_controller,
        "causal",
    ),
    "pre_switch_terminal_effect_after_switch_fence": (
        CASE_CONTRACT[2][1],
        mutate_terminal_effect_after_switch,
        "causal",
    ),
    "pre_switch_terminal_effect_missing": (
        CASE_CONTRACT[3][1],
        mutate_terminal_effect_missing,
        "causal",
    ),
    "normal_request_bound_to_old_blue": (
        CASE_CONTRACT[4][1],
        mutate_normal_old_blue,
        "continuity",
    ),
    "callback_before_required_actor_start": (
        CASE_CONTRACT[5][1],
        mutate_callback_before_start,
        "causal",
    ),
    "bridge_actor_receipt_readback_absent": (
        CASE_CONTRACT[6][1],
        mutate_receipt_readback_absent,
        "causal",
    ),
    "bridge_actor_receipt_commit_after_switch": (
        CASE_CONTRACT[7][1],
        mutate_receipt_after_switch,
        "causal",
    ),
    "bridge_required_event_missing": (
        CASE_CONTRACT[8][1],
        mutate_required_event_missing,
        "causal",
    ),
    "pre_switch_terminal_set_missing": (
        CASE_CONTRACT[9][1],
        mutate_terminal_set_missing,
        "continuity",
    ),
    "pre_switch_terminal_set_extra": (
        CASE_CONTRACT[10][1],
        mutate_terminal_set_extra,
        "continuity",
    ),
    "pre_switch_terminal_identity_mismatch": (
        CASE_CONTRACT[11][1],
        mutate_terminal_identity,
        "continuity",
    ),
    "all_submitted_last_request_green_routed": (
        CASE_CONTRACT[12][1],
        mutate_last_request_green,
        "continuity",
    ),
    "pending_crossover_set_missing": (
        CASE_CONTRACT[13][1],
        mutate_pending_missing,
        "continuity",
    ),
    "pending_crossover_set_extra": (
        CASE_CONTRACT[14][1],
        mutate_pending_extra,
        "continuity",
    ),
    "one_crossover_not_released": (
        CASE_CONTRACT[15][1],
        mutate_one_not_released,
        "continuity",
    ),
    "release_before_route_applied": (
        CASE_CONTRACT[16][1],
        mutate_release_before_route,
        "continuity",
    ),
    "release_before_receipt_gate": (
        CASE_CONTRACT[17][1],
        mutate_release_before_receipts,
        "continuity",
    ),
    "crossover_timeout_cleanup_residue": (
        CASE_CONTRACT[18][1],
        mutate_timeout_cleanup,
        "success",
    ),
    "pre_switch_online_wrong_response_id": (
        CASE_CONTRACT[19][1],
        mutate_online_wrong_response_id,
        "continuity",
    ),
    "pre_switch_online_stale_generation": (
        CASE_CONTRACT[20][1],
        mutate_online_stale_generation,
        "continuity",
    ),
    "pre_switch_online_wrong_artifact_version": (
        CASE_CONTRACT[21][1],
        mutate_online_wrong_artifact_version,
        "continuity",
    ),
    "pre_switch_online_durable_readback_absent": (
        CASE_CONTRACT[22][1],
        mutate_online_durable_readback_absent,
        "continuity",
    ),
    "pre_switch_terminal_fence_hash_mismatch": (
        CASE_CONTRACT[23][1],
        mutate_terminal_fence_hash,
        "continuity",
    ),
}


def run_case(
    source_root: Path,
    raw: dict[str, Any],
    config: S6BMConfig,
    case_id: str,
) -> dict[str, Any]:
    expected_reason, mutate, validator = MUTATIONS[case_id]
    with tempfile.TemporaryDirectory(prefix=f"s6bm-continuity-{case_id}-") as directory:
        root = Path(directory) / "bundle"
        shutil.copytree(source_root, root)
        candidate = copy.deepcopy(raw)
        mutate(root, candidate, config)
        observed = "validator_fail_open"
        try:
            if validator == "continuity":
                project_continuity_contract(candidate, config)
            elif validator == "success":
                project_success_attempt(candidate, config)
            else:
                validate_causal_bundle(root, candidate, config, compare_projection=False)
        except (S6BMCausalError, S6BMRuntimeError, KeyError, TypeError, ValueError) as exc:
            observed = str(exc)
        return {
            "case_id": case_id,
            "expected_reason": expected_reason,
            "observed_reason": observed,
            "rejected": observed == expected_reason,
        }


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw = read_json(args.qualification)
    config = S6BMConfig.from_path(args.config)
    if raw.get("credit") != "non_credit" or raw.get("acceptance_credit") is not False:
        raise ContinuityQualificationError("continuity_qualification_credit")
    positive_runtime = project_success_attempt(raw, config)
    positive_causal = validate_causal_bundle(
        args.private_root, raw, config, compare_projection=True
    )
    case_ids = tuple(case_id for case_id, _reason in CASE_CONTRACT)
    if (
        set(MUTATIONS) != set(case_ids)
        or canonical_sha256(CASE_CONTRACT) != CASE_CONTRACT_SHA256
        or canonical_sha256(SUPERSEDED_CASE_CONTRACT) != SUPERSEDED_CASE_CONTRACT_SHA256
        or canonical_sha256(HISTORICAL_CASE_CONTRACT) != HISTORICAL_CASE_CONTRACT_SHA256
        or canonical_sha256(PREVIOUS_CASE_CONTRACT) != PREVIOUS_CASE_CONTRACT_SHA256
    ):
        raise ContinuityQualificationError("continuity_mutation_case_contract")
    results = [run_case(args.private_root, raw, config, case_id) for case_id in case_ids]
    passed = all(item["rejected"] is True for item in results)
    payload = {
        "schema_version": "evm.s8_v4.s6bm_continuity_mutations.v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "acceptance_credit": False,
        "credit": "non_credit",
        "reviewer_sign_off": "pending",
        "source_revision": raw.get("source_revision"),
        "validator_revision": git_text("rev-parse", "HEAD"),
        "validator_tree": git_text("rev-parse", "HEAD^{tree}"),
        "config_sha256": sha256_file(args.config),
        "qualification_sha256": sha256_file(args.qualification),
        "case_contract": {
            "count": len(CASE_CONTRACT),
            "sha256": CASE_CONTRACT_SHA256,
            "cases": [
                {"case_id": case_id, "expected_reason": reason} for case_id, reason in CASE_CONTRACT
            ],
            "historical_prefix_count": len(HISTORICAL_CASE_CONTRACT),
            "historical_prefix_sha256": HISTORICAL_CASE_CONTRACT_SHA256,
            "superseded_count": len(SUPERSEDED_CASE_CONTRACT),
            "superseded_sha256": SUPERSEDED_CASE_CONTRACT_SHA256,
        },
        "positive": {
            "count": 1,
            "runtime_projection_sha256": canonical_sha256(positive_runtime),
            "causal_projection_sha256": canonical_sha256(positive_causal),
            "passed": True,
        },
        "negative": {
            "count": len(results),
            "rejected": sum(item["rejected"] is True for item in results),
            "cases": results,
        },
        "passed": passed,
        "accepted_matrix_started": False,
        "claim_boundary": config.claim_boundary,
    }
    canonical_write(args.output, payload)
    if not passed:
        raise ContinuityQualificationError("continuity_mutation_fail_open")
    return payload


def main() -> int:
    args = parse_args()
    result = run(args)
    print(canonical({"passed": result["passed"], "output_sha256": sha256_file(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

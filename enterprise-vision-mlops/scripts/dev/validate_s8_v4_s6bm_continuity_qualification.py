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
        "bridge_model_start_after_switch_fence",
        "s6bm_v4_continuity_model_after_switch",
    ),
    (
        "bridge_compute_start_after_switch_fence",
        "s6bm_v4_continuity_compute_after_switch",
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
)
CASE_CONTRACT_SHA256 = "c42a6245d1e48152d06c6f1bd31c7fca8de58f201129c99bb7c59abe9356d4d7"


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
    matches = [
        item for item in raw["request_records"] if item.get("request_id") == request_id
    ]
    if len(matches) != 1:
        raise ContinuityQualificationError("request_record_identity")
    return matches[0]


def span_entry(
    payload: dict[str, Any], *, trace_id: str, name: str
) -> dict[str, Any]:
    matches = [
        entry
        for entry in payload["entries"]
        if entry["span"].get("traceId") == trace_id
        and entry["span"].get("name") == name
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

    rewrite_reference(root, proof(raw)["causal_event_export"], mutate)
    gate_events = raw["continuity_execution"]["bridge_actor_receipt_gate"]["events"]
    gate = next(
        item
        for item in gate_events
        if item.get("request_id") == request_id and item.get("event_type") == stage
    )
    gate["payload_sha256"] = selected["payload_sha256"]
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
    compute_entry = span_entry(
        trace_payload, trace_id=str(record["trace_id"]), name="compute"
    )
    collectors = raw["continuity_execution"]["bridge_triton_start_receipts"]
    collector = next(item for item in collectors if item.get("request_id") == request_id)
    raw_trace_path = reference_path(root, collector["raw_trace"])
    bridge_trace = read_json(raw_trace_path)
    if update_model:
        bridge_trace["raw_model_entry"] = copy.deepcopy(model_entry)
    if update_compute:
        bridge_trace["compute_start_unix_ns"] = int(
            compute_entry["span"]["startTimeUnixNano"]
        )
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
        selected_event["payload"]["raw_trace_artifact_sha256"] = collector[
            "raw_trace"
        ]["sha256"]
        selected_event["payload"]["raw_trace_record_sha256"] = raw_trace_record_sha
        selected_event["payload_sha256"] = canonical_sha256(selected_event["payload"])

    rewrite_reference(root, proof(raw)["causal_event_export"], mutate_event)
    gate = next(
        item
        for item in raw["continuity_execution"]["bridge_actor_receipt_gate"]["events"]
        if item.get("request_id") == request_id
        and item.get("event_type") == "triton_backend_compute_entry"
    )
    gate["payload_sha256"] = selected_event["payload_sha256"]
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


def mutate_model(root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    move_bridge_span_and_receipt(
        root,
        raw,
        config,
        span_name=config.blue.model_name,
        stage=None,
    )
    sync_bridge_collector_trace(
        root, raw, config, update_model=True, update_compute=False
    )


def mutate_compute(root: Path, raw: dict[str, Any], config: S6BMConfig) -> None:
    move_bridge_span_and_receipt(
        root,
        raw,
        config,
        span_name="compute",
        stage="triton_backend_compute_entry",
    )
    sync_bridge_collector_trace(
        root, raw, config, update_model=False, update_compute=True
    )


def mutate_normal_old_blue(
    _root: Path, raw: dict[str, Any], _config: S6BMConfig
) -> None:
    request_id = str(raw["traffic_plan"]["roles"]["normal"][0]["request_id"])
    record = record_for(raw, request_id)
    transition = proof(raw)["route_transition_receipt"]
    record["model_role"] = "blue"
    record["route_generation"] = int(transition["old_route_generation"])


def mutate_callback_before_start(
    _root: Path, raw: dict[str, Any], _config: S6BMConfig
) -> None:
    execution = raw["continuity_execution"]
    execution["bridge_actor_receipt_gate"]["gate_satisfied_monotonic"] = execution[
        "causal_gate_started_monotonic"
    ]


def mutate_receipt_readback_absent(
    _root: Path, raw: dict[str, Any], _config: S6BMConfig
) -> None:
    raw["continuity_execution"]["bridge_actor_receipt_gate"]["events"][0][
        "readback_visible"
    ] = False


def unix_ns_iso(value: int) -> str:
    return (
        datetime.fromtimestamp(value / 1_000_000_000, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def mutate_receipt_after_switch(
    root: Path, raw: dict[str, Any], config: S6BMConfig
) -> None:
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

    rewrite_reference(root, proof(raw)["causal_event_export"], mutate)
    gate = next(
        item
        for item in raw["continuity_execution"]["bridge_actor_receipt_gate"]["events"]
        if item.get("request_id") == request_id and item.get("event_type") == stage
    )
    gate["database_recorded_at"] = timestamp
    gate["readback_at"] = timestamp


def mutate_required_event_missing(
    root: Path, raw: dict[str, Any], _config: S6BMConfig
) -> None:
    request_id = required_bridge_id(raw)
    stage = "controller_entry"

    def mutate(payload: dict[str, Any]) -> None:
        payload["events"] = [
            item
            for item in payload["events"]
            if not (
                item.get("request_id") == request_id and item.get("event_type") == stage
            )
        ]
        payload["event_count"] = len(payload["events"])

    rewrite_reference(root, proof(raw)["causal_event_export"], mutate)
    gate = raw["continuity_execution"]["bridge_actor_receipt_gate"]
    gate["events"] = [
        item
        for item in gate["events"]
        if not (item.get("request_id") == request_id and item.get("event_type") == stage)
    ]
    gate["visible_event_count"] = len(gate["events"])


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
    "bridge_model_start_after_switch_fence": (
        CASE_CONTRACT[2][1],
        mutate_model,
        "causal",
    ),
    "bridge_compute_start_after_switch_fence": (
        CASE_CONTRACT[3][1],
        mutate_compute,
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
                {"case_id": case_id, "expected_reason": reason}
                for case_id, reason in CASE_CONTRACT
            ],
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

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.s6bm_causal import (  # noqa: E402
    S6BMCausalError,
    validate_causal_bundle,
)
from evm.scale_validation.s6bm_observability import (  # noqa: E402
    S6BMObservabilityError,
    validate_observability_bundle,
)
from evm.scale_validation.s6bm_runtime import (  # noqa: E402
    S6BMConfig,
    S6BMRuntimeError,
    canonical,
    canonical_sha256,
    sha256_file,
)


HISTORICAL_MUTATION_SHA256 = "9ae31c2012e276cebcbfe32e58481df57162519f02fb42e1ea48dbd88e348e8d"
HISTORICAL_CASE_IDS = (
    "loss",
    "duplicate_request_identity",
    "wrong_model_digest",
    "trace_gap",
    "phase_order",
    "premature_drain",
    "rollback_mismatch",
    "illegal_owner_overlap",
    "cleanup_residue",
    "physical_model_residue",
    "wrong_digest_fail_open",
    "orphan",
    "readiness_route_switch",
    "canary_not_observed",
    "vram_not_over_capacity",
    "direct_metrics_absent",
    "prometheus_counts_zero",
    "duplicate_repetition_full_analysis",
    "repetition_out_of_contract",
    "offered_identity_substitution",
    "unbound_trace_id",
    "trace_artifact_absent",
    "metric_label_substitution",
    "attempt_mix",
    "hold_completion_before_switch",
    "unload_before_last_blue_completion",
    "unload_completed_before_last_blue_completion",
    "span_request_effect_timeline_mismatch",
)


class StrictV4QualificationError(RuntimeError):
    pass


Mutation = Callable[[Path, dict[str, Any]], None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the S6B-M Strict V4 qualification")
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_s6bm_blue_green_v4.toml",
    )
    parser.add_argument(
        "--historical-mutations",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-mutation-validation-v3.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise StrictV4QualificationError(f"noncanonical_json:{path.name}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise StrictV4QualificationError(f"json_object_required:{path.name}")
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
        raise StrictV4QualificationError(f"git_command:{' '.join(arguments)}")
    return result.stdout.strip()


def _proof(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw.get("causal_proof")
    return value if isinstance(value, dict) else raw


def _reference_path(root: Path, reference: dict[str, Any]) -> Path:
    return root / str(reference["path"])


def _refresh(reference: dict[str, Any], path: Path) -> None:
    reference["bytes"] = path.stat().st_size
    reference["sha256"] = sha256_file(path)


def _rewrite_reference(
    root: Path,
    reference: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    path = _reference_path(root, reference)
    payload = read_json(path)
    mutate(payload)
    canonical_write(path, payload)
    _refresh(reference, path)
    return payload


def _effect_receipt(raw: dict[str, Any]) -> dict[str, Any]:
    return raw["request_records"][0]["durable_effect"]


def _phase(raw: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in raw["phase_timeline"] if item["phase"] == name)


def _rehash_anchor(anchor: dict[str, Any]) -> None:
    anchor["anchor_hash"] = canonical_sha256(
        {key: value for key, value in anchor.items() if key != "anchor_hash"}
    )


def _rehash_runner_anchor_chain(raw: dict[str, Any]) -> None:
    previous: str | None = None
    for item in sorted(raw["phase_timeline"], key=lambda value: value["clock_anchor"]["sequence"]):
        anchor = item["clock_anchor"]
        anchor["previous_anchor_hash"] = previous
        _rehash_anchor(anchor)
        previous = anchor["anchor_hash"]


def _sync_collector_observation(root: Path, raw: dict[str, Any]) -> None:
    receipt = _proof(raw)["triton_start_receipt"]
    observation = copy.deepcopy(receipt.get("collector_observation", {}))
    event = _rewrite_start_event(
        root,
        raw,
        "triton_backend_compute_entry",
        lambda payload: payload.update(
            collector_observation=observation,
            collector_nonce=str(observation.get("anchor_nonce", "")),
            collector_source_identity=str(observation.get("source_identity", "")),
        ),
    )
    receipt["receipt"]["payload"] = copy.deepcopy(event["payload"])
    receipt["receipt"]["payload_sha256"] = event["payload_sha256"]
    projected = {
        key: value
        for key, value in receipt.items()
        if key not in {"spec", "result", "raw_trace", "stdout", "stderr"}
    }
    result_path = _reference_path(root, receipt["result"])
    canonical_write(result_path, projected)
    _refresh(receipt["result"], result_path)


def _route_transition_receipt(raw: dict[str, Any]) -> dict[str, Any]:
    return _proof(raw)["route_transition_receipt"]


def _rewrite_observed_transition(
    root: Path,
    raw: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    record = raw["request_records"][0]
    receipt = record["durable_effect"]
    changed = copy.deepcopy(receipt["observed_transition"])
    mutate(changed)
    receipt["observed_transition"] = copy.deepcopy(changed)
    effect_event_sha256 = ""

    def mutate_events(payload: dict[str, Any]) -> None:
        nonlocal effect_event_sha256
        event = next(
            item
            for item in payload["events"]
            if item["event_type"] == "durable_terminal_effect_commit"
        )
        event["payload"]["observed_transition"] = copy.deepcopy(changed)
        _rehash_event(event)
        effect_event_sha256 = str(event["payload_sha256"])

    _rewrite_events(root, raw, mutate_events)
    receipt["causal_payload_sha256"] = effect_event_sha256
    effect_payload: dict[str, Any] = {}

    def mutate_effects(payload: dict[str, Any]) -> None:
        nonlocal effect_payload
        effect_payload = payload["effects"][0]["payload"]
        effect_payload["observed_transition"] = copy.deepcopy(changed)
        effect_payload["durable_commit"]["observed_transition"] = copy.deepcopy(changed)
        effect_payload["durable_commit"]["causal_payload_sha256"] = effect_event_sha256

    _rewrite_reference(root, _proof(raw)["durable_effect_export"], mutate_effects)
    receipt["stored_payload_sha256"] = canonical_sha256(effect_payload)


def _sync_switch_dependencies(root: Path, raw: dict[str, Any]) -> None:
    events = read_json(_reference_path(root, _proof(raw)["causal_event_export"]))
    switch = next(
        item for item in events["events"] if item["event_type"] == "blue_to_green_switch_commit"
    )
    route = _route_transition_receipt(raw)
    fence = route["fence_receipt"]
    fence["payload"] = copy.deepcopy(switch["payload"])
    fence["payload_sha256"] = switch["payload_sha256"]
    fence["fence_payload_sha256"] = switch["payload_sha256"]
    route["fence_payload_sha256"] = switch["payload_sha256"]
    route["fence_receipt_sha256"] = canonical_sha256(fence)
    expected = {
        "schema_version": "evm.s6bm.observed_transition.v1",
        "transition_id": switch["payload"]["transition_id"],
        "fence_id": switch["payload"]["fence_id"],
        "fence_sequence": switch["causal_sequence"],
        "fence_transaction_id": switch["transaction_id"],
        "fence_payload_sha256": switch["payload_sha256"],
        "attempt_id": switch["attempt_id"],
        "run_id": switch["run_id"],
        "request_id": switch["request_id"],
        "old_route_generation": switch["payload"]["old_route_generation"],
        "new_route_generation": switch["payload"]["new_route_generation"],
        "source_payload_sha256": switch["payload"]["source_payload_sha256"],
        "cell_id": switch["payload"]["cell_id"],
        "replica_id": switch["payload"]["replica_id"],
        "database_recorded_at": switch["database_recorded_at"],
    }
    _rewrite_observed_transition(root, raw, lambda value: value.update(expected))


def _rewrite_trace(
    root: Path,
    raw: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    reference = raw["observability"]["artifacts"]["trace_export"]
    return _rewrite_reference(root, reference, mutate)


def _rewrite_events(
    root: Path,
    raw: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    reference = _proof(raw)["causal_event_export"]
    return _rewrite_reference(root, reference, mutate)


def _rehash_event(event: dict[str, Any]) -> None:
    event["payload_sha256"] = canonical_sha256(event["payload"])


def _rewrite_start_event(
    root: Path,
    raw: dict[str, Any],
    stage: str,
    mutate_payload: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    selected: dict[str, Any] = {}

    def mutate(export: dict[str, Any]) -> None:
        nonlocal selected
        selected = next(item for item in export["events"] if item["event_type"] == stage)
        mutate_payload(selected["payload"])
        _rehash_event(selected)
        switch = next(
            item for item in export["events"] if item["event_type"] == "blue_to_green_switch_commit"
        )
        switch["payload"]["receipt_payload_sha256"][stage] = selected["payload_sha256"]
        _rehash_event(switch)

    _rewrite_events(root, raw, mutate)
    _sync_switch_dependencies(root, raw)
    return selected


def _switch_unix_ns(raw: dict[str, Any]) -> int:
    return int(
        _proof(raw)["route_transition_receipt"]["route_applied_actor"][
            "actor_start_unix_ns"
        ]
    )


def _set_span_start_after_switch(
    root: Path, raw: dict[str, Any], span_name: str, offset_ms: int
) -> None:
    trace_id = raw["request_records"][0]["trace_id"]
    start = _switch_unix_ns(raw) + offset_ms * 1_000_000

    def mutate(payload: dict[str, Any]) -> None:
        span = next(
            entry["span"]
            for entry in payload["entries"]
            if entry["span"].get("traceId") == trace_id and entry["span"].get("name") == span_name
        )
        duration = max(1_000, int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]))
        span["startTimeUnixNano"] = str(start)
        span["endTimeUnixNano"] = str(start + duration)

    _rewrite_trace(root, raw, mutate)


def _server_start_after_switch(root: Path, raw: dict[str, Any]) -> None:
    _set_span_start_after_switch(
        root,
        raw,
        "POST /control-panel/v1/scenario-workloads/triton-blue-green/predict",
        1,
    )


def _controller_start_after_switch(root: Path, raw: dict[str, Any]) -> None:
    _set_span_start_after_switch(root, raw, "s6bm.controller.predict", 2)


def _actor_and_span_start_after_switch(
    root: Path,
    raw: dict[str, Any],
    *,
    stage: str,
    span_name: str,
    offset_ms: int,
) -> None:
    _set_span_start_after_switch(root, raw, span_name, offset_ms)
    switch_monotonic = int(_route_transition_receipt(raw)["route_applied_monotonic_ns"])
    start_unix = _switch_unix_ns(raw) + offset_ms * 1_000_000
    local_start = switch_monotonic + offset_ms * 1_000_000
    _rewrite_start_event(
        root,
        raw,
        stage,
        lambda payload: payload.update(
            actor_start_unix_ns=start_unix,
            monotonic_before_ns=local_start,
            monotonic_after_ns=local_start + 500,
        ),
    )


def _server_actor_and_span_after_switch(root: Path, raw: dict[str, Any]) -> None:
    _actor_and_span_start_after_switch(
        root,
        raw,
        stage="api_server_handler_entry",
        span_name="POST /control-panel/v1/scenario-workloads/triton-blue-green/predict",
        offset_ms=1,
    )


def _controller_actor_and_span_after_switch(root: Path, raw: dict[str, Any]) -> None:
    _actor_and_span_start_after_switch(
        root,
        raw,
        stage="controller_entry",
        span_name="s6bm.controller.predict",
        offset_ms=2,
    )


def _model_start_after_switch(root: Path, raw: dict[str, Any]) -> None:
    trace_id = raw["request_records"][0]["trace_id"]
    model_name = raw["request_records"][0]["model_name"]
    start = _switch_unix_ns(raw) + 3_000_000

    def mutate_trace(payload: dict[str, Any]) -> None:
        model_entry = next(
            entry
            for entry in payload["entries"]
            if entry["span"].get("traceId") == trace_id and entry["span"].get("name") == model_name
        )
        span = model_entry["span"]
        duration = max(1_000, int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]))
        span["startTimeUnixNano"] = str(start)
        span["endTimeUnixNano"] = str(start + duration)

    trace = _rewrite_trace(root, raw, mutate_trace)
    model_entry = next(
        entry
        for entry in trace["entries"]
        if entry["span"].get("traceId") == trace_id and entry["span"].get("name") == model_name
    )
    receipt = _proof(raw)["triton_start_receipt"]

    def mutate_triton(payload: dict[str, Any]) -> None:
        payload["raw_model_entry"] = model_entry

    triton_trace = _rewrite_reference(root, receipt["raw_trace"], mutate_triton)
    receipt["raw_trace_sha256"] = receipt["raw_trace"]["sha256"]
    receipt["raw_record_sha256"] = canonical_sha256(triton_trace)

    event = _rewrite_start_event(
        root,
        raw,
        "triton_backend_compute_entry",
        lambda payload: payload.update(
            raw_trace_artifact_sha256=receipt["raw_trace"]["sha256"],
            raw_trace_record_sha256=canonical_sha256(triton_trace),
        ),
    )
    receipt["receipt"]["payload"] = copy.deepcopy(event["payload"])
    receipt["receipt"]["payload_sha256"] = event["payload_sha256"]

    projected = {
        key: value
        for key, value in receipt.items()
        if key not in {"spec", "result", "raw_trace", "stdout", "stderr"}
    }
    result_path = _reference_path(root, receipt["result"])
    canonical_write(result_path, projected)
    _refresh(receipt["result"], result_path)


def _model_actor_and_spans_after_switch(root: Path, raw: dict[str, Any]) -> None:
    trace_id = raw["request_records"][0]["trace_id"]
    model_name = raw["request_records"][0]["model_name"]
    _set_span_start_after_switch(root, raw, model_name, 3)
    _set_span_start_after_switch(root, raw, "compute", 4)
    trace_reference = raw["observability"]["artifacts"]["trace_export"]
    trace = read_json(_reference_path(root, trace_reference))
    model_entry = next(
        entry
        for entry in trace["entries"]
        if entry["span"].get("traceId") == trace_id
        and entry["span"].get("name") == model_name
    )
    compute_entry = next(
        entry
        for entry in trace["entries"]
        if entry["span"].get("traceId") == trace_id
        and entry["span"].get("name") == "compute"
    )
    receipt = _proof(raw)["triton_start_receipt"]

    def mutate_triton(payload: dict[str, Any]) -> None:
        payload["raw_model_entry"] = copy.deepcopy(model_entry)
        payload["raw_compute_entry"] = copy.deepcopy(compute_entry)
        payload["compute_start_unix_ns"] = int(compute_entry["span"]["startTimeUnixNano"])

    triton_trace = _rewrite_reference(root, receipt["raw_trace"], mutate_triton)
    receipt["raw_trace_sha256"] = receipt["raw_trace"]["sha256"]
    receipt["raw_record_sha256"] = canonical_sha256(triton_trace)
    compute_start = int(compute_entry["span"]["startTimeUnixNano"])
    event = _rewrite_start_event(
        root,
        raw,
        "triton_backend_compute_entry",
        lambda payload: payload.update(
            actor_start_unix_ns=compute_start,
            raw_trace_artifact_sha256=receipt["raw_trace"]["sha256"],
            raw_trace_record_sha256=canonical_sha256(triton_trace),
        ),
    )
    receipt["receipt"]["payload"] = copy.deepcopy(event["payload"])
    receipt["receipt"]["payload_sha256"] = event["payload_sha256"]
    projected = {
        key: value
        for key, value in receipt.items()
        if key not in {"spec", "result", "raw_trace", "stdout", "stderr"}
    }
    result_path = _reference_path(root, receipt["result"])
    canonical_write(result_path, projected)
    _refresh(receipt["result"], result_path)


def _clock_self_reference(_root: Path, raw: dict[str, Any]) -> None:
    nonce = raw["request_records"][0]["request_nonce"]
    prefix = str(_phase(raw, "blue_only")["clock_anchor"]["source_identity"]).rsplit(":", 1)[0]
    for item in raw["phase_timeline"]:
        anchor = item["clock_anchor"]
        anchor["anchor_nonce"] = nonce
        anchor["source_identity"] = f"{prefix}:{nonce}"
    _rehash_runner_anchor_chain(raw)


def _clock_drift(root: Path, raw: dict[str, Any]) -> None:
    runner = [item["clock_anchor"] for item in raw["phase_timeline"]]
    collector = _proof(raw)["triton_start_receipt"]["collector_observation"]
    points = [*runner, collector]
    origin = min(
        (int(item["monotonic_before_ns"]) + int(item["monotonic_after_ns"])) // 2
        for item in points
    )
    for item in points:
        midpoint = (
            int(item["monotonic_before_ns"]) + int(item["monotonic_after_ns"])
        ) // 2
        item["unix_ns"] = int(item["unix_ns"]) + ((midpoint - origin) * 101) // 1_000_000
    _rehash_runner_anchor_chain(raw)
    _rehash_anchor(collector)
    _sync_collector_observation(root, raw)


def _affine_mutation_metrics(
    raw: dict[str, Any], *, anchor_index: int, unix_delta_ns: int
) -> tuple[Fraction, Fraction, Fraction]:
    anchors = [item["clock_anchor"] for item in raw["phase_timeline"]]
    collector = _proof(raw)["triton_start_receipt"]["collector_observation"]
    samples: list[tuple[Fraction, Fraction]] = []
    for index, anchor in enumerate([*anchors, collector]):
        midpoint = Fraction(
            int(anchor["monotonic_before_ns"]) + int(anchor["monotonic_after_ns"]),
            2,
        )
        unix_ns = int(anchor["unix_ns"])
        if index == anchor_index:
            unix_ns += unix_delta_ns
        samples.append((midpoint, Fraction(unix_ns)))

    count = len(samples)
    x_mean = sum((x for x, _ in samples), Fraction(0)) / count
    y_mean = sum((y for _, y in samples), Fraction(0)) / count
    denominator = sum(((x - x_mean) ** 2 for x, _ in samples), Fraction(0))
    slope = (
        sum(((x - x_mean) * (y - y_mean) for x, y in samples), Fraction(0))
        / denominator
    )
    intercept = y_mean - slope * x_mean
    residuals = [y - (intercept + slope * x) for x, y in samples]
    ordered = sorted(zip((x for x, _ in samples), residuals, strict=True))
    max_step = max(
        (
            abs(right_residual - left_residual)
            for (_, left_residual), (_, right_residual) in zip(
                ordered, ordered[1:], strict=False
            )
        ),
        default=Fraction(0),
    )
    return (
        max((abs(value) for value in residuals), default=Fraction(0)),
        max_step,
        abs(slope - 1) * 1_000_000,
    )


def _clock_residual_over_bound(_root: Path, raw: dict[str, Any]) -> None:
    residual_bound = Fraction(1_000_000)
    step_bound = Fraction(2_000_000)
    drift_bound = Fraction(100)
    candidates: list[tuple[Fraction, int, int]] = []
    anchors = [item["clock_anchor"] for item in raw["phase_timeline"]]
    for anchor_index in range(len(anchors)):
        for direction in (1, -1):
            low = 0
            high = 8_000_000
            high_metrics = _affine_mutation_metrics(
                raw,
                anchor_index=anchor_index,
                unix_delta_ns=direction * high,
            )
            if high_metrics[0] <= residual_bound:
                continue
            while low + 1 < high:
                midpoint = (low + high) // 2
                metrics = _affine_mutation_metrics(
                    raw,
                    anchor_index=anchor_index,
                    unix_delta_ns=direction * midpoint,
                )
                if metrics[0] > residual_bound:
                    high = midpoint
                else:
                    low = midpoint
            residual, step, drift = _affine_mutation_metrics(
                raw,
                anchor_index=anchor_index,
                unix_delta_ns=direction * high,
            )
            if step <= step_bound and drift <= drift_bound:
                candidates.append((residual, anchor_index, direction * high))
    if not candidates:
        raise StrictV4QualificationError("clock_residual_mutation_not_constructible")
    _, selected_index, selected_delta = min(
        candidates,
        key=lambda item: (item[0], item[1], item[2]),
    )
    anchors[selected_index]["unix_ns"] += selected_delta
    _rehash_runner_anchor_chain(raw)


def _clock_step_over_bound(_root: Path, raw: dict[str, Any]) -> None:
    for item in raw["phase_timeline"]:
        if int(item["clock_anchor"]["sequence"]) >= 6:
            item["clock_anchor"]["unix_ns"] += 3_000_000
    _rehash_runner_anchor_chain(raw)


def _collector_anchor_missing(root: Path, raw: dict[str, Any]) -> None:
    _proof(raw)["triton_start_receipt"]["collector_observation"] = {}
    _sync_collector_observation(root, raw)


def _clock_envelope_disjoint(_root: Path, raw: dict[str, Any]) -> None:
    for item in raw["phase_timeline"]:
        item["clock_anchor"]["unix_ns"] += 5_000_000
    _rehash_runner_anchor_chain(raw)


def _clock_source_substitution(_root: Path, raw: dict[str, Any]) -> None:
    _phase(raw, "blue_only")["clock_anchor"]["source_identity"] = "runner:substituted"
    _rehash_runner_anchor_chain(raw)


def _phase_clock_mismatch(_root: Path, raw: dict[str, Any]) -> None:
    _phase(raw, "green_active")["monotonic_seconds"] -= 1.0


def _transition_receipt_absent(_root: Path, raw: dict[str, Any]) -> None:
    _proof(raw).pop("route_transition_receipt", None)


def _transition_source_substitution(_root: Path, raw: dict[str, Any]) -> None:
    _route_transition_receipt(raw)["source_revision"] = "f" * 40


def _transition_id_mismatch(_root: Path, raw: dict[str, Any]) -> None:
    _route_transition_receipt(raw)["transition_id"] = "f" * 64


def _transition_generation_mismatch(_root: Path, raw: dict[str, Any]) -> None:
    _route_transition_receipt(raw)["state_readback"]["generation"] += 1


def _transition_fence_mismatch(_root: Path, raw: dict[str, Any]) -> None:
    _route_transition_receipt(raw)["fence_sequence"] += 1


def _transition_timestamp_inversion(_root: Path, raw: dict[str, Any]) -> None:
    receipt = _route_transition_receipt(raw)
    receipt["route_applied_monotonic_ns"] = (
        int(receipt["fence_readback_finished_monotonic_ns"]) - 1
    )


def _transition_readback_absent(_root: Path, raw: dict[str, Any]) -> None:
    _route_transition_receipt(raw)["fence_receipt"]["readback_visible"] = False


def _effect_transition_readback_absent(_root: Path, raw: dict[str, Any]) -> None:
    _effect_receipt(raw)["transition_readback_visible"] = False


def _wrong_fence_sequence(root: Path, raw: dict[str, Any]) -> None:
    _rewrite_observed_transition(
        root,
        raw,
        lambda value: value.update(fence_sequence=int(value["fence_sequence"]) + 1),
    )


def _reused_fence_identity(root: Path, raw: dict[str, Any]) -> None:
    _rewrite_observed_transition(
        root,
        raw,
        lambda value: value.update(fence_id="e" * 64),
    )


def _cross_attempt_transition_join(root: Path, raw: dict[str, Any]) -> None:
    _rewrite_observed_transition(
        root,
        raw,
        lambda value: value.update(attempt_id="s6bm-cross-attempt-substitution"),
    )


def _effect_database_precedes_switch_ack_delayed(root: Path, raw: dict[str, Any]) -> None:
    events = read_json(_reference_path(root, _proof(raw)["causal_event_export"]))
    switch = next(
        item for item in events["events"] if item["event_type"] == "blue_to_green_switch_commit"
    )
    switch_time = datetime.fromisoformat(
        str(switch["database_recorded_at"]).replace("Z", "+00:00")
    )
    earlier = (
        datetime.fromtimestamp(switch_time.timestamp() - 0.001, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    record = raw["request_records"][0]
    record["durable_effect"]["database_recorded_at"] = earlier
    effect_payload: dict[str, Any] = {}

    def mutate_effects(payload: dict[str, Any]) -> None:
        nonlocal effect_payload
        effect_payload = payload["effects"][0]["payload"]
        effect_payload["durable_commit"]["database_recorded_at"] = earlier

    _rewrite_reference(root, _proof(raw)["durable_effect_export"], mutate_effects)
    record["durable_effect"]["stored_payload_sha256"] = canonical_sha256(effect_payload)


def _terminal_effect_before_switch(root: Path, raw: dict[str, Any]) -> None:
    switch_unix = _switch_unix_ns(raw)
    timestamp = (
        datetime.fromtimestamp((switch_unix - 1_000_000) / 1_000_000_000, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    _effect_receipt(raw)["commit_timestamp"] = timestamp

    def mutate(payload: dict[str, Any]) -> None:
        for entry in payload["entries"]:
            if entry["span"].get("name") != "s6bm.terminal_effect.commit":
                continue
            for attribute in entry["span"].get("attributes", []):
                if attribute.get("key") == "evm.effect.commit.timestamp":
                    attribute["value"] = {"stringValue": timestamp}

    _rewrite_trace(root, raw, mutate)


def _terminal_effect_switch_overlap(root: Path, raw: dict[str, Any]) -> None:
    switch_unix = _switch_unix_ns(raw)
    timestamp = (
        datetime.fromtimestamp(switch_unix / 1_000_000_000, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    _effect_receipt(raw)["commit_timestamp"] = timestamp

    def mutate(payload: dict[str, Any]) -> None:
        for entry in payload["entries"]:
            if entry["span"].get("name") != "s6bm.terminal_effect.commit":
                continue
            for attribute in entry["span"].get("attributes", []):
                if attribute.get("key") == "evm.effect.commit.timestamp":
                    attribute["value"] = {"stringValue": timestamp}

    _rewrite_trace(root, raw, mutate)


def _durable_effect_absent(root: Path, raw: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["effects"] = []
        payload["effect_count"] = 0

    _rewrite_reference(root, _proof(raw)["durable_effect_export"], mutate)


def _effect_readback_absent(_root: Path, raw: dict[str, Any]) -> None:
    _effect_receipt(raw)["readback_visible"] = False


def _duplicate_effect_row(root: Path, raw: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["effects"].append(copy.deepcopy(payload["effects"][0]))
        payload["effect_count"] = 2

    _rewrite_reference(root, _proof(raw)["durable_effect_export"], mutate)


def _effect_identity_mismatch(root: Path, raw: dict[str, Any]) -> None:
    changed: dict[str, Any] = {}

    def mutate(payload: dict[str, Any]) -> None:
        payload["effects"][0]["payload"]["served_identity"]["model_name"] = "substituted"
        changed.update(payload["effects"][0]["payload"])

    _rewrite_reference(root, _proof(raw)["durable_effect_export"], mutate)
    _effect_receipt(raw)["stored_payload_sha256"] = canonical_sha256(changed)


def _effect_xid_mismatch(_root: Path, raw: dict[str, Any]) -> None:
    _effect_receipt(raw)["transaction_id"] = "999999999"


def _unload_before_last_effect(_root: Path, raw: dict[str, Any]) -> None:
    record = raw["request_records"][0]
    item = _phase(raw, "green_only")
    anchor = item["clock_anchor"]
    old_before = int(anchor["monotonic_before_ns"])
    new_phase = float(record["completed_monotonic"]) - 0.001
    new_before = int(new_phase * 1_000_000_000) + 500
    delta = new_before - old_before
    item["monotonic_seconds"] = new_phase
    anchor["monotonic_before_ns"] = new_before
    anchor["monotonic_after_ns"] = new_before + 500
    anchor["unix_ns"] = int(anchor["unix_ns"]) + delta
    _rehash_runner_anchor_chain(raw)


def _stale_blue_admission(_root: Path, raw: dict[str, Any]) -> None:
    switch = float(_route_transition_receipt(raw)["route_applied_monotonic_ns"]) / 1e9
    raw["request_records"][0]["attempted_monotonic"] = switch + 0.001


def _trace_parent_mismatch(root: Path, raw: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        span = next(
            entry["span"]
            for entry in payload["entries"]
            if entry["span"].get("name") == "s6bm.controller.predict"
        )
        span["parentSpanId"] = "f" * 16

    _rewrite_trace(root, raw, mutate)


def _effect_end_before_commit(root: Path, raw: dict[str, Any]) -> None:
    commit_unix_ns = int(
        datetime.fromisoformat(
            str(_effect_receipt(raw)["commit_timestamp"]).replace("Z", "+00:00")
        ).timestamp()
        * 1_000_000_000
    )

    def mutate(payload: dict[str, Any]) -> None:
        span = next(
            entry["span"]
            for entry in payload["entries"]
            if entry["span"].get("name") == "s6bm.terminal_effect.commit"
        )
        span["startTimeUnixNano"] = str(commit_unix_ns - 2_000_000)
        span["endTimeUnixNano"] = str(commit_unix_ns - 1)

    _rewrite_trace(root, raw, mutate)


def _controller_end_before_effect_end(root: Path, raw: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        effect = next(
            entry["span"]
            for entry in payload["entries"]
            if entry["span"].get("name") == "s6bm.terminal_effect.commit"
        )
        controller = next(
            entry["span"]
            for entry in payload["entries"]
            if entry["span"].get("name") == "s6bm.controller.predict"
        )
        controller["endTimeUnixNano"] = str(int(effect["endTimeUnixNano"]) - 1)

    _rewrite_trace(root, raw, mutate)


def _server_end_before_controller_end(root: Path, raw: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        controller = next(
            entry["span"]
            for entry in payload["entries"]
            if entry["span"].get("name") == "s6bm.controller.predict"
        )
        server = next(
            entry["span"]
            for entry in payload["entries"]
            if entry["span"].get("name")
            == "POST /control-panel/v1/scenario-workloads/triton-blue-green/predict"
        )
        server["endTimeUnixNano"] = str(int(controller["endTimeUnixNano"]) - 1)

    _rewrite_trace(root, raw, mutate)


def _metrics_missing_series(root: Path, raw: dict[str, Any]) -> None:
    reference = raw["observability"]["artifacts"]["prometheus_after"]

    def mutate(payload: dict[str, Any]) -> None:
        payload["queries"]["api_blue_completed"]["response"]["data"]["result"] = []

    _rewrite_reference(root, reference, mutate)


def _candidate_missing(_root: Path, raw: dict[str, Any]) -> None:
    _effect_receipt(raw)["database_clock_anchor_candidates"].pop()


def _candidate_duplicate(_root: Path, raw: dict[str, Any]) -> None:
    receipt = _effect_receipt(raw)
    first, second = receipt["database_clock_anchor_candidates"][:2]
    second["anchor_nonce"] = first["anchor_nonce"]
    second["source_identity"] = first["source_identity"]
    _rehash_anchor(second)


def _candidate_reordered(_root: Path, raw: dict[str, Any]) -> None:
    values = _effect_receipt(raw)["database_clock_anchor_candidates"]
    values[0:2] = reversed(values[0:2])


def _candidate_wrong_index(_root: Path, raw: dict[str, Any]) -> None:
    _effect_receipt(raw)["database_clock_anchor_selection"]["selected_sequence"] = 6


def _candidate_nonminimum(_root: Path, raw: dict[str, Any]) -> None:
    receipt = _effect_receipt(raw)
    selected = receipt["database_clock_anchor_candidates"][1]
    receipt["database_clock_anchor"] = copy.deepcopy(selected)
    receipt["database_clock_anchor_selection"]["selected_sequence"] = 2
    receipt["commit_timestamp_observed_at"] = selected["database_clock_timestamp"]


def _candidate_hash_drift(_root: Path, raw: dict[str, Any]) -> None:
    _effect_receipt(raw)["database_clock_anchor_candidates"][3]["anchor_hash"] = "f" * 64


def _all_candidates_over_bound(_root: Path, raw: dict[str, Any]) -> None:
    receipt = _effect_receipt(raw)
    start = int(receipt["commit_timestamp_started_monotonic_ns"]) + 10_000
    for index, candidate in enumerate(receipt["database_clock_anchor_candidates"]):
        before = start + index * 5_100_100
        candidate["monotonic_before_ns"] = before
        candidate["monotonic_after_ns"] = before + 5_000_001
        _rehash_anchor(candidate)
    selected = receipt["database_clock_anchor_candidates"][0]
    receipt["database_clock_anchor"] = copy.deepcopy(selected)
    receipt["database_clock_anchor_selection"]["selected_sequence"] = 1
    receipt["commit_timestamp_observed_at"] = selected["database_clock_timestamp"]
    timestamp_end = int(receipt["database_clock_anchor_candidates"][-1]["monotonic_after_ns"])
    receipt["commit_timestamp_finished_monotonic_ns"] = timestamp_end + 1_000
    receipt["readback_started_monotonic_ns"] = timestamp_end + 2_000
    receipt["readback_finished_monotonic_ns"] = timestamp_end + 3_000
    raw["request_records"][0]["completed_monotonic"] = (timestamp_end + 4_000) / 1e9


def _run_case(
    source_root: Path,
    raw: dict[str, Any],
    config: S6BMConfig,
    case_id: str,
    expected_reason: str,
    mutate: Mutation,
    *,
    validator: str = "causal",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"s6bm-v4-{case_id}-") as directory:
        root = Path(directory) / "bundle"
        shutil.copytree(source_root, root)
        candidate = copy.deepcopy(raw)
        mutate(root, candidate)
        try:
            if validator == "observability":
                validate_observability_bundle(root, candidate, config, compare_projection=True)
            else:
                validate_causal_bundle(root, candidate, config, compare_projection=False)
        except (
            S6BMCausalError,
            S6BMObservabilityError,
            S6BMRuntimeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            reason = str(exc)
            return {
                "case_id": case_id,
                "expected_reason": expected_reason,
                "observed_reason": reason,
                "rejected": reason == expected_reason,
            }
    return {
        "case_id": case_id,
        "expected_reason": expected_reason,
        "observed_reason": "validator_fail_open",
        "rejected": False,
    }


def _run_run_set_case(config: S6BMConfig) -> dict[str, Any]:
    candidate = replace(config, run_set={**config.run_set, "successful_transition": [1, 2]})
    reason = "validator_fail_open"
    try:
        candidate.validate()
    except S6BMRuntimeError as exc:
        reason = str(exc)
    expected = "s6bm_run_set_contract_v4"
    return {
        "case_id": "exact_frozen_run_set_mismatch",
        "expected_reason": expected,
        "observed_reason": reason,
        "rejected": reason == expected,
    }


def validate_historical(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    case_ids = tuple(str(item.get("mutation", "")) for item in payload.get("cases", []))
    if (
        sha256_file(path) != HISTORICAL_MUTATION_SHA256
        or case_ids != HISTORICAL_CASE_IDS
        or payload.get("positive") != 1
        or payload.get("negative") != 28
        or payload.get("negative_rejected") != 28
        or payload.get("passed") is not True
    ):
        raise StrictV4QualificationError("historical_mutation_prefix")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": HISTORICAL_MUTATION_SHA256,
        "case_count": len(case_ids),
        "case_ids": list(case_ids),
        "preserved": True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw = read_json(args.qualification)
    config = S6BMConfig.from_path(args.config)
    positive_causal = validate_causal_bundle(args.private_root, raw, config)
    positive_observability = validate_observability_bundle(
        args.private_root, raw, config, compare_projection=True
    )
    historical = validate_historical(args.historical_mutations)
    validator_revision = git_text("rev-parse", "HEAD")
    validator_tree = git_text("rev-parse", "HEAD^{tree}")
    cases = [
        ("database_candidate_missing", "s6bm_v4_database_clock_candidate_set", _candidate_missing),
        (
            "database_candidate_duplicate",
            "s6bm_v4_database_clock_candidate_duplicate",
            _candidate_duplicate,
        ),
        (
            "database_candidate_reordered",
            "s6bm_v4_database_clock_candidate_set",
            _candidate_reordered,
        ),
        (
            "database_selected_index_wrong",
            "s6bm_v4_database_clock_selection",
            _candidate_wrong_index,
        ),
        (
            "database_nonminimum_selection",
            "s6bm_v4_database_clock_selection",
            _candidate_nonminimum,
        ),
        (
            "database_candidate_hash_drift",
            "s6bm_v4_database_clock_candidate_hash",
            _candidate_hash_drift,
        ),
        (
            "database_all_candidates_over_bound",
            "s6bm_v4_database_clock_all_candidates_over_bound",
            _all_candidates_over_bound,
        ),
        (
            "database_selected_width_5000001ns",
            "s6bm_v4_database_clock_all_candidates_over_bound",
            _all_candidates_over_bound,
        ),
        (
            "server_span_start_after_switch",
            "s6bm_v4_api_server_handler_entry_span_after_switch",
            _server_start_after_switch,
        ),
        (
            "controller_span_start_after_switch",
            "s6bm_v4_controller_entry_span_after_switch",
            _controller_start_after_switch,
        ),
        (
            "model_span_start_after_switch",
            "s6bm_v4_triton_model_after_switch",
            _model_start_after_switch,
        ),
        (
            "server_actor_receipt_and_span_start_after_switch",
            "s6bm_v4_api_server_handler_entry_after_switch",
            _server_actor_and_span_after_switch,
        ),
        (
            "controller_actor_receipt_and_span_start_after_switch",
            "s6bm_v4_controller_entry_after_switch",
            _controller_actor_and_span_after_switch,
        ),
        (
            "triton_actor_receipt_and_spans_start_after_switch",
            "s6bm_v4_triton_backend_compute_entry_after_switch",
            _model_actor_and_spans_after_switch,
        ),
        (
            "per_request_clock_anchor_self_reference",
            "s6bm_v4_clock_anchor_self_reference",
            _clock_self_reference,
        ),
        (
            "run_clock_affine_drift_100ppm_plus_1",
            "s6bm_v4_clock_affine_drift",
            _clock_drift,
        ),
        (
            "run_clock_affine_residual_over_1ms",
            "s6bm_v4_clock_affine_residual",
            _clock_residual_over_bound,
        ),
        (
            "run_clock_affine_step_2ms_plus_1",
            "s6bm_v4_clock_affine_step",
            _clock_step_over_bound,
        ),
        (
            "run_clock_anchor_out_of_envelope",
            "s6bm_v4_clock_envelope_disjoint",
            _clock_envelope_disjoint,
        ),
        (
            "collector_anchor_missing",
            "s6bm_v4_collector_anchor_integrity",
            _collector_anchor_missing,
        ),
        (
            "run_clock_anchor_source_substitution",
            "s6bm_v4_clock_source_identity",
            _clock_source_substitution,
        ),
        ("phase_unix_monotonic_mismatch", "s6bm_v4_phase_anchor_interval", _phase_clock_mismatch),
        (
            "route_transition_receipt_absent",
            "s6bm_v4_transition_fence_receipt",
            _transition_receipt_absent,
        ),
        (
            "route_transition_source_substitution",
            "s6bm_v4_transition_receipt_binding",
            _transition_source_substitution,
        ),
        (
            "route_transition_id_mismatch",
            "s6bm_v4_transition_receipt_binding",
            _transition_id_mismatch,
        ),
        (
            "route_transition_generation_mismatch",
            "s6bm_v4_transition_state_readback",
            _transition_generation_mismatch,
        ),
        (
            "route_transition_fence_mismatch",
            "s6bm_v4_transition_receipt_binding",
            _transition_fence_mismatch,
        ),
        (
            "route_transition_timestamp_inversion",
            "s6bm_v4_transition_timestamp_order",
            _transition_timestamp_inversion,
        ),
        (
            "route_transition_readback_absent",
            "s6bm_v4_transition_fence_receipt",
            _transition_readback_absent,
        ),
        (
            "terminal_effect_before_switch_outer_completion_after",
            "s6bm_v4_hold_commit_interval_order",
            _terminal_effect_before_switch,
        ),
        (
            "database_commit_switch_interval_overlap",
            "s6bm_v4_hold_commit_interval_order",
            _terminal_effect_switch_overlap,
        ),
        ("durable_effect_row_absent", "s6bm_v4_durable_effect_count", _durable_effect_absent),
        (
            "effect_commit_readback_absent",
            "s6bm_v4_effect_receipt_binding",
            _effect_readback_absent,
        ),
        (
            "effect_transition_readback_absent",
            "s6bm_v4_effect_fence_happens_before",
            _effect_transition_readback_absent,
        ),
        (
            "effect_observed_fence_sequence_wrong",
            "s6bm_v4_effect_fence_happens_before",
            _wrong_fence_sequence,
        ),
        (
            "effect_reused_fence_identity",
            "s6bm_v4_effect_fence_happens_before",
            _reused_fence_identity,
        ),
        (
            "effect_cross_attempt_transition_join",
            "s6bm_v4_effect_fence_happens_before",
            _cross_attempt_transition_join,
        ),
        (
            "effect_database_precedes_switch_ack_delayed",
            "s6bm_v4_effect_fence_happens_before",
            _effect_database_precedes_switch_ack_delayed,
        ),
        (
            "duplicate_effect_row_idempotency_violation",
            "s6bm_v4_durable_effect_count",
            _duplicate_effect_row,
        ),
        (
            "request_trace_effect_model_binding_mismatch",
            "s6bm_v4_effect_entity_binding",
            _effect_identity_mismatch,
        ),
        (
            "causal_sequence_xid_receipt_mismatch",
            "s6bm_v4_effect_receipt_binding",
            _effect_xid_mismatch,
        ),
        (
            "unload_before_last_completion_or_effect",
            "s6bm_v4_unload_before_hold_completion",
            _unload_before_last_effect,
        ),
        ("stale_blue_admission", "s6bm_v4_stale_blue_admission", _stale_blue_admission),
        (
            "effect_end_before_commit",
            "s6bm_v4_effect_span_commit_order",
            _effect_end_before_commit,
        ),
        (
            "controller_end_before_effect_end",
            "s6bm_v4_controller_effect_span_order",
            _controller_end_before_effect_end,
        ),
        (
            "server_end_before_controller_end",
            "s6bm_v4_server_controller_span_order",
            _server_end_before_controller_end,
        ),
        ("parent_span_chain_mismatch", "s6bm_v4_trace_topology", _trace_parent_mismatch),
    ]
    results = [
        _run_case(args.private_root, raw, config, case_id, reason, mutate)
        for case_id, reason, mutate in cases
    ]
    results.append(
        _run_case(
            args.private_root,
            raw,
            config,
            "counter_generation_missing_series",
            "s6bm_prometheus_cardinality:api_blue_completed:0:0",
            _metrics_missing_series,
            validator="observability",
        )
    )
    results.append(_run_run_set_case(config))
    passed = all(item["rejected"] is True for item in results)
    payload = {
        "schema_version": "evm.s8_v4.s6bm_strict_v4_qualification_mutations.v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "acceptance_credit": False,
        "credit": "non_credit",
        "reviewer_sign_off": "pending",
        "source_revision": raw.get("source_revision"),
        "validation_identity": {
            "revision": validator_revision,
            "tree_sha": validator_tree,
            "config_sha256": sha256_file(args.config),
            "validator_sha256": sha256_file(Path(__file__)),
            "causal_validator_sha256": sha256_file(
                ROOT / "src/evm/scale_validation/s6bm_causal.py"
            ),
        },
        "qualification_sha256": sha256_file(args.qualification),
        "historical_mutation_prefix": historical,
        "positive": {
            "count": 1,
            "causal_projection_sha256": canonical_sha256(positive_causal),
            "observability_projection_sha256": canonical_sha256(positive_observability),
            "passed": True,
        },
        "strict_v4_negative": {
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
        raise StrictV4QualificationError("strict_v4_mutation_fail_open")
    return payload


def main() -> int:
    args = parse_args()
    result = run(args)
    print(canonical({"passed": result["passed"], "output_sha256": sha256_file(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

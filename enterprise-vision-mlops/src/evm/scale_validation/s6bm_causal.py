from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from evm.model_runtime.triton_blue_green import (
    TritonBlueGreenControlRequest,
    action_digest,
)
from evm.control_panel.transactional_store import (  # noqa: E402
    ControlPlaneParityError,
    s6bm_terminal_fence_record,
)
from evm.scale_validation.s6bm_runtime import (
    S6BMConfig,
    build_continuity_plan,
    canonical_sha256,
    sha256_file,
)


class S6BMCausalError(RuntimeError):
    pass


START_STAGES = (
    "api_server_handler_entry",
    "controller_entry",
    "triton_backend_compute_entry",
)
SWITCH_EVENT = "blue_to_green_switch_commit"
EFFECT_EVENT = "durable_terminal_effect_commit"
UNLOAD_EVENT = "blue_unload_intent"
SERVER_SPAN = "POST /control-panel/v1/scenario-workloads/triton-blue-green/predict"


def _read_json(path: Path, code: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise S6BMCausalError(f"{code}_noncanonical")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise S6BMCausalError(f"{code}_object")
    return value


def _resolve(root: Path, reference: Mapping[str, Any], code: str) -> Path:
    relative = Path(str(reference.get("path", "")))
    if relative.is_absolute() or ".." in relative.parts:
        raise S6BMCausalError(f"{code}_path")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise S6BMCausalError(f"{code}_path") from exc
    if not path.is_file() or sha256_file(path) != reference.get("sha256"):
        raise S6BMCausalError(f"{code}_sha")
    if int(reference.get("bytes", -1)) != path.stat().st_size:
        raise S6BMCausalError(f"{code}_bytes")
    return path


def _unix_nano(value: Any, code: str) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise S6BMCausalError(code) from exc
    if parsed.tzinfo is None:
        raise S6BMCausalError(code)
    normalized = parsed.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _finite(value: Any, code: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise S6BMCausalError(code) from exc
    if not math.isfinite(result):
        raise S6BMCausalError(code)
    return result


def _attribute_value(value: Mapping[str, Any]) -> Any:
    if len(value) != 1:
        raise S6BMCausalError("s6bm_v4_trace_attribute_value")
    return next(iter(value.values()))


def _attributes(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        key = str(item.get("key", ""))
        if not key or key in result:
            raise S6BMCausalError("s6bm_v4_trace_attribute_identity")
        result[key] = _attribute_value(dict(item.get("value", {})))
    return result


def _spans(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for entry in payload.get("entries", []):
        span = dict(dict(entry).get("span", {}))
        resource = _attributes(dict(entry).get("resource", {}).get("attributes", []))
        result.append(
            {
                "name": str(span.get("name", "")),
                "trace_id": str(span.get("traceId", "")),
                "span_id": str(span.get("spanId", "")),
                "parent_span_id": str(span.get("parentSpanId", "")),
                "start_unix_ns": int(span.get("startTimeUnixNano", 0)),
                "end_unix_ns": int(span.get("endTimeUnixNano", 0)),
                "attributes": _attributes(span.get("attributes", [])),
                "resource": resource,
                "events": [dict(item) for item in span.get("events", [])],
                "raw": dict(entry),
            }
        )
    return result


def _one(items: Sequence[Mapping[str, Any]], code: str) -> dict[str, Any]:
    if len(items) != 1:
        raise S6BMCausalError(f"{code}:{len(items)}")
    return dict(items[0])


def _proof(raw: Mapping[str, Any]) -> dict[str, Any]:
    return dict(raw.get("causal_proof", raw))


def _fraction_payload(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _fit_affine_clock_model(
    points: Sequence[Mapping[str, Any]], config: S6BMConfig
) -> tuple[tuple[int, int], dict[str, Any]]:
    if len(points) < 3:
        raise S6BMCausalError("s6bm_v4_clock_affine_point_count")
    samples: list[tuple[Fraction, Fraction, Mapping[str, Any]]] = []
    for point in points:
        before = int(point.get("monotonic_before_ns", 0))
        after = int(point.get("monotonic_after_ns", 0))
        unix_ns = int(point.get("unix_ns", 0))
        if before <= 0 or not before <= after or unix_ns <= 0:
            raise S6BMCausalError("s6bm_v4_clock_affine_point")
        samples.append((Fraction(before + after, 2), Fraction(unix_ns), point))

    count = len(samples)
    x_mean = sum((item[0] for item in samples), Fraction(0)) / count
    y_mean = sum((item[1] for item in samples), Fraction(0)) / count
    denominator = sum(((x - x_mean) ** 2 for x, _, _ in samples), Fraction(0))
    if denominator == 0:
        raise S6BMCausalError("s6bm_v4_clock_affine_degenerate")
    slope = (
        sum(((x - x_mean) * (y - y_mean) for x, y, _ in samples), Fraction(0))
        / denominator
    )
    intercept = y_mean - slope * x_mean
    drift_ppm = abs(slope - 1) * 1_000_000
    residuals = [y - (intercept + slope * x) for x, y, _ in samples]
    ordered = sorted(zip(samples, residuals, strict=True), key=lambda item: item[0][0])
    steps = [
        abs(right_residual - left_residual)
        for (_, left_residual), (_, right_residual) in zip(ordered, ordered[1:], strict=False)
    ]
    max_step = max(steps, default=Fraction(0))
    if max_step > int(config.clock["affine_max_step_ns"]):
        raise S6BMCausalError("s6bm_v4_clock_affine_step")
    if drift_ppm > int(config.clock["affine_max_drift_ppm"]):
        raise S6BMCausalError("s6bm_v4_clock_affine_drift")
    max_residual = max((abs(value) for value in residuals), default=Fraction(0))
    if max_residual > int(config.clock["affine_max_residual_ns"]):
        raise S6BMCausalError("s6bm_v4_clock_affine_residual")
    outlier_count = sum(
        abs(value) > int(config.clock["affine_max_residual_ns"]) for value in residuals
    )
    if outlier_count != int(config.clock["affine_required_outlier_count"]):
        raise S6BMCausalError("s6bm_v4_clock_affine_outlier")

    max_anchor_half_width = max(
        (
            Fraction(
                int(point["monotonic_after_ns"]) - int(point["monotonic_before_ns"]),
                2,
            )
            for _, _, point in samples
        ),
        default=Fraction(0),
    )
    uncertainty_ns = math.ceil(max_residual) + math.ceil(max_anchor_half_width) + 1
    offset_predictions = [intercept + (slope - 1) * x for x, _, _ in samples]
    envelope = (
        math.floor(min(offset_predictions) - uncertainty_ns),
        math.ceil(max(offset_predictions) + uncertainty_ns),
    )
    residual_payload = [
        {
            "source": str(point.get("source", "")),
            "sequence": int(point.get("source_sequence", 0)),
            "residual_ns": _fraction_payload(residual),
        }
        for (_, _, point), residual in zip(samples, residuals, strict=True)
    ]
    return envelope, {
        "contract": "centered_ols_fraction_all_points_v1",
        "point_count": count,
        "slope": _fraction_payload(slope),
        "intercept": _fraction_payload(intercept),
        "drift_ppm": _fraction_payload(drift_ppm),
        "max_absolute_residual_ns": _fraction_payload(max_residual),
        "max_absolute_residual_ns_ceil": math.ceil(max_residual),
        "max_consecutive_residual_step_ns": _fraction_payload(max_step),
        "max_consecutive_residual_step_ns_ceil": math.ceil(max_step),
        "max_anchor_half_width_ns_ceil": math.ceil(max_anchor_half_width),
        "projection_uncertainty_ns": uncertainty_ns,
        "projection_offset_envelope_ns": list(envelope),
        "outlier_count": outlier_count,
        "residuals": residual_payload,
    }


def _anchor_projection(
    raw: Mapping[str, Any], proof: Mapping[str, Any], config: S6BMConfig
) -> tuple[dict[str, tuple[int, int]], tuple[int, int], dict[str, Any]]:
    timeline = [dict(item) for item in raw.get("phase_timeline", [])]
    anchors = [dict(item.get("clock_anchor", {})) for item in timeline]
    receipt = dict(proof.get("triton_start_receipt", {}))
    adjudicated_request_nonces = {
        str(item.get("request_nonce", ""))
        for item in raw.get("request_records", [])
        if str(item.get("request_nonce", ""))
    }
    anchors.sort(key=lambda item: int(item.get("sequence", 0)))
    if not anchors or [int(item.get("sequence", 0)) for item in anchors] != list(
        range(1, len(anchors) + 1)
    ):
        raise S6BMCausalError("s6bm_v4_clock_sequence")

    expected_source_prefix = (
        f"runner:{raw.get('source_revision')}:"
        f"{str(dict(raw.get('identities', {})).get('lease', {}).get('run_id', '')).removeprefix('s8-v4-s6bm-')}:"
        f"{raw.get('attempt_id')}:"
    )
    previous: str | None = None
    offsets_low: list[int] = []
    offsets_high: list[int] = []
    midpoint_offsets: list[int] = []
    affine_points: list[dict[str, Any]] = []
    host: str | None = None
    process: int | None = None
    anchor_nonce: str | None = None
    for anchor in anchors:
        observed_hash = str(anchor.get("anchor_hash", ""))
        expected_hash = canonical_sha256(
            {key: value for key, value in anchor.items() if key != "anchor_hash"}
        )
        if observed_hash != expected_hash or anchor.get("previous_anchor_hash") != previous:
            raise S6BMCausalError("s6bm_v4_clock_hash_chain")
        previous = observed_hash
        before = int(anchor.get("monotonic_before_ns", 0))
        after = int(anchor.get("monotonic_after_ns", 0))
        unix_ns = int(anchor.get("unix_ns", 0))
        if before <= 0 or not before <= after:
            raise S6BMCausalError("s6bm_v4_clock_interval")
        if after - before > int(config.clock["max_anchor_width_ns"]):
            raise S6BMCausalError("s6bm_v4_clock_anchor_width")
        nonce = str(anchor.get("anchor_nonce", ""))
        if len(nonce) != 32 or any(character not in "0123456789abcdef" for character in nonce):
            raise S6BMCausalError("s6bm_v4_clock_anchor_nonce")
        if nonce in adjudicated_request_nonces:
            raise S6BMCausalError("s6bm_v4_clock_anchor_self_reference")
        if anchor_nonce is None:
            anchor_nonce = nonce
        if nonce != anchor_nonce or anchor.get("source_identity") != expected_source_prefix + nonce:
            raise S6BMCausalError("s6bm_v4_clock_source_identity")
        if host is None:
            host = str(anchor.get("host_identity", ""))
            process = int(anchor.get("process_id", 0))
        if (
            str(anchor.get("host_identity", "")) != host
            or int(anchor.get("process_id", 0)) != process
        ):
            raise S6BMCausalError("s6bm_v4_clock_actor_identity")
        offsets_low.append(unix_ns - after)
        offsets_high.append(unix_ns - before)
        midpoint_offsets.append(unix_ns - ((before + after) // 2))
        affine_points.append(
            {
                "source": "runner",
                "source_sequence": int(anchor["sequence"]),
                "monotonic_before_ns": before,
                "monotonic_after_ns": after,
                "unix_ns": unix_ns,
            }
        )
    for left, right in zip(anchors, anchors[1:], strict=False):
        gap = int(right["monotonic_before_ns"]) - int(left["monotonic_after_ns"])
        if gap < 0 or gap > int(float(config.clock["max_anchor_gap_seconds"]) * 1e9):
            raise S6BMCausalError("s6bm_v4_clock_anchor_gap")

    runner_envelope = (min(offsets_low), max(offsets_high))

    collector = dict(receipt.get("collector_observation", {}))
    collector_hash = str(collector.get("anchor_hash", ""))
    if (
        collector.get("schema_version") != "evm.s8_v4.s6bm_dual_clock_anchor.v3"
        or collector_hash
        != canonical_sha256(
            {key: value for key, value in collector.items() if key != "anchor_hash"}
        )
        or collector.get("previous_anchor_hash") is not None
        or int(collector.get("sequence", 0)) != 1
        or collector.get("phase") != "triton_compute_receipt_collected"
    ):
        raise S6BMCausalError("s6bm_v4_collector_anchor_integrity")
    collector_nonce = str(collector.get("anchor_nonce", ""))
    collector_process = int(collector.get("process_id", 0))
    collector_parent = int(collector.get("parent_process_id", 0))
    collector_source = (
        f"collector:{raw.get('source_revision')}:"
        f"{str(dict(raw.get('identities', {})).get('lease', {}).get('run_id', '')).removeprefix('s8-v4-s6bm-')}:"
        f"{raw.get('attempt_id')}:{collector_process}:{collector_nonce}"
    )
    if (
        len(collector_nonce) != 32
        or any(character not in "0123456789abcdef" for character in collector_nonce)
        or collector_nonce in adjudicated_request_nonces
        or collector_nonce == anchor_nonce
        or collector.get("source_identity") != collector_source
        or collector_process <= 0
        or collector_parent != process
        or collector.get("host_identity") != host
        or int(receipt.get("collector_process_id", 0)) != collector_process
        or int(receipt.get("collector_parent_process_id", 0)) != collector_parent
    ):
        raise S6BMCausalError("s6bm_v4_collector_anchor_identity")
    collector_before = int(collector.get("monotonic_before_ns", 0))
    collector_after = int(collector.get("monotonic_after_ns", 0))
    collector_unix = int(collector.get("unix_ns", 0))
    if (
        collector_before <= 0
        or not collector_before <= collector_after
        or collector_after - collector_before > int(config.clock["max_anchor_width_ns"])
    ):
        raise S6BMCausalError("s6bm_v4_collector_anchor_interval")
    collector_envelope = (
        collector_unix - collector_after,
        collector_unix - collector_before,
    )
    exact_intersection = (
        max(runner_envelope[0], collector_envelope[0]),
        min(runner_envelope[1], collector_envelope[1]),
    )
    exact_envelope_gap_ns = max(0, exact_intersection[0] - exact_intersection[1])
    if exact_envelope_gap_ns > int(config.clock["affine_max_step_ns"]):
        raise S6BMCausalError("s6bm_v4_clock_envelope_disjoint")
    collector_midpoint_offset = collector_unix - ((collector_before + collector_after) // 2)
    affine_points.append(
        {
            "source": "collector",
            "source_sequence": 1,
            "monotonic_before_ns": collector_before,
            "monotonic_after_ns": collector_after,
            "unix_ns": collector_unix,
        }
    )
    envelope, affine_model = _fit_affine_clock_model(affine_points, config)

    phase_bounds: dict[str, tuple[int, int]] = {}
    by_phase = {str(item.get("phase", "")): item for item in timeline}
    if len(by_phase) != len(timeline):
        raise S6BMCausalError("s6bm_v4_phase_identity")
    for phase, item in by_phase.items():
        anchor = dict(item.get("clock_anchor", {}))
        if anchor.get("phase") != phase:
            raise S6BMCausalError("s6bm_v4_phase_anchor_identity")
        phase_ns = int(_finite(item.get("monotonic_seconds"), "s6bm_v4_phase_time") * 1e9)
        before = int(anchor["monotonic_before_ns"])
        after = int(anchor["monotonic_after_ns"])
        if phase_ns > before or before - phase_ns > int(config.clock["max_phase_interval_ns"]):
            raise S6BMCausalError("s6bm_v4_phase_anchor_interval")
        phase_bounds[phase] = (phase_ns, after)
    required = set(config.clock.get("required_phase_anchors", []))
    if not required.issubset(phase_bounds):
        raise S6BMCausalError("s6bm_v4_required_phase_anchor")
    return (
        phase_bounds,
        envelope,
        {
            "runner_anchor_count": len(anchors),
            "collector_anchor_count": 1,
            "first_anchor_hash": str(anchors[0]["anchor_hash"]),
            "last_anchor_hash": str(anchors[-1]["anchor_hash"]),
            "collector_anchor_hash": collector_hash,
            "runner_anchor_nonce": anchor_nonce,
            "collector_anchor_nonce": collector_nonce,
            "max_anchor_width_ns": max(
                [
                    *(
                        int(item["monotonic_after_ns"]) - int(item["monotonic_before_ns"])
                        for item in anchors
                    ),
                    collector_after - collector_before,
                ]
            ),
            "offset_spread_ns": max([*midpoint_offsets, collector_midpoint_offset])
            - min([*midpoint_offsets, collector_midpoint_offset]),
            "exact_envelope_gap_ns": exact_envelope_gap_ns,
            "affine_model": affine_model,
            "combined_offset_envelope_ns": list(envelope),
        },
    )


def _project_unix(unix_ns: int, envelope: tuple[int, int]) -> tuple[int, int]:
    offset_low, offset_high = envelope
    return unix_ns - offset_high, unix_ns - offset_low


def project_run_clock_offset_envelope(
    raw: Mapping[str, Any], config: S6BMConfig
) -> dict[str, Any]:
    """Return the same frozen run-clock projection used by causal adjudication."""
    _phase_bounds, envelope, projection = _anchor_projection(
        raw, _proof(raw), config
    )
    return {
        "offset_envelope_ns": list(envelope),
        "projection": projection,
    }


def project_switch_fence_commit_interval(
    transition_receipt: Mapping[str, Any],
    effect_receipt: Mapping[str, Any],
    config: S6BMConfig,
) -> dict[str, Any]:
    """Project the durable switch fence separately from the later route-applied ACK."""
    transition = dict(transition_receipt)
    fence_receipt = dict(transition.get("fence_receipt", {}))
    database_recorded_at = _unix_nano(
        fence_receipt.get("database_recorded_at"),
        "s6bm_v4_switch_fence_database_recorded_at",
    )
    database_envelope, database_anchor = _database_clock_envelope(effect_receipt, config)
    write_interval = _project_unix(database_recorded_at, database_envelope)
    commit_ack = int(transition.get("actor_commit_ack_monotonic_ns", 0))
    readback_end = int(transition.get("fence_readback_finished_monotonic_ns", 0))
    route_applied = int(transition.get("route_applied_monotonic_ns", 0))
    if not 0 < commit_ack <= readback_end <= route_applied:
        raise S6BMCausalError("s6bm_v4_transition_timestamp_order")
    return {
        "fence_commit_interval_ns": [
            min(write_interval[0], commit_ack),
            max(write_interval[1], commit_ack),
        ],
        "database_write_interval_ns": list(write_interval),
        "database_offset_envelope_ns": list(database_envelope),
        "database_clock_anchor": database_anchor,
        "commit_ack_monotonic_ns": commit_ack,
        "readback_finished_monotonic_ns": readback_end,
        "route_applied_monotonic_ns": route_applied,
        "exact_commit_instant_claimed": False,
    }


def _validate_hold_effect_span_order(
    *,
    effect_start_interval: Sequence[int],
    commit_interval: Sequence[int],
    effect_end_interval: Sequence[int],
    effect_span: Mapping[str, Any],
    controller_span: Mapping[str, Any],
    server_span: Mapping[str, Any],
) -> None:
    # PostgreSQL and OTLP timestamps cross clock domains, so their intervals must not overlap.
    if not (
        effect_start_interval[1]
        <= commit_interval[0]
        <= commit_interval[1]
        <= effect_end_interval[0]
    ):
        raise S6BMCausalError("s6bm_v4_effect_span_commit_order")

    # Nested OTLP spans share one clock source. Equal closure timestamps are valid.
    if not (
        int(controller_span["start_unix_ns"])
        <= int(effect_span["start_unix_ns"])
        <= int(effect_span["end_unix_ns"])
        <= int(controller_span["end_unix_ns"])
    ):
        raise S6BMCausalError("s6bm_v4_controller_effect_span_order")
    if not (
        int(server_span["start_unix_ns"])
        <= int(controller_span["start_unix_ns"])
        and int(controller_span["end_unix_ns"]) <= int(server_span["end_unix_ns"])
    ):
        raise S6BMCausalError("s6bm_v4_server_controller_span_order")


def _validate_route_transition_receipt(
    *,
    receipt: Mapping[str, Any],
    switch_event: Mapping[str, Any],
    crossover: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    transition = dict(receipt)
    fence_receipt = dict(transition.get("fence_receipt", {}))
    switch_payload = dict(switch_event.get("payload", {}))
    source_payload = dict(switch_payload.get("source_payload", {}))
    actor = dict(switch_payload.get("actor", {}))
    route_actor = dict(transition.get("route_applied_actor", {}))
    state = dict(transition.get("state_readback", {}))
    try:
        control = TritonBlueGreenControlRequest.model_validate(source_payload)
    except (TypeError, ValueError) as exc:
        raise S6BMCausalError("s6bm_v4_transition_source_payload") from exc
    transition_core = {
        "attempt_id": crossover["attempt_id"],
        "run_id": crossover["run_id"],
        "request_id": crossover["request_id"],
        "action": "green_switched",
        "old_route_generation": int(crossover["route_generation"]),
        "new_route_generation": int(crossover["route_generation"]) + 1,
        "source_payload_sha256": canonical_sha256(source_payload),
        "source_revision": str(raw.get("source_revision", "")),
        "cell_id": crossover["attempt_id"],
        "replica_id": str(actor.get("service_instance_id", "")),
    }
    expected_transition_id = canonical_sha256(
        {"schema_version": "evm.s6bm.route_transition_identity.v1", **transition_core}
    )
    expected_fence_id = canonical_sha256(
        {
            "schema_version": "evm.s6bm.route_fence_identity.v1",
            "transition_id": expected_transition_id,
            "attempt_id": crossover["attempt_id"],
            "request_id": crossover["request_id"],
        }
    )
    expected_reference = _transition_reference(switch_event)
    if (
        switch_payload.get("schema_version") != "evm.s6bm.route_switch_fence.v2"
        or action_digest(control) != control.action_digest
        or control.run_id != crossover["run_id"]
        or control.action != "green_switched"
        or control.expected_generation != int(crossover["route_generation"])
        or control.causal_crossover is None
        or control.causal_crossover.model_dump(mode="json") != dict(crossover)
        or any(switch_payload.get(key) != value for key, value in transition_core.items())
        or switch_payload.get("transition_id") != expected_transition_id
        or switch_payload.get("fence_id") != expected_fence_id
        or actor.get("actor_identity") != "api-control-plane-route-switch"
        or int(actor.get("process_id", 0)) <= 0
        or int(actor.get("thread_id", 0)) <= 0
        or actor.get("source_revision") != raw.get("source_revision")
        or actor.get("service_instance_id") != transition_core["replica_id"]
    ):
        raise S6BMCausalError("s6bm_v4_transition_source_binding")
    if (
        fence_receipt.get("schema_version") != "evm.s6bm.route_switch_receipt.v2"
        or fence_receipt.get("payload") != switch_payload
        or fence_receipt.get("payload_sha256") != switch_event.get("payload_sha256")
        or fence_receipt.get("transaction_id") != switch_event.get("transaction_id")
        or fence_receipt.get("database_recorded_at")
        != switch_event.get("database_recorded_at")
        or int(fence_receipt.get("causal_sequence", 0))
        != int(switch_event.get("causal_sequence", 0))
        or fence_receipt.get("transition_id") != expected_transition_id
        or fence_receipt.get("fence_id") != expected_fence_id
        or int(fence_receipt.get("fence_sequence", 0))
        != int(switch_event.get("causal_sequence", 0))
        or str(fence_receipt.get("fence_transaction_id", ""))
        != str(switch_event.get("transaction_id", ""))
        or fence_receipt.get("fence_payload_sha256") != switch_event.get("payload_sha256")
        or fence_receipt.get("readback_visible") is not True
        or any(
            fence_receipt.get(key) != expected_reference[key]
            for key in (
                "transition_id",
                "fence_id",
                "old_route_generation",
                "new_route_generation",
                "source_payload_sha256",
                "cell_id",
                "replica_id",
            )
        )
    ):
        raise S6BMCausalError("s6bm_v4_transition_fence_receipt")
    if (
        transition.get("schema_version") != "evm.s6bm.route_transition_receipt.v1"
        or transition.get("transition_id") != expected_transition_id
        or transition.get("fence_id") != expected_fence_id
        or transition.get("fence_receipt_sha256") != canonical_sha256(fence_receipt)
        or any(
            transition.get(key) != expected_reference[key]
            for key in (
                "transition_id",
                "fence_id",
                "old_route_generation",
                "new_route_generation",
                "source_payload_sha256",
                "cell_id",
                "replica_id",
            )
        )
        or transition.get("attempt_id") != crossover["attempt_id"]
        or transition.get("run_id") != crossover["run_id"]
        or transition.get("request_id") != crossover["request_id"]
        or transition.get("source_revision") != raw.get("source_revision")
        or int(transition.get("fence_sequence", 0))
        != int(switch_event.get("causal_sequence", 0))
        or str(transition.get("fence_transaction_id", ""))
        != str(switch_event.get("transaction_id", ""))
        or transition.get("fence_payload_sha256") != switch_event.get("payload_sha256")
        or transition.get("actor_identity") != fence_receipt.get("actor_identity")
        or int(transition.get("actor_process_id", 0))
        != int(fence_receipt.get("actor_process_id", 0))
        or int(transition.get("actor_thread_id", 0))
        != int(fence_receipt.get("actor_thread_id", 0))
        or int(transition.get("actor_commit_ack_monotonic_ns", 0))
        != int(fence_receipt.get("commit_ack_monotonic_ns", 0))
        or int(transition.get("fence_readback_started_monotonic_ns", 0))
        != int(fence_receipt.get("readback_started_monotonic_ns", 0))
        or int(transition.get("fence_readback_finished_monotonic_ns", 0))
        != int(fence_receipt.get("readback_finished_monotonic_ns", 0))
    ):
        raise S6BMCausalError("s6bm_v4_transition_receipt_binding")
    commit_ack = int(transition.get("actor_commit_ack_monotonic_ns", 0))
    readback_start = int(transition.get("fence_readback_started_monotonic_ns", 0))
    readback_end = int(transition.get("fence_readback_finished_monotonic_ns", 0))
    route_applied = int(transition.get("route_applied_monotonic_ns", 0))
    if not 0 < commit_ack <= readback_start <= readback_end <= route_applied:
        raise S6BMCausalError("s6bm_v4_transition_timestamp_order")
    if (
        int(transition.get("actor_process_id", 0)) != int(actor["process_id"])
        or int(transition.get("actor_thread_id", 0)) != int(actor["thread_id"])
        or int(route_actor.get("process_id", 0)) != int(actor["process_id"])
        or int(route_actor.get("thread_id", 0)) != int(actor["thread_id"])
        or route_actor.get("actor_identity")
        != "api-control-plane-route-switch-applied"
        or route_actor.get("source_revision") != raw.get("source_revision")
        or route_actor.get("service_instance_id") != transition_core["replica_id"]
        or not route_applied
        <= int(route_actor.get("monotonic_before_ns", 0))
        <= int(route_actor.get("monotonic_after_ns", 0))
    ):
        raise S6BMCausalError("s6bm_v4_transition_actor_identity")
    if state != {
        "generation": int(crossover["route_generation"]) + 1,
        "phase": "green_active",
        "route_weights": {"blue": 0, "green": 100},
        "loaded_roles": ["blue", "green"],
    }:
        raise S6BMCausalError("s6bm_v4_transition_state_readback")
    return route_applied, {
        "transition_id": expected_transition_id,
        "fence_id": expected_fence_id,
        "old_route_generation": transition_core["old_route_generation"],
        "new_route_generation": transition_core["new_route_generation"],
        "fence_sequence": int(switch_event["causal_sequence"]),
        "fence_transaction_id": str(switch_event["transaction_id"]),
        "actor_process_id": int(actor["process_id"]),
        "actor_thread_id": int(actor["thread_id"]),
        "commit_ack_monotonic_ns": commit_ack,
        "readback_finished_monotonic_ns": readback_end,
        "route_applied_monotonic_ns": route_applied,
        "source_payload_sha256": transition_core["source_payload_sha256"],
        "cell_id": transition_core["cell_id"],
        "replica_id": transition_core["replica_id"],
    }


def _validate_continuity_receipt_switch_fence(
    *,
    switch_event: Mapping[str, Any],
    transition_receipt: Mapping[str, Any],
    required_start_events: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Recompute the exact bridge receipt set bound by the switch transaction."""
    switch_payload = dict(switch_event.get("payload", {}))
    source_payload = dict(switch_payload.get("source_payload", {}))
    request_ids = sorted(required_start_events)
    request_set_sha256 = canonical_sha256(request_ids)
    sequences = {
        request_id: {
            stage: int(required_start_events[request_id][stage]["causal_sequence"])
            for stage in sorted(START_STAGES)
        }
        for request_id in request_ids
    }
    payload_sha256 = {
        request_id: {
            stage: required_start_events[request_id][stage]["payload_sha256"]
            for stage in sorted(START_STAGES)
        }
        for request_id in request_ids
    }
    transaction_ids = {
        request_id: {
            stage: str(required_start_events[request_id][stage]["transaction_id"])
            for stage in sorted(START_STAGES)
        }
        for request_id in request_ids
    }
    switch_sequence = int(switch_event.get("causal_sequence", 0))
    if (
        source_payload.get("continuity_receipt_request_ids") != request_ids
        or switch_payload.get("continuity_receipt_request_ids") != request_ids
        or switch_payload.get("continuity_receipt_request_set_sha256")
        != request_set_sha256
        or switch_payload.get("continuity_receipt_sequences") != sequences
        or switch_payload.get("continuity_receipt_payload_sha256") != payload_sha256
        or switch_payload.get("continuity_receipt_transaction_ids") != transaction_ids
        or transition_receipt.get("continuity_receipt_request_ids") != request_ids
        or transition_receipt.get("continuity_receipt_request_set_sha256")
        != request_set_sha256
        or any(
            sequence >= switch_sequence
            for request_sequences in sequences.values()
            for sequence in request_sequences.values()
        )
    ):
        raise S6BMCausalError("s6bm_v4_continuity_actor_receipt_fence")
    return {
        "request_ids": request_ids,
        "request_set_sha256": request_set_sha256,
        "sequences": sequences,
        "payload_sha256": payload_sha256,
        "transaction_ids": transaction_ids,
    }


def _database_clock_envelope(
    receipt: Mapping[str, Any], config: S6BMConfig
) -> tuple[tuple[int, int], dict[str, Any]]:
    candidates = [dict(item) for item in receipt.get("database_clock_anchor_candidates", [])]
    expected_sequences = list(config.durable_effect["database_clock_anchor_sequence"])
    if (
        receipt.get("schema_version") != "evm.s6bm.durable_effect_receipt.v4"
        or len(candidates) != int(config.durable_effect["database_clock_anchor_samples"])
        or [int(item.get("sequence", 0)) for item in candidates] != expected_sequences
    ):
        raise S6BMCausalError("s6bm_v4_database_clock_candidate_set")
    nonces = [str(item.get("anchor_nonce", "")) for item in candidates]
    if len(set(nonces)) != len(nonces):
        raise S6BMCausalError("s6bm_v4_database_clock_candidate_duplicate")
    transaction_id = str(receipt.get("transaction_id", ""))
    backend_pid = int(receipt.get("commit_timestamp_backend_pid", 0))
    timestamp_start = int(receipt.get("commit_timestamp_started_monotonic_ns", 0))
    timestamp_end = int(receipt.get("commit_timestamp_finished_monotonic_ns", 0))
    previous_after = 0
    for candidate in candidates:
        nonce = str(candidate.get("anchor_nonce", ""))
        schema_name = str(candidate.get("schema_name", ""))
        expected_source = f"postgresql:{schema_name}:{transaction_id}:{backend_pid}:{nonce}"
        observed_hash = str(candidate.get("anchor_hash", ""))
        expected_hash = canonical_sha256(
            {key: value for key, value in candidate.items() if key != "anchor_hash"}
        )
        observed_at = str(candidate.get("database_clock_timestamp", ""))
        before = int(candidate.get("monotonic_before_ns", 0))
        after = int(candidate.get("monotonic_after_ns", 0))
        database_unix_ns = int(candidate.get("database_unix_ns", 0))
        if observed_hash != expected_hash:
            raise S6BMCausalError("s6bm_v4_database_clock_candidate_hash")
        if (
            candidate.get("schema_version") != "evm.s6bm.database_clock_anchor.v2"
            or len(nonce) != 32
            or any(character not in "0123456789abcdef" for character in nonce)
            or candidate.get("clock_source") != "postgresql_clock_timestamp"
            or not schema_name.startswith(str(config.durable_effect["schema_prefix"]))
            or candidate.get("source_identity") != expected_source
            or str(candidate.get("transaction_id", "")) != transaction_id
            or int(candidate.get("backend_pid", 0)) != backend_pid
        ):
            raise S6BMCausalError("s6bm_v4_database_clock_candidate_binding")
        if (
            before <= 0
            or not timestamp_start <= before <= after <= timestamp_end
            or before < previous_after
            or database_unix_ns != _unix_nano(observed_at, "s6bm_v4_database_clock_time")
        ):
            raise S6BMCausalError("s6bm_v4_database_clock_candidate_interval")
        previous_after = after

    selected = min(
        candidates,
        key=lambda item: (
            int(item["monotonic_after_ns"]) - int(item["monotonic_before_ns"]),
            int(item["sequence"]),
        ),
    )
    selection = dict(receipt.get("database_clock_anchor_selection", {}))
    if (
        config.durable_effect["database_clock_anchor_selection"] != "minimum_width_then_sequence"
        or receipt.get("database_clock_anchor") != selected
        or selection
        != {
            "strategy": "minimum_width_then_sequence",
            "candidate_count": len(expected_sequences),
            "selected_sequence": int(selected["sequence"]),
        }
    ):
        raise S6BMCausalError("s6bm_v4_database_clock_selection")
    before = int(selected["monotonic_before_ns"])
    after = int(selected["monotonic_after_ns"])
    max_selected_width_ns = int(
        config.durable_effect["database_clock_anchor_max_selected_width_ns"]
    )
    if after - before > max_selected_width_ns:
        raise S6BMCausalError("s6bm_v4_database_clock_all_candidates_over_bound")
    observed_at = str(selected["database_clock_timestamp"])
    if observed_at != str(receipt.get("commit_timestamp_observed_at", "")):
        raise S6BMCausalError("s6bm_v4_database_clock_selection_timestamp")
    database_unix_ns = int(selected["database_unix_ns"])
    envelope = (database_unix_ns - after, database_unix_ns - before)
    return envelope, {
        "anchor_hash": selected["anchor_hash"],
        "anchor_nonce": selected["anchor_nonce"],
        "backend_pid": backend_pid,
        "width_ns": after - before,
        "max_selected_width_ns": max_selected_width_ns,
        "offset_envelope_ns": list(envelope),
        "candidate_count": len(candidates),
        "candidate_set_sha256": canonical_sha256(candidates),
        "selected_sequence": int(selected["sequence"]),
    }


def _event_identity(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: event.get(key)
        for key in (
            "attempt_id",
            "run_id",
            "request_id",
            "request_nonce",
            "trace_id",
            "effect_id",
            "model_role",
            "model_name",
            "model_version",
            "artifact_sha256",
            "route_generation",
        )
    }


def _record_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": record.get("attempt_id"),
        "run_id": record.get("run_id"),
        "request_id": record.get("request_id"),
        "request_nonce": record.get("request_nonce"),
        "trace_id": record.get("trace_id"),
        "effect_id": record.get("effect_id"),
        "model_role": record.get("model_role"),
        "model_name": record.get("model_name"),
        "model_version": record.get("model_version"),
        "artifact_sha256": record.get("artifact_sha256"),
        "route_generation": record.get("route_generation"),
    }


def _transition_reference(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(event.get("payload", {}))
    return {
        "schema_version": "evm.s6bm.observed_transition.v1",
        "transition_id": str(payload.get("transition_id", "")),
        "fence_id": str(payload.get("fence_id", "")),
        "fence_sequence": int(event.get("causal_sequence", 0)),
        "fence_transaction_id": str(event.get("transaction_id", "")),
        "fence_payload_sha256": str(event.get("payload_sha256", "")),
        "attempt_id": str(event.get("attempt_id", "")),
        "run_id": str(event.get("run_id", "")),
        "request_id": str(event.get("request_id", "")),
        "old_route_generation": int(payload.get("old_route_generation", 0)),
        "new_route_generation": int(payload.get("new_route_generation", 0)),
        "source_payload_sha256": str(payload.get("source_payload_sha256", "")),
        "cell_id": str(payload.get("cell_id", "")),
        "replica_id": str(payload.get("replica_id", "")),
        "database_recorded_at": str(event.get("database_recorded_at", "")),
    }


def _validate_effects(
    records: Sequence[Mapping[str, Any]],
    effects_export: Mapping[str, Any],
    effect_events: Sequence[Mapping[str, Any]],
    switch_event: Mapping[str, Any],
    config: S6BMConfig,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, tuple[int, int]],
    dict[str, dict[str, Any]],
]:
    effects = [dict(item) for item in effects_export.get("effects", [])]
    if int(effects_export.get("effect_count", -1)) != len(records) or len(effects) != len(records):
        raise S6BMCausalError("s6bm_v4_durable_effect_count")
    by_request = {str(item.get("request_id", "")): dict(item) for item in records}
    exported = {str(item.get("idempotency_key", "")): item for item in effects}
    event_by_request = {str(item.get("request_id", "")): dict(item) for item in effect_events}
    database_envelopes: dict[str, tuple[int, int]] = {}
    database_anchors: dict[str, dict[str, Any]] = {}
    if set(exported) != set(by_request) or set(event_by_request) != set(by_request):
        raise S6BMCausalError("s6bm_v4_durable_effect_identity_set")
    if len(exported) != len(effects) or len(event_by_request) != len(effect_events):
        raise S6BMCausalError("s6bm_v4_durable_effect_duplicate")
    expected_transition = _transition_reference(switch_event)
    switch_payload = dict(switch_event.get("payload", {}))
    eligible_crossover_request_ids = sorted(
        str(value) for value in switch_payload.get("pending_crossover_request_ids", [])
    )
    for request_id, record in by_request.items():
        effect = exported[request_id]
        event = event_by_request[request_id]
        receipt = dict(record.get("durable_effect", {}))
        payload = dict(effect.get("payload", {}))
        durable_commit = dict(payload.get("durable_commit", {}))
        expected_identity = _record_identity(record)
        if _event_identity(event) != expected_identity:
            raise S6BMCausalError("s6bm_v4_effect_event_binding")
        if event.get("payload_sha256") != canonical_sha256(event.get("payload", {})):
            raise S6BMCausalError("s6bm_v4_effect_event_payload_sha")
        if (
            effect.get("entity_id") != record.get("effect_id")
            or effect.get("state") != "completed"
            or payload.get("terminal_outcome") != "completed"
            or payload.get("request_id") != request_id
            or payload.get("trace_id") != record.get("trace_id")
            or payload.get("effect_id") != record.get("effect_id")
            or payload.get("result_sha256") != record.get("result_sha256")
            or payload.get("offered_identity") != record.get("offered_identity")
            or payload.get("served_identity") != record.get("offered_identity")
        ):
            raise S6BMCausalError("s6bm_v4_effect_entity_binding")
        if (
            durable_commit.get("causal_sequence") != event.get("causal_sequence")
            or durable_commit.get("causal_payload_sha256") != event.get("payload_sha256")
            or str(durable_commit.get("transaction_id")) != str(event.get("transaction_id"))
            or durable_commit.get("synchronous_commit") != "on"
            or durable_commit.get("schema_version") != "evm.s6bm.durable_commit.v3"
            or int(durable_commit.get("write_backend_pid", 0)) <= 0
            or _unix_nano(
                durable_commit.get("database_recorded_at"),
                "s6bm_v4_effect_database_time",
            )
            > _unix_nano(
                event.get("database_recorded_at"),
                "s6bm_v4_effect_event_database_time",
            )
        ):
            raise S6BMCausalError("s6bm_v4_effect_transaction_binding")
        requires_switch = event.get("payload", {}).get("requires_switch_before_effect") is True
        if requires_switch:
            observed_values = (
                payload.get("observed_transition"),
                dict(event.get("payload", {})).get("observed_transition"),
                durable_commit.get("observed_transition"),
                receipt.get("observed_transition"),
            )
            if (
                any(value != expected_transition for value in observed_values)
                or receipt.get("transition_readback_visible") is not True
                or expected_transition["attempt_id"] != record.get("attempt_id")
                or expected_transition["run_id"] != record.get("run_id")
                or request_id not in eligible_crossover_request_ids
                or len(eligible_crossover_request_ids)
                != len(set(eligible_crossover_request_ids))
                or expected_transition["old_route_generation"]
                != int(record.get("route_generation", 0))
                or expected_transition["new_route_generation"]
                != expected_transition["old_route_generation"] + 1
                or str(event.get("transaction_id"))
                == expected_transition["fence_transaction_id"]
                or _unix_nano(
                    expected_transition["database_recorded_at"],
                    "s6bm_v4_transition_database_time",
                )
                > _unix_nano(
                    durable_commit.get("database_recorded_at"),
                    "s6bm_v4_effect_database_time",
                )
            ):
                raise S6BMCausalError("s6bm_v4_effect_fence_happens_before")
        elif any(
            value is not None
            for value in (
                payload.get("observed_transition"),
                dict(event.get("payload", {})).get("observed_transition"),
                durable_commit.get("observed_transition"),
                receipt.get("observed_transition"),
            )
        ):
            raise S6BMCausalError("s6bm_v4_effect_fence_unexpected")
        if (
            receipt.get("schema_version") != "evm.s6bm.durable_effect_receipt.v4"
            or receipt.get("entity_id") != record.get("effect_id")
            or receipt.get("causal_sequence") != event.get("causal_sequence")
            or receipt.get("causal_payload_sha256") != event.get("payload_sha256")
            or str(receipt.get("transaction_id")) != str(event.get("transaction_id"))
            or int(receipt.get("write_backend_pid", 0))
            != int(durable_commit.get("write_backend_pid", 0))
            or receipt.get("synchronous_commit") != "on"
            or receipt.get("readback_visible") is not True
            or receipt.get("replayed") is not False
            or receipt.get("commit_timestamp_tracking") != "on"
            or receipt.get("commit_timestamp_visible") is not True
            or receipt.get("separate_connection_readback") is not True
            or receipt.get("commit_timestamp_readback_lane")
            != config.durable_effect["commit_timestamp_readback_lane"]
            or int(receipt.get("commit_timestamp_readback_concurrency_limit", 0))
            != int(config.durable_effect["commit_timestamp_readback_max_concurrency"])
            or not 1
            <= int(receipt.get("commit_timestamp_readback_in_flight_at_acquire", 0))
            <= int(config.durable_effect["commit_timestamp_readback_max_concurrency"])
            or not 1
            <= int(receipt.get("commit_timestamp_readback_max_in_flight_observed", 0))
            <= int(config.durable_effect["commit_timestamp_readback_max_concurrency"])
            or int(receipt.get("commit_timestamp_backend_pid", 0)) <= 0
            or int(receipt.get("commit_timestamp_backend_pid", 0))
            == int(receipt.get("write_backend_pid", 0))
            or receipt.get("stored_payload_sha256") != canonical_sha256(payload)
        ):
            raise S6BMCausalError("s6bm_v4_effect_receipt_binding")
        ack = int(receipt.get("commit_ack_monotonic_ns", 0))
        lane_wait_start = int(receipt.get("commit_timestamp_readback_wait_started_monotonic_ns", 0))
        lane_acquired = int(receipt.get("commit_timestamp_readback_acquired_monotonic_ns", 0))
        lane_wait_seconds = _finite(
            receipt.get("commit_timestamp_readback_wait_seconds"),
            "s6bm_v4_effect_readback_lane_wait",
        )
        timestamp_start = int(receipt.get("commit_timestamp_started_monotonic_ns", 0))
        timestamp_end = int(receipt.get("commit_timestamp_finished_monotonic_ns", 0))
        read_start = int(receipt.get("readback_started_monotonic_ns", 0))
        read_end = int(receipt.get("readback_finished_monotonic_ns", 0))
        completion = int(_finite(record.get("completed_monotonic"), "s6bm_v4_completion") * 1e9)
        if not (
            0
            < ack
            <= lane_wait_start
            <= lane_acquired
            <= timestamp_start
            <= timestamp_end
            <= read_start
            <= read_end
            <= completion
        ):
            raise S6BMCausalError("s6bm_v4_effect_commit_readback_order")
        observed_lane_wait = (lane_acquired - lane_wait_start) / 1_000_000_000
        if abs(lane_wait_seconds - observed_lane_wait) > 1e-9 or lane_wait_seconds > float(
            config.durable_effect["commit_timestamp_readback_acquire_timeout_seconds"]
        ):
            raise S6BMCausalError("s6bm_v4_effect_readback_lane_wait")
        for field in ("database_recorded_at", "entity_created_at", "idempotency_created_at"):
            _unix_nano(receipt.get(field), f"s6bm_v4_effect_{field}")
        commit_unix = _unix_nano(receipt.get("commit_timestamp"), "s6bm_v4_effect_commit_timestamp")
        observed_unix = _unix_nano(
            receipt.get("commit_timestamp_observed_at"),
            "s6bm_v4_effect_commit_timestamp_observed",
        )
        readback_unix = _unix_nano(receipt.get("readback_at"), "s6bm_v4_effect_readback_timestamp")
        if not commit_unix <= observed_unix <= readback_unix:
            raise S6BMCausalError("s6bm_v4_effect_commit_timestamp_order")
        database_envelopes[request_id], database_anchors[request_id] = _database_clock_envelope(
            receipt, config
        )
    return by_request, event_by_request, database_envelopes, database_anchors


def validate_causal_bundle(
    private_root: Path,
    raw: Mapping[str, Any],
    config: S6BMConfig,
    *,
    compare_projection: bool = True,
) -> dict[str, Any]:
    if not config.schema_version.endswith(".v4"):
        raise S6BMCausalError("s6bm_v4_config_required")
    proof = _proof(raw)
    event_path = _resolve(
        private_root, dict(proof.get("causal_event_export", {})), "s6bm_v4_causal_events"
    )
    effect_path = _resolve(
        private_root, dict(proof.get("durable_effect_export", {})), "s6bm_v4_durable_effects"
    )
    trace_reference = dict(dict(raw.get("observability", {})).get("artifacts", {})).get(
        "trace_export", {}
    )
    trace_path = _resolve(private_root, dict(trace_reference), "s6bm_v4_trace_export")
    triton_receipt = dict(proof.get("triton_start_receipt", {}))
    collector_spec_path = _resolve(
        private_root,
        dict(triton_receipt.get("spec", {})),
        "s6bm_v4_collector_spec",
    )
    collector_result_path = _resolve(
        private_root,
        dict(triton_receipt.get("result", {})),
        "s6bm_v4_collector_result",
    )
    triton_trace_path = _resolve(
        private_root,
        dict(triton_receipt.get("raw_trace", {})),
        "s6bm_v4_triton_compute_trace",
    )
    events_export = _read_json(event_path, "s6bm_v4_causal_events")
    effects_export = _read_json(effect_path, "s6bm_v4_durable_effects")
    trace_export = _read_json(trace_path, "s6bm_v4_trace_export")
    triton_trace = _read_json(triton_trace_path, "s6bm_v4_triton_compute_trace")
    collector_spec = _read_json(collector_spec_path, "s6bm_v4_collector_spec")
    collector_result = _read_json(collector_result_path, "s6bm_v4_collector_result")
    projected_collector_result = {
        key: value
        for key, value in triton_receipt.items()
        if key not in {"spec", "result", "raw_trace", "stdout", "stderr"}
    }
    if collector_result != projected_collector_result:
        raise S6BMCausalError("s6bm_v4_collector_result_projection")
    if (
        collector_spec.get("schema_version") != "evm.s8_v4.s6bm_trace_collector_spec.v1"
        or collector_result.get("schema_version") != "evm.s8_v4.s6bm_trace_collector_result.v1"
        or collector_result.get("collector_spec_sha256") != sha256_file(collector_spec_path)
        or collector_result.get("raw_trace_sha256") != sha256_file(triton_trace_path)
    ):
        raise S6BMCausalError("s6bm_v4_collector_artifact_binding")

    attempt_id = str(raw.get("attempt_id", ""))
    records = [dict(item) for item in raw.get("request_records", [])]
    if not attempt_id or not records:
        raise S6BMCausalError("s6bm_v4_attempt_identity")
    expected_plan: dict[str, Any] | None = None
    required_bridge_items: list[dict[str, Any]] = []
    if raw.get("traffic_plan") is not None:
        expected_plan = build_continuity_plan(config, attempt_id)
        if dict(raw.get("traffic_plan", {})) != expected_plan:
            raise S6BMCausalError("s6bm_v4_continuity_plan_binding")
        required_bridge_items = [
            dict(item)
            for item in expected_plan["roles"]["bridge"]
            if item.get("actor_receipt_required") is True
        ]
    if (
        collector_spec.get("attempt_id") != attempt_id
        or collector_spec.get("source_revision") != raw.get("source_revision")
        or collector_spec.get("runner_process_id")
        != collector_result.get("collector_parent_process_id")
        or collector_spec.get("request_id") != collector_result.get("request_id")
        or collector_spec.get("trace_id") != collector_result.get("trace_id")
    ):
        raise S6BMCausalError("s6bm_v4_collector_spec_identity")
    if (
        events_export.get("attempt_id") != attempt_id
        or effects_export.get("attempt_id") != attempt_id
    ):
        raise S6BMCausalError("s6bm_v4_export_attempt_identity")
    events = [dict(item) for item in events_export.get("events", [])]
    if int(events_export.get("event_count", -1)) != len(events):
        raise S6BMCausalError("s6bm_v4_event_count")
    sequences = [int(item.get("causal_sequence", 0)) for item in events]
    if len(set(sequences)) != len(sequences) or sequences != sorted(sequences):
        raise S6BMCausalError("s6bm_v4_causal_sequence")
    transaction_ids = [str(item.get("transaction_id", "")) for item in events]
    if any(not item for item in transaction_ids) or len(set(transaction_ids)) != len(
        transaction_ids
    ):
        raise S6BMCausalError("s6bm_v4_transaction_identity")
    for event in events:
        if event.get("payload_sha256") != canonical_sha256(event.get("payload", {})):
            raise S6BMCausalError("s6bm_v4_event_payload_sha")

    by_type: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_type.setdefault(str(event.get("event_type", "")), []).append(event)
    allowed_types = {*START_STAGES, SWITCH_EVENT, EFFECT_EVENT, UNLOAD_EVENT}
    if set(by_type) != allowed_types or any(
        len(by_type.get(stage, [])) != 1 + len(required_bridge_items)
        for stage in START_STAGES
    ):
        raise S6BMCausalError("s6bm_v4_event_type_set")
    switch = _one(by_type.get(SWITCH_EVENT, []), "s6bm_v4_switch_event")
    unload = _one(by_type.get(UNLOAD_EVENT, []), "s6bm_v4_unload_event")
    effect_events = by_type.get(EFFECT_EVENT, [])
    by_request, event_by_request, database_envelopes, database_anchors = _validate_effects(
        records, effects_export, effect_events, switch, config
    )

    crossover = _event_identity(switch)
    hold_id = str(crossover["request_id"])
    if hold_id not in by_request or _record_identity(by_request[hold_id]) != crossover:
        raise S6BMCausalError("s6bm_v4_crossover_identity")
    starts = {
        stage: _one(
            [
                event
                for event in by_type.get(stage, [])
                if _event_identity(event) == crossover
            ],
            f"s6bm_v4_{stage}",
        )
        for stage in START_STAGES
    }
    for stage, event in starts.items():
        if _event_identity(event) != crossover or event.get("event_type") != stage:
            raise S6BMCausalError("s6bm_v4_start_receipt_binding")
    if _event_identity(switch) != crossover or _event_identity(unload) != crossover:
        raise S6BMCausalError("s6bm_v4_fence_identity")

    required_bridge_start_events: dict[str, dict[str, dict[str, Any]]] = {}
    for item in required_bridge_items:
        request_id = str(item["request_id"])
        record = by_request.get(request_id)
        if record is None:
            raise S6BMCausalError("s6bm_v4_continuity_request_missing")
        expected_identity = _record_identity(record)
        stage_events: dict[str, dict[str, Any]] = {}
        for stage in START_STAGES:
            event = _one(
                [
                    candidate
                    for candidate in by_type.get(stage, [])
                    if str(candidate.get("request_id", "")) == request_id
                ],
                f"s6bm_v4_continuity_{stage}",
            )
            if (
                _event_identity(event) != expected_identity
                or event.get("event_type") != stage
                or int(event.get("causal_sequence", 0))
                >= int(switch.get("causal_sequence", 0))
            ):
                raise S6BMCausalError("s6bm_v4_continuity_actor_receipt_fence")
            stage_events[stage] = event
        required_bridge_start_events[request_id] = stage_events
    expected_event_count = len(records) + 5 + 3 * len(required_bridge_items)
    if len(events) != expected_event_count or any(
        len(by_type.get(stage, [])) != 1 + len(required_bridge_items)
        for stage in START_STAGES
    ):
        raise S6BMCausalError("s6bm_v4_event_type_set")
    switch_payload = dict(switch.get("payload", {}))
    expected_sequences = {stage: int(starts[stage]["causal_sequence"]) for stage in START_STAGES}
    expected_hashes = {stage: starts[stage]["payload_sha256"] for stage in START_STAGES}
    expected_transactions = {stage: starts[stage]["transaction_id"] for stage in START_STAGES}
    if (
        switch_payload.get("receipt_sequences") != expected_sequences
        or switch_payload.get("receipt_payload_sha256") != expected_hashes
        or switch_payload.get("receipt_transaction_ids") != expected_transactions
        or max(expected_sequences.values()) >= int(switch["causal_sequence"])
    ):
        raise S6BMCausalError("s6bm_v4_switch_receipt_fence")
    continuity_receipt_fence: dict[str, Any] | None = None
    if required_bridge_items:
        continuity_receipt_fence = _validate_continuity_receipt_switch_fence(
            switch_event=switch,
            transition_receipt=dict(proof.get("route_transition_receipt", {})),
            required_start_events=required_bridge_start_events,
        )
    hold_effect = event_by_request[hold_id]
    if not int(switch["causal_sequence"]) < int(hold_effect["causal_sequence"]):
        raise S6BMCausalError("s6bm_v4_effect_before_switch")

    route_switch_ns, transition_projection = _validate_route_transition_receipt(
        receipt=dict(proof.get("route_transition_receipt", {})),
        switch_event=switch,
        crossover=crossover,
        raw=raw,
    )

    phase_bounds, envelope, clock_projection = _anchor_projection(raw, proof, config)
    if "green_active" not in phase_bounds or "green_only" not in phase_bounds:
        raise S6BMCausalError("s6bm_v4_transition_phase_anchor")
    observed_green_lower, observed_green_upper = phase_bounds["green_active"]
    if route_switch_ns > observed_green_lower:
        raise S6BMCausalError("s6bm_v4_transition_runner_observation_order")
    route_applied_lower = route_switch_ns
    route_applied_upper = route_switch_ns
    unload_lower, _unload_upper = phase_bounds["green_only"]
    hold = by_request[hold_id]
    receipt = dict(hold.get("durable_effect", {}))
    switch_fence = project_switch_fence_commit_interval(
        dict(proof.get("route_transition_receipt", {})), receipt, config
    )
    switch_fence_lower, switch_fence_upper = switch_fence[
        "fence_commit_interval_ns"
    ]

    spans = _spans(trace_export)
    trace_id = str(crossover["trace_id"])
    trace_spans = [item for item in spans if item["trace_id"] == trace_id]
    server = _one(
        [item for item in trace_spans if item["name"] == SERVER_SPAN],
        "s6bm_v4_server_span",
    )
    controller = _one(
        [item for item in trace_spans if item["name"] == "s6bm.controller.predict"],
        "s6bm_v4_controller_span",
    )
    inference = _one(
        [item for item in trace_spans if item["name"] == "s6bm.triton.infer"],
        "s6bm_v4_inference_span",
    )
    effect_span = _one(
        [item for item in trace_spans if item["name"] == "s6bm.terminal_effect.commit"],
        "s6bm_v4_effect_span",
    )
    model_span = _one(
        [
            item
            for item in trace_spans
            if item["resource"].get("service.name") == "triton-inference-server"
            and item["name"] == crossover["model_name"]
        ],
        "s6bm_v4_triton_model_span",
    )
    infer_request_span = _one(
        [
            item
            for item in trace_spans
            if item["resource"].get("service.name") == "triton-inference-server"
            and item["name"] == "InferRequest"
        ],
        "s6bm_v4_triton_infer_request_span",
    )
    compute_span = _one(
        [
            item
            for item in trace_spans
            if item["resource"].get("service.name") == "triton-inference-server"
            and item["name"] == "compute"
            and item["parent_span_id"] == model_span["span_id"]
        ],
        "s6bm_v4_triton_compute_span",
    )
    if (
        controller["parent_span_id"] != server["span_id"]
        or inference["parent_span_id"] != controller["span_id"]
        or infer_request_span["parent_span_id"] != inference["span_id"]
        or model_span["parent_span_id"] != infer_request_span["span_id"]
        or compute_span["parent_span_id"] != model_span["span_id"]
        or effect_span["parent_span_id"] != controller["span_id"]
        or model_span["attributes"].get("triton.request_id") != crossover["request_nonce"]
    ):
        raise S6BMCausalError("s6bm_v4_trace_topology")
    expected_attributes = {
        "evm.attempt.id": crossover["attempt_id"],
        "evm.run.id": crossover["run_id"],
        "evm.request.id": crossover["request_id"],
        "evm.effect.id": crossover["effect_id"],
        "evm.model.role": crossover["model_role"],
        "evm.model.name": crossover["model_name"],
        "evm.model.version": crossover["model_version"],
        "evm.model.artifact.sha256": crossover["artifact_sha256"],
    }
    for span in (server, controller, inference, effect_span):
        if any(span["attributes"].get(key) != value for key, value in expected_attributes.items()):
            raise S6BMCausalError("s6bm_v4_trace_identity_binding")
    if (
        effect_span["attributes"].get("evm.effect.transaction.id")
        != str(hold_effect["transaction_id"])
        or effect_span["attributes"].get("evm.effect.readback.visible") is not True
    ):
        raise S6BMCausalError("s6bm_v4_effect_span_receipt")

    raw_compute = dict(triton_trace.get("raw_compute_entry", {}))
    raw_model = dict(triton_trace.get("raw_model_entry", {}))
    if raw_compute != compute_span["raw"] or raw_model != model_span["raw"]:
        raise S6BMCausalError("s6bm_v4_triton_raw_trace_binding")
    if (
        triton_trace.get("trace_id") != trace_id
        or triton_trace.get("request_nonce") != crossover["request_nonce"]
        or triton_trace.get("model_name") != crossover["model_name"]
        or str(triton_trace.get("model_version")) != str(crossover["model_version"])
        or int(triton_trace.get("compute_start_unix_ns", 0)) != compute_span["start_unix_ns"]
    ):
        raise S6BMCausalError("s6bm_v4_triton_receipt_identity")
    triton_event_payload = dict(starts["triton_backend_compute_entry"].get("payload", {}))
    expected_backend = {
        "service_name": "triton-inference-server",
        "telemetry_sdk_language": "cpp",
        "model_request_span_id": model_span["span_id"],
        "compute_span_id": compute_span["span_id"],
        "compute_parent_span_id": model_span["span_id"],
    }
    if (
        triton_event_payload.get("schema_version") != "evm.s8_v4.s6bm_triton_actor_receipt.v1"
        or triton_event_payload.get("raw_trace_artifact_sha256") != sha256_file(triton_trace_path)
        or triton_event_payload.get("raw_trace_record_sha256") != canonical_sha256(triton_trace)
        or triton_event_payload.get("raw_trace_span_id") != compute_span["span_id"]
        or int(triton_event_payload.get("actor_start_unix_ns", 0)) != compute_span["start_unix_ns"]
        or triton_event_payload.get("backend_identity") != expected_backend
        or triton_receipt.get("backend_identity") != expected_backend
        or triton_event_payload.get("collector_spec_sha256") != sha256_file(collector_spec_path)
        or triton_event_payload.get("collector_nonce")
        != dict(triton_receipt.get("collector_observation", {})).get("anchor_nonce")
    ):
        raise S6BMCausalError("s6bm_v4_triton_receipt_raw_binding")

    actor_spans = {
        "api_server_handler_entry": server,
        "controller_entry": controller,
        "triton_backend_compute_entry": compute_span,
    }
    actor_intervals: dict[str, list[int]] = {}
    for stage, event in starts.items():
        payload = dict(event.get("payload", {}))
        start_unix = int(payload.get("actor_start_unix_ns", 0))
        projected = _project_unix(start_unix, envelope)
        actor_intervals[stage] = [projected[0], projected[1]]
        if projected[1] >= switch_fence_lower:
            raise S6BMCausalError(f"s6bm_v4_{stage}_after_switch")
        span_interval = _project_unix(actor_spans[stage]["start_unix_ns"], envelope)
        if span_interval[1] >= switch_fence_lower:
            raise S6BMCausalError(f"s6bm_v4_{stage}_span_after_switch")
        if abs(actor_spans[stage]["start_unix_ns"] - start_unix) > int(
            config.clock["max_phase_interval_ns"]
        ):
            raise S6BMCausalError(f"s6bm_v4_{stage}_span_receipt_gap")
        if stage != "triton_backend_compute_entry":
            local_before = int(payload.get("monotonic_before_ns", 0))
            local_after = int(payload.get("monotonic_after_ns", 0))
            if (
                local_before <= 0
                or not local_before <= local_after < switch_fence_lower
                or local_after - local_before > int(config.clock["max_anchor_width_ns"])
                or max(projected[0], local_before) - min(projected[1], local_after)
                > int(config.clock["max_offset_spread_ns"])
            ):
                raise S6BMCausalError(f"s6bm_v4_{stage}_local_clock")

    for backend_span, code in (
        (infer_request_span, "infer_request"),
        (model_span, "model"),
        (compute_span, "compute"),
    ):
        if _project_unix(backend_span["start_unix_ns"], envelope)[1] >= route_applied_lower:
            raise S6BMCausalError(f"s6bm_v4_triton_{code}_after_switch")

    bridge_actor_count = 0
    required_bridge_ids: list[str] = []
    gate_readback_intervals: list[dict[str, Any]] = []
    gate_readback_reference: dict[str, Any] | None = None
    if raw.get("traffic_plan") is not None:
        bridge_items = [dict(item) for item in expected_plan["roles"]["bridge"]]
        required_bridge_ids = [str(item["request_id"]) for item in required_bridge_items]
        required_request_set_sha = canonical_sha256(required_bridge_ids)
        execution = dict(raw.get("continuity_execution", {}))
        bridge_gate = dict(execution.get("bridge_actor_receipt_gate", {}))
        gate_events = [dict(item) for item in bridge_gate.get("events", [])]
        gate_readback_path = _resolve(
            private_root,
            dict(bridge_gate.get("raw_readback_export", {})),
            "s6bm_v4_continuity_actor_receipt_readback_export",
        )
        gate_readback_export = _read_json(
            gate_readback_path,
            "s6bm_v4_continuity_actor_receipt_readback_export",
        )
        gate_readback_reference = dict(bridge_gate["raw_readback_export"])
        gate_readback_rows = [
            dict(item) for item in gate_readback_export.get("events", [])
        ]
        expected_gate_keys = {
            (request_id, stage)
            for request_id in required_bridge_ids
            for stage in START_STAGES
        }
        selected_readback_rows = [
            item
            for item in gate_readback_rows
            if (str(item.get("request_id", "")), str(item.get("event_type", "")))
            in expected_gate_keys
        ]
        selected_readback_keys = [
            (str(item.get("request_id", "")), str(item.get("event_type", "")))
            for item in selected_readback_rows
        ]
        if (
            gate_readback_export.get("schema_version")
            != "evm.s8_v4.s6bm_causal_event_export.v1"
            or gate_readback_export.get("attempt_id") != attempt_id
            or int(gate_readback_export.get("event_count", -1))
            != len(gate_readback_rows)
            or int(bridge_gate.get("raw_readback_event_count", -1))
            != len(gate_readback_rows)
            or len(selected_readback_keys) != len(set(selected_readback_keys))
            or set(selected_readback_keys) != expected_gate_keys
        ):
            raise S6BMCausalError(
                "s6bm_v4_continuity_actor_receipt_readback_export"
            )
        stable_event_fields = (
            "causal_sequence",
            "event_type",
            "attempt_id",
            "run_id",
            "request_id",
            "request_nonce",
            "trace_id",
            "effect_id",
            "model_role",
            "model_name",
            "model_version",
            "artifact_sha256",
            "route_generation",
            "actor_identity",
            "payload_sha256",
            "payload",
            "transaction_id",
            "database_recorded_at",
        )
        for item in selected_readback_rows:
            request_id = str(item["request_id"])
            stage = str(item["event_type"])
            final_event = required_bridge_start_events[request_id][stage]
            if (
                any(item.get(key) != final_event.get(key) for key in stable_event_fields)
                or item.get("payload_sha256")
                != canonical_sha256(item.get("payload", {}))
                or not str(item.get("captured_at", ""))
            ):
                raise S6BMCausalError(
                    "s6bm_v4_continuity_actor_receipt_readback_binding"
                )
        expected_gate_events = sorted(
            (
                {
                    key: value
                    for key, value in {
                        **{
                            field: event[field]
                            for field in stable_event_fields
                            if field != "payload"
                        },
                        "readback_at": event["captured_at"],
                        "readback_visible": True,
                        "readback_source": "postgresql_attempt_export",
                    }.items()
                }
                for event in selected_readback_rows
            ),
            key=lambda item: (item["request_id"], item["event_type"]),
        )
        gate_satisfied_ns = int(
            _finite(
                bridge_gate.get("gate_satisfied_monotonic"),
                "s6bm_v4_continuity_gate_satisfied",
            )
            * 1e9
        )
        switch_invoked_ns = int(
            _finite(
                execution.get("switch_invoked_monotonic"),
                "s6bm_v4_continuity_switch_invoked",
            )
            * 1e9
        )
        gate_event_projection = [
            {key: item.get(key) for key in expected_gate_events[0]}
            for item in gate_events
        ] if expected_gate_events else []
        database_envelope = tuple(
            int(item) for item in switch_fence["database_offset_envelope_ns"]
        )
        for item in gate_events:
            database_interval = _project_unix(
                _unix_nano(
                    item.get("database_recorded_at"),
                    "s6bm_v4_continuity_receipt_database_time",
                ),
                database_envelope,
            )
            readback_interval = _project_unix(
                _unix_nano(
                    item.get("readback_at"),
                    "s6bm_v4_continuity_receipt_readback_time",
                ),
                database_envelope,
            )
            if (
                item.get("readback_visible") is not True
                or item.get("readback_source") != "postgresql_attempt_export"
                or database_interval[1] >= switch_fence_lower
                or readback_interval[1] >= switch_fence_lower
                or database_interval[1] > readback_interval[0]
            ):
                raise S6BMCausalError(
                    "s6bm_v4_continuity_actor_receipt_commit_readback"
                )
            gate_readback_intervals.append(
                {
                    "request_id": item.get("request_id"),
                    "event_type": item.get("event_type"),
                    "database_recorded_interval_ns": list(database_interval),
                    "readback_interval_ns": list(readback_interval),
                }
            )
        if any(
            int(item["readback_interval_ns"][1]) > gate_satisfied_ns
            for item in gate_readback_intervals
        ):
            raise S6BMCausalError(
                "s6bm_v4_continuity_actor_receipt_commit_readback"
            )
        if (
            bridge_gate.get("schema_version")
            != "evm.s8_v4.s6bm_bridge_actor_receipt_gate.v1"
            or bridge_gate.get("attempt_id") != attempt_id
            or int(bridge_gate.get("route_generation", 0))
            != int(transition_projection["old_route_generation"])
            or bridge_gate.get("required_request_ids") != required_bridge_ids
            or bridge_gate.get("required_request_set_sha256")
            != required_request_set_sha
            or int(bridge_gate.get("required_stage_count", 0)) != len(START_STAGES)
            or int(bridge_gate.get("expected_event_count", -1))
            != len(expected_gate_events)
            or int(bridge_gate.get("visible_event_count", -1))
            != len(expected_gate_events)
            or gate_event_projection != expected_gate_events
            or bridge_gate.get("selected_event_set_sha256")
            != canonical_sha256(expected_gate_events)
            or int(bridge_gate.get("maximum_visible_causal_sequence", 0))
            != max((int(item["causal_sequence"]) for item in expected_gate_events), default=0)
            or not 0 < gate_satisfied_ns < switch_invoked_ns
        ):
            raise S6BMCausalError("s6bm_v4_continuity_actor_receipt_gate")

        crossover_bridge_ids = [
            str(item["request_id"])
            for item in bridge_items
            if item.get("causal_crossover") is True
        ]
        expected_terminal_ids = sorted(
            set(str(item["request_id"]) for item in bridge_items)
            - set(crossover_bridge_ids)
        )
        terminal_gate = dict(execution.get("pre_switch_terminal_gate", {}))
        terminal_effect_path = _resolve(
            private_root,
            dict(terminal_gate.get("raw_effect_export", {})),
            "s6bm_v4_continuity_terminal_effect_export",
        )
        terminal_event_path = _resolve(
            private_root,
            dict(terminal_gate.get("raw_event_export", {})),
            "s6bm_v4_continuity_terminal_event_export",
        )
        terminal_effect_export = _read_json(
            terminal_effect_path, "s6bm_v4_continuity_terminal_effect_export"
        )
        terminal_event_export = _read_json(
            terminal_event_path, "s6bm_v4_continuity_terminal_event_export"
        )
        terminal_effect_rows = [
            dict(item) for item in terminal_effect_export.get("effects", [])
        ]
        terminal_event_rows = [
            dict(item)
            for item in terminal_event_export.get("events", [])
            if item.get("event_type") == EFFECT_EVENT
        ]
        terminal_effects_by_request = {
            str(item.get("idempotency_key", "")): item for item in terminal_effect_rows
        }
        terminal_events_by_request = {
            str(item.get("request_id", "")): item for item in terminal_event_rows
        }
        terminal_records: list[dict[str, Any]] = []
        try:
            terminal_records = sorted(
                (
                    s6bm_terminal_fence_record(
                        terminal_effects_by_request[request_id],
                        terminal_events_by_request[request_id],
                    )
                    for request_id in expected_terminal_ids
                ),
                key=lambda item: item["request_id"],
            )
        except (ControlPlaneParityError, KeyError) as exc:
            raise S6BMCausalError(
                "s6bm_v4_continuity_terminal_durable_binding"
            ) from exc
        transition_receipt = dict(proof.get("route_transition_receipt", {}))
        expected_terminal_set_sha = canonical_sha256(expected_terminal_ids)
        expected_terminal_records_sha = canonical_sha256(terminal_records)
        source_control = dict(switch_payload.get("source_payload", {}))
        expected_terminal_sequences = {
            item["request_id"]: item["causal_sequence"] for item in terminal_records
        }
        if (
            terminal_effect_export.get("schema_version")
            != "evm.s8_v4.s6bm_terminal_effect_export.v1"
            or terminal_effect_export.get("attempt_id") != attempt_id
            or int(terminal_effect_export.get("effect_count", -1))
            != len(terminal_effect_rows)
            or terminal_event_export.get("schema_version")
            != "evm.s8_v4.s6bm_causal_event_export.v1"
            or terminal_event_export.get("attempt_id") != attempt_id
            or int(terminal_event_export.get("event_count", -1))
            != len(terminal_event_export.get("events", []))
            or terminal_gate.get("schema_version")
            != "evm.s8_v4.s6bm_pre_switch_bridge_terminal_gate.v2"
            or terminal_gate.get("expected_terminal_request_ids")
            != expected_terminal_ids
            or terminal_gate.get("expected_terminal_request_set_sha256")
            != expected_terminal_set_sha
            or terminal_gate.get("observed_terminal_request_ids")
            != expected_terminal_ids
            or terminal_gate.get("observed_terminal_request_set_sha256")
            != expected_terminal_set_sha
            or terminal_gate.get("terminal_records") != terminal_records
            or terminal_gate.get("terminal_records_sha256")
            != expected_terminal_records_sha
            or terminal_gate.get("durable_readback_complete") is not True
            or source_control.get("continuity_terminal_request_ids")
            != expected_terminal_ids
            or source_control.get("continuity_terminal_request_set_sha256")
            != expected_terminal_set_sha
            or source_control.get("continuity_terminal_records_sha256")
            != expected_terminal_records_sha
            or switch_payload.get("continuity_terminal_request_ids")
            != expected_terminal_ids
            or switch_payload.get("continuity_terminal_request_set_sha256")
            != expected_terminal_set_sha
            or switch_payload.get("continuity_terminal_records_sha256")
            != expected_terminal_records_sha
            or switch_payload.get("continuity_terminal_sequences")
            != expected_terminal_sequences
            or transition_receipt.get("continuity_terminal_request_ids")
            != expected_terminal_ids
            or transition_receipt.get("continuity_terminal_request_set_sha256")
            != expected_terminal_set_sha
            or transition_receipt.get("continuity_terminal_records_sha256")
            != expected_terminal_records_sha
            or any(
                int(sequence) >= int(switch["causal_sequence"])
                for sequence in expected_terminal_sequences.values()
            )
        ):
            raise S6BMCausalError("s6bm_v4_continuity_terminal_fence")
        bridge_collector_receipts = [
            dict(item) for item in execution.get("bridge_triton_start_receipts", [])
        ]
        bridge_collectors_by_request = {
            str(item.get("request_id", "")): item for item in bridge_collector_receipts
        }
        if (
            len(bridge_collectors_by_request) != len(required_bridge_ids)
            or set(bridge_collectors_by_request) != set(required_bridge_ids)
            or bridge_gate.get("collector_request_ids") != required_bridge_ids
            or bridge_gate.get("collector_request_set_sha256")
            != required_request_set_sha
        ):
            raise S6BMCausalError("s6bm_v4_continuity_collector_set")
        for item in bridge_items:
            request_id = str(item["request_id"])
            bridge_record = by_request.get(request_id)
            if bridge_record is None:
                raise S6BMCausalError("s6bm_v4_continuity_request_missing")
            bridge_spans = [
                span for span in spans if span["trace_id"] == bridge_record["trace_id"]
            ]
            bridge_server = _one(
                [span for span in bridge_spans if span["name"] == SERVER_SPAN],
                "s6bm_v4_continuity_server_span",
            )
            bridge_controller = _one(
                [
                    span
                    for span in bridge_spans
                    if span["name"] == "s6bm.controller.predict"
                    and span["attributes"].get("evm.request.replayed") is False
                ],
                "s6bm_v4_continuity_controller_span",
            )
            bridge_inference = _one(
                [span for span in bridge_spans if span["name"] == "s6bm.triton.infer"],
                "s6bm_v4_continuity_inference_span",
            )
            bridge_effect = _one(
                [
                    span
                    for span in bridge_spans
                    if span["name"] == "s6bm.terminal_effect.commit"
                ],
                "s6bm_v4_continuity_effect_span",
            )
            bridge_infer_request = _one(
                [
                    span
                    for span in bridge_spans
                    if span["resource"].get("service.name") == "triton-inference-server"
                    and span["name"] == "InferRequest"
                ],
                "s6bm_v4_continuity_infer_request_span",
            )
            bridge_model = _one(
                [
                    span
                    for span in bridge_spans
                    if span["resource"].get("service.name") == "triton-inference-server"
                    and span["name"] == config.blue.model_name
                ],
                "s6bm_v4_continuity_model_span",
            )
            bridge_compute = _one(
                [
                    span
                    for span in bridge_spans
                    if span["resource"].get("service.name") == "triton-inference-server"
                    and span["name"] == "compute"
                    and span["parent_span_id"] == bridge_model["span_id"]
                ],
                "s6bm_v4_continuity_compute_span",
            )
            if (
                bridge_controller["parent_span_id"] != bridge_server["span_id"]
                or bridge_inference["parent_span_id"] != bridge_controller["span_id"]
                or bridge_infer_request["parent_span_id"] != bridge_inference["span_id"]
                or bridge_model["parent_span_id"] != bridge_infer_request["span_id"]
                or bridge_compute["parent_span_id"] != bridge_model["span_id"]
                or bridge_effect["parent_span_id"] != bridge_controller["span_id"]
            ):
                raise S6BMCausalError("s6bm_v4_continuity_trace_topology")
            for actor_span, code in (
                (bridge_server, "server"),
                (bridge_controller, "controller"),
                (bridge_model, "model"),
                (bridge_compute, "compute"),
            ):
                if (
                    _project_unix(actor_span["start_unix_ns"], envelope)[1]
                    >= switch_fence_lower
                ):
                    raise S6BMCausalError(f"s6bm_v4_continuity_{code}_after_switch")
            bridge_expected = {
                "evm.attempt.id": attempt_id,
                "evm.run.id": str(crossover["run_id"]),
                "evm.request.id": request_id,
                "evm.effect.id": bridge_record["effect_id"],
                "evm.model.role": "blue",
                "evm.model.name": config.blue.model_name,
                "evm.model.version": config.blue.model_version,
                "evm.model.artifact.sha256": config.blue.artifact_sha256,
            }
            for actor_span in (
                bridge_server,
                bridge_controller,
                bridge_inference,
                bridge_effect,
            ):
                if any(
                    actor_span["attributes"].get(key) != value
                    for key, value in bridge_expected.items()
                ):
                    raise S6BMCausalError("s6bm_v4_continuity_actor_binding")
            if (
                bridge_model["attributes"].get("triton.model_name")
                != config.blue.model_name
                or str(bridge_model["attributes"].get("triton.model_version"))
                != config.blue.model_version
                or bridge_model["attributes"].get("triton.request_id")
                != bridge_record["request_nonce"]
                or int(bridge_record.get("route_generation", 0))
                != int(dict(proof["route_transition_receipt"])["old_route_generation"])
            ):
                raise S6BMCausalError("s6bm_v4_continuity_model_binding")
            if request_id in required_bridge_start_events:
                required_events = required_bridge_start_events[request_id]
                stage_spans = {
                    "api_server_handler_entry": bridge_server,
                    "controller_entry": bridge_controller,
                    "triton_backend_compute_entry": bridge_compute,
                }
                for stage, start_event in required_events.items():
                    start_payload = dict(start_event.get("payload", {}))
                    start_unix_ns = int(start_payload.get("actor_start_unix_ns", 0))
                    if (
                        start_unix_ns <= 0
                        or _project_unix(start_unix_ns, envelope)[1]
                        >= switch_fence_lower
                        or abs(stage_spans[stage]["start_unix_ns"] - start_unix_ns)
                        > int(config.clock["max_phase_interval_ns"])
                        or start_payload.get("route_generation")
                        != int(transition_projection["old_route_generation"])
                    ):
                        raise S6BMCausalError(
                            "s6bm_v4_continuity_actor_receipt_order"
                        )
                collector = bridge_collectors_by_request[request_id]
                collector_spec_path = _resolve(
                    private_root,
                    dict(collector.get("spec", {})),
                    "s6bm_v4_continuity_collector_spec",
                )
                collector_result_path = _resolve(
                    private_root,
                    dict(collector.get("result", {})),
                    "s6bm_v4_continuity_collector_result",
                )
                collector_trace_path = _resolve(
                    private_root,
                    dict(collector.get("raw_trace", {})),
                    "s6bm_v4_continuity_collector_trace",
                )
                bridge_spec = _read_json(
                    collector_spec_path, "s6bm_v4_continuity_collector_spec"
                )
                bridge_result = _read_json(
                    collector_result_path, "s6bm_v4_continuity_collector_result"
                )
                bridge_trace = _read_json(
                    collector_trace_path, "s6bm_v4_continuity_collector_trace"
                )
                projected_bridge_result = {
                    key: value
                    for key, value in collector.items()
                    if key not in {"spec", "result", "raw_trace", "stdout", "stderr"}
                }
                triton_start_event = required_events["triton_backend_compute_entry"]
                triton_start_payload = dict(triton_start_event.get("payload", {}))
                result_receipt = dict(bridge_result.get("receipt", {}))
                if (
                    bridge_result != projected_bridge_result
                    or bridge_spec.get("attempt_id") != attempt_id
                    or bridge_spec.get("request_id") != request_id
                    or bridge_spec.get("trace_id") != bridge_record.get("trace_id")
                    or bridge_result.get("collector_spec_sha256")
                    != sha256_file(collector_spec_path)
                    or bridge_result.get("raw_trace_sha256")
                    != sha256_file(collector_trace_path)
                    or bridge_trace.get("request_nonce")
                    != bridge_record.get("request_nonce")
                    or bridge_trace.get("trace_id") != bridge_record.get("trace_id")
                    or int(bridge_trace.get("compute_start_unix_ns", 0))
                    != bridge_compute["start_unix_ns"]
                    or result_receipt.get("causal_sequence")
                    != triton_start_event.get("causal_sequence")
                    or result_receipt.get("transaction_id")
                    != triton_start_event.get("transaction_id")
                    or result_receipt.get("payload_sha256")
                    != triton_start_event.get("payload_sha256")
                    or triton_start_payload.get("raw_trace_artifact_sha256")
                    != sha256_file(collector_trace_path)
                    or triton_start_payload.get("raw_trace_record_sha256")
                    != canonical_sha256(bridge_trace)
                ):
                    raise S6BMCausalError(
                        "s6bm_v4_continuity_collector_binding"
                    )
            bridge_actor_count += 1

    attempted_ns = int(_finite(hold.get("attempted_monotonic"), "s6bm_v4_hold_start") * 1e9)
    completion_ns = int(_finite(hold.get("completed_monotonic"), "s6bm_v4_hold_completion") * 1e9)
    pre_switch_blue = [
        item
        for item in records
        if item.get("model_role") == "blue"
        and int(float(item.get("attempted_monotonic", math.inf)) * 1e9)
        < route_applied_lower
    ]
    stale_blue = [
        item
        for item in records
        if item.get("model_role") == "blue"
        and int(float(item.get("attempted_monotonic", 0)) * 1e9)
        >= route_applied_upper
    ]
    if stale_blue:
        raise S6BMCausalError("s6bm_v4_stale_blue_admission")
    commit_ack = int(receipt.get("commit_ack_monotonic_ns", 0))
    readback_end = int(receipt.get("readback_finished_monotonic_ns", 0))
    commit_interval = _project_unix(
        _unix_nano(receipt.get("commit_timestamp"), "s6bm_v4_hold_commit_timestamp"),
        database_envelopes[hold_id],
    )
    if not (
        attempted_ns < route_applied_lower <= route_applied_upper
        and switch_fence_upper < commit_ack <= readback_end <= completion_ns
    ):
        raise S6BMCausalError("s6bm_v4_hold_switch_effect_order")
    if not switch_fence_upper < commit_interval[0] <= commit_interval[1] <= completion_ns:
        raise S6BMCausalError("s6bm_v4_hold_commit_interval_order")
    effect_start_interval = _project_unix(effect_span["start_unix_ns"], envelope)
    effect_end_interval = _project_unix(effect_span["end_unix_ns"], envelope)
    _validate_hold_effect_span_order(
        effect_start_interval=effect_start_interval,
        commit_interval=commit_interval,
        effect_end_interval=effect_end_interval,
        effect_span=effect_span,
        controller_span=controller,
        server_span=server,
    )
    if completion_ns >= unload_lower:
        raise S6BMCausalError("s6bm_v4_unload_before_hold_completion")
    if completion_ns - attempted_ns < int(float(config.procedure["long_in_flight_hold_ms"]) * 1e6):
        raise S6BMCausalError("s6bm_v4_hold_duration")

    if any(
        int(float(item["completed_monotonic"]) * 1e9) >= unload_lower
        or int(dict(item.get("durable_effect", {})).get("readback_finished_monotonic_ns", 0))
        >= unload_lower
        for item in pre_switch_blue
    ):
        raise S6BMCausalError("s6bm_v4_unload_before_blue_terminal")
    pre_switch_ids = sorted(str(item["request_id"]) for item in pre_switch_blue)
    pre_switch_effects = sorted(
        (str(item["request_id"]), str(item["effect_id"])) for item in pre_switch_blue
    )
    unload_payload = dict(unload.get("payload", {}))
    last_effect_sequence = max(
        int(event_by_request[item]["causal_sequence"]) for item in pre_switch_ids
    )
    if (
        int(unload["causal_sequence"]) <= last_effect_sequence
        or int(unload_payload.get("switch_sequence", 0)) != int(switch["causal_sequence"])
        or int(unload_payload.get("last_terminal_effect_sequence", 0)) != last_effect_sequence
        or int(unload_payload.get("pre_switch_blue_request_count", -1)) != len(pre_switch_ids)
        or unload_payload.get("pre_switch_blue_request_set_sha256")
        != canonical_sha256(pre_switch_ids)
        or unload_payload.get("pre_switch_blue_effect_set_sha256")
        != canonical_sha256(pre_switch_effects)
        or int(raw.get("blue_in_flight_before_unload", -1)) != 0
    ):
        raise S6BMCausalError("s6bm_v4_unload_causal_fence")

    projection = {
        "schema_version": "evm.s8_v4.s6bm_causal_projection.v1",
        "attempt_id": attempt_id,
        "request_count": len(records),
        "durable_effect_count": len(effect_events),
        "causal_event_count": len(events),
        "clock": clock_projection,
        "crossover": {
            "request_id": hold_id,
            "trace_id": trace_id,
            "effect_id": crossover["effect_id"],
            "start_sequences": expected_sequences,
            "switch_sequence": int(switch["causal_sequence"]),
            "effect_sequence": int(hold_effect["causal_sequence"]),
            "unload_sequence": int(unload["causal_sequence"]),
            "actor_start_monotonic_intervals_ns": actor_intervals,
            "switch_fence_commit_interval_ns": [switch_fence_lower, switch_fence_upper],
            "switch_fence_projection": switch_fence,
            "route_applied_monotonic_interval_ns": [
                route_applied_lower,
                route_applied_upper,
            ],
            "runner_green_active_observation_interval_ns": [
                observed_green_lower,
                observed_green_upper,
            ],
            "route_transition_receipt": transition_projection,
            "effect_commit_ack_monotonic_ns": commit_ack,
            "effect_commit_monotonic_interval_ns": list(commit_interval),
            "database_clock_anchor": database_anchors[hold_id],
            "effect_readback_finished_monotonic_ns": readback_end,
            "request_completion_monotonic_ns": completion_ns,
            "blue_unload_monotonic_lower_ns": unload_lower,
            "pre_switch_blue_request_count": len(pre_switch_ids),
            "continuity_bridge_actor_bound_count": bridge_actor_count,
            "required_bridge_actor_receipt_mode": (
                "success_exact_set" if required_bridge_ids else "qualification_zero_set"
            ),
            "required_bridge_actor_receipt_count": len(required_bridge_ids),
            "required_bridge_actor_request_set_sha256": canonical_sha256(
                required_bridge_ids
            ),
            "required_bridge_actor_switch_fence": continuity_receipt_fence,
            "required_bridge_actor_commit_readback_intervals": gate_readback_intervals,
            "required_bridge_actor_raw_readback_export": gate_readback_reference,
        },
        "transaction_semantics": {
            "causal_sequence_is_auxiliary": True,
            "required_bridge_receipt_contract": (
                "success_exact_4x3" if required_bridge_ids else "qualification_exact_empty_set"
            ),
            "required_bridge_receipt_commit_readback_precedes_switch_fence": bool(
                required_bridge_ids
            ),
            "synchronous_commit": "on",
            "independent_readback": True,
            "effect_observed_committed_switch_fence": True,
            "exact_postgresql_commit_instant_claimed": False,
            "controller_span_is_commit_timestamp": False,
        },
        "passed": True,
    }
    recorded = raw.get("causal_projection")
    if compare_projection and recorded != projection:
        raise S6BMCausalError("s6bm_v4_causal_projection_mismatch")
    return projection

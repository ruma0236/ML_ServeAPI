from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from evm.scale_validation.s6bm_runtime import (
    S6BMConfig,
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
    if max(midpoint_offsets) - min(midpoint_offsets) > int(config.clock["max_offset_spread_ns"]):
        raise S6BMCausalError("s6bm_v4_clock_drift")
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
    envelope = (
        max(runner_envelope[0], collector_envelope[0]),
        min(runner_envelope[1], collector_envelope[1]),
    )
    if envelope[0] > envelope[1]:
        raise S6BMCausalError("s6bm_v4_clock_envelope_disjoint")
    collector_midpoint_offset = collector_unix - ((collector_before + collector_after) // 2)
    if max([*midpoint_offsets, collector_midpoint_offset]) - min(
        [*midpoint_offsets, collector_midpoint_offset]
    ) > int(config.clock["max_offset_spread_ns"]):
        raise S6BMCausalError("s6bm_v4_collector_clock_drift")

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
            "combined_offset_envelope_ns": list(envelope),
        },
    )


def _project_unix(unix_ns: int, envelope: tuple[int, int]) -> tuple[int, int]:
    offset_low, offset_high = envelope
    return unix_ns - offset_high, unix_ns - offset_low


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
    if after - before > int(config.clock["max_anchor_width_ns"]):
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


def _validate_effects(
    records: Sequence[Mapping[str, Any]],
    effects_export: Mapping[str, Any],
    effect_events: Sequence[Mapping[str, Any]],
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
            or durable_commit.get("schema_version") != "evm.s6bm.durable_commit.v2"
            or int(durable_commit.get("write_backend_pid", 0)) <= 0
        ):
            raise S6BMCausalError("s6bm_v4_effect_transaction_binding")
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
    starts = {stage: _one(by_type.get(stage, []), f"s6bm_v4_{stage}") for stage in START_STAGES}
    switch = _one(by_type.get(SWITCH_EVENT, []), "s6bm_v4_switch_event")
    unload = _one(by_type.get(UNLOAD_EVENT, []), "s6bm_v4_unload_event")
    effect_events = by_type.get(EFFECT_EVENT, [])
    allowed_types = {*START_STAGES, SWITCH_EVENT, EFFECT_EVENT, UNLOAD_EVENT}
    if set(by_type) != allowed_types or len(events) != len(records) + 5:
        raise S6BMCausalError("s6bm_v4_event_type_set")
    by_request, event_by_request, database_envelopes, database_anchors = _validate_effects(
        records, effects_export, effect_events, config
    )

    crossover = _event_identity(starts[START_STAGES[0]])
    hold_id = str(crossover["request_id"])
    if hold_id not in by_request or _record_identity(by_request[hold_id]) != crossover:
        raise S6BMCausalError("s6bm_v4_crossover_identity")
    for stage, event in starts.items():
        if _event_identity(event) != crossover or event.get("event_type") != stage:
            raise S6BMCausalError("s6bm_v4_start_receipt_binding")
    if _event_identity(switch) != crossover or _event_identity(unload) != crossover:
        raise S6BMCausalError("s6bm_v4_fence_identity")
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
    hold_effect = event_by_request[hold_id]
    if not int(switch["causal_sequence"]) < int(hold_effect["causal_sequence"]):
        raise S6BMCausalError("s6bm_v4_effect_before_switch")

    phase_bounds, envelope, clock_projection = _anchor_projection(raw, proof, config)
    if "green_active" not in phase_bounds or "green_only" not in phase_bounds:
        raise S6BMCausalError("s6bm_v4_transition_phase_anchor")
    switch_lower, switch_upper = phase_bounds["green_active"]
    unload_lower, _unload_upper = phase_bounds["green_only"]

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
        if projected[1] >= switch_lower:
            raise S6BMCausalError(f"s6bm_v4_{stage}_after_switch")
        span_interval = _project_unix(actor_spans[stage]["start_unix_ns"], envelope)
        if span_interval[1] >= switch_lower:
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
                or not local_before <= local_after < switch_lower
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
        if _project_unix(backend_span["start_unix_ns"], envelope)[1] >= switch_lower:
            raise S6BMCausalError(f"s6bm_v4_triton_{code}_after_switch")

    hold = by_request[hold_id]
    attempted_ns = int(_finite(hold.get("attempted_monotonic"), "s6bm_v4_hold_start") * 1e9)
    completion_ns = int(_finite(hold.get("completed_monotonic"), "s6bm_v4_hold_completion") * 1e9)
    pre_switch_blue = [
        item
        for item in records
        if item.get("model_role") == "blue"
        and int(float(item.get("attempted_monotonic", math.inf)) * 1e9) < switch_lower
    ]
    stale_blue = [
        item
        for item in records
        if item.get("model_role") == "blue"
        and int(float(item.get("attempted_monotonic", 0)) * 1e9) >= switch_upper
    ]
    if stale_blue:
        raise S6BMCausalError("s6bm_v4_stale_blue_admission")
    receipt = dict(hold.get("durable_effect", {}))
    commit_ack = int(receipt.get("commit_ack_monotonic_ns", 0))
    readback_end = int(receipt.get("readback_finished_monotonic_ns", 0))
    commit_interval = _project_unix(
        _unix_nano(receipt.get("commit_timestamp"), "s6bm_v4_hold_commit_timestamp"),
        database_envelopes[hold_id],
    )
    if (
        not attempted_ns
        < switch_lower
        <= switch_upper
        < commit_ack
        <= readback_end
        <= completion_ns
    ):
        raise S6BMCausalError("s6bm_v4_hold_switch_effect_order")
    if not switch_upper < commit_interval[0] <= commit_interval[1] <= completion_ns:
        raise S6BMCausalError("s6bm_v4_hold_commit_interval_order")
    effect_start_interval = _project_unix(effect_span["start_unix_ns"], envelope)
    effect_end_interval = _project_unix(effect_span["end_unix_ns"], envelope)
    controller_end_interval = _project_unix(controller["end_unix_ns"], envelope)
    server_end_interval = _project_unix(server["end_unix_ns"], envelope)
    if not (
        effect_start_interval[1]
        <= commit_interval[0]
        <= commit_interval[1]
        <= effect_end_interval[0]
        and effect_end_interval[1] <= controller_end_interval[0]
        and controller_end_interval[1] <= server_end_interval[0]
    ):
        raise S6BMCausalError("s6bm_v4_hold_effect_span_order")
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
            "switch_monotonic_interval_ns": [switch_lower, switch_upper],
            "effect_commit_ack_monotonic_ns": commit_ack,
            "effect_commit_monotonic_interval_ns": list(commit_interval),
            "database_clock_anchor": database_anchors[hold_id],
            "effect_readback_finished_monotonic_ns": readback_end,
            "request_completion_monotonic_ns": completion_ns,
            "blue_unload_monotonic_lower_ns": unload_lower,
            "pre_switch_blue_request_count": len(pre_switch_ids),
        },
        "transaction_semantics": {
            "sequence_order_proven": True,
            "synchronous_commit": "on",
            "independent_readback": True,
            "exact_postgresql_commit_instant_claimed": False,
            "controller_span_is_commit_timestamp": False,
        },
        "passed": True,
    }
    recorded = raw.get("causal_projection")
    if compare_projection and recorded != projection:
        raise S6BMCausalError("s6bm_v4_causal_projection_mismatch")
    return projection

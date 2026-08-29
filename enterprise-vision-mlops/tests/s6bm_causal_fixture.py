from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from evm.control_panel.transactional_store import s6bm_terminal_fence_record
from evm.model_runtime.triton_blue_green import (
    TritonBlueGreenControlRequest,
    action_digest,
)
from evm.scale_validation.s6bm_runtime import (
    S6BMConfig,
    canonical,
    canonical_sha256,
    sha256_file,
)


OFFSET_NS = 1_799_999_920_000_000_000
SOURCE_REVISION = "a" * 40
RUNNER_PID = 9001
COLLECTOR_PID = 9002


def _iso(unix_ns: int) -> str:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return (
        (epoch + timedelta(microseconds=unix_ns // 1_000))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _write(root: Path, relative: str, payload: Any) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    return {"stringValue": str(value)}


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": _value(value)} for key, value in values.items()]


def _entry(
    *,
    name: str,
    trace_id: str,
    span_id: str,
    parent_span_id: str,
    start_ns: int,
    end_ns: int,
    attributes: dict[str, Any],
    service_name: str,
) -> dict[str, Any]:
    return {
        "resource": {
            "attributes": _attributes(
                {
                    "service.name": service_name,
                    "telemetry.sdk.language": "cpp"
                    if service_name == "triton-inference-server"
                    else "python",
                }
            )
        },
        "scope": {"name": "s6bm.synthetic.fixture"},
        "span": {
            "name": name,
            "traceId": trace_id,
            "spanId": span_id,
            "parentSpanId": parent_span_id,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(end_ns),
            "attributes": _attributes(attributes),
            "status": {},
        },
    }


def _span_id(request_id: str, name: str) -> str:
    return hashlib.sha256(f"{request_id}:{name}".encode("ascii")).hexdigest()[:16]


def _record_spans(record: dict[str, Any], config: S6BMConfig) -> list[dict[str, Any]]:
    start = int(float(record["attempted_monotonic"]) * 1_000_000_000) + OFFSET_NS
    completion = int(float(record["completed_monotonic"]) * 1_000_000_000) + OFFSET_NS
    server_id = _span_id(record["request_id"], "server")
    controller_id = _span_id(record["request_id"], "controller")
    inference_id = _span_id(record["request_id"], "inference")
    infer_request_id = _span_id(record["request_id"], "infer-request")
    model_id = _span_id(record["request_id"], "model")
    compute_id = _span_id(record["request_id"], "compute")
    effect_id = _span_id(record["request_id"], "effect")
    expected = {
        "evm.attempt.id": record["attempt_id"],
        "evm.run.id": record["run_id"],
        "evm.request.id": record["request_id"],
        "evm.effect.id": record["effect_id"],
        "evm.model.role": record["model_role"],
        "evm.model.name": record["model_name"],
        "evm.model.version": record["model_version"],
        "evm.model.artifact.sha256": record["artifact_sha256"],
    }
    server_start = start + 1_000_000
    controller_start = start + 2_000_000
    inference_start = start + 3_000_000
    infer_request_start = start + 4_000_000
    model_start = start + 5_000_000
    compute_start = start + 6_000_000
    effect_start = completion - 10_000_000
    end = completion - 1_000_000
    return [
        _entry(
            name="POST /control-panel/v1/scenario-workloads/triton-blue-green/predict",
            trace_id=record["trace_id"],
            span_id=server_id,
            parent_span_id="",
            start_ns=server_start,
            end_ns=end,
            attributes=expected,
            service_name="enterprise-vision-mlops-api",
        ),
        _entry(
            name="s6bm.controller.predict",
            trace_id=record["trace_id"],
            span_id=controller_id,
            parent_span_id=server_id,
            start_ns=controller_start,
            end_ns=end,
            attributes={**expected, "evm.request.replayed": False},
            service_name="enterprise-vision-mlops-api",
        ),
        _entry(
            name="s6bm.triton.infer",
            trace_id=record["trace_id"],
            span_id=inference_id,
            parent_span_id=controller_id,
            start_ns=inference_start,
            end_ns=end - 3_000_000,
            attributes=expected,
            service_name="enterprise-vision-mlops-api",
        ),
        _entry(
            name="InferRequest",
            trace_id=record["trace_id"],
            span_id=infer_request_id,
            parent_span_id=inference_id,
            start_ns=infer_request_start,
            end_ns=end - 4_000_000,
            attributes={},
            service_name="triton-inference-server",
        ),
        _entry(
            name=config.blue.model_name,
            trace_id=record["trace_id"],
            span_id=model_id,
            parent_span_id=infer_request_id,
            start_ns=model_start,
            end_ns=end - 5_000_000,
            attributes={
                "triton.model_name": config.blue.model_name,
                "triton.model_version": config.blue.model_version,
                "triton.request_id": record["request_nonce"],
            },
            service_name="triton-inference-server",
        ),
        _entry(
            name="compute",
            trace_id=record["trace_id"],
            span_id=compute_id,
            parent_span_id=model_id,
            start_ns=compute_start,
            end_ns=end - 6_000_000,
            attributes={},
            service_name="triton-inference-server",
        ),
        _entry(
            name="s6bm.terminal_effect.commit",
            trace_id=record["trace_id"],
            span_id=effect_id,
            parent_span_id=controller_id,
            start_ns=effect_start,
            end_ns=end,
            attributes={
                **expected,
                "evm.effect.transaction.id": str(record["fixture_transaction_id"]),
                "evm.effect.readback.visible": True,
            },
            service_name="enterprise-vision-mlops-api",
        ),
    ]


def _clock_anchor(
    *,
    sequence: int,
    phase: str,
    monotonic_ns: int,
    nonce: str,
    previous: str | None,
    attempt_id: str,
    run_identity: str,
) -> dict[str, Any]:
    anchor = {
        "schema_version": "evm.s8_v4.s6bm_dual_clock_anchor.v3",
        "sequence": sequence,
        "phase": phase,
        "monotonic_before_ns": monotonic_ns + 1_000,
        "monotonic_after_ns": monotonic_ns + 1_100,
        "unix_ns": monotonic_ns + 1_050 + OFFSET_NS,
        "anchor_nonce": nonce,
        "previous_anchor_hash": previous,
        "host_identity": "fixture-host",
        "process_id": RUNNER_PID,
        "source_identity": f"runner:{SOURCE_REVISION}:{run_identity}:{attempt_id}:{nonce}",
    }
    anchor["anchor_hash"] = canonical_sha256(anchor)
    return anchor


def _database_candidates(
    transaction_id: str, base_ns: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = []
    schema = "evm_s6bm_v4_fixture"
    for sequence in range(1, 9):
        before = base_ns + sequence * 2_000
        after = before + 100 + sequence
        nonce = hashlib.sha256(f"{transaction_id}:{sequence}".encode("ascii")).hexdigest()[:32]
        database_unix_ns = ((((before + after) // 2) + OFFSET_NS) // 1_000) * 1_000
        candidate = {
            "schema_version": "evm.s6bm.database_clock_anchor.v2",
            "sequence": sequence,
            "anchor_nonce": nonce,
            "schema_name": schema,
            "transaction_id": transaction_id,
            "backend_pid": 222,
            "clock_source": "postgresql_clock_timestamp",
            "database_clock_timestamp": _iso(database_unix_ns),
            "database_unix_ns": database_unix_ns,
            "monotonic_before_ns": before,
            "monotonic_after_ns": after,
            "source_identity": f"postgresql:{schema}:{transaction_id}:222:{nonce}",
        }
        candidate["anchor_hash"] = canonical_sha256(candidate)
        candidates.append(candidate)
    return candidates, candidates[0]


def _event(
    identity: dict[str, Any], event_type: str, sequence: int, payload: dict[str, Any]
) -> dict[str, Any]:
    database_ns = int(payload.get("fixture_database_ns", 90_000_000_000))
    event_payload = {key: value for key, value in payload.items() if key != "fixture_database_ns"}
    return {
        "event_type": event_type,
        **identity,
        "actor_identity": f"fixture:{event_type}",
        "causal_sequence": sequence,
        "transaction_id": str(40_000 + sequence),
        "database_recorded_at": _iso(database_ns + OFFSET_NS),
        "captured_at": _iso(database_ns + 500_000 + OFFSET_NS),
        "payload": event_payload,
    } | {"payload_sha256": canonical_sha256(event_payload)}


def _collector(
    root: Path,
    *,
    record: dict[str, Any],
    entries: list[dict[str, Any]],
    sequence: int,
    relative_root: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model_entry = next(item for item in entries if item["span"]["name"] == record["model_name"])
    compute_entry = next(item for item in entries if item["span"]["name"] == "compute")
    raw_trace = {
        "schema_version": "evm.s8_v4.s6bm_triton_compute_trace.v1",
        "attempt_id": record["attempt_id"],
        "request_id": record["request_id"],
        "request_nonce": record["request_nonce"],
        "trace_id": record["trace_id"],
        "model_name": record["model_name"],
        "model_version": record["model_version"],
        "compute_start_unix_ns": int(compute_entry["span"]["startTimeUnixNano"]),
        "raw_model_entry": copy.deepcopy(model_entry),
        "raw_compute_entry": copy.deepcopy(compute_entry),
    }
    spec = {
        "schema_version": "evm.s8_v4.s6bm_trace_collector_spec.v1",
        "attempt_id": record["attempt_id"],
        "source_revision": SOURCE_REVISION,
        "runner_process_id": RUNNER_PID,
        "request_id": record["request_id"],
        "trace_id": record["trace_id"],
    }
    spec_ref = _write(root, f"{relative_root}/collector-spec.json", spec)
    trace_ref = _write(root, f"{relative_root}/triton-compute-start.json", raw_trace)
    collector_nonce = hashlib.sha256(
        f"collector:{record['request_id']}".encode("ascii")
    ).hexdigest()[:32]
    collector_observation = {
        "schema_version": "evm.s8_v4.s6bm_dual_clock_anchor.v3",
        "sequence": 1,
        "phase": "triton_compute_receipt_collected",
        "monotonic_before_ns": 90_000_000_000,
        "monotonic_after_ns": 90_000_000_100,
        "unix_ns": 90_000_000_050 + OFFSET_NS,
        "anchor_nonce": collector_nonce,
        "previous_anchor_hash": None,
        "host_identity": "fixture-host",
        "process_id": COLLECTOR_PID,
        "parent_process_id": RUNNER_PID,
        "source_identity": (
            f"collector:{SOURCE_REVISION}:unit-test:{record['attempt_id']}:"
            f"{COLLECTOR_PID}:{collector_nonce}"
        ),
    }
    collector_observation["anchor_hash"] = canonical_sha256(collector_observation)
    backend_identity = {
        "service_name": "triton-inference-server",
        "telemetry_sdk_language": "cpp",
        "model_request_span_id": model_entry["span"]["spanId"],
        "compute_span_id": compute_entry["span"]["spanId"],
        "compute_parent_span_id": model_entry["span"]["spanId"],
    }
    payload = {
        "schema_version": "evm.s8_v4.s6bm_triton_actor_receipt.v1",
        **{
            key: record[key]
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
        },
        "actor_identity": "triton:fixture",
        "actor_start_unix_ns": int(compute_entry["span"]["startTimeUnixNano"]),
        "backend_identity": backend_identity,
        "collector_nonce": collector_nonce,
        "collector_observation": collector_observation,
        "collector_parent_process_id": RUNNER_PID,
        "collector_process_id": COLLECTOR_PID,
        "collector_source_identity": collector_observation["source_identity"],
        "collector_spec_sha256": spec_ref["sha256"],
        "raw_trace_artifact_sha256": trace_ref["sha256"],
        "raw_trace_record_sha256": canonical_sha256(raw_trace),
        "raw_trace_span_id": compute_entry["span"]["spanId"],
        "gpu_uuid": "GPU-fixture",
        "trace_event_name": "COMPUTE_START",
        "triton_container_id": "fixture-container",
        "triton_image_digest": "sha256:" + "b" * 64,
    }
    event = _event(record, "triton_backend_compute_entry", sequence, payload)
    receipt = {
        "schema_version": "evm.s6bm.causal_receipt.v1",
        **{
            key: event[key]
            for key in (
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
                "causal_sequence",
                "transaction_id",
                "payload",
                "payload_sha256",
                "database_recorded_at",
            )
        },
        "actor_identity": "triton:fixture",
        "readback_at": _iso(91_000_000_000 + OFFSET_NS),
        "readback_visible": True,
        "replayed": False,
    }
    collector = {
        "schema_version": "evm.s8_v4.s6bm_trace_collector_result.v1",
        "attempt_id": record["attempt_id"],
        "request_id": record["request_id"],
        "trace_id": record["trace_id"],
        "collector_parent_process_id": RUNNER_PID,
        "collector_process_id": COLLECTOR_PID,
        "collector_spec_sha256": spec_ref["sha256"],
        "raw_trace_sha256": trace_ref["sha256"],
        "raw_record_sha256": canonical_sha256(raw_trace),
        "backend_identity": backend_identity,
        "collector_observation": collector_observation,
        "receipt": receipt,
        "spec": spec_ref,
        "raw_trace": trace_ref,
    }
    result_payload = {
        key: value
        for key, value in collector.items()
        if key not in {"spec", "result", "raw_trace", "stdout", "stderr"}
    }
    collector["result"] = _write(root, f"{relative_root}/collector-result.json", result_payload)
    return collector, event


def materialize_causal_mutation_bundle(
    root: Path,
    raw: dict[str, Any],
    config: S6BMConfig,
) -> dict[str, Any]:
    raw = copy.deepcopy(raw)
    raw["source_revision"] = SOURCE_REVISION
    plan = raw["traffic_plan"]
    hold_id = str(plan["roles"]["causal_hold"][0]["request_id"])
    bridge_ids = [str(item["request_id"]) for item in plan["roles"]["bridge"]]
    required_ids = [
        str(item["request_id"])
        for item in plan["roles"]["bridge"]
        if item["actor_receipt_required"] is True
    ]
    crossover_id = str(plan["bridge_subsets"]["crossover"]["request_ids"][0])
    selected_ids = {hold_id, *bridge_ids}
    records = [item for item in raw["request_records"] if item["request_id"] in selected_ids]
    raw["request_records"] = records
    by_request = {str(item["request_id"]): item for item in records}
    hold = by_request[hold_id]
    blue_identity_payload = {
        "role": "blue",
        "model_name": config.blue.model_name,
        "model_version": config.blue.model_version,
        "artifact_sha256": config.blue.artifact_sha256,
        "config_sha256": config.blue.config_sha256,
        "expected_output": list(config.blue.expected_output),
    }
    green_identity_payload = {
        "role": "green",
        "model_name": config.green.model_name,
        "model_version": config.green.model_version,
        "artifact_sha256": config.green.artifact_sha256,
        "config_sha256": config.green.config_sha256,
        "expected_output": list(config.green.expected_output),
    }
    blue_identity_sha256 = canonical_sha256(blue_identity_payload)
    green_identity_sha256 = canonical_sha256(green_identity_payload)
    lease_id = "lease-fixture"
    fencing_token_sha256 = "d" * 64
    active_blue_sha256 = canonical_sha256(
        {
            "routes": [
                {
                    "role": "blue",
                    "weight": 100,
                    "identity_sha256": blue_identity_sha256,
                }
            ]
        }
    )
    route_source_payload = {
        "schema_version": "evm.s6bm.route_revision.v1",
        "run_id": hold["run_id"],
        "source_revision": SOURCE_REVISION,
        "control_generation": 2,
        "route_generation": 2,
        "phase": "blue_active_rollback",
        "route_weights": {"blue": 100, "green": 0},
        "loaded_roles": ["blue", "green"],
        "active_route_identity_sha256": active_blue_sha256,
        "blue_identity_sha256": blue_identity_sha256,
        "green_identity_sha256": green_identity_sha256,
        "image_digest": raw["identities"]["image_digest"],
        "gpu_uuid": raw["identities"]["gpu_uuid"],
        "action": "blue_switched",
        "approval_id": "approval-blue-switched-fixture",
        "used_approvals": ["approval-blue-switched-fixture"],
        "route_changed": True,
        "lease_id": lease_id,
        "fencing_token_sha256": fencing_token_sha256,
        "transition_id": None,
        "transition_new_route_generation": None,
    }
    observed_route_revision = {
        "schema_version": "evm.s6bm.observed_route_revision.v1",
        "run_id": hold["run_id"],
        "route_generation": 2,
        "route_source_control_generation": 2,
        "route_source_action": route_source_payload["action"],
        "route_source_phase": route_source_payload["phase"],
        "route_source_payload_sha256": canonical_sha256(route_source_payload),
        "route_source_transaction_id": "49002",
        "route_source_database_recorded_at": "2026-08-25T00:00:00.490Z",
        "route_source_payload": route_source_payload,
        "active_route_identity_sha256": active_blue_sha256,
        "blue_identity_sha256": blue_identity_sha256,
        "green_identity_sha256": green_identity_sha256,
        "transition_id": None,
        "transition_new_route_generation": None,
        "lease_binding_control_generation": 2,
        "lease_binding_payload_sha256": canonical_sha256(route_source_payload),
        "lease_binding_transaction_id": "49002",
        "lease_binding_payload": route_source_payload,
        "lease_id": lease_id,
        "fencing_token_sha256": fencing_token_sha256,
    }
    for ordinal, record in enumerate(records, start=1):
        record["fixture_transaction_id"] = str(50_000 + ordinal)
        record["route_generation"] = 2
        record["route_identity_sha256"] = blue_identity_sha256
        record["lease_id_sha256"] = hashlib.sha256(lease_id.encode("utf-8")).hexdigest()
        record["fencing_token_sha256"] = fencing_token_sha256

    previous = None
    for sequence, item in enumerate(raw["phase_timeline"], start=1):
        monotonic_ns = int(float(item["monotonic_seconds"]) * 1_000_000_000)
        anchor = _clock_anchor(
            sequence=sequence,
            phase=str(item["phase"]),
            monotonic_ns=monotonic_ns,
            nonce="c" * 32,
            previous=previous,
            attempt_id=str(raw["attempt_id"]),
            run_identity="unit-test",
        )
        item["clock_anchor"] = anchor
        previous = anchor["anchor_hash"]

    trace_entries: list[dict[str, Any]] = []
    spans_by_request: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        entries = _record_spans(record, config)
        spans_by_request[str(record["request_id"])] = entries
        trace_entries.extend(entries)
    trace_export = {
        "schema_version": "evm.s8_v4.s6bm_otlp_export.v1",
        "attempt_id": raw["attempt_id"],
        "span_count": len(trace_entries),
        "trace_count": len(records),
        "entries": trace_entries,
    }
    trace_ref = _write(root, "observability/raw-otlp-spans.json", trace_export)

    events: list[dict[str, Any]] = []
    collectors: dict[str, dict[str, Any]] = {}
    sequence = 1
    for request_id in [hold_id, *required_ids]:
        record = by_request[request_id]
        entries = spans_by_request[request_id]
        for stage, span_name in (
            (
                "api_server_handler_entry",
                "POST /control-panel/v1/scenario-workloads/triton-blue-green/predict",
            ),
            ("controller_entry", "s6bm.controller.predict"),
        ):
            span = next(item for item in entries if item["span"]["name"] == span_name)
            start_ns = int(span["span"]["startTimeUnixNano"])
            payload = {
                **{
                    key: record[key]
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
                },
                "schema_version": "evm.s6bm.actor_start_observation.v1",
                "actor_identity": f"fixture:{stage}",
                "actor_start_unix_ns": start_ns,
                "monotonic_before_ns": start_ns - OFFSET_NS,
                "monotonic_after_ns": start_ns - OFFSET_NS + 100,
                "host_identity": "fixture-host",
                "process_id": RUNNER_PID,
                "thread_id": 1,
                "service_instance_id": "fixture-replica",
                "source_revision": SOURCE_REVISION,
            }
            events.append(_event(record, stage, sequence, payload))
            sequence += 1
        collector, compute_event = _collector(
            root,
            record=record,
            entries=entries,
            sequence=sequence,
            relative_root=f"causal/{request_id}",
        )
        collectors[request_id] = collector
        events.append(compute_event)
        sequence += 1

    effect_rows: list[dict[str, Any]] = []
    effect_events: list[dict[str, Any]] = []

    def add_effect(
        record: dict[str, Any], *, requires_switch: bool, observed: dict[str, Any] | None
    ) -> None:
        nonlocal sequence
        commit_ns = int(float(record["completed_monotonic"]) * 1_000_000_000) - 5_000_000
        transaction_id = str(record["fixture_transaction_id"])
        event_payload = {
            **{
                key: record[key]
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
            },
            "schema_version": "evm.s8_v4.s6bm_terminal_causal_event.v1",
            "result_sha256": record["result_sha256"],
            "route_identity_sha256": record["route_identity_sha256"],
            "route_revision_binding_required": True,
            "lease_id": lease_id,
            "fencing_token_sha256": fencing_token_sha256,
            "requires_switch_before_effect": requires_switch,
            "observed_route_revision": copy.deepcopy(observed_route_revision),
            **({"observed_transition": observed} if observed is not None else {}),
        }
        event = _event(
            record,
            "durable_terminal_effect_commit",
            sequence,
            {**event_payload, "fixture_database_ns": commit_ns - 1_000_000},
        )
        event["transaction_id"] = transaction_id
        event["payload_sha256"] = canonical_sha256(event["payload"])
        durable_commit = {
            "schema_version": "evm.s6bm.durable_commit.v3",
            "database_recorded_at": _iso(commit_ns - 2_000_000 + OFFSET_NS),
            "transaction_id": transaction_id,
            "write_backend_pid": 111,
            "synchronous_commit": "on",
            "causal_sequence": sequence,
            "causal_payload_sha256": event["payload_sha256"],
            "observed_transition": observed,
            "observed_route_revision": copy.deepcopy(observed_route_revision),
        }
        entity_payload = {
            "schema_version": "evm.s8_v4.s6bm_terminal_effect.v1",
            "attempt_id": record["attempt_id"],
            "run_id": record["run_id"],
            "request_id": record["request_id"],
            "trace_id": record["trace_id"],
            "effect_id": record["effect_id"],
            "offered_identity": copy.deepcopy(record["offered_identity"]),
            "served_identity": copy.deepcopy(record["offered_identity"]),
            "route_generation": record["route_generation"],
            "route_identity_sha256": record["route_identity_sha256"],
            "result_sha256": record["result_sha256"],
            "terminal_outcome": "completed",
            "durable_commit": durable_commit,
            "observed_route_revision": copy.deepcopy(observed_route_revision),
            **({"observed_transition": observed} if observed is not None else {}),
        }
        request_sha = hashlib.sha256(f"request:{record['request_id']}".encode("ascii")).hexdigest()
        candidates, selected = _database_candidates(transaction_id, commit_ns)
        timestamp_start = candidates[0]["monotonic_before_ns"] - 1_000
        timestamp_end = candidates[-1]["monotonic_after_ns"] + 1_000
        readback_end = timestamp_end + 2_000
        receipt = {
            "schema_version": "evm.s6bm.durable_effect_receipt.v4",
            "entity_kind": "s6bm_terminal_effect",
            "entity_id": record["effect_id"],
            "request_sha256": request_sha,
            "stored_payload_sha256": canonical_sha256(entity_payload),
            "database_recorded_at": durable_commit["database_recorded_at"],
            "entity_created_at": _iso(commit_ns - 1_500_000 + OFFSET_NS),
            "idempotency_created_at": _iso(commit_ns - 1_000_000 + OFFSET_NS),
            "readback_at": _iso(readback_end + OFFSET_NS),
            "transaction_id": transaction_id,
            "write_backend_pid": 111,
            "synchronous_commit": "on",
            "commit_ack_monotonic_ns": timestamp_start - 4_000,
            "commit_timestamp": _iso(commit_ns + OFFSET_NS),
            "commit_timestamp_observed_at": selected["database_clock_timestamp"],
            "commit_timestamp_backend_pid": 222,
            "commit_timestamp_tracking": "on",
            "commit_timestamp_visible": True,
            "separate_connection_readback": True,
            "commit_timestamp_readback_lane": "bounded_parallel_post_commit_v1",
            "commit_timestamp_readback_concurrency_limit": 2,
            "commit_timestamp_readback_in_flight_at_acquire": 1,
            "commit_timestamp_readback_max_in_flight_observed": 1,
            "commit_timestamp_readback_wait_started_monotonic_ns": timestamp_start - 3_000,
            "commit_timestamp_readback_acquired_monotonic_ns": timestamp_start - 2_000,
            "commit_timestamp_readback_wait_seconds": 0.000001,
            "commit_timestamp_started_monotonic_ns": timestamp_start,
            "commit_timestamp_finished_monotonic_ns": timestamp_end,
            "readback_started_monotonic_ns": timestamp_end + 1_000,
            "readback_finished_monotonic_ns": readback_end,
            "readback_visible": True,
            "replayed": False,
            "causal_sequence": sequence,
            "causal_payload_sha256": event["payload_sha256"],
            "database_clock_anchor": selected,
            "database_clock_anchor_candidates": candidates,
            "database_clock_anchor_selection": {
                "strategy": "minimum_width_then_sequence",
                "candidate_count": 8,
                "selected_sequence": 1,
            },
            "observed_transition": observed,
            "transition_readback_visible": requires_switch,
            "observed_route_revision": copy.deepcopy(observed_route_revision),
            "route_revision_readback_visible": True,
        }
        record["durable_effect"] = receipt
        effect_rows.append(
            {
                "entity_id": record["effect_id"],
                "state": "completed",
                "payload": entity_payload,
                "scope": f"s6bm.terminal-effect.{record['attempt_id']}",
                "idempotency_key": record["request_id"],
                "request_sha256": request_sha,
            }
        )
        effect_events.append(event)
        events.append(event)
        sequence += 1

    terminal_ids = sorted(set(bridge_ids) - {crossover_id})
    for request_id in terminal_ids:
        add_effect(by_request[request_id], requires_switch=False, observed=None)
    terminal_effect_by_id = {str(item["idempotency_key"]): item for item in effect_rows}
    terminal_event_by_id = {str(item["request_id"]): item for item in effect_events}
    terminal_records = [
        s6bm_terminal_fence_record(terminal_effect_by_id[item], terminal_event_by_id[item])
        for item in terminal_ids
    ]
    terminal_records_sha = canonical_sha256(terminal_records)

    start_events = {
        str(item["request_id"]): {}
        for item in events
        if item["event_type"]
        in {"api_server_handler_entry", "controller_entry", "triton_backend_compute_entry"}
    }
    for item in events:
        if item["event_type"] in {
            "api_server_handler_entry",
            "controller_entry",
            "triton_backend_compute_entry",
        }:
            start_events[str(item["request_id"])][str(item["event_type"])] = item
    pending_ids = sorted([hold_id, crossover_id])
    source_payload = {
        "schema_version": "evm.s8_v4.s6bm_control_request.v1",
        "run_id": hold["run_id"],
        "action": "green_switched",
        "expected_generation": 2,
        "lease_id": "fixture-lease",
        "fencing_token": "fixture-fence",
        "blue_artifact_sha256": config.blue.artifact_sha256,
        "green_artifact_sha256": config.green.artifact_sha256,
        "approval_id": "approval-fixture",
        "action_digest": "0" * 64,
        "preflight_vram_passed": True,
        "readiness_passed": True,
        "canary_passed": True,
        "causal_crossover": {
            key: hold[key]
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
        },
        "continuity_receipt_request_ids": required_ids,
        "continuity_crossover_request_ids": [crossover_id],
        "pending_crossover_request_ids": pending_ids,
        "continuity_terminal_request_ids": terminal_ids,
        "continuity_terminal_request_set_sha256": canonical_sha256(terminal_ids),
        "continuity_terminal_records_sha256": terminal_records_sha,
    }
    control = TritonBlueGreenControlRequest.model_validate(source_payload)
    source_payload["action_digest"] = action_digest(control)
    receipt_maps = {
        "continuity_receipt_sequences": {
            request_id: {
                stage: int(start_events[request_id][stage]["causal_sequence"])
                for stage in sorted(start_events[request_id])
            }
            for request_id in required_ids
        },
        "continuity_receipt_payload_sha256": {
            request_id: {
                stage: start_events[request_id][stage]["payload_sha256"]
                for stage in sorted(start_events[request_id])
            }
            for request_id in required_ids
        },
        "continuity_receipt_transaction_ids": {
            request_id: {
                stage: str(start_events[request_id][stage]["transaction_id"])
                for stage in sorted(start_events[request_id])
            }
            for request_id in required_ids
        },
    }
    actor = {
        "schema_version": "evm.s6bm.actor_start_observation.v1",
        "actor_identity": "api-control-plane-route-switch",
        "actor_start_unix_ns": 92_980_000_000 + OFFSET_NS,
        "monotonic_before_ns": 92_980_000_000,
        "monotonic_after_ns": 92_980_000_100,
        "host_identity": "fixture-host",
        "process_id": RUNNER_PID,
        "thread_id": 1,
        "service_instance_id": "fixture-replica",
        "source_revision": SOURCE_REVISION,
    }
    core = {
        "attempt_id": raw["attempt_id"],
        "run_id": hold["run_id"],
        "request_id": hold_id,
        "action": "green_switched",
        "old_route_generation": 2,
        "new_route_generation": 3,
        "source_payload_sha256": canonical_sha256(source_payload),
        "source_revision": SOURCE_REVISION,
        "cell_id": raw["attempt_id"],
        "replica_id": "fixture-replica",
    }
    transition_id = canonical_sha256(
        {"schema_version": "evm.s6bm.route_transition_identity.v1", **core}
    )
    fence_id = canonical_sha256(
        {
            "schema_version": "evm.s6bm.route_fence_identity.v1",
            "transition_id": transition_id,
            "attempt_id": raw["attempt_id"],
            "request_id": hold_id,
        }
    )
    switch_payload = {
        "schema_version": "evm.s6bm.route_switch_fence.v2",
        **{
            key: hold[key]
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
        },
        **core,
        "transition_id": transition_id,
        "fence_id": fence_id,
        "actor": actor,
        "source_payload": source_payload,
        "receipt_sequences": {
            stage: int(start_events[hold_id][stage]["causal_sequence"])
            for stage in sorted(start_events[hold_id])
        },
        "receipt_payload_sha256": {
            stage: start_events[hold_id][stage]["payload_sha256"]
            for stage in sorted(start_events[hold_id])
        },
        "receipt_transaction_ids": {
            stage: str(start_events[hold_id][stage]["transaction_id"])
            for stage in sorted(start_events[hold_id])
        },
        "continuity_receipt_request_ids": required_ids,
        "continuity_receipt_request_set_sha256": canonical_sha256(required_ids),
        **receipt_maps,
        "continuity_crossover_request_ids": [crossover_id],
        "continuity_crossover_request_set_sha256": canonical_sha256([crossover_id]),
        "pending_crossover_request_ids": pending_ids,
        "pending_crossover_request_set_sha256": canonical_sha256(pending_ids),
        "continuity_terminal_request_ids": terminal_ids,
        "continuity_terminal_request_set_sha256": canonical_sha256(terminal_ids),
        "continuity_terminal_records_sha256": terminal_records_sha,
        "continuity_terminal_sequences": {
            item["request_id"]: item["causal_sequence"] for item in terminal_records
        },
    }
    switch = _event(
        hold,
        "blue_to_green_switch_commit",
        sequence,
        {**switch_payload, "fixture_database_ns": 92_990_000_000},
    )
    sequence += 1
    observed = {
        "schema_version": "evm.s6bm.observed_transition.v1",
        "transition_id": transition_id,
        "fence_id": fence_id,
        "fence_sequence": switch["causal_sequence"],
        "fence_transaction_id": switch["transaction_id"],
        "fence_payload_sha256": switch["payload_sha256"],
        "attempt_id": raw["attempt_id"],
        "run_id": hold["run_id"],
        "request_id": hold_id,
        "old_route_generation": 2,
        "new_route_generation": 3,
        "source_payload_sha256": core["source_payload_sha256"],
        "cell_id": raw["attempt_id"],
        "replica_id": "fixture-replica",
        "database_recorded_at": switch["database_recorded_at"],
    }
    events.append(switch)
    add_effect(hold, requires_switch=True, observed=observed)
    add_effect(by_request[crossover_id], requires_switch=True, observed=observed)

    pre_switch_ids = sorted(str(item["request_id"]) for item in records)
    pre_switch_effects = sorted(
        (str(item["request_id"]), str(item["effect_id"])) for item in records
    )
    unload_payload = {
        **{
            key: hold[key]
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
        },
        "schema_version": "evm.s6bm.unload_intent.v1",
        "switch_sequence": switch["causal_sequence"],
        "last_terminal_effect_sequence": max(item["causal_sequence"] for item in effect_events),
        "pre_switch_blue_request_count": len(pre_switch_ids),
        "pre_switch_blue_request_set_sha256": canonical_sha256(pre_switch_ids),
        "pre_switch_blue_effect_set_sha256": canonical_sha256(pre_switch_effects),
    }
    events.append(
        _event(
            hold,
            "blue_unload_intent",
            sequence,
            {**unload_payload, "fixture_database_ns": 95_900_000_000},
        )
    )

    event_export = {
        "schema_version": "evm.s8_v4.s6bm_causal_event_export.v1",
        "attempt_id": raw["attempt_id"],
        "event_count": len(events),
        "events": events,
    }
    effect_export = {
        "schema_version": "evm.s8_v4.s6bm_terminal_effect_export.v1",
        "attempt_id": raw["attempt_id"],
        "effect_count": len(effect_rows),
        "effects": effect_rows,
    }
    event_ref = _write(root, "causal/causal-events.json", event_export)
    effect_ref = _write(root, "causal/durable-effects.json", effect_export)
    pre_switch_events = [
        item for item in events if int(item["causal_sequence"]) < int(switch["causal_sequence"])
    ]
    pre_switch_ref = _write(
        root,
        "causal/bridge-start-receipts-pre-switch.json",
        {
            "schema_version": "evm.s8_v4.s6bm_causal_event_export.v1",
            "attempt_id": raw["attempt_id"],
            "event_count": len(pre_switch_events),
            "events": pre_switch_events,
        },
    )
    terminal_effect_ref = _write(
        root,
        "causal/bridge-terminal-effects-pre-switch.json",
        {
            "schema_version": "evm.s8_v4.s6bm_terminal_effect_export.v1",
            "attempt_id": raw["attempt_id"],
            "effect_count": len(terminal_ids),
            "effects": [terminal_effect_by_id[item] for item in terminal_ids],
        },
    )
    terminal_event_ref = _write(
        root,
        "causal/bridge-terminal-events-pre-switch.json",
        {
            "schema_version": "evm.s8_v4.s6bm_causal_event_export.v1",
            "attempt_id": raw["attempt_id"],
            "event_count": len(terminal_ids),
            "events": [terminal_event_by_id[item] for item in terminal_ids],
        },
    )

    fence_receipt = {
        "schema_version": "evm.s6bm.route_switch_receipt.v2",
        **{
            key: switch[key]
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
                "causal_sequence",
                "transaction_id",
                "database_recorded_at",
                "payload",
                "payload_sha256",
            )
        },
        "transition_id": transition_id,
        "fence_id": fence_id,
        "fence_sequence": switch["causal_sequence"],
        "fence_transaction_id": switch["transaction_id"],
        "fence_payload_sha256": switch["payload_sha256"],
        "old_route_generation": 2,
        "new_route_generation": 3,
        "source_payload_sha256": core["source_payload_sha256"],
        "cell_id": raw["attempt_id"],
        "replica_id": "fixture-replica",
        "actor_identity": "api-control-plane-route-switch",
        "actor_process_id": RUNNER_PID,
        "actor_thread_id": 1,
        "commit_ack_monotonic_ns": 92_991_000_000,
        "readback_started_monotonic_ns": 92_991_500_000,
        "readback_finished_monotonic_ns": 92_992_000_000,
        "readback_visible": True,
        "replayed": False,
    }
    route_revision_payload = {
        "schema_version": "evm.s6bm.route_revision.v1",
        "run_id": hold["run_id"],
        "source_revision": SOURCE_REVISION,
        "control_generation": 3,
        "route_generation": 3,
        "phase": "green_active",
        "route_weights": {"blue": 0, "green": 100},
        "loaded_roles": ["blue", "green"],
        "active_route_identity_sha256": canonical_sha256(
            {
                "routes": [
                    {
                        "role": "green",
                        "weight": 100,
                        "identity_sha256": green_identity_sha256,
                    }
                ]
            }
        ),
        "blue_identity_sha256": blue_identity_sha256,
        "green_identity_sha256": green_identity_sha256,
        "image_digest": raw["identities"]["image_digest"],
        "gpu_uuid": raw["identities"]["gpu_uuid"],
        "action": "green_switched",
        "approval_id": "approval-green-switched-fixture",
        "used_approvals": [
            "approval-canary-started-fixture",
            "approval-green-loaded-fixture",
            "approval-green-switched-fixture",
        ],
        "route_changed": True,
        "lease_id": lease_id,
        "fencing_token_sha256": fencing_token_sha256,
        "transition_id": transition_id,
        "transition_new_route_generation": 3,
    }
    route_revision_receipt = {
        "schema_version": "evm.s6bm.route_revision_receipt.v1",
        "payload": route_revision_payload,
        "payload_sha256": canonical_sha256(route_revision_payload),
        "transaction_id": "902",
        "database_recorded_at": "2026-08-25T00:00:00.902Z",
        "readback_at": "2026-08-25T00:00:00.903Z",
        "readback_visible": True,
        "replayed": False,
    }
    transition = {
        "schema_version": "evm.s6bm.route_transition_receipt.v1",
        "transition_id": transition_id,
        "fence_id": fence_id,
        "fence_receipt": fence_receipt,
        "fence_receipt_sha256": canonical_sha256(fence_receipt),
        "fence_sequence": switch["causal_sequence"],
        "fence_transaction_id": switch["transaction_id"],
        "fence_payload_sha256": switch["payload_sha256"],
        "attempt_id": raw["attempt_id"],
        "run_id": hold["run_id"],
        "request_id": hold_id,
        "old_route_generation": 2,
        "new_route_generation": 3,
        "source_payload_sha256": core["source_payload_sha256"],
        "source_revision": SOURCE_REVISION,
        "cell_id": raw["attempt_id"],
        "replica_id": "fixture-replica",
        "actor_identity": "api-control-plane-route-switch",
        "actor_process_id": RUNNER_PID,
        "actor_thread_id": 1,
        "actor_commit_ack_monotonic_ns": 92_991_000_000,
        "fence_readback_started_monotonic_ns": 92_991_500_000,
        "fence_readback_finished_monotonic_ns": 92_992_000_000,
        "route_applied_monotonic_ns": 93_000_000_000,
        "route_applied_actor": {
            "actor_identity": "api-control-plane-route-switch-applied",
            "process_id": RUNNER_PID,
            "thread_id": 1,
            "source_revision": SOURCE_REVISION,
            "service_instance_id": "fixture-replica",
            "monotonic_before_ns": 93_000_000_000,
            "monotonic_after_ns": 93_000_000_100,
        },
        "state_readback": {
            "generation": 3,
            "route_generation": 3,
            "phase": "green_active",
            "route_weights": {"blue": 0, "green": 100},
            "loaded_roles": ["blue", "green"],
        },
        "route_revision_payload_sha256": route_revision_receipt["payload_sha256"],
        "route_revision_transaction_id": route_revision_receipt["transaction_id"],
        "continuity_receipt_request_ids": required_ids,
        "continuity_receipt_request_set_sha256": canonical_sha256(required_ids),
        "continuity_crossover_request_ids": [crossover_id],
        "continuity_crossover_request_set_sha256": canonical_sha256([crossover_id]),
        "pending_crossover_request_ids": pending_ids,
        "pending_crossover_request_set_sha256": canonical_sha256(pending_ids),
        "released_crossover_request_ids": pending_ids,
        "continuity_terminal_request_ids": terminal_ids,
        "continuity_terminal_request_set_sha256": canonical_sha256(terminal_ids),
        "continuity_terminal_records_sha256": terminal_records_sha,
        "crossover_release_monotonic_ns": 93_001_000_000,
        "route_switch_deadline_owner_request_id": crossover_id,
        "route_switch_deadline_started_monotonic_ns": raw["continuity_execution"][
            "route_switch_deadline"
        ]["started_monotonic_ns"],
        "route_switch_deadline_monotonic_ns": raw["continuity_execution"]["route_switch_deadline"][
            "deadline_monotonic_ns"
        ],
    }

    selected_gate_events = sorted(
        (
            {
                **{
                    key: event[key]
                    for key in (
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
                        "transaction_id",
                        "database_recorded_at",
                    )
                },
                "readback_at": event["captured_at"],
                "readback_visible": True,
                "readback_source": "postgresql_attempt_export",
            }
            for request_id in required_ids
            for event in start_events[request_id].values()
        ),
        key=lambda item: (item["request_id"], item["event_type"]),
    )
    raw["continuity_execution"]["bridge_actor_receipt_gate"] = {
        "schema_version": "evm.s8_v4.s6bm_bridge_actor_receipt_gate.v1",
        "attempt_id": raw["attempt_id"],
        "route_generation": 2,
        "required_request_ids": required_ids,
        "required_request_set_sha256": canonical_sha256(required_ids),
        "required_stage_count": 3,
        "expected_event_count": 12,
        "visible_event_count": 12,
        "raw_readback_export": pre_switch_ref,
        "raw_readback_event_count": len(pre_switch_events),
        "selected_event_set_sha256": canonical_sha256(selected_gate_events),
        "maximum_visible_causal_sequence": max(
            item["causal_sequence"] for item in selected_gate_events
        ),
        "events": selected_gate_events,
        "collector_request_ids": required_ids,
        "collector_request_set_sha256": canonical_sha256(required_ids),
        "gate_satisfied_monotonic": 92.98,
    }
    raw["continuity_execution"]["bridge_triton_start_receipts"] = [
        collectors[item] for item in required_ids
    ]
    raw["continuity_execution"]["pre_switch_terminal_gate"].update(
        {
            "terminal_records": terminal_records,
            "terminal_records_sha256": terminal_records_sha,
            "raw_effect_export": terminal_effect_ref,
            "raw_event_export": terminal_event_ref,
        }
    )
    raw["causal_proof"] = {
        "causal_event_export": event_ref,
        "durable_effect_export": effect_ref,
        "route_transition_receipt": transition,
        "route_revision_receipt": route_revision_receipt,
        "triton_start_receipt": collectors[hold_id],
    }
    raw["observability"] = {"artifacts": {"trace_export": trace_ref}}
    raw["blue_in_flight_before_unload"] = 0
    return raw

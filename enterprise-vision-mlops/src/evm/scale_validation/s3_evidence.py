from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from evm.scale_validation.s3_runtime import (
    S3LoadPoint,
    S3RuntimeConfig,
    analyze_capacity_results,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class S3EvidenceValidationError(RuntimeError):
    pass


def validate_s3_capacity_evidence(
    payload: Mapping[str, Any],
    *,
    config: S3RuntimeConfig,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "evm.s3_capacity_experiment.v1":
        errors.append("schema_version")
    source = dict(payload.get("source_identity", {}))
    runtime_revision = str(source.get("implementation_revision") or "")
    if not REVISION_PATTERN.fullmatch(runtime_revision):
        errors.append("runtime_revision")
    runtime_contract = dict(payload.get("runtime_contract", {}))
    if runtime_contract.get("sha256") != config.sha256:
        errors.append("runtime_config_sha256")
    if int(runtime_contract.get("repetitions", 0)) != config.repetitions:
        errors.append("runtime_repetitions")
    if not bool(runtime_contract.get("closure_eligible")):
        errors.append("runtime_closure_eligible")

    raw_results = payload.get("point_results")
    results = list(raw_results) if isinstance(raw_results, Sequence) else []
    raw_skipped = payload.get("skipped_points")
    skipped = list(raw_skipped) if isinstance(raw_skipped, Sequence) else []
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for index, item in enumerate(results):
        if not isinstance(item, Mapping):
            errors.append(f"result_{index}_mapping")
            continue
        point = dict(item.get("point", {}))
        try:
            point_id = S3LoadPoint(**point).point_id
        except (TypeError, ValueError):
            errors.append(f"result_{index}_point")
            continue
        grouped[point_id].append(item)
        _validate_point_result(
            item,
            index=index,
            runtime_revision=runtime_revision,
            runtime_config_sha256=config.sha256,
            errors=errors,
        )

    for point_id, repetitions in grouped.items():
        observed = sorted(int(item.get("repetition", 0)) for item in repetitions)
        expected = list(range(1, config.repetitions + 1))
        if observed != expected:
            errors.append(f"point_repetitions:{point_id}")

    expected_ids = {point.point_id for point in config.points()}
    skipped_ids = {
        str(item.get("point_id") or "")
        for item in skipped
        if isinstance(item, Mapping)
    }
    if set(grouped) & skipped_ids:
        errors.append("executed_skipped_overlap")
    if set(grouped) | skipped_ids != expected_ids:
        errors.append("matrix_point_coverage")
    if len(results) != sum(len(items) for items in grouped.values()):
        errors.append("result_grouping")

    failures = payload.get("failed_attempts_and_rca")
    if not isinstance(failures, Sequence) or failures:
        errors.append("accepted_run_failures")

    recomputed = analyze_capacity_results(
        results=results,
        skipped=skipped,
        config=config,
        closure_eligible=True,
    )
    if _canonical(payload.get("analysis")) != _canonical(recomputed):
        errors.append("analysis_projection")
    if _canonical(payload.get("acceptance")) != _canonical(
        recomputed["acceptance"]
    ):
        errors.append("acceptance_projection")
    if payload.get("runtime_verdict") != recomputed["runtime_verdict"]:
        errors.append("runtime_verdict_projection")
    if payload.get("scenario_status") != recomputed["scenario_status"]:
        errors.append("scenario_status_projection")
    if not all(bool(value) for value in recomputed["acceptance"].values()):
        errors.append("acceptance_not_all_passed")

    private = dict(payload.get("private_evidence", {}))
    if int(private.get("artifact_count", 0)) <= 0:
        errors.append("private_artifact_count")
    if not SHA256_PATTERN.fullmatch(str(private.get("aggregate_sha256") or "")):
        errors.append("private_aggregate_sha256")
    if private.get("location") != "outside_git_private_evidence_root":
        errors.append("private_location")

    if errors:
        raise S3EvidenceValidationError(
            "s3_capacity_evidence_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "status": "valid",
        "runtime_revision": runtime_revision,
        "point_result_count": len(results),
        "executed_point_count": len(grouped),
        "skipped_point_count": len(skipped_ids),
        "acceptance": recomputed["acceptance"],
        "runtime_verdict": recomputed["runtime_verdict"],
        "private_artifact_count": int(private["artifact_count"]),
        "private_aggregate_sha256": private["aggregate_sha256"],
    }


def _validate_point_result(
    item: Mapping[str, Any],
    *,
    index: int,
    runtime_revision: str,
    runtime_config_sha256: str,
    errors: list[str],
) -> None:
    prefix = f"result_{index}"
    if item.get("source_revision") != runtime_revision:
        errors.append(f"{prefix}_source_revision")
    if item.get("runtime_config_sha256") != runtime_config_sha256:
        errors.append(f"{prefix}_runtime_config")
    if not bool(item.get("evidence_valid")):
        errors.append(f"{prefix}_evidence_valid")
    assertions = item.get("assertions")
    if not isinstance(assertions, Mapping) or not assertions or not all(
        bool(value) for value in assertions.values()
    ):
        errors.append(f"{prefix}_assertions")
    if item.get("failure"):
        errors.append(f"{prefix}_failure")

    load = dict(item.get("load", {}))
    request_count = int(load.get("request_count", -1))
    status_counts = Counter(
        {
            int(status): int(count)
            for status, count in dict(load.get("status_counts", {})).items()
        }
    )
    if request_count < 1 or sum(status_counts.values()) != request_count:
        errors.append(f"{prefix}_request_accounting")
    if int(load.get("client_request_identity_count", -1)) != request_count:
        errors.append(f"{prefix}_client_identity")
    server_count = request_count - status_counts[0]
    if int(load.get("server_response_count", -1)) != server_count:
        errors.append(f"{prefix}_server_count")
    if int(load.get("trace_identity_match_count", -1)) != server_count:
        errors.append(f"{prefix}_server_trace_identity")
    if int(load.get("transport_error_count", -1)) != status_counts[0]:
        errors.append(f"{prefix}_transport_accounting")

    trace = dict(item.get("trace", {}))
    expected_trace_count = int(trace.get("expected_sampled_trace_count", -1))
    if expected_trace_count <= 0:
        errors.append(f"{prefix}_trace_nonzero")
    if int(trace.get("complete_sampled_trace_count", -1)) != expected_trace_count:
        errors.append(f"{prefix}_trace_complete")
    expected_contracts = Counter(
        {
            str(key): int(value)
            for key, value in dict(
                trace.get("expected_trace_contract_counts", {})
            ).items()
        }
    )
    complete_contracts = Counter(
        {
            str(key): int(value)
            for key, value in dict(
                trace.get("complete_trace_contract_counts", {})
            ).items()
        }
    )
    if expected_contracts != complete_contracts:
        errors.append(f"{prefix}_trace_contracts")
    if sum(expected_contracts.values()) != expected_trace_count:
        errors.append(f"{prefix}_trace_contract_count")
    if not bool(trace.get("flush_completed")):
        errors.append(f"{prefix}_trace_flush")
    if trace.get("flush_boundary") != "before_api_process_stop":
        errors.append(f"{prefix}_trace_flush_boundary")

    cleanup = dict(item.get("cleanup", {}))
    if (
        int(cleanup.get("lingering_pid_count", -1)) != 0
        or int(cleanup.get("marker_process_count", -1)) != 0
        or not bool(cleanup.get("prometheus_container_absent"))
    ):
        errors.append(f"{prefix}_cleanup")
    if not SHA256_PATTERN.fullmatch(
        str(item.get("private_evidence_sha256") or "")
    ):
        errors.append(f"{prefix}_private_sha256")


def canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))

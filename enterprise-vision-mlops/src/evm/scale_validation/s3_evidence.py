from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evm.scale_validation.s3_runtime import (
    PUBLIC_PROJECTION_DECIMAL_PLACES,
    S3LoadPoint,
    S3RuntimeConfig,
    analyze_capacity_results,
    evaluate_point_guardrails,
    stable_public_projection,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUNTIME_MODULE_PATH = (
    "enterprise-vision-mlops/src/evm/scale_validation/s3_runtime.py"
)
RUNTIME_CONFIG_PATH = "enterprise-vision-mlops/configs/s3_capacity_runtime.toml"


class S3EvidenceValidationError(RuntimeError):
    pass


def validate_s3_capacity_evidence(
    payload: Mapping[str, Any],
    *,
    config: S3RuntimeConfig,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    _validate_finite_numbers(payload, path="evidence", errors=errors)
    if errors:
        raise S3EvidenceValidationError(
            "s3_capacity_evidence_invalid:" + ",".join(sorted(set(errors)))
        )
    if payload.get("schema_version") != "evm.s3_capacity_experiment.v1":
        errors.append("schema_version")
    source = dict(payload.get("source_identity", {}))
    runtime_revision = str(source.get("implementation_revision") or "")
    if not REVISION_PATTERN.fullmatch(runtime_revision):
        errors.append("runtime_revision")
    projection = dict(payload.get("analysis_projection", {}))
    analysis_revision = str(projection.get("revision") or "")
    if not REVISION_PATTERN.fullmatch(analysis_revision):
        errors.append("analysis_revision")
    if projection.get("runtime_revision") != runtime_revision:
        errors.append("analysis_runtime_revision")
    if int(projection.get("precision_decimal_places", -1)) != (
        PUBLIC_PROJECTION_DECIMAL_PLACES
    ):
        errors.append("analysis_precision_contract")
    if projection.get("non_finite_policy") != "fail_closed":
        errors.append("analysis_non_finite_policy")
    strict_reclosure = dict(payload.get("strict_reclosure", {}))
    if strict_reclosure.get("status") != "passed":
        errors.append("strict_reclosure_pending")
    if strict_reclosure.get("workload_rerun") is not False:
        errors.append("strict_reclosure_workload_boundary")
    if int(strict_reclosure.get("persisted_point_result_count", 0)) != 111:
        errors.append("strict_reclosure_point_count")
    if int(strict_reclosure.get("retained_failed_attempt_count", 0)) != 4:
        errors.append("strict_reclosure_rca_count")
    if strict_reclosure.get("python_projection_versions") != [
        "3.11",
        "3.12",
        "3.13",
    ]:
        errors.append("strict_reclosure_python_versions")
    git_identity: dict[str, Any] = {}
    if git_root is not None:
        git_identity = _validate_git_source_identity(
            source=source,
            projection=projection,
            git_root=git_root,
            validation_revision=validation_revision,
            errors=errors,
        )
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
            config=config,
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
    normalized_recorded = stable_public_projection(payload.get("analysis"))
    if _canonical(payload.get("analysis")) != _canonical(normalized_recorded):
        errors.append("analysis_not_at_frozen_precision")
    if _canonical(normalized_recorded) != _canonical(recomputed):
        errors.append("analysis_projection")
    if strict_reclosure.get("analysis_projection_sha256") != canonical_sha256(
        recomputed
    ):
        errors.append("analysis_projection_sha256")
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
        "source_identity": git_identity,
    }


def validate_s3_capacity_closure(
    closure: Mapping[str, Any],
    *,
    experiment: Mapping[str, Any],
    experiment_sha256: str,
    config: S3RuntimeConfig,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if closure.get("schema_version") != "evm.s3_capacity_closure.v1":
        errors.append("closure_schema_version")
    strict_reclosure = dict(closure.get("strict_reclosure", {}))
    if strict_reclosure.get("status") != "passed":
        errors.append("closure_strict_reclosure_pending")
    validated = validate_s3_capacity_evidence(
        experiment,
        config=config,
        git_root=git_root,
        validation_revision=validation_revision,
    )
    closure_source = dict(closure.get("source_identity", {}))
    experiment_source = dict(experiment.get("source_identity", {}))
    experiment_projection = dict(experiment.get("analysis_projection", {}))
    source_pairs = {
        "runtime_revision": experiment_source.get("implementation_revision"),
        "runtime_module_sha256": experiment_source.get("runtime_module_sha256"),
        "runtime_module_blob_oid": experiment_source.get("runtime_module_blob_oid"),
        "analysis_projection_revision": experiment_projection.get("revision"),
        "analysis_module_sha256": experiment_projection.get(
            "analysis_module_sha256"
        ),
        "analysis_module_blob_oid": experiment_projection.get(
            "analysis_module_blob_oid"
        ),
    }
    for key, expected in source_pairs.items():
        if closure_source.get(key) != expected:
            errors.append(f"closure_source_{key}")
    final = dict(closure.get("final_runtime_evidence", {}))
    expected_final = {
        "git_blob_sha256": experiment_sha256,
        "point_result_count": validated["point_result_count"],
        "executed_point_count": validated["executed_point_count"],
        "skipped_point_count": validated["skipped_point_count"],
        "acceptance": validated["acceptance"],
        "runtime_verdict": validated["runtime_verdict"],
        "private_artifact_count": validated["private_artifact_count"],
        "private_aggregate_sha256": validated["private_aggregate_sha256"],
    }
    for key, expected in expected_final.items():
        if final.get(key) != expected:
            errors.append(f"closure_final_{key}")
    if final.get("path") != "docs/status/evidence/s3-capacity-experiment.json":
        errors.append("closure_final_path")
    if int(final.get("repetitions_per_executed_point", 0)) != config.repetitions:
        errors.append("closure_repetitions")

    analysis = dict(experiment.get("analysis", {}))
    capacity = dict(analysis.get("s2_capacity_recalculation", {}))
    closure_capacity = dict(closure.get("s2_capacity_recalculation", {}))
    for key in (
        "formula",
        "measured_service_rate_per_second",
        "maximum_queue_wait_seconds",
        "safety_factor",
        "calculated_depth",
        "prior_depth",
        "selected_depth",
        "rollback_depth",
        "automatic_increase_allowed",
    ):
        if closure_capacity.get(key) != capacity.get(key):
            errors.append(f"closure_capacity_{key}")

    bottleneck = dict(analysis.get("bottleneck", {}))
    first = dict(bottleneck.get("first_observed", {}))
    measured = dict(closure.get("measured_capacity", {}))
    closure_knee = dict(measured.get("first_saturation_knee", {}))
    if closure_knee.get("curve") != first.get("curve"):
        errors.append("closure_bottleneck_curve")
    if closure_knee.get("cause") != first.get("cause"):
        errors.append("closure_bottleneck_cause")
    signal_map = {
        "client_p99_ms": "p99_ms",
        "server_total_p99_ms": "server_total_p99_ms",
        "queue_wait_p99_ms": "queue_wait_p99_ms",
        "prediction_p99_ms": "prediction_p99_ms",
        "api_process_tree_cpu_percent": "api_process_tree_cpu_percent",
        "load_generator_cpu_percent": "load_generator_cpu_percent",
    }
    signals = dict(first.get("signals", {}))
    for closure_key, signal_key in signal_map.items():
        if closure_knee.get(closure_key) != signals.get(signal_key):
            errors.append(f"closure_bottleneck_{closure_key}")
    if closure_knee.get("attribution_boundary") != bottleneck.get(
        "attribution_boundary"
    ):
        errors.append("closure_bottleneck_boundary")

    topology = dict(closure.get("topology_result", {}))
    if int(topology.get("topology_point_count", 0)) != int(
        bottleneck.get("topology_point_count", -1)
    ):
        errors.append("closure_topology_count")
    if topology.get("monotonic_throughput_gain_observed") is not False:
        errors.append("closure_topology_claim")

    regression = dict(closure.get("regression", {}))
    for key in (
        "focused_s3_tests",
        "full_python_tests_with_real_postgresql",
        "lifecycle_host_tests",
        "control_panel_tests",
    ):
        if int(regression.get(key, 0)) <= 0:
            errors.append(f"closure_regression_{key}")
    if regression.get("control_panel_production_build") != "passed":
        errors.append("closure_frontend_build")
    smoke = dict(regression.get("current_revision_runtime_smoke", {}))
    if (
        not bool(smoke.get("external_tcp_http"))
        or smoke.get("sampled_trace_chains") != "33/33"
        or not bool(smoke.get("prometheus_targets_up"))
        or not bool(smoke.get("terminal_gauges_zero"))
        or not bool(smoke.get("cleanup_complete"))
    ):
        errors.append("closure_runtime_smoke")

    attempts = closure.get("failed_attempts_and_rca")
    if not isinstance(attempts, Sequence) or len(attempts) != 4:
        errors.append("closure_failed_attempts")
    else:
        for index, attempt in enumerate(attempts):
            if not isinstance(attempt, Mapping) or not SHA256_PATTERN.fullmatch(
                str(attempt.get("sha256") or "")
            ):
                errors.append(f"closure_failed_attempt_{index}")
    cleanup = dict(closure.get("cleanup", {}))
    required_cleanup = (
        "all_accepted_point_cleanup_assertions_passed",
        "private_inventory_rehash_passed",
    )
    if not all(bool(cleanup.get(key)) for key in required_cleanup):
        errors.append("closure_cleanup")
    if (
        int(cleanup.get("active_or_in_flight_gauges_after_each_point", -1)) != 0
        or int(cleanup.get("marker_processes_remaining", -1)) != 0
        or int(cleanup.get("isolated_prometheus_containers_remaining", -1)) != 0
    ):
        errors.append("closure_cleanup_counts")
    if closure.get("verdict") != "passed":
        errors.append("closure_verdict")

    if errors:
        raise S3EvidenceValidationError(
            "s3_capacity_closure_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "status": "valid",
        "verdict": "passed",
        "experiment_sha256": experiment_sha256,
        "point_result_count": validated["point_result_count"],
        "acceptance": validated["acceptance"],
        "selected_s2_depth": closure_capacity["selected_depth"],
        "failed_attempt_count": len(attempts),
    }


def _validate_point_result(
    item: Mapping[str, Any],
    *,
    index: int,
    runtime_revision: str,
    runtime_config_sha256: str,
    config: S3RuntimeConfig,
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
    resources = dict(item.get("resources", {}))
    recomputed_guardrails = evaluate_point_guardrails(load, resources, config)
    if _canonical(item.get("guardrails")) != _canonical(recomputed_guardrails):
        errors.append(f"{prefix}_guardrail_projection")

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
    errors: list[str] = []
    _validate_finite_numbers(payload, path="canonical", errors=errors)
    if errors:
        raise S3EvidenceValidationError(
            "s3_capacity_evidence_invalid:" + ",".join(sorted(set(errors)))
        )
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_finite_numbers(
    value: Any,
    *,
    path: str,
    errors: list[str],
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite_numbers(
                item,
                path=f"{path}.{key}",
                errors=errors,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_finite_numbers(
                item,
                path=f"{path}[{index}]",
                errors=errors,
            )
        return
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"non_finite:{path}")


def _validate_git_source_identity(
    *,
    source: Mapping[str, Any],
    projection: Mapping[str, Any],
    git_root: Path,
    validation_revision: str,
    errors: list[str],
) -> dict[str, Any]:
    runtime_revision = str(source.get("implementation_revision") or "")
    analysis_revision = str(projection.get("revision") or "")
    validation_commit = _resolve_commit(
        git_root,
        validation_revision,
        "validation_revision",
        errors,
    )
    runtime_commit = _resolve_commit(
        git_root,
        runtime_revision,
        "runtime_revision",
        errors,
    )
    analysis_commit = _resolve_commit(
        git_root,
        analysis_revision,
        "analysis_revision",
        errors,
    )
    if runtime_commit and analysis_commit and not _is_ancestor(
        git_root, runtime_commit, analysis_commit
    ):
        errors.append("runtime_not_ancestor_of_analysis")
    if analysis_commit and validation_commit and not _is_ancestor(
        git_root, analysis_commit, validation_commit
    ):
        errors.append("analysis_not_ancestor_of_validation")

    runtime_blob = _validate_blob_identity(
        git_root=git_root,
        revision=runtime_commit,
        expected_path=RUNTIME_MODULE_PATH,
        recorded_path=source.get("runtime_module_path"),
        recorded_oid=source.get("runtime_module_blob_oid"),
        recorded_sha256=source.get("runtime_module_sha256"),
        prefix="runtime_module",
        errors=errors,
    )
    config_blob = _validate_blob_identity(
        git_root=git_root,
        revision=runtime_commit,
        expected_path=RUNTIME_CONFIG_PATH,
        recorded_path=source.get("runtime_config_path"),
        recorded_oid=source.get("runtime_config_blob_oid"),
        recorded_sha256=source.get("runtime_config_sha256"),
        prefix="runtime_config",
        errors=errors,
    )
    analysis_blob = _validate_blob_identity(
        git_root=git_root,
        revision=analysis_commit,
        expected_path=RUNTIME_MODULE_PATH,
        recorded_path=projection.get("analysis_module_path"),
        recorded_oid=projection.get("analysis_module_blob_oid"),
        recorded_sha256=projection.get("analysis_module_sha256"),
        prefix="analysis_module",
        errors=errors,
    )
    if source.get("hash_basis") != "canonical_git_blob_bytes":
        errors.append("runtime_hash_basis")
    if projection.get("hash_basis") != "canonical_git_blob_bytes":
        errors.append("analysis_hash_basis")
    return {
        "validation_revision": validation_commit,
        "runtime_revision": runtime_commit,
        "analysis_revision": analysis_commit,
        "runtime_module": runtime_blob,
        "runtime_config": config_blob,
        "analysis_module": analysis_blob,
    }


def _resolve_commit(
    git_root: Path,
    revision: str,
    label: str,
    errors: list[str],
) -> str | None:
    if not revision:
        errors.append(label)
        return None
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=git_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        errors.append(f"{label}_missing")
        return None
    return result.stdout.strip()


def _is_ancestor(git_root: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=git_root,
            capture_output=True,
            timeout=15,
            check=False,
        ).returncode
        == 0
    )


def _validate_blob_identity(
    *,
    git_root: Path,
    revision: str | None,
    expected_path: str,
    recorded_path: Any,
    recorded_oid: Any,
    recorded_sha256: Any,
    prefix: str,
    errors: list[str],
) -> dict[str, str]:
    if recorded_path != expected_path:
        errors.append(f"{prefix}_path")
    if revision is None:
        return {}
    spec = f"{revision}:{expected_path}"
    oid_result = subprocess.run(
        ["git", "rev-parse", "--verify", spec],
        cwd=git_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if oid_result.returncode != 0:
        errors.append(f"{prefix}_missing")
        return {}
    oid = oid_result.stdout.strip()
    raw = subprocess.run(
        ["git", "cat-file", "blob", oid],
        cwd=git_root,
        capture_output=True,
        timeout=15,
        check=True,
    ).stdout
    digest = hashlib.sha256(raw).hexdigest()
    if recorded_oid != oid:
        errors.append(f"{prefix}_blob_oid")
    if recorded_sha256 != digest:
        errors.append(f"{prefix}_sha256")
    return {"path": expected_path, "blob_oid": oid, "sha256": digest}

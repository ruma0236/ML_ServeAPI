from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evm.scale_validation.s5_runtime import (
    S5RuntimeConfig,
    analyze_s5_results,
    file_sha256,
    payload_sha256,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_RESULT_FIELDS = (
    "engine",
    "stage",
    "repetition",
    "profile",
    "executor_count",
    "semantic_row_count",
    "effective_row_count",
    "repeat_factor",
    "generated_io_only",
    "duration_seconds",
    "records_per_second",
    "mib_per_second",
    "peak_executor_memory_bytes",
    "gc_time_ms",
    "gc_ratio",
    "shuffle_read_bytes",
    "shuffle_write_bytes",
    "memory_spill_bytes",
    "disk_spill_bytes",
    "skew_ratio",
    "missing_records",
    "duplicate_records",
    "output_digest",
    "commit_state",
    "task_count",
    "failed_task_count",
    "executors_added",
    "executors_removed",
    "executor_kill_observed",
    "executor_identity_count",
    "retry_output_digest",
    "retry_row_count",
    "retry_commit_state",
)
SOURCE_PATHS = {
    "config": "enterprise-vision-mlops/configs/s5_spark_data_scale.toml",
    "runtime": "enterprise-vision-mlops/src/evm/scale_validation/s5_runtime.py",
    "spark_job": "enterprise-vision-mlops/src/evm/scale_validation/s5_spark_job.py",
    "runner": "enterprise-vision-mlops/scripts/dev/run_s5_spark_data_scale_experiment.py",
}
S5_SMOKE_PATH = (
    "enterprise-vision-mlops/docs/status/evidence/s5-current-revision-runtime-smoke.json"
)
S5_REGRESSION_PATH = (
    "enterprise-vision-mlops/docs/status/evidence/s5-reclosure-regression-evidence.json"
)
REQUIRED_REGRESSION_SUITES = (
    "changed_file_lint",
    "focused_s5",
    "full_python_real_postgresql",
    "lifecycle_host_e2e",
    "control_panel",
    "frontend_production_build",
    "s0_s7_regression",
)


class S5EvidenceValidationError(RuntimeError):
    pass


def project_s5_result(result: Mapping[str, Any], *, point_id: str) -> dict[str, Any]:
    return {
        "point_id": point_id,
        **{key: result[key] for key in PUBLIC_RESULT_FIELDS if key in result},
    }


def source_git_identity(git_root: Path, revision: str) -> dict[str, Any]:
    _git(git_root, "cat-file", "-e", f"{revision}^{{commit}}")
    return {
        name: _git_blob_identity(git_root, revision, path)
        for name, path in SOURCE_PATHS.items()
    }


def validate_s5_spark_data_scale_evidence(
    payload: Mapping[str, Any],
    *,
    config: S5RuntimeConfig,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
    private_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    _validate_finite_numbers(payload, "evidence", errors)
    if payload.get("schema_version") != "evm.s5_spark_data_scale_experiment.v1":
        errors.append("schema_version")

    source = dict(payload.get("source_identity", {}))
    runtime_revision = str(source.get("revision") or "")
    if not REVISION_PATTERN.fullmatch(runtime_revision):
        errors.append("runtime_revision")
    if source.get("config_sha256") != config.sha256:
        errors.append("runtime_config_sha256")
    git_identity: dict[str, Any] = {}
    if git_root is not None and REVISION_PATTERN.fullmatch(runtime_revision):
        try:
            if subprocess.run(
                ["git", "merge-base", "--is-ancestor", runtime_revision, validation_revision],
                cwd=git_root,
                check=False,
            ).returncode != 0:
                errors.append("runtime_revision_not_ancestor")
            git_identity = source_git_identity(git_root, runtime_revision)
            if _canonical(source.get("git_blobs")) != _canonical(git_identity):
                errors.append("git_blob_identity")
            if git_identity.get("config", {}).get("sha256") != config.sha256:
                errors.append("config_git_blob_sha256")
        except (OSError, subprocess.CalledProcessError):
            errors.append("git_identity_unavailable")

    dataset = dict(payload.get("dataset", {}))
    expected_dataset = {
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "source_revision": config.source_revision,
        "license": config.source_license,
    }
    for key, expected in expected_dataset.items():
        if dataset.get(key) != expected:
            errors.append(f"dataset:{key}")
    if dataset.get("generated_io_is_semantic_diversity") is not False:
        errors.append("generated_io_semantic_claim")
    stage_rows = dict(dataset.get("stage_semantic_rows", {}))
    if set(stage_rows) != {"small", "medium", "large"}:
        errors.append("dataset_stage_rows")
    elif not all(int(stage_rows[name]) > 0 for name in stage_rows):
        errors.append("dataset_stage_rows_nonpositive")

    raw_results = payload.get("results")
    results = list(raw_results) if isinstance(raw_results, Sequence) else []
    _validate_result_matrix(results, config=config, errors=errors)
    analysis_inputs = [
        {key: value for key, value in item.items() if key != "point_id"}
        for item in results
        if isinstance(item, Mapping)
    ]
    try:
        recomputed = analyze_s5_results(analysis_inputs, config)
    except Exception:
        errors.append("analysis_recompute_failed")
        recomputed = {}
    if _canonical(payload.get("analysis")) != _canonical(recomputed):
        errors.append("analysis_projection")
    if payload.get("status") != "verified" or payload.get("verdict") != "passed":
        errors.append("verdict")
    if recomputed.get("status") != "passed":
        errors.append("acceptance_not_all_passed")
    if int(payload.get("failed_attempt_count", -1)) != 0:
        errors.append("accepted_suite_failed_attempts")
    if payload.get("claim_boundary") != config.claim_boundary:
        errors.append("claim_boundary")
    _validate_cleanup(payload.get("cleanup"), errors)

    private_summary: dict[str, Any] = {}
    if private_root is not None:
        private_summary = _validate_private_evidence(
            payload=payload,
            results=results,
            root=private_root,
            errors=errors,
        )
    else:
        private = dict(payload.get("private_evidence", {}))
        if int(private.get("artifact_count", 0)) <= 0:
            errors.append("private_artifact_count")
        if not SHA256_PATTERN.fullmatch(str(private.get("index_sha256") or "")):
            errors.append("private_index_sha256")

    if errors:
        raise S5EvidenceValidationError(
            "s5_spark_data_scale_evidence_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "status": "valid",
        "runtime_revision": runtime_revision,
        "point_result_count": len(results),
        "acceptance": recomputed["acceptance"],
        "engine_summaries": recomputed["engine_summaries"],
        "source_identity": git_identity,
        "private_evidence": private_summary,
    }


def validate_s5_spark_data_scale_closure(
    closure: Mapping[str, Any],
    *,
    experiment: Mapping[str, Any],
    experiment_sha256: str,
    config: S5RuntimeConfig,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
    private_root: Path | None = None,
    runtime_smoke: Mapping[str, Any] | None = None,
    runtime_smoke_sha256: str | None = None,
    regression_evidence: Mapping[str, Any] | None = None,
    regression_evidence_sha256: str | None = None,
    regression_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if closure.get("schema_version") != "evm.s5_spark_data_scale_closure.v2":
        errors.append("closure_schema_version")
    validated = validate_s5_spark_data_scale_evidence(
        experiment,
        config=config,
        git_root=git_root,
        validation_revision=validation_revision,
        private_root=private_root,
    )
    final = dict(closure.get("final_runtime_evidence", {}))
    if final.get("git_blob_sha256") != experiment_sha256:
        errors.append("closure_experiment_sha256")
    if int(final.get("point_result_count", 0)) != 30:
        errors.append("closure_point_count")
    if _canonical(final.get("acceptance")) != _canonical(validated["acceptance"]):
        errors.append("closure_acceptance")
    if closure.get("status") != "verified" or closure.get("verdict") != "passed":
        errors.append("closure_verdict")
    smoke_result = validate_s5_runtime_smoke(
        runtime_smoke or {},
        config=config,
        git_root=git_root,
        validation_revision=validation_revision,
    )
    regression_result = validate_s5_regression_evidence(
        regression_evidence or {},
        regression_root=regression_root,
        git_root=git_root,
        validation_revision=validation_revision,
    )
    source = dict(closure.get("source_identity", {}))
    validator_revision = str(source.get("validator_revision") or "")
    if not REVISION_PATTERN.fullmatch(validator_revision):
        errors.append("closure_validator_revision")
    if final.get("runtime_smoke_git_blob_sha256") != runtime_smoke_sha256:
        errors.append("closure_runtime_smoke_sha256")
    if final.get("regression_git_blob_sha256") != regression_evidence_sha256:
        errors.append("closure_regression_sha256")
    if final.get("runtime_smoke_revision") != smoke_result.get("revision"):
        errors.append("closure_runtime_smoke_revision")
    if final.get("regression_revision") != regression_result.get("revision"):
        errors.append("closure_regression_revision")
    if git_root is not None and REVISION_PATTERN.fullmatch(validator_revision):
        try:
            expected_smoke = _git_blob_identity(
                git_root, validator_revision, S5_SMOKE_PATH
            )
            expected_regression = _git_blob_identity(
                git_root, validator_revision, S5_REGRESSION_PATH
            )
            if _canonical(source.get("runtime_smoke")) != _canonical(expected_smoke):
                errors.append("closure_runtime_smoke_git_blob")
            if _canonical(source.get("regression_evidence")) != _canonical(
                expected_regression
            ):
                errors.append("closure_regression_git_blob")
        except (OSError, subprocess.CalledProcessError):
            errors.append("closure_supporting_git_identity")
    cleanup = dict(closure.get("cleanup", {}))
    for key in (
        "runtime_cleanup_passed",
        "private_inventory_rehash_passed",
        "git_blob_validation_passed",
        "source_dataset_unchanged",
    ):
        if cleanup.get(key) is not True:
            errors.append(f"closure_cleanup:{key}")
    if int(cleanup.get("s5_jobs_remaining", -1)) != 0:
        errors.append("closure_cleanup:s5_jobs_remaining")
    if int(cleanup.get("s5_executor_pods_remaining", -1)) != 0:
        errors.append("closure_cleanup:s5_executor_pods_remaining")
    if cleanup.get("pvc_phase") != "Bound":
        errors.append("closure_cleanup:pvc_phase")
    smoke_cleanup = dict(runtime_smoke.get("cleanup", {})) if runtime_smoke else {}
    if int(cleanup.get("s5_jobs_remaining", -1)) != int(
        smoke_cleanup.get("kubernetes_jobs_remaining", -2)
    ):
        errors.append("closure_cleanup:smoke_jobs_parity")
    if int(cleanup.get("s5_executor_pods_remaining", -1)) != int(
        smoke_cleanup.get("kubernetes_executor_pods_remaining", -2)
    ):
        errors.append("closure_cleanup:smoke_pods_parity")
    _validate_s5_observed_results(final, validated, errors)
    if errors:
        raise S5EvidenceValidationError(
            "s5_spark_data_scale_closure_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "status": "valid",
        "experiment_sha256": experiment_sha256,
        "point_result_count": 30,
        "acceptance": validated["acceptance"],
        "runtime_smoke": smoke_result,
        "regression": regression_result,
    }


def validate_s5_runtime_smoke(
    payload: Mapping[str, Any],
    *,
    config: S5RuntimeConfig,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "evm.s5_current_revision_runtime_smoke.v2":
        errors.append("smoke_schema_version")
    if payload.get("status") != "passed" or payload.get("acceptance_credit") is not False:
        errors.append("smoke_status")
    source = dict(payload.get("source_identity", {}))
    revision = str(source.get("revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("smoke_revision")
    if source.get("config_sha256") != config.sha256:
        errors.append("smoke_config_sha256")
    if git_root is not None and REVISION_PATTERN.fullmatch(revision):
        try:
            if subprocess.run(
                ["git", "merge-base", "--is-ancestor", revision, validation_revision],
                cwd=git_root,
                check=False,
            ).returncode:
                errors.append("smoke_revision_not_ancestor")
            if _canonical(source.get("git_blobs")) != _canonical(
                source_git_identity(git_root, revision)
            ):
                errors.append("smoke_git_blob_identity")
        except (OSError, subprocess.CalledProcessError):
            errors.append("smoke_git_identity_unavailable")
    engines = list(payload.get("engines", []))
    expected_engines = {
        "single_process_columnar",
        "spark_local",
        "spark_kubernetes_1",
    }
    if {str(item.get("engine")) for item in engines} != expected_engines:
        errors.append("smoke_engines")
    digests = {str(item.get("output_digest") or "") for item in engines}
    if len(digests) != 1 or "" in digests:
        errors.append("smoke_cross_engine_digest")
    if payload.get("cross_engine_output_digest_equal") is not (len(digests) == 1):
        errors.append("smoke_digest_summary")
    if any(
        int(item.get("missing_records", -1)) != 0
        or int(item.get("duplicate_records", -1)) != 0
        for item in engines
    ):
        errors.append("smoke_integrity")
    cleanup = dict(payload.get("cleanup", {}))
    if (
        int(cleanup.get("kubernetes_jobs_remaining", -1)) != 0
        or int(cleanup.get("kubernetes_executor_pods_remaining", -1)) != 0
        or cleanup.get("pvc_phase") != "Bound"
        or cleanup.get("source_dataset_unchanged") is not True
    ):
        errors.append("smoke_cleanup")
    health = dict(payload.get("runtime_health", {}))
    if (
        health.get("api_status") != "ok"
        or int(health.get("existing_serving_desired_replicas", -1)) != 1
        or int(health.get("existing_serving_available_replicas", -1)) != 1
        or int(health.get("prometheus_targets_total", -1)) < 1
        or health.get("prometheus_targets_total") != health.get("prometheus_targets_up")
    ):
        errors.append("smoke_runtime_health")
    if errors:
        raise S5EvidenceValidationError(
            "s5_runtime_smoke_invalid:" + ",".join(sorted(set(errors)))
        )
    return {"status": "valid", "revision": revision, "engine_count": len(engines)}


def validate_s5_regression_evidence(
    payload: Mapping[str, Any],
    *,
    regression_root: Path | None,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "evm.s5_reclosure_regression.v1":
        errors.append("regression_schema_version")
    if payload.get("status") != "passed":
        errors.append("regression_status")
    revision = str(dict(payload.get("source_identity", {})).get("revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("regression_revision")
    elif git_root is not None:
        try:
            if subprocess.run(
                ["git", "merge-base", "--is-ancestor", revision, validation_revision],
                cwd=git_root,
                check=False,
            ).returncode:
                errors.append("regression_revision_not_ancestor")
        except OSError:
            errors.append("regression_revision_unavailable")
    suites = list(payload.get("suites", []))
    by_id = {str(item.get("suite_id")): item for item in suites if isinstance(item, Mapping)}
    if set(by_id) != set(REQUIRED_REGRESSION_SUITES):
        errors.append("regression_suite_set")
    for suite_id in REQUIRED_REGRESSION_SUITES:
        item = dict(by_id.get(suite_id, {}))
        if (
            item.get("status") != "passed"
            or int(item.get("exit_code", -1)) != 0
            or not str(item.get("command") or "").strip()
            or not SHA256_PATTERN.fullmatch(str(item.get("log_sha256") or ""))
            or int(item.get("log_bytes", 0)) <= 0
        ):
            errors.append(f"regression_suite:{suite_id}")
        relative = Path(str(item.get("log_path") or ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            errors.append(f"regression_log_path:{suite_id}")
        elif regression_root is not None:
            target = regression_root / relative
            if (
                not target.is_file()
                or target.stat().st_size != int(item.get("log_bytes", -1))
                or file_sha256(target) != item.get("log_sha256")
            ):
                errors.append(f"regression_log_identity:{suite_id}")
        if suite_id not in {"changed_file_lint", "frontend_production_build"} and int(
            item.get("tests_passed", 0)
        ) <= 0:
            errors.append(f"regression_test_count:{suite_id}")
    if regression_root is None:
        errors.append("regression_root_missing")
    if errors:
        raise S5EvidenceValidationError(
            "s5_regression_invalid:" + ",".join(sorted(set(errors)))
        )
    return {"status": "valid", "revision": revision, "suite_count": len(suites)}


def _validate_s5_observed_results(
    final: Mapping[str, Any], validated: Mapping[str, Any], errors: list[str]
) -> None:
    summaries = dict(validated.get("engine_summaries", {}))
    columnar = dict(summaries.get("single_process_columnar", {}))
    spark = [
        dict(value)
        for name, value in summaries.items()
        if str(name).startswith("spark_")
    ]
    expected_spark_peak = max(
        (int(item.get("max_peak_executor_memory_bytes", 0)) for item in spark),
        default=0,
    )
    expected_columnar_peak = int(columnar.get("max_peak_executor_memory_bytes", 0))
    if int(final.get("maximum_peak_spark_executor_memory_bytes", -1)) != expected_spark_peak:
        errors.append("closure_spark_peak_memory")
    if int(final.get("maximum_peak_columnar_process_memory_bytes", -1)) != expected_columnar_peak:
        errors.append("closure_columnar_peak_memory")


def _validate_result_matrix(
    results: list[Any], *, config: S5RuntimeConfig, errors: list[str]
) -> None:
    if len(results) != 30:
        errors.append("point_result_count")
    expected: Counter[tuple[str, str, str, int]] = Counter()
    for stage in ("small", "medium", "large"):
        for repetition in range(1, config.repetitions + 1):
            expected[("single_process_columnar", stage, "columnar_stage", repetition)] += 1
            expected[("spark_local", stage, "spark_local_stage", repetition)] += 1
    for executors in config.executor_counts:
        for repetition in range(1, config.repetitions + 1):
            expected[(f"spark_kubernetes_{executors}", "large", "kubernetes_scale", repetition)] += 1
    for repetition in range(1, config.repetitions + 1):
        expected[("spark_kubernetes_4", "large", "executor_loss_retry", repetition)] += 1
    observed: Counter[tuple[str, str, str, int]] = Counter()
    for index, item in enumerate(results, start=1):
        if not isinstance(item, Mapping):
            errors.append(f"point_mapping:{index}")
            continue
        if item.get("point_id") != f"s5-point-{index:03d}":
            errors.append(f"point_id:{index}")
        observed[(
            str(item.get("engine")),
            str(item.get("stage")),
            str(item.get("profile")),
            int(item.get("repetition", 0)),
        )] += 1
        if item.get("profile") == "executor_loss_retry":
            if item.get("executor_kill_observed") is not True:
                errors.append(f"retry_kill:{index}")
            if int(item.get("executor_identity_count", 0)) <= 4:
                errors.append(f"retry_replacement:{index}")
            if int(item.get("executors_removed", 0)) < 1:
                errors.append(f"retry_removed:{index}")
            if item.get("retry_commit_state") != "replayed":
                errors.append(f"retry_commit:{index}")
    if observed != expected:
        errors.append("result_matrix")


def _validate_cleanup(value: Any, errors: list[str]) -> None:
    cleanup = dict(value) if isinstance(value, Mapping) else {}
    if cleanup.get("passed") is not True:
        errors.append("cleanup_passed")
    if int(cleanup.get("executor_pods_remaining", -1)) != 0:
        errors.append("cleanup_executor_pods")
    if cleanup.get("pvc_phase") != "Bound":
        errors.append("cleanup_pvc")


def _validate_private_evidence(
    *,
    payload: Mapping[str, Any],
    results: list[Any],
    root: Path,
    errors: list[str],
) -> dict[str, Any]:
    index_path = root / "private-evidence-index.json"
    if not index_path.is_file():
        errors.append("private_index_missing")
        return {}
    raw = index_path.read_bytes()
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        errors.append("private_index_not_canonical_lf")
    try:
        index = json.loads(raw)
    except json.JSONDecodeError:
        errors.append("private_index_json")
        return {}
    entries = list(index.get("entries", []))
    listed_paths: set[str] = set()
    total_bytes = 0
    for entry in entries:
        relative = str(entry.get("path") or "")
        path = Path(relative)
        if not relative or path.is_absolute() or ".." in path.parts:
            errors.append("private_entry_path")
            continue
        target = root / path
        listed_paths.add(path.as_posix())
        if not target.is_file():
            errors.append("private_entry_missing")
            continue
        size = target.stat().st_size
        total_bytes += size
        if size != int(entry.get("bytes", -1)):
            errors.append("private_entry_bytes")
        if file_sha256(target) != entry.get("sha256"):
            errors.append("private_entry_sha256")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "private-evidence-index.json"
    }
    if listed_paths != actual_paths:
        errors.append("private_inventory_paths")
    if int(index.get("artifact_count", -1)) != len(entries):
        errors.append("private_index_count")
    if int(index.get("total_bytes", -1)) != total_bytes:
        errors.append("private_index_bytes")
    public_private = dict(payload.get("private_evidence", {}))
    if int(public_private.get("artifact_count", -1)) != len(entries):
        errors.append("public_private_count")
    if int(public_private.get("total_bytes", -1)) != total_bytes:
        errors.append("public_private_bytes")
    index_sha = hashlib.sha256(raw).hexdigest()
    if public_private.get("index_sha256") != index_sha:
        errors.append("public_private_index_sha256")

    raw_results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*-result.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("profile") != "executor_loss_retry":
            raw_results.append(item)
    for path in sorted(root.glob("*-retry-closed.json")):
        raw_results.append(json.loads(path.read_text(encoding="utf-8")))
    projected_private = Counter(
        payload_sha256(project_s5_result(item, point_id="private"))
        for item in raw_results
    )
    projected_public = Counter(
        payload_sha256({**dict(item), "point_id": "private"})
        for item in results
        if isinstance(item, Mapping)
    )
    if projected_private != projected_public:
        errors.append("private_result_projection")
    return {
        "artifact_count": len(entries),
        "total_bytes": total_bytes,
        "index_sha256": index_sha,
        "result_projection_count": len(raw_results),
    }


def _git_blob_identity(git_root: Path, revision: str, path: str) -> dict[str, str]:
    oid = _git(git_root, "rev-parse", f"{revision}:{path}")
    raw = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=git_root,
        check=True,
        capture_output=True,
    ).stdout
    return {"path": path, "blob_oid": oid, "sha256": hashlib.sha256(raw).hexdigest()}


def _git(git_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=git_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_finite_numbers(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"nonfinite:{path}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite_numbers(item, f"{path}.{key}", errors)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_finite_numbers(item, f"{path}[{index}]", errors)

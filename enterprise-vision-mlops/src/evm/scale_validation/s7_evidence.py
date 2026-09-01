from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Mapping

from evm.scale_validation.s7_manifest_contract import (
    S7ManifestContractError,
    validate_manifest_snapshot_contract,
    validate_trusted_manifest_envelope,
)
from evm.scale_validation.s7_runtime import (
    GENERATION_SCHEMAS,
    OPERATIONAL_SCHEMAS,
    QUALITY_SCHEMAS,
    S7RuntimeConfig,
    S7RuntimeError,
    analyze_s7_profiles,
    canonical_sha256,
    percentile,
    profile_family,
)


REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SOURCE_PATHS = {
    "config": "enterprise-vision-mlops/configs/s7_family_admission.toml",
    "runtime": "enterprise-vision-mlops/src/evm/scale_validation/s7_runtime.py",
    "evidence": "enterprise-vision-mlops/src/evm/scale_validation/s7_evidence.py",
    "runner": ("enterprise-vision-mlops/scripts/dev/run_s7_auxiliary_admission_experiment.py"),
    "validator": (
        "enterprise-vision-mlops/scripts/dev/validate_s7_auxiliary_admission_evidence.py"
    ),
    "admission": ("enterprise-vision-mlops/src/evm/model_runtime/family_admission.py"),
    "generative_serving": ("enterprise-vision-mlops/src/evm/model_runtime/serving.py"),
    "image_serving": ("enterprise-vision-mlops/apps/api/efficientnet_serving.py"),
    "gpu_lease": ("enterprise-vision-mlops/src/evm/control_panel/scenario_workloads.py"),
    "prometheus": ("enterprise-vision-mlops/monitoring/prometheus/prometheus.yml"),
    "image_dataset_contract": (
        "enterprise-vision-mlops/configs/scenarios/manufacturing-visual-inspection.json"
    ),
    "vlm_dataset_contract": (
        "enterprise-vision-mlops/configs/scenarios/scienceqa-vlm-evaluation.json"
    ),
    "llm_dataset_contract": (
        "enterprise-vision-mlops/configs/scenarios/dolly-instruction-tuning.json"
    ),
}
SMOKE_V3_MANIFEST_CONTRACT_PATH = (
    "enterprise-vision-mlops/src/evm/scale_validation/s7_manifest_contract.py"
)
HISTORICAL_EXPERIMENT_PATH = (
    "enterprise-vision-mlops/docs/status/evidence/s7-auxiliary-admission-experiment.json"
)
EXPERIMENT_PATH = (
    "enterprise-vision-mlops/docs/status/evidence/s7-auxiliary-admission-reprojection.json"
)
RUNTIME_SMOKE_PATH = (
    "enterprise-vision-mlops/docs/status/evidence/s7-current-revision-cuda-smoke.json"
)
CLOSURE_VALIDATOR_PATHS = {
    "validator_cli": SOURCE_PATHS["validator"],
    "validator_module": SOURCE_PATHS["evidence"],
}
HISTORICAL_EXPERIMENT_COMMIT = "c94c70d15b333b4047db55767ab291aadbae7edd"
HISTORICAL_CLOSURE_COMMIT = "3ec30392bbde2313a26a43fa9bf74b757fa7ecbe"
REGRESSION_PATH = (
    "enterprise-vision-mlops/docs/status/evidence/s7-reclosure-regression-evidence.json"
)
REQUIRED_REGRESSION_SUITES = (
    "changed_file_lint",
    "focused_s5_s6_s7",
    "real_postgresql",
    "lifecycle_host_e2e",
    "full_python",
    "control_panel",
    "frontend_production_build",
    "s0_s7_status_evidence",
)
SMOKE_CLAIM_BOUNDARY = (
    "Current-revision external-HTTP CUDA identity smoke for image, VLM, and LLM "
    "on one local physical node and one consumer GPU. ScienceQA is non-commercial "
    "research/portfolio-only. This is not production, SLA, HA, multi-GPU, or broad "
    "model-quality evidence."
)
RECLOSURE_CLAIM_SUFFIX = (
    " ScienceQA-derived VLM evidence is restricted to non-commercial portfolio "
    "and research use under CC-BY-NC-SA-4.0."
)
OVER_LIMIT_REASON = {
    "image": "image_pixels_exceeded",
    "vlm": "output_tokens_exceeded",
    "llm": "input_tokens_exceeded",
}


class S7EvidenceValidationError(RuntimeError):
    pass


def project_profile(
    payload: Mapping[str, Any], *, config: S7RuntimeConfig, errors: list[str]
) -> dict[str, Any]:
    profile_id = str(payload.get("profile_id") or "")
    prefix = f"{profile_id}:{payload.get('repetition', 'unknown')}"
    try:
        family = profile_family(profile_id)
    except S7RuntimeError:
        errors.append(f"{prefix}:family")
        family = "image"
    if payload.get("family") != family:
        errors.append(f"{prefix}:family_identity")
    requests = list(payload.get("requests", []))
    completed = [item for item in requests if item.get("outcome") == "completed"]
    rejected = [item for item in requests if item.get("outcome") == "rejected"]
    expired = [item for item in requests if item.get("outcome") == "expired"]
    transport = [item for item in requests if item.get("outcome") == "transport_failed"]
    latencies = [_finite(item.get("latency_seconds"), f"{prefix}:latency") for item in completed]
    measurement_seconds = _finite(
        payload.get("measurement_seconds"), f"{prefix}:measurement_seconds"
    )
    operational = [
        dict(item.get("response", {})).get("operational_metrics", {}) for item in completed
    ]
    allowed_operational = set(OPERATIONAL_SCHEMAS[family])
    for index, metrics in enumerate(operational):
        if not isinstance(metrics, Mapping):
            errors.append(f"{prefix}:operational_metrics_{index}")
            continue
        unexpected = set(metrics) - allowed_operational
        if unexpected:
            errors.append(f"{prefix}:unsupported_operational:{','.join(sorted(unexpected))}")
        if family == "image" and set(metrics) & {
            "input_tokens",
            "generated_tokens",
            "ttft_seconds",
            "tpot_seconds",
            "tokens_per_second",
        }:
            errors.append(f"{prefix}:image_token_metric_present")
        for key, value in metrics.items():
            _finite(value, f"{prefix}:operational:{key}")

    quality = _quality(family, completed)
    generation = _generation(family, completed)
    service_order = sorted(
        completed,
        key=lambda item: _finite(item.get("finished_offset_seconds"), f"{prefix}:finished_offset"),
    )
    long_requests = [item for item in requests if item.get("request_class") == "long"]
    short_requests = [item for item in requests if item.get("request_class") == "short"]
    long_completed = [item for item in completed if item.get("request_class") == "long"]
    short_completed = [item for item in completed if item.get("request_class") == "short"]
    maximum_short_bypass = 0
    for long_item in long_completed:
        arrived = _finite(long_item.get("arrived_offset_seconds"), f"{prefix}:long_arrived")
        finished = _finite(long_item.get("finished_offset_seconds"), f"{prefix}:long_finished")
        bypass = sum(
            1
            for item in service_order
            if item.get("request_class") == "short"
            and arrived
            <= _finite(item.get("finished_offset_seconds"), f"{prefix}:short_finished")
            < finished
        )
        maximum_short_bypass = max(maximum_short_bypass, bypass)
    long_waits = [
        _finite(
            dict(item.get("response", {}))
            .get("operational_metrics", {})
            .get("queue_wait_seconds", 0),
            f"{prefix}:long_queue_wait",
        )
        for item in long_completed
    ]
    admitted_long_requests = [item for item in long_requests if item.get("outcome") != "rejected"]
    admitted_starvation_count = sum(
        1
        for item in admitted_long_requests
        if item.get("outcome") != "completed"
        or _finite(
            dict(item.get("response", {}))
            .get("operational_metrics", {})
            .get("queue_wait_seconds", 0),
            f"{prefix}:starvation_wait",
        )
        > config.starvation_seconds
    )
    long_request_noncompletion_count = sum(
        1 for item in long_requests if item.get("outcome") != "completed"
    )
    known_outcomes = len(completed) + len(rejected) + len(expired) + len(transport)
    if known_outcomes != len(requests):
        errors.append(f"{prefix}:unknown_request_outcome")
    is_over_limit = profile_id.endswith("over-limit")
    if is_over_limit:
        expected_reason = OVER_LIMIT_REASON[family]
        intentional_rejected = [
            item
            for item in rejected
            if int(item.get("status_code", 0)) == 422
            and dict(item.get("response", {})).get("detail") == expected_reason
            and item.get("oom") is False
        ]
        if (
            len(intentional_rejected) != len(requests)
            or completed
            or expired
            or transport
            or any(item.get("oom") is not False for item in requests)
        ):
            errors.append(f"{prefix}:over_limit_outcome_invariant")
    else:
        intentional_rejected = []
        if (
            len(completed) != len(requests)
            or rejected
            or expired
            or transport
            or any(
                int(item.get("status_code", 0)) != 200 or item.get("oom") is not False
                for item in requests
            )
        ):
            errors.append(f"{prefix}:admitted_outcome_invariant")
    for item in completed:
        observed_long = _scheduling_units(family, item) >= config.long_request_cost_units[family]
        if observed_long != (item.get("request_class") == "long"):
            errors.append(f"{prefix}:request_class_cost_mismatch")
    trace_complete = bool(requests) and all(
        item.get("trace_id_sent") and item.get("trace_id_sent") == item.get("trace_id_observed")
        for item in requests
    )
    final_admission = dict(payload.get("final_admission", {}))
    drained = (
        int(final_admission.get("active_requests", -1)) == 0
        and int(final_admission.get("queue_depth", -1)) == 0
        and all(int(value) == 0 for value in dict(final_admission.get("reserved", {})).values())
    )
    resource_samples = list(payload.get("resource_samples", []))
    peak_vram = max(
        (int(sample.get("gpu_used_memory_bytes") or 0) for sample in resource_samples),
        default=0,
    )
    max_gpu_util = max(
        (
            _finite(sample.get("gpu_utilization_percent", 0), f"{prefix}:gpu_util")
            for sample in resource_samples
        ),
        default=0.0,
    )
    max_temperature = max(
        (
            _finite(sample.get("gpu_temperature_celsius", 0), f"{prefix}:gpu_temp")
            for sample in resource_samples
        ),
        default=0.0,
    )
    expected_request_count = (
        config.fairness_short_requests + config.fairness_long_requests
        if profile_id.endswith("fairness")
        else config.requests_per_profile
    )
    if len(requests) != expected_request_count:
        errors.append(f"{prefix}:request_count")
    if len({str(item.get("request_id")) for item in requests}) != len(requests):
        errors.append(f"{prefix}:request_identity_duplicate")
    if payload.get("seed_applied") is not True:
        errors.append(f"{prefix}:seed_not_applied")
    if not resource_samples:
        errors.append(f"{prefix}:resource_samples_missing")
    projection = {
        "profile_id": profile_id,
        "family": family,
        "repetition": int(payload.get("repetition", 0)),
        "request_count": len(requests),
        "accepted": len(completed),
        "completed": len(completed),
        "rejected": len(rejected),
        "expired": len(expired),
        "transport_failed": len(transport),
        "rejection_statuses": sorted({int(item.get("status_code", 0)) for item in rejected}),
        "measurement_seconds": measurement_seconds,
        "throughput_requests_per_second": (
            len(completed) / measurement_seconds if measurement_seconds else 0.0
        ),
        "p50_seconds": percentile(latencies, 0.50),
        "p95_seconds": percentile(latencies, 0.95),
        "p99_seconds": percentile(latencies, 0.99),
        "maximum_queue_wait_seconds": max(
            (
                _finite(
                    dict(item.get("response", {}))
                    .get("operational_metrics", {})
                    .get("queue_wait_seconds", 0),
                    f"{prefix}:queue_wait",
                )
                for item in completed
            ),
            default=0.0,
        ),
        "short_request_count": len(short_requests),
        "long_request_count": len(long_requests),
        "short_completed": len(short_completed),
        "long_completed": len(long_completed),
        "maximum_short_bypass": maximum_short_bypass,
        "long_request_max_wait_seconds": max(long_waits, default=0.0),
        "pre_admission_rejection_count": len(intentional_rejected),
        "admitted_starvation_count": admitted_starvation_count,
        "long_request_noncompletion_count": long_request_noncompletion_count,
        "oom_count": sum(1 for item in requests if item.get("oom") is True),
        "peak_gpu_used_memory_bytes": peak_vram,
        "gpu_utilization_percent_max": max_gpu_util,
        "gpu_temperature_celsius_max": max_temperature,
        "quality": quality,
        "metric_schema": {
            "quality": list(QUALITY_SCHEMAS[family]),
            "generation": list(GENERATION_SCHEMAS[family]),
            "operational": list(OPERATIONAL_SCHEMAS[family]),
        },
        "trace_complete": trace_complete,
        "prometheus_up": payload.get("prometheus_up") is True,
        "drained": drained,
        "lease_identity_exact": payload.get("lease_identity_exact") is True,
        "cleanup_passed": payload.get("cleanup_passed") is True,
    }
    if generation:
        projection["generation"] = generation
    return projection


def validate_s7_experiment(
    payload: Mapping[str, Any],
    *,
    config: S7RuntimeConfig,
    private_root: Path,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "evm.s7_auxiliary_admission_reprojection.v2":
        errors.append("schema_version")
    if payload.get("status") != "verified" or payload.get("verdict") != "passed":
        errors.append("verdict")
    source = dict(payload.get("source_identity", {}))
    execution_revision = str(source.get("execution_revision") or "")
    projection_revision = str(source.get("projection_revision") or "")
    for label, revision in (
        ("execution_revision", execution_revision),
        ("projection_revision", projection_revision),
    ):
        if not REVISION_PATTERN.fullmatch(revision):
            errors.append(label)
    if source.get("config_sha256") != config.sha256:
        errors.append("config_sha256")
    execution_git_identity: dict[str, Any] = {}
    projection_git_identity: dict[str, Any] = {}
    if git_root is not None and all(
        REVISION_PATTERN.fullmatch(value) for value in (execution_revision, projection_revision)
    ):
        try:
            for revision in (execution_revision, projection_revision):
                if subprocess.run(
                    ["git", "merge-base", "--is-ancestor", revision, validation_revision],
                    cwd=git_root,
                    check=False,
                ).returncode:
                    errors.append("source_revision_not_ancestor")
            execution_git_identity = source_git_identity(git_root, execution_revision)
            projection_git_identity = source_git_identity(git_root, projection_revision)
            if _canonical(source.get("execution_git_blobs")) != _canonical(execution_git_identity):
                errors.append("execution_git_blob_identity")
            if _canonical(source.get("projection_git_blobs")) != _canonical(
                projection_git_identity
            ):
                errors.append("projection_git_blob_identity")
            if projection_git_identity.get("config", {}).get("sha256") != config.sha256:
                errors.append("config_git_blob_sha256")
            historical = git_blob_identity(
                git_root, HISTORICAL_EXPERIMENT_COMMIT, HISTORICAL_EXPERIMENT_PATH
            )
            if _canonical(source.get("historical_experiment")) != _canonical(historical):
                errors.append("historical_experiment_identity")
        except (OSError, subprocess.CalledProcessError):
            errors.append("git_identity_unavailable")
    private = validate_private_evidence(private_root, errors)
    preflight = dict(private.get("documents", {}).get("preflight.json", {}))
    if preflight.get("source_revision") != execution_revision:
        errors.append("private_execution_revision")
    projected = [
        project_profile(item, config=config, errors=errors) for item in private.get("profiles", [])
    ]
    public_profiles = profile_projection_by_identity(
        list(payload.get("profiles", [])),
        errors=errors,
        label="public",
    )
    private_profiles = profile_projection_by_identity(
        projected,
        errors=errors,
        label="private",
    )
    if _canonical(public_profiles) != _canonical(private_profiles):
        errors.append("public_projection")
    try:
        analysis = analyze_s7_profiles(projected, config)
    except Exception:
        analysis = {}
        errors.append("analysis_recompute_failed")
    if _canonical(payload.get("analysis")) != _canonical(analysis):
        errors.append("analysis_projection")
    expected_assets = asset_contract_projection(
        config=config,
        git_root=git_root,
        revision=projection_revision,
    )
    if _canonical(payload.get("asset_contracts")) != _canonical(expected_assets):
        errors.append("asset_contract_projection")
    if _canonical(preflight.get("assets")) != _canonical(_preflight_asset_projection(config)):
        errors.append("private_asset_identity")
    public_private = dict(payload.get("private_evidence", {}))
    expected_private = private.get("summary", {})
    for key in ("artifact_count", "total_bytes", "aggregate_sha256", "index_sha256"):
        if public_private.get(key) != expected_private.get(key):
            errors.append(f"private_evidence:{key}")
    if payload.get("claim_boundary") != config.claim_boundary:
        errors.append("claim_boundary")
    if analysis.get("runtime_verdict") != "passed":
        errors.append("acceptance")
    if errors:
        raise S7EvidenceValidationError("s7_experiment_invalid:" + ",".join(sorted(set(errors))))
    return {
        "status": "valid",
        "execution_revision": execution_revision,
        "projection_revision": projection_revision,
        "source_identity": projection_git_identity,
        "analysis": analysis,
        "asset_contracts": expected_assets,
        "private_evidence": expected_private,
    }


def validate_s7_closure(
    closure: Mapping[str, Any],
    *,
    experiment: Mapping[str, Any],
    experiment_sha256: str,
    config: S7RuntimeConfig,
    private_root: Path,
    runtime_smoke: Mapping[str, Any],
    runtime_smoke_sha256: str,
    runtime_smoke_private_root: Path,
    runtime_smoke_trusted_envelope: Mapping[str, Any] | None = None,
    data_root: Path,
    regression_evidence: Mapping[str, Any],
    regression_evidence_sha256: str,
    regression_root: Path,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if closure.get("schema_version") != "evm.s7_auxiliary_admission_closure.v2":
        errors.append("schema_version")
    validated = validate_s7_experiment(
        experiment,
        config=config,
        private_root=private_root,
        git_root=git_root,
        validation_revision=validation_revision,
    )
    final = dict(closure.get("final_runtime_evidence", {}))
    if final.get("experiment_git_blob_sha256") != experiment_sha256:
        errors.append("experiment_sha256")
    if _canonical(final.get("acceptance")) != _canonical(validated["analysis"]["acceptance"]):
        errors.append("acceptance")
    accounting = dict(validated["analysis"].get("outcome_accounting", {}))
    expected_final = {
        "profile_repetitions": accounting.get("profile_repetitions"),
        "completed_requests": accounting.get("completed_requests"),
        "intentional_pre_admission_rejections": accounting.get(
            "intentional_pre_admission_rejections"
        ),
        "expired_requests": accounting.get("expired_requests"),
        "transport_failures": accounting.get("transport_failures"),
        "oom_count": accounting.get("all_profile_oom_count"),
        "selected_admitted_starvation_count": accounting.get("selected_admitted_starvation_count"),
        "full_matrix_long_noncompletion_count": accounting.get(
            "full_matrix_long_noncompletion_count"
        ),
        "family_repetitions": accounting.get("family_repetitions"),
    }
    for key, expected in expected_final.items():
        if _canonical(final.get(key)) != _canonical(expected):
            errors.append(f"final_runtime:{key}")
    if "starvation_count" in final:
        errors.append("final_runtime:ambiguous_starvation_count")
    smoke_result = validate_s7_runtime_smoke(
        runtime_smoke,
        config=config,
        private_root=runtime_smoke_private_root,
        data_root=data_root,
        trusted_manifest_envelope=runtime_smoke_trusted_envelope,
        trusted_public_evidence_sha256=runtime_smoke_sha256,
        git_root=git_root,
        validation_revision=validation_revision,
    )
    if smoke_result.get("status") != "valid":
        errors.append(
            "runtime_smoke:" + str(smoke_result.get("classification") or "remediation_required")
        )
    regression_result = validate_s7_regression_evidence(
        regression_evidence,
        regression_root=regression_root,
        git_root=git_root,
        validation_revision=validation_revision,
    )
    if final.get("runtime_smoke_git_blob_sha256") != runtime_smoke_sha256:
        errors.append("runtime_smoke_sha256")
    if final.get("regression_git_blob_sha256") != regression_evidence_sha256:
        errors.append("regression_sha256")
    cleanup = dict(closure.get("cleanup", {}))
    for key in (
        "source_serving_ready",
        "actual_cuda_inference",
        "s7_processes_removed",
        "gpu_lease_zero",
        "queue_and_outcome_unknown_zero",
        "prometheus_baseline_healthy",
        "private_inventory_rehash_passed",
        "git_blob_validation_passed",
    ):
        if cleanup.get(key) is not True:
            errors.append(f"cleanup:{key}")
    if closure.get("status") != "verified" or closure.get("verdict") != "passed":
        errors.append("verdict")
    if closure.get("claim_boundary") != config.claim_boundary + RECLOSURE_CLAIM_SUFFIX:
        errors.append("claim_boundary")
    source = dict(closure.get("source_identity", {}))
    experiment_commit = str(source.get("reprojection_commit") or "")
    validator_revision = str(source.get("validator_revision") or "")
    if git_root is not None:
        try:
            for revision in (experiment_commit, validator_revision):
                if not REVISION_PATTERN.fullmatch(revision):
                    raise subprocess.CalledProcessError(1, "revision")
                _git(git_root, "cat-file", "-e", f"{revision}^{{commit}}")
                if subprocess.run(
                    ["git", "merge-base", "--is-ancestor", revision, validation_revision],
                    cwd=git_root,
                    check=False,
                ).returncode:
                    errors.append("closure_revision_not_ancestor")
            expected_experiment = git_blob_identity(git_root, experiment_commit, EXPERIMENT_PATH)
            if _canonical(source.get("experiment")) != _canonical(expected_experiment):
                errors.append("closure_experiment_git_blob")
            expected_validators = {
                name: git_blob_identity(git_root, validator_revision, path)
                for name, path in CLOSURE_VALIDATOR_PATHS.items()
            }
            if _canonical(source.get("validators")) != _canonical(expected_validators):
                errors.append("closure_validator_git_blobs")
            supporting = {
                "runtime_smoke": git_blob_identity(
                    git_root, validator_revision, RUNTIME_SMOKE_PATH
                ),
                "regression": git_blob_identity(git_root, validator_revision, REGRESSION_PATH),
                "historical_closure": git_blob_identity(
                    git_root,
                    HISTORICAL_CLOSURE_COMMIT,
                    "enterprise-vision-mlops/docs/status/evidence/"
                    "s7-auxiliary-admission-closure.json",
                ),
            }
            if _canonical(source.get("supporting_evidence")) != _canonical(supporting):
                errors.append("closure_supporting_git_blobs")
        except (OSError, subprocess.CalledProcessError):
            errors.append("closure_git_identity_unavailable")
    if errors:
        raise S7EvidenceValidationError("s7_closure_invalid:" + ",".join(sorted(set(errors))))
    return {
        "status": "valid",
        "experiment_sha256": experiment_sha256,
        "acceptance": validated["analysis"]["acceptance"],
        "profile_repetitions": 36,
        "runtime_smoke": smoke_result,
        "regression": regression_result,
    }


def asset_contract_projection(
    *,
    config: S7RuntimeConfig,
    git_root: Path | None,
    revision: str,
) -> dict[str, Any]:
    raw_config = tomllib.loads(config.path.read_text(encoding="utf-8"))
    assets = dict(raw_config.get("assets", {}))
    result: dict[str, Any] = {}
    for family in ("image", "vlm", "llm"):
        raw_asset = dict(assets.get(family, {}))
        contract_path = SOURCE_PATHS[f"{family}_dataset_contract"]
        if git_root is not None and REVISION_PATTERN.fullmatch(revision):
            raw_contract = subprocess.run(
                ["git", "show", f"{revision}:{contract_path}"],
                cwd=git_root,
                check=True,
                capture_output=True,
            ).stdout
            contract_identity = git_blob_identity(git_root, revision, contract_path)
        else:
            local = config.path.parents[1] / Path(contract_path).relative_to(
                "enterprise-vision-mlops"
            )
            raw_contract = local.read_bytes()
            contract_identity = {
                "path": contract_path,
                "blob_oid": None,
                "sha256": hashlib.sha256(raw_contract).hexdigest(),
            }
        contract = json.loads(raw_contract)
        dataset = dict(contract.get("dataset", {}))
        license_id = str(dataset.get("license_id") or "")
        result[family] = {
            "scenario_contract": contract_identity,
            "dataset": {
                key: dataset.get(key)
                for key in (
                    "dataset_id",
                    "dataset_version",
                    "source_url",
                    "source_revision",
                    "license_id",
                    "license_url",
                    "usage_policy",
                )
            },
            "runtime_manifest_sha256": raw_asset.get("manifest_sha256"),
            "model": {
                "repository": raw_asset.get("model_repository") or raw_asset.get("candidate_id"),
                "revision": raw_asset.get("model_revision")
                or raw_asset.get("model_artifact_sha256"),
                "artifact_sha256": raw_asset.get("model_artifact_sha256")
                or raw_asset.get("adapter_sha256"),
                "source_commit": raw_asset.get("model_source_commit"),
                "quantization": raw_asset.get("quantization", "none"),
            },
            "noncommercial_restriction": license_id == "CC-BY-NC-SA-4.0",
        }
    return result


def validate_s7_runtime_smoke(
    payload: Mapping[str, Any],
    *,
    config: S7RuntimeConfig,
    private_root: Path,
    data_root: Path,
    trusted_manifest_envelope: Mapping[str, Any] | None = None,
    trusted_public_evidence_sha256: str | None = None,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version == "evm.s7_current_revision_cuda_smoke.v2":
        return {
            "status": "remediation_required",
            "classification": "legacy_snapshot_absent",
            "acceptance_credit": False,
            "suite_id": payload.get("suite_id"),
            "legacy_schema_version": schema_version,
            "live_manifest_rehashed": False,
        }
    if schema_version != "evm.s7_current_revision_cuda_smoke.v3":
        errors.append("smoke_schema_version")
    if (
        payload.get("status") != "verified"
        or payload.get("verdict") != "passed"
        or payload.get("acceptance_credit") is not False
    ):
        errors.append("smoke_verdict")
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
                source_git_identity(git_root, revision, include_manifest_contract=True)
            ):
                errors.append("smoke_git_blob_identity")
        except (OSError, subprocess.CalledProcessError):
            errors.append("smoke_git_identity_unavailable")

    trusted_envelope: dict[str, Any] = {}
    if trusted_manifest_envelope is None:
        errors.append("smoke_trusted_manifest_envelope_missing")
    else:
        try:
            trusted_envelope = validate_trusted_manifest_envelope(
                trusted_manifest_envelope,
                suite_id=str(payload.get("suite_id") or ""),
                source_revision=revision,
            )
            if (
                trusted_public_evidence_sha256 is None
                or trusted_envelope.get("public_evidence_sha256") != trusted_public_evidence_sha256
            ):
                errors.append("smoke_trusted_public_evidence_sha256")
        except S7ManifestContractError as exc:
            errors.append(f"smoke_trusted_manifest_envelope:{exc}")

    private = validate_private_evidence(
        private_root,
        errors,
        trusted_manifest_snapshot_binding_sha256=str(
            trusted_envelope.get("manifest_snapshot_binding_sha256") or ""
        ),
    )
    documents = dict(private.get("documents", {}))
    _validate_lifecycle_checkpoints(
        documents,
        errors,
        suite_id=str(payload.get("suite_id") or ""),
        source_revision=revision,
    )
    preflight = dict(documents.get("preflight.json", {}))
    snapshot_contract = private.get("manifest_snapshot_contract")
    snapshot_binding_sha256 = private.get("manifest_snapshot_binding_sha256")
    if private.get("summary", {}).get("index_sha256") != trusted_envelope.get(
        "private_evidence_index_sha256"
    ):
        errors.append("smoke_trusted_private_index_sha256")
    if not isinstance(snapshot_contract, Mapping):
        errors.append("smoke_manifest_snapshot_missing")
        snapshot_contract = {}
    if _canonical(source.get("manifest_snapshot_contract")) != _canonical(snapshot_contract):
        errors.append("smoke_manifest_snapshot_projection")
    if source.get("manifest_snapshot_binding_sha256") != snapshot_binding_sha256:
        errors.append("smoke_manifest_snapshot_binding")
    if _canonical(preflight.get("manifest_snapshot_contract")) != _canonical(snapshot_contract):
        errors.append("smoke_preflight_manifest_snapshot_projection")
    if preflight.get("manifest_snapshot_binding_sha256") != snapshot_binding_sha256:
        errors.append("smoke_preflight_manifest_snapshot_binding")
    runtime_overrides = _validate_runtime_asset_overrides(
        source.get("runtime_asset_overrides"),
        errors,
        config=config,
        data_root=data_root,
        validate_live_manifest=False,
    )
    if _canonical(preflight.get("runtime_asset_overrides", {})) != _canonical(runtime_overrides):
        errors.append("smoke_runtime_asset_override_projection")
    projected = [
        project_profile(item, config=config, errors=errors) for item in private.get("profiles", [])
    ]
    if len(projected) != 3 or {item.get("family") for item in projected} != {
        "image",
        "vlm",
        "llm",
    }:
        errors.append("smoke_family_profiles")
    if _canonical(
        profile_projection_by_identity(
            list(payload.get("profiles", [])), errors=errors, label="smoke_public"
        )
    ) != _canonical(
        profile_projection_by_identity(projected, errors=errors, label="smoke_private")
    ):
        errors.append("smoke_profile_projection")

    raw_config = tomllib.loads(config.path.read_text(encoding="utf-8"))
    snapshot_families = dict(snapshot_contract.get("families", {}))
    configured_assets = dict(raw_config.get("assets", {}))
    for family in ("image", "vlm", "llm"):
        snapshot_identity = dict(snapshot_families.get(family, {}))
        configured_asset = dict(configured_assets.get(family, {}))
        override = dict(runtime_overrides.get(family, {}))
        expected_raw_sha256 = (
            override.get("observed_manifest_sha256")
            if override
            else configured_asset.get("manifest_sha256")
        )
        if snapshot_identity.get("raw_sha256") != expected_raw_sha256:
            errors.append(f"smoke_manifest_snapshot_raw:{family}")
        if override and snapshot_identity.get("record_count") != override.get("record_count"):
            errors.append(f"smoke_manifest_snapshot_records:{family}")
    ready_projection: dict[str, Any] = {}
    for family in ("image", "vlm", "llm"):
        ready = dict(documents.get(f"{family}-ready.json", {}))
        try:
            ready_projection[family] = _ready_identity_projection(
                family,
                ready,
                dict(dict(raw_config.get("assets", {})).get(family, {})),
                revision,
            )
        except (KeyError, TypeError, ValueError):
            errors.append(f"smoke_ready_identity:{family}")
    if _canonical(payload.get("family_ready_identity")) != _canonical(ready_projection):
        errors.append("smoke_ready_projection")

    expected_contracts = asset_contract_projection(
        config=config, git_root=git_root, revision=revision
    )
    expected_preflight_assets = _preflight_asset_projection(config)
    for family, override in runtime_overrides.items():
        expected_contracts[family]["runtime_manifest_sha256"] = override["observed_manifest_sha256"]
        expected_preflight_assets[family]["manifest_sha256"] = override["observed_manifest_sha256"]
    if _canonical(preflight.get("assets")) != _canonical(expected_preflight_assets):
        errors.append("smoke_preflight_asset_identity")
    provenance_projection: dict[str, Any] = {}
    for family in ("image", "vlm", "llm"):
        raw_provenance = dict(documents.get(f"asset-provenance/{family}.json", {}))
        provenance_projection[family] = _validate_asset_provenance(
            family,
            raw_provenance,
            expected_contracts[family],
            errors,
        )
    if _canonical(payload.get("asset_provenance")) != _canonical(provenance_projection):
        errors.append("smoke_asset_provenance_projection")

    runtime = dict(payload.get("runtime_evidence", {}))
    expected_runtime = {
        "transport": "external_http",
        "submitted_requests": sum(int(item["request_count"]) for item in projected),
        "completed_requests": sum(int(item["completed"]) for item in projected),
        "rejected_requests": sum(int(item["rejected"]) for item in projected),
        "transport_failures": sum(int(item["transport_failed"]) for item in projected),
        "actual_cuda": all(
            item.get("cuda_available") is True
            or dict(item.get("runtime", {})).get("cuda_available") is True
            for item in ready_projection.values()
        ),
        "trace_identity_complete": all(item["trace_complete"] for item in projected),
        "oom_count": sum(int(item["oom_count"]) for item in projected),
        "admitted_starvation_count": sum(
            int(item["admitted_starvation_count"]) for item in projected
        ),
    }
    if _canonical(runtime) != _canonical(expected_runtime):
        errors.append("smoke_runtime_projection")
    if any(
        int(item.get("request_count", -1)) != config.requests_per_profile
        or int(item.get("completed", -1)) != config.requests_per_profile
        or int(item.get("rejected", -1)) != 0
        or int(item.get("expired", -1)) != 0
        or int(item.get("transport_failed", -1)) != 0
        or int(item.get("pre_admission_rejection_count", -1)) != 0
        or int(item.get("oom_count", -1)) != 0
        or int(item.get("admitted_starvation_count", -1)) != 0
        or item.get("trace_complete") is not True
        or item.get("prometheus_up") is not True
        or item.get("drained") is not True
        or item.get("lease_identity_exact") is not True
        or item.get("cleanup_passed") is not True
        for item in projected
    ):
        errors.append("smoke_success_invariants")
    if expected_runtime != {
        "transport": "external_http",
        "submitted_requests": 18,
        "completed_requests": 18,
        "rejected_requests": 0,
        "transport_failures": 0,
        "actual_cuda": True,
        "trace_identity_complete": True,
        "oom_count": 0,
        "admitted_starvation_count": 0,
    }:
        errors.append("smoke_runtime_success_invariants")
    if (
        ready_projection.get("llm", {}).get("quantization_observed") != "int4_nf4"
        or ready_projection.get("llm", {}).get("loaded_in_4bit") is not True
        or int(ready_projection.get("llm", {}).get("linear_4bit_module_count", 0)) < 1
    ):
        errors.append("smoke_llm_4bit_runtime")
    if expected_contracts["vlm"].get("noncommercial_restriction") is not True:
        errors.append("smoke_vlm_noncommercial_boundary")

    cleanup = dict(payload.get("cleanup", {}))
    expected_cleanup = {
        "source_serving_ready": True,
        "source_cuda_inference": True,
        "source_model_identity_exact": True,
        "source_candidate_identity_exact": True,
        "source_holder_identity_exact": True,
        "service_processes_stopped": True,
        "gpu_lease_zero": True,
        "family_queues_drained": True,
        "s7_prometheus_target_zero": True,
        "prometheus_baseline_target_count": 5,
        "prometheus_baseline_up_count": 5,
    }
    if _canonical(cleanup) != _canonical(expected_cleanup):
        errors.append("smoke_cleanup")
    public_private = dict(payload.get("private_evidence", {}))
    for key in ("artifact_count", "total_bytes", "aggregate_sha256", "index_sha256"):
        if public_private.get(key) != private.get("summary", {}).get(key):
            errors.append(f"smoke_private_evidence:{key}")
    if payload.get("claim_boundary") != SMOKE_CLAIM_BOUNDARY:
        errors.append("smoke_claim_boundary")
    if errors:
        raise S7EvidenceValidationError("s7_runtime_smoke_invalid:" + ",".join(sorted(set(errors))))
    return {
        "status": "valid",
        "classification": "immutable_manifest_snapshots_valid",
        "revision": revision,
        "families": 3,
        "private_evidence": private.get("summary", {}),
        "manifest_snapshot_binding_sha256": snapshot_binding_sha256,
    }


def validate_s7_regression_evidence(
    payload: Mapping[str, Any],
    *,
    regression_root: Path,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "evm.s7_reclosure_regression.v1":
        errors.append("regression_schema_version")
    if payload.get("status") != "passed":
        errors.append("regression_status")
    revision = str(dict(payload.get("source_identity", {})).get("revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("regression_revision")
    elif (
        git_root is not None
        and subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, validation_revision],
            cwd=git_root,
            check=False,
        ).returncode
    ):
        errors.append("regression_revision_not_ancestor")
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
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("log_sha256") or ""))
            or int(item.get("log_bytes", 0)) <= 0
        ):
            errors.append(f"regression_suite:{suite_id}")
        relative = Path(str(item.get("log_path") or ""))
        target = regression_root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not target.is_file()
            or target.stat().st_size != int(item.get("log_bytes", -1))
            or _file_sha256(target) != item.get("log_sha256")
        ):
            errors.append(f"regression_log_identity:{suite_id}")
        elif not _regression_log_matches_counts(
            target,
            suite_id=suite_id,
            tests_passed=int(item.get("tests_passed", -1)),
            tests_skipped=int(item.get("tests_skipped", -1)),
        ):
            errors.append(f"regression_log_counts:{suite_id}")
        if (
            suite_id not in {"changed_file_lint", "frontend_production_build"}
            and int(item.get("tests_passed", 0)) <= 0
        ):
            errors.append(f"regression_test_count:{suite_id}")
    if errors:
        raise S7EvidenceValidationError("s7_regression_invalid:" + ",".join(sorted(set(errors))))
    return {"status": "valid", "revision": revision, "suite_count": len(suites)}


def _regression_log_matches_counts(
    path: Path, *, suite_id: str, tests_passed: int, tests_skipped: int
) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    if suite_id == "changed_file_lint":
        return tests_passed == 0 and tests_skipped == 0 and "All checks passed!" in text
    if suite_id == "frontend_production_build":
        return tests_passed == 0 and tests_skipped == 0 and "built in" in text
    pytest_matches = re.findall(
        r"(?m)^(\d+) passed(?:, (\d+) skipped)?(?:, \d+ warnings?)? in ", text
    )
    passed = sum(int(item[0]) for item in pytest_matches)
    skipped = sum(int(item[1] or 0) for item in pytest_matches)
    vitest_matches = re.findall(r"(?m)^\s*Tests\s+(\d+) passed", text)
    passed += sum(int(item) for item in vitest_matches)
    return passed == tests_passed and skipped == tests_skipped


def _preflight_asset_projection(config: S7RuntimeConfig) -> dict[str, Any]:
    assets = dict(tomllib.loads(config.path.read_text(encoding="utf-8")).get("assets", {}))
    return {
        family: {
            "manifest_sha256": raw.get("manifest_sha256"),
            "model_artifact_sha256": raw.get("model_artifact_sha256") or raw.get("adapter_sha256"),
            "model_revision": raw.get("model_revision"),
            "data_identity_sha256": raw.get("data_identity_sha256") or raw.get("dataset_version"),
            "quantization": raw.get("quantization", "none"),
        }
        for family, raw_value in assets.items()
        if family in {"image", "vlm", "llm"}
        for raw in [dict(raw_value)]
    }


def _validate_runtime_asset_overrides(
    value: Any,
    errors: list[str],
    *,
    config: S7RuntimeConfig,
    data_root: Path,
    validate_live_manifest: bool = True,
) -> dict[str, dict[str, Any]]:
    overrides = dict(value) if isinstance(value, Mapping) else {}
    raw_assets = dict(tomllib.loads(config.path.read_text(encoding="utf-8")).get("assets", {}))
    if set(overrides) - {"image"}:
        errors.append("smoke_runtime_asset_override_scope")
    for family, raw in overrides.items():
        item = dict(raw)
        if (
            family != "image"
            or item.get("scope") != "non_acceptance_current_revision_diagnostic_only"
            or item.get("reason") != "curated_manifest_regenerated_after_accepted_matrix"
            or item.get("acceptance_credit") is not False
            or item.get("dataset_version") != "visa-open-data-e35d93d5561f"
            or int(item.get("record_count", 0)) < 6
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("frozen_manifest_sha256") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("observed_manifest_sha256") or ""))
            or item.get("frozen_manifest_sha256") == item.get("observed_manifest_sha256")
        ):
            errors.append("smoke_runtime_asset_override_identity")
    if not validate_live_manifest:
        return {family: dict(raw) for family, raw in overrides.items()}
    for family in ("image", "vlm", "llm"):
        raw_asset = dict(raw_assets.get(family, {}))
        manifest = data_root / str(raw_asset.get("manifest") or "")
        if not manifest.is_file():
            errors.append(f"smoke_runtime_manifest_missing:{family}")
            continue
        observed_sha = _file_sha256(manifest)
        override = dict(overrides.get(family, {}))
        expected_sha = (
            override.get("observed_manifest_sha256")
            if override
            else raw_asset.get("manifest_sha256")
        )
        if observed_sha != expected_sha:
            errors.append(f"smoke_runtime_manifest_sha256:{family}")
        if override:
            record_count = sum(
                1 for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()
            )
            if int(override.get("record_count", -1)) != record_count:
                errors.append(f"smoke_runtime_manifest_records:{family}")
    return {family: dict(raw) for family, raw in overrides.items()}


def _ready_identity_projection(
    family: str,
    ready: Mapping[str, Any],
    asset: Mapping[str, Any],
    revision: str,
) -> dict[str, Any]:
    if family == "image":
        expected = {
            "status": "ok",
            "model_sha256": asset.get("model_artifact_sha256"),
            "candidate_id": asset.get("candidate_id"),
            "dataset_version": asset.get("dataset_version"),
            "device": "cuda",
            "cuda_available": True,
        }
        if any(ready.get(key) != value for key, value in expected.items()):
            raise ValueError("image_ready_identity")
        return {
            "status": "ok",
            "model_family": "image",
            "candidate_id": ready["candidate_id"],
            "model_artifact_sha256": ready["model_sha256"],
            "data_identity_sha256": ready["dataset_version"],
            "device": "cuda",
            "cuda_available": True,
            "quantization_requested": "none",
            "quantization_observed": "none",
        }
    quantization = dict(ready.get("quantization_runtime", {}))
    expected_observed = "int4_nf4" if family == "llm" else "none"
    expected = {
        "status": "ready",
        "model_family": family,
        "model_repository": asset.get("model_repository"),
        "model_revision": asset.get("model_revision"),
        "model_artifact_sha256": asset.get("adapter_sha256"),
        "data_identity_sha256": asset.get("data_identity_sha256"),
        "model_source_commit": asset.get("model_source_commit"),
        "runtime_source_commit": revision,
        "quantization": asset.get("quantization", "none"),
    }
    if any(ready.get(key) != value for key, value in expected.items()):
        raise ValueError(f"{family}_ready_identity")
    if (
        quantization.get("requested") != asset.get("quantization", "none")
        or quantization.get("observed") != expected_observed
        or dict(ready.get("runtime", {})).get("cuda_available") is not True
    ):
        raise ValueError(f"{family}_runtime_identity")
    if family == "llm" and (
        quantization.get("loaded_in_4bit") is not True
        or int(quantization.get("linear_4bit_module_count", 0)) < 1
    ):
        raise ValueError("llm_4bit_runtime")
    return {
        "status": "ready",
        "model_family": family,
        "model_repository": ready["model_repository"],
        "model_revision": ready["model_revision"],
        "model_artifact_sha256": ready["model_artifact_sha256"],
        "data_identity_sha256": ready["data_identity_sha256"],
        "model_source_commit": ready["model_source_commit"],
        "runtime_source_commit": ready["runtime_source_commit"],
        "quantization_requested": asset.get("quantization", "none"),
        "quantization_observed": quantization["observed"],
        "loaded_in_4bit": quantization.get("loaded_in_4bit", False),
        "linear_4bit_module_count": int(quantization.get("linear_4bit_module_count", 0)),
        "runtime": {
            "cuda_available": True,
            "torch": dict(ready["runtime"]).get("torch"),
            "cuda": dict(ready["runtime"]).get("cuda"),
        },
    }


def _validate_asset_provenance(
    family: str,
    payload: Mapping[str, Any],
    expected: Mapping[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    if payload.get("schema_version") != "evm.s7_asset_provenance.v1":
        errors.append(f"asset_provenance_schema:{family}")
    contract = dict(expected.get("scenario_contract", {}))
    observed_contract_path = str(payload.get("scenario_contract_path") or "")
    normalized_contract_path = (
        observed_contract_path
        if observed_contract_path.startswith("enterprise-vision-mlops/")
        else f"enterprise-vision-mlops/{observed_contract_path}"
    )
    if (
        payload.get("family") != family
        or normalized_contract_path != contract.get("path")
        or payload.get("scenario_contract_sha256") != contract.get("sha256")
        or _canonical(payload.get("dataset")) != _canonical(expected.get("dataset"))
        or payload.get("runtime_manifest_sha256") != expected.get("runtime_manifest_sha256")
        or _canonical(payload.get("model")) != _canonical(expected.get("model"))
    ):
        errors.append(f"asset_provenance_identity:{family}")
    cache = dict(payload.get("cache_manifest", {}))
    entries = list(cache.get("entries", []))
    if (
        not entries
        or int(cache.get("file_count", -1)) != len(entries)
        or int(cache.get("total_bytes", -1)) != sum(int(item.get("bytes", -1)) for item in entries)
        or cache.get("aggregate_sha256") != canonical_sha256(entries)
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
            or int(item.get("bytes", 0)) <= 0
            for item in entries
        )
    ):
        errors.append(f"asset_cache_manifest:{family}")
    return {
        "scenario_contract_path": payload.get("scenario_contract_path"),
        "scenario_contract_sha256": payload.get("scenario_contract_sha256"),
        "dataset": payload.get("dataset"),
        "runtime_manifest_sha256": payload.get("runtime_manifest_sha256"),
        "model": payload.get("model"),
        "cache_manifest": {
            key: cache.get(key) for key in ("file_count", "total_bytes", "aggregate_sha256")
        },
    }


def validate_private_evidence(
    root: Path,
    errors: list[str],
    *,
    trusted_manifest_snapshot_binding_sha256: str | None = None,
) -> dict[str, Any]:
    index_path = root / "private-evidence-index.json"
    if not index_path.is_file():
        errors.append("private_index_missing")
        return {"profiles": [], "summary": {}}
    raw = index_path.read_bytes()
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        errors.append("private_index_not_canonical_lf")
    try:
        index = json.loads(raw)
    except json.JSONDecodeError:
        errors.append("private_index_invalid")
        return {"profiles": [], "summary": {}}
    index_schema = index.get("schema_version")
    if index_schema not in {
        "evm.s7_private_evidence_index.v1",
        "evm.s7_private_evidence_index.v2",
    }:
        errors.append("private_index_schema")
    if index_schema == "evm.s7_private_evidence_index.v2" and set(index) != {
        "schema_version",
        "suite_id",
        "manifest_snapshot_contract",
        "manifest_snapshot_binding_sha256",
        "artifacts",
        "aggregate_sha256",
        "generated_at",
    }:
        errors.append("private_index_v2_keys")
    entries = list(index.get("artifacts", []))
    indexed_paths = {str(entry.get("path") or "") for entry in entries}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "private-evidence-index.json"
    }
    if actual_paths != indexed_paths:
        for relative in sorted(actual_paths - indexed_paths):
            errors.append(f"private_unindexed_artifact:{relative}")
        for relative in sorted(indexed_paths - actual_paths):
            errors.append(f"private_indexed_artifact_absent:{relative}")
    observed: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    for entry in entries:
        relative = str(entry.get("path") or "")
        path = root / relative
        if not relative or not path.is_file():
            errors.append(f"private_artifact_missing:{relative}")
            continue
        digest = _file_sha256(path)
        size = path.stat().st_size
        observed.append({"path": relative, "sha256": digest, "bytes": size})
        if digest != entry.get("sha256") or size != int(entry.get("bytes", -1)):
            errors.append(f"private_artifact_identity:{relative}")
        if relative.startswith("profiles/") and relative.endswith(".json"):
            try:
                profiles.append(json.loads(path.read_bytes()))
            except json.JSONDecodeError:
                errors.append(f"private_profile_invalid:{relative}")
        elif relative.endswith(".json"):
            try:
                value = json.loads(path.read_bytes())
                if isinstance(value, dict):
                    documents[relative] = value
            except json.JSONDecodeError:
                errors.append(f"private_document_invalid:{relative}")
    observed.sort(key=lambda item: item["path"])
    if _canonical(observed) != _canonical(entries):
        errors.append("private_index_projection")
    aggregate = canonical_sha256(observed)
    if aggregate != index.get("aggregate_sha256"):
        errors.append("private_aggregate_sha256")
    summary = {
        "artifact_count": len(observed),
        "total_bytes": sum(int(item["bytes"]) for item in observed),
        "aggregate_sha256": aggregate,
        "index_sha256": hashlib.sha256(raw).hexdigest(),
    }
    snapshot_contract: dict[str, Any] | None = None
    snapshot_binding_sha256: str | None = None
    if index_schema == "evm.s7_private_evidence_index.v2":
        raw_contract = index.get("manifest_snapshot_contract")
        if not isinstance(raw_contract, Mapping):
            errors.append("private_manifest_snapshot_contract")
        else:
            snapshot_contract = dict(raw_contract)
            snapshot_binding_sha256 = str(index.get("manifest_snapshot_binding_sha256") or "")
            try:
                validate_manifest_snapshot_contract(
                    suite_root=root,
                    suite_id=str(index.get("suite_id") or ""),
                    contract=snapshot_contract,
                    indexed_artifacts=entries,
                    trusted_binding_sha256=(
                        trusted_manifest_snapshot_binding_sha256
                        if trusted_manifest_snapshot_binding_sha256 is not None
                        else snapshot_binding_sha256
                    ),
                )
            except (OSError, S7ManifestContractError, ValueError) as exc:
                errors.append(f"private_manifest_snapshot:{exc}")
    return {
        "profiles": profiles,
        "documents": documents,
        "summary": summary,
        "manifest_snapshot_contract": snapshot_contract,
        "manifest_snapshot_binding_sha256": snapshot_binding_sha256,
        "legacy_snapshot_absent": index_schema == "evm.s7_private_evidence_index.v1",
    }


def _validate_lifecycle_checkpoints(
    documents: Mapping[str, Mapping[str, Any]],
    errors: list[str],
    *,
    suite_id: str,
    source_revision: str,
) -> None:
    pre = dict(documents.get("lifecycle-pre-mutation.json", {}))
    post = dict(documents.get("lifecycle-post-restore.json", {}))
    if (
        pre.get("schema_version") != "evm.s7_lifecycle_checkpoint.v1"
        or pre.get("stage") != "pre_mutation"
        or pre.get("suite_id") != suite_id
        or pre.get("mutations_started") is not False
        or pre.get("active_gpu_lease") is not None
    ):
        errors.append("smoke_lifecycle_pre_mutation")
    pre_holder = dict(pre.get("holder", {}))
    try:
        pre_replicas = int(pre_holder.get("replicas", -1))
    except (TypeError, ValueError):
        pre_replicas = -1
    if (
        not str(pre_holder.get("uid") or "")
        or not str(pre_holder.get("image") or "")
        or pre_replicas < 1
    ):
        errors.append("smoke_lifecycle_pre_holder")
    pre_file_sd = dict(pre.get("file_sd", {}))
    if set(pre_file_sd) != {"exists", "bytes", "sha256"}:
        errors.append("smoke_lifecycle_pre_file_sd_schema")
    else:
        encoded = pre.get("file_sd_restore_bytes_base64")
        if pre_file_sd.get("exists") is True:
            try:
                restored_raw = base64.b64decode(str(encoded), validate=True)
                expected_bytes = int(pre_file_sd.get("bytes", -1))
            except (TypeError, ValueError, binascii.Error):
                errors.append("smoke_lifecycle_pre_file_sd_base64")
            else:
                if len(restored_raw) != expected_bytes or hashlib.sha256(
                    restored_raw
                ).hexdigest() != pre_file_sd.get("sha256"):
                    errors.append("smoke_lifecycle_pre_file_sd_identity")
        elif pre_file_sd != {"exists": False, "bytes": 0, "sha256": None} or encoded is not None:
            errors.append("smoke_lifecycle_pre_file_sd_absent")

    post_prometheus = dict(post.get("prometheus", {}))
    if (
        post.get("schema_version") != "evm.s7_lifecycle_checkpoint.v1"
        or post.get("stage") != "post_restore"
        or post.get("suite_id") != suite_id
        or _canonical(post.get("holder")) != _canonical(pre_holder)
        or post.get("holder_uid_exact") is not True
        or post.get("holder_image_exact") is not True
        or post.get("holder_replicas_exact") is not True
        or post.get("active_gpu_lease") is not None
        or _canonical(post.get("file_sd")) != _canonical(pre_file_sd)
        or post.get("file_sd_matches_pre_mutation") is not True
        or post.get("restore_complete") is not True
        or post_prometheus != {"target_count": 5, "up_count": 5, "all_up": True}
    ):
        errors.append("smoke_lifecycle_post_restore")

    lease_keys = {
        "schema_version",
        "stage",
        "run_id",
        "lease_id",
        "fencing_token_sha256",
        "scenario_id",
        "model_family",
        "lease_purpose",
        "source_commit",
        "owner_pid",
        "acquired_at",
        "expires_at",
        "state",
        "released_at",
        "release_reason",
    }
    for family in ("image", "vlm", "llm"):
        acquired = dict(documents.get(f"{family}-lease-acquired.json", {}))
        released = dict(documents.get(f"{family}-lease-released.json", {}))
        cleanup = dict(documents.get(f"{family}-cleanup.json", {}))
        common = (
            "run_id",
            "lease_id",
            "fencing_token_sha256",
            "scenario_id",
            "model_family",
            "lease_purpose",
            "source_commit",
            "owner_pid",
            "acquired_at",
            "expires_at",
        )
        expected_run_id = f"s7-{family}-{suite_id}"
        if (
            set(acquired) != lease_keys
            or acquired.get("schema_version") != "evm.s7_gpu_lease_checkpoint.v1"
            or acquired.get("stage") != "acquired"
            or acquired.get("run_id") != expected_run_id
            or acquired.get("scenario_id") != "S7"
            or acquired.get("model_family") != family
            or acquired.get("lease_purpose") != "scale_validation_inference"
            or acquired.get("source_commit") != source_revision
            or not re.fullmatch(r"[0-9a-f]{64}", str(acquired.get("fencing_token_sha256") or ""))
            or acquired.get("state") != "active"
            or acquired.get("released_at") is not None
            or acquired.get("release_reason") is not None
        ):
            errors.append(f"smoke_lifecycle_lease_acquired:{family}")
        if (
            set(released) != lease_keys
            or released.get("schema_version") != "evm.s7_gpu_lease_checkpoint.v1"
            or released.get("stage") != "released"
            or any(released.get(key) != acquired.get(key) for key in common)
            or released.get("state") != "released"
            or not str(released.get("released_at") or "")
            or released.get("release_reason") != f"S7 {family} family profiles completed"
        ):
            errors.append(f"smoke_lifecycle_lease_released:{family}")
        process = dict(cleanup.get("process_evidence", {}))
        if (
            cleanup.get("family") != family
            or cleanup.get("lease_state") != "released"
            or cleanup.get("service_process_stopped") is not True
            or cleanup.get("active_lease_zero") is not True
            or process.get("schema_version") != "evm.s7_cooperative_service_stop.v1"
            or process.get("family") != family
            or process.get("signal_error") is not None
            or process.get("residual_process_count") != 0
            or process.get("residual_processes") != []
            or process.get("forced_termination_attempts") != 0
            or process.get("automatic_retry_count") != 0
            or process.get("subsequent_probe_after_residual") != 0
        ):
            errors.append(f"smoke_lifecycle_cleanup:{family}")


def profile_projection_by_identity(
    profiles: list[Any],
    *,
    errors: list[str],
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(profiles):
        if not isinstance(value, Mapping):
            errors.append(f"{label}_profile_invalid:{index}")
            continue
        profile = dict(value)
        profile_id = str(profile.get("profile_id") or "")
        try:
            repetition = int(profile.get("repetition", 0))
        except (TypeError, ValueError):
            errors.append(f"{label}_profile_identity:{index}")
            continue
        if not profile_id or repetition < 1:
            errors.append(f"{label}_profile_identity:{index}")
            continue
        identity = f"{profile_id}:r{repetition:02d}"
        if identity in result:
            errors.append(f"{label}_profile_duplicate:{identity}")
            continue
        result[identity] = profile
    return result


def source_git_identity(
    git_root: Path, revision: str, *, include_manifest_contract: bool = False
) -> dict[str, Any]:
    _git(git_root, "cat-file", "-e", f"{revision}^{{commit}}")
    result = {
        name: git_blob_identity(git_root, revision, path) for name, path in SOURCE_PATHS.items()
    }
    if include_manifest_contract:
        result["manifest_contract"] = git_blob_identity(
            git_root, revision, SMOKE_V3_MANIFEST_CONTRACT_PATH
        )
    return result


def git_blob_identity(git_root: Path, revision: str, path: str) -> dict[str, Any]:
    raw = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=git_root,
        check=True,
        capture_output=True,
    ).stdout
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        raise subprocess.CalledProcessError(1, "git_blob_not_canonical_lf")
    oid = _git(git_root, "rev-parse", f"{revision}:{path}")
    return {"path": path, "blob_oid": oid, "sha256": hashlib.sha256(raw).hexdigest()}


def token_f1(prediction: str, expected: str) -> float:
    left = prediction.lower().split()
    right = expected.lower().split()
    if not left or not right:
        return 0.0
    counts: dict[str, int] = {}
    for token in right:
        counts[token] = counts.get(token, 0) + 1
    overlap = 0
    for token in left:
        if counts.get(token, 0) > 0:
            counts[token] -= 1
            overlap += 1
    if overlap == 0:
        return 0.0
    precision = overlap / len(left)
    recall = overlap / len(right)
    return 2 * precision * recall / (precision + recall)


def _quality(family: str, completed: list[Mapping[str, Any]]) -> dict[str, float]:
    if not completed:
        return {key: 0.0 for key in QUALITY_SCHEMAS[family]}
    if family == "image":
        correct = sum(
            1
            for item in completed
            if str(dict(item.get("response", {})).get("prediction")) == str(item.get("expected"))
        )
        confidence = [
            _finite(dict(item.get("response", {})).get("confidence"), "confidence")
            for item in completed
        ]
        return {
            "binary_accuracy": correct / len(completed),
            "mean_confidence": sum(confidence) / len(confidence),
        }
    if family == "vlm":
        parsed = [
            item
            for item in completed
            if dict(item.get("response", {})).get("predicted_index") is not None
        ]
        correct = sum(
            1
            for item in parsed
            if int(dict(item.get("response", {}))["predicted_index"]) == int(item.get("expected"))
        )
        return {
            "accuracy": correct / len(completed),
            "parse_rate": len(parsed) / len(completed),
        }
    scores = [
        token_f1(
            str(dict(item.get("response", {})).get("output") or ""),
            str(item.get("expected") or ""),
        )
        for item in completed
    ]
    nonempty = sum(
        1 for item in completed if str(dict(item.get("response", {})).get("output") or "").strip()
    )
    return {
        "mean_token_f1": sum(scores) / len(scores),
        "nonempty_rate": nonempty / len(completed),
    }


def _generation(family: str, completed: list[Mapping[str, Any]]) -> dict[str, Any]:
    if family == "image" or not completed:
        return {}
    metrics = [
        dict(dict(item.get("response", {})).get("operational_metrics", {})) for item in completed
    ]
    generated = [int(item.get("generated_tokens", 0)) for item in metrics]
    token_rates = [_finite(item.get("tokens_per_second", 0), "token_rate") for item in metrics]
    ttft = [_finite(item["ttft_seconds"], "ttft") for item in metrics if "ttft_seconds" in item]
    tpot = [_finite(item["tpot_seconds"], "tpot") for item in metrics if "tpot_seconds" in item]
    reasons: dict[str, int] = {}
    for item in completed:
        reason = str(dict(item.get("response", {})).get("termination_reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "generated_tokens_total": sum(generated),
        "tokens_per_second_mean": sum(token_rates) / len(token_rates),
        "ttft_p95_seconds": percentile(ttft, 0.95),
        "tpot_p95_seconds": percentile(tpot, 0.95),
        "termination_reasons": reasons,
    }


def _scheduling_units(family: str, item: Mapping[str, Any]) -> float:
    metrics = dict(dict(item.get("response", {})).get("operational_metrics", {}))
    request_units = _finite(metrics.get("request_bytes", 0), "request_bytes") / 1024
    if family == "image":
        return max(
            1.0,
            request_units,
            _finite(metrics.get("image_bytes", 0), "image_bytes") / 1024,
            _finite(metrics.get("image_pixels", 0), "image_pixels") / 1024,
        )
    requested_output = int(item.get("requested_output_tokens", 0))
    if family == "vlm":
        return max(
            1.0,
            request_units,
            _finite(metrics.get("image_bytes", 0), "image_bytes") / 1024,
            _finite(metrics.get("image_pixels", 0), "image_pixels") / 1024,
            1536 + requested_output,
        )
    return max(
        1.0,
        request_units,
        _finite(metrics.get("input_tokens", 0), "input_tokens") + requested_output,
    )


def _finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise S7EvidenceValidationError(f"s7_metric_invalid:{label}") from exc
    if not (result >= 0) or result in {float("inf"), float("-inf")}:
        raise S7EvidenceValidationError(f"s7_metric_invalid:{label}")
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()

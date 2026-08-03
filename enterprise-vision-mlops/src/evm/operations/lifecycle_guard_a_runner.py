from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import tomllib
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evm.control_panel.deployment_executor import model_mount_path
from evm.operations.failure_evidence import sha256_file
from evm.operations.failure_scenarios import (
    ApprovalRejected,
    ApprovalStore,
    ScenarioStateStore,
    TargetRef,
    atomic_write_json,
)
from evm.operations.reconciliation import (
    discover_wsl_driver_paths,
    plan_device_plugin_reconciliation,
)
from evm.operations.runtime_adapters import (
    HttpAdapter,
    KubernetesAdapter,
    ScenarioACollector,
    pod_is_historical,
    select_prometheus_target,
)
from evm.operations.scenario_a_live import (
    post_inference,
    run_scenario_a_live,
    validate_inference,
)
from evm.operations.scenario_a_preflight import (
    issue_scenario_a_approval,
    prepare_scenario_a_preflight,
)
from evm.operations.scenario_a_runner import (
    ScenarioAArtifactConfig,
    ScenarioAConfig,
    ScenarioAExecutionConfig,
    ScenarioAHttpConfig,
    ScenarioAInferenceConfig,
    ScenarioARuntimeConfig,
    ScenarioATargetConfig,
    collect_runtime_source,
    discover_scenario_a_selectors,
    load_scenario_a_config,
    run_read_only_baseline,
)
from evm.operations.target_health import (
    ScenarioAIdentityContract,
    evaluate_scenario_a_health,
)


class ScenarioAIntegrationError(RuntimeError):
    pass


WINDOWS_ATOMIC_PATH_BUDGET = 240


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntegrationTarget(StrictModel):
    namespace: str
    deployment: str
    pod_label: str
    container: str = "serving"


class IntegrationM0(StrictModel):
    scenario_config_path: Path
    candidate_id: str
    dataset_version: str
    model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_digest: str


class IntegrationM1(StrictModel):
    lifecycle_run_id: str
    lifecycle_run_root: Path
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    candidate_id: str
    dataset_version: str
    model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_digest: str
    ct_report_path: Path
    sample_image_uri: str
    expected_prediction: str


class IntegrationExecution(StrictModel):
    evidence_root: Path
    detection_budget_seconds: float = Field(gt=0)
    recovery_budget_seconds: float = Field(gt=0)
    sample_cadence_seconds: float = Field(gt=0)
    prometheus_convergence_seconds: float = Field(gt=0)
    approval_ttl_seconds: int = Field(gt=0)


class IntegrationConfig(StrictModel):
    target: IntegrationTarget
    m0: IntegrationM0
    m1: IntegrationM1
    execution: IntegrationExecution


class ModelIdentity(StrictModel):
    candidate_id: str
    dataset_version: str
    model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_digest: str
    model_path: str


class M1Package(StrictModel):
    identity: ModelIdentity
    lifecycle_run_id: str
    lifecycle_series_id: str
    attempt_id: str
    correlation_id: str
    source_revision: str
    mlflow_run_id: str
    ct_evaluation_id: str
    artifact_paths: dict[str, str]
    artifact_sha256: dict[str, str]


class TargetSnapshot(StrictModel):
    captured_at: str
    deployment_uid: str
    deployment_resource_version: str
    deployment_generation: int
    pod_name: str
    pod_uid: str
    identity: ModelIdentity
    deployment: dict[str, Any]
    pod: dict[str, Any]
    readiness: dict[str, Any]
    prediction: dict[str, Any]
    prometheus: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScenarioAIntegrationError(f"json_unavailable:{path}") from exc
    if not isinstance(payload, dict):
        raise ScenarioAIntegrationError(f"json_object_required:{path}")
    return payload


def payload_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_config(path: Path) -> IntegrationConfig:
    with path.open("rb") as handle:
        return IntegrationConfig.model_validate(tomllib.load(handle))


def normalized_image_digest(value: str) -> str:
    digest = value.rsplit("@", maxsplit=1)[-1]
    if not digest.startswith("sha256:"):
        digest = f"sha256:{digest.removeprefix('sha256:')}"
    if len(digest) != 71:
        raise ScenarioAIntegrationError("image_digest_invalid")
    return digest


def checked_file(path: Path, expected: str | None = None) -> str:
    if not path.is_file():
        raise ScenarioAIntegrationError(f"artifact_missing:{path}")
    observed = sha256_file(path)
    if expected and observed.lower() != expected.lower():
        raise ScenarioAIntegrationError(f"artifact_digest_mismatch:{path}")
    return observed


def _require(value: bool, code: str) -> None:
    if not value:
        raise ScenarioAIntegrationError(code)


def validate_m1_package(config: IntegrationConfig) -> M1Package:
    expected = config.m1
    root = expected.lifecycle_run_root.resolve()
    release_path = root / "validation" / "release-submission.json"
    lifecycle_path = root / "lifecycle_run.json"
    readiness_path = root / "readiness.json"
    serving_path = root / "serving" / "validation.json"
    monitoring_path = root / "monitoring" / "validation.json"
    release = read_json(release_path)
    lifecycle = read_json(lifecycle_path)
    readiness = read_json(readiness_path)
    serving = read_json(serving_path)
    monitoring = read_json(monitoring_path)

    _require(
        release.get("schema_version") == "evm.lifecycle_release_submission.v1",
        "m1_release_schema_invalid",
    )
    _require(release.get("run_id") == expected.lifecycle_run_id, "m1_release_run_mismatch")
    _require(release.get("source_commit") == expected.source_revision, "m1_release_source_mismatch")
    _require(release.get("candidate_id") == expected.candidate_id, "m1_release_candidate_mismatch")
    _require(
        release.get("dataset_version") == expected.dataset_version, "m1_release_dataset_mismatch"
    )
    _require(release.get("model_digest") == expected.model_sha256, "m1_release_model_mismatch")
    _require(
        release.get("container_image_digest") == expected.image_digest, "m1_release_image_mismatch"
    )

    evidence = release.get("evidence") or {}
    required_evidence = {"readiness", "model_matrix", "ct_evaluation", "model_artifact"}
    _require(set(evidence) == required_evidence, "m1_release_evidence_cardinality")
    artifact_paths: dict[str, str] = {
        "release_submission": str(release_path),
        "lifecycle_run": str(lifecycle_path),
        "serving_validation": str(serving_path),
        "monitoring_validation": str(monitoring_path),
    }
    artifact_sha256: dict[str, str] = {}
    for name, reference in evidence.items():
        path = Path(str(reference.get("uri") or ""))
        digest = str(reference.get("sha256") or "")
        checked_file(path, digest)
        artifact_paths[name] = str(path)
        artifact_sha256[name] = digest

    model_path = Path(str(release.get("model_artifact_uri") or ""))
    checked_file(model_path, expected.model_sha256)
    _require(model_path.resolve().is_relative_to(root), "m1_model_outside_lifecycle_root")
    _require(
        Path(artifact_paths["readiness"]).resolve() == readiness_path.resolve(),
        "m1_readiness_reference_mismatch",
    )
    _require(
        Path(artifact_paths["ct_evaluation"]).resolve() == expected.ct_report_path.resolve(),
        "m1_ct_reference_mismatch",
    )

    stages = lifecycle.get("stages") or []
    _require(lifecycle.get("run_id") == expected.lifecycle_run_id, "m1_lifecycle_run_mismatch")
    _require(
        lifecycle.get("source_commit") == expected.source_revision, "m1_lifecycle_source_mismatch"
    )
    _require(len(stages) == 10, "m1_lifecycle_stage_cardinality")
    _require(all(item.get("state") == "completed" for item in stages), "m1_lifecycle_not_complete")
    required_stages = {
        "profile_snapshot",
        "data_pipeline",
        "model_training",
        "model_evaluation",
        "artifact_readiness",
        "ci_ct_gate",
        "approval",
        "deployment",
        "serving_validation",
        "monitoring",
    }
    _require(
        {str(item.get("stage_id")) for item in stages} == required_stages,
        "m1_lifecycle_stage_identity_mismatch",
    )

    _require(
        readiness.get("decision") == "ready" and readiness.get("status") == "pass",
        "m1_readiness_not_ready",
    )
    _require(
        readiness.get("candidate_id") == expected.candidate_id, "m1_readiness_candidate_mismatch"
    )
    _require(
        readiness.get("dataset_version") == expected.dataset_version,
        "m1_readiness_dataset_mismatch",
    )
    _require(len(readiness.get("checks") or []) == 13, "m1_readiness_check_cardinality")
    _require(
        all(item.get("status") == "pass" for item in readiness.get("checks") or []),
        "m1_readiness_check_failed",
    )

    ct = read_json(expected.ct_report_path)
    _require(ct.get("status") == "pass" and ct.get("decision") == "pass", "m1_ct_not_passed")
    _require(ct.get("candidate_id") == expected.candidate_id, "m1_ct_candidate_mismatch")
    _require(ct.get("model_sha256") == expected.model_sha256, "m1_ct_model_mismatch")
    _require(ct.get("device") == "cuda", "m1_ct_not_cuda")
    _require(int(ct.get("ct_record_count") or 0) > 0, "m1_ct_records_missing")
    ct_checks = ct.get("checks") or {}
    _require(
        isinstance(ct_checks, dict)
        and bool(ct_checks)
        and all(value == "pass" for value in ct_checks.values()),
        "m1_ct_check_failed",
    )
    _require(ct.get("overlap_count") == 0 and ct.get("mutated") is False, "m1_ct_isolation_failed")

    candidate_path = model_path.parent / "candidate_summary.json"
    split_path = model_path.parent / "split_manifest.json"
    candidate = read_json(candidate_path)
    split = read_json(split_path)
    _require(candidate.get("status") == "pass", "m1_candidate_not_pass")
    _require(
        candidate.get("candidate_id") == expected.candidate_id, "m1_candidate_identity_mismatch"
    )
    _require(candidate.get("model_sha256") == expected.model_sha256, "m1_candidate_model_mismatch")
    _require(not candidate.get("promotion_blockers"), "m1_candidate_has_blockers")
    _require(split.get("dataset_version") == expected.dataset_version, "m1_split_dataset_mismatch")
    checked_file(candidate_path)
    checked_file(split_path)
    artifact_paths.update(candidate_summary=str(candidate_path), split_manifest=str(split_path))

    for name, payload in (("serving", serving), ("monitoring", monitoring)):
        _require(payload.get("status") == "pass", f"m1_{name}_not_passed")
    _require(
        (serving.get("ready") or {}).get("candidate_id") == expected.candidate_id,
        "m1_serving_candidate_mismatch",
    )
    _require(
        (serving.get("ready") or {}).get("model_sha256") == expected.model_sha256,
        "m1_serving_model_mismatch",
    )
    _require((serving.get("ready") or {}).get("device") == "cuda", "m1_serving_not_cuda")

    for name, path_text in artifact_paths.items():
        artifact_sha256.setdefault(name, checked_file(Path(path_text)))
    submission_digest = str(release.get("submission_digest") or "")
    release_material = {key: value for key, value in release.items() if key != "submission_digest"}
    _require(
        payload_digest(release_material) == submission_digest,
        "m1_release_submission_digest_mismatch",
    )

    return M1Package(
        identity=ModelIdentity(
            candidate_id=expected.candidate_id,
            dataset_version=expected.dataset_version,
            model_sha256=expected.model_sha256,
            image_digest=expected.image_digest,
            model_path=str(model_path),
        ),
        lifecycle_run_id=expected.lifecycle_run_id,
        lifecycle_series_id=str(lifecycle.get("lifecycle_series_id") or ""),
        attempt_id=str(lifecycle.get("attempt_id") or ""),
        correlation_id=str(lifecycle.get("correlation_id") or ""),
        source_revision=expected.source_revision,
        mlflow_run_id=str(release.get("mlflow_run_id") or ""),
        ct_evaluation_id=str(release.get("ct_evaluation_id") or ""),
        artifact_paths=artifact_paths,
        artifact_sha256=artifact_sha256,
    )


def _run(args: list[str], *, timeout: int = 60) -> str:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.stdout.strip()


def kubectl_json(*args: str) -> dict[str, Any]:
    output = _run(["kubectl", *args], timeout=60)
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ScenarioAIntegrationError("kubectl_json_object_required")
    return payload


def active_pod(items: list[dict[str, Any]]) -> dict[str, Any]:
    active = active_pods(items)
    if len(active) != 1:
        raise ScenarioAIntegrationError(f"active_pod_cardinality:{len(active)}")
    return active[0]


def active_pods(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if not pod_is_historical(item) and not (item.get("metadata") or {}).get("deletionTimestamp")
    ]


def container_identity(deployment: dict[str, Any], container_name: str) -> ModelIdentity:
    containers = (((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}).get(
        "containers"
    ) or []
    selected = [item for item in containers if item.get("name") == container_name]
    if len(selected) != 1:
        raise ScenarioAIntegrationError(f"serving_container_cardinality:{len(selected)}")
    container = selected[0]
    env_items = container.get("env") or []
    env = {str(item.get("name")): str(item.get("value") or "") for item in env_items}
    required = [
        "EVM_MODEL_CANDIDATE_ID",
        "EVM_DATASET_VERSION",
        "EVM_MODEL_SHA256",
        "EVM_MODEL_PATH",
    ]
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise ScenarioAIntegrationError(f"serving_env_missing:{','.join(missing)}")
    return ModelIdentity(
        candidate_id=env["EVM_MODEL_CANDIDATE_ID"],
        dataset_version=env["EVM_DATASET_VERSION"],
        model_sha256=env["EVM_MODEL_SHA256"],
        image_digest=str(container.get("image") or ""),
        model_path=env["EVM_MODEL_PATH"],
    )


def pod_container_identity(pod: dict[str, Any], container_name: str) -> ModelIdentity:
    containers = ((pod.get("spec") or {}).get("containers") or [])
    selected = [item for item in containers if item.get("name") == container_name]
    if len(selected) != 1:
        raise ScenarioAIntegrationError(f"serving_container_cardinality:{len(selected)}")
    container = selected[0]
    env = {
        str(item.get("name")): str(item.get("value") or "")
        for item in container.get("env") or []
    }
    required = [
        "EVM_MODEL_CANDIDATE_ID",
        "EVM_DATASET_VERSION",
        "EVM_MODEL_SHA256",
        "EVM_MODEL_PATH",
    ]
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise ScenarioAIntegrationError(f"serving_env_missing:{','.join(missing)}")
    return ModelIdentity(
        candidate_id=env["EVM_MODEL_CANDIDATE_ID"],
        dataset_version=env["EVM_DATASET_VERSION"],
        model_sha256=env["EVM_MODEL_SHA256"],
        image_digest=str(container.get("image") or ""),
        model_path=env["EVM_MODEL_PATH"],
    )


def _prometheus_target(config: ScenarioAConfig, http: HttpAdapter) -> dict[str, Any]:
    payload = http.get_json(config.http.prometheus_targets_url)
    from evm.operations.runtime_adapters import PrometheusTargetSelector

    return select_prometheus_target(
        payload,
        PrometheusTargetSelector(
            job=config.http.prometheus_job,
            instance=config.http.prometheus_instance,
        ),
    )


def capture_target(
    config: ScenarioAConfig, expected: IntegrationM0 | ModelIdentity
) -> TargetSnapshot:
    kube = KubernetesAdapter()
    http = HttpAdapter()
    deployment = kube.get_named(
        kind="deployment",
        namespace=config.target.deployment_namespace,
        name=config.target.deployment_name,
    )
    pods = kube.list_by_label(
        kind="pod",
        namespace=config.target.deployment_namespace,
        label=config.target.pod_label,
    )
    pod = active_pod(pods)
    identity = container_identity(deployment, "serving")
    expected_identity = (
        ModelIdentity(
            candidate_id=expected.candidate_id,
            dataset_version=expected.dataset_version,
            model_sha256=expected.model_sha256,
            image_digest=expected.image_digest,
            model_path=identity.model_path,
        )
        if isinstance(expected, IntegrationM0)
        else expected
    )
    _require(identity.candidate_id == expected_identity.candidate_id, "target_candidate_mismatch")
    _require(
        identity.dataset_version == expected_identity.dataset_version, "target_dataset_mismatch"
    )
    _require(identity.model_sha256 == expected_identity.model_sha256, "target_model_mismatch")
    _require(identity.image_digest == expected_identity.image_digest, "target_image_mismatch")
    ready = http.get_json(config.http.readiness_url)
    _require(
        ready.get("candidate_id") == expected_identity.candidate_id,
        "target_ready_candidate_mismatch",
    )
    _require(
        ready.get("model_sha256") == expected_identity.model_sha256, "target_ready_model_mismatch"
    )
    _require(
        ready.get("dataset_version") == expected_identity.dataset_version,
        "target_ready_dataset_mismatch",
    )
    _require(
        ready.get("device") == "cuda" and ready.get("cuda_available") is True,
        "target_ready_not_cuda",
    )
    prediction = post_inference(config)
    _require(validate_inference(config, prediction).passed, "target_inference_mismatch")
    prometheus = _prometheus_target(config, http)
    _require(
        prometheus.get("health") == "up" and not prometheus.get("lastError"),
        "target_prometheus_not_up",
    )
    metadata = deployment.get("metadata") or {}
    pod_metadata = pod.get("metadata") or {}
    return TargetSnapshot(
        captured_at=utc_now(),
        deployment_uid=str(metadata.get("uid") or ""),
        deployment_resource_version=str(metadata.get("resourceVersion") or ""),
        deployment_generation=int(metadata.get("generation") or 0),
        pod_name=str(pod_metadata.get("name") or ""),
        pod_uid=str(pod_metadata.get("uid") or ""),
        identity=identity,
        deployment=deployment,
        pod=pod,
        readiness=ready,
        prediction=prediction,
        prometheus=prometheus,
    )


def build_deployment_patch(
    snapshot: TargetSnapshot,
    target: ModelIdentity,
    *,
    transaction_id: str,
    action: Literal["apply_m1", "rollback_m0"],
) -> dict[str, Any]:
    return {
        "metadata": {"resourceVersion": snapshot.deployment_resource_version},
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "evm.openai.local/lifecycle-guard-a": (
                            f"{transaction_id}:{action}:{target.model_sha256[:12]}"
                        )
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "serving",
                            "image": target.image_digest,
                            "env": [
                                {"name": "EVM_MODEL_PATH", "value": target.model_path},
                                {"name": "EVM_MODEL_SHA256", "value": target.model_sha256},
                                {"name": "EVM_MODEL_CANDIDATE_ID", "value": target.candidate_id},
                                {"name": "EVM_DATASET_VERSION", "value": target.dataset_version},
                            ],
                        }
                    ]
                },
            }
        },
    }


def build_recreate_reconcile_patch(
    *,
    resource_version: str,
    transaction_id: str,
    target: ModelIdentity,
    nonce: str,
) -> dict[str, Any]:
    return {
        "metadata": {"resourceVersion": resource_version},
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "evm.openai.local/lifecycle-guard-a-reconcile": (
                            f"{transaction_id}:rollback_m0:{target.model_sha256[:12]}:{nonce}"
                        )
                    }
                }
            }
        },
    }


def patch_deployment(
    config: IntegrationConfig,
    snapshot: TargetSnapshot,
    target: ModelIdentity,
    *,
    transaction_id: str,
    action: Literal["apply_m1", "rollback_m0"],
) -> dict[str, Any]:
    current = kubectl_json(
        "get",
        "deployment",
        config.target.deployment,
        "-n",
        config.target.namespace,
        "-o",
        "json",
    )
    metadata = current.get("metadata") or {}
    _require(str(metadata.get("uid") or "") == snapshot.deployment_uid, "deployment_uid_changed")
    _require(
        str(metadata.get("resourceVersion") or "") == snapshot.deployment_resource_version,
        "deployment_resource_version_changed",
    )
    _require(
        container_identity(current, config.target.container) == snapshot.identity,
        "deployment_identity_changed",
    )
    patch = build_deployment_patch(
        snapshot,
        target,
        transaction_id=transaction_id,
        action=action,
    )
    output = _run(
        [
            "kubectl",
            "patch",
            "deployment",
            config.target.deployment,
            "-n",
            config.target.namespace,
            "--type=strategic",
            "-p",
            json.dumps(patch, separators=(",", ":")),
            "-o",
            "json",
        ],
        timeout=60,
    )
    payload = json.loads(output)
    _require(
        str((payload.get("metadata") or {}).get("uid") or "") == snapshot.deployment_uid,
        "patched_deployment_uid_changed",
    )
    return payload


def ensure_recreate_rollout_started(
    config: IntegrationConfig,
    *,
    deployment_uid: str,
    target: ModelIdentity,
    transaction_id: str,
    grace_seconds: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + grace_seconds
    last_active_count = 0
    while time.monotonic() < deadline:
        deployment = kubectl_json(
            "get",
            "deployment",
            config.target.deployment,
            "-n",
            config.target.namespace,
            "-o",
            "json",
        )
        metadata = deployment.get("metadata") or {}
        _require(str(metadata.get("uid") or "") == deployment_uid, "deployment_uid_changed")
        _require(
            container_identity(deployment, config.target.container) == target,
            "deployment_identity_changed",
        )
        pods_payload = kubectl_json(
            "get",
            "pods",
            "-n",
            config.target.namespace,
            "-l",
            config.target.pod_label,
            "-o",
            "json",
        )
        active = active_pods(pods_payload.get("items") or [])
        last_active_count = len(active)
        _require(last_active_count <= 1, f"active_pod_cardinality:{last_active_count}")
        if last_active_count == 1 and pod_container_identity(active[0], config.target.container) == target:
            return {
                "decision": "not_required",
                "active_pod_uid": str((active[0].get("metadata") or {}).get("uid") or ""),
            }
        time.sleep(min(config.execution.sample_cadence_seconds, 2.0))

    deployment = kubectl_json(
        "get",
        "deployment",
        config.target.deployment,
        "-n",
        config.target.namespace,
        "-o",
        "json",
    )
    metadata = deployment.get("metadata") or {}
    _require(str(metadata.get("uid") or "") == deployment_uid, "deployment_uid_changed")
    _require(
        container_identity(deployment, config.target.container) == target,
        "deployment_identity_changed",
    )
    _require(
        str(((deployment.get("spec") or {}).get("strategy") or {}).get("type") or "") == "Recreate",
        "reconcile_requires_recreate_strategy",
    )
    _require(
        int((deployment.get("spec") or {}).get("replicas") or 0) == 1,
        "reconcile_replica_intent_changed",
    )
    patch = build_recreate_reconcile_patch(
        resource_version=str(metadata.get("resourceVersion") or ""),
        transaction_id=transaction_id,
        target=target,
        nonce=uuid.uuid4().hex[:12],
    )
    output = _run(
        [
            "kubectl",
            "patch",
            "deployment",
            config.target.deployment,
            "-n",
            config.target.namespace,
            "--type=strategic",
            "-p",
            json.dumps(patch, separators=(",", ":")),
            "-o",
            "json",
        ],
        timeout=60,
    )
    patched = json.loads(output)
    _require(
        str((patched.get("metadata") or {}).get("uid") or "") == deployment_uid,
        "reconciled_deployment_uid_changed",
    )
    _require(
        container_identity(patched, config.target.container) == target,
        "reconciled_deployment_identity_changed",
    )
    return {
        "decision": "requested",
        "reason": "recreate_rollout_had_no_active_pod_after_grace",
        "grace_seconds": grace_seconds,
        "active_pod_count_before": last_active_count,
        "deployment_generation": int((patched.get("metadata") or {}).get("generation") or 0),
        "deployment_resource_version": str(
            (patched.get("metadata") or {}).get("resourceVersion") or ""
        ),
        "annotation": (((patched.get("spec") or {}).get("template") or {}).get("metadata") or {})
        .get("annotations", {})
        .get("evm.openai.local/lifecycle-guard-a-reconcile"),
    }


def build_model_config(
    base: ScenarioAConfig,
    package: M1Package,
    config: IntegrationConfig,
    evidence_root: Path,
) -> ScenarioAConfig:
    paths = package.artifact_paths
    return ScenarioAConfig(
        target=ScenarioATargetConfig(**base.target.model_dump()),
        http=ScenarioAHttpConfig(**base.http.model_dump()),
        runtime=ScenarioARuntimeConfig(**base.runtime.model_dump()),
        execution=ScenarioAExecutionConfig(
            evidence_root=evidence_root,
            sample_cadence_seconds=config.execution.sample_cadence_seconds,
            signal_precedence=base.execution.signal_precedence,
            detection_budget_seconds=config.execution.detection_budget_seconds,
            recovery_budget_seconds=config.execution.recovery_budget_seconds,
            cooldown_seconds=0,
            required_independent_runs=1,
        ),
        identity=ScenarioAIdentityContract(
            service="evm-b0-production",
            candidate_id=package.identity.candidate_id,
            dataset_version=package.identity.dataset_version,
            model_sha256=package.identity.model_sha256,
            image_digest=normalized_image_digest(package.identity.image_digest),
            device="cuda",
        ),
        artifacts=ScenarioAArtifactConfig(
            candidate_summary_path=Path(paths["candidate_summary"]),
            model_path=Path(paths["model_artifact"]),
            model_sha256=package.identity.model_sha256,
            split_manifest_path=Path(paths["split_manifest"]),
            split_manifest_sha256=package.artifact_sha256["split_manifest"],
            readiness_manifest_path=Path(paths["readiness"]),
            readiness_manifest_sha256=package.artifact_sha256["readiness"],
            ct_report_path=config.m1.ct_report_path,
        ),
        inference=ScenarioAInferenceConfig(
            url=base.inference.url,
            image_uri=config.m1.sample_image_uri,
            expected_prediction=config.m1.expected_prediction,
        ),
    )


def wait_for_exact_health(
    scenario_config: ScenarioAConfig,
    expected: ModelIdentity,
    *,
    started_at: datetime,
    timeout_seconds: float,
) -> dict[str, Any]:
    kube = KubernetesAdapter()
    http = HttpAdapter()
    deadline = time.monotonic() + timeout_seconds
    samples: list[dict[str, Any]] = []
    distinct_scrapes: list[str] = []
    while time.monotonic() < deadline:
        sample: dict[str, Any] = {"observed_at": utc_now(), "ready": False}
        try:
            deployment = kube.get_named(
                kind="deployment",
                namespace=scenario_config.target.deployment_namespace,
                name=scenario_config.target.deployment_name,
            )
            pods = kube.list_by_label(
                kind="pod",
                namespace=scenario_config.target.deployment_namespace,
                label=scenario_config.target.pod_label,
            )
            pod = active_pod(pods)
            observation = ScenarioACollector(kube, http).collect(
                discover_scenario_a_selectors(scenario_config, kube)
            )
            health = evaluate_scenario_a_health(observation, scenario_config.identity)
            identity = container_identity(deployment, "serving")
            inference = post_inference(scenario_config)
            inference_ok = validate_inference(scenario_config, inference).passed
            target = _prometheus_target(scenario_config, http)
            scrape = str(target.get("lastScrape") or target.get("lastScrapeTime") or "")
            scrape_time = datetime.fromisoformat(scrape.replace("Z", "+00:00")) if scrape else None
            prometheus_ok = (
                target.get("health") == "up"
                and not target.get("lastError")
                and scrape_time is not None
                and scrape_time > started_at
            )
            if prometheus_ok and scrape not in distinct_scrapes:
                distinct_scrapes.append(scrape)
            sample.update(
                ready=(
                    health.decision == "passed"
                    and identity == expected
                    and inference_ok
                    and len(distinct_scrapes) >= 2
                ),
                pod_uid=str((pod.get("metadata") or {}).get("uid") or ""),
                health=health.decision,
                inference=inference,
                prometheus_health=target.get("health"),
                prometheus_scrape=scrape,
                distinct_up_scrapes=len(distinct_scrapes),
            )
            samples.append(sample)
            if sample["ready"]:
                return {
                    "decision": "passed",
                    "elapsed_seconds": (datetime.now(timezone.utc) - started_at).total_seconds(),
                    "samples": samples,
                    "final_pod_uid": sample["pod_uid"],
                    "prometheus_scrapes": distinct_scrapes,
                }
        except (Exception, urllib.error.URLError) as exc:
            sample["error"] = f"{type(exc).__name__}:{exc}"
            samples.append(sample)
        time.sleep(scenario_config.execution.sample_cadence_seconds)
    raise ScenarioAIntegrationError("exact_health_convergence_timeout")


def write_pointer(
    path: Path,
    *,
    expected_digest: str | None,
    identity: ModelIdentity,
    deployment_uid: str,
    transaction_id: str,
    state: Literal["m1_committed", "m0_restored"],
) -> dict[str, Any]:
    current = read_json(path) if path.is_file() else None
    current_digest = str((current or {}).get("identity", {}).get("model_sha256") or "") or None
    if current_digest != expected_digest:
        raise ScenarioAIntegrationError(
            f"stable_pointer_cas_failed:expected={expected_digest},actual={current_digest}"
        )
    payload = {
        "schema_version": "evm.lifecycle_guard_a_stable_pointer.v1",
        "revision": int((current or {}).get("revision") or 0) + 1,
        "state": state,
        "identity": identity.model_dump(mode="json"),
        "deployment_uid": deployment_uid,
        "transaction_id": transaction_id,
        "committed_at": utc_now(),
    }
    atomic_write_json(path, payload)
    return payload


def issue_approval(
    root: Path,
    *,
    run_id: str,
    target: TargetRef,
    action: str,
    source_revision: str,
    approver: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    store = ApprovalStore(root)
    binding = store.issue(
        run_id=run_id,
        target=target,
        action=action,
        source_revision=source_revision,
        approver=approver,
        ttl_seconds=ttl_seconds,
    )
    return {
        "approval_id": binding.approval_id,
        "action": action,
        "action_digest": binding.action_digest,
        "source_revision": source_revision,
        "target_uid": target.uid,
        "expires_at": binding.expires_at.isoformat(),
        "single_use": True,
        "consumed": False,
        "replay_blocked": None,
    }


def consume_approval(
    root: Path,
    approval: dict[str, Any],
    *,
    run_id: str,
    target: TargetRef,
    source_revision: str,
) -> dict[str, Any]:
    store = ApprovalStore(root)
    action = str(approval["action"])
    approval_id = str(approval["approval_id"])
    store.consume(
        approval_id,
        run_id=run_id,
        target=target,
        action=action,
        source_revision=source_revision,
    )
    replay_blocked = False
    try:
        store.consume(
            approval_id,
            run_id=run_id,
            target=target,
            action=action,
            source_revision=source_revision,
        )
    except ApprovalRejected:
        replay_blocked = True
    _require(replay_blocked, "approval_replay_not_blocked")
    receipt = read_json(store.root / f"{approval_id}.consumed.json")
    return {
        **approval,
        "consumed": True,
        "consumed_at": receipt["consumed_at"],
        "replay_blocked": True,
    }


def prepare_recovery(
    config: ScenarioAConfig,
    *,
    project_root: Path,
    run_id: str,
    approver: str,
) -> dict[str, Any]:
    baseline = run_read_only_baseline(
        config=config,
        project_root=project_root,
        run_id=run_id,
    )
    _require(
        baseline.decision == "passed", f"recovery_baseline_blocked:{','.join(baseline.blockers)}"
    )
    run_root = baseline.run_root
    kube = KubernetesAdapter()
    before = kube.get_named(
        kind="daemonset",
        namespace=config.target.device_plugin_namespace,
        name=config.target.device_plugin_name,
    )
    atomic_write_json(run_root / "device-plugin-before.json", before)
    plan = plan_device_plugin_reconciliation(before, discover_wsl_driver_paths())
    _require(
        plan.decision in {"no_change", "change_required"}, "device_plugin_reconciliation_blocked"
    )
    _require(plan.mutation_performed is False, "device_plugin_reconciliation_mutated")
    atomic_write_json(
        run_root / "device-plugin-reconciliation-plan.json", plan.model_dump(mode="json")
    )
    after = kube.get_named(
        kind="daemonset",
        namespace=config.target.device_plugin_namespace,
        name=config.target.device_plugin_name,
    )
    atomic_write_json(run_root / "device-plugin-after.json", after)
    state_store = ScenarioStateStore(config.execution.evidence_root / "A")
    state = state_store.load(run_id)
    state_store.transition(
        run_id,
        next_state="non_disruptive_validated",
        expected_revision=state.revision,
        reason=f"device_plugin_reconciliation_{plan.decision}",
    )
    preflight = prepare_scenario_a_preflight(
        config=config,
        project_root=project_root,
        run_id=run_id,
    )
    _require(
        preflight.decision == "passed", f"recovery_preflight_blocked:{','.join(preflight.blockers)}"
    )
    approval = issue_scenario_a_approval(
        config=config,
        run_id=run_id,
        approver=approver,
        ttl_seconds=1800,
    )
    return {
        "baseline": baseline.model_dump(mode="json"),
        "reconciliation": plan.model_dump(mode="json"),
        "preflight": preflight.model_dump(mode="json"),
        "approval_id": approval.binding.approval_id,
    }


def evidence_index(root: Path) -> dict[str, Any]:
    files = sorted(
        path for path in root.rglob("*") if path.is_file() and path.name != "evidence-index.json"
    )
    artifacts = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in files
    ]
    payload = {
        "schema_version": "evm.lifecycle_guard_a_evidence_index.v1",
        "generated_at": utc_now(),
        "root": str(root),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    atomic_write_json(root / "evidence-index.json", payload)
    return payload


def recovery_storage_plan(run_root: Path, transaction_id: str) -> dict[str, Any]:
    recovery_root = run_root / "r"
    recovery_id = f"a8-{hashlib.sha256(transaction_id.encode('utf-8')).hexdigest()[:12]}"
    atomic_temp_probe = (
        recovery_root / "A" / recovery_id / f".state.json.{('f' * 32)}.tmp"
    ).resolve()
    observed_length = len(str(atomic_temp_probe))
    return {
        "decision": "passed" if observed_length <= WINDOWS_ATOMIC_PATH_BUDGET else "blocked",
        "evidence_root": str(recovery_root.resolve()),
        "run_id": recovery_id,
        "longest_atomic_path_probe": str(atomic_temp_probe),
        "observed_path_length": observed_length,
        "path_budget": WINDOWS_ATOMIC_PATH_BUDGET,
    }


def run_integrated_scenario_a(
    *,
    config: IntegrationConfig,
    project_root: Path,
    approver: str,
    maintenance_approved: bool,
) -> dict[str, Any]:
    if not maintenance_approved:
        raise ScenarioAIntegrationError("maintenance_approval_required")
    source_revision = _run(["git", "rev-parse", "HEAD"])
    dirty = bool(_run(["git", "status", "--porcelain", "--", "."], timeout=30))
    _require(not dirty, "project_worktree_dirty")
    package = validate_m1_package(config)
    _require(package.identity.model_sha256 != config.m0.model_sha256, "m1_reuses_m0_model")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    transaction_id = f"scenario-a-lifecycle-{stamp}-{source_revision[:8]}-{uuid.uuid4().hex[:8]}"
    run_root = config.execution.evidence_root / transaction_id
    run_root.mkdir(parents=True, exist_ok=False)
    base = load_scenario_a_config((project_root / config.m0.scenario_config_path).resolve())
    source = collect_runtime_source(project_root=project_root, config=base)
    _require(source.revision_converged, "runtime_revision_not_converged")
    _require(
        source.source.commit == source_revision and not source.source.dirty,
        "runtime_source_mismatch",
    )
    m0 = capture_target(base, config.m0)
    target = TargetRef(
        namespace=config.target.namespace,
        name=config.target.deployment,
        uid=m0.deployment_uid,
    )
    contract = {
        "schema_version": "evm.lifecycle_guard_a_contract.v1",
        "transaction_id": transaction_id,
        "source_revision": source_revision,
        "target": target.model_dump(mode="json"),
        "m0": m0.identity.model_dump(mode="json"),
        "m0_deployment_resource_version": m0.deployment_resource_version,
        "m0_pod_uid": m0.pod_uid,
        "m1": package.model_dump(mode="json"),
        "budgets": {
            "detection_seconds": config.execution.detection_budget_seconds,
            "recovery_seconds": config.execution.recovery_budget_seconds,
        },
        "excluded_mutations": [
            "device_plugin",
            "data",
            "registry",
            "cluster_wide",
            "unrelated_workloads",
        ],
    }
    contract_digest = payload_digest(contract)
    atomic_write_json(run_root / "contract.json", {**contract, "contract_digest": contract_digest})
    atomic_write_json(run_root / "m0-snapshot.json", m0.model_dump(mode="json"))
    atomic_write_json(run_root / "m1-package.json", package.model_dump(mode="json"))
    recovery_storage = recovery_storage_plan(run_root, transaction_id)
    atomic_write_json(run_root / "recovery-storage-preflight.json", recovery_storage)
    _require(recovery_storage["decision"] == "passed", "recovery_storage_path_budget_exceeded")
    pointer_path = config.execution.evidence_root / "_state" / "stable-pointer.json"
    existing_pointer = read_json(pointer_path) if pointer_path.is_file() else None
    expected_pointer = (
        str((existing_pointer or {}).get("identity", {}).get("model_sha256") or "") or None
    )
    if expected_pointer is not None:
        _require(expected_pointer == m0.identity.model_sha256, "stable_pointer_not_m0")

    apply_action = f"apply_m1:{contract_digest}"
    rollback_action = f"rollback_m0:{contract_digest}"
    approvals = {
        "apply": issue_approval(
            run_root / "transaction",
            run_id=transaction_id,
            target=target,
            action=apply_action,
            source_revision=source_revision,
            approver=approver,
            ttl_seconds=config.execution.approval_ttl_seconds,
        ),
        "rollback": issue_approval(
            run_root / "transaction",
            run_id=transaction_id,
            target=target,
            action=rollback_action,
            source_revision=source_revision,
            approver=approver,
            ttl_seconds=config.execution.approval_ttl_seconds,
        ),
    }
    atomic_write_json(run_root / "transaction-approvals.json", approvals)

    m1_identity = package.identity.model_copy(
        update={"model_path": model_mount_path(package.identity.model_path)}
    )
    m1_config = build_model_config(
        base,
        package,
        config,
        Path(recovery_storage["evidence_root"]),
    )
    result: dict[str, Any] = {
        "schema_version": "evm.lifecycle_guard_a_result.v1",
        "transaction_id": transaction_id,
        "status": "failed",
        "source_revision": source_revision,
        "contract_digest": contract_digest,
        "m0": m0.identity.model_dump(mode="json"),
        "m1": m1_identity.model_dump(mode="json"),
        "approvals": approvals,
        "phases": [],
        "claim_boundary": "controlled local single-node maintenance drill; not HA, zero downtime, production traffic or SLA evidence",
    }
    m1_active = False
    m1_committed = False
    rollback_consumed = False
    try:
        approvals["apply"] = consume_approval(
            run_root / "transaction",
            approvals["apply"],
            run_id=transaction_id,
            target=target,
            source_revision=source_revision,
        )
        atomic_write_json(run_root / "transaction-approvals.json", approvals)
        apply_started = datetime.now(timezone.utc)
        patched = patch_deployment(
            config,
            m0,
            m1_identity,
            transaction_id=transaction_id,
            action="apply_m1",
        )
        m1_active = True
        atomic_write_json(run_root / "m1-patched-deployment.json", patched)
        apply_health = wait_for_exact_health(
            m1_config,
            m1_identity,
            started_at=apply_started,
            timeout_seconds=min(
                config.execution.recovery_budget_seconds,
                config.execution.prometheus_convergence_seconds,
            ),
        )
        atomic_write_json(run_root / "m1-apply-health.json", apply_health)
        pointer_m1 = write_pointer(
            pointer_path,
            expected_digest=expected_pointer,
            identity=m1_identity,
            deployment_uid=m0.deployment_uid,
            transaction_id=transaction_id,
            state="m1_committed",
        )
        m1_committed = True
        result["phases"].append(
            {
                "phase": "prepare_approve_apply_verify_commit",
                "status": "passed",
                "elapsed_seconds": apply_health["elapsed_seconds"],
                "new_pod_uid": apply_health["final_pod_uid"],
                "stable_pointer_revision": pointer_m1["revision"],
            }
        )

        recovery_id = str(recovery_storage["run_id"])
        recovery_preflight = prepare_recovery(
            m1_config,
            project_root=project_root,
            run_id=recovery_id,
            approver=approver,
        )
        atomic_write_json(run_root / "m1-recovery-preflight.json", recovery_preflight)
        recovery = run_scenario_a_live(
            config=m1_config,
            project_root=project_root,
            run_id=recovery_id,
        )
        result["phases"].append(
            {
                "phase": "committed_m1_exact_pod_recovery",
                "status": "passed",
                **recovery.model_dump(mode="json"),
            }
        )

        current_m1 = capture_target(m1_config, m1_identity)
        approvals["rollback"] = consume_approval(
            run_root / "transaction",
            approvals["rollback"],
            run_id=transaction_id,
            target=target,
            source_revision=source_revision,
        )
        rollback_consumed = True
        atomic_write_json(run_root / "transaction-approvals.json", approvals)
        rollback_started = datetime.now(timezone.utc)
        rollback_patched = patch_deployment(
            config,
            current_m1,
            m0.identity,
            transaction_id=transaction_id,
            action="rollback_m0",
        )
        atomic_write_json(run_root / "m0-rollback-patched-deployment.json", rollback_patched)
        rollback_reconcile = ensure_recreate_rollout_started(
            config,
            deployment_uid=m0.deployment_uid,
            target=m0.identity,
            transaction_id=transaction_id,
        )
        atomic_write_json(run_root / "m0-rollback-reconcile.json", rollback_reconcile)
        rollback_health = wait_for_exact_health(
            base,
            m0.identity,
            started_at=rollback_started,
            timeout_seconds=min(
                config.execution.recovery_budget_seconds,
                config.execution.prometheus_convergence_seconds,
            ),
        )
        atomic_write_json(run_root / "m0-rollback-health.json", rollback_health)
        final_m0 = capture_target(base, m0.identity)
        _require(final_m0.deployment_uid == m0.deployment_uid, "rollback_deployment_uid_mismatch")
        pointer_m0 = write_pointer(
            pointer_path,
            expected_digest=m1_identity.model_sha256,
            identity=m0.identity,
            deployment_uid=m0.deployment_uid,
            transaction_id=transaction_id,
            state="m0_restored",
        )
        m1_active = False
        result["phases"].append(
            {
                "phase": "separate_m0_rollback",
                "status": "passed",
                "elapsed_seconds": rollback_health["elapsed_seconds"],
                "final_pod_uid": rollback_health["final_pod_uid"],
                "stable_pointer_revision": pointer_m0["revision"],
            }
        )
        result.update(
            status="passed",
            completed_at=utc_now(),
            final_identity=final_m0.identity.model_dump(mode="json"),
            production_mutations={
                "deployment_model_rollouts": 2,
                "exact_m1_pod_restarts": 1,
                "device_plugin": 0,
                "data": 0,
                "registry": 0,
                "cluster_wide": 0,
            },
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}:{exc}"
        result["failed_at"] = utc_now()
        if m1_active:
            try:
                if rollback_consumed:
                    raise ScenarioAIntegrationError("emergency_rollback_requires_new_approval")
                approvals["rollback"] = consume_approval(
                    run_root / "transaction",
                    approvals["rollback"],
                    run_id=transaction_id,
                    target=target,
                    source_revision=source_revision,
                )
                rollback_consumed = True
                atomic_write_json(run_root / "transaction-approvals.json", approvals)
                current = capture_target(m1_config, m1_identity)
                emergency_started = datetime.now(timezone.utc)
                patch_deployment(
                    config,
                    current,
                    m0.identity,
                    transaction_id=transaction_id,
                    action="rollback_m0",
                )
                emergency_reconcile = ensure_recreate_rollout_started(
                    config,
                    deployment_uid=m0.deployment_uid,
                    target=m0.identity,
                    transaction_id=transaction_id,
                )
                atomic_write_json(
                    run_root / "emergency-rollback-reconcile.json", emergency_reconcile
                )
                emergency = wait_for_exact_health(
                    base,
                    m0.identity,
                    started_at=emergency_started,
                    timeout_seconds=min(
                        config.execution.recovery_budget_seconds,
                        config.execution.prometheus_convergence_seconds,
                    ),
                )
                result["emergency_rollback"] = {"status": "passed", **emergency}
                if m1_committed:
                    write_pointer(
                        pointer_path,
                        expected_digest=m1_identity.model_sha256,
                        identity=m0.identity,
                        deployment_uid=m0.deployment_uid,
                        transaction_id=transaction_id,
                        state="m0_restored",
                    )
            except Exception as rollback_exc:
                result["emergency_rollback"] = {
                    "status": "failed",
                    "error": f"{type(rollback_exc).__name__}:{rollback_exc}",
                }
        atomic_write_json(run_root / "result.json", result)
        evidence_index(run_root)
        raise
    atomic_write_json(run_root / "result.json", result)
    index = evidence_index(run_root)
    result["evidence_index_sha256"] = sha256_file(run_root / "evidence-index.json")
    result["evidence_artifact_count"] = index["artifact_count"]
    atomic_write_json(run_root / "result.json", result)
    evidence_index(run_root)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the integrated lifecycle Scenario A drill.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--maintenance-approved", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_integrated_scenario_a(
        config=load_config(args.config.resolve()),
        project_root=args.project_root.resolve(),
        approver=args.approver,
        maintenance_approved=args.maintenance_approved,
    )
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

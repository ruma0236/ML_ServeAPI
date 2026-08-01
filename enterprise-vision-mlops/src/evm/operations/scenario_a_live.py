from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evm.operations.failure_evidence import (
    ApprovalEvidence,
    ArtifactEvidence,
    CheckEvidence,
    ClosureEvidence,
    DecisionEvidence,
    EnvironmentEvidence,
    InjectionEvidence,
    OperationalFailureReport,
    PortfolioEvidence,
    RecoveryEvidence,
    SCHEMA_VERSION,
    SignalEvidence,
    TimingEvidence,
    sha256_file,
)
from evm.operations.failure_scenarios import (
    ALLOWED_TRANSITIONS,
    ApprovalStore,
    LeaseManager,
    ScenarioStateStore,
    TargetRef,
    action_digest,
    atomic_write_json,
)
from evm.operations.metrics import OperationalMetricProjection
from evm.operations.runtime_adapters import (
    HttpAdapter,
    KubernetesAdapter,
    ScenarioACollector,
    pod_is_historical,
    select_prometheus_target,
)
from evm.operations.scenario_a_preflight import (
    ScenarioAPreflight,
    deployment_rollback_payload,
    evaluate_identity_bundle,
    payload_sha256,
    read_json,
)
from evm.operations.scenario_a_runner import (
    ScenarioAConfig,
    collect_runtime_source,
    discover_scenario_a_selectors,
)
from evm.operations.target_health import evaluate_scenario_a_health


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationalSample(StrictModel):
    sequence: int = Field(ge=0)
    observed_at: datetime
    monotonic_ns: int = Field(ge=0)
    signals: dict[str, bool]
    selected_unhealthy_signal: str | None
    active_pod_uids: list[str]
    detail: dict[str, Any]

    @field_validator("observed_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("observed_at must be UTC")
        return value


class LiveRunResult(StrictModel):
    run_id: str
    report_path: Path
    old_pod_uid: str
    new_pod_uid: str
    detection_seconds: float
    recovery_seconds: float
    interruption_seconds: float
    sample_count: int
    approval_id: str


def delete_options(target: TargetRef) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "DeleteOptions",
        "preconditions": {"uid": target.uid},
        "propagationPolicy": "Background",
    }


def delete_exact_pod(target: TargetRef, options_path: Path) -> dict[str, Any]:
    encoded_name = urllib.parse.quote(target.name, safe="")
    encoded_namespace = urllib.parse.quote(target.namespace, safe="")
    uri = f"/api/v1/namespaces/{encoded_namespace}/pods/{encoded_name}"
    completed = subprocess.run(
        ["kubectl", "delete", f"--raw={uri}", f"--filename={options_path}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    observed_uid = str(
        (payload.get("metadata") or {}).get("uid")
        or (payload.get("details") or {}).get("uid")
        or ""
    )
    observed_name = str(
        (payload.get("metadata") or {}).get("name")
        or (payload.get("details") or {}).get("name")
        or ""
    )
    if observed_uid and observed_uid != target.uid:
        raise RuntimeError("delete_response_uid_mismatch")
    if observed_name and observed_name != target.name:
        raise RuntimeError("delete_response_name_mismatch")
    return payload


def _pod_ready(pod: dict[str, Any]) -> bool:
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in ((pod.get("status") or {}).get("conditions") or [])
    )


def select_unhealthy_signal(signals: dict[str, bool], precedence: list[str]) -> str | None:
    return next((signal for signal in precedence if not signals.get(signal, False)), None)


def collect_sample(
    *,
    config: ScenarioAConfig,
    kubernetes: KubernetesAdapter,
    http: HttpAdapter,
    old_pod_uid: str,
    sequence: int,
) -> OperationalSample:
    pods = kubernetes.list_by_label(
        kind="pod",
        namespace=config.target.deployment_namespace,
        label=config.target.pod_label,
    )
    active = [item for item in pods if not pod_is_historical(item)]
    active_uids = [str((item.get("metadata") or {}).get("uid") or "") for item in active]
    old_matches = [item for item in active if str((item.get("metadata") or {}).get("uid")) == old_pod_uid]
    kubernetes_healthy = (
        len(active) == 1
        and len(old_matches) == 1
        and (old_matches[0].get("status") or {}).get("phase") == "Running"
        and _pod_ready(old_matches[0])
    )
    ready: dict[str, Any] = {}
    readiness_healthy = False
    try:
        ready = http.get_json(config.http.readiness_url)
        readiness_healthy = (
            ready.get("status") == "ok"
            and ready.get("model_loaded") is True
            and ready.get("cuda_available") is True
        )
    except (OSError, ValueError, urllib.error.URLError) as exc:
        ready = {"error": type(exc).__name__}
    prometheus: dict[str, Any] = {}
    prometheus_healthy = False
    try:
        payload = http.get_json(config.http.prometheus_targets_url)
        prometheus = select_prometheus_target(
            payload,
            discover_prometheus_selector(config),
        )
        prometheus_healthy = prometheus.get("health") == "up" and not prometheus.get("lastError")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        prometheus = {"error": type(exc).__name__}
    signals = {
        "kubernetes_pod": kubernetes_healthy,
        "readiness": readiness_healthy,
        "prometheus": prometheus_healthy,
    }
    return OperationalSample(
        sequence=sequence,
        observed_at=datetime.now(timezone.utc),
        monotonic_ns=time.monotonic_ns(),
        signals=signals,
        selected_unhealthy_signal=select_unhealthy_signal(
            signals,
            config.execution.signal_precedence,
        ),
        active_pod_uids=active_uids,
        detail={"readiness": ready, "prometheus": prometheus},
    )


def discover_prometheus_selector(config: ScenarioAConfig):
    from evm.operations.runtime_adapters import PrometheusTargetSelector

    return PrometheusTargetSelector(
        job=config.http.prometheus_job,
        instance=config.http.prometheus_instance,
    )


def post_inference(config: ScenarioAConfig) -> dict[str, Any]:
    body = json.dumps({"image_uri": config.inference.image_uri}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - fixed local config
        config.inference.url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def validate_inference(config: ScenarioAConfig, payload: dict[str, Any]) -> CheckEvidence:
    passed = (
        payload.get("candidate_id") == config.identity.candidate_id
        and payload.get("model_sha256") == config.identity.model_sha256
        and payload.get("dataset_version") == config.identity.dataset_version
        and payload.get("device") == "cuda"
        and payload.get("prediction") == config.inference.expected_prediction
    )
    return CheckEvidence(
        check_id="post_cuda_inference",
        passed=passed,
        observed=payload,
        reason_code=None if passed else "post_cuda_inference_failed",
    )


def _artifact(path: Path) -> ArtifactEvidence:
    return ArtifactEvidence(
        uri=str(path.resolve()),
        sha256=sha256_file(path),
        media_type="application/json",
        evidence_role="run_evidence",
    )


def _load_approval_id(run_root: Path) -> str:
    return str(read_json(run_root / "approval-reference.json")["approval_id"])


def verify_live_preflight(
    *,
    config: ScenarioAConfig,
    project_root: Path,
    preflight: ScenarioAPreflight,
    kubernetes: KubernetesAdapter,
    http: HttpAdapter,
) -> tuple[Any, Any, Any, Any]:
    selectors = discover_scenario_a_selectors(config, kubernetes)
    observation = ScenarioACollector(kubernetes, http).collect(selectors)
    health = evaluate_scenario_a_health(observation, config.identity)
    source = collect_runtime_source(project_root=project_root, config=config)
    rollback_digest = payload_sha256(deployment_rollback_payload(observation.deployment))
    identities = evaluate_identity_bundle(
        config,
        observed_image_digest=health.observed_identity["image_digest"],
        rollback_digest=rollback_digest,
    )
    expected_action = action_digest(
        run_id=preflight.run_id,
        action=preflight.action,
        target=preflight.target,
        source_revision=source.source.commit,
    )
    checks = {
        "target": selectors.pod.uid == preflight.target.uid,
        "deployment": selectors.deployment.uid == preflight.deployment_uid,
        "health": health.decision == "passed",
        "source": (
            source.revision_converged
            and not source.source.dirty
            and source.source.commit == preflight.source.commit
        ),
        "rollback": rollback_digest == preflight.rollback_digest,
        "identity": not identities.blockers and identities.identities == preflight.identities,
        "action": expected_action == preflight.action_digest,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"live_preflight_failed:{','.join(failed)}")
    return selectors, observation, health, source


def _write_series_result(config: ScenarioAConfig, result: dict[str, Any]) -> None:
    path = config.execution.evidence_root / "A" / "_series" / "production-b0.json"
    payload = read_json(path) if path.is_file() else {
        "schema_version": "evm.scenario_a_series.v1",
        "required_independent_runs": config.execution.required_independent_runs,
        "cooldown_seconds": config.execution.cooldown_seconds,
        "runs": [],
    }
    if any(item.get("run_id") == result["run_id"] for item in payload["runs"]):
        raise RuntimeError("series_run_id_duplicate")
    payload["runs"].append(result)
    atomic_write_json(path, payload)


def _failure(
    *,
    state_store: ScenarioStateStore,
    run_id: str,
    run_root: Path,
    error: Exception,
) -> None:
    state = state_store.load(run_id)
    if "failed" in ALLOWED_TRANSITIONS[state.state]:
        state_store.transition(
            run_id,
            next_state="failed",
            expected_revision=state.revision,
            reason=f"live_run_failed:{type(error).__name__}",
        )
    atomic_write_json(
        run_root / "live-failure.json",
        {
            "schema_version": "evm.scenario_a_live_failure.v1",
            "run_id": run_id,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "error_type": type(error).__name__,
            "error": str(error),
        },
    )


def run_scenario_a_live(
    *,
    config: ScenarioAConfig,
    project_root: Path,
    run_id: str,
) -> LiveRunResult:
    run_root = config.execution.evidence_root / "A" / run_id
    preflight = ScenarioAPreflight.model_validate_json(
        (run_root / "preflight.json").read_text(encoding="utf-8")
    )
    state_store = ScenarioStateStore(config.execution.evidence_root / "A")
    state = state_store.load(run_id)
    if state.state != "approved":
        raise ValueError(f"live_state_required:approved:actual={state.state}")
    kubernetes = KubernetesAdapter()
    http = HttpAdapter()
    selectors, before_observation, before_health, source = verify_live_preflight(
        config=config,
        project_root=project_root,
        preflight=preflight,
        kubernetes=kubernetes,
        http=http,
    )
    target = preflight.target
    lease_manager = LeaseManager(config.execution.evidence_root / "A")
    lease = lease_manager.acquire(
        run_id=run_id,
        owner="scenario-a-live-runner",
        target=target,
        ttl_seconds=int(config.execution.recovery_budget_seconds + 120),
    )
    approval_id = _load_approval_id(run_root)
    approval_store = ApprovalStore(config.execution.evidence_root / "A")
    samples: list[OperationalSample] = []
    injection_ns: int | None = None
    detection_ns: int | None = None
    recovery_ns: int | None = None
    detection_signal: str | None = None
    new_selectors = None
    final_observation = None
    final_health = None
    inference: dict[str, Any] = {}
    started_at = datetime.now(timezone.utc)
    monotonic_started_ns = time.monotonic_ns()
    try:
        binding = approval_store.consume(
            approval_id,
            run_id=run_id,
            target=target,
            action=preflight.action,
            source_revision=source.source.commit,
        )
        state = state_store.transition(
            run_id,
            next_state="injecting",
            expected_revision=state.revision,
            reason=f"approval_consumed:{approval_id}",
        )
        options_path = run_root / "delete-options.json"
        atomic_write_json(options_path, delete_options(target))
        injection_ns = time.monotonic_ns()
        deleted = delete_exact_pod(target, options_path)
        atomic_write_json(run_root / "deleted-pod-response.json", deleted)

        next_sample_ns = injection_ns
        sequence = 0
        while True:
            now_ns = time.monotonic_ns()
            if now_ns < next_sample_ns:
                time.sleep((next_sample_ns - now_ns) / 1_000_000_000)
            sample = collect_sample(
                config=config,
                kubernetes=kubernetes,
                http=http,
                old_pod_uid=target.uid,
                sequence=sequence,
            )
            samples.append(sample)
            sequence += 1
            next_sample_ns = injection_ns + int(
                sequence * config.execution.sample_cadence_seconds * 1_000_000_000
            )
            if detection_ns is None and sample.selected_unhealthy_signal is not None:
                detection_ns = sample.monotonic_ns
                detection_signal = sample.selected_unhealthy_signal
                state = state_store.transition(
                    run_id,
                    next_state="detected",
                    expected_revision=state.revision,
                    reason=f"detected:{detection_signal}",
                )
                state = state_store.transition(
                    run_id,
                    next_state="recovering",
                    expected_revision=state.revision,
                    reason="deployment_controller_recovery_wait",
                )
            if detection_ns is None and (
                sample.monotonic_ns - injection_ns
            ) / 1_000_000_000 > config.execution.detection_budget_seconds:
                raise TimeoutError("detection_budget_exceeded")
            if detection_ns is not None:
                try:
                    candidate_selectors = discover_scenario_a_selectors(config, kubernetes)
                    candidate_observation = ScenarioACollector(kubernetes, http).collect(
                        candidate_selectors
                    )
                    candidate_health = evaluate_scenario_a_health(
                        candidate_observation,
                        config.identity,
                    )
                    if (
                        candidate_selectors.pod.uid != target.uid
                        and candidate_health.decision == "passed"
                    ):
                        new_selectors = candidate_selectors
                        final_observation = candidate_observation
                        final_health = candidate_health
                        recovery_ns = sample.monotonic_ns
                        break
                except Exception:
                    pass
            if (
                sample.monotonic_ns - injection_ns
            ) / 1_000_000_000 > config.execution.recovery_budget_seconds:
                raise TimeoutError("recovery_budget_exceeded")

        assert injection_ns is not None and detection_ns is not None and recovery_ns is not None
        assert new_selectors is not None and final_observation is not None and final_health is not None
        detection_seconds = (detection_ns - injection_ns) / 1_000_000_000
        recovery_seconds = (recovery_ns - injection_ns) / 1_000_000_000
        if detection_seconds > config.execution.detection_budget_seconds:
            raise TimeoutError("detection_budget_exceeded")
        if recovery_seconds > config.execution.recovery_budget_seconds:
            raise TimeoutError("recovery_budget_exceeded")
        state = state_store.transition(
            run_id,
            next_state="verifying",
            expected_revision=state.revision,
            reason="target_health_recovered",
        )
        inference = post_inference(config)
        inference_check = validate_inference(config, inference)
        if not inference_check.passed:
            raise RuntimeError(str(inference_check.reason_code))
        rollback_after = payload_sha256(deployment_rollback_payload(final_observation.deployment))
        identity_after = evaluate_identity_bundle(
            config,
            observed_image_digest=final_health.observed_identity["image_digest"],
            rollback_digest=rollback_after,
        )
        if identity_after.blockers or identity_after.identities != preflight.identities:
            raise RuntimeError("post_recovery_identity_mismatch")
        finished_at = datetime.now(timezone.utc)
        monotonic_finished_ns = time.monotonic_ns()
        atomic_write_json(
            run_root / "samples.json",
            {
                "schema_version": "evm.scenario_a_samples.v1",
                "cadence_seconds": config.execution.sample_cadence_seconds,
                "signal_precedence": config.execution.signal_precedence,
                "samples": [item.model_dump(mode="json") for item in samples],
            },
        )
        atomic_write_json(
            run_root / "final-observation.json",
            final_observation.model_dump(mode="json"),
        )
        atomic_write_json(run_root / "inference.json", inference)
        receipt_path = approval_store.root / f"{approval_id}.consumed.json"
        receipt = read_json(receipt_path)
        artifacts = [
            _artifact(run_root / "preflight.json"),
            _artifact(approval_store.root / f"{approval_id}.json"),
            _artifact(run_root / "rollback-target.json"),
            _artifact(run_root / "delete-options.json"),
            _artifact(run_root / "deleted-pod-response.json"),
            _artifact(run_root / "samples.json"),
            _artifact(run_root / "final-observation.json"),
            _artifact(run_root / "inference.json"),
            _artifact(receipt_path),
        ]
        interruption_samples = [item for item in samples if not item.signals["readiness"]]
        interruption_seconds = 0.0
        if interruption_samples:
            interruption_seconds = (
                interruption_samples[-1].monotonic_ns - interruption_samples[0].monotonic_ns
            ) / 1_000_000_000 + config.execution.sample_cadence_seconds
        pre_checks = [
            CheckEvidence(
                check_id=f"pre_{item.check_id}",
                passed=item.passed,
                observed=item.observed,
                reason_code=item.reason_code,
            )
            for item in preflight.checks
        ]
        post_checks = [
            CheckEvidence(
                check_id="post_new_pod_uid",
                passed=new_selectors.pod.uid != target.uid,
                observed={"old": target.uid, "new": new_selectors.pod.uid},
            ),
            CheckEvidence(
                check_id="post_detection_budget",
                passed=detection_seconds <= config.execution.detection_budget_seconds,
                observed=detection_seconds,
            ),
            CheckEvidence(
                check_id="post_recovery_budget",
                passed=recovery_seconds <= config.execution.recovery_budget_seconds,
                observed=recovery_seconds,
            ),
            CheckEvidence(
                check_id="post_exact_identity",
                passed=identity_after.identities == preflight.identities,
                observed=identity_after.identities.model_dump(),
            ),
            CheckEvidence(
                check_id="post_rollback_target_unchanged",
                passed=rollback_after == preflight.rollback_digest,
                observed={"before": preflight.rollback_digest, "after": rollback_after},
            ),
            inference_check,
        ]
        report = OperationalFailureReport(
            schema_version=SCHEMA_VERSION,
            scenario_id="A",
            run_id=run_id,
            claim_class="local_operational_validation",
            status="passed",
            started_at=started_at,
            finished_at=finished_at,
            actor="codex-local-operator",
            approval=ApprovalEvidence(
                required=True,
                decision="consumed",
                approval_id=approval_id,
                run_id=run_id,
                target_uid=target.uid,
                action_digest=binding.action_digest,
                source_revision=binding.source_revision,
                expires_at=binding.expires_at,
                consumed_at=datetime.fromisoformat(str(receipt["consumed_at"])),
                single_use=True,
            ),
            source=source.source,
            environment=EnvironmentEvidence(
                cluster_context="docker-desktop",
                node=new_selectors.node.name,
                namespaces=["kube-system", new_selectors.pod.namespace or ""],
                hardware={"single_node": True, "single_gpu": True, "gpu": "1/1"},
                runtime_versions={"evidence_contract": SCHEMA_VERSION},
            ),
            identities=preflight.identities,
            identity_requirements=[
                "dataset_version",
                "split_digest",
                "model_digest",
                "artifact_digest",
                "image_digest",
                "ct_digest",
                "rollback_digest",
            ],
            preconditions=pre_checks,
            injection=InjectionEvidence(
                method="kubernetes_delete_options_uid_precondition",
                action=preflight.action,
                target=target.model_dump(),
                expected_effect="single-replica endpoint interruption and controller recovery",
                blast_radius="one production B0 Pod only",
                performed=True,
            ),
            signals=[
                SignalEvidence(
                    signal_id=f"sample_{item.sequence}_{signal}",
                    source=signal,
                    observed_at=item.observed_at,
                    healthy=healthy,
                    detail={"selected": item.selected_unhealthy_signal == signal},
                )
                for item in samples
                for signal, healthy in item.signals.items()
            ],
            decision=DecisionEvidence(
                expected="detect <=30s and recover exact identity <=300s",
                observed=(
                    f"detected_by={detection_signal};detection={detection_seconds:.3f}s;"
                    f"recovery={recovery_seconds:.3f}s"
                ),
            ),
            mitigation={
                "controller": "Kubernetes Deployment controller",
                "manual_patch": False,
                "endpoint_interruption_seconds": interruption_seconds,
            },
            recovery=RecoveryEvidence(
                action="deployment_controller_recreated_pod",
                target_identity={"old_uid": target.uid, "new_uid": new_selectors.pod.uid},
                result="passed",
            ),
            postconditions=post_checks,
            artifacts=artifacts,
            limitations=[
                "single-node local Docker Desktop Kubernetes",
                "single replica caused measured interruption and does not prove HA",
                "controlled maintenance replay without real user traffic",
            ],
            portfolio=PortfolioEvidence(
                competencies=[
                    "UID-preconditioned fault injection",
                    "monotonic SLI measurement",
                    "artifact and runtime identity recovery",
                ],
                interview_questions=[
                    "How does a UID precondition prevent deleting a replacement Pod?",
                    "Why is controller recovery different from high availability?",
                ],
                trade_offs=[
                    "single-replica recovery is cheaper locally but necessarily interrupts service",
                ],
                factual_claims=[
                    "A bounded local production-Pod restart recovered exact model identity on CUDA",
                ],
                prohibited_claims=["zero downtime", "high availability", "production traffic A/B"],
            ),
            timing=TimingEvidence(
                audit_started_at=started_at,
                audit_finished_at=finished_at,
                monotonic_started_ns=monotonic_started_ns,
                monotonic_finished_ns=monotonic_finished_ns,
                injection_monotonic_ns=injection_ns,
                detection_monotonic_ns=detection_ns,
                recovery_monotonic_ns=recovery_ns,
                detection_seconds=detection_seconds,
                recovery_seconds=recovery_seconds,
                sample_cadence_seconds=config.execution.sample_cadence_seconds,
                signal_precedence=config.execution.signal_precedence,
            ),
            readiness_closure=ClosureEvidence(
                decision="passed",
                required_check_ids=[item.check_id for item in pre_checks],
                completed_at=preflight.checked_at,
            ),
            live_proof_closure=ClosureEvidence(
                decision="passed",
                required_check_ids=[item.check_id for item in post_checks],
                completed_at=finished_at,
            ),
        )
        report_path = run_root / "live-report.json"
        atomic_write_json(report_path, report.model_dump(mode="json"))
        state = state_store.transition(
            run_id,
            next_state="passed",
            expected_revision=state.revision,
            reason="live_recovery_acceptance_passed",
        )
        atomic_write_json(
            config.execution.evidence_root / "_latest" / "metrics.json",
            OperationalMetricProjection(
                schema_version="evm.operational_metrics.v1",
                scenario="A",
                target="production-b0",
                state="passed",
                signals={
                    "gpu_allocatable": True,
                    "device_plugin": True,
                    "deployment_ready": True,
                    "pod_ready": True,
                    "readiness": True,
                    "inference": True,
                    "prometheus": True,
                    "identity": True,
                },
                detection_seconds=detection_seconds,
                recovery_seconds=recovery_seconds,
                validation_result="passed",
            ).model_dump(mode="json"),
        )
        _write_series_result(
            config,
            {
                "run_id": run_id,
                "completed_at": finished_at.isoformat(),
                "source_revision": source.source.commit,
                "old_pod_uid": target.uid,
                "new_pod_uid": new_selectors.pod.uid,
                "detection_seconds": detection_seconds,
                "recovery_seconds": recovery_seconds,
                "interruption_seconds": interruption_seconds,
                "result": "passed",
            },
        )
        return LiveRunResult(
            run_id=run_id,
            report_path=report_path,
            old_pod_uid=target.uid,
            new_pod_uid=new_selectors.pod.uid,
            detection_seconds=detection_seconds,
            recovery_seconds=recovery_seconds,
            interruption_seconds=interruption_seconds,
            sample_count=len(samples),
            approval_id=approval_id,
        )
    except Exception as exc:
        _failure(state_store=state_store, run_id=run_id, run_root=run_root, error=exc)
        raise
    finally:
        lease_manager.release(lease)

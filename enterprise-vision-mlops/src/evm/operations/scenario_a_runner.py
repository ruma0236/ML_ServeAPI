from __future__ import annotations

import json
import subprocess
import time
import tomllib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evm.operations.failure_evidence import (
    ApprovalEvidence,
    ArtifactEvidence,
    CheckEvidence,
    ClosureEvidence,
    DecisionEvidence,
    EnvironmentEvidence,
    IdentityEvidence,
    InjectionEvidence,
    OperationalFailureReport,
    PortfolioEvidence,
    RecoveryEvidence,
    SCHEMA_VERSION,
    SignalEvidence,
    SourceEvidence,
    TimingEvidence,
    sha256_file,
)
from evm.operations.failure_scenarios import ScenarioStateStore, atomic_write_json
from evm.operations.metrics import OperationalMetricProjection
from evm.operations.runtime_adapters import (
    ExactSelectionError,
    HttpAdapter,
    KubernetesAdapter,
    PrometheusTargetSelector,
    ResourceSelector,
    ScenarioACollector,
    ScenarioAObservation,
    ScenarioASelectors,
    pod_is_historical,
)
from evm.operations.target_health import (
    ScenarioAHealth,
    ScenarioAIdentityContract,
    evaluate_scenario_a_health,
)


TextRunner = Callable[[list[str], Path | None], str]


def _run_text(args: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioATargetConfig(StrictModel):
    node_name: str
    device_plugin_namespace: str
    device_plugin_name: str
    deployment_namespace: str
    deployment_name: str
    pod_label: str


class ScenarioAHttpConfig(StrictModel):
    readiness_url: str
    prometheus_targets_url: str
    prometheus_job: str
    prometheus_instance: str


class ScenarioARuntimeConfig(StrictModel):
    api_container: str
    supervisor_path: Path


class ScenarioAExecutionConfig(StrictModel):
    evidence_root: Path
    sample_cadence_seconds: float = Field(gt=0)
    signal_precedence: list[str] = Field(min_length=1)


class ScenarioAConfig(StrictModel):
    target: ScenarioATargetConfig
    http: ScenarioAHttpConfig
    runtime: ScenarioARuntimeConfig
    execution: ScenarioAExecutionConfig
    identity: ScenarioAIdentityContract


class RuntimeSourceSnapshot(StrictModel):
    source: SourceEvidence
    supervisor_status: str
    child_status: dict[str, str]
    revision_converged: bool


class BaselineResult(StrictModel):
    run_id: str
    run_root: Path
    report_path: Path
    metrics_path: Path
    decision: str
    blockers: list[str]
    state_revision: int
    target_pod_uid: str


def load_scenario_a_config(path: Path) -> ScenarioAConfig:
    with path.open("rb") as handle:
        return ScenarioAConfig.model_validate(tomllib.load(handle))


def _resource_selector(kind: str, payload: dict[str, Any]) -> ResourceSelector:
    metadata = payload.get("metadata") or {}
    return ResourceSelector(
        kind=kind,
        namespace=metadata.get("namespace"),
        name=str(metadata.get("name") or ""),
        uid=str(metadata.get("uid") or ""),
    )


def discover_scenario_a_selectors(
    config: ScenarioAConfig,
    kubernetes: KubernetesAdapter,
) -> ScenarioASelectors:
    target = config.target
    node = kubernetes.get_named(kind="node", name=target.node_name)
    plugin = kubernetes.get_named(
        kind="daemonset",
        namespace=target.device_plugin_namespace,
        name=target.device_plugin_name,
    )
    deployment = kubernetes.get_named(
        kind="deployment",
        namespace=target.deployment_namespace,
        name=target.deployment_name,
    )
    pods = kubernetes.list_by_label(
        kind="pod",
        namespace=target.deployment_namespace,
        label=target.pod_label,
    )
    active = [item for item in pods if not pod_is_historical(item)]
    if len(active) != 1:
        raise ExactSelectionError(
            f"active_pod_discovery_cardinality_failed:label={target.pod_label}:active={len(active)}"
        )
    pod = active[0]
    return ScenarioASelectors(
        node=_resource_selector("node", node),
        device_plugin=_resource_selector("daemonset", plugin),
        deployment=_resource_selector("deployment", deployment),
        pod=_resource_selector("pod", pod),
        pod_label=target.pod_label,
        prometheus=PrometheusTargetSelector(
            job=config.http.prometheus_job,
            instance=config.http.prometheus_instance,
        ),
        readiness_url=config.http.readiness_url,
        prometheus_targets_url=config.http.prometheus_targets_url,
    )


def collect_runtime_source(
    *,
    project_root: Path,
    config: ScenarioAConfig,
    runner: TextRunner = _run_text,
) -> RuntimeSourceSnapshot:
    commit = runner(["git", "rev-parse", "HEAD"], project_root)
    branch = runner(["git", "branch", "--show-current"], project_root)
    dirty = bool(runner(["git", "status", "--porcelain", "--", "."], project_root))
    container_env = json.loads(
        runner(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", config.runtime.api_container],
            project_root,
        )
    )
    env = dict(item.split("=", maxsplit=1) for item in container_env if "=" in item)
    api_revision = env.get("GIT_COMMIT") or env.get("EVM_EXPECTED_CI_COMMIT") or "unknown"
    supervisor = json.loads(config.runtime.supervisor_path.read_text(encoding="utf-8-sig"))
    supervisor_revision = str(supervisor.get("source_commit") or "unknown")
    children = {
        str(item.get("name") or "unknown"): str(item.get("status") or "unknown")
        for item in supervisor.get("children") or []
    }
    revision_matches = all(
        bool(item.get("revision_matches")) for item in supervisor.get("children") or []
    )
    converged = (
        commit == api_revision == supervisor_revision
        and supervisor.get("status") == "healthy"
        and children.get("lifecycle_worker") == "live"
        and children.get("kubernetes_observer") == "live"
        and revision_matches
    )
    return RuntimeSourceSnapshot(
        source=SourceEvidence(
            commit=commit,
            branch=branch,
            dirty=dirty,
            api_revision=api_revision,
            worker_revision=supervisor_revision,
            observer_revision=supervisor_revision,
        ),
        supervisor_status=str(supervisor.get("status") or "unknown"),
        child_status=children,
        revision_converged=converged,
    )


def _write_artifact(path: Path, payload: dict[str, Any]) -> ArtifactEvidence:
    atomic_write_json(path, payload)
    return ArtifactEvidence(
        uri=str(path.resolve()),
        sha256=sha256_file(path),
        media_type="application/json",
        evidence_role="run_evidence",
    )


def _signal_projection(health: ScenarioAHealth) -> dict[str, bool]:
    checks = {check.check_id: check.passed for check in health.checks}
    identity = all(value for key, value in checks.items() if key.startswith("identity_"))
    return {
        "gpu_allocatable": checks.get("node_gpu_capacity", False),
        "device_plugin": checks.get("device_plugin_ready", False),
        "deployment_ready": checks.get("deployment_ready", False),
        "pod_ready": checks.get("exact_pod_ready", False),
        "readiness": checks.get("readiness_cuda", False),
        "prometheus": checks.get("prometheus_target_up", False),
        "identity": identity,
    }


def _build_report(
    *,
    run_id: str,
    selectors: ScenarioASelectors,
    observation: ScenarioAObservation,
    health: ScenarioAHealth,
    runtime_source: RuntimeSourceSnapshot,
    artifacts: list[ArtifactEvidence],
    started_at: datetime,
    finished_at: datetime,
    monotonic_started_ns: int,
    monotonic_finished_ns: int,
    config: ScenarioAConfig,
) -> OperationalFailureReport:
    checks = [
        CheckEvidence(
            check_id=item.check_id,
            passed=item.passed,
            observed=item.observed,
            reason_code=item.reason_code,
        )
        for item in health.checks
    ]
    checks.extend(
        [
            CheckEvidence(
                check_id="source_revision_converged",
                passed=runtime_source.revision_converged,
                observed={
                    "source": runtime_source.source.model_dump(mode="json"),
                    "supervisor_status": runtime_source.supervisor_status,
                    "children": runtime_source.child_status,
                },
                reason_code=None if runtime_source.revision_converged else "revision_mismatch",
            ),
            CheckEvidence(
                check_id="project_worktree_clean",
                passed=not runtime_source.source.dirty,
                observed={"scoped_to": str(Path.cwd()), "dirty": runtime_source.source.dirty},
                reason_code=None if not runtime_source.source.dirty else "dirty_worktree",
            ),
        ]
    )
    blockers = [str(item.reason_code) for item in checks if not item.passed]
    readiness_decision = "passed" if not blockers else "blocked"
    pod_metadata = observation.pod.get("metadata") or {}
    node_status = observation.node.get("status") or {}
    image_digest = health.observed_identity["image_digest"].removeprefix("sha256:")
    model_digest = health.observed_identity["model_sha256"]
    signals = _signal_projection(health)
    return OperationalFailureReport(
        schema_version=SCHEMA_VERSION,
        scenario_id="A",
        run_id=run_id,
        claim_class="local_operational_validation",
        status="blocked",
        started_at=started_at,
        finished_at=finished_at,
        actor="codex-local-operator",
        approval=ApprovalEvidence(required=True, decision="pending", run_id=run_id),
        source=runtime_source.source,
        environment=EnvironmentEvidence(
            cluster_context="docker-desktop",
            node=selectors.node.name,
            namespaces=[
                selectors.device_plugin.namespace or "",
                selectors.deployment.namespace or "",
            ],
            hardware={
                "gpu_capacity": (node_status.get("capacity") or {}).get("nvidia.com/gpu"),
                "gpu_allocatable": (node_status.get("allocatable") or {}).get("nvidia.com/gpu"),
                "single_node": True,
                "single_gpu": True,
            },
            runtime_versions={"evidence_contract": SCHEMA_VERSION},
        ),
        identities=IdentityEvidence(
            dataset_version=health.observed_identity["dataset_version"],
            model_digest=model_digest,
            image_digest=image_digest,
        ),
        identity_requirements=["dataset_version", "model_digest", "image_digest"],
        preconditions=checks,
        injection=InjectionEvidence(
            method="kubernetes_exact_uid_delete",
            action="delete one production B0 Pod after approval",
            target={
                "namespace": selectors.pod.namespace or "",
                "name": selectors.pod.name,
                "uid": selectors.pod.uid,
            },
            expected_effect="bounded single-replica endpoint interruption and controller recovery",
            blast_radius="one production B0 Pod; no device-plugin, data, or staging mutation",
            performed=False,
        ),
        signals=[
            SignalEvidence(
                signal_id=signal,
                source="target_scoped_baseline",
                observed_at=finished_at,
                healthy=healthy,
            )
            for signal, healthy in signals.items()
        ],
        decision=DecisionEvidence(
            expected="readiness passes; live proof remains blocked before approval",
            observed=f"readiness={readiness_decision};live_proof=not_run",
            blocker_codes=blockers + ["live_proof_not_run"],
        ),
        mitigation={"planned": "allow the deployment controller to recreate the exact Pod"},
        recovery=RecoveryEvidence(
            action="not_run",
            target_identity={"pod_uid": str(pod_metadata.get("uid") or "")},
            result="not_run",
        ),
        postconditions=[],
        artifacts=artifacts,
        limitations=[
            "single-node local Docker Desktop Kubernetes; this is not HA",
            "single production replica; a live drill is expected to interrupt the endpoint",
            "baseline collection is read-only and does not prove live recovery",
        ],
        portfolio=PortfolioEvidence(
            competencies=[
                "exact target selection",
                "fail-closed operational evidence",
                "runtime revision reconciliation",
            ],
            interview_questions=[
                "Why are resource names insufficient without UID binding?",
                "Why must readiness and live recovery evidence be separate?",
            ],
            trade_offs=[
                "strict identity and clean-revision gates reduce convenience but prevent stale proof",
            ],
            factual_claims=[
                "A read-only local baseline was captured with exact Kubernetes and Prometheus identity",
            ],
            prohibited_claims=["high availability", "zero downtime", "production traffic A/B"],
        ),
        timing=TimingEvidence(
            audit_started_at=started_at,
            audit_finished_at=finished_at,
            monotonic_started_ns=monotonic_started_ns,
            monotonic_finished_ns=monotonic_finished_ns,
            sample_cadence_seconds=config.execution.sample_cadence_seconds,
            signal_precedence=config.execution.signal_precedence,
        ),
        readiness_closure=ClosureEvidence(
            decision=readiness_decision,
            required_check_ids=[item.check_id for item in checks],
            blockers=blockers,
            completed_at=finished_at if readiness_decision == "passed" else None,
        ),
        live_proof_closure=ClosureEvidence(
            decision="not_run",
            blockers=["live_proof_not_run"],
        ),
    )


def run_read_only_baseline(
    *,
    config: ScenarioAConfig,
    project_root: Path,
    run_id: str | None = None,
    kubernetes: KubernetesAdapter | None = None,
    http: HttpAdapter | None = None,
    text_runner: TextRunner = _run_text,
) -> BaselineResult:
    started_at = datetime.now(timezone.utc)
    monotonic_started_ns = time.monotonic_ns()
    rid = run_id or f"scenario-a-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_root = config.execution.evidence_root / "A" / rid
    state_store = ScenarioStateStore(config.execution.evidence_root / "A")
    state = state_store.create(scenario_id="A", run_id=rid, now=started_at)
    kube = kubernetes or KubernetesAdapter()
    selectors = discover_scenario_a_selectors(config, kube)
    observation = ScenarioACollector(kube, http or HttpAdapter()).collect(selectors)
    health = evaluate_scenario_a_health(observation, config.identity)
    runtime_source = collect_runtime_source(
        project_root=project_root,
        config=config,
        runner=text_runner,
    )
    artifacts = [
        _write_artifact(run_root / "selectors.json", selectors_to_payload(selectors)),
        _write_artifact(run_root / "observation.json", observation.model_dump(mode="json")),
        _write_artifact(run_root / "target-health.json", health.model_dump(mode="json")),
        _write_artifact(run_root / "runtime-source.json", runtime_source.model_dump(mode="json")),
    ]
    finished_at = datetime.now(timezone.utc)
    monotonic_finished_ns = time.monotonic_ns()
    report = _build_report(
        run_id=rid,
        selectors=selectors,
        observation=observation,
        health=health,
        runtime_source=runtime_source,
        artifacts=artifacts,
        started_at=started_at,
        finished_at=finished_at,
        monotonic_started_ns=monotonic_started_ns,
        monotonic_finished_ns=monotonic_finished_ns,
        config=config,
    )
    report_path = run_root / "report.json"
    atomic_write_json(report_path, report.model_dump(mode="json"))
    if report.readiness_closure.decision == "passed":
        state = state_store.transition(
            rid,
            next_state="baseline_validated",
            expected_revision=state.revision,
            reason="read_only_baseline_passed",
            now=finished_at,
        )
    else:
        state = state_store.transition(
            rid,
            next_state="blocked",
            expected_revision=state.revision,
            reason="read_only_baseline_blocked",
            now=finished_at,
        )

    projection = OperationalMetricProjection(
        schema_version="evm.operational_metrics.v1",
        scenario="A",
        target="production-b0",
        state=state.state,
        signals=_signal_projection(health),
        validation_result="not_run" if state.state == "baseline_validated" else "blocked",
        blockers=[] if state.state == "baseline_validated" else _metric_blockers(report),
    )
    metrics_path = config.execution.evidence_root / "_latest" / "metrics.json"
    atomic_write_json(metrics_path, projection.model_dump(mode="json"))
    return BaselineResult(
        run_id=rid,
        run_root=run_root,
        report_path=report_path,
        metrics_path=metrics_path,
        decision=report.readiness_closure.decision,
        blockers=report.readiness_closure.blockers,
        state_revision=state.revision,
        target_pod_uid=selectors.pod.uid,
    )


def _metric_blockers(report: OperationalFailureReport) -> list[str]:
    allowed = {
        "dirty_worktree",
        "revision_mismatch",
        "target_unhealthy",
        "identity_mismatch",
        "target_ambiguous",
        "target_missing",
    }
    normalized = []
    for blocker in report.readiness_closure.blockers:
        if blocker in allowed:
            normalized.append(blocker)
        elif blocker.startswith("identity_"):
            normalized.append("identity_mismatch")
        else:
            normalized.append("target_unhealthy")
    return sorted(set(normalized))


def selectors_to_payload(selectors: ScenarioASelectors) -> dict[str, Any]:
    return {
        "node": selectors.node.model_dump(),
        "device_plugin": selectors.device_plugin.model_dump(),
        "deployment": selectors.deployment.model_dump(),
        "pod": selectors.pod.model_dump(),
        "pod_label": selectors.pod_label,
        "prometheus": selectors.prometheus.model_dump(),
        "readiness_url": selectors.readiness_url,
        "prometheus_targets_url": selectors.prometheus_targets_url,
    }

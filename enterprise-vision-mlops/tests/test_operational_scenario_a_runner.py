from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from evm.operations.failure_evidence import OperationalFailureReport, validate_closure
from evm.operations.runtime_adapters import ExactSelectionError, HttpAdapter, KubernetesAdapter
from evm.operations.scenario_a_runner import (
    ScenarioAConfig,
    ScenarioAArtifactConfig,
    ScenarioAExecutionConfig,
    ScenarioAHttpConfig,
    ScenarioAInferenceConfig,
    ScenarioARuntimeConfig,
    ScenarioATargetConfig,
    discover_scenario_a_selectors,
    load_scenario_a_config,
    run_read_only_baseline,
)
from evm.operations.target_health import ScenarioAIdentityContract


FIXTURES = Path(__file__).parent / "fixtures" / "operations"
ROOT = Path(__file__).parents[1]
COMMIT = "1" * 40


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _config(tmp_path: Path) -> ScenarioAConfig:
    supervisor_path = tmp_path / "supervisor.json"
    supervisor_path.write_text(
        json.dumps(
            {
                "status": "healthy",
                "source_commit": COMMIT,
                "children": [
                    {
                        "name": "lifecycle_worker",
                        "status": "live",
                        "revision_matches": True,
                    },
                    {
                        "name": "kubernetes_observer",
                        "status": "live",
                        "revision_matches": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return ScenarioAConfig(
        target=ScenarioATargetConfig(
            node_name="docker-desktop",
            device_plugin_namespace="kube-system",
            device_plugin_name="nvidia-device-plugin-daemonset",
            deployment_namespace="evm-production",
            deployment_name="evm-b0-production",
            pod_label="app.kubernetes.io/name=evm-b0-production",
        ),
        http=ScenarioAHttpConfig(
            readiness_url="http://ready",
            prometheus_targets_url="http://prometheus",
            prometheus_job="evm-b0-production",
            prometheus_instance="host.docker.internal:30800",
        ),
        runtime=ScenarioARuntimeConfig(
            api_container="evm-api",
            supervisor_path=supervisor_path,
        ),
        execution=ScenarioAExecutionConfig(
            evidence_root=tmp_path / "evidence",
            sample_cadence_seconds=5,
            signal_precedence=["kubernetes_pod", "readiness", "prometheus"],
            detection_budget_seconds=30,
            recovery_budget_seconds=300,
            cooldown_seconds=30,
            required_independent_runs=3,
        ),
        identity=ScenarioAIdentityContract(
            service="evm-b0-production",
            candidate_id="effnet-b0-img224-expedited-adamw",
            dataset_version="visa-open-data-e35d93d5561f",
            model_sha256="a" * 64,
            image_digest="sha256:" + "b" * 64,
            device="cuda",
        ),
        artifacts=ScenarioAArtifactConfig(
            candidate_summary_path=tmp_path / "candidate_summary.json",
            model_path=tmp_path / "model.pt",
            model_sha256="a" * 64,
            split_manifest_path=tmp_path / "split_manifest.json",
            split_manifest_sha256="c" * 64,
            readiness_manifest_path=tmp_path / "readiness_manifest.json",
            readiness_manifest_sha256="d" * 64,
            ct_report_path=tmp_path / "ct_evaluation.json",
        ),
        inference=ScenarioAInferenceConfig(
            url="http://ready/predict",
            image_uri="file:///F:/sample.jpg",
            expected_prediction="normal",
        ),
    )


def _adapters(payload: dict | None = None) -> tuple[KubernetesAdapter, HttpAdapter]:
    resources = payload or _payload("scenario_a_kubernetes_baseline.json")
    objects = {
        "node": resources["node"],
        "daemonset": resources["device_plugin"],
        "deployment": resources["deployment"],
    }

    def runner(args: list[str]) -> dict:
        if args[2] == "pod":
            return resources["pods"]
        return objects[args[2]]

    readiness = _payload("scenario_a_readiness_baseline.json")
    prometheus = _payload("scenario_a_prometheus_baseline.json")
    return (
        KubernetesAdapter(runner),
        HttpAdapter(lambda url: readiness if url == "http://ready" else prometheus),
    )


def _text_runner(args: list[str], cwd: Path | None) -> str:
    if args[:3] == ["git", "rev-parse", "HEAD"]:
        return COMMIT
    if args[:3] == ["git", "branch", "--show-current"]:
        return "codex/mac-mini-worker"
    if args[:3] == ["git", "status", "--porcelain"]:
        assert args[-2:] == ["--", "."]
        return ""
    if args[:2] == ["docker", "inspect"]:
        return json.dumps([f"GIT_COMMIT={COMMIT}"])
    raise AssertionError(args)


def test_discovery_binds_live_resources_to_exact_uids(tmp_path: Path) -> None:
    kubernetes, _ = _adapters()
    selectors = discover_scenario_a_selectors(_config(tmp_path), kubernetes)

    assert selectors.node.uid == "node-uid"
    assert selectors.device_plugin.uid == "plugin-uid"
    assert selectors.deployment.uid == "deployment-uid"
    assert selectors.pod.uid == "pod-current"


def test_versioned_local_config_uses_the_production_workload_label() -> None:
    config = load_scenario_a_config(
        ROOT / "configs" / "operations" / "local_failure_validation.toml"
    )

    assert config.target.pod_label == "app.kubernetes.io/name=evm-b0-production"
    assert config.target.deployment_namespace == "evm-production"


def test_discovery_rejects_multiple_active_pods(tmp_path: Path) -> None:
    payload = _payload("scenario_a_kubernetes_baseline.json")
    duplicate = deepcopy(payload["pods"]["items"][0])
    duplicate["metadata"].update({"name": "second", "uid": "second"})
    payload["pods"]["items"].append(duplicate)
    kubernetes, _ = _adapters(payload)

    with pytest.raises(ExactSelectionError, match="active_pod_discovery_cardinality_failed"):
        discover_scenario_a_selectors(_config(tmp_path), kubernetes)


def test_read_only_baseline_writes_valid_evidence_and_no_live_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)
    kubernetes, http = _adapters()
    result = run_read_only_baseline(
        config=config,
        project_root=tmp_path,
        run_id="scenario-a-test-1",
        kubernetes=kubernetes,
        http=http,
        text_runner=_text_runner,
    )
    report = OperationalFailureReport.model_validate_json(result.report_path.read_text())

    assert result.decision == "passed"
    assert result.target_pod_uid == "pod-current"
    assert report.readiness_closure.decision == "passed"
    assert report.live_proof_closure.decision == "not_run"
    assert report.injection.performed is False
    assert validate_closure(report, "readiness") == []
    assert json.loads(result.metrics_path.read_text())["state"] == "baseline_validated"

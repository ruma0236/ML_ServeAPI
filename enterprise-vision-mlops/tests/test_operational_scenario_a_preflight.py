from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from copy import deepcopy
from pathlib import Path

from evm.operations.failure_scenarios import ScenarioStateStore, atomic_write_json
from evm.operations.runtime_adapters import HttpAdapter, KubernetesAdapter
from evm.operations.scenario_a_live import (
    delete_exact_pod,
    delete_options,
    select_unhealthy_signal,
)
from evm.operations.scenario_a_preflight import (
    deployment_rollback_payload,
    evaluate_identity_bundle,
    payload_sha256,
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
    run_read_only_baseline,
)
from evm.operations.target_health import ScenarioAIdentityContract


FIXTURES = Path(__file__).parent / "fixtures" / "operations"
COMMIT = "1" * 40
IMAGE = "sha256:" + "b" * 64


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _config(tmp_path: Path) -> ScenarioAConfig:
    model = tmp_path / "model.pt"
    split = tmp_path / "split.json"
    readiness_manifest = tmp_path / "readiness.json"
    model.write_bytes(b"model")
    split.write_bytes(b"split")
    readiness_manifest.write_bytes(b"readiness")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "status": "pass",
                "candidate_id": "candidate-a",
                "dataset_version": "dataset-a",
                "model_sha256": _sha(b"model"),
                "promotion_blockers": [],
            }
        ),
        encoding="utf-8",
    )
    ct = tmp_path / "ct.json"
    ct.write_text(
        json.dumps(
            {
                "evaluation_id": "ct-a",
                "status": "pass",
                "decision": "pass",
                "candidate_id": "candidate-a",
                "dataset_version": "dataset-a",
                "model_sha256": _sha(b"model"),
                "device": "cuda",
                "ct_record_count": 20,
                "overlap_count": 0,
                "mutated": False,
                "training_mount_isolated": True,
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    supervisor = tmp_path / "supervisor.json"
    supervisor.write_text(
        json.dumps(
            {
                "status": "healthy",
                "source_commit": COMMIT,
                "children": [
                    {"name": "lifecycle_worker", "status": "live", "revision_matches": True},
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
        runtime=ScenarioARuntimeConfig(api_container="evm-api", supervisor_path=supervisor),
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
            candidate_id="candidate-a",
            dataset_version="dataset-a",
            model_sha256=_sha(b"model"),
            image_digest=IMAGE,
            device="cuda",
        ),
        artifacts=ScenarioAArtifactConfig(
            candidate_summary_path=candidate,
            model_path=model,
            model_sha256=_sha(b"model"),
            split_manifest_path=split,
            split_manifest_sha256=_sha(b"split"),
            readiness_manifest_path=readiness_manifest,
            readiness_manifest_sha256=_sha(b"readiness"),
            ct_report_path=ct,
        ),
        inference=ScenarioAInferenceConfig(
            url="http://ready/predict",
            image_uri="file:///F:/sample.jpg",
            expected_prediction="normal",
        ),
    )


def _adapters(config: ScenarioAConfig) -> tuple[KubernetesAdapter, HttpAdapter, dict]:
    resources = _payload("scenario_a_kubernetes_baseline.json")
    readiness = _payload("scenario_a_readiness_baseline.json")
    readiness.update(
        {
            "candidate_id": config.identity.candidate_id,
            "dataset_version": config.identity.dataset_version,
            "model_sha256": config.identity.model_sha256,
        }
    )
    objects = {
        "node": resources["node"],
        "daemonset": resources["device_plugin"],
        "deployment": resources["deployment"],
    }

    def runner(args: list[str]) -> dict:
        return resources["pods"] if args[2] == "pod" else objects[args[2]]

    prometheus = _payload("scenario_a_prometheus_baseline.json")
    return (
        KubernetesAdapter(runner),
        HttpAdapter(lambda url: readiness if url == "http://ready" else prometheus),
        resources,
    )


def _text_runner(args: list[str], cwd: Path | None) -> str:
    if args[:3] == ["git", "rev-parse", "HEAD"]:
        return COMMIT
    if args[:3] == ["git", "branch", "--show-current"]:
        return "branch"
    if args[:3] == ["git", "status", "--porcelain"]:
        return ""
    return json.dumps([f"GIT_COMMIT={COMMIT}"])


def test_identity_bundle_requires_matching_production_ct(tmp_path: Path) -> None:
    config = _config(tmp_path)
    bundle = evaluate_identity_bundle(
        config,
        observed_image_digest=IMAGE,
        rollback_digest="e" * 64,
    )
    assert bundle.blockers == []
    assert bundle.identities.ct_digest
    assert all(bundle.identities.model_dump().values())

    ct = json.loads(config.artifacts.ct_report_path.read_text())
    ct["model_sha256"] = "f" * 64
    config.artifacts.ct_report_path.write_text(json.dumps(ct), encoding="utf-8")
    blocked = evaluate_identity_bundle(
        config,
        observed_image_digest=IMAGE,
        rollback_digest="e" * 64,
    )
    assert "identity_isolated_ct_failed" in blocked.blockers


def test_rollback_digest_ignores_status_but_not_template() -> None:
    deployment = _payload("scenario_a_kubernetes_baseline.json")["deployment"]
    deployment["spec"] = {
        "replicas": 1,
        "selector": {"matchLabels": {"app": "serving"}},
        "template": {"spec": {"containers": [{"image": "stable"}]}},
    }
    changed_status = deepcopy(deployment)
    changed_status["status"]["readyReplicas"] = 0
    changed_template = deepcopy(deployment)
    changed_template["spec"]["template"] = {"spec": {"containers": [{"image": "changed"}]}}

    assert payload_sha256(deployment_rollback_payload(deployment)) == payload_sha256(
        deployment_rollback_payload(changed_status)
    )
    assert payload_sha256(deployment_rollback_payload(deployment)) != payload_sha256(
        deployment_rollback_payload(changed_template)
    )


def test_delete_contract_and_signal_precedence_are_fail_closed() -> None:
    from evm.operations.failure_scenarios import TargetRef

    target = TargetRef(namespace="evm-production", name="pod-a", uid="uid-a")
    assert delete_options(target)["preconditions"] == {"uid": "uid-a"}
    assert (
        select_unhealthy_signal(
            {"kubernetes_pod": False, "readiness": False, "prometheus": True},
            ["kubernetes_pod", "readiness", "prometheus"],
        )
        == "kubernetes_pod"
    )


def test_exact_delete_uses_raw_uid_precondition_body(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from evm.operations.failure_scenarios import TargetRef

    target = TargetRef(namespace="evm-production", name="pod-a", uid="uid-a")
    options_path = tmp_path / "delete-options.json"
    atomic_write_json(options_path, delete_options(target))
    observed: list[str] = []

    def fake_run(args, **kwargs):
        observed.extend(args)
        return SimpleNamespace(stdout=json.dumps({"metadata": {"uid": "uid-a"}}))

    monkeypatch.setattr("evm.operations.scenario_a_live.subprocess.run", fake_run)
    response = delete_exact_pod(target, options_path)

    assert response["metadata"]["uid"] == "uid-a"
    assert "--raw=/api/v1/namespaces/evm-production/pods/pod-a" in observed
    assert f"--filename={options_path}" in observed


def test_preflight_closes_identity_rollback_and_non_mutation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    kubernetes, http, resources = _adapters(config)
    baseline = run_read_only_baseline(
        config=config,
        project_root=tmp_path,
        run_id="run-a",
        kubernetes=kubernetes,
        http=http,
        text_runner=_text_runner,
    )
    run_root = baseline.run_root
    state_store = ScenarioStateStore(config.execution.evidence_root / "A")
    state = state_store.load("run-a")
    state_store.transition(
        "run-a",
        next_state="non_disruptive_validated",
        expected_revision=state.revision,
        reason="fixture_reconciliation_passed",
    )
    atomic_write_json(
        run_root / "device-plugin-reconciliation-plan.json",
        {"decision": "no_change", "mutation_performed": False},
    )
    atomic_write_json(run_root / "device-plugin-before.json", resources["device_plugin"])
    atomic_write_json(run_root / "device-plugin-after.json", resources["device_plugin"])

    preflight = prepare_scenario_a_preflight(
        config=config,
        project_root=tmp_path,
        run_id="run-a",
        kubernetes=kubernetes,
        http=http,
        text_runner=_text_runner,
    )

    assert preflight.decision == "passed"
    assert preflight.blockers == []
    assert preflight.target.uid == "pod-current"
    assert state_store.load("run-a").state == "pending_approval"

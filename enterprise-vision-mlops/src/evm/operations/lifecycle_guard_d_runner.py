from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from evm.control_panel import operations
from evm.control_panel.lifecycle_guards import (
    LifecycleGuardBlocked,
    LifecycleSideEffectLedger,
    canonical_digest,
    complete_side_effect,
    file_digest,
    reserve_side_effect,
)
from evm.control_panel.lifecycle_orchestrator import (
    LifecycleStageBlocked,
    execute_guarded_kubernetes_task,
    reserve_external_action,
)
from evm.control_panel.schemas import TaskAssignmentRequest
from evm.operations.lifecycle_guard_e_runner import (
    DEFAULT_INFERENCE_IMAGE_URI,
    build_evidence_index,
    external_side_effect_snapshot,
    git_text,
    invariant_diff,
    production_snapshot,
    read_json,
    utc_now,
    write_json,
)


SCHEMA = "evm.lifecycle_guard_scenario_d_replay.v1"
DEFAULT_OUTPUT_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/"
    "lifecycle_guard_validation"
)


@contextmanager
def temporary_environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def blocker_result(replay: int, blockers: list[str], elapsed: float) -> dict[str, Any]:
    normalized = sorted(set(blockers))
    return {
        "replay": replay,
        "decision": "blocked",
        "blockers": normalized,
        "decision_fingerprint": canonical_digest(normalized),
        "elapsed_seconds": round(elapsed, 6),
    }


def replay_terminal_ledger(directory: Path, replay_count: int = 3) -> list[dict[str, Any]]:
    path = directory / "side_effect_ledger.json"
    baseline_digest = file_digest(path)
    ledger = LifecycleSideEffectLedger.model_validate(read_json(path))
    results: list[dict[str, Any]] = []
    for replay in range(1, replay_count + 1):
        started = time.monotonic()
        for entry in ledger.entries:
            complete_side_effect(
                directory=directory,
                side_effect_key=entry.side_effect_key,
                state=entry.state,
                runtime_id=entry.runtime_id,
                evidence_uri=entry.evidence_uri,
            )
        result = {
            "replay": replay,
            "decision": "pass",
            "entry_count": len(ledger.entries),
            "unique_key_count": len({item.side_effect_key for item in ledger.entries}),
            "ledger_digest": file_digest(path),
            "decision_fingerprint": canonical_digest(
                [
                    {
                        "key": item.side_effect_key,
                        "state": item.state,
                        "runtime_id": item.runtime_id,
                    }
                    for item in ledger.entries
                ]
            ),
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        write_json(directory / "replays" / f"replay-{replay}.json", result)
        results.append(result)
    if file_digest(path) != baseline_digest:
        raise RuntimeError("terminal_side_effect_replay_mutated_ledger")
    return results


def replay_invalid_ledger(
    directory: Path,
    *,
    identity: dict[str, str],
    replay_count: int = 3,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for replay in range(1, replay_count + 1):
        started = time.monotonic()
        try:
            reserve_side_effect(
                directory=directory,
                lifecycle_series_id=identity["lifecycle_series_id"],
                run_id=identity["run_id"],
                attempt_id=identity["attempt_id"],
                correlation_id=identity["correlation_id"],
                stage_id="model_training",
                action="execute_kubernetes_job",
                action_payload={"replay_fixture": "must_not_dispatch"},
            )
        except LifecycleGuardBlocked as exc:
            result = blocker_result(
                replay,
                exc.blockers,
                time.monotonic() - started,
            )
        else:
            result = {
                "replay": replay,
                "decision": "unexpected_pass",
                "blockers": [],
                "decision_fingerprint": "",
                "elapsed_seconds": round(time.monotonic() - started, 6),
            }
        write_json(directory / "replays" / f"replay-{replay}.json", result)
        results.append(result)
    return results


def replay_terminal_state_regression(
    directory: Path,
    *,
    side_effect_key: str,
    replay_count: int = 3,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for replay in range(1, replay_count + 1):
        started = time.monotonic()
        try:
            complete_side_effect(
                directory=directory,
                side_effect_key=side_effect_key,
                state="reconciled",
            )
        except LifecycleGuardBlocked as exc:
            result = blocker_result(replay, exc.blockers, time.monotonic() - started)
        else:
            result = {
                "replay": replay,
                "decision": "unexpected_pass",
                "blockers": [],
                "decision_fingerprint": "",
                "elapsed_seconds": round(time.monotonic() - started, 6),
            }
        write_json(directory / "replays" / f"replay-{replay}.json", result)
        results.append(result)
    return results


def fixture_job(
    *,
    run_id: str,
    job_name: str,
    source_revision: str,
    uid: str | None = None,
    candidate_label: str = "efficientnet-b0-d-recovery",
    complete: bool = False,
) -> dict[str, Any]:
    run_label = run_id[-12:]
    payload: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": "evm-training",
            "labels": {
                "app.kubernetes.io/part-of": "enterprise-vision-mlops",
                "evm.openai.local/lifecycle-run": run_label,
                "evm.openai.local/candidate-id": candidate_label,
            },
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "trainer",
                            "image": "evm-pipeline@sha256:" + "d" * 64,
                            "env": [
                                {
                                    "name": "EVM_EXPECTED_COMPONENT_SOURCE_REVISION",
                                    "value": source_revision,
                                },
                                {"name": "EVM_LIFECYCLE_RUN_ID", "value": run_id},
                            ],
                        }
                    ],
                    "restartPolicy": "Never",
                }
            }
        },
    }
    if uid:
        payload["metadata"]["uid"] = uid
        payload["status"] = (
            {"conditions": [{"type": "Complete", "status": "True"}]}
            if complete
            else {"active": 1}
        )
    return payload


class ControlledKubernetesRunner:
    def __init__(self, expected: dict[str, Any], *, wrong_candidate: bool = False):
        self.expected = expected
        self.wrong_candidate = wrong_candidate
        self.calls: list[list[str]] = []
        self.get_count = 0

    def __call__(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[1:3] == ["config", "current-context"]:
            return subprocess.CompletedProcess(command, 0, "docker-desktop\n", "")
        if command[1:3] == ["get", "job"]:
            self.get_count += 1
            payload = copy.deepcopy(self.expected)
            payload["metadata"]["uid"] = "controlled-job-uid"
            if self.wrong_candidate:
                payload["metadata"]["labels"][
                    "evm.openai.local/candidate-id"
                ] = "wrong-candidate"
            payload["status"] = (
                {"conditions": [{"type": "Complete", "status": "True"}]}
                if self.get_count > 1
                else {"active": 1}
            )
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[1] == "logs":
            return subprocess.CompletedProcess(command, 0, "controlled replay complete\n", "")
        raise AssertionError(f"unexpected_mutating_command:{command}")


def run_exact_reconciliation_fixture(
    fixture_root: Path,
    *,
    project_root: Path,
    source_revision: str,
    wrong_candidate: bool,
) -> dict[str, Any]:
    run_id = f"lifecycle-d-{fixture_root.name[-12:]}"
    job_name = f"evm-lifecycle-train-{run_id[-12:]}"
    branch = "w" if wrong_candidate else "e"
    replay = fixture_root.name.rsplit("-", 1)[-1]
    short_root = fixture_root.parents[1] / f"_r-{branch}-{replay}"
    manifest_dir = short_root / "g"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    expected = fixture_job(
        run_id=run_id,
        job_name=job_name,
        source_revision=source_revision,
    )
    write_json(manifest_dir / "training-job.json", expected)
    (manifest_dir / "kustomization.yaml").write_text(
        "resources:\n  - training-job.json\n", encoding="utf-8"
    )
    guard_dir = fixture_root / "guard"
    guard_dir.mkdir(parents=True, exist_ok=True)
    identity = {
        "lifecycle_series_id": f"series-{fixture_root.name}",
        "run_id": run_id,
        "attempt_id": f"attempt-{fixture_root.name}",
        "correlation_id": f"correlation-{fixture_root.name}",
    }
    write_json(
        guard_dir / "side_effect_ledger.json",
        LifecycleSideEffectLedger(lifecycle_run_id=run_id).model_dump(mode="json"),
    )
    environment = {
        "EVM_PROJECT_ROOT": str(project_root),
        "EVM_CONTROL_PANEL_LEDGER_ROOT": str(short_root / "o"),
        "EVM_KUBERNETES_GENERATED_MANIFEST_ROOT": str(short_root / "g"),
    }
    with temporary_environment(environment):
        task = operations.create_task_assignment(
            TaskAssignmentRequest(
                cycle_id=run_id,
                task_type="kubernetes_job",
                owner="scenario-d-runner",
                priority="high",
                resource_profile="controlled-replay",
                approval_policy="auto",
                config_payload={
                    "adapter": "host-kubectl-bridge",
                    "manifest_dir": str(manifest_dir),
                    "namespace": "evm-training",
                    "job_name": job_name,
                    "timeout_seconds": 60,
                    "delete_existing": True,
                    "lifecycle_run_id": run_id,
                },
                dry_run=False,
            )
        )
        runtime_id = f"evm-training/job/{job_name}"
        task = operations.update_task_runtime(
            task.task_id,
            actor="scenario-d-runner",
            event="controlled_job_admitted_before_worker_exit",
            status="running",
            runtime_system="kubernetes",
            runtime_id=runtime_id,
            runtime_state="running",
        )
        if task is None:
            raise RuntimeError("controlled_task_update_failed")
        run = SimpleNamespace(
            identity_envelope_uri=str(guard_dir / "identity.envelope.json"),
            lifecycle_series_id=identity["lifecycle_series_id"],
            run_id=run_id,
            attempt_id=identity["attempt_id"],
            correlation_id=identity["correlation_id"],
        )
        entry, created = reserve_external_action(
            run,
            "model_training",
            "execute_kubernetes_job",
            {"task_id": task.task_id, "config_payload": task.config_payload},
        )
        if not created:
            raise RuntimeError("controlled_side_effect_reservation_not_created")
        runner = ControlledKubernetesRunner(expected, wrong_candidate=wrong_candidate)
        started = time.monotonic()
        blockers: list[str] = []
        try:
            result = execute_guarded_kubernetes_task(
                run,
                "model_training",
                task,
                runner=runner,
            )
            decision = "pass"
            task_state = result.status
        except LifecycleStageBlocked as exc:
            decision = "blocked"
            blockers = exc.blockers
            observed = next(
                item
                for item in operations.read_tasks().tasks
                if item.task_id == task.task_id
            )
            task_state = observed.status
        ledger = LifecycleSideEffectLedger.model_validate(
            read_json(guard_dir / "side_effect_ledger.json")
        )
    semantic_blockers = [
        item for item in sorted(set(blockers)) if not item.startswith("side_effect_key:")
    ]
    mutating_commands = [
        command
        for command in runner.calls
        if len(command) > 1 and command[1] in {"apply", "delete", "replace", "patch"}
    ]
    return {
        "decision": decision,
        "blockers": sorted(set(blockers)),
        "decision_fingerprint": canonical_digest(
            {"decision": decision, "semantic_blockers": semantic_blockers}
        ),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "task_state": task_state,
        "side_effect_state": ledger.entries[0].state,
        "runtime_id": ledger.entries[0].runtime_id,
        "kubectl_calls": runner.calls,
        "mutating_commands": mutating_commands,
        "external_mutation_performed": False,
        "wrong_candidate": wrong_candidate,
        "side_effect_key": entry.side_effect_key,
    }


def stable_decisions(
    results: list[dict[str, Any]],
    *,
    decision: str,
    blocker: str | None = None,
) -> bool:
    return (
        len(results) == 3
        and all(item["decision"] == decision for item in results)
        and (blocker is None or all(blocker in item.get("blockers", []) for item in results))
        and len({item["decision_fingerprint"] for item in results}) == 1
        and all(float(item["elapsed_seconds"]) <= 30 for item in results)
    )


def run(project_root: Path, golden_root: Path, output_root: Path) -> Path:
    started_at = datetime.now(UTC)
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{u}")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream:
        raise RuntimeError(
            f"lifecycle_scenario_d_source_preflight_failed:dirty={dirty}:"
            f"head={head}:upstream={upstream}"
        )
    lifecycle = read_json(golden_root / "lifecycle_run.json")
    if lifecycle.get("state") != "completed":
        raise RuntimeError("golden_lifecycle_not_completed")
    golden_ledger = golden_root / "side_effect_ledger.json"
    ledger = LifecycleSideEffectLedger.model_validate(read_json(golden_ledger))
    if len(ledger.entries) != 8 or any(entry.state != "completed" for entry in ledger.entries):
        raise RuntimeError("golden_side_effect_ledger_not_closed")
    run_id = f"scenario-d-lifecycle-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    write_json(
        run_root / "execution-contract.json",
        {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "source_revision": head,
            "golden_run_id": lifecycle["run_id"],
            "mode": "non_disruptive_side_effect_and_exact_observation_replay",
            "production_mutation_allowed": False,
            "process_mutation_allowed": False,
            "replays_per_decision": 3,
            "decision_slo_seconds": 30,
            "started_at": started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
    )
    before_runtime = production_snapshot(DEFAULT_INFERENCE_IMAGE_URI)
    before_effects = external_side_effect_snapshot()
    golden_hashes = {str(golden_ledger.resolve()): file_digest(golden_ledger)}
    write_json(run_root / "pre-runtime.json", before_runtime)
    write_json(run_root / "pre-side-effects.json", before_effects)

    terminal_root = run_root / "ledger-branches" / "terminal-idempotent"
    terminal_root.mkdir(parents=True)
    shutil.copy2(golden_ledger, terminal_root / "side_effect_ledger.json")
    terminal_results = replay_terminal_ledger(terminal_root)

    first = ledger.entries[0]
    identity = {
        "lifecycle_series_id": first.lifecycle_series_id,
        "run_id": first.lifecycle_run_id,
        "attempt_id": first.attempt_id,
        "correlation_id": first.correlation_id,
    }
    duplicate_root = run_root / "ledger-branches" / "duplicate-key"
    duplicate_root.mkdir(parents=True)
    duplicate_payload = read_json(golden_ledger)
    duplicate_payload["entries"].append(copy.deepcopy(duplicate_payload["entries"][0]))
    write_json(duplicate_root / "side_effect_ledger.json", duplicate_payload)
    duplicate_results = replay_invalid_ledger(duplicate_root, identity=identity)

    wrong_run_root = run_root / "ledger-branches" / "wrong-run-identity"
    wrong_run_root.mkdir(parents=True)
    wrong_run_payload = read_json(golden_ledger)
    wrong_run_payload["entries"][0]["lifecycle_run_id"] = "wrong-run"
    write_json(wrong_run_root / "side_effect_ledger.json", wrong_run_payload)
    wrong_run_results = replay_invalid_ledger(wrong_run_root, identity=identity)

    state_root = run_root / "ledger-branches" / "terminal-state-regression"
    state_root.mkdir(parents=True)
    shutil.copy2(golden_ledger, state_root / "side_effect_ledger.json")
    state_results = replay_terminal_state_regression(
        state_root,
        side_effect_key=first.side_effect_key,
    )

    exact_results: list[dict[str, Any]] = []
    mismatch_results: list[dict[str, Any]] = []
    for replay in range(1, 4):
        exact = run_exact_reconciliation_fixture(
            run_root / "exact-observation" / f"replay-{replay}",
            project_root=project_root,
            source_revision=head,
            wrong_candidate=False,
        )
        exact["replay"] = replay
        write_json(run_root / "exact-observation" / f"replay-{replay}.json", exact)
        exact_results.append(exact)
        mismatch = run_exact_reconciliation_fixture(
            run_root / "wrong-observation" / f"replay-{replay}",
            project_root=project_root,
            source_revision=head,
            wrong_candidate=True,
        )
        mismatch["replay"] = replay
        write_json(run_root / "wrong-observation" / f"replay-{replay}.json", mismatch)
        mismatch_results.append(mismatch)

    after_runtime = production_snapshot(DEFAULT_INFERENCE_IMAGE_URI)
    after_effects = external_side_effect_snapshot()
    write_json(run_root / "post-runtime.json", after_runtime)
    write_json(run_root / "post-side-effects.json", after_effects)
    invariants = invariant_diff(
        before_runtime,
        after_runtime,
        before_effects,
        after_effects,
        golden_hashes,
        {str(golden_ledger.resolve()): file_digest(golden_ledger)},
    )
    write_json(run_root / "invariant-diff.json", invariants)

    checks = {
        "golden_terminal_ledger_three_replays": (
            len(terminal_results) == 3
            and all(item["decision"] == "pass" for item in terminal_results)
            and len({item["decision_fingerprint"] for item in terminal_results}) == 1
            and len({item["ledger_digest"] for item in terminal_results}) == 1
        ),
        "duplicate_key_three_replays_blocked": stable_decisions(
            duplicate_results,
            decision="blocked",
            blocker="side_effect_ledger_invalid",
        ),
        "wrong_run_three_replays_blocked": stable_decisions(
            wrong_run_results,
            decision="blocked",
            blocker="side_effect_ledger_invalid",
        ),
        "terminal_state_regression_three_replays_blocked": stable_decisions(
            state_results,
            decision="blocked",
            blocker="side_effect_state_transition_invalid:completed:reconciled",
        ),
        "exact_observation_three_replays_resumed": (
            len(exact_results) == 3
            and all(item["decision"] == "pass" for item in exact_results)
            and all(item["task_state"] == "done" for item in exact_results)
            and all(item["side_effect_state"] == "completed" for item in exact_results)
            and all(not item["mutating_commands"] for item in exact_results)
        ),
        "wrong_observation_three_replays_blocked": (
            stable_decisions(
                mismatch_results,
                decision="blocked",
                blocker="kubernetes_reconciliation_label_identity_mismatch",
            )
            and all(item["task_state"] == "running" for item in mismatch_results)
            and all(item["side_effect_state"] == "reserved" for item in mismatch_results)
            and all(not item["mutating_commands"] for item in mismatch_results)
        ),
        "runtime_and_external_side_effect_invariants": bool(invariants["passed"]),
    }
    result = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "source_revision": head,
        "golden_run_id": lifecycle["run_id"],
        "started_at": started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "finished_at": utc_now(),
        "checks": checks,
        "status": "pass" if all(checks.values()) else "blocked",
        "claim_boundary": (
            "Single-node local controlled side-effect and exact observation replay; "
            "live supervised child recovery is a separate proof and distributed exactly-once, "
            "HA, or production traffic are not claimed."
        ),
    }
    result_path = run_root / "result.json"
    result["evidence_index_uri"] = str((run_root / "evidence-index.json").resolve())
    write_json(result_path, result)
    build_evidence_index(run_root)
    if result["status"] != "pass":
        raise RuntimeError(f"lifecycle_scenario_d_acceptance_failed:{checks}")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Scenario D side-effect continuity across lifecycle boundaries."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--golden-run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    result = run(
        args.project_root.resolve(),
        args.golden_run_root.resolve(),
        args.output_root.resolve(),
    )
    print(json.dumps({"result_uri": str(result.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

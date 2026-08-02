from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from evm.operations.correlation import (
    CorrelationPolicy,
    CorrelationStore,
    KubernetesSubject,
    NormalizedEvent,
    StrictModel,
    load_policy,
    stable_digest,
)
from evm.operations.failure_evidence import sha256_file
from evm.operations.failure_scenarios import atomic_write_json


class ReplaySeriesResult(StrictModel):
    schema_version: Literal["evm.cross_scenario_replay_series.v1"]
    series_id: str
    event_count: int = Field(gt=0)
    unrelated_event_count: int = Field(ge=0)
    expected_incident_count: int = Field(gt=0)
    observed_incident_count: int = Field(gt=0)
    expected_action_count: int = Field(gt=0)
    observed_action_count: int = Field(ge=0)
    primary_duplicate_count: int = Field(ge=0)
    false_merge_count: int = Field(ge=0)
    duplicate_parent_count: int = Field(ge=0)
    duplicate_action_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    held_count: int = Field(ge=0)
    coordinator_restart_count: int = Field(ge=1)
    coordinator_overhead_p95_ms: float = Field(ge=0)
    passed: bool


class ReplayProofResult(StrictModel):
    schema_version: Literal["evm.cross_scenario_replay_proof.v1"]
    source_revision: str
    policy_version: str
    series: list[ReplaySeriesResult] = Field(min_length=1)
    total_events: int = Field(gt=0)
    total_unrelated_events: int = Field(ge=0)
    false_merge_count: int = Field(ge=0)
    duplicate_parent_count: int = Field(ge=0)
    duplicate_action_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    held_count: int = Field(ge=0)
    passed: bool
    generated_at: datetime


def _event(
    *,
    sequence: int,
    target_index: int,
    source_revision: str,
    policy: CorrelationPolicy,
    observed_at: datetime,
    series_id: str,
) -> tuple[NormalizedEvent, dict[str, Any]]:
    target_uid = f"uid-{target_index:08d}"
    raw_evidence = {
        "series_id": series_id,
        "sequence": sequence,
        "target_uid": target_uid,
        "observed_at": observed_at.isoformat(),
        "sample": "controlled-replay",
    }
    decision_inputs = {
        "cause": "target_readiness_changed",
        "target_uid": target_uid,
        "expected_state": "ready",
    }
    binding_digest = stable_digest({"series_id": series_id, "dataset": "visa"})
    subject = KubernetesSubject(
        lifecycle_series_id=series_id,
        lifecycle_run_id=f"{series_id}-golden",
        attempt_id="golden-g3",
        bindings={
            "dataset_version": "visa-open-data-e35d93d5561f",
            "lifecycle_binding": binding_digest,
        },
        cluster="docker-desktop",
        namespace="evm-production",
        resource_kind="Deployment",
        name="evm-b0-production",
        uid=target_uid,
        pod_uid=f"pod-{target_index:08d}",
        container_name="efficientnet-serving",
        image_digest=f"sha256:{'2' * 64}",
        expected_replica_identity="replica-1-of-1",
    )
    event = NormalizedEvent(
        schema_version="evm.cross_scenario_event.v1",
        event_id=f"evt-{series_id}-{sequence:08d}",
        scenario_id="A",
        event_type="serving_readiness",
        cause_code="target_readiness_changed",
        severity="warning",
        observed_at_utc=observed_at,
        monotonic_elapsed_ms=sequence * policy.collector_cadence_ms,
        collector_cadence_ms=policy.collector_cadence_ms,
        fresh_until_utc=observed_at + timedelta(seconds=policy.freshness_seconds),
        producer_boot_id=f"boot-{series_id}",
        producer_sequence=sequence + 1,
        source_component="correlation-coordinator",
        source_revision=source_revision,
        policy_version=policy.policy_version,
        evidence_digest=stable_digest(raw_evidence),
        semantic_identity_digest=stable_digest(decision_inputs),
        decision_inputs=decision_inputs,
        subject_identity=subject,
        target_match_count=1,
        actor_or_controller="cross-scenario-replay",
        recommended_action="recommend",
    )
    return event, raw_evidence


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def run_series(
    *,
    root: Path,
    series_id: str,
    policy: CorrelationPolicy,
    source_revision: str,
    event_count: int,
    unrelated_event_count: int,
    observed_at: datetime,
) -> ReplaySeriesResult:
    if event_count <= unrelated_event_count or unrelated_event_count < 1:
        raise ValueError("replay requires a primary stream plus unrelated events")
    store = CorrelationStore(root, policy)
    primary_count = event_count - unrelated_event_count
    primary_incident_id: str | None = None
    negative_incidents: set[str] = set()
    outcomes: list[str] = []
    overhead_ms: list[float] = []
    restart_count = 0

    for index in range(event_count):
        if index == event_count // 2:
            store = CorrelationStore(root, policy)
            restart_count += 1
        target_index = 0 if index < primary_count else index - primary_count + 1
        event, raw = _event(
            sequence=index,
            target_index=target_index,
            source_revision=source_revision,
            policy=policy,
            observed_at=observed_at,
            series_id=series_id,
        )
        started = time.perf_counter_ns()
        decision = store.ingest(
            event,
            raw_evidence=raw,
            ingested_at=observed_at + timedelta(seconds=1),
            coordinator_monotonic_elapsed_ms=index,
        )
        overhead_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        outcomes.append(decision.outcome)
        if target_index == 0:
            primary_incident_id = primary_incident_id or decision.incident_id
            if decision.incident_id != primary_incident_id:
                negative_incidents.add(decision.incident_id)
        else:
            negative_incidents.add(decision.incident_id)

    state = store.snapshot()
    expected_incidents = unrelated_event_count + 1
    observed_incidents = len(set(state.root_index.values()))
    expected_actions = expected_incidents
    observed_actions = len(state.action_index)
    false_merges = max(0, unrelated_event_count - len(negative_incidents))
    duplicate_parents = max(0, observed_incidents - expected_incidents)
    action_lines = _line_count(root / "action-ledger.jsonl")
    duplicate_actions = max(0, action_lines - observed_actions)
    blocked_count = outcomes.count("blocked")
    held_count = outcomes.count("held")
    primary_record = next(
        record
        for record in state.dedupe.values()
        if record.incident_id == primary_incident_id
    )
    passed = all(
        (
            observed_incidents == expected_incidents,
            observed_actions == expected_actions,
            primary_record.count == primary_count,
            false_merges == 0,
            duplicate_parents == 0,
            duplicate_actions == 0,
            blocked_count == 0,
            held_count == 0,
            restart_count == 1,
        )
    )
    result = ReplaySeriesResult(
        schema_version="evm.cross_scenario_replay_series.v1",
        series_id=series_id,
        event_count=event_count,
        unrelated_event_count=unrelated_event_count,
        expected_incident_count=expected_incidents,
        observed_incident_count=observed_incidents,
        expected_action_count=expected_actions,
        observed_action_count=observed_actions,
        primary_duplicate_count=primary_record.count,
        false_merge_count=false_merges,
        duplicate_parent_count=duplicate_parents,
        duplicate_action_count=duplicate_actions,
        blocked_count=blocked_count,
        held_count=held_count,
        coordinator_restart_count=restart_count,
        coordinator_overhead_p95_ms=_percentile_95(overhead_ms),
        passed=passed,
    )
    atomic_write_json(root / "validation-report.json", result.model_dump(mode="json"))
    return result


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _artifact_index(root: Path) -> dict[str, Any]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "artifact-index.json" or path.suffix == ".lock":
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return {
        "schema_version": "evm.cross_scenario_artifact_index.v1",
        "file_count": len(files),
        "files": files,
    }


def run_proof(
    *,
    output: Path,
    policy: CorrelationPolicy,
    source_revision: str,
    series_count: int = 3,
    event_count: int = 1_000,
    unrelated_event_count: int = 100,
    observed_at: datetime | None = None,
) -> ReplayProofResult:
    root_time = observed_at or datetime.now(UTC).replace(microsecond=0)
    results = []
    for index in range(series_count):
        results.append(
            run_series(
                root=output / f"series-{index + 1}",
                series_id=f"correlation-replay-{index + 1}",
                policy=policy,
                source_revision=source_revision,
                event_count=event_count,
                unrelated_event_count=unrelated_event_count,
                observed_at=root_time + timedelta(minutes=index),
            )
        )
    proof = ReplayProofResult(
        schema_version="evm.cross_scenario_replay_proof.v1",
        source_revision=source_revision,
        policy_version=policy.policy_version,
        series=results,
        total_events=sum(item.event_count for item in results),
        total_unrelated_events=sum(item.unrelated_event_count for item in results),
        false_merge_count=sum(item.false_merge_count for item in results),
        duplicate_parent_count=sum(item.duplicate_parent_count for item in results),
        duplicate_action_count=sum(item.duplicate_action_count for item in results),
        blocked_count=sum(item.blocked_count for item in results),
        held_count=sum(item.held_count for item in results),
        passed=all(item.passed for item in results),
        generated_at=datetime.now(UTC),
    )
    atomic_write_json(output / "validation-report.json", proof.model_dump(mode="json"))
    atomic_write_json(output / "artifact-index.json", _artifact_index(output))
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description="Run cross-scenario correlation replay proof.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--series-count", type=int, default=3)
    parser.add_argument("--event-count", type=int, default=1_000)
    parser.add_argument("--unrelated-event-count", type=int, default=100)
    args = parser.parse_args()
    policy = load_policy(args.policy, revision=args.source_revision)
    proof = run_proof(
        output=args.output,
        policy=policy,
        source_revision=args.source_revision,
        series_count=args.series_count,
        event_count=args.event_count,
        unrelated_event_count=args.unrelated_event_count,
    )
    print(json.dumps(proof.model_dump(mode="json"), indent=2, default=str))
    return 0 if proof.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

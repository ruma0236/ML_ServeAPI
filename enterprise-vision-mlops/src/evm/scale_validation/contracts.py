from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROGRESS_SCHEMA_VERSION = "evm.scale_validation.progress.v1"
BENCHMARK_SCHEMA_VERSION = "evm.scale_validation.benchmark_evidence.v1"
PRIVATE_INDEX_SCHEMA_VERSION = "evm.scale_validation.private_evidence_index.v1"
SHA256_PATTERN = r"^[a-f0-9]{64}$"
SOURCE_REVISION_PATTERN = r"^[a-f0-9]{7,64}$"

SCENARIO_TITLES = {
    "S0": "Runtime Baseline & Evidence Contract",
    "S1": "Transactional Job State & Idempotency",
    "S2": "Bounded Queue & Backpressure",
    "S3": "HIGGS Lightweight Capacity Envelope",
    "S4": "HIGGS Tiny MLP GPU Batching",
    "S5": "Criteo Spark Memory-bounded Data Scale",
    "S6": "API Rolling Continuity & GPU Controlled Handoff",
    "S7": "Image/VLM/LLM Auxiliary Admission",
    "S8": "Dependency Soak & Resource-efficiency Closure",
}


def _require_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def _require_repo_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("public artifact paths must be repository-relative")
    path = PurePosixPath(normalized)
    if ".." in path.parts or "." in path.parts:
        raise ValueError("public artifact paths cannot traverse directories")
    if "://" in normalized:
        raise ValueError("public artifact paths cannot be URLs")
    return normalized


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceArtifact(StrictModel):
    path: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    generated_at: datetime
    claim: str = Field(min_length=1)

    _validate_path = field_validator("path")(_require_repo_relative_path)
    _validate_generated_at = field_validator("generated_at")(_require_utc)


class ChangedComponent(StrictModel):
    component: str = Field(min_length=1)
    files: list[str] = Field(min_length=1)

    @field_validator("files")
    @classmethod
    def validate_files(cls, values: list[str]) -> list[str]:
        normalized = [_require_repo_relative_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("changed component files must be unique")
        return normalized


class AcceptanceCriterion(StrictModel):
    criterion_id: str = Field(pattern=r"^S[0-8]-AC-[0-9]{2}$")
    description: str = Field(min_length=1)
    status: Literal["pending", "passed", "failed", "blocked"]
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, values: list[str]) -> list[str]:
        return [_require_repo_relative_path(value) for value in values]


class ScenarioProgress(StrictModel):
    scenario_id: Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
    title: str = Field(min_length=1)
    engineering_question: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    observed_gap: str = Field(min_length=1)
    proposed_design: list[str] = Field(min_length=1)
    changed_components: list[ChangedComponent]
    implementation_summary: list[str] = Field(min_length=1)
    experiment_environment: str = Field(min_length=1)
    test_or_experiment_steps: list[str] = Field(min_length=1)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    observed_result: str | None
    evidence_artifacts: list[EvidenceArtifact]
    status: Literal["planned", "implementing", "exercised", "verified", "blocked"]
    claim_boundary: str = Field(min_length=1)
    unresolved_items: list[str]
    next_action: str = Field(min_length=1)
    architecture_before: str = Field(min_length=1)
    architecture_after: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_progress_state(self) -> "ScenarioProgress":
        expected_title = SCENARIO_TITLES[self.scenario_id]
        if self.title != expected_title:
            raise ValueError(f"{self.scenario_id} title must remain '{expected_title}'")

        expected_prefix = f"{self.scenario_id}-AC-"
        criterion_ids = [item.criterion_id for item in self.acceptance_criteria]
        if any(not item.startswith(expected_prefix) for item in criterion_ids):
            raise ValueError("acceptance criterion IDs must belong to the scenario")
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("acceptance criterion IDs must be unique")

        evidence_paths = {item.path for item in self.evidence_artifacts}
        referenced_paths = {
            path for criterion in self.acceptance_criteria for path in criterion.evidence_refs
        }
        if not referenced_paths.issubset(evidence_paths):
            raise ValueError("acceptance evidence_refs must resolve to declared evidence artifacts")

        if self.status == "planned" and (self.observed_result or self.evidence_artifacts):
            raise ValueError("planned scenarios cannot contain execution results or evidence")
        if self.status == "exercised" and not (self.observed_result and self.evidence_artifacts):
            raise ValueError("exercised scenarios require a result and evidence")
        if self.status == "verified":
            if not self.observed_result or not self.evidence_artifacts:
                raise ValueError("verified scenarios require a result and evidence")
            if any(item.status != "passed" for item in self.acceptance_criteria):
                raise ValueError("verified scenarios require every acceptance criterion to pass")
            if any(not item.evidence_refs for item in self.acceptance_criteria):
                raise ValueError("verified acceptance criteria require evidence references")
        if self.status == "blocked" and not self.unresolved_items:
            raise ValueError("blocked scenarios must state unresolved items")
        return self


class ScenarioProgressLedger(StrictModel):
    schema_version: Literal["evm.scale_validation.progress.v1"]
    generated_at: datetime
    authoritative_plan: str
    public_record: Literal[True]
    claim_boundary: str = Field(min_length=1)
    scenarios: list[ScenarioProgress] = Field(min_length=9, max_length=9)

    _validate_generated_at = field_validator("generated_at")(_require_utc)
    _validate_authoritative_plan = field_validator("authoritative_plan")(
        _require_repo_relative_path
    )

    @model_validator(mode="after")
    def validate_scenario_set(self) -> "ScenarioProgressLedger":
        scenario_ids = [item.scenario_id for item in self.scenarios]
        if scenario_ids != list(SCENARIO_TITLES):
            raise ValueError("scenarios must contain S0 through S8 in authoritative order")
        return self


class PrivateEvidenceEntry(StrictModel):
    scenario_id: Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
    evidence_id: str = Field(min_length=1)
    absolute_path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    generated_at: datetime
    claim: str = Field(min_length=1)
    status: Literal["captured", "accepted", "rejected", "superseded"]

    _validate_generated_at = field_validator("generated_at")(_require_utc)


class PrivateEvidenceIndex(StrictModel):
    schema_version: Literal["evm.scale_validation.private_evidence_index.v1"]
    generated_at: datetime
    authoritative_plan: str
    evidence_root: str = Field(min_length=1)
    entries: list[PrivateEvidenceEntry]

    _validate_generated_at = field_validator("generated_at")(_require_utc)
    _validate_authoritative_plan = field_validator("authoritative_plan")(
        _require_repo_relative_path
    )


class BenchmarkIdentity(StrictModel):
    source_revision: str = Field(pattern=SOURCE_REVISION_PATTERN)
    data_digest: str = Field(pattern=SHA256_PATTERN)
    model_digest: str = Field(pattern=SHA256_PATTERN)
    runtime_digest: str = Field(pattern=SHA256_PATTERN)


class BenchmarkEnvironment(StrictModel):
    physical_nodes: int = Field(ge=1)
    cpu_logical_count: int = Field(ge=1)
    ram_gib: float = Field(gt=0)
    gpu_count: int = Field(ge=0)
    gpu_class: str = Field(min_length=1)
    load_generator_placement: Literal["co_located", "external"]
    environment_scope: Literal["local_single_node", "local_multi_host"]


class LoadProfile(StrictModel):
    mode: Literal["low_load_control", "closed_model", "open_model", "soak"]
    concurrency: int = Field(ge=1)
    target_requests_per_second: float = Field(gt=0)
    warmup_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    seed: int = Field(ge=0)


class MetricObservation(StrictModel):
    metric: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    sample_count: int = Field(ge=1)
    statistics: dict[str, float] = Field(min_length=1)
    query: str = Field(min_length=1)

    @field_validator("statistics")
    @classmethod
    def validate_statistics(cls, values: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("metric statistics must be finite")
        return values


class BenchmarkControlRun(StrictModel):
    repetition: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
    metrics: list[MetricObservation] = Field(min_length=1)
    evidence_artifacts: list[EvidenceArtifact] = Field(min_length=1)

    _validate_started_at = field_validator("started_at")(_require_utc)
    _validate_finished_at = field_validator("finished_at")(_require_utc)

    @model_validator(mode="after")
    def validate_timing(self) -> "BenchmarkControlRun":
        if self.finished_at <= self.started_at:
            raise ValueError("benchmark run must finish after it starts")
        metric_names = [item.metric for item in self.metrics]
        if len(set(metric_names)) != len(metric_names):
            raise ValueError("benchmark metric names must be unique within a repetition")
        return self


class TracePropagationEvidence(StrictModel):
    required_stages: list[str] = Field(min_length=1)
    observed_stages: list[str] = Field(min_length=1)
    missing_propagation_count: int = Field(ge=0)
    metric_labels_bounded: bool
    trace_artifact: EvidenceArtifact

    @model_validator(mode="after")
    def validate_stage_identity(self) -> "TracePropagationEvidence":
        if len(set(self.required_stages)) != len(self.required_stages):
            raise ValueError("required trace stages must be unique")
        if len(set(self.observed_stages)) != len(self.observed_stages):
            raise ValueError("observed trace stages must be unique")
        return self


class BenchmarkClosure(StrictModel):
    decision: Literal["passed", "failed", "blocked", "not_run"]
    blockers: list[str]
    completed_at: datetime | None

    _validate_completed_at = field_validator("completed_at")(_require_utc)

    @model_validator(mode="after")
    def validate_decision(self) -> "BenchmarkClosure":
        if self.decision == "passed" and (self.blockers or self.completed_at is None):
            raise ValueError("passed benchmark closure requires completion and no blockers")
        if self.decision != "passed" and not self.blockers:
            raise ValueError("non-passed benchmark closure requires blockers")
        return self


class BenchmarkEvidence(StrictModel):
    schema_version: Literal["evm.scale_validation.benchmark_evidence.v1"]
    scenario_id: Literal["S0"]
    benchmark_suite_id: str = Field(min_length=1)
    generated_at: datetime
    identity: BenchmarkIdentity
    environment: BenchmarkEnvironment
    load_profile: LoadProfile
    control_runs: list[BenchmarkControlRun]
    trace_propagation: TracePropagationEvidence
    variance: dict[str, float]
    closure: BenchmarkClosure

    _validate_generated_at = field_validator("generated_at")(_require_utc)

    @model_validator(mode="after")
    def validate_passed_baseline(self) -> "BenchmarkEvidence":
        if self.closure.decision != "passed":
            return self
        if len(self.control_runs) < 3:
            raise ValueError("passed S0 baseline requires at least three independent control runs")
        repetitions = [item.repetition for item in self.control_runs]
        if len(set(repetitions)) != len(repetitions):
            raise ValueError("control run repetitions must be unique")
        required_metrics = {
            "request_latency_seconds",
            "request_throughput_per_second",
            "queue_depth",
            "queue_oldest_age_seconds",
            "worker_active_count",
            "cpu_utilization_ratio",
            "memory_working_set_bytes",
            "gpu_utilization_ratio",
            "gpu_memory_used_bytes",
            "connection_pool_wait_seconds",
            "retry_attempt_total",
        }
        for run in self.control_runs:
            by_name = {item.metric: item for item in run.metrics}
            missing = required_metrics - by_name.keys()
            if missing:
                raise ValueError(f"passed S0 baseline is missing metrics: {sorted(missing)}")
            latency_keys = by_name["request_latency_seconds"].statistics.keys()
            if not {"p50", "p95", "p99"}.issubset(latency_keys):
                raise ValueError("request latency requires p50, p95 and p99")
        required_variance = {"request_latency_p95_cv", "request_throughput_cv"}
        if not required_variance.issubset(self.variance):
            raise ValueError("passed S0 baseline requires latency and throughput variance")
        if any(not math.isfinite(value) or value < 0 for value in self.variance.values()):
            raise ValueError("benchmark variance values must be finite and non-negative")
        required_trace_stages = {"api", "queue", "worker", "spark", "mlflow", "serving"}
        observed_stages = set(self.trace_propagation.observed_stages)
        if not required_trace_stages.issubset(self.trace_propagation.required_stages):
            raise ValueError("S0 trace contract must require every lifecycle stage")
        if not required_trace_stages.issubset(observed_stages):
            raise ValueError("passed S0 baseline requires observed end-to-end trace stages")
        if self.trace_propagation.missing_propagation_count != 0:
            raise ValueError("passed S0 baseline requires zero missing trace propagation")
        if not self.trace_propagation.metric_labels_bounded:
            raise ValueError("passed S0 baseline requires bounded metric labels")
        return self


def render_progress_markdown(ledger: ScenarioProgressLedger) -> str:
    lines = [
        "# Distributed Scale Scenario Progress",
        "",
        f"- Schema: `{ledger.schema_version}`",
        f"- Generated: `{ledger.generated_at.isoformat().replace('+00:00', 'Z')}`",
        f"- Authoritative plan: `{ledger.authoritative_plan}`",
        f"- Claim boundary: {ledger.claim_boundary}",
        "",
        "Only a scenario with passed acceptance criteria and hashed evidence may be `verified`.",
        "",
    ]
    for scenario in ledger.scenarios:
        lines.extend(
            [
                f"## {scenario.scenario_id}: {scenario.title}",
                "",
                f"- Status: `{scenario.status}`",
                f"- Engineering question: {scenario.engineering_question}",
                f"- Why now: {scenario.why_now}",
                f"- Observed gap: {scenario.observed_gap}",
                f"- Architecture before: {scenario.architecture_before}",
                f"- Architecture after: {scenario.architecture_after}",
                f"- Claim boundary: {scenario.claim_boundary}",
                f"- Next action: {scenario.next_action}",
                "",
                "### Proposed Design",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in scenario.proposed_design)
        lines.extend(["", "### Acceptance", ""])
        lines.extend(
            f"- `{item.criterion_id}` [{item.status}]: {item.description}"
            for item in scenario.acceptance_criteria
        )
        lines.extend(["", "### Current Evidence", ""])
        if scenario.evidence_artifacts:
            lines.extend(
                f"- `{item.path}` (`{item.sha256}`): {item.claim}"
                for item in scenario.evidence_artifacts
            )
        else:
            lines.append("- No accepted execution evidence yet.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

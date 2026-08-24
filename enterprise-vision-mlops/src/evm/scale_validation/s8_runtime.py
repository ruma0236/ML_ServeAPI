from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

import psutil

from evm.control_panel.admission_queue import AdmissionQueueConfig
from evm.scale_validation.evidence import write_public_json
from evm.scale_validation.s1_runtime import (
    canonical_write,
    materialize_isolated_profile,
    schema_identifier,
    sha256_file,
    source_revision,
    utc_now,
)
from evm.scale_validation.s2_runtime import (
    FULL_TRACE_NAMES,
    TIMEOUT_FAILURE_TRACE_NAMES,
    RuntimeScope,
    accepted_tasks,
    assertion,
    create_runtime_scope,
    finalize_profile_scope,
    marker_processes,
    payload_digest,
    port_is_available,
    private_evidence_index,
    profile_payloads,
    run_profile_i,
    start_worker_and_monitoring,
    submit_payloads,
    wait_for_terminal,
)
from evm.scale_validation.s3_runtime import (
    S3LoadPoint,
    S3RuntimeConfig,
    run_capacity_point,
    verify_runtime_identity,
)


SCHEMA_VERSION = "evm.s8_dependency_soak_experiment.v1"
FAULT_PROFILE_IDS = (
    "control",
    "latency",
    "transient",
    "retry-budget",
    "poison",
    "timeout",
    "worker-loss",
)
CLAIM_BOUNDARY = (
    "Controlled external HTTP, PostgreSQL, process, Prometheus, OTLP, lightweight "
    "CPU/API, and one-CUDA-device evidence on one local physical node. No customer "
    "traffic, production SLA, physical multi-node or multi-zone HA/DR, multi-GPU, "
    "or simultaneous multi-model GPU residency claim."
)


class S8RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class FaultMatrix:
    version: str
    seed: int
    repetitions: int
    warmup_seconds: float
    sample_interval_seconds: float
    drain_timeout_seconds: float
    trace_flush_seconds: float
    prometheus_scrape_interval_seconds: float
    profiles: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class S8RuntimeConfig:
    path: Path
    sha256: str
    schema_version: str
    seed: int
    repetitions: int
    fault_sample_interval_seconds: float
    fault_drain_timeout_seconds: float
    trace_flush_seconds: float
    prometheus_scrape_interval_seconds: float
    soak_probe_family: str
    soak_requests_per_second: float
    capacity_reference_requests_per_second: float
    capacity_fraction: float
    soak_api_replicas: int
    soak_cpu_workers: int
    soak_warmup_seconds: float
    soak_measurement_seconds: float
    soak_cooldown_seconds: float
    soak_resource_sample_interval_seconds: float
    maximum_retry_amplification: float
    maximum_error_rate: float
    maximum_p99_ms: float
    maximum_rss_slope_bytes_per_minute: float
    maximum_fd_slope_per_minute: float
    maximum_queue_slope_items_per_minute: float
    maximum_artifact_slope_bytes_per_minute: float
    maximum_mttr_seconds: float
    retry_transient_request_count: int
    retry_healthy_request_count: int

    @classmethod
    def from_path(cls, path: Path) -> "S8RuntimeConfig":
        resolved = path.resolve()
        raw = resolved.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8"))
        experiment = section(payload, "experiment")
        soak = section(payload, "soak")
        faults = section(payload, "faults")
        retry_budget = section(faults, "retry_budget")
        guardrails = section(payload, "guardrails")
        config = cls(
            path=resolved,
            sha256=hashlib.sha256(raw).hexdigest(),
            schema_version=str(experiment["schema_version"]),
            seed=int(experiment["seed"]),
            repetitions=int(experiment["repetitions"]),
            fault_sample_interval_seconds=float(
                experiment["fault_sample_interval_seconds"]
            ),
            fault_drain_timeout_seconds=float(
                experiment["fault_drain_timeout_seconds"]
            ),
            trace_flush_seconds=float(experiment["trace_flush_seconds"]),
            prometheus_scrape_interval_seconds=float(
                experiment["prometheus_scrape_interval_seconds"]
            ),
            soak_probe_family=str(soak["probe_family"]),
            soak_requests_per_second=float(soak["requests_per_second"]),
            capacity_reference_requests_per_second=float(
                soak["capacity_reference_requests_per_second"]
            ),
            capacity_fraction=float(soak["capacity_fraction"]),
            soak_api_replicas=int(soak["api_replicas"]),
            soak_cpu_workers=int(soak["cpu_workers"]),
            soak_warmup_seconds=float(soak["warmup_seconds"]),
            soak_measurement_seconds=float(soak["measurement_seconds"]),
            soak_cooldown_seconds=float(soak["cooldown_seconds"]),
            soak_resource_sample_interval_seconds=float(
                soak["resource_sample_interval_seconds"]
            ),
            maximum_retry_amplification=float(
                guardrails["maximum_retry_amplification"]
            ),
            maximum_error_rate=float(guardrails["maximum_error_rate"]),
            maximum_p99_ms=float(guardrails["maximum_p99_ms"]),
            maximum_rss_slope_bytes_per_minute=float(
                guardrails["maximum_rss_slope_bytes_per_minute"]
            ),
            maximum_fd_slope_per_minute=float(
                guardrails["maximum_fd_slope_per_minute"]
            ),
            maximum_queue_slope_items_per_minute=float(
                guardrails["maximum_queue_slope_items_per_minute"]
            ),
            maximum_artifact_slope_bytes_per_minute=float(
                guardrails["maximum_artifact_slope_bytes_per_minute"]
            ),
            maximum_mttr_seconds=float(guardrails["maximum_mttr_seconds"]),
            retry_transient_request_count=int(
                retry_budget["transient_request_count"]
            ),
            retry_healthy_request_count=int(retry_budget["healthy_request_count"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.schema_version != "evm.s8_dependency_soak_config.v1":
            raise S8RuntimeError("s8_config_schema_invalid")
        if self.seed <= 0 or self.repetitions != 3:
            raise S8RuntimeError("s8_requires_fixed_seed_and_three_repetitions")
        expected = (
            self.capacity_reference_requests_per_second * self.capacity_fraction
        )
        if not math.isclose(
            self.soak_requests_per_second, expected, rel_tol=0, abs_tol=0.02
        ):
            raise S8RuntimeError("s8_soak_rate_is_not_seventy_percent_capacity")
        if not 1800 <= self.soak_measurement_seconds <= 3600:
            raise S8RuntimeError("s8_soak_measurement_must_be_30_to_60_minutes")
        positive = (
            self.fault_sample_interval_seconds,
            self.fault_drain_timeout_seconds,
            self.trace_flush_seconds,
            self.prometheus_scrape_interval_seconds,
            self.soak_warmup_seconds,
            self.soak_cooldown_seconds,
            self.soak_resource_sample_interval_seconds,
            self.maximum_retry_amplification,
            self.maximum_p99_ms,
            self.maximum_rss_slope_bytes_per_minute,
            self.maximum_fd_slope_per_minute,
            self.maximum_queue_slope_items_per_minute,
            self.maximum_artifact_slope_bytes_per_minute,
            self.maximum_mttr_seconds,
        )
        if min(positive) <= 0 or not 0 < self.capacity_fraction < 1:
            raise S8RuntimeError("s8_positive_frozen_bound_invalid")
        if not 0 <= self.maximum_error_rate < 1:
            raise S8RuntimeError("s8_error_guardrail_invalid")
        if self.retry_transient_request_count != 12 or self.retry_healthy_request_count != 4:
            raise S8RuntimeError("s8_retry_budget_workload_contract_invalid")

    def fault_matrix(self) -> FaultMatrix:
        return FaultMatrix(
            version="s8-isolated-faults-v5-20260824",
            seed=self.seed,
            repetitions=self.repetitions,
            warmup_seconds=2.0,
            sample_interval_seconds=self.fault_sample_interval_seconds,
            drain_timeout_seconds=self.fault_drain_timeout_seconds,
            trace_flush_seconds=self.trace_flush_seconds,
            prometheus_scrape_interval_seconds=self.prometheus_scrape_interval_seconds,
            profiles={
                "control": {"name": "no-fault-control"},
                "latency": {"name": "bounded-dependency-latency"},
                "transient": {"name": "transient-recovery"},
                "retry-budget": {
                    "name": "retry-budget-and-circuit-recovery",
                    "transient_request_count": self.retry_transient_request_count,
                    "healthy_request_count": self.retry_healthy_request_count,
                },
                "poison": {"name": "poison-dlq-and-healthy-hol"},
                "timeout": {"name": "dependency-timeout-and-recovery"},
                "worker-loss": {
                    "name": "timeout-and-real-worker-restart",
                    "worker_loss_delay_seconds": 5.0,
                    "timeout_delay_seconds": 12.0,
                    "healthy_request_count": 1,
                    "concurrency": 2,
                },
                # The reused S2 worker-loss routine reads its frozen profile by
                # the original matrix key. Keep the alias private to this
                # in-process contract; public evidence remains `worker-loss`.
                "I": {
                    "name": "timeout-and-real-worker-restart",
                    "worker_loss_delay_seconds": 5.0,
                    "timeout_delay_seconds": 12.0,
                    "healthy_request_count": 1,
                    "concurrency": 2,
                },
            },
        )


def section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise S8RuntimeError(f"s8_config_section_missing:{name}")
    return value


def worktree_is_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return not result.stdout.strip()


def validate_start_gate(root: Path, progress_path: Path) -> dict[str, Any]:
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    statuses = {
        str(item["scenario_id"]): str(item["status"])
        for item in progress.get("scenarios", [])
    }
    prior = all(statuses.get(f"S{index}") == "verified" for index in range(8))
    current = statuses.get("S8") == "implementing"
    if not prior or not current:
        raise S8RuntimeError(f"s8_start_gate_failed:{statuses}")
    if not worktree_is_clean(root):
        raise S8RuntimeError("s8_experiment_requires_clean_tracked_worktree")
    return {
        "s0_s7_verified": prior,
        "s8_implementing": current,
        "tracked_worktree_clean": True,
    }


def run_fault_scope(
    *,
    root: Path,
    suite_root: Path,
    profile_root: Path,
    database_url: str,
    revision: str,
    branch: str,
    config: S8RuntimeConfig,
    profile_id: str,
    repetition: int,
    queue_config_path: Path,
    trace_path: Path,
) -> dict[str, Any]:
    matrix = config.fault_matrix()
    schema = schema_identifier(
        f"s8_{revision[:8]}_{profile_id.replace('-', '')}{repetition}_{uuid4().hex[:6]}"
    )
    marker = f"s8-{profile_id}-r{repetition}-{uuid4().hex[:5]}"
    private_root = suite_root / "faults" / profile_id / f"repetition-{repetition}"
    scope: RuntimeScope | None = None
    private: dict[str, Any] = {}
    cleanup: dict[str, Any] = {
        "schema_dropped": False,
        "marker_processes_remaining": [],
        "errors": ["scope_not_started"],
    }
    try:
        scope = create_runtime_scope(
            root=root,
            private_root=private_root,
            profile_root=profile_root,
            database_url=database_url,
            schema=schema,
            revision=revision,
            branch=branch,
            queue_config_path=queue_config_path,
            trace_path=trace_path,
            marker=marker,
        )
        execution_started = time.monotonic()
        if profile_id == "worker-loss":
            result = run_profile_i(scope, matrix, repetition)
        else:
            result = execute_fault_profile(scope, matrix, profile_id, repetition)
        result["extra"] = {
            **dict(result.get("extra") or {}),
            "fault_recovery_elapsed_seconds": time.monotonic() - execution_started,
        }
        queue_config = AdmissionQueueConfig.from_path(queue_config_path)
        public, private = finalize_profile_scope(
            scope=scope,
            profile_id=profile_id,
            repetition=repetition,
            matrix=matrix,
            queue_config=queue_config,
            submissions=result["submissions"],
            accepted=result["accepted"],
            terminal=result["terminal"],
            trace_expected=result["trace_expected"],
            trace_requirements=result.get("trace_requirements"),
            effect_expected=result["effect_expected"],
            no_effect_expected=result["no_effect_expected"],
            assertions=result["assertions"],
            input_sequence_sha256=result["input_sequence_sha256"],
            extra=result.get("extra"),
        )
    except Exception as exc:
        private_root.mkdir(parents=True, exist_ok=True)
        private = {
            "schema_version": "evm.s8_fault_failure.v1",
            "generated_at": utc_now(),
            "profile_id": profile_id,
            "repetition": repetition,
            "failure": f"{type(exc).__name__}:{exc}",
            "accepted_for_closure": False,
        }
        public = {
            "profile_id": profile_id,
            "name": matrix.profiles[profile_id]["name"],
            "repetition": repetition,
            "assertions": [assertion("profile_execution", False, private["failure"])],
            "passed": False,
        }
    finally:
        if scope is not None:
            cleanup = scope.close()
    cleanup_passed = (
        bool(cleanup.get("schema_dropped"))
        and not cleanup.get("marker_processes_remaining")
        and not cleanup.get("errors")
    )
    public["cleanup"] = cleanup
    public["assertions"] = [
        *list(public.get("assertions", [])),
        assertion("isolated_runtime_cleanup", cleanup_passed, cleanup),
    ]
    public["passed"] = bool(public.get("passed")) and cleanup_passed
    private["cleanup"] = cleanup
    private["passed"] = public["passed"]
    private_path = private_root / "profile-result-private.json"
    canonical_write(private_path, private)
    public["private_evidence_sha256"] = sha256_file(private_path)
    return public


def execute_fault_profile(
    scope: RuntimeScope,
    matrix: FaultMatrix,
    profile_id: str,
    repetition: int,
) -> dict[str, Any]:
    if profile_id == "control":
        return execute_payload_mix(
            scope, matrix, profile_id, repetition, ["healthy"] * 8
        )
    if profile_id == "latency":
        return execute_payload_mix(
            scope,
            matrix,
            profile_id,
            repetition,
            ["healthy"] * 8,
            delay_seconds=1.0,
        )
    if profile_id == "transient":
        return execute_payload_mix(
            scope,
            matrix,
            profile_id,
            repetition,
            ["transient_once"] * 8,
        )
    if profile_id == "poison":
        return execute_payload_mix(
            scope,
            matrix,
            profile_id,
            repetition,
            ["permanent"] * 4 + ["healthy"] * 8,
        )
    if profile_id == "timeout":
        return execute_payload_mix(
            scope,
            matrix,
            profile_id,
            repetition,
            ["timeout_once"] * 2 + ["healthy"] * 4,
            delay_seconds=12.0,
        )
    if profile_id == "retry-budget":
        return execute_retry_budget_profile(scope, matrix, repetition)
    raise S8RuntimeError(f"s8_fault_profile_unknown:{profile_id}")


def execute_payload_mix(
    scope: RuntimeScope,
    matrix: FaultMatrix,
    profile_id: str,
    repetition: int,
    modes: Sequence[str],
    *,
    delay_seconds: float = 0.0,
) -> dict[str, Any]:
    payloads, traces = profile_payloads(
        profile_id=f"S8-{profile_id}",
        repetition=repetition,
        count=len(modes),
        seed=matrix.seed + repetition,
        failure_modes=modes,
        delay_seconds=delay_seconds,
        terminal_after_seconds=0.1,
    )
    isolate_timeout_delays(payloads, profile_id=profile_id)
    start_worker_and_monitoring(scope, matrix)
    submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads,
        trace_seeds=traces,
        concurrency=min(8, len(payloads)),
        timeout=max(15.0, delay_seconds + 5.0),
    )
    accepted = accepted_tasks(submission)
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    mode_by_task = task_modes(submission, payloads)
    no_effect, expected_states, trace_requirements = fault_mode_contract(mode_by_task)
    effects = set(accepted) - no_effect
    rows = {str(row["task_id"]): row for row in terminal["final"]["queue"]}
    profile_assertions = [
        assertion(
            f"{profile_id}_all_accepted_terminal",
            bool(terminal.get("closed"))
            and all(
                rows.get(task_id, {}).get("state") == state
                for task_id, state in expected_states.items()
            ),
            {
                "accepted": len(accepted),
                "expected_states": Counter(expected_states.values()),
            },
        )
    ]
    timeout_tasks = {
        task_id for task_id, mode in mode_by_task.items() if mode == "timeout_once"
    }
    if timeout_tasks:
        profile_assertions.append(
            assertion(
                f"{profile_id}_timeout_closed_without_effect",
                all(
                    rows.get(task_id, {}).get("state") == "failed"
                    and rows.get(task_id, {}).get("terminal_reason")
                    == "external_effect_not_found_after_timeout"
                    for task_id in timeout_tasks
                ),
                {
                    task_id: {
                        "state": rows.get(task_id, {}).get("state"),
                        "terminal_reason": rows.get(task_id, {}).get("terminal_reason"),
                    }
                    for task_id in timeout_tasks
                },
            )
        )
    return {
        "submissions": [submission],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "trace_requirements": trace_requirements,
        "effect_expected": effects,
        "no_effect_expected": no_effect,
        "assertions": profile_assertions,
        "input_sequence_sha256": payload_digest(payloads),
        "extra": {
            "fault_profile": profile_id,
            "delay_seconds": delay_seconds,
            "mode_counts": dict(Counter(modes)),
        },
    }


def fault_mode_contract(
    mode_by_task: Mapping[str, str],
) -> tuple[set[str], dict[str, str], dict[str, set[str]]]:
    no_effect_modes = {"permanent", "always_transient", "timeout_once"}
    no_effect = {
        task_id for task_id, mode in mode_by_task.items() if mode in no_effect_modes
    }
    expected_states = {
        task_id: (
            "failed"
            if mode == "timeout_once"
            else "dlq"
            if mode in {"permanent", "always_transient"}
            else "completed"
        )
        for task_id, mode in mode_by_task.items()
    }
    trace_requirements = {
        task_id: set(
            TIMEOUT_FAILURE_TRACE_NAMES if mode == "timeout_once" else FULL_TRACE_NAMES
        )
        for task_id, mode in mode_by_task.items()
    }
    return no_effect, expected_states, trace_requirements


def isolate_timeout_delays(
    payloads: Sequence[dict[str, Any]], *, profile_id: str
) -> None:
    if profile_id != "timeout":
        return
    for payload in payloads:
        config = payload.get("config_payload")
        if not isinstance(config, dict):
            raise S8RuntimeError("s8_timeout_payload_config_invalid")
        if str(config.get("s2_failure_mode")) != "timeout_once":
            config["s2_delay_seconds"] = 0.0


def execute_retry_budget_profile(
    scope: RuntimeScope,
    matrix: FaultMatrix,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["retry-budget"]
    modes = ["always_transient"] * int(spec["transient_request_count"]) + [
        "healthy"
    ] * int(spec["healthy_request_count"])
    payloads, traces = profile_payloads(
        profile_id="S8-retry-budget",
        repetition=repetition,
        count=len(modes),
        seed=matrix.seed + 100 + repetition,
        failure_modes=modes,
        terminal_after_seconds=0.1,
    )
    start_worker_and_monitoring(scope, matrix)
    first = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads,
        trace_seeds=traces,
        concurrency=16,
    )
    accepted = accepted_tasks(first)
    first_terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    time.sleep(2.25)
    recovery_payloads, recovery_traces = profile_payloads(
        profile_id="S8-retry-recovery",
        repetition=repetition,
        count=1,
        seed=matrix.seed + 200 + repetition,
        failure_modes=["healthy"],
        terminal_after_seconds=0.1,
    )
    recovery = submit_payloads(
        api_url=scope.api.base_url,
        payloads=recovery_payloads,
        trace_seeds=recovery_traces,
        concurrency=1,
    )
    recovery_accepted = accepted_tasks(recovery)
    accepted = {**accepted, **recovery_accepted}
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    modes_by_task = task_modes(first, payloads)
    transient = {
        task_id
        for task_id, mode in modes_by_task.items()
        if mode == "always_transient"
    }
    healthy = set(accepted) - transient
    rows = {str(row["task_id"]): row for row in terminal["final"]["queue"]}
    return {
        "submissions": [first, recovery],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": healthy,
        "no_effect_expected": transient,
        "assertions": [
            assertion(
                "retry_budget_terminal_and_recovery",
                bool(first_terminal.get("closed"))
                and all(rows.get(task_id, {}).get("state") == "dlq" for task_id in transient)
                and all(
                    rows.get(task_id, {}).get("state") == "completed"
                    for task_id in healthy
                ),
                {
                    "transient": len(transient),
                    "healthy": len(healthy),
                    "recovery": len(recovery_accepted),
                },
            )
        ],
        "input_sequence_sha256": payload_digest(
            [*payloads, *recovery_payloads]
        ),
        "extra": {
            "fault_profile": "retry-budget",
            "recovery_after_hold": True,
        },
    }


def task_modes(
    submission: Mapping[str, Any], payloads: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    observed: dict[str, str] = {}
    for result in submission.get("results", []):
        if int(result.get("status_code", 0)) != 202:
            continue
        body = result.get("body")
        if not isinstance(body, Mapping):
            continue
        index = int(result["index"])
        config = payloads[index].get("config_payload", {})
        observed[str(body["task_id"])] = str(
            dict(config).get("s2_failure_mode", "healthy")
        )
    return observed


def analyze_fault_results(
    results: Sequence[Mapping[str, Any]], config: S8RuntimeConfig
) -> dict[str, Any]:
    expected = len(FAULT_PROFILE_IDS) * config.repetitions
    amplification: list[float] = []
    mttr: list[float] = []
    circuit_opens = 0.0
    duplicate_effects = 0
    pool_timeouts = 0.0
    for result in results:
        accepted = int(dict(result.get("terminal", {})).get("accepted_count", 0))
        attempts = int(dict(result.get("external_effects", {})).get("attempts", 0))
        amplification.append(attempts / accepted if accepted else math.inf)
        mttr.append(
            float(
                dict(result.get("profile_observations", {})).get(
                    "fault_recovery_elapsed_seconds", math.inf
                )
            )
        )
        metrics = dict(result.get("metrics", {}))
        circuit_opens += float(
            dict(metrics.get("dependency_circuit", {})).get("opens", 0)
        )
        duplicate_effects += int(
            dict(result.get("external_effects", {})).get("duplicates", -1)
        )
        for pool in dict(metrics.get("control_plane_pool", {})).values():
            pool_timeouts += float(dict(pool).get("timeouts", 0))
    checks = {
        "exact_matrix": len(results) == expected,
        "all_profiles_passed": bool(results)
        and all(bool(item.get("passed")) for item in results),
        "retry_amplification_bounded": bool(amplification)
        and max(amplification) <= config.maximum_retry_amplification,
        "dependency_circuit_observed": circuit_opens > 0,
        "duplicate_effects_zero": duplicate_effects == 0,
        "pool_timeouts_zero": pool_timeouts == 0,
        "mttr_bounded": bool(mttr) and max(mttr) <= config.maximum_mttr_seconds,
        "cleanup_complete": all(
            bool(dict(item.get("cleanup", {})).get("schema_dropped"))
            and not dict(item.get("cleanup", {})).get("marker_processes_remaining")
            and not dict(item.get("cleanup", {})).get("errors")
            for item in results
        ),
    }
    return {
        "result_count": len(results),
        "expected_result_count": expected,
        "retry_amplification": statistics(amplification),
        "mttr_seconds": statistics(mttr),
        "dependency_circuit_open_count": circuit_opens,
        "duplicate_external_effect_count": duplicate_effects,
        "pool_timeout_count": pool_timeouts,
        "checks": checks,
        "passed": all(checks.values()),
    }


def run_soak_repetitions(
    *,
    root: Path,
    data_root: Path,
    suite_root: Path,
    config: S8RuntimeConfig,
    soak_config_path: Path,
    trace_path: Path,
    revision: str,
    branch: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    soak_config = S3RuntimeConfig.from_path(soak_config_path, data_root=data_root)
    verify_runtime_identity(soak_config)
    if not math.isclose(
        soak_config.open_rates[0], config.soak_requests_per_second, abs_tol=1e-9
    ):
        raise S8RuntimeError("s8_soak_runtime_rate_mismatch")
    point = S3LoadPoint(
        mode="open",
        probe_family=config.soak_probe_family,
        load=config.soak_requests_per_second,
        api_replicas=config.soak_api_replicas,
        cpu_workers=config.soak_cpu_workers,
        matrix_scope="s8-soak",
    )
    soak_root = suite_root / "soak"
    results: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []
    for repetition in range(1, config.repetitions + 1):
        public = run_capacity_point(
            root=root,
            data_root=data_root,
            suite_root=soak_root,
            config=soak_config,
            point=point,
            repetition=repetition,
            source_revision=revision,
            source_branch=branch,
            trace_path=trace_path,
        )
        results.append(public)
        private_path = (
            soak_root
            / point.point_id
            / f"repetition-{repetition}"
            / "point-evidence-private.json"
        )
        analyses.append(analyze_soak_private(private_path, config))
        canonical_write(
            suite_root / "suite-progress-private.json",
            {"faults_complete": True, "soak_results": results, "soak_analysis": analyses},
        )
        if not bool(public.get("evidence_valid")) or not analyses[-1]["passed"]:
            break
    return results, analyze_soak_results(results, analyses, config)


def analyze_soak_private(
    private_path: Path, config: S8RuntimeConfig
) -> dict[str, Any]:
    payload = json.loads(private_path.read_text(encoding="utf-8"))
    samples = [dict(item) for item in payload.get("resource_samples", [])]
    minimum_samples = int(
        config.soak_measurement_seconds
        / config.soak_resource_sample_interval_seconds
        * 0.90
    )
    slopes = {
        "rss_bytes_per_minute": linear_slope(
            samples,
            lambda item: float(item.get("api_process_tree_rss_bytes", 0))
            + float(item.get("load_generator_rss_bytes", 0)),
        ),
        "open_handles_per_minute": linear_slope(
            samples,
            lambda item: float(item.get("api_process_tree_open_handles", 0))
            + float(item.get("load_generator_open_handles", 0)),
        ),
        "pool_in_use_per_minute": linear_slope(
            samples,
            lambda item: float(item.get("evm_control_plane_db_pool_in_use", 0)),
        ),
        "pool_waiting_per_minute": linear_slope(
            samples,
            lambda item: float(item.get("evm_control_plane_db_pool_waiting", 0)),
        ),
        "queue_depth_per_minute": linear_slope(
            samples,
            lambda item: float(
                item.get("evm_s3_capacity_executor_queue_depth", 0)
            ),
        ),
        "artifact_bytes_per_minute": linear_slope(
            samples, lambda item: float(item.get("artifact_bytes", 0))
        ),
    }
    cpu_seconds = integrate_cpu_seconds(samples)
    measurement = dict(payload.get("measurement", {}))
    observations = [dict(item) for item in measurement.get("observations", [])]
    completed = sum(int(item.get("status_code", 0)) == 200 for item in observations)
    efficiency = completed / cpu_seconds if cpu_seconds > 0 else 0.0
    checks = {
        "sample_count": len(samples) >= minimum_samples,
        "rss_slope": slopes["rss_bytes_per_minute"]
        <= config.maximum_rss_slope_bytes_per_minute,
        "fd_slope": slopes["open_handles_per_minute"]
        <= config.maximum_fd_slope_per_minute,
        "pool_in_use_slope": slopes["pool_in_use_per_minute"] <= 0.05,
        "pool_waiting_slope": slopes["pool_waiting_per_minute"] <= 0.05,
        "queue_slope": slopes["queue_depth_per_minute"]
        <= config.maximum_queue_slope_items_per_minute,
        "artifact_slope": slopes["artifact_bytes_per_minute"]
        <= config.maximum_artifact_slope_bytes_per_minute,
        "cpu_seconds_nonzero": cpu_seconds > 0,
        "runtime_evidence_valid": bool(payload.get("evidence_valid")),
    }
    return {
        "private_evidence_sha256": sha256_file(private_path),
        "resource_sample_count": len(samples),
        "slopes": slopes,
        "completed_requests": completed,
        "cpu_seconds": cpu_seconds,
        "requests_per_cpu_second": efficiency,
        "checks": checks,
        "passed": all(checks.values()),
    }


def analyze_soak_results(
    results: Sequence[Mapping[str, Any]],
    analyses: Sequence[Mapping[str, Any]],
    config: S8RuntimeConfig,
) -> dict[str, Any]:
    service_rates = [float(dict(item.get("load", {})).get("service_rate_per_second", 0)) for item in results]
    p99 = [float(dict(dict(item.get("load", {})).get("latency_ms", {})).get("p99", math.inf)) for item in results]
    errors = [float(dict(item.get("load", {})).get("error_rate", 1)) for item in results]
    checks = {
        "three_independent_repetitions": len(results) == config.repetitions,
        "all_runtime_evidence_valid": bool(results)
        and all(bool(item.get("evidence_valid")) for item in results),
        "all_slope_projections_passed": len(analyses) == config.repetitions
        and all(bool(item.get("passed")) for item in analyses),
        "error_guardrail": bool(errors)
        and max(errors) <= config.maximum_error_rate,
        "p99_guardrail": bool(p99) and max(p99) <= config.maximum_p99_ms,
        "terminal_cleanup": all(
            bool(dict(item.get("assertions", {})).get("terminal_gauges_zero"))
            and bool(dict(item.get("assertions", {})).get("cleanup_complete"))
            for item in results
        ),
    }
    return {
        "service_rate_per_second": statistics(service_rates),
        "p99_ms": statistics(p99),
        "error_rate": statistics(errors),
        "resource_repetitions": list(analyses),
        "checks": checks,
        "passed": all(checks.values()),
    }


def linear_slope(
    samples: Sequence[Mapping[str, Any]],
    value: Any,
) -> float:
    points = [
        (float(item.get("offset_seconds", 0)), float(value(item)))
        for item in samples
    ]
    if len(points) < 2:
        return math.inf
    x_mean = sum(point[0] for point in points) / len(points)
    y_mean = sum(point[1] for point in points) / len(points)
    denominator = sum((point[0] - x_mean) ** 2 for point in points)
    if denominator <= 0:
        return math.inf
    slope_per_second = sum(
        (point[0] - x_mean) * (point[1] - y_mean) for point in points
    ) / denominator
    if not math.isfinite(slope_per_second):
        return math.inf
    return max(0.0, slope_per_second * 60.0)


def integrate_cpu_seconds(samples: Sequence[Mapping[str, Any]]) -> float:
    total = 0.0
    for previous, current in zip(samples, samples[1:], strict=False):
        elapsed = max(
            0.0,
            float(current.get("offset_seconds", 0))
            - float(previous.get("offset_seconds", 0)),
        )
        percent = max(
            0.0,
            float(current.get("api_process_tree_cpu_percent", 0))
            + float(current.get("load_generator_cpu_percent", 0)),
        )
        total += elapsed * percent / 100.0
    return total


def statistics(values: Sequence[float]) -> dict[str, float | int]:
    observed = sorted(float(value) for value in values)
    if not observed or any(not math.isfinite(value) for value in observed):
        return {
            "count": len(observed),
            "finite": False,
            "min": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": len(observed),
        "finite": True,
        "min": observed[0],
        "max": observed[-1],
        "mean": sum(observed) / len(observed),
    }


def s4_gpu_efficiency_reference(root: Path) -> dict[str, Any]:
    path = root / "docs/status/evidence/s4-gpu-batching-experiment.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = dict(dict(payload.get("analysis", {})).get("selected_operating_point", {}))
    batch = int(selected.get("batch_size", 0))
    delay = int(selected.get("max_delay_ms", -1))
    instances = int(selected.get("instance_count", 0))
    points = [
        dict(item)
        for item in payload.get("point_results", [])
        if item.get("mode") == "matrix"
        and int(item.get("batch_size", 0)) == batch
        and int(item.get("max_delay_ms", -1)) == delay
        and int(item.get("instance_count", 0)) == instances
    ]
    measurement_seconds = 30.0
    equivalent_gpu_seconds = sum(
        measurement_seconds * float(item.get("gpu_utilization_percent_mean", 0)) / 100.0
        for item in points
    )
    successful = sum(int(item.get("success_count", 0)) for item in points)
    if len(points) != 3 or equivalent_gpu_seconds <= 0:
        raise S8RuntimeError("s8_s4_gpu_efficiency_reference_invalid")
    return {
        "scope": "accepted_s4_reference_not_fresh_s8_gpu_concurrency",
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "selected_point": {"batch_size": batch, "max_delay_ms": delay, "instances": instances},
        "repetitions": len(points),
        "successful_requests": successful,
        "equivalent_gpu_seconds": equivalent_gpu_seconds,
        "requests_per_equivalent_gpu_second": successful / equivalent_gpu_seconds,
    }


def git_blob_identity(root: Path, revision: str, relative_path: Path) -> dict[str, str]:
    git_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    project_prefix = root.resolve().relative_to(git_root.resolve())
    git_path = (project_prefix / relative_path).as_posix()
    blob_oid = subprocess.run(
        ["git", "rev-parse", f"{revision}:{git_path}"],
        cwd=git_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "show", f"{revision}:{git_path}"],
        cwd=git_root,
        check=True,
        capture_output=True,
    ).stdout
    return {
        "path": relative_path.as_posix(),
        "blob_oid": blob_oid,
        "sha256": hashlib.sha256(blob).hexdigest(),
    }


def run_s8_experiment(
    *,
    root: Path,
    data_root: Path,
    private_parent: Path,
    profile_root: Path,
    database_url: str,
    queue_config_path: Path,
    soak_config_path: Path,
    progress_path: Path,
    trace_path: Path,
    output_path: Path,
    scenario_config_path: Path,
) -> dict[str, Any]:
    root = root.resolve()
    config = S8RuntimeConfig.from_path(scenario_config_path)
    queue_config = AdmissionQueueConfig.from_path(queue_config_path)
    if queue_config.profile_version != "s8-dependency-soak-v5-20260824":
        raise S8RuntimeError("s8_queue_profile_identity_invalid")
    if not port_is_available(queue_config.metrics_port):
        raise S8RuntimeError(f"s8_worker_metrics_port_in_use:{queue_config.metrics_port}")
    start_gate = validate_start_gate(root, progress_path)
    revision, branch = source_revision(root)
    suite_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    suite_root = (private_parent / suite_id).resolve()
    suite_root.mkdir(parents=True, exist_ok=False)
    isolated = materialize_isolated_profile(
        source_root=profile_root,
        profile_id="standard-b0-manual-tuning",
        profile_version=11,
        isolated_root=suite_root / "isolated-pipeline-profiles",
    )
    active_profile_root = Path(isolated["profile_root"])
    faults: list[dict[str, Any]] = []
    started_at = utc_now()
    for profile_id in FAULT_PROFILE_IDS:
        for repetition in range(1, config.repetitions + 1):
            result = run_fault_scope(
                root=root,
                suite_root=suite_root,
                profile_root=active_profile_root,
                database_url=database_url,
                revision=revision,
                branch=branch,
                config=config,
                profile_id=profile_id,
                repetition=repetition,
                queue_config_path=queue_config_path,
                trace_path=trace_path,
            )
            faults.append(result)
            canonical_write(
                suite_root / "suite-progress-private.json",
                {"fault_results": faults, "soak_results": []},
            )
            if not result.get("passed"):
                break
        if faults and not faults[-1].get("passed"):
            break
    fault_analysis = analyze_fault_results(faults, config)
    soak_results: list[dict[str, Any]] = []
    soak_analysis: dict[str, Any] = {"passed": False, "reason": "fault_gate_failed"}
    if fault_analysis["passed"]:
        soak_results, soak_analysis = run_soak_repetitions(
            root=root,
            data_root=data_root,
            suite_root=suite_root,
            config=config,
            soak_config_path=soak_config_path,
            trace_path=trace_path,
            revision=revision,
            branch=branch,
        )
    private_index = private_evidence_index(suite_root)
    canonical_write(suite_root / "private-evidence-index.json", private_index)
    gpu_reference = s4_gpu_efficiency_reference(root)
    source_paths = {
        "runtime": Path("src/evm/scale_validation/s8_runtime.py"),
        "runner": Path("scripts/dev/run_s8_dependency_soak_experiment.py"),
        "s2_runtime": Path("src/evm/scale_validation/s2_runtime.py"),
        "s3_runtime": Path("src/evm/scale_validation/s3_runtime.py"),
        "admission": Path("src/evm/control_panel/admission_queue.py"),
        "worker": Path("src/evm/control_panel/task_queue_worker.py"),
        "store": Path("src/evm/control_panel/transactional_store.py"),
        "scenario_config": scenario_config_path.resolve().relative_to(root),
        "soak_config": soak_config_path.resolve().relative_to(root),
    }
    source_blobs = {
        label: git_blob_identity(root, revision, path)
        for label, path in source_paths.items()
    }
    ac = {
        "S8-AC-01": bool(fault_analysis.get("passed")),
        "S8-AC-02": bool(soak_analysis.get("passed")),
        "S8-AC-03": bool(fault_analysis.get("passed"))
        and bool(soak_analysis.get("passed"))
        and bool(gpu_reference),
        "S8-AC-04": False,
    }
    public = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "started_at": started_at,
        "source_identity": {
            "implementation_revision": revision,
            "branch": branch,
            "runtime_module": "src/evm/scale_validation/s8_runtime.py",
            "runtime_module_sha256": source_blobs["runtime"]["sha256"],
            "git_blobs": source_blobs,
        },
        "start_gate": start_gate,
        "config": {
            "version": queue_config.profile_version,
            "scenario_sha256": config.sha256,
            "queue_sha256": queue_config.sha256,
            "seed": config.seed,
            "repetitions": config.repetitions,
            "soak_rps": config.soak_requests_per_second,
            "soak_measurement_seconds": config.soak_measurement_seconds,
        },
        "fault_results": faults,
        "fault_analysis": fault_analysis,
        "soak_results": soak_results,
        "soak_analysis": soak_analysis,
        "resource_efficiency": {
            "cpu_soak": [
                {
                    "repetition": index + 1,
                    "cpu_seconds": item.get("cpu_seconds"),
                    "requests_per_cpu_second": item.get("requests_per_cpu_second"),
                }
                for index, item in enumerate(
                    soak_analysis.get("resource_repetitions", [])
                )
            ],
            "gpu_reference": gpu_reference,
        },
        "acceptance": ac,
        "runtime_verdict": (
            "exercised_pending_hash_closure"
            if all(ac[key] for key in ("S8-AC-01", "S8-AC-02", "S8-AC-03"))
            else "not_passed"
        ),
        "scenario_status": (
            "exercised" if all(ac[key] for key in ("S8-AC-01", "S8-AC-02", "S8-AC-03")) else "implementing"
        ),
        "private_evidence": {
            "artifact_count": private_index["artifact_count"],
            "aggregate_sha256": private_index["aggregate_sha256"],
            "location": "outside_git_private_evidence_root",
        },
        "claim_boundary": CLAIM_BOUNDARY,
    }
    write_public_json(output_path, public)
    canonical_write(suite_root / "suite-summary-private.json", public)
    return public


def current_runtime_cleanup(root: Path) -> dict[str, Any]:
    markers = []
    for prefix in ("s8-", "s3-s8-soak"):
        markers.extend(marker_processes(prefix))
    containers = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.splitlines()
    return {
        "marker_processes": sorted(set(markers)),
        "temporary_containers": sorted(
            name for name in containers if name.startswith(("s8-", "s3-s8-soak"))
        ),
        "worker_metrics_port_available": port_is_available(9478),
    }


def assert_no_lingering_runtime(root: Path) -> None:
    cleanup = current_runtime_cleanup(root)
    if cleanup["marker_processes"] or cleanup["temporary_containers"]:
        raise S8RuntimeError(f"s8_cleanup_residue:{cleanup}")


def process_tree_identity(pid: int) -> dict[str, Any]:
    process = psutil.Process(pid)
    return {
        "pid": pid,
        "create_time": process.create_time(),
        "children": [child.pid for child in process.children(recursive=True)],
    }

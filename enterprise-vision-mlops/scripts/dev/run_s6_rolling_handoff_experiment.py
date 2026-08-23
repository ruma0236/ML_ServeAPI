from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import requests

from evm.control_panel.lifecycle_gpu_handoff import (
    GpuHandoffError,
    acquire_gpu_handoff,
    consume_gpu_handoff_approval,
    issue_gpu_handoff_approval,
    release_gpu_handoff,
)
from evm.control_panel.lifecycle_kubernetes import ServingBundle
from evm.control_panel.lifecycle_runs import LifecycleRun
from evm.scale_validation.evidence import write_public_json
from evm.scale_validation.s6_evidence import source_git_identity
from evm.scale_validation.s6_runtime import (
    S6RuntimeConfig,
    S6RuntimeError,
    analyze_s6_results,
    deterministic_traceparent,
    file_sha256,
    payload_sha256,
    summarize_latencies,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/s6_rolling_handoff.toml"
DEFAULT_PUBLIC = ROOT / "docs/status/evidence/s6-rolling-handoff-experiment.json"
DEFAULT_DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
DEFAULT_TRACE_PATH = (
    DEFAULT_DATA_ROOT / "artifacts/scale_validation/otel/traces.json"
)
PROMETHEUS_URL = "http://127.0.0.1:9090"
API_TRACE_SPAN = "POST /control-panel/v1/runtime/rollout-probes"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Scenario S6 controlled validation")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--database-url", default=os.getenv("EVM_CONTROL_PLANE_DATABASE_URL"))
    parser.add_argument("--database-schema", default="evm_s6_api")
    parser.add_argument("--old-image", required=True)
    parser.add_argument("--new-image", required=True)
    parser.add_argument("--trace-path", type=Path, default=DEFAULT_TRACE_PATH)
    parser.add_argument("--skip-gpu", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise S6RuntimeError("s6_database_url_missing")
    config = S6RuntimeConfig.from_path(args.config, data_root=args.data_root)
    source_revision = git("rev-parse", "HEAD")
    branch = git("branch", "--show-current")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise S6RuntimeError("s6_tracked_worktree_not_clean")
    args.private_root.mkdir(parents=True, exist_ok=False)
    started_at = utc_now()
    failed_attempts: list[dict[str, Any]] = []
    try:
        preflight = runtime_preflight(
            config=config,
            database_url=args.database_url,
            database_schema=args.database_schema,
            old_image=args.old_image,
            new_image=args.new_image,
            source_revision=source_revision,
        )
        api_results = run_api_suite(
            config=config,
            database_url=args.database_url,
            database_schema=args.database_schema,
            old_image=args.old_image,
            new_image=args.new_image,
            source_revision=source_revision,
            private_root=args.private_root / "api",
            trace_path=args.trace_path,
        )
        if args.skip_gpu:
            raise S6RuntimeError("s6_gpu_acceptance_not_executed")
        gpu_gate = candidate_gate(config)
        gpu_calibration = run_gpu_handoff(
            config=config,
            source_revision=source_revision,
            private_root=args.private_root / "gpu" / "calibration",
            repetition=0,
            candidate_gate_payload=gpu_gate,
            inference_requests=config.gpu_handoff.calibration_inference_requests,
            phase="calibration",
        )
        if gpu_calibration["status"] != "passed":
            raise S6RuntimeError("s6_gpu_calibration_failed")
        gpu_results = []
        for repetition in range(1, config.gpu_handoff.repetitions + 1):
            result = run_gpu_handoff(
                config=config,
                source_revision=source_revision,
                private_root=args.private_root / "gpu" / f"repetition-{repetition:02d}",
                repetition=repetition,
                candidate_gate_payload=gpu_gate,
                inference_requests=config.gpu_handoff.acceptance_inference_requests,
                phase="acceptance",
            )
            gpu_results.append(result)
            if result["status"] != "passed":
                raise S6RuntimeError(f"s6_gpu_repetition_failed:{repetition}")
            time.sleep(config.gpu_handoff.cooldown_seconds)
        analysis = analyze_s6_results(
            api_repetitions=api_results,
            gpu_calibration=gpu_calibration,
            gpu_repetitions=gpu_results,
            config=config,
        )
        if analysis["status"] != "passed":
            raise S6RuntimeError("s6_acceptance_analysis_failed")
        cleanup = cleanup_runtime(
            config=config,
            old_image=args.old_image,
            source_revision=source_revision,
        )
        if not cleanup["passed"]:
            raise S6RuntimeError("s6_cleanup_failed")
        private_index = build_private_index(args.private_root)
        private_index_path = args.private_root / "private-evidence-index.json"
        canonical_write(private_index_path, private_index)
        public = {
            "schema_version": "evm.s6_rolling_handoff_experiment.v1",
            "generated_at": utc_now(),
            "started_at": started_at,
            "finished_at": utc_now(),
            "status": "verified",
            "verdict": "passed",
            "source_identity": {
                "branch": branch,
                "revision": source_revision,
                "config_sha256": config.sha256,
                "old_image_id": preflight["old_image_id"],
                "new_image_id": preflight["new_image_id"],
                "git_blobs": source_git_identity(ROOT.parent, source_revision),
            },
            "frozen_contract": config.public_dict(),
            "preflight": public_preflight(preflight),
            "api_repetitions": [public_api_result(item) for item in api_results],
            "gpu_calibration": public_gpu_result(gpu_calibration),
            "gpu_repetitions": [public_gpu_result(item) for item in gpu_results],
            "analysis": analysis,
            "cleanup": cleanup,
            "failed_attempts": failed_attempts,
            "private_evidence": {
                "logical_root": "private://scale_validation/s6/accepted",
                "artifact_count": private_index["artifact_count"],
                "total_bytes": private_index["total_bytes"],
                "aggregate_sha256": private_index["aggregate_sha256"],
                "index_sha256": file_sha256(private_index_path),
            },
            "claim_boundary": config.claim_boundary,
        }
        write_public_json(args.public_output, public)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "public_output": str(args.public_output),
                    "public_sha256": file_sha256(args.public_output),
                    "private_index": str(private_index_path),
                    "private_index_sha256": file_sha256(private_index_path),
                    "analysis": analysis,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        failure = {
            "schema_version": "evm.s6_failed_attempt.v1",
            "generated_at": utc_now(),
            "source_revision": source_revision,
            "config_sha256": config.sha256,
            "error": f"{type(exc).__name__}:{exc}",
        }
        failed_attempts.append(failure)
        canonical_write(args.private_root / "failed-attempt.json", failure)
        try:
            failure["cleanup"] = cleanup_runtime(
                config=config,
                old_image=args.old_image,
                source_revision=source_revision,
            )
            canonical_write(args.private_root / "failed-attempt.json", failure)
        except Exception as cleanup_exc:
            failure["cleanup_error"] = f"{type(cleanup_exc).__name__}:{cleanup_exc}"
            canonical_write(args.private_root / "failed-attempt.json", failure)
        print(json.dumps(failure, sort_keys=True))
        return 2


def runtime_preflight(
    *,
    config: S6RuntimeConfig,
    database_url: str,
    database_schema: str,
    old_image: str,
    new_image: str,
    source_revision: str,
) -> dict[str, Any]:
    old_identity = docker_image_identity(old_image)
    new_identity = docker_image_identity(new_image)
    for label, expected_release in (
        (old_identity, "old"),
        (new_identity, "new"),
    ):
        if label["source_revision"] != source_revision:
            raise S6RuntimeError("s6_image_source_revision_mismatch")
        if label["release"] != expected_release:
            raise S6RuntimeError("s6_image_release_label_mismatch")
    if old_identity["image_id"] == new_identity["image_id"]:
        raise S6RuntimeError("s6_old_new_image_identity_not_distinct")
    gpu_allocatable = cluster_gpu_allocatable()
    if gpu_allocatable != 1:
        raise S6RuntimeError(
            f"s6_exact_single_gpu_required:actual={gpu_allocatable}"
        )
    gpu_identity = host_gpu_identity()
    deployment = deployment_snapshot(config.api.namespace, config.api.deployment)
    if deployment["desired_replicas"] != config.api.replicas:
        raise S6RuntimeError("s6_api_replica_preflight_mismatch")
    patch_api_release(
        config=config,
        image=old_image,
        release_id="old",
        source_revision=source_revision,
    )
    wait_api_release_settled(config=config, release_id="old")
    ready = request_json(f"http://127.0.0.1:{config.api.node_port}/ready")
    if ready.get("status") != "ready" and ready.get("status") != "ok":
        raise S6RuntimeError("s6_api_readiness_preflight_failed")
    if prometheus_target_health("evm-s6-api") != "up":
        raise S6RuntimeError("s6_prometheus_preflight_failed")
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name='entities'",
                (database_schema,),
            )
            if int(cursor.fetchone()[0]) != 1:
                raise S6RuntimeError("s6_database_schema_preflight_failed")
    production = deployment_snapshot(
        config.gpu_handoff.source_namespace, config.gpu_handoff.source_deployment
    )
    staging = deployment_snapshot(
        config.gpu_handoff.target_namespace, config.gpu_handoff.target_deployment
    )
    if production["ready_replicas"] != 1 or staging["desired_replicas"] != 0:
        raise S6RuntimeError("s6_gpu_baseline_preflight_failed")
    return {
        "old_image_id": old_identity["image_id"],
        "new_image_id": new_identity["image_id"],
        "api_ready_replicas": config.api.replicas,
        "prometheus_up": True,
        "production_ready": 1,
        "staging_scaled_zero": True,
        "gpu_allocatable": gpu_allocatable,
        "gpu_identity": gpu_identity,
    }


def run_api_suite(
    *,
    config: S6RuntimeConfig,
    database_url: str,
    database_schema: str,
    old_image: str,
    new_image: str,
    source_revision: str,
    private_root: Path,
    trace_path: Path,
) -> list[dict[str, Any]]:
    private_root.mkdir(parents=True, exist_ok=True)
    results = []
    for repetition in range(1, config.api.repetitions + 1):
        patch_api_release(
            config=config,
            image=old_image,
            release_id="old",
            source_revision=source_revision,
        )
        wait_api_release_settled(config=config, release_id="old")
        result = run_api_repetition(
            config=config,
            database_url=database_url,
            database_schema=database_schema,
            new_image=new_image,
            source_revision=source_revision,
            repetition=repetition,
            trace_path=trace_path,
        )
        canonical_write(private_root / f"repetition-{repetition:02d}.json", result)
        results.append(result)
        if not api_repetition_passed(result, config):
            raise S6RuntimeError(f"s6_api_repetition_failed:{repetition}")
        patch_api_release(
            config=config,
            image=old_image,
            release_id="old",
            source_revision=source_revision,
        )
        wait_api_release_settled(config=config, release_id="old")
        time.sleep(config.api.cooldown_seconds)
    return results


def run_api_repetition(
    *,
    config: S6RuntimeConfig,
    database_url: str,
    database_schema: str,
    new_image: str,
    source_revision: str,
    repetition: int,
    trace_path: Path,
) -> dict[str, Any]:
    run_id = f"s6-api-{source_revision[:12]}-r{repetition:02d}"
    warmup_id = f"{run_id}-warmup"
    execute_open_loop(
        base_url=f"http://127.0.0.1:{config.api.node_port}",
        prefix=warmup_id,
        seed=config.seed + repetition,
        rps=config.api.target_requests_per_second,
        duration_seconds=config.api.warmup_seconds,
        processing_delay_ms=config.api.processing_delay_ms,
        config=config,
    )
    trace_offset = trace_path.stat().st_size if trace_path.is_file() else 0
    before = deployment_snapshot(config.api.namespace, config.api.deployment)
    rollout_result: dict[str, Any] = {}
    rollout_error: list[str] = []

    def rollout_action() -> None:
        try:
            time.sleep(config.api.rollout_offset_seconds)
            started = time.monotonic()
            started_at = utc_now()
            patch_api_release(
                config=config,
                image=new_image,
                release_id="new",
                source_revision=source_revision,
            )
            wait_api_release_settled(config=config, release_id="new")
            rollout_result.update(
                {
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "elapsed_seconds": time.monotonic() - started,
                }
            )
        except Exception as exc:
            rollout_error.append(f"{type(exc).__name__}:{exc}")

    timeline: list[dict[str, Any]] = []
    stop_monitor = threading.Event()
    monitor = threading.Thread(
        target=monitor_api_pods,
        kwargs={
            "config": config,
            "stop": stop_monitor,
            "timeline": timeline,
        },
        daemon=True,
    )
    rollout = threading.Thread(target=rollout_action, daemon=True)
    monitor.start()
    rollout.start()
    started = time.monotonic()
    observations = execute_open_loop(
        base_url=f"http://127.0.0.1:{config.api.node_port}",
        prefix=f"{run_id}-request",
        seed=config.seed + 1000 + repetition,
        rps=config.api.target_requests_per_second,
        duration_seconds=config.api.measurement_seconds,
        processing_delay_ms=config.api.processing_delay_ms,
        config=config,
    )
    measurement_elapsed = time.monotonic() - started
    rollout.join(timeout=config.rolling.rollout_timeout_seconds + 5)
    stop_monitor.set()
    monitor.join(timeout=5)
    if rollout.is_alive() or rollout_error:
        raise S6RuntimeError(
            "s6_api_rollout_failed:" + ",".join(rollout_error or ["timeout"])
        )
    after = deployment_snapshot(config.api.namespace, config.api.deployment)
    database = query_api_database(
        database_url=database_url,
        schema=database_schema,
        request_prefix=f"{run_id}-request",
        drain_instance_ids={str(item["uid"]) for item in before["active_pods"]},
    )
    successful = [item for item in observations if item.get("success") is True]
    latencies = [float(item["logical_latency_ms"]) for item in successful]
    latency = summarize_latencies(latencies)
    client_ids = {str(item["logical_request_id"]) for item in successful}
    accepted_ids = set(database["accepted_ids"])
    terminal_ids = set(database["terminal_ids"])
    expected_sampled = {
        str(item["trace_id"])
        for item in observations
        if item.get("sampled") is True
    }
    traces = wait_for_trace_ids(
        trace_path,
        offset=trace_offset,
        expected=expected_sampled,
        timeout=config.api.trace_flush_timeout_seconds,
        poll_interval=config.api.runtime_poll_interval_seconds,
    )
    attempts = sum(len(item.get("attempts", [])) for item in observations)
    error_rate = (
        (len(observations) - len(successful)) / len(observations)
        if observations
        else 1.0
    )
    prometheus = wait_prometheus_target_up(
        "evm-s6-api",
        timeout=config.api.trace_flush_timeout_seconds,
        poll_interval=config.api.runtime_poll_interval_seconds,
    )
    result = {
        "schema_version": "evm.s6_api_rolling_repetition.v1",
        "run_id": run_id,
        "repetition": repetition,
        "started_at": rollout_result["started_at"],
        "finished_at": utc_now(),
        "logical_requests": len(observations),
        "attempts": attempts,
        "client_success": len(client_ids),
        "database_accepted": len(accepted_ids),
        "database_terminal": len(terminal_ids),
        "accepted_loss": len(accepted_ids - client_ids),
        "client_success_without_acceptance": len(client_ids - accepted_ids),
        "duplicate_effects": int(database["duplicate_effects"]),
        "error_rate": error_rate,
        "retry_amplification": attempts / max(1, len(observations)),
        "measurement_seconds": measurement_elapsed,
        "service_rps": len(successful) / max(measurement_elapsed, 1e-9),
        **latency,
        "trace_identity_matches": sum(
            1 for item in successful if item.get("trace_identity_matches") is True
        ),
        "trace_expected": int(traces["expected"]),
        "trace_observed": int(traces["observed"]),
        "trace_complete": bool(traces["complete"]),
        "trace_summary": traces,
        "drain_event_count": len(database["drain_events"]),
        "maximum_drain_seconds": max(
            [float(item.get("drain_elapsed_seconds", 0)) for item in database["drain_events"]]
            or [0.0]
        ),
        "rollout_seconds": float(rollout_result["elapsed_seconds"]),
        "prometheus_up": prometheus["up"],
        "prometheus_recovery_seconds": prometheus["elapsed_seconds"],
        "prometheus_samples": prometheus["samples"],
        "before": before,
        "after": after,
        "timeline": timeline,
        "database": database,
        "observations": observations,
        "cleanup_passed": (
            after["ready_replicas"] == config.api.replicas
            and after["release_ids"] == ["new"]
        ),
    }
    return result


def execute_open_loop(
    *,
    base_url: str,
    prefix: str,
    seed: int,
    rps: float,
    duration_seconds: float,
    processing_delay_ms: int,
    config: S6RuntimeConfig,
) -> list[dict[str, Any]]:
    count = max(1, round(rps * duration_seconds))
    started = time.monotonic()
    futures: list[concurrent.futures.Future[dict[str, Any]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
        for index in range(count):
            target = started + index / rps
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            identity = f"{prefix}-{index:05d}"
            futures.append(
                executor.submit(
                    execute_probe,
                    base_url=base_url,
                    identity=identity,
                    seed=seed + index,
                    processing_delay_ms=processing_delay_ms,
                    config=config,
                    sampled=index % config.api.trace_sample_every == 0,
                )
            )
        return [future.result() for future in futures]


def execute_probe(
    *,
    base_url: str,
    identity: str,
    seed: int,
    processing_delay_ms: int,
    config: S6RuntimeConfig,
    sampled: bool,
) -> dict[str, Any]:
    traceparent, trace_id = deterministic_traceparent(identity, sampled=sampled)
    payload = {
        "logical_request_id": identity,
        "seed": seed,
        "processing_delay_ms": processing_delay_ms,
        "payload_token": "s6-controlled-replay",
    }
    attempts = []
    logical_started = time.monotonic()
    final: dict[str, Any] = {}
    for attempt in range(1, config.api.maximum_attempts_per_logical_request + 1):
        started = time.monotonic()
        try:
            response = requests.post(
                f"{base_url}/control-panel/v1/runtime/rollout-probes",
                json=payload,
                headers={"Idempotency-Key": identity, "traceparent": traceparent},
                timeout=config.api.request_timeout_seconds,
            )
            body = response.json() if response.content else {}
            attempts.append(
                {
                    "attempt": attempt,
                    "status": response.status_code,
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "trace_header": response.headers.get("x-evm-trace-id"),
                    "release_id": body.get("release_id") if isinstance(body, dict) else None,
                    "instance_id": body.get("instance_id") if isinstance(body, dict) else None,
                    "effect_id": body.get("effect_id") if isinstance(body, dict) else None,
                }
            )
            if response.status_code == 200 and isinstance(body, dict):
                final = body
                break
            if response.status_code not in {429, 502, 503, 504}:
                break
        except requests.RequestException as exc:
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "transport_error",
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "error": type(exc).__name__,
                }
            )
        if attempt < config.api.maximum_attempts_per_logical_request:
            time.sleep(config.api.retry_backoff_seconds * attempt)
    response_trace = next(
        (
            str(item.get("trace_header"))
            for item in reversed(attempts)
            if item.get("status") == 200
        ),
        "",
    )
    return {
        "logical_request_id": identity,
        "trace_id": trace_id,
        "sampled": sampled,
        "success": bool(final),
        "trace_identity_matches": response_trace == trace_id,
        "logical_latency_ms": (time.monotonic() - logical_started) * 1000,
        "effect_id": final.get("effect_id"),
        "release_id": final.get("release_id"),
        "instance_id": final.get("instance_id"),
        "attempts": attempts,
    }


def query_api_database(
    *,
    database_url: str,
    schema: str,
    request_prefix: str,
    drain_instance_ids: set[str],
) -> dict[str, Any]:
    validate_sql_identifier(schema)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT entity_id, state, payload FROM {schema}.entities "
                "WHERE entity_kind='s6_rollout_probe' AND entity_id LIKE %s ORDER BY entity_id",
                (f"{request_prefix}%",),
            )
            entities = [
                {"entity_id": row[0], "state": row[1], "payload": row[2]}
                for row in cursor.fetchall()
            ]
            cursor.execute(
                f"SELECT payload FROM {schema}.entities "
                "WHERE entity_kind='s6_api_drain' "
                "AND entity_id = ANY(%s) "
                "ORDER BY updated_at",
                (sorted(drain_instance_ids),),
            )
            drains = [row[0] for row in cursor.fetchall()]
    effect_ids = [str(item["payload"].get("effect_id") or "") for item in entities]
    accepted_ids = [str(item["entity_id"]) for item in entities]
    terminal_ids = [
        str(item["entity_id"]) for item in entities if item["state"] == "completed"
    ]
    return {
        "accepted_ids": accepted_ids,
        "terminal_ids": terminal_ids,
        "effect_ids": effect_ids,
        "duplicate_effects": len(effect_ids) - len(set(effect_ids)),
        "drain_events": drains,
        "expected_drain_instance_ids": sorted(drain_instance_ids),
    }


def api_repetition_passed(result: Mapping[str, Any], config: S6RuntimeConfig) -> bool:
    return (
        int(result["logical_requests"])
        == round(config.api.target_requests_per_second * config.api.measurement_seconds)
        and int(result["database_accepted"])
        == int(result["database_terminal"])
        == int(result["client_success"])
        and int(result["accepted_loss"]) == config.guardrails.accepted_loss
        and int(result["duplicate_effects"]) == config.guardrails.duplicate_effects
        and float(result["error_rate"]) <= config.guardrails.maximum_error_rate
        and int(result["drain_event_count"]) == config.api.replicas
        and float(result["p99_ms"]) <= config.guardrails.maximum_api_p99_ms
        and result["trace_summary"]["complete"] is True
        and result["prometheus_up"] is True
        and result["cleanup_passed"] is True
    )


def candidate_gate(config: S6RuntimeConfig) -> dict[str, Any]:
    gpu = config.gpu_handoff
    readiness = read_json(gpu.candidate_readiness_path)
    summary = read_json(gpu.candidate_summary_path)
    submission = read_json(gpu.candidate_release_submission_path)
    deployment = deployment_snapshot(gpu.target_namespace, gpu.target_deployment)
    expected_candidate = str(summary.get("candidate_id") or "")
    expected_model_sha = file_sha256(gpu.candidate_model_path)
    evaluation_check = next(
        (
            item
            for item in readiness.get("checks", [])
            if item.get("check_id") == "evaluation_report"
        ),
        {},
    )
    model_check = next(
        (
            item
            for item in readiness.get("checks", [])
            if item.get("check_id") == "model_artifact"
        ),
        {},
    )
    checks = {
        "readiness_passed": readiness.get("decision") == "ready"
        and readiness.get("status") == "pass",
        "candidate_summary_passed": summary.get("status") == "pass"
        and not summary.get("promotion_blockers"),
        "candidate_identity_matches": readiness.get("candidate_id") == expected_candidate
        == submission.get("candidate_id")
        == deployment["candidate_id"],
        "model_identity_matches": expected_model_sha
        == summary.get("model_sha256")
        == submission.get("model_digest")
        == deployment["model_sha256"],
        "container_identity_matches": submission.get("container_image_digest")
        == deployment["image"],
        "evaluation_digest_matches": evaluation_check.get("evidence_digest")
        == file_sha256(gpu.candidate_summary_path),
        "model_digest_matches": model_check.get("evidence_digest") == expected_model_sha,
        "ct_quality_metrics_present": all(
            key in summary.get("metrics", {}) for key in ("accuracy", "f1", "auroc")
        ),
    }
    if not all(checks.values()):
        raise S6RuntimeError(
            "s6_candidate_gate_failed:"
            + ",".join(sorted(key for key, value in checks.items() if not value))
        )
    return {
        "status": "passed",
        "checks": checks,
        "candidate_id": expected_candidate,
        "model_sha256": expected_model_sha,
        "image": deployment["image"],
        "quality_metrics": summary["metrics"],
        "evidence_sha256": {
            "readiness": file_sha256(gpu.candidate_readiness_path),
            "candidate_summary": file_sha256(gpu.candidate_summary_path),
            "release_submission": file_sha256(gpu.candidate_release_submission_path),
            "model": expected_model_sha,
        },
    }


def run_gpu_handoff(
    *,
    config: S6RuntimeConfig,
    source_revision: str,
    private_root: Path,
    repetition: int,
    candidate_gate_payload: Mapping[str, Any],
    inference_requests: int,
    phase: str,
) -> dict[str, Any]:
    private_root.mkdir(parents=True, exist_ok=False)
    source = deployment_snapshot(
        config.gpu_handoff.source_namespace,
        config.gpu_handoff.source_deployment,
    )
    target = deployment_snapshot(
        config.gpu_handoff.target_namespace,
        config.gpu_handoff.target_deployment,
    )
    if source["desired_replicas"] != 1 or source["ready_replicas"] != 1:
        raise S6RuntimeError("s6_gpu_source_not_exact_ready")
    if target["desired_replicas"] != 0:
        raise S6RuntimeError("s6_gpu_target_not_scaled_zero")
    source_ready_before = request_json(config.gpu_handoff.source_endpoint + "/ready")
    source_prediction_before, source_before_at = predict_once(
        config.gpu_handoff.source_endpoint,
        config.gpu_handoff.sample_image_uri,
        config.gpu_handoff.request_timeout_seconds,
    )
    run = handoff_lifecycle_run(
        private_root=private_root,
        source_revision=source_revision,
        repetition=repetition,
        phase=phase,
    )
    serving = ServingBundle(
        manifest_dir=private_root / "target-manifest",
        namespace=config.gpu_handoff.target_namespace,
        deployment_name=config.gpu_handoff.target_deployment,
        endpoint=config.gpu_handoff.target_endpoint,
        image=target["image"],
    )
    owner_timeline: list[dict[str, Any]] = []
    owner_stop = threading.Event()
    owner_monitor = threading.Thread(
        target=monitor_gpu_owners,
        kwargs={"config": config, "stop": owner_stop, "timeline": owner_timeline},
        daemon=True,
    )
    acquired = False
    released = False
    approval_reuse_rejected = False
    operation_error: Exception | None = None
    recovery_error: Exception | None = None
    owner_monitor.start()
    try:
        with handoff_environment():
            issue_gpu_handoff_approval(
                run,
                phase="staging_deployment",
                approver="s6-maintenance-approval",
                reason="S6 controlled single-GPU handoff validation",
                ttl_seconds=900,
                runner=subprocess.run,
            )
            acquire_gpu_handoff(run, serving, runner=subprocess.run)
            acquired = True
            owner_timeline.append(gpu_owner_snapshot(config))
            if owner_timeline[-1]["owner_count"] != 0:
                raise S6RuntimeError("s6_gpu_source_release_not_converged")
            try:
                consume_gpu_handoff_approval(
                    run,
                    "staging_deployment",
                    [
                        {
                            "namespace": source["namespace"],
                            "deployment": source["name"],
                            "uid": source["uid"],
                        }
                    ],
                )
            except GpuHandoffError as exc:
                approval_reuse_rejected = "approval_already_consumed" in str(exc)
            if not approval_reuse_rejected:
                raise S6RuntimeError("s6_gpu_approval_single_use_not_proven")
            run_checked(
                [
                    "kubectl",
                    "-n",
                    config.gpu_handoff.target_namespace,
                    "scale",
                    f"deployment/{config.gpu_handoff.target_deployment}",
                    "--replicas=1",
                ],
                timeout=30,
            )
            wait_deployment_ready(
                config.gpu_handoff.target_namespace,
                config.gpu_handoff.target_deployment,
                1,
                timeout=config.gpu_handoff.readiness_timeout_seconds,
            )
            owner_timeline.append(gpu_owner_snapshot(config))
            if (
                owner_timeline[-1]["owner_count"] != 1
                or owner_timeline[-1]["source_count"]
            ):
                raise S6RuntimeError("s6_gpu_target_owner_identity_invalid")
            target_ready = request_json(config.gpu_handoff.target_endpoint + "/ready")
            target_samples = predict_many(
                config.gpu_handoff.target_endpoint,
                config.gpu_handoff.sample_image_uri,
                config.gpu_handoff.request_timeout_seconds,
                inference_requests,
            )
            first_target_at = float(target_samples[0]["observed_monotonic"])
            target_last_at = float(target_samples[-1]["observed_monotonic"])
            target_latency = summarize_latencies(
                [float(item["http_elapsed_ms"]) for item in target_samples]
            )
            release_gpu_handoff(
                run,
                serving,
                runner=subprocess.run,
                reason="s6_controlled_rollback",
            )
            released = True
    except Exception as exc:
        operation_error = exc
    finally:
        if acquired and not released:
            try:
                with handoff_environment():
                    release_gpu_handoff(
                        run,
                        serving,
                        runner=subprocess.run,
                        reason="s6_failure_recovery",
                    )
                released = True
            except Exception as exc:
                recovery_error = exc
        owner_stop.set()
        owner_monitor.join(timeout=10)
        if owner_monitor.is_alive() and operation_error is None:
            operation_error = S6RuntimeError("s6_gpu_owner_monitor_not_stopped")
    if operation_error is not None:
        if recovery_error is not None:
            raise S6RuntimeError(
                "s6_gpu_handoff_and_recovery_failed:"
                f"operation={type(operation_error).__name__}:{operation_error};"
                f"recovery={type(recovery_error).__name__}:{recovery_error}"
            ) from operation_error
        raise operation_error
    if recovery_error is not None:
        raise S6RuntimeError(
            f"s6_gpu_handoff_recovery_failed:{type(recovery_error).__name__}:{recovery_error}"
        ) from recovery_error
    owner_timeline.append(gpu_owner_snapshot(config))
    wait_deployment_ready(
        config.gpu_handoff.source_namespace,
        config.gpu_handoff.source_deployment,
        1,
        timeout=config.gpu_handoff.readiness_timeout_seconds,
    )
    source_ready_after = request_json(config.gpu_handoff.source_endpoint + "/ready")
    source_prediction_after, source_after_at = predict_once(
        config.gpu_handoff.source_endpoint,
        config.gpu_handoff.sample_image_uri,
        config.gpu_handoff.request_timeout_seconds,
    )
    restored = deployment_snapshot(
        config.gpu_handoff.source_namespace,
        config.gpu_handoff.source_deployment,
    )
    target_after = deployment_snapshot(
        config.gpu_handoff.target_namespace,
        config.gpu_handoff.target_deployment,
    )
    owner_timeline.append(gpu_owner_snapshot(config))
    approval = read_json(
        private_root
        / "kubernetes/handoff_approvals/staging_deployment/approval-reference.json"
    )
    source_identity_before = serving_identity(source, source_ready_before)
    source_identity_after = serving_identity(restored, source_ready_after)
    target_identity = serving_identity(
        deployment_snapshot(
            config.gpu_handoff.target_namespace,
            config.gpu_handoff.target_deployment,
        ),
        target_ready,
    )
    zero_overlap = bool(owner_timeline) and all(
        "error" not in item and 0 <= int(item.get("owner_count", -1)) <= 1
        for item in owner_timeline
    )
    target_exact = (
        target_ready.get("candidate_id") == candidate_gate_payload["candidate_id"]
        and target_ready.get("model_sha256") == candidate_gate_payload["model_sha256"]
        and target_ready.get("cuda_available") is True
        and target_ready.get("device") == "cuda"
    )
    rollback_exact = source_identity_before == source_identity_after
    source_to_target = first_target_at - source_before_at
    target_to_source = source_after_at - target_last_at
    prometheus_observation = wait_prometheus_target_up(
        "evm-b0-production",
        timeout=config.api.trace_flush_timeout_seconds,
        poll_interval=config.gpu_handoff.runtime_poll_interval_seconds,
    )
    prometheus_health = "up" if prometheus_observation["up"] else "unhealthy"
    status = "passed" if all(
        (
            candidate_gate_payload.get("status") == "passed",
            approval.get("state") == "consumed",
            approval_reuse_rejected,
            zero_overlap,
            target_exact,
            rollback_exact,
            source_prediction_before.get("device") == "cuda",
            source_prediction_after.get("device") == "cuda",
            target_after["desired_replicas"] == 0,
            owner_timeline[-1]["source_count"] == 1,
            owner_timeline[-1]["target_count"] == 0,
            source_to_target > 0,
            target_to_source > 0,
            source_to_target <= config.gpu_handoff.maximum_interruption_seconds,
            target_to_source <= config.gpu_handoff.maximum_interruption_seconds,
            target_latency["p99_ms"] <= config.guardrails.maximum_gpu_p99_ms,
            prometheus_health == "up",
        )
    ) else "failed"
    result = {
        "schema_version": "evm.s6_gpu_handoff_repetition.v1",
        "phase": phase,
        "repetition": repetition,
        "status": status,
        "candidate_gate_passed": candidate_gate_payload.get("status") == "passed",
        "candidate_gate": dict(candidate_gate_payload),
        "approval_consumed_once": approval.get("state") == "consumed"
        and approval.get("single_use") is True
        and approval_reuse_rejected,
        "approval_reuse_rejected": approval_reuse_rejected,
        "approval": approval,
        "zero_owner_overlap": zero_overlap,
        "owner_timeline": owner_timeline,
        "target_identity_exact": target_exact,
        "target_identity": target_identity,
        "rollback_exact": rollback_exact,
        "source_identity_before": source_identity_before,
        "source_identity_after": source_identity_after,
        "source_to_target_interruption_seconds": source_to_target,
        "target_to_source_interruption_seconds": target_to_source,
        "target_p50_ms": target_latency["p50_ms"],
        "target_p95_ms": target_latency["p95_ms"],
        "target_p99_ms": target_latency["p99_ms"],
        "target_inference_count": len(target_samples),
        "target_cuda_inference": all(item["device"] == "cuda" for item in target_samples),
        "source_cuda_inference_restored": source_prediction_after.get("device") == "cuda",
        "prometheus_restored": prometheus_health == "up",
        "prometheus_health": prometheus_health,
        "prometheus_observation": prometheus_observation,
        "source_prediction_before": source_prediction_before,
        "source_prediction_after": source_prediction_after,
        "target_ready": target_ready,
        "target_after": target_after,
        "target_samples": target_samples,
    }
    canonical_write(private_root / "gpu-handoff-result.json", result)
    return result


def cleanup_runtime(
    *, config: S6RuntimeConfig, old_image: str, source_revision: str
) -> dict[str, Any]:
    errors = []
    try:
        run_checked(
            [
                "kubectl",
                "-n",
                config.gpu_handoff.target_namespace,
                "scale",
                f"deployment/{config.gpu_handoff.target_deployment}",
                "--replicas=0",
            ],
            timeout=30,
        )
        wait_deployment_scaled_zero(
            config.gpu_handoff.target_namespace,
            config.gpu_handoff.target_deployment,
            timeout=config.gpu_handoff.readiness_timeout_seconds,
        )
        wait_deployment_ready(
            config.gpu_handoff.source_namespace,
            config.gpu_handoff.source_deployment,
            1,
            timeout=config.gpu_handoff.readiness_timeout_seconds,
        )
    except Exception as exc:
        errors.append(f"gpu:{type(exc).__name__}:{exc}")
    try:
        patch_api_release(
            config=config,
            image=old_image,
            release_id="old",
            source_revision=source_revision,
        )
        wait_api_release_settled(config=config, release_id="old")
    except Exception as exc:
        errors.append(f"api:{type(exc).__name__}:{exc}")
    source = deployment_snapshot(
        config.gpu_handoff.source_namespace, config.gpu_handoff.source_deployment
    )
    target = deployment_snapshot(
        config.gpu_handoff.target_namespace, config.gpu_handoff.target_deployment
    )
    api = deployment_snapshot(config.api.namespace, config.api.deployment)
    prometheus_s6 = wait_prometheus_target_up(
        "evm-s6-api",
        timeout=config.api.trace_flush_timeout_seconds,
        poll_interval=config.api.runtime_poll_interval_seconds,
    )
    prometheus_serving = wait_prometheus_target_up(
        "evm-b0-production",
        timeout=config.api.trace_flush_timeout_seconds,
        poll_interval=config.gpu_handoff.runtime_poll_interval_seconds,
    )
    passed = (
        not errors
        and source["ready_replicas"] == 1
        and target["desired_replicas"] == 0
        and api["ready_replicas"] == config.api.replicas
        and api["release_ids"] == ["old"]
        and prometheus_serving["up"] is True
        and prometheus_s6["up"] is True
    )
    return {
        "passed": passed,
        "errors": errors,
        "source_ready": source["ready_replicas"],
        "target_desired": target["desired_replicas"],
        "api_ready": api["ready_replicas"],
        "api_release_ids": api["release_ids"],
        "prometheus_s6": "up" if prometheus_s6["up"] else "unhealthy",
        "prometheus_serving": "up" if prometheus_serving["up"] else "unhealthy",
        "prometheus_s6_recovery_seconds": prometheus_s6["elapsed_seconds"],
        "prometheus_serving_recovery_seconds": prometheus_serving["elapsed_seconds"],
    }


def patch_api_release(
    *, config: S6RuntimeConfig, image: str, release_id: str, source_revision: str
) -> None:
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "labels": {"evm.openai.local/release": release_id},
                    "annotations": {
                        "evm.openai.local/source-revision": source_revision,
                        "evm.openai.local/image-id": docker_image_identity(image)["image_id"],
                    },
                },
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "image": image,
                            "env": [
                                {"name": "EVM_API_RELEASE_ID", "value": release_id},
                                {"name": "GIT_COMMIT", "value": source_revision},
                            ],
                        }
                    ]
                },
            }
        }
    }
    run_checked(
        [
            "kubectl",
            "-n",
            config.api.namespace,
            "patch",
            f"deployment/{config.api.deployment}",
            "--type=strategic",
            "-p",
            json.dumps(patch, separators=(",", ":")),
        ],
        timeout=30,
    )


def wait_deployment_ready(
    namespace: str, deployment: str, replicas: int, timeout: float = 300
) -> dict[str, Any]:
    run_checked(
        [
            "kubectl",
            "-n",
            namespace,
            "rollout",
            "status",
            f"deployment/{deployment}",
            f"--timeout={int(timeout)}s",
        ],
        timeout=timeout + 10,
    )
    snapshot = deployment_snapshot(namespace, deployment)
    if snapshot["desired_replicas"] != replicas or snapshot["ready_replicas"] != replicas:
        raise S6RuntimeError(f"s6_deployment_not_exact_ready:{namespace}/{deployment}")
    return snapshot


def wait_api_release_settled(
    *, config: S6RuntimeConfig, release_id: str
) -> dict[str, Any]:
    run_checked(
        [
            "kubectl",
            "-n",
            config.api.namespace,
            "rollout",
            "status",
            f"deployment/{config.api.deployment}",
            f"--timeout={int(config.rolling.rollout_timeout_seconds)}s",
        ],
        timeout=config.rolling.rollout_timeout_seconds + 10,
    )
    deadline = time.monotonic() + config.rolling.rollout_timeout_seconds
    while time.monotonic() < deadline:
        snapshot = deployment_snapshot(config.api.namespace, config.api.deployment)
        if (
            snapshot["desired_replicas"] == config.api.replicas
            and snapshot["ready_replicas"] == config.api.replicas
            and len(snapshot["active_pods"]) == config.api.replicas
            and not snapshot["terminating_pods"]
            and snapshot["release_ids"] == [release_id]
        ):
            return snapshot
        time.sleep(config.api.runtime_poll_interval_seconds)
    raise S6RuntimeError(
        f"s6_api_release_not_settled:{config.api.namespace}/{config.api.deployment}:"
        f"release={release_id}"
    )


def wait_deployment_scaled_zero(
    namespace: str, deployment: str, *, timeout: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = deployment_snapshot(namespace, deployment)
        if snapshot["desired_replicas"] == 0 and not snapshot["active_pods"]:
            return snapshot
        time.sleep(0.25)
    raise S6RuntimeError(f"s6_deployment_not_scaled_zero:{namespace}/{deployment}")


def deployment_snapshot(namespace: str, deployment: str) -> dict[str, Any]:
    payload = kubectl_json(
        ["kubectl", "-n", namespace, "get", f"deployment/{deployment}", "-o", "json"]
    )
    selector = payload.get("spec", {}).get("selector", {}).get("matchLabels", {})
    selector_text = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
    pods = kubectl_json(
        ["kubectl", "-n", namespace, "get", "pods", "-l", selector_text, "-o", "json"]
    )
    container = payload["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item.get("value") for item in container.get("env", [])}
    active = []
    terminating = []
    for pod in pods.get("items", []):
        if pod.get("status", {}).get("phase") in {"Failed", "Succeeded"}:
            continue
        statuses = pod.get("status", {}).get("containerStatuses") or []
        item = {
            "name": pod["metadata"]["name"],
            "uid": pod["metadata"]["uid"],
            "phase": pod.get("status", {}).get("phase"),
            "ready": bool(statuses and statuses[0].get("ready")),
            "image_id": statuses[0].get("imageID") if statuses else None,
            "release_id": pod.get("metadata", {})
            .get("labels", {})
            .get("evm.openai.local/release"),
        }
        if pod.get("metadata", {}).get("deletionTimestamp"):
            terminating.append(item)
        else:
            active.append(item)
    return {
        "namespace": namespace,
        "name": deployment,
        "uid": payload["metadata"]["uid"],
        "generation": payload["metadata"]["generation"],
        "desired_replicas": int(payload.get("spec", {}).get("replicas") or 0),
        "ready_replicas": int(payload.get("status", {}).get("readyReplicas") or 0),
        "image": container["image"],
        "candidate_id": env.get("EVM_MODEL_CANDIDATE_ID"),
        "model_sha256": env.get("EVM_MODEL_SHA256"),
        "dataset_version": env.get("EVM_DATASET_VERSION"),
        "component_source_revision": env.get("EVM_EXPECTED_COMPONENT_SOURCE_REVISION"),
        "active_pods": active,
        "terminating_pods": terminating,
        "release_ids": sorted(
            {str(item["release_id"]) for item in active if item.get("release_id")}
        ),
    }


def monitor_api_pods(
    *, config: S6RuntimeConfig, stop: threading.Event, timeline: list[dict[str, Any]]
) -> None:
    while not stop.is_set():
        try:
            snapshot = deployment_snapshot(config.api.namespace, config.api.deployment)
            timeline.append(
                {
                    "observed_at": utc_now(),
                    "ready_replicas": snapshot["ready_replicas"],
                    "active_pods": snapshot["active_pods"],
                }
            )
        except Exception as exc:
            timeline.append(
                {"observed_at": utc_now(), "error": f"{type(exc).__name__}:{exc}"}
            )
        stop.wait(config.api.runtime_poll_interval_seconds)


def gpu_owner_snapshot(config: S6RuntimeConfig) -> dict[str, Any]:
    source = deployment_snapshot(
        config.gpu_handoff.source_namespace, config.gpu_handoff.source_deployment
    )
    target = deployment_snapshot(
        config.gpu_handoff.target_namespace, config.gpu_handoff.target_deployment
    )
    source_count = len(source["active_pods"])
    target_count = len(target["active_pods"])
    return {
        "observed_at": utc_now(),
        "source_count": source_count,
        "target_count": target_count,
        "owner_count": source_count + target_count,
        "source_pods": source["active_pods"],
        "target_pods": target["active_pods"],
    }


def monitor_gpu_owners(
    *, config: S6RuntimeConfig, stop: threading.Event, timeline: list[dict[str, Any]]
) -> None:
    while not stop.is_set():
        try:
            timeline.append(gpu_owner_snapshot(config))
        except Exception as exc:
            timeline.append(
                {"observed_at": utc_now(), "error": f"{type(exc).__name__}:{exc}"}
            )
        stop.wait(config.gpu_handoff.runtime_poll_interval_seconds)


def serving_identity(deployment: Mapping[str, Any], ready: Mapping[str, Any]) -> str:
    return payload_sha256(
        {
            "deployment_uid": deployment.get("uid"),
            "image": deployment.get("image"),
            "candidate_id": deployment.get("candidate_id"),
            "model_sha256": deployment.get("model_sha256"),
            "dataset_version": deployment.get("dataset_version"),
            "component_source_revision": deployment.get("component_source_revision"),
            "ready_candidate_id": ready.get("candidate_id"),
            "ready_model_sha256": ready.get("model_sha256"),
            "ready_dataset_version": ready.get("dataset_version"),
            "ready_device": ready.get("device"),
            "ready_cuda": ready.get("cuda_available"),
        }
    )


def handoff_lifecycle_run(
    *, private_root: Path, source_revision: str, repetition: int, phase: str
) -> LifecycleRun:
    now = utc_now()
    return LifecycleRun(
        run_id=f"s6-{phase}-r{repetition:02d}-{source_revision[:12]}",
        profile_id="s6-controlled-gpu-handoff",
        profile_version=1,
        profile_digest="0" * 64,
        effective_config_digest="1" * 64,
        source_commit=source_revision,
        source_branch="codex/distributed-scale-validation-plan",
        state="running",
        version=1,
        actor="codex-s6-runner",
        reason="S6 controlled single-GPU handoff validation",
        dry_run=False,
        execution_mode="automatic",
        created_at=now,
        updated_at=now,
        profile_snapshot_uri=str(private_root / "profile.json"),
        airflow_config_uri=str(private_root / "airflow-config.json"),
        airflow_runtime_uri=str(private_root / "airflow-runtime.json"),
        model_config_uri=str(private_root / "model-config.json"),
        model_runtime_uri=str(private_root / "model-runtime.json"),
        artifact_root=str(private_root),
    )


@contextmanager
def handoff_environment():
    keys = {
        "EVM_LIFECYCLE_SINGLE_GPU_HANDOFF_ENABLED": "true",
        "EVM_LIFECYCLE_GPU_HOLDERS": "evm-production/evm-b0-production",
    }
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def predict_many(
    endpoint: str, image_uri: str, timeout: float, count: int
) -> list[dict[str, Any]]:
    return [predict_once(endpoint, image_uri, timeout)[0] for _ in range(count)]


def predict_once(
    endpoint: str, image_uri: str, timeout: float
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    response = requests.post(
        endpoint + "/predict",
        json={"image_uri": image_uri},
        timeout=timeout,
    )
    response.raise_for_status()
    observed = time.monotonic()
    payload = response.json()
    payload["http_elapsed_ms"] = (observed - started) * 1000
    payload["observed_monotonic"] = observed
    return payload, observed


def wait_for_trace_ids(
    path: Path,
    *,
    offset: int,
    expected: set[str],
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    raw = b""
    observed: set[str] = set()
    while time.monotonic() < deadline:
        if path.is_file():
            with path.open("rb") as handle:
                handle.seek(offset)
                raw = handle.read()
        for line in raw.splitlines():
            try:
                payload = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            for resource in payload.get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        trace_id = str(span.get("traceId") or "")
                        if trace_id in expected and span.get("name") == API_TRACE_SPAN:
                            observed.add(trace_id)
        if observed == expected:
            break
        time.sleep(poll_interval)
    return {
        "expected": len(expected),
        "observed": len(observed),
        "missing": len(expected - observed),
        "complete": observed == expected,
        "raw_tail_bytes": len(raw),
        "raw_tail_sha256": hashlib.sha256(raw).hexdigest(),
    }


def prometheus_target_health(job: str) -> str:
    payload = request_json(PROMETHEUS_URL + "/api/v1/targets")
    matches = [
        item
        for item in payload.get("data", {}).get("activeTargets", [])
        if item.get("labels", {}).get("job") == job
    ]
    if len(matches) != 1:
        return "ambiguous"
    return str(matches[0].get("health") or "unknown")


def wait_prometheus_target_up(
    job: str, *, timeout: float, poll_interval: float
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout
    samples: list[dict[str, Any]] = []
    while True:
        elapsed = time.monotonic() - started
        health = prometheus_target_health(job)
        samples.append({"elapsed_seconds": elapsed, "health": health})
        if health == "up":
            return {"up": True, "elapsed_seconds": elapsed, "samples": samples}
        if time.monotonic() >= deadline:
            return {"up": False, "elapsed_seconds": elapsed, "samples": samples}
        time.sleep(poll_interval)


def docker_image_identity(image: str) -> dict[str, str]:
    payload = json.loads(run_checked(["docker", "image", "inspect", image], timeout=30).stdout)[0]
    labels = payload.get("Config", {}).get("Labels", {}) or {}
    return {
        "image": image,
        "image_id": str(payload.get("Id") or ""),
        "source_revision": str(labels.get("org.opencontainers.image.revision") or ""),
        "release": str(labels.get("evm.openai.s6.release") or ""),
    }


def cluster_gpu_allocatable() -> int:
    payload = kubectl_json(["kubectl", "get", "nodes", "-o", "json"])
    return sum(
        int(item.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu") or 0)
        for item in payload.get("items", [])
    )


def host_gpu_identity() -> dict[str, str | int]:
    completed = run_checked(
        [
            "nvidia-smi",
            "--query-gpu=uuid,name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=30,
    )
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise S6RuntimeError(f"s6_exact_host_gpu_identity_required:actual={len(rows)}")
    values = [value.strip() for value in rows[0].split(",")]
    if len(values) != 4 or not values[0].startswith("GPU-"):
        raise S6RuntimeError("s6_host_gpu_identity_invalid")
    return {
        "uuid": values[0],
        "name": values[1],
        "driver_version": values[2],
        "memory_total_mib": int(values[3]),
    }


def public_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "api_ready_replicas": payload["api_ready_replicas"],
        "prometheus_up": payload["prometheus_up"],
        "production_ready": payload["production_ready"],
        "staging_scaled_zero": payload["staging_scaled_zero"],
        "gpu_allocatable": payload["gpu_allocatable"],
        "gpu_identity_observed": bool(payload.get("gpu_identity", {}).get("uuid")),
    }


def public_api_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "repetition",
        "logical_requests",
        "attempts",
        "client_success",
        "database_accepted",
        "database_terminal",
        "accepted_loss",
        "client_success_without_acceptance",
        "duplicate_effects",
        "error_rate",
        "retry_amplification",
        "measurement_seconds",
        "service_rps",
        "mean_ms",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "maximum_ms",
        "trace_identity_matches",
        "trace_expected",
        "trace_observed",
        "trace_complete",
        "drain_event_count",
        "maximum_drain_seconds",
        "rollout_seconds",
        "prometheus_up",
        "prometheus_recovery_seconds",
        "cleanup_passed",
    )
    return {key: payload[key] for key in keys}


def public_gpu_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "phase",
        "repetition",
        "status",
        "candidate_gate_passed",
        "approval_consumed_once",
        "approval_reuse_rejected",
        "zero_owner_overlap",
        "target_identity_exact",
        "rollback_exact",
        "source_to_target_interruption_seconds",
        "target_to_source_interruption_seconds",
        "target_p50_ms",
        "target_p95_ms",
        "target_p99_ms",
        "target_inference_count",
        "target_cuda_inference",
        "source_cuda_inference_restored",
        "prometheus_restored",
    )
    return {key: payload[key] for key in keys}


def build_private_index(root: Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "private-evidence-index.json":
            continue
        artifacts.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    return {
        "schema_version": "evm.s6_private_evidence_index.v1",
        "generated_at": utc_now(),
        "artifact_count": len(artifacts),
        "total_bytes": sum(item["bytes"] for item in artifacts),
        "aggregate_sha256": payload_sha256(artifacts),
        "artifacts": artifacts,
    }


def validate_sql_identifier(value: str) -> None:
    if not value.replace("_", "").isalnum():
        raise S6RuntimeError("s6_sql_identifier_invalid")


def request_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise S6RuntimeError("s6_http_payload_invalid")
    return payload


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise S6RuntimeError(f"s6_json_payload_invalid:{path.name}")
    return payload


def kubectl_json(command: Sequence[str]) -> dict[str, Any]:
    payload = json.loads(run_checked(list(command), timeout=30).stdout)
    if not isinstance(payload, dict):
        raise S6RuntimeError("s6_kubectl_payload_invalid")
    return payload


def run_checked(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "command failed").strip()
        raise S6RuntimeError(f"s6_command_failed:{command[0]}:{message}")
    return completed


def git(*args: str) -> str:
    return run_checked(["git", *args], timeout=30).stdout.strip()


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )
    )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())

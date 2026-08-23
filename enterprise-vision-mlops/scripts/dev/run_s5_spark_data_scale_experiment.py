from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.evidence import write_public_json  # noqa: E402
from evm.scale_validation.s5_runtime import (  # noqa: E402
    S5RuntimeConfig,
    S5RuntimeError,
    analyze_s5_results,
    execute_columnar_control,
    file_sha256,
    nearest_existing_parent,
    parse_spark_event_log,
    prepare_criteo_dataset,
    private_evidence_index,
    stage_input_paths,
    utc_now,
)


def main() -> int:
    args = _parse_args()
    source_revision = _git("rev-parse", "HEAD")
    source_branch = _git("branch", "--show-current")
    tracked_dirty = _tracked_worktree_dirty()
    if not args.allow_dirty and tracked_dirty:
        raise S5RuntimeError("s5_experiment_requires_clean_tracked_revision")
    config = S5RuntimeConfig.from_path(args.config, data_root=args.data_root)
    manifest = prepare_criteo_dataset(config)
    image_tag = "checkpoint-dev" if tracked_dirty else source_revision[:12]
    image = f"{config.spark_image_repository}:{image_tag}"
    _preflight(config, image=image, build_image=not args.skip_build)
    suite_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{source_revision[:12]}"
    suite_root = args.private_root / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    failed_attempts: list[dict[str, Any]] = []
    try:
        if args.smoke_only:
            smoke = _run_preparation_smoke(
                config=config,
                manifest=manifest,
                image=image,
                suite_id=suite_id,
                suite_root=suite_root,
            )
            print(json.dumps(smoke, indent=2, ensure_ascii=False))
            return 0
        for stage in ("small", "medium", "large"):
            for repetition in range(1, config.repetitions + 1):
                logical_id = f"{suite_id}-columnar-{stage}-r{repetition}"
                result = execute_columnar_control(
                    config=config,
                    manifest=manifest,
                    stage=stage,
                    repetition=repetition,
                    logical_output_id=logical_id,
                )
                result["profile"] = "columnar_stage"
                _record_result(suite_root, result)
                results.append(result)

        for stage in ("small", "medium", "large"):
            for repetition in range(1, config.repetitions + 1):
                run_token = f"local-{stage}-r{repetition}-{uuid4().hex[:6]}"
                result = _run_local_spark(
                    config=config,
                    manifest=manifest,
                    image=image,
                    suite_id=suite_id,
                    run_token=run_token,
                    stage=stage,
                    repetition=repetition,
                    suite_root=suite_root,
                )
                results.append(result)

        for executor_count in config.executor_counts:
            for repetition in range(1, config.repetitions + 1):
                run_token = f"k8s-e{executor_count}-r{repetition}-{uuid4().hex[:6]}"
                result = _run_kubernetes_spark(
                    config=config,
                    manifest=manifest,
                    image=image,
                    suite_id=suite_id,
                    run_token=run_token,
                    stage="large",
                    repetition=repetition,
                    executor_count=executor_count,
                    suite_root=suite_root,
                    inject_executor_loss=False,
                )
                results.append(result)

        for repetition in range(1, config.repetitions + 1):
            run_token = f"retry-r{repetition}-{uuid4().hex[:6]}"
            result = _run_kubernetes_spark(
                config=config,
                manifest=manifest,
                image=image,
                suite_id=suite_id,
                run_token=run_token,
                stage="large",
                repetition=repetition,
                executor_count=4,
                suite_root=suite_root,
                inject_executor_loss=True,
            )
            replay = _run_kubernetes_spark(
                config=config,
                manifest=manifest,
                image=image,
                suite_id=suite_id,
                run_token=f"{run_token}-replay",
                stage="large",
                repetition=repetition,
                executor_count=4,
                suite_root=suite_root,
                inject_executor_loss=False,
                logical_output_id=result["logical_output_id"],
                replay_only=True,
            )
            result.update(
                {
                    "retry_output_digest": replay["output_digest"],
                    "retry_row_count": replay["effective_row_count"],
                    "retry_commit_state": replay["commit_state"],
                }
            )
            _record_result(suite_root, result, suffix="retry-closed")
            results.append(result)

        analysis = analyze_s5_results(results, config)
        cleanup = _cleanup_and_verify(config, suite_id=suite_id)
        if analysis["status"] != "passed":
            raise S5RuntimeError(f"s5_acceptance_failed:{analysis['acceptance']}")
        if not cleanup["passed"]:
            raise S5RuntimeError(f"s5_cleanup_failed:{cleanup}")
        private_index = private_evidence_index(suite_root)
        write_public_json(suite_root / "private-evidence-index.json", private_index)
        public = _public_summary(
            source_revision=source_revision,
            source_branch=source_branch,
            config=config,
            manifest=manifest,
            suite_id=suite_id,
            results=results,
            analysis=analysis,
            private_index=private_index,
            cleanup=cleanup,
            failed_attempts=failed_attempts,
        )
        write_public_json(args.public_evidence, public)
        print(json.dumps(public, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        failed_attempts.append(
            {
                "occurred_at": utc_now(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        write_public_json(suite_root / "failed-attempts.json", failed_attempts)
        _cleanup_and_verify(config, suite_id=suite_id)
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the S5 Spark scale matrix.")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "s5_spark_data_scale.toml",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.getenv("EVM_DATA_ROOT", "F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
        ),
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            os.getenv(
                "EVM_S5_PRIVATE_ROOT",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/private/s5",
            )
        ),
    )
    parser.add_argument(
        "--public-evidence",
        type=Path,
        default=ROOT / "docs" / "status" / "evidence" / "s5-spark-data-scale-experiment.json",
    )
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    return parser.parse_args()


def _run_preparation_smoke(
    *,
    config: S5RuntimeConfig,
    manifest: dict[str, Any],
    image: str,
    suite_id: str,
    suite_root: Path,
) -> dict[str, Any]:
    columnar = execute_columnar_control(
        config=config,
        manifest=manifest,
        stage="small",
        repetition=1,
        logical_output_id=f"{suite_id}-smoke-columnar",
    )
    columnar["profile"] = "preparation_smoke"
    _record_result(suite_root, columnar)
    local = _run_local_spark(
        config=config,
        manifest=manifest,
        image=image,
        suite_id=suite_id,
        run_token=f"smoke-local-{uuid4().hex[:6]}",
        stage="small",
        repetition=1,
        suite_root=suite_root,
    )
    kubernetes = _run_kubernetes_spark(
        config=config,
        manifest=manifest,
        image=image,
        suite_id=suite_id,
        run_token=f"smoke-k8s-{uuid4().hex[:6]}",
        stage="small",
        repetition=1,
        executor_count=1,
        suite_root=suite_root,
        inject_executor_loss=False,
    )
    cleanup = _cleanup_and_verify(config, suite_id=suite_id)
    digests = {columnar["output_digest"], local["output_digest"], kubernetes["output_digest"]}
    result = {
        "schema_version": "evm.s5_preparation_smoke.v1",
        "status": "passed"
        if len(digests) == 1 and cleanup["passed"]
        else "failed",
        "engines": [columnar, local, kubernetes],
        "cross_engine_output_digest_equal": len(digests) == 1,
        "cleanup": cleanup,
        "acceptance_credit": False,
    }
    write_public_json(suite_root / "preparation-smoke.json", result)
    if result["status"] != "passed":
        raise S5RuntimeError(f"s5_preparation_smoke_failed:{result}")
    return result


def _preflight(config: S5RuntimeConfig, *, image: str, build_image: bool) -> None:
    _run(["docker", "version"], timeout=30)
    if (
        shutil.disk_usage(nearest_existing_parent(config.private_root)).free
        < config.minimum_free_disk_bytes
    ):
        raise S5RuntimeError("s5_preflight_free_disk_below_bound")
    node = json.loads(_run(["kubectl", "get", "node", "docker-desktop", "-o", "json"]))
    if node.get("status", {}).get("allocatable", {}).get("cpu") is None:
        raise S5RuntimeError("s5_kubernetes_node_allocatable_missing")
    _run(["kubectl", "apply", "-f", str(ROOT / "infra/kubernetes/local/namespace.yaml")])
    _run(["kubectl", "apply", "-f", str(ROOT / "infra/kubernetes/local/storage.yaml")])
    _run(["kubectl", "apply", "-f", str(ROOT / "infra/kubernetes/s5/spark-rbac.yaml")])
    _run(
        [
            "kubectl",
            "wait",
            "--for=jsonpath={.status.phase}=Bound",
            f"pvc/{config.pvc_name}",
            "-n",
            config.namespace,
            "--timeout=60s",
        ]
    )
    if build_image:
        _run(
            [
                "docker",
                "build",
                "-f",
                str(ROOT / "infra/docker/spark/Dockerfile"),
                "-t",
                image,
                ".",
            ],
            timeout=900,
        )
    else:
        _run(["docker", "image", "inspect", image])


def _run_local_spark(
    *,
    config: S5RuntimeConfig,
    manifest: dict[str, Any],
    image: str,
    suite_id: str,
    run_token: str,
    stage: str,
    repetition: int,
    suite_root: Path,
) -> dict[str, Any]:
    report_host = suite_root / f"{run_token}-job-report.json"
    event_root_host = config.event_log_root / suite_id / run_token
    event_root_host.mkdir(parents=True, exist_ok=True)
    logical_id = f"{suite_id}-{run_token}"
    job_args = _job_args(
        config=config,
        manifest=manifest,
        stage=stage,
        repetition=repetition,
        run_token=run_token,
        engine="spark_local",
        executor_count=1,
        report_path=_container_path(config, report_host),
        logical_output_id=logical_id,
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"evm-s5-{run_token}",
        "-v",
        f"{config.raw_root.parents[3]}:/mnt/evm-data",
        image,
        "/opt/spark/bin/spark-submit",
        "--master",
        f"local[{config.local_threads}]",
        "--driver-memory",
        config.driver_memory,
        "--conf",
        "spark.eventLog.enabled=true",
        "--conf",
        "spark.eventLog.compress=false",
        "--conf",
        f"spark.eventLog.dir=file://{_container_path(config, event_root_host)}",
        "--conf",
        "spark.executor.processTreeMetrics.enabled=true",
        "--conf",
        "spark.executor.metrics.pollingInterval=1000",
        "local:///opt/evm/src/evm/scale_validation/s5_spark_job.py",
        *job_args,
    ]
    _run(command, timeout=config.maximum_run_seconds)
    report = _load_job_report(report_host)
    result = dict(report["result"])
    event_log = _find_event_log(event_root_host, str(result["application_id"]))
    result.update(parse_spark_event_log(event_log))
    result["profile"] = "spark_local_stage"
    _record_result(suite_root, result)
    return result


def _run_kubernetes_spark(
    *,
    config: S5RuntimeConfig,
    manifest: dict[str, Any],
    image: str,
    suite_id: str,
    run_token: str,
    stage: str,
    repetition: int,
    executor_count: int,
    suite_root: Path,
    inject_executor_loss: bool,
    logical_output_id: str | None = None,
    replay_only: bool = False,
) -> dict[str, Any]:
    report_host = suite_root / f"{run_token}-job-report.json"
    event_root_host = config.event_log_root / suite_id / run_token
    event_root_host.mkdir(parents=True, exist_ok=True)
    logical_id = logical_output_id or f"{suite_id}-{run_token}"
    retry_profile, repeat_factor, hold_ms = _kubernetes_run_identity(
        config,
        inject_executor_loss=inject_executor_loss,
        replay_only=replay_only,
    )
    job_args = _job_args(
        config=config,
        manifest=manifest,
        stage=stage,
        repetition=repetition,
        run_token=run_token,
        engine=f"spark_kubernetes_{executor_count}",
        executor_count=executor_count,
        report_path=_container_path(config, report_host),
        logical_output_id=logical_id,
        repeat_factor=repeat_factor,
        partition_hold_ms=hold_ms,
        profile="executor_loss_retry" if retry_profile else "kubernetes_scale",
    )
    job_name = _kubernetes_name(f"evm-s5-{run_token}")
    run_label = _kubernetes_name(run_token)
    command = " ".join(
        _shell_quote(item)
        for item in [
            "/opt/spark/bin/spark-submit",
            "--master",
            "k8s://https://kubernetes.default.svc",
            "--deploy-mode",
            "client",
            "--driver-memory",
            config.driver_memory,
            "--executor-memory",
            config.executor_memory,
            "--conf",
            f"spark.executor.instances={executor_count}",
            "--conf",
            f"spark.executor.cores={config.executor_cores}",
            "--conf",
            f"spark.executor.memoryOverhead={config.executor_memory_overhead}",
            "--conf",
            f"spark.kubernetes.namespace={config.namespace}",
            "--conf",
            f"spark.kubernetes.container.image={image}",
            "--conf",
            "spark.kubernetes.container.image.pullPolicy=IfNotPresent",
            "--conf",
            f"spark.kubernetes.authenticate.driver.serviceAccountName={config.service_account}",
            "--conf",
            f"spark.kubernetes.executor.label.evm_s5_run={run_label}",
            "--conf",
            "spark.driver.host=${POD_IP}",
            "--conf",
            "spark.driver.bindAddress=0.0.0.0",
            "--conf",
            "spark.driver.port=7078",
            "--conf",
            "spark.blockManager.port=7079",
            "--conf",
            "spark.eventLog.enabled=true",
            "--conf",
            "spark.eventLog.compress=false",
            "--conf",
            f"spark.eventLog.dir=file://{_container_path(config, event_root_host)}",
            "--conf",
            "spark.executor.processTreeMetrics.enabled=true",
            "--conf",
            "spark.executor.metrics.pollingInterval=1000",
            "--conf",
            "spark.task.maxFailures=4",
            "--conf",
            f"spark.kubernetes.executor.volumes.persistentVolumeClaim.s5.options.claimName={config.pvc_name}",
            "--conf",
            "spark.kubernetes.executor.volumes.persistentVolumeClaim.s5.mount.path=/mnt/evm-data",
            "local:///opt/evm/src/evm/scale_validation/s5_spark_job.py",
            *job_args,
        ]
    )
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": config.namespace,
            "labels": {"evm_s5_run": run_label, "evm_s5_role": "submitter"},
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 600,
            "template": {
                "metadata": {"labels": {"evm_s5_run": run_label}},
                "spec": {
                    "serviceAccountName": config.service_account,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "spark-driver",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/bash", "-lc"],
                            "args": [command],
                            "env": [
                                {
                                    "name": "POD_IP",
                                    "valueFrom": {
                                        "fieldRef": {"fieldPath": "status.podIP"}
                                    },
                                },
                                {"name": "PYTHONPATH", "value": "/opt/evm/src"},
                            ],
                            "volumeMounts": [
                                {"name": "large-data", "mountPath": "/mnt/evm-data"}
                            ],
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "1024Mi"},
                                "limits": {"cpu": "2", "memory": "2048Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "large-data",
                            "persistentVolumeClaim": {"claimName": config.pvc_name},
                        }
                    ],
                },
            },
        },
    }
    _run(["kubectl", "apply", "-f", "-"], input_text=json.dumps(job))
    observed_uids: set[str] = set()
    killed: dict[str, str] | None = None
    deadline = time.monotonic() + config.maximum_run_seconds
    while time.monotonic() < deadline:
        status = json.loads(
            _run(
                [
                    "kubectl",
                    "get",
                    "job",
                    job_name,
                    "-n",
                    config.namespace,
                    "-o",
                    "json",
                ]
            )
        )
        pods = _executor_pods(config, run_label)
        observed_uids.update(
            str(item.get("metadata", {}).get("uid") or "") for item in pods
        )
        if inject_executor_loss and killed is None:
            running = [
                item
                for item in pods
                if item.get("status", {}).get("phase") == "Running"
            ]
            if running:
                target = sorted(
                    running, key=lambda item: str(item["metadata"]["name"])
                )[0]
                killed = {
                    "name": str(target["metadata"]["name"]),
                    "uid": str(target["metadata"]["uid"]),
                }
                current = json.loads(
                    _run(
                        [
                            "kubectl",
                            "get",
                            "pod",
                            killed["name"],
                            "-n",
                            config.namespace,
                            "-o",
                            "json",
                        ]
                    )
                )
                if current["metadata"]["uid"] != killed["uid"]:
                    raise S5RuntimeError("s5_executor_uid_changed_before_injection")
                _run(
                    [
                        "kubectl",
                        "delete",
                        "pod",
                        killed["name"],
                        "-n",
                        config.namespace,
                        "--wait=false",
                    ]
                )
        if int(status.get("status", {}).get("succeeded", 0) or 0) >= 1:
            break
        if int(status.get("status", {}).get("failed", 0) or 0) >= 1:
            logs = _job_logs(config, job_name)
            raise S5RuntimeError(f"s5_kubernetes_job_failed:{job_name}:{logs[-2000:]}")
        time.sleep(1.0)
    else:
        raise S5RuntimeError(f"s5_kubernetes_job_timeout:{job_name}")
    report = _load_job_report(report_host)
    result = dict(report["result"])
    if replay_only:
        if result.get("commit_state") != "replayed":
            raise S5RuntimeError("s5_replay_did_not_reuse_commit")
    else:
        event_log = _find_event_log(event_root_host, str(result["application_id"]))
        result.update(parse_spark_event_log(event_log))
    result["profile"] = "executor_loss_retry" if retry_profile else "kubernetes_scale"
    result["executor_kill_observed"] = killed is not None
    result["executor_identity_count"] = len({uid for uid in observed_uids if uid})
    if inject_executor_loss and (
        killed is None or result["executor_identity_count"] <= executor_count
    ):
        raise S5RuntimeError("s5_executor_replacement_not_observed")
    _record_result(suite_root, result)
    _run(
        [
            "kubectl",
            "delete",
            "job",
            job_name,
            "-n",
            config.namespace,
            "--wait=true",
            "--ignore-not-found=true",
        ]
    )
    _delete_executor_pods(config, run_label)
    _wait_no_executor_pods(config, run_label)
    return result


def _kubernetes_run_identity(
    config: S5RuntimeConfig,
    *,
    inject_executor_loss: bool,
    replay_only: bool,
) -> tuple[bool, int, int]:
    retry_profile = inject_executor_loss or replay_only
    return (
        retry_profile,
        config.retry_generated_io_factor if retry_profile else 1,
        config.retry_partition_hold_ms if inject_executor_loss else 0,
    )


def _job_args(
    *,
    config: S5RuntimeConfig,
    manifest: dict[str, Any],
    stage: str,
    repetition: int,
    run_token: str,
    engine: str,
    executor_count: int,
    report_path: str,
    logical_output_id: str,
    repeat_factor: int = 1,
    partition_hold_ms: int = 0,
    profile: str = "scale",
) -> list[str]:
    result = []
    for path in stage_input_paths(config, manifest, stage):
        result.extend(["--input", _container_path(config, path)])
    result.extend(
        [
            "--report-path",
            report_path,
            "--commit-root",
            _container_path(config, config.output_root),
            "--logical-output-id",
            logical_output_id,
            "--application-name",
            f"evm-s5-{run_token}",
            "--engine",
            engine,
            "--stage",
            stage,
            "--profile",
            profile,
            "--repetition",
            str(repetition),
            "--semantic-row-count",
            str(manifest["stages"][stage]["semantic_row_count"]),
            "--repeat-factor",
            str(repeat_factor),
            "--executor-count",
            str(executor_count),
            "--output-partitions",
            str(config.output_partitions),
            "--shuffle-partitions",
            str(config.shuffle_partitions),
            "--skew-fraction-percent",
            str(config.skew_fraction_percent),
            "--partition-hold-ms",
            str(partition_hold_ms),
            "--claim-boundary",
            config.claim_boundary,
        ]
    )
    result.append("--adaptive-enabled" if config.adaptive_enabled else "--no-adaptive-enabled")
    return result


def _public_summary(
    *,
    source_revision: str,
    source_branch: str,
    config: S5RuntimeConfig,
    manifest: dict[str, Any],
    suite_id: str,
    results: list[dict[str, Any]],
    analysis: dict[str, Any],
    private_index: dict[str, Any],
    cleanup: dict[str, Any],
    failed_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    public_results = []
    for index, result in enumerate(results, start=1):
        public_results.append(
            {
                "point_id": f"s5-point-{index:03d}",
                **{
                    key: result[key]
                    for key in (
                        "engine",
                        "stage",
                        "repetition",
                        "profile",
                        "executor_count",
                        "semantic_row_count",
                        "effective_row_count",
                        "repeat_factor",
                        "generated_io_only",
                        "duration_seconds",
                        "records_per_second",
                        "mib_per_second",
                        "peak_executor_memory_bytes",
                        "gc_time_ms",
                        "gc_ratio",
                        "shuffle_read_bytes",
                        "shuffle_write_bytes",
                        "memory_spill_bytes",
                        "disk_spill_bytes",
                        "skew_ratio",
                        "missing_records",
                        "duplicate_records",
                        "output_digest",
                        "commit_state",
                    )
                    if key in result
                },
            }
        )
    return {
        "schema_version": "evm.s5_spark_data_scale_experiment.v1",
        "generated_at": utc_now(),
        "status": "verified" if analysis["status"] == "passed" else "failed",
        "verdict": analysis["status"],
        "suite_id": suite_id,
        "source_identity": {
            "revision": source_revision,
            "branch": source_branch,
            "config_sha256": config.sha256,
        },
        "dataset": {
            "dataset_id": config.dataset_id,
            "dataset_version": config.dataset_version,
            "source_revision": config.source_revision,
            "license": config.source_license,
            "shard_count": len(manifest["shards"]),
            "stage_semantic_rows": {
                name: int(value["semantic_row_count"])
                for name, value in manifest["stages"].items()
            },
            "generated_io_is_semantic_diversity": False,
        },
        "results": public_results,
        "analysis": analysis,
        "cleanup": cleanup,
        "private_evidence": {
            "artifact_count": private_index["artifact_count"],
            "total_bytes": private_index["total_bytes"],
            "index_sha256": file_sha256(
                config.private_root / suite_id / "private-evidence-index.json"
            ),
        },
        "failed_attempt_count": len(failed_attempts),
        "claim_boundary": config.claim_boundary,
    }


def _cleanup_and_verify(config: S5RuntimeConfig, *, suite_id: str) -> dict[str, Any]:
    _run(
        [
            "kubectl",
            "delete",
            "job",
            "-n",
            config.namespace,
            "-l",
            "evm_s5_role=submitter",
            "--ignore-not-found=true",
            "--wait=true",
        ],
        check=False,
    )
    _run(
        [
            "kubectl",
            "delete",
            "pods",
            "-n",
            config.namespace,
            "-l",
            "evm_s5_run",
            "--ignore-not-found=true",
            "--wait=true",
        ],
        check=False,
    )
    pods = json.loads(
        _run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                config.namespace,
                "-l",
                "evm_s5_run",
                "-o",
                "json",
            ],
            check=False,
            default='{"items": []}',
        )
    ).get("items", [])
    temporary_outputs = list(config.output_root.glob(f".{suite_id}*.building-*"))
    for path in temporary_outputs:
        shutil.rmtree(path, ignore_errors=True)
    return {
        "passed": len(pods) == 0 and not temporary_outputs,
        "executor_pods_remaining": len(pods),
        "temporary_outputs_removed": len(temporary_outputs),
        "pvc_phase": _pvc_phase(config),
    }


def _executor_pods(config: S5RuntimeConfig, run_label: str) -> list[dict[str, Any]]:
    payload = json.loads(
        _run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                config.namespace,
                "-l",
                f"evm_s5_run={run_label},spark-role=executor",
                "-o",
                "json",
            ]
        )
    )
    return list(payload.get("items", []))


def _wait_no_executor_pods(config: S5RuntimeConfig, run_label: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not _executor_pods(config, run_label):
            return
        time.sleep(1)
    raise S5RuntimeError(f"s5_executor_cleanup_timeout:{run_label}")


def _delete_executor_pods(config: S5RuntimeConfig, run_label: str) -> None:
    _run(
        [
            "kubectl",
            "delete",
            "pods",
            "-n",
            config.namespace,
            "-l",
            f"evm_s5_run={run_label},spark-role=executor",
            "--ignore-not-found=true",
            "--wait=true",
        ]
    )


def _pvc_phase(config: S5RuntimeConfig) -> str:
    payload = json.loads(
        _run(
            [
                "kubectl",
                "get",
                "pvc",
                config.pvc_name,
                "-n",
                config.namespace,
                "-o",
                "json",
            ]
        )
    )
    return str(payload.get("status", {}).get("phase") or "")


def _job_logs(config: S5RuntimeConfig, job_name: str) -> str:
    return _run(
        ["kubectl", "logs", f"job/{job_name}", "-n", config.namespace],
        check=False,
        default="",
    )


def _find_event_log(root: Path, application_id: str) -> Path:
    candidates = [
        path
        for path in root.rglob("*")
        if path.is_file() and application_id in path.name and not path.name.endswith(".inprogress")
    ]
    if not candidates:
        candidates = [path for path in root.rglob("*") if path.is_file()]
    if len(candidates) != 1:
        raise S5RuntimeError(
            f"s5_event_log_ambiguous:{application_id}:{len(candidates)}"
        )
    return candidates[0]


def _load_job_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise S5RuntimeError(f"s5_job_report_missing:{path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "passed" or not isinstance(payload.get("result"), dict):
        raise S5RuntimeError(f"s5_job_report_failed:{payload}")
    return payload


def _record_result(root: Path, result: dict[str, Any], *, suffix: str = "result") -> None:
    token = str(result.get("logical_output_id") or uuid4().hex)
    path = root / f"{_kubernetes_name(token)}-{suffix}.json"
    write_public_json(path, result)


def _container_path(config: S5RuntimeConfig, path: Path) -> str:
    data_root = config.raw_root.parents[3]
    return "/mnt/evm-data/" + path.resolve().relative_to(data_root.resolve()).as_posix()


def _kubernetes_name(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "-" for character in value.lower())
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:63].rstrip("-")


def _shell_quote(value: str) -> str:
    if "${POD_IP}" in value:
        return '"' + value.replace('"', '\\"') + '"'
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _tracked_worktree_dirty() -> bool:
    return bool(_git("status", "--short", "--untracked-files=no"))


def _git(*args: str) -> str:
    return _run(["git", *args], cwd=ROOT).strip()


def _run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    timeout: float = 120,
    check: bool = True,
    input_text: str | None = None,
    default: str | None = None,
) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        raise S5RuntimeError(
            f"s5_command_failed:{command[:4]}:{completed.returncode}:"
            f"{completed.stderr[-2000:]}:{completed.stdout[-2000:]}"
        )
    if completed.returncode != 0 and default is not None:
        return default
    return completed.stdout


if __name__ == "__main__":
    raise SystemExit(main())

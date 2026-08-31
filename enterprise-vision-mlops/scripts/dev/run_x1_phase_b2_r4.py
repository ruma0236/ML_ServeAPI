from __future__ import annotations

import argparse
import base64
import binascii
import json
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.phase_b2_r4 import (  # noqa: E402
    BoundedProcessRunner,
    ContractValidationError,
    RestoreCheckpoint,
    RestoreDeadline,
    RestoreHarness,
    RestoreStage,
    TimeoutContract,
    create_failure_evidence,
    create_restore_only_evidence,
    validate_manifest_runtime_contract,
    validate_release_readiness,
)


EXPECTED_PROMETHEUS_JOBS = {
    "evm-api",
    "evm-task-queue-worker",
    "evm-b0-production",
    "evm-otel-collector",
    "prometheus",
}
DEFAULT_COMPOSE_SERVICES = {
    "control-plane-postgres",
    "postgres",
    "airflow-postgres",
    "airflow-webserver",
    "airflow-scheduler",
    "minio",
    "mlflow",
    "api",
    "task-queue-worker",
    "control-panel",
    "otel-collector",
    "prometheus",
    "grafana",
}
REQUIRED_INVARIANTS = (
    "docker_engine",
    "compose_healthy",
    "kubernetes_readyz",
    "node_ready_1_of_1",
    "device_plugin_ready_1_of_1",
    "gpu_capacity_1",
    "gpu_allocatable_1",
    "b0_exact_uid",
    "b0_exact_image",
    "b0_replica_1_of_1",
    "b0_actual_cuda",
    "prometheus_5_of_5",
    "api_health_200",
    "api_ready_200",
    "api_revision_exact",
    "api_runtime_revision_matches",
    "queue_active_zero",
    "queue_leased_zero",
    "queue_outcome_unknown_zero",
    "active_jobs_zero",
    "active_claims_zero",
    "gpu_lease_zero",
    "x1_residue_zero",
)


class RestoreOnlyProbeSet:
    """Windows read-only probes for the checkpointed r4 restore path."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        contract: TimeoutContract,
        expected_revision: str,
        repository_root: Path,
    ) -> None:
        self.manifest = dict(manifest)
        self.contract = contract.validate()
        self.expected_revision = expected_revision
        self.repository_root = repository_root
        self.runner = BoundedProcessRunner(contract)
        self.expected = self._required_mapping(self.manifest, "expected_state")
        self.docker = self._find_executable(
            "docker",
            Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
        )
        self.kubectl = self._find_executable(
            "kubectl",
            Path("C:/Program Files/Docker/Docker/resources/bin/kubectl.exe"),
        )

    @staticmethod
    def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        nested = value.get(key)
        if not isinstance(nested, Mapping):
            raise ContractValidationError(f"manifest_mapping_required:{key}")
        return nested

    @staticmethod
    def _find_executable(name: str, fallback: Path) -> str:
        found = shutil.which(name)
        if found:
            return found
        if fallback.is_file():
            return str(fallback)
        raise ContractValidationError(f"required_executable_missing:{name}")

    @property
    def launch_budget_seconds(self) -> float:
        return (
            self.contract.wrapper_timeout_seconds
            + self.contract.residual_repoll_seconds
            + self.contract.stream_drain_seconds
        )

    def _run(
        self,
        deadline: RestoreDeadline,
        command: Sequence[str],
        *,
        name: str,
    ) -> dict[str, Any]:
        deadline.assert_can_launch(self.launch_budget_seconds)
        outcome = self.runner.run(command, name=name, cwd=self.repository_root)
        passed = (
            not outcome.timed_out
            and outcome.return_code == 0
            and not outcome.manual_intervention_required
            and not outcome.residual_pids
        )
        error = None
        if not passed:
            error = (
                f"{name}:return_code={outcome.return_code}:timed_out={outcome.timed_out}:"
                f"residual={list(outcome.residual_pids)}:{outcome.stderr[-1000:]}"
            )
        return {
            "passed": passed,
            "last_error": error,
            "residual_pids": list(outcome.residual_pids),
            # A timed-out root can create and reparent a descendant between
            # polling observations.  Even when every observed process exits
            # naturally inside 120 seconds, latch the restore path closed so
            # no later probe can race an unobserved descendant.
            "manual_intervention_required": (
                outcome.manual_intervention_required or outcome.timed_out
            ),
            "timeout_manual_latch": outcome.timed_out,
            "process_evidence": outcome.to_dict(),
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
        }

    def _kubectl_command(self, *arguments: str) -> list[str]:
        seconds = int(self.contract.kubectl_timeout_seconds)
        return [self.kubectl, f"--request-timeout={seconds}s", *arguments]

    @staticmethod
    def _json_object(
        result: Mapping[str, Any], name: str
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        if not result.get("passed"):
            return None, str(result.get("last_error") or f"{name}_command_failed")
        try:
            value = json.loads(str(result["stdout"]))
        except json.JSONDecodeError:
            return None, f"{name}_json_invalid"
        if not isinstance(value, Mapping):
            return None, f"{name}_json_object_required"
        return value, None

    @staticmethod
    def _failed_process_chain(
        results: Sequence[Mapping[str, Any]],
        *,
        last_error: str | None = None,
        invariant_names: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Stop a compound probe before another child can race a residual."""

        residual_pids = sorted(
            {
                int(pid)
                for result in results
                for pid in result.get("residual_pids", ())
            }
        )
        manual = bool(residual_pids) or any(
            bool(result.get("manual_intervention_required")) for result in results
        )
        process_evidence = [
            result.get("process_evidence")
            for result in results
            if result.get("process_evidence") is not None
        ]
        error = last_error or ";".join(
            str(result.get("last_error"))
            for result in results
            if result.get("last_error")
        )
        if not error:
            error = "process_chain_failed"
        return {
            "passed": False,
            "retryable": not manual and "eof" in error.lower(),
            "last_error": error,
            "manual_intervention_required": manual,
            "residual_pids": residual_pids,
            "invariants": {str(name): False for name in invariant_names},
            "process_evidence": process_evidence,
            "completed_process_count": sum(
                bool(result.get("passed")) for result in results
            ),
            "process_chain_stopped": True,
        }

    def docker_engine(self, deadline: RestoreDeadline) -> dict[str, Any]:
        result = self._run(
            deadline,
            [self.docker, "version", "--format", "{{json .Server}}"],
            name="docker-engine-readback",
        )
        if result["passed"]:
            try:
                server = json.loads(str(result["stdout"]))
            except json.JSONDecodeError:
                result["passed"] = False
                result["last_error"] = "docker_server_json_invalid"
                server = None
            result["server"] = server
        result["invariants"] = {"docker_engine": bool(result["passed"])}
        return result

    def compose(self, deadline: RestoreDeadline) -> dict[str, Any]:
        result = self._run(
            deadline,
            [
                self.docker,
                "compose",
                "-f",
                str(self.repository_root / "docker-compose.yml"),
                "ps",
                "--format",
                "json",
            ],
            name="compose-readback",
        )
        rows: list[dict[str, Any]] = []
        if result["passed"]:
            text = str(result["stdout"]).strip()
            try:
                decoded = json.loads(text)
                rows = decoded if isinstance(decoded, list) else [decoded]
            except json.JSONDecodeError:
                try:
                    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
                except json.JSONDecodeError:
                    result["passed"] = False
                    result["last_error"] = "compose_ps_json_invalid"
        expected_services = set(
            str(value)
            for value in self.expected.get("compose_services", DEFAULT_COMPOSE_SERVICES)
        )
        by_service = {str(row.get("Service")): row for row in rows}
        missing = sorted(expected_services - set(by_service))
        unhealthy = sorted(
            service
            for service in expected_services & set(by_service)
            if str(by_service[service].get("State", "")).lower() != "running"
            or str(by_service[service].get("Health", "")).lower() not in {"", "healthy"}
        )
        healthy = bool(result["passed"] and not missing and not unhealthy)
        result.update({"services": rows, "missing": missing, "unhealthy": unhealthy})
        result["passed"] = healthy
        result["last_error"] = None if healthy else result.get("last_error") or (
            f"compose_not_healthy:missing={missing}:unhealthy={unhealthy}"
        )
        result["invariants"] = {"compose_healthy": healthy}
        return result

    def kubernetes_api(self, deadline: RestoreDeadline) -> dict[str, Any]:
        result = self._run(
            deadline,
            self._kubectl_command("get", "--raw=/readyz"),
            name="kubernetes-readyz",
        )
        ready = bool(result["passed"] and str(result["stdout"]).strip().lower() == "ok")
        failure_text = (
            f"{result.get('last_error') or ''}\n{result.get('stderr') or ''}"
        ).lower()
        retryable_markers = (
            "eof",
            "connection refused",
            "i/o timeout",
            "tls handshake timeout",
            "server is currently unable",
        )
        result["passed"] = ready
        result["retryable"] = (
            not ready
            and not result.get("manual_intervention_required")
            and any(marker in failure_text for marker in retryable_markers)
        )
        result["last_error"] = None if ready else result.get("last_error") or "readyz_not_ok"
        result["invariants"] = {"kubernetes_readyz": ready}
        return result

    def node_device_plugin_gpu(self, deadline: RestoreDeadline) -> dict[str, Any]:
        invariant_names = (
            "node_ready_1_of_1",
            "device_plugin_ready_1_of_1",
            "gpu_capacity_1",
            "gpu_allocatable_1",
        )
        node_result = self._run(
            deadline,
            self._kubectl_command("get", "nodes", "-o", "json"),
            name="kubernetes-node-readback",
        )
        nodes, node_error = self._json_object(node_result, "nodes")
        if node_error:
            return self._failed_process_chain(
                [node_result],
                last_error=node_error,
                invariant_names=invariant_names,
            )
        assert nodes is not None
        items = list(nodes.get("items", []))
        node = items[0] if len(items) == 1 else {}
        conditions = {
            str(item.get("type")): str(item.get("status"))
            for item in node.get("status", {}).get("conditions", [])
        }
        capacity = str(
            node.get("status", {}).get("capacity", {}).get("nvidia.com/gpu", "0")
        )
        allocatable = str(
            node.get("status", {})
            .get("allocatable", {})
            .get("nvidia.com/gpu", "0")
        )
        node_invariants = {
            "node_ready_1_of_1": len(items) == 1
            and conditions.get("Ready") == "True",
            "gpu_capacity_1": capacity == "1",
            "gpu_allocatable_1": allocatable == "1",
        }
        if not all(node_invariants.values()):
            return {
                "passed": False,
                "last_error": f"node_gpu_invariant:{node_invariants}",
                "manual_intervention_required": False,
                "residual_pids": list(node_result["residual_pids"]),
                "invariants": {
                    **node_invariants,
                    "device_plugin_ready_1_of_1": False,
                },
                "node": node,
                "process_evidence": [node_result["process_evidence"]],
                "process_chain_stopped": True,
            }
        plugin_result = self._run(
            deadline,
            self._kubectl_command(
                "-n",
                "kube-system",
                "get",
                "daemonset/nvidia-device-plugin-daemonset",
                "-o",
                "json",
            ),
            name="device-plugin-readback",
        )
        plugin, plugin_error = self._json_object(plugin_result, "device_plugin")
        if plugin_error:
            return self._failed_process_chain(
                [node_result, plugin_result],
                last_error=plugin_error,
                invariant_names=invariant_names,
            )
        assert plugin is not None
        plugin_status = plugin.get("status", {})
        invariants = {
            **node_invariants,
            "device_plugin_ready_1_of_1": int(plugin_status.get("desiredNumberScheduled", 0))
            == 1
            and int(plugin_status.get("numberReady", 0)) == 1,
        }
        passed = all(invariants.values())
        return {
            "passed": passed,
            "last_error": None if passed else f"node_device_gpu_invariant:{invariants}",
            "invariants": invariants,
            "node": node,
            "device_plugin": plugin,
            "process_evidence": [
                node_result["process_evidence"],
                plugin_result["process_evidence"],
            ],
            "residual_pids": sorted(
                set(node_result["residual_pids"]) | set(plugin_result["residual_pids"])
            ),
        }

    def _http_json(
        self,
        deadline: RestoreDeadline,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        deadline.assert_can_launch(self.contract.kubectl_timeout_seconds)
        started_at = time.monotonic()
        try:
            response = requests.request(
                method,
                url,
                json=None if body is None else dict(body),
                timeout=self.contract.kubectl_timeout_seconds,
            )
            text = response.text
            try:
                payload: Any = response.json()
            except ValueError:
                payload = None
            return {
                "url": url,
                "method": method,
                "status": response.status_code,
                "body": payload,
                "body_text": text,
                "duration_seconds": time.monotonic() - started_at,
                "error": None,
            }
        except requests.RequestException as exc:
            return {
                "url": url,
                "method": method,
                "status": None,
                "body": None,
                "body_text": "",
                "duration_seconds": time.monotonic() - started_at,
                "error": f"{type(exc).__name__}:{exc}",
            }

    def b0_identity_cuda(self, deadline: RestoreDeadline) -> dict[str, Any]:
        invariant_names = (
            "b0_exact_uid",
            "b0_exact_image",
            "b0_replica_1_of_1",
            "b0_actual_cuda",
        )
        deployment_result = self._run(
            deadline,
            self._kubectl_command(
                "-n",
                "evm-production",
                "get",
                "deployment/evm-b0-production",
                "-o",
                "json",
            ),
            name="b0-deployment-readback",
        )
        deployment, deployment_error = self._json_object(
            deployment_result, "b0_deployment"
        )
        if deployment_error:
            return self._failed_process_chain(
                [deployment_result],
                last_error=deployment_error,
                invariant_names=invariant_names,
            )
        assert deployment is not None
        expected_b0 = self._required_mapping(self.expected, "b0")
        metadata = deployment.get("metadata", {})
        spec = deployment.get("spec", {})
        status = deployment.get("status", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        image = str(containers[0].get("image", "")) if len(containers) == 1 else ""
        identity_invariants = {
            "b0_exact_uid": str(metadata.get("uid", "")) == str(expected_b0["uid"]),
            "b0_exact_image": image == str(expected_b0["image"]),
            "b0_replica_1_of_1": int(spec.get("replicas", 0)) == 1
            and int(status.get("readyReplicas", 0)) == 1
            and int(status.get("availableReplicas", 0)) == 1,
        }
        if not all(identity_invariants.values()):
            invariants = {**identity_invariants, "b0_actual_cuda": False}
            return {
                "passed": False,
                "last_error": f"b0_identity_invariant:{invariants}",
                "manual_intervention_required": False,
                "invariants": invariants,
                "deployment": deployment,
                "process_evidence": [deployment_result["process_evidence"]],
                "residual_pids": list(deployment_result["residual_pids"]),
                "process_chain_stopped": True,
            }
        ready = self._http_json(
            deadline,
            "GET",
            str(expected_b0.get("ready_url", "http://127.0.0.1:30800/ready")),
        )
        ready_body = ready["body"] if isinstance(ready["body"], Mapping) else {}
        ready_cuda = (
            ready["status"] == 200
            and ready_body.get("status") == "ok"
            and ready_body.get("device") == "cuda"
        )
        if not ready_cuda:
            invariants = {**identity_invariants, "b0_actual_cuda": False}
            return {
                "passed": False,
                "last_error": f"b0_ready_cuda_invariant:{ready}",
                "manual_intervention_required": False,
                "invariants": invariants,
                "deployment": deployment,
                "ready": ready,
                "process_evidence": [deployment_result["process_evidence"]],
                "residual_pids": list(deployment_result["residual_pids"]),
                "process_chain_stopped": True,
            }
        prediction = self._http_json(
            deadline,
            "POST",
            str(expected_b0.get("predict_url", "http://127.0.0.1:30800/predict")),
            body={"image_uri": str(expected_b0["sample_image_uri"])},
        )
        prediction_body = (
            prediction["body"] if isinstance(prediction["body"], Mapping) else {}
        )
        invariants = {
            **identity_invariants,
            "b0_actual_cuda": prediction["status"] == 200
            and prediction_body.get("device") == "cuda"
            and bool(prediction_body.get("prediction")),
        }
        passed = all(invariants.values())
        return {
            "passed": passed,
            "last_error": None if passed else f"b0_invariant:{invariants}",
            "invariants": invariants,
            "deployment": deployment,
            "ready": ready,
            "prediction": prediction,
            "process_evidence": [deployment_result["process_evidence"]],
            "residual_pids": deployment_result["residual_pids"],
        }

    def prometheus(self, deadline: RestoreDeadline) -> dict[str, Any]:
        url = str(
            self.expected.get(
                "prometheus_targets_url",
                "http://127.0.0.1:9090/api/v1/targets",
            )
        )
        readback = self._http_json(deadline, "GET", url)
        body = readback["body"] if isinstance(readback["body"], Mapping) else {}
        targets = body.get("data", {}).get("activeTargets", [])
        jobs = sorted(str(item.get("labels", {}).get("job")) for item in targets)
        up = sum(str(item.get("health")) == "up" for item in targets)
        expected_jobs = set(
            str(item)
            for item in self.expected.get("prometheus_jobs", EXPECTED_PROMETHEUS_JOBS)
        )
        passed = (
            readback["status"] == 200
            and len(targets) == 5
            and up == 5
            and set(jobs) == expected_jobs
        )
        return {
            "passed": passed,
            "last_error": None if passed else f"prometheus_not_exact_5_of_5:{jobs}:{up}",
            "invariants": {"prometheus_5_of_5": passed},
            "readback": readback,
            "jobs": jobs,
            "total": len(targets),
            "up": up,
        }

    def api_release_identity(self, deadline: RestoreDeadline) -> dict[str, Any]:
        base_url = str(self.expected.get("api_base_url", "http://127.0.0.1:8000"))
        health = self._http_json(deadline, "GET", f"{base_url}/health")
        ready = self._http_json(deadline, "GET", f"{base_url}/ready")
        ready_body = ready["body"] if isinstance(ready["body"], Mapping) else {}
        revision = validate_release_readiness(
            int(ready["status"] or 0), ready_body, self.expected_revision
        )
        invariants = {
            "api_health_200": health["status"] == 200,
            "api_ready_200": ready["status"] == 200,
            **revision,
        }
        passed = all(invariants.values())
        return {
            "passed": passed,
            "last_error": None if passed else f"api_release_identity:{invariants}",
            "invariants": invariants,
            "health": health,
            "ready": ready,
        }

    def queue_jobs_lease_residue(self, deadline: RestoreDeadline) -> dict[str, Any]:
        invariant_names = (
            "queue_active_zero",
            "queue_leased_zero",
            "queue_outcome_unknown_zero",
            "active_jobs_zero",
            "active_claims_zero",
            "gpu_lease_zero",
            "x1_residue_zero",
        )
        process_results: list[Mapping[str, Any]] = []
        sql = (
            "SELECT "
            "(SELECT count(*) FILTER (WHERE state IN "
            "('available','retry_wait','leased','runtime_pending','outcome_unknown')) "
            "FROM evm_control_plane.task_admission_queue),"
            "(SELECT count(*) FILTER (WHERE state='leased') "
            "FROM evm_control_plane.task_admission_queue),"
            "(SELECT count(*) FILTER (WHERE state='outcome_unknown') "
            "FROM evm_control_plane.task_admission_queue),"
            "(SELECT count(*) FROM evm_control_plane.lifecycle_claims "
            "WHERE released_at IS NULL AND expires_at > clock_timestamp());"
        )
        queue_result = self._run(
            deadline,
            [
                self.docker,
                "exec",
                "evm-control-plane-postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "evm_control_plane",
                "-d",
                "evm_control_plane",
                "-At",
                "-F",
                "|",
                "-c",
                sql,
            ],
            name="queue-readback",
        )
        process_results.append(queue_result)
        if not queue_result["passed"]:
            return self._failed_process_chain(
                process_results, invariant_names=invariant_names
            )
        try:
            values = [
                int(item)
                for item in str(queue_result["stdout"]).strip().split("|")
            ]
        except ValueError:
            return self._failed_process_chain(
                process_results,
                last_error="queue_readback_values_invalid",
                invariant_names=invariant_names,
            )
        queues_valid = len(values) == 4
        if not queues_valid:
            return self._failed_process_chain(
                process_results,
                last_error="queue_readback_field_count_invalid",
                invariant_names=invariant_names,
            )
        queues = values[:3] if queues_valid else [-1, -1, -1]
        database_active_claims = values[3] if queues_valid else -1
        queue_invariants = {
            "queue_active_zero": queues[0] == 0,
            "queue_leased_zero": queues[1] == 0,
            "queue_outcome_unknown_zero": queues[2] == 0,
            "active_claims_zero": database_active_claims == 0,
        }
        if not all(queue_invariants.values()):
            failure = self._failed_process_chain(
                process_results,
                last_error=f"queue_or_database_claim_nonzero:{queue_invariants}",
                invariant_names=invariant_names,
            )
            failure.update(
                {
                    "invariants": {
                        **failure["invariants"],
                        **queue_invariants,
                    },
                    "queues": {
                        "active": queues[0],
                        "leased": queues[1],
                        "outcome_unknown": queues[2],
                    },
                    "database_active_claims": database_active_claims,
                }
            )
            return failure

        jobs_result = self._run(
            deadline,
            self._kubectl_command("get", "jobs", "-A", "-o", "json"),
            name="active-jobs-readback",
        )
        process_results.append(jobs_result)
        jobs_payload, jobs_error = self._json_object(jobs_result, "active_jobs")
        if jobs_error:
            return self._failed_process_chain(
                process_results,
                last_error=jobs_error,
                invariant_names=invariant_names,
            )
        assert jobs_payload is not None
        job_items = list(jobs_payload.get("items", []))
        kubernetes_active_jobs = sum(
            int(item.get("status", {}).get("active", 0) or 0) for item in job_items
        )
        if kubernetes_active_jobs != 0:
            failure = self._failed_process_chain(
                process_results,
                last_error=f"kubernetes_active_jobs_nonzero:{kubernetes_active_jobs}",
                invariant_names=invariant_names,
            )
            failure["active_jobs"] = {
                "kubernetes_active": kubernetes_active_jobs,
                "kubernetes_total": len(job_items),
            }
            return failure

        active_job_roots = [Path(str(item)) for item in self.expected.get("active_job_roots", [])]
        claim_roots = [Path(str(item)) for item in self.expected.get("active_claim_roots", [])]
        lease_path = Path(
            str(
                self.expected.get(
                    "gpu_lease_path",
                    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease/active.json",
                )
            )
        )
        residue_paths = [Path(str(item)) for item in self.expected.get("x1_residue_paths", [])]
        file_active_jobs = sum(
            1 for root in active_job_roots if root.exists() for item in root.iterdir() if item.is_file()
        )
        file_active_claims = sum(
            1 for root in claim_roots if root.exists() for item in root.iterdir() if item.is_file()
        )
        residue = [str(path) for path in residue_paths if path.exists()]
        if file_active_jobs or file_active_claims or lease_path.exists() or residue:
            failure = self._failed_process_chain(
                process_results,
                last_error=(
                    "file_job_claim_lease_or_residue_nonzero:"
                    f"jobs={file_active_jobs}:claims={file_active_claims}:"
                    f"lease={lease_path.exists()}:residue={residue}"
                ),
                invariant_names=invariant_names,
            )
            failure.update(
                {
                    "file_active_jobs": file_active_jobs,
                    "file_active_claims": file_active_claims,
                    "gpu_lease_path": str(lease_path),
                    "residue_paths": residue,
                }
            )
            return failure

        container_result = self._run(
            deadline,
            [
                self.docker,
                "ps",
                "-a",
                "--filter",
                str(self.expected.get("x1_docker_name_filter", "name=evm-x1")),
                "--format",
                "{{json .}}",
            ],
            name="x1-docker-residue-readback",
        )
        process_results.append(container_result)
        if not container_result["passed"]:
            return self._failed_process_chain(
                process_results, invariant_names=invariant_names
            )
        container_lines = [
            line for line in str(container_result["stdout"]).splitlines() if line.strip()
        ]
        try:
            x1_containers = [json.loads(line) for line in container_lines]
        except json.JSONDecodeError:
            return self._failed_process_chain(
                process_results,
                last_error="x1_docker_residue_json_invalid",
                invariant_names=invariant_names,
            )
        open_ports: list[int] = []
        for raw_port in self.expected.get("x1_ports", [31120, 31121, 31122]):
            deadline.assert_can_launch(0.5)
            port = int(raw_port)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
                probe_socket.settimeout(0.5)
                if probe_socket.connect_ex(("127.0.0.1", port)) == 0:
                    open_ports.append(port)
        if open_ports:
            failure = self._failed_process_chain(
                process_results,
                last_error=f"x1_ports_open:{open_ports}",
                invariant_names=invariant_names,
            )
            failure["open_ports"] = open_ports
            return failure

        # K8s residue is required whenever the manifest supplies selectors.  An
        # unreadable query is a failure, never proof of absence.
        residue_queries: list[dict[str, Any]] = []
        for index, selector in enumerate(self.expected.get("x1_kubernetes_selectors", []), start=1):
            result = self._run(
                deadline,
                self._kubectl_command("get", "all", "-A", "-l", str(selector), "-o", "json"),
                name=f"x1-residue-readback-{index}",
            )
            process_results.append(result)
            payload, payload_error = self._json_object(
                result, f"x1_residue_{index}"
            )
            if payload_error:
                return self._failed_process_chain(
                    process_results,
                    last_error=payload_error,
                    invariant_names=invariant_names,
                )
            assert payload is not None
            count = len(payload.get("items", []))
            residue_queries.append(
                {
                    "selector": str(selector),
                    "count": count,
                    "process_evidence": result["process_evidence"],
                }
            )
            if count != 0:
                failure = self._failed_process_chain(
                    process_results,
                    last_error=f"x1_kubernetes_residue_nonzero:{selector}:{count}",
                    invariant_names=invariant_names,
                )
                failure["kubernetes_residue"] = residue_queries
                return failure
        k8s_residue_count = sum(item["count"] for item in residue_queries)
        invariants = {
            "queue_active_zero": queues_valid and queues[0] == 0,
            "queue_leased_zero": queues_valid and queues[1] == 0,
            "queue_outcome_unknown_zero": queues_valid and queues[2] == 0,
            "active_jobs_zero": kubernetes_active_jobs == 0 and file_active_jobs == 0,
            "active_claims_zero": database_active_claims == 0 and file_active_claims == 0,
            "gpu_lease_zero": not lease_path.exists(),
            "x1_residue_zero": (
                not residue
                and k8s_residue_count == 0
                and not x1_containers
                and not open_ports
            ),
        }
        passed = all(invariants.values())
        return {
            "passed": passed,
            "last_error": None if passed else f"queue_jobs_lease_residue:{invariants}",
            "invariants": invariants,
            "queues": {
                "active": queues[0],
                "leased": queues[1],
                "outcome_unknown": queues[2],
            },
            "active_jobs": {
                "kubernetes_active": kubernetes_active_jobs,
                "kubernetes_total": len(job_items),
                "file_markers": file_active_jobs,
            },
            "active_claims": {
                "database_active": database_active_claims,
                "file_markers": file_active_claims,
            },
            "gpu_lease_path": str(lease_path),
            "residue_paths": residue,
            "kubernetes_residue": residue_queries,
            "docker_residue": x1_containers,
            "open_ports": open_ports,
            "process_evidence": [
                result["process_evidence"] for result in process_results
            ],
            "residual_pids": sorted(
                {
                    int(pid)
                    for result in process_results
                    for pid in result["residual_pids"]
                }
            ),
        }

    def probes(self) -> dict[str, Any]:
        return {
            RestoreStage.DOCKER_ENGINE.value: self.docker_engine,
            RestoreStage.COMPOSE.value: self.compose,
            RestoreStage.KUBERNETES_API.value: self.kubernetes_api,
            RestoreStage.NODE_DEVICE_PLUGIN_GPU.value: self.node_device_plugin_gpu,
            RestoreStage.B0_IDENTITY_CUDA.value: self.b0_identity_cuda,
            RestoreStage.PROMETHEUS.value: self.prometheus,
            RestoreStage.API_RELEASE_IDENTITY.value: self.api_release_identity,
            RestoreStage.QUEUE_JOBS_LEASE_RESIDUE.value: self.queue_jobs_lease_residue,
        }


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ContractValidationError(f"{label}_file_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ContractValidationError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label}_object_required:{path}")
    return value


def _full_revision(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 40 or any(item not in "0123456789abcdef" for item in normalized):
        raise ContractValidationError("full_canonical_revision_required")
    return normalized


def _decode_launcher_evidence(encoded: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("launcher_evidence_base64_json_invalid") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("launcher_evidence_object_required")
    token = value.get("token_evidence", value.get("token"))
    chain = value.get("sha_chain")
    if not isinstance(token, Mapping) or not isinstance(chain, Mapping):
        raise ContractValidationError("launcher_token_and_sha_chain_evidence_required")

    administrator = token.get("administrator", token.get("Administrator"))
    integrity = str(
        token.get(
            "integrity",
            token.get("Integrity", token.get("integrity_level", token.get("IntegrityLevel", ""))),
        )
    ).lower()
    elevation = str(
        token.get(
            "token_elevation_type",
            token.get("TokenElevationType", token.get("elevation_type", "")),
        )
    ).lower()
    if administrator is not True:
        raise ContractValidationError("launcher_administrator_token_required")
    if "high" not in integrity and "system" not in integrity and "s-1-16-12288" not in integrity:
        raise ContractValidationError("launcher_integrity_high_or_system_required")
    if "full" not in elevation:
        raise ContractValidationError("launcher_full_elevation_token_required")

    for name in ("outer", "bridge", "manifest"):
        digest = str(chain.get(name, "")).lower()
        if len(digest) != 64 or any(item not in "0123456789abcdef" for item in digest):
            raise ContractValidationError(f"launcher_sha_chain_evidence_invalid:{name}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the checkpointed, read-only S8-V4/X1 Phase B2 r4 restore harness."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--launcher-evidence-base64", required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("restore-only",), default="restore-only")
    return parser.parse_args()


def _execute(args: argparse.Namespace) -> int:
    launcher_evidence = _decode_launcher_evidence(args.launcher_evidence_base64)
    manifest = _read_object(args.manifest, "manifest")
    checkpoint_payload = _read_object(args.checkpoint, "checkpoint")
    expected_revision = _full_revision(args.expected_revision)
    runtime_contract = TimeoutContract().validate()
    validate_manifest_runtime_contract(manifest, runtime_contract)
    if str(manifest.get("canonical_revision", "")).lower() != expected_revision:
        raise ContractValidationError("manifest_canonical_revision_mismatch")
    checkpoint_counts = checkpoint_payload.get("call_counts", checkpoint_payload)
    if not isinstance(checkpoint_counts, Mapping):
        raise ContractValidationError("checkpoint_call_counts_mapping_required")
    checkpoint = RestoreCheckpoint.from_r3_call_counts(checkpoint_counts)

    probes = RestoreOnlyProbeSet(
        manifest=manifest,
        contract=runtime_contract,
        expected_revision=expected_revision,
        repository_root=args.repository_root.resolve(),
    )
    harness = RestoreHarness(
        contract=runtime_contract,
        probes=probes.probes(),
        expected_revision=expected_revision,
        required_invariants=REQUIRED_INVARIANTS,
        max_probe_attempts=3,
    )
    report = harness.run_restore_only(checkpoint)
    metadata = {
        "manifest": str(args.manifest.resolve()),
        "checkpoint": str(args.checkpoint.resolve()),
        "canonical_revision": expected_revision,
        "docker_off_probe_executed": False,
        "service_lifecycle_actions_executed": 0,
        "launcher_evidence": launcher_evidence,
    }
    if report.passed:
        evidence = create_restore_only_evidence(
            args.output_directory.resolve(), report, metadata=metadata
        )
        print(json.dumps({"decision": "restore_only_pass", "report": report.to_dict(), **evidence}))
        return 0

    evidence = create_failure_evidence(
        args.output_directory.resolve(), report, metadata=metadata
    )
    print(
        json.dumps(
            {
                "decision": "manual_intervention_required",
                "report": report.to_dict(),
                **evidence,
            }
        )
    )
    return 2


def main() -> int:
    args = parse_args()
    try:
        return _execute(args)
    except Exception as exc:
        failure = {
            "schema": "s8-v4-x1-phase-b2-r4-restore-bootstrap-failure/v1",
            "mode": "restore-only",
            "passed": False,
            "manual_intervention_required": True,
            "decision": "manual_intervention_required",
            "error": f"{type(exc).__name__}:{exc}",
            "call_counts": {
                "docker_off_probe": 0,
                "compose_stop": 0,
                "desktop_stop": 0,
                "wsl_shutdown": 0,
                "desktop_start": 0,
                "compose_start": 0,
            },
        }
        try:
            try:
                launcher_evidence: Mapping[str, Any] = _decode_launcher_evidence(
                    args.launcher_evidence_base64
                )
            except Exception as launcher_exc:
                launcher_evidence = {
                    "unavailable": True,
                    "error": f"{type(launcher_exc).__name__}:{launcher_exc}",
                }
            evidence = create_failure_evidence(
                args.output_directory.resolve(),
                failure,
                metadata={
                    "manifest": str(args.manifest.resolve()),
                    "checkpoint": str(args.checkpoint.resolve()),
                    "docker_off_probe_executed": False,
                    "service_lifecycle_actions_executed": 0,
                    "launcher_evidence": dict(launcher_evidence),
                },
            )
        except Exception as seal_exc:
            evidence = {
                "failure_seal_error": f"{type(seal_exc).__name__}:{seal_exc}",
            }
        print(json.dumps({**failure, **evidence}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evm.control_panel.scenario_workloads import (  # noqa: E402
    GpuLease,
    acquire_scale_validation_gpu_lease,
    assert_scale_validation_gpu_lease_owner,
)
from evm.scale_validation.x1_contract import (  # noqa: E402
    X1Contract,
    canonical_sha256,
    sha256_file,
)
from evm.scale_validation.x1_topology import (  # noqa: E402
    NAMESPACE,
    TRITON_IMAGE,
    TRITON_NAME,
    TRITON_WSL_LD_LIBRARY_PATH,
    kubernetes_resource_list,
)
from scripts.dev import run_s8_v4_x1_calibration as base  # noqa: E402


DLRM_MODEL_ID = "criteo_dlrm_lite"
REPEATED_REQUESTS = 64
REQUEST_TIMEOUT_SECONDS = 10
PRIVATE_BASE = base.PRIVATE_BASE / "diagnostics"
EXPECTED_SOURCE_SUITE_ID = "x1-canonical-20260829t230109z-3e7df802"
EXPECTED_SOURCE_AGGREGATE_SHA256 = (
    "ec514ccfb31fd81b645cc31ea6a7018a866b53cc9ac4a902fea35becbbd7a031"
)
EXPECTED_SOURCE_INDEX_SHA256 = "f5cf1c20c8534088d3e05a4852a7f20ee1ac796edeb893843b37e7e6c036b1f9"
EXPECTED_DLRM_MODEL_SHA256 = "1fa6a64ca3de07cbc5633b6516861ef23a421c0cdf61d69aa8a1f2fabf6eff63"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the bounded non-credit X1 DLRM backend isolation diagnostic."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
    )
    parser.add_argument("--data-root", type=Path, default=base.DATA_ROOT)
    parser.add_argument("--private-base", type=Path, default=PRIVATE_BASE)
    parser.add_argument("--source-suite-root", type=Path, required=True)
    parser.add_argument("--maintenance-approved", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, reason: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise base.X1ExperimentError(f"x1_dlrm_diagnostic_{reason}") from exc
    if not isinstance(payload, dict):
        raise base.X1ExperimentError(f"x1_dlrm_diagnostic_{reason}")
    return payload


def validate_source_suite(root: Path, contract: X1Contract, *, data_root: Path) -> dict[str, Any]:
    resolved = root.resolve()
    try:
        resolved.relative_to(base.PRIVATE_BASE.resolve())
    except ValueError as exc:
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_containment") from exc
    index_path = resolved / "private-evidence-index.json"
    stored_index = _load_json(index_path, "source_index")
    projected_index = base.private_index(resolved)
    if (
        resolved.name != EXPECTED_SOURCE_SUITE_ID
        or stored_index != projected_index
        or stored_index.get("aggregate_sha256") != EXPECTED_SOURCE_AGGREGATE_SHA256
        or sha256_file(index_path) != EXPECTED_SOURCE_INDEX_SHA256
    ):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_index_projection")
    failure = _load_json(resolved / "failed-attempt.json", "source_failure")
    if (
        failure.get("suite_id") != resolved.name
        or failure.get("credit") != "zero_credit"
        or failure.get("acceptance_credit") is not False
        or not str(failure.get("error") or "").startswith("x1_q0_transport:criteo_dlrm_lite:")
    ):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_failure_identity")
    artifact_root = resolved / "artifacts"
    manifest_path = artifact_root / "x1-artifact-manifest.json"
    manifest = _load_json(manifest_path, "source_manifest")
    source_identity = manifest.get("source_identity")
    if not isinstance(source_identity, Mapping):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_revision")
    revision = str(source_identity.get("revision") or "")
    tree = str(source_identity.get("tree") or "")
    if (
        manifest.get("contract_sha256") != contract.sha256
        or failure.get("source_revision") != revision
        or base.run(["git", "rev-parse", f"{revision}^{{tree}}"], timeout=30).stdout.strip() != tree
        or base.run(
            ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
            check=False,
            timeout=30,
        ).returncode
        != 0
    ):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_revision")
    model = manifest.get("models", {}).get(DLRM_MODEL_ID)
    oracle_ref = manifest.get("correctness_oracles", {}).get(DLRM_MODEL_ID)
    if not isinstance(model, Mapping) or not isinstance(oracle_ref, Mapping):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_model")
    model_path = artifact_root / "model-repositories" / "disabled" / DLRM_MODEL_ID / "1/model.pt"
    oracle_path = artifact_root / str(oracle_ref.get("path") or "")
    if (
        sha256_file(model_path) != model.get("artifact_sha256")
        or sha256_file(model_path) != EXPECTED_DLRM_MODEL_SHA256
        or sha256_file(oracle_path) != oracle_ref.get("sha256")
    ):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_artifact_sha")
    oracle = _load_json(oracle_path, "source_oracle")
    features = oracle.get("input")
    expected = oracle.get("output")
    if (
        oracle.get("model_id") != DLRM_MODEL_ID
        or not isinstance(features, list)
        or not isinstance(expected, list)
        or len(features) != REPEATED_REQUESTS
        or len(expected) != REPEATED_REQUESTS
    ):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_source_oracle")
    try:
        profile_relative_root = model_path.parents[2].relative_to(data_root.resolve()).as_posix()
    except ValueError as exc:
        raise base.X1ExperimentError("x1_dlrm_diagnostic_profile_containment") from exc
    return {
        "suite_id": resolved.name,
        "root": resolved,
        "artifact_root": artifact_root,
        "model_path": model_path,
        "model_sha256": sha256_file(model_path),
        "oracle_path": oracle_path,
        "oracle_sha256": sha256_file(oracle_path),
        "features": features,
        "expected": expected,
        "relative_tolerance": float(oracle["relative_tolerance"]),
        "absolute_tolerance": float(oracle["absolute_tolerance"]),
        "profile_relative_root": profile_relative_root,
        "source_revision": revision,
        "source_tree": tree,
        "private_artifact_count": stored_index["artifact_count"],
        "private_aggregate_sha256": stored_index["aggregate_sha256"],
        "private_index_sha256": sha256_file(index_path),
    }


def triton_only_bundle(bundle: Mapping[str, Any], *, trace_enabled: bool) -> dict[str, Any]:
    projected = copy.deepcopy(dict(bundle))
    items = projected.get("items")
    if not isinstance(items, list):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_topology_items")
    selected = [
        item
        for item in items
        if isinstance(item, Mapping) and item.get("metadata", {}).get("name") == TRITON_NAME
    ]
    if {str(item.get("kind")) for item in selected} != {"Deployment", "Service"} or len(
        selected
    ) != 2:
        raise base.X1ExperimentError("x1_dlrm_diagnostic_topology_identity")
    deployment = next(item for item in selected if item["kind"] == "Deployment")
    command = deployment["spec"]["template"]["spec"]["containers"][0]["command"]
    trace_args = [str(value) for value in command if str(value).startswith("--trace-config=")]
    if len(trace_args) != 5:
        raise base.X1ExperimentError("x1_dlrm_diagnostic_trace_contract")
    if not trace_enabled:
        command[:] = [value for value in command if not str(value).startswith("--trace-config=")]
    projected["items"] = selected
    return projected


def select_loaded_models(bundle: Mapping[str, Any], *, model_ids: Sequence[str]) -> dict[str, Any]:
    selected_ids = list(model_ids)
    if (
        not selected_ids
        or len(selected_ids) != len(set(selected_ids))
        or not set(selected_ids) <= set(base.MODEL_IDS)
    ):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_loaded_model_set")
    projected = copy.deepcopy(dict(bundle))
    deployment = next(
        item
        for item in projected["items"]
        if item["kind"] == "Deployment" and item["metadata"]["name"] == TRITON_NAME
    )
    command = deployment["spec"]["template"]["spec"]["containers"][0]["command"]
    existing = [value for value in command if str(value).startswith("--load-model=")]
    if existing != [f"--load-model={model_id}" for model_id in base.MODEL_IDS]:
        raise base.X1ExperimentError("x1_dlrm_diagnostic_loaded_model_contract")
    first_index = next(
        index for index, value in enumerate(command) if str(value).startswith("--load-model=")
    )
    command[:] = [value for value in command if not str(value).startswith("--load-model=")]
    command[first_index:first_index] = [f"--load-model={model_id}" for model_id in selected_ids]
    return projected


def build_triton_bundle(
    contract: X1Contract,
    *,
    diagnostic_id: str,
    source_revision: str,
    profile_relative_root: str,
    lease: GpuLease,
    trace_enabled: bool,
    loaded_models: Sequence[str] = base.MODEL_IDS,
) -> dict[str, Any]:
    bundle = kubernetes_resource_list(
        contract,
        suite_id=diagnostic_id,
        source_revision=source_revision,
        api_image="x1-diagnostic-api-not-used",
        api_replicas=1,
        cpu_workers=1,
        profile_relative_root=profile_relative_root,
        runtime_manifest_relative_path="diagnostics/api-runtime-not-used.json",
        database_url=base.CONTROL_PLANE_DATABASE_URL,
        database_schema="evm_x1_v1_diagnostic_not_used",
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
    )
    triton_only = triton_only_bundle(bundle, trace_enabled=trace_enabled)
    return select_loaded_models(triton_only, model_ids=loaded_models)


def wait_triton_ready(*, timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    with requests.Session() as session:
        session.trust_env = False
        while time.monotonic() < deadline:
            try:
                live = session.get(f"{base.TRITON_URL}/v2/health/live", timeout=2)
                ready = session.get(f"{base.TRITON_URL}/v2/health/ready", timeout=2)
                model = session.get(
                    f"{base.TRITON_URL}/v2/models/{DLRM_MODEL_ID}/versions/1/ready",
                    timeout=2,
                )
                latest = {
                    "live_status": live.status_code,
                    "ready_status": ready.status_code,
                    "model_status": model.status_code,
                }
                if set(latest.values()) == {200}:
                    return latest
            except requests.RequestException as exc:
                latest = {"error_type": type(exc).__name__, "error": str(exc)}
            time.sleep(1)
    raise base.X1ExperimentError(f"x1_dlrm_diagnostic_readiness:{latest}")


def apply_triton_bundle(path: Path) -> dict[str, Any]:
    base.run(["kubectl", "apply", "-f", str(path)], timeout=120)
    base.run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "rollout",
            "status",
            f"deployment/{TRITON_NAME}",
            "--timeout=180s",
        ],
        timeout=200,
    )
    return wait_triton_ready()


def _metrics_text(session: requests.Session) -> str:
    response = session.get(base.TRITON_METRICS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    if not response.text.endswith("\n"):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_metrics_lf")
    return response.text


def _model_metrics(text: str) -> dict[str, float]:
    from prometheus_client.parser import text_string_to_metric_families

    expected = {
        "success": "nv_inference_request_success",
        "failure": "nv_inference_request_failure",
        "inference": "nv_inference_count",
        "execution": "nv_inference_exec_count",
        "pending": "nv_inference_pending_request_count",
        "compute_us": "nv_inference_compute_infer_duration_us",
    }
    samples: list[tuple[str, Mapping[str, str], float]] = []
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            samples.append((sample.name, dict(sample.labels), float(sample.value)))
    result: dict[str, float] = {}
    for field, name in expected.items():
        matches = [
            value
            for sample_name, labels, value in samples
            if sample_name in {name, f"{name}_total"}
            and labels.get("model") == DLRM_MODEL_ID
            and labels.get("version") == "1"
        ]
        if (
            not matches
            or any(not math.isfinite(value) or value < 0 for value in matches)
            or (field != "failure" and len(matches) != 1)
        ):
            raise base.X1ExperimentError(f"x1_dlrm_diagnostic_metric:{field}:{len(matches)}")
        result[field] = sum(matches) if field == "failure" else matches[0]
    return result


def run_triton_repetitions(
    *,
    diagnostic_id: str,
    mode: str,
    features: Sequence[Sequence[float]],
    expected: Sequence[Sequence[float]],
    relative_tolerance: float,
    absolute_tolerance: float,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    with requests.Session() as session:
        session.trust_env = False
        before_text = _metrics_text(session)
        before = _model_metrics(before_text)
        for sequence, (feature_row, expected_row) in enumerate(
            zip(features, expected, strict=True)
        ):
            request_id = f"{diagnostic_id}-{mode}-{sequence:04d}"
            started = time.perf_counter()
            try:
                observed = base._direct_infer(
                    DLRM_MODEL_ID,
                    feature_row,
                    request_id=request_id,
                    session=session,
                )
                correct = math.isclose(
                    observed,
                    float(expected_row[0]),
                    rel_tol=relative_tolerance,
                    abs_tol=absolute_tolerance,
                )
                records.append(
                    {
                        "request_id": request_id,
                        "sequence": sequence,
                        "elapsed_ms": (time.perf_counter() - started) * 1000,
                        "observed": observed,
                        "expected": float(expected_row[0]),
                        "correct": correct,
                    }
                )
                if not correct:
                    failure = {
                        "request_id": request_id,
                        "sequence": sequence,
                        "error_type": "OracleMismatch",
                    }
                    break
            except Exception as exc:  # noqa: BLE001 - preserve bounded diagnostic outcome
                failure = {
                    "request_id": request_id,
                    "sequence": sequence,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                break
        after_text = _metrics_text(session)
        after = _model_metrics(after_text)
    delta = {key: after[key] - before[key] for key in before}
    if any(not math.isfinite(value) for value in delta.values()):
        raise base.X1ExperimentError("x1_dlrm_diagnostic_metric_delta")
    return {
        "mode": mode,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "expected_request_count": REPEATED_REQUESTS,
        "success_count": len(records),
        "correct_count": sum(record["correct"] for record in records),
        "failure": failure,
        "records": records,
        "metrics_before": before,
        "metrics_after": after,
        "metrics_delta": delta,
        "metrics_before_sha256": hashlib.sha256(before_text.encode("utf-8")).hexdigest(),
        "metrics_after_sha256": hashlib.sha256(after_text.encode("utf-8")).hexdigest(),
        "metrics_before_raw": before_text,
        "metrics_after_raw": after_text,
    }


DIRECT_IMAGE_CODE = """
import json, math, time, torch
model = torch.jit.load('/diagnostic/model.pt', map_location='cuda:0').eval()
oracle = json.load(open('/diagnostic/oracle.json', encoding='utf-8'))
rows = []
started = time.perf_counter()
with torch.inference_mode():
    for sequence, (features, expected) in enumerate(zip(oracle['input'], oracle['output'], strict=True)):
        tensor = torch.tensor(features, dtype=torch.float32, device='cuda:0')
        value = float(model(tensor).detach().reshape(-1)[0].cpu())
        torch.cuda.synchronize()
        rows.append({'sequence': sequence, 'value': value, 'expected': float(expected[0]), 'correct': math.isclose(value, float(expected[0]), rel_tol=float(oracle['relative_tolerance']), abs_tol=float(oracle['absolute_tolerance']))})
print(json.dumps({'request_count': len(rows), 'correct_count': sum(item['correct'] for item in rows), 'cuda_available': torch.cuda.is_available(), 'device_name': torch.cuda.get_device_name(0), 'elapsed_ms': (time.perf_counter() - started) * 1000, 'rows_sha256': __import__('hashlib').sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()}, sort_keys=True, separators=(',', ':')))
""".strip()


def run_direct_image(*, diagnostic_id: str, model_path: Path, oracle_path: Path) -> dict[str, Any]:
    container_name = f"evm-x1-dlrm-diagnostic-{diagnostic_id[-8:]}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--gpus",
        "all",
        "--entrypoint",
        "python3",
        "--env",
        f"LD_LIBRARY_PATH={TRITON_WSL_LD_LIBRARY_PATH}",
        "--mount",
        f"type=bind,source={model_path},target=/diagnostic/model.pt,readonly",
        "--mount",
        f"type=bind,source={oracle_path},target=/diagnostic/oracle.json,readonly",
        TRITON_IMAGE,
        "-c",
        DIRECT_IMAGE_CODE,
    ]
    started = time.perf_counter()
    try:
        result = base.run(command, check=False, timeout=180)
        parsed: dict[str, Any] | None = None
        if result.returncode == 0 and result.stdout.strip():
            try:
                candidate = json.loads(result.stdout.splitlines()[-1])
                if isinstance(candidate, dict):
                    parsed = candidate
            except json.JSONDecodeError:
                parsed = None
        return {
            "mode": "pinned_triton_image_direct_torchscript",
            "container_name": container_name,
            "command": command,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "exit_code": result.returncode,
            "stdout": result.stdout[-200_000:],
            "stderr": result.stderr[-200_000:],
            "projection": parsed,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "mode": "pinned_triton_image_direct_torchscript",
            "container_name": container_name,
            "command": command,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def run_host_torchscript(
    *,
    model_path: Path,
    features: Sequence[Sequence[float]],
    expected: Sequence[Sequence[float]],
    relative_tolerance: float,
    absolute_tolerance: float,
) -> dict[str, Any]:
    import torch

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    try:
        model = torch.jit.load(str(model_path), map_location="cuda:0").eval()
        with torch.inference_mode():
            for sequence, (feature_row, expected_row) in enumerate(
                zip(features, expected, strict=True)
            ):
                request_started = time.perf_counter()
                tensor = torch.tensor(feature_row, dtype=torch.float32, device="cuda:0")
                observed = float(model(tensor).detach().reshape(-1)[0].cpu())
                torch.cuda.synchronize()
                rows.append(
                    {
                        "sequence": sequence,
                        "elapsed_ms": (time.perf_counter() - request_started) * 1000,
                        "observed": observed,
                        "expected": float(expected_row[0]),
                        "correct": math.isclose(
                            observed,
                            float(expected_row[0]),
                            rel_tol=relative_tolerance,
                            abs_tol=absolute_tolerance,
                        ),
                    }
                )
        gpu = base.capture_gpu()
        return {
            "mode": "host_python_direct_torchscript",
            "python_executable": sys.executable,
            "torch_version": torch.__version__,
            "torch_cuda_version": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_name": torch.cuda.get_device_name(0),
            "gpu": gpu,
            "request_count": len(rows),
            "correct_count": sum(row["correct"] for row in rows),
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "rows_sha256": canonical_sha256(rows),
            "rows": rows,
        }
    except Exception as exc:  # noqa: BLE001 - preserve bounded diagnostic outcome
        return {
            "mode": "host_python_direct_torchscript",
            "python_executable": sys.executable,
            "torch_version": torch.__version__,
            "torch_cuda_version": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "completed_request_count": len(rows),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def capture_kubernetes_state() -> dict[str, Any]:
    return {
        "deployment": base._bounded_command_snapshot(
            ["kubectl", "-n", NAMESPACE, "get", f"deployment/{TRITON_NAME}", "-o", "json"]
        ),
        "pods": base._bounded_command_snapshot(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "get",
                "pods",
                "-l",
                f"app.kubernetes.io/name={TRITON_NAME}",
                "-o",
                "json",
            ]
        ),
        "service": base._bounded_command_snapshot(
            ["kubectl", "-n", NAMESPACE, "get", f"service/{TRITON_NAME}", "-o", "json"]
        ),
        "endpoints": base._bounded_command_snapshot(
            ["kubectl", "-n", NAMESPACE, "get", f"endpoints/{TRITON_NAME}", "-o", "json"]
        ),
        "logs": base._bounded_command_snapshot(
            ["kubectl", "-n", NAMESPACE, "logs", f"deployment/{TRITON_NAME}", "--tail=500"]
        ),
    }


def classify_diagnostic(
    direct: Mapping[str, Any],
    host_direct: Mapping[str, Any],
    trace_enabled: Mapping[str, Any],
    trace_disabled: Mapping[str, Any],
    dlrm_only: Mapping[str, Any],
) -> str:
    direct_projection = direct.get("projection")
    direct_passed = (
        isinstance(direct_projection, Mapping)
        and direct_projection.get("request_count") == REPEATED_REQUESTS
        and direct_projection.get("correct_count") == REPEATED_REQUESTS
        and direct_projection.get("cuda_available") is True
    )
    host_passed = (
        host_direct.get("request_count") == REPEATED_REQUESTS
        and host_direct.get("correct_count") == REPEATED_REQUESTS
        and host_direct.get("cuda_available") is True
    )
    enabled_passed = (
        trace_enabled.get("success_count") == REPEATED_REQUESTS
        and trace_enabled.get("failure") is None
    )
    disabled_passed = (
        trace_disabled.get("success_count") == REPEATED_REQUESTS
        and trace_disabled.get("failure") is None
    )
    dlrm_only_passed = (
        dlrm_only.get("success_count") == REPEATED_REQUESTS and dlrm_only.get("failure") is None
    )
    if not host_passed:
        return "host_direct_artifact_or_framework_failure"
    if not enabled_passed and not disabled_passed and dlrm_only_passed:
        return "triton_four_model_coresidency_correlated_stall"
    if not enabled_passed and not disabled_passed and not dlrm_only_passed:
        return "triton_pytorch_backend_or_dlrm_artifact_interaction"
    if not enabled_passed and disabled_passed:
        return "triton_trace_pipeline_correlated_stall"
    if enabled_passed and disabled_passed and dlrm_only_passed:
        return "stall_not_reproduced_in_bounded_isolation"
    if not direct_passed:
        return "pinned_image_python_torch_unavailable_but_triton_path_inconclusive"
    return "diagnostic_inconclusive"


def main() -> int:
    args = parse_args()
    if not args.maintenance_approved:
        raise base.X1ExperimentError("x1_maintenance_approval_required")
    contract = X1Contract.from_path(args.config, source_root=ROOT, data_root=args.data_root)
    environment = base.preflight(contract)
    source = environment["source"]
    source_suite = validate_source_suite(args.source_suite_root, contract, data_root=args.data_root)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower()
    diagnostic_id = f"x1-diagnostic-{timestamp}-{uuid4().hex[:8]}"
    diagnostic_root = args.private_base / diagnostic_id
    diagnostic_root.mkdir(parents=True, exist_ok=False)
    base.canonical_write(diagnostic_root / "environment-preflight.json", environment)
    source_binding = {
        "schema_version": "evm.s8_v4.x1_dlrm_backend_source_binding.v1",
        "diagnostic_id": diagnostic_id,
        "source_suite_id": source_suite["suite_id"],
        "source_suite_revision": source_suite["source_revision"],
        "source_suite_tree": source_suite["source_tree"],
        "source_suite_private_artifact_count": source_suite["private_artifact_count"],
        "source_suite_private_aggregate_sha256": source_suite["private_aggregate_sha256"],
        "source_suite_private_index_sha256": source_suite["private_index_sha256"],
        "model_sha256": source_suite["model_sha256"],
        "oracle_sha256": source_suite["oracle_sha256"],
        "diagnostic_source_revision": source["revision"],
        "diagnostic_source_tree": source["tree_sha"],
        "credit": "non_credit",
        "acceptance_credit": False,
    }
    base.canonical_write(diagnostic_root / "source-binding.json", source_binding)
    holder = base.Holder(**environment["holder"])
    gpu_before = environment["gpu"]
    prometheus_before = environment["prometheus"]
    database_schema = f"evm_x1_v1_{hashlib.sha256(diagnostic_id.encode('ascii')).hexdigest()[:12]}"
    lease: GpuLease | None = None
    holder_scaled = False
    topology_started = False
    failure: dict[str, Any] | None = None
    cleanup_errors: list[dict[str, str]] = []
    direct: dict[str, Any] | None = None
    host_direct: dict[str, Any] | None = None
    mode_results: dict[str, dict[str, Any]] = {}
    container_name = f"evm-x1-dlrm-diagnostic-{diagnostic_id[-8:]}"
    try:
        base.scale_holder(holder, 0)
        holder_scaled = True
        lease = acquire_scale_validation_gpu_lease(
            f"s8-v4-x1-{diagnostic_id}-backend-diagnostic",
            source_commit=source["revision"],
            purpose="scale_validation_inference",
            scenario_id="X1",
            model_family="heterogeneous",
            owner_pid=os.getpid(),
            ttl_seconds=3600,
        )
        assert_scale_validation_gpu_lease_owner(
            run_id=lease.run_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            purpose="scale_validation_inference",
            scenario_id="X1",
            model_family="heterogeneous",
        )
        direct = run_direct_image(
            diagnostic_id=diagnostic_id,
            model_path=source_suite["model_path"],
            oracle_path=source_suite["oracle_path"],
        )
        base.canonical_write(diagnostic_root / "direct-image.json", direct)
        host_direct = run_host_torchscript(
            model_path=source_suite["model_path"],
            features=source_suite["features"],
            expected=source_suite["expected"],
            relative_tolerance=source_suite["relative_tolerance"],
            absolute_tolerance=source_suite["absolute_tolerance"],
        )
        base.canonical_write(diagnostic_root / "host-direct.json", host_direct)
        modes = (
            ("trace-enabled", True, base.MODEL_IDS),
            ("trace-disabled", False, base.MODEL_IDS),
            ("trace-disabled-dlrm-only", False, (DLRM_MODEL_ID,)),
        )
        for mode, trace_enabled, loaded_models in modes:
            assert_scale_validation_gpu_lease_owner(
                run_id=lease.run_id,
                lease_id=lease.lease_id,
                fencing_token=lease.fencing_token,
                purpose="scale_validation_inference",
                scenario_id="X1",
                model_family="heterogeneous",
            )
            bundle = build_triton_bundle(
                contract,
                diagnostic_id=diagnostic_id,
                source_revision=source["revision"],
                profile_relative_root=source_suite["profile_relative_root"],
                lease=lease,
                trace_enabled=trace_enabled,
                loaded_models=loaded_models,
            )
            manifest_path = diagnostic_root / f"kubernetes-{mode}.json"
            base.canonical_write(manifest_path, bundle)
            topology_started = True
            readiness = apply_triton_bundle(manifest_path)
            result = run_triton_repetitions(
                diagnostic_id=diagnostic_id,
                mode=mode,
                features=source_suite["features"],
                expected=source_suite["expected"],
                relative_tolerance=source_suite["relative_tolerance"],
                absolute_tolerance=source_suite["absolute_tolerance"],
            )
            result["trace_enabled"] = trace_enabled
            result["loaded_models"] = list(loaded_models)
            result["readiness"] = readiness
            result["kubernetes"] = capture_kubernetes_state()
            mode_results[mode] = result
            base.canonical_write(diagnostic_root / f"{mode}.json", result)
            base.delete_topology()
            topology_started = False
        classification = classify_diagnostic(
            direct,
            host_direct,
            mode_results["trace-enabled"],
            mode_results["trace-disabled"],
            mode_results["trace-disabled-dlrm-only"],
        )
        summary = {
            "schema_version": "evm.s8_v4.x1_dlrm_backend_diagnostic.v2",
            "diagnostic_id": diagnostic_id,
            "captured_at": utc_now(),
            "source_binding_sha256": sha256_file(diagnostic_root / "source-binding.json"),
            "direct_image_sha256": sha256_file(diagnostic_root / "direct-image.json"),
            "host_direct_sha256": sha256_file(diagnostic_root / "host-direct.json"),
            "trace_enabled_sha256": sha256_file(diagnostic_root / "trace-enabled.json"),
            "trace_disabled_sha256": sha256_file(diagnostic_root / "trace-disabled.json"),
            "trace_disabled_dlrm_only_sha256": sha256_file(
                diagnostic_root / "trace-disabled-dlrm-only.json"
            ),
            "classification": classification,
            "credit": "non_credit",
            "acceptance_credit": False,
            "q0_credit": 0,
            "calibration_credit": 0,
            "threshold_or_matrix_change": False,
            "claim_boundary": (
                "bounded backend-isolation diagnostic on one Windows/WSL2 node and one RTX 4080; "
                "correlation is not causation and no Q0, calibration, production SLA, HA/DR, "
                "multi-node, multi-GPU, MIG, MPS, or kernel-overlap claim is made"
            ),
        }
        base.canonical_write(diagnostic_root / "diagnostic-summary.json", summary)
    except Exception as exc:  # noqa: BLE001 - preserve diagnostic failure
        failure = {
            "schema_version": "evm.s8_v4.x1_dlrm_backend_diagnostic_failure.v1",
            "diagnostic_id": diagnostic_id,
            "failed_at": utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "credit": "zero_credit",
            "acceptance_credit": False,
        }
        base.canonical_write(diagnostic_root / "failed-diagnostic.json", failure)
    finally:
        if topology_started:
            try:
                base.delete_topology()
            except Exception as exc:  # noqa: BLE001 - preserve cleanup failure
                cleanup_errors.append(
                    {"action": "kubernetes", "error_type": type(exc).__name__, "error": str(exc)}
                )
        base.run(["docker", "rm", "-f", container_name], check=False, timeout=30)
        if lease is not None:
            try:
                released = base.release_lease(
                    lease, reason=f"X1 diagnostic {diagnostic_id} cleanup"
                )
                base.canonical_write(diagnostic_root / "inference-lease-release.json", released)
            except Exception as exc:  # noqa: BLE001 - preserve cleanup failure
                cleanup_errors.append(
                    {"action": "lease", "error_type": type(exc).__name__, "error": str(exc)}
                )
        if holder_scaled:
            try:
                base.scale_holder(holder, holder.replicas)
            except Exception as exc:  # noqa: BLE001 - preserve cleanup failure
                cleanup_errors.append(
                    {"action": "b0", "error_type": type(exc).__name__, "error": str(exc)}
                )
        if cleanup_errors:
            base.canonical_write(diagnostic_root / "cleanup-errors.json", cleanup_errors)
    if not cleanup_errors:
        try:
            cleanup = base.cleanup_snapshot(
                holder=holder,
                gpu_before=gpu_before,
                prometheus_before=prometheus_before,
                database_schema=database_schema,
            )
            base.canonical_write(diagnostic_root / "final-cleanup.json", cleanup)
        except Exception as exc:  # noqa: BLE001 - cleanup failure invalidates diagnostic
            cleanup_errors.append(
                {
                    "action": "cleanup_validation",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            base.canonical_write(diagnostic_root / "cleanup-errors.json", cleanup_errors)
    index = base.private_index(diagnostic_root)
    index_path = diagnostic_root / "private-evidence-index.json"
    base.canonical_write(index_path, index)
    result = {
        "diagnostic_id": diagnostic_id,
        "status": "zero_credit" if failure is not None or cleanup_errors else "non_credit_complete",
        "failure": failure,
        "cleanup_errors": cleanup_errors,
        "private_artifact_count": index["artifact_count"],
        "private_total_bytes": index["total_bytes"],
        "private_aggregate_sha256": index["aggregate_sha256"],
        "private_index_sha256": sha256_file(index_path),
    }
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 1 if failure is not None or cleanup_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

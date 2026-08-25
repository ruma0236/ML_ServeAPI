from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests

from evm.scale_validation.s6bm_observability import find_triton_compute_start
from evm.scale_validation.s6bm_runtime import canonical, canonical_sha256, sha256_file


class S6BMTraceCollectorError(RuntimeError):
    pass


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")


def _run(command: Sequence[str], *, timeout: float = 10) -> str:
    result = subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise S6BMTraceCollectorError(
            f"collector_command_failed:{result.returncode}:{result.stderr[-500:]}"
        )
    return result.stdout.strip()


def _gpu_identity() -> dict[str, str]:
    output = _run(
        [
            "nvidia-smi",
            "--query-gpu=uuid,name",
            "--format=csv,noheader,nounits",
        ]
    )
    rows = [row for row in output.splitlines() if row.strip()]
    if len(rows) != 1:
        raise S6BMTraceCollectorError(f"collector_single_gpu_required:{len(rows)}")
    uuid, name = (item.strip() for item in rows[0].split(",", 1))
    return {"uuid": uuid, "name": name}


def _independent_anchor(spec: Mapping[str, Any]) -> dict[str, Any]:
    nonce = secrets.token_hex(16)
    before = time.perf_counter_ns()
    unix_ns = time.time_ns()
    after = time.perf_counter_ns()
    payload = {
        "schema_version": "evm.s8_v4.s6bm_dual_clock_anchor.v3",
        "sequence": 1,
        "phase": "triton_compute_receipt_collected",
        "anchor_nonce": nonce,
        "monotonic_before_ns": before,
        "unix_ns": unix_ns,
        "monotonic_after_ns": after,
        "source_identity": (
            f"collector:{spec['source_revision']}:{spec['suite_id']}:"
            f"{spec['attempt_id']}:{os.getpid()}:{nonce}"
        ),
        "process_id": os.getpid(),
        "parent_process_id": os.getppid(),
        "host_identity": socket.gethostname(),
        "previous_anchor_hash": None,
    }
    payload["anchor_hash"] = canonical_sha256(payload)
    return payload


def collect(spec_path: Path) -> dict[str, Any]:
    raw_spec = spec_path.read_bytes()
    if b"\r" in raw_spec or not raw_spec.endswith(b"\n"):
        raise S6BMTraceCollectorError("collector_spec_not_canonical")
    spec = json.loads(raw_spec)
    if spec.get("schema_version") != "evm.s8_v4.s6bm_trace_collector_spec.v1":
        raise S6BMTraceCollectorError("collector_spec_schema")
    if int(spec.get("runner_process_id", 0)) != os.getppid():
        raise S6BMTraceCollectorError("collector_parent_process_identity")
    timeout = float(spec["timeout_seconds"])
    if timeout <= 0:
        raise S6BMTraceCollectorError("collector_timeout_bound")
    deadline = time.monotonic() + timeout
    observed: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        observed = find_triton_compute_start(
            Path(spec["otel_trace_path"]),
            request_nonce=str(spec["request_nonce"]),
            trace_id=str(spec["trace_id"]),
            model_name=str(spec["model_name"]),
            model_version=str(spec["model_version"]),
            start_offset=int(spec["trace_start_offset"]),
        )
        if observed is not None:
            break
        time.sleep(0.01)
    if observed is None:
        raise S6BMTraceCollectorError("triton_compute_start_trace_timeout")

    raw_trace_path = Path(spec["raw_trace_output_path"])
    _write_json(raw_trace_path, observed)
    container_id = _run(
        ["docker", "inspect", "--format", "{{.Id}}", str(spec["container_name"])]
    )
    if container_id != str(spec["expected_container_id"]):
        raise S6BMTraceCollectorError("collector_container_identity")
    gpu = _gpu_identity()
    if gpu["uuid"] != str(spec["expected_gpu_uuid"]):
        raise S6BMTraceCollectorError("collector_gpu_identity")
    anchor = _independent_anchor(spec)
    payload = {
        "schema_version": "evm.s8_v4.s6bm_triton_start_receipt.v2",
        "causal_identity": dict(spec["causal_identity"]),
        "trace_event_name": "COMPUTE_START",
        "actor_start_unix_ns": int(observed["compute_start_unix_ns"]),
        "raw_trace_artifact_sha256": sha256_file(raw_trace_path),
        "raw_trace_record_sha256": canonical_sha256(observed),
        "raw_trace_span_id": str(observed["span_id"]),
        "triton_container_id": container_id,
        "triton_image_digest": str(spec["image_digest"]),
        "gpu_uuid": gpu["uuid"],
        "collector_process_id": os.getpid(),
        "collector_parent_process_id": os.getppid(),
        "collector_nonce": str(anchor["anchor_nonce"]),
        "collector_source_identity": str(anchor["source_identity"]),
        "collector_spec_sha256": sha256_file(spec_path),
        "collector_observation": anchor,
        "backend_identity": {
            "service_name": observed["resource"].get("service.name"),
            "telemetry_sdk_language": observed["resource"].get(
                "telemetry.sdk.language"
            ),
            "model_request_span_id": observed["model_request_span_id"],
            "compute_span_id": observed["span_id"],
            "compute_parent_span_id": observed["parent_span_id"],
        },
    }
    response = requests.post(str(spec["receipt_url"]), json=payload, timeout=5)
    if response.status_code != 200:
        raise S6BMTraceCollectorError(
            f"triton_start_receipt_rejected:{response.status_code}:{response.text[:500]}"
        )
    receipt = response.json()
    if receipt.get("readback_visible") is not True:
        raise S6BMTraceCollectorError("triton_start_receipt_not_visible")
    result = {
        "schema_version": "evm.s8_v4.s6bm_trace_collector_result.v1",
        "attempt_id": spec["attempt_id"],
        "request_id": spec["request_id"],
        "trace_id": spec["trace_id"],
        "collector_process_id": os.getpid(),
        "collector_parent_process_id": os.getppid(),
        "collector_spec_sha256": sha256_file(spec_path),
        "raw_trace_path": raw_trace_path.name,
        "raw_trace_sha256": sha256_file(raw_trace_path),
        "raw_record_sha256": canonical_sha256(observed),
        "collector_observation": anchor,
        "backend_identity": payload["backend_identity"],
        "receipt": receipt,
    }
    _write_json(Path(spec["result_output_path"]), result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect one actor-origin Triton receipt")
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    try:
        collect(args.spec.resolve())
    except Exception as exc:
        error_path = args.spec.with_name("collector-error.json")
        _write_json(
            error_path,
            {
                "schema_version": "evm.s8_v4.s6bm_trace_collector_error.v1",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "process_id": os.getpid(),
                "parent_process_id": os.getppid(),
            },
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def gpu_inventory() -> list[dict[str, str]]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,uuid,driver_version,memory.total",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    inventory = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name, uuid, driver, memory_mib = [item.strip() for item in line.split(",", 3)]
        inventory.append(
            {
                "name": name,
                "uuid": uuid,
                "driver_version": driver,
                "memory_total_mib": memory_mib,
            }
        )
    if not inventory:
        raise RuntimeError("nvidia-smi returned no GPU inventory")
    return inventory


def require_equal(actual: Any, expected: Any, name: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{name} mismatch: expected={expected!r} actual={actual!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify real VisA image inference against the immutable CUDA B7 service."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--image-uri", required=True)
    parser.add_argument("--expected-candidate", required=True)
    parser.add_argument("--expected-dataset-version", required=True)
    parser.add_argument("--expected-model-sha256", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    image_path = Path(args.image_uri)
    if not image_path.is_file() or image_path.stat().st_size <= 0:
        raise RuntimeError(f"real input image is not readable: {image_path}")
    if args.repetitions < 3:
        raise RuntimeError("at least three predictions are required")

    base_url = args.base_url.rstrip("/")
    ready = request_json("GET", f"{base_url}/ready")
    require_equal(ready.get("status"), "ok", "readiness status")
    require_equal(ready.get("model_loaded"), True, "model_loaded")
    require_equal(ready.get("architecture"), "efficientnet-b7", "architecture")
    require_equal(ready.get("device"), "cuda", "readiness device")
    require_equal(ready.get("cuda_available"), True, "cuda availability")
    require_equal(ready.get("candidate_id"), args.expected_candidate, "candidate")
    require_equal(ready.get("dataset_version"), args.expected_dataset_version, "dataset version")
    require_equal(ready.get("model_sha256"), args.expected_model_sha256, "model sha256")

    predictions: list[dict[str, Any]] = []
    for sequence in range(1, args.repetitions + 1):
        started = time.perf_counter()
        prediction = request_json(
            "POST", f"{base_url}/predict", {"image_uri": str(image_path)}
        )
        prediction["sequence"] = sequence
        prediction["round_trip_ms"] = round((time.perf_counter() - started) * 1000, 3)
        require_equal(prediction.get("device"), "cuda", "inference device")
        require_equal(prediction.get("candidate_id"), args.expected_candidate, "inference candidate")
        require_equal(
            prediction.get("dataset_version"),
            args.expected_dataset_version,
            "inference dataset version",
        )
        require_equal(
            prediction.get("model_sha256"),
            args.expected_model_sha256,
            "inference model sha256",
        )
        predictions.append(prediction)

    labels = {item["prediction"] for item in predictions}
    if len(labels) != 1:
        raise RuntimeError(f"repeated inference is not deterministic: {sorted(labels)}")
    inference_latencies = [float(item["latency_ms"]) for item in predictions]
    output = Path(args.output)
    report = {
        "schema_version": "evm.live_gpu_inference_validation.v1",
        "status": "pass",
        "executed_at": utc_now(),
        "execution_mode": "real_kubernetes_cuda_inference",
        "mock": False,
        "smoke_only": False,
        "base_url": base_url,
        "input": {
            "image_uri": str(image_path).replace("\\", "/"),
            "image_size_bytes": image_path.stat().st_size,
            "image_sha256": sha256_file(image_path),
        },
        "expected_identity": {
            "candidate_id": args.expected_candidate,
            "dataset_version": args.expected_dataset_version,
            "model_sha256": args.expected_model_sha256,
        },
        "host_gpu_inventory": gpu_inventory(),
        "readiness": ready,
        "predictions": predictions,
        "summary": {
            "repetitions": len(predictions),
            "prediction": predictions[-1]["prediction"],
            "confidence": predictions[-1]["confidence"],
            "first_inference_latency_ms": inference_latencies[0],
            "warm_inference_median_ms": round(statistics.median(inference_latencies[1:]), 3),
            "all_cuda": all(item["device"] == "cuda" for item in predictions),
            "identity_stable": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"status": "pass", "output": str(output), **report["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CONFIG = """name: "e0_cuda_linear"
backend: "python"
max_batch_size: 8
input [
  {
    name: "INPUT__0"
    data_type: TYPE_FP32
    dims: [ 4 ]
  }
]
output [
  {
    name: "OUTPUT__0"
    data_type: TYPE_FP32
    dims: [ 4 ]
  }
]
instance_group [
  {
    count: 1
    kind: KIND_GPU
    gpus: [ 0 ]
  }
]
"""


MODEL = """import cupy as cp
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        self.device_id = int(args["model_instance_device_id"])
        cp.cuda.Device(self.device_id).use()

    def execute(self, requests):
        responses = []
        for request in requests:
            input_tensor = pb_utils.get_input_tensor_by_name(request, "INPUT__0")
            gpu_input = cp.asarray(input_tensor.as_numpy(), dtype=cp.float32)
            gpu_output = gpu_input * cp.float32(2.0) + cp.float32(1.0)
            cp.cuda.get_current_stream().synchronize()
            output_tensor = pb_utils.Tensor("OUTPUT__0", cp.asnumpy(gpu_output))
            responses.append(pb_utils.InferenceResponse(output_tensors=[output_tensor]))
        return responses
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the deterministic E0 Triton model.")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_root = args.output / "e0_cuda_linear"
    version_root = model_root / "1"
    version_root.mkdir(parents=True, exist_ok=False)
    config_path = model_root / "config.pbtxt"
    artifact_path = version_root / "model.py"
    config_path.write_text(CONFIG, encoding="utf-8", newline="\n")
    artifact_path.write_text(MODEL, encoding="utf-8", newline="\n")
    entries = [
        {
            "path": path.relative_to(args.output).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(item for item in args.output.rglob("*") if item.is_file())
    ]
    manifest = {
        "schema_version": "evm.s8_v4.e0_model_repository.v1",
        "model_name": "e0_cuda_linear",
        "model_version": "1",
        "backend": "python",
        "entries": entries,
        "repository_sha256": hashlib.sha256(canonical(entries).encode("ascii")).hexdigest(),
        "config_sha256": sha256(config_path),
        "artifact_sha256": sha256(artifact_path),
    }
    manifest_path = args.output / "model-repository-manifest.json"
    manifest_path.write_text(canonical(manifest) + "\n", encoding="utf-8", newline="\n")
    print(canonical(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

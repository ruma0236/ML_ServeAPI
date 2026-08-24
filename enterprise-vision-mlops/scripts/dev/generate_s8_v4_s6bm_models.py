from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch


CONFIG = """name: \"{model_name}\"
backend: \"pytorch\"
max_batch_size: 16
input [
  {{
    name: \"INPUT__0\"
    data_type: TYPE_FP32
    dims: [ 4 ]
  }}
]
output [
  {{
    name: \"OUTPUT__0\"
    data_type: TYPE_FP32
    dims: [ 4 ]
  }}
]
instance_group [
  {{
    count: 1
    kind: KIND_GPU
    gpus: [ 0 ]
  }}
]
"""


class Affine(torch.nn.Module):
    def __init__(self, scale: float, offset: float) -> None:
        super().__init__()
        self.scale = scale
        self.offset = offset

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return value * self.scale + self.offset


def canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_model(root: Path, role: str, scale: float, offset: float) -> dict[str, object]:
    model_name = f"s6bm_{role}"
    model_root = root / model_name
    version_root = model_root / "1"
    version_root.mkdir(parents=True, exist_ok=False)
    config_path = model_root / "config.pbtxt"
    artifact_path = version_root / "model.pt"
    config_path.write_text(
        CONFIG.format(model_name=model_name), encoding="utf-8", newline="\n"
    )
    torch.manual_seed(20260825)
    model = torch.jit.script(Affine(scale, offset).eval())
    torch.jit.save(model, artifact_path)
    expected = [value * scale + offset for value in [1.0, 2.0, 3.0, 4.0]]
    return {
        "role": role,
        "model_name": model_name,
        "model_version": "1",
        "backend": "pytorch",
        "artifact_sha256": sha256(artifact_path),
        "config_sha256": sha256(config_path),
        "expected_output": expected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic S6B-M models.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    models = [
        write_model(args.output, "blue", 2.0, 1.0),
        write_model(args.output, "green", 2.0, 2.0),
    ]
    entries = [
        {
            "path": path.relative_to(args.output).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(item for item in args.output.rglob("*") if item.is_file())
    ]
    manifest = {
        "schema_version": "evm.s8_v4.s6bm_model_repository.v1",
        "seed": 20260825,
        "framework": {
            "name": "torchscript",
            "torch_version": torch.__version__,
            "cuda_build": torch.version.cuda,
        },
        "models": models,
        "entries": entries,
        "repository_sha256": hashlib.sha256(canonical(entries).encode("ascii")).hexdigest(),
    }
    (args.output / "model-repository-manifest.json").write_text(
        canonical(manifest) + "\n", encoding="utf-8", newline="\n"
    )
    print(canonical(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

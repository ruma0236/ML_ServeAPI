from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.model_runtime.tiny_mlp import build_tiny_mlp  # noqa: E402
from evm.scale_validation.x1_resume_testbed import (  # noqa: E402
    DEFAULT_CONFIG_RELATIVE_PATH,
    MANIFEST_SCHEMA_VERSION,
    MODEL_CLAIM_CONTRACT,
    REQUIRED_SOURCE_BLOB_PATHS,
    X1ResumeConfig,
    X1ResumeTestbedError,
    canonical,
    canonical_sha256,
    canonical_write,
    require_default_config_path,
    sha256_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare deterministic Triton repositories for X1 Resume Testbed v1."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / DEFAULT_CONFIG_RELATIVE_PATH,
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise X1ResumeTestbedError(f"x1_resume_input_missing:{label}:{path}")
    return path


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(require_file(path, label).read_bytes())
    except json.JSONDecodeError as exc:
        raise X1ResumeTestbedError(f"x1_resume_input_json:{label}") from exc
    if not isinstance(payload, dict):
        raise X1ResumeTestbedError(f"x1_resume_input_mapping:{label}")
    return payload


def governed_manifest_file(manifest_path: Path, raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise X1ResumeTestbedError(f"x1_resume_s5_shard_path:{label}")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise X1ResumeTestbedError(f"x1_resume_s5_shard_path:{label}")
    root = manifest_path.parent.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise X1ResumeTestbedError(f"x1_resume_s5_shard_containment:{label}") from exc
    return require_file(path, label)


def resolve_registry_artifact(registry_path: Path, entry: dict[str, Any], label: str) -> Path:
    relative = Path(str(entry.get("artifact_uri") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise X1ResumeTestbedError(f"x1_resume_artifact_uri:{label}")
    path = require_file(registry_path.parent / relative, label)
    if sha256_file(path) != entry.get("artifact_sha256"):
        raise X1ResumeTestbedError(f"x1_resume_artifact_digest:{label}")
    return path


def stable_bucket(value: object, buckets: int) -> int:
    if value is None:
        return 0
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % buckets


def load_criteo_samples(
    manifest_path: Path, rows: int, buckets: int, *, data_root: Path
) -> tuple[list[list[float]], dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise X1ResumeTestbedError("x1_resume_pyarrow_missing") from exc
    manifest = read_json(manifest_path, "s5_manifest")
    if manifest.get("schema_version") != "evm.s5_criteo_dataset_manifest.v1":
        raise X1ResumeTestbedError("x1_resume_s5_manifest_schema")
    shards = list(manifest.get("shards", []))
    if not shards:
        raise X1ResumeTestbedError("x1_resume_s5_shards_absent")
    for item in shards:
        shard = dict(item)
        governed = governed_manifest_file(manifest_path, shard.get("governed_path"), "s5_shard")
        if sha256_file(governed) != shard.get("governed_sha256"):
            raise X1ResumeTestbedError("x1_resume_s5_shard_digest")
    first = dict(shards[0])
    shard_path = governed_manifest_file(manifest_path, first.get("governed_path"), "s5_shard")
    if sha256_file(shard_path) != first.get("governed_sha256"):
        raise X1ResumeTestbedError("x1_resume_s5_shard_digest")
    names = (
        ["label"]
        + [f"int_feature_{index}" for index in range(1, 14)]
        + [f"cat_feature_{index}" for index in range(1, 27)]
    )
    table = pq.read_table(shard_path, columns=names).slice(0, rows)
    if table.num_rows != rows:
        raise X1ResumeTestbedError("x1_resume_s5_sample_rows")
    samples: list[list[float]] = []
    columns = {name: table[name].to_pylist() for name in names}
    for row in range(rows):
        dense = []
        for index in range(1, 14):
            value = columns[f"int_feature_{index}"][row]
            numeric = max(0.0, float(value or 0.0))
            # The test model uses a bounded dense transform; no accuracy claim is made.
            import math

            dense.append(math.log1p(numeric))
        categorical = [
            float(stable_bucket(columns[f"cat_feature_{index}"][row], buckets))
            for index in range(1, 27)
        ]
        samples.append(dense + categorical)
    return samples, {
        "manifest_path": manifest_path.relative_to(data_root).as_posix(),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "dataset_version": manifest.get("dataset_version"),
        "source_revision": manifest.get("source_revision"),
        "shard_path": str(first.get("governed_path")),
        "shard_sha256": sha256_file(shard_path),
        "shard_bytes": shard_path.stat().st_size,
        "sample_rows": rows,
        "categorical_hash": "sha256-first-u64-mod-4096",
        "dense_transform": "log1p(max(value,0))",
    }


def config_pbtxt(model_id: str, width: int, *, batching: dict[str, Any]) -> str:
    dynamic = ""
    if batching.get("enabled") is True:
        sizes = ", ".join(str(int(value)) for value in batching["preferred_batch_sizes"])
        dynamic = (
            "dynamic_batching {\n"
            f"  preferred_batch_size: [ {sizes} ]\n"
            f"  max_queue_delay_microseconds: {int(batching['max_queue_delay_microseconds'])}\n"
            "}\n"
        )
    return (
        f'name: "{model_id}"\n'
        'backend: "pytorch"\n'
        "max_batch_size: 32\n"
        "input [\n"
        "  {\n"
        '    name: "FEATURES__0"\n'
        "    data_type: TYPE_FP32\n"
        f"    dims: [ {width} ]\n"
        "  }\n"
        "]\n"
        "output [\n"
        "  {\n"
        '    name: "SCORE__0"\n'
        "    data_type: TYPE_FP32\n"
        "    dims: [ 1 ]\n"
        "  }\n"
        "]\n"
        "instance_group [\n"
        "  {\n"
        "    count: 1\n"
        "    kind: KIND_GPU\n"
        "    gpus: [ 0 ]\n"
        "  }\n"
        "]\n"
        f"{dynamic}"
    )


def build_models(
    config: X1ResumeConfig, data_root: Path
) -> tuple[dict[str, Any], dict[str, list[list[float]]]]:
    try:
        import numpy as np
        import torch
    except ImportError as exc:
        raise X1ResumeTestbedError("x1_resume_torch_numpy_missing") from exc

    class LogisticModule(torch.nn.Module):
        def __init__(self, artifact: dict[str, Any]) -> None:
            super().__init__()
            self.register_buffer(
                "mean", torch.tensor(artifact["transform"]["mean"], dtype=torch.float32)
            )
            self.register_buffer(
                "scale", torch.tensor(artifact["transform"]["scale"], dtype=torch.float32)
            )
            self.register_buffer(
                "weights", torch.tensor(artifact["model"]["weights"], dtype=torch.float32)
            )
            self.register_buffer(
                "bias", torch.tensor(float(artifact["model"]["intercept"]), dtype=torch.float32)
            )

        def forward(self, values):
            logits = (((values - self.mean) / self.scale) * self.weights).sum(
                dim=1, keepdim=True
            ) + self.bias
            return torch.sigmoid(logits)

    class GaussianModule(torch.nn.Module):
        def __init__(self, artifact: dict[str, Any]) -> None:
            super().__init__()
            self.register_buffer(
                "theta", torch.tensor(artifact["model"]["theta"], dtype=torch.float32)
            )
            self.register_buffer(
                "variance", torch.tensor(artifact["model"]["variance"], dtype=torch.float32)
            )
            self.register_buffer(
                "prior", torch.tensor(artifact["model"]["class_log_prior"], dtype=torch.float32)
            )

        def forward(self, values):
            expanded = values.unsqueeze(1)
            scores = -0.5 * (
                torch.log(6.283185307179586 * self.variance)
                + ((expanded - self.theta) ** 2) / self.variance
            ).sum(dim=2)
            probabilities = torch.softmax(scores + self.prior, dim=1)
            return probabilities[:, 1:2]

    class TinyMlpModule(torch.nn.Module):
        def __init__(self, base: Any, mean: list[float], scale: list[float]) -> None:
            super().__init__()
            self.base = base
            self.register_buffer("mean", torch.tensor(mean, dtype=torch.float32))
            self.register_buffer("scale", torch.tensor(scale, dtype=torch.float32))

        def forward(self, values):
            return torch.sigmoid(self.base((values - self.mean) / self.scale)).reshape(-1, 1)

    class DlrmLiteModule(torch.nn.Module):
        def __init__(self, buckets: int = 4096, embedding_dim: int = 8) -> None:
            super().__init__()
            self.buckets = buckets
            self.embedding = torch.nn.Embedding(buckets, embedding_dim)
            self.bottom = torch.nn.Sequential(
                torch.nn.Linear(13, 32), torch.nn.ReLU(), torch.nn.Linear(32, 16), torch.nn.ReLU()
            )
            self.top = torch.nn.Sequential(
                torch.nn.Linear(16 + embedding_dim, 16), torch.nn.ReLU(), torch.nn.Linear(16, 1)
            )

        def forward(self, values):
            dense = self.bottom(values[:, :13])
            categorical = torch.remainder(torch.abs(values[:, 13:].to(torch.int64)), self.buckets)
            embedded = self.embedding(categorical).mean(dim=1)
            return torch.sigmoid(self.top(torch.cat((dense, embedded), dim=1)))

    registry_path = require_file(data_root / config.input_paths["s3_registry"], "s3_registry")
    registry = read_json(registry_path, "s3_registry")
    probes = dict(registry.get("probes", {}))
    logistic_path = resolve_registry_artifact(
        registry_path, dict(probes.get("logistic", {})), "s3_logistic"
    )
    gaussian_path = resolve_registry_artifact(
        registry_path, dict(probes.get("probabilistic", {})), "s3_gaussian"
    )
    logistic_artifact = read_json(logistic_path, "s3_logistic")
    gaussian_artifact = read_json(gaussian_path, "s3_gaussian")
    replay_path = require_file(data_root / config.input_paths["s3_replay_features"], "s3_replay")
    replay = np.load(replay_path, mmap_mode="r")[: config.sample_rows_per_dataset]
    if replay.shape != (config.sample_rows_per_dataset, 28):
        raise X1ResumeTestbedError("x1_resume_higgs_replay_shape")
    higgs_samples = np.asarray(replay, dtype="float32").tolist()
    higgs_replay_binding = {
        "registry_path": registry_path.relative_to(data_root).as_posix(),
        "registry_sha256": sha256_file(registry_path),
        "registry_bytes": registry_path.stat().st_size,
        "replay_path": replay_path.relative_to(data_root).as_posix(),
        "replay_sha256": sha256_file(replay_path),
        "replay_bytes": replay_path.stat().st_size,
        "replay_shape": [int(value) for value in np.load(replay_path, mmap_mode="r").shape],
        "sample_shape": [int(value) for value in replay.shape],
        "dataset_identity_sha256": registry.get("dataset_identity_sha256"),
        "split_manifest_sha256": registry.get("split_manifest_sha256"),
    }

    s4_registry_path = require_file(data_root / config.input_paths["s4_registry"], "s4_registry")
    s4_registry = read_json(s4_registry_path, "s4_registry")
    s4_artifact_path = resolve_registry_artifact(s4_registry_path, s4_registry, "s4_tiny_mlp")
    checkpoint = torch.load(s4_artifact_path, map_location="cpu", weights_only=False)
    tiny_base = build_tiny_mlp(torch)
    tiny_base.load_state_dict(checkpoint["state_dict"], strict=True)
    preprocessing = dict(s4_registry.get("preprocessing", {}))

    buckets = 4096
    criteo_samples, criteo_binding = load_criteo_samples(
        data_root / config.input_paths["s5_manifest"],
        config.sample_rows_per_dataset,
        buckets,
        data_root=data_root,
    )
    torch.manual_seed(config.seed)
    modules = {
        "higgs_logistic_regression": LogisticModule(logistic_artifact).eval(),
        "higgs_gaussian_nb": GaussianModule(gaussian_artifact).eval(),
        "higgs_tiny_mlp": TinyMlpModule(
            tiny_base.eval(), preprocessing["mean"], preprocessing["scale"]
        ).eval(),
        "criteo_dlrm_lite": DlrmLiteModule(buckets=buckets).eval(),
    }
    source_bindings = {
        "higgs_logistic_regression": {
            "source_schema": logistic_artifact.get("schema_version"),
            "source_path": logistic_path.relative_to(data_root).as_posix(),
            "source_sha256": sha256_file(logistic_path),
            "source_bytes": logistic_path.stat().st_size,
            "dataset_identity_sha256": logistic_artifact.get("dataset_identity_sha256"),
            "replay": higgs_replay_binding,
        },
        "higgs_gaussian_nb": {
            "source_schema": gaussian_artifact.get("schema_version"),
            "source_path": gaussian_path.relative_to(data_root).as_posix(),
            "source_sha256": sha256_file(gaussian_path),
            "source_bytes": gaussian_path.stat().st_size,
            "dataset_identity_sha256": gaussian_artifact.get("dataset_identity_sha256"),
            "replay": higgs_replay_binding,
        },
        "higgs_tiny_mlp": {
            "source_schema": s4_registry.get("schema_version"),
            "source_path": s4_artifact_path.relative_to(data_root).as_posix(),
            "source_sha256": sha256_file(s4_artifact_path),
            "source_bytes": s4_artifact_path.stat().st_size,
            "model_identity_sha256": s4_registry.get("model_identity_sha256"),
            "registry_sha256": sha256_file(s4_registry_path),
            "registry_path": s4_registry_path.relative_to(data_root).as_posix(),
            "registry_bytes": s4_registry_path.stat().st_size,
            "preprocessing_sha256": s4_registry.get("preprocessing_sha256"),
            "dataset_identity_sha256": s4_registry.get("dataset_identity_sha256"),
            "split_manifest_sha256": s4_registry.get("split_manifest_sha256"),
            "replay": higgs_replay_binding,
        },
        "criteo_dlrm_lite": {
            **criteo_binding,
            "parameter_origin": "deterministic_seeded_testbed_initialization",
            "training_or_quality_claim": False,
            "seed": config.seed,
        },
    }
    scripted: dict[str, Any] = {}
    for model_id, module in modules.items():
        width = 39 if model_id == "criteo_dlrm_lite" else 28
        scripted[model_id] = torch.jit.trace(
            module,
            torch.zeros((2, width), dtype=torch.float32),
            strict=True,
        )
    return {
        "modules": scripted,
        "bindings": source_bindings,
    }, {
        "higgs_logistic_regression": higgs_samples,
        "higgs_gaussian_nb": higgs_samples,
        "higgs_tiny_mlp": higgs_samples,
        "criteo_dlrm_lite": criteo_samples,
    }


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def committed_blob(relative: str, revision: str) -> dict[str, str]:
    path = ROOT / relative
    repository_root = Path(git("rev-parse", "--show-toplevel"))
    repository_relative = path.resolve().relative_to(repository_root.resolve()).as_posix()
    blob = git("rev-parse", f"{revision}:{repository_relative}")
    committed = subprocess.run(
        ["git", "show", f"{revision}:{repository_relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    if path.read_bytes() != committed:
        raise X1ResumeTestbedError(f"x1_resume_source_blob_mismatch:{relative}")
    return {
        "path": relative,
        "source_revision": revision,
        "blob_oid": blob,
        "sha256": hashlib.sha256(committed).hexdigest(),
        "working_sha256": sha256_file(path),
    }


def main() -> int:
    args = parse_args()
    config_path = require_default_config_path(args.config, ROOT)
    config = X1ResumeConfig.from_path(config_path)
    if git("status", "--porcelain"):
        raise X1ResumeTestbedError("x1_resume_prepare_requires_clean_committed_worktree")
    if args.output.exists():
        raise X1ResumeTestbedError("x1_resume_output_exists")
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        import torch

        built, samples = build_models(config, args.data_root)
        for profile, batching in config.batching.items():
            repository = args.output / f"batch-{profile}"
            for model in config.models:
                version_root = repository / model.model_id / "1"
                version_root.mkdir(parents=True)
                built["modules"][model.model_id].save(str(version_root / "model.pt"))
                (version_root.parent / "config.pbtxt").write_text(
                    config_pbtxt(model.model_id, model.input_width, batching=dict(batching)),
                    encoding="utf-8",
                    newline="\n",
                )
        oracle: dict[str, Any] = {}
        for model in config.models:
            values = torch.tensor(samples[model.model_id], dtype=torch.float32)
            output = built["modules"][model.model_id](values).detach().cpu().reshape(-1).tolist()
            if len(output) != len(values):
                raise X1ResumeTestbedError(f"x1_resume_oracle_shape:{model.model_id}")
            oracle[model.model_id] = {
                "input_width": model.input_width,
                "sample_count": len(values),
                "first_output": float(output[0]),
                "output_sha256": canonical_sha256([float(value) for value in output]),
                "outputs": [float(value) for value in output],
            }
    except Exception:
        shutil.rmtree(args.output, ignore_errors=True)
        raise
    sample_path = args.output / "testbed-samples.json"
    canonical_write(
        sample_path,
        {
            "schema_version": "evm.s8_v4.x1_resume_samples.v1",
            "seed": config.seed,
            "samples": samples,
            "oracle": oracle,
        },
    )
    entries = [
        {
            "path": path.relative_to(args.output).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(item for item in args.output.rglob("*") if item.is_file())
    ]
    profile_identities = {}
    model_identities = {}
    for profile in config.batching:
        selected = [item for item in entries if item["path"].startswith(f"batch-{profile}/")]
        profile_identities[profile] = {
            "entry_count": len(selected),
            "repository_sha256": canonical_sha256(selected),
        }
        for model in config.models:
            artifact = next(
                item
                for item in selected
                if item["path"] == f"batch-{profile}/{model.model_id}/1/model.pt"
            )
            model_config = next(
                item
                for item in selected
                if item["path"] == f"batch-{profile}/{model.model_id}/config.pbtxt"
            )
            model_identities[f"{profile}:{model.model_id}"] = {
                "artifact_sha256": artifact["sha256"],
                "config_sha256": model_config["sha256"],
            }
    source_revision = git("rev-parse", "HEAD")
    source_tree_sha = git("rev-parse", "HEAD^{tree}")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "claim_class": "preliminary_controlled_testbed",
        "credit": "non_credit",
        "config_sha256": config.sha256,
        "source_revision": source_revision,
        "source_tree_sha": source_tree_sha,
        "source_blobs": [
            committed_blob(relative, source_revision) for relative in REQUIRED_SOURCE_BLOB_PATHS
        ],
        "triton_image": config.immutable_image,
        "backend": "pytorch",
        "instance_kind": "KIND_GPU",
        "cpu_fallback_allowed": False,
        "model_ids": [model.model_id for model in config.models],
        "source_bindings": built["bindings"],
        "samples_sha256": sha256_file(sample_path),
        "profile_identities": profile_identities,
        "model_identities": model_identities,
        "entries": entries,
        "repository_sha256": canonical_sha256(entries),
        "model_claim_contract": dict(MODEL_CLAIM_CONTRACT),
        "model_claim_contract_sha256": canonical_sha256(MODEL_CLAIM_CONTRACT),
        "claim_boundary": config.claim_boundary,
    }
    canonical_write(args.output / "model-repository-manifest.json", manifest)
    print(canonical(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

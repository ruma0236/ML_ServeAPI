"""Freeze three governed HIGGS logistic requests without executing the model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


EXPECTED_FILE_SHA256 = {
    "registry": "6666ef28ea681ee694525e2d5ee1f2e1fefe460f4cc730a6c64fb78fe6bed012",
    "split_manifest": "7058c9fd81e06465e64d8be98cfad065aefccea2063c0404775fcaf0d7c19e00",
    "features": "dde408a9a1df8a1cdd0e12ac6cbd0ef655ffa5049781b1d008a449c8b8fbc359",
    "source_config": "57fe6f4f4c9f1da3cad9e466852e7fdc5a5f14c0b146ffb743858b21c0aac937",
}
DATASET_SHA256 = "eecb0f824e149c8e9062f216d834e8ccc519ea774c0f79392f23bb11f3f7550d"
ARTIFACT_SHA256 = "b4bbd5b76612b18b945b4441682688b759614d12748089e368d5c12b1614e18b"
MODEL_SHA256 = "f82e8a55c8f8f50076ab21043d4857dd810b0e2d8a49918be89b5a914df9950a"
SEQUENCE_SHA256 = "bbecd6044bbef0beac52e4cb7bd5eb2155fd0b61da8e7bfe121e447ce7247c30"
DATASET_VERSION = "uci-higgs-2014-s3-v1"
SEED = 20260817
ROW_COUNT = 200000
FEATURE_COUNT = 28
SELECTION_COUNT = 32768


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def pretty_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--features", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-config", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    require(not output_dir.exists(), f"output directory already exists: {output_dir}")
    require(output_dir.parent.is_dir(), f"output parent is not a directory: {output_dir.parent}")
    paths = {
        "registry": args.registry.resolve(),
        "features": args.features.resolve(),
        "split_manifest": args.split_manifest.resolve(),
        "source_config": args.source_config.resolve(),
    }
    for name, path in paths.items():
        require(path.is_file(), f"{name} is not a file: {path}")
    hashes = {name: file_sha256(path) for name, path in paths.items()}
    require(hashes == EXPECTED_FILE_SHA256, f"governed input hash mismatch: {hashes}")

    registry = load_object(paths["registry"])
    split = load_object(paths["split_manifest"])
    registry_fields = {
        "schema_version": "evm.s3_capacity_registry.v1",
        "dataset_id": "uci-higgs",
        "dataset_version": DATASET_VERSION,
        "dataset_identity_sha256": DATASET_SHA256,
        "feature_count": FEATURE_COUNT,
        "seed": SEED,
        "split_manifest_sha256": EXPECTED_FILE_SHA256["split_manifest"],
    }
    require(
        all(registry.get(key) == value for key, value in registry_fields.items()),
        "registry contract mismatch",
    )
    split_fields = {
        "schema_version": "evm.s3_higgs_split_manifest.v1",
        "dataset_version": DATASET_VERSION,
        "dataset_identity_sha256": DATASET_SHA256,
        "seed": SEED,
        "experiment_config_sha256": registry.get("experiment_config_sha256"),
    }
    require(
        all(split.get(key) == value for key, value in split_fields.items()),
        "split contract mismatch",
    )
    replay = split.get("samples", {}).get("replay", {})
    require(replay.get("row_count") == ROW_COUNT, "replay row-count mismatch")
    require(replay.get("features_sha256") == hashes["features"], "replay hash binding")
    referenced_features = (paths["split_manifest"].parent / replay["features_uri"]).resolve()
    require(referenced_features == paths["features"], "replay path binding")

    descriptor = registry.get("probes", {}).get("logistic", {})
    descriptor_fields = {
        "algorithm": "linear_logit",
        "artifact_sha256": ARTIFACT_SHA256,
        "model_identity_sha256": MODEL_SHA256,
    }
    require(
        all(descriptor.get(key) == value for key, value in descriptor_fields.items()),
        "logistic descriptor mismatch",
    )
    artifact_ref = Path(descriptor["artifact_uri"])
    artifact_path = (paths["registry"].parent / artifact_ref).resolve()
    require(not artifact_ref.is_absolute(), "model artifact URI is absolute")
    require(
        artifact_path.is_relative_to(paths["registry"].parent), "artifact escapes registry root"
    )
    require(artifact_path.is_file(), f"model artifact is not a file: {artifact_path}")
    require(file_sha256(artifact_path) == ARTIFACT_SHA256, "model artifact hash mismatch")
    identity_material = {
        "schema_version": "evm.s3_capacity_model_identity.v1",
        "probe_family": "logistic",
        "dataset_identity_sha256": DATASET_SHA256,
        "artifact_sha256": ARTIFACT_SHA256,
        "algorithm": "linear_logit",
    }
    require(
        hashlib.sha256(compact_bytes(identity_material)).hexdigest() == MODEL_SHA256,
        "derived model identity mismatch",
    )

    features = np.load(paths["features"], mmap_mode="r", allow_pickle=False)
    require(
        features.shape == (ROW_COUNT, FEATURE_COUNT), f"replay shape mismatch: {features.shape}"
    )
    indices = np.random.default_rng(SEED).choice(
        ROW_COUNT, size=min(SELECTION_COUNT, ROW_COUNT), replace=False
    )
    sequence_sha256 = hashlib.sha256(compact_bytes([int(index) for index in indices])).hexdigest()
    require(sequence_sha256 == SEQUENCE_SHA256, "selection sequence mismatch")
    rows = [features[int(index)].astype(float).tolist() for index in indices[:3]]
    require(all(math.isfinite(value) for row in rows for value in row), "non-finite feature")
    require(file_sha256(paths["features"]) == hashes["features"], "features changed while read")

    requests = []
    for number, row in enumerate(rows, 1):
        body = {
            "schema_version": "evm.s3_capacity_probe_request.v1",
            "probe_family": "logistic",
            "dataset_identity_sha256": DATASET_SHA256,
            "features": row,
        }
        requests.append({"logical_request_id": f"capacity-smoke-{number:06d}", "body": body})
    corpus = {"schema_version": "evm.container_test.request_corpus.v1", "requests": requests}
    corpus_bytes = pretty_bytes(corpus)
    sources = {name: {"path": str(path), "sha256": hashes[name]} for name, path in paths.items()}
    sources["model_artifact"] = {
        "path": str(artifact_path),
        "uri": artifact_ref.as_posix(),
        "sha256": ARTIFACT_SHA256,
    }
    provenance = {
        "schema_version": "evm.container_test.corpus_provenance.v1",
        "dataset": {
            "id": "uci-higgs",
            "version": DATASET_VERSION,
            "identity_sha256": DATASET_SHA256,
        },
        "sources": sources,
        "model": {**descriptor_fields, "probe_family": "logistic"},
        "selection": {
            "method": "numpy.random.Generator.choice",
            "numpy_version": np.__version__,
            "seed": SEED,
            "population_size": ROW_COUNT,
            "selected_count": len(indices),
            "replace": False,
            "first_three_indices": [int(index) for index in indices[:3]],
            "sequence_sha256": sequence_sha256,
        },
        "request_corpus": {
            "path": "request_corpus.json",
            "sha256": hashlib.sha256(corpus_bytes).hexdigest(),
            "request_count": len(requests),
        },
    }
    output_dir.mkdir(exist_ok=False)
    (output_dir / "request_corpus.json").write_bytes(corpus_bytes)
    (output_dir / "corpus-provenance.json").write_bytes(pretty_bytes(provenance))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

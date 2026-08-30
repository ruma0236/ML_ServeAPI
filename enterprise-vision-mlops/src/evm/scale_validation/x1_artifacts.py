from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from evm.control_panel.scenario_workloads import assert_scale_validation_gpu_lease_owner
from evm.model_runtime.tiny_mlp import build_tiny_mlp
from evm.scale_validation.x1_contract import MODEL_IDS, X1Contract, canonical_sha256, sha256_file


PROFILE_IDS = ("disabled", "enabled-4-8-2ms", "enabled-8-16-10ms")
CUDA_REPEATABILITY_REQUESTS = 64
CUDA_REPEATABILITY_TIMEOUT_SECONDS = 30


class X1ArtifactError(RuntimeError):
    pass


def _load_artifact_dependencies() -> tuple[Any, Any, Any]:
    # PyArrow can initialize a conflicting Windows DLL before Torch loads c10.dll.
    import torch
    import numpy as np
    import pyarrow.parquet as pq

    return np, pq, torch


def render_triton_config(*, model_id: str, feature_count: int, profile_id: str) -> bytes:
    if model_id not in MODEL_IDS or feature_count not in {28, 39}:
        raise X1ArtifactError("x1_artifact_model_contract")
    batching = {
        "disabled": None,
        "enabled-4-8-2ms": (16, (4, 8), 2000),
        "enabled-8-16-10ms": (32, (8, 16), 10000),
    }.get(profile_id)
    if profile_id not in PROFILE_IDS:
        raise X1ArtifactError("x1_artifact_profile")
    max_batch_size = 0 if batching is None else batching[0]
    lines = [
        f'name: "{model_id}"',
        'backend: "pytorch"',
        f"max_batch_size: {max_batch_size}",
        "input [",
        "  {",
        '    name: "INPUT__0"',
        "    data_type: TYPE_FP32",
        f"    dims: [ {feature_count} ]",
        "  }",
        "]",
        "output [",
        "  {",
        '    name: "OUTPUT__0"',
        "    data_type: TYPE_FP32",
        "    dims: [ 1 ]",
        "  }",
        "]",
        "version_policy { specific { versions: [ 1 ] } }",
        "instance_group [",
        "  {",
        "    count: 1",
        "    kind: KIND_GPU",
        "    gpus: [ 0 ]",
        "  }",
        "]",
    ]
    if batching is not None:
        preferred = ", ".join(str(value) for value in batching[1])
        lines.extend(
            [
                "dynamic_batching {",
                f"  preferred_batch_size: [ {preferred} ]",
                f"  max_queue_delay_microseconds: {batching[2]}",
                "  preserve_ordering: true",
                "}",
            ]
        )
    return ("\n".join(lines) + "\n").encode("ascii")


def prepare_x1_artifacts(
    contract: X1Contract,
    *,
    output_root: Path,
    source_revision: str,
    source_tree: str,
    lease_run_id: str,
    lease_id: str,
    fencing_token: str,
) -> dict[str, Any]:
    contract.assert_unchanged()
    if len(source_revision) != 40 or len(source_tree) != 40:
        raise X1ArtifactError("x1_artifact_source_identity")
    root = output_root.resolve()
    if root.exists():
        raise X1ArtifactError("x1_artifact_output_exists")
    root.mkdir(parents=True, exist_ok=False)
    assert_scale_validation_gpu_lease_owner(
        run_id=lease_run_id,
        lease_id=lease_id,
        fencing_token=fencing_token,
        purpose="scale_validation_training",
        scenario_id="X1",
        model_family="heterogeneous",
    )
    try:
        np, pq, torch = _load_artifact_dependencies()
    except ImportError as exc:
        raise X1ArtifactError("x1_artifact_dependency_missing") from exc
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise X1ArtifactError("x1_artifact_cuda_required")
    torch.manual_seed(int(contract.payload["seed"]))
    torch.cuda.manual_seed_all(int(contract.payload["seed"]))
    np.random.seed(int(contract.payload["seed"]))
    training = dict(contract.payload["artifact_training"])
    source = dict(contract.payload["source"])
    source_manifest = _source_manifest(contract, source_revision, source_tree)
    source_root = root / "source-artifacts"
    source_root.mkdir()
    models: dict[str, dict[str, Any]] = {}
    samples: dict[str, list[list[float]]] = {}

    logistic_source = _read_json(contract.data_root / source["s3_registry"])["probes"]["logistic"]
    logistic_payload = _read_json(
        contract.data_root / "artifacts/scale_validation/s3" / logistic_source["artifact_uri"]
    )
    logistic_model = _build_logistic(torch, logistic_payload).eval().cuda()
    models[MODEL_IDS[0]] = _freeze_model(
        torch,
        logistic_model,
        source_root / f"{MODEL_IDS[0]}.pt",
        feature_count=28,
        source_artifact={
            "path": logistic_source["artifact_uri"],
            "sha256": logistic_source["artifact_sha256"],
            "model_identity_sha256": logistic_source["model_identity_sha256"],
        },
    )

    probabilistic_source = _read_json(contract.data_root / source["s3_registry"])["probes"][
        "probabilistic"
    ]
    probabilistic_payload = _read_json(
        contract.data_root / "artifacts/scale_validation/s3" / probabilistic_source["artifact_uri"]
    )
    probabilistic_model = _build_gaussian_nb(torch, probabilistic_payload).eval().cuda()
    models[MODEL_IDS[1]] = _freeze_model(
        torch,
        probabilistic_model,
        source_root / f"{MODEL_IDS[1]}.pt",
        feature_count=28,
        source_artifact={
            "path": probabilistic_source["artifact_uri"],
            "sha256": probabilistic_source["artifact_sha256"],
            "model_identity_sha256": probabilistic_source["model_identity_sha256"],
        },
    )

    train_features = np.load(contract.data_root / source["s4_train_features"], mmap_mode="r")
    train_labels = np.load(contract.data_root / source["s4_train_labels"], mmap_mode="r")
    validation_features = np.load(
        contract.data_root / source["s4_validation_features"], mmap_mode="r"
    )
    validation_labels = np.load(contract.data_root / source["s4_validation_labels"], mmap_mode="r")
    replay_features = np.load(contract.data_root / source["s4_replay_features"], mmap_mode="r")
    samples[MODEL_IDS[0]] = np.asarray(
        replay_features[: training["oracle_rows"]], dtype="float32"
    ).tolist()
    samples[MODEL_IDS[1]] = list(samples[MODEL_IDS[0]])
    tiny_model, tiny_training = _train_tiny_mlp(
        torch,
        np,
        train_features,
        train_labels,
        validation_features,
        validation_labels,
        training,
        seed=int(contract.payload["seed"]),
    )
    tiny_checkpoint = source_root / f"{MODEL_IDS[2]}-training.pt"
    torch.save(tiny_training["checkpoint"], tiny_checkpoint)
    models[MODEL_IDS[2]] = _freeze_model(
        torch,
        tiny_model,
        source_root / f"{MODEL_IDS[2]}.pt",
        feature_count=28,
        source_artifact={
            "path": tiny_checkpoint.relative_to(root).as_posix(),
            "sha256": sha256_file(tiny_checkpoint),
            "training": tiny_training["summary"],
        },
    )
    samples[MODEL_IDS[2]] = list(samples[MODEL_IDS[0]])

    criteo_manifest_path = contract.data_root / source["s5_manifest"]
    criteo_shard_path = contract.data_root / source["s5_training_shard"]
    criteo_manifest = _read_json(criteo_manifest_path)
    criteo_table = pq.read_table(criteo_shard_path).slice(
        0, training["criteo_train_rows"] + training["criteo_validation_rows"]
    )
    criteo_values, criteo_labels, criteo_preprocessing = _preprocess_criteo(
        np,
        criteo_table,
        vocab_size=training["criteo_embedding_vocab_size"],
        train_rows=training["criteo_train_rows"],
    )
    dlrm_model, dlrm_training = _train_dlrm_lite(
        torch,
        np,
        criteo_values,
        criteo_labels,
        training,
        seed=int(contract.payload["seed"]),
    )
    dlrm_checkpoint = source_root / f"{MODEL_IDS[3]}-training.pt"
    torch.save(dlrm_training["checkpoint"], dlrm_checkpoint)
    models[MODEL_IDS[3]] = _freeze_model(
        torch,
        dlrm_model,
        source_root / f"{MODEL_IDS[3]}.pt",
        feature_count=39,
        repeatability_input=criteo_values[0].tolist(),
        expected_device_name=str(contract.payload["triton"]["gpu_name"]),
        source_artifact={
            "path": dlrm_checkpoint.relative_to(root).as_posix(),
            "sha256": sha256_file(dlrm_checkpoint),
            "training": dlrm_training["summary"],
            "dataset_manifest_sha256": sha256_file(criteo_manifest_path),
            "dataset_identity": {
                "dataset_id": criteo_manifest["dataset_id"],
                "dataset_version": criteo_manifest["dataset_version"],
                "source_revision": criteo_manifest["source_revision"],
                "source_license": criteo_manifest["source_license"],
                "training_shard_sha256": sha256_file(criteo_shard_path),
            },
            "preprocessing": criteo_preprocessing,
            "preprocessing_sha256": canonical_sha256(criteo_preprocessing),
        },
    )
    samples[MODEL_IDS[3]] = criteo_values[: training["oracle_rows"]].tolist()

    repositories: dict[str, Any] = {}
    for profile_id in PROFILE_IDS:
        profile_root = root / "model-repositories" / profile_id
        profile_root.mkdir(parents=True)
        profile_entries: list[dict[str, Any]] = []
        for model_id in MODEL_IDS:
            model_root = profile_root / model_id
            version_root = model_root / "1"
            version_root.mkdir(parents=True)
            config_path = model_root / "config.pbtxt"
            config_path.write_bytes(
                render_triton_config(
                    model_id=model_id,
                    feature_count=int(models[model_id]["feature_count"]),
                    profile_id=profile_id,
                )
            )
            artifact_path = version_root / "model.pt"
            shutil.copyfile(source_root / f"{model_id}.pt", artifact_path)
        profile_entries = _entries(profile_root)
        repositories[profile_id] = {
            "relative_root": profile_root.relative_to(root).as_posix(),
            "entries": profile_entries,
            "aggregate_sha256": canonical_sha256(profile_entries),
        }

    oracle: dict[str, Any] = {}
    for model_id in MODEL_IDS:
        values = np.asarray(samples[model_id], dtype="float32")
        device_values = torch.from_numpy(values).cuda()
        with torch.inference_mode():
            output = models[model_id]["runtime_model"](device_values).detach().cpu().numpy()
        sample_path = root / f"oracle-{model_id}.json"
        sample_payload = {
            "schema_version": "evm.s8_v4.x1_correctness_oracle.v1",
            "model_id": model_id,
            "input": values.tolist(),
            "output": output.tolist(),
            "absolute_tolerance": 1e-5,
            "relative_tolerance": 1e-5,
        }
        _write_json(sample_path, sample_payload)
        oracle[model_id] = {
            "path": sample_path.relative_to(root).as_posix(),
            "sha256": sha256_file(sample_path),
            "rows": len(values),
        }
        models[model_id].pop("runtime_model")

    manifest = {
        "schema_version": "evm.s8_v4.x1_artifact_manifest.v1",
        "source_identity": {"revision": source_revision, "tree": source_tree},
        "contract_sha256": contract.sha256,
        "preparation_lease": {
            "run_id": lease_run_id,
            "lease_id": lease_id,
            "fencing_token_sha256": hashlib.sha256(fencing_token.encode("utf-8")).hexdigest(),
            "purpose": "scale_validation_training",
            "scenario_id": "X1",
            "model_family": "heterogeneous",
            "source_revision": source_revision,
        },
        "source_manifest": source_manifest,
        "models": models,
        "repositories": repositories,
        "correctness_oracles": oracle,
        "framework": {
            "torch": str(torch.__version__),
            "cuda_runtime": str(torch.version.cuda),
            "cudnn": str(torch.backends.cudnn.version()),
        },
        "claim_boundary": contract.payload["claim"]["boundary"],
    }
    manifest["artifact_inventory"] = _entries(root)
    manifest["artifact_inventory_aggregate_sha256"] = canonical_sha256(
        manifest["artifact_inventory"]
    )
    manifest["artifact_identity_sha256"] = canonical_sha256(manifest)
    manifest_path = root / "x1-artifact-manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "artifact_identity_sha256": manifest["artifact_identity_sha256"],
        "profiles": list(PROFILE_IDS),
        "models": list(MODEL_IDS),
    }


def validate_x1_artifacts(
    contract: X1Contract,
    *,
    manifest_path: Path,
    source_revision: str,
    source_tree: str,
    lease_run_id: str,
    lease_id: str,
    fencing_token: str,
) -> dict[str, Any]:
    contract.assert_unchanged()
    root = manifest_path.resolve().parent
    manifest = _read_json(manifest_path)
    expected_keys = {
        "schema_version",
        "source_identity",
        "contract_sha256",
        "preparation_lease",
        "source_manifest",
        "models",
        "repositories",
        "correctness_oracles",
        "framework",
        "claim_boundary",
        "artifact_inventory",
        "artifact_inventory_aggregate_sha256",
        "artifact_identity_sha256",
    }
    if set(manifest) != expected_keys:
        raise X1ArtifactError("x1_artifact_manifest_schema")
    if manifest["schema_version"] != "evm.s8_v4.x1_artifact_manifest.v1":
        raise X1ArtifactError("x1_artifact_manifest_version")
    if manifest["source_identity"] != {"revision": source_revision, "tree": source_tree}:
        raise X1ArtifactError("x1_artifact_source_identity")
    if manifest["contract_sha256"] != contract.sha256:
        raise X1ArtifactError("x1_artifact_contract_binding")
    expected_lease = {
        "run_id": lease_run_id,
        "lease_id": lease_id,
        "fencing_token_sha256": hashlib.sha256(fencing_token.encode("utf-8")).hexdigest(),
        "purpose": "scale_validation_training",
        "scenario_id": "X1",
        "model_family": "heterogeneous",
        "source_revision": source_revision,
    }
    if manifest["preparation_lease"] != expected_lease:
        raise X1ArtifactError("x1_artifact_preparation_lease")
    if manifest["source_manifest"] != _source_manifest(contract, source_revision, source_tree):
        raise X1ArtifactError("x1_artifact_source_manifest")
    framework = manifest["framework"]
    if (
        not isinstance(framework, Mapping)
        or set(framework) != {"torch", "cuda_runtime", "cudnn"}
        or any(not isinstance(value, str) or not value for value in framework.values())
    ):
        raise X1ArtifactError("x1_artifact_framework_identity")
    models = manifest["models"]
    if not isinstance(models, Mapping) or set(models) != set(MODEL_IDS):
        raise X1ArtifactError("x1_artifact_model_set")
    expected_features = dict(zip(MODEL_IDS, (28, 28, 28, 39), strict=True))
    for model_id in MODEL_IDS:
        identity = models[model_id]
        expected_model_keys = {
            "feature_count",
            "dtype",
            "backend",
            "model_version",
            "artifact_path",
            "artifact_sha256",
            "source_artifact",
        }
        if model_id == MODEL_IDS[3]:
            expected_model_keys.add("cuda_repeatability")
        if not isinstance(identity, Mapping) or set(identity) != expected_model_keys:
            raise X1ArtifactError(f"x1_artifact_model_schema:{model_id}")
        if (
            identity["feature_count"] != expected_features[model_id]
            or identity["dtype"] != "float32"
            or identity["backend"] != "pytorch"
            or identity["model_version"] != "1"
            or identity["artifact_path"] != f"{model_id}.pt"
            or not isinstance(identity["source_artifact"], Mapping)
        ):
            raise X1ArtifactError(f"x1_artifact_model_identity:{model_id}")
        artifact = _contained(root, f"source-artifacts/{model_id}.pt")
        if identity["artifact_sha256"] != sha256_file(artifact):
            raise X1ArtifactError(f"x1_artifact_model_sha:{model_id}")
        if model_id == MODEL_IDS[3]:
            _validate_cuda_repeatability_record(
                identity["cuda_repeatability"],
                expected_device_name=str(contract.payload["triton"]["gpu_name"]),
            )
    repositories = manifest["repositories"]
    if not isinstance(repositories, Mapping) or set(repositories) != set(PROFILE_IDS):
        raise X1ArtifactError("x1_artifact_repository_set")
    for profile_id in PROFILE_IDS:
        profile = repositories[profile_id]
        if not isinstance(profile, Mapping) or set(profile) != {
            "relative_root",
            "entries",
            "aggregate_sha256",
        }:
            raise X1ArtifactError(f"x1_artifact_repository_schema:{profile_id}")
        relative_root = f"model-repositories/{profile_id}"
        if profile["relative_root"] != relative_root:
            raise X1ArtifactError(f"x1_artifact_repository_root:{profile_id}")
        profile_root = _contained_directory(root, relative_root)
        entries = _entries(profile_root)
        if profile["entries"] != entries or profile["aggregate_sha256"] != canonical_sha256(
            entries
        ):
            raise X1ArtifactError(f"x1_artifact_repository_inventory:{profile_id}")
        expected_paths = {f"{model_id}/config.pbtxt" for model_id in MODEL_IDS} | {
            f"{model_id}/1/model.pt" for model_id in MODEL_IDS
        }
        if {entry["path"] for entry in entries} != expected_paths:
            raise X1ArtifactError(f"x1_artifact_repository_paths:{profile_id}")
        for model_id in MODEL_IDS:
            config_path = _contained(profile_root, f"{model_id}/config.pbtxt")
            expected_config = render_triton_config(
                model_id=model_id,
                feature_count=expected_features[model_id],
                profile_id=profile_id,
            )
            if config_path.read_bytes() != expected_config:
                raise X1ArtifactError(f"x1_artifact_config_bytes:{profile_id}:{model_id}")
            artifact = _contained(profile_root, f"{model_id}/1/model.pt")
            if sha256_file(artifact) != models[model_id]["artifact_sha256"]:
                raise X1ArtifactError(f"x1_artifact_repository_model_sha:{profile_id}:{model_id}")
    oracles = manifest["correctness_oracles"]
    if not isinstance(oracles, Mapping) or set(oracles) != set(MODEL_IDS):
        raise X1ArtifactError("x1_artifact_oracle_set")
    expected_rows = int(contract.payload["artifact_training"]["oracle_rows"])
    for model_id in MODEL_IDS:
        reference = oracles[model_id]
        if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256", "rows"}:
            raise X1ArtifactError(f"x1_artifact_oracle_schema:{model_id}")
        expected_path = f"oracle-{model_id}.json"
        if reference["path"] != expected_path or reference["rows"] != expected_rows:
            raise X1ArtifactError(f"x1_artifact_oracle_identity:{model_id}")
        oracle_path = _contained(root, expected_path)
        if reference["sha256"] != sha256_file(oracle_path):
            raise X1ArtifactError(f"x1_artifact_oracle_sha:{model_id}")
        oracle = _read_json(oracle_path)
        if (
            oracle.get("schema_version") != "evm.s8_v4.x1_correctness_oracle.v1"
            or oracle.get("model_id") != model_id
            or oracle.get("absolute_tolerance") != 1e-5
            or oracle.get("relative_tolerance") != 1e-5
            or not isinstance(oracle.get("input"), list)
            or not isinstance(oracle.get("output"), list)
            or len(oracle["input"]) != expected_rows
            or len(oracle["output"]) != expected_rows
        ):
            raise X1ArtifactError(f"x1_artifact_oracle_payload:{model_id}")
        if model_id == MODEL_IDS[3]:
            repeatability = models[model_id]["cuda_repeatability"]
            if repeatability["input_sha256"] != canonical_sha256(oracle["input"][0]):
                raise X1ArtifactError("x1_artifact_cuda_repeatability_input")
            expected_output = float(oracle["output"][0][0])
            if any(
                not math.isclose(
                    float(value),
                    expected_output,
                    rel_tol=float(oracle["relative_tolerance"]),
                    abs_tol=float(oracle["absolute_tolerance"]),
                )
                for value in repeatability["output_values"]
            ):
                raise X1ArtifactError("x1_artifact_cuda_repeatability_oracle")
    inventory = _entries(root, excluded={manifest_path.resolve()})
    expected_inventory_paths = {
        *(f"source-artifacts/{model_id}.pt" for model_id in MODEL_IDS),
        "source-artifacts/higgs_tiny_mlp-training.pt",
        "source-artifacts/criteo_dlrm_lite-training.pt",
        *(f"oracle-{model_id}.json" for model_id in MODEL_IDS),
        *(
            f"model-repositories/{profile_id}/{model_id}/{relative}"
            for profile_id in PROFILE_IDS
            for model_id in MODEL_IDS
            for relative in ("config.pbtxt", "1/model.pt")
        ),
    }
    if (
        {entry["path"] for entry in inventory} != expected_inventory_paths
        or len(inventory) != 34
        or manifest["artifact_inventory"] != inventory
        or manifest["artifact_inventory_aggregate_sha256"] != canonical_sha256(inventory)
    ):
        raise X1ArtifactError("x1_artifact_inventory")
    if manifest["claim_boundary"] != contract.payload["claim"]["boundary"]:
        raise X1ArtifactError("x1_artifact_claim_boundary")
    identity_payload = dict(manifest)
    observed_identity = identity_payload.pop("artifact_identity_sha256")
    if observed_identity != canonical_sha256(identity_payload):
        raise X1ArtifactError("x1_artifact_identity_sha")
    return manifest


def _build_logistic(torch: Any, payload: Mapping[str, Any]) -> Any:
    transform = payload["transform"]
    model = payload["model"]

    class Logistic(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.register_buffer("mean", torch.tensor(transform["mean"], dtype=torch.float32))
            self.register_buffer("scale", torch.tensor(transform["scale"], dtype=torch.float32))
            self.register_buffer("weights", torch.tensor(model["weights"], dtype=torch.float32))
            self.register_buffer(
                "intercept", torch.tensor([model["intercept"]], dtype=torch.float32)
            )

        def forward(self, value: Any) -> Any:
            logits = ((value - self.mean) / self.scale).matmul(self.weights) + self.intercept
            return torch.sigmoid(logits).unsqueeze(-1)

    return Logistic()


def _build_gaussian_nb(torch: Any, payload: Mapping[str, Any]) -> Any:
    model = payload["model"]

    class GaussianNb(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.register_buffer("theta", torch.tensor(model["theta"], dtype=torch.float32))
            self.register_buffer("variance", torch.tensor(model["variance"], dtype=torch.float32))
            self.register_buffer(
                "log_prior", torch.tensor(model["class_log_prior"], dtype=torch.float32)
            )

        def forward(self, value: Any) -> Any:
            expanded = value.reshape(-1, 28).unsqueeze(1)
            log_likelihood = -0.5 * (
                torch.log(2.0 * math.pi * self.variance)
                + ((expanded - self.theta) ** 2) / self.variance
            ).sum(dim=2)
            probability = torch.softmax(log_likelihood + self.log_prior, dim=1)[:, 1]
            return probability.unsqueeze(-1)

    return GaussianNb()


def _build_dlrm_lite(torch: Any, *, vocab_size: int, embedding_dim: int) -> Any:
    class DlrmLite(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.bottom = torch.nn.Sequential(
                torch.nn.Linear(13, 32), torch.nn.ReLU(), torch.nn.Linear(32, 16), torch.nn.ReLU()
            )
            self.embedding = torch.nn.Embedding(vocab_size * 26, embedding_dim)
            self.register_buffer(
                "categorical_offsets",
                torch.arange(26, dtype=torch.long) * vocab_size,
            )
            self.top = torch.nn.Sequential(
                torch.nn.Linear(16 + 26 * embedding_dim, 32),
                torch.nn.ReLU(),
                torch.nn.Linear(32, 1),
            )

        def forward(self, value: Any) -> Any:
            matrix = value.reshape(-1, 39)
            dense = self.bottom(matrix[:, :13])
            categorical = matrix[:, 13:].to(dtype=torch.long).remainder(vocab_size)
            embedded = self.embedding(categorical + self.categorical_offsets).flatten(1)
            return torch.sigmoid(self.top(torch.cat([dense, embedded], dim=1)))

    return DlrmLite()


def _train_tiny_mlp(
    torch: Any,
    np: Any,
    train_features: Any,
    train_labels: Any,
    validation_features: Any,
    validation_labels: Any,
    training: Mapping[str, Any],
    *,
    seed: int,
) -> tuple[Any, dict[str, Any]]:
    train_rows = int(training["higgs_train_rows"])
    validation_rows = int(training["higgs_validation_rows"])
    features = np.asarray(train_features[:train_rows], dtype="float32")
    labels = np.asarray(train_labels[:train_rows], dtype="float32")
    mean = features.astype("float64").mean(axis=0).astype("float32")
    scale = features.astype("float64").std(axis=0).astype("float32")
    scale[scale < 1e-6] = 1.0
    base = build_tiny_mlp(torch).cuda()
    optimizer = torch.optim.AdamW(
        base.parameters(),
        lr=float(training["higgs_learning_rate"]),
        weight_decay=float(training["higgs_weight_decay"]),
    )
    loss_function = torch.nn.BCEWithLogitsLoss()
    generator = np.random.default_rng(seed)
    losses: list[float] = []
    for _ in range(int(training["higgs_epochs"])):
        permutation = generator.permutation(train_rows)
        epoch_losses: list[float] = []
        for offset in range(0, train_rows, int(training["higgs_batch_size"])):
            indexes = permutation[offset : offset + int(training["higgs_batch_size"])]
            values = torch.from_numpy((features[indexes] - mean) / scale).cuda()
            targets = torch.from_numpy(labels[indexes]).cuda()
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(base(values), targets)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        losses.append(sum(epoch_losses) / len(epoch_losses))

    class TinyWrapper(torch.nn.Module):
        def __init__(self, wrapped: Any) -> None:
            super().__init__()
            self.wrapped = wrapped
            self.register_buffer("mean", torch.from_numpy(mean))
            self.register_buffer("scale", torch.from_numpy(scale))

        def forward(self, value: Any) -> Any:
            return torch.sigmoid(self.wrapped((value - self.mean) / self.scale)).unsqueeze(-1)

    wrapper = TinyWrapper(base.eval()).cuda().eval()
    with torch.inference_mode():
        validation = np.asarray(validation_features[:validation_rows], dtype="float32")
        targets = np.asarray(validation_labels[:validation_rows], dtype="int64")
        predicted = (wrapper(torch.from_numpy(validation).cuda()).squeeze(-1) >= 0.5).cpu().numpy()
    summary = {
        "seed": seed,
        "epochs": int(training["higgs_epochs"]),
        "train_rows": train_rows,
        "validation_rows": validation_rows,
        "final_loss": losses[-1],
        "validation_accuracy": float((predicted == targets).mean()),
        "preprocessing_sha256": canonical_sha256(
            {"kind": "standardize", "mean": mean.tolist(), "scale": scale.tolist()}
        ),
    }
    return wrapper, {"summary": summary, "checkpoint": {"state_dict": base.state_dict(), **summary}}


def _preprocess_criteo(
    np: Any, table: Any, *, vocab_size: int, train_rows: int
) -> tuple[Any, Any, dict[str, Any]]:
    names = table.column_names
    dense_names = [f"int_feature_{index}" for index in range(1, 14)]
    categorical_names = [f"cat_feature_{index}" for index in range(1, 27)]
    if names[:1] != ["label"] or any(name not in names for name in dense_names + categorical_names):
        raise X1ArtifactError("x1_criteo_schema")
    dense = np.zeros((table.num_rows, 13), dtype="float32")
    for index, name in enumerate(dense_names):
        values = table[name].to_pylist()
        dense[:, index] = np.asarray([0.0 if value is None else float(value) for value in values])
    dense = np.log1p(np.maximum(dense, 0.0))
    mean = dense[:train_rows].astype("float64").mean(axis=0).astype("float32")
    scale = dense[:train_rows].astype("float64").std(axis=0).astype("float32")
    scale[scale < 1e-6] = 1.0
    dense = (dense - mean) / scale
    categorical = np.zeros((table.num_rows, 26), dtype="float32")
    for index, name in enumerate(categorical_names):
        categorical[:, index] = np.asarray(
            [
                int(hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16], 16)
                % vocab_size
                for value in table[name].to_pylist()
            ],
            dtype="float32",
        )
    labels = np.asarray([int(value or 0) for value in table["label"].to_pylist()], dtype="float32")
    return (
        np.concatenate([dense, categorical], axis=1),
        labels,
        {
            "dense": {"kind": "log1p_standardize", "mean": mean.tolist(), "scale": scale.tolist()},
            "categorical": {
                "kind": "sha256_prefix_modulo",
                "vocab_size": vocab_size,
                "feature_count": 26,
            },
        },
    )


def _train_dlrm_lite(
    torch: Any,
    np: Any,
    values: Any,
    labels: Any,
    training: Mapping[str, Any],
    *,
    seed: int,
) -> tuple[Any, dict[str, Any]]:
    train_rows = int(training["criteo_train_rows"])
    validation_rows = int(training["criteo_validation_rows"])
    vocab_size = int(training["criteo_embedding_vocab_size"])
    embedding_dim = int(training["criteo_embedding_dim"])

    model = _build_dlrm_lite(
        torch,
        vocab_size=vocab_size,
        embedding_dim=embedding_dim,
    ).cuda()
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(training["criteo_learning_rate"]),
        weight_decay=float(training["criteo_weight_decay"]),
    )
    loss_function = torch.nn.BCELoss()
    generator = np.random.default_rng(seed)
    losses: list[float] = []
    for _ in range(int(training["criteo_epochs"])):
        permutation = generator.permutation(train_rows)
        epoch_losses: list[float] = []
        for offset in range(0, train_rows, int(training["criteo_batch_size"])):
            indexes = permutation[offset : offset + int(training["criteo_batch_size"])]
            batch = torch.from_numpy(np.asarray(values[indexes], dtype="float32")).cuda()
            targets = (
                torch.from_numpy(np.asarray(labels[indexes], dtype="float32")).cuda().unsqueeze(-1)
            )
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(batch), targets)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        losses.append(sum(epoch_losses) / len(epoch_losses))
    model.eval()
    validation = np.asarray(values[train_rows : train_rows + validation_rows], dtype="float32")
    targets = np.asarray(labels[train_rows : train_rows + validation_rows], dtype="int64")
    with torch.inference_mode():
        predicted = (model(torch.from_numpy(validation).cuda()).squeeze(-1) >= 0.5).cpu().numpy()
    summary = {
        "seed": seed,
        "architecture": "dlrm-lite-dense13-32-16-cat26x4-offset-table-top32-fp32",
        "epochs": int(training["criteo_epochs"]),
        "train_rows": train_rows,
        "validation_rows": validation_rows,
        "final_loss": losses[-1],
        "validation_accuracy_diagnostic_only": float((predicted == targets).mean()),
        "model_accuracy_claim": False,
        "training_quality_claim": False,
    }
    return model, {"summary": summary, "checkpoint": {"state_dict": model.state_dict(), **summary}}


def _freeze_model(
    torch: Any,
    model: Any,
    output_path: Path,
    *,
    feature_count: int,
    source_artifact: Mapping[str, Any],
    repeatability_input: list[float] | None = None,
    expected_device_name: str | None = None,
) -> dict[str, Any]:
    model.eval()
    example = torch.zeros((2, feature_count), dtype=torch.float32, device="cuda")
    with torch.inference_mode():
        traced = torch.jit.trace(model, example, strict=True)
    torch.jit.save(traced.cpu(), output_path)
    runtime_model = model.cuda().eval()
    runtime_tensors = [*runtime_model.parameters(), *runtime_model.buffers()]
    if not runtime_tensors or any(tensor.device.type != "cuda" for tensor in runtime_tensors):
        raise X1ArtifactError("x1_artifact_runtime_model_cuda")
    frozen = {
        "feature_count": feature_count,
        "dtype": "float32",
        "backend": "pytorch",
        "model_version": "1",
        "artifact_path": output_path.name,
        "artifact_sha256": sha256_file(output_path),
        "source_artifact": dict(source_artifact),
        "runtime_model": runtime_model,
    }
    if repeatability_input is not None:
        if expected_device_name is None:
            raise X1ArtifactError("x1_artifact_cuda_repeatability_device")
        frozen["cuda_repeatability"] = _run_frozen_cuda_repeatability(
            output_path,
            repeatability_input,
            expected_device_name=expected_device_name,
        )
    return frozen


def _run_frozen_cuda_repeatability(
    artifact_path: Path,
    input_values: list[float],
    *,
    expected_device_name: str,
) -> dict[str, Any]:
    if len(input_values) != 39 or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in input_values
    ):
        raise X1ArtifactError("x1_artifact_cuda_repeatability_input")
    child = """
import json
import math
import sys
import torch

artifact_path = sys.argv[1]
request_count = int(sys.argv[2])
values = json.loads(sys.stdin.read())
if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
    raise RuntimeError("cuda_device_contract")
torch.cuda.set_device(0)
model = torch.jit.load(artifact_path, map_location="cuda:0").eval()
value = torch.tensor([values], dtype=torch.float32, device="cuda:0")
outputs = []
with torch.inference_mode():
    for _ in range(request_count):
        output = float(model(value).detach().reshape(-1)[0].cpu())
        torch.cuda.synchronize(0)
        if not math.isfinite(output):
            raise RuntimeError("nonfinite_output")
        outputs.append(output)
print(json.dumps({
    "device_count": torch.cuda.device_count(),
    "device_index": torch.cuda.current_device(),
    "device_name": torch.cuda.get_device_name(0),
    "device_type": "cuda",
    "output_values": outputs,
    "request_count": request_count,
}, sort_keys=True, separators=(",", ":")))
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                child,
                str(artifact_path.resolve()),
                str(CUDA_REPEATABILITY_REQUESTS),
            ],
            input=json.dumps(input_values, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=CUDA_REPEATABILITY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise X1ArtifactError("x1_artifact_cuda_repeatability_timeout") from exc
    if completed.returncode != 0:
        raise X1ArtifactError("x1_artifact_cuda_repeatability_process")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise X1ArtifactError("x1_artifact_cuda_repeatability_output") from exc
    if not isinstance(payload, Mapping):
        raise X1ArtifactError("x1_artifact_cuda_repeatability_output")
    record = {
        "schema_version": "evm.s8_v4.x1_cuda_repeatability.v1",
        "request_count": payload.get("request_count"),
        "timeout_seconds": CUDA_REPEATABILITY_TIMEOUT_SECONDS,
        "device_type": payload.get("device_type"),
        "device_count": payload.get("device_count"),
        "device_index": payload.get("device_index"),
        "device_name": payload.get("device_name"),
        "cpu_fallback_detected": False,
        "input_sha256": canonical_sha256(input_values),
        "output_values": payload.get("output_values"),
    }
    try:
        record["output_sequence_sha256"] = canonical_sha256(record["output_values"])
    except (TypeError, ValueError) as exc:
        raise X1ArtifactError("x1_artifact_cuda_repeatability_contract") from exc
    _validate_cuda_repeatability_record(record, expected_device_name=expected_device_name)
    return record


def _validate_cuda_repeatability_record(
    record: Any,
    *,
    expected_device_name: str,
) -> None:
    expected_keys = {
        "schema_version",
        "request_count",
        "timeout_seconds",
        "device_type",
        "device_count",
        "device_index",
        "device_name",
        "cpu_fallback_detected",
        "input_sha256",
        "output_values",
        "output_sequence_sha256",
    }
    if not isinstance(record, Mapping) or set(record) != expected_keys:
        raise X1ArtifactError("x1_artifact_cuda_repeatability_schema")
    outputs = record["output_values"]
    if (
        record["schema_version"] != "evm.s8_v4.x1_cuda_repeatability.v1"
        or type(record["request_count"]) is not int
        or record["request_count"] != CUDA_REPEATABILITY_REQUESTS
        or type(record["timeout_seconds"]) is not int
        or record["timeout_seconds"] != CUDA_REPEATABILITY_TIMEOUT_SECONDS
        or record["device_type"] != "cuda"
        or type(record["device_count"]) is not int
        or record["device_count"] != 1
        or type(record["device_index"]) is not int
        or record["device_index"] != 0
        or record["device_name"] != expected_device_name
        or record["cpu_fallback_detected"] is not False
        or not isinstance(record["input_sha256"], str)
        or len(record["input_sha256"]) != 64
        or not isinstance(outputs, list)
        or len(outputs) != CUDA_REPEATABILITY_REQUESTS
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in outputs
        )
        or not all(float(value) == float(outputs[0]) for value in outputs[1:])
        or record["output_sequence_sha256"] != canonical_sha256(outputs)
    ):
        raise X1ArtifactError("x1_artifact_cuda_repeatability_contract")


def _source_manifest(
    contract: X1Contract, source_revision: str, source_tree: str
) -> dict[str, Any]:
    source = dict(contract.payload["source"])
    entries = []
    for key, relative in sorted(source.items()):
        if key == "contract_base_revision":
            continue
        root = contract.source_root if key.endswith("_config") else contract.data_root
        path = (root / relative).resolve()
        entries.append(
            {
                "identity": key,
                "path": str(relative).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "revision": source_revision,
        "tree": source_tree,
        "entries": entries,
        "aggregate_sha256": canonical_sha256(entries),
    }


def _entries(root: Path, *, excluded: set[Path] | None = None) -> list[dict[str, Any]]:
    excluded = excluded or set()
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in _regular_files(root)
        if path not in excluded
    ]


def _regular_files(root: Path) -> list[Path]:
    resolved_root = root.resolve()
    files: list[Path] = []

    def visit(directory: Path) -> None:
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(entry.path)
            observed = entry.stat(follow_symlinks=False)
            attributes = int(getattr(observed, "st_file_attributes", 0))
            if entry.is_symlink() or attributes & 0x400:
                raise X1ArtifactError(f"x1_artifact_reparse:{path.name}")
            if stat.S_ISDIR(observed.st_mode):
                visit(path)
            elif stat.S_ISREG(observed.st_mode):
                files.append(path.resolve())
            else:
                raise X1ArtifactError(f"x1_artifact_nonregular:{path.name}")

    visit(resolved_root)
    return sorted(files, key=lambda path: path.relative_to(resolved_root).as_posix())


def _contained(root: Path, relative: str) -> Path:
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise X1ArtifactError(f"x1_artifact_path_escape:{relative}") from exc
    if not path.is_file():
        raise X1ArtifactError(f"x1_artifact_file_missing:{relative}")
    return path


def _contained_directory(root: Path, relative: str) -> Path:
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise X1ArtifactError(f"x1_artifact_path_escape:{relative}") from exc
    if not path.is_dir():
        raise X1ArtifactError(f"x1_artifact_directory_missing:{relative}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise X1ArtifactError(f"x1_json_object:{path.name}")
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
        newline="\n",
    )

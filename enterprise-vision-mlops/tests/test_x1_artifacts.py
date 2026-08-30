from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import json

from evm.scale_validation.x1_artifacts import (
    CUDA_REPEATABILITY_REQUESTS,
    CUDA_REPEATABILITY_TIMEOUT_SECONDS,
    MODEL_IDS,
    PROFILE_IDS,
    X1ArtifactError,
    _build_dlrm_lite,
    _build_gaussian_nb,
    _entries,
    _freeze_model,
    _load_artifact_dependencies,
    _preprocess_criteo,
    _run_frozen_cuda_repeatability,
    _source_manifest,
    _write_json,
    prepare_x1_artifacts,
    render_triton_config,
    validate_x1_artifacts,
)
from evm.scale_validation.x1_contract import X1Contract, canonical_sha256, sha256_file

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")


def test_x1_triton_config_profiles_are_exact_and_gpu_only() -> None:
    disabled = render_triton_config(
        model_id="higgs_logistic_regression", feature_count=28, profile_id="disabled"
    )
    assert b"max_batch_size: 0\n" in disabled
    assert b"dynamic_batching" not in disabled
    assert b"kind: KIND_GPU" in disabled
    assert b"versions: [ 1 ]" in disabled
    enabled = render_triton_config(
        model_id="criteo_dlrm_lite", feature_count=39, profile_id="enabled-4-8-2ms"
    )
    assert b"max_batch_size: 16\n" in enabled
    assert b"preferred_batch_size: [ 4, 8 ]" in enabled
    assert b"max_queue_delay_microseconds: 2000" in enabled
    assert enabled.endswith(b"\n")
    assert len(PROFILE_IDS) == 3


def test_x1_artifact_dependencies_load_in_clean_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from evm.scale_validation.x1_artifacts import "
                "_load_artifact_dependencies; "
                "np, pq, torch = _load_artifact_dependencies(); "
                "print(torch.__version__, np.__version__, pq.__name__)"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_x1_probabilistic_and_dlrm_artifacts_support_disabled_and_batched_shapes() -> None:
    _np, _pq, torch = _load_artifact_dependencies()
    gaussian = _build_gaussian_nb(
        torch,
        {
            "model": {
                "theta": [[0.0] * 28, [1.0] * 28],
                "variance": [[1.0] * 28, [1.0] * 28],
                "class_log_prior": [-0.6931471805599453, -0.6931471805599453],
            }
        },
    ).eval()
    dlrm = _build_dlrm_lite(torch, vocab_size=32, embedding_dim=2).eval()
    for model, feature_count in ((gaussian, 28), (dlrm, 39)):
        traced = torch.jit.trace(model, torch.zeros((2, feature_count)), strict=True)
        disabled = traced(torch.zeros(feature_count))
        batched = traced(torch.zeros((2, feature_count)))
        assert disabled.numel() == 1
        assert batched.shape == (2, 1)


def test_x1_dlrm_uses_one_field_offset_embedding_table_and_repeats() -> None:
    _np, _pq, torch = _load_artifact_dependencies()
    model = _build_dlrm_lite(torch, vocab_size=32, embedding_dim=2).eval()
    embeddings = [module for module in model.modules() if isinstance(module, torch.nn.Embedding)]
    assert len(embeddings) == 1
    assert embeddings[0].num_embeddings == 32 * 26
    assert embeddings[0].embedding_dim == 2
    assert model.categorical_offsets.tolist() == [index * 32 for index in range(26)]
    traced = torch.jit.trace(model, torch.zeros((2, 39)), strict=True)
    outputs = [traced(torch.zeros((1, 39))) for _ in range(64)]
    assert all(output.shape == (1, 1) for output in outputs)
    assert all(torch.equal(output, outputs[0]) for output in outputs[1:])


def test_x1_freeze_restores_shared_runtime_model_to_cuda(tmp_path: Path) -> None:
    class Device:
        def __init__(self, kind: str) -> None:
            self.type = kind

    class Tensor:
        def __init__(self, kind: str) -> None:
            self.device = Device(kind)

    class Model:
        def __init__(self) -> None:
            self.weight = Tensor("cuda")

        def eval(self) -> Model:
            return self

        def cuda(self) -> Model:
            self.weight.device = Device("cuda")
            return self

        def parameters(self) -> list[Tensor]:
            return [self.weight]

        def buffers(self) -> list[Tensor]:
            return []

    class Traced:
        def __init__(self, model: Model) -> None:
            self.model = model

        def cpu(self) -> Traced:
            self.model.weight.device = Device("cpu")
            return self

    class InferenceMode:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    class Jit:
        @staticmethod
        def trace(model: Model, _example: object, *, strict: bool) -> Traced:
            assert strict is True
            return Traced(model)

        @staticmethod
        def save(_traced: Traced, path: Path) -> None:
            path.write_bytes(b"frozen-model")

    class Torch:
        float32 = "float32"
        jit = Jit()

        @staticmethod
        def zeros(_shape: tuple[int, int], *, dtype: str, device: str) -> object:
            assert dtype == "float32"
            assert device == "cuda"
            return object()

        @staticmethod
        def inference_mode() -> InferenceMode:
            return InferenceMode()

    model = Model()
    frozen = _freeze_model(
        Torch(),
        model,
        tmp_path / "model.pt",
        feature_count=28,
        source_artifact={},
    )

    assert frozen["runtime_model"] is model
    assert model.weight.device.type == "cuda"


def test_x1_frozen_cuda_repeatability_is_bounded_and_source_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"frozen")
    values = [float(index) for index in range(39)]
    outputs = [0.25] * CUDA_REPEATABILITY_REQUESTS

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == CUDA_REPEATABILITY_TIMEOUT_SECONDS
        assert json.loads(str(kwargs["input"])) == values
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "device_count": 1,
                    "device_index": 0,
                    "device_name": "NVIDIA GeForce RTX 4080 SUPER",
                    "device_type": "cuda",
                    "output_values": outputs,
                    "request_count": CUDA_REPEATABILITY_REQUESTS,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("evm.scale_validation.x1_artifacts.subprocess.run", run)
    result = _run_frozen_cuda_repeatability(
        artifact,
        values,
        expected_device_name="NVIDIA GeForce RTX 4080 SUPER",
    )

    assert result["request_count"] == 64
    assert result["input_sha256"] == canonical_sha256(values)
    assert result["output_sequence_sha256"] == canonical_sha256(outputs)
    assert result["cpu_fallback_detected"] is False


def test_x1_frozen_cuda_repeatability_timeout_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"frozen")

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("unit", CUDA_REPEATABILITY_TIMEOUT_SECONDS)

    monkeypatch.setattr("evm.scale_validation.x1_artifacts.subprocess.run", timeout)
    with pytest.raises(X1ArtifactError, match="x1_artifact_cuda_repeatability_timeout"):
        _run_frozen_cuda_repeatability(
            artifact,
            [0.0] * 39,
            expected_device_name="NVIDIA GeForce RTX 4080 SUPER",
        )


def test_x1_frozen_cuda_repeatability_nonfinite_output_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"frozen")

    def run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "device_count": 1,
                    "device_index": 0,
                    "device_name": "NVIDIA GeForce RTX 4080 SUPER",
                    "device_type": "cuda",
                    "output_values": [0.5] * 63 + [float("nan")],
                    "request_count": CUDA_REPEATABILITY_REQUESTS,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("evm.scale_validation.x1_artifacts.subprocess.run", run)
    with pytest.raises(X1ArtifactError, match="x1_artifact_cuda_repeatability_contract"):
        _run_frozen_cuda_repeatability(
            artifact,
            [0.0] * 39,
            expected_device_name="NVIDIA GeForce RTX 4080 SUPER",
        )


def test_x1_criteo_preprocessing_uses_governed_shard_schema() -> None:
    np, pq, _torch = _load_artifact_dependencies()
    shard = DATA_ROOT / "datasets/criteo-click-logs/s5/governed/shard-000.parquet"
    table = pq.read_table(shard).slice(0, 8)

    values, labels, preprocessing = _preprocess_criteo(
        np,
        table,
        vocab_size=4096,
        train_rows=4,
    )
    assert values.shape == (8, 39)
    assert labels.shape == (8,)
    assert len(preprocessing["dense"]["mean"]) == 13
    assert preprocessing["categorical"]["feature_count"] == 26

    mutated_names = list(table.column_names)
    mutated_names[mutated_names.index("int_feature_1")] = "integer_feature_1"
    mutated = table.rename_columns(mutated_names)
    with pytest.raises(X1ArtifactError, match="x1_criteo_schema"):
        _preprocess_criteo(np, mutated, vocab_size=4096, train_rows=4)


@pytest.mark.parametrize(
    "model_id,feature_count,profile_id",
    [
        ("unknown", 28, "disabled"),
        ("higgs_tiny_mlp", 29, "disabled"),
        ("higgs_tiny_mlp", 28, "unknown"),
    ],
)
def test_x1_triton_config_rejects_unfrozen_identity(
    model_id: str, feature_count: int, profile_id: str
) -> None:
    with pytest.raises(X1ArtifactError):
        render_triton_config(
            model_id=model_id,
            feature_count=feature_count,
            profile_id=profile_id,
        )


def test_x1_artifact_preparation_requires_exact_x1_training_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def stop_after_lease(**kwargs: object) -> None:
        observed.update(kwargs)
        raise X1ArtifactError("unit_stop_after_lease")

    monkeypatch.setattr(
        "evm.scale_validation.x1_artifacts.assert_scale_validation_gpu_lease_owner",
        stop_after_lease,
    )
    contract = X1Contract.from_path(
        ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
        source_root=ROOT,
        data_root=DATA_ROOT,
    )
    with pytest.raises(X1ArtifactError, match="unit_stop_after_lease"):
        prepare_x1_artifacts(
            contract,
            output_root=tmp_path / "fresh",
            source_revision="a" * 40,
            source_tree="b" * 40,
            lease_run_id="s8-v4-x1-training-unit",
            lease_id="lease-unit",
            fencing_token="fence-unit",
        )
    assert observed["scenario_id"] == "X1"
    assert observed["model_family"] == "heterogeneous"
    assert observed["purpose"] == "scale_validation_training"


def artifact_fixture(tmp_path: Path) -> tuple[Path, X1Contract]:
    loaded = X1Contract.from_path(
        ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
        source_root=ROOT,
        data_root=DATA_ROOT,
    )
    source = tmp_path / "source-artifacts"
    source.mkdir()
    models: dict[str, dict[str, object]] = {}
    feature_counts = dict(zip(MODEL_IDS, (28, 28, 28, 39), strict=True))
    for model_id in MODEL_IDS:
        artifact = source / f"{model_id}.pt"
        artifact.write_bytes(f"artifact:{model_id}".encode())
        models[model_id] = {
            "feature_count": feature_counts[model_id],
            "dtype": "float32",
            "backend": "pytorch",
            "model_version": "1",
            "artifact_path": artifact.name,
            "artifact_sha256": sha256_file(artifact),
            "source_artifact": {},
        }
        if model_id == MODEL_IDS[3]:
            outputs = [0.5] * CUDA_REPEATABILITY_REQUESTS
            models[model_id]["cuda_repeatability"] = {
                "schema_version": "evm.s8_v4.x1_cuda_repeatability.v1",
                "request_count": CUDA_REPEATABILITY_REQUESTS,
                "timeout_seconds": CUDA_REPEATABILITY_TIMEOUT_SECONDS,
                "device_type": "cuda",
                "device_count": 1,
                "device_index": 0,
                "device_name": "NVIDIA GeForce RTX 4080 SUPER",
                "cpu_fallback_detected": False,
                "input_sha256": canonical_sha256([0.0] * 39),
                "output_values": outputs,
                "output_sequence_sha256": canonical_sha256(outputs),
            }
    (source / "higgs_tiny_mlp-training.pt").write_bytes(b"tiny-training")
    (source / "criteo_dlrm_lite-training.pt").write_bytes(b"dlrm-training")
    repositories: dict[str, object] = {}
    for profile_id in PROFILE_IDS:
        profile = tmp_path / "model-repositories" / profile_id
        for model_id in MODEL_IDS:
            version = profile / model_id / "1"
            version.mkdir(parents=True)
            (profile / model_id / "config.pbtxt").write_bytes(
                render_triton_config(
                    model_id=model_id,
                    feature_count=feature_counts[model_id],
                    profile_id=profile_id,
                )
            )
            (version / "model.pt").write_bytes((source / f"{model_id}.pt").read_bytes())
        entries = _entries(profile)
        repositories[profile_id] = {
            "relative_root": f"model-repositories/{profile_id}",
            "entries": entries,
            "aggregate_sha256": canonical_sha256(entries),
        }
    oracles: dict[str, object] = {}
    for model_id in MODEL_IDS:
        path = tmp_path / f"oracle-{model_id}.json"
        rows = 64
        payload = {
            "schema_version": "evm.s8_v4.x1_correctness_oracle.v1",
            "model_id": model_id,
            "input": [[0.0] * feature_counts[model_id] for _ in range(rows)],
            "output": [[0.5] for _ in range(rows)],
            "absolute_tolerance": 1e-5,
            "relative_tolerance": 1e-5,
        }
        _write_json(path, payload)
        oracles[model_id] = {"path": path.name, "sha256": sha256_file(path), "rows": rows}
    manifest_path = tmp_path / "x1-artifact-manifest.json"
    manifest: dict[str, object] = {
        "schema_version": "evm.s8_v4.x1_artifact_manifest.v1",
        "source_identity": {"revision": "a" * 40, "tree": "b" * 40},
        "contract_sha256": loaded.sha256,
        "preparation_lease": {
            "run_id": "s8-v4-x1-training-unit",
            "lease_id": "lease-unit",
            "fencing_token_sha256": __import__("hashlib").sha256(b"fence-unit").hexdigest(),
            "purpose": "scale_validation_training",
            "scenario_id": "X1",
            "model_family": "heterogeneous",
            "source_revision": "a" * 40,
        },
        "source_manifest": _source_manifest(loaded, "a" * 40, "b" * 40),
        "models": models,
        "repositories": repositories,
        "correctness_oracles": oracles,
        "framework": {"torch": "unit", "cuda_runtime": "unit", "cudnn": "unit"},
        "claim_boundary": loaded.payload["claim"]["boundary"],
    }
    manifest["artifact_inventory"] = _entries(tmp_path)
    manifest["artifact_inventory_aggregate_sha256"] = canonical_sha256(
        manifest["artifact_inventory"]
    )
    manifest["artifact_identity_sha256"] = canonical_sha256(manifest)
    _write_json(manifest_path, manifest)
    return manifest_path, loaded


def validate_fixture(path: Path, loaded: X1Contract) -> dict[str, object]:
    return validate_x1_artifacts(
        loaded,
        manifest_path=path,
        source_revision="a" * 40,
        source_tree="b" * 40,
        lease_run_id="s8-v4-x1-training-unit",
        lease_id="lease-unit",
        fencing_token="fence-unit",
    )


def test_x1_artifact_validator_reopens_complete_tree_and_hashes(tmp_path: Path) -> None:
    path, loaded = artifact_fixture(tmp_path)
    assert validate_fixture(path, loaded)["artifact_inventory_aggregate_sha256"]


def test_x1_artifact_validator_rejects_extra_or_coherently_rehashed_config(
    tmp_path: Path,
) -> None:
    path, loaded = artifact_fixture(tmp_path)
    (tmp_path / "unexpected.bin").write_bytes(b"extra")
    with pytest.raises(X1ArtifactError, match="x1_artifact_inventory"):
        validate_fixture(path, loaded)
    (tmp_path / "unexpected.bin").unlink()

    manifest = json.loads(path.read_bytes())
    config = (
        tmp_path / "model-repositories" / "disabled" / "higgs_logistic_regression" / "config.pbtxt"
    )
    config.write_bytes(config.read_bytes() + b"response_cache { enable: true }\n")
    entries = _entries(tmp_path / "model-repositories" / "disabled")
    manifest["repositories"]["disabled"]["entries"] = entries
    manifest["repositories"]["disabled"]["aggregate_sha256"] = canonical_sha256(entries)
    manifest["artifact_inventory"] = _entries(tmp_path, excluded={path.resolve()})
    manifest["artifact_inventory_aggregate_sha256"] = canonical_sha256(
        manifest["artifact_inventory"]
    )
    manifest.pop("artifact_identity_sha256")
    manifest["artifact_identity_sha256"] = canonical_sha256(manifest)
    _write_json(path, manifest)
    with pytest.raises(X1ArtifactError, match="x1_artifact_config_bytes"):
        validate_fixture(path, loaded)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_count", 63),
        ("request_count", True),
        ("device_name", "wrong-device"),
        ("cpu_fallback_detected", True),
        ("output_values", [0.5] * 63),
        ("output_values", [0.5] * 63 + [0.25]),
    ],
)
def test_x1_artifact_validator_rejects_cuda_repeatability_mutations(
    tmp_path: Path, field: str, value: object
) -> None:
    path, loaded = artifact_fixture(tmp_path)
    manifest = json.loads(path.read_bytes())
    repeatability = manifest["models"][MODEL_IDS[3]]["cuda_repeatability"]
    repeatability[field] = value
    if field == "output_values":
        repeatability["output_sequence_sha256"] = canonical_sha256(value)
    manifest.pop("artifact_identity_sha256")
    manifest["artifact_identity_sha256"] = canonical_sha256(manifest)
    _write_json(path, manifest)

    with pytest.raises(X1ArtifactError, match="x1_artifact_cuda_repeatability_contract"):
        validate_fixture(path, loaded)

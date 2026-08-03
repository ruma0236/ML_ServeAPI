from __future__ import annotations

import json
from pathlib import Path

from evm.core.data_intake import build_manifest_records, sample_id_for
from evm.core.dataset import shard_index_identity_digest, stable_record_digest
from evm.pipelines.dataset_shards.run import run as run_dataset_shards


def _record(*, image_uri: str, image_path: str) -> dict[str, object]:
    return {
        "id": "visa_000_1234",
        "dataset_id": "visa",
        "sample_id": "visa_000_1234",
        "image_uri": image_uri,
        "image_path": image_path,
        "label": "normal",
        "width": 1284,
        "height": 1168,
        "content_sha256": "a" * 64,
        "source_uri": "https://registry.opendata.aws/visa/",
        "metadata": {"relative_path": "candle/Data/Images/Normal/000.JPG"},
    }


def test_dataset_digest_does_not_depend_on_runtime_mount_path() -> None:
    host_record = _record(
        image_uri=(
            "file:///F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "data/raw/industrial/visa/candle/Data/Images/Normal/000.JPG"
        ),
        image_path=(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "data/raw/industrial/visa/candle/Data/Images/Normal/000.JPG"
        ),
    )
    container_record = _record(
        image_uri=(
            "file:///mnt/evm-data/data/raw/industrial/visa/"
            "candle/Data/Images/Normal/000.JPG"
        ),
        image_path=(
            "/mnt/evm-data/data/raw/industrial/visa/"
            "candle/Data/Images/Normal/000.JPG"
        ),
    )

    assert stable_record_digest([host_record]) == stable_record_digest([container_record])


def test_manifest_builder_uses_mapped_runtime_root_for_relative_identity(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime" / "visa"
    image_path = runtime_root / "candle" / "Data" / "Images" / "Normal" / "000.JPG"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"not-a-real-jpeg")
    dataset = {
        "id": "visa",
        "raw_root": "F:/canonical/data/raw/industrial/visa",
        "source_url": "https://registry.opendata.aws/visa/",
        "license_id": "CC-BY-4.0",
    }

    records = build_manifest_records(
        dataset,
        [image_path],
        [],
        dataset_version="visa-open-data-canonical",
        allowed_extensions={".jpg"},
        runtime_root=runtime_root,
    )

    assert len(records) == 1
    assert records[0]["sample_id"] == sample_id_for(
        "visa",
        Path("candle/Data/Images/Normal/000.JPG"),
    )
    assert records[0]["class_name"] == "candle"
    assert records[0]["metadata"]["relative_path"] == (
        "candle/Data/Images/Normal/000.JPG"
    )


def test_shard_identity_ignores_runtime_paths_and_trace_metadata() -> None:
    common = {
        "schema_version": "evm.dataset_shards.v1",
        "records_per_shard": 512,
        "record_count": 2,
        "shard_count": 1,
        "split_counts": {"train": 2},
        "label_counts": {"normal": 2},
        "label_type_counts": {"normal": 2},
    }
    host = {
        **common,
        "input_manifest": "F:/data/quality.jsonl",
        "output_dir": "F:/data/shards",
        "trace": {"pipeline_run_id": "host-run"},
        "shards": [
            {
                "shard_id": "train-0000",
                "split": "train",
                "path": "F:/data/shards/train_shard_0000.jsonl",
                "record_count": 2,
                "first_sample_id": "sample-1",
                "last_sample_id": "sample-2",
            }
        ],
    }
    container = {
        **common,
        "input_manifest": "/mnt/evm-data/data/quality.jsonl",
        "output_dir": "/mnt/evm-data/data/shards",
        "trace": {"pipeline_run_id": "container-run"},
        "shards": [
            {
                "shard_id": "train-0000",
                "split": "train",
                "path": "/mnt/evm-data/data/shards/train_shard_0000.jsonl",
                "record_count": 2,
                "first_sample_id": "sample-1",
                "last_sample_id": "sample-2",
            }
        ],
    }

    assert shard_index_identity_digest(host) == shard_index_identity_digest(container)


def test_dataset_shard_index_records_split_policy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manifest = tmp_path / "quality.jsonl"
    manifest.write_text(
        "".join(
            json.dumps({"sample_id": f"sample-{index}", "label": "normal"}) + "\n"
            for index in range(10)
        ),
        encoding="utf-8",
    )
    output = tmp_path / "shards"
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                "[project]",
                'name = "tmp"',
                "",
                "[paths]",
                f'artifacts_root = "{(tmp_path / "artifacts").as_posix()}"',
                f'reports_root = "{(tmp_path / "reports").as_posix()}"',
                "",
                "[pipelines.dataset_shards]",
                f'input_manifest = "{manifest.as_posix()}"',
                f'output_dir = "{output.as_posix()}"',
                f'index_path = "{(output / "shard_index.json").as_posix()}"',
                "records_per_shard = 4",
                "split_seed = 20260706",
                "",
                "[pipelines.dataset_shards.split_ratios]",
                "train = 0.6",
                "validation = 0.2",
                "test = 0.2",
            ]
        ),
        encoding="utf-8",
    )

    result = run_dataset_shards(str(config))

    assert result["split_seed"] == 20260706
    assert result["split_ratios"] == {"train": 0.6, "validation": 0.2, "test": 0.2}
    assert all(len(shard["sha256"]) == 64 for shard in result["shards"])
    assert result["identity_sha256"] == shard_index_identity_digest(result)
    assert b"\r\n" not in (output / "shard_index.json").read_bytes()
    for shard in result["shards"]:
        assert b"\r\n" not in Path(str(shard["path"])).read_bytes()


def test_dataset_shards_preserves_canonical_index_for_same_identity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manifest = tmp_path / "quality.jsonl"
    manifest.write_text(
        "".join(
            json.dumps({"sample_id": f"sample-{index}", "label": "normal"})
            + "\n"
            for index in range(10)
        ),
        encoding="utf-8",
    )
    output = tmp_path / "shards"
    index_path = output / "shard_index.json"
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                "[project]",
                'name = "tmp"',
                "",
                "[paths]",
                f'artifacts_root = "{(tmp_path / "artifacts").as_posix()}"',
                f'reports_root = "{(tmp_path / "reports").as_posix()}"',
                "",
                "[pipelines.dataset_shards]",
                f'input_manifest = "{manifest.as_posix()}"',
                f'output_dir = "{output.as_posix()}"',
                f'index_path = "{index_path.as_posix()}"',
                "records_per_shard = 4",
                "split_seed = 20260706",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("EVM_TRACE_ID", "trace-first")
    first = run_dataset_shards(str(config))
    canonical = index_path.read_bytes()
    monkeypatch.setenv("EVM_TRACE_ID", "trace-second")
    second = run_dataset_shards(str(config))

    assert first["identity_sha256"] == second["identity_sha256"]
    assert first["trace"]["trace_id"] == "trace-first"
    assert second["trace"]["trace_id"] == "trace-second"
    assert index_path.read_bytes() == canonical

from __future__ import annotations

from pathlib import Path

from evm.core.data_intake import build_manifest_records, sample_id_for
from evm.core.dataset import shard_index_identity_digest, stable_record_digest


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

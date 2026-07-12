from __future__ import annotations

from pathlib import Path

from evm.core.data_intake import build_manifest_records, sample_id_for
from evm.core.dataset import stable_record_digest


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

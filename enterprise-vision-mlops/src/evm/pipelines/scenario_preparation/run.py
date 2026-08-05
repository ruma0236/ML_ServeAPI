from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class ScenarioPreparationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_scienceqa_adaptation_view(
    source_manifest: Path,
    output_root: Path,
    *,
    seed: int = 20260805,
    train_count: int = 32,
    validation_count: int = 8,
    test_count: int = 8,
) -> dict[str, Any]:
    records = read_jsonl(source_manifest)
    total = train_count + validation_count + test_count
    if total <= 0 or len(records) < total:
        raise ScenarioPreparationError("scienceqa_adaptation_view_insufficient_records")
    ranked = sorted(
        records,
        key=lambda item: hashlib.sha256(
            f"{seed}:{item.get('sample_id', '')}".encode("utf-8")
        ).hexdigest(),
    )[:total]
    boundaries = (train_count, train_count + validation_count)
    prepared: list[dict[str, Any]] = []
    for index, source in enumerate(ranked):
        image_path = file_uri_path(str(source.get("image_uri") or ""))
        expected_image_sha = str(source.get("image_sha256") or "")
        if not image_path.is_file() or file_sha256(image_path) != expected_image_sha:
            raise ScenarioPreparationError(
                f"scienceqa_image_identity_mismatch:{source.get('sample_id', '')}"
            )
        split = "train" if index < boundaries[0] else "validation" if index < boundaries[1] else "test"
        prepared.append({**source, "source_split": source.get("split"), "split": split})
    return write_data_view(
        source_manifest,
        output_root,
        prepared,
        recipe_id="scienceqa_local_adaptation_view_v1",
        seed=seed,
        claim_boundary=(
            "Official test-derived records are repartitioned only for local pipeline proof; "
            "results are not a ScienceQA benchmark."
        ),
    )


def build_dolly_approved_view(
    source_manifest: Path,
    output_root: Path,
    *,
    approver: str,
    reason: str,
    seed: int = 20260805,
    train_count: int = 256,
    validation_count: int = 32,
    test_count: int = 32,
) -> dict[str, Any]:
    if len(approver.strip()) < 3 or len(reason.strip()) < 12:
        raise ScenarioPreparationError("dolly_review_approval_incomplete")
    records = read_jsonl(source_manifest)
    flagged = [item for item in records if item.get("pii_flags")]
    clean = [item for item in records if not item.get("pii_flags")]
    requested = {
        "train": train_count,
        "validation": validation_count,
        "test": test_count,
    }
    prepared: list[dict[str, Any]] = []
    for split, count in requested.items():
        candidates = [item for item in clean if item.get("split") == split]
        if len(candidates) < count:
            raise ScenarioPreparationError(f"dolly_clean_split_insufficient:{split}")
        candidates.sort(
            key=lambda item: hashlib.sha256(
                f"{seed}:{item.get('sample_id', '')}".encode("utf-8")
            ).hexdigest()
        )
        prepared.extend(candidates[:count])
    prepared.sort(key=lambda item: (str(item.get("split")), str(item.get("sample_id"))))
    view = write_data_view(
        source_manifest,
        output_root,
        prepared,
        recipe_id="dolly_pii_filtered_bounded_adaptation_view_v1",
        seed=seed,
        claim_boundary=(
            "Automated pattern flags are removed and the bounded clean view is approved for "
            "local adapter tuning; this is not a complete privacy audit."
        ),
        extra={"source_pii_flagged_records": len(flagged), "source_clean_records": len(clean)},
    )
    disposition = {
        "schema_version": "evm.scenario_quality_disposition.v1",
        "decision": "approved",
        "dataset_version": str(prepared[0]["dataset_version"]),
        "input_manifest_sha256": file_sha256(source_manifest),
        "output_manifest_uri": view["output_manifest_uri"],
        "output_manifest_sha256": view["output_manifest_sha256"],
        "output_split_manifest_uri": view["output_split_manifest_uri"],
        "output_identity_sha256": view["output_identity_sha256"],
        "excluded_pii_flagged_records": len(flagged),
        "approved_record_count": len(prepared),
        "approver": approver,
        "reason": reason,
        "approved_at": utc_now(),
        "claim_boundary": view["claim_boundary"],
    }
    disposition_path = output_root / "evidence" / "quality_disposition.json"
    if disposition_path.is_file():
        existing = json.loads(disposition_path.read_text(encoding="utf-8"))
        expected = {
            "decision": "approved",
            "dataset_version": str(prepared[0]["dataset_version"]),
            "input_manifest_sha256": file_sha256(source_manifest),
            "output_manifest_sha256": view["output_manifest_sha256"],
            "output_identity_sha256": view["output_identity_sha256"],
            "approver": approver,
        }
        if not isinstance(existing, dict) or any(existing.get(key) != value for key, value in expected.items()):
            raise ScenarioPreparationError("dolly_quality_disposition_immutable_conflict")
        return {**view, "quality_disposition_uri": str(disposition_path)}
    atomic_write_json(disposition_path, disposition)
    return {**view, "quality_disposition_uri": str(disposition_path)}


def write_data_view(
    source_manifest: Path,
    output_root: Path,
    records: list[dict[str, Any]],
    *,
    recipe_id: str,
    seed: int,
    claim_boundary: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not records:
        raise ScenarioPreparationError("scenario_data_view_empty")
    manifest_path = output_root / "processed" / "normalized_manifest.jsonl"
    split_path = output_root / "evidence" / "split_manifest.json"
    quality_path = output_root / "evidence" / "quality_report.json"
    lineage_path = output_root / "evidence" / "lineage.json"
    view_path = output_root / "evidence" / "data_view.json"
    expected_manifest_sha = hashlib.sha256(jsonl_bytes(records)).hexdigest()
    if view_path.is_file():
        existing = json.loads(view_path.read_text(encoding="utf-8"))
        expected = {
            "status": "pass",
            "input_manifest_sha256": file_sha256(source_manifest),
            "output_manifest_sha256": expected_manifest_sha,
            "record_count": len(records),
            "split_counts": dict(sorted(Counter(str(item["split"]) for item in records).items())),
            "recipe_id": recipe_id,
            "seed": seed,
        }
        if not isinstance(existing, dict) or any(existing.get(key) != value for key, value in expected.items()):
            raise ScenarioPreparationError("scenario_data_view_immutable_conflict")
        existing_manifest = Path(str(existing.get("output_manifest_uri") or ""))
        existing_split = Path(str(existing.get("output_split_manifest_uri") or ""))
        if (
            not existing_manifest.is_file()
            or file_sha256(existing_manifest) != expected_manifest_sha
            or not existing_split.is_file()
            or file_sha256(existing_split) != existing.get("output_split_manifest_sha256")
        ):
            raise ScenarioPreparationError("scenario_data_view_existing_evidence_mismatch")
        return {
            **existing,
            "data_view_uri": str(view_path),
            "quality_report_uri": str(quality_path),
        }
    write_jsonl(manifest_path, records)
    manifest_sha = file_sha256(manifest_path)
    identity_material = [
        {
            "sample_id": item["sample_id"],
            "split": item["split"],
            "content_sha256": item["content_sha256"],
        }
        for item in records
    ]
    identity_sha = payload_sha256(identity_material)
    source_dataset_version = str(records[0]["dataset_version"])
    split_counts = dict(sorted(Counter(str(item["split"]) for item in records).items()))
    atomic_write_json(
        split_path,
        {
            "schema_version": "evm.scenario_split_manifest.v1",
            "dataset_version": source_dataset_version,
            "identity_sha256": identity_sha,
            "manifest_sha256": manifest_sha,
            "input_manifest_sha256": file_sha256(source_manifest),
            "split_seed": seed,
            "split_counts": split_counts,
            "record_count": len(records),
            "immutable": True,
            "created_at": utc_now(),
        },
    )
    atomic_write_json(
        quality_path,
        {
            "schema_version": "evm.scenario_quality_report.v1",
            "status": "pass",
            "dataset_version": source_dataset_version,
            "records_in": len(records),
            "records_out": len(records),
            "pii_pattern_records": sum(bool(item.get("pii_flags")) for item in records),
            "split_counts": split_counts,
            "manifest_sha256": manifest_sha,
            "created_at": utc_now(),
        },
    )
    atomic_write_json(
        lineage_path,
        {
            "schema_version": "evm.scenario_data_view_lineage.v1",
            "source_manifest_uri": str(source_manifest),
            "source_manifest_sha256": file_sha256(source_manifest),
            "output_manifest_uri": str(manifest_path),
            "output_manifest_sha256": manifest_sha,
            "recipe_id": recipe_id,
            "seed": seed,
            "created_at": utc_now(),
        },
    )
    view = {
        "schema_version": "evm.scenario_data_view.v1",
        "status": "pass",
        "source_dataset_version": source_dataset_version,
        "input_manifest_uri": str(source_manifest),
        "input_manifest_sha256": file_sha256(source_manifest),
        "output_manifest_uri": str(manifest_path),
        "output_manifest_sha256": manifest_sha,
        "output_split_manifest_uri": str(split_path),
        "output_split_manifest_sha256": file_sha256(split_path),
        "output_identity_sha256": identity_sha,
        "record_count": len(records),
        "split_counts": split_counts,
        "recipe_id": recipe_id,
        "seed": seed,
        "claim_boundary": claim_boundary,
        "lineage_uri": str(lineage_path),
        "created_at": utc_now(),
        **(extra or {}),
    }
    atomic_write_json(view_path, view)
    return {**view, "data_view_uri": str(view_path), "quality_report_uri": str(quality_path)}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ScenarioPreparationError(f"source_manifest_missing:{path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ScenarioPreparationError("source_manifest_record_invalid")
                records.append(payload)
    return records


def file_uri_path(value: str) -> Path:
    normalized = value.replace("\\", "/")
    if normalized.lower().startswith("file:///"):
        normalized = normalized[8:]
    return Path(normalized)


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(jsonl_bytes(records))
    temporary.replace(path)


def jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
    ).encode("utf-8")


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build governed scenario adaptation views.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scienceqa = subparsers.add_parser("scienceqa")
    scienceqa.add_argument("--source-manifest", type=Path, required=True)
    scienceqa.add_argument("--output-root", type=Path, required=True)
    dolly = subparsers.add_parser("dolly")
    dolly.add_argument("--source-manifest", type=Path, required=True)
    dolly.add_argument("--output-root", type=Path, required=True)
    dolly.add_argument("--approver", required=True)
    dolly.add_argument("--reason", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "scienceqa":
        result = build_scienceqa_adaptation_view(args.source_manifest, args.output_root)
    else:
        result = build_dolly_approved_view(
            args.source_manifest,
            args.output_root,
            approver=args.approver,
            reason=args.reason,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

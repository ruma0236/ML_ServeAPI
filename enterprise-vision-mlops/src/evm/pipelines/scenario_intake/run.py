from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from evm.core.config import load_config, map_runtime_data_path
from evm.core.image_quality import read_image_dimensions


ALLOWED_SOURCE_HOSTS = {
    "raw.githubusercontent.com",
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cas-bridge.xethub.hf.co",
}
APPROVED_PARSERS = {"banking77_csv", "dolly_jsonl", "scienceqa_parquet"}
APPROVED_TRANSFORMS = {
    "normalize_unicode_nfc",
    "collapse_whitespace",
    "enforce_text_length",
    "deduplicate_content_prefer_holdout",
    "scan_pii_patterns",
    "extract_embedded_image",
    "validate_image_header",
}
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")


class ScenarioIntakeError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(config_path: str = "configs/scenarios/banking77-intent-classification.json") -> dict[str, object]:
    config = load_config(config_path)
    scenario = object_value(config, "scenario")
    dataset = object_value(config, "dataset")
    acquisition = object_value(config, "acquisition")
    preprocessing = object_value(config, "preprocessing")
    scenario_id = required_slug(scenario, "scenario_id")
    parser = str(acquisition.get("parser") or "")
    if parser not in APPROVED_PARSERS:
        raise ScenarioIntakeError(f"scenario_parser_not_approved:{parser}")
    if not bool(scenario.get("intake_supported")):
        raise ScenarioIntakeError("scenario_intake_not_supported")
    steps = validated_steps(preprocessing)
    output_root_value = required_text(dataset, "output_root")
    output_root = map_runtime_data_path(output_root_value)
    raw_root = output_root / "raw"
    processed_root = output_root / "processed"
    evidence_root = output_root / "evidence"
    state_path = evidence_root / "intake_state.json"
    started_at = utc_now()
    state: dict[str, Any] = {
        "schema_version": "evm.scenario_intake_state.v1",
        "scenario_id": scenario_id,
        "dataset_id": required_text(dataset, "dataset_id"),
        "dataset_version": required_text(dataset, "dataset_version"),
        "status": "running",
        "phase": "preflight",
        "progress": 0.0,
        "started_at": started_at,
        "updated_at": started_at,
        "finished_at": None,
        "records_processed": 0,
        "records_output": 0,
        "blockers": [],
    }
    write_state(state_path, state)
    try:
        sources = acquire_sources(acquisition, raw_root, state_path, state)
        state.update(phase="preprocessing", progress=0.58, updated_at=utc_now())
        write_state(state_path, state)
        parser_result = parse_dataset(
            parser,
            sources,
            config=config,
            output_root=output_root,
            output_root_value=output_root_value,
            steps=steps,
            state_path=state_path,
            state=state,
        )
        records = parser_result["records"]
        records, deduplication = apply_dataset_transforms(records, steps)
        parser_result.update(deduplication)
        if not records:
            raise ScenarioIntakeError("scenario_intake_zero_records")
        processed_root.mkdir(parents=True, exist_ok=True)
        evidence_root.mkdir(parents=True, exist_ok=True)
        manifest_path = processed_root / "normalized_manifest.jsonl"
        write_jsonl(manifest_path, records)
        manifest_sha256 = sha256_file(manifest_path)
        identity_payload = [
            {
                "sample_id": item["sample_id"],
                "split": item["split"],
                "content_sha256": item["content_sha256"],
            }
            for item in records
        ]
        identity_sha256 = canonical_sha256(identity_payload)
        split_policy = object_value(config, "split_policy")
        split_manifest = {
            "schema_version": "evm.scenario_split_manifest.v1",
            "scenario_id": scenario_id,
            "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["dataset_version"],
            "identity_sha256": identity_sha256,
            "manifest_sha256": manifest_sha256,
            "split_seed": int(split_policy.get("seed") or 20260714),
            "split_ratios": {
                name: float(split_policy.get(name) or 0.0)
                for name in ("train", "validation", "test")
            },
            "split_counts": dict(sorted(Counter(item["split"] for item in records).items())),
            "record_count": len(records),
            "immutable": True,
            "created_at": utc_now(),
        }
        split_path = evidence_root / "split_manifest.json"
        write_json(split_path, split_manifest)
        source_registry = {
            "schema_version": "evm.scenario_source_registry.v1",
            "scenario_id": scenario_id,
            "dataset": dataset,
            "sources": [source["evidence"] for source in sources],
            "recipe_id": preprocessing.get("recipe_id"),
            "recipe_version": preprocessing.get("version"),
            "approved_transforms": [item["transform_id"] for item in steps],
            "created_at": utc_now(),
        }
        registry_path = evidence_root / "source_registry.json"
        write_json(registry_path, source_registry)
        quality_report = build_quality_report(
            config,
            records,
            parser_result,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            split_manifest_path=split_path,
            source_registry_path=registry_path,
        )
        quality_path = evidence_root / "quality_report.json"
        write_json(quality_path, quality_report)
        state.update(
            status="pass",
            quality_status=quality_report["status"],
            warnings=quality_report["warnings"],
            phase="completed",
            progress=1.0,
            updated_at=utc_now(),
            finished_at=utc_now(),
            records_processed=int(parser_result["records_in"]),
            records_output=len(records),
            manifest_uri=host_uri(output_root_value, "processed/normalized_manifest.jsonl"),
            manifest_sha256=manifest_sha256,
            split_manifest_uri=host_uri(output_root_value, "evidence/split_manifest.json"),
            identity_sha256=identity_sha256,
            quality_report_uri=host_uri(output_root_value, "evidence/quality_report.json"),
            source_registry_uri=host_uri(output_root_value, "evidence/source_registry.json"),
        )
        write_state(state_path, state)
        return state
    except Exception as exc:
        state.update(
            status="failed",
            phase="failed",
            updated_at=utc_now(),
            finished_at=utc_now(),
            blockers=[f"{type(exc).__name__}:{exc}"],
        )
        write_state(state_path, state)
        raise


def acquire_sources(
    acquisition: dict[str, Any],
    raw_root: Path,
    state_path: Path,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    definitions = acquisition.get("source_files")
    if not isinstance(definitions, list) or not definitions:
        raise ScenarioIntakeError("scenario_source_files_missing")
    raw_root.mkdir(parents=True, exist_ok=True)
    total_expected = sum(int(item.get("size_bytes") or 0) for item in definitions if isinstance(item, dict))
    completed_bytes = 0
    sources: list[dict[str, Any]] = []
    for definition in definitions:
        if not isinstance(definition, dict):
            raise ScenarioIntakeError("scenario_source_file_invalid")
        relative = safe_relative_path(required_text(definition, "relative_path"))
        target = raw_root / relative
        expected_sha256 = required_sha256(definition, "sha256")
        expected_size = int(definition.get("size_bytes") or 0)
        url = required_text(definition, "url")
        validate_source_url(url)
        cached = target.is_file() and sha256_file(target) == expected_sha256
        if not cached:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.download")
            temporary.unlink(missing_ok=True)
            request = Request(url, headers={"User-Agent": "EnterpriseMLOps-ScenarioIntake/1.0"})
            downloaded = 0
            with urlopen(request, timeout=180) as response, temporary.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if downloaded % (8 * 1024 * 1024) < len(chunk):
                        state.update(
                            phase="acquiring",
                            active_file=relative.as_posix(),
                            bytes_downloaded=completed_bytes + downloaded,
                            bytes_total=total_expected,
                            progress=round(
                                0.55 * (completed_bytes + downloaded) / max(total_expected, 1),
                                6,
                            ),
                            updated_at=utc_now(),
                        )
                        write_state(state_path, state)
            actual_sha256 = sha256_file(temporary)
            if actual_sha256 != expected_sha256:
                temporary.unlink(missing_ok=True)
                raise ScenarioIntakeError(f"source_sha256_mismatch:{relative.as_posix()}")
            if expected_size and temporary.stat().st_size != expected_size:
                temporary.unlink(missing_ok=True)
                raise ScenarioIntakeError(f"source_size_mismatch:{relative.as_posix()}")
            temporary.replace(target)
        actual_size = target.stat().st_size
        completed_bytes += actual_size
        state.update(
            phase="acquiring",
            active_file=relative.as_posix(),
            bytes_downloaded=completed_bytes,
            bytes_total=total_expected,
            progress=round(0.55 * completed_bytes / max(total_expected, 1), 6),
            updated_at=utc_now(),
        )
        write_state(state_path, state)
        sources.append(
            {
                "path": target,
                "definition": definition,
                "evidence": {
                    "file_id": definition.get("file_id"),
                    "role": definition.get("role"),
                    "url": url,
                    "relative_path": relative.as_posix(),
                    "sha256": expected_sha256,
                    "size_bytes": actual_size,
                    "cache_reused": cached,
                },
            }
        )
    return sources


def parse_dataset(
    parser: str,
    sources: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    output_root: Path,
    output_root_value: str,
    steps: list[dict[str, Any]],
    state_path: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    if parser == "banking77_csv":
        return parse_banking77(sources, config, steps, state_path, state)
    if parser == "dolly_jsonl":
        return parse_dolly(sources, config, steps, state_path, state)
    return parse_scienceqa(
        sources,
        config,
        output_root,
        output_root_value,
        steps,
        state_path,
        state,
    )


def parse_banking77(
    sources: list[dict[str, Any]],
    config: dict[str, Any],
    steps: list[dict[str, Any]],
    state_path: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    dataset = object_value(config, "dataset")
    by_role = {str(item["definition"].get("role")): item["path"] for item in sources}
    categories = json.loads(by_role["label_schema"].read_text(encoding="utf-8"))
    if not isinstance(categories, list) or len(categories) != 77:
        raise ScenarioIntakeError("banking77_category_contract_failed")
    rows: list[tuple[str, dict[str, str]]] = []
    for role in ("train", "test"):
        with by_role[role].open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append((role, row))
    records: list[dict[str, Any]] = []
    dropped = 0
    pii_count = 0
    for index, (source_split, row) in enumerate(rows):
        text, flags, accepted = transform_text(str(row.get("text") or ""), steps)
        label = str(row.get("category") or "").strip()
        if not accepted or label not in categories:
            dropped += 1
            continue
        split = "test" if source_split == "test" else development_split(text, config)
        content_sha256 = canonical_sha256({"text": text, "label": label})
        pii_count += int(bool(flags))
        records.append(
            base_record(config, content_sha256, split, index)
            | {"text": text, "label": label, "pii_flags": flags, "task_type": "intent_classification"}
        )
        update_parse_progress(index + 1, len(rows), state_path, state)
    return {"records": records, "records_in": len(rows), "dropped": dropped, "pii_flagged": pii_count}


def parse_dolly(
    sources: list[dict[str, Any]],
    config: dict[str, Any],
    steps: list[dict[str, Any]],
    state_path: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    path = sources[0]["path"]
    max_records = int(object_value(config, "acquisition").get("max_records") or 0)
    records: list[dict[str, Any]] = []
    dropped = 0
    pii_count = 0
    with path.open("r", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    for index, line in enumerate(lines):
        if max_records and index >= max_records:
            break
        item = json.loads(line)
        instruction, instruction_flags, instruction_ok = transform_text(
            str(item.get("instruction") or ""), steps
        )
        response, response_flags, response_ok = transform_text(
            str(item.get("response") or ""), steps
        )
        context = normalize_optional_text(str(item.get("context") or ""), steps)
        flags = sorted(set(instruction_flags + response_flags))
        if not instruction_ok or not response_ok:
            dropped += 1
            continue
        category = str(item.get("category") or "unknown").strip()
        content_sha256 = canonical_sha256(
            {"instruction": instruction, "context": context, "response": response, "category": category}
        )
        pii_count += int(bool(flags))
        split = stable_split(content_sha256, object_value(config, "split_policy"))
        records.append(
            base_record(config, content_sha256, split, index)
            | {
                "instruction": instruction,
                "context": context,
                "response": response,
                "category": category,
                "label": category,
                "pii_flags": flags,
                "task_type": "instruction_tuning",
            }
        )
        update_parse_progress(index + 1, min(len(lines), max_records or len(lines)), state_path, state)
    return {"records": records, "records_in": min(len(lines), max_records or len(lines)), "dropped": dropped, "pii_flagged": pii_count}


def parse_scienceqa(
    sources: list[dict[str, Any]],
    config: dict[str, Any],
    output_root: Path,
    output_root_value: str,
    steps: list[dict[str, Any]],
    state_path: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    import pyarrow.parquet as parquet

    max_records = int(object_value(config, "acquisition").get("max_records") or 0)
    image_root = output_root / "processed" / "images"
    image_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    dropped = 0
    records_in = 0
    parquet_file = parquet.ParquetFile(sources[0]["path"])
    total = min(parquet_file.metadata.num_rows, max_records or parquet_file.metadata.num_rows)
    for batch in parquet_file.iter_batches(batch_size=32):
        for row in batch.to_pylist():
            if max_records and records_in >= max_records:
                break
            source_index = records_in
            records_in += 1
            question, _, question_ok = transform_text(str(row.get("question") or ""), steps)
            choices = [normalize_optional_text(str(item), steps) for item in (row.get("choices") or [])]
            answer = int(row.get("answer") or 0)
            image_bytes = embedded_image_bytes(row.get("image"))
            if not question_ok or not choices or answer < 0 or answer >= len(choices) or not image_bytes:
                dropped += 1
                continue
            image_sha256 = hashlib.sha256(image_bytes).hexdigest()
            content_sha256 = canonical_sha256(
                {
                    "image_sha256": image_sha256,
                    "question": question,
                    "choices": choices,
                    "answer": answer,
                }
            )
            extension = image_extension(image_bytes)
            sample_id = sample_id_for(object_value(config, "dataset"), content_sha256, source_index)
            image_path = image_root / f"{sample_id}{extension}"
            if not image_path.is_file() or sha256_file(image_path) != image_sha256:
                image_path.write_bytes(image_bytes)
            dimensions = read_image_dimensions(image_path)
            if dimensions is None:
                dropped += 1
                image_path.unlink(missing_ok=True)
                continue
            record = base_record(config, content_sha256, "test", source_index, sample_id=sample_id)
            record.update(
                {
                    "image_uri": "file:///" + host_uri(output_root_value, f"processed/images/{image_path.name}"),
                    "image_sha256": image_sha256,
                    "width": dimensions[0],
                    "height": dimensions[1],
                    "question": question,
                    "choices": choices,
                    "answer_index": answer,
                    "answer": choices[answer],
                    "hint": normalize_optional_text(str(row.get("hint") or ""), steps),
                    "lecture": normalize_optional_text(str(row.get("lecture") or ""), steps),
                    "solution": normalize_optional_text(str(row.get("solution") or ""), steps),
                    "subject": str(row.get("subject") or ""),
                    "topic": str(row.get("topic") or ""),
                    "grade": str(row.get("grade") or ""),
                    "label": str(answer),
                    "task_type": "multimodal_multiple_choice_qa",
                }
            )
            records.append(record)
            update_parse_progress(records_in, total, state_path, state)
        if max_records and records_in >= max_records:
            break
    return {"records": records, "records_in": records_in, "dropped": dropped, "pii_flagged": 0}


def validated_steps(preprocessing: dict[str, Any]) -> list[dict[str, Any]]:
    steps = preprocessing.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ScenarioIntakeError("preprocessing_steps_missing")
    validated: list[dict[str, Any]] = []
    for item in steps:
        if not isinstance(item, dict):
            raise ScenarioIntakeError("preprocessing_step_invalid")
        transform_id = str(item.get("transform_id") or "")
        if transform_id not in APPROVED_TRANSFORMS:
            raise ScenarioIntakeError(f"preprocessing_transform_not_approved:{transform_id}")
        parameters = item.get("parameters")
        if not isinstance(parameters, dict):
            raise ScenarioIntakeError(f"preprocessing_parameters_invalid:{transform_id}")
        validated.append({"transform_id": transform_id, "parameters": parameters})
    return validated


def apply_dataset_transforms(
    records: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    step = next(
        (item for item in steps if item["transform_id"] == "deduplicate_content_prefer_holdout"),
        None,
    )
    if step is None:
        return records, {"duplicates_observed": 0, "duplicates_removed": 0, "cross_split_leakage_removed": 0}
    order = step["parameters"].get("holdout_order")
    priorities = {
        str(name): index
        for index, name in enumerate(order if isinstance(order, list) else ["test", "validation", "train"])
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["content_sha256"]), []).append(record)
    kept: list[dict[str, Any]] = []
    duplicates_observed = 0
    cross_split_leakage = 0
    for group in grouped.values():
        duplicates_observed += max(0, len(group) - 1)
        if len({str(item["split"]) for item in group}) > 1:
            cross_split_leakage += len(group) - 1
        selected = min(
            group,
            key=lambda item: (
                priorities.get(str(item["split"]), len(priorities)),
                int(item.get("source_index") or 0),
            ),
        )
        kept.append(selected)
    kept.sort(key=lambda item: int(item.get("source_index") or 0))
    return kept, {
        "duplicates_observed": duplicates_observed,
        "duplicates_removed": len(records) - len(kept),
        "cross_split_leakage_removed": cross_split_leakage,
    }


def transform_text(value: str, steps: list[dict[str, Any]]) -> tuple[str, list[str], bool]:
    result = value
    flags: list[str] = []
    accepted = True
    for step in steps:
        transform_id = step["transform_id"]
        parameters = step["parameters"]
        if transform_id == "normalize_unicode_nfc":
            result = unicodedata.normalize("NFC", result)
        elif transform_id == "collapse_whitespace":
            result = " ".join(result.split())
        elif transform_id == "enforce_text_length":
            minimum = int(parameters.get("min_chars") or 0)
            maximum = int(parameters.get("max_chars") or 0)
            accepted = accepted and len(result) >= minimum and (not maximum or len(result) <= maximum)
        elif transform_id == "scan_pii_patterns":
            if EMAIL_PATTERN.search(result):
                flags.append("email_pattern")
            if PHONE_PATTERN.search(result):
                flags.append("phone_pattern")
    return result, sorted(set(flags)), accepted


def normalize_optional_text(value: str, steps: list[dict[str, Any]]) -> str:
    result = value
    for step in steps:
        if step["transform_id"] == "normalize_unicode_nfc":
            result = unicodedata.normalize("NFC", result)
        elif step["transform_id"] == "collapse_whitespace":
            result = " ".join(result.split())
    return result


def base_record(
    config: dict[str, Any],
    content_sha256: str,
    split: str,
    source_index: int,
    *,
    sample_id: str | None = None,
) -> dict[str, Any]:
    scenario = object_value(config, "scenario")
    dataset = object_value(config, "dataset")
    return {
        "schema_version": "evm.scenario_record.v1",
        "scenario_id": scenario["scenario_id"],
        "department": scenario["department"],
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "modality": scenario["modality"],
        "sample_id": sample_id or sample_id_for(dataset, content_sha256, source_index),
        "split": split,
        "content_sha256": content_sha256,
        "source_revision": dataset["source_revision"],
        "source_index": source_index,
        "license_id": dataset["license_id"],
        "usage_policy": dataset["usage_policy"],
    }


def build_quality_report(
    config: dict[str, Any],
    records: list[dict[str, Any]],
    parser_result: dict[str, Any],
    *,
    manifest_path: Path,
    manifest_sha256: str,
    split_manifest_path: Path,
    source_registry_path: Path,
) -> dict[str, Any]:
    hashes = Counter(str(item["content_sha256"]) for item in records)
    duplicates = sum(count - 1 for count in hashes.values() if count > 1)
    duplicates_observed = int(parser_result.get("duplicates_observed") or duplicates)
    duplicates_removed = int(parser_result.get("duplicates_removed") or 0)
    cross_split_leakage_removed = int(parser_result.get("cross_split_leakage_removed") or 0)
    pii_pattern_records = int(parser_result["pii_flagged"])
    preprocessing = object_value(config, "preprocessing")
    pii_scan = next(
        (
            item
            for item in preprocessing.get("steps", [])
            if isinstance(item, dict) and item.get("transform_id") == "scan_pii_patterns"
        ),
        None,
    )
    pii_mode = str((pii_scan or {}).get("parameters", {}).get("mode") or "report_only")
    warnings: list[str] = []
    review_reasons: list[str] = []
    if duplicates:
        review_reasons.append(f"duplicate_records_remaining:{duplicates}")
    if pii_pattern_records:
        warnings.append(f"pii_patterns_detected:{pii_pattern_records}")
        if pii_mode == "review_required":
            review_reasons.append(f"pii_review_required:{pii_pattern_records}")
    status = "failed" if not records else "review_required" if review_reasons else "pass"
    labels = Counter(str(item.get("label") or "unknown") for item in records)
    return {
        "schema_version": "evm.scenario_quality_report.v1",
        "scenario_id": object_value(config, "scenario")["scenario_id"],
        "dataset_id": object_value(config, "dataset")["dataset_id"],
        "dataset_version": object_value(config, "dataset")["dataset_version"],
        "status": status,
        "review_reasons": review_reasons,
        "warnings": warnings,
        "records_in": int(parser_result["records_in"]),
        "records_out": len(records),
        "records_dropped": int(parser_result["dropped"]),
        "duplicate_records": duplicates,
        "duplicates_observed": duplicates_observed,
        "duplicates_removed": duplicates_removed,
        "cross_split_leakage_removed": cross_split_leakage_removed,
        "pii_pattern_records": pii_pattern_records,
        "split_counts": dict(sorted(Counter(str(item["split"]) for item in records).items())),
        "label_count": len(labels),
        "largest_labels": dict(labels.most_common(12)),
        "manifest_uri": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "split_manifest_uri": str(split_manifest_path),
        "source_registry_uri": str(source_registry_path),
        "created_at": utc_now(),
    }


def development_split(value: str, config: dict[str, Any]) -> str:
    policy = object_value(config, "split_policy")
    train = float(policy.get("train") or 0.0)
    validation = float(policy.get("validation") or 0.0)
    denominator = train + validation
    if denominator <= 0:
        return "train"
    bucket = stable_bucket(value, int(policy.get("seed") or 20260714))
    return "train" if bucket < train / denominator else "validation"


def stable_split(value: str, policy: dict[str, Any]) -> str:
    bucket = stable_bucket(value, int(policy.get("seed") or 20260714))
    train = float(policy.get("train") or 0.0)
    validation = float(policy.get("validation") or 0.0)
    if bucket < train:
        return "train"
    if bucket < train + validation:
        return "validation"
    return "test"


def stable_bucket(value: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / 0xFFFFFFFFFFFFFFFF


def embedded_image_bytes(value: Any) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, dict):
        payload = value.get("bytes")
        return payload if isinstance(payload, bytes) else None
    return None


def image_extension(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload.startswith(b"\xff\xd8"):
        return ".jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    raise ScenarioIntakeError("embedded_image_format_not_allowed")


def sample_id_for(dataset: dict[str, Any], content_sha256: str, source_index: int) -> str:
    return f"{dataset['dataset_id']}-{source_index:06d}-{content_sha256[:12]}"


def update_parse_progress(
    completed: int,
    total: int,
    state_path: Path,
    state: dict[str, Any],
) -> None:
    if completed != total and completed % 128:
        return
    state.update(
        phase="preprocessing",
        records_processed=completed,
        progress=round(0.58 + 0.37 * completed / max(total, 1), 6),
        updated_at=utc_now(),
    )
    write_state(state_path, state)


def validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SOURCE_HOSTS:
        raise ScenarioIntakeError("scenario_source_url_not_allowed")


def safe_relative_path(value: str) -> Path:
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ScenarioIntakeError("scenario_source_relative_path_invalid")
    return path


def host_uri(root: str, relative: str) -> str:
    return f"{root.replace(chr(92), '/').rstrip('/')}/{relative.lstrip('/')}"


def required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ScenarioIntakeError(f"scenario_field_missing:{key}")
    return value


def required_slug(payload: dict[str, Any], key: str) -> str:
    value = required_text(payload, key)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", value):
        raise ScenarioIntakeError(f"scenario_slug_invalid:{key}")
    return value


def required_sha256(payload: dict[str, Any], key: str) -> str:
    value = required_text(payload, key).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ScenarioIntakeError(f"scenario_sha256_invalid:{key}")
    return value


def object_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ScenarioIntakeError(f"scenario_object_missing:{key}")
    return value


def canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def write_state(path: Path, state: dict[str, Any]) -> None:
    write_json(path, state)


def main(argv: Sequence[str] | None = None) -> None:
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    config_path = arguments[0] if arguments else "configs/scenarios/banking77-intent-classification.json"
    print(json.dumps(run(config_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

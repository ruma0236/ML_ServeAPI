from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from evm.control_panel.lifecycle_guards import canonical_digest, file_digest
from evm.control_panel.readiness_evaluator import runtime_path
from evm.core.dataset import shard_index_identity_digest


DATA_SCHEMA = "evm.lifecycle_data_integrity.v1"
RELEASE_SCHEMA = "evm.lifecycle_release_submission.v1"


class LifecycleIntegrityBlocked(RuntimeError):
    def __init__(
        self,
        blockers: list[str],
        *,
        decision_fingerprint: str | None = None,
    ):
        self.blockers = sorted(set(blockers))
        self.decision_fingerprint = decision_fingerprint
        super().__init__(", ".join(self.blockers))


def atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path, blocker: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleIntegrityBlocked([blocker]) from exc
    if not isinstance(payload, dict):
        raise LifecycleIntegrityBlocked([blocker])
    return payload


def read_jsonl(path: Path, blocker: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"line {line_number} is not an object")
                records.append(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LifecycleIntegrityBlocked([blocker]) from exc
    return records


def evidence_path(value: str, *, base: Path | None = None) -> Path:
    path = runtime_path(value)
    if path.is_file() or path.is_absolute() or base is None:
        return path
    return base / path


def record_id(record: dict[str, Any]) -> str:
    return str(record.get("sample_id") or record.get("id") or "").strip()


def record_content_id(record: dict[str, Any]) -> str:
    return str(record.get("content_sha256") or "").strip().lower()


def validate_lifecycle_data_integrity(artifact_root: Path) -> Path:
    index_path = artifact_root / "data" / "shards" / "shard_index.json"
    report_path = artifact_root / "data" / "integrity-validation.json"
    blockers: list[str] = []
    try:
        index = read_json(index_path, "integrity_shard_index_invalid")
    except LifecycleIntegrityBlocked as exc:
        atomic_write(
            report_path,
            {
                "schema_version": DATA_SCHEMA,
                "decision": "blocked",
                "blockers": exc.blockers,
                "decision_fingerprint": canonical_digest(exc.blockers),
            },
        )
        raise

    embedded_identity = str(index.get("identity_sha256") or "").lower()
    semantic_identity = shard_index_identity_digest(index)
    if embedded_identity != semantic_identity:
        blockers.append("integrity_shard_index_identity_mismatch")
    shards = index.get("shards") if isinstance(index.get("shards"), list) else []
    if not shards:
        blockers.append("integrity_shards_missing")

    source_uri = str(index.get("input_manifest") or "")
    source_path = evidence_path(source_uri, base=index_path.parent)
    try:
        source_records = read_jsonl(
            source_path,
            "integrity_source_manifest_invalid",
        )
    except LifecycleIntegrityBlocked as exc:
        blockers.extend(exc.blockers)
        source_records = []
    source_by_id: dict[str, dict[str, Any]] = {}
    source_content_counts: Counter[str] = Counter()
    for record in source_records:
        sample_id = record_id(record)
        content_id = record_content_id(record)
        if not sample_id or sample_id in source_by_id:
            blockers.append("integrity_duplicate_source_record_identity")
            continue
        source_by_id[sample_id] = record
        if not content_id:
            blockers.append("integrity_source_content_identity_missing")
        else:
            source_content_counts[content_id] += 1
    if not source_records:
        blockers.append("integrity_source_records_empty")
    if any(count > 1 for count in source_content_counts.values()):
        blockers.append("integrity_duplicate_source_content_identity")

    observed_ids: Counter[str] = Counter()
    observed_content: Counter[str] = Counter()
    observed_splits: dict[str, set[str]] = defaultdict(set)
    observed_split_counts: Counter[str] = Counter()
    shard_hashes: dict[str, str] = {}
    total_records = 0
    seen_shard_ids: set[str] = set()
    for item in shards:
        if not isinstance(item, dict):
            blockers.append("integrity_shard_entry_invalid")
            continue
        shard_id = str(item.get("shard_id") or "").strip()
        split = str(item.get("split") or "").strip()
        if not shard_id or shard_id in seen_shard_ids:
            blockers.append("integrity_duplicate_shard_identity")
            continue
        if split not in {"train", "validation", "test"}:
            blockers.append("integrity_shard_split_invalid")
        seen_shard_ids.add(shard_id)
        shard_path = evidence_path(str(item.get("path") or ""), base=index_path.parent)
        try:
            records = read_jsonl(shard_path, "integrity_shard_invalid")
        except LifecycleIntegrityBlocked as exc:
            blockers.extend(exc.blockers)
            continue
        shard_hashes[shard_id] = file_digest(shard_path)
        if int(item.get("record_count") or 0) != len(records):
            blockers.append("integrity_shard_record_count_mismatch")
        if records:
            if str(item.get("first_sample_id") or "") != record_id(records[0]):
                blockers.append("integrity_shard_boundary_mismatch")
            if str(item.get("last_sample_id") or "") != record_id(records[-1]):
                blockers.append("integrity_shard_boundary_mismatch")
        total_records += len(records)
        observed_split_counts[split] += len(records)
        for record in records:
            sample_id = record_id(record)
            content_id = record_content_id(record)
            observed_ids[sample_id] += 1
            if not content_id:
                blockers.append("integrity_record_content_identity_missing")
            else:
                observed_content[content_id] += 1
            observed_splits[sample_id].add(split)
            if str(record.get("split") or "") != split:
                blockers.append("integrity_record_split_mismatch")
            if record.get("label") in {None, ""}:
                blockers.append("integrity_record_label_missing")
            source = source_by_id.get(sample_id)
            if source is None:
                blockers.append("integrity_record_not_in_source")
                continue
            if content_id != record_content_id(source):
                blockers.append("integrity_record_content_mismatch")
            if str(record.get("label") or "") != str(source.get("label") or ""):
                blockers.append("integrity_record_label_mismatch")

    if any(count > 1 for sample_id, count in observed_ids.items() if sample_id):
        blockers.append("integrity_duplicate_record_identity")
    if "" in observed_ids:
        blockers.append("integrity_record_identity_missing")
    if any(len(splits) > 1 for splits in observed_splits.values()):
        blockers.append("integrity_split_leakage_detected")
    if any(count > 1 for count in observed_content.values()):
        blockers.append("integrity_duplicate_content_identity")
    if set(observed_ids) != set(source_by_id):
        blockers.append("integrity_manifest_membership_mismatch")
    if total_records != int(index.get("record_count") or 0):
        blockers.append("integrity_manifest_count_mismatch")
    if total_records == 0:
        blockers.append("integrity_shard_records_empty")
    expected_split_counts = {
        str(key): int(value)
        for key, value in (
            index.get("split_counts") if isinstance(index.get("split_counts"), dict) else {}
        ).items()
    }
    if dict(sorted(observed_split_counts.items())) != dict(sorted(expected_split_counts.items())):
        blockers.append("integrity_split_count_mismatch")
    if len(seen_shard_ids) != int(index.get("shard_count") or 0):
        blockers.append("integrity_shard_count_mismatch")

    blockers = sorted(set(blockers))
    report = {
        "schema_version": DATA_SCHEMA,
        "decision": "blocked" if blockers else "pass",
        "shard_index_uri": str(index_path),
        "shard_index_file_sha256": file_digest(index_path),
        "embedded_identity_sha256": embedded_identity,
        "semantic_identity_sha256": semantic_identity,
        "source_manifest_uri": str(source_path),
        "source_manifest_sha256": (
            file_digest(source_path) if source_path.is_file() else None
        ),
        "source_record_count": len(source_records),
        "observed_record_count": total_records,
        "observed_shard_count": len(seen_shard_ids),
        "observed_split_counts": dict(sorted(observed_split_counts.items())),
        "shard_file_sha256": dict(sorted(shard_hashes.items())),
        "blockers": blockers,
    }
    report["decision_fingerprint"] = canonical_digest(
        {
            "decision": report["decision"],
            "embedded_identity_sha256": embedded_identity,
            "semantic_identity_sha256": semantic_identity,
            "source_manifest_sha256": report["source_manifest_sha256"],
            "shard_file_sha256": report["shard_file_sha256"],
            "blockers": blockers,
        }
    )
    atomic_write(report_path, report)
    if blockers:
        raise LifecycleIntegrityBlocked(blockers)
    return report_path


def readiness_check(readiness: dict[str, Any], check_id: str) -> dict[str, Any]:
    checks = readiness.get("checks") if isinstance(readiness.get("checks"), list) else []
    return next(
        (
            item
            for item in checks
            if isinstance(item, dict) and item.get("check_id") == check_id
        ),
        {},
    )


def build_lifecycle_release_submission(
    *,
    artifact_root: Path,
    run_id: str,
    source_commit: str,
    readiness_uri: str,
    model_matrix_uri: str,
    ct_evaluation_uri: str,
) -> Path:
    readiness_path = runtime_path(readiness_uri)
    matrix_path = runtime_path(model_matrix_uri)
    ct_path = runtime_path(ct_evaluation_uri)
    readiness = read_json(readiness_path, "release_readiness_invalid")
    matrix = read_json(matrix_path, "release_model_matrix_invalid")
    ct = read_json(ct_path, "release_ct_evaluation_invalid")
    candidate_id = str(readiness.get("candidate_id") or "")
    candidates = matrix.get("candidates") if isinstance(matrix.get("candidates"), list) else []
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and item.get("candidate_id") == candidate_id
        ),
        {},
    )
    model_check = readiness_check(readiness, "model_artifact")
    runtime_check = readiness_check(readiness, "kubernetes_runtime")
    mlflow_check = readiness_check(readiness, "mlflow_run")
    model_uri = str(model_check.get("evidence_uri") or candidate.get("model_artifact") or "")
    model_path = runtime_path(model_uri)
    if not model_path.is_file():
        raise LifecycleIntegrityBlocked(["release_model_artifact_missing"])
    actual_model_digest = file_digest(model_path)
    model_digest = str(
        model_check.get("observed", {}).get("actual_sha256")
        if isinstance(model_check.get("observed"), dict)
        else ""
    ) or str(candidate.get("model_sha256") or "")
    if model_digest != actual_model_digest:
        raise LifecycleIntegrityBlocked(["release_model_artifact_digest_mismatch"])
    runtime_observed = (
        runtime_check.get("observed")
        if isinstance(runtime_check.get("observed"), dict)
        else {}
    )
    mlflow_observed = (
        mlflow_check.get("observed")
        if isinstance(mlflow_check.get("observed"), dict)
        else {}
    )
    submission = {
        "schema_version": RELEASE_SCHEMA,
        "run_id": run_id,
        "source_commit": source_commit,
        "candidate_id": candidate_id,
        "dataset_version": str(readiness.get("dataset_version") or ""),
        "model_digest": model_digest,
        "model_artifact_uri": model_uri,
        "container_image_digest": str(runtime_observed.get("serving_image_digest") or ""),
        "mlflow_run_id": str(mlflow_observed.get("run_id") or candidate.get("mlflow_run_id") or ""),
        "ct_evaluation_id": str(ct.get("evaluation_id") or ""),
        "evidence": {
            "readiness": {"uri": str(readiness_path), "sha256": file_digest(readiness_path)},
            "model_matrix": {"uri": str(matrix_path), "sha256": file_digest(matrix_path)},
            "ct_evaluation": {"uri": str(ct_path), "sha256": file_digest(ct_path)},
            "model_artifact": {"uri": str(model_path), "sha256": actual_model_digest},
        },
    }
    submission["submission_digest"] = canonical_digest(submission)
    path = artifact_root / "validation" / "release-submission.json"
    atomic_write(path, submission)
    return path


def validate_lifecycle_release_submission(
    submission_path: Path,
    *,
    run_id: str,
    source_commit: str,
    expected_candidate_id: str | None = None,
    expected_model_digest: str | None = None,
    expected_ct_evaluation_id: str | None = None,
) -> dict[str, Any]:
    submission = read_json(submission_path, "release_submission_invalid")
    blockers: list[str] = []
    stored_digest = str(submission.get("submission_digest") or "")
    material = dict(submission)
    material.pop("submission_digest", None)
    if stored_digest != canonical_digest(material):
        blockers.append("release_submission_digest_mismatch")
    if submission.get("schema_version") != RELEASE_SCHEMA:
        blockers.append("release_submission_schema_mismatch")
    if submission.get("run_id") != run_id:
        blockers.append("release_submission_run_mismatch")
    if submission.get("source_commit") != source_commit:
        blockers.append("release_submission_source_mismatch")
    if len(str(submission.get("source_commit") or "")) != 40:
        blockers.append("release_source_identity_invalid")
    if expected_candidate_id and submission.get("candidate_id") != expected_candidate_id:
        blockers.append("release_candidate_identity_mismatch")
    if expected_model_digest and submission.get("model_digest") != expected_model_digest:
        blockers.append("release_model_digest_mismatch")
    if (
        expected_ct_evaluation_id
        and submission.get("ct_evaluation_id") != expected_ct_evaluation_id
    ):
        blockers.append("release_ct_identity_mismatch")

    evidence = (
        submission.get("evidence") if isinstance(submission.get("evidence"), dict) else {}
    )
    loaded: dict[str, dict[str, Any]] = {}
    for role in ("readiness", "model_matrix", "ct_evaluation", "model_artifact"):
        item = evidence.get(role) if isinstance(evidence.get(role), dict) else {}
        path = runtime_path(str(item.get("uri") or ""))
        expected_sha = str(item.get("sha256") or "")
        if not path.is_file():
            blockers.append(f"release_{role}_missing")
            continue
        actual_sha = file_digest(path)
        if actual_sha != expected_sha:
            blockers.append(f"release_{role}_evidence_digest_mismatch")
        if role == "model_artifact" and actual_sha != submission.get("model_digest"):
            blockers.append("release_model_artifact_digest_mismatch")
        if role != "model_artifact":
            try:
                loaded[role] = read_json(path, f"release_{role}_invalid")
            except LifecycleIntegrityBlocked as exc:
                blockers.extend(exc.blockers)

    readiness = loaded.get("readiness", {})
    matrix = loaded.get("model_matrix", {})
    ct = loaded.get("ct_evaluation", {})
    candidate_id = str(submission.get("candidate_id") or "")
    model_digest = str(submission.get("model_digest") or "")
    dataset_version = str(submission.get("dataset_version") or "")
    required_identities = {
        "candidate": candidate_id,
        "dataset": dataset_version,
        "model": model_digest,
        "container_image": str(submission.get("container_image_digest") or ""),
        "mlflow_run": str(submission.get("mlflow_run_id") or ""),
        "ct_evaluation": str(submission.get("ct_evaluation_id") or ""),
    }
    blockers.extend(
        f"release_{role}_identity_missing"
        for role, value in required_identities.items()
        if not value
    )
    if len(model_digest) != 64:
        blockers.append("release_model_identity_invalid")
    if readiness.get("decision") != "ready" or readiness.get("status") != "pass":
        blockers.append("release_readiness_not_ready")
    if readiness.get("candidate_id") != candidate_id:
        blockers.append("release_readiness_candidate_mismatch")
    if readiness.get("dataset_version") != dataset_version:
        blockers.append("release_readiness_dataset_mismatch")
    model_check = readiness_check(readiness, "model_artifact")
    observed_model = (
        model_check.get("observed") if isinstance(model_check.get("observed"), dict) else {}
    )
    if observed_model.get("actual_sha256") != model_digest:
        blockers.append("release_readiness_model_digest_mismatch")
    runtime_observed = readiness_check(readiness, "kubernetes_runtime").get("observed")
    runtime_observed = runtime_observed if isinstance(runtime_observed, dict) else {}
    if runtime_observed.get("serving_image_digest") != submission.get(
        "container_image_digest"
    ):
        blockers.append("release_container_image_mismatch")
    mlflow_observed = readiness_check(readiness, "mlflow_run").get("observed")
    mlflow_observed = mlflow_observed if isinstance(mlflow_observed, dict) else {}
    if mlflow_observed.get("run_id") != submission.get("mlflow_run_id"):
        blockers.append("release_mlflow_identity_mismatch")

    candidates = matrix.get("candidates") if isinstance(matrix.get("candidates"), list) else []
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and item.get("candidate_id") == candidate_id
        ),
        {},
    )
    if matrix.get("status") != "pass" or candidate.get("status") != "pass":
        blockers.append("release_model_matrix_not_pass")
    if candidate.get("dataset_version") != dataset_version:
        blockers.append("release_model_matrix_dataset_mismatch")
    if candidate.get("model_sha256") != model_digest:
        blockers.append("release_model_matrix_digest_mismatch")
    if candidate.get("mlflow_run_id") != submission.get("mlflow_run_id"):
        blockers.append("release_model_matrix_mlflow_mismatch")

    if ct.get("status") != "pass" or ct.get("decision") != "pass":
        blockers.append("release_ct_not_pass")
    if ct.get("lifecycle_run_id") != run_id:
        blockers.append("release_ct_run_mismatch")
    if ct.get("candidate_id") != candidate_id:
        blockers.append("release_ct_candidate_mismatch")
    if ct.get("dataset_version") != dataset_version:
        blockers.append("release_ct_dataset_mismatch")
    if ct.get("model_sha256") != model_digest:
        blockers.append("release_ct_model_digest_mismatch")
    if ct.get("evaluation_id") != submission.get("ct_evaluation_id"):
        blockers.append("release_ct_evaluation_mismatch")

    blockers = sorted(set(blockers))
    result = {
        "schema_version": "evm.lifecycle_release_integrity_decision.v1",
        "run_id": run_id,
        "decision": "blocked" if blockers else "pass",
        "candidate_id": candidate_id,
        "model_digest": model_digest,
        "ct_evaluation_id": str(submission.get("ct_evaluation_id") or ""),
        "blockers": blockers,
    }
    result["decision_fingerprint"] = canonical_digest(result)
    if blockers:
        raise LifecycleIntegrityBlocked(
            blockers,
            decision_fingerprint=result["decision_fingerprint"],
        )
    return result

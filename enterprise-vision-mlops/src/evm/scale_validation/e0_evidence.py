from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evm.scale_validation.e0_runtime import (
    CLAIM_BOUNDARY,
    SCHEMA_VERSION,
    E0RuntimeConfig,
    analyze_attempts,
    canonical,
    project_attempt,
    sha256_file,
)
from evm.scale_validation.s8_runtime import git_blob_identity


REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class E0EvidenceValidationError(RuntimeError):
    pass


SOURCE_PATHS = {
    "runtime": Path("src/evm/scale_validation/e0_runtime.py"),
    "validator": Path("src/evm/scale_validation/e0_evidence.py"),
    "runner": Path("scripts/dev/run_s8_v4_e0_environment.py"),
    "validator_cli": Path("scripts/dev/validate_s8_v4_e0_environment.py"),
    "model_generator": Path("scripts/dev/generate_e0_triton_model.py"),
    "config": Path("configs/s8_v4_e0_environment_v1.toml"),
    "prometheus": Path("monitoring/prometheus/prometheus.yml"),
}


def validate_e0_evidence(
    payload: Mapping[str, Any],
    *,
    config: E0RuntimeConfig,
    private_root: Path,
    project_root: Path,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version")
    if payload.get("status") != "review_pending":
        errors.append("status_must_be_review_pending")
    if payload.get("acceptance_credit") is not False:
        errors.append("review_pending_acceptance_credit_must_be_false")
    if payload.get("reviewer_sign_off") != "pending":
        errors.append("reviewer_sign_off_must_be_pending")
    if payload.get("claim_boundary") != CLAIM_BOUNDARY:
        errors.append("claim_boundary")
    if canonical(payload.get("runtime_contract")) != canonical(config.public_dict()):
        errors.append("runtime_contract")

    source = dict(payload.get("source_identity", {}))
    revision = str(source.get("runtime_revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("source_revision")
    elif not _is_ancestor(project_root, revision, validation_revision):
        errors.append("source_revision_ancestry")
    recorded_blobs = dict(source.get("git_blobs", {}))
    if REVISION_PATTERN.fullmatch(revision):
        for label, path in SOURCE_PATHS.items():
            try:
                expected = git_blob_identity(project_root, revision, path)
            except (OSError, subprocess.SubprocessError):
                errors.append(f"source_blob_unresolvable:{label}")
                continue
            if canonical(recorded_blobs.get(label)) != canonical(expected):
                errors.append(f"source_blob_identity:{label}")

    projected_attempts: list[dict[str, Any]] = []
    for public_attempt in payload.get("attempts", []):
        if not isinstance(public_attempt, Mapping):
            errors.append("attempt_mapping")
            continue
        private = dict(public_attempt.get("private_evidence", {}))
        relative = str(private.get("path") or "")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            errors.append("private_attempt_path")
            continue
        path = private_root / relative
        if not path.is_file():
            errors.append(f"private_attempt_missing:{relative}")
            continue
        if sha256_file(path) != private.get("sha256"):
            errors.append(f"private_attempt_sha256:{relative}")
            continue
        raw = json.loads(path.read_bytes())
        try:
            projected = project_attempt(raw, config)
        except (ValueError, E0EvidenceValidationError, RuntimeError) as exc:
            errors.append(f"private_attempt_invalid:{relative}:{exc}")
            continue
        if canonical(public_attempt.get("summary")) != canonical(projected):
            errors.append(f"attempt_projection:{relative}")
        projected_attempts.append(projected)

    analysis = analyze_attempts(projected_attempts, config)
    if canonical(payload.get("analysis")) != canonical(analysis):
        errors.append("analysis_projection")
    if canonical(payload.get("acceptance")) != canonical(analysis["acceptance"]):
        errors.append("acceptance_projection")
    if not analysis["evidence_ready"]:
        errors.append("acceptance_not_evidence_ready")
    alignment = dict(payload.get("alignment", {}))
    for key in (
        "definition_alignment",
        "experiment_purpose_alignment",
        "validation_purpose_alignment",
        "test_purpose_alignment",
    ):
        if alignment.get(key) is not True:
            errors.append(f"alignment:{key}")

    index_summary = dict(payload.get("private_evidence", {}))
    index_path = private_root / "private-evidence-index.json"
    if not index_path.is_file():
        errors.append("private_index_missing")
    else:
        if sha256_file(index_path) != index_summary.get("index_sha256"):
            errors.append("private_index_sha256")
        index = json.loads(index_path.read_bytes())
        entries = list(index.get("entries", []))
        observed_entries: list[dict[str, Any]] = []
        for entry in entries:
            relative = str(entry.get("path") or "")
            path = private_root / relative
            if not path.is_file() or relative == "private-evidence-index.json":
                errors.append(f"private_index_entry:{relative}")
                continue
            observed = {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            observed_entries.append(observed)
            if canonical(entry) != canonical(observed):
                errors.append(f"private_index_projection:{relative}")
        aggregate = (
            __import__("hashlib").sha256(canonical(observed_entries).encode("ascii")).hexdigest()
        )
        if aggregate != index.get("aggregate_sha256"):
            errors.append("private_index_aggregate")
        if aggregate != index_summary.get("aggregate_sha256"):
            errors.append("private_summary_aggregate")
        if len(observed_entries) != index_summary.get("artifact_count"):
            errors.append("private_summary_count")

    if errors:
        raise E0EvidenceValidationError("e0_evidence_invalid:" + ",".join(sorted(set(errors))))
    return {
        "valid": True,
        "status": "review_pending",
        "runtime_revision": revision,
        "attempt_count": len(projected_attempts),
        "acceptance": analysis["acceptance"],
        "reviewer_sign_off": "pending",
    }


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )

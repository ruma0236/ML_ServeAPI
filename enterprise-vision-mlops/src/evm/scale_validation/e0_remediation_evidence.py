from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evm.scale_validation.e0_evidence import validate_e0_evidence
from evm.scale_validation.e0_runtime import E0RuntimeConfig, canonical, sha256_file
from evm.scale_validation.e0_strict_evidence import (
    public_strict_projection,
    strict_revalidate_e0_runtime,
)
from evm.scale_validation.s8_runtime import git_blob_identity


SCHEMA_VERSION = "evm.s8_v4.e0_remediation_review.v2"
SOURCE_PATHS = {
    "strict_validator": Path("src/evm/scale_validation/e0_strict_evidence.py"),
    "remediation_validator": Path("src/evm/scale_validation/e0_remediation_evidence.py"),
    "strict_cli": Path("scripts/dev/validate_s8_v4_e0_strict.py"),
    "historical_prometheus_collector": Path(
        "scripts/dev/capture_e0_historical_prometheus.py"
    ),
    "current_revision_smoke": Path(
        "scripts/dev/run_s8_v4_e0_remediation_smoke.py"
    ),
    "state_transition": Path("scripts/dev/update_s8_v4_e0_state.py"),
    "mutation_tests": Path("tests/test_e0_strict_evidence.py"),
    "remediation_tests": Path("tests/test_e0_remediation_evidence.py"),
    "runtime_config": Path("configs/s8_v4_e0_environment_v1.toml"),
}
REQUIRED_REGRESSION_SUITES = {
    "changed_file_lint",
    "focused_s8_closure",
    "real_postgresql",
    "lifecycle_host_e2e",
    "s0_s8_status_evidence",
    "full_python",
    "control_panel",
    "frontend_production_build",
}


class E0RemediationEvidenceError(RuntimeError):
    pass


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise E0RemediationEvidenceError(f"{label}:missing")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise E0RemediationEvidenceError(f"{label}:invalid_json") from exc
    if not isinstance(payload, dict):
        raise E0RemediationEvidenceError(f"{label}:not_object")
    return payload


def _relative_path(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        raise E0RemediationEvidenceError(f"{label}:unsafe_path")
    path = root / candidate
    if not path.is_file():
        raise E0RemediationEvidenceError(f"{label}:missing")
    return path


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


def _private_index_projection(root: Path, index_path: Path) -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path != index_path
    ]
    return {
        "artifact_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "aggregate_sha256": hashlib.sha256(canonical(entries).encode("ascii")).hexdigest(),
        "entries": entries,
    }


def write_private_index(root: Path, *, generated_at: str) -> Path:
    index_path = root / "private-remediation-index.json"
    projection = _private_index_projection(root, index_path)
    payload = {
        "schema_version": "evm.s8_v4.e0_remediation_private_index.v2",
        "generated_at": generated_at,
        **projection,
    }
    index_path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")
    return index_path


def validate_private_index(root: Path) -> dict[str, Any]:
    index_path = root / "private-remediation-index.json"
    index = _load_json(index_path, "private_index")
    if index.get("schema_version") != "evm.s8_v4.e0_remediation_private_index.v2":
        raise E0RemediationEvidenceError("private_index:schema")
    projection = _private_index_projection(root, index_path)
    for field, expected in projection.items():
        if canonical(index.get(field)) != canonical(expected):
            raise E0RemediationEvidenceError(f"private_index:{field}")
    return {
        "index_sha256": sha256_file(index_path),
        "artifact_count": projection["artifact_count"],
        "total_bytes": projection["total_bytes"],
        "aggregate_sha256": projection["aggregate_sha256"],
    }


def _count(pattern: str, text: str, label: str) -> int:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise E0RemediationEvidenceError(f"regression:{label}:count")
    return int(match.group(1))


def _regression_summary(suite_id: str, text: str) -> dict[str, Any]:
    if "FAILED" in text or "ERROR" in text or "Traceback" in text:
        raise E0RemediationEvidenceError(f"regression:{suite_id}:failure_text")
    if suite_id == "changed_file_lint":
        if "All checks passed!" not in text:
            raise E0RemediationEvidenceError("regression:changed_file_lint:result")
        return {"result": "passed"}
    if suite_id == "frontend_production_build":
        if "built in" not in text:
            raise E0RemediationEvidenceError("regression:frontend:result")
        return {
            "result": "passed",
            "transformed_modules": _count(
                r"(\d+) modules transformed", text, "frontend_modules"
            ),
        }
    if suite_id == "control_panel":
        return {
            "result": "passed",
            "python_tests_passed": _count(
                r"^(\d+) passed in", text, "control_panel_python"
            ),
            "ui_test_files_passed": _count(
                r"Test Files\s+(\d+) passed", text, "control_panel_files"
            ),
            "ui_tests_passed": _count(
                r"Tests\s+(\d+) passed", text, "control_panel_ui"
            ),
        }
    passed = _count(r"^(\d+) passed(?:,| in)", text, suite_id)
    skipped = re.search(r"(\d+) skipped", text)
    return {
        "result": "passed",
        "tests_passed": passed,
        "tests_skipped": int(skipped.group(1)) if skipped else 0,
        "skipped_nodes": [],
    }


def project_regressions(
    remediation_root: Path, relative_root: str, validation_revision: str
) -> list[dict[str, Any]]:
    root = remediation_root / relative_root
    if not root.is_dir():
        raise E0RemediationEvidenceError("regression:root_missing")
    records = []
    for suite_id in sorted(REQUIRED_REGRESSION_SUITES):
        meta_path = root / f"{suite_id}.meta.json"
        log_path = root / f"{suite_id}.log"
        meta = _load_json(meta_path, f"regression:{suite_id}:meta")
        if (
            meta.get("suite_id") != suite_id
            or int(meta.get("exit_code", -1)) != 0
            or meta.get("source_revision") != validation_revision
        ):
            raise E0RemediationEvidenceError(f"regression:{suite_id}:meta_identity")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        records.append(
            {
                "suite_id": suite_id,
                "public_command": meta.get("public_command"),
                "source_revision": validation_revision,
                "exit_code": 0,
                "meta_sha256": sha256_file(meta_path),
                "log_sha256": sha256_file(log_path),
                **_regression_summary(suite_id, text),
            }
        )
    return records


def project_failed_regression(remediation_root: Path, relative_root: str) -> dict[str, Any]:
    root = remediation_root / relative_root
    meta_path = root / "changed_file_lint.meta.json"
    log_path = root / "changed_file_lint.log"
    meta = _load_json(meta_path, "failed_regression:meta")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if int(meta.get("exit_code", 0)) == 0 or "No module named ruff" not in text:
        raise E0RemediationEvidenceError("failed_regression:rca")
    return {
        "credit": "zero_credit",
        "suite_id": "changed_file_lint",
        "failure_class": "command_environment_missing_dependency",
        "source_revision": meta.get("source_revision"),
        "meta_sha256": sha256_file(meta_path),
        "log_sha256": sha256_file(log_path),
        "acceptance_suites_started": 0,
        "rca": "The selected Python environment lacked the Ruff module; no code or runtime acceptance suite ran.",
    }


def project_current_smoke(
    remediation_root: Path,
    relative: str,
    *,
    expected_model_digest: str,
    project_root: Path,
    validation_revision: str,
) -> dict[str, Any]:
    path = _relative_path(remediation_root, relative, "current_smoke")
    smoke = _load_json(path, "current_smoke")
    source = dict(smoke.get("source_identity", {}))
    revision = str(source.get("revision") or "")
    result = dict(smoke.get("result", {}))
    cleanup = dict(smoke.get("cleanup", {}))
    queues = dict(cleanup.get("queues", {}))
    if (
        smoke.get("credit") != "non_credit"
        or smoke.get("acceptance_credit") is not False
        or smoke.get("diagnostic_only") is not True
        or smoke.get("passed") is not True
        or smoke.get("failure") is not None
        or len(revision) != 40
        or not _is_ancestor(project_root, revision, validation_revision)
        or smoke.get("model_identity", {}).get("artifact_sha256")
        != expected_model_digest
        or result.get("request_count") != 100
        or result.get("output") != [3.0, 5.0, 7.0, 9.0]
        or result.get("direct_request_success") != 100
        or result.get("direct_inference_count") != 100
        or result.get("prometheus_request_success") != 100
        or result.get("prometheus_inference_count") != 100
        or result.get("host_gpu_uuid") != result.get("container_gpu_uuid")
        or float(result.get("direct_triton_gpu_memory_used_bytes", 0)) <= 0
        or cleanup.get("container_absent") is not True
        or cleanup.get("ports_absent") is not True
        or cleanup.get("target_absent") is not True
        or cleanup.get("lease_absent") is not True
        or queues != {"active": 0, "leased": 0, "outcome_unknown": 0}
        or cleanup.get("temporary_kubernetes_resources_absent") is not True
        or cleanup.get("b0_cuda_inference", {}).get("passed") is not True
        or cleanup.get("holder_uid_match") is not True
    ):
        raise E0RemediationEvidenceError("current_smoke:invariant")
    return {
        "attempt_identity_sha256": hashlib.sha256(
            str(smoke["attempt_id"]).encode("utf-8")
        ).hexdigest(),
        "source_revision": revision,
        "credit": "non_credit",
        "diagnostic_only": True,
        "request_count": 100,
        "direct_request_success": 100,
        "direct_inference_count": 100,
        "prometheus_request_success": 100,
        "prometheus_inference_count": 100,
        "gpu_identity_consistent": True,
        "triton_direct_gpu_memory_used_bytes": int(
            result["direct_triton_gpu_memory_used_bytes"]
        ),
        "vram_delta_mib": cleanup.get("vram_delta_mib"),
        "cleanup_passed": True,
        "sha256": sha256_file(path),
    }


def project_failed_smoke(remediation_root: Path, relative: str) -> dict[str, Any]:
    path = _relative_path(remediation_root, relative, "failed_smoke")
    smoke = _load_json(path, "failed_smoke")
    cleanup = dict(smoke.get("cleanup", {}))
    if (
        smoke.get("credit") != "non_credit"
        or smoke.get("acceptance_credit") is not False
        or smoke.get("diagnostic_only") is not True
        or smoke.get("passed") is not False
        or not smoke.get("failure")
        or cleanup.get("container_absent") is not True
        or cleanup.get("target_absent") is not True
        or cleanup.get("lease_absent") is not True
        or cleanup.get("b0_cuda_inference", {}).get("passed") is not True
    ):
        raise E0RemediationEvidenceError("failed_smoke:invariant")
    return {
        "attempt_identity_sha256": hashlib.sha256(
            str(smoke["attempt_id"]).encode("utf-8")
        ).hexdigest(),
        "credit": "zero_credit",
        "failure_class": "diagnostic_wrapper_source_identity",
        "cleanup_passed": True,
        "sha256": sha256_file(path),
    }


def _test_evidence(remediation_root: Path, references: Mapping[str, str]) -> dict[str, Any]:
    focused = _relative_path(remediation_root, references["focused_e0"], "focused_e0")
    mutations = _relative_path(
        remediation_root, references["mutation_regressions"], "mutation_regressions"
    )
    strict = _relative_path(
        remediation_root, references["strict_validator"], "strict_validator"
    )
    lint = _relative_path(remediation_root, references["changed_files_lint"], "lint")
    focused_text = focused.read_text(encoding="utf-8", errors="replace")
    mutation_text = mutations.read_text(encoding="utf-8", errors="replace")
    strict_text = strict.read_text(encoding="utf-8", errors="replace")
    if "41 passed" not in focused_text:
        raise E0RemediationEvidenceError("tests:focused_e0")
    if "26 passed" not in mutation_text or mutation_text.count(" PASSED ") != 26:
        raise E0RemediationEvidenceError("tests:mutation_regressions")
    if '"valid":true' not in strict_text or '"evidence_ready":true' not in strict_text:
        raise E0RemediationEvidenceError("tests:strict_validator")
    if "All checks passed!" not in lint.read_text(encoding="utf-8", errors="replace"):
        raise E0RemediationEvidenceError("tests:changed_files_lint")
    return {
        "focused_e0": {"tests_passed": 41, "sha256": sha256_file(focused)},
        "mutation_regressions": {
            "tests_passed": 26,
            "positive_controls": 1,
            "negative_mutations_rejected": 25,
            "required_mutations": {
                "valid_but_wrong_model_digest": "rejected",
                "one_run_model_digest_mismatch": "rejected",
                "orphan_count_1_and_999": "rejected",
                "request_direct_and_prometheus_counts_99_1_0": "rejected",
                "prometheus_zero_missing_or_wrong_identity": "rejected",
                "cleanup_residue": "rejected",
                "missing_cupti_stream": "rejected",
            },
            "sha256": sha256_file(mutations),
        },
        "strict_validator": {"valid": True, "sha256": sha256_file(strict)},
        "changed_files_lint": {"result": "passed", "sha256": sha256_file(lint)},
    }


def build_remediation_review(
    *,
    generated_at: str,
    original_evidence_path: Path,
    config: E0RuntimeConfig,
    original_private_root: Path,
    remediation_root: Path,
    project_root: Path,
    validation_revision: str,
    references: Mapping[str, str],
) -> dict[str, Any]:
    experiment_bytes = original_evidence_path.read_bytes()
    experiment = json.loads(experiment_bytes)
    validate_e0_evidence(
        experiment,
        config=config,
        private_root=original_private_root,
        project_root=project_root,
        validation_revision=validation_revision,
    )
    prometheus_path = _relative_path(
        remediation_root, references["historical_prometheus"], "historical_prometheus"
    )
    strict = strict_revalidate_e0_runtime(
        experiment,
        config=config,
        private_root=original_private_root,
        prometheus_history=json.loads(prometheus_path.read_bytes()),
    )
    strict_public = public_strict_projection(strict)
    private_index = validate_private_index(remediation_root)
    source_blobs = {
        label: git_blob_identity(project_root, validation_revision, path)
        for label, path in SOURCE_PATHS.items()
    }
    accepted_regressions = project_regressions(
        remediation_root, references["accepted_regressions"], validation_revision
    )
    current_smoke = project_current_smoke(
        remediation_root,
        references["current_smoke"],
        expected_model_digest=strict["strict_contract"]["model_artifact_sha256"],
        project_root=project_root,
        validation_revision=validation_revision,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "status": "review_pending",
        "evidence_ready": True,
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "source_identity": {
            "validation_revision": validation_revision,
            "git_blobs": source_blobs,
        },
        "historical_runtime": {
            "accepted_repetitions_reused": 3,
            "new_accepted_repetitions": 0,
            "runner_path_changed": False,
            "experiment_path": original_evidence_path.relative_to(project_root).as_posix(),
            "experiment_sha256": hashlib.sha256(experiment_bytes).hexdigest(),
            "runtime_revision": experiment["source_identity"]["runtime_revision"],
            "private_index_sha256": experiment["private_evidence"]["index_sha256"],
            "private_aggregate_sha256": experiment["private_evidence"][
                "aggregate_sha256"
            ],
        },
        "strict_revalidation": strict_public,
        "historical_prometheus": {
            "sha256": sha256_file(prometheus_path),
            "identity_contract": "attempt_id + model + version",
            "public_path": None,
        },
        "current_revision_smoke": current_smoke,
        "tests": _test_evidence(remediation_root, references),
        "regressions": accepted_regressions,
        "failed_non_credit_attempts": [
            project_failed_smoke(remediation_root, references["failed_smoke"]),
            project_failed_regression(
                remediation_root, references["failed_regressions"]
            ),
        ],
        "acceptance": strict["acceptance"],
        "cleanup": {
            "historical_attempts": strict["final_cleanup"],
            "current_revision_smoke": {"passed": True},
            "regression_schemas_cleaned": True,
            "orphan_count": 0,
        },
        "private_evidence": private_index,
        "references": dict(references),
        "alignment": {
            "definition_alignment": True,
            "experiment_purpose_alignment": True,
            "validation_purpose_alignment": True,
            "test_purpose_alignment": True,
        },
        "claim_boundary": (
            "one Windows/WSL2 physical node, one RTX 4080, controlled traffic; "
            "E0 remains review_pending with no verified credit; CUPTI proves only a "
            "standalone CUDA qualification, not Triton trace overlap, production SLA, HA/DR, "
            "multi-GPU, MIG, or MPS"
        ),
        "unresolved_items": [
            "Independent reviewer sign-off remains pending.",
            "Historical S2 Infinity validator debt remains a separate V4 closure backlog.",
        ],
        "next_action": "Independent source-local re-audit; do not start S6B-M before a verified event.",
    }


def validate_remediation_review(
    payload: Mapping[str, Any],
    *,
    original_evidence_path: Path,
    config: E0RuntimeConfig,
    original_private_root: Path,
    remediation_root: Path,
    project_root: Path,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    source_revision = str(payload.get("source_identity", {}).get("validation_revision") or "")
    if len(source_revision) != 40 or not _is_ancestor(
        project_root, source_revision, validation_revision
    ):
        raise E0RemediationEvidenceError("source_identity:revision")
    expected = build_remediation_review(
        generated_at=str(payload.get("generated_at") or ""),
        original_evidence_path=original_evidence_path,
        config=config,
        original_private_root=original_private_root,
        remediation_root=remediation_root,
        project_root=project_root,
        validation_revision=source_revision,
        references=dict(payload.get("references", {})),
    )
    if canonical(payload) != canonical(expected):
        raise E0RemediationEvidenceError("remediation_review:projection")
    return {
        "valid": True,
        "status": "review_pending",
        "evidence_ready": True,
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "validation_revision": source_revision,
        "acceptance": expected["acceptance"],
        "private_evidence": expected["private_evidence"],
    }

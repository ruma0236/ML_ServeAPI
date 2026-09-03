from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import ntpath
import os
import re
import shutil
import stat
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

# A production-shaped direct entry must prove isolation before any project
# package can be imported.  The trusted outer sets this flag and launches
# Python with -I -B -S; offline internal review remains explicitly non-authoritative.
_ENTRY_BOOTSTRAP_SCOPE = os.environ.get("EVM_PRE_R8_REVIEW_ENTRY_AUTHORITY_SCOPE", "")
if __name__ == "__main__":
    if sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1 or sys.flags.no_site != 1:
        raise SystemExit("review_requires_python_I_B_S_before_project_import")
    if _ENTRY_BOOTSTRAP_SCOPE != "trusted_outer_internal_non_authoritative":
        raise SystemExit("review_requires_trusted_internal_non_authoritative_outer")
    raise SystemExit("review_os_bound_outer_capability_unprovisioned")

SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _project_path in (SCRIPT_PROJECT_ROOT, SCRIPT_PROJECT_ROOT / "src"):
    if str(_project_path) not in sys.path:
        sys.path.append(str(_project_path))

from evm.scale_validation import phase_b2_r7s5_admission as admission  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_ci as ci  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_dual_clock as dual_clock  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_etw as etw  # noqa: E402
from evm.scale_validation import phase_b2_r7s6_evidence as evidence  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_gate as gate  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_reservation as reservation  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_windows_wsl as windows_wsl  # noqa: E402
from evm.scale_validation.phase_b2_r7s3_process import (  # noqa: E402
    DEFAULT_MAX_STREAM_BYTES,
    ProcessContainmentFailure,
    TimeoutContract,
    WindowsJobProcessRunner,
)
from evm.scale_validation.phase_b2_r7s4_handle_io import (  # noqa: E402
    HandleBoundIoError,
    validate_strict_windows_leaf,
)


SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.review-publisher.v3"
VALIDATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.code-validation.v3"
COMMAND_EVIDENCE_SCHEMA = f"{VALIDATION_SCHEMA}.command-evidence"
VALIDATION_PUBLICATION_INDEX_LEAF = "code-validation-publication-index.json"
VALIDATION_SUMMARY_LEAF = "code-validation-summary.json"
REVIEWER_PENDING_REPORT_LEAF = "reviewer-pending-report.json"
NO_GO_SEAL_LEAF = "no-go-seal.json"
REVIEW_PARENT_SCHEMA = f"{SCHEMA}.evidence-parent.v1"
PUBLISHER_CHILD_WRAPPER_TIMEOUT_SECONDS = 120.0
PUBLISHER_CHILD_RESIDUAL_REPOLL_SECONDS = 120.0
PUBLISHER_CHILD_STREAM_DRAIN_SECONDS = 30.0
PUBLISHER_CHILD_OBSERVATION_SCOPE = (
    "publisher_git_and_process_identity_children_windows_job_accounted_no_kill"
)
_PUBLISHER_CHILD_EXECUTIONS: list[dict[str, Any]] = []
_PUBLISHER_FAILURE_CONTEXT: dict[str, Any] = {}
EXPECTED_PRIMARY_PUBLISHER_CHILD_COUNT = 240
EXPECTED_TERMINAL_PUBLISHER_CHILD_COUNT = 252
_IMPORT_ACTIVE_UNTRACKED_SUFFIXES = frozenset({".py", ".pyc", ".pyo", ".pyd", ".pth", ".so"})
_IMPORT_ACTIVE_TOOL_CONFIG_BASENAMES = frozenset(
    {
        ".pytest.ini",
        ".ruff.toml",
        "pyproject.toml",
        "pytest.ini",
        "ruff.toml",
        "setup.cfg",
        "tox.ini",
    }
)
_IGNORED_IMPORT_ACTIVE_PATHSPECS = tuple(
    f":(icase,glob)**/*{suffix}" for suffix in sorted(_IMPORT_ACTIVE_UNTRACKED_SUFFIXES)
) + tuple(f":(icase,glob)**/{name}" for name in sorted(_IMPORT_ACTIVE_TOOL_CONFIG_BASENAMES))
REQUIRED_VALIDATION_COMMANDS = frozenset(
    {
        "kubectl-client-version-1.34.1",
        "r7s5-focused-pytest-py311",
        "full-general-pytest-py311",
        "pinned-host-pytest-py313",
        "ruff-check-0.12.2",
        "ruff-format-check-0.12.2",
        "py-compile-py311",
        "powershell-ast",
        "git-diff-check",
        "ci-manifest-validator",
        "ci-active-workflow-required-rejection",
        "ci-mutation-pytest",
    }
)
REQUIRED_ZERO_LIVE_CALLS = frozenset(
    {
        "docker",
        "compose",
        "kubernetes",
        "wsl",
        "etw",
        "service_lifecycle",
        "restore_only",
        "dual_collector",
        "fresh_phase_b2",
        "r8",
        "automatic_retry",
        "force_kill",
        "full_stack_3180",
        "q0",
        "calibration_54",
        "matrix_78",
        "integrated_v4",
    }
)
R6_PROJECTION = {
    "schema": gate.R6_SCHEMA,
    "decision": "manual_intervention_required",
    "credit": "zero_credit",
    "go": False,
    "completion_marker_created": False,
    "acceptance_credit": False,
    "success_marker_created": False,
    "phase_b2_executed": False,
    "r6_restore_only": dict(gate.R6_RESTORE_ONLY),
}

# These are out-of-band checkpoints from the already sealed, read-only evidence
# trees.  The publisher must not accept caller-selected substitutes or silently
# repin a changed historical directory.
SEALED_HISTORICAL_DIRECTORIES: dict[str, dict[str, Any]] = {
    "r7s4": {
        "root": (
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
            "private/s8-v4/x1-clock-phase-b2-pre-r8-r7s4-hardening/"
            "x1-clock-phase-b2-pre-r8-r7s4-hardening-20260902T032634Z-"
            "0f9a3b9-ng-d1571edd"
        ),
        "file_count": 5,
        "directory_count": 0,
        "entry_count": 5,
        "total_bytes": 23_942,
        "inventory_sha256": "f50ae2fba22c41e1004a4b0d6e57258c54d57a2d43bcdb10b3b4989aa1db363b",
        "tree_inventory_sha256": (
            "3a3637e621e077b8cb24195e9500950adc24fc975e0e8f60834eead3b5a0ac06"
        ),
    },
    "r6": {
        "root": (
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
            "private/s8-v4/x1-clock-phase-b2-r6-compose-rca/"
            "x1-clock-phase-b2-r6-compose-rca-20260901T024007Z-167cb01"
        ),
        "file_count": 56,
        "directory_count": 1,
        "entry_count": 57,
        "total_bytes": 443_844,
        "inventory_sha256": "5e84d0ee31bbdb71569719215804ddc97116c2179dc5654e83143ef100ed8ace",
        "tree_inventory_sha256": (
            "fb9abe72c359f2bd974fbc31a70277d587dcdce55404490365d17be567711c7b"
        ),
    },
}
SEALED_ETW_AMENDMENT = {
    "path": (
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
        "private/s8-v4/x1-clock-phase-b2-failure-seals/"
        "x1-clock-phase-b2-r3-failure-seal-20260831T135958Z-0a68addf/"
        "etw-contract-amendment.json"
    ),
    "bytes": 2_806,
    "sha256": "71ddc50a2a91f707b8183a19c87f490bdad8421ab18446dceb21622bc3439715",
}
REQUIRED_SELECTED_SOURCE_PATHS = frozenset(
    {
        ".gitattributes",
        "enterprise-vision-mlops/ci/pre-r8-r7s5-test-lanes.json",
        "enterprise-vision-mlops/docs/status/2026-08-24-s8-v4-progress-ledger.jsonl",
        "enterprise-vision-mlops/scripts/dev/publish_pre_r8_r7s5_review.py",
        "enterprise-vision-mlops/scripts/dev/invoke_pre_r8_r7s7_review.ps1",
        "enterprise-vision-mlops/scripts/dev/invoke_pre_r8_r7s7_windows_qualification.ps1",
        "enterprise-vision-mlops/scripts/dev/pre_r8_r7s7_windows_fixture.py",
        "enterprise-vision-mlops/scripts/dev/prepare_pre_r8_r7s7_windows_qualification.py",
        "enterprise-vision-mlops/scripts/dev/qualify_pre_r8_r7s7_windows.py",
        "enterprise-vision-mlops/scripts/dev/run_pre_r8_r7s5_validation.py",
        "enterprise-vision-mlops/scripts/dev/validate_pre_r8_r7s4_ci_bootstrap.py",
        "enterprise-vision-mlops/scripts/dev/validate_pre_r8_r7s5_ci.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s3_process.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s4_authority.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_admission.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_ci.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_dual_clock.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_etw.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_evidence.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_gate.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_reservation.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_windows_wsl.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s6_evidence.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s7_admission.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s7_qualification_work_order.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s1.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s1_validator.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s3_job_capability.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s3_process.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s4_authority.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_admission.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_ci.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_dual_clock.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_etw.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_evidence.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_gate.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_reservation.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_windows_wsl.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s6_evidence.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s7_admission.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s7_qualification_work_order.py",
        "enterprise-vision-mlops/tests/test_prepare_pre_r8_r7s7_windows_qualification.py",
        "enterprise-vision-mlops/tests/test_publish_pre_r8_r7s5_review.py",
        "enterprise-vision-mlops/tests/test_run_pre_r8_r7s5_validation.py",
        "enterprise-vision-mlops/tests/test_pre_r8_r7s4_ci_bootstrap.py",
        "enterprise-vision-mlops/tests/test_qualify_pre_r8_r7s7_windows.py",
        "enterprise-vision-mlops/tests/test_scenario_workload_production.py",
        "enterprise-vision-mlops/tests/test_task_queue_process_safety.py",
    }
)


class ReviewPublisherError(RuntimeError):
    pass


class ReplayConsumeAdapter(Protocol):
    """External atomic consume boundary. Implementations must be independent WORM."""

    authority_scope: str
    authority_verified: bool
    worm_storage: bool

    def consume_once(self, replay_key: str, summary_sha256: str) -> Mapping[str, Any]: ...


def consume_replay_once(
    adapter: ReplayConsumeAdapter | None,
    *,
    replay_key: str,
    summary_sha256: str,
) -> dict[str, Any]:
    """Public production boundary: fail closed until a real WORM adapter is wired.

    Caller-supplied Python objects cannot establish external authority.  Keep this
    entry point closed even when such an object advertises production-shaped
    attributes; the production adapter must be provisioned outside this module.
    """

    del adapter, replay_key, summary_sha256
    raise ReviewPublisherError("production_external_worm_adapter_unprovisioned")


def _consume_replay_once_for_test(
    adapter: ReplayConsumeAdapter | None,
    *,
    replay_key: str,
    summary_sha256: str,
) -> dict[str, Any]:
    """Exercise receipt validation without producing authority-bearing output."""

    if (
        adapter is None
        or getattr(adapter, "authority_scope", None) != "production_external_worm"
        or getattr(adapter, "authority_verified", None) is not True
        or getattr(adapter, "worm_storage", None) is not True
    ):
        raise ReviewPublisherError("test_replay_worm_adapter_contract_required")
    receipt = adapter.consume_once(
        _hex64(replay_key, "replay_key"),
        _hex64(summary_sha256, "summary_sha256"),
    )
    if (
        not isinstance(receipt, Mapping)
        or set(receipt)
        != {
            "status",
            "replay_key",
            "summary_sha256",
            "authority_scope",
            "worm_storage",
        }
        or receipt.get("status") != "consumed"
        or receipt.get("replay_key") != replay_key
        or receipt.get("summary_sha256") != summary_sha256
        or receipt.get("authority_scope") != "production_external_worm"
        or receipt.get("worm_storage") is not True
    ):
        raise ReviewPublisherError("test_replay_consume_receipt_invalid")
    return {
        "schema": f"{SCHEMA}.test-only-replay-consume-observation.v1",
        "authority_scope": "test_only_internal_non_authoritative",
        "authority_verified": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
        "adapter_receipt": dict(receipt),
    }


def validation_replay_key(
    *,
    validation_run_uuid: str,
    validation_attempt_uuid: str,
    handoff_challenge_sha256: str,
    work_order_sha256: str,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "validation_run_uuid": validation_run_uuid,
                "validation_attempt_uuid": validation_attempt_uuid,
                "handoff_challenge_sha256": _hex64(handoff_challenge_sha256, "handoff_challenge"),
                "work_order_sha256": _hex64(work_order_sha256, "work_order"),
            }
        )
    ).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hex64(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ReviewPublisherError(f"{label}_sha256_invalid")
    return value


def _hex40(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ReviewPublisherError(f"{label}_git_identity_invalid")
    return value


def _executable_stat_identity(observed: os.stat_result) -> dict[str, int]:
    """Return metadata that must remain stable while an executable is hashed."""

    return {
        "device": int(observed.st_dev),
        "file_id": int(observed.st_ino),
        # Windows' stat-by-path and fstat-on-handle permission bits are not
        # necessarily identical for the same file.  File type is stable and
        # is the security-relevant portion after the regular-file check.
        "file_type": int(stat.S_IFMT(observed.st_mode)),
        "link_count": int(observed.st_nlink),
        "bytes": int(observed.st_size),
        "mtime_ns": int(observed.st_mtime_ns),
        "ctime_ns": int(observed.st_ctime_ns),
        "attributes": int(getattr(observed, "st_file_attributes", 0)),
        "reparse_tag": int(getattr(observed, "st_reparse_tag", 0)),
    }


def _require_regular_non_reparse_executable(
    observed: os.stat_result,
    *,
    label: str,
) -> None:
    identity = _executable_stat_identity(observed)
    if (
        not stat.S_ISREG(observed.st_mode)
        or identity["attributes"] & 0x400
        or identity["reparse_tag"] != 0
        or identity["link_count"] < 1
        or identity["bytes"] <= 0
    ):
        raise ReviewPublisherError(f"{label}_regular_non_reparse_file_required")


def validate_independent_executable_pin(
    path: Path,
    expected_sha256: str,
    *,
    label: str,
) -> dict[str, Any]:
    """Validate a caller/work-order pin without executing the referenced file."""

    if not path.is_absolute():
        raise ReviewPublisherError(f"{label}_path_must_be_absolute")
    try:
        unresolved = os.lstat(path)
        _require_regular_non_reparse_executable(unresolved, label=label)
        resolved = path.resolve(strict=True)
        before = os.lstat(resolved)
        _require_regular_non_reparse_executable(before, label=label)
    except OSError as exc:
        raise ReviewPublisherError(f"{label}_path_unavailable") from exc
    before_identity = _executable_stat_identity(before)
    if _executable_stat_identity(unresolved) != before_identity:
        raise ReviewPublisherError(f"{label}_leaf_identity_changed_during_resolution")
    expected = _hex64(expected_sha256, label)
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as stream:
            opened_identity = _executable_stat_identity(os.fstat(stream.fileno()))
            if opened_identity != before_identity:
                raise ReviewPublisherError(f"{label}_identity_changed_before_sha256")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            opened_after_identity = _executable_stat_identity(os.fstat(stream.fileno()))
        after_identity = _executable_stat_identity(os.lstat(resolved))
    except OSError as exc:
        raise ReviewPublisherError(f"{label}_sha256_measurement_failed") from exc
    if opened_after_identity != before_identity or after_identity != before_identity:
        raise ReviewPublisherError(f"{label}_identity_changed_during_sha256")
    actual = digest.hexdigest()
    if actual != expected:
        raise ReviewPublisherError(f"{label}_sha256_mismatch")
    return {
        "path": str(resolved),
        "bytes": before_identity["bytes"],
        "sha256": actual,
        "identity": before_identity,
        "identity_stable_across_sha256": True,
        "hardlink_allowed_with_launch_lock": True,
        "launch_lock_contract": "FILE_SHARE_READ_only_through_CreateProcessW",
        "source": "independent_cli_work_order_pin",
        "executed_during_pin_validation": False,
    }


def _comparable_windows_path(value: str | os.PathLike[str]) -> str:
    """Normalize plain and extended-length Win32 paths to one comparison form."""

    normalized = os.fspath(value)
    if normalized.startswith("\\\\?\\UNC\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]
    return ntpath.normcase(ntpath.normpath(normalized))


def review_parent_commitment(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    attributes = int(getattr(os.lstat(resolved), "st_file_attributes", 0))
    if not resolved.is_dir() or attributes & 0x400:
        raise ReviewPublisherError("review_parent_plain_directory_required")
    payload = {
        "schema": REVIEW_PARENT_SCHEMA,
        "normalized_path": _comparable_windows_path(resolved),
    }
    return {
        **payload,
        "sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def require_disjoint_review_batch_namespaces(
    parent: Path,
    primary_leaf: str,
    primary_run_uuid: str,
    postpublication_leaf: str,
    postpublication_run_uuid: str,
) -> None:
    """Reject every cross-batch alias/existing path before either publication."""

    primary = evidence.planned_parent_directory_leaves(primary_leaf, primary_run_uuid)
    postpublication = evidence.planned_parent_directory_leaves(
        postpublication_leaf,
        postpublication_run_uuid,
    )
    primary_paths = {
        _comparable_windows_path(parent / leaf): role for role, leaf in primary.items()
    }
    postpublication_paths = {
        _comparable_windows_path(parent / leaf): role for role, leaf in postpublication.items()
    }
    overlap = set(primary_paths).intersection(postpublication_paths)
    if overlap:
        path = sorted(overlap)[0]
        raise ReviewPublisherError(
            "review_primary_postpublication_namespace_collision:"
            f"{primary_paths[path]}:{postpublication_paths[path]}"
        )
    for batch_role, planned in (
        ("primary", primary),
        ("postpublication", postpublication),
    ):
        for directory_role, leaf in planned.items():
            if os.path.lexists(parent / leaf):
                raise ReviewPublisherError(
                    f"review_batch_namespace_path_exists:{batch_role}:{directory_role}"
                )


def validate_review_parent_gate(
    requested: Path,
    *,
    expected_path: Path,
    expected_sha256: str,
    output_leaf: str,
    forbidden_roots: Sequence[Path],
) -> tuple[Path, dict[str, Any]]:
    try:
        leaf = validate_strict_windows_leaf(output_leaf)
    except Exception as exc:
        raise ReviewPublisherError("review_output_leaf_invalid") from exc
    requested_resolved = requested.resolve(strict=True)
    expected_resolved = expected_path.resolve(strict=True)
    if _comparable_windows_path(requested_resolved) != _comparable_windows_path(expected_resolved):
        raise ReviewPublisherError("review_parent_path_mismatch")
    commitment = review_parent_commitment(requested_resolved)
    if _hex64(expected_sha256, "review_parent") != commitment["sha256"]:
        raise ReviewPublisherError("review_parent_sha256_mismatch")
    target = requested_resolved / leaf
    for root in forbidden_roots:
        root_resolved = root.resolve(strict=True)
        if (
            target == root_resolved
            or root_resolved in target.parents
            or target in root_resolved.parents
        ):
            raise ReviewPublisherError("review_output_target_overlaps_protected_root")
    return requested_resolved, commitment


def read_json_mapping(path: Path, label: str) -> Mapping[str, Any]:
    raw = path.read_bytes()

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ReviewPublisherError(f"{label}_duplicate_json_key:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewPublisherError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict):
        raise ReviewPublisherError(f"{label}_mapping_required")
    canonical_json_bytes(value)
    return value


def _redacted_publisher_child_evidence(
    value: Mapping[str, Any],
    *,
    purpose: str,
    clean: bool,
    environment_commitment: Mapping[str, Any],
    expected_executable_sha256: str,
    secret_like_output_detected: bool,
) -> dict[str, Any]:
    evidence = dict(value)
    for label in ("stdout", "stderr"):
        stream = evidence.pop(label, "")
        if not isinstance(stream, str):
            stream = ""
        raw = stream.encode("utf-8")
        evidence[f"{label}_bytes"] = len(raw)
        evidence[f"{label}_sha256"] = hashlib.sha256(raw).hexdigest()
        evidence[f"{label}_persisted"] = False
    return {
        "purpose": purpose,
        "clean_containment_verified": clean,
        "environment_commitment": dict(environment_commitment),
        "expected_executable_sha256": expected_executable_sha256,
        "secret_like_output_detected": secret_like_output_detected,
        "process": evidence,
    }


def _append_publisher_child_execution(value: Mapping[str, Any]) -> dict[str, Any]:
    entry = {
        "execution_sequence": len(_PUBLISHER_CHILD_EXECUTIONS) + 1,
        **dict(value),
    }
    _PUBLISHER_CHILD_EXECUTIONS.append(entry)
    return entry


def _replace_publisher_child_execution(
    target: dict[str, Any], replacement: Mapping[str, Any]
) -> None:
    sequence = target["execution_sequence"]
    target.clear()
    target.update({"execution_sequence": sequence, **dict(replacement)})


def _publisher_child_clean(
    outcome: Any,
    *,
    expected_return_code: int,
    expected_executable_sha256: str,
) -> bool:
    strict_bools = {
        "timed_out": False,
        "cancelled": False,
        "manual_intervention_required": False,
        "stdout_drained": True,
        "stderr_drained": True,
        "streams_drained": True,
        "active_process_zero": True,
        "identity_coverage_complete": True,
        "safe_for_followup": expected_return_code == 0,
        "stdout_capture_overflow": False,
        "stderr_capture_overflow": False,
    }
    if any(
        type(getattr(outcome, key, None)) is not bool or getattr(outcome, key) is not expected
        for key, expected in strict_bools.items()
    ):
        return False
    strict_ints = {
        "return_code": expected_return_code,
        "final_active_process_count": 0,
        "forced_termination_attempts": 0,
        "job_limit_flags": 0,
        "stream_capture_limit_bytes": DEFAULT_MAX_STREAM_BYTES,
    }
    if any(
        type(getattr(outcome, key, None)) is not int or getattr(outcome, key) != expected
        for key, expected in strict_ints.items()
    ):
        return False
    if getattr(outcome, "residual_pids", None) != () or getattr(outcome, "errors", None) != ():
        return False
    executable_identity = getattr(outcome, "executable_identity", None)
    if (
        not isinstance(executable_identity, dict)
        or executable_identity.get("sha256") != expected_executable_sha256
        or executable_identity.get("expected_sha256") != expected_executable_sha256
        or executable_identity.get("pin_required") is not True
        or executable_identity.get("pin_match") is not True
        or executable_identity.get("measurement_scope") != "immediately_before_CreateProcessW"
        or executable_identity.get("handle_lock_held_through_create") is not True
        or executable_identity.get("handle_lock_share_mode") != "FILE_SHARE_READ_only"
        or executable_identity.get("handle_lock_inheritable") is not False
        or executable_identity.get("pre_kernel_create_gate_required") is not True
        or executable_identity.get("pre_kernel_create_gate_passed") is not True
        or type(executable_identity.get("pre_kernel_create_gate_invocations")) is not int
        or executable_identity["pre_kernel_create_gate_invocations"] != 1
        or type(executable_identity.get("pre_kernel_remaining_seconds")) not in {int, float}
        or not math.isfinite(float(executable_identity["pre_kernel_remaining_seconds"]))
        or type(executable_identity.get("pre_kernel_required_seconds")) not in {int, float}
        or not math.isfinite(float(executable_identity["pre_kernel_required_seconds"]))
        or executable_identity["pre_kernel_required_seconds"] <= 0
        or executable_identity["pre_kernel_remaining_seconds"]
        < executable_identity["pre_kernel_required_seconds"]
    ):
        return False
    command = getattr(outcome, "command", None)
    raw_run_uuid = getattr(outcome, "run_uuid", None)
    try:
        parsed_run_uuid = uuid.UUID(str(raw_run_uuid))
    except (ValueError, TypeError, AttributeError):
        return False
    if (
        not isinstance(command, tuple)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
        or not Path(command[0]).is_absolute()
        or not isinstance(raw_run_uuid, str)
        or parsed_run_uuid.version != 4
        or str(parsed_run_uuid) != raw_run_uuid
    ):
        return False
    if os.path.normcase(os.path.abspath(str(executable_identity.get("path", "")))) != (
        os.path.normcase(os.path.abspath(command[0]))
    ):
        return False
    for label in ("stdout", "stderr"):
        stream = getattr(outcome, label, None)
        total_bytes = getattr(outcome, f"{label}_total_bytes", None)
        if (
            not isinstance(stream, str)
            or type(total_bytes) is not int
            or total_bytes != len(stream.encode("utf-8"))
        ):
            return False
    identities = getattr(outcome, "identities", None)
    events = getattr(outcome, "events", None)
    accounting = getattr(outcome, "accounting", None)
    if (
        not isinstance(identities, tuple)
        or not identities
        or not isinstance(events, tuple)
        or not events
        or not isinstance(accounting, tuple)
        or not accounting
    ):
        return False
    stable_keys = {
        (getattr(identity, "pid", None), getattr(identity, "creation_time_ns", None))
        for identity in identities
    }
    if len(stable_keys) != len(identities):
        return False
    required_events = {
        "job_created": None,
        "root_created_suspended": "root",
        "job_membership_verified": "root",
        "root_resumed": "root",
        "active_process_count_zero": None,
        "streams_drained": None,
    }
    events_by_name: dict[str, list[Any]] = {
        name: [event for event in events if getattr(event, "event", None) == name]
        for name in required_events
    }
    if any(len(matches) != 1 for matches in events_by_name.values()):
        return False
    root_pid = getattr(events_by_name["root_created_suspended"][0], "pid", None)
    if type(root_pid) is not int or root_pid <= 0:
        return False
    for name, expected_pid in required_events.items():
        observed_pid = getattr(events_by_name[name][0], "pid", None)
        if observed_pid != (root_pid if expected_pid == "root" else None):
            return False
    root_identities = [
        identity for identity in identities if getattr(identity, "pid", None) == root_pid
    ]
    if len(root_identities) != 1:
        return False
    root_identity = root_identities[0]
    if (
        getattr(root_identity, "run_uuid", None) != raw_run_uuid
        or os.path.normcase(os.path.abspath(str(getattr(root_identity, "image", ""))))
        != os.path.normcase(os.path.abspath(command[0]))
        or not any(
            getattr(event, "event", None) == "identity_observed"
            and getattr(event, "pid", None) == root_pid
            and getattr(event, "sequence", None)
            == getattr(root_identity, "observed_sequence", None)
            for event in events
        )
    ):
        return False
    identity_pids = {getattr(identity, "pid", None) for identity in identities}
    prior_total = 0
    prior_terminated = 0
    prior_sequence = 0
    prior_monotonic = 0
    for snapshot in accounting:
        sequence = getattr(snapshot, "sequence", None)
        monotonic_ns = getattr(snapshot, "monotonic_ns", None)
        total = getattr(snapshot, "total_processes", None)
        active = getattr(snapshot, "active_processes", None)
        terminated = getattr(snapshot, "total_terminated_processes", None)
        active_pids = getattr(snapshot, "active_pids", None)
        if (
            type(sequence) is not int
            or sequence <= prior_sequence
            or type(monotonic_ns) is not int
            or monotonic_ns < prior_monotonic
            or type(total) is not int
            or total < prior_total
            or type(active) is not int
            or active < 0
            or not isinstance(active_pids, tuple)
            or active != len(active_pids)
            or any(
                type(pid) is not int or pid <= 0 or pid not in identity_pids for pid in active_pids
            )
            or type(terminated) is not int
            or terminated < prior_terminated
            or active + terminated > total
        ):
            return False
        prior_sequence = sequence
        prior_monotonic = monotonic_ns
        prior_total = total
        prior_terminated = terminated
    final = accounting[-1]
    return bool(
        getattr(final, "active_processes", None) == 0
        and getattr(final, "active_pids", None) == ()
        and len(identities) == getattr(final, "total_processes", None)
    )


def _run_publisher_child(
    argv: Sequence[str],
    *,
    cwd: Path,
    purpose: str,
    expected_return_code: int = 0,
    expected_executable_sha256: str | None = None,
) -> Any:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ReviewPublisherError(f"publisher_child_arguments_invalid:{purpose}")
    executable = Path(argv[0])
    if not executable.is_absolute():
        located = shutil.which(argv[0])
        if located is None:
            raise ReviewPublisherError(f"publisher_child_executable_not_found:{purpose}")
        executable = Path(located)
    try:
        executable = executable.resolve(strict=True)
    except OSError as exc:
        raise ReviewPublisherError(f"publisher_child_executable_invalid:{purpose}") from exc
    command = (str(executable), *argv[1:])
    observed_executable_sha256 = sha256_file(executable)
    if expected_executable_sha256 is None:
        expected_executable_sha256 = observed_executable_sha256
    else:
        expected_executable_sha256 = _hex64(
            expected_executable_sha256, "publisher_child_expected_executable"
        )
        if observed_executable_sha256 != expected_executable_sha256:
            raise ReviewPublisherError(f"publisher_child_independent_pin_mismatch:{purpose}")
    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner

    child_environment = validation_runner.build_child_environment(
        SCRIPT_PROJECT_ROOT,
        (str(executable),),
    )
    contract = TimeoutContract(
        kubectl_timeout_seconds=8.0,
        wrapper_timeout_seconds=PUBLISHER_CHILD_WRAPPER_TIMEOUT_SECONDS,
        restore_deadline_seconds=(
            PUBLISHER_CHILD_WRAPPER_TIMEOUT_SECONDS
            + PUBLISHER_CHILD_RESIDUAL_REPOLL_SECONDS
            + PUBLISHER_CHILD_STREAM_DRAIN_SECONDS
            + 10.0
        ),
        residual_repoll_seconds=PUBLISHER_CHILD_RESIDUAL_REPOLL_SECONDS,
        stream_drain_seconds=PUBLISHER_CHILD_STREAM_DRAIN_SECONDS,
    )
    pending = _append_publisher_child_execution(
        _redacted_publisher_child_evidence(
            {
                "name": f"r7s6-publisher-{purpose}",
                "child_launch_attempt_registered": True,
                "child_launch_boundary_crossed": "unproven",
                "terminal_process_evidence_recorded": False,
                "command_sha256": hashlib.sha256(canonical_json_bytes(list(command))).hexdigest(),
                "command_persisted": False,
                "forced_termination_attempts": 0,
                "automatic_retry_count": 0,
            },
            purpose=purpose,
            clean=False,
            environment_commitment=child_environment.commitment,
            expected_executable_sha256=expected_executable_sha256,
            secret_like_output_detected=False,
        )
    )
    try:
        outcome = WindowsJobProcessRunner(contract).run(
            command,
            name=f"r7s6-publisher-{purpose}",
            cwd=cwd,
            env=child_environment.values,
            run_uuid=None,
            expected_executable_sha256=expected_executable_sha256,
        )
    except BaseException as exc:
        if isinstance(exc, ProcessContainmentFailure):
            try:
                sanitized, secret_detected = validation_runner._sanitize_for_evidence(
                    exc.to_dict(), child_environment.secret_values
                )
            except BaseException as sanitization_error:
                sanitized = {
                    "terminal_process_evidence_recorded": False,
                    "sanitization_exception_type": (
                        f"{type(sanitization_error).__module__}."
                        f"{type(sanitization_error).__qualname__}"
                    ),
                    "exception_message_disclosed": False,
                    "forced_termination_attempts": 0,
                    "automatic_retry_count": 0,
                }
                secret_detected = True
        else:
            sanitized = {
                "child_launch_boundary_crossed": "unproven",
                "terminal_process_evidence_recorded": False,
                "exception_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "exception_message_disclosed": False,
                "forced_termination_attempts": 0,
                "automatic_retry_count": 0,
            }
            secret_detected = False
        _replace_publisher_child_execution(
            pending,
            _redacted_publisher_child_evidence(
                sanitized,
                purpose=purpose,
                clean=False,
                environment_commitment=child_environment.commitment,
                expected_executable_sha256=expected_executable_sha256,
                secret_like_output_detected=secret_detected,
            ),
        )
        if isinstance(exc, ProcessContainmentFailure):
            raise ReviewPublisherError(f"publisher_child_containment_failed:{purpose}") from exc
        raise
    sanitized, secret_detected = validation_runner._sanitize_for_evidence(
        outcome.to_dict(), child_environment.secret_values
    )
    clean = not secret_detected and _publisher_child_clean(
        outcome,
        expected_return_code=expected_return_code,
        expected_executable_sha256=expected_executable_sha256,
    )
    _replace_publisher_child_execution(
        pending,
        _redacted_publisher_child_evidence(
            sanitized,
            purpose=purpose,
            clean=clean,
            environment_commitment=child_environment.commitment,
            expected_executable_sha256=expected_executable_sha256,
            secret_like_output_detected=secret_detected,
        ),
    )
    if not clean:
        raise ReviewPublisherError(f"publisher_child_not_cleanly_contained:{purpose}")
    return outcome


def publisher_child_containment_observation(
    *, terminal: bool = False, expected_purposes: Sequence[str] | None = None
) -> dict[str, Any]:
    children = json.loads(json.dumps(_PUBLISHER_CHILD_EXECUTIONS))
    sequence_complete = [item.get("execution_sequence") for item in children] == list(
        range(1, len(children) + 1)
    )
    observed_purposes = [item.get("purpose") for item in children]
    purpose_plan_exact = (
        observed_purposes == list(expected_purposes) if expected_purposes is not None else None
    )
    all_clean = (
        bool(children)
        and sequence_complete
        and all(item.get("clean_containment_verified") is True for item in children)
    )
    if expected_purposes is not None:
        all_clean = all_clean and purpose_plan_exact is True
    return {
        "schema": f"{SCHEMA}.publisher-child-containment-summary",
        "observation_scope": PUBLISHER_CHILD_OBSERVATION_SCOPE,
        "wrapper_timeout_seconds": PUBLISHER_CHILD_WRAPPER_TIMEOUT_SECONDS,
        "residual_repoll_seconds": PUBLISHER_CHILD_RESIDUAL_REPOLL_SECONDS,
        "stream_drain_seconds": PUBLISHER_CHILD_STREAM_DRAIN_SECONDS,
        "child_count": len(children),
        "execution_sequence_complete": sequence_complete,
        "expected_purpose_count": (
            len(expected_purposes) if expected_purposes is not None else None
        ),
        "expected_purpose_plan_sha256": (
            hashlib.sha256(canonical_json_bytes(list(expected_purposes))).hexdigest()
            if expected_purposes is not None
            else None
        ),
        "purpose_plan_exact": purpose_plan_exact,
        "children": children,
        "subprocess_timeout_force_kill_calls": 0,
        "terminate_job_object_calls": 0,
        "kill_on_job_close": False,
        "automatic_retry_count": 0,
        "all_children_cleanly_contained": all_clean,
        "terminal_observation": terminal,
    }


def publisher_child_containment_summary(
    *, expected_purposes: Sequence[str] | None = None
) -> dict[str, Any]:
    observation = publisher_child_containment_observation(
        terminal=False, expected_purposes=expected_purposes
    )
    if observation["all_children_cleanly_contained"] is not True:
        raise ReviewPublisherError("publisher_child_containment_summary_not_clean")
    return observation


def _git_child_purpose(
    subcommand: str, *, binary: bool = False, purpose_context: str | None = None
) -> str:
    suffix = f"git-{subcommand}-{'binary' if binary else 'text'}"
    return f"{purpose_context}:{suffix}" if purpose_context else suffix


def _untracked_purpose_plan(
    *, reject_import_active: bool, purpose_context: str | None = None
) -> list[str]:
    ordinary_context = (
        f"{purpose_context}:ordinary-untracked" if purpose_context is not None else None
    )
    result = [_git_child_purpose("ls-files", binary=True, purpose_context=ordinary_context)]
    if reject_import_active:
        ignored_context = (
            f"{purpose_context}:ignored-import-active" if purpose_context is not None else None
        )
        result.append(_git_child_purpose("ls-files", binary=True, purpose_context=ignored_context))
    return result


def _git_snapshot_purpose_plan() -> list[str]:
    return [
        _git_child_purpose("rev-parse"),
        _git_child_purpose("rev-parse"),
        _git_child_purpose("branch"),
        _git_child_purpose("rev-parse"),
        _git_child_purpose("ls-remote"),
        _git_child_purpose("status"),
    ]


def _isolated_repository_purpose_plan() -> list[str]:
    return [
        _git_child_purpose("rev-parse"),
        _git_child_purpose("rev-parse"),
        _git_child_purpose("status"),
    ]


def _live_command_plan_purpose_plan(command_names: Sequence[str]) -> list[str]:
    purposes: list[str] = []
    for command_name in command_names:
        for child_kind, phase_kind in (
            ("tool-version", "tool_version"),
            ("runtime-version", "runtime_version"),
        ):
            child_name = f"{child_kind}-{command_name}"
            before_context = f"live-command-plan:before:{child_name}"
            purposes.extend(
                _untracked_purpose_plan(
                    reject_import_active=True,
                    purpose_context=before_context,
                )
            )
            purposes.append(f"live-command-plan:command_plan:{command_name}:{phase_kind}")
    return purposes


def _selected_source_purpose_plan() -> list[str]:
    purposes = [_git_child_purpose("diff")]
    for _relative in sorted(REQUIRED_SELECTED_SOURCE_PATHS):
        purposes.extend(
            (
                _git_child_purpose("rev-parse"),
                _git_child_purpose("hash-object"),
                _git_child_purpose("cat-file"),
            )
        )
    return purposes


def _expected_primary_publisher_purpose_plan(
    *, token: Mapping[str, Any], validation: Mapping[str, Any]
) -> list[str]:
    command_plan_value = validation.get("command_plan")
    commands = (
        command_plan_value.get("commands") if isinstance(command_plan_value, Mapping) else None
    )
    if not isinstance(commands, list) or any(
        not isinstance(item, Mapping) or not isinstance(item.get("name"), str) for item in commands
    ):
        raise ReviewPublisherError("publisher_expected_command_purpose_plan_unavailable")
    process_order = (
        token.get("publisher_runtime"),
        token.get("publisher_parent"),
        token.get("codex"),
    )
    process_purposes: list[str] = []
    for item in process_order:
        process = item.get("process") if isinstance(item, Mapping) else None
        pid = process.get("pid") if isinstance(process, Mapping) else None
        if type(pid) is not int or pid <= 0:
            raise ReviewPublisherError("publisher_expected_process_identity_purpose_unavailable")
        process_purposes.append(f"process-identity-{pid}")
    purposes = [
        *_untracked_purpose_plan(reject_import_active=True),
        *_git_snapshot_purpose_plan(),
        *_isolated_repository_purpose_plan(),
        *_untracked_purpose_plan(reject_import_active=False),
        *process_purposes,
        *_untracked_purpose_plan(reject_import_active=True),
        *_live_command_plan_purpose_plan([str(item["name"]) for item in commands]),
        *_selected_source_purpose_plan(),
        *_untracked_purpose_plan(reject_import_active=False),
        *_untracked_purpose_plan(reject_import_active=True),
        *_isolated_repository_purpose_plan(),
    ]
    if len(purposes) != EXPECTED_PRIMARY_PUBLISHER_CHILD_COUNT:
        raise ReviewPublisherError("publisher_primary_expected_child_count_contract_mismatch")
    return purposes


def _expected_terminal_publisher_purpose_plan(
    *, token: Mapping[str, Any], validation: Mapping[str, Any]
) -> list[str]:
    purposes = _expected_primary_publisher_purpose_plan(token=token, validation=validation)
    purposes.extend(
        (
            *_git_snapshot_purpose_plan(),
            *_untracked_purpose_plan(reject_import_active=False),
            *_isolated_repository_purpose_plan(),
            *_untracked_purpose_plan(reject_import_active=True),
        )
    )
    if len(purposes) != EXPECTED_TERMINAL_PUBLISHER_CHILD_COUNT:
        raise ReviewPublisherError("publisher_terminal_expected_child_count_contract_mismatch")
    return purposes


def run_git(
    repository: Path,
    arguments: Sequence[str],
    *,
    binary: bool = False,
    git_executable: Path | None = None,
    git_sha256: str | None = None,
    purpose_context: str | None = None,
) -> str | bytes:
    if not arguments or any(not isinstance(item, str) or not item for item in arguments):
        raise ReviewPublisherError("git_arguments_invalid")
    if (git_executable is None) != (git_sha256 is None):
        raise ReviewPublisherError("git_independent_pin_pair_required")
    executable_value = "git"
    expected_sha256: str | None = None
    if git_executable is not None and git_sha256 is not None:
        pin = validate_independent_executable_pin(
            git_executable,
            git_sha256,
            label="git_executable",
        )
        executable_value = str(pin["path"])
        expected_sha256 = str(pin["sha256"])
    outcome = _run_publisher_child(
        (executable_value, *arguments),
        cwd=repository,
        purpose=(
            f"{purpose_context}:git-{arguments[0]}-{'binary' if binary else 'text'}"
            if purpose_context
            else f"git-{arguments[0]}-{'binary' if binary else 'text'}"
        ),
        expected_executable_sha256=expected_sha256,
    )
    if binary:
        return outcome.stdout.encode("utf-8")
    return outcome.stdout.strip()


def git_snapshot(
    repository: Path,
    branch: str,
    *,
    git_executable: Path | None = None,
    git_sha256: str | None = None,
) -> dict[str, Any]:
    git_kwargs = {"git_executable": git_executable, "git_sha256": git_sha256}
    local = str(run_git(repository, ["rev-parse", "HEAD"], **git_kwargs))
    tree = str(run_git(repository, ["rev-parse", "HEAD^{tree}"], **git_kwargs))
    active_branch = str(run_git(repository, ["branch", "--show-current"], **git_kwargs))
    origin = str(run_git(repository, ["rev-parse", f"refs/remotes/origin/{branch}"], **git_kwargs))
    remote_raw = str(
        run_git(repository, ["ls-remote", "origin", f"refs/heads/{branch}"], **git_kwargs)
    )
    remote_fields = remote_raw.split()
    remote = remote_fields[0] if len(remote_fields) == 2 else ""
    tracked = str(
        run_git(
            repository,
            ["status", "--porcelain=v1", "--untracked-files=no"],
            **git_kwargs,
        )
    )
    if active_branch != branch:
        raise ReviewPublisherError("canonical_branch_mismatch")
    if not local or local != origin or local != remote:
        raise ReviewPublisherError("canonical_local_origin_remote_mismatch")
    if tracked:
        raise ReviewPublisherError("canonical_tracked_changes_present")
    return {
        "repository": str(repository),
        "branch": active_branch,
        "local_head": local,
        "origin_tracking_head": origin,
        "remote_head": remote,
        "tree": tree,
        "tracked_changes": 0,
    }


def verify_isolated_repository_prepublication(
    repository: Path,
    *,
    expected_head: str,
    expected_tree: str,
    git_executable: Path | None = None,
    git_sha256: str | None = None,
) -> dict[str, Any]:
    """Fail closed if the isolated candidate changes before publication."""

    git_kwargs = {"git_executable": git_executable, "git_sha256": git_sha256}
    observed_head = str(run_git(repository, ["rev-parse", "HEAD"], **git_kwargs))
    observed_tree = str(run_git(repository, ["rev-parse", "HEAD^{tree}"], **git_kwargs))
    tracked = str(
        run_git(
            repository,
            ["status", "--porcelain=v1", "--untracked-files=no"],
            **git_kwargs,
        )
    )
    if observed_head != expected_head:
        raise ReviewPublisherError("isolated_head_changed_during_publication_preflight")
    if observed_tree != expected_tree:
        raise ReviewPublisherError("isolated_tree_changed_during_publication_preflight")
    if tracked:
        raise ReviewPublisherError("isolated_tracked_changes_present_during_publication_preflight")
    return {
        "head": observed_head,
        "tree": observed_tree,
        "tracked_changes": 0,
    }


def untracked_summary(
    repository: Path,
    *,
    git_executable: Path | None = None,
    git_sha256: str | None = None,
    reject_import_active: bool = False,
    purpose_context: str | None = None,
) -> dict[str, Any]:
    raw = run_git(
        repository,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        binary=True,
        git_executable=git_executable,
        git_sha256=git_sha256,
        purpose_context=(f"{purpose_context}:ordinary-untracked" if purpose_context else None),
    )
    assert isinstance(raw, bytes)
    paths = sorted(
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in raw.split(b"\0")
        if item
    )
    if reject_import_active and any(
        Path(relative).name.casefold() in _IMPORT_ACTIVE_TOOL_CONFIG_BASENAMES
        or Path(relative).suffix.casefold() in _IMPORT_ACTIVE_UNTRACKED_SUFFIXES
        for relative in paths
    ):
        raise ReviewPublisherError("isolated_untracked_import_shadow_path_present")
    if reject_import_active:
        ignored_raw = run_git(
            repository,
            [
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *_IGNORED_IMPORT_ACTIVE_PATHSPECS,
            ],
            binary=True,
            git_executable=git_executable,
            git_sha256=git_sha256,
            purpose_context=(
                f"{purpose_context}:ignored-import-active" if purpose_context else None
            ),
        )
        assert isinstance(ignored_raw, bytes)
        if ignored_raw:
            raise ReviewPublisherError("isolated_ignored_import_shadow_path_present")
    total_bytes = 0
    regular_files = 0
    content_entries: list[dict[str, Any]] = []
    for relative in paths:
        candidate = repository / Path(relative)
        if candidate.is_file():
            before = candidate.stat()
            content_sha256 = sha256_file(candidate)
            after = candidate.stat()
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
            ):
                raise ReviewPublisherError("canonical_user_untracked_changed_during_readback")
            regular_files += 1
            total_bytes += after.st_size
            content_entries.append(
                {"path": relative, "bytes": after.st_size, "sha256": content_sha256}
            )
        else:
            content_entries.append({"path": relative, "kind": "non_regular_or_missing"})
    # Preserve the established baseline contract: normalized, sorted UTF-8 paths
    # separated (and terminated) by NUL.  A newline inventory is ambiguous for
    # valid Git paths containing newlines and would not match the sealed r7s4
    # path-set digest.
    path_inventory = ("\0".join(paths) + "\0").encode("utf-8", errors="surrogateescape")
    path_sha256 = hashlib.sha256(path_inventory).hexdigest()
    return {
        "count": len(paths),
        "regular_files": regular_files,
        "bytes": total_bytes,
        "path_inventory_sha256": path_sha256,
        "path_inventory_encoding": "utf-8-nul-sorted",
        "path_list_sha256": hashlib.sha256(canonical_json_bytes(paths)).hexdigest(),
        "path_list_encoding": "canonical-json-sorted-paths",
        "content_inventory_sha256": hashlib.sha256(
            canonical_json_bytes(content_entries)
        ).hexdigest(),
        "content_inventory_encoding": "canonical-json-path-bytes-sha256",
        "paths_persisted_in_evidence": False,
    }


def directory_inventory(root: Path) -> dict[str, Any]:
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ReviewPublisherError("inventory_root_directory_required")
    root_stat = os.lstat(resolved)
    if getattr(root_stat, "st_file_attributes", 0) & 0x400:
        raise ReviewPublisherError(f"inventory_reparse_point_forbidden:{resolved}")
    file_inventory: list[dict[str, Any]] = []
    directory_inventory_entries: list[dict[str, Any]] = []
    tree_entries: list[dict[str, Any]] = []
    entries = sorted(resolved.rglob("*"), key=lambda item: item.relative_to(resolved).as_posix())
    for item in entries:
        observed = os.lstat(item)
        if getattr(observed, "st_file_attributes", 0) & 0x400:
            raise ReviewPublisherError(f"inventory_reparse_point_forbidden:{item}")
        relative_path = item.relative_to(resolved).as_posix()
        if stat.S_ISDIR(observed.st_mode):
            directory_entry = {
                "relative_path": relative_path,
                "type": "directory",
            }
            directory_inventory_entries.append(directory_entry)
            tree_entries.append(directory_entry)
        elif stat.S_ISREG(observed.st_mode):
            file_entry = {
                "relative_path": relative_path,
                "bytes": observed.st_size,
                "sha256": sha256_file(item),
            }
            file_inventory.append(file_entry)
            tree_entries.append({**file_entry, "type": "regular_file"})
        else:
            raise ReviewPublisherError(f"inventory_unsupported_entry_type:{item}")
    return {
        "root": str(resolved),
        "file_count": len(file_inventory),
        "directory_count": len(directory_inventory_entries),
        "entry_count": len(tree_entries),
        "total_bytes": sum(item["bytes"] for item in file_inventory),
        "files": file_inventory,
        "directories": directory_inventory_entries,
        "tree_entries": tree_entries,
        "inventory_sha256": hashlib.sha256(canonical_json_bytes(file_inventory)).hexdigest(),
        "tree_inventory_sha256": hashlib.sha256(canonical_json_bytes(tree_entries)).hexdigest(),
        "tree_inventory_encoding": "canonical-json-relative-path-type-bytes-sha256",
        "read_only_operation": True,
    }


def verify_primary_inventory_readback(
    batch: evidence.PreSerializedBatch,
    observed: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the postpublication path readback to the handle-verified primary bytes."""

    expected_files: list[dict[str, Any]] = []
    normalized_leaves: set[str] = set()
    for position, item in enumerate(batch.final_verification.inventory):
        if not isinstance(item, Mapping):
            raise ReviewPublisherError("primary_final_verification_inventory_mapping_required")
        leaf = item.get("leaf")
        try:
            leaf = validate_strict_windows_leaf(leaf)
        except (HandleBoundIoError, TypeError) as exc:
            raise ReviewPublisherError(
                f"primary_final_verification_leaf_invalid:{position}"
            ) from exc
        comparable = ntpath.normcase(leaf)
        if comparable in normalized_leaves:
            raise ReviewPublisherError("primary_final_verification_leaf_duplicate")
        normalized_leaves.add(comparable)
        byte_count = item.get("bytes")
        if type(byte_count) is not int or byte_count < 0:
            raise ReviewPublisherError(f"primary_final_verification_bytes_invalid:{position}")
        expected_files.append(
            {
                "relative_path": leaf,
                "bytes": byte_count,
                "sha256": _hex64(
                    item.get("sha256"),
                    f"primary_final_verification_inventory_{position}",
                ),
            }
        )
    expected_files.sort(key=lambda item: item["relative_path"])
    expected_inventory_sha256 = hashlib.sha256(canonical_json_bytes(expected_files)).hexdigest()
    expected_tree_entries = [{**item, "type": "regular_file"} for item in expected_files]
    expected_tree_inventory_sha256 = hashlib.sha256(
        canonical_json_bytes(expected_tree_entries)
    ).hexdigest()
    expected_total_bytes = sum(item["bytes"] for item in expected_files)
    observed_files = observed.get("files")
    exact_match = bool(
        isinstance(observed_files, list)
        and observed_files == expected_files
        and observed.get("file_count") == len(expected_files)
        and observed.get("directory_count") == 0
        and observed.get("entry_count") == len(expected_files)
        and observed.get("total_bytes") == expected_total_bytes
        and observed.get("inventory_sha256") == expected_inventory_sha256
        and observed.get("tree_entries") == expected_tree_entries
        and observed.get("tree_inventory_sha256") == expected_tree_inventory_sha256
        and observed.get("read_only_operation") is True
        and _comparable_windows_path(str(observed.get("root", "")))
        == _comparable_windows_path(str(batch.output_directory.resolve(strict=True)))
    )
    return {
        "schema": f"{SCHEMA}.primary-inventory-readback.v1",
        "status": "PASS" if exact_match else "FAIL",
        "exact_match": exact_match,
        "comparison_scope": "leaf_bytes_sha256_and_root",
        "expected_file_count": len(expected_files),
        "observed_file_count": observed.get("file_count", "unproven"),
        "expected_directory_count": 0,
        "observed_directory_count": observed.get("directory_count", "unproven"),
        "expected_entry_count": len(expected_files),
        "observed_entry_count": observed.get("entry_count", "unproven"),
        "expected_total_bytes": expected_total_bytes,
        "observed_total_bytes": observed.get("total_bytes", "unproven"),
        "expected_inventory_sha256": expected_inventory_sha256,
        "observed_inventory_sha256": observed.get("inventory_sha256", "unproven"),
        "expected_tree_inventory_sha256": expected_tree_inventory_sha256,
        "observed_tree_inventory_sha256": observed.get("tree_inventory_sha256", "unproven"),
    }


def verify_sealed_directory(path: Path, label: str) -> dict[str, Any]:
    expected = SEALED_HISTORICAL_DIRECTORIES[label]
    resolved = path.resolve(strict=True)
    expected_root = Path(str(expected["root"])).resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(expected_root)):
        raise ReviewPublisherError(f"{label}_sealed_root_mismatch")
    inventory = directory_inventory(resolved)
    projection = {
        "file_count": inventory["file_count"],
        "directory_count": inventory["directory_count"],
        "entry_count": inventory["entry_count"],
        "total_bytes": inventory["total_bytes"],
        "inventory_sha256": inventory["inventory_sha256"],
        "tree_inventory_sha256": inventory["tree_inventory_sha256"],
    }
    if projection != {
        "file_count": expected["file_count"],
        "directory_count": expected["directory_count"],
        "entry_count": expected["entry_count"],
        "total_bytes": expected["total_bytes"],
        "inventory_sha256": expected["inventory_sha256"],
        "tree_inventory_sha256": expected["tree_inventory_sha256"],
    }:
        raise ReviewPublisherError(f"{label}_sealed_inventory_mismatch")
    return {**inventory, "sealed_reference_verified": True}


def verify_sealed_etw_amendment(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    expected_path = Path(str(SEALED_ETW_AMENDMENT["path"])).resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(expected_path)):
        raise ReviewPublisherError("etw_amendment_sealed_path_mismatch")
    attributes = getattr(os.lstat(resolved), "st_file_attributes", 0)
    if not resolved.is_file() or attributes & 0x400:
        raise ReviewPublisherError("etw_amendment_regular_file_required")
    result = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if (
        result["bytes"] != SEALED_ETW_AMENDMENT["bytes"]
        or result["sha256"] != (SEALED_ETW_AMENDMENT["sha256"])
    ):
        raise ReviewPublisherError("etw_amendment_sealed_identity_mismatch")
    return {**result, "sealed_reference_verified": True, "fresh_etw_invocations": 0}


def verify_ci_readback(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    observation = manifest.get("hosted_failure_observation")
    if not isinstance(observation, dict):
        raise ReviewPublisherError("ci_hosted_failure_observation_required")
    run_id = str(observation.get("run_id", ""))
    artifact_id = observation.get("artifact_id")
    if not run_id.isdecimal() or type(artifact_id) is not int or artifact_id <= 0:
        raise ReviewPublisherError("ci_readback_identity_invalid")
    expected = {
        "evm-python-tests.xml": (
            observation.get("junit_xml_bytes"),
            observation.get("junit_xml_sha256"),
        ),
        f"run-{run_id}-artifact-{artifact_id}.zip": (
            observation.get("artifact_archive_bytes"),
            observation.get("artifact_archive_sha256"),
        ),
        f"run-{run_id}-nodeid-inventory.json": (
            None,
            observation.get("nodeid_inventory_readback_sha256"),
        ),
    }
    inventory = directory_inventory(root)
    if inventory["directory_count"] != 0 or inventory["entry_count"] != len(expected):
        raise ReviewPublisherError("ci_readback_directory_tree_not_flat")
    entries = {item["relative_path"]: item for item in inventory["files"]}
    if set(entries) != set(expected):
        raise ReviewPublisherError("ci_readback_file_set_mismatch")
    for name, (expected_bytes, expected_sha256) in expected.items():
        entry = entries[name]
        if expected_bytes is not None and entry["bytes"] != expected_bytes:
            raise ReviewPublisherError(f"ci_readback_bytes_mismatch:{name}")
        if entry["sha256"] != _hex64(expected_sha256, f"ci_readback_{name}"):
            raise ReviewPublisherError(f"ci_readback_sha256_mismatch:{name}")
    return {**inventory, "manifest_artifact_identity_verified": True}


def selected_source_inventory(
    repository: Path,
    *,
    git_executable: Path | None = None,
    git_sha256: str | None = None,
) -> dict[str, Any]:
    git_kwargs = {"git_executable": git_executable, "git_sha256": git_sha256}
    tracked = sorted(
        item
        for item in str(
            run_git(
                repository,
                ["diff", "--name-only", f"{ci.EXPECTED_BASELINE_COMMIT}..HEAD", "--"],
                **git_kwargs,
            )
        ).splitlines()
        if item
    )
    if set(tracked) != REQUIRED_SELECTED_SOURCE_PATHS or len(tracked) != len(
        REQUIRED_SELECTED_SOURCE_PATHS
    ):
        raise ReviewPublisherError("r7s5_changed_source_inventory_not_exact")
    entries: list[dict[str, Any]] = []
    for relative in tracked:
        path = repository / relative
        worktree = path.read_bytes()
        worktree_sha256 = hashlib.sha256(worktree).hexdigest()
        committed_blob = _hex40(
            run_git(repository, ["rev-parse", f"HEAD:{relative}"], **git_kwargs),
            "selected_source_committed_blob",
        )
        filtered_worktree_blob = _hex40(
            run_git(
                repository,
                ["hash-object", f"--path={relative}", "--", relative],
                **git_kwargs,
            ),
            "selected_source_filtered_worktree_blob",
        )
        if filtered_worktree_blob != committed_blob:
            raise ReviewPublisherError(f"selected_source_worktree_commit_mismatch:{relative}")
        try:
            committed_bytes = int(
                str(run_git(repository, ["cat-file", "-s", committed_blob], **git_kwargs))
            )
        except (TypeError, ValueError) as exc:
            raise ReviewPublisherError("selected_source_committed_size_invalid") from exc
        if committed_bytes < 0:
            raise ReviewPublisherError("selected_source_committed_size_invalid")
        entries.append(
            {
                "path": relative,
                "worktree_bytes": len(worktree),
                "worktree_sha256": worktree_sha256,
                "committed_git_blob": committed_blob,
                "git_filtered_worktree_blob": filtered_worktree_blob,
                "committed_bytes": committed_bytes,
                "git_filtered_worktree_matches_committed": True,
                "raw_worktree_bytes_may_reflect_checkout_filters": True,
            }
        )
    return {
        "files": entries,
        "file_count": len(entries),
        "inventory_sha256": hashlib.sha256(canonical_json_bytes(entries)).hexdigest(),
    }


def measure_process_identity(
    pid: int,
    *,
    powershell_executable: Path | None = None,
    powershell_sha256: str | None = None,
) -> dict[str, Any]:
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        raise ReviewPublisherError("windows_process_identity_pid_invalid")
    script = (
        "$ErrorActionPreference='Stop';"
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}';"
        "if($null -eq $p){throw 'process_not_found'};"
        "[ordered]@{pid=[int]$p.ProcessId;ppid=[int]$p.ParentProcessId;"
        "creation_time_utc=$p.CreationDate.ToUniversalTime().ToString('o');"
        "path=[string]$p.ExecutablePath;command_line=[string]$p.CommandLine}"
        "|ConvertTo-Json -Compress"
    )
    if (powershell_executable is None) != (powershell_sha256 is None):
        raise ReviewPublisherError("powershell_independent_pin_pair_required")
    executable_value = "powershell.exe"
    expected_sha256: str | None = None
    if powershell_executable is not None and powershell_sha256 is not None:
        pin = validate_independent_executable_pin(
            powershell_executable,
            powershell_sha256,
            label="powershell_executable",
        )
        executable_value = str(pin["path"])
        expected_sha256 = str(pin["sha256"])
    outcome = _run_publisher_child(
        (
            executable_value,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ),
        cwd=SCRIPT_PROJECT_ROOT,
        purpose=f"process-identity-{pid}",
        expected_executable_sha256=expected_sha256,
    )
    try:
        raw = json.loads(outcome.stdout)
    except (TypeError, ValueError) as exc:
        raise ReviewPublisherError("windows_process_identity_json_invalid") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "pid",
        "ppid",
        "creation_time_utc",
        "path",
        "command_line",
    }:
        raise ReviewPublisherError("windows_process_identity_keys_not_exact")
    command_line = raw["command_line"]
    if (
        type(raw["pid"]) is not int
        or raw["pid"] != pid
        or type(raw["ppid"]) is not int
        or not isinstance(raw["path"], str)
        or not raw["path"]
        or not isinstance(command_line, str)
    ):
        raise ReviewPublisherError("windows_process_identity_payload_invalid")
    try:
        created = datetime.fromisoformat(str(raw["creation_time_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewPublisherError("windows_process_creation_time_invalid") from exc
    if created.tzinfo is None:
        raise ReviewPublisherError("windows_process_creation_timezone_required")
    danger_full_access = (
        re.search(
            r"(?i)(?:^|\s)-s(?:\s+|=)[\"']?danger-full-access(?:[\"']?)(?:\s|$)",
            command_line,
        )
        is not None
    )
    approval_never = (
        re.search(
            r"(?i)(?:^|\s)-a(?:\s+|=)[\"']?never(?:[\"']?)(?:\s|$)",
            command_line,
        )
        is not None
    )
    return {
        "pid": pid,
        "ppid": raw["ppid"],
        "creation_time_utc": created.isoformat().replace("+00:00", "Z"),
        "path": str(Path(raw["path"]).resolve()),
        "command_line_sha256": hashlib.sha256(command_line.encode("utf-8")).hexdigest(),
        "danger_full_access_flag_present": danger_full_access,
        "approval_never_flag_present": approval_never,
        "command_line_persisted": False,
        "measurement": "cim-win32-process-direct-readback",
    }


def _token_requirements(token: Mapping[str, Any], label: str) -> None:
    if (
        token.get("administrator") is not True
        or token.get("administrator_group_member") is not True
        or token.get("integrity") not in {"High", "System"}
        or token.get("token_elevation_type") != "Full"
        or token.get("token_elevation_value") != 2
    ):
        raise ReviewPublisherError(f"{label}_token_requirements_not_met")


def _process_file_binding(identity: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(identity.get("path", ""))).resolve(strict=True)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ReviewPublisherError("lineage_executable_regular_file_required")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": info.st_size,
        "device": info.st_dev,
        "file_id": info.st_ino,
        "creation_time_ns": info.st_ctime_ns,
    }


def _validate_lineage_work_order(
    path: Path,
    expected_sha256: str,
    *,
    runtime_identity: Mapping[str, Any],
    parent_identity: Mapping[str, Any],
    codex_identity: Mapping[str, Any],
) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    raw = resolved.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _hex64(expected_sha256, "lineage_work_order"):
        raise ReviewPublisherError("lineage_work_order_sha256_mismatch")
    value = read_json_mapping(resolved, "lineage_work_order")
    if raw != canonical_json_bytes(dict(value)):
        raise ReviewPublisherError("lineage_work_order_not_canonical")
    # A work order can be issued before the publisher child exists, so it may
    # bind executable files but must not pretend to know that future child's
    # PID or creation timestamp.  Those values are measured live below and are
    # explicitly retained as non-authoritative observations.
    expected_executables: dict[str, Any] = {}
    for label, identity in (
        ("codex", codex_identity),
        ("powershell_parent", parent_identity),
        ("publisher_python", runtime_identity),
    ):
        expected_executables[label] = _process_file_binding(identity)
    if (
        set(value) != {"schema", "authority_scope", "authority_verified", "executable_bindings"}
        or value.get("schema") != f"{SCHEMA}.lineage-work-order.v2"
        or value.get("authority_scope") != "internal_non_authoritative"
        or value.get("authority_verified") is not False
        or value.get("executable_bindings") != expected_executables
    ):
        raise ReviewPublisherError("lineage_work_order_exact_binding_mismatch")
    return {
        "path": str(resolved),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "executable_bindings": expected_executables,
        "future_process_identity_preissued": False,
        "live_process_identity_source": "publisher_direct_measurement_after_launch",
    }


def validate_token_evidence(
    value: Mapping[str, Any],
    *,
    powershell_executable: Path | None = None,
    powershell_sha256: str | None = None,
    lineage_work_order: Path | None = None,
    lineage_work_order_sha256: str | None = None,
) -> dict[str, Any]:
    if set(value) != {
        "authority_scope",
        "authority_verified",
        "codex_pid",
        "publisher_parent_pid",
    }:
        raise ReviewPublisherError("token_evidence_keys_not_exact")
    if (
        value.get("authority_scope") != "internal_non_authoritative"
        or value.get("authority_verified") is not False
    ):
        raise ReviewPublisherError("token_evidence_authority_scope_invalid")
    codex_pid = value["codex_pid"]
    parent_pid = value["publisher_parent_pid"]
    if (
        type(codex_pid) is not int
        or codex_pid <= 0
        or type(parent_pid) is not int
        or parent_pid <= 0
        or parent_pid != os.getppid()
    ):
        raise ReviewPublisherError("token_evidence_process_binding_invalid")
    if os.name != "nt" or ctypes.windll.shell32.IsUserAnAdmin() != 1:
        raise ReviewPublisherError("publisher_runtime_not_administrator")

    runtime_token = measure_current_token()
    parent_token = measure_process_token(parent_pid)
    codex_token = measure_process_token(codex_pid)
    _token_requirements(runtime_token, "publisher_runtime")
    _token_requirements(parent_token, "publisher_parent")
    _token_requirements(codex_token, "codex")
    identity_kwargs = {
        "powershell_executable": powershell_executable,
        "powershell_sha256": powershell_sha256,
    }
    runtime_identity = measure_process_identity(os.getpid(), **identity_kwargs)
    parent_identity = measure_process_identity(parent_pid, **identity_kwargs)
    codex_identity = measure_process_identity(codex_pid, **identity_kwargs)
    if runtime_identity["ppid"] != parent_pid:
        raise ReviewPublisherError("publisher_parent_relationship_mismatch")
    if parent_identity["ppid"] != codex_pid:
        raise ReviewPublisherError("codex_parent_relationship_mismatch")
    if Path(parent_identity["path"]).name.lower() not in {"powershell.exe", "pwsh.exe"}:
        raise ReviewPublisherError("publisher_parent_powershell_required")
    for label, token, identity in (
        ("runtime", runtime_token, runtime_identity),
        ("parent", parent_token, parent_identity),
        ("codex", codex_token, codex_identity),
    ):
        if token["session_id"] != runtime_token["session_id"] or os.path.normcase(
            str(token["path"])
        ) != os.path.normcase(str(identity["path"])):
            raise ReviewPublisherError(f"{label}_token_process_identity_mismatch")
    if Path(codex_identity["path"]).name.lower() != "codex.exe":
        raise ReviewPublisherError("codex_executable_identity_mismatch")
    if (
        codex_identity["danger_full_access_flag_present"] is not True
        or codex_identity["approval_never_flag_present"] is not True
    ):
        raise ReviewPublisherError("codex_launcher_settings_readback_mismatch")
    created = [
        datetime.fromisoformat(item["creation_time_utc"].replace("Z", "+00:00"))
        for item in (codex_identity, parent_identity, runtime_identity)
    ]
    if created != sorted(created):
        raise ReviewPublisherError("publisher_process_creation_order_mismatch")
    if lineage_work_order is None or lineage_work_order_sha256 is None:
        raise ReviewPublisherError("lineage_work_order_required")
    lineage_binding = _validate_lineage_work_order(
        lineage_work_order,
        lineage_work_order_sha256,
        runtime_identity=runtime_identity,
        parent_identity=parent_identity,
        codex_identity=codex_identity,
    )
    return {
        "codex": {"token": codex_token, "process": codex_identity},
        "publisher_parent": {"token": parent_token, "process": parent_identity},
        "publisher_runtime": {"token": runtime_token, "process": runtime_identity},
        "launcher_settings_readback": {
            "sandbox_mode": "danger-full-access",
            "approval_policy": "never",
            "source": "codex_process_command_line_direct_readback",
        },
        "lineage_work_order_binding": lineage_binding,
        "lineage_authority_verified": False,
        "lineage_observation_scope": "live_direct_internal_non_authoritative",
    }


def measure_current_token() -> dict[str, Any]:
    return measure_process_token(os.getpid())


def measure_process_token(pid: int) -> dict[str, Any]:
    if os.name != "nt":
        raise ReviewPublisherError("windows_token_measurement_required")
    if type(pid) is not int or pid <= 0:
        raise ReviewPublisherError("windows_token_pid_invalid")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    token = ctypes.c_void_p()
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    process = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        raise ReviewPublisherError("publisher_open_process_failed")
    advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    advapi32.GetTokenInformation.restype = ctypes.c_int
    advapi32.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.CreateWellKnownSid.restype = ctypes.c_int
    advapi32.CheckTokenMembership.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    advapi32.CheckTokenMembership.restype = ctypes.c_int
    advapi32.DuplicateToken.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    advapi32.DuplicateToken.restype = ctypes.c_int
    kernel32.ProcessIdToSessionId.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    kernel32.ProcessIdToSessionId.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    if not advapi32.OpenProcessToken(process, 0x000A, ctypes.byref(token)):
        kernel32.CloseHandle(process)
        raise ReviewPublisherError("publisher_open_process_token_failed")
    impersonation_token = ctypes.c_void_p()
    try:
        elevation = ctypes.c_uint32()
        returned = ctypes.c_uint32()
        if not advapi32.GetTokenInformation(
            token,
            18,  # TokenElevationType
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            raise ReviewPublisherError("publisher_token_elevation_read_failed")
        token_elevated = ctypes.c_uint32()
        if not advapi32.GetTokenInformation(
            token,
            20,  # TokenElevation
            ctypes.byref(token_elevated),
            ctypes.sizeof(token_elevated),
            ctypes.byref(returned),
        ):
            raise ReviewPublisherError("publisher_token_elevated_flag_read_failed")
        required = ctypes.c_uint32()
        advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(required))
        if required.value == 0:
            raise ReviewPublisherError("publisher_token_integrity_size_read_failed")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token, 25, buffer, required.value, ctypes.byref(required)
        ):
            raise ReviewPublisherError("publisher_token_integrity_read_failed")

        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("sid", ctypes.c_void_p), ("attributes", ctypes.c_uint32)]

        label = ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents
        advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        advapi32.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.c_uint32)
        count = advapi32.GetSidSubAuthorityCount(label.sid).contents.value
        rid = advapi32.GetSidSubAuthority(label.sid, count - 1).contents.value
        integrity = {0x2000: "Medium", 0x3000: "High", 0x4000: "System"}.get(rid, f"RID-{rid}")
        admin_sid_size = ctypes.c_uint32(68)
        admin_sid = ctypes.create_string_buffer(admin_sid_size.value)
        if not advapi32.CreateWellKnownSid(
            26,  # WinBuiltinAdministratorsSid
            None,
            admin_sid,
            ctypes.byref(admin_sid_size),
        ):
            raise ReviewPublisherError("publisher_administrator_sid_create_failed")
        administrator_member = ctypes.c_int()
        if not advapi32.DuplicateToken(token, 2, ctypes.byref(impersonation_token)):
            raise ReviewPublisherError("publisher_token_duplicate_failed")
        if not advapi32.CheckTokenMembership(
            impersonation_token,
            admin_sid,
            ctypes.byref(administrator_member),
        ):
            raise ReviewPublisherError("publisher_administrator_membership_read_failed")
        session = ctypes.c_uint32()
        if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise ReviewPublisherError("publisher_session_read_failed")
        path_buffer = ctypes.create_unicode_buffer(32_768)
        path_size = ctypes.c_uint32(len(path_buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process, 0, path_buffer, ctypes.byref(path_size)
        ):
            raise ReviewPublisherError("publisher_process_path_read_failed")

        return {
            "pid": pid,
            "path": str(Path(path_buffer.value).resolve()),
            "session_id": session.value,
            "administrator": (
                administrator_member.value == 1 and token_elevated.value == 1 and rid >= 0x3000
            ),
            "administrator_group_member": administrator_member.value == 1,
            "integrity": integrity,
            "integrity_rid": rid,
            "token_elevation_type": {1: "Default", 2: "Full", 3: "Limited"}.get(
                elevation.value, f"Unknown-{elevation.value}"
            ),
            "token_elevation_value": elevation.value,
            "measurement": "win32-current-process-token",
        }
    finally:
        if impersonation_token:
            kernel32.CloseHandle(impersonation_token)
        kernel32.CloseHandle(token)
        kernel32.CloseHandle(process)


def _validate_validation_process_evidence(
    value: object,
    *,
    spec: object,
    planned_tool: object,
    command_record: Mapping[str, Any],
) -> None:
    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner

    if (
        not isinstance(spec, validation_runner.CommandSpec)
        or not isinstance(value, dict)
        or not isinstance(planned_tool, Mapping)
    ):
        raise ReviewPublisherError("validation_process_evidence_mapping_required")
    expected_keys = {
        "name",
        "run_uuid",
        "command",
        "started_at_utc",
        "ended_at_utc",
        "duration_seconds",
        "timed_out",
        "cancelled",
        "return_code",
        "manual_intervention_required",
        "residual_pids",
        "stdout",
        "stderr",
        "stdout_drained",
        "stderr_drained",
        "streams_drained",
        "active_process_zero",
        "final_active_process_count",
        "identity_coverage_complete",
        "safe_for_followup",
        "forced_termination_attempts",
        "job_limit_flags",
        "identities",
        "events",
        "accounting",
        "errors",
        "executable_identity",
        "stream_capture_limit_bytes",
        "stdout_total_bytes",
        "stderr_total_bytes",
        "stdout_capture_overflow",
        "stderr_capture_overflow",
        "stream_cleanup",
    }
    if set(value) != expected_keys:
        raise ReviewPublisherError("validation_process_evidence_keys_not_exact")
    raw_process_run_uuid = value["run_uuid"]
    try:
        parsed_process_run_uuid = uuid.UUID(str(raw_process_run_uuid))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReviewPublisherError("validation_process_run_uuid_invalid") from exc
    process_run_uuid = str(parsed_process_run_uuid)
    if (
        not isinstance(raw_process_run_uuid, str)
        or parsed_process_run_uuid.version != 4
        or raw_process_run_uuid != process_run_uuid
    ):
        raise ReviewPublisherError("validation_process_run_uuid_not_canonical_uuid4")

    strict_bools = {
        "timed_out": False,
        "cancelled": False,
        "manual_intervention_required": False,
        "stdout_drained": True,
        "stderr_drained": True,
        "streams_drained": True,
        "active_process_zero": True,
        "identity_coverage_complete": True,
        "safe_for_followup": spec.expected_exit_code == 0,
        "stdout_capture_overflow": False,
        "stderr_capture_overflow": False,
    }
    if any(
        type(value.get(key)) is not bool or value[key] is not expected
        for key, expected in strict_bools.items()
    ):
        raise ReviewPublisherError("validation_process_boolean_not_exact")
    strict_ints = {
        "return_code": spec.expected_exit_code,
        "final_active_process_count": 0,
        "forced_termination_attempts": 0,
        "job_limit_flags": 0,
        "stream_capture_limit_bytes": DEFAULT_MAX_STREAM_BYTES,
    }
    if any(
        type(value.get(key)) is not int or value[key] != expected
        for key, expected in strict_ints.items()
    ):
        raise ReviewPublisherError("validation_process_integer_not_exact")
    if validation_runner._stream_cleanup_evidence_errors(value.get("stream_cleanup")):
        raise ReviewPublisherError("validation_process_stream_cleanup_invalid")
    fixed = {
        "name": f"r7s6-validation-{spec.name}",
        "command": [str(planned_tool.get("path", "")), *spec.argv[1:]],
        "residual_pids": [],
        "errors": [],
    }
    if any(value.get(key) != expected for key, expected in fixed.items()):
        raise ReviewPublisherError("validation_process_evidence_not_cleanly_contained")

    executable_identity = value.get("executable_identity")
    executable_identity_keys = {
        "path",
        "sha256",
        "bytes",
        "device",
        "file_id",
        "expected_sha256",
        "pin_required",
        "pin_match",
        "measurement_scope",
        "handle_lock_held_through_create",
        "handle_lock_share_mode",
        "handle_lock_inheritable",
        "ancestor_directory_locks_held_through_create",
        "ancestor_directory_lock_count",
        "ancestor_directory_lock_share_mode",
        "path_lock_scope",
        "pre_kernel_create_gate_required",
        "pre_kernel_create_gate_passed",
        "pre_kernel_create_gate_invocations",
        "pre_kernel_remaining_seconds",
        "pre_kernel_required_seconds",
    }
    planned_sha256 = _hex64(planned_tool.get("sha256"), "validation_planned_tool")
    planned_path = planned_tool.get("path")
    if (
        not isinstance(executable_identity, dict)
        or set(executable_identity) != executable_identity_keys
        or not isinstance(planned_path, str)
        or not planned_path
        or os.path.normcase(os.path.abspath(str(executable_identity.get("path", ""))))
        != os.path.normcase(os.path.abspath(planned_path))
        or executable_identity.get("sha256") != planned_sha256
        or executable_identity.get("expected_sha256") != planned_sha256
        or executable_identity.get("pin_required") is not True
        or executable_identity.get("pin_match") is not True
        or executable_identity.get("measurement_scope") != "immediately_before_CreateProcessW"
        or executable_identity.get("handle_lock_held_through_create") is not True
        or executable_identity.get("handle_lock_share_mode") != "FILE_SHARE_READ_only"
        or executable_identity.get("handle_lock_inheritable") is not False
        or executable_identity.get("ancestor_directory_locks_held_through_create") is not True
        or isinstance(executable_identity.get("ancestor_directory_lock_count"), bool)
        or not isinstance(executable_identity.get("ancestor_directory_lock_count"), int)
        or executable_identity.get("ancestor_directory_lock_count", -1) < 0
        or executable_identity.get("ancestor_directory_lock_share_mode")
        != "FILE_SHARE_READ_WRITE_no_delete"
        or executable_identity.get("path_lock_scope") != "all_nonroot_ancestors_and_leaf"
        or executable_identity.get("pre_kernel_create_gate_required") is not True
        or executable_identity.get("pre_kernel_create_gate_passed") is not True
        or type(executable_identity.get("pre_kernel_create_gate_invocations")) is not int
        or executable_identity["pre_kernel_create_gate_invocations"] != 1
        or type(executable_identity.get("pre_kernel_remaining_seconds")) not in {int, float}
        or not math.isfinite(float(executable_identity["pre_kernel_remaining_seconds"]))
        or type(executable_identity.get("pre_kernel_required_seconds")) not in {int, float}
        or not math.isfinite(float(executable_identity["pre_kernel_required_seconds"]))
        or executable_identity["pre_kernel_required_seconds"] <= 0
        or executable_identity["pre_kernel_remaining_seconds"]
        < executable_identity["pre_kernel_required_seconds"]
        or type(executable_identity.get("bytes")) is not int
        or executable_identity["bytes"] <= 0
        or type(executable_identity.get("device")) is not int
        or executable_identity["device"] < 0
        or type(executable_identity.get("file_id")) is not int
        or executable_identity["file_id"] < 0
    ):
        raise ReviewPublisherError("validation_process_executable_launch_pin_invalid")
    if (
        type(planned_tool.get("bytes")) is int
        and executable_identity["bytes"] != planned_tool["bytes"]
    ):
        raise ReviewPublisherError("validation_process_executable_bytes_mismatch")
    if (
        type(value.get("duration_seconds")) not in {int, float}
        or value["duration_seconds"] < 0
        or not isinstance(value.get("stdout"), str)
        or not isinstance(value.get("stderr"), str)
    ):
        raise ReviewPublisherError("validation_process_stream_or_duration_invalid")
    try:
        started = datetime.fromisoformat(str(value["started_at_utc"]).replace("Z", "+00:00"))
        ended = datetime.fromisoformat(str(value["ended_at_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewPublisherError("validation_process_timestamp_invalid") from exc
    if started.tzinfo is None or ended.tzinfo is None or ended < started:
        raise ReviewPublisherError("validation_process_timestamp_order_invalid")
    command_timestamp_keys = {
        key for key in ("started_at_utc", "ended_at_utc") if key in command_record
    }
    if command_timestamp_keys:
        if command_timestamp_keys != {"started_at_utc", "ended_at_utc"}:
            raise ReviewPublisherError("validation_command_process_bracket_incomplete")
        try:
            command_started = datetime.fromisoformat(
                str(command_record["started_at_utc"]).replace("Z", "+00:00")
            )
            command_ended = datetime.fromisoformat(
                str(command_record["ended_at_utc"]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ReviewPublisherError("validation_command_process_bracket_invalid") from exc
        if (
            command_started.tzinfo is None
            or command_ended.tzinfo is None
            or not (command_started <= started <= ended <= command_ended)
        ):
            raise ReviewPublisherError("validation_command_process_bracket_invalid")

    identities = value.get("identities")
    identity_keys = {
        "pid",
        "ppid",
        "creation_time_ns",
        "creation_time_utc",
        "image",
        "run_uuid",
        "observed_sequence",
    }
    if not isinstance(identities, list) or not identities:
        raise ReviewPublisherError("validation_process_identity_required")
    stable_keys: set[tuple[int, int]] = set()
    identity_observed_sequences: set[int] = set()
    identities_by_pid: dict[int, list[Mapping[str, Any]]] = {}
    for identity in identities:
        if not isinstance(identity, dict) or set(identity) != identity_keys:
            raise ReviewPublisherError("validation_process_identity_keys_not_exact")
        if (
            type(identity.get("pid")) is not int
            or identity["pid"] <= 0
            or type(identity.get("ppid")) is not int
            or identity["ppid"] <= 0
            or type(identity.get("creation_time_ns")) is not int
            or identity["creation_time_ns"] <= 0
            or not isinstance(identity.get("creation_time_utc"), str)
            or not identity["creation_time_utc"]
            or not isinstance(identity.get("image"), str)
            or not identity["image"]
            or identity.get("run_uuid") != process_run_uuid
            or type(identity.get("observed_sequence")) is not int
            or identity["observed_sequence"] <= 0
        ):
            raise ReviewPublisherError("validation_process_identity_invalid")
        try:
            creation_time = datetime.fromisoformat(
                identity["creation_time_utc"].replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ReviewPublisherError("validation_process_identity_timestamp_invalid") from exc
        if creation_time.tzinfo is None or not (started <= creation_time <= ended):
            raise ReviewPublisherError("validation_process_identity_timestamp_invalid")
        stable_keys.add((identity["pid"], identity["creation_time_ns"]))
        if identity["observed_sequence"] in identity_observed_sequences:
            raise ReviewPublisherError("validation_process_identity_sequence_reused")
        identity_observed_sequences.add(identity["observed_sequence"])
        identities_by_pid.setdefault(identity["pid"], []).append(identity)
    if len(stable_keys) != len(identities):
        raise ReviewPublisherError("validation_process_identity_reused")

    events = value.get("events")
    event_keys = {"sequence", "event", "monotonic_ns", "timestamp_utc", "pid", "details"}
    if not isinstance(events, list) or not events:
        raise ReviewPublisherError("validation_process_events_required")
    event_sequences: list[int] = []
    sequence_points: list[tuple[int, int, datetime]] = []
    names: list[str] = []
    for event in events:
        if not isinstance(event, dict) or set(event) != event_keys:
            raise ReviewPublisherError("validation_process_event_keys_not_exact")
        if (
            type(event.get("sequence")) is not int
            or event["sequence"] <= 0
            or type(event.get("monotonic_ns")) is not int
            or event["monotonic_ns"] <= 0
            or not isinstance(event.get("event"), str)
            or not event["event"]
            or not isinstance(event.get("timestamp_utc"), str)
            or (
                event.get("pid") is not None
                and (type(event["pid"]) is not int or event["pid"] <= 0)
            )
            or not isinstance(event.get("details"), dict)
        ):
            raise ReviewPublisherError("validation_process_event_invalid")
        try:
            event_time = datetime.fromisoformat(event["timestamp_utc"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReviewPublisherError("validation_process_event_timestamp_invalid") from exc
        if event_time.tzinfo is None or not (started <= event_time <= ended):
            raise ReviewPublisherError("validation_process_event_timestamp_invalid")
        event_sequences.append(event["sequence"])
        sequence_points.append((event["sequence"], event["monotonic_ns"], event_time))
        names.append(event["event"])
    if event_sequences != sorted(set(event_sequences)):
        raise ReviewPublisherError("validation_process_event_sequence_invalid")
    required_order = [
        "job_created",
        "root_created_suspended",
        "job_membership_verified",
        "root_resumed",
        "active_process_count_zero",
        "streams_drained",
    ]
    try:
        positions = [names.index(name) for name in required_order]
    except ValueError as exc:
        raise ReviewPublisherError("validation_process_required_event_missing") from exc
    if positions != sorted(positions):
        raise ReviewPublisherError("validation_process_required_event_order_invalid")
    if any(names.count(name) != 1 for name in required_order):
        raise ReviewPublisherError("validation_process_required_event_not_unique")

    events_by_name = {event["event"]: event for event in events if event["event"] in required_order}
    root_pid = events_by_name["root_created_suspended"]["pid"]
    if type(root_pid) is not int or root_pid <= 0:
        raise ReviewPublisherError("validation_process_root_pid_invalid")
    for name in ("job_membership_verified", "root_resumed"):
        if events_by_name[name]["pid"] != root_pid:
            raise ReviewPublisherError("validation_process_root_pid_event_mismatch")
    membership_details = events_by_name["job_membership_verified"].get("details")
    if (
        not isinstance(membership_details, dict)
        or set(membership_details) != {"active_processes", "job_limit_flags"}
        or type(membership_details.get("active_processes")) is not int
        or membership_details["active_processes"] != 1
        or type(membership_details.get("job_limit_flags")) is not int
        or membership_details["job_limit_flags"] != 0
    ):
        raise ReviewPublisherError("validation_process_job_membership_details_invalid")
    if events_by_name["job_created"]["pid"] is not None:
        raise ReviewPublisherError("validation_process_job_created_pid_must_be_null")

    root_identities = identities_by_pid.get(root_pid, [])
    if len(root_identities) != 1:
        raise ReviewPublisherError("validation_process_root_identity_not_unique")
    root_identity = root_identities[0]
    identity_events = [
        event
        for event in events
        if event["event"] == "identity_observed"
        and event["pid"] == root_pid
        and event["sequence"] == root_identity["observed_sequence"]
    ]
    if len(identity_events) != 1:
        raise ReviewPublisherError("validation_process_root_identity_sequence_mismatch")
    expected_image = planned_tool.get("path")
    if not isinstance(expected_image, str) or not expected_image:
        raise ReviewPublisherError("validation_process_planned_tool_path_invalid")
    if os.path.normcase(os.path.abspath(root_identity["image"])) != os.path.normcase(
        os.path.abspath(expected_image)
    ):
        raise ReviewPublisherError("validation_process_root_image_mismatch")

    accounting = value.get("accounting")
    accounting_keys = {
        "sequence",
        "monotonic_ns",
        "timestamp_utc",
        "total_processes",
        "active_processes",
        "total_terminated_processes",
        "active_pids",
    }
    if not isinstance(accounting, list) or not accounting:
        raise ReviewPublisherError("validation_process_accounting_required")
    accounting_sequences: list[int] = []
    prior_total_processes = 0
    prior_terminated_processes = 0
    for item in accounting:
        if not isinstance(item, dict) or set(item) != accounting_keys:
            raise ReviewPublisherError("validation_process_accounting_keys_not_exact")
        active_pids = item["active_pids"]
        if (
            type(item["sequence"]) is not int
            or item["sequence"] <= 0
            or type(item["monotonic_ns"]) is not int
            or item["monotonic_ns"] <= 0
            or not isinstance(item["timestamp_utc"], str)
            or type(item["total_processes"]) is not int
            or item["total_processes"] < 1
            or type(item["active_processes"]) is not int
            or item["active_processes"] < 0
            or type(item["total_terminated_processes"]) is not int
            or item["total_terminated_processes"] < 0
            or not isinstance(active_pids, list)
            or any(type(pid) is not int or pid <= 0 for pid in active_pids)
            or len(active_pids) != len(set(active_pids))
        ):
            raise ReviewPublisherError("validation_process_accounting_invalid")
        try:
            accounting_time = datetime.fromisoformat(item["timestamp_utc"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReviewPublisherError("validation_process_accounting_timestamp_invalid") from exc
        if accounting_time.tzinfo is None or not (started <= accounting_time <= ended):
            raise ReviewPublisherError("validation_process_accounting_timestamp_invalid")
        if (
            item["active_processes"] != len(active_pids)
            or item["active_processes"] > item["total_processes"]
            or item["total_terminated_processes"] > item["total_processes"]
            or item["active_processes"] + item["total_terminated_processes"]
            > item["total_processes"]
            or item["total_processes"] < prior_total_processes
            or item["total_terminated_processes"] < prior_terminated_processes
        ):
            raise ReviewPublisherError("validation_process_accounting_relationship_invalid")
        if any(pid not in identities_by_pid for pid in active_pids):
            raise ReviewPublisherError("validation_process_accounting_identity_mismatch")
        prior_total_processes = item["total_processes"]
        prior_terminated_processes = item["total_terminated_processes"]
        accounting_sequences.append(item["sequence"])
        sequence_points.append((item["sequence"], item["monotonic_ns"], accounting_time))
    if accounting_sequences != sorted(set(accounting_sequences)):
        raise ReviewPublisherError("validation_process_accounting_sequence_invalid")
    combined_sequences = sorted(event_sequences + accounting_sequences)
    if combined_sequences != list(range(1, len(combined_sequences) + 1)):
        raise ReviewPublisherError("validation_process_global_sequence_invalid")
    ordered_points = sorted(sequence_points)
    monotonic_values = [item[1] for item in ordered_points]
    wall_clock_values = [item[2] for item in ordered_points]
    if monotonic_values != sorted(monotonic_values):
        raise ReviewPublisherError("validation_process_monotonic_order_invalid")
    if wall_clock_values != sorted(wall_clock_values):
        raise ReviewPublisherError("validation_process_wall_clock_order_invalid")
    final_accounting = accounting[-1]
    if final_accounting["active_processes"] != 0 or final_accounting["active_pids"] != []:
        raise ReviewPublisherError("validation_process_final_accounting_not_zero")
    if len(stable_keys) != final_accounting["total_processes"]:
        raise ReviewPublisherError("validation_process_identity_coverage_mismatch")
    identity_events_all = [event for event in events if event["event"] == "identity_observed"]
    identity_event_sequences = {(event["pid"], event["sequence"]) for event in identity_events_all}
    expected_identity_event_sequences = {
        (identity["pid"], identity["observed_sequence"]) for identity in identities
    }
    if (
        len(identity_event_sequences) != len(identity_events_all)
        or identity_event_sequences != expected_identity_event_sequences
        or any(
            (identity["pid"], identity["observed_sequence"]) not in identity_event_sequences
            for identity in identities
        )
    ):
        raise ReviewPublisherError("validation_process_identity_event_binding_mismatch")

    for label in ("stdout", "stderr"):
        stream = value[label]
        assert isinstance(stream, str)
        raw = stream.encode("utf-8")
        expected_tail = raw[-16_384:].decode("utf-8", errors="replace")
        if (
            type(command_record.get(f"{label}_bytes")) is not int
            or command_record[f"{label}_bytes"] != len(raw)
            or command_record.get(f"{label}_sha256") != hashlib.sha256(raw).hexdigest()
            or command_record.get(f"{label}_tail") != expected_tail
            or type(value.get(f"{label}_total_bytes")) is not int
            or value[f"{label}_total_bytes"] != len(raw)
        ):
            raise ReviewPublisherError("validation_process_stream_binding_mismatch")
    if any(
        token not in value["stdout"] and token not in value["stderr"]
        for token in spec.required_output_tokens
    ):
        raise ReviewPublisherError("validation_process_required_output_token_missing")


def _validate_child_environment_commitment(
    value: object,
    *,
    validation_runner: Any,
) -> dict[str, Any]:
    expected_keys = {
        "schema",
        "sha256",
        "key_count",
        "keys",
        "removed_secret_like_variable_count",
        "removed_secret_like_variable_name_sha256",
        "values_disclosed",
        "runner_injected_ephemeral_keys_excluded",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ReviewPublisherError("validation_environment_commitment_keys_not_exact")
    keys = value.get("keys")
    excluded = value.get("runner_injected_ephemeral_keys_excluded")
    if (
        value.get("schema") != validation_runner.ENVIRONMENT_SCHEMA
        or not isinstance(keys, list)
        or keys != sorted(set(keys))
        or any(not isinstance(item, str) or not item for item in keys)
        or type(value.get("key_count")) is not int
        or value["key_count"] != len(keys)
        or type(value.get("removed_secret_like_variable_count")) is not int
        or value["removed_secret_like_variable_count"] < 0
        or value.get("values_disclosed") is not False
        or excluded != list(validation_runner._RUNNER_INJECTED_ENVIRONMENT_KEYS)
        or any(validation_runner._SECRET_ENV_NAME_RE.search(item) for item in keys)
    ):
        raise ReviewPublisherError("validation_environment_commitment_invalid")
    _hex64(value.get("sha256"), "validation_environment")
    _hex64(
        value.get("removed_secret_like_variable_name_sha256"),
        "validation_environment_secret_names",
    )
    return dict(value)


def _validate_tool_metadata_process_evidence(
    value: object,
    *,
    spec: object,
    expected_work_order_tool_bindings: Mapping[str, Any],
) -> None:
    """Validate both nondeterministic Job outcomes embedded in one tool identity."""

    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner

    if not isinstance(spec, validation_runner.CommandSpec) or not isinstance(value, dict):
        raise ReviewPublisherError("validation_tool_identity_mapping_required")
    expected_keys = {
        "path",
        "bytes",
        "sha256",
        "version_argv",
        "version",
        "runtime_version_argv",
        "runtime_version",
        "version_process_containment",
        "runtime_version_process_containment",
        "environment_commitment",
        "python_tool_module",
        "work_order_binding_role",
        "work_order_binding_sha256",
        "work_order_module_binding_sha256",
    }
    if set(value) != expected_keys:
        raise ReviewPublisherError("validation_tool_identity_keys_not_exact")
    path = value.get("path")
    if (
        not isinstance(path, str)
        or not path
        or type(value.get("bytes")) is not int
        or value["bytes"] <= 0
    ):
        raise ReviewPublisherError("validation_tool_identity_scalar_invalid")
    tool_sha256 = _hex64(value.get("sha256"), "validation_tool_identity")
    expected_contract = validation_runner.WORK_ORDER_TOOL_CONTRACT_BY_COMMAND.get(spec.name)
    role = spec.work_order_tool_role
    if (
        expected_contract != (role, spec.python_tool_distribution)
        or not isinstance(role, str)
        or not role
        or value.get("work_order_binding_role") != role
    ):
        raise ReviewPublisherError("validation_tool_work_order_role_mismatch")
    submitted_binding = expected_work_order_tool_bindings.get(role)
    if not isinstance(submitted_binding, Mapping):
        raise ReviewPublisherError("validation_tool_work_order_binding_missing")
    try:
        observed_binding = validation_runner.work_order_tool_binding(
            role,
            Path(path),
            tool_sha256,
        )
    except (OSError, validation_runner.ValidationRunnerError) as exc:
        raise ReviewPublisherError("validation_tool_work_order_binding_invalid") from exc
    expected_module_binding = (
        observed_binding.get("python_tool_module")
        if spec.python_tool_distribution is not None
        else None
    )
    expected_module_sha256 = (
        hashlib.sha256(canonical_json_bytes(expected_module_binding)).hexdigest()
        if expected_module_binding is not None
        else None
    )
    if (
        dict(submitted_binding) != observed_binding
        or value.get("python_tool_module") != expected_module_binding
        or value.get("work_order_binding_sha256")
        != hashlib.sha256(canonical_json_bytes(observed_binding)).hexdigest()
        or value.get("work_order_module_binding_sha256") != expected_module_sha256
    ):
        raise ReviewPublisherError("validation_tool_work_order_binding_mismatch")
    _validate_child_environment_commitment(
        value.get("environment_commitment"),
        validation_runner=validation_runner,
    )

    cases = (
        (
            "version",
            "version_argv",
            "version_process_containment",
            f"metadata-tool-version-{spec.name}",
        ),
        (
            "runtime_version",
            "runtime_version_argv",
            "runtime_version_process_containment",
            f"metadata-runtime-version-{spec.name}",
        ),
    )
    for version_key, argv_key, evidence_key, metadata_name in cases:
        version = value.get(version_key)
        argv = value.get(argv_key)
        process = value.get(evidence_key)
        if (
            not isinstance(version, str)
            or not version
            or not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
            or os.path.normcase(os.path.abspath(argv[0])) != os.path.normcase(os.path.abspath(path))
            or not isinstance(process, dict)
        ):
            raise ReviewPublisherError("validation_tool_metadata_identity_invalid")
        metadata_spec = validation_runner.CommandSpec(
            metadata_name,
            tuple(argv),
            expected_exit_code=0,
        )
        stream_binding: dict[str, Any] = {}
        stream_values: list[str] = []
        for label in ("stdout", "stderr"):
            stream = process.get(label)
            if not isinstance(stream, str):
                raise ReviewPublisherError("validation_tool_metadata_stream_invalid")
            raw = stream.encode("utf-8")
            stream_binding[f"{label}_bytes"] = len(raw)
            stream_binding[f"{label}_sha256"] = hashlib.sha256(raw).hexdigest()
            stream_binding[f"{label}_tail"] = raw[-16_384:].decode("utf-8", errors="replace")
            stream_values.append(stream)
        _validate_validation_process_evidence(
            process,
            spec=metadata_spec,
            planned_tool={"path": path, "sha256": tool_sha256, "bytes": value["bytes"]},
            command_record=stream_binding,
        )
        if not any(stream.strip() == version for stream in stream_values) or any(
            stream.strip() not in {"", version} for stream in stream_values
        ):
            raise ReviewPublisherError("validation_tool_metadata_output_mismatch")


def _deterministic_command_plan_projection(value: object) -> dict[str, Any]:
    """Remove only Job-run-specific tool evidence and the hash derived from it."""

    if not isinstance(value, dict):
        raise ReviewPublisherError("code_validation_command_plan_mapping_required")
    expected_keys = {
        "repository",
        "project_root",
        "head",
        "tree",
        "commands",
        "environment_commitment",
        "observation_scope",
        "sha256",
    }
    if set(value) != expected_keys or not isinstance(value.get("commands"), list):
        raise ReviewPublisherError("code_validation_command_plan_keys_not_exact")
    projected = {key: item for key, item in value.items() if key not in {"commands", "sha256"}}
    projected_commands: list[dict[str, Any]] = []
    for command in value["commands"]:
        if not isinstance(command, dict) or not isinstance(command.get("tool"), dict):
            raise ReviewPublisherError("code_validation_planned_command_mapping_required")
        projected_tool = {
            key: item
            for key, item in command["tool"].items()
            if key
            not in {
                "version_process_containment",
                "runtime_version_process_containment",
            }
        }
        projected_commands.append(
            {
                **{key: item for key, item in command.items() if key != "tool"},
                "tool": projected_tool,
            }
        )
    projected["commands"] = projected_commands
    return projected


def _validate_output_parent_commitment(
    value: object,
    *,
    validation_runner: Any,
    expected_parent: Path | None = None,
    forbidden_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    expected_keys = {"schema", "normalized_path", "sha256"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ReviewPublisherError("validation_output_parent_commitment_keys_not_exact")
    normalized = value.get("normalized_path")
    if (
        value.get("schema") != validation_runner.OUTPUT_PARENT_SCHEMA
        or not isinstance(normalized, str)
        or not normalized
    ):
        raise ReviewPublisherError("validation_output_parent_commitment_invalid")
    digest = _hex64(value.get("sha256"), "validation_output_parent")
    payload = {"schema": value["schema"], "normalized_path": normalized}
    if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != digest:
        raise ReviewPublisherError("validation_output_parent_commitment_digest_mismatch")
    if expected_parent is not None:
        resolved = expected_parent.resolve(strict=True)
        observed_normalized = os.path.normcase(os.path.normpath(str(resolved)))
        if normalized != observed_normalized:
            raise ReviewPublisherError("validation_output_parent_commitment_path_mismatch")
        for root in forbidden_roots:
            root_resolved = root.resolve(strict=True)
            if resolved == root_resolved or root_resolved in resolved.parents:
                raise ReviewPublisherError("validation_output_parent_inside_repository")
    return dict(value)


def _validate_handle_identity(
    value: object,
    *,
    expected_path: Path,
    expected_size: int | None,
    is_directory: bool,
) -> dict[str, Any]:
    keys = {
        "final_path",
        "volume_serial_number",
        "file_id_hex",
        "size",
        "link_count",
        "attributes",
        "reparse_tag",
        "file_type",
        "owner_sid",
        "security_descriptor_sha256",
        "dacl_present",
        "dacl_protected",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ReviewPublisherError("validation_publication_handle_identity_keys_not_exact")
    if (
        _comparable_windows_path(str(value.get("final_path", "")))
        != _comparable_windows_path(expected_path)
        or type(value.get("volume_serial_number")) is not int
        or value["volume_serial_number"] < (0 if is_directory else 1)
        or not isinstance(value.get("file_id_hex"), str)
        or not value["file_id_hex"]
        or type(value.get("size")) is not int
        or value["size"] < 0
        or (expected_size is not None and value["size"] != expected_size)
        or type(value.get("link_count")) is not int
        or (value["link_count"] < 1 if is_directory else value["link_count"] != 1)
        or type(value.get("attributes")) is not int
        or bool(value["attributes"] & 0x10) is not is_directory
        or type(value.get("reparse_tag")) is not int
        or value["reparse_tag"] != 0
        or type(value.get("file_type")) is not int
        or value["file_type"] != 1
        or not isinstance(value.get("owner_sid"), str)
        or not value["owner_sid"]
        or not isinstance(value.get("security_descriptor_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["security_descriptor_sha256"]) is None
        or value.get("dacl_present") is not True
        or type(value.get("dacl_protected")) is not bool
        or (not is_directory and value.get("dacl_protected") is not True)
    ):
        raise ReviewPublisherError("validation_publication_handle_identity_invalid")
    return dict(value)


def _validate_publication_receipt(
    value: object,
    *,
    expected_path: Path,
    expected_sha256: str,
    expected_bytes: int,
) -> dict[str, Any]:
    keys = {
        "final_path",
        "temporary_leaf",
        "sha256",
        "bytes",
        "identity",
        "directory_identity",
        "file_flush_count",
        "directory_flush_count",
        "directory_flush_succeeded",
        "replace_if_exists",
        "same_handle_readback",
        "file_identity_stable_across_rename",
        "power_loss_durability_proven",
        "same_token_hostile_admin_protected",
        "go_evidence_eligible",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ReviewPublisherError("validation_publication_receipt_keys_not_exact")
    temporary_leaf = value.get("temporary_leaf")
    if (
        _comparable_windows_path(str(value.get("final_path", "")))
        != _comparable_windows_path(expected_path)
        or value.get("sha256") != expected_sha256
        or type(value.get("bytes")) is not int
        or value["bytes"] != expected_bytes
        or not isinstance(temporary_leaf, str)
        or not temporary_leaf
        or ntpath.basename(temporary_leaf) != temporary_leaf
        or not temporary_leaf.startswith(f".{expected_path.name}.")
        or not temporary_leaf.endswith(".partial")
        or type(value.get("file_flush_count")) is not int
        or value["file_flush_count"] != 2
        or type(value.get("directory_flush_count")) is not int
        or value["directory_flush_count"] != 1
        or value.get("directory_flush_succeeded") is not True
        or value.get("replace_if_exists") is not False
        or value.get("same_handle_readback") is not True
        or value.get("file_identity_stable_across_rename") is not True
        or value.get("power_loss_durability_proven") is not False
        or value.get("same_token_hostile_admin_protected") is not False
        or value.get("go_evidence_eligible") is not False
    ):
        raise ReviewPublisherError("validation_publication_receipt_invalid")
    identity = _validate_handle_identity(
        value.get("identity"),
        expected_path=expected_path,
        expected_size=expected_bytes,
        is_directory=False,
    )
    directory = _validate_handle_identity(
        value.get("directory_identity"),
        expected_path=expected_path.parent,
        expected_size=None,
        is_directory=True,
    )
    if identity["volume_serial_number"] != directory["volume_serial_number"]:
        raise ReviewPublisherError("validation_publication_cross_volume_identity")
    return dict(value)


def _validate_runner_untracked_inventory(
    value: object,
    *,
    repository: Path,
    expected_count: int,
    expected_path_list_sha256: str,
    expected_content_inventory_sha256: str,
) -> dict[str, Any]:
    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner

    expected_keys = {
        "schema",
        "count",
        "paths",
        "path_list_sha256",
        "content_inventory",
        "content_inventory_sha256",
        "import_active_shadow_path_count",
        "ignored_import_active_shadow_path_count",
        "expected",
        "matches_expected",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ReviewPublisherError("validation_untracked_inventory_keys_not_exact")
    paths = value.get("paths")
    if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
        raise ReviewPublisherError("validation_untracked_inventory_paths_invalid")
    try:
        observed = validation_runner._inventory_from_untracked_paths(repository, paths)
    except (OSError, RuntimeError, validation_runner.ValidationRunnerError) as exc:
        raise ReviewPublisherError("validation_untracked_inventory_live_readback_failed") from exc
    expected = {
        "count": expected_count,
        "path_list_sha256": expected_path_list_sha256,
        "content_inventory_sha256": expected_content_inventory_sha256,
    }
    if (
        value.get("schema") != validation_runner.UNTRACKED_INVENTORY_SCHEMA
        or value.get("count") != expected_count
        or value.get("path_list_sha256") != expected_path_list_sha256
        or value.get("content_inventory_sha256") != expected_content_inventory_sha256
        or value.get("content_inventory") != observed["content_inventory"]
        or value.get("import_active_shadow_path_count") != 0
        or value.get("ignored_import_active_shadow_path_count") != 0
        or value.get("expected") != expected
        or value.get("matches_expected") is not True
        or any(value.get(key) != observed[key] for key in observed)
    ):
        raise ReviewPublisherError("validation_untracked_inventory_mismatch")
    return dict(value)


def validate_code_summary(
    value: Mapping[str, Any],
    *,
    repository: Path,
    project_root: Path,
    expected_head: str,
    expected_tree: str,
    python_general: Path,
    python_general_sha256: str,
    python_host: Path,
    python_host_sha256: str,
    python_ruff: Path,
    python_ruff_sha256: str,
    kubectl_executable: Path,
    kubectl_executable_sha256: str,
    git_executable: Path,
    git_executable_sha256: str,
    powershell_executable: Path,
    powershell_executable_sha256: str,
    expected_untracked_count: int,
    expected_untracked_path_list_sha256: str,
    expected_untracked_content_inventory_sha256: str,
    expected_summary_sha256: str,
    external_work_order: Path,
    external_work_order_sha256: str,
    trusted_outer: Path,
    trusted_outer_sha256: str,
    replay_adapter: ReplayConsumeAdapter | None = None,
    production_authority_verified: bool = False,
) -> dict[str, Any]:
    """Validate local, non-attested code checks against an exact live tool plan.

    This deliberately does not promote the records to external execution
    attestation.  Planned validation children must carry kernel Job accounting;
    the small identity/read-back metadata children remain timeout-bounded only.
    """

    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner

    expected_keys = {
        "schema",
        "status",
        "decision",
        "credit",
        "evidence_scope",
        "go_evidence_eligible",
        "runtime_identity_stability",
        "immutable_checkout_namespace_authority",
        "runtime_stdlib_native_closure_verified",
        "validation_run_uuid",
        "validation_attempt_uuid",
        "handoff_challenge_sha256",
        "issued_at_utc",
        "completed_at_utc",
        "expires_at_utc",
        "external_work_order_binding",
        "replay_consumption",
        "repository",
        "project_root",
        "head",
        "tree",
        "command_plan",
        "command_plan_sha256",
        "environment_commitment",
        "output_parent_commitment",
        "independent_executable_pins",
        "expected_untracked_inventory",
        "metadata_children",
        "metadata_child_call_count",
        "commands",
        "planned_command_count",
        "executed_command_count",
        "validation_child_call_count",
        "not_run_commands",
        "terminal_containment_latch",
        "terminal_latch_reason",
        "followup_child_count_after_containment_latch",
        "live_call_telemetry",
        "completion_marker_created",
        "success_marker_created",
        "r8_authorized",
    }
    if set(value) != expected_keys:
        raise ReviewPublisherError("code_validation_summary_keys_not_exact")
    summary_sha256 = hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()
    expected_summary_sha256 = _hex64(expected_summary_sha256, "expected_validation_summary")
    repository = repository.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    expected_head = _hex40(expected_head, "code_validation_head")
    expected_tree = _hex40(expected_tree, "code_validation_tree")
    if (
        isinstance(expected_untracked_count, bool)
        or not isinstance(expected_untracked_count, int)
        or expected_untracked_count < 0
    ):
        raise ReviewPublisherError("code_validation_expected_untracked_count_invalid")
    expected_untracked_path_list_sha256 = _hex64(
        expected_untracked_path_list_sha256,
        "code_validation_expected_untracked_path_list",
    )
    expected_untracked_content_inventory_sha256 = _hex64(
        expected_untracked_content_inventory_sha256,
        "code_validation_expected_untracked_content_inventory",
    )
    try:
        work_order_value, work_order_raw = validation_runner._read_canonical_mapping(
            external_work_order,
            external_work_order_sha256,
            "external_work_order",
        )
    except (OSError, validation_runner.ValidationRunnerError) as exc:
        raise ReviewPublisherError("code_validation_external_work_order_invalid") from exc
    executable_pins = {
        "kubectl": validate_independent_executable_pin(
            kubectl_executable,
            kubectl_executable_sha256,
            label="kubectl_executable",
        ),
        "python_general": validate_independent_executable_pin(
            python_general,
            python_general_sha256,
            label="python_general",
        ),
        "python_host": validate_independent_executable_pin(
            python_host,
            python_host_sha256,
            label="python_host",
        ),
        "python_ruff": validate_independent_executable_pin(
            python_ruff,
            python_ruff_sha256,
            label="python_ruff",
        ),
        "git": validate_independent_executable_pin(
            git_executable,
            git_executable_sha256,
            label="git_executable",
        ),
        "powershell": validate_independent_executable_pin(
            powershell_executable,
            powershell_executable_sha256,
            label="powershell_executable",
        ),
    }
    submitted_pin_payload = value.get("independent_executable_pins")
    expected_pin_payload = {
        name: {"path": str(pin["path"]), "sha256": pin["sha256"]}
        for name, pin in sorted(executable_pins.items())
    }
    expected_tool_file_bindings = {
        name: validation_runner.work_order_tool_binding(name, Path(pin["path"]), str(pin["sha256"]))
        for name, pin in sorted(executable_pins.items())
    }
    expected_untracked_payload = {
        "count": expected_untracked_count,
        "path_list_sha256": expected_untracked_path_list_sha256,
        "content_inventory_sha256": expected_untracked_content_inventory_sha256,
    }
    if submitted_pin_payload != expected_pin_payload:
        raise ReviewPublisherError("code_validation_independent_executable_pins_mismatch")
    work_order_keys = {
        "schema",
        "authority_scope",
        "authority_verified",
        "validation_run_uuid",
        "validation_attempt_uuid",
        "handoff_challenge_sha256",
        "issued_at_utc",
        "expires_at_utc",
        "expected_head",
        "expected_tree",
        "tool_file_bindings",
        "code_file_bindings",
        "immutable_checkout_namespace_authority",
        "runtime_stdlib_native_closure_verified",
        "command_invocation_sha256",
        "pycache_prefix",
    }
    work_order_binding = value.get("external_work_order_binding")
    expected_code_file_bindings = validation_runner.work_order_code_file_bindings(
        trusted_outer, trusted_outer_sha256
    )
    expected_work_order_binding = {
        "path": str(Path(external_work_order).resolve(strict=True)),
        "bytes": len(work_order_raw),
        "sha256": hashlib.sha256(work_order_raw).hexdigest(),
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "payload": work_order_value,
    }
    if (
        set(work_order_value) != work_order_keys
        or work_order_value.get("schema") != validation_runner.WORK_ORDER_SCHEMA
        or work_order_value.get("authority_scope") != "internal_non_authoritative"
        or work_order_value.get("authority_verified") is not False
        or work_order_value.get("immutable_checkout_namespace_authority") is not False
        or work_order_value.get("runtime_stdlib_native_closure_verified") is not False
        or work_order_value.get("expected_head") != expected_head
        or work_order_value.get("expected_tree") != expected_tree
        or work_order_value.get("tool_file_bindings") != expected_tool_file_bindings
        or work_order_value.get("code_file_bindings") != expected_code_file_bindings
        or not Path(str(work_order_value.get("pycache_prefix", ""))).is_absolute()
        or Path(str(work_order_value.get("pycache_prefix", ""))).name
        != f".pre-r8-r7s7-pycache-{work_order_value.get('validation_run_uuid')}"
        or os.path.lexists(Path(str(work_order_value.get("pycache_prefix", ""))))
        or work_order_binding != expected_work_order_binding
    ):
        raise ReviewPublisherError("code_validation_external_work_order_binding_mismatch")
    try:
        validation_run_uuid = validation_runner._strict_uuid(
            value.get("validation_run_uuid"), "validation_run"
        )
        validation_attempt_uuid = validation_runner._strict_uuid(
            value.get("validation_attempt_uuid"), "validation_attempt"
        )
        issued_at = validation_runner._strict_utc(value.get("issued_at_utc"), "issued_at")
        completed_at = validation_runner._strict_utc(value.get("completed_at_utc"), "completed_at")
        expires_at = validation_runner._strict_utc(value.get("expires_at_utc"), "expires_at")
    except validation_runner.ValidationRunnerError as exc:
        raise ReviewPublisherError("code_validation_handoff_window_invalid") from exc
    if (
        validation_run_uuid == validation_attempt_uuid
        or work_order_value.get("validation_run_uuid") != validation_run_uuid
        or work_order_value.get("validation_attempt_uuid") != validation_attempt_uuid
        or value.get("handoff_challenge_sha256")
        != _hex64(work_order_value.get("handoff_challenge_sha256"), "handoff_challenge")
        or value.get("issued_at_utc") != work_order_value.get("issued_at_utc")
        or value.get("expires_at_utc") != work_order_value.get("expires_at_utc")
        or not (issued_at <= completed_at < expires_at)
        or datetime.now().astimezone() >= expires_at
    ):
        raise ReviewPublisherError("code_validation_handoff_binding_or_expiry_mismatch")
    replay_key = validation_replay_key(
        validation_run_uuid=validation_run_uuid,
        validation_attempt_uuid=validation_attempt_uuid,
        handoff_challenge_sha256=str(value["handoff_challenge_sha256"]),
        work_order_sha256=str(expected_work_order_binding["sha256"]),
    )
    if value.get("replay_consumption") != {
        "status": "not_consumed",
        "adapter_scope": "none",
        "authority_verified": False,
        "replay_key": replay_key,
    }:
        raise ReviewPublisherError("code_validation_replay_consumption_claim_invalid")
    if production_authority_verified:
        raise ReviewPublisherError(
            "production_summary_requires_separate_external_authority_admission"
        )
    if replay_adapter is not None:
        raise ReviewPublisherError("internal_validation_replay_adapter_forbidden")
    if value.get("expected_untracked_inventory") != expected_untracked_payload:
        raise ReviewPublisherError("code_validation_expected_untracked_inventory_mismatch")
    if (
        value.get("schema") != VALIDATION_SCHEMA
        or value.get("status") != "PASS"
        or value.get("decision") != "NO-GO"
        or value.get("credit") != "zero_credit"
        or value.get("evidence_scope") != "internal_non_authoritative"
        or value.get("go_evidence_eligible") is not False
        or value.get("runtime_identity_stability") != "unproven"
        or value.get("immutable_checkout_namespace_authority") is not False
        or value.get("runtime_stdlib_native_closure_verified") is not False
        or os.path.normcase(str(value.get("repository"))) != os.path.normcase(str(repository))
        or os.path.normcase(str(value.get("project_root"))) != os.path.normcase(str(project_root))
        or value.get("head") != expected_head
        or value.get("tree") != expected_tree
    ):
        raise ReviewPublisherError("code_validation_summary_identity_mismatch")

    plan = value.get("command_plan")
    if not isinstance(plan, dict):
        raise ReviewPublisherError("code_validation_command_plan_mapping_required")
    plan_digest = _hex64(value.get("command_plan_sha256"), "command_plan")
    plan_payload = {key: item for key, item in plan.items() if key != "sha256"}
    recomputed_plan_digest = hashlib.sha256(canonical_json_bytes(plan_payload)).hexdigest()
    if plan.get("sha256") != plan_digest or recomputed_plan_digest != plan_digest:
        raise ReviewPublisherError("code_validation_command_plan_digest_mismatch")
    planned_commands = plan.get("commands")
    if not isinstance(planned_commands, list):
        raise ReviewPublisherError("code_validation_planned_commands_required")
    by_name = {
        item.get("name"): item
        for item in planned_commands
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if set(by_name) != REQUIRED_VALIDATION_COMMANDS or len(by_name) != len(planned_commands):
        raise ReviewPublisherError("code_validation_planned_command_set_mismatch")
    try:
        specs = validation_runner.build_command_specs(
            repository=repository,
            project_root=project_root,
            python_general=Path(executable_pins["python_general"]["path"]),
            python_host=Path(executable_pins["python_host"]["path"]),
            python_ruff=Path(executable_pins["python_ruff"]["path"]),
            kubectl_executable=Path(executable_pins["kubectl"]["path"]),
            git_executable=Path(executable_pins["git"]["path"]),
            git_executable_sha256=str(executable_pins["git"]["sha256"]),
            powershell_executable=Path(executable_pins["powershell"]["path"]),
            pycache_prefix=Path(str(work_order_value["pycache_prefix"])),
        )
    except (OSError, TypeError, validation_runner.ValidationRunnerError) as exc:
        raise ReviewPublisherError("code_validation_static_plan_reconstruction_failed") from exc
    if work_order_value.get("command_invocation_sha256") != (
        validation_runner.command_invocation_commitment(specs)
    ):
        raise ReviewPublisherError("code_validation_work_order_command_invocation_mismatch")

    command_pin_names = {
        name: role
        for name, (
            role,
            _distribution,
        ) in validation_runner.WORK_ORDER_TOOL_CONTRACT_BY_COMMAND.items()
    }
    if command_pin_names != {spec.name: spec.work_order_tool_role for spec in specs}:
        raise ReviewPublisherError("code_validation_command_tool_contract_mismatch")
    expected_pin_sha256_by_command = {
        name: executable_pins[pin_name]["sha256"] for name, pin_name in command_pin_names.items()
    }
    expected_plan_command_keys = {
        "name",
        "argv",
        "cwd",
        "expected_exit_code",
        "required_output_tokens",
        "wrapper_timeout_seconds",
        "residual_repoll_seconds",
        "stream_drain_seconds",
        "tool",
    }
    for submitted, spec in zip(planned_commands, specs, strict=True):
        if not isinstance(submitted, dict) or set(submitted) != expected_plan_command_keys:
            raise ReviewPublisherError("code_validation_planned_command_mapping_required")
        expected_static = {
            "name": spec.name,
            "argv": list(spec.argv),
            "cwd": str(project_root),
            "expected_exit_code": spec.expected_exit_code,
            "required_output_tokens": list(spec.required_output_tokens),
            "wrapper_timeout_seconds": spec.wrapper_timeout_seconds,
            "residual_repoll_seconds": validation_runner.VALIDATION_RESIDUAL_REPOLL_SECONDS,
            "stream_drain_seconds": validation_runner.VALIDATION_STREAM_DRAIN_SECONDS,
        }
        if any(submitted.get(key) != expected for key, expected in expected_static.items()):
            raise ReviewPublisherError("code_validation_static_plan_mismatch")
        _validate_tool_metadata_process_evidence(
            submitted.get("tool"),
            spec=spec,
            expected_work_order_tool_bindings=expected_tool_file_bindings,
        )
        if spec.name in command_pin_names:
            pin = executable_pins[command_pin_names[spec.name]]
            tool = submitted["tool"]
            if (
                _comparable_windows_path(str(tool.get("path", "")))
                != _comparable_windows_path(str(pin["path"]))
                or tool.get("sha256") != pin["sha256"]
                or tool.get("bytes") != pin["bytes"]
            ):
                raise ReviewPublisherError("code_validation_independent_executable_pin_mismatch")

    environment_commitment = _validate_child_environment_commitment(
        value.get("environment_commitment"),
        validation_runner=validation_runner,
    )
    if plan.get("environment_commitment") != environment_commitment or any(
        not isinstance(item, dict)
        or item.get("tool", {}).get("environment_commitment") != environment_commitment
        for item in planned_commands
    ):
        raise ReviewPublisherError("code_validation_environment_commitment_mismatch")
    output_parent_commitment = _validate_output_parent_commitment(
        value.get("output_parent_commitment"),
        validation_runner=validation_runner,
    )

    expected_metadata = validation_runner.expected_success_metadata_child_sequence(
        specs,
        git_executable=Path(executable_pins["git"]["path"]),
    )
    metadata_children = value.get("metadata_children")
    if (
        not isinstance(metadata_children, list)
        or len(metadata_children) != len(expected_metadata)
        or value.get("metadata_child_call_count") != len(expected_metadata)
    ):
        raise ReviewPublisherError("code_validation_metadata_children_not_exact")
    executable_pin_by_path = {
        _comparable_windows_path(str(pin["path"])): pin for pin in executable_pins.values()
    }
    for child, (expected_name, expected_phase, expected_argv) in zip(
        metadata_children,
        expected_metadata,
        strict=True,
    ):
        if (
            not isinstance(child, dict)
            or set(child) != {"name", "phase", "status", "child_invoked", "process_containment"}
            or child.get("name") != expected_name
            or child.get("phase") != expected_phase
            or child.get("status") != "PASS"
            or child.get("child_invoked") is not True
        ):
            raise ReviewPublisherError("code_validation_metadata_child_payload_mismatch")
        process = child.get("process_containment")
        if not isinstance(process, dict):
            raise ReviewPublisherError("code_validation_metadata_process_mapping_required")
        metadata_spec = validation_runner.CommandSpec(f"metadata-{expected_name}", expected_argv)
        planned_tool = executable_pin_by_path.get(_comparable_windows_path(expected_argv[0]))
        if planned_tool is None:
            raise ReviewPublisherError(
                "code_validation_metadata_executable_not_independently_pinned"
            )
        metadata_stream_binding: dict[str, Any] = {}
        for label in ("stdout", "stderr"):
            stream = process.get(label)
            if not isinstance(stream, str):
                raise ReviewPublisherError("code_validation_metadata_stream_invalid")
            raw = stream.encode("utf-8")
            metadata_stream_binding[f"{label}_bytes"] = len(raw)
            metadata_stream_binding[f"{label}_sha256"] = hashlib.sha256(raw).hexdigest()
            metadata_stream_binding[f"{label}_tail"] = raw[-16_384:].decode(
                "utf-8", errors="replace"
            )
        _validate_validation_process_evidence(
            process,
            spec=metadata_spec,
            planned_tool=planned_tool,
            command_record=metadata_stream_binding,
        )

    commands = value.get("commands")
    if not isinstance(commands, list) or len(commands) != len(specs):
        raise ReviewPublisherError("code_validation_commands_required")
    evidence_parents: set[Path] = set()
    normalized_commands: list[dict[str, Any]] = []
    record_keys = {
        "schema",
        "name",
        "status",
        "exit_code",
        "expected_exit_code",
        "argv",
        "cwd",
        "repository",
        "repository_head_before",
        "repository_head_after",
        "repository_tree_before",
        "repository_tree_after",
        "tracked_clean_before",
        "tracked_clean_after",
        "untracked_inventory_before",
        "untracked_inventory_after",
        "command_plan_sha256",
        "tool",
        "environment_commitment",
        "output_parent_commitment",
        "started_at_utc",
        "ended_at_utc",
        "duration_ns",
        "stdout_bytes",
        "stdout_sha256",
        "stdout_tail",
        "stderr_bytes",
        "stderr_sha256",
        "stderr_tail",
        "stream_encoding",
        "stream_hash_scope",
        "stream_boundary_token_synthesis_allowed",
        "required_tokens_present_in_individual_streams",
        "secret_like_output_detected",
        "process_containment",
        "derived_containment_errors",
        "containment_cleared_before_followup",
        "followup_child_count_after_containment_latch",
        "forced_termination_attempts",
        "automatic_retry_count",
        "orchestrator_prohibited_live_command_calls",
        "live_call_observation_scope",
    }
    for index, (command, spec, planned) in enumerate(
        zip(commands, specs, planned_commands, strict=True), start=1
    ):
        if (
            not isinstance(command, dict)
            or set(command)
            != {
                "name",
                "status",
                "exit_code",
                "expected_exit_code",
                "evidence_path",
                "evidence_bytes",
                "evidence_sha256",
                "publication",
            }
            or command.get("name") != spec.name
            or command.get("status") != "PASS"
            or type(command.get("exit_code")) is not int
            or command.get("exit_code") != spec.expected_exit_code
            or command.get("expected_exit_code") != spec.expected_exit_code
            or type(command.get("evidence_bytes")) is not int
            or command["evidence_bytes"] <= 0
        ):
            raise ReviewPublisherError("code_validation_command_not_exact_pass")
        expected_sha256 = _hex64(command["evidence_sha256"], "command_evidence")
        path = Path(command["evidence_path"]).resolve(strict=True)
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        if not path.is_file() or attributes & 0x400 or path.name != f"{index:02d}-{spec.name}.json":
            raise ReviewPublisherError("code_validation_evidence_regular_file_required")
        evidence_parents.add(path.parent)
        if path.stat().st_size != command["evidence_bytes"] or sha256_file(path) != expected_sha256:
            raise ReviewPublisherError("code_validation_evidence_identity_mismatch")
        publication = _validate_publication_receipt(
            command.get("publication"),
            expected_path=path,
            expected_sha256=expected_sha256,
            expected_bytes=command["evidence_bytes"],
        )
        record = read_json_mapping(path, f"command_evidence_{command['name']}")
        if path.read_bytes() != canonical_json_bytes(dict(record)):
            raise ReviewPublisherError("code_validation_command_evidence_not_canonical")
        if set(record) != record_keys:
            raise ReviewPublisherError("code_validation_evidence_keys_not_exact")
        fixed = {
            "schema": COMMAND_EVIDENCE_SCHEMA,
            "name": spec.name,
            "status": "PASS",
            "exit_code": spec.expected_exit_code,
            "expected_exit_code": spec.expected_exit_code,
            "argv": list(spec.argv),
            "cwd": str(project_root),
            "repository": str(repository),
            "repository_head_before": expected_head,
            "repository_head_after": expected_head,
            "repository_tree_before": expected_tree,
            "repository_tree_after": expected_tree,
            "tracked_clean_before": True,
            "tracked_clean_after": True,
            "command_plan_sha256": plan_digest,
            "tool": planned["tool"],
            "environment_commitment": environment_commitment,
            "output_parent_commitment": output_parent_commitment,
            "stream_encoding": "utf-8",
            "stream_hash_scope": "decoded_text_reencoded_utf8_not_raw_pipe_bytes",
            "stream_boundary_token_synthesis_allowed": False,
            "required_tokens_present_in_individual_streams": True,
            "secret_like_output_detected": False,
            "derived_containment_errors": [],
            "containment_cleared_before_followup": True,
            "followup_child_count_after_containment_latch": 0,
            "forced_termination_attempts": 0,
            "automatic_retry_count": 0,
            "orchestrator_prohibited_live_command_calls": 0,
            "live_call_observation_scope": validation_runner.VALIDATION_OBSERVATION_SCOPE,
        }
        if any(record.get(key) != expected for key, expected in fixed.items()):
            raise ReviewPublisherError("code_validation_evidence_payload_mismatch")
        untracked_before = _validate_runner_untracked_inventory(
            record.get("untracked_inventory_before"),
            repository=repository,
            expected_count=expected_untracked_count,
            expected_path_list_sha256=expected_untracked_path_list_sha256,
            expected_content_inventory_sha256=expected_untracked_content_inventory_sha256,
        )
        untracked_after = _validate_runner_untracked_inventory(
            record.get("untracked_inventory_after"),
            repository=repository,
            expected_count=expected_untracked_count,
            expected_path_list_sha256=expected_untracked_path_list_sha256,
            expected_content_inventory_sha256=expected_untracked_content_inventory_sha256,
        )
        if untracked_after != untracked_before:
            raise ReviewPublisherError("code_validation_untracked_inventory_changed")
        _validate_validation_process_evidence(
            record.get("process_containment"),
            spec=spec,
            planned_tool=planned["tool"],
            command_record=record,
        )
        for label in ("stdout", "stderr"):
            if (
                type(record.get(f"{label}_bytes")) is not int
                or record[f"{label}_bytes"] < 0
                or not isinstance(record.get(f"{label}_tail"), str)
            ):
                raise ReviewPublisherError("code_validation_stream_metadata_invalid")
            _hex64(record.get(f"{label}_sha256"), f"command_{label}")
        if type(record.get("duration_ns")) is not int or record["duration_ns"] <= 0:
            raise ReviewPublisherError("code_validation_duration_invalid")
        try:
            started = datetime.fromisoformat(str(record["started_at_utc"]).replace("Z", "+00:00"))
            ended = datetime.fromisoformat(str(record["ended_at_utc"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReviewPublisherError("code_validation_timestamp_invalid") from exc
        if started.tzinfo is None or ended.tzinfo is None or ended < started:
            raise ReviewPublisherError("code_validation_timestamp_order_invalid")
        normalized_commands.append({**dict(command), "publication": publication})
    if len(evidence_parents) != 1:
        raise ReviewPublisherError("code_validation_evidence_directory_not_exact")
    evidence_directory = next(iter(evidence_parents))
    _validate_output_parent_commitment(
        output_parent_commitment,
        validation_runner=validation_runner,
        expected_parent=evidence_directory.parent,
        forbidden_roots=(repository, project_root),
    )

    if (
        value.get("planned_command_count") != len(specs)
        or value.get("executed_command_count") != len(specs)
        or value.get("validation_child_call_count") != len(specs)
        or value.get("not_run_commands") != []
        or value.get("terminal_containment_latch") is not False
        or value.get("terminal_latch_reason") is not None
        or value.get("followup_child_count_after_containment_latch") != 0
    ):
        raise ReviewPublisherError("code_validation_containment_closure_not_exact")

    telemetry = value.get("live_call_telemetry")
    if not isinstance(telemetry, Mapping) or set(telemetry) != {
        "path",
        "bytes",
        "sha256",
        "payload",
    }:
        raise ReviewPublisherError("code_validation_live_call_telemetry_mapping_required")
    try:
        telemetry_value, telemetry_raw = validation_runner._read_canonical_mapping(
            Path(str(telemetry.get("path"))),
            str(telemetry.get("sha256")),
            "live_call_telemetry",
        )
    except (OSError, validation_runner.ValidationRunnerError) as exc:
        raise ReviewPublisherError("code_validation_live_call_telemetry_readback_failed") from exc
    telemetry_counts = telemetry_value.get("counts")
    if (
        telemetry.get("bytes") != len(telemetry_raw)
        or telemetry.get("payload") != telemetry_value
        or telemetry_value.get("schema") != validation_runner.LIVE_TELEMETRY_SCHEMA
        or telemetry_value.get("authority_scope") != "internal_non_authoritative"
        or telemetry_value.get("authority_verified") is not False
        or telemetry_value.get("observation_state") != "unknown"
        or telemetry_value.get("observation_scope") != "internal_non_authoritative"
        or telemetry_value.get("collector_authority_verified") is not False
        or not isinstance(telemetry_counts, Mapping)
        or set(telemetry_counts) != REQUIRED_ZERO_LIVE_CALLS
        or any(item is not None for item in telemetry_counts.values())
        or telemetry_value.get("raw_events_sha256")
        != hashlib.sha256(canonical_json_bytes([])).hexdigest()
    ):
        raise ReviewPublisherError("code_validation_live_call_telemetry_unobserved_invalid")
    if value.get("completion_marker_created") is not False:
        raise ReviewPublisherError("code_validation_completion_marker_forbidden")
    if value.get("success_marker_created") is not False or value.get("r8_authorized") is not False:
        raise ReviewPublisherError("code_validation_success_or_r8_authorization_forbidden")

    # Execute only the independently pinned interpreter paths after every
    # submitted artifact and its terminal publication envelope has been
    # structurally validated by the caller.  No argv[0] from the summary is
    # ever adopted as an executable path.
    live_child_environment = validation_runner.build_child_environment(
        project_root,
        tuple(str(pin["path"]) for pin in executable_pins.values()),
    )
    try:
        validation_runner.validate_pinned_child_path_resolution(
            live_child_environment,
            command_name="kubectl",
            pin=validation_runner.ExecutablePin(
                label="kubectl",
                path=Path(executable_pins["kubectl"]["path"]),
                sha256=str(executable_pins["kubectl"]["sha256"]),
            ),
        )
    except validation_runner.ValidationRunnerError as exc:
        raise ReviewPublisherError("code_validation_child_path_tool_binding_invalid") from exc
    metadata_expected_sha256 = {
        child_name: expected_pin_sha256_by_command[spec.name]
        for spec in specs
        for child_name in (
            f"tool-version-{spec.name}",
            f"runtime-version-{spec.name}",
        )
    }

    class _ImmediatePublisherMetadataLedger(list[dict[str, Any]]):
        """Mirror each completed metadata child into the global ledger immediately."""

        def __init__(self) -> None:
            super().__init__()
            self.publisher_entries: list[dict[str, Any]] = []

        def append(self, record: dict[str, Any]) -> None:
            super().append(record)
            name_value = record.get("name") if isinstance(record, Mapping) else None
            phase_value = record.get("phase") if isinstance(record, Mapping) else None
            process_value = (
                record.get("process_containment") if isinstance(record, Mapping) else None
            )
            expected_sha256 = metadata_expected_sha256.get(str(name_value), "0" * 64)
            try:
                sanitized, secret_detected = validation_runner._sanitize_for_evidence(
                    dict(process_value) if isinstance(process_value, Mapping) else {},
                    live_child_environment.secret_values,
                )
            except BaseException:
                sanitized = {"metadata_process_evidence_serialization": "unproven"}
                secret_detected = True
            if not isinstance(sanitized, dict):
                sanitized = {"metadata_process_evidence_mapping": "invalid"}
                secret_detected = True
            entry = _redacted_publisher_child_evidence(
                sanitized,
                purpose=f"live-command-plan:{phase_value or 'unknown'}",
                clean=False,
                environment_commitment=live_child_environment.commitment,
                expected_executable_sha256=expected_sha256,
                secret_like_output_detected=bool(secret_detected),
            )
            entry["metadata_child_name"] = name_value
            entry["metadata_phase"] = phase_value
            publisher_entry = _append_publisher_child_execution(entry)
            self.publisher_entries.append(publisher_entry)
            bind_terminal_observer = getattr(record, "bind_terminal_observer", None)
            if callable(bind_terminal_observer):

                def mirror_terminal(terminal_record: Mapping[str, Any]) -> None:
                    terminal_process = terminal_record.get("process_containment")
                    try:
                        terminal_sanitized, terminal_secret_detected = (
                            validation_runner._sanitize_for_evidence(
                                dict(terminal_process)
                                if isinstance(terminal_process, Mapping)
                                else {},
                                live_child_environment.secret_values,
                            )
                        )
                    except BaseException as mirror_error:
                        terminal_sanitized = {
                            "terminal_process_evidence_recorded": False,
                            "mirror_exception_type": (
                                f"{type(mirror_error).__module__}.{type(mirror_error).__qualname__}"
                            ),
                            "exception_message_disclosed": False,
                        }
                        terminal_secret_detected = True
                    if not isinstance(terminal_sanitized, dict):
                        terminal_sanitized = {
                            "terminal_process_evidence_recorded": False,
                            "terminal_process_evidence_mapping": "invalid",
                        }
                        terminal_secret_detected = True
                    terminal_entry = _redacted_publisher_child_evidence(
                        terminal_sanitized,
                        purpose=f"live-command-plan:{terminal_record.get('phase') or 'unknown'}",
                        clean=False,
                        environment_commitment=live_child_environment.commitment,
                        expected_executable_sha256=metadata_expected_sha256.get(
                            str(terminal_record.get("name")), "0" * 64
                        ),
                        secret_like_output_detected=bool(terminal_secret_detected),
                    )
                    terminal_entry["metadata_child_name"] = terminal_record.get("name")
                    terminal_entry["metadata_phase"] = terminal_record.get("phase")
                    terminal_entry["metadata_status"] = terminal_record.get("status")
                    terminal_entry["metadata_failure_kind"] = terminal_record.get("failure_kind")
                    _replace_publisher_child_execution(publisher_entry, terminal_entry)

                bind_terminal_observer(mirror_terminal)

    def verify_inventory_before_live_metadata_child(child_name: str) -> None:
        observed = untracked_summary(
            repository,
            git_executable=Path(executable_pins["git"]["path"]),
            git_sha256=str(executable_pins["git"]["sha256"]),
            reject_import_active=True,
            purpose_context=f"live-command-plan:before:{child_name}",
        )
        if (
            observed["count"] != expected_untracked_count
            or observed["regular_files"] != expected_untracked_count
            or observed["path_list_sha256"] != expected_untracked_path_list_sha256
            or observed["content_inventory_sha256"] != expected_untracked_content_inventory_sha256
        ):
            raise ReviewPublisherError(
                f"isolated_untracked_changed_before_live_metadata_child:{child_name}"
            )

    live_metadata_evidence = _ImmediatePublisherMetadataLedger()
    live_publisher_ledger_start = len(_PUBLISHER_CHILD_EXECUTIONS)
    expected_live_publisher_purposes = _live_command_plan_purpose_plan(
        [spec.name for spec in specs]
    )
    if _PUBLISHER_FAILURE_CONTEXT:
        _PUBLISHER_FAILURE_CONTEXT["active_segment_name"] = "live_command_plan"
        _PUBLISHER_FAILURE_CONTEXT["active_segment_start_index"] = live_publisher_ledger_start
        _PUBLISHER_FAILURE_CONTEXT["active_segment_expected_purposes"] = list(
            expected_live_publisher_purposes
        )
    try:
        live_plan = validation_runner.command_plan(
            repository=repository,
            project_root=project_root,
            head=expected_head,
            tree=expected_tree,
            specs=specs,
            child_environment=live_child_environment,
            expected_executable_sha256_by_command=expected_pin_sha256_by_command,
            metadata_evidence=live_metadata_evidence,
            before_metadata_child=verify_inventory_before_live_metadata_child,
            expected_work_order_tool_bindings=expected_tool_file_bindings,
        )
    except BaseException as exc:
        raise ReviewPublisherError("code_validation_live_plan_reconstruction_failed") from exc
    live_plan_digest = live_plan.get("sha256")
    live_plan_payload = {key: item for key, item in live_plan.items() if key != "sha256"}
    if (
        not isinstance(live_plan_digest, str)
        or hashlib.sha256(canonical_json_bytes(live_plan_payload)).hexdigest() != live_plan_digest
    ):
        raise ReviewPublisherError("code_validation_live_plan_digest_invalid")
    live_commands = live_plan.get("commands")
    if not isinstance(live_commands, list) or len(live_commands) != len(specs):
        raise ReviewPublisherError("code_validation_live_plan_commands_invalid")
    for observed, spec in zip(live_commands, specs, strict=True):
        if not isinstance(observed, dict):
            raise ReviewPublisherError("code_validation_live_plan_commands_invalid")
        _validate_tool_metadata_process_evidence(
            observed.get("tool"),
            spec=spec,
            expected_work_order_tool_bindings=expected_tool_file_bindings,
        )
    if _deterministic_command_plan_projection(plan) != _deterministic_command_plan_projection(
        live_plan
    ):
        raise ReviewPublisherError("code_validation_live_plan_mismatch")
    if live_plan.get("environment_commitment") != environment_commitment:
        raise ReviewPublisherError("code_validation_environment_commitment_mismatch")
    expected_live_metadata: list[tuple[str, str, Mapping[str, Any], str]] = []
    for observed, spec in zip(live_commands, specs, strict=True):
        tool = observed.get("tool")
        if not isinstance(tool, Mapping):
            raise ReviewPublisherError("code_validation_live_metadata_tool_mapping_required")
        expected_live_metadata.extend(
            (
                (
                    f"tool-version-{spec.name}",
                    f"command_plan:{spec.name}:tool_version",
                    tool.get("version_process_containment"),
                    expected_pin_sha256_by_command[spec.name],
                ),
                (
                    f"runtime-version-{spec.name}",
                    f"command_plan:{spec.name}:runtime_version",
                    tool.get("runtime_version_process_containment"),
                    expected_pin_sha256_by_command[spec.name],
                ),
            )
        )
    if len(live_metadata_evidence) != len(expected_live_metadata):
        raise ReviewPublisherError("code_validation_live_metadata_child_count_not_exact")
    for record, publisher_entry, (
        expected_name,
        expected_phase,
        expected_process,
        expected_sha256,
    ) in zip(
        live_metadata_evidence,
        live_metadata_evidence.publisher_entries,
        expected_live_metadata,
        strict=True,
    ):
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "phase", "status", "child_invoked", "process_containment"}
            or record.get("name") != expected_name
            or record.get("phase") != expected_phase
            or record.get("status") != "PASS"
            or record.get("child_invoked") is not True
            or not isinstance(expected_process, Mapping)
            or record.get("process_containment") != expected_process
        ):
            raise ReviewPublisherError("code_validation_live_metadata_child_ledger_mismatch")
        sanitized, secret_detected = validation_runner._sanitize_for_evidence(
            dict(expected_process),
            live_child_environment.secret_values,
        )
        if not isinstance(sanitized, dict) or secret_detected:
            raise ReviewPublisherError("code_validation_live_metadata_secret_detected")
        if (
            publisher_entry.get("purpose") != f"live-command-plan:{expected_phase}"
            or publisher_entry.get("metadata_child_name") != expected_name
            or publisher_entry.get("metadata_phase") != expected_phase
            or publisher_entry.get("expected_executable_sha256") != expected_sha256
            or publisher_entry.get("secret_like_output_detected") is not False
        ):
            raise ReviewPublisherError("code_validation_live_metadata_publisher_ledger_mismatch")
        terminal_entry = _redacted_publisher_child_evidence(
            sanitized,
            purpose=f"live-command-plan:{expected_phase}",
            clean=True,
            environment_commitment=live_child_environment.commitment,
            expected_executable_sha256=expected_sha256,
            secret_like_output_detected=False,
        )
        terminal_entry["metadata_child_name"] = expected_name
        terminal_entry["metadata_phase"] = expected_phase
        _replace_publisher_child_execution(publisher_entry, terminal_entry)
    live_publisher_entries = _PUBLISHER_CHILD_EXECUTIONS[live_publisher_ledger_start:]
    if (
        len(live_publisher_entries) != len(expected_live_publisher_purposes)
        or [item.get("purpose") for item in live_publisher_entries]
        != expected_live_publisher_purposes
        or any(
            item.get("clean_containment_verified") is not True for item in live_publisher_entries
        )
    ):
        raise ReviewPublisherError("code_validation_live_publisher_purpose_plan_mismatch")
    if _PUBLISHER_FAILURE_CONTEXT:
        _PUBLISHER_FAILURE_CONTEXT.pop("active_segment_name", None)
        _PUBLISHER_FAILURE_CONTEXT.pop("active_segment_start_index", None)
        _PUBLISHER_FAILURE_CONTEXT.pop("active_segment_expected_purposes", None)
    if summary_sha256 != expected_summary_sha256:
        raise ReviewPublisherError("code_validation_expected_summary_sha256_mismatch")
    return {**dict(value), "commands": normalized_commands}


def validate_code_validation_publication_index(
    index_path: Path,
    *,
    summary_path: Path,
    validated_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the validated summary to the runner's terminal publication index.

    The index cannot contain its own publication receipt without becoming
    self-referential.  It therefore remains non-GO evidence, but the publisher
    requires its canonical bytes and verifies the durable summary receipt it
    carries before accepting the validation result.
    """

    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner

    summary_path = summary_path.resolve(strict=True)
    index_path = index_path.resolve(strict=True)
    if (
        summary_path.name != VALIDATION_SUMMARY_LEAF
        or index_path.name != VALIDATION_PUBLICATION_INDEX_LEAF
        or summary_path.parent != index_path.parent
    ):
        raise ReviewPublisherError("code_validation_publication_index_path_mismatch")
    commands = validated_summary.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ReviewPublisherError("code_validation_publication_index_commands_required")
    try:
        command_paths = tuple(
            Path(command["evidence_path"]).resolve(strict=True)
            for command in commands
            if isinstance(command, dict)
        )
    except (KeyError, OSError, TypeError) as exc:
        raise ReviewPublisherError(
            "code_validation_publication_index_command_path_invalid"
        ) from exc
    if len(command_paths) != len(commands) or any(
        path.parent != summary_path.parent for path in command_paths
    ):
        raise ReviewPublisherError("code_validation_publication_directory_splice")
    expected_leaves = {
        VALIDATION_SUMMARY_LEAF,
        VALIDATION_PUBLICATION_INDEX_LEAF,
        *(path.name for path in command_paths),
    }
    observed_leaves = {path.name for path in summary_path.parent.iterdir()}
    if observed_leaves != expected_leaves:
        raise ReviewPublisherError("code_validation_publication_leaf_set_mismatch")
    for path, label in (
        (summary_path, "summary"),
        (index_path, "publication_index"),
    ):
        measured = os.lstat(path)
        if (
            not path.is_file()
            or int(getattr(measured, "st_file_attributes", 0)) & 0x400
            or int(getattr(measured, "st_nlink", 1)) != 1
        ):
            raise ReviewPublisherError(f"code_validation_{label}_regular_single_link_required")

    summary_raw = summary_path.read_bytes()
    if summary_raw != canonical_json_bytes(dict(validated_summary)):
        raise ReviewPublisherError("code_validation_summary_canonical_bytes_mismatch")
    index_raw = index_path.read_bytes()
    index = read_json_mapping(index_path, "validation_publication_index")
    if index_raw != canonical_json_bytes(index):
        raise ReviewPublisherError("code_validation_publication_index_not_canonical")
    expected_keys = {
        "schema",
        "status",
        "summary",
        "environment_commitment",
        "output_parent_commitment",
        "metadata_child_call_count",
        "command_publication_receipts_bound_through_summary",
        "completion_marker_created",
        "success_marker_created",
        "self_publication_receipt_embedded",
        "self_publication_receipt_scope",
    }
    if set(index) != expected_keys:
        raise ReviewPublisherError("code_validation_publication_index_keys_not_exact")
    if (
        index.get("schema") != validation_runner.PUBLICATION_INDEX_SCHEMA
        or index.get("status") != "PASS"
        or index.get("environment_commitment") != validated_summary.get("environment_commitment")
        or index.get("output_parent_commitment")
        != validated_summary.get("output_parent_commitment")
        or index.get("metadata_child_call_count")
        != validated_summary.get("metadata_child_call_count")
        or index.get("command_publication_receipts_bound_through_summary") is not True
        or index.get("completion_marker_created") is not False
        or index.get("success_marker_created") is not False
        or index.get("self_publication_receipt_embedded") is not False
        or index.get("self_publication_receipt_scope")
        != "outer_result_only_non_self_referential_by_construction"
    ):
        raise ReviewPublisherError("code_validation_publication_index_payload_mismatch")
    summary_reference = index.get("summary")
    if not isinstance(summary_reference, dict) or set(summary_reference) != {
        "path",
        "bytes",
        "sha256",
        "publication",
    }:
        raise ReviewPublisherError("code_validation_publication_index_summary_ref_invalid")
    summary_sha256 = hashlib.sha256(summary_raw).hexdigest()
    if (
        os.path.normcase(os.path.abspath(str(summary_reference.get("path", ""))))
        != os.path.normcase(os.path.abspath(str(summary_path)))
        or summary_reference.get("bytes") != len(summary_raw)
        or summary_reference.get("sha256") != summary_sha256
    ):
        raise ReviewPublisherError("code_validation_publication_index_summary_mismatch")
    publication = _validate_publication_receipt(
        summary_reference.get("publication"),
        expected_path=summary_path,
        expected_sha256=summary_sha256,
        expected_bytes=len(summary_raw),
    )
    _validate_output_parent_commitment(
        validated_summary.get("output_parent_commitment"),
        validation_runner=validation_runner,
        expected_parent=summary_path.parent.parent,
    )
    return {
        **dict(index),
        "summary": {**summary_reference, "publication": publication},
        "index_path": str(index_path),
        "index_bytes": len(index_raw),
        "index_sha256": hashlib.sha256(index_raw).hexdigest(),
        "index_self_publication_receipt_available": False,
        "go_evidence_eligible": False,
    }


def etw_not_run_decision() -> dict[str, Any]:
    record = {
        "schema": etw.NOT_RUN_SCHEMA,
        "status": "not_run",
        "decision": "NO-GO",
        "reason": "approved_external_manifest_required",
        "approved_external_manifest_present": False,
        "collector_started": False,
        "administrator_session": False,
        "archive_created": False,
        "service_configuration_changed": False,
        "acceptance_credit": False,
        "completion_credit": "non_credit_only",
        "go": False,
        "completion_marker_created": False,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "call_counts": dict(etw.ZERO_ETW_CALLS),
    }
    return etw.validate_etw_qualification(record).to_dict()


def _canonical_document_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = canonical_json_bytes(dict(value))
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def reviewer_pending_no_go_documents(
    *,
    run_uuid: str,
    attempt_uuid: str,
    commit: str,
    tree: str,
    blockers: Sequence[str],
    base_documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Bind the explicit reviewer report and NO-GO seal into one atomic batch.

    These records are internal, non-authoritative review evidence.  They can
    never stand in for an external approval receipt or a success/completion
    marker.  The aggregate manifest and index are added later by the atomic
    no-replace evidence writer.
    """

    canonical_run_uuid = str(uuid.UUID(run_uuid))
    canonical_attempt_uuid = str(uuid.UUID(attempt_uuid))
    if canonical_run_uuid == canonical_attempt_uuid:
        raise ReviewPublisherError("review_report_run_attempt_uuid_must_differ")
    canonical_commit = _hex40(commit, "review_report_commit")
    canonical_tree = _hex40(tree, "review_report_tree")
    normalized_blockers = sorted(set(blockers))
    if (
        not normalized_blockers
        or any(not isinstance(item, str) or not item for item in normalized_blockers)
        or set(base_documents) & {REVIEWER_PENDING_REPORT_LEAF, NO_GO_SEAL_LEAF}
        or not all(isinstance(value, Mapping) for value in base_documents.values())
    ):
        raise ReviewPublisherError("review_report_inputs_invalid")
    validation = base_documents.get(VALIDATION_SUMMARY_LEAF)
    offline = base_documents.get("offline-admission-decision.json")
    if (
        not isinstance(validation, Mapping)
        or validation.get("status") != "PASS"
        or validation.get("decision") != "NO-GO"
        or validation.get("credit") != "zero_credit"
        or validation.get("evidence_scope") != "internal_non_authoritative"
        or validation.get("go_evidence_eligible") is not False
        or validation.get("completion_marker_created") is not False
        or validation.get("success_marker_created") is not False
        or validation.get("r8_authorized") is not False
        or not isinstance(offline, Mapping)
        or offline.get("status") != "manual_intervention_required"
        or offline.get("decision") != "NO-GO"
        or offline.get("credit") != "zero_credit"
        or offline.get("go_evidence_eligible") is not False
        or offline.get("completion_marker_created") is not False
        or offline.get("success_marker_created") is not False
    ):
        raise ReviewPublisherError("review_report_fail_closed_source_state_required")
    whole_system_telemetry = offline.get("whole_system_live_call_telemetry")
    if (
        not isinstance(whole_system_telemetry, Mapping)
        or whole_system_telemetry.get("observation_state") != "unknown"
        or not isinstance(whole_system_telemetry.get("counts"), Mapping)
        or set(whole_system_telemetry["counts"]) != REQUIRED_ZERO_LIVE_CALLS
        or any(value is not None for value in whole_system_telemetry["counts"].values())
    ):
        raise ReviewPublisherError("review_report_whole_system_telemetry_must_remain_unknown")

    base_references = {
        leaf: _canonical_document_reference(value) for leaf, value in sorted(base_documents.items())
    }
    qualification_states = {
        "windows_non_credit_qualification": "not_run",
        "wsl_detached_residual_qualification": "not_run",
        "runtime_restore_etw_gate": "not_run",
        "dual_collector_non_credit_qualification": "not_run",
        "r8": "not_run",
        "fresh_phase_b2": "not_run",
    }
    report = {
        "schema": f"{SCHEMA}.reviewer-pending-report.v1",
        "status": "manual_intervention_required",
        "review_state": "reviewer_pending",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "reviewer_sign_off": "pending",
        "run_uuid": canonical_run_uuid,
        "attempt_uuid": canonical_attempt_uuid,
        "commit": canonical_commit,
        "tree": canonical_tree,
        "code_validation_status": "PASS",
        "code_validation_decision": "NO-GO",
        "bound_documents": base_references,
        "remaining_blockers": normalized_blockers,
        "qualification_states": qualification_states,
        "whole_system_live_call_telemetry": dict(whole_system_telemetry),
        "external_approval_receipt_created": False,
        "external_worm_receipt_created": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
        "r8_authorized": False,
        "completion_marker_created": False,
        "success_marker_created": False,
        "success_index_created": False,
    }
    sealed_references = {
        **base_references,
        REVIEWER_PENDING_REPORT_LEAF: _canonical_document_reference(report),
    }
    no_go_seal = {
        "schema": f"{SCHEMA}.append-only-no-go-seal.v1",
        "status": "manual_intervention_required",
        "review_state": "reviewer_pending",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "seal_semantics": "append_only_reviewer_pending_no_go_not_success_evidence",
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "reviewer_sign_off": "pending",
        "run_uuid": canonical_run_uuid,
        "attempt_uuid": canonical_attempt_uuid,
        "commit": canonical_commit,
        "tree": canonical_tree,
        "sealed_documents": sealed_references,
        "remaining_blockers": normalized_blockers,
        "qualification_states": qualification_states,
        "whole_system_live_call_telemetry": dict(whole_system_telemetry),
        "aggregate_manifest_and_index_emitted_by_atomic_writer": True,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "external_approval_receipt_created": False,
        "external_worm_receipt_created": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
        "r8_authorized": False,
        "completion_marker_created": False,
        "success_marker_created": False,
        "success_index_created": False,
    }
    return {
        REVIEWER_PENDING_REPORT_LEAF: report,
        NO_GO_SEAL_LEAF: no_go_seal,
    }


def _best_effort_emit_result(result: Mapping[str, Any]) -> None:
    """Keep console delivery outside the durable evidence transaction."""

    try:
        print(json.dumps(dict(result), ensure_ascii=False, sort_keys=True))
    except BaseException:
        pass


def _publisher_failure_coordinates(args: argparse.Namespace) -> tuple[str, str]:
    run_value = uuid.UUID(str(args.run_uuid))
    attempt_value = uuid.UUID(str(args.attempt_uuid))
    failure_uuid = uuid.uuid5(
        run_value,
        f"{SCHEMA}:publisher-failure:{attempt_value}",
    )
    return f"publisher-failure-{failure_uuid}", str(failure_uuid)


def _set_publisher_stage(stage: str) -> None:
    if _PUBLISHER_FAILURE_CONTEXT:
        _PUBLISHER_FAILURE_CONTEXT["stage"] = stage


def publisher_failure_seal_contract() -> dict[str, Any]:
    return {
        "scope": "after_review_parent_and_failure_namespace_admission",
        "parse_and_untrusted_parent_admission_failures_durably_sealed": False,
        "first_child_launch_preceded_by_failure_context_arm": True,
        "post_admission_base_exception_failure_batch_attempted": True,
        "partial_clean_prefix_treated_as_complete_containment": False,
        "failure_batch_automatic_retry_count": 0,
        "failure_batch_success_or_completion_marker_supported": False,
        "failure_to_emergency_fallback_delegated_to_atomic_evidence_writer": True,
        "durable_record_after_independent_emergency_writer_failure_guaranteed": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


def _record_publication_checkpoint(role: str, batch: Any, *, run_uuid: str) -> None:
    if not _PUBLISHER_FAILURE_CONTEXT:
        return
    if role not in {"primary", "postpublication"}:
        return
    _PUBLISHER_FAILURE_CONTEXT[f"{role}_batch_object_available"] = True
    try:
        payload = batch.to_dict()
        raw = canonical_json_bytes(payload)
    except BaseException as exc:
        _PUBLISHER_FAILURE_CONTEXT[f"{role}_publication_checkpoint"] = {
            "status": "unproven",
            "run_uuid": str(uuid.UUID(str(run_uuid))),
            "exception_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "exception_message_disclosed": False,
        }
        return
    _PUBLISHER_FAILURE_CONTEXT[f"{role}_publication_checkpoint"] = {
        "status": "published_and_serialized",
        "run_uuid": str(uuid.UUID(str(run_uuid))),
        "batch": payload,
        "batch_sha256": hashlib.sha256(raw).hexdigest(),
        "batch_bytes": len(raw),
    }


def _publish_publisher_failure_batch(
    args: argparse.Namespace, cause: BaseException
) -> Mapping[str, Any]:
    context = dict(_PUBLISHER_FAILURE_CONTEXT)
    if context.get("armed") is not True:
        raise ReviewPublisherError("publisher_failure_context_not_armed")
    parent = context.get("parent")
    output_leaf = context.get("output_leaf")
    run_uuid = context.get("run_uuid")
    parent_commitment = context.get("parent_commitment")
    if (
        not isinstance(parent, Path)
        or not isinstance(output_leaf, str)
        or not isinstance(run_uuid, str)
        or not isinstance(parent_commitment, Mapping)
    ):
        raise ReviewPublisherError("publisher_failure_context_invalid")
    expected_count = context.get("expected_purpose_count")
    expected_purposes_value = context.get("expected_purposes")
    expected_purposes = (
        tuple(expected_purposes_value)
        if isinstance(expected_purposes_value, (list, tuple))
        and all(isinstance(item, str) for item in expected_purposes_value)
        else None
    )
    try:
        child_observation = publisher_child_containment_observation(
            terminal=True,
            expected_purposes=expected_purposes,
        )
    except BaseException as observation_error:
        child_observation = {
            "schema": f"{SCHEMA}.publisher-child-containment-summary",
            "terminal_observation": True,
            "status": "unproven",
            "exception_type": (
                f"{type(observation_error).__module__}.{type(observation_error).__qualname__}"
            ),
            "exception_message_disclosed": False,
            "all_children_cleanly_contained": False,
            "automatic_retry_count": 0,
            "terminate_job_object_calls": 0,
        }
    observed_count = child_observation.get("child_count")
    workflow_complete = bool(
        expected_purposes is not None
        and type(expected_count) is int
        and expected_count == len(expected_purposes)
        and observed_count == expected_count
        and child_observation.get("purpose_plan_exact") is True
        and child_observation.get("execution_sequence_complete") is True
    )
    child_observation["workflow_execution_complete"] = workflow_complete
    child_observation["expected_purpose_plan_available"] = expected_purposes is not None
    child_observation["expected_purpose_count"] = (
        expected_count if type(expected_count) is int else "unproven"
    )
    child_observation["observed_purpose_count"] = (
        observed_count if type(observed_count) is int else "unproven"
    )
    child_observation["first_missing_execution_sequence"] = (
        observed_count + 1
        if type(observed_count) is int
        and type(expected_count) is int
        and observed_count < expected_count
        else None
    )
    if not workflow_complete:
        child_observation["all_children_cleanly_contained"] = False
    if expected_purposes is not None:
        observed_purposes = [item.get("purpose") for item in _PUBLISHER_CHILD_EXECUTIONS]
        full_prefix_length = 0
        for observed, expected in zip(observed_purposes, expected_purposes, strict=False):
            if observed != expected:
                break
            full_prefix_length += 1
        child_observation["purpose_plan_comparison"] = {
            "exact_prefix_length": full_prefix_length,
            "first_missing_or_mismatched_index": (
                full_prefix_length
                if full_prefix_length < len(expected_purposes)
                or len(observed_purposes) > len(expected_purposes)
                else None
            ),
            "first_expected_purpose": (
                expected_purposes[full_prefix_length]
                if full_prefix_length < len(expected_purposes)
                else None
            ),
            "first_observed_purpose": (
                observed_purposes[full_prefix_length]
                if full_prefix_length < len(observed_purposes)
                else None
            ),
        }
    active_expected_value = context.get("active_segment_expected_purposes")
    active_start = context.get("active_segment_start_index")
    if (
        isinstance(active_expected_value, (list, tuple))
        and all(isinstance(item, str) for item in active_expected_value)
        and type(active_start) is int
        and active_start >= 0
    ):
        active_expected = list(active_expected_value)
        active_observed = [
            item.get("purpose") for item in _PUBLISHER_CHILD_EXECUTIONS[active_start:]
        ]
        prefix_length = 0
        for observed, expected in zip(active_observed, active_expected, strict=False):
            if observed != expected:
                break
            prefix_length += 1
        child_observation["active_segment"] = {
            "name": str(context.get("active_segment_name", "unproven")),
            "expected_count": len(active_expected),
            "observed_count": len(active_observed),
            "exact_prefix_length": prefix_length,
            "exact_complete": active_observed == active_expected,
            "first_missing_or_mismatched_index": (
                prefix_length if prefix_length < len(active_expected) else None
            ),
            "first_missing_or_mismatched_purpose": (
                active_expected[prefix_length] if prefix_length < len(active_expected) else None
            ),
            "expected_purpose_plan_sha256": hashlib.sha256(
                canonical_json_bytes(active_expected)
            ).hexdigest(),
        }
        if active_observed != active_expected:
            child_observation["all_children_cleanly_contained"] = False
    original_atomic_failure: Mapping[str, Any] | str = "not_applicable"
    if isinstance(cause, evidence.R7S6EvidencePublicationError):
        try:
            original_atomic_failure = json.loads(canonical_json_bytes(cause.to_dict()))
        except BaseException as atomic_observation_error:
            original_atomic_failure = {
                "status": "unproven",
                "exception_type": (
                    f"{type(atomic_observation_error).__module__}."
                    f"{type(atomic_observation_error).__qualname__}"
                ),
                "exception_message_disclosed": False,
            }
    primary_checkpoint = context.get("primary_publication_checkpoint", "not_available")
    if isinstance(primary_checkpoint, Mapping):
        try:
            primary_checkpoint = json.loads(canonical_json_bytes(primary_checkpoint))
        except BaseException as checkpoint_error:
            primary_checkpoint = {
                "status": "unproven",
                "exception_type": (
                    f"{type(checkpoint_error).__module__}.{type(checkpoint_error).__qualname__}"
                ),
                "exception_message_disclosed": False,
            }
    postpublication_checkpoint = context.get(
        "postpublication_publication_checkpoint", "not_available"
    )
    if isinstance(postpublication_checkpoint, Mapping):
        try:
            postpublication_checkpoint = json.loads(
                canonical_json_bytes(postpublication_checkpoint)
            )
        except BaseException as checkpoint_error:
            postpublication_checkpoint = {
                "status": "unproven",
                "exception_type": (
                    f"{type(checkpoint_error).__module__}.{type(checkpoint_error).__qualname__}"
                ),
                "exception_message_disclosed": False,
            }
    publication_state = context.get("publication_state")
    if not isinstance(publication_state, Mapping):
        publication_state = {
            "primary": "unproven",
            "postpublication": "unproven",
        }
    documents = {
        "publisher-failure-report.json": {
            "schema": f"{SCHEMA}.prepublication-failure.v1",
            "status": "manual_intervention_required",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "failure_seal_semantics": "append_only_failure_batch_not_success_evidence",
            "failure_stage": str(context.get("stage", "unproven")),
            "exception_type": f"{type(cause).__module__}.{type(cause).__qualname__}",
            "exception_message_disclosed": False,
            "review_parent_commitment": dict(parent_commitment),
            "failure_seal_contract": publisher_failure_seal_contract(),
            "primary_output_leaf": str(args.output_leaf),
            "postpublication_output_leaf": str(args.post_output_leaf),
            "publication_state": dict(publication_state),
            "primary_publication_checkpoint": primary_checkpoint,
            "postpublication_publication_checkpoint": postpublication_checkpoint,
            "original_atomic_publication_failure": original_atomic_failure,
            "publisher_child_terminal_containment": child_observation,
            "publisher_local_dispatch_telemetry": {
                "observation_scope": "this_publisher_process_only",
                "automatic_retry_count": 0,
                "forced_termination_attempts": 0,
            },
            "whole_system_live_call_telemetry": {
                "observation_state": "unknown",
                "observation_scope": "not_observed_by_failure_publisher",
                "counts": {name: None for name in sorted(REQUIRED_ZERO_LIVE_CALLS)},
                "raw_telemetry_sha256": None,
            },
            "completion_marker_created": False,
            "success_marker_created": False,
            "go_evidence_eligible": False,
        }
    }
    batch = evidence.publish_pre_serialized_batch(
        parent,
        output_leaf,
        documents,
        run_uuid=run_uuid,
    )
    return {
        "schema": f"{SCHEMA}.failure-publication-result.v1",
        "status": "manual_intervention_required",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "failure_batch": batch.to_dict(),
        "completion_marker_created": False,
        "success_marker_created": False,
        "whole_system_r8_calls": None,
    }


def _dispatch_publisher_failure(
    args: argparse.Namespace,
    cause: BaseException,
    *,
    state: dict[str, bool],
) -> Mapping[str, Any]:
    """Enter the append-only failure publisher exactly once.

    The entry bit is set inside this callee.  Consequently, an asynchronous
    exception at the caller's dispatch CALL cannot be confused with a failure
    raised after the evidence writer began.  The latter is passed through and
    is never retried.
    """

    if state.get("entered") is True:
        raise ReviewPublisherError("publisher_failure_dispatch_reentry_forbidden")
    state["entered"] = True
    return _publish_publisher_failure_batch(args, cause)


def _publisher_failure_publication_error(
    original_error: BaseException,
    seal_error: BaseException,
) -> dict[str, Any]:
    fallback: dict[str, Any] = {
        "schema": f"{SCHEMA}.failure-publication-error.v1",
        "status": "manual_intervention_required",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "original_exception_type": (
            f"{type(original_error).__module__}.{type(original_error).__qualname__}"
        ),
        "seal_exception_type": (f"{type(seal_error).__module__}.{type(seal_error).__qualname__}"),
        "exception_messages_disclosed": False,
        "failure_dispatch_retry_count": 0,
        "completion_marker_created": False,
        "success_marker_created": False,
        "whole_system_r8_calls": None,
    }
    if isinstance(seal_error, evidence.R7S6EvidencePublicationError):
        fallback["atomic_failure_or_emergency_seal"] = seal_error.to_dict()
    return fallback


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish append-only pre-r8 r7s5 review evidence")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--canonical-repository", type=Path, required=True)
    parser.add_argument("--canonical-branch", required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--expected-parent", type=Path, required=True)
    parser.add_argument("--expected-parent-sha256", required=True)
    parser.add_argument("--output-leaf", required=True)
    parser.add_argument("--post-output-leaf", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--attempt-uuid", required=True)
    parser.add_argument("--r7s4-evidence", type=Path, required=True)
    parser.add_argument("--r6-rca", type=Path, required=True)
    parser.add_argument("--etw-amendment", type=Path, required=True)
    parser.add_argument("--ci-readback", type=Path, required=True)
    parser.add_argument("--ci-manifest", type=Path, required=True)
    parser.add_argument("--token-evidence", type=Path, required=True)
    parser.add_argument("--lineage-work-order", type=Path, required=True)
    parser.add_argument("--lineage-work-order-sha256", required=True)
    parser.add_argument("--validation-summary", type=Path, required=True)
    parser.add_argument("--expected-validation-summary-sha256", required=True)
    parser.add_argument("--external-work-order", type=Path, required=True)
    parser.add_argument("--external-work-order-sha256", required=True)
    parser.add_argument("--trusted-outer", type=Path, required=True)
    parser.add_argument("--trusted-outer-sha256", required=True)
    parser.add_argument("--python-general", type=Path, required=True)
    parser.add_argument("--python-general-sha256", required=True)
    parser.add_argument("--python-host", type=Path, required=True)
    parser.add_argument("--python-host-sha256", required=True)
    parser.add_argument("--python-ruff", type=Path, required=True)
    parser.add_argument("--python-ruff-sha256", required=True)
    parser.add_argument("--kubectl-executable", type=Path, required=True)
    parser.add_argument("--kubectl-executable-sha256", required=True)
    parser.add_argument("--git-executable", type=Path, required=True)
    parser.add_argument("--git-executable-sha256", required=True)
    parser.add_argument("--powershell-executable", type=Path, required=True)
    parser.add_argument("--powershell-executable-sha256", required=True)
    parser.add_argument("--expected-untracked-count", type=int, required=True)
    parser.add_argument("--expected-untracked-path-sha256", required=True)
    parser.add_argument("--expected-untracked-content-sha256", required=True)
    parser.add_argument("--expected-isolated-untracked-count", type=int, required=True)
    parser.add_argument("--expected-isolated-untracked-path-list-sha256", required=True)
    parser.add_argument("--expected-isolated-untracked-content-inventory-sha256", required=True)
    return parser.parse_args(argv)


def _run_publisher(args: argparse.Namespace) -> int:
    repository = args.repository.resolve(strict=True)
    project_root = args.project_root.resolve(strict=True)
    if project_root != SCRIPT_PROJECT_ROOT:
        raise ReviewPublisherError("publisher_script_project_origin_mismatch")
    for module in (admission, ci, dual_clock, etw, evidence, gate, reservation, windows_wsl):
        origin = Path(module.__file__).resolve(strict=True)
        if SCRIPT_PROJECT_ROOT not in origin.parents:
            raise ReviewPublisherError("publisher_module_origin_mismatch")
    if repository not in project_root.parents:
        raise ReviewPublisherError("project_root_not_inside_repository")
    canonical_repository = args.canonical_repository.resolve(strict=True)
    validation_summary_path = args.validation_summary.resolve(strict=True)
    git_pin = validate_independent_executable_pin(
        args.git_executable,
        args.git_executable_sha256,
        label="git_executable",
    )
    powershell_pin = validate_independent_executable_pin(
        args.powershell_executable,
        args.powershell_executable_sha256,
        label="powershell_executable",
    )
    git_kwargs = {
        "git_executable": Path(git_pin["path"]),
        "git_sha256": str(git_pin["sha256"]),
    }
    expected_isolated_path_list_sha256 = _hex64(
        args.expected_isolated_untracked_path_list_sha256,
        "expected_isolated_untracked_path_list",
    )
    expected_isolated_content_inventory_sha256 = _hex64(
        args.expected_isolated_untracked_content_inventory_sha256,
        "expected_isolated_untracked_content_inventory",
    )

    def require_isolated_untracked_inventory() -> dict[str, Any]:
        observed = untracked_summary(
            repository,
            reject_import_active=True,
            **git_kwargs,
        )
        if (
            observed["count"] != args.expected_isolated_untracked_count
            or observed["regular_files"] != args.expected_isolated_untracked_count
            or observed["path_list_sha256"] != expected_isolated_path_list_sha256
            or observed["content_inventory_sha256"] != expected_isolated_content_inventory_sha256
        ):
            raise ReviewPublisherError("isolated_untracked_inventory_changed")
        return observed

    review_parent, review_parent_pin = validate_review_parent_gate(
        args.parent,
        expected_path=args.expected_parent,
        expected_sha256=args.expected_parent_sha256,
        output_leaf=args.output_leaf,
        forbidden_roots=(
            repository,
            project_root,
            canonical_repository,
            args.r7s4_evidence,
            args.r6_rca,
            args.etw_amendment,
            args.ci_readback,
            validation_summary_path.parent,
        ),
    )
    post_review_parent, post_review_parent_pin = validate_review_parent_gate(
        args.parent,
        expected_path=args.expected_parent,
        expected_sha256=args.expected_parent_sha256,
        output_leaf=args.post_output_leaf,
        forbidden_roots=(
            repository,
            project_root,
            canonical_repository,
            args.r7s4_evidence,
            args.r6_rca,
            args.etw_amendment,
            args.ci_readback,
            validation_summary_path.parent,
        ),
    )
    if post_review_parent != review_parent or post_review_parent_pin != review_parent_pin:
        raise ReviewPublisherError("review_postpublication_parent_commitment_mismatch")
    if str(uuid.UUID(args.run_uuid)) == str(uuid.UUID(args.attempt_uuid)):
        raise ReviewPublisherError("review_run_and_attempt_uuid_must_be_distinct")
    require_disjoint_review_batch_namespaces(
        review_parent,
        args.output_leaf,
        args.run_uuid,
        args.post_output_leaf,
        args.attempt_uuid,
    )
    failure_output_leaf, failure_run_uuid = _publisher_failure_coordinates(args)
    failure_review_parent, failure_review_parent_pin = validate_review_parent_gate(
        args.parent,
        expected_path=args.expected_parent,
        expected_sha256=args.expected_parent_sha256,
        output_leaf=failure_output_leaf,
        forbidden_roots=(
            repository,
            project_root,
            canonical_repository,
            args.r7s4_evidence,
            args.r6_rca,
            args.etw_amendment,
            args.ci_readback,
            validation_summary_path.parent,
        ),
    )
    if failure_review_parent != review_parent or failure_review_parent_pin != review_parent_pin:
        raise ReviewPublisherError("review_failure_parent_commitment_mismatch")
    require_disjoint_review_batch_namespaces(
        review_parent,
        args.output_leaf,
        args.run_uuid,
        failure_output_leaf,
        failure_run_uuid,
    )
    require_disjoint_review_batch_namespaces(
        review_parent,
        args.post_output_leaf,
        args.attempt_uuid,
        failure_output_leaf,
        failure_run_uuid,
    )
    _PUBLISHER_FAILURE_CONTEXT.update(
        {
            "armed": True,
            "stage": "initial_repository_readback",
            "parent": review_parent,
            "parent_commitment": review_parent_pin,
            "output_leaf": failure_output_leaf,
            "run_uuid": failure_run_uuid,
            "expected_purpose_count": EXPECTED_PRIMARY_PUBLISHER_CHILD_COUNT,
            "expected_purposes": None,
            "publication_state": {
                "primary": "not_attempted",
                "postpublication": "not_attempted",
            },
        }
    )
    isolated_untracked = require_isolated_untracked_inventory()
    canonical_git = git_snapshot(canonical_repository, args.canonical_branch, **git_kwargs)
    isolated_head = str(run_git(repository, ["rev-parse", "HEAD"], **git_kwargs))
    isolated_tree = str(run_git(repository, ["rev-parse", "HEAD^{tree}"], **git_kwargs))
    if isolated_head != canonical_git["local_head"] or isolated_tree != canonical_git["tree"]:
        raise ReviewPublisherError("isolated_canonical_commit_tree_mismatch")
    isolated_tracked = str(
        run_git(
            repository,
            ["status", "--porcelain=v1", "--untracked-files=no"],
            **git_kwargs,
        )
    )
    if isolated_tracked:
        raise ReviewPublisherError("isolated_tracked_changes_present")

    untracked = untracked_summary(canonical_repository, **git_kwargs)
    if (
        untracked["count"] != args.expected_untracked_count
        or untracked["regular_files"] != args.expected_untracked_count
        or untracked["path_inventory_sha256"] != args.expected_untracked_path_sha256
        or untracked["content_inventory_sha256"]
        != _hex64(args.expected_untracked_content_sha256, "expected_untracked_content")
    ):
        raise ReviewPublisherError("canonical_user_untracked_inventory_changed")

    _set_publisher_stage("administrator_token_validation")
    token = validate_token_evidence(
        read_json_mapping(args.token_evidence, "token_evidence"),
        powershell_executable=Path(powershell_pin["path"]),
        powershell_sha256=str(powershell_pin["sha256"]),
        lineage_work_order=args.lineage_work_order,
        lineage_work_order_sha256=args.lineage_work_order_sha256,
    )
    if require_isolated_untracked_inventory() != isolated_untracked:
        raise ReviewPublisherError("isolated_untracked_changed_before_validation_review")
    if sha256_file(validation_summary_path) != _hex64(
        args.expected_validation_summary_sha256,
        "expected_validation_summary",
    ):
        raise ReviewPublisherError("validation_summary_external_sha256_mismatch")
    submitted_validation = read_json_mapping(validation_summary_path, "validation_summary")
    validation_publication_index = validate_code_validation_publication_index(
        validation_summary_path.parent / VALIDATION_PUBLICATION_INDEX_LEAF,
        summary_path=validation_summary_path,
        validated_summary=submitted_validation,
    )
    _set_publisher_stage("code_validation_live_plan")
    validation = validate_code_summary(
        submitted_validation,
        repository=repository,
        project_root=project_root,
        expected_head=isolated_head,
        expected_tree=isolated_tree,
        python_general=args.python_general,
        python_general_sha256=args.python_general_sha256,
        python_host=args.python_host,
        python_host_sha256=args.python_host_sha256,
        python_ruff=args.python_ruff,
        python_ruff_sha256=args.python_ruff_sha256,
        kubectl_executable=args.kubectl_executable,
        kubectl_executable_sha256=args.kubectl_executable_sha256,
        git_executable=Path(git_pin["path"]),
        git_executable_sha256=str(git_pin["sha256"]),
        powershell_executable=Path(powershell_pin["path"]),
        powershell_executable_sha256=str(powershell_pin["sha256"]),
        expected_untracked_count=args.expected_isolated_untracked_count,
        expected_untracked_path_list_sha256=expected_isolated_path_list_sha256,
        expected_untracked_content_inventory_sha256=(expected_isolated_content_inventory_sha256),
        expected_summary_sha256=args.expected_validation_summary_sha256,
        external_work_order=args.external_work_order,
        external_work_order_sha256=args.external_work_order_sha256,
        trusted_outer=args.trusted_outer,
        trusted_outer_sha256=args.trusted_outer_sha256,
    )
    primary_purpose_plan = _expected_primary_publisher_purpose_plan(
        token=token,
        validation=validation,
    )
    _PUBLISHER_FAILURE_CONTEXT["expected_purposes"] = list(primary_purpose_plan)
    ci_validation = ci.load_and_validate_manifest(
        args.ci_manifest.resolve(strict=True), project_root=project_root
    )
    ci_manifest = ci.load_manifest(args.ci_manifest.resolve(strict=True), project_root=project_root)
    _set_publisher_stage("selected_source_inventory")
    source_inventory = selected_source_inventory(repository, **git_kwargs)
    candidate_sha256 = source_inventory["inventory_sha256"]
    gate_decision = gate.evaluate_r7s5_gate(
        historical_r6=R6_PROJECTION,
        run_uuid=str(uuid.UUID(args.run_uuid)),
        attempt_uuid=str(uuid.UUID(args.attempt_uuid)),
        candidate_sha256=candidate_sha256,
    ).to_dict()
    if gate_decision["decision"] != "NO-GO" or gate_decision["downstream_calls"] != dict(
        gate.ZERO_DOWNSTREAM_CALLS
    ):
        raise ReviewPublisherError("offline_gate_must_remain_no_go_zero_downstream")

    historical = {
        "r7s4": verify_sealed_directory(args.r7s4_evidence, "r7s4"),
        "r6": verify_sealed_directory(args.r6_rca, "r6"),
        "etw_amendment": verify_sealed_etw_amendment(args.etw_amendment),
        "sealed_reference_verification": True,
        "measurement_scope": "first_and_immediate_prepublication_readback",
    }
    ci_readback = verify_ci_readback(args.ci_readback, ci_manifest)
    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner_contract

    contracts = {
        "reservation": reservation.reservation_contract(),
        "admission": admission.admission_contract(),
        "gate": gate.gate_contract(),
        "dual_clock": dual_clock.dual_clock_contract(),
        "etw": etw.etw_contract(),
        "evidence": evidence.source_contract(),
        "windows_wsl": windows_wsl.source_contract(),
        "publisher_failure_seal": publisher_failure_seal_contract(),
        "validation_runner_failure_seal": (
            validation_runner_contract.validation_failure_seal_contract()
        ),
    }
    blockers = sorted(
        {
            *gate_decision["blockers"],
            *ci_validation["remaining_blockers"],
            "external_independent_authority_unconfigured",
            "multi_host_global_one_shot_unproven",
            "actual_windows_job_qualification_not_run",
            "actual_wsl_process_group_qualification_not_run",
            "actual_dual_collector_180s_qualification_not_run",
            "r6_restore_only_pass_and_independent_approval_absent",
            "production_runtime_admission_not_authorized",
            "validation_subprocess_descendant_live_call_telemetry_unproven",
            "outer_invocation_authority_unproven",
            "os_bound_outer_capability_unprovisioned",
        }
    )
    documents = {
        "administrator-token-readback.json": {
            "schema": f"{SCHEMA}.administrator-token-readback",
            **token,
        },
        "git-user-file-readback.json": {
            "schema": f"{SCHEMA}.git-user-file-readback",
            "canonical": canonical_git,
            "isolated": {
                "repository": str(repository),
                "head": isolated_head,
                "tree": isolated_tree,
                "tracked_changes": 0,
            },
            "canonical_user_untracked": untracked,
            "postpublication_verification": {
                "required": True,
                "output_leaf": args.post_output_leaf,
                "run_uuid": str(uuid.UUID(args.attempt_uuid)),
            },
        },
        "historical-immutability-readback.json": {
            "schema": f"{SCHEMA}.historical-immutability-readback",
            **historical,
        },
        "ci-artifact-readback.json": {
            "schema": f"{SCHEMA}.ci-artifact-readback",
            "manifest_validation": ci_validation,
            "downloaded_readback_inventory": ci_readback,
            "ci_rerun_count": 0,
        },
        "source-contract-inventory.json": {
            "schema": f"{SCHEMA}.source-contract-inventory",
            "commit": isolated_head,
            "tree": isolated_tree,
            "review_parent_commitment": review_parent_pin,
            "source_inventory": source_inventory,
            "contracts": contracts,
        },
        "code-validation-summary.json": validation,
        "code-validation-publication-index.json": validation_publication_index,
        "offline-admission-decision.json": {
            "schema": f"{SCHEMA}.offline-admission-decision",
            "status": "manual_intervention_required",
            "credit": "zero_credit",
            "decision": "NO-GO",
            "gate": gate_decision,
            "etw_optional_diagnostic": etw_not_run_decision(),
            "blockers": blockers,
            "outer_invocation_authority": "unproven_internal_non_authoritative",
            "publisher_local_dispatch_telemetry": {
                "observation_scope": "this_publisher_process_only",
                "automatic_retry_count": 0,
                "forced_termination_attempts": 0,
            },
            "whole_system_live_call_telemetry": {
                "observation_state": "unknown",
                "observation_scope": "external_collector_not_provisioned",
                "counts": dict(validation["live_call_telemetry"]["payload"]["counts"]),
                "raw_telemetry_sha256": validation["live_call_telemetry"]["sha256"],
            },
            "completion_marker_created": False,
            "success_marker_created": False,
            "go_evidence_eligible": False,
        },
    }
    # Re-read every historical checkpoint immediately before the primary
    # publication.  A separate append-only terminal batch below binds the
    # postpublication repository and child-process closure.
    _set_publisher_stage("primary_prepublication_readback")
    second_historical = {
        "r7s4": verify_sealed_directory(args.r7s4_evidence, "r7s4"),
        "r6": verify_sealed_directory(args.r6_rca, "r6"),
        "etw_amendment": verify_sealed_etw_amendment(args.etw_amendment),
    }
    if any(second_historical[key] != historical[key] for key in second_historical):
        raise ReviewPublisherError("historical_checkpoint_changed_during_publication_preflight")
    if untracked_summary(canonical_repository, **git_kwargs) != untracked:
        raise ReviewPublisherError("canonical_user_untracked_changed_during_publication_preflight")
    if require_isolated_untracked_inventory() != isolated_untracked:
        raise ReviewPublisherError("isolated_untracked_changed_during_publication_preflight")
    verify_isolated_repository_prepublication(
        repository,
        expected_head=isolated_head,
        expected_tree=isolated_tree,
        **git_kwargs,
    )
    documents["publisher-child-containment-summary.json"] = publisher_child_containment_summary(
        expected_purposes=primary_purpose_plan
    )
    documents.update(
        reviewer_pending_no_go_documents(
            run_uuid=args.run_uuid,
            attempt_uuid=args.attempt_uuid,
            commit=isolated_head,
            tree=isolated_tree,
            blockers=blockers,
            base_documents=dict(documents),
        )
    )
    _set_publisher_stage("primary_atomic_publication")
    _PUBLISHER_FAILURE_CONTEXT["publication_state"]["primary"] = "attempting"
    batch: Any | None = None
    try:
        batch = evidence.publish_pre_serialized_batch(
            review_parent,
            args.output_leaf,
            documents,
            run_uuid=args.run_uuid,
        )
        _record_publication_checkpoint("primary", batch, run_uuid=args.run_uuid)
        _PUBLISHER_FAILURE_CONTEXT["publication_state"]["primary"] = "published"
    except BaseException:
        if batch is not None:
            _record_publication_checkpoint("primary", batch, run_uuid=args.run_uuid)
            _PUBLISHER_FAILURE_CONTEXT["publication_state"]["primary"] = (
                "published_checkpoint_followup_failed"
            )
        else:
            _PUBLISHER_FAILURE_CONTEXT["publication_state"]["primary"] = "failed"
        raise
    canonical_post: dict[str, Any] | None = None
    untracked_post: dict[str, Any] | None = None
    isolated_post: dict[str, Any] | None = None
    isolated_untracked_post: dict[str, Any] | None = None
    primary_inventory: dict[str, Any] | None = None
    primary_inventory_verification: dict[str, Any] | None = None
    post_error_type: str | None = None
    terminal_purpose_plan = _expected_terminal_publisher_purpose_plan(
        token=token,
        validation=validation,
    )
    _PUBLISHER_FAILURE_CONTEXT["expected_purpose_count"] = EXPECTED_TERMINAL_PUBLISHER_CHILD_COUNT
    _PUBLISHER_FAILURE_CONTEXT["expected_purposes"] = list(terminal_purpose_plan)
    _PUBLISHER_FAILURE_CONTEXT["publication_state"]["postpublication"] = "not_attempted"
    _set_publisher_stage("postpublication_readback")
    try:
        canonical_post = git_snapshot(canonical_repository, args.canonical_branch, **git_kwargs)
        untracked_post = untracked_summary(canonical_repository, **git_kwargs)
        isolated_post = verify_isolated_repository_prepublication(
            repository,
            expected_head=isolated_head,
            expected_tree=isolated_tree,
            **git_kwargs,
        )
        isolated_untracked_post = require_isolated_untracked_inventory()
        primary_inventory = directory_inventory(batch.output_directory)
        primary_inventory_verification = verify_primary_inventory_readback(
            batch,
            primary_inventory,
        )
    except BaseException as post_error:
        post_error_type = f"{type(post_error).__module__}.{type(post_error).__qualname__}"
    child_closure = publisher_child_containment_observation(
        terminal=True,
        expected_purposes=terminal_purpose_plan,
    )
    post_preserved = bool(
        post_error_type is None
        and canonical_post == canonical_git
        and untracked_post == untracked
        and isolated_post == {"head": isolated_head, "tree": isolated_tree, "tracked_changes": 0}
        and isolated_untracked_post == isolated_untracked
        and primary_inventory_verification is not None
        and primary_inventory_verification["exact_match"] is True
        and child_closure["all_children_cleanly_contained"] is True
    )
    post_documents = {
        "primary-review-reference.json": {
            "schema": f"{SCHEMA}.primary-review-reference",
            "primary_output_directory": str(batch.output_directory),
            "primary_inventory": (
                primary_inventory if primary_inventory is not None else "unproven"
            ),
            "primary_inventory_verification": (
                primary_inventory_verification
                if primary_inventory_verification is not None
                else "unproven"
            ),
            "primary_batch_sha256": hashlib.sha256(
                canonical_json_bytes(batch.to_dict())
            ).hexdigest(),
            "primary_run_uuid": str(uuid.UUID(args.run_uuid)),
        },
        "postpublication-readback.json": {
            "schema": f"{SCHEMA}.postpublication-readback",
            "status": "PASS" if post_preserved else "FAIL",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "exception_type": post_error_type,
            "exception_message_disclosed": False,
            "canonical_before": canonical_git,
            "canonical_after": canonical_post if canonical_post is not None else "unproven",
            "canonical_user_untracked_before": untracked,
            "canonical_user_untracked_after": (
                untracked_post if untracked_post is not None else "unproven"
            ),
            "isolated_after": isolated_post if isolated_post is not None else "unproven",
            "all_prepublication_baselines_preserved": post_preserved,
            "completion_marker_created": False,
            "success_marker_created": False,
            "r8_authorized": False,
        },
        "publisher-child-terminal-containment.json": child_closure,
    }
    _set_publisher_stage("postpublication_atomic_publication")
    _PUBLISHER_FAILURE_CONTEXT["publication_state"]["postpublication"] = "attempting"
    post_batch: Any | None = None
    try:
        post_batch = evidence.publish_pre_serialized_batch(
            post_review_parent,
            args.post_output_leaf,
            post_documents,
            run_uuid=args.attempt_uuid,
        )
        _record_publication_checkpoint(
            "postpublication",
            post_batch,
            run_uuid=args.attempt_uuid,
        )
    except BaseException:
        if post_batch is not None:
            _record_publication_checkpoint(
                "postpublication",
                post_batch,
                run_uuid=args.attempt_uuid,
            )
            _PUBLISHER_FAILURE_CONTEXT["publication_state"]["postpublication"] = (
                "published_checkpoint_followup_failed"
            )
        else:
            _PUBLISHER_FAILURE_CONTEXT["publication_state"]["postpublication"] = "failed"
        raise
    _PUBLISHER_FAILURE_CONTEXT["publication_state"]["postpublication"] = "published"
    if not post_preserved:
        raise ReviewPublisherError("postpublication_readback_failed_sealed_no_go")
    result = {
        **batch.to_dict(),
        "review_parent_commitment": review_parent_pin,
        "postpublication_verification": post_batch.to_dict(),
        "postpublication_readback": {
            "canonical_git": canonical_post,
            "canonical_user_untracked": untracked_post,
            "isolated": isolated_post,
            "all_prepublication_baselines_preserved": True,
        },
    }
    _best_effort_emit_result(result)
    return 0


def _main_internal_non_authoritative(
    argv: Sequence[str] | None = None,
    *,
    outer_invocation_authority_unproven: bool,
) -> int:
    if outer_invocation_authority_unproven is not True:
        raise ReviewPublisherError("internal_outer_unproven_latch_required")
    _PUBLISHER_CHILD_EXECUTIONS.clear()
    _PUBLISHER_FAILURE_CONTEXT.clear()
    args = parse_args(argv)
    failure_cause: BaseException | None = None
    dispatch_state = {"entered": False}
    try:
        try:
            return _run_publisher(args)
        except BaseException as exc:
            failure_cause = exc  # publisher-failure-handler-continuation
        if failure_cause is None:
            raise ReviewPublisherError("publisher_failure_cause_missing")
        sealed = _dispatch_publisher_failure(  # publisher-failure-dispatch-call
            args,
            failure_cause,
            state=dispatch_state,
        )
    except BaseException as seal_error:
        effective_error = failure_cause if failure_cause is not None else seal_error
        if dispatch_state["entered"] is not True:
            try:
                sealed = _dispatch_publisher_failure(
                    args,
                    effective_error,
                    state=dispatch_state,
                )
            except BaseException as final_seal_error:
                _best_effort_emit_result(
                    _publisher_failure_publication_error(
                        effective_error,
                        final_seal_error,
                    )
                )
            else:
                _best_effort_emit_result(sealed)
        else:
            _best_effort_emit_result(
                _publisher_failure_publication_error(effective_error, seal_error)
            )
        raise effective_error
    else:
        _best_effort_emit_result(sealed)
        assert failure_cause is not None
        raise failure_cause


def main(argv: Sequence[str] | None = None) -> int:
    """Public publication entry stays closed without an OS-bound outer capability."""

    del argv
    raise ReviewPublisherError("review_os_bound_outer_capability_unprovisioned")


if __name__ == "__main__":
    raise SystemExit(main())

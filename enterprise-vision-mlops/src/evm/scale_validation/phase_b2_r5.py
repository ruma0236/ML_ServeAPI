"""Fail-closed contracts and append-only evidence for Phase B2 r5.

R5 keeps the r4 restore state-machine semantics, but binds them to a new
kernel-backed process runner and to a genuinely fresh clock-sampling report.
This module performs no Docker, Kubernetes, WSL, or process termination action.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from evm.scale_validation.phase_b2_r4 import (
    ContractValidationError,
    DISRUPTIVE_CALL_NAMES,
    RESTORE_STAGE_ORDER,
    ProbeResult,
    RestoreCheckpoint,
    RestoreDeadline,
    RestoreHarness,
    RestoreReport,
    RestoreStage,
    TimeoutContract,
)


OLD_R4_REVISION = "e48c1d82938b9f64b414d58bb71c53dd258fbd78"
EXPECTED_B0_UID = "cfdab424-dcc5-4d5f-a46f-ae7530441ef4"
EXPECTED_ETW_AMENDMENT_SHA256 = "71ddc50a2a91f707b8183a19c87f490bdad8421ab18446dceb21622bc3439715"
RUNTIME_COMPONENTS = ("core", "process", "fresh", "runner", "validator")
RESTORE_LIFECYCLE_COUNTS = {
    "docker_off_probe": 0,
    "compose_stop": 0,
    "desktop_stop": 0,
    "wsl_shutdown": 0,
    "desktop_start": 0,
    "compose_start": 0,
}
FRESH_LIFECYCLE_COUNTS = {
    "docker_off_probe": 1,
    "compose_stop": 1,
    "desktop_stop": 1,
    "wsl_shutdown": 0,
    "desktop_start": 1,
    "compose_start": 1,
}
DOWNSTREAM_COUNTS = {
    "full_stack_3180": 0,
    "q0": 0,
    "calibration_54": 0,
    "matrix_78": 0,
    "integrated_v4": 0,
    "etw": 0,
}
ZERO_METRICS = (
    "windows_discontinuity",
    "wsl_discontinuity",
    "backward_step",
    "unclassified_gap",
    "bracket_violation",
)
FULL_SHA1 = re.compile(r"^[0-9a-f]{40}$")
FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PhaseB2R5Error(RuntimeError):
    """Base exception for an r5 fail-closed decision."""


class R5ContractError(PhaseB2R5Error):
    """Raised when executable state differs from a pinned r5 contract."""


class R5EvidenceExistsError(PhaseB2R5Error):
    """Raised when append-only evidence would overwrite an existing path."""


class R5SuccessInvariantError(PhaseB2R5Error):
    """Raised when success evidence is requested before every gate passes."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_oid(path: Path) -> str:
    content = Path(path).read_bytes()
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def git_head_blob_oid(repository_root: Path, path: Path) -> str:
    """Read the committed blob identity without depending on checkout EOLs."""

    root_result = subprocess.run(
        ["git", "-C", str(Path(repository_root).resolve()), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if root_result.returncode != 0:
        raise R5ContractError("runtime_git_toplevel_read_failed")
    git_root = Path(root_result.stdout.strip()).resolve()
    try:
        relative = Path(path).resolve().relative_to(git_root).as_posix()
    except ValueError as exc:
        raise R5ContractError("runtime_path_outside_git_repository") from exc
    blob_result = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", f"HEAD:{relative}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    value = blob_result.stdout.strip().lower()
    if blob_result.returncode != 0 or FULL_SHA1.fullmatch(value) is None:
        raise R5ContractError(f"runtime_git_blob_read_failed:{relative}")
    return value


def _full_sha1(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if FULL_SHA1.fullmatch(normalized) is None:
        raise R5ContractError(f"{label}_full_sha1_required")
    return normalized


def _full_sha256(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if FULL_SHA256.fullmatch(normalized) is None:
        raise R5ContractError(f"{label}_full_sha256_required")
    return normalized


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R5ContractError(f"{label}_mapping_required")
    return value


def _exact_count_mapping(
    value: Any,
    expected: Mapping[str, int],
    label: str,
) -> dict[str, int]:
    source = _mapping(value, label)
    actual = {str(key): int(raw) for key, raw in source.items()}
    if actual != dict(expected):
        raise R5ContractError(f"{label}_exact_counts_required:{actual}")
    return actual


def _validate_uuid(value: Any, label: str) -> str:
    normalized = str(value).lower()
    try:
        parsed = uuid.UUID(normalized)
    except (ValueError, AttributeError) as exc:
        raise R5ContractError(f"{label}_valid_uuid_required") from exc
    if str(parsed) != normalized:
        raise R5ContractError(f"{label}_canonical_uuid_required")
    return normalized


@dataclass(frozen=True)
class LifecycleTimeoutContract:
    """Separate budgets for long, graceful lifecycle and collection commands."""

    compose_internal_seconds: float = 120.0
    compose_wrapper_seconds: float = 150.0
    desktop_internal_seconds: float = 300.0
    desktop_wrapper_seconds: float = 330.0
    sampler_internal_seconds: float = 180.0
    sampler_wrapper_seconds: float = 210.0
    attempt_deadline_seconds: float = 1200.0

    FIELD_NAMES = (
        "compose_internal_seconds",
        "compose_wrapper_seconds",
        "desktop_internal_seconds",
        "desktop_wrapper_seconds",
        "sampler_internal_seconds",
        "sampler_wrapper_seconds",
        "attempt_deadline_seconds",
    )

    def validate(self) -> "LifecycleTimeoutContract":
        for name in self.FIELD_NAMES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise R5ContractError(f"{name}_numeric_required")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise R5ContractError(f"{name}_finite_positive_required")
        if not self.compose_internal_seconds < self.compose_wrapper_seconds:
            raise R5ContractError("compose_internal_must_be_less_than_wrapper")
        if not self.desktop_internal_seconds < self.desktop_wrapper_seconds:
            raise R5ContractError("desktop_internal_must_be_less_than_wrapper")
        if not self.sampler_internal_seconds < self.sampler_wrapper_seconds:
            raise R5ContractError("sampler_internal_must_be_less_than_wrapper")
        largest_wrapper = max(
            self.compose_wrapper_seconds,
            self.desktop_wrapper_seconds,
            self.sampler_wrapper_seconds,
        )
        if not largest_wrapper < self.attempt_deadline_seconds:
            raise R5ContractError("lifecycle_wrapper_must_be_less_than_attempt_deadline")
        return self

    def to_dict(self) -> dict[str, float]:
        self.validate()
        return {name: float(getattr(self, name)) for name in self.FIELD_NAMES}

    @classmethod
    def from_mapping(cls, value: Any) -> "LifecycleTimeoutContract":
        source = _mapping(value, "lifecycle_timeout_contract")
        if set(source) != set(cls.FIELD_NAMES):
            raise R5ContractError("lifecycle_timeout_contract_fields_mismatch")
        try:
            contract = cls(**{name: float(source[name]) for name in cls.FIELD_NAMES})
        except (TypeError, ValueError) as exc:
            raise R5ContractError("lifecycle_timeout_contract_numeric_required") from exc
        return contract.validate()


class ReconcileRestoreHarness(RestoreHarness):
    """Read every safe invariant, but latch closed on process uncertainty.

    A cleanly exited read-only probe that reports a service invariant failure
    does not suppress later read-only observations. A timeout, residual
    process, deadline/budget failure, or other manual latch stops the sequence
    immediately so no later probe can race an uncertain descendant.
    """

    def run_restore_only(self, checkpoint: RestoreCheckpoint) -> RestoreReport:
        if not isinstance(checkpoint, RestoreCheckpoint):
            raise TypeError("restore_checkpoint_required")
        started_at = self.utc_clock()
        started = float(self.clock())
        deadline = RestoreDeadline(
            self.contract.restore_deadline_seconds,
            clock=self.clock,
            started_monotonic=started,
        )
        call_counts = {name: 0 for name in DISRUPTIVE_CALL_NAMES}
        stages = []
        invariants: dict[str, bool] = {
            **{name: False for name in self.required_invariants},
            **{stage.value: False for stage in RESTORE_STAGE_ORDER},
        }
        residual_pids: set[int] = set()
        manual = False
        last_error: str | None = None
        budget_blocked = False

        for stage in RESTORE_STAGE_ORDER:
            evidence, result = self._run_stage(stage, deadline)
            stages.append(evidence)
            invariants[stage.value] = result.passed
            invariants.update(result.invariants)
            residual_pids.update(result.residual_pids)
            if not result.passed and last_error is None:
                last_error = result.last_error or f"restore_stage_failed:{stage.value}"
            budget_blocked = budget_blocked or bool(
                result.last_error is not None and "budget" in result.last_error
            )

            unsafe_to_follow = bool(
                result.manual_intervention_required
                or result.residual_pids
                or deadline.remaining_seconds <= 0
                or budget_blocked
            )
            if unsafe_to_follow:
                manual = True
                break

        required_ok = all(invariants.get(name) is True for name in self.required_invariants)
        all_stages = len(stages) == len(RESTORE_STAGE_ORDER) and all(
            stage.passed for stage in stages
        )
        deadline_exceeded = deadline.remaining_seconds <= 0 or budget_blocked
        passed = bool(
            all_stages and required_ok and not manual and not residual_pids and last_error is None
        )
        if not passed:
            manual = True
            if last_error is None:
                last_error = "restore_invariants_incomplete"
        ended = float(self.clock())
        return RestoreReport(
            mode="restore-only",
            started_at=started_at,
            ended_at=self.utc_clock(),
            duration_seconds=max(0.0, ended - started),
            expected_revision=self.expected_revision,
            passed=passed,
            manual_intervention_required=manual,
            deadline_exceeded=deadline_exceeded,
            last_error=last_error,
            stages=stages,
            call_counts=call_counts,
            residual_pids=tuple(sorted(residual_pids)),
            checkpoint=checkpoint.to_dict(),
            success_invariants=invariants,
            required_invariants=self.required_invariants,
            decision="restore_only_pass" if passed else "manual_intervention_required",
        )


def validate_runtime_pins(
    manifest: Mapping[str, Any], repository_root: Path
) -> dict[str, dict[str, str]]:
    runtime = _mapping(manifest.get("runtime"), "runtime")
    root = Path(repository_root).resolve()
    measured: dict[str, dict[str, str]] = {}
    for name in RUNTIME_COMPONENTS:
        component = _mapping(runtime.get(name), f"runtime_{name}")
        path = Path(str(component.get("path", ""))).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise R5ContractError(f"runtime_{name}_path_outside_repository") from exc
        if not path.is_file():
            raise R5ContractError(f"runtime_{name}_file_missing:{path}")
        expected_sha = _full_sha256(component.get("sha256"), f"runtime_{name}")
        expected_blob = _full_sha1(component.get("blob_oid"), f"runtime_{name}_blob")
        actual_sha = sha256_file(path)
        actual_blob = git_head_blob_oid(root, path)
        if actual_sha != expected_sha:
            raise R5ContractError(f"runtime_{name}_sha256_mismatch")
        if actual_blob != expected_blob:
            raise R5ContractError(f"runtime_{name}_blob_oid_mismatch")
        measured[name] = {
            "path": str(path),
            "sha256": actual_sha,
            "blob_oid": actual_blob,
        }
    return measured


def validate_r5_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_revision: str,
    mode: str,
    repository_root: Path | None = None,
    runtime_timeout: TimeoutContract | None = None,
    lifecycle_timeout: LifecycleTimeoutContract | None = None,
) -> dict[str, Any]:
    """Validate the sealed manifest against immutable executable defaults."""

    if manifest.get("schema_version") != "evm.s8_v4.x1_phase_b2_r5_work_order.v1":
        raise R5ContractError("r5_manifest_schema_required")
    if mode not in {"restore-only", "fresh"}:
        raise R5ContractError("r5_execution_mode_invalid")
    if manifest.get("execution_mode") != mode:
        raise R5ContractError("manifest_execution_mode_mismatch")
    revision = _full_sha1(manifest.get("canonical_revision"), "canonical_revision")
    expected = _full_sha1(expected_revision, "expected_revision")
    if revision != expected:
        raise R5ContractError("manifest_canonical_revision_mismatch")
    if revision == OLD_R4_REVISION:
        raise R5ContractError("r4_revision_pin_reuse_forbidden")
    tree = _full_sha1(manifest.get("canonical_tree"), "canonical_tree")

    try:
        executable_timeout = (runtime_timeout or TimeoutContract()).validate()
        promised_timeout = TimeoutContract.from_mapping(manifest.get("timeout_contract"))
    except ContractValidationError as exc:
        raise R5ContractError(str(exc)) from exc
    if promised_timeout.to_dict() != executable_timeout.to_dict():
        raise R5ContractError("manifest_runtime_timeout_contract_mismatch")
    executable_lifecycle = (lifecycle_timeout or LifecycleTimeoutContract()).validate()
    promised_lifecycle = LifecycleTimeoutContract.from_mapping(
        manifest.get("lifecycle_timeout_contract")
    )
    if promised_lifecycle.to_dict() != executable_lifecycle.to_dict():
        raise R5ContractError("manifest_runtime_lifecycle_timeout_mismatch")

    containment = _mapping(manifest.get("process_containment"), "process_containment")
    required_containment = {
        "provider": "windows_job_object",
        "create_suspended": True,
        "assign_before_resume": True,
        "breakaway_allowed": False,
        "kill_on_job_close": False,
        "terminate_job_object_allowed": False,
        "job_accounting_authoritative": True,
        "stdio_drain_before_followup": True,
        "residual_repoll_seconds": 120,
        "force_termination_attempts": 0,
        "wsl_run_uuid_and_process_group": True,
        "wsl_proc_residual_check": True,
    }
    if dict(containment) != required_containment:
        raise R5ContractError("process_containment_contract_mismatch")

    phase = _mapping(manifest.get("phase_b2_contract"), "phase_b2_contract")
    expected_phase = {
        "mode": "docker-off",
        "duration_seconds": 180,
        "cadence_ms": 100,
        "windows_samples": 1800,
        "wsl_samples": 1800,
        "windows_discontinuity": 0,
        "wsl_discontinuity": 0,
        "backward_step": 0,
        "unclassified_gap": 0,
        "bracket_violation": 0,
        "residual_pid": 0,
        "maximum_invocations": 1,
        "raw_samples_required": True,
        "restore_report_synthesis_forbidden": True,
    }
    if dict(phase) != expected_phase:
        raise R5ContractError("fresh_phase_b2_contract_mismatch")

    calls = _mapping(manifest.get("call_contract"), "call_contract")
    expected_lifecycle = (
        RESTORE_LIFECYCLE_COUNTS if mode == "restore-only" else FRESH_LIFECYCLE_COUNTS
    )
    _exact_count_mapping(calls.get(mode), expected_lifecycle, f"{mode}_call_contract")
    _exact_count_mapping(calls.get("downstream"), DOWNSTREAM_COUNTS, "downstream_call_contract")
    launcher_calls = _exact_count_mapping(
        calls.get("launcher"),
        {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0},
        "launcher_call_contract",
    )

    expected_state = _mapping(manifest.get("expected_state"), "expected_state")
    b0 = _mapping(expected_state.get("b0"), "expected_state_b0")
    uid = _validate_uuid(b0.get("uid"), "expected_b0_uid")
    if uid != EXPECTED_B0_UID:
        raise R5ContractError("expected_b0_uid_not_canonical")

    etw = _mapping(manifest.get("etw_contract"), "etw_contract")
    if etw.get("fresh_capture_required_for_phase_b2_go") is not False:
        raise R5ContractError("etw_contract_decision_mismatch")
    if int(etw.get("fresh_invocations", -1)) != 0:
        raise R5ContractError("etw_fresh_invocations_must_be_zero")
    if _full_sha256(etw.get("amendment_sha256"), "etw_amendment") != (
        EXPECTED_ETW_AMENDMENT_SHA256
    ):
        raise R5ContractError("etw_amendment_sha256_mismatch")

    evidence = _mapping(manifest.get("evidence"), "evidence")
    if evidence.get("write_mode") != "create-exclusive":
        raise R5ContractError("append_only_create_exclusive_required")
    if evidence.get("failure_creates_completion_marker") is not False:
        raise R5ContractError("failure_completion_marker_forbidden")
    if evidence.get("success_requires_all_invariants") is not True:
        raise R5ContractError("success_all_invariants_required")

    checkpoint = _mapping(manifest.get("checkpoint"), "checkpoint")
    _full_sha256(checkpoint.get("sha256"), "checkpoint")
    if not str(checkpoint.get("path", "")):
        raise R5ContractError("checkpoint_path_required")
    companion = _mapping(checkpoint.get("companion_index"), "checkpoint_companion_index")
    _full_sha256(companion.get("sha256"), "checkpoint_companion_index")
    if not str(companion.get("path", "")):
        raise R5ContractError("checkpoint_companion_index_path_required")
    expected_kind = "r4_failure_seal" if mode == "restore-only" else "r5_restore_only_index"
    if checkpoint.get("kind") != expected_kind:
        raise R5ContractError("checkpoint_kind_mismatch")

    runtime = None
    if repository_root is not None:
        runtime = validate_runtime_pins(manifest, repository_root)
    return {
        "revision": revision,
        "tree": tree,
        "mode": mode,
        "timeout_contract": executable_timeout.to_dict(),
        "lifecycle_timeout_contract": executable_lifecycle.to_dict(),
        "launcher_calls": launcher_calls,
        "b0_uid": uid,
        "runtime": runtime,
    }


def decode_launcher_evidence(encoded: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R5ContractError("launcher_evidence_base64_json_invalid") from exc
    if not isinstance(value, dict):
        raise R5ContractError("launcher_evidence_object_required")
    if value.get("schema") != "s8-v4-x1-phase-b2-r5-launcher-evidence/v1":
        raise R5ContractError("launcher_evidence_schema_mismatch")
    token = _mapping(value.get("token_evidence"), "launcher_token_evidence")
    if token.get("administrator") is not True:
        raise R5ContractError("launcher_administrator_token_required")
    integrity = str(token.get("integrity", "")).lower()
    if not any(marker in integrity for marker in ("high", "system", "s-1-16-12288")):
        raise R5ContractError("launcher_high_or_system_integrity_required")
    if str(token.get("token_elevation_type", "")).lower() != "full":
        raise R5ContractError("launcher_full_token_required")

    chain = _mapping(value.get("sha_chain"), "launcher_sha_chain")
    for name in (
        "outer",
        "bridge",
        "manifest",
        *RUNTIME_COMPONENTS,
        "checkpoint",
        "checkpoint_index",
    ):
        _full_sha256(chain.get(name), f"launcher_sha_chain_{name}")
    runtime = _mapping(manifest.get("runtime"), "runtime")
    for name in RUNTIME_COMPONENTS:
        component = _mapping(runtime.get(name), f"runtime_{name}")
        if str(chain[name]).lower() != str(component.get("sha256", "")).lower():
            raise R5ContractError(f"launcher_runtime_sha_chain_mismatch:{name}")
    if (
        str(chain["checkpoint"]).lower()
        != str(_mapping(manifest.get("checkpoint"), "checkpoint").get("sha256", "")).lower()
    ):
        raise R5ContractError("launcher_checkpoint_sha_chain_mismatch")
    companion = _mapping(
        _mapping(manifest.get("checkpoint"), "checkpoint").get("companion_index"),
        "checkpoint_companion_index",
    )
    if str(chain["checkpoint_index"]).lower() != str(companion.get("sha256", "")).lower():
        raise R5ContractError("launcher_checkpoint_index_sha_chain_mismatch")
    calls = _exact_count_mapping(
        value.get("invocation_counts"),
        {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0},
        "launcher_evidence_invocation_counts",
    )
    value["invocation_counts"] = calls
    return value


def read_checkpoint(
    path: Path,
    expected_sha256: str,
    *,
    mode: str,
) -> tuple[dict[str, Any], RestoreCheckpoint | None]:
    checkpoint_path = Path(path)
    if not checkpoint_path.is_file():
        raise R5ContractError(f"checkpoint_file_missing:{checkpoint_path}")
    expected = _full_sha256(expected_sha256, "checkpoint")
    if sha256_file(checkpoint_path) != expected:
        raise R5ContractError("checkpoint_sha256_mismatch")
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise R5ContractError("checkpoint_json_invalid") from exc
    if not isinstance(payload, dict):
        raise R5ContractError("checkpoint_object_required")
    if mode == "restore-only":
        if payload.get("failure_only") is not True:
            raise R5ContractError("r4_failure_checkpoint_required")
        if payload.get("acceptance_credit") is not False:
            raise R5ContractError("r4_checkpoint_acceptance_credit_forbidden")
        if payload.get("success_marker_created") is not False:
            raise R5ContractError("r4_checkpoint_success_marker_forbidden")
        metadata_value = payload.get("metadata", {})
        metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
        report = _mapping(metadata.get("report", payload.get("report", {})), "r4_report")
        raw_counts = _mapping(report.get("call_counts", {}), "r4_checkpoint_call_counts")
        counts = {name: int(raw_counts.get(name, 0)) for name in DISRUPTIVE_CALL_NAMES}
        checkpoint = RestoreCheckpoint(
            source="r4_failure_seal_checkpoint",
            historical_call_counts=counts,
            previous_attempt_failed=True,
        )
        return payload, checkpoint
    if mode == "fresh":
        if payload.get("restore_only_pass") is not True:
            raise R5ContractError("fresh_requires_passing_r5_restore_checkpoint")
        if payload.get("acceptance_credit") is not False:
            raise R5ContractError("restore_checkpoint_phase_b2_credit_forbidden")
        if payload.get("completion_marker_created") is not False:
            raise R5ContractError("restore_checkpoint_completion_marker_forbidden")
        return payload, None
    raise R5ContractError("checkpoint_mode_invalid")


def read_checkpoint_pair(
    primary_path: Path,
    primary_sha256: str,
    index_path: Path,
    index_sha256: str,
    *,
    mode: str,
) -> tuple[dict[str, Any], dict[str, Any], RestoreCheckpoint | None]:
    primary, restore_checkpoint = read_checkpoint(primary_path, primary_sha256, mode=mode)
    companion_path = Path(index_path)
    if Path(primary_path).resolve() == companion_path.resolve():
        raise R5ContractError("checkpoint_primary_and_index_must_be_distinct")
    if not companion_path.is_file():
        raise R5ContractError(f"checkpoint_index_file_missing:{companion_path}")
    expected_index = _full_sha256(index_sha256, "checkpoint_index")
    if sha256_file(companion_path) != expected_index:
        raise R5ContractError("checkpoint_index_sha256_mismatch")
    try:
        companion = json.loads(companion_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise R5ContractError("checkpoint_index_json_invalid") from exc
    if not isinstance(companion, dict):
        raise R5ContractError("checkpoint_index_object_required")
    if mode == "restore-only":
        if companion.get("failure_only") is not True:
            raise R5ContractError("r4_failure_index_required")
        if companion.get("acceptance_credit") is not False:
            raise R5ContractError("r4_failure_index_credit_forbidden")
        if companion.get("success_marker_created") is not False:
            raise R5ContractError("r4_failure_index_success_marker_forbidden")
    else:
        if companion.get("restore_only_pass") is not True:
            raise R5ContractError("r5_restore_only_index_required")
        if companion.get("acceptance_credit") is not False:
            raise R5ContractError("r5_restore_only_index_credit_forbidden")
        if companion.get("completion_marker_created") is not False:
            raise R5ContractError("r5_restore_only_index_marker_forbidden")
    return primary, companion, restore_checkpoint


def r5_restore_report(report: RestoreReport, run_id: str) -> dict[str, Any]:
    if report.mode != "restore-only":
        raise R5ContractError("restore_only_report_mode_required")
    value = report.to_dict()
    value.update(
        {
            "schema": "s8-v4-x1-phase-b2-r5-restore-report/v1",
            "run_id": str(run_id),
            "restore_only_pass": bool(report.passed),
            "acceptance_credit": False,
            "phase_b2_executed": False,
            "completion_marker_created": False,
            "process_containment": "windows_job_object",
        }
    )
    return value


def _file_identity(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


class EvidenceWriter:
    """One create-exclusive directory for one r5 restore or fresh attempt."""

    def __init__(self, output_directory: Path) -> None:
        self.root = Path(output_directory).resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise R5EvidenceExistsError(f"evidence_directory_exists:{self.root}") from exc

    def write_bytes(self, name: str, payload: bytes) -> dict[str, Any]:
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("evidence_leaf_name_required")
        path = self.root / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise R5EvidenceExistsError(f"evidence_path_exists:{path}") from exc
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return _file_identity(path, self.root)

    def write_json(self, name: str, value: Any) -> dict[str, Any]:
        return self.write_bytes(name, canonical_json_bytes(value))

    def inventory(self, *, exclude: Sequence[str] = ()) -> list[dict[str, Any]]:
        excluded = set(exclude)
        return [
            _file_identity(path, self.root)
            for path in sorted(self.root.iterdir(), key=lambda item: item.name)
            if path.is_file() and path.name not in excluded
        ]

    def seal_failure(
        self,
        report: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if (self.root / "completion-marker.json").exists():
            raise R5SuccessInvariantError("failure_after_completion_marker_forbidden")
        prior = self.inventory(exclude=("failure-seal.json", "failure-evidence-index.json"))
        seal = {
            "schema": "s8-v4-x1-phase-b2-r5-failure-seal/v1",
            "sealed_at": utc_now(),
            "failure_only": True,
            "decision": "manual_intervention_required",
            "acceptance_credit": False,
            "success_marker_created": False,
            "report": dict(report),
            "metadata": dict(metadata or {}),
            "prior_files": prior,
        }
        seal_file = self.write_json("failure-seal.json", seal)
        index = {
            "schema": "s8-v4-x1-phase-b2-r5-failure-evidence-index/v1",
            "created_at": utc_now(),
            "failure_only": True,
            "is_success_index": False,
            "acceptance_credit": False,
            "completion_marker_created": False,
            "files": [*prior, seal_file],
        }
        index_file = self.write_json("failure-evidence-index.json", index)
        return {"failure_seal": seal_file, "failure_index": index_file}

    def seal_restore_only(
        self,
        report: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if report.get("restore_only_pass") is not True or report.get("passed") is not True:
            raise R5SuccessInvariantError("passing_restore_only_report_required")
        if report.get("phase_b2_executed") is not False:
            raise R5SuccessInvariantError("restore_only_phase_b2_execution_forbidden")
        if report.get("residual_pids"):
            raise R5SuccessInvariantError("restore_only_residual_process_forbidden")
        report_file = self.write_json("restore-only-report.json", dict(report))
        index = {
            "schema": "s8-v4-x1-phase-b2-r5-restore-only-index/v1",
            "created_at": utc_now(),
            "restore_only_pass": True,
            "acceptance_credit": False,
            "is_phase_b2_success_index": False,
            "completion_marker_created": False,
            "metadata": dict(metadata or {}),
            "files": [report_file],
        }
        index_file = self.write_json("restore-only-index.json", index)
        return {"restore_only_report": report_file, "restore_only_index": index_file}

    def seal_fresh_success(
        self,
        report: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del report, metadata
        # A report-only API cannot prove that its raw Windows/WSL streams were
        # collected live or that its derived metrics recompute.  The sole r5
        # success path is phase_b2_r5_fresh.write_fresh_evidence(), which takes
        # a validated FreshExecution and re-analyzes its raw samples before it
        # creates a private index or completion marker.
        raise R5SuccessInvariantError("fresh_success_requires_validated_live_execution_writer")


def validate_fresh_success(report: Mapping[str, Any]) -> None:
    del report
    raise R5SuccessInvariantError("fresh_success_requires_validated_live_execution_writer")


__all__ = [
    "DOWNSTREAM_COUNTS",
    "EXPECTED_B0_UID",
    "EvidenceWriter",
    "FRESH_LIFECYCLE_COUNTS",
    "LifecycleTimeoutContract",
    "ProbeResult",
    "R5ContractError",
    "R5EvidenceExistsError",
    "ReconcileRestoreHarness",
    "R5SuccessInvariantError",
    "RESTORE_LIFECYCLE_COUNTS",
    "RESTORE_STAGE_ORDER",
    "RestoreCheckpoint",
    "RestoreDeadline",
    "RestoreHarness",
    "RestoreReport",
    "RestoreStage",
    "TimeoutContract",
    "decode_launcher_evidence",
    "git_blob_oid",
    "git_head_blob_oid",
    "r5_restore_report",
    "read_checkpoint",
    "read_checkpoint_pair",
    "sha256_file",
    "validate_fresh_success",
    "validate_r5_manifest",
    "validate_runtime_pins",
]

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


WORK_ORDER_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.oob-work-order.v1"
APPROVAL_REQUEST_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.approval-request.v1"
APPROVAL_RECEIPT_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.external-approval.v1"
RECEIPT_STRUCTURAL_VALIDATION_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.receipt-structural-validation.v1"
)
RECEIPT_CONSUMPTION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.receipt-consumption.v1"
CANDIDATE_INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.candidate-index.v1"
WORK_ORDER_STATE = "frozen_unexecuted"
WORK_ORDER_TTL_SECONDS = 1800
EXECUTION_MODE = "non_credit_wsl_containment_only"
DOMAIN = "wsl"

HEX40_RE = re.compile(r"[0-9a-f]{40}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
RUN_ID_RE = re.compile(r"pre-r8-r7s3-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}")
SAFE_LEAF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")

SOURCE_ROLES = {
    "outer_launcher",
    "process_module",
    "qualifier",
    "r7s1_runner",
    "stager",
}
SOURCE_PIN_FIELDS = {
    "path",
    "sha256",
    "bytes",
    "relative_path",
    "lf_normalized_sha256",
    "git_head_blob_oid",
    "git_mode",
}
BOOTSTRAP_PIN_FIELDS = {
    "root_orchestrator",
    "stager_bootstrap_sha256",
    "outer_bootstrap_sha256",
    "inner_bootstrap_sha256",
}
FORBIDDEN_WORK_ORDER_CYCLE_KEYS = {
    "root_bootstrap",
    "root_bootstrap_sha256",
    "oob_bootstrap",
    "oob_bootstrap_sha256",
    "bootstrap_source_sha256",
    "bootstrap_argv_sha256",
    "approval_request_sha256",
    "external_approval_receipt_sha256",
}

POST_APPROVAL_BUDGET = {
    "root_orchestrator": 1,
    "stager": 1,
    "outer": 1,
    "qualifier": 1,
    "automatic_retry": 0,
    "force_kill": 0,
    "service_mutation": 0,
    "r8": 0,
}
CANDIDATE_OBSERVED_CALLS = {
    "subprocess": 0,
    "live_wsl_qualification": 0,
    "docker_lifecycle": 0,
    "service_mutation": 0,
    "r8": 0,
    "automatic_retry": 0,
    "force_kill": 0,
}
CALL_CONTRACT = {
    "post_external_approval_budget": POST_APPROVAL_BUDGET,
    "candidate_observed_calls": CANDIDATE_OBSERVED_CALLS,
}
SAFETY_CONTRACT = {
    "append_only": True,
    "exclusive_create": True,
    "overwrite_allowed": False,
    "automatic_retry_allowed": False,
    "force_kill_allowed": False,
    "service_mutation_allowed": False,
    "production_execution_without_external_receipt_allowed": False,
    "reviewer_text_is_authority": False,
    "jira_is_authority": False,
    "notion_is_authority": False,
    "local_self_signature_is_authority": False,
    "root_bootstrap_pin_in_work_order": False,
    "caller_supplied_receipt_sha_is_independent_authority": False,
    "production_receipt_acceptance_implemented": False,
    "one_shot_consumption_primitive_available": True,
    "production_one_shot_consumption_wired": False,
}
CONTAINMENT_CONTRACT = {
    "create_suspended": True,
    "job_assignment_before_resume": True,
    "breakaway_allowed": False,
    "private_inherited_capability_required": True,
    "terminate_job_object_allowed": False,
    "kill_on_job_close_allowed": False,
    "residual_blocks_followup": True,
}
ARTIFACT_DAG_CONTRACT = {
    "external_approval_parents": [],
    "bootstrap_parents": ["external_approval"],
    "work_order_parents": ["external_approval"],
    "root_orchestrator_parents": ["external_approval", "bootstrap", "work_order"],
    "bootstrap_children": ["work_order", "root_orchestrator", "python_tcb"],
    "work_order_contains_bootstrap_pin": False,
    "hash_cycle_forbidden": True,
}
TIMEOUT_CONTRACTS = {"work_order_ttl_seconds": WORK_ORDER_TTL_SECONDS}

WORK_ORDER_FIELDS = {
    "schema",
    "state",
    "approval_request_id",
    "work_order_id",
    "created_at_utc",
    "expires_at_utc",
    "run_identity",
    "canonical_repository",
    "evidence_layout",
    "parent_map",
    "source_pins",
    "bootstrap_pins",
    "runtime_tcb",
    "timeout_contracts",
    "call_contract",
    "containment_contract",
    "safety_contract",
    "artifact_dag",
}


class R7S3OOBError(RuntimeError):
    """Fail-closed candidate freeze or external-approval validation error."""


@dataclass(frozen=True)
class CandidateArtifacts:
    output_directory: Path
    work_order: bytes
    bootstrap_source: bytes
    bootstrap_argv: bytes
    approval_request: bytes
    candidate_index: bytes

    def files(self) -> tuple[tuple[str, bytes], ...]:
        return (
            ("work-order.json", self.work_order),
            ("bootstrap-source.py", self.bootstrap_source),
            ("bootstrap-argv.json", self.bootstrap_argv),
            ("approval-request.json", self.approval_request),
            ("candidate-index.json", self.candidate_index),
        )


def canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise R7S3OOBError("canonical_json_serialization_failed") from exc
    return (rendered + "\n").encode("ascii")


def strict_canonical_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise R7S3OOBError(f"{label}_bytes_required")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate_key:{key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"nonfinite:{value}")

    try:
        text = raw.decode("utf-8")
        if "\ufeff" in text or "\ufffd" in text or "\x00" in text:
            raise ValueError("noncanonical_encoding")
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise R7S3OOBError(f"{label}_json_invalid") from exc
    if not isinstance(value, dict):
        raise R7S3OOBError(f"{label}_object_required")
    if raw != canonical_json_bytes(value):
        raise R7S3OOBError(f"{label}_json_not_canonical")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7S3OOBError(f"{label}_fields_mismatch")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S3OOBError(f"{label}_object_required")
    return dict(value)


def _sequence(value: Any, label: str) -> list[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise R7S3OOBError(f"{label}_array_required")
    return list(value)


def _hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise R7S3OOBError(f"{label}_invalid")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R7S3OOBError(f"{label}_positive_integer_required")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise R7S3OOBError(f"{label}_nonempty_string_required")
    return value


def _absolute_path(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    path = Path(text)
    if not path.is_absolute() or str(path) != text:
        raise R7S3OOBError(f"{label}_absolute_normalized_path_required")
    return text


def _uuid(value: Any, label: str) -> str:
    try:
        parsed = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise R7S3OOBError(f"{label}_uuid_invalid") from exc
    if not isinstance(value, str) or value != parsed:
        raise R7S3OOBError(f"{label}_uuid_noncanonical")
    return parsed


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise R7S3OOBError(f"{label}_canonical_utc_required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise R7S3OOBError(f"{label}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise R7S3OOBError(f"{label}_utc_required")
    return parsed.astimezone(UTC)


def _pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    _exact_keys(pin, {"path", "sha256", "bytes"}, label)
    return {
        "path": _absolute_path(pin["path"], f"{label}_path"),
        "sha256": _hex(pin["sha256"], HEX64_RE, f"{label}_sha256"),
        "bytes": _positive_int(pin["bytes"], f"{label}_bytes"),
    }


def _source_pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    _exact_keys(pin, SOURCE_PIN_FIELDS, label)
    relative = _nonempty(pin["relative_path"], f"{label}_relative_path")
    if relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
        raise R7S3OOBError(f"{label}_relative_path_invalid")
    if pin["git_mode"] not in {"100644", "100755"}:
        raise R7S3OOBError(f"{label}_git_mode_invalid")
    return {
        "path": _absolute_path(pin["path"], f"{label}_path"),
        "sha256": _hex(pin["sha256"], HEX64_RE, f"{label}_sha256"),
        "bytes": _positive_int(pin["bytes"], f"{label}_bytes"),
        "relative_path": relative,
        "lf_normalized_sha256": _hex(pin["lf_normalized_sha256"], HEX64_RE, f"{label}_lf_sha256"),
        "git_head_blob_oid": _hex(pin["git_head_blob_oid"], HEX40_RE, f"{label}_git_blob"),
        "git_mode": pin["git_mode"],
    }


def _runtime_pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    _exact_keys(pin, {"path", "sha256", "bytes", "version"}, label)
    return {
        "path": _absolute_path(pin["path"], f"{label}_path"),
        "sha256": _hex(pin["sha256"], HEX64_RE, f"{label}_sha256"),
        "bytes": _positive_int(pin["bytes"], f"{label}_bytes"),
        "version": _nonempty(pin["version"], f"{label}_version"),
    }


def _reject_hash_cycle_keys(value: Any, label: str = "work_order") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in FORBIDDEN_WORK_ORDER_CYCLE_KEYS:
                raise R7S3OOBError(f"{label}_bootstrap_hash_cycle_forbidden:{key}")
            _reject_hash_cycle_keys(item, f"{label}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_hash_cycle_keys(item, f"{label}[{index}]")


def validate_work_order(
    value: Mapping[str, Any], *, validation_time: datetime | None = None
) -> dict[str, Any]:
    work_order = _mapping(value, "work_order")
    _reject_hash_cycle_keys(work_order)
    _exact_keys(work_order, WORK_ORDER_FIELDS, "work_order")
    if work_order["schema"] != WORK_ORDER_SCHEMA:
        raise R7S3OOBError("work_order_schema_mismatch")
    if work_order["state"] != WORK_ORDER_STATE:
        raise R7S3OOBError("work_order_state_mismatch")
    approval_request_id = _nonempty(work_order["approval_request_id"], "approval_request_id")
    work_order_id = _nonempty(work_order["work_order_id"], "work_order_id")
    if (
        SAFE_ID_RE.fullmatch(approval_request_id) is None
        or SAFE_ID_RE.fullmatch(work_order_id) is None
    ):
        raise R7S3OOBError("work_order_or_approval_request_id_invalid")

    created = _utc(work_order["created_at_utc"], "work_order_created_at")
    expires = _utc(work_order["expires_at_utc"], "work_order_expires_at")
    if (expires - created).total_seconds() != WORK_ORDER_TTL_SECONDS:
        raise R7S3OOBError("work_order_ttl_exact_mismatch")
    now = (validation_time or datetime.now(UTC)).astimezone(UTC)
    if created > now:
        raise R7S3OOBError("work_order_from_future")
    if now >= expires:
        raise R7S3OOBError("work_order_expired")

    identity = _mapping(work_order["run_identity"], "run_identity")
    _exact_keys(
        identity,
        {
            "qualification_id",
            "global_run_id",
            "domain_run_id",
            "domain",
            "run_uuid",
            "attempt_uuid",
            "execution_mode",
        },
        "run_identity",
    )
    global_run_id = _nonempty(identity["global_run_id"], "global_run_id")
    if RUN_ID_RE.fullmatch(global_run_id) is None:
        raise R7S3OOBError("global_run_id_invalid")
    if identity["qualification_id"] != f"{global_run_id}-qualification":
        raise R7S3OOBError("qualification_id_binding_mismatch")
    if identity["domain"] != DOMAIN or identity["domain_run_id"] != f"{global_run_id}-wsl":
        raise R7S3OOBError("domain_identity_binding_mismatch")
    run_uuid = _uuid(identity["run_uuid"], "run_uuid")
    attempt_uuid = _uuid(identity["attempt_uuid"], "attempt_uuid")
    if run_uuid == attempt_uuid:
        raise R7S3OOBError("run_and_attempt_uuid_must_differ")
    if identity["execution_mode"] != EXECUTION_MODE:
        raise R7S3OOBError("execution_mode_mismatch")

    repository = _mapping(work_order["canonical_repository"], "canonical_repository")
    _exact_keys(
        repository,
        {"path", "branch", "commit", "tree", "tracked_changes", "untracked"},
        "canonical_repository",
    )
    _absolute_path(repository["path"], "canonical_repository_path")
    _nonempty(repository["branch"], "canonical_repository_branch")
    _hex(repository["commit"], HEX40_RE, "canonical_repository_commit")
    _hex(repository["tree"], HEX40_RE, "canonical_repository_tree")
    if repository["tracked_changes"] != 0 or isinstance(repository["tracked_changes"], bool):
        raise R7S3OOBError("canonical_repository_tracked_changes_must_equal_zero")
    untracked = _mapping(repository["untracked"], "canonical_repository_untracked")
    _exact_keys(untracked, {"count", "encoding", "path_set_sha256"}, "untracked")
    if untracked["count"] != 4244 or isinstance(untracked["count"], bool):
        raise R7S3OOBError("untracked_count_must_equal_4244")
    if untracked["encoding"] != "utf-8-nul-sorted":
        raise R7S3OOBError("untracked_encoding_mismatch")
    _hex(untracked["path_set_sha256"], HEX64_RE, "untracked_path_set_sha256")

    layout = _mapping(work_order["evidence_layout"], "evidence_layout")
    _exact_keys(
        layout,
        {
            "candidate_root",
            "runtime_evidence_root",
            "root_leaf",
            "root_emergency_leaf",
            "staging_leaf",
            "outer_leaf",
            "qualification_leaf",
        },
        "evidence_layout",
    )
    _absolute_path(layout["candidate_root"], "candidate_root")
    _absolute_path(layout["runtime_evidence_root"], "runtime_evidence_root")
    leaves = []
    for name in (
        "root_leaf",
        "root_emergency_leaf",
        "staging_leaf",
        "outer_leaf",
        "qualification_leaf",
    ):
        leaf = _nonempty(layout[name], f"evidence_{name}")
        if SAFE_LEAF_RE.fullmatch(leaf) is None:
            raise R7S3OOBError(f"evidence_{name}_invalid")
        leaves.append(leaf)
    if len(set(leaves)) != len(leaves):
        raise R7S3OOBError("evidence_leaves_must_be_distinct")

    _pin(work_order["parent_map"], "parent_map")
    source_pins = _mapping(work_order["source_pins"], "source_pins")
    _exact_keys(source_pins, SOURCE_ROLES, "source_pins")
    for role in sorted(SOURCE_ROLES):
        _source_pin(source_pins[role], f"source_{role}")

    bootstrap_pins = _mapping(work_order["bootstrap_pins"], "bootstrap_pins")
    _exact_keys(bootstrap_pins, BOOTSTRAP_PIN_FIELDS, "bootstrap_pins")
    _pin(bootstrap_pins["root_orchestrator"], "root_orchestrator")
    for role in ("stager", "outer", "inner"):
        _hex(
            bootstrap_pins[f"{role}_bootstrap_sha256"],
            HEX64_RE,
            f"{role}_bootstrap_sha256",
        )
    runtime = _mapping(work_order["runtime_tcb"], "runtime_tcb")
    _exact_keys(runtime, {"python", "closure_inventory", "closure_status"}, "runtime_tcb")
    _runtime_pin(runtime["python"], "python")
    _pin(runtime["closure_inventory"], "python_closure_inventory")
    if runtime["closure_status"] != "review_pending":
        raise R7S3OOBError("python_tcb_closure_must_remain_review_pending")

    if work_order["timeout_contracts"] != TIMEOUT_CONTRACTS:
        raise R7S3OOBError("timeout_contracts_mismatch")
    if work_order["call_contract"] != CALL_CONTRACT:
        raise R7S3OOBError("call_contract_mismatch")
    if work_order["containment_contract"] != CONTAINMENT_CONTRACT:
        raise R7S3OOBError("containment_contract_mismatch")
    if work_order["safety_contract"] != SAFETY_CONTRACT:
        raise R7S3OOBError("safety_contract_mismatch")
    if work_order["artifact_dag"] != ARTIFACT_DAG_CONTRACT:
        raise R7S3OOBError("artifact_dag_or_hash_cycle_mismatch")
    return work_order


def load_work_order_bytes(raw: bytes, *, validation_time: datetime | None = None) -> dict[str, Any]:
    return validate_work_order(
        strict_canonical_json_bytes(raw, "work_order"), validation_time=validation_time
    )


def _pin_for_bytes(path: Path, raw: bytes) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def render_bootstrap_source(
    *,
    approval_request_id: str,
    work_order_pin: Mapping[str, Any],
    root_orchestrator_pin: Mapping[str, Any],
    python_pin: Mapping[str, Any],
    run_identity: Mapping[str, Any],
    evidence_layout: Mapping[str, Any],
) -> bytes:
    approval_id = _nonempty(approval_request_id, "bootstrap_approval_request_id")
    work_order = _pin(work_order_pin, "bootstrap_work_order")
    root = _pin(root_orchestrator_pin, "bootstrap_root_orchestrator")
    python = _runtime_pin(python_pin, "bootstrap_python")
    identity = _mapping(run_identity, "bootstrap_run_identity")
    layout = _mapping(evidence_layout, "bootstrap_evidence_layout")
    bindings = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.frozen-bootstrap-bindings.v1",
        "approval_request_id": approval_id,
        "work_order": work_order,
        "root_orchestrator": root,
        "python": python,
        "run_identity": identity,
        "evidence_layout": layout,
        "work_order_contains_bootstrap_pin": False,
    }
    binding_literal = json.dumps(
        bindings,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    frozen_binding_literal = json.dumps(binding_literal, ensure_ascii=True)
    lines = (
        "from __future__ import annotations",
        "import sys",
        "R7S3_OOB_CANDIDATE_ONLY=True",
        "R7S3_PRODUCTION_ENTRY_ENABLED=False",
        f"FROZEN_BINDINGS_JSON={frozen_binding_literal}",
        "if len(sys.orig_argv)!=6 or sys.orig_argv[1:5]!=['-I','-S','-B','-c']:",
        "    raise SystemExit('r7s3_frozen_bootstrap_argv_rejected')",
        "if sys.argv!=['-c']:",
        "    raise SystemExit('r7s3_frozen_bootstrap_extra_argv_rejected')",
        "raise SystemExit('r7s3_external_approval_receipt_required')",
    )
    raw = ("\n".join(lines) + "\n").encode("ascii")
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise R7S3OOBError("bootstrap_byte_contract_violation")
    if raw.endswith(b"\n\n"):
        raise R7S3OOBError("bootstrap_terminal_lf_mismatch")
    return raw


def canonical_bootstrap_argv(python_path: str, bootstrap_source: bytes) -> tuple[list[str], bytes]:
    path = _absolute_path(python_path, "bootstrap_argv_python")
    if (
        not isinstance(bootstrap_source, bytes)
        or bootstrap_source.startswith(b"\xef\xbb\xbf")
        or b"\r" in bootstrap_source
        or not bootstrap_source.endswith(b"\n")
        or bootstrap_source.endswith(b"\n\n")
    ):
        raise R7S3OOBError("bootstrap_source_not_canonical_utf8_lf")
    try:
        source_text = bootstrap_source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R7S3OOBError("bootstrap_source_utf8_required") from exc
    argv = [path, "-I", "-S", "-B", "-c", source_text]
    return argv, canonical_json_bytes(argv)


def validate_bootstrap_argv(raw: bytes, *, python_path: str, bootstrap_source: bytes) -> list[str]:
    expected_argv, expected_raw = canonical_bootstrap_argv(python_path, bootstrap_source)
    if not isinstance(raw, bytes) or raw != expected_raw:
        raise R7S3OOBError("bootstrap_argv_exact_binding_mismatch")
    return expected_argv


def _approval_subject(
    *,
    bootstrap_source_path: Path,
    bootstrap_source: bytes,
    bootstrap_argv_path: Path,
    bootstrap_argv: bytes,
    work_order_pin: Mapping[str, Any],
    root_orchestrator_pin: Mapping[str, Any],
    commit: str,
    tree: str,
) -> dict[str, Any]:
    return {
        "bootstrap": _pin_for_bytes(bootstrap_source_path, bootstrap_source),
        "bootstrap_argv": _pin_for_bytes(bootstrap_argv_path, bootstrap_argv),
        "work_order": dict(work_order_pin),
        "root_orchestrator": dict(root_orchestrator_pin),
        "canonical_revision": {
            "commit": _hex(commit, HEX40_RE, "approval_subject_commit"),
            "tree": _hex(tree, HEX40_RE, "approval_subject_tree"),
        },
    }


def build_candidate_artifacts(
    output_directory: Path,
    work_order_value: Mapping[str, Any],
    *,
    validation_time: datetime | None = None,
) -> CandidateArtifacts:
    now = (validation_time or datetime.now(UTC)).astimezone(UTC)
    output = Path(os.path.abspath(output_directory))
    if not output.is_absolute() or str(output) != str(output_directory):
        raise R7S3OOBError("candidate_output_absolute_normalized_path_required")
    work_order = validate_work_order(work_order_value, validation_time=now)
    layout = _mapping(work_order["evidence_layout"], "evidence_layout")
    if layout["candidate_root"] != str(output):
        raise R7S3OOBError("candidate_output_work_order_binding_mismatch")

    work_order_raw = canonical_json_bytes(work_order)
    work_order_pin = _pin_for_bytes(output / "work-order.json", work_order_raw)
    root_pin = _pin(work_order["bootstrap_pins"]["root_orchestrator"], "root_orchestrator")
    python_pin = _runtime_pin(work_order["runtime_tcb"]["python"], "python")
    bootstrap_source = render_bootstrap_source(
        approval_request_id=work_order["approval_request_id"],
        work_order_pin=work_order_pin,
        root_orchestrator_pin=root_pin,
        python_pin=python_pin,
        run_identity=work_order["run_identity"],
        evidence_layout=layout,
    )
    _argv, bootstrap_argv = canonical_bootstrap_argv(python_pin["path"], bootstrap_source)
    subject = _approval_subject(
        bootstrap_source_path=output / "bootstrap-source.py",
        bootstrap_source=bootstrap_source,
        bootstrap_argv_path=output / "bootstrap-argv.json",
        bootstrap_argv=bootstrap_argv,
        work_order_pin=work_order_pin,
        root_orchestrator_pin=root_pin,
        commit=work_order["canonical_repository"]["commit"],
        tree=work_order["canonical_repository"]["tree"],
    )
    approval_request = {
        "schema": APPROVAL_REQUEST_SCHEMA,
        "status": "review_pending",
        "decision": "not_approved",
        "approval_request_id": work_order["approval_request_id"],
        "created_at_utc": now.isoformat().replace("+00:00", "Z"),
        "expires_at_utc": work_order["expires_at_utc"],
        "subject": subject,
        "authority_boundary": {
            "independent_external_receipt_required": True,
            "caller_supplied_expected_receipt_sha256_required": True,
            "caller_supplied_sha_is_independent_authority": False,
            "reviewer_text_authoritative": False,
            "jira_authoritative": False,
            "notion_authoritative": False,
            "local_self_signature_authoritative": False,
            "production_receipt_acceptance_implemented": False,
            "production_one_shot_consumption_wired": False,
        },
        "call_counts": dict(CANDIDATE_OBSERVED_CALLS),
        "production_entry_enabled": False,
    }
    approval_request_raw = canonical_json_bytes(approval_request)
    artifacts_without_index = {
        "work-order.json": work_order_raw,
        "bootstrap-source.py": bootstrap_source,
        "bootstrap-argv.json": bootstrap_argv,
        "approval-request.json": approval_request_raw,
    }
    candidate_index = {
        "schema": CANDIDATE_INDEX_SCHEMA,
        "status": "review_pending",
        "decision": "no_go_without_external_receipt",
        "approval_request_id": work_order["approval_request_id"],
        "artifacts": {
            name: _pin_for_bytes(output / name, raw)
            for name, raw in sorted(artifacts_without_index.items())
        },
        "call_counts": dict(CANDIDATE_OBSERVED_CALLS),
        "production_entry_enabled": False,
    }
    return CandidateArtifacts(
        output_directory=output,
        work_order=work_order_raw,
        bootstrap_source=bootstrap_source,
        bootstrap_argv=bootstrap_argv,
        approval_request=approval_request_raw,
        candidate_index=canonical_json_bytes(candidate_index),
    )


def _exclusive_write(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("candidate_write_zero")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_candidate(artifacts: CandidateArtifacts) -> dict[str, dict[str, Any]]:
    output = artifacts.output_directory
    output.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(output)
    published: dict[str, dict[str, Any]] = {}
    for name, raw in artifacts.files():
        path = output / name
        _exclusive_write(path, raw)
        readback = path.read_bytes()
        if readback != raw:
            raise R7S3OOBError(f"candidate_readback_mismatch:{name}")
        published[name] = _pin_for_bytes(path, readback)
    return published


def _validate_approval_request(value: Mapping[str, Any]) -> dict[str, Any]:
    request = _mapping(value, "approval_request")
    _exact_keys(
        request,
        {
            "schema",
            "status",
            "decision",
            "approval_request_id",
            "created_at_utc",
            "expires_at_utc",
            "subject",
            "authority_boundary",
            "call_counts",
            "production_entry_enabled",
        },
        "approval_request",
    )
    if (
        request["schema"] != APPROVAL_REQUEST_SCHEMA
        or request["status"] != "review_pending"
        or request["decision"] != "not_approved"
        or request["call_counts"] != CANDIDATE_OBSERVED_CALLS
        or request["production_entry_enabled"] is not False
    ):
        raise R7S3OOBError("approval_request_fail_closed_state_mismatch")
    expected_boundary = {
        "independent_external_receipt_required": True,
        "caller_supplied_expected_receipt_sha256_required": True,
        "caller_supplied_sha_is_independent_authority": False,
        "reviewer_text_authoritative": False,
        "jira_authoritative": False,
        "notion_authoritative": False,
        "local_self_signature_authoritative": False,
        "production_receipt_acceptance_implemented": False,
        "production_one_shot_consumption_wired": False,
    }
    if request["authority_boundary"] != expected_boundary:
        raise R7S3OOBError("approval_request_authority_boundary_mismatch")
    _nonempty(request["approval_request_id"], "approval_request_id")
    _utc(request["created_at_utc"], "approval_request_created")
    _utc(request["expires_at_utc"], "approval_request_expires")
    subject = _mapping(request["subject"], "approval_request_subject")
    _exact_keys(
        subject,
        {
            "bootstrap",
            "bootstrap_argv",
            "work_order",
            "root_orchestrator",
            "canonical_revision",
        },
        "approval_request_subject",
    )
    for role in ("bootstrap", "bootstrap_argv", "work_order", "root_orchestrator"):
        _pin(subject[role], f"approval_request_{role}")
    revision = _mapping(subject["canonical_revision"], "approval_request_revision")
    _exact_keys(revision, {"commit", "tree"}, "approval_request_revision")
    _hex(revision["commit"], HEX40_RE, "approval_request_commit")
    _hex(revision["tree"], HEX40_RE, "approval_request_tree")
    return request


def validate_external_approval_receipt(
    raw: bytes,
    *,
    expected_receipt_sha256: str | None,
    approval_request: Mapping[str, Any],
    validation_time: datetime | None = None,
) -> dict[str, Any]:
    if expected_receipt_sha256 is None:
        raise R7S3OOBError("caller_supplied_expected_external_receipt_sha256_required")
    expected_sha = _hex(expected_receipt_sha256, HEX64_RE, "expected_external_receipt_sha256")
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise R7S3OOBError("external_receipt_sha256_mismatch")
    request = _validate_approval_request(approval_request)
    receipt = strict_canonical_json_bytes(raw, "external_approval_receipt")
    _exact_keys(
        receipt,
        {
            "schema",
            "status",
            "decision",
            "approval_request_id",
            "issued_at_utc",
            "expires_at_utc",
            "authority",
            "approval_request",
            "subject",
        },
        "external_approval_receipt",
    )
    if (
        receipt["schema"] != APPROVAL_RECEIPT_SCHEMA
        or receipt["status"] != "approved"
        or receipt["decision"] != "approve_exact_candidate_once"
        or receipt["approval_request_id"] != request["approval_request_id"]
        or receipt["subject"] != request["subject"]
    ):
        raise R7S3OOBError("external_receipt_subject_or_decision_mismatch")
    authority = _mapping(receipt["authority"], "external_receipt_authority")
    _exact_keys(
        authority,
        {"mechanism", "reviewer_identity", "approval_id"},
        "external_receipt_authority",
    )
    if authority["mechanism"] != "independent_out_of_band_sha256":
        raise R7S3OOBError("external_receipt_authority_mechanism_mismatch")
    _nonempty(authority["reviewer_identity"], "external_receipt_reviewer_identity")
    _nonempty(authority["approval_id"], "external_receipt_approval_id")

    request_raw = canonical_json_bytes(request)
    expected_request_pin = {
        "sha256": hashlib.sha256(request_raw).hexdigest(),
        "bytes": len(request_raw),
    }
    if receipt["approval_request"] != expected_request_pin:
        raise R7S3OOBError("external_receipt_approval_request_pin_mismatch")
    request_created = _utc(request["created_at_utc"], "approval_request_created")
    request_expires = _utc(request["expires_at_utc"], "approval_request_expires")
    issued = _utc(receipt["issued_at_utc"], "external_receipt_issued")
    expires = _utc(receipt["expires_at_utc"], "external_receipt_expires")
    now = (validation_time or datetime.now(UTC)).astimezone(UTC)
    if issued < request_created or issued > now:
        raise R7S3OOBError("external_receipt_issuance_time_invalid")
    if expires <= issued or expires > request_expires or now >= expires:
        raise R7S3OOBError("external_receipt_expired_or_window_invalid")
    return {
        "schema": RECEIPT_STRUCTURAL_VALIDATION_SCHEMA,
        "status": "structure_and_caller_pin_match_unanchored",
        "receipt_sha256": expected_sha,
        "approval_request_sha256": expected_request_pin["sha256"],
        "receipt": receipt,
        "independent_anchor_verified": False,
        "one_shot_consumed": False,
        "production_approval_eligible": False,
    }


def consume_structurally_valid_receipt_once(
    validation: Mapping[str, Any], directory: str | os.PathLike[str]
) -> dict[str, Any]:
    """Atomically consume one structurally valid receipt once.

    This local append-only primitive prevents accidental/reentrant reuse.  It
    deliberately does not authenticate the caller-provided SHA or establish an
    independent reviewer authority, so its result remains production-ineligible.
    """

    expected_keys = {
        "schema",
        "status",
        "receipt_sha256",
        "approval_request_sha256",
        "receipt",
        "independent_anchor_verified",
        "one_shot_consumed",
        "production_approval_eligible",
    }
    _exact_keys(validation, expected_keys, "receipt_structural_validation")
    if (
        validation["schema"] != RECEIPT_STRUCTURAL_VALIDATION_SCHEMA
        or validation["status"] != "structure_and_caller_pin_match_unanchored"
        or validation["independent_anchor_verified"] is not False
        or validation["one_shot_consumed"] is not False
        or validation["production_approval_eligible"] is not False
    ):
        raise R7S3OOBError("receipt_structural_validation_not_consumable")
    receipt_sha = _hex(validation["receipt_sha256"], HEX64_RE, "receipt_sha256")
    request_sha = _hex(validation["approval_request_sha256"], HEX64_RE, "approval_request_sha256")
    receipt = _mapping(validation["receipt"], "validated_receipt")
    if receipt.get("approval_request_id") is None:
        raise R7S3OOBError("validated_receipt_request_id_missing")
    record = {
        "schema": RECEIPT_CONSUMPTION_SCHEMA,
        "status": "consumed_once_unanchored",
        "receipt_sha256": receipt_sha,
        "approval_request_sha256": request_sha,
        "approval_request_id": receipt["approval_request_id"],
        "independent_anchor_verified": False,
        "production_approval_eligible": False,
    }
    raw = canonical_json_bytes(record)
    destination = Path(directory)
    destination.mkdir(parents=False, exist_ok=True)
    marker = destination / f"receipt-{receipt_sha}.consumed.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(marker, flags, 0o600)
    except FileExistsError as exc:
        raise R7S3OOBError("external_receipt_already_consumed") from exc
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise R7S3OOBError("receipt_consumption_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if marker.read_bytes() != raw:
        raise R7S3OOBError("receipt_consumption_readback_mismatch")
    return {
        **record,
        "path": str(marker),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


__all__ = [
    "APPROVAL_RECEIPT_SCHEMA",
    "APPROVAL_REQUEST_SCHEMA",
    "ARTIFACT_DAG_CONTRACT",
    "CALL_CONTRACT",
    "CANDIDATE_OBSERVED_CALLS",
    "CONTAINMENT_CONTRACT",
    "CandidateArtifacts",
    "EXECUTION_MODE",
    "R7S3OOBError",
    "RECEIPT_CONSUMPTION_SCHEMA",
    "RECEIPT_STRUCTURAL_VALIDATION_SCHEMA",
    "SAFETY_CONTRACT",
    "TIMEOUT_CONTRACTS",
    "WORK_ORDER_SCHEMA",
    "WORK_ORDER_STATE",
    "WORK_ORDER_TTL_SECONDS",
    "build_candidate_artifacts",
    "canonical_bootstrap_argv",
    "canonical_json_bytes",
    "consume_structurally_valid_receipt_once",
    "load_work_order_bytes",
    "publish_candidate",
    "render_bootstrap_source",
    "strict_canonical_json_bytes",
    "validate_bootstrap_argv",
    "validate_external_approval_receipt",
    "validate_work_order",
]

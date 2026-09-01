from __future__ import annotations

import sys

if __name__ == "__main__" and not (
    sys.flags.isolated
    and sys.flags.no_site
    and sys.flags.no_user_site
    and sys.flags.ignore_environment
    and sys.flags.dont_write_bytecode
    and sys.flags.safe_path
):
    raise SystemExit("isolated_interpreter_flags_required:-I -S -B")

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import stat
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-launcher.v1"
RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-reservation.v1"
FAILURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-failure-seal.v1"
EMERGENCY_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-emergency-seal.v1"
FAILURE_INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-failure-index.v1"
BOOTSTRAP_ATTESTATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.bootstrap-attestation.v1"
LAUNCH_INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.launch-index.v1"
QUALIFICATION_EVIDENCE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.wsl-qualification.v1"
QUALIFICATION_INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.evidence-index.v1"
QUALIFICATION_FAILURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.failure-seal.v1"
QUALIFICATION_EMERGENCY_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.emergency-seal.v1"
WINDOWS_PATH_BUDGET = 240
R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED = False
PROJECT_ROOT = Path(os.path.abspath(__file__)).parents[2]
CANONICAL_EVIDENCE_ROOT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2-pre-r8-r7s2-gate"
    r"\x1-clock-phase-b2-pre-r8-r7s2-gate-20260901T131707Z-55f09ef"
)
TRUSTED_BOOTSTRAP_SOURCE_SHA256 = "f5043a9a0d6b27dafd4f1e7b123f1c147d6d93cc617bbc3696d26ed6a41d7a38"
TRUSTED_SOURCE_CONTENT = {
    "qualification_script": {
        "path": str(PROJECT_ROOT / "scripts" / "dev" / "qualify_wsl_process_group_r7s2.py"),
        "lf_normalized_sha256": "649dd27997d8bcc979ce6129783be4e4553e393c9b657f9edb34a2874ef365d6",
        "git_head_blob_oid": "4e19f03476cc6dae188acab0921e9cf103d15b03",
    },
    "stager": {
        "path": str(PROJECT_ROOT / "scripts" / "dev" / "stage_wsl_process_group_r7s2.py"),
        "lf_normalized_sha256": "39106798582fe4ff3ef6aa5f4543de17c689510414e23e0280dc64263fdb23f5",
        "git_head_blob_oid": "51703b56dc21ae553a6c45f2d174ead6c2a06f48",
    },
    "process_module": {
        "path": str(PROJECT_ROOT / "src" / "evm" / "scale_validation" / "phase_b2_r7_process.py"),
        "lf_normalized_sha256": "75aac2336d6f93bf5df6434871e1911d0922a3ff5e4f8dbea25712b0c65d8c74",
        "git_head_blob_oid": "6a65d8184afd4a4dbec0163f820ac7b8f03914be",
    },
}
OUTER_TIMEOUT_CONTRACT = {
    "source_git_call_count": 9,
    "source_git_per_call_total_seconds": 16,
    "source_git_total_seconds": 144,
    "ubuntu_gate_call_count": 2,
    "ubuntu_gate_per_call_total_seconds": 16,
    "ubuntu_gate_total_seconds": 32,
    "qualification_concurrent_total_seconds": 122,
    "serialization_allowance_seconds": 10,
    "inner_worst_case_seconds": 298,
    "outer_wrapper_seconds": 308,
    "outer_residual_seconds": 20,
    "outer_stream_drain_seconds": 10,
    "outer_restore_padding_seconds": 20,
    "outer_total_deadline_seconds": 358,
}
INNER_BOOTSTRAP_SOURCE = r"""import hashlib,json,os,stat,sys
def fail(message):
    raise SystemExit('r7s2_inner_bootstrap_rejected:'+message)
def no_reparse(path):
    current=os.path.abspath(path); chain=[]
    while True:
        chain.append(current)
        parent=os.path.dirname(current)
        if parent==current: break
        current=parent
    for item in reversed(chain):
        metadata=os.lstat(item)
        if stat.S_ISLNK(metadata.st_mode) or int(getattr(metadata,'st_file_attributes',0)) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            fail('reparse_component:'+item)
def normalized(raw):
    without_crlf=raw.replace(b'\r\n',b'')
    if b'\r' in without_crlf or (b'\r\n' in raw and b'\n' in without_crlf): fail('mixed_or_bare_cr')
    return raw.replace(b'\r\n',b'\n')
if not (sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment and sys.flags.dont_write_bytecode and sys.flags.safe_path): fail('isolated_flags')
if len(sys.argv)!=19: fail('argument_count')
qualifier_path,qualifier_sha,qualifier_bytes,qualifier_lf_sha,qualifier_blob,*inner_args=sys.argv[1:]
if len(inner_args)!=13 or inner_args[0]!='--contract' or inner_args[2]!='--expected-contract-sha256' or inner_args[4]!='--launch-index' or inner_args[6]!='--expected-launch-index-sha256' or inner_args[8]!='--outer-reservation' or inner_args[10]!='--expected-outer-reservation-sha256' or inner_args[12]!='--execute-non-credit-once': fail('inner_argv')
no_reparse(sys.executable); no_reparse(qualifier_path)
python_raw=open(sys.executable,'rb').read()
if os.path.normcase(os.path.abspath(sys.executable))!=os.path.normcase(os.path.abspath(r'C:\Users\opop0\miniconda3\python.exe')) or len(python_raw)!=104264 or hashlib.sha256(python_raw).hexdigest()!='ec0ea8d6907787b76dcf8524aaa93e52e167ceee62fa8778e182ea637a3dbc1d' or '.'.join(str(value) for value in sys.version_info[:3])!='3.13.11': fail('python_identity')
raw=open(qualifier_path,'rb').read()
if len(raw)!=int(qualifier_bytes) or hashlib.sha256(raw).hexdigest()!=qualifier_sha: fail('qualifier_raw')
lf=normalized(raw)
if hashlib.sha256(lf).hexdigest()!=qualifier_lf_sha: fail('qualifier_lf')
blob=hashlib.sha1(b'blob '+str(len(lf)).encode()+b'\0'+lf,usedforsecurity=False).hexdigest()
if blob!=qualifier_blob: fail('qualifier_blob')
attestation={'schema':'evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1','qualifier_path':qualifier_path,'qualifier_sha256':qualifier_sha,'qualifier_bytes':int(qualifier_bytes),'qualifier_lf_sha256':qualifier_lf_sha,'qualifier_blob_oid':qualifier_blob,'inner_argv_sha256':hashlib.sha256(json.dumps(inner_args,separators=(',',':')).encode()).hexdigest(),'bootstrap_source_sha256':hashlib.sha256(sys.orig_argv[5].encode()).hexdigest()}
sys.argv=[qualifier_path,*inner_args]
namespace={'__name__':'__main__','__file__':qualifier_path,'__package__':None,'__evm_r7s2_inner_bootstrap_attestation__':attestation}
exec(compile(lf,qualifier_path,'exec'),namespace,namespace)
"""
INNER_BOOTSTRAP_SOURCE_SHA256 = hashlib.sha256(INNER_BOOTSTRAP_SOURCE.encode("utf-8")).hexdigest()


class R7S2OuterError(RuntimeError):
    def __init__(self, message: str, *, process_evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.process_evidence = dict(process_evidence or {})


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _strict_canonical_json_line(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, str) or "\ufffd" in value or "\x00" in value:
        raise R7S2OuterError(f"{label}_encoding_invalid")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = item
        return result

    def reject_constant(token: str) -> None:
        raise ValueError(f"nonfinite_json_constant:{token}")

    try:
        parsed = json.loads(
            value,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise R7S2OuterError(f"{label}_json_invalid") from exc
    if not isinstance(parsed, dict) or _canonical_json(parsed).decode("utf-8") != value:
        raise R7S2OuterError(f"{label}_canonical_wire_mismatch")
    return parsed


def _path_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _assert_no_reparse_chain(path: Path, *, allow_missing_leaf: bool = False) -> None:
    absolute = Path(os.path.abspath(path))
    chain: list[Path] = []
    cursor = absolute
    while True:
        chain.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for index, current in enumerate(reversed(chain)):
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if index == len(chain) - 1 and allow_missing_leaf:
                continue
            raise R7S2OuterError(f"path_component_missing:{current}") from None
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if stat.S_ISLNK(metadata.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise R7S2OuterError(f"reparse_component_forbidden:{current}")


def _lf_normalized_source(raw: bytes) -> bytes:
    without_crlf = raw.replace(b"\r\n", b"")
    if b"\r" in without_crlf or (b"\r\n" in raw and b"\n" in without_crlf):
        raise R7S2OuterError("mixed_or_bare_cr_source_line_endings")
    return raw.replace(b"\r\n", b"\n")


def _git_blob_oid(raw: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw, usedforsecurity=False
    ).hexdigest()


def _read_json_once(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
    terminal_newline: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    if _sha256(raw) != expected_sha256:
        raise R7S2OuterError(f"{label}_sha256_mismatch")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate_key:{key}")
            result[key] = item
        return result

    def reject_constant(token: str) -> None:
        raise ValueError(f"non_finite_number:{token}")

    try:
        text = raw.decode("utf-8")
        if "\ufeff" in text or "\ufffd" in text or "\x00" in text:
            raise ValueError("invalid_utf8_scalar")
        payload = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise R7S2OuterError(f"{label}_json_invalid") from exc
    if not isinstance(payload, dict):
        raise R7S2OuterError(f"{label}_object_required")
    canonical = _canonical_json(payload)
    if not terminal_newline:
        canonical = canonical[:-1]
    if raw != canonical:
        raise R7S2OuterError(f"{label}_json_not_canonical")
    return raw, payload


def _verify_trusted_source(role: str, pin: Any) -> tuple[dict[str, Any], bytes]:
    if role not in TRUSTED_SOURCE_CONTENT or not isinstance(pin, Mapping):
        raise R7S2OuterError(f"trusted_source_pin_missing:{role}")
    expected = TRUSTED_SOURCE_CONTENT[role]
    path = Path(str(pin.get("path", "")))
    if not _path_equal(path, Path(expected["path"])):
        raise R7S2OuterError(f"trusted_source_path_mismatch:{role}")
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    normalized = _lf_normalized_source(raw)
    if (
        pin.get("bytes") != len(raw)
        or pin.get("sha256") != _sha256(raw)
        or pin.get("worktree_blob_oid") != _git_blob_oid(raw)
        or pin.get("git_mode") not in {"100644", "100755"}
        or pin.get("git_head_blob_oid") != expected["git_head_blob_oid"]
        or pin.get("git_normalized_worktree_blob_oid") != expected["git_head_blob_oid"]
        or _sha256(normalized) != expected["lf_normalized_sha256"]
        or _git_blob_oid(normalized) != expected["git_head_blob_oid"]
    ):
        raise R7S2OuterError(f"trusted_source_content_mismatch:{role}")
    return dict(pin), normalized


def _require_bootstrap_attestation(
    *,
    contract_path: Path,
    expected_contract_sha256: str,
    launch_index_path: Path,
    expected_launch_index_sha256: str,
    expected_outer_sha256: str,
    expected_source_commit: str,
    expected_source_tree: str,
) -> dict[str, Any]:
    value = globals().get("__evm_r7s2_bootstrap_attestation__")
    if not isinstance(value, Mapping):
        raise R7S2OuterError("verified_bootstrap_attestation_required")
    original_argv = list(getattr(sys, "orig_argv", ()))
    if (
        len(original_argv) < 6
        or original_argv[1:5] != ["-I", "-S", "-B", "-c"]
        or _sha256(original_argv[5].encode("utf-8")) != TRUSTED_BOOTSTRAP_SOURCE_SHA256
    ):
        raise R7S2OuterError("verified_bootstrap_command_required")
    expected_keys = {
        "schema",
        "outer_path",
        "outer_raw_sha256",
        "outer_bytes",
        "outer_lf_normalized_sha256",
        "contract_path",
        "contract_sha256",
        "launch_index_path",
        "launch_index_sha256",
        "expected_source_commit",
        "expected_source_tree",
        "outer_argv_sha256",
        "python_identity",
        "bootstrap_source_sha256",
    }
    if set(value) != expected_keys:
        raise R7S2OuterError("bootstrap_attestation_fields_mismatch")
    self_path = Path(os.path.abspath(__file__))
    _assert_no_reparse_chain(self_path)
    self_raw = self_path.read_bytes()
    self_normalized = _lf_normalized_source(self_raw)
    expected_argv_sha256 = _sha256(json.dumps(sys.argv[1:], separators=(",", ":")).encode("utf-8"))
    if (
        value["schema"] != BOOTSTRAP_ATTESTATION_SCHEMA
        or not _path_equal(Path(str(value["outer_path"])), self_path)
        or value["outer_raw_sha256"] != expected_outer_sha256
        or value["outer_bytes"] != len(self_raw)
        or value["outer_lf_normalized_sha256"] != _sha256(self_normalized)
        or not _path_equal(Path(str(value["contract_path"])), contract_path)
        or value["contract_sha256"] != expected_contract_sha256
        or not _path_equal(Path(str(value["launch_index_path"])), launch_index_path)
        or value["launch_index_sha256"] != expected_launch_index_sha256
        or value["expected_source_commit"] != expected_source_commit
        or value["expected_source_tree"] != expected_source_tree
        or value["outer_argv_sha256"] != expected_argv_sha256
        or value["bootstrap_source_sha256"] != TRUSTED_BOOTSTRAP_SOURCE_SHA256
    ):
        raise R7S2OuterError("bootstrap_attestation_binding_mismatch")
    python_identity = value["python_identity"]
    expected_python = {
        "path": r"C:\Users\opop0\miniconda3\python.exe",
        "sha256": "ec0ea8d6907787b76dcf8524aaa93e52e167ceee62fa8778e182ea637a3dbc1d",
        "bytes": 104264,
        "version": "3.13.11",
    }
    if python_identity != expected_python:
        raise R7S2OuterError("bootstrap_python_identity_mismatch")
    return dict(value)


def _validated_outer_timeout_contract(value: Any) -> dict[str, int]:
    if value != OUTER_TIMEOUT_CONTRACT:
        raise R7S2OuterError("outer_timeout_contract_exact_mismatch")
    timeout = {key: int(item) for key, item in OUTER_TIMEOUT_CONTRACT.items()}
    if not (
        timeout["source_git_call_count"] * timeout["source_git_per_call_total_seconds"]
        == timeout["source_git_total_seconds"]
        and timeout["ubuntu_gate_call_count"] * timeout["ubuntu_gate_per_call_total_seconds"]
        == timeout["ubuntu_gate_total_seconds"]
        and timeout["source_git_total_seconds"]
        + timeout["ubuntu_gate_total_seconds"]
        + timeout["qualification_concurrent_total_seconds"]
        == timeout["inner_worst_case_seconds"]
        and timeout["outer_wrapper_seconds"]
        == timeout["inner_worst_case_seconds"] + timeout["serialization_allowance_seconds"]
        and timeout["outer_total_deadline_seconds"]
        == timeout["outer_wrapper_seconds"]
        + timeout["outer_residual_seconds"]
        + timeout["outer_stream_drain_seconds"]
        + timeout["outer_restore_padding_seconds"]
        and timeout["inner_worst_case_seconds"]
        < timeout["outer_wrapper_seconds"]
        < timeout["outer_total_deadline_seconds"]
    ):
        raise R7S2OuterError("outer_timeout_contract_arithmetic_mismatch")
    return timeout


def _atomic_exclusive_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    if len(str(Path(os.path.abspath(path)))) > WINDOWS_PATH_BUDGET:
        raise R7S2OuterError("windows_path_budget_exceeded")
    _assert_no_reparse_chain(path.parent)
    _assert_no_reparse_chain(path, allow_missing_leaf=True)
    raw = _canonical_json(value)
    temporary = path.parent / f".t-{uuid.uuid4().hex[:8]}.tmp"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("outer_write_zero")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _assert_no_reparse_chain(path.parent)
    _assert_no_reparse_chain(path, allow_missing_leaf=True)
    if os.name != "nt":
        raise R7S2OuterError(f"atomic_no_replace_move_requires_windows:{temporary}")
    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    move_file.restype = wintypes.BOOL
    if not move_file(str(temporary), str(path), 0):
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, "atomic_destination_exists", str(path))
        raise OSError(error, f"atomic_no_replace_move_failed;partial={temporary}")
    _assert_no_reparse_chain(path)
    readback = path.read_bytes()
    if readback != raw or _sha256(readback) != _sha256(raw):
        raise R7S2OuterError("outer_publication_readback_mismatch")
    return {"path": str(path), "sha256": _sha256(raw), "bytes": len(raw)}


def _load_verified_qualifier(
    contract_path: Path,
    *,
    expected_contract_sha256: str,
    launch_index_path: Path,
    expected_launch_index_sha256: str,
    expected_outer_sha256: str,
    expected_source_commit: str,
    expected_source_tree: str,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    bootstrap_attestation = _require_bootstrap_attestation(
        contract_path=contract_path,
        expected_contract_sha256=expected_contract_sha256,
        launch_index_path=launch_index_path,
        expected_launch_index_sha256=expected_launch_index_sha256,
        expected_outer_sha256=expected_outer_sha256,
        expected_source_commit=expected_source_commit,
        expected_source_tree=expected_source_tree,
    )
    self_path = Path(os.path.abspath(__file__))
    _assert_no_reparse_chain(self_path)
    self_raw = self_path.read_bytes()
    if _sha256(self_raw) != expected_outer_sha256:
        raise R7S2OuterError("outer_self_sha256_mismatch")
    contract_raw, contract_payload = _read_json_once(
        contract_path,
        expected_sha256=expected_contract_sha256,
        label="outer_contract",
    )
    launch_raw, launch_payload = _read_json_once(
        launch_index_path,
        expected_sha256=expected_launch_index_sha256,
        label="outer_launch_index",
    )
    if (
        not _path_equal(contract_path.parent / "staging-index.json", launch_index_path)
        or not _path_equal(
            Path(str(contract_payload.get("evidence_root", ""))), CANONICAL_EVIDENCE_ROOT
        )
        or contract_payload.get("outer_timeout_contract") != OUTER_TIMEOUT_CONTRACT
        or launch_payload.get("outer_timeout_contract") != OUTER_TIMEOUT_CONTRACT
    ):
        raise R7S2OuterError("outer_canonical_path_or_timeout_binding_mismatch")
    source_identity = contract_payload.get("source_identity")
    launch_source_identity = launch_payload.get("source_identity")
    if not isinstance(source_identity, Mapping) or not isinstance(launch_source_identity, Mapping):
        raise R7S2OuterError("outer_source_identity_missing")
    if (
        source_identity.get("commit") != expected_source_commit
        or source_identity.get("tree") != expected_source_tree
        or launch_source_identity.get("commit") != expected_source_commit
        or launch_source_identity.get("tree") != expected_source_tree
    ):
        raise R7S2OuterError("outer_source_revision_mismatch")
    contract_pin = {
        "path": str(contract_path),
        "sha256": expected_contract_sha256,
        "bytes": len(contract_raw),
    }
    if (
        launch_payload.get("schema") != LAUNCH_INDEX_SCHEMA
        or launch_payload.get("status") != "ready_non_credit_not_executed"
        or launch_payload.get("contract") != contract_pin
        or launch_payload.get("qualification_id") != contract_payload.get("qualification_id")
        or launch_payload.get("run_uuid") != contract_payload.get("run_uuid")
        or launch_payload.get("attempt_id") != contract_payload.get("attempt_id")
    ):
        raise R7S2OuterError("outer_launch_index_binding_mismatch")
    try:
        published = datetime.fromisoformat(str(launch_payload["published_at_utc"]))
        expires = datetime.fromisoformat(str(launch_payload["expires_at_utc"]))
    except (KeyError, ValueError) as exc:
        raise R7S2OuterError("outer_launch_index_time_invalid") from exc
    now = datetime.now(UTC)
    if (
        published.tzinfo is None
        or expires.tzinfo is None
        or published > now
        or expires <= published
        or now >= expires
    ):
        raise R7S2OuterError("outer_launch_index_expired_or_future")
    source_pins = contract_payload.get("source_pins")
    launch_source_pins = launch_source_identity.get("source_pins")
    if not isinstance(source_pins, Mapping) or not isinstance(launch_source_pins, Mapping):
        raise R7S2OuterError("outer_contract_source_pin_missing")
    if any(launch_source_pins.get(role) != source_pins.get(role) for role in source_pins):
        raise R7S2OuterError("outer_contract_launch_source_pin_mismatch")
    stager_pin = launch_source_pins.get("stager")
    if stager_pin != source_identity.get("stager"):
        raise R7S2OuterError("outer_stager_pin_chain_mismatch")
    _verify_trusted_source("stager", stager_pin)
    qualifier_pin, qualifier_normalized = _verify_trusted_source(
        "qualification_script", source_pins.get("qualification_script")
    )
    _verify_trusted_source("process_module", source_pins.get("process_module"))
    outer_pin = source_pins.get("outer_launcher")
    self_normalized = _lf_normalized_source(self_raw)
    if not isinstance(outer_pin, Mapping) or (
        not _path_equal(Path(str(outer_pin.get("path"))), self_path)
        or outer_pin.get("sha256") != expected_outer_sha256
        or outer_pin.get("bytes") != len(self_raw)
        or outer_pin.get("worktree_blob_oid") != _git_blob_oid(self_raw)
        or outer_pin.get("git_head_blob_oid") != _git_blob_oid(self_normalized)
        or outer_pin.get("git_normalized_worktree_blob_oid") != _git_blob_oid(self_normalized)
    ):
        raise R7S2OuterError("outer_contract_self_pin_mismatch")
    bootstrap = launch_payload.get("bootstrap")
    if not isinstance(bootstrap, Mapping) or bootstrap != {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-bootstrap.v1",
        "source_sha256": TRUSTED_BOOTSTRAP_SOURCE_SHA256,
        "line_ending_policy": "uniform_lf_or_uniform_crlf_normalized_to_lf_bare_cr_forbidden",
        "outer_lf_normalized_sha256": _sha256(self_normalized),
        "expected_source_commit": expected_source_commit,
        "expected_source_tree": expected_source_tree,
    }:
        raise R7S2OuterError("outer_bootstrap_index_binding_mismatch")
    qualifier_path = Path(str(qualifier_pin.get("path")))
    module_name = f"_evm_pre_r8_r7s2_qualifier_{qualifier_pin['sha256'][:16]}"
    module = types.ModuleType(module_name)
    module.__file__ = str(qualifier_path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(qualifier_normalized, str(qualifier_path), "exec"), module.__dict__)
        if (
            module.CANONICAL_EVIDENCE_ROOT != CANONICAL_EVIDENCE_ROOT
            or module.OUTER_BOOTSTRAP_SOURCE_SHA256 != TRUSTED_BOOTSTRAP_SOURCE_SHA256
            or module.OUTER_TIMEOUT_CONTRACT != OUTER_TIMEOUT_CONTRACT
        ):
            raise R7S2OuterError("outer_qualifier_trusted_contract_mismatch")
        contract = module.load_contract(
            contract_path,
            expected_sha256=expected_contract_sha256,
            expected_evidence_root=module.CANONICAL_EVIDENCE_ROOT,
            launch_index_path=launch_index_path,
            expected_launch_index_sha256=expected_launch_index_sha256,
        )
        if contract.execution_authorized is not False:
            raise R7S2OuterError("outer_pre_reservation_contract_must_be_non_executable")
        token = dict(module._runtime_admin_token_readback(contract))
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module, contract, token, launch_payload, bootstrap_attestation


def _read_artifact_pin(
    value: Any, *, expected_path: Path, label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256", "bytes"}:
        raise R7S2OuterError(f"{label}_pin_invalid")
    pin = dict(value)
    if not _path_equal(Path(str(pin["path"])), expected_path):
        raise R7S2OuterError(f"{label}_path_mismatch")
    raw, payload = _read_json_once(
        expected_path,
        expected_sha256=str(pin["sha256"]),
        label=label,
        terminal_newline=False,
    )
    if pin["bytes"] != len(raw):
        raise R7S2OuterError(f"{label}_bytes_mismatch")
    return pin, payload


def _require_identity(payload: Mapping[str, Any], contract: Any, label: str) -> None:
    if (
        payload.get("qualification_id") != contract.qualification_id
        or payload.get("run_uuid") != contract.run_uuid
        or payload.get("attempt_id") != contract.attempt_id
    ):
        raise R7S2OuterError(f"{label}_identity_mismatch")


def _validate_inner_result(
    *,
    process: Mapping[str, Any],
    result: Any,
    contract: Any,
    expected_bootstrap_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, Mapping) or not isinstance(result.get("passed"), bool):
        raise R7S2OuterError("outer_inner_result_shape_invalid", process_evidence=process)
    return_code = process.get("return_code")
    if (return_code == 0) != (result["passed"] is True) or return_code not in {0, 3}:
        raise R7S2OuterError("outer_inner_return_code_result_mismatch", process_evidence=process)
    run_directory = Path(contract.run_directory)
    for forbidden_name in (
        "completion-marker.json",
        "completion.marker",
        "private-success-index.json",
    ):
        forbidden = run_directory / forbidden_name
        _assert_no_reparse_chain(forbidden, allow_missing_leaf=True)
        if forbidden.exists():
            raise R7S2OuterError("outer_inner_completion_artifact_forbidden")
    if return_code == 0:
        if set(result) != {"evidence", "index", "passed"}:
            raise R7S2OuterError("outer_inner_success_fields_mismatch")
        evidence_pin, evidence = _read_artifact_pin(
            result["evidence"],
            expected_path=run_directory / "qualification-evidence.json",
            label="inner_qualification_evidence",
        )
        index_pin, index = _read_artifact_pin(
            result["index"],
            expected_path=run_directory / "qualification-index.json",
            label="inner_qualification_index",
        )
        _require_identity(evidence, contract, "inner_qualification_evidence")
        _require_identity(index, contract, "inner_qualification_index")
        analysis = evidence.get("analysis")
        if (
            evidence.get("schema") != QUALIFICATION_EVIDENCE_SCHEMA
            or not isinstance(analysis, Mapping)
            or analysis.get("passed") is not True
            or index.get("schema") != QUALIFICATION_INDEX_SCHEMA
            or index.get("status") != "qualified_non_credit"
            or index.get("credit") != "non_credit_only"
            or index.get("report") != evidence_pin
            or index.get("failure_seal") is not None
            or index.get("completion_marker_created") is not False
            or index.get("private_phase_b2_success_index_created") is not False
            or index.get("r8_started") is not False
            or evidence.get("inner_bootstrap_attestation") != expected_bootstrap_attestation
        ):
            raise R7S2OuterError("outer_inner_success_semantics_mismatch")
        return {
            "kind": "qualified_non_credit",
            "evidence": evidence_pin,
            "index": index_pin,
        }
    emergency_pin_value = result.get("emergency_seal")
    if emergency_pin_value is not None:
        if set(result) != {
            "emergency_seal",
            "passed",
            "manual_intervention_required",
            "irrecoverable_primary_publication_failure",
            "failed_stage",
        }:
            raise R7S2OuterError("outer_inner_emergency_fields_mismatch")
        emergency_directory = Path(contract.emergency_directory)
        emergency_pin, emergency = _read_artifact_pin(
            emergency_pin_value,
            expected_path=emergency_directory / "emergency-seal.json",
            label="inner_emergency_seal",
        )
        _require_identity(emergency, contract, "inner_emergency_seal")
        bootstrap_provenance = emergency.get("bootstrap_provenance")
        if (
            result.get("manual_intervention_required") is not True
            or result.get("irrecoverable_primary_publication_failure") is not True
            or emergency.get("schema") != QUALIFICATION_EMERGENCY_SCHEMA
            or emergency.get("status") != "manual_intervention_required"
            or emergency.get("credit") != "zero_credit"
            or emergency.get("primary_run_directory") != str(run_directory)
            or not isinstance(bootstrap_provenance, Mapping)
            or set(bootstrap_provenance) != {"stage", "inner_bootstrap_attestation"}
            or bootstrap_provenance.get("stage") != "inner_bootstrap_verified_before_qualification"
            or bootstrap_provenance.get("inner_bootstrap_attestation")
            != expected_bootstrap_attestation
            or emergency.get("completion_marker_created") is not False
            or emergency.get("r8_started") is not False
        ):
            raise R7S2OuterError("outer_inner_emergency_semantics_mismatch")
        return {"kind": "emergency_zero_credit", "emergency_seal": emergency_pin}
    allowed = {"failure_seal", "index", "passed"}
    if "evidence" in result:
        allowed.add("evidence")
    if set(result) != allowed:
        raise R7S2OuterError("outer_inner_failure_fields_mismatch")
    failure_pin, failure = _read_artifact_pin(
        result["failure_seal"],
        expected_path=run_directory / "failure-seal.json",
        label="inner_failure_seal",
    )
    index_pin, index = _read_artifact_pin(
        result["index"],
        expected_path=run_directory / "failure-index.json",
        label="inner_failure_index",
    )
    evidence_pin: dict[str, Any] | None = None
    evidence_payload: dict[str, Any] | None = None
    if "evidence" in result:
        evidence_pin, evidence_payload = _read_artifact_pin(
            result["evidence"],
            expected_path=run_directory / "failure-evidence.json",
            label="inner_failure_evidence",
        )
        _require_identity(evidence_payload, contract, "inner_failure_evidence")
    _require_identity(failure, contract, "inner_failure_seal")
    _require_identity(index, contract, "inner_failure_index")
    expected_report = evidence_pin if evidence_pin is not None else failure_pin
    partial_evidence = failure.get("partial_evidence")
    referenced_evidence_valid = (
        evidence_pin is not None
        and isinstance(evidence_payload, Mapping)
        and evidence_payload.get("schema") == QUALIFICATION_EVIDENCE_SCHEMA
        and isinstance(evidence_payload.get("analysis"), Mapping)
        and evidence_payload["analysis"].get("passed") is False
        and evidence_payload.get("inner_bootstrap_attestation") == expected_bootstrap_attestation
        and failure.get("failure_evidence") == evidence_pin
        and partial_evidence is None
    )
    exception_partial_valid = (
        evidence_pin is None
        and failure.get("failure_evidence") is None
        and isinstance(partial_evidence, Mapping)
        and partial_evidence.get("inner_bootstrap_attestation") == expected_bootstrap_attestation
    )
    if (
        failure.get("schema") != QUALIFICATION_FAILURE_SCHEMA
        or failure.get("status") != "manual_intervention_required"
        or failure.get("credit") != "zero_credit"
        or failure.get("completion_marker_created") is not False
        or failure.get("r8_started") is not False
        or index.get("schema") != QUALIFICATION_INDEX_SCHEMA
        or index.get("status") != "zero_credit_failure"
        or index.get("credit") != "zero_credit"
        or index.get("report") != expected_report
        or index.get("failure_seal") != failure_pin
        or index.get("completion_marker_created") is not False
        or index.get("private_phase_b2_success_index_created") is not False
        or index.get("r8_started") is not False
        or not (referenced_evidence_valid or exception_partial_valid)
    ):
        raise R7S2OuterError("outer_inner_failure_semantics_mismatch")
    validated = {
        "kind": "failure_zero_credit",
        "failure_seal": failure_pin,
        "index": index_pin,
    }
    if evidence_pin is not None:
        validated["evidence"] = evidence_pin
    return validated


def _expected_inner_bootstrap_attestation(
    command: Sequence[str], qualifier_pin: Mapping[str, Any]
) -> dict[str, Any]:
    if (
        len(command) != 24
        or list(command[1:6]) != ["-I", "-S", "-B", "-c", INNER_BOOTSTRAP_SOURCE]
        or command[6] != qualifier_pin.get("path")
        or command[7] != qualifier_pin.get("sha256")
        or command[8] != str(qualifier_pin.get("bytes"))
        or command[9] != TRUSTED_SOURCE_CONTENT["qualification_script"]["lf_normalized_sha256"]
        or command[10] != qualifier_pin.get("git_head_blob_oid")
        or list(command[11::2])
        != [
            "--contract",
            "--expected-contract-sha256",
            "--launch-index",
            "--expected-launch-index-sha256",
            "--outer-reservation",
            "--expected-outer-reservation-sha256",
            "--execute-non-credit-once",
        ]
    ):
        raise R7S2OuterError("inner_bootstrap_command_layout_mismatch")
    inner_args = list(command[11:])
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1",
        "qualifier_path": qualifier_pin["path"],
        "qualifier_sha256": qualifier_pin["sha256"],
        "qualifier_bytes": qualifier_pin["bytes"],
        "qualifier_lf_sha256": TRUSTED_SOURCE_CONTENT["qualification_script"][
            "lf_normalized_sha256"
        ],
        "qualifier_blob_oid": qualifier_pin["git_head_blob_oid"],
        "inner_argv_sha256": _sha256(json.dumps(inner_args, separators=(",", ":")).encode("utf-8")),
        "bootstrap_source_sha256": INNER_BOOTSTRAP_SOURCE_SHA256,
    }


def _typed_exception_evidence(exception: Exception) -> dict[str, Any]:
    if isinstance(exception, R7S2OuterError):
        return dict(exception.process_evidence)
    to_dict = getattr(exception, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _partial_inventory(directory: Path) -> list[dict[str, Any]]:
    _assert_no_reparse_chain(directory, allow_missing_leaf=True)
    if not directory.exists():
        return []
    _assert_no_reparse_chain(directory)
    inventory: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        _assert_no_reparse_chain(path)
        if not path.is_file():
            raise R7S2OuterError(f"outer_partial_non_file_forbidden:{path}")
        raw = path.read_bytes()
        inventory.append(
            {"path": str(path), "name": path.name, "sha256": _sha256(raw), "bytes": len(raw)}
        )
    return inventory


def _process_failure_payload(
    *,
    contract: Any,
    expected_contract_sha256: str,
    expected_launch_index_sha256: str,
    failed_stage: str,
    exception: Exception,
    reservation: Mapping[str, Any] | None,
    child_calls: int,
    validated_inner_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = _typed_exception_evidence(exception)
    residual = evidence.get("residual_pids", [])
    residual_pids = (
        sorted(
            {
                pid
                for pid in residual
                if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
            }
        )
        if isinstance(residual, list)
        else []
    )
    return {
        "schema": FAILURE_SCHEMA,
        "qualification_id": contract.qualification_id,
        "run_uuid": contract.run_uuid,
        "attempt_id": contract.attempt_id,
        "sealed_at_utc": datetime.now(UTC).isoformat(),
        "status": "manual_intervention_required",
        "credit": "zero_credit",
        "failed_stage": failed_stage,
        "exception_type": type(exception).__name__,
        "exception": str(exception),
        "process_evidence": evidence,
        "validated_inner_result": dict(validated_inner_result or {}),
        "partial_inventory": _partial_inventory(
            Path(contract.launch_authorization["outer_evidence_directory"])
        ),
        "residual_pids": residual_pids,
        "reservation": dict(reservation or {}),
        "expected_contract_sha256": expected_contract_sha256,
        "expected_launch_index_sha256": expected_launch_index_sha256,
        "qualification_child_calls": child_calls,
        "automatic_retry_count": 0,
        "forced_termination_attempts": int(evidence.get("forced_termination_attempts", 0) or 0),
        "wsl_shutdown_calls": 0,
        "docker_kubernetes_service_mutations": 0,
        "r8_started": False,
    }


def _emergency(
    contract: Any,
    *,
    failed_stage: str,
    exception: Exception,
    expected_contract_sha256: str,
    expected_launch_index_sha256: str,
    reservation: Mapping[str, Any] | None,
    child_calls: int,
) -> dict[str, Any]:
    primary = Path(contract.launch_authorization["outer_evidence_directory"])
    emergency = primary.parent / f"{primary.name}-emergency-seal"
    try:
        _assert_no_reparse_chain(emergency, allow_missing_leaf=True)
        os.mkdir(emergency)
        payload = _process_failure_payload(
            contract=contract,
            expected_contract_sha256=expected_contract_sha256,
            expected_launch_index_sha256=expected_launch_index_sha256,
            failed_stage=failed_stage,
            exception=exception,
            reservation=reservation,
            child_calls=child_calls,
        )
        payload["schema"] = EMERGENCY_SCHEMA
        payload["primary_outer_directory"] = str(primary)
        pin = _atomic_exclusive_json(emergency / "emergency-seal.json", payload)
    except Exception as emergency_exc:
        raise R7S2OuterError("outer_emergency_seal_failed_no_retry") from emergency_exc
    return {"passed": False, "emergency_seal": pin, "manual_intervention_required": True}


def _publish_outer_failure(
    contract: Any,
    *,
    outer_directory: Path,
    failed_stage: str,
    exception: Exception,
    expected_contract_sha256: str,
    expected_launch_index_sha256: str,
    reservation: Mapping[str, Any],
    child_calls: int,
    validated_inner_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    failure_payload = _process_failure_payload(
        contract=contract,
        expected_contract_sha256=expected_contract_sha256,
        expected_launch_index_sha256=expected_launch_index_sha256,
        failed_stage=failed_stage,
        exception=exception,
        reservation=reservation,
        child_calls=child_calls,
        validated_inner_result=validated_inner_result,
    )
    try:
        seal = _atomic_exclusive_json(
            outer_directory / "outer-failure-seal.json",
            failure_payload,
        )
    except Exception as seal_exc:
        return _emergency(
            contract,
            failed_stage="outer_failure_seal_publication",
            exception=seal_exc,
            expected_contract_sha256=expected_contract_sha256,
            expected_launch_index_sha256=expected_launch_index_sha256,
            reservation=reservation,
            child_calls=child_calls,
        )
    try:
        index = _atomic_exclusive_json(
            outer_directory / "outer-failure-index.json",
            {
                "schema": FAILURE_INDEX_SCHEMA,
                "qualification_id": contract.qualification_id,
                "run_uuid": contract.run_uuid,
                "attempt_id": contract.attempt_id,
                "indexed_at_utc": datetime.now(UTC).isoformat(),
                "status": "manual_intervention_required",
                "credit": "zero_credit",
                "failure_seal": seal,
                "validated_inner_result": dict(validated_inner_result or {}),
                "qualification_child_calls": child_calls,
                "automatic_retry_count": 0,
                "forced_termination_attempts": failure_payload["forced_termination_attempts"],
                "completion_marker_created": False,
                "r8_started": False,
            },
        )
    except Exception as index_exc:
        return _emergency(
            contract,
            failed_stage="outer_failure_index_publication",
            exception=index_exc,
            expected_contract_sha256=expected_contract_sha256,
            expected_launch_index_sha256=expected_launch_index_sha256,
            reservation=reservation,
            child_calls=child_calls,
        )
    return {
        "passed": False,
        "failure_seal": seal,
        "index": index,
        "manual_intervention_required": True,
    }


def execute_once(
    *,
    contract_path: Path,
    expected_contract_sha256: str,
    launch_index_path: Path,
    expected_launch_index_sha256: str,
    expected_outer_sha256: str,
    expected_source_commit: str,
    expected_source_tree: str,
) -> dict[str, Any]:
    if R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED is not True:
        raise R7S2OuterError("r7s2_out_of_band_root_anchor_required")
    _validated_outer_timeout_contract(OUTER_TIMEOUT_CONTRACT)
    module, contract, token, _launch_payload, bootstrap_attestation = _load_verified_qualifier(
        contract_path,
        expected_contract_sha256=expected_contract_sha256,
        launch_index_path=launch_index_path,
        expected_launch_index_sha256=expected_launch_index_sha256,
        expected_outer_sha256=expected_outer_sha256,
        expected_source_commit=expected_source_commit,
        expected_source_tree=expected_source_tree,
    )
    outer_directory = Path(contract.launch_authorization["outer_evidence_directory"])
    emergency_directory = outer_directory.parent / f"{outer_directory.name}-emergency-seal"
    for path in (outer_directory, emergency_directory):
        _assert_no_reparse_chain(path, allow_missing_leaf=True)
        if path.exists():
            raise FileExistsError(path)
    try:
        os.mkdir(outer_directory)
        _assert_no_reparse_chain(outer_directory)
    except FileExistsError:
        raise R7S2OuterError("concurrent_or_replayed_attempt_rejected_no_write") from None
    except Exception as directory_exc:
        return _emergency(
            contract,
            failed_stage="outer_directory_creation",
            exception=directory_exc,
            expected_contract_sha256=expected_contract_sha256,
            expected_launch_index_sha256=expected_launch_index_sha256,
            reservation=None,
            child_calls=0,
        )
    reservation_payload = {
        "schema": RESERVATION_SCHEMA,
        "qualification_id": contract.qualification_id,
        "run_uuid": contract.run_uuid,
        "attempt_id": contract.attempt_id,
        "reserved_at_utc": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "process_creation_filetime": module._process_creation_filetime(os.getpid()),
        "administrator_token_evidence": token,
        "contract_sha256": expected_contract_sha256,
        "launch_index_sha256": expected_launch_index_sha256,
        "outer_sha256": expected_outer_sha256,
        "outer_bootstrap_attestation": bootstrap_attestation,
        "qualification_child_budget": 1,
        "automatic_retry_budget": 0,
        "forced_termination_budget": 0,
    }
    try:
        reservation = _atomic_exclusive_json(
            outer_directory / "outer-reservation.json", reservation_payload
        )
    except Exception as reservation_exc:
        return _emergency(
            contract,
            failed_stage="outer_reservation_publication",
            exception=reservation_exc,
            expected_contract_sha256=expected_contract_sha256,
            expected_launch_index_sha256=expected_launch_index_sha256,
            reservation=None,
            child_calls=0,
        )
    source_pins = contract.raw["source_pins"]
    process_pin = source_pins["process_module"]
    qualifier_pin = source_pins["qualification_script"]
    python_pin = contract.host_binaries["python"]
    runner = module.WindowsJobProcessRunner(
        module._make_contract(
            wrapper=OUTER_TIMEOUT_CONTRACT["outer_wrapper_seconds"],
            residual=OUTER_TIMEOUT_CONTRACT["outer_residual_seconds"],
            stream=OUTER_TIMEOUT_CONTRACT["outer_stream_drain_seconds"],
            restore_padding=OUTER_TIMEOUT_CONTRACT["outer_restore_padding_seconds"],
        )
    )
    command = (
        python_pin.path,
        "-I",
        "-S",
        "-B",
        "-c",
        INNER_BOOTSTRAP_SOURCE,
        qualifier_pin["path"],
        qualifier_pin["sha256"],
        str(qualifier_pin["bytes"]),
        TRUSTED_SOURCE_CONTENT["qualification_script"]["lf_normalized_sha256"],
        qualifier_pin["git_head_blob_oid"],
        "--contract",
        str(contract_path),
        "--expected-contract-sha256",
        expected_contract_sha256,
        "--launch-index",
        str(launch_index_path),
        "--expected-launch-index-sha256",
        expected_launch_index_sha256,
        "--outer-reservation",
        reservation["path"],
        "--expected-outer-reservation-sha256",
        reservation["sha256"],
        "--execute-non-credit-once",
    )
    expected_inner_bootstrap_attestation = _expected_inner_bootstrap_attestation(
        command, qualifier_pin
    )
    child_calls = 1
    try:
        outcome = runner.run(
            command,
            name="pre-r8-r7s2-qualification-inner-exactly-once",
            cwd=os.environ.get("SystemRoot", r"C:\Windows"),
            env={
                "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
            },
            poll_interval_seconds=0.01,
            run_uuid=contract.run_uuid,
        )
        try:
            process = json.loads(json.dumps(outcome.to_dict(), allow_nan=False))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise R7S2OuterError("outer_inner_process_not_canonical_json") from exc
        if set(process) != {
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
        }:
            raise R7S2OuterError("outer_inner_process_fields_mismatch", process_evidence=process)
        if not (
            process.get("name") == "pre-r8-r7s2-qualification-inner-exactly-once"
            and process.get("run_uuid") == contract.run_uuid
            and process.get("command") == list(command)
            and process.get("safe_for_followup") is (process.get("return_code") == 0)
            and process.get("forced_termination_attempts") == 0
            and process.get("active_process_zero") is True
            and process.get("final_active_process_count") == 0
            and process.get("job_limit_flags") == 0
            and process.get("cancelled") is False
            and process.get("manual_intervention_required") is False
            and process.get("streams_drained") is True
            and process.get("stdout_drained") is True
            and process.get("stderr_drained") is True
            and process.get("identity_coverage_complete") is True
            and process.get("timed_out") is False
            and process.get("residual_pids") == []
            and process.get("errors") == []
            and process.get("return_code") in {0, 3}
        ):
            raise R7S2OuterError("outer_inner_process_unsafe", process_evidence=process)
        try:
            module._validate_success_job_timeline(
                process,
                label="outer_inner_process",
                run_uuid=contract.run_uuid,
                expected_image=python_pin.path,
            )
        except Exception as exc:
            raise R7S2OuterError(
                f"outer_inner_job_timeline_invalid:{type(exc).__name__}:{exc}",
                process_evidence=process,
            ) from exc
        if process.get("stderr") != "":
            raise R7S2OuterError("outer_inner_stderr_not_empty", process_evidence=process)
        try:
            result = _strict_canonical_json_line(process.get("stdout"), "outer_inner_result")
        except R7S2OuterError as exc:
            raise R7S2OuterError(str(exc), process_evidence=process) from exc
        validated_inner = _validate_inner_result(
            process=process,
            result=result,
            contract=contract,
            expected_bootstrap_attestation=expected_inner_bootstrap_attestation,
        )
        if process["return_code"] == 3:
            terminal_validated_inner = _validate_inner_result(
                process=process,
                result=result,
                contract=contract,
                expected_bootstrap_attestation=expected_inner_bootstrap_attestation,
            )
            if terminal_validated_inner != validated_inner:
                raise R7S2OuterError("outer_inner_terminal_readback_mismatch")
            validated_inner = terminal_validated_inner
            return _publish_outer_failure(
                contract,
                outer_directory=outer_directory,
                failed_stage="inner_qualification_zero_credit",
                exception=R7S2OuterError(
                    "inner_qualification_zero_credit", process_evidence=process
                ),
                expected_contract_sha256=expected_contract_sha256,
                expected_launch_index_sha256=expected_launch_index_sha256,
                reservation=reservation,
                child_calls=child_calls,
                validated_inner_result=validated_inner,
            )
        _assert_no_reparse_chain(emergency_directory, allow_missing_leaf=True)
        if emergency_directory.exists():
            raise R7S2OuterError("outer_emergency_conflict_before_success")
        terminal_validated_inner = _validate_inner_result(
            process=process,
            result=result,
            contract=contract,
            expected_bootstrap_attestation=expected_inner_bootstrap_attestation,
        )
        if terminal_validated_inner != validated_inner:
            raise R7S2OuterError("outer_inner_terminal_readback_mismatch")
        validated_inner = terminal_validated_inner
        report = {
            "schema": SCHEMA,
            "qualification_id": contract.qualification_id,
            "run_uuid": contract.run_uuid,
            "attempt_id": contract.attempt_id,
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "reservation": reservation,
            "outer_bootstrap_attestation": bootstrap_attestation,
            "contract_sha256": expected_contract_sha256,
            "launch_index_sha256": expected_launch_index_sha256,
            "outer_sha256": expected_outer_sha256,
            "process_module_sha256": process_pin["sha256"],
            "qualification_process": process,
            "qualification_result": result,
            "validated_inner_result": validated_inner,
            "outer_timeout_contract": dict(OUTER_TIMEOUT_CONTRACT),
            "qualification_child_calls": 1,
            "automatic_retry_count": 0,
            "forced_termination_attempts": 0,
            "wsl_shutdown_calls": 0,
            "docker_kubernetes_service_mutations": 0,
            "r8_started": False,
        }
        report_pin = _atomic_exclusive_json(outer_directory / "outer-report.json", report)
        _assert_no_reparse_chain(emergency_directory, allow_missing_leaf=True)
        if emergency_directory.exists():
            raise R7S2OuterError("outer_emergency_conflict_before_success_index")
        final_validated_inner = _validate_inner_result(
            process=process,
            result=result,
            contract=contract,
            expected_bootstrap_attestation=expected_inner_bootstrap_attestation,
        )
        if final_validated_inner != validated_inner:
            raise R7S2OuterError("outer_inner_terminal_readback_mismatch")
        validated_inner = final_validated_inner
        index_pin = _atomic_exclusive_json(
            outer_directory / "outer-index.json",
            {
                "schema": SCHEMA,
                "status": "qualified_non_credit_returned",
                "credit": "non_credit_only",
                "reservation": reservation,
                "outer_bootstrap_attestation": bootstrap_attestation,
                "report": report_pin,
                "qualification_child_calls": 1,
                "automatic_retry_count": 0,
                "forced_termination_attempts": 0,
                "completion_marker_created": False,
                "r8_started": False,
            },
        )
        _assert_no_reparse_chain(emergency_directory, allow_missing_leaf=True)
        if emergency_directory.exists():
            raise R7S2OuterError("outer_emergency_conflict_after_success_index")
        return {"passed": True, "report": report_pin, "index": index_pin}
    except Exception as exc:
        return _publish_outer_failure(
            contract,
            outer_directory=outer_directory,
            failed_stage="outer_inner_qualification",
            exception=exc,
            expected_contract_sha256=expected_contract_sha256,
            expected_launch_index_sha256=expected_launch_index_sha256,
            reservation=reservation,
            child_calls=child_calls,
            validated_inner_result=None,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinned outer for the non-credit r7s2 WSL gate")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--expected-contract-sha256", required=True)
    parser.add_argument("--launch-index", type=Path, required=True)
    parser.add_argument("--expected-launch-index-sha256", required=True)
    parser.add_argument("--expected-outer-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-source-tree", required=True)
    parser.add_argument("--execute-non-credit-once", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute_non_credit_once:
        raise R7S2OuterError("explicit_execute_non_credit_once_required")
    result = execute_once(
        contract_path=args.contract,
        expected_contract_sha256=args.expected_contract_sha256,
        launch_index_path=args.launch_index,
        expected_launch_index_sha256=args.expected_launch_index_sha256,
        expected_outer_sha256=args.expected_outer_sha256,
        expected_source_commit=args.expected_source_commit,
        expected_source_tree=args.expected_source_tree,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

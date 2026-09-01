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
import configparser
import hashlib
import json
import os
import platform as host_platform
import re
import stat
import threading
import time
import types
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from ctypes import wintypes


QUALIFIER_INVOCATION_PATH = Path(os.path.abspath(__file__))
PROJECT_ROOT = QUALIFIER_INVOCATION_PATH.parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
GIT_ROOT = PROJECT_ROOT.parent
STAGER_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "stage_wsl_process_group_r7s2.py"
OUTER_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "invoke_wsl_process_group_r7s2.py"

CONTRACT_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.wsl-qualification-contract.v1"
RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.invocation-reservation.v1"
EVIDENCE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.wsl-qualification.v1"
FAILURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.failure-seal.v1"
INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.evidence-index.v1"
EMERGENCY_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.emergency-seal.v1"
LINUX_READBACK_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.linux-toolchain.v1"
FIXTURE_ACK_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.detached-descendant.v1"
STAGING_INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-index.v1"
STAGING_ATTESTATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-attestation.v1"
LAUNCH_INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.launch-index.v1"
STAGER_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.contract-stager.v1"
STAGING_RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-reservation.v1"
LINUX_DISCOVERY_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.linux-discovery.v1"
OUTER_RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-reservation.v1"
OUTER_RESERVATION_MAX_AGE_SECONDS = 120
INNER_BOOTSTRAP_SOURCE_SHA256 = "9bf7ed29937baaafbafcf36b04eff764d2d258b06e952b2b48107091aba91aca"

CANONICAL_EVIDENCE_ROOT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2-pre-r8-r7s2-gate"
    r"\x1-clock-phase-b2-pre-r8-r7s2-gate-20260901T131707Z-55f09ef"
)
RUN_ID_RE = re.compile(r"pre-r8-r7s2-wsl-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
EVIDENCE_LEAF_RE = re.compile(r"wsl-[0-9a-f]{8}")
STAGING_LEAF_RE = re.compile(r"c-[0-9a-f]{8}")
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")
WINDOWS_PATH_BUDGET = 240
GIT_CONFIG_POLICY = "dynamic-raw-pin-dangerous-execution-keys-rejected-v1"

PARENT_ROLES = {
    "attempt_1_failure",
    "attempt_2_failure",
    "pre_r8_r7s1_no_go_seal",
}
HOST_BINARY_ROLES = {"python", "system32_wsl", "store_wsl", "wslhost"}
LINUX_BINARY_ROLES = {"python3", "env", "setsid"}
SOURCE_ROLES = {"qualification_script", "process_module", "r7s1_runner", "outer_launcher"}
HOST_EXPECTATIONS = {
    "python": {
        "path": r"C:\Users\opop0\miniconda3\python.exe",
        "sha256": "ec0ea8d6907787b76dcf8524aaa93e52e167ceee62fa8778e182ea637a3dbc1d",
        "bytes": 104264,
        "version": "3.13.11",
    },
    "system32_wsl": {
        "path": r"C:\Windows\System32\wsl.exe",
        "sha256": "27cc8dd52be326e138a89f8889241b1d8c51dd1978b22eb70be77036ccdee3c2",
        "bytes": 274432,
        "version": "10.0.26100.8737",
    },
    "store_wsl": {
        "path": r"C:\Program Files\WSL\wsl.exe",
        "sha256": "9708903a1e0646d68a2007d62384030e831558b475484e83dff3f32e6e99623a",
        "bytes": 4291936,
        "version": "2.7.12.0",
    },
    "wslhost": {
        "path": r"C:\Program Files\WSL\wslhost.exe",
        "sha256": "ad41985218e1a872ad676e06ea00a7efdeada65fb1d22996482927a2213cfb84",
        "bytes": 3492664,
        "version": "2.7.12.0",
    },
}
GIT_EXPECTATION = {
    "path": r"C:\Program Files\Git\mingw64\bin\git.exe",
    "sha256": "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
    "bytes": 4422544,
    "version": "2.54.0.windows.1",
}
CANONICAL_PARENT_ROOT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2-pre-r8-gate"
    r"\x1-clock-phase-b2-pre-r8-gate-20260901T072422Z-b9140ad"
)
CANONICAL_PARENT_PINS = {
    "attempt_1_failure": {
        "path": str(
            CANONICAL_PARENT_ROOT / "wsl-process-qualification-attempt-1-failure-summary.json"
        ),
        "sha256": "30691a2574ca4d2637cffccd3cc4adcb2ab1092a1867053cf857786d2f15d328",
        "bytes": 1287,
    },
    "attempt_2_failure": {
        "path": str(CANONICAL_PARENT_ROOT / "wsl-process-qualification-attempt-2.json"),
        "sha256": "5d8b1ff37c865609285daf5b56e6863a5f1af28d561a93b84cefc71565f15b95",
        "bytes": 6522,
    },
    "pre_r8_r7s1_no_go_seal": {
        "path": str(CANONICAL_PARENT_ROOT / "pre-r8-r7s1-no-go-seal.json"),
        "sha256": "442cb086379fb4b1a36da4b9baddde237f1cc7eb21b9eb00477a642b53580f9b",
        "bytes": 8057,
    },
}
CONTRACT_TIMEOUTS = {
    "launch_wrapper_seconds": 12,
    "launch_residual_seconds": 8,
    "stream_drain_seconds": 5,
    "scan_wrapper_seconds": 5,
    "scan_residual_seconds": 3,
    "observer_deadline_seconds": 30,
    "observer_interval_seconds": 0.05,
    "observer_max_scans": 64,
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
FIXTURE_LIFETIME_SECONDS = 4
R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED = False
STAGER_LINUX_DISCOVERY_SOURCE_SHA256 = (
    "333262f10a18e0f156c627801d7ee77230e3f728216c4a4a568d4337e4c177f1"
)
OUTER_BOOTSTRAP_SOURCE_SHA256 = "f5043a9a0d6b27dafd4f1e7b123f1c147d6d93cc617bbc3696d26ed6a41d7a38"
STAGER_BOOTSTRAP_SOURCE_SHA256 = "6b44097e05535ca83961c09cfcaf38e5ccee24bfd0580156e998dc35bfb82928"
TRUSTED_PROCESS_RUNTIME = {
    "path": str(SRC_ROOT / "evm" / "scale_validation" / "phase_b2_r7_process.py"),
    "lf_normalized_sha256": "75aac2336d6f93bf5df6434871e1911d0922a3ff5e4f8dbea25712b0c65d8c74",
    "git_head_blob_oid": "6a65d8184afd4a4dbec0163f820ac7b8f03914be",
}
TRUSTED_STAGER_SOURCE = {
    "path": str(STAGER_SCRIPT),
    "lf_normalized_sha256": "39106798582fe4ff3ef6aa5f4543de17c689510414e23e0280dc64263fdb23f5",
    "git_head_blob_oid": "51703b56dc21ae553a6c45f2d174ead6c2a06f48",
}

TimeoutContract: Any = None
WindowsJobProcessRunner: Any = None
WslResidualProtocol: Any = None
_PROCESS_RUNTIME_SHA256: str | None = None

INVOCATION_POLICY = {
    "adversary_launches": 1,
    "automatic_retries": 0,
    "forced_termination_attempts": 0,
    "docker_lifecycle_calls": 0,
    "service_mutations": 0,
    "wsl_shutdown_calls": 0,
    "runtime_probe_calls": 0,
    "observer_calls_are_read_only_proc_scans": True,
    "windows_child_environment": "minimal_systemroot_windir_wsl_utf8_no_wslenv",
    "linux_child_environment": "env_i_c_utf8_plus_fixture_run_uuid_only",
    "completion_credit": "non_credit_only",
}

DETACHED_DESCENDANT_SOURCE = r"""import json,os,pathlib,sys,time
duration=float(sys.argv[1])
first=os.fork()
if first:
    os._exit(0)
os.setsid()
second=os.fork()
if second:
    os._exit(0)
raw=pathlib.Path('/proc/self/stat').read_text()
right=raw.rfind(')')
fields=raw[right+1:].strip().split()
ack={
    'schema':'evm.s8-v4.x1.phase-b2.pre-r8-r7s2.detached-descendant.v1',
    'run_uuid':os.environ['EVM_PHASE_B2_RUN_UUID'],
    'pid':os.getpid(),
    'ppid':int(fields[1]),
    'pgrp':int(fields[2]),
    'session':int(fields[3]),
    'start_time_ticks':int(fields[19]),
    'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'stdio_detach':'close_all_inherited_fds_after_ack',
}
print(json.dumps(ack,sort_keys=True,separators=(',',':')),flush=True)
for name in os.listdir('/proc/self/fd'):
    try:
        descriptor=int(name)
        os.close(descriptor)
    except (OSError,ValueError):
        pass
time.sleep(duration)
os._exit(0)
"""

QUALIFICATION_SCANNER_SOURCE = r"""import hashlib,json,pathlib,sys
run_uuid,expected_pgrp,expected_start,expected_boot=sys.argv[1:]
expected_pgrp_i=int(expected_pgrp) if expected_pgrp else None
expected_start_i=int(expected_start) if expected_start else None
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
needle=('EVM_PHASE_B2_RUN_UUID='+run_uuid).encode()
records=[]
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        raw=(proc/'stat').read_text()
        right=raw.rfind(')')
        left=raw.find('(')
        if left<=0 or right<=left:
            continue
        pid=int(raw[:left].strip())
        fields=raw[right+1:].strip().split()
        if len(fields)<20:
            continue
        ppid,pgrp,session,start=(int(fields[1]),int(fields[2]),int(fields[3]),int(fields[19]))
        group_match=bool(
            expected_boot and boot==expected_boot
            and expected_pgrp_i is not None and pgrp==expected_pgrp_i
            and expected_start_i is not None and start>=expected_start_i
        )
    except (FileNotFoundError,PermissionError,ProcessLookupError,ValueError):
        continue
    unreadable=[]
    try:
        environ=(proc/'environ').read_bytes().split(b'\0')
        uuid_match=needle in environ
    except (FileNotFoundError,PermissionError,ProcessLookupError):
        uuid_match=False
        unreadable.append('environ')
    if not uuid_match and not group_match:
        continue
    try:
        cmdline=(proc/'cmdline').read_bytes()
        cmdline_sha256=hashlib.sha256(cmdline).hexdigest()
    except (FileNotFoundError,PermissionError,ProcessLookupError):
        cmdline_sha256=None
        unreadable.append('cmdline')
    try:
        fds=sorted(int(item.name) for item in (proc/'fd').iterdir() if item.name.isdigit())
        open_fd_count=len(fds)
        stdio_fds_present=[descriptor for descriptor in fds if descriptor in (0,1,2)]
    except (FileNotFoundError,PermissionError,ProcessLookupError):
        open_fd_count=None
        stdio_fds_present=None
        unreadable.append('fd')
    records.append({
        'pid':pid,'ppid':ppid,'pgrp':pgrp,'session':session,
        'start_time_ticks':start,'boot_id':boot,
        'run_uuid_match':uuid_match,'process_group_match':group_match,
        'auxiliary_read_status':'unreadable_residual' if unreadable else 'complete',
        'unreadable_fields':sorted(set(unreadable)),
        'cmdline_sha256':cmdline_sha256,
        'open_fd_count':open_fd_count,
        'stdio_fds_present':stdio_fds_present,
    })
print(json.dumps(sorted(records,key=lambda row:(row['pid'],row['start_time_ticks'])),
                 sort_keys=True,separators=(',',':')))
"""

LINUX_TOOLCHAIN_SOURCE = r"""import hashlib,json,os,pathlib,platform,sys
def identity(path):
    real=os.path.realpath(path)
    raw=pathlib.Path(real).read_bytes()
    return {'path':path,'realpath':real,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
os_release=pathlib.Path('/etc/os-release').read_bytes()
machine_id=pathlib.Path('/etc/machine-id').read_bytes()
values={}
for raw_line in os_release.decode('utf-8').splitlines():
    if '=' in raw_line and not raw_line.startswith('#'):
        key,value=raw_line.split('=',1)
        values[key]=value.strip().strip('"')
payload={
    'schema':'evm.s8-v4.x1.phase-b2.pre-r8-r7s2.linux-toolchain.v1',
    'status':'observed',
    'distro':sys.argv[1],
    'kernel_release':platform.release(),
    'python_version':sys.version.split()[0],
    'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'distro_version':values.get('PRETTY_NAME',''),
    'rootfs_identity':hashlib.sha256(os_release+b'\0'+machine_id).hexdigest(),
    'os_release_sha256':hashlib.sha256(os_release).hexdigest(),
    'machine_id_sha256':hashlib.sha256(machine_id).hexdigest(),
    'binaries':{
        'python3':identity(sys.argv[2]),
        'env':identity(sys.argv[3]),
        'setsid':identity(sys.argv[4]),
    },
}
print(json.dumps(payload,sort_keys=True,separators=(',',':')))
"""


class R7S2QualificationError(RuntimeError):
    """Fail-closed qualification error."""


class R7S2SourceIdentityError(R7S2QualificationError):
    def __init__(self, message: str, evidence: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.evidence = dict(evidence)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.evidence)


@dataclass(frozen=True)
class BinaryPin:
    path: str
    sha256: str
    bytes: int
    version: str


@dataclass(frozen=True)
class QualificationContract:
    raw: dict[str, Any]
    qualification_id: str
    run_uuid: str
    attempt_id: str
    evidence_root: Path
    run_directory: Path
    emergency_directory: Path
    distribution: str
    host_binaries: dict[str, BinaryPin]
    linux_binaries: dict[str, BinaryPin]
    platform_identity: dict[str, str]
    source_identity: dict[str, Any]
    staging_attestation: dict[str, Any]
    launch_authorization: dict[str, Any]
    timeouts: dict[str, float | int]
    contract_path: Path
    contract_sha256: str
    launch_index_path: Path | None
    launch_index_sha256: str | None
    outer_reservation_path: Path | None
    outer_reservation_sha256: str | None
    outer_parent_pid: int | None
    execution_authorized: bool


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _lf_normalized_source(raw: bytes) -> bytes:
    without_crlf = raw.replace(b"\r\n", b"")
    if b"\r" in without_crlf or (b"\r\n" in raw and b"\n" in without_crlf):
        raise R7S2QualificationError("mixed_or_bare_cr_source_line_endings")
    normalized = raw.replace(b"\r\n", b"\n")
    try:
        normalized.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R7S2QualificationError("source_utf8_required") from exc
    return normalized


def _git_blob_oid(raw: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw, usedforsecurity=False
    ).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _strict_json_bytes(
    raw: bytes,
    label: str,
    *,
    canonical_owned: bool = False,
    terminal_newline: bool = False,
) -> Any:
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
        decoded = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise R7S2QualificationError(f"{label}_json_invalid") from exc
    if canonical_owned:
        if not isinstance(decoded, Mapping):
            canonical = json.dumps(
                decoded,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        else:
            canonical = _canonical_json(dict(decoded))
        if terminal_newline:
            canonical += b"\n"
        if raw != canonical:
            raise R7S2QualificationError(f"{label}_json_not_canonical")
    return decoded


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S2QualificationError(f"{label}_mapping_required")
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7S2QualificationError(f"{label}_fields_mismatch")


def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise R7S2QualificationError(f"{label}_invalid") from exc


def _hex64(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if HEX64.fullmatch(normalized) is None:
        raise R7S2QualificationError(f"{label}_invalid")
    return normalized


def _hex40(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if HEX40.fullmatch(normalized) is None:
        raise R7S2QualificationError(f"{label}_invalid")
    return normalized


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R7S2QualificationError(f"{label}_invalid")
    return value


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise R7S2QualificationError(f"{label}_invalid")
    return float(value)


def _path_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _process_creation_filetime(pid: int) -> int:
    process_id = _positive_int(pid, "process_creation_pid")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        raise R7S2QualificationError("process_creation_open_failed")
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            raise R7S2QualificationError("process_creation_read_failed")
    finally:
        kernel32.CloseHandle(handle)
    return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)


def _assert_path_budget(path: Path) -> None:
    if len(os.path.abspath(path)) > WINDOWS_PATH_BUDGET:
        raise R7S2QualificationError(f"windows_path_budget_exceeded:{path}")


def _assert_no_reparse_chain(path: Path, *, allow_missing_leaf: bool = False) -> None:
    absolute = Path(os.path.abspath(path))
    chain: list[Path] = []
    cursor = absolute
    while True:
        chain.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for index, item in enumerate(reversed(chain)):
        try:
            metadata = os.lstat(item)
        except FileNotFoundError:
            is_leaf = index == len(chain) - 1
            if is_leaf and allow_missing_leaf:
                continue
            raise R7S2QualificationError(f"path_component_missing:{item}") from None
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if stat.S_ISLNK(metadata.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise R7S2QualificationError(f"reparse_component_forbidden:{item}")


def _binary_pin(value: Any, label: str) -> BinaryPin:
    pin = _mapping(value, label)
    _exact_keys(pin, {"path", "sha256", "bytes", "version"}, label)
    path = str(pin["path"])
    if not path or (label.startswith("host_") and not Path(path).is_absolute()):
        raise R7S2QualificationError(f"{label}_path_invalid")
    version = str(pin["version"])
    if not version:
        raise R7S2QualificationError(f"{label}_version_invalid")
    return BinaryPin(
        path=path,
        sha256=_hex64(pin["sha256"], f"{label}_sha256"),
        bytes=_positive_int(pin["bytes"], f"{label}_bytes"),
        version=version,
    )


def _verify_file_pin(pin: BinaryPin, label: str) -> None:
    path = Path(pin.path)
    _assert_no_reparse_chain(path)
    if not path.is_file():
        raise R7S2QualificationError(f"{label}_missing")
    if path.stat().st_size != pin.bytes or _sha256_file(path) != pin.sha256:
        raise R7S2QualificationError(f"{label}_identity_mismatch")


def _bind_verified_process_runtime(pin: Mapping[str, Any]) -> None:
    global TimeoutContract
    global WindowsJobProcessRunner
    global WslResidualProtocol
    global _PROCESS_RUNTIME_SHA256

    expected_sha256 = _hex64(pin["sha256"], "process_runtime_sha256")
    if _PROCESS_RUNTIME_SHA256 is not None:
        if _PROCESS_RUNTIME_SHA256 != expected_sha256:
            raise R7S2QualificationError("process_runtime_rebind_forbidden")
        return
    path = Path(str(pin["path"]))
    if not _path_equal(path, Path(TRUSTED_PROCESS_RUNTIME["path"])):
        raise R7S2QualificationError("process_runtime_trusted_path_mismatch")
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    if len(raw) != pin["bytes"] or _sha256_bytes(raw) != expected_sha256:
        raise R7S2QualificationError("process_runtime_identity_mismatch_at_load")
    normalized = _lf_normalized_source(raw)
    if (
        _sha256_bytes(normalized) != TRUSTED_PROCESS_RUNTIME["lf_normalized_sha256"]
        or _git_blob_oid(normalized) != TRUSTED_PROCESS_RUNTIME["git_head_blob_oid"]
        or pin.get("git_head_blob_oid") != TRUSTED_PROCESS_RUNTIME["git_head_blob_oid"]
        or pin.get("git_normalized_worktree_blob_oid")
        != TRUSTED_PROCESS_RUNTIME["git_head_blob_oid"]
    ):
        raise R7S2QualificationError("process_runtime_trusted_content_mismatch")
    module_name = f"_evm_pre_r8_r7s2_process_{expected_sha256[:16]}"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(normalized, str(path), "exec"), module.__dict__)
        timeout_contract = module.__dict__["TimeoutContract"]
        windows_runner = module.__dict__["WindowsJobProcessRunner"]
        residual_protocol = module.__dict__["WslResidualProtocol"]
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    TimeoutContract = timeout_contract
    WindowsJobProcessRunner = windows_runner
    WslResidualProtocol = residual_protocol
    _PROCESS_RUNTIME_SHA256 = expected_sha256


def _verify_trusted_stager_source(pin: Mapping[str, Any]) -> None:
    path = Path(str(pin.get("path", "")))
    if not _path_equal(path, Path(TRUSTED_STAGER_SOURCE["path"])):
        raise R7S2QualificationError("stager_trusted_path_mismatch")
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    normalized = _lf_normalized_source(raw)
    if (
        pin.get("bytes") != len(raw)
        or pin.get("sha256") != _sha256_bytes(raw)
        or pin.get("worktree_blob_oid") != _git_blob_oid(raw)
        or pin.get("git_head_blob_oid") != TRUSTED_STAGER_SOURCE["git_head_blob_oid"]
        or pin.get("git_normalized_worktree_blob_oid") != TRUSTED_STAGER_SOURCE["git_head_blob_oid"]
        or _sha256_bytes(normalized) != TRUSTED_STAGER_SOURCE["lf_normalized_sha256"]
        or _git_blob_oid(normalized) != TRUSTED_STAGER_SOURCE["git_head_blob_oid"]
    ):
        raise R7S2QualificationError("stager_trusted_content_mismatch")


def _read_pinned_json(
    value: Any, label: str, *, canonical_owned: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    pin = _mapping(value, label)
    _exact_keys(pin, {"path", "sha256", "bytes"}, label)
    normalized = {
        "path": str(pin["path"]),
        "sha256": _hex64(pin["sha256"], f"{label}_sha256"),
        "bytes": _positive_int(pin["bytes"], f"{label}_bytes"),
    }
    path = Path(normalized["path"])
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    if len(raw) != normalized["bytes"] or _sha256_bytes(raw) != normalized["sha256"]:
        raise R7S2QualificationError(f"{label}_identity_mismatch")
    payload = _mapping(
        _strict_json_bytes(
            raw,
            f"{label}_payload",
            canonical_owned=canonical_owned,
            terminal_newline=canonical_owned,
        ),
        f"{label}_payload",
    )
    return normalized, payload


def _validate_canonical_parent(role: str, value: Any) -> dict[str, Any]:
    pin, payload = _read_pinned_json(value, f"parent_{role}")
    expected = CANONICAL_PARENT_PINS[role]
    if (
        not _path_equal(Path(pin["path"]), Path(str(expected["path"])))
        or pin["sha256"] != expected["sha256"]
        or pin["bytes"] != expected["bytes"]
    ):
        raise R7S2QualificationError(f"parent_{role}_canonical_pin_mismatch")
    if role == "attempt_1_failure":
        accepted = (
            payload.get("schema")
            == "evm.s8-v4.x1.phase-b2.pre-r8.wsl-process-qualification-failure-summary.v1"
            and payload.get("passed") is False
            and payload.get("classification")
            == "adversarial_fixture_did_not_create_post_launcher_linux_residual"
            and payload.get("forced_termination_attempts") == 0
            and payload.get("automatic_retry_count") == 0
            and payload.get("next_runtime_probe_started") is False
        )
    elif role == "attempt_2_failure":
        accepted = (
            payload.get("schema") == "evm.s8-v4.x1.phase-b2.pre-r8.wsl-process-qualification.v2"
            and payload.get("passed") is False
            and payload.get("manual_intervention_required") is True
            and payload.get("maximum_residual_count") == 0
            and payload.get("next_runtime_probe_allowed") is False
            and payload.get("forced_termination_attempts") == 0
            and payload.get("automatic_retry_count") == 0
        )
    else:
        publication = payload.get("publication")
        accepted = (
            payload.get("schema") == "evm.s8-v4.x1.phase-b2.pre-r8.r7s1-no-go-seal.v1"
            and payload.get("status") == "manual_intervention_required"
            and payload.get("decision") == "no_go"
            and payload.get("credit") == "zero_credit"
            and payload.get("acceptance_credit") is False
            and isinstance(publication, Mapping)
            and publication.get("r8_started") is False
        )
    if not accepted:
        raise R7S2QualificationError(f"parent_{role}_content_invariants_failed")
    return pin


def _git_blob_oid(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


def _validate_source_pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    _exact_keys(
        pin,
        {
            "path",
            "sha256",
            "bytes",
            "relative_path",
            "worktree_blob_oid",
            "git_mode",
            "git_head_blob_oid",
            "git_normalized_worktree_blob_oid",
        },
        label,
    )
    path = Path(str(pin["path"]))
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    normalized = {
        "path": str(path.resolve()),
        "sha256": _hex64(pin["sha256"], f"{label}_sha256"),
        "bytes": _positive_int(pin["bytes"], f"{label}_bytes"),
        "relative_path": str(pin["relative_path"]),
        "worktree_blob_oid": _hex40(pin["worktree_blob_oid"], f"{label}_worktree_blob_oid"),
        "git_mode": str(pin["git_mode"]),
        "git_head_blob_oid": _hex40(pin["git_head_blob_oid"], f"{label}_head_blob_oid"),
        "git_normalized_worktree_blob_oid": _hex40(
            pin["git_normalized_worktree_blob_oid"], f"{label}_normalized_worktree_blob_oid"
        ),
    }
    try:
        expected_relative = path.resolve().relative_to(GIT_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise R7S2QualificationError(f"{label}_outside_git_root") from exc
    if (
        normalized["relative_path"] != expected_relative
        or normalized["git_mode"] not in {"100644", "100755"}
        or len(raw) != normalized["bytes"]
        or _sha256_bytes(raw) != normalized["sha256"]
        or _git_blob_oid(raw) != normalized["worktree_blob_oid"]
        or normalized["git_head_blob_oid"] != normalized["git_normalized_worktree_blob_oid"]
    ):
        raise R7S2QualificationError(f"{label}_identity_mismatch")
    return normalized


def _git_config_semantic_readback() -> dict[str, Any]:
    path = GIT_ROOT / ".git" / "config"
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(raw.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise R7S2QualificationError("git_config_parse_failed") from exc
    key_names = {
        f"{section.casefold()}.{key.casefold()}"
        for section in parser.sections()
        for key, _value in parser.items(section, raw=True)
    }
    forbidden_exact = {
        "core.worktree",
        "core.fsmonitor",
        "core.hookspath",
        "core.attributesfile",
        "core.sshcommand",
        "core.gitproxy",
        "core.askpass",
        "core.pager",
        "core.editor",
        "protocol.ext.allow",
    }
    forbidden = {
        key
        for key in key_names
        if key in forbidden_exact
        or key.startswith(("include.", "includeif ", "filter ", "alias.", "credential.", "url "))
        or (key.startswith("diff ") and key.endswith((".command", ".textconv")))
        or (key.startswith("merge ") and key.endswith(".driver"))
        or (key.startswith("remote ") and key.endswith((".proxy", ".uploadpack", ".receivepack")))
    }
    if forbidden:
        raise R7S2QualificationError("git_config_dangerous_key_forbidden")
    config_worktree = GIT_ROOT / ".git" / "config.worktree"
    _assert_no_reparse_chain(config_worktree, allow_missing_leaf=True)
    if config_worktree.exists():
        raise R7S2QualificationError("git_config_worktree_must_be_absent")
    if not parser.has_option('remote "origin"', "url"):
        raise R7S2QualificationError("git_config_origin_url_missing")
    remote_url = parser.get('remote "origin"', "url", raw=True)
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "key_names": sorted(key_names),
        "remote_url_sha256": _sha256_bytes(remote_url.encode("utf-8")),
        "config_worktree_absent": True,
        "policy": GIT_CONFIG_POLICY,
    }


def _validate_source_identity(value: Any) -> dict[str, Any]:
    identity = _mapping(value, "source_identity")
    _exact_keys(identity, {"commit", "tree", "stager", "git", "git_config"}, "source_identity")
    commit = _hex40(identity["commit"], "source_identity_commit")
    tree = _hex40(identity["tree"], "source_identity_tree")
    stager = _validate_source_pin(identity["stager"], "source_identity_stager")
    if not _path_equal(Path(stager["path"]), STAGER_SCRIPT):
        raise R7S2QualificationError("source_identity_stager_path_mismatch")
    git = _binary_pin(identity["git"], "source_identity_git")
    _verify_file_pin(git, "source_identity_git")
    if {
        "path": os.path.normcase(os.path.abspath(git.path)),
        "sha256": git.sha256,
        "bytes": git.bytes,
        "version": git.version,
    } != {
        "path": os.path.normcase(os.path.abspath(str(GIT_EXPECTATION["path"]))),
        "sha256": GIT_EXPECTATION["sha256"],
        "bytes": GIT_EXPECTATION["bytes"],
        "version": GIT_EXPECTATION["version"],
    }:
        raise R7S2QualificationError("source_identity_git_canonical_identity_mismatch")
    config = _mapping(identity["git_config"], "source_identity_git_config")
    _exact_keys(
        config,
        {
            "path",
            "sha256",
            "bytes",
            "key_names",
            "remote_url_sha256",
            "config_worktree_absent",
            "policy",
        },
        "source_identity_git_config",
    )
    normalized_config = {
        "path": str(config["path"]),
        "sha256": _hex64(config["sha256"], "source_identity_git_config_sha256"),
        "bytes": _positive_int(config["bytes"], "source_identity_git_config_bytes"),
        "key_names": list(config["key_names"]),
        "remote_url_sha256": _hex64(
            config["remote_url_sha256"], "source_identity_git_config_remote_url_sha256"
        ),
        "config_worktree_absent": config["config_worktree_absent"] is True,
        "policy": str(config["policy"]),
    }
    if (
        normalized_config["key_names"] != sorted(set(normalized_config["key_names"]))
        or any(not isinstance(item, str) for item in normalized_config["key_names"])
        or normalized_config != _git_config_semantic_readback()
    ):
        raise R7S2QualificationError("source_identity_git_config_mismatch")
    return {
        "commit": commit,
        "tree": tree,
        "stager": stager,
        "git": {
            "path": git.path,
            "sha256": git.sha256,
            "bytes": git.bytes,
            "version": git.version,
        },
        "git_config": normalized_config,
    }


def _utc_datetime(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise R7S2QualificationError(f"{label}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise R7S2QualificationError(f"{label}_must_be_utc")
    return parsed


def _strict_process_streams(
    outcome: Mapping[str, Any], label: str, *, stdout_allows_nul: bool = False
) -> tuple[str, str]:
    stdout = outcome.get("stdout")
    stderr = outcome.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise R7S2QualificationError(f"{label}_stream_type_invalid")
    if stderr != "":
        raise R7S2QualificationError(f"{label}_stderr_not_empty")
    for stream_name, stream in (("stdout", stdout), ("stderr", stderr)):
        if "\ufffd" in stream or any(0xD800 <= ord(character) <= 0xDFFF for character in stream):
            raise R7S2QualificationError(f"{label}_{stream_name}_noninjective_decode")
        for character in stream:
            codepoint = ord(character)
            if codepoint == 0 and stdout_allows_nul and stream_name == "stdout":
                continue
            if codepoint < 0x20 and character not in {"\n", "\r", "\t"}:
                raise R7S2QualificationError(f"{label}_{stream_name}_control_character")
            if codepoint == 0x7F:
                raise R7S2QualificationError(f"{label}_{stream_name}_control_character")
    return stdout, stderr


def _strict_wsl_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise R7S2QualificationError(f"{label}_type_invalid")
    if "\x00" in value or "\ufffd" in value:
        raise R7S2QualificationError(f"{label}_noninjective_decode")
    if "\r" in value.replace("\r\n", ""):
        raise R7S2QualificationError(f"{label}_bare_carriage_return")
    for character in value:
        codepoint = ord(character)
        if (
            0xD800 <= codepoint <= 0xDFFF
            or (codepoint < 0x20 and character not in {"\n", "\r", "\t"})
            or codepoint == 0x7F
        ):
            raise R7S2QualificationError(f"{label}_control_character")
    normalized = value.replace("\r\n", "\n")
    if not normalized.endswith("\n") or "\n\n" in normalized:
        raise R7S2QualificationError(f"{label}_record_termination_invalid")
    return normalized


def _require_ubuntu_verbose_running(value: Any, label: str) -> None:
    lines = _strict_wsl_text(value, label)[:-1].split("\n")
    if sum(line.strip() == "NAME STATE VERSION" for line in lines) != 1:
        raise R7S2QualificationError(f"{label}_header_mismatch")
    ubuntu_rows: list[tuple[str, int]] = []
    for line in lines:
        if "Ubuntu" not in line:
            continue
        match = re.fullmatch(
            r"[ \t]*\*?[ \t]*Ubuntu[ \t]+(Running|Stopped)[ \t]+([12])[ \t]*", line
        )
        if match is None:
            raise R7S2QualificationError(f"{label}_ubuntu_record_invalid")
        ubuntu_rows.append((match.group(1), int(match.group(2))))
    if ubuntu_rows != [("Running", 2)]:
        raise R7S2QualificationError(f"{label}_ubuntu_not_running_version_2")


def _require_ubuntu_running_membership(value: Any, label: str) -> None:
    lines = _strict_wsl_text(value, label)[:-1].split("\n")
    names: list[str] = []
    for line in lines:
        name = line.strip()
        if not name or name != line or "\t" in name:
            raise R7S2QualificationError(f"{label}_distribution_record_invalid")
        names.append(name)
    if names.count("Ubuntu") != 1:
        raise R7S2QualificationError(f"{label}_ubuntu_membership_mismatch")


def _canonical_json_line(value: Any, label: str) -> Mapping[str, Any]:
    text = _strict_wsl_text(value, label)

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate_key:{key}")
            result[key] = item
        return result

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non_finite_number:{constant}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise R7S2QualificationError(f"{label}_json_invalid") from exc
    mapping = _mapping(decoded, label)
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":")) + "\n"
    if text != canonical:
        raise R7S2QualificationError(f"{label}_json_not_canonical")
    return mapping


def _validate_success_job_timeline(
    outcome: Mapping[str, Any], *, label: str, run_uuid: str, expected_image: str
) -> None:
    events = outcome.get("events")
    accounting = outcome.get("accounting")
    identities = outcome.get("identities")
    if (
        not isinstance(events, list)
        or not events
        or not isinstance(accounting, list)
        or len(accounting) < 2
        or not isinstance(identities, list)
        or not identities
    ):
        raise R7S2QualificationError(f"{label}_job_timeline_missing")
    event_keys = {"sequence", "event", "monotonic_ns", "timestamp_utc", "pid", "details"}
    accounting_keys = {
        "sequence",
        "monotonic_ns",
        "timestamp_utc",
        "total_processes",
        "active_processes",
        "total_terminated_processes",
        "active_pids",
    }
    identity_keys = {
        "pid",
        "ppid",
        "creation_time_ns",
        "creation_time_utc",
        "image",
        "run_uuid",
        "observed_sequence",
    }
    if any(not isinstance(item, Mapping) or set(item) != event_keys for item in events):
        raise R7S2QualificationError(f"{label}_event_schema_mismatch")
    if any(not isinstance(item, Mapping) or set(item) != accounting_keys for item in accounting):
        raise R7S2QualificationError(f"{label}_accounting_schema_mismatch")
    if any(not isinstance(item, Mapping) or set(item) != identity_keys for item in identities):
        raise R7S2QualificationError(f"{label}_identity_schema_mismatch")

    def utc_value(value: Any) -> datetime:
        if not isinstance(value, str):
            raise R7S2QualificationError(f"{label}_timestamp_type_mismatch")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise R7S2QualificationError(f"{label}_timestamp_invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise R7S2QualificationError(f"{label}_timestamp_not_utc")
        return parsed

    started = utc_value(outcome.get("started_at_utc"))
    ended = utc_value(outcome.get("ended_at_utc"))
    if started > ended:
        raise R7S2QualificationError(f"{label}_timestamp_envelope_invalid")
    timeline = sorted([*events, *accounting], key=lambda item: item["sequence"])
    if (
        [item["sequence"] for item in events] != sorted(item["sequence"] for item in events)
        or [item["sequence"] for item in accounting]
        != sorted(item["sequence"] for item in accounting)
        or [item["sequence"] for item in timeline] != list(range(1, len(timeline) + 1))
        or any(
            type(item["monotonic_ns"]) is not int or item["monotonic_ns"] <= 0 for item in timeline
        )
        or any(
            left["monotonic_ns"] > right["monotonic_ns"]
            for left, right in zip(timeline, timeline[1:], strict=False)
        )
        or any(not (started <= utc_value(item["timestamp_utc"]) <= ended) for item in timeline)
    ):
        raise R7S2QualificationError(f"{label}_timeline_sequence_mismatch")
    for event in events:
        if (
            type(event["sequence"]) is not int
            or not isinstance(event["event"], str)
            or not isinstance(event["timestamp_utc"], str)
            or not isinstance(event["details"], Mapping)
            or (event["pid"] is not None and (type(event["pid"]) is not int or event["pid"] <= 0))
        ):
            raise R7S2QualificationError(f"{label}_event_type_mismatch")
    by_name = {item["event"]: item for item in events}
    unique_required = [
        "job_created",
        "root_created_suspended",
        "job_membership_verified",
        "root_resumed",
        "active_process_count_zero",
        "streams_drained",
    ]
    if any(sum(item["event"] == name for item in events) != 1 for name in unique_required):
        raise R7S2QualificationError(f"{label}_lifecycle_event_mismatch")
    root_pid = by_name["root_created_suspended"]["pid"]
    root_identity_events = [
        item for item in events if item["event"] == "identity_observed" and item["pid"] == root_pid
    ]
    if len(root_identity_events) != 1:
        raise R7S2QualificationError(f"{label}_root_identity_event_mismatch")
    ordered = [
        by_name["job_created"]["sequence"],
        by_name["root_created_suspended"]["sequence"],
        by_name["job_membership_verified"]["sequence"],
        root_identity_events[0]["sequence"],
        by_name["root_resumed"]["sequence"],
        by_name["active_process_count_zero"]["sequence"],
        by_name["streams_drained"]["sequence"],
    ]
    if ordered != sorted(ordered):
        raise R7S2QualificationError(f"{label}_lifecycle_order_mismatch")
    if (
        type(root_pid) is not int
        or root_pid <= 0
        or by_name["job_created"]["details"] != {"run_uuid": run_uuid}
        or by_name["job_membership_verified"]["pid"] != root_pid
        or by_name["job_membership_verified"]["details"]
        != {"active_processes": 1, "job_limit_flags": 0}
        or by_name["root_resumed"]["pid"] != root_pid
    ):
        raise R7S2QualificationError(f"{label}_root_lifecycle_mismatch")
    forbidden = {
        "job_abnormal_exit_process",
        "job_accounting_error",
        "timeout_latched",
        "cancel_latched",
        "residual_repoll_exhausted",
        "residual_processes_observed",
        "stream_drain_incomplete",
        "identity_coverage_incomplete",
    }
    if any(
        item["event"] in forbidden or item["event"].startswith("job_message_") for item in events
    ):
        raise R7S2QualificationError(f"{label}_forbidden_event")
    allowed_events = {
        *unique_required,
        "identity_observed",
        "job_new_process",
        "job_exit_process",
        "job_active_process_zero",
    }
    if any(item["event"] not in allowed_events for item in events):
        raise R7S2QualificationError(f"{label}_unknown_event")
    identity_events = {
        (item["sequence"], item["pid"]) for item in events if item["event"] == "identity_observed"
    }
    stable_keys: set[tuple[int, int]] = set()
    for identity in identities:
        key = (identity["pid"], identity["creation_time_ns"])
        if (
            type(key[0]) is not int
            or key[0] <= 0
            or type(key[1]) is not int
            or key[1] <= 0
            or key in stable_keys
            or identity["run_uuid"] != run_uuid
            or type(identity["ppid"]) is not int
            or identity["ppid"] <= 0
            or not isinstance(identity["image"], str)
            or not identity["image"]
            or type(identity["observed_sequence"]) is not int
            or not (started <= utc_value(identity["creation_time_utc"]) <= ended)
            or (identity["observed_sequence"], identity["pid"]) not in identity_events
        ):
            raise R7S2QualificationError(f"{label}_identity_binding_mismatch")
        stable_keys.add(key)
    if len({identity["pid"] for identity in identities}) != len(identities):
        raise R7S2QualificationError(f"{label}_pid_reuse_ambiguous")
    observed_sequence_by_pid = {
        identity["pid"]: identity["observed_sequence"] for identity in identities
    }
    root_identities = [item for item in identities if item["pid"] == root_pid]
    if len(root_identities) != 1 or os.path.normcase(
        root_identities[0]["image"]
    ) != os.path.normcase(expected_image):
        raise R7S2QualificationError(f"{label}_root_image_mismatch")
    prior_total = -1
    prior_terminated = -1
    for snapshot in accounting:
        if (
            type(snapshot["sequence"]) is not int
            or type(snapshot["monotonic_ns"]) is not int
            or any(
                type(snapshot[key]) is not int
                for key in ("total_processes", "active_processes", "total_terminated_processes")
            )
            or not isinstance(snapshot["active_pids"], list)
            or any(type(pid) is not int or pid <= 0 for pid in snapshot["active_pids"])
            or len(snapshot["active_pids"]) != len(set(snapshot["active_pids"]))
            or min(
                snapshot["total_processes"],
                snapshot["active_processes"],
                snapshot["total_terminated_processes"],
            )
            < 0
            or snapshot["active_processes"] > snapshot["total_processes"]
            or any(
                pid not in observed_sequence_by_pid
                or observed_sequence_by_pid[pid] >= snapshot["sequence"]
                for pid in snapshot["active_pids"]
            )
            or snapshot["total_processes"] < prior_total
            or snapshot["total_terminated_processes"] < prior_terminated
        ):
            raise R7S2QualificationError(f"{label}_accounting_counter_mismatch")
        prior_total = snapshot["total_processes"]
        prior_terminated = snapshot["total_terminated_processes"]
    final = accounting[-1]
    if (
        final["active_processes"] != 0
        or final["active_pids"] != []
        or final["total_processes"] != len(stable_keys)
        or final["total_terminated_processes"] != 0
        or final["sequence"] >= by_name["streams_drained"]["sequence"]
        or by_name["streams_drained"]["sequence"] != len(timeline)
    ):
        raise R7S2QualificationError(f"{label}_final_accounting_mismatch")


def _validate_stager_process_evidence(
    value: Any,
    *,
    run_uuid: str,
    source_commit: str,
    source_tree: str,
    source_pins: Mapping[str, Any],
    stager_pin: Mapping[str, Any],
    host_binaries: Mapping[str, BinaryPin],
    linux_identity: Mapping[str, Any],
) -> None:
    processes = _mapping(value, "staging_index_process_evidence")
    all_sources = {**source_pins, "stager": stager_pin}
    source_roles = sorted(all_sources)
    expected_stages = {
        "git_identity",
        "git_status",
        "git_source_ls_tree",
        "git_source_ls_files",
        *(f"git_source_hash_object_{role}" for role in source_roles),
        "ubuntu_verbose_pre",
        "ubuntu_running_pre",
        "linux_identity_readback",
        "ubuntu_verbose_post",
        "ubuntu_running_post",
    }
    _exact_keys(processes, expected_stages, "staging_index_process_evidence")
    git = host_binaries.get("git")
    git_path = GIT_EXPECTATION["path"] if git is None else git.path
    wsl_path = host_binaries["system32_wsl"].path
    relative_paths = [str(all_sources[role]["relative_path"]) for role in source_roles]
    git_common = [git_path, "-c", "core.fsmonitor=false", "-c", "core.autocrlf=true"]
    expected_commands: dict[str, list[str]] = {
        "git_identity": [
            git_path,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(GIT_ROOT),
            "rev-parse",
            "HEAD",
            "HEAD^{tree}",
        ],
        "git_status": [
            git_path,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(GIT_ROOT),
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        ],
        "git_source_ls_tree": [
            *git_common,
            "-C",
            str(GIT_ROOT),
            "ls-tree",
            "-rz",
            "--full-tree",
            source_commit,
            "--",
            *relative_paths,
        ],
        "git_source_ls_files": [
            *git_common,
            "-C",
            str(GIT_ROOT),
            "ls-files",
            "-vz",
            "--stage",
            "--",
            *relative_paths,
        ],
        "ubuntu_verbose_pre": [wsl_path, "--list", "--verbose"],
        "ubuntu_running_pre": [wsl_path, "--list", "--running", "--quiet"],
        "ubuntu_verbose_post": [wsl_path, "--list", "--verbose"],
        "ubuntu_running_post": [wsl_path, "--list", "--running", "--quiet"],
    }
    for role in source_roles:
        expected_commands[f"git_source_hash_object_{role}"] = [
            *git_common,
            "-C",
            str(GIT_ROOT),
            "hash-object",
            f"--path={all_sources[role]['relative_path']}",
            str(all_sources[role]["path"]),
        ]
    git_environment_keys = sorted(
        {
            "SystemRoot",
            "WINDIR",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_TERMINAL_PROMPT",
            "GCM_INTERACTIVE",
            "GIT_OPTIONAL_LOCKS",
            "GIT_NO_REPLACE_OBJECTS",
            "GIT_ATTR_NOSYSTEM",
            "LC_ALL",
        }
    )
    wsl_environment_keys = ["SystemRoot", "WINDIR", "WSL_UTF8"]
    for stage, raw_outcome in processes.items():
        outcome = _mapping(raw_outcome, f"staging_process_{stage}")
        _exact_keys(
            outcome,
            {
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
                "invocation",
            },
            f"staging_process_{stage}",
        )
        if not (
            outcome.get("safe_for_followup") is True
            and outcome.get("forced_termination_attempts") == 0
            and outcome.get("active_process_zero") is True
            and outcome.get("final_active_process_count") == 0
            and outcome.get("job_limit_flags") == 0
            and outcome.get("cancelled") is False
            and outcome.get("manual_intervention_required") is False
            and outcome.get("streams_drained") is True
            and outcome.get("stdout_drained") is True
            and outcome.get("stderr_drained") is True
            and outcome.get("identity_coverage_complete") is True
            and outcome.get("timed_out") is False
            and outcome.get("return_code") == 0
            and outcome.get("residual_pids") == []
            and outcome.get("errors") == []
        ):
            raise R7S2QualificationError(f"staging_process_unsafe:{stage}")
        invocation = _mapping(outcome.get("invocation"), f"staging_process_{stage}_invocation")
        _exact_keys(
            invocation,
            {"name", "command", "argv_sha256", "cwd", "environment_keys", "run_uuid"},
            f"staging_process_{stage}_invocation",
        )
        command = invocation["command"]
        if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
            raise R7S2QualificationError(f"staging_process_{stage}_command_invalid")
        if (
            invocation["run_uuid"] != run_uuid
            or invocation["argv_sha256"]
            != _sha256_bytes(json.dumps(command, separators=(",", ":")).encode("utf-8"))
            or not isinstance(invocation["cwd"], str)
            or not Path(invocation["cwd"]).is_absolute()
        ):
            raise R7S2QualificationError(f"staging_process_{stage}_invocation_mismatch")
        if (
            outcome["name"] != invocation["name"]
            or outcome["run_uuid"] != invocation["run_uuid"]
            or outcome["command"] != invocation["command"]
        ):
            raise R7S2QualificationError(f"staging_process_{stage}_outcome_invocation_mismatch")
        _validate_success_job_timeline(
            outcome,
            label=f"staging_process_{stage}",
            run_uuid=run_uuid,
            expected_image=command[0],
        )
        identities = outcome["identities"]
        accounting = outcome["accounting"]
        events = outcome["events"]
        if (
            not isinstance(identities, list)
            or not identities
            or not isinstance(accounting, list)
            or not accounting
            or not isinstance(events, list)
            or not events
        ):
            raise R7S2QualificationError(f"staging_process_{stage}_job_evidence_missing")
        stable_keys = {
            (identity.get("pid"), identity.get("creation_time_ns"))
            for identity in identities
            if isinstance(identity, Mapping)
        }
        total_processes = max(
            int(snapshot.get("total_processes", -1))
            for snapshot in accounting
            if isinstance(snapshot, Mapping)
        )
        if len(stable_keys) != len(identities) or len(stable_keys) != total_processes:
            raise R7S2QualificationError(f"staging_process_{stage}_identity_coverage_mismatch")
        expected_name = f"pre-r8-r7s2-stager-{stage.replace('_', '-')}"
        if stage == "linux_identity_readback":
            expected_name += "-exactly-once"
            if (
                invocation["name"] != expected_name
                or invocation["environment_keys"] != wsl_environment_keys
                or len(command) != 20
                or command[:11]
                != [
                    wsl_path,
                    "--distribution",
                    "Ubuntu",
                    "--cd",
                    "/",
                    "--exec",
                    "/usr/bin/env",
                    "-i",
                    "LANG=C.UTF-8",
                    "LC_ALL=C.UTF-8",
                    f"EVM_PHASE_B2_RUN_UUID={run_uuid}",
                ]
                or command[11:16] != ["/usr/bin/python3", "-I", "-S", "-B", "-c"]
                or _sha256_bytes(command[16].encode("utf-8"))
                != STAGER_LINUX_DISCOVERY_SOURCE_SHA256
                or command[17:] != ["Ubuntu", "/usr/bin/env", "/usr/bin/setsid"]
            ):
                raise R7S2QualificationError("staging_linux_identity_invocation_mismatch")
        elif stage == "git_source_ls_tree":
            expected = expected_commands[stage]
            if (
                invocation["name"] != expected_name
                or invocation["environment_keys"] != git_environment_keys
                or command[:10] != expected[:10]
                or command[11:] != expected[11:]
                or command[10] != source_commit
            ):
                raise R7S2QualificationError(f"staging_process_{stage}_command_mismatch")
        else:
            expected = expected_commands.get(stage)
            if (
                expected is None
                or invocation["name"] != expected_name
                or command != expected
                or invocation["environment_keys"]
                != (git_environment_keys if stage.startswith("git_") else wsl_environment_keys)
            ):
                raise R7S2QualificationError(f"staging_process_{stage}_command_mismatch")

        stdout, _ = _strict_process_streams(
            outcome,
            f"staging_process_{stage}",
            stdout_allows_nul=stage in {"git_source_ls_tree", "git_source_ls_files"},
        )
        if stage == "git_identity":
            if stdout != f"{source_commit}\n{source_tree}\n":
                raise R7S2QualificationError("staging_git_identity_stdout_mismatch")
        elif stage == "git_status":
            if stdout != "":
                raise R7S2QualificationError("staging_git_status_stdout_not_clean")
        elif stage == "git_source_ls_tree":
            if (
                not stdout.endswith("\0")
                or "\0\0" in stdout
                or any(character in stdout for character in ("\n", "\r", "\ufffd"))
            ):
                raise R7S2QualificationError("staging_git_ls_tree_stdout_invalid")
            observed_tree: dict[str, dict[str, str]] = {}
            for raw_record in stdout[:-1].split("\0"):
                match = re.fullmatch(
                    r"(100644|100755) (blob) ([0-9a-f]{40})\t([^\t\0]+)", raw_record
                )
                if match is None or match.group(4) in observed_tree:
                    raise R7S2QualificationError("staging_git_ls_tree_stdout_invalid")
                mode, object_type, object_id, relative_path = match.groups()
                observed_tree[relative_path] = {
                    "mode": mode,
                    "type": object_type,
                    "oid": object_id,
                }
            expected_tree = {
                str(pin["relative_path"]): {
                    "mode": str(pin["git_mode"]),
                    "type": "blob",
                    "oid": str(pin["git_head_blob_oid"]),
                }
                for pin in all_sources.values()
            }
            if observed_tree != expected_tree:
                raise R7S2QualificationError("staging_git_ls_tree_stdout_mismatch")
        elif stage == "git_source_ls_files":
            if (
                not stdout.endswith("\0")
                or "\0\0" in stdout
                or any(character in stdout for character in ("\n", "\r", "\ufffd"))
            ):
                raise R7S2QualificationError("staging_git_ls_files_stdout_invalid")
            observed_index: dict[str, dict[str, str]] = {}
            for raw_record in stdout[:-1].split("\0"):
                match = re.fullmatch(
                    r"(H) (100644|100755) ([0-9a-f]{40}) (0)\t([^\t\0]+)", raw_record
                )
                if match is None or match.group(5) in observed_index:
                    raise R7S2QualificationError("staging_git_ls_files_stdout_invalid")
                flag, mode, object_id, git_stage, relative_path = match.groups()
                observed_index[relative_path] = {
                    "flag": flag,
                    "mode": mode,
                    "oid": object_id,
                    "stage": git_stage,
                }
            expected_index = {
                str(pin["relative_path"]): {
                    "flag": "H",
                    "mode": str(pin["git_mode"]),
                    "oid": str(pin["git_head_blob_oid"]),
                    "stage": "0",
                }
                for pin in all_sources.values()
            }
            if observed_index != expected_index:
                raise R7S2QualificationError("staging_git_ls_files_stdout_mismatch")
        elif stage.startswith("git_source_hash_object_"):
            role = stage.removeprefix("git_source_hash_object_")
            if role not in all_sources:
                raise R7S2QualificationError(f"staging_git_hash_stdout_mismatch:{role}")
            expected_oid = str(all_sources[role]["git_normalized_worktree_blob_oid"])
            if (
                expected_oid != str(all_sources[role]["git_head_blob_oid"])
                or HEX40.fullmatch(expected_oid) is None
                or stdout != f"{expected_oid}\n"
            ):
                raise R7S2QualificationError(f"staging_git_hash_stdout_mismatch:{role}")
        elif stage in {"ubuntu_verbose_pre", "ubuntu_verbose_post"}:
            _require_ubuntu_verbose_running(stdout, f"staging_{stage}")
        elif stage in {"ubuntu_running_pre", "ubuntu_running_post"}:
            _require_ubuntu_running_membership(stdout, f"staging_{stage}")
        elif stage == "linux_identity_readback":
            discovery = _canonical_json_line(stdout, "staging_linux_stdout")
            _exact_keys(
                discovery,
                {
                    "schema",
                    "status",
                    "distro",
                    "kernel_release",
                    "distro_version",
                    "boot_id",
                    "rootfs_identity",
                    "os_release_sha256",
                    "machine_id_sha256",
                    "binaries",
                },
                "staging_linux_stdout",
            )
            candidate_paths = {
                "python3": "/usr/bin/python3",
                "env": "/usr/bin/env",
                "setsid": "/usr/bin/setsid",
            }
            raw_binaries = _mapping(discovery["binaries"], "staging_linux_stdout_binaries")
            _exact_keys(raw_binaries, set(candidate_paths), "staging_linux_stdout_binaries")
            normalized_binaries: dict[str, dict[str, Any]] = {}
            for role, candidate in candidate_paths.items():
                raw_pin = _mapping(raw_binaries[role], f"staging_linux_stdout_{role}")
                _exact_keys(
                    raw_pin,
                    {"candidate_path", "realpath", "sha256", "bytes", "version"},
                    f"staging_linux_stdout_{role}",
                )
                normalized_binaries[role] = {
                    "path": raw_pin["realpath"],
                    "sha256": raw_pin["sha256"],
                    "bytes": raw_pin["bytes"],
                    "version": raw_pin["version"],
                }
                if raw_pin["candidate_path"] != candidate:
                    raise R7S2QualificationError(f"staging_linux_stdout_candidate_mismatch:{role}")
            normalized_discovery = dict(discovery)
            normalized_discovery["binaries"] = normalized_binaries
            if normalized_discovery != linux_identity:
                raise R7S2QualificationError("staging_linux_stdout_identity_mismatch")


def _validated_stager_bootstrap_attestation(
    value: Any, stager_pin: Mapping[str, Any], *, expected_argv_sha256: str
) -> dict[str, Any]:
    attestation = _mapping(value, "stager_bootstrap_attestation")
    _exact_keys(
        attestation,
        {
            "schema",
            "stager_path",
            "stager_sha256",
            "stager_bytes",
            "stager_lf_sha256",
            "stager_blob_oid",
            "stager_argv_sha256",
            "bootstrap_source_sha256",
        },
        "stager_bootstrap_attestation",
    )
    stager_path = Path(str(stager_pin["path"]))
    _assert_no_reparse_chain(stager_path)
    raw = stager_path.read_bytes()
    normalized = _lf_normalized_source(raw)
    if (
        attestation["schema"] != "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.stager-bootstrap-attestation.v1"
        or not _path_equal(Path(str(attestation["stager_path"])), stager_path)
        or attestation["stager_sha256"] != stager_pin["sha256"]
        or attestation["stager_bytes"] != stager_pin["bytes"]
        or attestation["stager_lf_sha256"] != _sha256_bytes(normalized)
        or attestation["stager_blob_oid"] != stager_pin["git_head_blob_oid"]
        or attestation["stager_blob_oid"] != _git_blob_oid(normalized)
        or attestation["stager_argv_sha256"] != expected_argv_sha256
        or attestation["bootstrap_source_sha256"] != STAGER_BOOTSTRAP_SOURCE_SHA256
    ):
        raise R7S2QualificationError("stager_bootstrap_attestation_mismatch")
    return dict(attestation)


def _validate_staging_attestation(
    value: Any,
    *,
    contract_path: Path,
    qualification_id: str,
    run_uuid: str,
    attempt_id: str,
    source_identity: Mapping[str, Any],
    source_pins: Mapping[str, Any],
    parent_evidence: Mapping[str, Any],
    host_binaries: Mapping[str, BinaryPin],
    linux_binaries: Mapping[str, BinaryPin],
    platform_identity: Mapping[str, str],
    allow_unpublished_validation: bool,
) -> dict[str, Any]:
    attestation = _mapping(value, "staging_attestation")
    _exact_keys(
        attestation,
        {"schema", "issued_at_utc", "expires_at_utc", "preauthorization_index"},
        "staging_attestation",
    )
    if attestation["schema"] != STAGING_ATTESTATION_SCHEMA:
        raise R7S2QualificationError("staging_attestation_schema_mismatch")
    issued = _utc_datetime(attestation["issued_at_utc"], "staging_attestation_issued")
    expires = _utc_datetime(attestation["expires_at_utc"], "staging_attestation_expires")
    now = datetime.now(UTC)
    if issued > now or expires <= issued or (expires - issued).total_seconds() > 1800:
        raise R7S2QualificationError("staging_attestation_time_window_invalid")
    if not allow_unpublished_validation and now >= expires:
        raise R7S2QualificationError("staging_attestation_expired")
    index_pin, index = _read_pinned_json(
        attestation["preauthorization_index"],
        "preauthorization_index",
        canonical_owned=True,
    )
    expected_index_path = contract_path.parent / "preauthorization-index.json"
    if not _path_equal(Path(index_pin["path"]), expected_index_path):
        raise R7S2QualificationError("staging_index_path_mismatch")
    expected_keys = {
        "schema",
        "stager_schema",
        "status",
        "qualification_id",
        "run_uuid",
        "attempt_id",
        "indexed_at_utc",
        "expires_at_utc",
        "contract_expected_path",
        "source_identity",
        "token_evidence",
        "host_binaries",
        "linux_identity",
        "ubuntu_state_pre",
        "ubuntu_state_post",
        "parent_map",
        "parent_evidence",
        "reservation",
        "stager_bootstrap_attestation",
        "process_evidence",
        "call_counts",
        "qualification_invocation_policy",
        "automatic_retry_count",
        "forced_termination_attempts",
        "wsl_shutdown_calls",
        "docker_kubernetes_service_mutations",
        "qualification_started",
        "r8_started",
    }
    _exact_keys(index, expected_keys, "staging_index_payload")
    if (
        index["schema"] != STAGING_INDEX_SCHEMA
        or index["stager_schema"] != STAGER_SCHEMA
        or index["status"] != "staging_authorized_contract_pending"
        or index["qualification_id"] != qualification_id
        or index["run_uuid"] != run_uuid
        or index["attempt_id"] != attempt_id
        or index["indexed_at_utc"] != attestation["issued_at_utc"]
        or index["expires_at_utc"] != attestation["expires_at_utc"]
        or not _path_equal(Path(str(index["contract_expected_path"])), contract_path)
        or index["parent_evidence"] != parent_evidence
        or index["automatic_retry_count"] != 0
        or index["forced_termination_attempts"] != 0
        or index["wsl_shutdown_calls"] != 0
        or index["docker_kubernetes_service_mutations"] != 0
        or index["qualification_started"] is not False
        or index["r8_started"] is not False
    ):
        raise R7S2QualificationError("staging_index_binding_mismatch")
    indexed_source = _mapping(index["source_identity"], "staging_index_source_identity")
    _exact_keys(
        indexed_source,
        {"commit", "tree", "source_pins", "git_config"},
        "staging_index_source_identity",
    )
    if (
        indexed_source.get("commit") != source_identity["commit"]
        or indexed_source.get("tree") != source_identity["tree"]
        or indexed_source.get("source_pins") != {**source_pins, "stager": source_identity["stager"]}
        or indexed_source.get("git_config") != source_identity["git_config"]
    ):
        raise R7S2QualificationError("staging_index_source_identity_mismatch")
    expected_host = {
        role: {
            "path": pin.path,
            "sha256": pin.sha256,
            "bytes": pin.bytes,
            "version": pin.version,
        }
        for role, pin in host_binaries.items()
    }
    expected_linux = {
        role: {
            "path": pin.path,
            "sha256": pin.sha256,
            "bytes": pin.bytes,
            "version": pin.version,
        }
        for role, pin in linux_binaries.items()
    }
    if index["host_binaries"] != expected_host:
        raise R7S2QualificationError("staging_index_host_identity_mismatch")
    linux_identity = _mapping(index["linux_identity"], "staging_index_linux_identity")
    expected_linux_identity = {
        "schema": LINUX_DISCOVERY_SCHEMA,
        "status": "observed",
        "distro": "Ubuntu",
        "kernel_release": platform_identity["kernel_release"],
        "distro_version": platform_identity["distro_version"],
        "boot_id": platform_identity["boot_id"],
        "rootfs_identity": platform_identity["rootfs_identity"],
        "os_release_sha256": platform_identity["os_release_sha256"],
        "machine_id_sha256": platform_identity["machine_id_sha256"],
        "binaries": expected_linux,
    }
    if linux_identity != expected_linux_identity:
        raise R7S2QualificationError("staging_index_linux_identity_mismatch")
    for label in ("ubuntu_state_pre", "ubuntu_state_post"):
        if index[label] != {"distribution": "Ubuntu", "state": "Running", "version": 2}:
            raise R7S2QualificationError(f"staging_index_{label}_mismatch")
    token = _mapping(index["token_evidence"], "staging_index_token_evidence")
    _exact_keys(
        token,
        {
            "captured_at_utc",
            "pid",
            "ppid",
            "session_id",
            "path",
            "path_sha256",
            "administrator",
            "integrity",
            "integrity_rid",
            "token_elevation_type",
            "token_elevation_type_value",
        },
        "staging_index_token_evidence",
    )
    token_time = _utc_datetime(token["captured_at_utc"], "staging_index_token_captured")
    if not (
        token.get("administrator") is True
        and token.get("integrity") in {"High", "System"}
        and token.get("token_elevation_type") == "Full"
        and token.get("token_elevation_type_value") == 2
        and isinstance(token.get("integrity_rid"), int)
        and not isinstance(token.get("integrity_rid"), bool)
        and int(token["integrity_rid"]) >= 0x3000
        and isinstance(token.get("pid"), int)
        and not isinstance(token.get("pid"), bool)
        and int(token["pid"]) > 0
        and isinstance(token.get("ppid"), int)
        and not isinstance(token.get("ppid"), bool)
        and int(token["ppid"]) >= 0
        and isinstance(token.get("session_id"), int)
        and not isinstance(token.get("session_id"), bool)
        and int(token["session_id"]) >= 0
        and _path_equal(Path(str(token.get("path"))), Path(host_binaries["python"].path))
        and token.get("path_sha256") == host_binaries["python"].sha256
        and token_time <= issued
    ):
        raise R7S2QualificationError("staging_index_admin_token_invalid")

    parent_map_pin, parent_map_payload = _read_pinned_json(
        index["parent_map"], "staging_parent_map"
    )
    if parent_map_payload != {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.parent-map.v1",
        "parents": parent_evidence,
    }:
        raise R7S2QualificationError("staging_parent_map_payload_mismatch")
    expected_stager_argv = [
        "--qualification-id",
        qualification_id,
        "--run-uuid",
        run_uuid,
        "--attempt-id",
        attempt_id,
        "--expected-source-commit",
        str(source_identity["commit"]),
        "--expected-source-tree",
        str(source_identity["tree"]),
        "--expected-qualification-sha256",
        str(source_pins["qualification_script"]["sha256"]),
        "--expected-process-module-sha256",
        str(source_pins["process_module"]["sha256"]),
        "--expected-r7s1-runner-sha256",
        str(source_pins["r7s1_runner"]["sha256"]),
        "--expected-stager-sha256",
        str(source_identity["stager"]["sha256"]),
        "--expected-outer-sha256",
        str(source_pins["outer_launcher"]["sha256"]),
        "--parent-map",
        str(parent_map_pin["path"]),
        "--expected-parent-map-sha256",
        str(parent_map_pin["sha256"]),
        "--execute-stage-non-credit-once",
    ]
    expected_stager_argv_sha256 = _sha256_bytes(
        json.dumps(expected_stager_argv, separators=(",", ":")).encode("utf-8")
    )
    bootstrap_attestation = _validated_stager_bootstrap_attestation(
        index["stager_bootstrap_attestation"],
        source_identity["stager"],
        expected_argv_sha256=expected_stager_argv_sha256,
    )
    reservation_pin, reservation = _read_pinned_json(
        index["reservation"], "staging_reservation", canonical_owned=True
    )
    if not _path_equal(
        Path(reservation_pin["path"]), contract_path.parent / "staging-reservation.json"
    ):
        raise R7S2QualificationError("staging_reservation_path_mismatch")
    _exact_keys(
        reservation,
        {
            "schema",
            "qualification_id",
            "run_uuid",
            "attempt_id",
            "reserved_at_utc",
            "expected_source_commit",
            "expected_source_tree",
            "expected_source_sha256",
            "parent_map",
            "stager_bootstrap_attestation",
            "automatic_retry_budget",
            "forced_termination_budget",
            "service_mutation_budget",
            "linux_identity_readback_budget",
            "qualification_budget",
        },
        "staging_reservation_payload",
    )
    expected_source_sha = {
        role: ({**source_pins, "stager": source_identity["stager"]})[role]["sha256"]
        for role in sorted({*source_pins, "stager"})
    }
    if (
        reservation["schema"] != STAGING_RESERVATION_SCHEMA
        or reservation["qualification_id"] != qualification_id
        or reservation["run_uuid"] != run_uuid
        or reservation["attempt_id"] != attempt_id
        or reservation["expected_source_commit"] != source_identity["commit"]
        or reservation["expected_source_tree"] != source_identity["tree"]
        or reservation["expected_source_sha256"] != expected_source_sha
        or reservation["parent_map"] != parent_map_pin
        or reservation["stager_bootstrap_attestation"] != bootstrap_attestation
        or _utc_datetime(reservation["reserved_at_utc"], "staging_reservation_reserved_at") > issued
        or reservation["automatic_retry_budget"] != 0
        or reservation["forced_termination_budget"] != 0
        or reservation["service_mutation_budget"] != 0
        or reservation["linux_identity_readback_budget"] != 1
        or reservation["qualification_budget"] != 0
    ):
        raise R7S2QualificationError("staging_reservation_binding_mismatch")

    positive_calls = {
        "git_identity",
        "git_status",
        "git_source_ls_tree",
        "git_source_ls_files",
        *(f"git_source_hash_object_{role}" for role in sorted({*source_pins, "stager"})),
        "ubuntu_verbose_pre",
        "ubuntu_running_pre",
        "linux_identity_readback",
        "ubuntu_verbose_post",
        "ubuntu_running_post",
    }
    zero_calls = {
        "automatic_retries",
        "forced_termination_attempts",
        "wsl_shutdown_calls",
        "docker_kubernetes_service_mutations",
        "qualification_calls",
        "r8_calls",
    }
    expected_call_counts = {**{key: 1 for key in positive_calls}, **{key: 0 for key in zero_calls}}
    if index["call_counts"] != expected_call_counts:
        raise R7S2QualificationError("staging_call_counts_exact_mismatch")
    _validate_stager_process_evidence(
        index["process_evidence"],
        run_uuid=run_uuid,
        source_commit=source_identity["commit"],
        source_tree=source_identity["tree"],
        source_pins=source_pins,
        stager_pin=source_identity["stager"],
        host_binaries=host_binaries,
        linux_identity=linux_identity,
    )
    if index["qualification_invocation_policy"] != {
        "execution_route": "outer_launcher_only",
        "outer_launcher": source_pins["outer_launcher"]["path"],
        "qualification_script_sha256": source_pins["qualification_script"]["sha256"],
        "contract_and_final_index_sha256": "out_of_band_required",
    }:
        raise R7S2QualificationError("staging_invocation_policy_mismatch")
    for forbidden in (
        contract_path.parent / "staging-failure-seal.json",
        contract_path.parent / "failure-index.json",
        contract_path.parent.parent / f"{contract_path.parent.name}-emergency-seal",
    ):
        _assert_no_reparse_chain(forbidden, allow_missing_leaf=True)
        if forbidden.exists():
            raise R7S2QualificationError("staging_failure_or_emergency_artifact_present")
    return {
        "schema": STAGING_ATTESTATION_SCHEMA,
        "issued_at_utc": attestation["issued_at_utc"],
        "expires_at_utc": attestation["expires_at_utc"],
        "preauthorization_index": index_pin,
        "preauthorization_payload": index,
        "stager_bootstrap_attestation": bootstrap_attestation,
    }


def _validate_launch_authorization(
    path: Path,
    *,
    expected_sha256: str,
    contract_path: Path,
    contract_sha256: str,
    contract_bytes: int,
    qualification_id: str,
    run_uuid: str,
    attempt_id: str,
    source_identity: Mapping[str, Any],
    source_pins: Mapping[str, Any],
    staging_attestation: Mapping[str, Any],
    host_binaries: Mapping[str, BinaryPin],
) -> dict[str, Any]:
    authorization_path = Path(os.path.abspath(path))
    expected_path = contract_path.parent / "staging-index.json"
    if not _path_equal(authorization_path, expected_path):
        raise R7S2QualificationError("launch_index_path_mismatch")
    _assert_no_reparse_chain(authorization_path)
    raw = authorization_path.read_bytes()
    actual_sha = _sha256_bytes(raw)
    if actual_sha != _hex64(expected_sha256, "expected_launch_index_sha256"):
        raise R7S2QualificationError("launch_index_sha256_mismatch")
    value = _mapping(
        _strict_json_bytes(raw, "launch_index", canonical_owned=True, terminal_newline=True),
        "launch_index",
    )
    _exact_keys(
        value,
        {
            "schema",
            "status",
            "qualification_id",
            "run_uuid",
            "attempt_id",
            "published_at_utc",
            "expires_at_utc",
            "contract",
            "preauthorization_index",
            "reservation",
            "stager_bootstrap_attestation",
            "source_identity",
            "bootstrap",
            "outer_timeout_contract",
            "outer_evidence_leaf",
            "outer_evidence_directory",
            "qualification_invocation_policy",
            "automatic_retry_count",
            "forced_termination_attempts",
            "wsl_shutdown_calls",
            "docker_kubernetes_service_mutations",
            "qualification_started",
            "r8_started",
        },
        "launch_index",
    )
    published = _utc_datetime(value["published_at_utc"], "launch_index_published")
    expires = _utc_datetime(value["expires_at_utc"], "launch_index_expires")
    preauth = _mapping(staging_attestation["preauthorization_payload"], "preauthorization_payload")
    preauth_issued = _utc_datetime(preauth["indexed_at_utc"], "preauthorization_indexed")
    now = datetime.now(UTC)
    if published < preauth_issued or published > now or expires <= published or now >= expires:
        raise R7S2QualificationError("launch_index_time_window_invalid")
    contract_pin = _mapping(value["contract"], "launch_index_contract")
    if contract_pin != {
        "path": str(contract_path),
        "sha256": contract_sha256,
        "bytes": contract_bytes,
    }:
        raise R7S2QualificationError("launch_index_contract_binding_mismatch")
    if value["preauthorization_index"] != staging_attestation["preauthorization_index"]:
        raise R7S2QualificationError("launch_index_preauthorization_binding_mismatch")
    if value["reservation"] != preauth["reservation"]:
        raise R7S2QualificationError("launch_index_reservation_binding_mismatch")
    if (
        value["stager_bootstrap_attestation"] != staging_attestation["stager_bootstrap_attestation"]
        or value["stager_bootstrap_attestation"] != preauth["stager_bootstrap_attestation"]
    ):
        raise R7S2QualificationError("launch_index_stager_bootstrap_binding_mismatch")
    expected_sources = {**source_pins, "stager": source_identity["stager"]}
    if value["source_identity"] != {
        "commit": source_identity["commit"],
        "tree": source_identity["tree"],
        "source_pins": expected_sources,
        "git_config": source_identity["git_config"],
    }:
        raise R7S2QualificationError("launch_index_source_identity_mismatch")
    outer_pin = _mapping(source_pins["outer_launcher"], "outer_launcher_source_pin")
    outer_path = Path(str(outer_pin["path"]))
    _assert_no_reparse_chain(outer_path)
    outer_raw = outer_path.read_bytes()
    if len(outer_raw) != outer_pin["bytes"] or _sha256_bytes(outer_raw) != outer_pin["sha256"]:
        raise R7S2QualificationError("outer_launcher_identity_mismatch_at_launch_index")
    expected_bootstrap = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-bootstrap.v1",
        "source_sha256": OUTER_BOOTSTRAP_SOURCE_SHA256,
        "line_ending_policy": ("uniform_lf_or_uniform_crlf_normalized_to_lf_bare_cr_forbidden"),
        "outer_lf_normalized_sha256": _sha256_bytes(_lf_normalized_source(outer_raw)),
        "expected_source_commit": source_identity["commit"],
        "expected_source_tree": source_identity["tree"],
    }
    if value["bootstrap"] != expected_bootstrap:
        raise R7S2QualificationError("launch_index_bootstrap_binding_mismatch")
    if value["outer_timeout_contract"] != OUTER_TIMEOUT_CONTRACT:
        raise R7S2QualificationError("launch_index_outer_timeout_contract_mismatch")
    outer_leaf = f"outer-{attempt_id.replace('-', '')[:8]}"
    expected_outer_directory = contract_path.parent.parent / outer_leaf
    if (
        value["schema"] != LAUNCH_INDEX_SCHEMA
        or value["status"] != "ready_non_credit_not_executed"
        or value["qualification_id"] != qualification_id
        or value["run_uuid"] != run_uuid
        or value["attempt_id"] != attempt_id
        or value["expires_at_utc"] != staging_attestation["expires_at_utc"]
        or value["outer_evidence_leaf"] != outer_leaf
        or not _path_equal(Path(str(value["outer_evidence_directory"])), expected_outer_directory)
        or value["automatic_retry_count"] != 0
        or value["forced_termination_attempts"] != 0
        or value["wsl_shutdown_calls"] != 0
        or value["docker_kubernetes_service_mutations"] != 0
        or value["qualification_started"] is not False
        or value["r8_started"] is not False
    ):
        raise R7S2QualificationError("launch_index_binding_mismatch")
    expected_invocation = {
        "python": host_binaries["python"].path,
        "isolated_flags": ["-I", "-S", "-B"],
        "execution_route": "python_c_sha_pinned_bootstrap_then_outer",
        "bootstrap_source_sha256": OUTER_BOOTSTRAP_SOURCE_SHA256,
        "outer_launcher": source_pins["outer_launcher"]["path"],
        "contract_path": str(contract_path),
        "launch_index_path": str(authorization_path),
        "contract_sha256_source": "out_of_band_required",
        "launch_index_sha256_source": "out_of_band_required",
        "outer_sha256_source": "out_of_band_required",
        "source_commit_tree_source": "out_of_band_required",
        "execute_flag": "--execute-non-credit-once",
    }
    if value["qualification_invocation_policy"] != expected_invocation:
        raise R7S2QualificationError("launch_index_invocation_policy_mismatch")
    return {
        "path": str(authorization_path),
        "sha256": actual_sha,
        "bytes": len(raw),
        "outer_evidence_directory": str(expected_outer_directory),
        "published_at_utc": value["published_at_utc"],
        "expires_at_utc": value["expires_at_utc"],
    }


def _validate_outer_reservation(
    path: Path,
    *,
    expected_sha256: str,
    expected_outer_directory: Path,
    qualification_id: str,
    run_uuid: str,
    attempt_id: str,
    contract_sha256: str,
    launch_index_sha256: str,
    outer_sha256: str,
    source_commit: str,
    source_tree: str,
    require_child_parent: bool,
) -> dict[str, Any]:
    reservation_path = Path(os.path.abspath(path))
    expected_path = expected_outer_directory / "outer-reservation.json"
    if not _path_equal(reservation_path, expected_path):
        raise R7S2QualificationError("outer_reservation_path_mismatch")
    _assert_no_reparse_chain(reservation_path)
    raw = reservation_path.read_bytes()
    actual_sha = _sha256_bytes(raw)
    if actual_sha != _hex64(expected_sha256, "expected_outer_reservation_sha256"):
        raise R7S2QualificationError("outer_reservation_sha256_mismatch")
    payload = _mapping(
        _strict_json_bytes(raw, "outer_reservation", canonical_owned=True, terminal_newline=True),
        "outer_reservation",
    )
    _exact_keys(
        payload,
        {
            "schema",
            "qualification_id",
            "run_uuid",
            "attempt_id",
            "reserved_at_utc",
            "pid",
            "process_creation_filetime",
            "administrator_token_evidence",
            "contract_sha256",
            "launch_index_sha256",
            "outer_sha256",
            "outer_bootstrap_attestation",
            "qualification_child_budget",
            "automatic_retry_budget",
            "forced_termination_budget",
        },
        "outer_reservation",
    )
    pid = _positive_int(payload["pid"], "outer_reservation_pid")
    creation_filetime = _positive_int(
        payload["process_creation_filetime"], "outer_reservation_process_creation_filetime"
    )
    token = _mapping(payload["administrator_token_evidence"], "outer_reservation_token")
    bootstrap = _mapping(payload["outer_bootstrap_attestation"], "outer_bootstrap_attestation")
    _exact_keys(
        bootstrap,
        {
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
        },
        "outer_bootstrap_attestation",
    )
    expected_staging_directory = (
        expected_outer_directory.parent / f"c-{attempt_id.replace('-', '')[:8]}"
    )
    expected_contract_path = expected_staging_directory / "qualification-contract.json"
    expected_launch_index_path = expected_staging_directory / "staging-index.json"
    outer_raw = OUTER_SCRIPT.read_bytes()
    expected_outer_argv = [
        "--contract",
        str(expected_contract_path),
        "--expected-contract-sha256",
        contract_sha256,
        "--launch-index",
        str(expected_launch_index_path),
        "--expected-launch-index-sha256",
        launch_index_sha256,
        "--expected-outer-sha256",
        outer_sha256,
        "--expected-source-commit",
        source_commit,
        "--expected-source-tree",
        source_tree,
        "--execute-non-credit-once",
    ]
    expected_outer_argv_sha256 = _sha256_bytes(
        json.dumps(expected_outer_argv, separators=(",", ":")).encode("utf-8")
    )
    reserved_at = _utc_datetime(payload["reserved_at_utc"], "outer_reservation_reserved_at_utc")
    age_seconds = (datetime.now(UTC) - reserved_at).total_seconds()
    if age_seconds < -5 or age_seconds > OUTER_RESERVATION_MAX_AGE_SECONDS:
        raise R7S2QualificationError("outer_reservation_freshness_mismatch")
    if (
        payload["schema"] != OUTER_RESERVATION_SCHEMA
        or payload["qualification_id"] != qualification_id
        or payload["run_uuid"] != run_uuid
        or payload["attempt_id"] != attempt_id
        or payload["contract_sha256"] != contract_sha256
        or payload["launch_index_sha256"] != launch_index_sha256
        or payload["outer_sha256"] != outer_sha256
        or bootstrap["schema"] != "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.bootstrap-attestation.v1"
        or bootstrap["outer_raw_sha256"] != outer_sha256
        or bootstrap["contract_sha256"] != contract_sha256
        or bootstrap["launch_index_sha256"] != launch_index_sha256
        or bootstrap["expected_source_commit"] != source_commit
        or bootstrap["expected_source_tree"] != source_tree
        or bootstrap["bootstrap_source_sha256"] != OUTER_BOOTSTRAP_SOURCE_SHA256
        or not _path_equal(Path(str(bootstrap["contract_path"])), expected_contract_path)
        or not _path_equal(Path(str(bootstrap["launch_index_path"])), expected_launch_index_path)
        or not _path_equal(Path(str(bootstrap["outer_path"])), OUTER_SCRIPT)
        or bootstrap["outer_bytes"] != len(outer_raw)
        or bootstrap["outer_lf_normalized_sha256"]
        != _sha256_bytes(_lf_normalized_source(outer_raw))
        or bootstrap["outer_argv_sha256"] != expected_outer_argv_sha256
        or bootstrap["python_identity"] != HOST_EXPECTATIONS["python"]
        or payload["qualification_child_budget"] != 1
        or payload["automatic_retry_budget"] != 0
        or payload["forced_termination_budget"] != 0
        or token.get("administrator") is not True
        or token.get("integrity") not in {"High", "System"}
        or token.get("token_elevation_type") != "Full"
        or token.get("token_elevation_type_value") != 2
    ):
        raise R7S2QualificationError("outer_reservation_binding_mismatch")
    if _process_creation_filetime(pid) != creation_filetime:
        raise R7S2QualificationError("outer_reservation_process_identity_mismatch")
    role = (
        "qualification_child"
        if os.getppid() == pid
        else "outer_preflight"
        if os.getpid() == pid
        else None
    )
    if role is None or (require_child_parent and role != "qualification_child"):
        raise R7S2QualificationError("outer_reservation_parent_process_mismatch")
    return {
        "path": str(reservation_path),
        "sha256": actual_sha,
        "bytes": len(raw),
        "pid": pid,
        "process_creation_filetime": creation_filetime,
        "role": role,
    }


def load_contract(
    path: Path,
    *,
    expected_sha256: str,
    expected_evidence_root: Path = CANONICAL_EVIDENCE_ROOT,
    allow_unpublished_validation: bool = False,
    launch_index_path: Path | None = None,
    expected_launch_index_sha256: str | None = None,
    outer_reservation_path: Path | None = None,
    expected_outer_reservation_sha256: str | None = None,
) -> QualificationContract:
    _assert_no_reparse_chain(QUALIFIER_INVOCATION_PATH)
    contract_path = Path(os.path.abspath(path))
    _assert_no_reparse_chain(contract_path)
    raw_bytes = contract_path.read_bytes()
    if _sha256_bytes(raw_bytes) != _hex64(expected_sha256, "expected_contract_sha256"):
        raise R7S2QualificationError("contract_sha256_mismatch")
    contract = _mapping(
        _strict_json_bytes(raw_bytes, "contract", canonical_owned=True, terminal_newline=True),
        "contract",
    )
    _exact_keys(
        contract,
        {
            "schema",
            "qualification_id",
            "evidence_leaf",
            "run_uuid",
            "attempt_id",
            "evidence_root",
            "distribution",
            "host_binaries",
            "linux_binaries",
            "platform_identity",
            "source_identity",
            "staging_attestation",
            "parent_evidence",
            "source_pins",
            "timeouts",
            "outer_timeout_contract",
            "fixture",
            "invocation_policy",
        },
        "contract",
    )
    if contract["schema"] != CONTRACT_SCHEMA:
        raise R7S2QualificationError("contract_schema_mismatch")
    qualification_id = str(contract["qualification_id"])
    if RUN_ID_RE.fullmatch(qualification_id) is None:
        raise R7S2QualificationError("qualification_id_invalid")
    run_uuid = _uuid(contract["run_uuid"], "run_uuid")
    attempt_id = _uuid(contract["attempt_id"], "attempt_id")
    if run_uuid == attempt_id:
        raise R7S2QualificationError("run_uuid_attempt_id_must_differ")
    expected_staging_leaf = f"c-{attempt_id.replace('-', '')[:8]}"
    if (
        STAGING_LEAF_RE.fullmatch(contract_path.parent.name) is None
        or contract_path.parent.name != expected_staging_leaf
        or (
            not allow_unpublished_validation and contract_path.name != "qualification-contract.json"
        )
    ):
        raise R7S2QualificationError("contract_staging_path_mismatch")
    evidence_leaf = str(contract["evidence_leaf"])
    expected_leaf = f"wsl-{attempt_id.replace('-', '')[:8]}"
    if EVIDENCE_LEAF_RE.fullmatch(evidence_leaf) is None or evidence_leaf != expected_leaf:
        raise R7S2QualificationError("evidence_leaf_mismatch")
    evidence_root = Path(str(contract["evidence_root"]))
    if not evidence_root.is_absolute() or not _path_equal(evidence_root, expected_evidence_root):
        raise R7S2QualificationError("evidence_root_mismatch")
    _assert_no_reparse_chain(evidence_root)
    run_directory = evidence_root / evidence_leaf
    emergency_directory = evidence_root / f"{evidence_leaf}-emergency-seal"
    _assert_path_budget(run_directory)
    for leaf in (
        "invocation-reservation.json",
        "qualification-evidence.json",
        "failure-evidence.json",
        "failure-seal.json",
        "qualification-index.json",
        "failure-index.json",
    ):
        _assert_path_budget(run_directory / leaf)
    _assert_path_budget(emergency_directory)
    _assert_path_budget(emergency_directory / "emergency-seal.json")
    _assert_no_reparse_chain(run_directory, allow_missing_leaf=True)
    _assert_no_reparse_chain(emergency_directory, allow_missing_leaf=True)
    if run_directory.exists():
        raise R7S2QualificationError("qualification_run_directory_exists")
    if emergency_directory.exists():
        raise R7S2QualificationError("qualification_emergency_directory_exists")

    distribution = str(contract["distribution"])
    if distribution != "Ubuntu":
        raise R7S2QualificationError("distribution_must_equal_Ubuntu")

    host_raw = _mapping(contract["host_binaries"], "host_binaries")
    _exact_keys(host_raw, HOST_BINARY_ROLES, "host_binaries")
    host_binaries = {
        role: _binary_pin(host_raw[role], f"host_{role}") for role in sorted(HOST_BINARY_ROLES)
    }
    for role, pin in host_binaries.items():
        _verify_file_pin(pin, f"host_{role}")
        expected = HOST_EXPECTATIONS[role]
        if {
            "path": os.path.normcase(os.path.abspath(pin.path)),
            "sha256": pin.sha256,
            "bytes": pin.bytes,
            "version": pin.version,
        } != {
            "path": os.path.normcase(os.path.abspath(str(expected["path"]))),
            "sha256": expected["sha256"],
            "bytes": expected["bytes"],
            "version": expected["version"],
        }:
            raise R7S2QualificationError(f"host_{role}_canonical_identity_mismatch")
    if any(host_binaries[role].version == "2.7.11.0" for role in ("store_wsl", "wslhost")):
        raise R7S2QualificationError("stale_wsl_2_7_11_pin_forbidden")
    if Path(host_binaries["system32_wsl"].path).name.lower() != "wsl.exe":
        raise R7S2QualificationError("system32_wsl_filename_mismatch")
    if Path(host_binaries["store_wsl"].path).name.lower() != "wsl.exe":
        raise R7S2QualificationError("store_wsl_filename_mismatch")
    if Path(host_binaries["wslhost"].path).name.lower() != "wslhost.exe":
        raise R7S2QualificationError("wslhost_filename_mismatch")
    if (
        Path(host_binaries["python"].path).name.lower() != "python.exe"
        or not _path_equal(Path(host_binaries["python"].path), Path(sys.executable))
        or host_binaries["python"].version != host_platform.python_version()
    ):
        raise R7S2QualificationError("host_python_identity_mismatch")
    if _path_equal(Path(host_binaries["system32_wsl"].path), Path(host_binaries["store_wsl"].path)):
        raise R7S2QualificationError("wsl_launcher_paths_must_differ")

    linux_raw = _mapping(contract["linux_binaries"], "linux_binaries")
    _exact_keys(linux_raw, LINUX_BINARY_ROLES, "linux_binaries")
    linux_binaries = {
        role: _binary_pin(linux_raw[role], f"linux_{role}") for role in sorted(LINUX_BINARY_ROLES)
    }
    for role, pin in linux_binaries.items():
        if not pin.path.startswith("/"):
            raise R7S2QualificationError(f"linux_{role}_path_not_absolute")

    platform = _mapping(contract["platform_identity"], "platform_identity")
    _exact_keys(
        platform,
        {
            "windows_build",
            "wsl_package_version",
            "kernel_release",
            "distro_version",
            "rootfs_identity",
            "os_release_sha256",
            "machine_id_sha256",
            "boot_id",
        },
        "platform_identity",
    )
    platform_identity = {key: str(value) for key, value in platform.items()}
    if any(not value for value in platform_identity.values()):
        raise R7S2QualificationError("platform_identity_empty")
    for key in ("rootfs_identity", "os_release_sha256", "machine_id_sha256"):
        platform_identity[key] = _hex64(platform_identity[key], f"platform_{key}")
    platform_identity["boot_id"] = _uuid(platform_identity["boot_id"], "platform_boot_id")
    if platform_identity["windows_build"] != host_platform.version():
        raise R7S2QualificationError("windows_build_identity_mismatch")
    if platform_identity["wsl_package_version"] != host_binaries["store_wsl"].version:
        raise R7S2QualificationError("wsl_package_version_mismatch")

    parents = _mapping(contract["parent_evidence"], "parent_evidence")
    _exact_keys(parents, PARENT_ROLES, "parent_evidence")
    contract["parent_evidence"] = {
        role: _validate_canonical_parent(role, parents[role]) for role in sorted(PARENT_ROLES)
    }

    source_pins = _mapping(contract["source_pins"], "source_pins")
    _exact_keys(source_pins, SOURCE_ROLES, "source_pins")
    for role in sorted(source_pins):
        source_pins[role] = _validate_source_pin(source_pins[role], f"source_{role}")
    expected_source_paths = {
        "qualification_script": Path(__file__),
        "process_module": SRC_ROOT / "evm" / "scale_validation" / "phase_b2_r7_process.py",
        "r7s1_runner": PROJECT_ROOT / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py",
        "outer_launcher": OUTER_SCRIPT,
    }
    for role, expected_path in expected_source_paths.items():
        if not _path_equal(Path(source_pins[role]["path"]), expected_path):
            raise R7S2QualificationError(f"{role}_path_mismatch")
    contract["source_pins"] = source_pins

    source_identity = _validate_source_identity(contract["source_identity"])
    _verify_trusted_stager_source(source_identity["stager"])
    contract["source_identity"] = source_identity

    staging_attestation = _validate_staging_attestation(
        contract["staging_attestation"],
        contract_path=(
            Path(
                str(
                    _mapping(contract["staging_attestation"], "staging_attestation")[
                        "preauthorization_index"
                    ]["path"]
                )
            ).parent
            / "qualification-contract.json"
            if allow_unpublished_validation
            else contract_path
        ),
        qualification_id=qualification_id,
        run_uuid=run_uuid,
        attempt_id=attempt_id,
        source_identity=source_identity,
        source_pins=source_pins,
        parent_evidence=contract["parent_evidence"],
        host_binaries=host_binaries,
        linux_binaries=linux_binaries,
        platform_identity=platform_identity,
        allow_unpublished_validation=allow_unpublished_validation,
    )
    contract["staging_attestation"] = staging_attestation
    timeouts = _mapping(contract["timeouts"], "timeouts")
    _exact_keys(
        timeouts,
        {
            "launch_wrapper_seconds",
            "launch_residual_seconds",
            "stream_drain_seconds",
            "scan_wrapper_seconds",
            "scan_residual_seconds",
            "observer_deadline_seconds",
            "observer_interval_seconds",
            "observer_max_scans",
        },
        "timeouts",
    )
    normalized_timeouts: dict[str, float | int] = {
        key: _positive_number(value, f"timeouts_{key}") for key, value in timeouts.items()
    }
    max_scans = _positive_int(timeouts["observer_max_scans"], "observer_max_scans")
    normalized_timeouts["observer_max_scans"] = max_scans
    if normalized_timeouts != CONTRACT_TIMEOUTS:
        raise R7S2QualificationError("timeout_contract_exact_mismatch")
    if _mapping(contract["outer_timeout_contract"], "outer_timeout_contract") != (
        OUTER_TIMEOUT_CONTRACT
    ):
        raise R7S2QualificationError("outer_timeout_contract_exact_mismatch")
    if not (
        normalized_timeouts["scan_wrapper_seconds"]
        < normalized_timeouts["launch_wrapper_seconds"]
        < normalized_timeouts["observer_deadline_seconds"]
        and normalized_timeouts["launch_wrapper_seconds"] <= 30
        and normalized_timeouts["launch_residual_seconds"] <= 15
        and normalized_timeouts["stream_drain_seconds"] <= 10
        and normalized_timeouts["scan_wrapper_seconds"] <= 8
        and normalized_timeouts["scan_residual_seconds"] <= 5
        and normalized_timeouts["observer_deadline_seconds"] <= 60
        and max_scans <= 128
        and 0.005 <= normalized_timeouts["observer_interval_seconds"] <= 0.25
    ):
        raise R7S2QualificationError("timeout_order_or_bound_invalid")

    fixture = _mapping(contract["fixture"], "fixture")
    _exact_keys(fixture, {"source_sha256", "lifetime_seconds"}, "fixture")
    if _hex64(fixture["source_sha256"], "fixture_source_sha256") != _sha256_bytes(
        DETACHED_DESCENDANT_SOURCE.encode("utf-8")
    ):
        raise R7S2QualificationError("fixture_source_sha256_mismatch")
    lifetime = _positive_number(fixture["lifetime_seconds"], "fixture_lifetime_seconds")
    if lifetime != FIXTURE_LIFETIME_SECONDS:
        raise R7S2QualificationError("fixture_lifetime_exact_mismatch")
    fixture["lifetime_seconds"] = lifetime

    if _mapping(contract["invocation_policy"], "invocation_policy") != INVOCATION_POLICY:
        raise R7S2QualificationError("invocation_policy_mismatch")

    if allow_unpublished_validation:
        launch_authorization = {"status": "unpublished_validation_only"}
        outer_reservation = None
    else:
        if launch_index_path is None or expected_launch_index_sha256 is None:
            raise R7S2QualificationError("out_of_band_launch_index_pin_required")
        launch_authorization = _validate_launch_authorization(
            launch_index_path,
            expected_sha256=expected_launch_index_sha256,
            contract_path=contract_path,
            contract_sha256=_sha256_bytes(raw_bytes),
            contract_bytes=len(raw_bytes),
            qualification_id=qualification_id,
            run_uuid=run_uuid,
            attempt_id=attempt_id,
            source_identity=source_identity,
            source_pins=source_pins,
            staging_attestation=staging_attestation,
            host_binaries=host_binaries,
        )
        if (outer_reservation_path is None) != (expected_outer_reservation_sha256 is None):
            raise R7S2QualificationError("outer_reservation_pin_pair_required")
        outer_reservation = (
            _validate_outer_reservation(
                outer_reservation_path,
                expected_sha256=str(expected_outer_reservation_sha256),
                expected_outer_directory=Path(launch_authorization["outer_evidence_directory"]),
                qualification_id=qualification_id,
                run_uuid=run_uuid,
                attempt_id=attempt_id,
                contract_sha256=_sha256_bytes(raw_bytes),
                launch_index_sha256=_hex64(
                    expected_launch_index_sha256, "expected_launch_index_sha256"
                ),
                outer_sha256=source_pins["outer_launcher"]["sha256"],
                source_commit=source_identity["commit"],
                source_tree=source_identity["tree"],
                require_child_parent=False,
            )
            if outer_reservation_path is not None
            else None
        )

    _bind_verified_process_runtime(source_pins["process_module"])

    interpreter_isolated = bool(
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.no_user_site
        and sys.flags.ignore_environment
        and sys.flags.dont_write_bytecode
        and sys.flags.safe_path
    )
    execution_authorized = bool(
        not allow_unpublished_validation
        and _path_equal(evidence_root, CANONICAL_EVIDENCE_ROOT)
        and interpreter_isolated
        and launch_index_path is not None
        and expected_launch_index_sha256 is not None
        and outer_reservation is not None
        and outer_reservation["role"] == "qualification_child"
    )

    return QualificationContract(
        raw=contract,
        qualification_id=qualification_id,
        run_uuid=run_uuid,
        attempt_id=attempt_id,
        evidence_root=evidence_root,
        run_directory=run_directory,
        emergency_directory=emergency_directory,
        distribution=distribution,
        host_binaries=host_binaries,
        linux_binaries=linux_binaries,
        platform_identity=platform_identity,
        source_identity=source_identity,
        staging_attestation=staging_attestation,
        launch_authorization=launch_authorization,
        timeouts=normalized_timeouts,
        contract_path=contract_path,
        contract_sha256=_sha256_bytes(raw_bytes),
        launch_index_path=(Path(os.path.abspath(launch_index_path)) if launch_index_path else None),
        launch_index_sha256=(
            _hex64(expected_launch_index_sha256, "expected_launch_index_sha256")
            if expected_launch_index_sha256
            else None
        ),
        outer_reservation_path=(
            Path(outer_reservation["path"]) if outer_reservation is not None else None
        ),
        outer_reservation_sha256=(
            str(outer_reservation["sha256"]) if outer_reservation is not None else None
        ),
        outer_parent_pid=(int(outer_reservation["pid"]) if outer_reservation is not None else None),
        execution_authorized=execution_authorized,
    )


def _atomic_exclusive_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    _assert_path_budget(path)
    _assert_no_reparse_chain(path.parent)
    _assert_no_reparse_chain(path, allow_missing_leaf=True)
    if path.exists():
        raise FileExistsError(path)
    raw = _canonical_json(value)
    temporary = path.parent / f".t-{uuid.uuid4().hex[:8]}.tmp"
    _assert_path_budget(temporary)
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("evidence_write_zero")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _assert_no_reparse_chain(path.parent)
    _assert_no_reparse_chain(path, allow_missing_leaf=True)
    if os.name != "nt":
        raise R7S2QualificationError(f"atomic_no_replace_move_requires_windows:{temporary}")
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
    expected_sha256 = _sha256_bytes(raw)
    if readback != raw or len(readback) != len(raw) or _sha256_bytes(readback) != expected_sha256:
        raise R7S2QualificationError("atomic_publication_readback_mismatch")
    return {"path": str(path), "sha256": expected_sha256, "bytes": len(raw)}


def _make_contract(
    *, wrapper: float, residual: float, stream: float, restore_padding: float = 10
) -> TimeoutContract:
    return TimeoutContract(
        kubectl_timeout_seconds=max(0.01, min(1.0, wrapper / 2)),
        wrapper_timeout_seconds=wrapper,
        restore_deadline_seconds=wrapper + residual + stream + restore_padding,
        residual_repoll_seconds=residual,
        stream_drain_seconds=stream,
    )


def _source_git_environment() -> dict[str, str]:
    return {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "NUL",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "LC_ALL": "C",
    }


def _parse_source_ls_tree(stdout: str) -> dict[str, dict[str, str]]:
    if (
        not isinstance(stdout, str)
        or not stdout.endswith("\0")
        or "\0\0" in stdout
        or any(character in stdout for character in ("\n", "\r", "\ufffd"))
    ):
        raise R7S2QualificationError("runtime_source_ls_tree_stream_invalid")
    rows: dict[str, dict[str, str]] = {}
    for record in stdout[:-1].split("\0"):
        match = re.fullmatch(r"(100644|100755) (blob) ([0-9a-f]{40})\t([^\t\0]+)", record)
        if match is None:
            raise R7S2QualificationError("runtime_source_ls_tree_record_invalid")
        mode, object_type, object_id, path = match.groups()
        if path in rows:
            raise R7S2QualificationError("runtime_source_ls_tree_duplicate_path")
        rows[path] = {"mode": mode, "type": object_type, "oid": object_id}
    return rows


def _parse_source_ls_files(stdout: str) -> dict[str, dict[str, str]]:
    if (
        not isinstance(stdout, str)
        or not stdout.endswith("\0")
        or "\0\0" in stdout
        or any(character in stdout for character in ("\n", "\r", "\ufffd"))
    ):
        raise R7S2QualificationError("runtime_source_ls_files_stream_invalid")
    rows: dict[str, dict[str, str]] = {}
    for record in stdout[:-1].split("\0"):
        match = re.fullmatch(r"(H) (100644|100755) ([0-9a-f]{40}) (0)\t([^\t\0]+)", record)
        if match is None:
            raise R7S2QualificationError("runtime_source_ls_files_record_invalid")
        flag, mode, object_id, stage, path = match.groups()
        if path in rows:
            raise R7S2QualificationError("runtime_source_ls_files_duplicate_path")
        rows[path] = {
            "flag": flag,
            "mode": mode,
            "oid": object_id,
            "stage": stage,
        }
    return rows


def _source_process_outcome(outcome: Any, name: str) -> dict[str, Any]:
    evidence = outcome.to_dict()
    if not (
        evidence.get("safe_for_followup") is True
        and evidence.get("forced_termination_attempts") == 0
        and evidence.get("active_process_zero") is True
        and evidence.get("streams_drained") is True
        and evidence.get("identity_coverage_complete") is True
        and evidence.get("timed_out") is False
        and evidence.get("return_code") == 0
        and evidence.get("residual_pids") == []
    ):
        raise R7S2SourceIdentityError(f"runtime_source_process_unsafe:{name}", evidence)
    stdout = evidence.get("stdout")
    stderr = evidence.get("stderr")
    if (
        not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or stderr != ""
        or "\ufffd" in stdout
        or "\ufffd" in stderr
        or "\r" in stdout
        or "\r" in stderr
    ):
        raise R7S2SourceIdentityError(f"runtime_source_process_stream_invalid:{name}", evidence)
    return evidence


def _runtime_source_identity_readback(contract: QualificationContract) -> dict[str, Any]:
    source = contract.source_identity
    git = _mapping(source["git"], "runtime_source_git")
    git_pin = BinaryPin(
        path=str(git["path"]),
        sha256=str(git["sha256"]),
        bytes=int(git["bytes"]),
        version=str(git["version"]),
    )
    runner = WindowsJobProcessRunner(
        _make_contract(wrapper=5, residual=3, stream=3, restore_padding=5)
    )
    environment = _source_git_environment()
    results: dict[str, Any] = {}

    def run_git(name: str, command: Sequence[str]) -> dict[str, Any]:
        _verify_file_pin(git_pin, "runtime_source_git")
        if _git_config_semantic_readback() != source["git_config"]:
            raise R7S2QualificationError("runtime_source_git_config_changed")
        try:
            outcome = runner.run(
                tuple(command),
                name=f"pre-r8-r7s2-runtime-source-{name.replace('_', '-')}",
                cwd=os.environ.get("SystemRoot", r"C:\Windows"),
                env=environment,
                poll_interval_seconds=0.01,
                run_uuid=contract.run_uuid,
            )
        except Exception as exc:
            typed = getattr(exc, "to_dict", None)
            try:
                evidence = typed() if callable(typed) else {}
            except Exception as evidence_exc:
                evidence = {
                    "evidence_extraction_error": f"{type(evidence_exc).__name__}:{evidence_exc}"
                }
            raise R7S2SourceIdentityError(
                f"runtime_source_process_exception:{name}:{type(exc).__name__}:{exc}", evidence
            ) from exc
        result = _source_process_outcome(outcome, name)
        results[name] = result
        return result

    identity = run_git(
        "git_identity",
        (
            git_pin.path,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(GIT_ROOT),
            "rev-parse",
            "HEAD",
            "HEAD^{tree}",
        ),
    )
    if identity.get("stdout") != f"{source['commit']}\n{source['tree']}\n":
        raise R7S2SourceIdentityError("runtime_source_commit_tree_mismatch", identity)
    status = run_git(
        "git_status",
        (
            git_pin.path,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(GIT_ROOT),
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        ),
    )
    if status.get("stdout") != "":
        raise R7S2SourceIdentityError("runtime_source_tracked_changes_nonzero", status)

    all_sources = {**contract.raw["source_pins"], "stager": source["stager"]}
    relative_paths = [all_sources[role]["relative_path"] for role in sorted(all_sources)]
    common = (git_pin.path, "-c", "core.fsmonitor=false", "-c", "core.autocrlf=true")
    tree_outcome = run_git(
        "git_source_ls_tree",
        (
            *common,
            "-C",
            str(GIT_ROOT),
            "ls-tree",
            "-rz",
            "--full-tree",
            source["commit"],
            "--",
            *relative_paths,
        ),
    )
    index_outcome = run_git(
        "git_source_ls_files",
        (
            *common,
            "-C",
            str(GIT_ROOT),
            "ls-files",
            "-vz",
            "--stage",
            "--",
            *relative_paths,
        ),
    )
    tree = _parse_source_ls_tree(str(tree_outcome["stdout"]))
    index = _parse_source_ls_files(str(index_outcome["stdout"]))
    if set(tree) != set(relative_paths) or set(index) != set(relative_paths):
        raise R7S2SourceIdentityError("runtime_source_path_set_mismatch", results)
    normalized: dict[str, str] = {}
    for role in sorted(all_sources):
        pin = all_sources[role]
        relative = pin["relative_path"]
        if tree[relative] != {
            "mode": pin["git_mode"],
            "type": "blob",
            "oid": pin["git_head_blob_oid"],
        } or index[relative] != {
            "flag": "H",
            "mode": pin["git_mode"],
            "oid": pin["git_head_blob_oid"],
            "stage": "0",
        }:
            raise R7S2SourceIdentityError(
                f"runtime_source_tracked_binding_mismatch:{role}", results
            )
        name = f"git_source_hash_object_{role}"
        result = run_git(
            name,
            (
                *common,
                "-C",
                str(GIT_ROOT),
                "hash-object",
                f"--path={relative}",
                pin["path"],
            ),
        )
        stdout = result["stdout"]
        if not isinstance(stdout, str) or re.fullmatch(r"[0-9a-f]{40}\n", stdout) is None:
            raise R7S2SourceIdentityError(
                f"runtime_source_hash_object_stream_invalid:{role}", result
            )
        normalized[role] = stdout[:-1]
        if normalized[role] != pin["git_normalized_worktree_blob_oid"]:
            raise R7S2SourceIdentityError(f"runtime_source_normalized_blob_mismatch:{role}", result)
    source_call_counts = {
        "git_identity": 1,
        "git_status": 1,
        "git_source_ls_tree": 1,
        "git_source_ls_files": 1,
        **{f"git_source_hash_object_{role}": 1 for role in sorted(all_sources)},
    }
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.runtime-source-readback.v1",
        "commit": source["commit"],
        "tree": source["tree"],
        "processes": results,
        "normalized_worktree_blob_oids": normalized,
        "call_counts": source_call_counts,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
    }


def _runtime_admin_token_readback(contract: QualificationContract) -> dict[str, Any]:
    del contract
    if os.name != "nt":
        raise R7S2QualificationError("windows_token_measurement_requires_windows")

    class SidAndAttributes(ctypes.Structure):
        _fields_ = (("sid", wintypes.LPVOID), ("attributes", wintypes.DWORD))

    class TokenMandatoryLabel(ctypes.Structure):
        _fields_ = (("label", SidAndAttributes),)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ProcessIdToSessionId.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = (wintypes.LPVOID,)
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = (wintypes.LPVOID, wintypes.DWORD)
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    shell32.IsUserAnAdmin.restype = wintypes.BOOL

    token_handle = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token_handle)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        returned = wintypes.DWORD()
        elevation = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token_handle,
            18,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token_handle, 25, None, 0, ctypes.byref(required))
        if required.value <= 0:
            raise R7S2QualificationError("token_integrity_buffer_size_invalid")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token_handle,
            25,
            buffer,
            required.value,
            ctypes.byref(returned),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        label = ctypes.cast(buffer, ctypes.POINTER(TokenMandatoryLabel)).contents
        count_pointer = advapi32.GetSidSubAuthorityCount(label.label.sid)
        if not count_pointer or count_pointer.contents.value < 1:
            raise R7S2QualificationError("token_integrity_sid_invalid")
        rid_pointer = advapi32.GetSidSubAuthority(label.label.sid, count_pointer.contents.value - 1)
        if not rid_pointer:
            raise R7S2QualificationError("token_integrity_rid_missing")
        integrity_rid = int(rid_pointer.contents.value)
    except Exception as exc:
        raise R7S2QualificationError(
            f"administrator_token_required:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if token_handle:
            kernel32.CloseHandle(token_handle)
    session_id = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise R7S2QualificationError(
            f"administrator_token_required:{ctypes.WinError(ctypes.get_last_error())}"
        )
    integrity = (
        "System" if integrity_rid >= 0x4000 else "High" if integrity_rid >= 0x3000 else "Other"
    )
    elevation_name = {1: "Default", 2: "Full", 3: "Limited"}.get(
        int(elevation.value), f"Unknown:{int(elevation.value)}"
    )
    token = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "session_id": int(session_id.value),
        "path": str(Path(sys.executable).resolve()),
        "path_sha256": _sha256_file(Path(sys.executable)),
        "administrator": bool(shell32.IsUserAnAdmin()),
        "integrity": integrity,
        "integrity_rid": integrity_rid,
        "token_elevation_type": elevation_name,
        "token_elevation_type_value": int(elevation.value),
    }
    if not (
        token["administrator"] is True
        and token["integrity"] in {"High", "System"}
        and token["token_elevation_type"] == "Full"
        and token["token_elevation_type_value"] == 2
    ):
        raise R7S2QualificationError(
            "administrator_token_required:"
            f"administrator={token['administrator']}:"
            f"integrity={token['integrity']}:"
            f"token_elevation_type={token['token_elevation_type']}"
        )
    return token


def _validated_runtime_job_containment_observation(
    *,
    current_pid: int,
    is_process_in_job: bool,
    limit_flags: int,
    active_processes: int,
    total_processes: int,
    terminated_processes: int,
    assigned_processes: int,
    listed_pids: Sequence[int],
) -> dict[str, Any]:
    integer_values = {
        "current_pid": current_pid,
        "limit_flags": limit_flags,
        "active_processes": active_processes,
        "total_processes": total_processes,
        "terminated_processes": terminated_processes,
        "assigned_processes": assigned_processes,
    }
    if is_process_in_job is not True:
        raise R7S2QualificationError("outer_kernel_job_membership_required")
    if any(
        isinstance(value, bool) or not isinstance(value, int) for value in integer_values.values()
    ):
        raise R7S2QualificationError("outer_kernel_job_snapshot_type_invalid")
    if (
        current_pid <= 0
        or not isinstance(listed_pids, Sequence)
        or isinstance(listed_pids, (str, bytes))
    ):
        raise R7S2QualificationError("outer_kernel_job_snapshot_type_invalid")
    normalized_pids = list(listed_pids)
    if any(
        isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 for pid in normalized_pids
    ):
        raise R7S2QualificationError("outer_kernel_job_snapshot_type_invalid")
    if len(normalized_pids) != len(set(normalized_pids)):
        raise R7S2QualificationError("outer_kernel_job_pid_list_duplicate")
    forbidden_flags = 0x00000800 | 0x00001000 | 0x00002000
    if (
        limit_flags != 0
        or limit_flags & forbidden_flags
        or active_processes != 1
        or total_processes != 1
        or terminated_processes != 0
        or assigned_processes != 1
        or normalized_pids != [current_pid]
    ):
        raise R7S2QualificationError(
            "outer_kernel_job_not_exclusive_or_forbidden_limits:"
            f"flags={limit_flags}:active={active_processes}:total={total_processes}:"
            f"terminated={terminated_processes}:assigned={assigned_processes}:"
            f"pids={normalized_pids}"
        )
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-job-readback.v1",
        "query_scope": "immediate_job_of_calling_process_null_handle",
        "pid": current_pid,
        "is_process_in_job": True,
        "limit_flags": limit_flags,
        "kill_on_job_close": False,
        "breakaway_ok": False,
        "silent_breakaway_ok": False,
        "active_processes": active_processes,
        "total_processes": total_processes,
        "terminated_processes": terminated_processes,
        "assigned_processes": assigned_processes,
        "process_ids": normalized_pids,
    }


def _runtime_job_containment_readback() -> dict[str, Any]:
    """Prove that this qualifier is the sole process in its immediate kernel Job."""

    if os.name != "nt":
        raise R7S2QualificationError("windows_job_containment_requires_windows")

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = (
            ("per_process_user_time_limit", ctypes.c_longlong),
            ("per_job_user_time_limit", ctypes.c_longlong),
            ("limit_flags", wintypes.DWORD),
            ("minimum_working_set_size", ctypes.c_size_t),
            ("maximum_working_set_size", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        )

    class IoCounters(ctypes.Structure):
        _fields_ = (
            ("read_operation_count", ctypes.c_ulonglong),
            ("write_operation_count", ctypes.c_ulonglong),
            ("other_operation_count", ctypes.c_ulonglong),
            ("read_transfer_count", ctypes.c_ulonglong),
            ("write_transfer_count", ctypes.c_ulonglong),
            ("other_transfer_count", ctypes.c_ulonglong),
        )

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = (
            ("basic_limit_information", BasicLimitInformation),
            ("io_info", IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory_used", ctypes.c_size_t),
            ("peak_job_memory_used", ctypes.c_size_t),
        )

    class BasicAccountingInformation(ctypes.Structure):
        _fields_ = (
            ("total_user_time", ctypes.c_longlong),
            ("total_kernel_time", ctypes.c_longlong),
            ("this_period_total_user_time", ctypes.c_longlong),
            ("this_period_total_kernel_time", ctypes.c_longlong),
            ("total_page_fault_count", wintypes.DWORD),
            ("total_processes", wintypes.DWORD),
            ("active_processes", wintypes.DWORD),
            ("total_terminated_processes", wintypes.DWORD),
        )

    class BasicProcessIdList(ctypes.Structure):
        _fields_ = (
            ("number_of_assigned_processes", wintypes.DWORD),
            ("number_of_process_ids_in_list", wintypes.DWORD),
            ("process_id_list", ctypes.c_size_t * 64),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    )
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL

    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise R7S2QualificationError(
            f"outer_kernel_job_membership_query_failed:{ctypes.get_last_error()}"
        )

    def query(information_class: int, value: Any) -> int:
        returned = wintypes.DWORD()
        if not kernel32.QueryInformationJobObject(
            None,
            information_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
            ctypes.byref(returned),
        ):
            raise R7S2QualificationError(
                f"outer_kernel_job_query_failed:{information_class}:{ctypes.get_last_error()}"
            )
        if returned.value <= 0 or returned.value > ctypes.sizeof(value):
            raise R7S2QualificationError(
                f"outer_kernel_job_query_size_invalid:{information_class}:{returned.value}"
            )
        return int(returned.value)

    limits = ExtendedLimitInformation()
    accounting = BasicAccountingInformation()
    process_ids = BasicProcessIdList()
    limits_bytes = query(9, limits)
    accounting_bytes = query(1, accounting)
    process_ids_bytes = query(3, process_ids)
    if limits_bytes != ctypes.sizeof(limits) or accounting_bytes != ctypes.sizeof(accounting):
        raise R7S2QualificationError("outer_kernel_job_fixed_query_size_mismatch")

    current_pid = os.getpid()
    listed_count = int(process_ids.number_of_process_ids_in_list)
    if listed_count > len(process_ids.process_id_list):
        raise R7S2QualificationError("outer_kernel_job_pid_list_truncated")
    required_pid_bytes = BasicProcessIdList.process_id_list.offset + listed_count * ctypes.sizeof(
        ctypes.c_size_t
    )
    if process_ids_bytes != required_pid_bytes:
        raise R7S2QualificationError("outer_kernel_job_pid_query_size_mismatch")
    listed_pids = sorted(int(process_ids.process_id_list[index]) for index in range(listed_count))
    limit_flags = int(limits.basic_limit_information.limit_flags)
    return _validated_runtime_job_containment_observation(
        current_pid=current_pid,
        is_process_in_job=bool(in_job.value),
        limit_flags=limit_flags,
        active_processes=int(accounting.active_processes),
        total_processes=int(accounting.total_processes),
        terminated_processes=int(accounting.total_terminated_processes),
        assigned_processes=int(process_ids.number_of_assigned_processes),
        listed_pids=listed_pids,
    )


def _runtime_ubuntu_running_readback(contract: QualificationContract) -> dict[str, Any]:
    runner = WindowsJobProcessRunner(
        _make_contract(wrapper=5, residual=3, stream=3, restore_padding=5)
    )
    wsl = contract.host_binaries["system32_wsl"]
    environment = _wsl_windows_environment(contract)
    commands = {
        "ubuntu_verbose_gate": (wsl.path, "--list", "--verbose"),
        "ubuntu_running_gate": (wsl.path, "--list", "--running", "--quiet"),
    }
    processes: dict[str, Any] = {}
    for name, command in commands.items():
        _verify_file_pin(wsl, "runtime_system32_wsl")
        try:
            outcome = runner.run(
                command,
                name=f"pre-r8-r7s2-runtime-{name.replace('_', '-')}",
                cwd=os.environ.get("SystemRoot", r"C:\Windows"),
                env=environment,
                poll_interval_seconds=0.01,
                run_uuid=contract.run_uuid,
            )
        except Exception as exc:
            typed = getattr(exc, "to_dict", None)
            evidence = typed() if callable(typed) else {}
            raise R7S2SourceIdentityError(
                f"runtime_ubuntu_state_process_exception:{name}:{type(exc).__name__}:{exc}",
                evidence if isinstance(evidence, Mapping) else {},
            ) from exc
        processes[name] = _source_process_outcome(outcome, name)
    try:
        verbose_stdout, _ = _strict_process_streams(
            processes["ubuntu_verbose_gate"], "runtime_ubuntu_verbose"
        )
        running_stdout, _ = _strict_process_streams(
            processes["ubuntu_running_gate"], "runtime_ubuntu_running"
        )
        _require_ubuntu_verbose_running(verbose_stdout, "runtime_ubuntu_verbose")
        _require_ubuntu_running_membership(running_stdout, "runtime_ubuntu_running")
    except R7S2QualificationError as exc:
        raise R7S2SourceIdentityError(str(exc), processes) from exc
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.runtime-ubuntu-running-readback.v1",
        "state": {"distribution": "Ubuntu", "state": "Running", "version": 2},
        "processes": processes,
        "call_counts": {"ubuntu_verbose_gate": 1, "ubuntu_running_gate": 1},
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "wsl_shutdown_calls": 0,
    }


def _wsl_windows_environment(contract: QualificationContract) -> dict[str, str]:
    system32 = Path(contract.host_binaries["system32_wsl"].path).parent
    _assert_no_reparse_chain(system32)
    if system32.name.casefold() != "system32":
        raise R7S2QualificationError("system32_wsl_parent_mismatch")
    windows_root = str(system32.parent.resolve())
    return {"SystemRoot": windows_root, "WINDIR": windows_root, "WSL_UTF8": "1"}


def _linux_readback_command(contract: QualificationContract) -> tuple[str, ...]:
    linux = contract.linux_binaries
    return (
        contract.host_binaries["system32_wsl"].path,
        "--distribution",
        contract.distribution,
        "--cd",
        "/",
        "--exec",
        linux["env"].path,
        "-i",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        linux["python3"].path,
        "-I",
        "-S",
        "-B",
        "-c",
        LINUX_TOOLCHAIN_SOURCE,
        contract.distribution,
        linux["python3"].path,
        linux["env"].path,
        linux["setsid"].path,
    )


def _scan_command(
    contract: QualificationContract,
    protocol: WslResidualProtocol,
    *,
    expected_pgrp: int | None = None,
    expected_start_time_ticks: int | None = None,
    expected_boot_id: str | None = None,
) -> tuple[str, ...]:
    linux = contract.linux_binaries
    return (
        contract.host_binaries["system32_wsl"].path,
        "--distribution",
        contract.distribution,
        "--cd",
        "/",
        "--exec",
        linux["env"].path,
        "-i",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        linux["python3"].path,
        "-I",
        "-S",
        "-B",
        "-c",
        QUALIFICATION_SCANNER_SOURCE,
        protocol.run_uuid,
        "" if expected_pgrp is None else str(expected_pgrp),
        "" if expected_start_time_ticks is None else str(expected_start_time_ticks),
        "" if expected_boot_id is None else expected_boot_id,
    )


def _launch_command(
    contract: QualificationContract, protocol: WslResidualProtocol
) -> tuple[str, ...]:
    linux = contract.linux_binaries
    return (
        contract.host_binaries["system32_wsl"].path,
        "--distribution",
        contract.distribution,
        "--cd",
        "/",
        "--exec",
        linux["env"].path,
        "-i",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        f"EVM_PHASE_B2_RUN_UUID={protocol.run_uuid}",
        linux["setsid"].path,
        "--fork",
        "--wait",
        linux["python3"].path,
        "-I",
        "-S",
        "-B",
        "-c",
        DETACHED_DESCENDANT_SOURCE,
        str(contract.raw["fixture"]["lifetime_seconds"]),
    )


def _parse_json_object(payload: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, str):
        raise R7S2QualificationError(f"{label}_text_required")
    return _mapping(
        _strict_json_bytes(
            payload.encode("utf-8"),
            label,
            canonical_owned=True,
            terminal_newline=True,
        ),
        label,
    )


def _validate_linux_readback(
    contract: QualificationContract, outcome: Mapping[str, Any]
) -> dict[str, Any]:
    if not outcome.get("safe_for_followup") or outcome.get("forced_termination_attempts") != 0:
        raise R7S2QualificationError("linux_toolchain_readback_process_failed")
    payload = _parse_json_object(str(outcome.get("stdout", "")), "linux_readback")
    _exact_keys(
        payload,
        {
            "schema",
            "status",
            "distro",
            "kernel_release",
            "python_version",
            "boot_id",
            "distro_version",
            "rootfs_identity",
            "os_release_sha256",
            "machine_id_sha256",
            "binaries",
        },
        "linux_readback",
    )
    if payload["schema"] != LINUX_READBACK_SCHEMA or payload["status"] != "observed":
        raise R7S2QualificationError("linux_readback_schema_or_status_mismatch")
    if (
        payload["distro"] != contract.distribution
        or payload["kernel_release"] != contract.platform_identity["kernel_release"]
        or payload["python_version"] != contract.linux_binaries["python3"].version
        or payload["distro_version"] != contract.platform_identity["distro_version"]
        or payload["rootfs_identity"] != contract.platform_identity["rootfs_identity"]
        or payload["os_release_sha256"] != contract.platform_identity["os_release_sha256"]
        or payload["machine_id_sha256"] != contract.platform_identity["machine_id_sha256"]
        or payload["boot_id"] != contract.platform_identity["boot_id"]
    ):
        raise R7S2QualificationError("linux_platform_identity_mismatch")
    try:
        uuid.UUID(str(payload["boot_id"]))
    except ValueError as exc:
        raise R7S2QualificationError("linux_boot_id_invalid") from exc
    binaries = _mapping(payload["binaries"], "linux_readback_binaries")
    _exact_keys(binaries, LINUX_BINARY_ROLES, "linux_readback_binaries")
    for role in sorted(LINUX_BINARY_ROLES):
        observed = _mapping(binaries[role], f"linux_readback_{role}")
        _exact_keys(observed, {"path", "realpath", "sha256", "bytes"}, f"linux_readback_{role}")
        expected = contract.linux_binaries[role]
        if (
            observed["path"] != expected.path
            or observed["realpath"] != expected.path
            or observed["sha256"] != expected.sha256
            or observed["bytes"] != expected.bytes
        ):
            raise R7S2QualificationError(f"linux_{role}_identity_mismatch")
    return payload


def _parse_scan(outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not outcome.get("safe_for_followup") or outcome.get("forced_termination_attempts") != 0:
        raise R7S2QualificationError("observer_scan_process_failed")
    rows = _strict_json_bytes(
        str(outcome.get("stdout", "")).encode("utf-8"),
        "observer_scan",
        canonical_owned=True,
        terminal_newline=True,
    )
    if not isinstance(rows, list):
        raise R7S2QualificationError("observer_scan_list_required")
    expected = {
        "pid",
        "ppid",
        "pgrp",
        "session",
        "start_time_ticks",
        "boot_id",
        "run_uuid_match",
        "process_group_match",
        "auxiliary_read_status",
        "unreadable_fields",
        "cmdline_sha256",
        "open_fd_count",
        "stdio_fds_present",
    }
    records: list[dict[str, Any]] = []
    for row in rows:
        record = _mapping(row, "observer_scan_record")
        _exact_keys(record, expected, "observer_scan_record")
        for key in ("pid", "pgrp", "session", "start_time_ticks"):
            _positive_int(record[key], f"observer_scan_{key}")
        if (
            isinstance(record["ppid"], bool)
            or not isinstance(record["ppid"], int)
            or record["ppid"] < 0
        ):
            raise R7S2QualificationError("observer_scan_ppid_invalid")
        if not isinstance(record["run_uuid_match"], bool) or not isinstance(
            record["process_group_match"], bool
        ):
            raise R7S2QualificationError("observer_scan_match_flags_invalid")
        try:
            uuid.UUID(str(record["boot_id"]))
        except ValueError as exc:
            raise R7S2QualificationError("observer_scan_boot_id_invalid") from exc
        unreadable_fields = record["unreadable_fields"]
        if (
            not isinstance(unreadable_fields, list)
            or unreadable_fields != sorted(set(unreadable_fields))
            or any(item not in {"environ", "cmdline", "fd"} for item in unreadable_fields)
        ):
            raise R7S2QualificationError("observer_scan_unreadable_fields_invalid")
        if record["auxiliary_read_status"] == "complete":
            if unreadable_fields:
                raise R7S2QualificationError("observer_scan_complete_with_unreadable_fields")
            _hex64(record["cmdline_sha256"], "observer_scan_cmdline_sha256")
            if (
                isinstance(record["open_fd_count"], bool)
                or not isinstance(record["open_fd_count"], int)
                or record["open_fd_count"] < 0
                or not isinstance(record["stdio_fds_present"], list)
                or any(item not in (0, 1, 2) for item in record["stdio_fds_present"])
                or record["stdio_fds_present"] != sorted(set(record["stdio_fds_present"]))
            ):
                raise R7S2QualificationError("observer_scan_fd_evidence_invalid")
        elif record["auxiliary_read_status"] == "unreadable_residual":
            if not record["process_group_match"] or not unreadable_fields:
                raise R7S2QualificationError("observer_scan_unreadable_residual_unbound")
            if (
                ("cmdline" in unreadable_fields) != (record["cmdline_sha256"] is None)
                or ("fd" in unreadable_fields) != (record["open_fd_count"] is None)
                or ("fd" in unreadable_fields) != (record["stdio_fds_present"] is None)
            ):
                raise R7S2QualificationError("observer_scan_unreadable_evidence_inconsistent")
            if "cmdline" not in unreadable_fields:
                _hex64(record["cmdline_sha256"], "observer_scan_cmdline_sha256")
        else:
            raise R7S2QualificationError("observer_scan_auxiliary_status_invalid")
        records.append(record)
    return records


def _parse_fixture_ack(payload: str, contract: QualificationContract) -> dict[str, Any]:
    ack = _parse_json_object(payload, "fixture_ack")
    _exact_keys(
        ack,
        {
            "schema",
            "run_uuid",
            "pid",
            "ppid",
            "pgrp",
            "session",
            "start_time_ticks",
            "boot_id",
            "stdio_detach",
        },
        "fixture_ack",
    )
    if (
        ack["schema"] != FIXTURE_ACK_SCHEMA
        or ack["run_uuid"] != contract.run_uuid
        or ack["boot_id"] != contract.platform_identity["boot_id"]
        or ack["stdio_detach"] != "close_all_inherited_fds_after_ack"
    ):
        raise R7S2QualificationError("fixture_ack_identity_mismatch")
    for key in ("pid", "pgrp", "session", "start_time_ticks"):
        _positive_int(ack[key], f"fixture_ack_{key}")
    if isinstance(ack["ppid"], bool) or not isinstance(ack["ppid"], int) or ack["ppid"] < 0:
        raise R7S2QualificationError("fixture_ack_ppid_invalid")
    return ack


def analyse_observation(
    contract: QualificationContract,
    *,
    linux_readback: Mapping[str, Any],
    initial_scan: Mapping[str, Any],
    launch: Mapping[str, Any],
    observer_scans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        initial_records = _parse_scan(initial_scan)
    except (R7S2QualificationError, ValueError, json.JSONDecodeError) as exc:
        initial_records = []
        errors.append(str(exc))
    if initial_records:
        errors.append("initial_uuid_residual_nonzero")
    if (
        not launch.get("safe_for_followup")
        or launch.get("forced_termination_attempts") != 0
        or not launch.get("active_process_zero")
        or not launch.get("streams_drained")
        or not launch.get("identity_coverage_complete")
    ):
        errors.append("launch_containment_gate_failed")
    try:
        ack = _parse_fixture_ack(str(launch.get("stdout", "")), contract)
    except R7S2QualificationError as exc:
        errors.append(str(exc))
        ack = {}

    identities = launch.get("identities")
    events = launch.get("events")
    accounting = launch.get("accounting")
    if (
        not isinstance(identities, list)
        or not isinstance(events, list)
        or not isinstance(accounting, list)
    ):
        errors.append("full_job_event_accounting_missing")
        identities = []
        events = []
        accounting = []
    by_pid = {
        int(item["pid"]): item
        for item in identities
        if isinstance(item, Mapping) and isinstance(item.get("pid"), int)
    }
    if len(by_pid) != len(identities):
        errors.append("pid_reuse_or_duplicate_identity_ambiguous")
    launcher_paths = {
        role: os.path.normcase(os.path.abspath(contract.host_binaries[role].path))
        for role in ("system32_wsl", "store_wsl")
    }
    launcher_pids_by_role = {
        role: {
            pid
            for pid, item in by_pid.items()
            if os.path.normcase(os.path.abspath(str(item.get("image", "")))) == expected_path
        }
        for role, expected_path in launcher_paths.items()
    }
    launcher_pids = set().union(*launcher_pids_by_role.values())
    if any(not pids for pids in launcher_pids_by_role.values()):
        errors.append("launcher_identity_coverage_incomplete")
    wslhost_path = os.path.normcase(os.path.abspath(contract.host_binaries["wslhost"].path))
    wslhost_pids = {
        pid
        for pid, item in by_pid.items()
        if os.path.normcase(os.path.abspath(str(item.get("image", "")))) == wslhost_path
    }
    if not wslhost_pids:
        errors.append("contained_wslhost_identity_missing")

    exit_by_pid: dict[int, int] = {}
    active_zero_times: list[int] = []
    for item in events:
        if not isinstance(item, Mapping):
            continue
        event = item.get("event")
        pid = item.get("pid")
        timestamp = item.get("monotonic_ns")
        if not isinstance(timestamp, int):
            continue
        if event in {"job_exit_process", "job_abnormal_exit_process"} and isinstance(pid, int):
            exit_by_pid[pid] = timestamp
        if event in {"job_active_process_zero", "active_process_count_zero"}:
            active_zero_times.append(timestamp)
    if launcher_pids - set(exit_by_pid):
        errors.append("launcher_exit_event_missing")
    latest_launcher_exit = max(
        (exit_by_pid[pid] for pid in launcher_pids if pid in exit_by_pid),
        default=None,
    )
    first_active_zero = min(active_zero_times, default=None)

    parsed_scans: list[dict[str, Any]] = []
    for row in observer_scans:
        try:
            records = _parse_scan(_mapping(row.get("outcome"), "observer_outcome"))
        except (R7S2QualificationError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            records = []
        parsed_scans.append({**dict(row), "records": records})
        if any(record.get("auxiliary_read_status") == "unreadable_residual" for record in records):
            errors.append("unreadable_group_residual_observed")

    candidate: dict[str, Any] | None = None
    for row in parsed_scans:
        start = row.get("started_monotonic_ns")
        end = row.get("ended_monotonic_ns")
        records = row["records"]
        if (
            latest_launcher_exit is None
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start <= latest_launcher_exit
            or not records
        ):
            continue
        matching_ack = [
            item
            for item in records
            if item.get("run_uuid_match") is True
            and (not ack or item.get("pid") == ack.get("pid"))
            and (not ack or item.get("start_time_ticks") == ack.get("start_time_ticks"))
            and (not ack or item.get("pgrp") == ack.get("pgrp"))
            and (not ack or item.get("session") == ack.get("session"))
            and item.get("boot_id") == linux_readback.get("boot_id")
            and item.get("open_fd_count") == 0
            and item.get("stdio_fds_present") == []
        ]
        active_wslhost = any(
            isinstance(snapshot, Mapping)
            and isinstance(snapshot.get("monotonic_ns"), int)
            and start <= snapshot["monotonic_ns"] <= end
            and bool(wslhost_pids & set(snapshot.get("active_pids", ())))
            and int(snapshot.get("active_processes", 0)) > 0
            for snapshot in accounting
        )
        if matching_ack and active_wslhost:
            candidate = {**row, "matching_records": matching_ack}
            break
    if candidate is None:
        errors.append("launcher_exit_linux_residual_overlap_unproven")

    if candidate is not None and first_active_zero is not None:
        if first_active_zero <= int(candidate["ended_monotonic_ns"]):
            errors.append("job_active_zero_preceded_live_residual_observation")
    elif first_active_zero is None:
        errors.append("job_active_zero_event_missing")

    final_zero = next(
        (
            row
            for row in parsed_scans
            if first_active_zero is not None
            and isinstance(row.get("started_monotonic_ns"), int)
            and row["started_monotonic_ns"] >= first_active_zero
            and row.get("query")
            == {
                "run_uuid": contract.run_uuid,
                "expected_pgrp": ack.get("pgrp"),
                "expected_start_time_ticks": ack.get("start_time_ticks"),
                "expected_boot_id": linux_readback.get("boot_id"),
                "match_policy": "run_uuid_or_ack_process_group",
            }
            and not row["records"]
        ),
        None,
    )
    if final_zero is None:
        errors.append("post_job_uuid_zero_scan_missing")

    forced_attempts = (
        int(launch.get("forced_termination_attempts", 0))
        + int(initial_scan.get("forced_termination_attempts", 0))
        + sum(
            int(
                _mapping(row.get("outcome"), "observer_outcome").get(
                    "forced_termination_attempts", 0
                )
            )
            for row in observer_scans
        )
    )
    if forced_attempts != 0:
        errors.append("forced_termination_attempts_nonzero")

    return {
        "passed": not errors,
        "classification": "qualified_non_credit" if not errors else "zero_credit_failure",
        "manual_intervention_required": bool(errors),
        "errors": errors,
        "fixture_ack": ack,
        "launcher_pids": sorted(launcher_pids),
        "launcher_pids_by_role": {
            role: sorted(pids) for role, pids in launcher_pids_by_role.items()
        },
        "launcher_exit_monotonic_ns": latest_launcher_exit,
        "wslhost_pids": sorted(wslhost_pids),
        "job_active_zero_monotonic_ns": first_active_zero,
        "live_overlap_scan_sequence": candidate.get("sequence") if candidate else None,
        "post_job_zero_scan_sequence": final_zero.get("sequence") if final_zero else None,
        "observer_scan_count": len(observer_scans),
        "adversary_launch_count": 1,
        "automatic_retry_count": 0,
        "forced_termination_attempts": forced_attempts,
        "runtime_probe_calls": 0,
        "lifecycle_calls": 0,
        "completion_credit": "non_credit_only",
    }


class ConcurrentQualification:
    def __init__(
        self,
        contract: QualificationContract,
        *,
        launch_runner: Any | None = None,
        scan_runner: Any | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.contract = contract
        self.child_environment = _wsl_windows_environment(contract)
        timeouts = contract.timeouts
        self.launch_runner = launch_runner or WindowsJobProcessRunner(
            _make_contract(
                wrapper=float(timeouts["launch_wrapper_seconds"]),
                residual=float(timeouts["launch_residual_seconds"]),
                stream=float(timeouts["stream_drain_seconds"]),
            )
        )
        self.scan_runner = scan_runner or WindowsJobProcessRunner(
            _make_contract(
                wrapper=float(timeouts["scan_wrapper_seconds"]),
                residual=float(timeouts["scan_residual_seconds"]),
                stream=float(timeouts["stream_drain_seconds"]),
            )
        )
        self.clock_ns = clock_ns
        self.monotonic = monotonic
        self.call_counts = {
            "git_identity": 0,
            "git_status": 0,
            "git_source_ls_tree": 0,
            "git_source_ls_files": 0,
            **{f"git_source_hash_object_{role}": 0 for role in sorted(SOURCE_ROLES | {"stager"})},
            "ubuntu_verbose_gate": 0,
            "ubuntu_running_gate": 0,
            "linux_toolchain_readback": 0,
            "initial_scan": 0,
            "observer_scans": 0,
            "adversary_launches": 0,
            "post_launch_zero_scans": 0,
        }
        self.partial_evidence: dict[str, Any] = {
            "source_identity_readback": None,
            "administrator_token_evidence": None,
            "ubuntu_running_readback": None,
            "linux_toolchain_process": None,
            "linux_toolchain_readback": None,
            "initial_scan": None,
            "launch": None,
            "observer_scans": [],
            "call_counts": self.call_counts,
            "ambient_environment_policy": {
                "windows_inherited_environment": False,
                "windows_child_keys": sorted(self.child_environment),
                "wslenv_present": False,
                "linux_env_i": True,
                "linux_locale": "C.UTF-8",
                "exact_distribution": contract.distribution,
                "exact_working_directory": "/",
                "runner_injected_run_uuid": True,
                "wsl_registration_kernel_rootfs_tcb": True,
            },
            "process_failures": [],
        }

    def _record_process_failure(
        self, *, name: str, sequence: int | None, started: int, exc: Exception
    ) -> None:
        typed = getattr(exc, "to_dict", None)
        try:
            evidence = typed() if callable(typed) else None
        except Exception as evidence_exc:
            evidence = {
                "evidence_extraction_error": f"{type(evidence_exc).__name__}:{evidence_exc}"
            }
        self.partial_evidence["process_failures"].append(
            {
                "name": name,
                "sequence": sequence,
                "started_monotonic_ns": started,
                "ended_monotonic_ns": self.clock_ns(),
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "typed_evidence": evidence if isinstance(evidence, Mapping) else None,
            }
        )

    def _run_scan(
        self,
        command: Sequence[str],
        *,
        name: str,
        sequence: int,
        counter: str,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.call_counts[counter] += 1
        started = self.clock_ns()
        try:
            outcome = self.scan_runner.run(
                tuple(command),
                name=name,
                env=self.child_environment,
                poll_interval_seconds=0.01,
                run_uuid=self.contract.run_uuid,
            )
        except Exception as exc:
            self._record_process_failure(name=name, sequence=sequence, started=started, exc=exc)
            raise
        ended = self.clock_ns()
        return {
            "sequence": sequence,
            "started_monotonic_ns": started,
            "ended_monotonic_ns": ended,
            "query": dict(
                query or {"match_policy": "run_uuid_only", "run_uuid": self.contract.run_uuid}
            ),
            "outcome": outcome.to_dict(),
        }

    def run(self) -> dict[str, Any]:
        contract = self.contract
        protocol = WslResidualProtocol(contract.run_uuid)
        scan_command = _scan_command(contract, protocol)
        self.call_counts["linux_toolchain_readback"] += 1
        linux_started = self.clock_ns()
        try:
            linux_outcome = self.scan_runner.run(
                _linux_readback_command(contract),
                name="pre-r8-r7s2-linux-toolchain-readback",
                env=self.child_environment,
                poll_interval_seconds=0.01,
                run_uuid=contract.run_uuid,
            )
        except Exception as exc:
            self._record_process_failure(
                name="pre-r8-r7s2-linux-toolchain-readback",
                sequence=None,
                started=linux_started,
                exc=exc,
            )
            raise
        linux_outcome_dict = linux_outcome.to_dict()
        self.partial_evidence["linux_toolchain_process"] = linux_outcome_dict
        linux_readback = _validate_linux_readback(contract, linux_outcome_dict)
        self.partial_evidence["linux_toolchain_readback"] = linux_readback

        initial_row = self._run_scan(
            scan_command,
            name="pre-r8-r7s2-initial-uuid-scan",
            sequence=0,
            counter="initial_scan",
        )
        self.partial_evidence["initial_scan"] = initial_row
        if _parse_scan(initial_row["outcome"]):
            raise R7S2QualificationError("initial_uuid_residual_nonzero")

        launch_done = threading.Event()
        observer_ready = threading.Event()
        scan_start_gate = threading.Lock()
        observer_rows: list[dict[str, Any]] = []
        self.partial_evidence["observer_scans"] = observer_rows
        observer_errors: list[str] = []
        observer_deadline = self.monotonic() + float(contract.timeouts["observer_deadline_seconds"])

        def observe() -> None:
            observer_ready.set()
            sequence = 1
            while (
                sequence <= int(contract.timeouts["observer_max_scans"])
                and self.monotonic() < observer_deadline
            ):
                try:
                    with scan_start_gate:
                        if launch_done.is_set():
                            return
                        row = self._run_scan(
                            scan_command,
                            name=f"pre-r8-r7s2-concurrent-proc-observer-{sequence:03d}",
                            sequence=sequence,
                            counter="observer_scans",
                        )
                    _parse_scan(row["outcome"])
                    observer_rows.append(row)
                except Exception as exc:
                    observer_errors.append(f"observer_failed:{type(exc).__name__}:{exc}")
                    return
                if launch_done.is_set():
                    return
                sequence += 1
                launch_done.wait(float(contract.timeouts["observer_interval_seconds"]))
            if not launch_done.is_set():
                observer_errors.append("observer_deadline_or_scan_bound_exhausted")

        observer = threading.Thread(target=observe, name="pre-r8-r7s2-proc-observer")
        observer.start()
        if not observer_ready.wait(timeout=1):
            launch_done.set()
            observer.join()
            raise R7S2QualificationError("observer_thread_not_ready")
        launch = None
        launch_error: Exception | None = None
        self.call_counts["adversary_launches"] += 1
        launch_started = self.clock_ns()
        try:
            launch = self.launch_runner.run(
                _launch_command(contract, protocol),
                name="pre-r8-r7s2-adversary-launch-exactly-once",
                env=self.child_environment,
                poll_interval_seconds=0.01,
                run_uuid=contract.run_uuid,
            )
            self.partial_evidence["launch"] = launch.to_dict()
        except Exception as exc:
            self._record_process_failure(
                name="pre-r8-r7s2-adversary-launch-exactly-once",
                sequence=None,
                started=launch_started,
                exc=exc,
            )
            launch_error = exc
        finally:
            with scan_start_gate:
                launch_done.set()
        observer.join()
        if launch_error is not None:
            raise launch_error
        if launch is None:
            raise R7S2QualificationError("launch_outcome_missing")
        if observer_errors:
            raise R7S2QualificationError(";".join(observer_errors))

        launch_dict = launch.to_dict()
        if launch_dict.get("safe_for_followup"):
            ack = _parse_fixture_ack(str(launch_dict.get("stdout", "")), contract)
            if ack["boot_id"] != linux_readback.get("boot_id"):
                raise R7S2QualificationError("fixture_ack_runtime_boot_id_mismatch")
            final_query = {
                "run_uuid": contract.run_uuid,
                "expected_pgrp": ack["pgrp"],
                "expected_start_time_ticks": ack["start_time_ticks"],
                "expected_boot_id": ack["boot_id"],
                "match_policy": "run_uuid_or_ack_process_group",
            }
            final_row = self._run_scan(
                _scan_command(
                    contract,
                    protocol,
                    expected_pgrp=ack["pgrp"],
                    expected_start_time_ticks=ack["start_time_ticks"],
                    expected_boot_id=ack["boot_id"],
                ),
                name="pre-r8-r7s2-post-launch-uuid-pgrp-zero-scan",
                sequence=len(observer_rows) + 1,
                counter="post_launch_zero_scans",
                query=final_query,
            )
            observer_rows.append(final_row)
        analysis = analyse_observation(
            contract,
            linux_readback=linux_readback,
            initial_scan=initial_row["outcome"],
            launch=launch_dict,
            observer_scans=observer_rows,
        )
        return {
            "schema": EVIDENCE_SCHEMA,
            "qualification_id": contract.qualification_id,
            "run_uuid": contract.run_uuid,
            "attempt_id": contract.attempt_id,
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "contract": contract.raw,
            "linux_toolchain_process": linux_outcome.to_dict(),
            "linux_toolchain_readback": linux_readback,
            "initial_scan": initial_row,
            "launch": launch_dict,
            "observer_scans": observer_rows,
            "analysis": analysis,
            "invocation_policy": dict(INVOCATION_POLICY),
            "call_counts": dict(self.call_counts),
            "ambient_environment_policy": {
                "windows_inherited_environment": False,
                "windows_child_keys": sorted(self.child_environment),
                "wslenv_present": False,
                "linux_env_i": True,
                "linux_locale": "C.UTF-8",
                "exact_distribution": contract.distribution,
                "exact_working_directory": "/",
                "runner_injected_run_uuid": True,
                "wsl_registration_kernel_rootfs_tcb": True,
            },
            "publication": {
                "completion_marker_created": False,
                "private_success_index_created": False,
                "phase_b2_credit": False,
                "r8_started": False,
            },
        }


def _partial_process_summary(partial: Mapping[str, Any]) -> dict[str, Any]:
    outcomes: list[Mapping[str, Any]] = []
    for key in ("linux_toolchain_process", "launch"):
        value = partial.get(key)
        if isinstance(value, Mapping):
            outcomes.append(value)
    initial = partial.get("initial_scan")
    if isinstance(initial, Mapping) and isinstance(initial.get("outcome"), Mapping):
        outcomes.append(initial["outcome"])
    observer_scans = partial.get("observer_scans")
    if isinstance(observer_scans, Sequence) and not isinstance(
        observer_scans, (str, bytes, bytearray)
    ):
        for row in observer_scans:
            if isinstance(row, Mapping) and isinstance(row.get("outcome"), Mapping):
                outcomes.append(row["outcome"])
    process_failures = partial.get("process_failures")
    if isinstance(process_failures, Sequence) and not isinstance(
        process_failures, (str, bytes, bytearray)
    ):
        for failure in process_failures:
            if isinstance(failure, Mapping) and isinstance(failure.get("typed_evidence"), Mapping):
                outcomes.append(failure["typed_evidence"])
    residual_pids = sorted(
        {
            pid
            for outcome in outcomes
            for pid in outcome.get("residual_pids", ())
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        }
    )
    return {
        "process_outcome_count": len(outcomes),
        "residual_pids": residual_pids,
        "manual_latch_observed": any(
            bool(outcome.get("manual_intervention_required")) for outcome in outcomes
        ),
        "unsafe_for_followup_observed": any(
            outcome.get("safe_for_followup") is False for outcome in outcomes
        ),
        "forced_termination_attempts": sum(
            int(outcome.get("forced_termination_attempts", 0)) for outcome in outcomes
        ),
    }


def _failure_seal_payload(
    contract: QualificationContract,
    *,
    reservation_pin: Mapping[str, Any],
    qualification: ConcurrentQualification,
    failed_stage: str,
    exception: Exception | None,
    failure_evidence: Mapping[str, Any] | None,
    include_partial_evidence: bool,
) -> dict[str, Any]:
    summary = _partial_process_summary(qualification.partial_evidence)
    return {
        "schema": FAILURE_SCHEMA,
        "qualification_id": contract.qualification_id,
        "run_uuid": contract.run_uuid,
        "attempt_id": contract.attempt_id,
        "sealed_at_utc": datetime.now(UTC).isoformat(),
        "status": "manual_intervention_required",
        "credit": "zero_credit",
        "failed_stage": failed_stage,
        "exception_type": type(exception).__name__ if exception is not None else None,
        "exception": str(exception) if exception is not None else None,
        "failure_evidence": dict(failure_evidence) if failure_evidence is not None else None,
        "partial_evidence": qualification.partial_evidence if include_partial_evidence else None,
        "partial_process_summary": summary,
        "reservation": dict(reservation_pin),
        "call_counts": dict(qualification.call_counts),
        "automatic_retry_count": 0,
        "forced_termination_attempts": summary["forced_termination_attempts"],
        "runtime_probe_calls": 0,
        "lifecycle_calls": 0,
        "completion_marker_created": False,
        "r8_started": False,
    }


def _index_payload(
    contract: QualificationContract,
    *,
    reservation_pin: Mapping[str, Any],
    report_pin: Mapping[str, Any],
    failure_seal_pin: Mapping[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    return {
        "schema": INDEX_SCHEMA,
        "qualification_id": contract.qualification_id,
        "run_uuid": contract.run_uuid,
        "attempt_id": contract.attempt_id,
        "indexed_at_utc": datetime.now(UTC).isoformat(),
        "status": status,
        "credit": "non_credit_only" if status == "qualified_non_credit" else "zero_credit",
        "reservation": dict(reservation_pin),
        "report": dict(report_pin),
        "failure_seal": dict(failure_seal_pin) if failure_seal_pin is not None else None,
        "completion_marker_created": False,
        "private_phase_b2_success_index_created": False,
        "r8_started": False,
    }


def _primary_partial_inventory(run_directory: Path) -> list[dict[str, Any]]:
    _assert_no_reparse_chain(run_directory, allow_missing_leaf=True)
    if not run_directory.exists():
        return []
    _assert_no_reparse_chain(run_directory)
    inventory: list[dict[str, Any]] = []
    for path in sorted(run_directory.iterdir(), key=lambda item: item.name):
        _assert_no_reparse_chain(path)
        if not path.is_file():
            raise R7S2QualificationError("primary_partial_non_file_forbidden")
        inventory.append(
            {
                "name": path.name,
                "path": str(path),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return inventory


def _publish_emergency_seal(
    contract: QualificationContract,
    *,
    expected_contract_sha256: str,
    failed_stage: str,
    exception: Exception,
    qualification: ConcurrentQualification | None,
    child_launch_attempted: bool,
    inner_bootstrap_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        partial = qualification.partial_evidence if qualification is not None else {}
        summary = _partial_process_summary(partial)
        inventory = _primary_partial_inventory(contract.run_directory)
        _assert_no_reparse_chain(contract.emergency_directory, allow_missing_leaf=True)
        os.mkdir(contract.emergency_directory)
        _assert_no_reparse_chain(contract.emergency_directory)
        payload = {
            "schema": EMERGENCY_SCHEMA,
            "qualification_id": contract.qualification_id,
            "run_uuid": contract.run_uuid,
            "attempt_id": contract.attempt_id,
            "sealed_at_utc": datetime.now(UTC).isoformat(),
            "status": "manual_intervention_required",
            "credit": "zero_credit",
            "failed_stage": failed_stage,
            "exception_type": type(exception).__name__,
            "exception": str(exception),
            "expected_contract_sha256": _hex64(
                expected_contract_sha256, "expected_contract_sha256"
            ),
            "primary_run_directory": str(contract.run_directory),
            "emergency_directory": str(contract.emergency_directory),
            "partial_inventory": inventory,
            "partial_evidence": partial,
            "partial_process_summary": summary,
            "bootstrap_provenance": {
                "stage": "inner_bootstrap_verified_before_qualification",
                "inner_bootstrap_attestation": dict(inner_bootstrap_attestation),
            },
            "child_launch_attempted": child_launch_attempted,
            "automatic_retry_count": 0,
            "forced_termination_attempts": summary["forced_termination_attempts"],
            "lifecycle_calls": 0,
            "completion_marker_created": False,
            "r8_started": False,
        }
        return _atomic_exclusive_json(contract.emergency_directory / "emergency-seal.json", payload)
    except Exception as emergency_exc:
        raise R7S2QualificationError(
            "emergency_seal_publication_failed_no_retry"
        ) from emergency_exc


def _emergency_result(
    contract: QualificationContract,
    *,
    expected_contract_sha256: str,
    failed_stage: str,
    exception: Exception,
    qualification: ConcurrentQualification | None,
    child_launch_attempted: bool,
    inner_bootstrap_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    emergency = _publish_emergency_seal(
        contract,
        expected_contract_sha256=expected_contract_sha256,
        failed_stage=failed_stage,
        exception=exception,
        qualification=qualification,
        child_launch_attempted=child_launch_attempted,
        inner_bootstrap_attestation=inner_bootstrap_attestation,
    )
    return {
        "emergency_seal": emergency,
        "passed": False,
        "manual_intervention_required": True,
        "irrecoverable_primary_publication_failure": True,
        "failed_stage": failed_stage,
    }


def execute_once(
    contract: QualificationContract,
    *,
    expected_contract_sha256: str,
) -> dict[str, Any]:
    if R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED is not True:
        raise R7S2QualificationError("r7s2_out_of_band_root_anchor_required")
    inner_bootstrap_attestation = _require_inner_bootstrap_attestation()
    _assert_no_reparse_chain(QUALIFIER_INVOCATION_PATH)
    if (
        contract.execution_authorized is not True
        or not _path_equal(contract.evidence_root, CANONICAL_EVIDENCE_ROOT)
        or contract.launch_index_path is None
        or contract.launch_index_sha256 is None
        or contract.outer_reservation_path is None
        or contract.outer_reservation_sha256 is None
        or contract.outer_parent_pid is None
        or contract.contract_sha256 != _hex64(expected_contract_sha256, "expected_contract_sha256")
    ):
        raise R7S2QualificationError("execution_authorization_required")
    _validate_launch_authorization(
        contract.launch_index_path,
        expected_sha256=contract.launch_index_sha256,
        contract_path=contract.contract_path,
        contract_sha256=contract.contract_sha256,
        contract_bytes=contract.contract_path.stat().st_size,
        qualification_id=contract.qualification_id,
        run_uuid=contract.run_uuid,
        attempt_id=contract.attempt_id,
        source_identity=contract.source_identity,
        source_pins=contract.raw["source_pins"],
        staging_attestation=contract.staging_attestation,
        host_binaries=contract.host_binaries,
    )
    _validate_outer_reservation(
        contract.outer_reservation_path,
        expected_sha256=contract.outer_reservation_sha256,
        expected_outer_directory=Path(contract.launch_authorization["outer_evidence_directory"]),
        qualification_id=contract.qualification_id,
        run_uuid=contract.run_uuid,
        attempt_id=contract.attempt_id,
        contract_sha256=contract.contract_sha256,
        launch_index_sha256=contract.launch_index_sha256,
        outer_sha256=contract.raw["source_pins"]["outer_launcher"]["sha256"],
        source_commit=contract.source_identity["commit"],
        source_tree=contract.source_identity["tree"],
        require_child_parent=True,
    )
    measured_token = dict(_runtime_admin_token_readback(contract))
    if not (
        measured_token.get("administrator") is True
        and measured_token.get("integrity") in {"High", "System"}
        and measured_token.get("token_elevation_type") == "Full"
        and measured_token.get("token_elevation_type_value") == 2
    ):
        raise R7S2QualificationError("administrator_token_required")
    measured_job = _runtime_job_containment_readback()
    try:
        os.mkdir(contract.run_directory)
        _assert_no_reparse_chain(contract.run_directory)
    except FileExistsError:
        raise
    except Exception as directory_exc:
        return _emergency_result(
            contract,
            expected_contract_sha256=expected_contract_sha256,
            failed_stage="primary_run_directory_creation",
            exception=directory_exc,
            qualification=None,
            child_launch_attempted=False,
            inner_bootstrap_attestation=inner_bootstrap_attestation,
        )
    reservation = {
        "schema": RESERVATION_SCHEMA,
        "qualification_id": contract.qualification_id,
        "run_uuid": contract.run_uuid,
        "attempt_id": contract.attempt_id,
        "reserved_at_utc": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "administrator_token_evidence": measured_token,
        "outer_kernel_job_evidence": measured_job,
        "inner_bootstrap_attestation": inner_bootstrap_attestation,
        "expected_contract_sha256": _hex64(expected_contract_sha256, "expected_contract_sha256"),
        "adversary_launch_budget": 1,
        "automatic_retry_budget": 0,
        "forced_termination_budget": 0,
        "credit": "non_credit_only",
    }
    try:
        reservation_pin = _atomic_exclusive_json(
            contract.run_directory / "invocation-reservation.json", reservation
        )
    except Exception as reservation_exc:
        return _emergency_result(
            contract,
            expected_contract_sha256=expected_contract_sha256,
            failed_stage="invocation_reservation_publication",
            exception=reservation_exc,
            qualification=None,
            child_launch_attempted=False,
            inner_bootstrap_attestation=inner_bootstrap_attestation,
        )
    try:
        qualification = ConcurrentQualification(contract)
    except Exception as initialization_exc:
        return _emergency_result(
            contract,
            expected_contract_sha256=expected_contract_sha256,
            failed_stage="qualification_initialization",
            exception=initialization_exc,
            qualification=None,
            child_launch_attempted=False,
            inner_bootstrap_attestation=inner_bootstrap_attestation,
        )
    try:
        qualification.partial_evidence["outer_kernel_job_evidence"] = measured_job
        qualification.partial_evidence["inner_bootstrap_attestation"] = inner_bootstrap_attestation
        source_started = time.monotonic_ns()
        try:
            source_readback = dict(_runtime_source_identity_readback(contract))
        except Exception as source_exc:
            recorder = getattr(qualification, "_record_process_failure", None)
            if callable(recorder):
                recorder(
                    name="pre-r8-r7s2-runtime-source-readback",
                    sequence=None,
                    started=source_started,
                    exc=source_exc,
                )
            raise
        qualification.partial_evidence["source_identity_readback"] = source_readback
        source_counts = _mapping(source_readback.get("call_counts", {}), "source_call_counts")
        for key in qualification.call_counts:
            if key.startswith("git_"):
                qualification.call_counts[key] = int(source_counts.get(key, 0))
        ubuntu_readback = dict(_runtime_ubuntu_running_readback(contract))
        qualification.partial_evidence["administrator_token_evidence"] = measured_token
        qualification.partial_evidence["ubuntu_running_readback"] = ubuntu_readback
        ubuntu_counts = _mapping(ubuntu_readback.get("call_counts", {}), "ubuntu_state_call_counts")
        for key in ("ubuntu_verbose_gate", "ubuntu_running_gate"):
            if int(ubuntu_counts.get(key, 0)) != 1:
                raise R7S2QualificationError(f"runtime_{key}_call_count_mismatch")
            qualification.call_counts[key] = int(ubuntu_counts.get(key, 0))
        evidence = qualification.run()
        evidence["source_identity_readback"] = source_readback
        evidence["administrator_token_evidence"] = measured_token
        evidence["ubuntu_running_readback"] = ubuntu_readback
        evidence["inner_bootstrap_attestation"] = inner_bootstrap_attestation
    except Exception as operation_exc:
        failure = _failure_seal_payload(
            contract,
            reservation_pin=reservation_pin,
            qualification=qualification,
            failed_stage="concurrent_wsl_qualification",
            exception=operation_exc,
            failure_evidence=None,
            include_partial_evidence=True,
        )
        try:
            seal = _atomic_exclusive_json(contract.run_directory / "failure-seal.json", failure)
        except Exception as seal_exc:
            return _emergency_result(
                contract,
                expected_contract_sha256=expected_contract_sha256,
                failed_stage="primary_failure_seal_publication",
                exception=seal_exc,
                qualification=qualification,
                child_launch_attempted=qualification.call_counts["adversary_launches"] > 0,
                inner_bootstrap_attestation=inner_bootstrap_attestation,
            )
        try:
            index = _atomic_exclusive_json(
                contract.run_directory / "failure-index.json",
                _index_payload(
                    contract,
                    reservation_pin=reservation_pin,
                    report_pin=seal,
                    failure_seal_pin=seal,
                    status="zero_credit_failure",
                ),
            )
        except Exception as index_exc:
            return _emergency_result(
                contract,
                expected_contract_sha256=expected_contract_sha256,
                failed_stage="primary_failure_index_publication",
                exception=index_exc,
                qualification=qualification,
                child_launch_attempted=qualification.call_counts["adversary_launches"] > 0,
                inner_bootstrap_attestation=inner_bootstrap_attestation,
            )
        return {"failure_seal": seal, "index": index, "passed": False}

    evidence["reservation"] = reservation_pin
    child_launch_attempted = qualification.call_counts["adversary_launches"] > 0
    if evidence["analysis"]["passed"]:
        try:
            publication = _atomic_exclusive_json(
                contract.run_directory / "qualification-evidence.json", evidence
            )
        except Exception as report_exc:
            return _emergency_result(
                contract,
                expected_contract_sha256=expected_contract_sha256,
                failed_stage="qualification_report_publication",
                exception=report_exc,
                qualification=qualification,
                child_launch_attempted=child_launch_attempted,
                inner_bootstrap_attestation=inner_bootstrap_attestation,
            )
        try:
            index = _atomic_exclusive_json(
                contract.run_directory / "qualification-index.json",
                _index_payload(
                    contract,
                    reservation_pin=reservation_pin,
                    report_pin=publication,
                    failure_seal_pin=None,
                    status="qualified_non_credit",
                ),
            )
        except Exception as index_exc:
            return _emergency_result(
                contract,
                expected_contract_sha256=expected_contract_sha256,
                failed_stage="qualification_index_publication",
                exception=index_exc,
                qualification=qualification,
                child_launch_attempted=child_launch_attempted,
                inner_bootstrap_attestation=inner_bootstrap_attestation,
            )
        return {"evidence": publication, "index": index, "passed": True}

    try:
        publication = _atomic_exclusive_json(
            contract.run_directory / "failure-evidence.json", evidence
        )
    except Exception as report_exc:
        return _emergency_result(
            contract,
            expected_contract_sha256=expected_contract_sha256,
            failed_stage="failure_report_publication",
            exception=report_exc,
            qualification=qualification,
            child_launch_attempted=child_launch_attempted,
            inner_bootstrap_attestation=inner_bootstrap_attestation,
        )
    failure = _failure_seal_payload(
        contract,
        reservation_pin=reservation_pin,
        qualification=qualification,
        failed_stage="concurrent_wsl_qualification_analysis",
        exception=None,
        failure_evidence=publication,
        include_partial_evidence=False,
    )
    try:
        seal = _atomic_exclusive_json(contract.run_directory / "failure-seal.json", failure)
    except Exception as seal_exc:
        return _emergency_result(
            contract,
            expected_contract_sha256=expected_contract_sha256,
            failed_stage="primary_failure_seal_publication",
            exception=seal_exc,
            qualification=qualification,
            child_launch_attempted=child_launch_attempted,
            inner_bootstrap_attestation=inner_bootstrap_attestation,
        )
    try:
        index = _atomic_exclusive_json(
            contract.run_directory / "failure-index.json",
            _index_payload(
                contract,
                reservation_pin=reservation_pin,
                report_pin=publication,
                failure_seal_pin=seal,
                status="zero_credit_failure",
            ),
        )
    except Exception as index_exc:
        return _emergency_result(
            contract,
            expected_contract_sha256=expected_contract_sha256,
            failed_stage="primary_failure_index_publication",
            exception=index_exc,
            qualification=qualification,
            child_launch_attempted=child_launch_attempted,
            inner_bootstrap_attestation=inner_bootstrap_attestation,
        )
    return {
        "evidence": publication,
        "failure_seal": seal,
        "index": index,
        "passed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Non-credit pre-r8 r7s2 WSL containment qualifier")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--expected-contract-sha256", required=True)
    parser.add_argument("--launch-index", type=Path, required=True)
    parser.add_argument("--expected-launch-index-sha256", required=True)
    parser.add_argument("--outer-reservation", type=Path, required=True)
    parser.add_argument("--expected-outer-reservation-sha256", required=True)
    parser.add_argument("--execute-non-credit-once", action="store_true")
    return parser


def _require_inner_bootstrap_attestation() -> dict[str, Any]:
    value = globals().get("__evm_r7s2_inner_bootstrap_attestation__")
    attestation = _mapping(value, "inner_bootstrap_attestation")
    _exact_keys(
        attestation,
        {
            "schema",
            "qualifier_path",
            "qualifier_sha256",
            "qualifier_bytes",
            "qualifier_lf_sha256",
            "qualifier_blob_oid",
            "inner_argv_sha256",
            "bootstrap_source_sha256",
        },
        "inner_bootstrap_attestation",
    )
    if (
        len(sys.orig_argv) < 6
        or sys.orig_argv[1:5] != ["-I", "-S", "-B", "-c"]
        or _sha256_bytes(sys.orig_argv[5].encode("utf-8")) != INNER_BOOTSTRAP_SOURCE_SHA256
        or attestation["schema"]
        != "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.inner-bootstrap-attestation.v1"
        or attestation["bootstrap_source_sha256"] != INNER_BOOTSTRAP_SOURCE_SHA256
        or not _path_equal(Path(str(attestation["qualifier_path"])), QUALIFIER_INVOCATION_PATH)
        or attestation["inner_argv_sha256"]
        != _sha256_bytes(json.dumps(sys.argv[1:], separators=(",", ":")).encode("utf-8"))
    ):
        raise R7S2QualificationError("verified_inner_bootstrap_required")
    _assert_no_reparse_chain(QUALIFIER_INVOCATION_PATH)
    raw = QUALIFIER_INVOCATION_PATH.read_bytes()
    normalized = _lf_normalized_source(raw)
    if (
        attestation["qualifier_sha256"] != _sha256_bytes(raw)
        or attestation["qualifier_bytes"] != len(raw)
        or attestation["qualifier_lf_sha256"] != _sha256_bytes(normalized)
        or attestation["qualifier_blob_oid"] != _git_blob_oid(normalized)
    ):
        raise R7S2QualificationError("inner_bootstrap_qualifier_readback_mismatch")
    return dict(attestation)


def main(argv: Sequence[str] | None = None) -> int:
    if argv is not None:
        raise R7S2QualificationError("production_main_argv_override_forbidden")
    _require_inner_bootstrap_attestation()
    args = _parser().parse_args(argv)
    if not args.execute_non_credit_once:
        raise R7S2QualificationError("explicit_execute_non_credit_once_required")
    contract = load_contract(
        args.contract,
        expected_sha256=args.expected_contract_sha256,
        expected_evidence_root=CANONICAL_EVIDENCE_ROOT,
        launch_index_path=args.launch_index,
        expected_launch_index_sha256=args.expected_launch_index_sha256,
        outer_reservation_path=args.outer_reservation,
        expected_outer_reservation_sha256=args.expected_outer_reservation_sha256,
    )
    result = execute_once(contract, expected_contract_sha256=args.expected_contract_sha256)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

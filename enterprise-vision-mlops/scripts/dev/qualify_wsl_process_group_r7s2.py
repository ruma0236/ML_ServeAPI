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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

CONTRACT_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.wsl-qualification-contract.v1"
RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.invocation-reservation.v1"
EVIDENCE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.wsl-qualification.v1"
FAILURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.failure-seal.v1"
INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.evidence-index.v1"
EMERGENCY_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.emergency-seal.v1"
LINUX_READBACK_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.linux-toolchain.v1"
FIXTURE_ACK_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.detached-descendant.v1"

CANONICAL_EVIDENCE_ROOT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2-pre-r8-r7s2-gate"
    r"\x1-clock-phase-b2-pre-r8-r7s2-gate-20260901T131707Z-55f09ef"
)
RUN_ID_RE = re.compile(r"pre-r8-r7s2-wsl-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
EVIDENCE_LEAF_RE = re.compile(r"wsl-[0-9a-f]{8}")
HEX64 = re.compile(r"[0-9a-f]{64}")
WINDOWS_PATH_BUDGET = 240

PARENT_ROLES = {
    "attempt_1_failure",
    "attempt_2_failure",
    "pre_r8_r7s1_no_go_seal",
}
HOST_BINARY_ROLES = {"python", "system32_wsl", "store_wsl", "wslhost"}
LINUX_BINARY_ROLES = {"python3", "env", "setsid"}

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
        environ=(proc/'environ').read_bytes().split(b'\0')
        cmdline=(proc/'cmdline').read_bytes()
        uuid_match=needle in environ
        group_match=bool(
            expected_boot and boot==expected_boot
            and expected_pgrp_i is not None and pgrp==expected_pgrp_i
            and expected_start_i is not None and start>=expected_start_i
        )
        if uuid_match or group_match:
            fds=sorted(int(item.name) for item in (proc/'fd').iterdir() if item.name.isdigit())
            records.append({
                'pid':pid,'ppid':ppid,'pgrp':pgrp,'session':session,
                'start_time_ticks':start,'boot_id':boot,
                'run_uuid_match':uuid_match,'process_group_match':group_match,
                'cmdline_sha256':hashlib.sha256(cmdline).hexdigest(),
                'open_fd_count':len(fds),
                'stdio_fds_present':[descriptor for descriptor in fds if descriptor in (0,1,2)],
            })
    except (FileNotFoundError,PermissionError,ProcessLookupError,ValueError):
        continue
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
    timeouts: dict[str, float | int]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    if len(raw) != pin["bytes"] or _sha256_bytes(raw) != expected_sha256:
        raise R7S2QualificationError("process_runtime_identity_mismatch_at_load")
    module_name = f"_evm_pre_r8_r7s2_process_{expected_sha256[:16]}"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
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


def _validate_parent_pin(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    _exact_keys(pin, {"path", "sha256", "bytes"}, label)
    normalized = {
        "path": str(pin["path"]),
        "sha256": _hex64(pin["sha256"], f"{label}_sha256"),
        "bytes": _positive_int(pin["bytes"], f"{label}_bytes"),
    }
    _verify_file_pin(BinaryPin(version="parent", **normalized), label)
    return normalized


def load_contract(
    path: Path,
    *,
    expected_sha256: str,
    expected_evidence_root: Path = CANONICAL_EVIDENCE_ROOT,
) -> QualificationContract:
    contract_path = Path(os.path.abspath(path))
    _assert_no_reparse_chain(contract_path)
    raw_bytes = contract_path.read_bytes()
    if _sha256_bytes(raw_bytes) != _hex64(expected_sha256, "expected_contract_sha256"):
        raise R7S2QualificationError("contract_sha256_mismatch")
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise R7S2QualificationError("contract_json_invalid") from exc
    contract = _mapping(raw, "contract")
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
            "parent_evidence",
            "source_pins",
            "timeouts",
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
        },
        "platform_identity",
    )
    platform_identity = {key: str(value) for key, value in platform.items()}
    if any(not value for value in platform_identity.values()):
        raise R7S2QualificationError("platform_identity_empty")
    for key in ("rootfs_identity", "os_release_sha256", "machine_id_sha256"):
        platform_identity[key] = _hex64(platform_identity[key], f"platform_{key}")
    if platform_identity["windows_build"] != host_platform.version():
        raise R7S2QualificationError("windows_build_identity_mismatch")
    if platform_identity["wsl_package_version"] != host_binaries["store_wsl"].version:
        raise R7S2QualificationError("wsl_package_version_mismatch")

    parents = _mapping(contract["parent_evidence"], "parent_evidence")
    _exact_keys(parents, PARENT_ROLES, "parent_evidence")
    contract["parent_evidence"] = {
        role: _validate_parent_pin(parents[role], f"parent_{role}") for role in sorted(PARENT_ROLES)
    }

    source_pins = _mapping(contract["source_pins"], "source_pins")
    _exact_keys(
        source_pins,
        {
            "qualification_script",
            "process_module",
            "r7s1_runner",
        },
        "source_pins",
    )
    for role in sorted(source_pins):
        source_pins[role] = _validate_parent_pin(source_pins[role], f"source_{role}")
    expected_source_paths = {
        "qualification_script": Path(__file__),
        "process_module": SRC_ROOT / "evm" / "scale_validation" / "phase_b2_r7_process.py",
        "r7s1_runner": PROJECT_ROOT / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py",
    }
    for role, expected_path in expected_source_paths.items():
        if not _path_equal(Path(source_pins[role]["path"]), expected_path):
            raise R7S2QualificationError(f"{role}_path_mismatch")
    contract["source_pins"] = source_pins

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
    if lifetime > 8 or lifetime >= normalized_timeouts["launch_wrapper_seconds"]:
        raise R7S2QualificationError("fixture_lifetime_out_of_bounds")
    fixture["lifetime_seconds"] = lifetime

    if _mapping(contract["invocation_policy"], "invocation_policy") != INVOCATION_POLICY:
        raise R7S2QualificationError("invocation_policy_mismatch")

    _bind_verified_process_runtime(source_pins["process_module"])

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
        timeouts=normalized_timeouts,
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
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
    try:
        _assert_no_reparse_chain(path.parent)
        _assert_no_reparse_chain(path, allow_missing_leaf=True)
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
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
        linux["python3"].path,
        "-I",
        "-S",
        "-B",
        "-c",
        QUALIFICATION_SCANNER_SOURCE,
        protocol.run_uuid,
        "",
        "",
        "",
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
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise R7S2QualificationError(f"{label}_json_invalid") from exc
    return _mapping(value, label)


def _validate_linux_readback(
    contract: QualificationContract, outcome: Mapping[str, Any]
) -> dict[str, Any]:
    if not outcome.get("safe_for_followup") or outcome.get("forced_termination_attempts") != 0:
        raise R7S2QualificationError("linux_toolchain_readback_process_failed")
    payload = _parse_json_object(str(outcome.get("stdout", "")).strip(), "linux_readback")
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
    try:
        rows = json.loads(str(outcome.get("stdout", "")).strip() or "[]")
    except json.JSONDecodeError as exc:
        raise R7S2QualificationError("observer_scan_json_invalid") from exc
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
        records.append(record)
    return records


def _parse_fixture_ack(payload: str, contract: QualificationContract) -> dict[str, Any]:
    lines = [line.strip() for line in payload.splitlines() if line.strip()]
    if len(lines) != 1:
        raise R7S2QualificationError("fixture_ack_count_mismatch")
    ack = _parse_json_object(lines[0], "fixture_ack")
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
            "linux_toolchain_readback": 0,
            "initial_scan": 0,
            "observer_scans": 0,
            "adversary_launches": 0,
            "post_launch_zero_scans": 0,
        }
        self.partial_evidence: dict[str, Any] = {
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
            final_row = self._run_scan(
                scan_command,
                name="pre-r8-r7s2-post-launch-uuid-zero-scan",
                sequence=len(observer_rows) + 1,
                counter="post_launch_zero_scans",
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
) -> dict[str, Any]:
    emergency = _publish_emergency_seal(
        contract,
        expected_contract_sha256=expected_contract_sha256,
        failed_stage=failed_stage,
        exception=exception,
        qualification=qualification,
        child_launch_attempted=child_launch_attempted,
    )
    return {
        "emergency_seal": emergency,
        "passed": False,
        "manual_intervention_required": True,
        "irrecoverable_primary_publication_failure": True,
        "failed_stage": failed_stage,
    }


def execute_once(
    contract: QualificationContract, *, expected_contract_sha256: str
) -> dict[str, Any]:
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
        )
    reservation = {
        "schema": RESERVATION_SCHEMA,
        "qualification_id": contract.qualification_id,
        "run_uuid": contract.run_uuid,
        "attempt_id": contract.attempt_id,
        "reserved_at_utc": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
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
        )
    try:
        evidence = qualification.run()
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
    parser.add_argument("--execute-non-credit-once", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute_non_credit_once:
        raise R7S2QualificationError("explicit_execute_non_credit_once_required")
    contract = load_contract(
        args.contract,
        expected_sha256=args.expected_contract_sha256,
        expected_evidence_root=CANONICAL_EVIDENCE_ROOT,
    )
    result = execute_once(contract, expected_contract_sha256=args.expected_contract_sha256)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

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
import configparser
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import platform
import re
import stat
import types
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


STAGER_INVOCATION_PATH = Path(os.path.abspath(__file__))
PROJECT_ROOT = STAGER_INVOCATION_PATH.parents[2]
GIT_ROOT = PROJECT_ROOT.parent
CANONICAL_EVIDENCE_ROOT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2-pre-r8-r7s2-gate"
    r"\x1-clock-phase-b2-pre-r8-r7s2-gate-20260901T131707Z-55f09ef"
)

STAGER_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.contract-stager.v1"
PARENT_MAP_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.parent-map.v1"
RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-reservation.v1"
FAILURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-failure-seal.v1"
EMERGENCY_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-emergency-seal.v1"
INDEX_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-index.v1"
ATTESTATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.staging-attestation.v1"
LINUX_DISCOVERY_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.linux-discovery.v1"

RUN_ID_RE = re.compile(r"pre-r8-r7s2-wsl-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
HEX40_RE = re.compile(r"[0-9a-f]{40}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
WINDOWS_PATH_BUDGET = 240
PARENT_ROLES = {
    "attempt_1_failure",
    "attempt_2_failure",
    "pre_r8_r7s1_no_go_seal",
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

QUALIFICATION_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "qualify_wsl_process_group_r7s2.py"
PROCESS_MODULE = PROJECT_ROOT / "src" / "evm" / "scale_validation" / "phase_b2_r7_process.py"
R7S1_RUNNER = PROJECT_ROOT / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py"
OUTER_LAUNCHER = PROJECT_ROOT / "scripts" / "dev" / "invoke_wsl_process_group_r7s2.py"
STAGER_SCRIPT = STAGER_INVOCATION_PATH

HOST_EXPECTATIONS: dict[str, dict[str, Any]] = {
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
    "git": {
        "path": r"C:\Program Files\Git\mingw64\bin\git.exe",
        "sha256": "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        "bytes": 4422544,
        "version": "2.54.0.windows.1",
    },
}
SYSTEM32_WSL_VERSION_RESOURCE = {
    "path": r"C:\Windows\System32\en-US\wsl.exe.mui",
    "sha256": "c23ec0ab383bd492ef08b8ff7be98014fb2b462b1d904afb494a8e4b39c85074",
    "bytes": 4608,
    "version": "10.0.26100.8737",
}
GIT_CONFIG_POLICY = "dynamic-raw-pin-dangerous-execution-keys-rejected-v1"
STAGING_ATTESTATION_TTL_SECONDS = 1800
SOURCE_ROLES = {
    "qualification_script",
    "process_module",
    "r7s1_runner",
    "stager",
    "outer_launcher",
}
SOURCE_PIN_FIELDS = (
    "path",
    "sha256",
    "bytes",
    "relative_path",
    "worktree_blob_oid",
    "git_mode",
    "git_head_blob_oid",
    "git_normalized_worktree_blob_oid",
)

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
TRUSTED_PROCESS_RUNTIME = {
    "path": str(PROCESS_MODULE),
    "lf_normalized_sha256": "75aac2336d6f93bf5df6434871e1911d0922a3ff5e4f8dbea25712b0c65d8c74",
    "git_head_blob_oid": "6a65d8184afd4a4dbec0163f820ac7b8f03914be",
}

OUTER_BOOTSTRAP_SOURCE = r"""import hashlib,json,os,stat,sys
PYTHON_PATH=r'C:\Users\opop0\miniconda3\python.exe'
PYTHON_SHA256='ec0ea8d6907787b76dcf8524aaa93e52e167ceee62fa8778e182ea637a3dbc1d'
PYTHON_BYTES=104264
PYTHON_VERSION='3.13.11'
def fail(message):
    raise SystemExit('r7s2_outer_bootstrap_rejected:'+message)
def same_path(left,right):
    return os.path.normcase(os.path.abspath(left))==os.path.normcase(os.path.abspath(right))
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
    if b'\r' in without_crlf or (b'\r\n' in raw and b'\n' in without_crlf):
        fail('mixed_or_bare_cr_line_endings')
    return raw.replace(b'\r\n',b'\n')
def strict_owned_json(raw,label):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: fail(label+'_duplicate_key:'+key)
            result[key]=value
        return result
    def reject_constant(value): fail(label+'_nonfinite:'+value)
    try:
        text=raw.decode('utf-8')
        if '\ufeff' in text or '\ufffd' in text or '\x00' in text: fail(label+'_encoding')
        value=json.loads(text,object_pairs_hook=unique,parse_constant=reject_constant)
        canonical=(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
    except (UnicodeDecodeError,json.JSONDecodeError,ValueError) as exc:
        fail(label+'_json:'+type(exc).__name__)
    if not isinstance(value,dict) or raw!=canonical: fail(label+'_canonical')
    return value
if not (sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment and sys.flags.dont_write_bytecode and sys.flags.safe_path):
    fail('isolated_flags')
if len(sys.argv)!=26: fail('argument_count')
outer_path,outer_raw_sha,outer_bytes,outer_lf_sha,index_path,index_sha,expected_commit,expected_tree,contract_path,contract_sha,*outer_args=sys.argv[1:]
expected_outer_args=['--contract',contract_path,'--expected-contract-sha256',contract_sha,'--launch-index',index_path,'--expected-launch-index-sha256',index_sha,'--expected-outer-sha256',outer_raw_sha,'--expected-source-commit',expected_commit,'--expected-source-tree',expected_tree,'--execute-non-credit-once']
if outer_args!=expected_outer_args: fail('outer_argv')
for path in (outer_path,index_path,contract_path): no_reparse(path)
index_raw=open(index_path,'rb').read()
if hashlib.sha256(index_raw).hexdigest()!=index_sha: fail('index_sha')
index=strict_owned_json(index_raw,'index')
contract_raw=open(contract_path,'rb').read()
if hashlib.sha256(contract_raw).hexdigest()!=contract_sha: fail('contract_sha')
contract=strict_owned_json(contract_raw,'contract')
no_reparse(sys.executable)
python_raw=open(sys.executable,'rb').read()
python_version='.'.join(str(value) for value in sys.version_info[:3])
if not same_path(sys.executable,PYTHON_PATH) or len(python_raw)!=PYTHON_BYTES or hashlib.sha256(python_raw).hexdigest()!=PYTHON_SHA256 or python_version!=PYTHON_VERSION: fail('interpreter_identity')
if contract.get('host_binaries',{}).get('python')!={'path':PYTHON_PATH,'sha256':PYTHON_SHA256,'bytes':PYTHON_BYTES,'version':PYTHON_VERSION}: fail('contract_interpreter_pin')
if index.get('schema')!='evm.s8-v4.x1.phase-b2.pre-r8-r7s2.launch-index.v1' or index.get('status')!='ready_non_credit_not_executed': fail('index_state')
source=index.get('source_identity',{})
if source.get('commit')!=expected_commit or source.get('tree')!=expected_tree: fail('revision')
if index.get('contract')!={'path':contract_path,'sha256':contract_sha,'bytes':len(contract_raw)}: fail('contract_pin')
pins=source.get('source_pins',{}); outer=pins.get('outer_launcher',{})
if not same_path(outer.get('path',''),outer_path) or outer.get('sha256')!=outer_raw_sha or outer.get('bytes')!=int(outer_bytes): fail('outer_pin')
if contract.get('source_pins',{}).get('outer_launcher')!=outer: fail('contract_outer_pin')
outer_raw=open(outer_path,'rb').read()
if len(outer_raw)!=int(outer_bytes) or hashlib.sha256(outer_raw).hexdigest()!=outer_raw_sha: fail('outer_raw')
outer_lf=normalized(outer_raw)
if hashlib.sha256(outer_lf).hexdigest()!=outer_lf_sha: fail('outer_lf')
blob=hashlib.sha1(b'blob '+str(len(outer_lf)).encode()+b'\0'+outer_lf,usedforsecurity=False).hexdigest()
if blob!=outer.get('git_head_blob_oid') or blob!=outer.get('git_normalized_worktree_blob_oid'): fail('outer_blob')
bootstrap=index.get('bootstrap',{})
if bootstrap.get('outer_lf_normalized_sha256')!=outer_lf_sha: fail('bootstrap_binding')
attestation={'schema':'evm.s8-v4.x1.phase-b2.pre-r8-r7s2.bootstrap-attestation.v1','outer_path':outer_path,'outer_raw_sha256':outer_raw_sha,'outer_bytes':int(outer_bytes),'outer_lf_normalized_sha256':outer_lf_sha,'contract_path':contract_path,'contract_sha256':contract_sha,'launch_index_path':index_path,'launch_index_sha256':index_sha,'expected_source_commit':expected_commit,'expected_source_tree':expected_tree,'outer_argv_sha256':hashlib.sha256(json.dumps(outer_args,separators=(',',':')).encode()).hexdigest(),'python_identity':{'path':PYTHON_PATH,'sha256':PYTHON_SHA256,'bytes':PYTHON_BYTES,'version':PYTHON_VERSION},'bootstrap_source_sha256':bootstrap.get('source_sha256')}
sys.argv=[outer_path,*outer_args]
namespace={'__name__':'__main__','__file__':outer_path,'__package__':None,'__evm_r7s2_bootstrap_attestation__':attestation}
exec(compile(outer_lf,outer_path,'exec'),namespace,namespace)
"""
STAGER_BOOTSTRAP_SOURCE = r"""import hashlib,json,os,stat,sys
def fail(message):
    raise SystemExit('r7s2_stager_bootstrap_rejected:'+message)
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
if len(sys.argv)!=31: fail('argument_count')
stager_path,stager_sha,stager_bytes,stager_lf_sha,stager_blob,*stager_args=sys.argv[1:]
option_names=['--qualification-id','--run-uuid','--attempt-id','--expected-source-commit','--expected-source-tree','--expected-qualification-sha256','--expected-process-module-sha256','--expected-r7s1-runner-sha256','--expected-stager-sha256','--expected-outer-sha256','--parent-map','--expected-parent-map-sha256']
if len(stager_args)!=25 or stager_args[-1]!='--execute-stage-non-credit-once' or stager_args[0::2][:-1]!=option_names: fail('stager_argv')
if stager_args[17]!=stager_sha: fail('stager_expected_sha')
no_reparse(sys.executable); no_reparse(stager_path)
python_raw=open(sys.executable,'rb').read()
if os.path.normcase(os.path.abspath(sys.executable))!=os.path.normcase(os.path.abspath(r'C:\Users\opop0\miniconda3\python.exe')) or len(python_raw)!=104264 or hashlib.sha256(python_raw).hexdigest()!='ec0ea8d6907787b76dcf8524aaa93e52e167ceee62fa8778e182ea637a3dbc1d' or '.'.join(str(value) for value in sys.version_info[:3])!='3.13.11': fail('python_identity')
raw=open(stager_path,'rb').read()
if len(raw)!=int(stager_bytes) or hashlib.sha256(raw).hexdigest()!=stager_sha: fail('stager_raw')
lf=normalized(raw)
if hashlib.sha256(lf).hexdigest()!=stager_lf_sha: fail('stager_lf')
blob=hashlib.sha1(b'blob '+str(len(lf)).encode()+b'\0'+lf,usedforsecurity=False).hexdigest()
if blob!=stager_blob: fail('stager_blob')
attestation={'schema':'evm.s8-v4.x1.phase-b2.pre-r8-r7s2.stager-bootstrap-attestation.v1','stager_path':stager_path,'stager_sha256':stager_sha,'stager_bytes':int(stager_bytes),'stager_lf_sha256':stager_lf_sha,'stager_blob_oid':stager_blob,'stager_argv_sha256':hashlib.sha256(json.dumps(stager_args,separators=(',',':')).encode()).hexdigest(),'bootstrap_source_sha256':hashlib.sha256(sys.orig_argv[5].encode()).hexdigest()}
sys.argv=[stager_path,*stager_args]
namespace={'__name__':'__main__','__file__':stager_path,'__package__':None,'__evm_r7s2_stager_bootstrap_attestation__':attestation}
exec(compile(lf,stager_path,'exec'),namespace,namespace)
"""
STAGER_BOOTSTRAP_SOURCE_SHA256 = hashlib.sha256(STAGER_BOOTSTRAP_SOURCE.encode("utf-8")).hexdigest()

LINUX_DISCOVERY_SOURCE = r"""import hashlib,json,os,pathlib,platform,subprocess,sys
def identity(candidate,version_args):
    real=os.path.realpath(candidate)
    raw=pathlib.Path(real).read_bytes()
    completed=subprocess.run([real,*version_args],stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env={'LANG':'C.UTF-8','LC_ALL':'C.UTF-8'},
        timeout=3,check=True,text=True)
    lines=[line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError('version_output_missing:'+candidate)
    return {'candidate_path':candidate,'realpath':real,'sha256':hashlib.sha256(raw).hexdigest(),
            'bytes':len(raw),'version':lines[0]}
def file_identity(candidate):
    real=os.path.realpath(candidate)
    raw=pathlib.Path(real).read_bytes()
    return {'candidate_path':candidate,'realpath':real,'sha256':hashlib.sha256(raw).hexdigest(),
            'bytes':len(raw),'version':platform.python_version()}
os_release=pathlib.Path('/etc/os-release').read_bytes()
machine_id=pathlib.Path('/etc/machine-id').read_bytes()
values={}
for line in os_release.decode('utf-8').splitlines():
    if '=' in line and not line.startswith('#'):
        key,value=line.split('=',1)
        values[key]=value.strip().strip('"')
payload={'schema':'evm.s8-v4.x1.phase-b2.pre-r8-r7s2.linux-discovery.v1','status':'observed',
 'distro':sys.argv[1],'kernel_release':platform.release(),'distro_version':values.get('PRETTY_NAME',''),
 'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'rootfs_identity':hashlib.sha256(os_release+b'\0'+machine_id).hexdigest(),
 'os_release_sha256':hashlib.sha256(os_release).hexdigest(),
 'machine_id_sha256':hashlib.sha256(machine_id).hexdigest(),
 'binaries':{'python3':file_identity(sys.executable),'env':identity(sys.argv[2],['--version']),
             'setsid':identity(sys.argv[3],['--version'])}}
print(json.dumps(payload,sort_keys=True,separators=(',',':')))
"""


class R7S2StagerError(RuntimeError):
    """Fail-closed production contract staging error."""

    def __init__(self, message: str, *, process_evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.process_evidence = dict(process_evidence or {})


@dataclass(frozen=True)
class StageRequest:
    qualification_id: str
    run_uuid: str
    attempt_id: str
    expected_source_commit: str
    expected_source_tree: str
    expected_qualification_sha256: str
    expected_process_module_sha256: str
    expected_r7s1_runner_sha256: str
    expected_stager_sha256: str
    expected_outer_sha256: str
    parent_map_path: Path
    expected_parent_map_sha256: str


@dataclass(frozen=True)
class StagePaths:
    root: Path
    staging_directory: Path
    emergency_directory: Path
    qualification_directory: Path
    qualification_emergency_directory: Path
    outer_directory: Path
    outer_emergency_directory: Path
    contract_path: Path
    preauthorization_path: Path
    index_path: Path


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


def _strict_json_bytes(
    raw: bytes, label: str, *, canonical_owned: bool = False
) -> Mapping[str, Any]:
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
        raise R7S2StagerError(f"{label}_json_invalid") from exc
    mapping = _mapping(decoded, label)
    if canonical_owned and raw != _canonical_json(mapping):
        raise R7S2StagerError(f"{label}_json_not_canonical")
    return mapping


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _lf_normalized_source(raw: bytes) -> bytes:
    without_crlf = raw.replace(b"\r\n", b"")
    if b"\r" in without_crlf or (b"\r\n" in raw and b"\n" in without_crlf):
        raise R7S2StagerError("mixed_or_bare_cr_source_line_endings")
    normalized = raw.replace(b"\r\n", b"\n")
    try:
        normalized.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R7S2StagerError("source_utf8_required") from exc
    return normalized


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob_oid(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


def _source_relative_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(GIT_ROOT.resolve())
    except ValueError as exc:
        raise R7S2StagerError(f"source_path_outside_git_root:{path}") from exc
    value = relative.as_posix()
    if not value or value.startswith("../"):
        raise R7S2StagerError(f"source_relative_path_invalid:{path}")
    return value


def _hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    text = str(value).lower()
    if pattern.fullmatch(text) is None:
        raise R7S2StagerError(f"{label}_invalid")
    return text


def _uuid(value: Any, label: str) -> str:
    try:
        parsed = str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise R7S2StagerError(f"{label}_invalid") from exc
    if parsed != str(value).lower():
        raise R7S2StagerError(f"{label}_noncanonical")
    return parsed


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S2StagerError(f"{label}_object_required")
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7S2StagerError(f"{label}_keys_mismatch")


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
            is_leaf = index == len(chain) - 1
            if is_leaf and allow_missing_leaf:
                continue
            raise R7S2StagerError(f"path_component_missing:{current}") from None
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if stat.S_ISLNK(metadata.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise R7S2StagerError(f"reparse_component_forbidden:{current}")


def _assert_path_budget(path: Path) -> None:
    if len(str(Path(os.path.abspath(path)))) > WINDOWS_PATH_BUDGET:
        raise R7S2StagerError(f"windows_path_budget_exceeded:{path}")


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
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("staging_write_zero")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _assert_no_reparse_chain(path.parent)
    _assert_no_reparse_chain(path, allow_missing_leaf=True)
    if os.name != "nt":
        raise R7S2StagerError(f"atomic_no_replace_move_requires_windows:{temporary}")
    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = (
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD,
    )
    move_file.restype = ctypes.wintypes.BOOL
    if not move_file(str(temporary), str(path), 0):
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, "atomic_destination_exists", str(path))
        raise OSError(error, f"atomic_no_replace_move_failed;partial={temporary}")
    _assert_no_reparse_chain(path)
    readback = path.read_bytes()
    expected = _sha256_bytes(raw)
    if readback != raw or len(readback) != len(raw) or _sha256_bytes(readback) != expected:
        raise R7S2StagerError("atomic_publication_readback_mismatch")
    return {"path": str(path), "sha256": expected, "bytes": len(raw)}


def _temporary_json_for_validation(directory: Path, value: Mapping[str, Any]) -> tuple[Path, str]:
    raw = _canonical_json(value)
    path = directory / f".v-{uuid.uuid4().hex[:8]}.tmp"
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
    )
    try:
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("validation_write_zero")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if path.read_bytes() != raw:
        raise R7S2StagerError("validation_temp_readback_mismatch")
    return path, _sha256_bytes(raw)


def _windows_file_version(path: Path) -> str:
    if os.name != "nt":
        raise R7S2StagerError("windows_file_version_requires_windows")

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPDWORD)
    version.GetFileVersionInfoSizeW.restype = ctypes.wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = (
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.LPVOID,
    )
    version.GetFileVersionInfoW.restype = ctypes.wintypes.BOOL
    version.VerQueryValueW.argtypes = (
        ctypes.wintypes.LPCVOID,
        ctypes.wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.wintypes.LPVOID),
        ctypes.POINTER(ctypes.wintypes.UINT),
    )
    version.VerQueryValueW.restype = ctypes.wintypes.BOOL
    ignored = ctypes.wintypes.DWORD()
    size = int(version.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored)))
    if size <= 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    pointer = ctypes.wintypes.LPVOID()
    length = ctypes.wintypes.UINT()
    if not version.VerQueryValueW(
        buffer, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if int(length.value) < ctypes.sizeof(ctypes.wintypes.WORD) * 2:
        raise R7S2StagerError("windows_file_version_translation_invalid")
    words = ctypes.cast(pointer, ctypes.POINTER(ctypes.wintypes.WORD))
    query = f"\\StringFileInfo\\{int(words[0]):04x}{int(words[1]):04x}\\FileVersion"
    if not version.VerQueryValueW(buffer, query, ctypes.byref(pointer), ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    value = ctypes.wstring_at(pointer, int(length.value)).rstrip("\x00").strip()
    if not value:
        raise R7S2StagerError("windows_file_version_empty")
    return value.split(" ", 1)[0]


def _git_config_readback() -> dict[str, Any]:
    path = GIT_ROOT / ".git" / "config"
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(raw.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise R7S2StagerError("git_config_parse_failed") from exc
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
        raise R7S2StagerError(f"git_config_dangerous_key_forbidden:{sorted(forbidden)}")
    config_worktree = GIT_ROOT / ".git" / "config.worktree"
    _assert_no_reparse_chain(config_worktree, allow_missing_leaf=True)
    if config_worktree.exists():
        raise R7S2StagerError("git_config_worktree_must_be_absent")
    if not parser.has_option('remote "origin"', "url"):
        raise R7S2StagerError("git_config_origin_url_missing")
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


def _measure_admin_token() -> dict[str, Any]:
    if os.name != "nt":
        raise R7S2StagerError("windows_token_measurement_requires_windows")

    class SidAndAttributes(ctypes.Structure):
        _fields_ = (("sid", ctypes.wintypes.LPVOID), ("attributes", ctypes.wintypes.DWORD))

    class TokenMandatoryLabel(ctypes.Structure):
        _fields_ = (("label", SidAndAttributes),)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    kernel32.ProcessIdToSessionId.argtypes = (
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    )
    kernel32.ProcessIdToSessionId.restype = ctypes.wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = ctypes.wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.c_int,
        ctypes.wintypes.LPVOID,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = ctypes.wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.wintypes.LPVOID,)
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = (ctypes.wintypes.LPVOID, ctypes.wintypes.DWORD)
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.wintypes.DWORD)
    shell32.IsUserAnAdmin.restype = ctypes.wintypes.BOOL

    token = ctypes.wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        returned = ctypes.wintypes.DWORD()
        elevation = ctypes.wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            18,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        required = ctypes.wintypes.DWORD()
        advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(required))
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            25,
            buffer,
            required.value,
            ctypes.byref(returned),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        label = ctypes.cast(buffer, ctypes.POINTER(TokenMandatoryLabel)).contents
        count_pointer = advapi32.GetSidSubAuthorityCount(label.label.sid)
        if not count_pointer or count_pointer.contents.value < 1:
            raise R7S2StagerError("token_integrity_sid_invalid")
        rid_pointer = advapi32.GetSidSubAuthority(label.label.sid, count_pointer.contents.value - 1)
        if not rid_pointer:
            raise R7S2StagerError("token_integrity_rid_missing")
        integrity_rid = int(rid_pointer.contents.value)
    finally:
        kernel32.CloseHandle(token)
    session_id = ctypes.wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise ctypes.WinError(ctypes.get_last_error())
    integrity = (
        "System" if integrity_rid >= 0x4000 else "High" if integrity_rid >= 0x3000 else "Other"
    )
    elevation_name = {1: "Default", 2: "Full", 3: "Limited"}.get(
        int(elevation.value), f"Unknown:{int(elevation.value)}"
    )
    return {
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


def _require_admin(token: Mapping[str, Any]) -> None:
    if not (
        token.get("administrator") is True
        and token.get("integrity") in {"High", "System"}
        and token.get("token_elevation_type") == "Full"
        and token.get("token_elevation_type_value") == 2
    ):
        raise R7S2StagerError(
            "administrator_token_required:"
            f"administrator={token.get('administrator')}:"
            f"integrity={token.get('integrity')}:"
            f"token_elevation_type={token.get('token_elevation_type')}"
        )


def _paths(request: StageRequest, root: Path) -> StagePaths:
    attempt_short = request.attempt_id.replace("-", "")[:8]
    staging = root / f"c-{attempt_short}"
    emergency = root / f"c-{attempt_short}-emergency-seal"
    qualification = root / f"wsl-{attempt_short}"
    qualification_emergency = root / f"wsl-{attempt_short}-emergency-seal"
    outer = root / f"outer-{attempt_short}"
    outer_emergency = root / f"outer-{attempt_short}-emergency-seal"
    result = StagePaths(
        root=root,
        staging_directory=staging,
        emergency_directory=emergency,
        qualification_directory=qualification,
        qualification_emergency_directory=qualification_emergency,
        outer_directory=outer,
        outer_emergency_directory=outer_emergency,
        contract_path=staging / "qualification-contract.json",
        preauthorization_path=staging / "preauthorization-index.json",
        index_path=staging / "staging-index.json",
    )
    for path in (
        staging,
        emergency,
        qualification,
        qualification_emergency,
        outer,
        outer_emergency,
        result.contract_path,
        result.preauthorization_path,
        result.index_path,
        staging / "staging-reservation.json",
        staging / "staging-failure-seal.json",
        staging / "failure-index.json",
        emergency / "emergency-seal.json",
        staging / ".t-12345678.tmp",
    ):
        _assert_path_budget(path)
    return result


def _validate_request(request: StageRequest) -> StageRequest:
    if RUN_ID_RE.fullmatch(request.qualification_id) is None:
        raise R7S2StagerError("qualification_id_invalid")
    run_uuid = _uuid(request.run_uuid, "run_uuid")
    attempt_id = _uuid(request.attempt_id, "attempt_id")
    if run_uuid == attempt_id:
        raise R7S2StagerError("run_uuid_attempt_id_must_differ")
    return StageRequest(
        qualification_id=request.qualification_id,
        run_uuid=run_uuid,
        attempt_id=attempt_id,
        expected_source_commit=_hex(
            request.expected_source_commit, HEX40_RE, "expected_source_commit"
        ),
        expected_source_tree=_hex(request.expected_source_tree, HEX40_RE, "expected_source_tree"),
        expected_qualification_sha256=_hex(
            request.expected_qualification_sha256, HEX64_RE, "expected_qualification_sha256"
        ),
        expected_process_module_sha256=_hex(
            request.expected_process_module_sha256, HEX64_RE, "expected_process_module_sha256"
        ),
        expected_r7s1_runner_sha256=_hex(
            request.expected_r7s1_runner_sha256, HEX64_RE, "expected_r7s1_runner_sha256"
        ),
        expected_stager_sha256=_hex(
            request.expected_stager_sha256, HEX64_RE, "expected_stager_sha256"
        ),
        expected_outer_sha256=_hex(
            request.expected_outer_sha256, HEX64_RE, "expected_outer_sha256"
        ),
        parent_map_path=request.parent_map_path,
        expected_parent_map_sha256=_hex(
            request.expected_parent_map_sha256, HEX64_RE, "expected_parent_map_sha256"
        ),
    )


def _file_pin(path: Path, expected_sha256: str, *, version: str = "source") -> dict[str, Any]:
    _assert_no_reparse_chain(path)
    if not path.is_file():
        raise R7S2StagerError(f"pinned_file_missing:{path}")
    raw_size = path.stat().st_size
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise R7S2StagerError(f"pinned_file_sha256_mismatch:{path}")
    return {
        "path": str(path.resolve()),
        "sha256": actual_sha256,
        "bytes": raw_size,
        "version": version,
    }


def _measure_host_pins() -> dict[str, dict[str, Any]]:
    pins: dict[str, dict[str, Any]] = {}
    for role, expected in HOST_EXPECTATIONS.items():
        path = Path(str(expected["path"]))
        pin = _file_pin(path, str(expected["sha256"]), version=str(expected["version"]))
        if pin["bytes"] != expected["bytes"]:
            raise R7S2StagerError(f"host_{role}_bytes_mismatch")
        if role == "python":
            observed_version = platform.python_version()
        elif role == "system32_wsl":
            resource = Path(str(SYSTEM32_WSL_VERSION_RESOURCE["path"]))
            resource_pin = _file_pin(resource, str(SYSTEM32_WSL_VERSION_RESOURCE["sha256"]))
            if resource_pin["bytes"] != SYSTEM32_WSL_VERSION_RESOURCE["bytes"]:
                raise R7S2StagerError("system32_wsl_version_resource_bytes_mismatch")
            observed_version = _windows_file_version(resource)
        else:
            observed_version = _windows_file_version(path)
        if role == "python":
            if not _path_equal(path, Path(sys.executable)):
                raise R7S2StagerError("host_python_execution_path_mismatch")
        if role in {"store_wsl", "wslhost"} and (
            observed_version == "2.7.11.0" or expected["version"] == "2.7.11.0"
        ):
            raise R7S2StagerError("stale_wsl_2_7_11_pin_forbidden")
        if observed_version != expected["version"]:
            raise R7S2StagerError(f"host_{role}_version_mismatch:{observed_version}")
        pin["version"] = observed_version
        pins[role] = pin
    return pins


def _measure_source_pins(request: StageRequest) -> dict[str, dict[str, Any]]:
    expected = {
        "qualification_script": (QUALIFICATION_SCRIPT, request.expected_qualification_sha256),
        "process_module": (PROCESS_MODULE, request.expected_process_module_sha256),
        "r7s1_runner": (R7S1_RUNNER, request.expected_r7s1_runner_sha256),
        "stager": (STAGER_SCRIPT, request.expected_stager_sha256),
        "outer_launcher": (OUTER_LAUNCHER, request.expected_outer_sha256),
    }
    pins: dict[str, dict[str, Any]] = {}
    for role, (path, digest) in expected.items():
        pin = _file_pin(path, digest)
        raw = path.read_bytes()
        pin["relative_path"] = _source_relative_path(path)
        pin["worktree_blob_oid"] = _git_blob_oid(raw)
        pins[role] = pin
    return pins


def _strict_process_streams(
    outcome: Mapping[str, Any], label: str, *, stdout_allows_nul: bool = False
) -> tuple[str, str]:
    stdout = outcome.get("stdout")
    stderr = outcome.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise R7S2StagerError(f"{label}_stream_type_invalid")
    if stderr != "":
        raise R7S2StagerError(f"{label}_stderr_not_empty")
    for stream_name, stream in (("stdout", stdout), ("stderr", stderr)):
        if "\ufffd" in stream or any(0xD800 <= ord(character) <= 0xDFFF for character in stream):
            raise R7S2StagerError(f"{label}_{stream_name}_noninjective_decode")
        for character in stream:
            codepoint = ord(character)
            if codepoint == 0 and stdout_allows_nul and stream_name == "stdout":
                continue
            if codepoint < 0x20 and character not in {"\n", "\r", "\t"}:
                raise R7S2StagerError(f"{label}_{stream_name}_control_character")
            if codepoint == 0x7F:
                raise R7S2StagerError(f"{label}_{stream_name}_control_character")
    return stdout, stderr


def _strict_wsl_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise R7S2StagerError(f"{label}_type_invalid")
    if "\x00" in value or "\ufffd" in value:
        raise R7S2StagerError(f"{label}_noninjective_decode")
    if "\r" in value.replace("\r\n", ""):
        raise R7S2StagerError(f"{label}_bare_carriage_return")
    for character in value:
        codepoint = ord(character)
        if (
            0xD800 <= codepoint <= 0xDFFF
            or (codepoint < 0x20 and character not in {"\n", "\r", "\t"})
            or codepoint == 0x7F
        ):
            raise R7S2StagerError(f"{label}_control_character")
    normalized = value.replace("\r\n", "\n")
    if not normalized.endswith("\n") or "\n\n" in normalized:
        raise R7S2StagerError(f"{label}_record_termination_invalid")
    return normalized


def _require_git_identity(outcome: Mapping[str, Any], commit: str, tree: str) -> None:
    stdout, _ = _strict_process_streams(outcome, "git_identity")
    if stdout != f"{commit}\n{tree}\n":
        raise R7S2StagerError("source_commit_tree_mismatch")


def _require_ubuntu_verbose_running(value: Any, label: str) -> None:
    lines = _strict_wsl_text(value, label)[:-1].split("\n")
    if sum(line.strip() == "NAME STATE VERSION" for line in lines) != 1:
        raise R7S2StagerError(f"{label}_header_mismatch")
    ubuntu_rows: list[tuple[str, int]] = []
    for line in lines:
        if "Ubuntu" not in line:
            continue
        match = re.fullmatch(
            r"[ \t]*\*?[ \t]*Ubuntu[ \t]+(Running|Stopped)[ \t]+([12])[ \t]*", line
        )
        if match is None:
            raise R7S2StagerError(f"{label}_ubuntu_record_invalid")
        ubuntu_rows.append((match.group(1), int(match.group(2))))
    if ubuntu_rows != [("Running", 2)]:
        raise R7S2StagerError(f"{label}_ubuntu_not_running_version_2")


def _require_ubuntu_running_membership(value: Any, label: str) -> None:
    names: list[str] = []
    for line in _strict_wsl_text(value, label)[:-1].split("\n"):
        name = line.strip()
        if not name or name != line or "\t" in name:
            raise R7S2StagerError(f"{label}_distribution_record_invalid")
        names.append(name)
    if names.count("Ubuntu") != 1:
        raise R7S2StagerError(f"{label}_ubuntu_membership_mismatch")


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
        raise R7S2StagerError(f"{label}_json_invalid") from exc
    mapping = _mapping(decoded, label)
    if text != json.dumps(mapping, sort_keys=True, separators=(",", ":")) + "\n":
        raise R7S2StagerError(f"{label}_json_not_canonical")
    return mapping


def _parse_ls_tree_records(stdout: str) -> dict[str, dict[str, str]]:
    if (
        not stdout.endswith("\0")
        or "\0\0" in stdout
        or any(character in stdout for character in ("\n", "\r", "\ufffd"))
    ):
        raise R7S2StagerError("git_ls_tree_stream_invalid")
    result: dict[str, dict[str, str]] = {}
    for record in stdout[:-1].split("\0"):
        match = re.fullmatch(r"(100644|100755) (blob) ([0-9a-f]{40})\t([^\t\0]+)", record)
        if match is None:
            raise R7S2StagerError("git_ls_tree_record_invalid")
        mode, object_type, object_id, path = match.groups()
        if path in result:
            raise R7S2StagerError("git_ls_tree_duplicate_path")
        result[path] = {"mode": mode, "type": object_type, "oid": object_id}
    return result


def _parse_ls_files_records(stdout: str) -> dict[str, dict[str, str]]:
    if (
        not stdout.endswith("\0")
        or "\0\0" in stdout
        or any(character in stdout for character in ("\n", "\r", "\ufffd"))
    ):
        raise R7S2StagerError("git_ls_files_stream_invalid")
    result: dict[str, dict[str, str]] = {}
    for record in stdout[:-1].split("\0"):
        match = re.fullmatch(r"(H) (100644|100755) ([0-9a-f]{40}) (0)\t([^\t\0]+)", record)
        if match is None:
            raise R7S2StagerError("git_ls_files_record_invalid")
        flag, mode, object_id, stage, path = match.groups()
        if path in result:
            raise R7S2StagerError("git_ls_files_duplicate_path")
        result[path] = {
            "flag": flag,
            "mode": mode,
            "oid": object_id,
            "stage": stage,
        }
    return result


def _contained_source_bindings(
    request: StageRequest,
    source_pins: Mapping[str, Mapping[str, Any]],
    *,
    runner: Any,
    git_path: str,
    environment: Mapping[str, str],
    call_counts: dict[str, int],
    processes: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    relative_paths = [str(source_pins[role]["relative_path"]) for role in sorted(SOURCE_ROLES)]
    common = (git_path, "-c", "core.fsmonitor=false", "-c", "core.autocrlf=true")
    commands = {
        "git_source_ls_tree": (
            *common,
            "-C",
            str(GIT_ROOT),
            "ls-tree",
            "-rz",
            "--full-tree",
            request.expected_source_commit,
            "--",
            *relative_paths,
        ),
        "git_source_ls_files": (
            *common,
            "-C",
            str(GIT_ROOT),
            "ls-files",
            "-vz",
            "--stage",
            "--",
            *relative_paths,
        ),
    }
    aggregate: dict[str, dict[str, Any]] = {}
    for name, command in commands.items():
        call_counts[name] += 1
        aggregate[name] = _contained(
            runner,
            command,
            name=f"pre-r8-r7s2-stager-{name.replace('_', '-')}",
            cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
            env=environment,
            run_uuid=request.run_uuid,
        )
        processes[name] = aggregate[name]
    tree_stdout, _ = _strict_process_streams(
        aggregate["git_source_ls_tree"], "git_source_ls_tree", stdout_allows_nul=True
    )
    index_stdout, _ = _strict_process_streams(
        aggregate["git_source_ls_files"], "git_source_ls_files", stdout_allows_nul=True
    )
    tree = _parse_ls_tree_records(tree_stdout)
    index = _parse_ls_files_records(index_stdout)
    if set(tree) != set(relative_paths) or set(index) != set(relative_paths):
        raise R7S2StagerError("git_source_path_set_mismatch")

    result: dict[str, dict[str, Any]] = {}
    for role in sorted(SOURCE_ROLES):
        raw_pin = dict(source_pins[role])
        relative = str(raw_pin["relative_path"])
        tree_row = tree[relative]
        index_row = index[relative]
        if (
            tree_row["type"] != "blob"
            or tree_row["mode"] not in {"100644", "100755"}
            or index_row["flag"] != "H"
            or index_row["stage"] != "0"
            or index_row["mode"] != tree_row["mode"]
            or index_row["oid"] != tree_row["oid"]
            or HEX40_RE.fullmatch(tree_row["oid"]) is None
        ):
            raise R7S2StagerError(f"git_source_tracked_identity_invalid:{role}")
        name = f"git_source_hash_object_{role}"
        call_counts[name] += 1
        outcome = _contained(
            runner,
            (
                *common,
                "-C",
                str(GIT_ROOT),
                "hash-object",
                f"--path={relative}",
                str(Path(raw_pin["path"])),
            ),
            name=f"pre-r8-r7s2-stager-{name.replace('_', '-')}",
            cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
            env=environment,
            run_uuid=request.run_uuid,
        )
        processes[name] = outcome
        hash_stdout, _ = _strict_process_streams(outcome, name)
        normalized_oid = tree_row["oid"]
        if hash_stdout != f"{normalized_oid}\n":
            raise R7S2StagerError(f"git_source_normalized_blob_mismatch:{role}")
        raw_pin.update(
            {
                "git_mode": tree_row["mode"],
                "git_head_blob_oid": tree_row["oid"],
                "git_normalized_worktree_blob_oid": normalized_oid,
            }
        )
        result[role] = raw_pin
    return result


def _load_parent_map(request: StageRequest) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    path = request.parent_map_path
    if not path.is_absolute():
        raise R7S2StagerError("parent_map_path_must_be_absolute")
    _assert_no_reparse_chain(path)
    raw = path.read_bytes()
    actual_sha = _sha256_bytes(raw)
    if actual_sha != request.expected_parent_map_sha256:
        raise R7S2StagerError("parent_map_sha256_mismatch")
    mapping = _strict_json_bytes(raw, "parent_map")
    _exact_keys(mapping, {"schema", "parents"}, "parent_map")
    if mapping["schema"] != PARENT_MAP_SCHEMA:
        raise R7S2StagerError("parent_map_schema_mismatch")
    parents = _mapping(mapping["parents"], "parents")
    _exact_keys(parents, PARENT_ROLES, "parents")
    normalized: dict[str, dict[str, Any]] = {}
    for role in sorted(PARENT_ROLES):
        pin = _mapping(parents[role], f"parent_{role}")
        _exact_keys(pin, {"path", "sha256", "bytes"}, f"parent_{role}")
        parent_path = Path(str(pin["path"]))
        if not parent_path.is_absolute():
            raise R7S2StagerError(f"parent_{role}_path_not_absolute")
        expected_sha = _hex(pin["sha256"], HEX64_RE, f"parent_{role}_sha256")
        canonical = CANONICAL_PARENT_PINS[role]
        if (
            not _path_equal(parent_path, Path(str(canonical["path"])))
            or expected_sha != canonical["sha256"]
            or pin["bytes"] != canonical["bytes"]
        ):
            raise R7S2StagerError(f"parent_{role}_canonical_pin_mismatch")
        _assert_no_reparse_chain(parent_path)
        parent_raw = parent_path.read_bytes()
        if len(parent_raw) != pin["bytes"] or _sha256_bytes(parent_raw) != expected_sha:
            raise R7S2StagerError(f"parent_{role}_identity_mismatch")
        normalized[role] = {
            "path": str(parent_path.resolve()),
            "sha256": expected_sha,
            "bytes": len(parent_raw),
        }
        payload = _strict_json_bytes(parent_raw, f"parent_{role}")
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
            raise R7S2StagerError(f"parent_{role}_content_invariants_failed")
    return normalized, {
        "path": str(path.resolve()),
        "sha256": actual_sha,
        "bytes": len(raw),
    }


def _load_verified_module(path: Path, expected_sha256: str, name: str) -> types.ModuleType:
    raw = path.read_bytes()
    if _sha256_bytes(raw) != expected_sha256:
        raise R7S2StagerError(f"verified_module_sha256_mismatch:{name}")
    module_name = f"_evm_r7s2_{name}_{expected_sha256[:16]}"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _runner_factory(process_module_sha256: str, *, linux: bool) -> Any:
    if not _path_equal(PROCESS_MODULE, Path(TRUSTED_PROCESS_RUNTIME["path"])):
        raise R7S2StagerError("process_runtime_trusted_path_mismatch")
    _assert_no_reparse_chain(PROCESS_MODULE)
    raw = PROCESS_MODULE.read_bytes()
    normalized = _lf_normalized_source(raw)
    if (
        _sha256_bytes(raw) != process_module_sha256
        or _sha256_bytes(normalized) != TRUSTED_PROCESS_RUNTIME["lf_normalized_sha256"]
        or _git_blob_oid(normalized) != TRUSTED_PROCESS_RUNTIME["git_head_blob_oid"]
    ):
        raise R7S2StagerError("process_runtime_trusted_content_mismatch")
    module = _load_verified_module(PROCESS_MODULE, process_module_sha256, "process_runtime")
    timeout = module.TimeoutContract(
        kubectl_timeout_seconds=1,
        wrapper_timeout_seconds=20 if linux else 5,
        restore_deadline_seconds=45 if linux else 16,
        residual_repoll_seconds=10 if linux else 3,
        stream_drain_seconds=5 if linux else 3,
    )
    return module.WindowsJobProcessRunner(timeout)


def _minimal_windows_environment(system32_wsl: Path) -> dict[str, str]:
    windows_root = system32_wsl.parent.parent
    return {"SystemRoot": str(windows_root), "WINDIR": str(windows_root), "WSL_UTF8": "1"}


def _minimal_git_environment() -> dict[str, str]:
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
        raise R7S2StagerError(f"{label}_job_timeline_missing")
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
        raise R7S2StagerError(f"{label}_event_schema_mismatch")
    if any(not isinstance(item, Mapping) or set(item) != accounting_keys for item in accounting):
        raise R7S2StagerError(f"{label}_accounting_schema_mismatch")
    if any(not isinstance(item, Mapping) or set(item) != identity_keys for item in identities):
        raise R7S2StagerError(f"{label}_identity_schema_mismatch")

    def utc_value(value: Any) -> datetime:
        if not isinstance(value, str):
            raise R7S2StagerError(f"{label}_timestamp_type_mismatch")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise R7S2StagerError(f"{label}_timestamp_invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise R7S2StagerError(f"{label}_timestamp_not_utc")
        return parsed

    started = utc_value(outcome.get("started_at_utc"))
    ended = utc_value(outcome.get("ended_at_utc"))
    if started > ended:
        raise R7S2StagerError(f"{label}_timestamp_envelope_invalid")
    for event in events:
        if (
            type(event["sequence"]) is not int
            or event["sequence"] <= 0
            or not isinstance(event["event"], str)
            or not event["event"]
            or type(event["monotonic_ns"]) is not int
            or event["monotonic_ns"] <= 0
            or not isinstance(event["timestamp_utc"], str)
            or not isinstance(event["details"], Mapping)
            or (event["pid"] is not None and (type(event["pid"]) is not int or event["pid"] <= 0))
        ):
            raise R7S2StagerError(f"{label}_event_type_mismatch")
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
        raise R7S2StagerError(f"{label}_timeline_sequence_mismatch")
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
        raise R7S2StagerError(f"{label}_lifecycle_event_mismatch")
    root_pid = by_name["root_created_suspended"]["pid"]
    root_identity_events = [
        item for item in events if item["event"] == "identity_observed" and item["pid"] == root_pid
    ]
    if len(root_identity_events) != 1:
        raise R7S2StagerError(f"{label}_root_identity_event_mismatch")
    ordered_sequences = [
        by_name["job_created"]["sequence"],
        by_name["root_created_suspended"]["sequence"],
        by_name["job_membership_verified"]["sequence"],
        root_identity_events[0]["sequence"],
        by_name["root_resumed"]["sequence"],
        by_name["active_process_count_zero"]["sequence"],
        by_name["streams_drained"]["sequence"],
    ]
    if ordered_sequences != sorted(ordered_sequences):
        raise R7S2StagerError(f"{label}_lifecycle_order_mismatch")
    forbidden_events = {
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
        item["event"] in forbidden_events or item["event"].startswith("job_message_")
        for item in events
    ):
        raise R7S2StagerError(f"{label}_forbidden_event")
    allowed_events = {
        *unique_required,
        "identity_observed",
        "job_new_process",
        "job_exit_process",
        "job_active_process_zero",
    }
    if any(item["event"] not in allowed_events for item in events):
        raise R7S2StagerError(f"{label}_unknown_event")
    if (
        type(root_pid) is not int
        or root_pid <= 0
        or by_name["job_created"]["details"] != {"run_uuid": run_uuid}
        or by_name["job_membership_verified"]["pid"] != root_pid
        or by_name["job_membership_verified"]["details"]
        != {"active_processes": 1, "job_limit_flags": 0}
        or by_name["root_resumed"]["pid"] != root_pid
    ):
        raise R7S2StagerError(f"{label}_root_lifecycle_mismatch")
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
            raise R7S2StagerError(f"{label}_identity_binding_mismatch")
        stable_keys.add(key)
    if len({identity["pid"] for identity in identities}) != len(identities):
        raise R7S2StagerError(f"{label}_pid_reuse_ambiguous")
    observed_sequence_by_pid = {
        identity["pid"]: identity["observed_sequence"] for identity in identities
    }
    root_identities = [item for item in identities if item["pid"] == root_pid]
    if len(root_identities) != 1 or os.path.normcase(
        root_identities[0]["image"]
    ) != os.path.normcase(expected_image):
        raise R7S2StagerError(f"{label}_root_image_mismatch")
    prior_total = -1
    prior_terminated = -1
    for snapshot in accounting:
        if (
            type(snapshot["sequence"]) is not int
            or type(snapshot["monotonic_ns"]) is not int
            or type(snapshot["total_processes"]) is not int
            or type(snapshot["active_processes"]) is not int
            or type(snapshot["total_terminated_processes"]) is not int
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
            raise R7S2StagerError(f"{label}_accounting_counter_mismatch")
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
        raise R7S2StagerError(f"{label}_final_accounting_mismatch")


def _contained(
    runner: Any,
    command: Sequence[str],
    *,
    name: str,
    cwd: Path | None,
    env: Mapping[str, str],
    run_uuid: str,
) -> dict[str, Any]:
    try:
        outcome = runner.run(
            tuple(command),
            name=name,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env),
            poll_interval_seconds=0.01,
            run_uuid=run_uuid,
        )
        evidence = json.loads(json.dumps(outcome.to_dict(), allow_nan=False))
    except Exception as exc:
        typed = getattr(exc, "to_dict", None)
        try:
            evidence = typed() if callable(typed) else {}
        except Exception as evidence_exc:
            evidence = {
                "evidence_extraction_error": f"{type(evidence_exc).__name__}:{evidence_exc}"
            }
        raise R7S2StagerError(
            f"contained_process_exception:{name}:{type(exc).__name__}:{exc}",
            process_evidence=evidence,
        ) from exc
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
        raise R7S2StagerError(f"contained_process_unsafe:{name}", process_evidence=evidence)
    _validate_success_job_timeline(
        evidence,
        label=name,
        run_uuid=run_uuid,
        expected_image=str(command[0]),
    )
    normalized_command = [str(item) for item in command]
    evidence["invocation"] = {
        "name": name,
        "command": normalized_command,
        "argv_sha256": _sha256_bytes(
            json.dumps(normalized_command, separators=(",", ":")).encode("utf-8")
        ),
        "cwd": str(cwd) if cwd is not None else None,
        "environment_keys": sorted(env),
        "run_uuid": run_uuid,
    }
    return evidence


def _listed_ubuntu_version(outcome: Mapping[str, Any]) -> int:
    stdout, _ = _strict_process_streams(outcome, "ubuntu_verbose")
    _require_ubuntu_verbose_running(stdout, "ubuntu_verbose")
    return 2


def _running_ubuntu(outcome: Mapping[str, Any]) -> bool:
    stdout, _ = _strict_process_streams(outcome, "ubuntu_running")
    _require_ubuntu_running_membership(stdout, "ubuntu_running")
    return True


def _parse_linux_discovery(outcome: Mapping[str, Any]) -> dict[str, Any]:
    stdout, _ = _strict_process_streams(outcome, "linux_discovery")
    value = _canonical_json_line(stdout, "linux_discovery")
    _exact_keys(
        value,
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
        "linux_discovery",
    )
    if value["schema"] != LINUX_DISCOVERY_SCHEMA or value["status"] != "observed":
        raise R7S2StagerError("linux_discovery_schema_or_status_mismatch")
    if value["distro"] != "Ubuntu":
        raise R7S2StagerError("linux_discovery_distro_mismatch")
    _uuid(value["boot_id"], "linux_boot_id")
    for key in ("rootfs_identity", "os_release_sha256", "machine_id_sha256"):
        _hex(value[key], HEX64_RE, f"linux_{key}")
    for key in ("kernel_release", "distro_version"):
        if not isinstance(value[key], str) or not value[key]:
            raise R7S2StagerError(f"linux_{key}_invalid")
    binaries = _mapping(value["binaries"], "linux_binaries")
    _exact_keys(binaries, {"python3", "env", "setsid"}, "linux_binaries")
    normalized: dict[str, dict[str, Any]] = {}
    for role in ("python3", "env", "setsid"):
        pin = _mapping(binaries[role], f"linux_{role}")
        _exact_keys(
            pin,
            {"candidate_path", "realpath", "sha256", "bytes", "version"},
            f"linux_{role}",
        )
        realpath = str(pin["realpath"])
        if not realpath.startswith("/") or realpath == str(pin["candidate_path"]):
            if role == "python3" and not realpath.startswith("/"):
                raise R7S2StagerError(f"linux_{role}_realpath_invalid")
        if not realpath.startswith("/"):
            raise R7S2StagerError(f"linux_{role}_realpath_invalid")
        version = str(pin["version"])
        if not version:
            raise R7S2StagerError(f"linux_{role}_version_missing")
        size = pin["bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise R7S2StagerError(f"linux_{role}_bytes_invalid")
        normalized[role] = {
            "path": realpath,
            "sha256": _hex(pin["sha256"], HEX64_RE, f"linux_{role}_sha256"),
            "bytes": size,
            "version": version,
        }
    value["binaries"] = normalized
    return value


def _partial_inventory(directory: Path) -> list[dict[str, Any]]:
    _assert_no_reparse_chain(directory, allow_missing_leaf=True)
    if not directory.exists():
        return []
    _assert_no_reparse_chain(directory)
    result: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        _assert_no_reparse_chain(path)
        if not path.is_file():
            raise R7S2StagerError("staging_partial_non_file_forbidden")
        result.append(
            {
                "name": path.name,
                "path": str(path),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return result


def _failure_payload(
    request: StageRequest,
    paths: StagePaths,
    *,
    failed_stage: str,
    exception: Exception,
    partial: Mapping[str, Any],
    call_counts: Mapping[str, int],
) -> dict[str, Any]:
    process_evidence = exception.process_evidence if isinstance(exception, R7S2StagerError) else {}
    forced = int(process_evidence.get("forced_termination_attempts", 0) or 0)
    residual = process_evidence.get("residual_pids", [])
    return {
        "schema": FAILURE_SCHEMA,
        "qualification_id": request.qualification_id,
        "run_uuid": request.run_uuid,
        "attempt_id": request.attempt_id,
        "sealed_at_utc": datetime.now(UTC).isoformat(),
        "status": "manual_intervention_required",
        "credit": "zero_credit",
        "failed_stage": failed_stage,
        "exception_type": type(exception).__name__,
        "exception": str(exception),
        "process_evidence": process_evidence,
        "partial": dict(partial),
        "partial_inventory": _partial_inventory(paths.staging_directory),
        "call_counts": dict(call_counts),
        "residual_pids": residual if isinstance(residual, list) else [],
        "automatic_retry_count": 0,
        "forced_termination_attempts": forced,
        "wsl_shutdown_calls": 0,
        "docker_kubernetes_service_mutations": 0,
        "contract_usable": False,
        "qualification_started": False,
        "r8_started": False,
    }


def _emergency(
    request: StageRequest,
    paths: StagePaths,
    *,
    failed_stage: str,
    exception: Exception,
    partial: Mapping[str, Any],
    call_counts: Mapping[str, int],
) -> dict[str, Any]:
    process_evidence = exception.process_evidence if isinstance(exception, R7S2StagerError) else {}
    residual = process_evidence.get("residual_pids", [])
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
    forced = int(process_evidence.get("forced_termination_attempts", 0) or 0)
    try:
        _assert_no_reparse_chain(paths.emergency_directory, allow_missing_leaf=True)
        os.mkdir(paths.emergency_directory)
        _assert_no_reparse_chain(paths.emergency_directory)
        payload = {
            "schema": EMERGENCY_SCHEMA,
            "qualification_id": request.qualification_id,
            "run_uuid": request.run_uuid,
            "attempt_id": request.attempt_id,
            "sealed_at_utc": datetime.now(UTC).isoformat(),
            "status": "manual_intervention_required",
            "credit": "zero_credit",
            "failed_stage": failed_stage,
            "exception_type": type(exception).__name__,
            "exception": str(exception),
            "process_evidence": process_evidence,
            "residual_pids": residual_pids,
            "primary_staging_directory": str(paths.staging_directory),
            "partial": dict(partial),
            "partial_inventory": _partial_inventory(paths.staging_directory),
            "call_counts": dict(call_counts),
            "automatic_retry_count": 0,
            "forced_termination_attempts": forced,
            "wsl_shutdown_calls": 0,
            "docker_kubernetes_service_mutations": 0,
            "contract_usable": False,
            "qualification_started": False,
            "r8_started": False,
        }
        pin = _atomic_exclusive_json(paths.emergency_directory / "emergency-seal.json", payload)
    except Exception as emergency_exc:
        raise R7S2StagerError("staging_emergency_seal_failed_no_retry") from emergency_exc
    return {
        "passed": False,
        "manual_intervention_required": True,
        "emergency_seal": pin,
        "failed_stage": failed_stage,
    }


def _publish_failure(
    request: StageRequest,
    paths: StagePaths,
    *,
    failed_stage: str,
    exception: Exception,
    partial: Mapping[str, Any],
    call_counts: Mapping[str, int],
) -> dict[str, Any]:
    try:
        seal = _atomic_exclusive_json(
            paths.staging_directory / "staging-failure-seal.json",
            _failure_payload(
                request,
                paths,
                failed_stage=failed_stage,
                exception=exception,
                partial=partial,
                call_counts=call_counts,
            ),
        )
    except Exception as seal_exc:
        original_evidence = (
            exception.process_evidence if isinstance(exception, R7S2StagerError) else {}
        )
        return _emergency(
            request,
            paths,
            failed_stage="staging_failure_seal_publication",
            exception=R7S2StagerError(
                "staging_failure_seal_publication_failed:"
                f"{type(seal_exc).__name__}:{seal_exc};"
                f"original={type(exception).__name__}:{exception}",
                process_evidence=original_evidence,
            ),
            partial=partial,
            call_counts=call_counts,
        )
    try:
        index = _atomic_exclusive_json(
            paths.staging_directory / "failure-index.json",
            {
                "schema": INDEX_SCHEMA,
                "status": "zero_credit_failure",
                "qualification_id": request.qualification_id,
                "run_uuid": request.run_uuid,
                "attempt_id": request.attempt_id,
                "indexed_at_utc": datetime.now(UTC).isoformat(),
                "failure_seal": seal,
                "contract_usable": False,
                "qualification_started": False,
                "r8_started": False,
            },
        )
    except Exception as index_exc:
        original_evidence = (
            exception.process_evidence if isinstance(exception, R7S2StagerError) else {}
        )
        return _emergency(
            request,
            paths,
            failed_stage="staging_failure_index_publication",
            exception=R7S2StagerError(
                "staging_failure_index_publication_failed:"
                f"{type(index_exc).__name__}:{index_exc};"
                f"original={type(exception).__name__}:{exception}",
                process_evidence=original_evidence,
            ),
            partial=partial,
            call_counts=call_counts,
        )
    return {"passed": False, "failure_seal": seal, "index": index}


def _stage_once_impl(
    request: StageRequest,
    *,
    bootstrap_attestation: Mapping[str, Any],
    evidence_root: Path = CANONICAL_EVIDENCE_ROOT,
    token_measure: Callable[[], Mapping[str, Any]] = _measure_admin_token,
    metadata_runner: Any | None = None,
    linux_runner: Any | None = None,
) -> dict[str, Any]:
    request = _validate_request(request)
    paths = _paths(request, evidence_root)
    _assert_no_reparse_chain(evidence_root)
    token = dict(token_measure())
    _require_admin(token)
    _assert_no_reparse_chain(STAGER_INVOCATION_PATH)
    host_pins = _measure_host_pins()
    git_config = _git_config_readback()
    source_pins = _measure_source_pins(request)
    parents, parent_map_pin = _load_parent_map(request)
    for path in (
        paths.staging_directory,
        paths.emergency_directory,
        paths.qualification_directory,
        paths.qualification_emergency_directory,
        paths.outer_directory,
        paths.outer_emergency_directory,
    ):
        _assert_no_reparse_chain(path, allow_missing_leaf=True)
        if path.exists():
            raise FileExistsError(path)

    try:
        os.mkdir(paths.staging_directory)
        _assert_no_reparse_chain(paths.staging_directory)
    except FileExistsError:
        raise
    except Exception as directory_exc:
        return _emergency(
            request,
            paths,
            failed_stage="staging_directory_creation",
            exception=directory_exc,
            partial={},
            call_counts={},
        )

    call_counts = {
        "git_identity": 0,
        "git_status": 0,
        "git_source_ls_tree": 0,
        "git_source_ls_files": 0,
        **{f"git_source_hash_object_{role}": 0 for role in sorted(SOURCE_ROLES)},
        "ubuntu_verbose_pre": 0,
        "ubuntu_running_pre": 0,
        "linux_identity_readback": 0,
        "ubuntu_verbose_post": 0,
        "ubuntu_running_post": 0,
        "automatic_retries": 0,
        "forced_termination_attempts": 0,
        "wsl_shutdown_calls": 0,
        "docker_kubernetes_service_mutations": 0,
        "qualification_calls": 0,
        "r8_calls": 0,
    }
    partial: dict[str, Any] = {
        "stager_bootstrap_attestation": dict(bootstrap_attestation),
        "token_evidence": token,
        "host_binaries": host_pins,
        "source_pins": source_pins,
        "git_config": git_config,
        "parent_map": parent_map_pin,
        "parent_evidence": parents,
        "processes": {},
    }
    reservation = {
        "schema": RESERVATION_SCHEMA,
        "qualification_id": request.qualification_id,
        "run_uuid": request.run_uuid,
        "attempt_id": request.attempt_id,
        "reserved_at_utc": datetime.now(UTC).isoformat(),
        "expected_source_commit": request.expected_source_commit,
        "expected_source_tree": request.expected_source_tree,
        "expected_source_sha256": {
            "qualification_script": request.expected_qualification_sha256,
            "process_module": request.expected_process_module_sha256,
            "r7s1_runner": request.expected_r7s1_runner_sha256,
            "stager": request.expected_stager_sha256,
            "outer_launcher": request.expected_outer_sha256,
        },
        "parent_map": parent_map_pin,
        "stager_bootstrap_attestation": dict(bootstrap_attestation),
        "automatic_retry_budget": 0,
        "forced_termination_budget": 0,
        "service_mutation_budget": 0,
        "linux_identity_readback_budget": 1,
        "qualification_budget": 0,
    }
    try:
        reservation_pin = _atomic_exclusive_json(
            paths.staging_directory / "staging-reservation.json", reservation
        )
    except Exception as reservation_exc:
        return _emergency(
            request,
            paths,
            failed_stage="staging_reservation_publication",
            exception=reservation_exc,
            partial=partial,
            call_counts=call_counts,
        )

    metadata_runner = metadata_runner or _runner_factory(
        request.expected_process_module_sha256, linux=False
    )
    linux_runner = linux_runner or _runner_factory(
        request.expected_process_module_sha256, linux=True
    )
    git_path = host_pins["git"]["path"]
    wsl_path = host_pins["system32_wsl"]["path"]
    git_environment = _minimal_git_environment()
    wsl_environment = _minimal_windows_environment(Path(wsl_path))
    stage = "git_identity"
    try:
        call_counts[stage] += 1
        git_identity = _contained(
            metadata_runner,
            (
                git_path,
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(GIT_ROOT),
                "rev-parse",
                "HEAD",
                "HEAD^{tree}",
            ),
            name="pre-r8-r7s2-stager-git-identity",
            cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
            env=git_environment,
            run_uuid=request.run_uuid,
        )
        partial["processes"][stage] = git_identity
        _require_git_identity(
            git_identity, request.expected_source_commit, request.expected_source_tree
        )

        stage = "git_status"
        call_counts[stage] += 1
        git_status = _contained(
            metadata_runner,
            (
                git_path,
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(GIT_ROOT),
                "status",
                "--porcelain=v1",
                "--untracked-files=no",
            ),
            name="pre-r8-r7s2-stager-git-status",
            cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
            env=git_environment,
            run_uuid=request.run_uuid,
        )
        partial["processes"][stage] = git_status
        status_stdout, _ = _strict_process_streams(git_status, "git_status")
        if status_stdout != "":
            raise R7S2StagerError("tracked_worktree_not_clean")

        stage = "git_source_bindings"
        source_pins = _contained_source_bindings(
            request,
            source_pins,
            runner=metadata_runner,
            git_path=git_path,
            environment=git_environment,
            call_counts=call_counts,
            processes=partial["processes"],
        )
        partial["source_pins"] = source_pins

        state_commands = {
            "ubuntu_verbose_pre": (wsl_path, "--list", "--verbose"),
            "ubuntu_running_pre": (wsl_path, "--list", "--running", "--quiet"),
        }
        state_outcomes: dict[str, dict[str, Any]] = {}
        for stage, command in state_commands.items():
            call_counts[stage] += 1
            state_outcomes[stage] = _contained(
                metadata_runner,
                command,
                name=f"pre-r8-r7s2-stager-{stage.replace('_', '-')}",
                cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
                env=wsl_environment,
                run_uuid=request.run_uuid,
            )
            partial["processes"][stage] = state_outcomes[stage]
        _listed_ubuntu_version(state_outcomes["ubuntu_verbose_pre"])
        _running_ubuntu(state_outcomes["ubuntu_running_pre"])
        partial["ubuntu_state_pre"] = {"distribution": "Ubuntu", "state": "Running", "version": 2}

        stage = "linux_identity_readback"
        call_counts[stage] += 1
        linux_process = _contained(
            linux_runner,
            (
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
                f"EVM_PHASE_B2_RUN_UUID={request.run_uuid}",
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                "-c",
                LINUX_DISCOVERY_SOURCE,
                "Ubuntu",
                "/usr/bin/env",
                "/usr/bin/setsid",
            ),
            name="pre-r8-r7s2-stager-linux-identity-readback-exactly-once",
            cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
            env=wsl_environment,
            run_uuid=request.run_uuid,
        )
        partial["processes"][stage] = linux_process
        linux = _parse_linux_discovery(linux_process)
        partial["linux_identity"] = linux

        post_commands = {
            "ubuntu_verbose_post": (wsl_path, "--list", "--verbose"),
            "ubuntu_running_post": (wsl_path, "--list", "--running", "--quiet"),
        }
        post_outcomes: dict[str, dict[str, Any]] = {}
        for stage, command in post_commands.items():
            call_counts[stage] += 1
            post_outcomes[stage] = _contained(
                metadata_runner,
                command,
                name=f"pre-r8-r7s2-stager-{stage.replace('_', '-')}",
                cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
                env=wsl_environment,
                run_uuid=request.run_uuid,
            )
            partial["processes"][stage] = post_outcomes[stage]
        _listed_ubuntu_version(post_outcomes["ubuntu_verbose_post"])
        _running_ubuntu(post_outcomes["ubuntu_running_post"])
        partial["ubuntu_state_post"] = {"distribution": "Ubuntu", "state": "Running", "version": 2}

        qualification_module = _load_verified_module(
            QUALIFICATION_SCRIPT,
            request.expected_qualification_sha256,
            "qualification_contract",
        )
        source_contract_pins = {
            role: {key: source_pins[role][key] for key in SOURCE_PIN_FIELDS}
            for role in (
                "qualification_script",
                "process_module",
                "r7s1_runner",
                "outer_launcher",
            )
        }
        stager_contract_pin = {key: source_pins["stager"][key] for key in SOURCE_PIN_FIELDS}
        host_contract_pins = {
            role: {key: host_pins[role][key] for key in ("path", "sha256", "bytes", "version")}
            for role in ("python", "system32_wsl", "store_wsl", "wslhost")
        }
        platform_identity = {
            "windows_build": platform.version(),
            "wsl_package_version": host_pins["store_wsl"]["version"],
            "kernel_release": linux["kernel_release"],
            "distro_version": linux["distro_version"],
            "rootfs_identity": linux["rootfs_identity"],
            "os_release_sha256": linux["os_release_sha256"],
            "machine_id_sha256": linux["machine_id_sha256"],
            "boot_id": linux["boot_id"],
        }
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=STAGING_ATTESTATION_TTL_SECONDS)
        stage = "preauthorization_index_publication"
        index = {
            "schema": INDEX_SCHEMA,
            "stager_schema": STAGER_SCHEMA,
            "status": "staging_authorized_contract_pending",
            "qualification_id": request.qualification_id,
            "run_uuid": request.run_uuid,
            "attempt_id": request.attempt_id,
            "indexed_at_utc": issued_at.isoformat(),
            "expires_at_utc": expires_at.isoformat(),
            "contract_expected_path": str(paths.contract_path),
            "source_identity": {
                "commit": request.expected_source_commit,
                "tree": request.expected_source_tree,
                "source_pins": {**source_contract_pins, "stager": stager_contract_pin},
                "git_config": git_config,
            },
            "token_evidence": token,
            "host_binaries": host_contract_pins,
            "linux_identity": linux,
            "ubuntu_state_pre": partial["ubuntu_state_pre"],
            "ubuntu_state_post": partial["ubuntu_state_post"],
            "parent_map": parent_map_pin,
            "parent_evidence": parents,
            "reservation": reservation_pin,
            "stager_bootstrap_attestation": dict(bootstrap_attestation),
            "process_evidence": partial["processes"],
            "call_counts": call_counts,
            "qualification_invocation_policy": {
                "execution_route": "outer_launcher_only",
                "outer_launcher": source_pins["outer_launcher"]["path"],
                "qualification_script_sha256": source_pins["qualification_script"]["sha256"],
                "contract_and_final_index_sha256": "out_of_band_required",
            },
            "automatic_retry_count": 0,
            "forced_termination_attempts": 0,
            "wsl_shutdown_calls": 0,
            "docker_kubernetes_service_mutations": 0,
            "qualification_started": False,
            "r8_started": False,
        }
        preauthorization_pin = _atomic_exclusive_json(paths.preauthorization_path, index)
        partial["preauthorization_index"] = preauthorization_pin

        contract = {
            "schema": qualification_module.CONTRACT_SCHEMA,
            "qualification_id": request.qualification_id,
            "evidence_leaf": paths.qualification_directory.name,
            "run_uuid": request.run_uuid,
            "attempt_id": request.attempt_id,
            "evidence_root": str(paths.root.resolve()),
            "distribution": "Ubuntu",
            "host_binaries": host_contract_pins,
            "linux_binaries": linux["binaries"],
            "platform_identity": platform_identity,
            "source_identity": {
                "commit": request.expected_source_commit,
                "tree": request.expected_source_tree,
                "stager": stager_contract_pin,
                "git": host_pins["git"],
                "git_config": git_config,
            },
            "staging_attestation": {
                "schema": ATTESTATION_SCHEMA,
                "issued_at_utc": issued_at.isoformat(),
                "expires_at_utc": expires_at.isoformat(),
                "preauthorization_index": preauthorization_pin,
            },
            "parent_evidence": parents,
            "source_pins": source_contract_pins,
            "timeouts": dict(CONTRACT_TIMEOUTS),
            "outer_timeout_contract": dict(OUTER_TIMEOUT_CONTRACT),
            "fixture": {
                "source_sha256": _sha256_bytes(
                    qualification_module.DETACHED_DESCENDANT_SOURCE.encode("utf-8")
                ),
                "lifetime_seconds": FIXTURE_LIFETIME_SECONDS,
            },
            "invocation_policy": dict(qualification_module.INVOCATION_POLICY),
        }
        validation_path, validation_sha = _temporary_json_for_validation(
            paths.staging_directory, contract
        )
        try:
            qualification_module.load_contract(
                validation_path,
                expected_sha256=validation_sha,
                expected_evidence_root=paths.root,
                allow_unpublished_validation=True,
            )
        finally:
            validation_path.unlink(missing_ok=True)

        stage = "qualification_contract_publication"
        contract_pin = _atomic_exclusive_json(paths.contract_path, contract)
        partial["contract"] = contract_pin
        stage = "final_launch_index_publication"
        outer_lf_sha256 = _sha256_bytes(
            _lf_normalized_source(Path(source_pins["outer_launcher"]["path"]).read_bytes())
        )
        bootstrap = {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.outer-bootstrap.v1",
            "source_sha256": _sha256_bytes(OUTER_BOOTSTRAP_SOURCE.encode("utf-8")),
            "line_ending_policy": "uniform_lf_or_uniform_crlf_normalized_to_lf_bare_cr_forbidden",
            "outer_lf_normalized_sha256": outer_lf_sha256,
            "expected_source_commit": request.expected_source_commit,
            "expected_source_tree": request.expected_source_tree,
        }
        invocation_policy = {
            "python": host_pins["python"]["path"],
            "isolated_flags": ["-I", "-S", "-B"],
            "execution_route": "python_c_sha_pinned_bootstrap_then_outer",
            "bootstrap_source_sha256": bootstrap["source_sha256"],
            "outer_launcher": source_pins["outer_launcher"]["path"],
            "contract_path": str(paths.contract_path),
            "launch_index_path": str(paths.index_path),
            "contract_sha256_source": "out_of_band_required",
            "launch_index_sha256_source": "out_of_band_required",
            "outer_sha256_source": "out_of_band_required",
            "source_commit_tree_source": "out_of_band_required",
            "execute_flag": "--execute-non-credit-once",
        }
        final_index = {
            "schema": qualification_module.LAUNCH_INDEX_SCHEMA,
            "status": "ready_non_credit_not_executed",
            "qualification_id": request.qualification_id,
            "run_uuid": request.run_uuid,
            "attempt_id": request.attempt_id,
            "published_at_utc": datetime.now(UTC).isoformat(),
            "expires_at_utc": expires_at.isoformat(),
            "contract": contract_pin,
            "preauthorization_index": preauthorization_pin,
            "reservation": reservation_pin,
            "stager_bootstrap_attestation": dict(bootstrap_attestation),
            "source_identity": {
                "commit": request.expected_source_commit,
                "tree": request.expected_source_tree,
                "source_pins": {**source_contract_pins, "stager": stager_contract_pin},
                "git_config": git_config,
            },
            "bootstrap": bootstrap,
            "outer_timeout_contract": dict(OUTER_TIMEOUT_CONTRACT),
            "outer_evidence_leaf": paths.outer_directory.name,
            "outer_evidence_directory": str(paths.outer_directory),
            "qualification_invocation_policy": invocation_policy,
            "automatic_retry_count": 0,
            "forced_termination_attempts": 0,
            "wsl_shutdown_calls": 0,
            "docker_kubernetes_service_mutations": 0,
            "qualification_started": False,
            "r8_started": False,
        }
        final_index_pin = _atomic_exclusive_json(paths.index_path, final_index)
        partial["final_launch_index"] = final_index_pin
        command = [
            host_pins["python"]["path"],
            "-I",
            "-S",
            "-B",
            "-c",
            OUTER_BOOTSTRAP_SOURCE,
            source_pins["outer_launcher"]["path"],
            source_pins["outer_launcher"]["sha256"],
            str(source_pins["outer_launcher"]["bytes"]),
            outer_lf_sha256,
            str(paths.index_path),
            final_index_pin["sha256"],
            request.expected_source_commit,
            request.expected_source_tree,
            str(paths.contract_path),
            contract_pin["sha256"],
            "--contract",
            str(paths.contract_path),
            "--expected-contract-sha256",
            contract_pin["sha256"],
            "--launch-index",
            str(paths.index_path),
            "--expected-launch-index-sha256",
            final_index_pin["sha256"],
            "--expected-outer-sha256",
            source_pins["outer_launcher"]["sha256"],
            "--expected-source-commit",
            request.expected_source_commit,
            "--expected-source-tree",
            request.expected_source_tree,
            "--execute-non-credit-once",
        ]
        return {
            "passed": True,
            "status": "staged_non_credit_not_executed",
            "contract": contract_pin,
            "preauthorization_index": preauthorization_pin,
            "index": final_index_pin,
            "stager_bootstrap_attestation": dict(bootstrap_attestation),
            "outer_command": command,
            "qualification_started": False,
            "r8_started": False,
        }
    except Exception as exc:
        return _publish_failure(
            request,
            paths,
            failed_stage=stage,
            exception=exc,
            partial=partial,
            call_counts=call_counts,
        )


def stage_once(request: StageRequest) -> dict[str, Any]:
    """Production entry point with fixed real gates and canonical append-only root."""
    if R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED is not True:
        raise R7S2StagerError("r7s2_out_of_band_root_anchor_required")
    bootstrap_attestation = _require_stager_bootstrap_attestation()
    return _stage_once_impl(
        request,
        bootstrap_attestation=bootstrap_attestation,
        evidence_root=CANONICAL_EVIDENCE_ROOT,
        token_measure=_measure_admin_token,
        metadata_runner=None,
        linux_runner=None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage the non-credit pre-r8 r7s2 WSL contract")
    parser.add_argument("--qualification-id", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-source-tree", required=True)
    parser.add_argument("--expected-qualification-sha256", required=True)
    parser.add_argument("--expected-process-module-sha256", required=True)
    parser.add_argument("--expected-r7s1-runner-sha256", required=True)
    parser.add_argument("--expected-stager-sha256", required=True)
    parser.add_argument("--expected-outer-sha256", required=True)
    parser.add_argument("--parent-map", type=Path, required=True)
    parser.add_argument("--expected-parent-map-sha256", required=True)
    parser.add_argument("--execute-stage-non-credit-once", action="store_true")
    return parser


def _require_stager_bootstrap_attestation() -> dict[str, Any]:
    value = globals().get("__evm_r7s2_stager_bootstrap_attestation__")
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
    if (
        len(sys.orig_argv) < 6
        or sys.orig_argv[1:5] != ["-I", "-S", "-B", "-c"]
        or _sha256_bytes(sys.orig_argv[5].encode("utf-8")) != STAGER_BOOTSTRAP_SOURCE_SHA256
        or attestation["schema"]
        != "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.stager-bootstrap-attestation.v1"
        or attestation["bootstrap_source_sha256"] != STAGER_BOOTSTRAP_SOURCE_SHA256
        or not _path_equal(Path(str(attestation["stager_path"])), STAGER_INVOCATION_PATH)
        or attestation["stager_argv_sha256"]
        != _sha256_bytes(json.dumps(sys.argv[1:], separators=(",", ":")).encode("utf-8"))
    ):
        raise R7S2StagerError("verified_stager_bootstrap_required")
    _assert_no_reparse_chain(STAGER_INVOCATION_PATH)
    raw = STAGER_INVOCATION_PATH.read_bytes()
    normalized = _lf_normalized_source(raw)
    if (
        attestation["stager_sha256"] != _sha256_bytes(raw)
        or attestation["stager_bytes"] != len(raw)
        or attestation["stager_lf_sha256"] != _sha256_bytes(normalized)
        or attestation["stager_blob_oid"] != _git_blob_oid(normalized)
    ):
        raise R7S2StagerError("stager_bootstrap_source_readback_mismatch")
    return dict(attestation)


def main(argv: Sequence[str] | None = None) -> int:
    if argv is not None:
        raise R7S2StagerError("production_main_argv_override_forbidden")
    _require_stager_bootstrap_attestation()
    args = _parser().parse_args(argv)
    if not args.execute_stage_non_credit_once:
        raise R7S2StagerError("explicit_execute_stage_non_credit_once_required")
    result = stage_once(
        StageRequest(
            qualification_id=args.qualification_id,
            run_uuid=args.run_uuid,
            attempt_id=args.attempt_id,
            expected_source_commit=args.expected_source_commit,
            expected_source_tree=args.expected_source_tree,
            expected_qualification_sha256=args.expected_qualification_sha256,
            expected_process_module_sha256=args.expected_process_module_sha256,
            expected_r7s1_runner_sha256=args.expected_r7s1_runner_sha256,
            expected_stager_sha256=args.expected_stager_sha256,
            expected_outer_sha256=args.expected_outer_sha256,
            parent_map_path=args.parent_map,
            expected_parent_map_sha256=args.expected_parent_map_sha256,
        )
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

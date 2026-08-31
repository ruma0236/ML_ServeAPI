from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GIT_ROOT = PROJECT_ROOT.parent
OLD_R4_REVISION = "e48c1d82938b9f64b414d58bb71c53dd258fbd78"
EXPECTED_B0_UID = "cfdab424-dcc5-4d5f-a46f-ae7530441ef4"
EXPECTED_B0_IMAGE = (
    "enterprise-vision-mlops-efficientnet-serving@"
    "sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a"
)
ETW_AMENDMENT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private"
    r"\s8-v4\x1-clock-phase-b2-failure-seals"
    r"\x1-clock-phase-b2-r3-failure-seal-20260831T135958Z-0a68addf"
    r"\etw-contract-amendment.json"
)
ETW_AMENDMENT_SHA256 = "71ddc50a2a91f707b8183a19c87f490bdad8421ab18446dceb21622bc3439715"
RUNTIME_PATHS = {
    "core": Path("src/evm/scale_validation/phase_b2_r5.py"),
    "process": Path("src/evm/scale_validation/phase_b2_r5_process.py"),
    "fresh": Path("src/evm/scale_validation/phase_b2_r5_fresh.py"),
    "runner": Path("scripts/dev/run_x1_phase_b2_r5.py"),
    "validator": Path("scripts/dev/validate_phase_b2_r5_bundle.ps1"),
}


class BundleBuildError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BundleBuildError(f"bundle_file_exists:{path}") from exc
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("exclusive bundle write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise BundleBuildError(f"git_command_failed:{arguments}:{result.stderr.strip()}")
    return result.stdout.strip()


def git_blob_oid(repository: Path, path: Path) -> str:
    relative = path.resolve().relative_to(repository.resolve()).as_posix()
    value = git(repository, "rev-parse", f"HEAD:{relative}")
    if len(value) != 40:
        raise BundleBuildError(f"git_blob_oid_invalid:{relative}:{value}")
    return value


def source_pin(project_root: Path, path: Path) -> dict[str, Any]:
    absolute = (project_root / path).resolve()
    if not absolute.is_file():
        raise BundleBuildError(f"runtime_source_missing:{path}")
    return {
        "path": str(absolute),
        "sha256": sha256_file(absolute),
        "blob_oid": git_blob_oid(project_root.parent, absolute),
        "bytes": absolute.stat().st_size,
    }


def read_checkpoint(path: Path, mode: str) -> dict[str, Any]:
    if not path.is_file():
        raise BundleBuildError(f"checkpoint_missing:{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise BundleBuildError("checkpoint_json_invalid") from exc
    if not isinstance(payload, dict):
        raise BundleBuildError("checkpoint_object_required")
    if mode == "restore-only":
        if payload.get("failure_only") is not True or payload.get("acceptance_credit") is not False:
            raise BundleBuildError("restore_only_requires_r4_failure_seal")
        if payload.get("success_marker_created") is not False:
            raise BundleBuildError("r4_failure_checkpoint_success_marker_forbidden")
    else:
        if payload.get("restore_only_pass") is not True:
            raise BundleBuildError("fresh_requires_passing_r5_restore_index")
        if payload.get("acceptance_credit") is not False:
            raise BundleBuildError("restore_index_phase_b2_credit_forbidden")
        if payload.get("completion_marker_created") is not False:
            raise BundleBuildError("restore_index_completion_marker_forbidden")
    return payload


def read_checkpoint_index(path: Path, mode: str) -> dict[str, Any]:
    if not path.is_file():
        raise BundleBuildError(f"checkpoint_index_missing:{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise BundleBuildError("checkpoint_index_json_invalid") from exc
    if not isinstance(payload, dict):
        raise BundleBuildError("checkpoint_index_object_required")
    if mode == "restore-only":
        if payload.get("failure_only") is not True:
            raise BundleBuildError("r4_failure_index_required")
        if payload.get("acceptance_credit") is not False:
            raise BundleBuildError("r4_failure_index_credit_forbidden")
        if payload.get("success_marker_created") is not False:
            raise BundleBuildError("r4_failure_index_success_marker_forbidden")
    else:
        if payload.get("restore_only_pass") is not True:
            raise BundleBuildError("r5_restore_only_index_required")
        if payload.get("acceptance_credit") is not False:
            raise BundleBuildError("r5_restore_only_index_credit_forbidden")
        if payload.get("completion_marker_created") is not False:
            raise BundleBuildError("r5_restore_only_index_marker_forbidden")
    return payload


def verify_source_identity(
    project_root: Path,
    branch: str,
    expected_untracked: int,
) -> dict[str, Any]:
    repository = project_root.parent
    revision = git(repository, "rev-parse", "HEAD").lower()
    tree = git(repository, "rev-parse", "HEAD^{tree}").lower()
    actual_branch = git(repository, "branch", "--show-current")
    origin = git(repository, "rev-parse", f"origin/{branch}").lower()
    remote_line = git(repository, "ls-remote", "origin", f"refs/heads/{branch}")
    remote_parts = remote_line.split()
    remote = remote_parts[0].lower() if len(remote_parts) == 2 else ""
    tracked = git(repository, "status", "--porcelain=v1", "--untracked-files=no")
    all_status = git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    untracked = sum(line.startswith("?? ") for line in all_status.splitlines())
    if revision == OLD_R4_REVISION:
        raise BundleBuildError("old_r4_revision_pin_reuse_forbidden")
    if actual_branch != branch:
        raise BundleBuildError(f"branch_mismatch:{actual_branch}")
    if not revision or revision != origin or revision != remote:
        raise BundleBuildError(f"local_origin_remote_mismatch:{revision}:{origin}:{remote}")
    if tracked:
        raise BundleBuildError("tracked_changes_present")
    if untracked != expected_untracked:
        raise BundleBuildError(f"untracked_count_mismatch:{untracked}")
    return {
        "revision": revision,
        "tree": tree,
        "branch": actual_branch,
        "origin_revision": origin,
        "remote_revision": remote,
        "tracked": 0,
        "untracked": untracked,
    }


def build_manifest(
    *,
    mode: str,
    run_id: str,
    source_identity: Mapping[str, Any],
    project_root: Path,
    checkpoint: Path,
    checkpoint_index: Path,
    output_directory: Path,
    python_path: Path,
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    lifecycle = (
        {
            "docker_off_probe": 0,
            "compose_stop": 0,
            "desktop_stop": 0,
            "wsl_shutdown": 0,
            "desktop_start": 0,
            "compose_start": 0,
        }
        if mode == "restore-only"
        else {
            "docker_off_probe": 1,
            "compose_stop": 1,
            "desktop_stop": 1,
            "wsl_shutdown": 0,
            "desktop_start": 1,
            "compose_start": 1,
        }
    )
    return {
        "schema_version": "evm.s8_v4.x1_phase_b2_r5_work_order.v1",
        "work_order_id": "s8-v4-x1-phase-b2-r5-restore-and-fresh-validation",
        "bundle_id": run_id,
        "execution_mode": mode,
        "created_at": utc_now(),
        "canonical_revision": source_identity["revision"],
        "canonical_tree": source_identity["tree"],
        "repository": {
            "path": str(project_root.resolve()),
            "branch": source_identity["branch"],
            "preserved_untracked_count": source_identity["untracked"],
            "local_origin_remote_equal": True,
        },
        "checkpoint": {
            "kind": "r4_failure_seal" if mode == "restore-only" else "r5_restore_only_index",
            "path": str(checkpoint.resolve()),
            "sha256": sha256_file(checkpoint),
            "immutable": True,
            "must_not_execute": True,
            "companion_index": {
                "path": str(checkpoint_index.resolve()),
                "sha256": sha256_file(checkpoint_index),
            },
        },
        "output": {
            "path": str(output_directory.resolve()),
            "must_not_exist_before_runner": True,
            "write_mode": "create-exclusive",
        },
        "timeout_contract": {
            "kubectl_timeout_seconds": 8.0,
            "wrapper_timeout_seconds": 15.0,
            "restore_deadline_seconds": 600.0,
            "residual_repoll_seconds": 120.0,
            "stream_drain_seconds": 5.0,
        },
        "lifecycle_timeout_contract": {
            "compose_internal_seconds": 120.0,
            "compose_wrapper_seconds": 150.0,
            "desktop_internal_seconds": 300.0,
            "desktop_wrapper_seconds": 330.0,
            "sampler_internal_seconds": 180.0,
            "sampler_wrapper_seconds": 210.0,
            "attempt_deadline_seconds": 1200.0,
        },
        "process_containment": {
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
        },
        "phase_b2_contract": {
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
        },
        "call_contract": {
            mode: lifecycle,
            "launcher": {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0},
            "downstream": {
                "full_stack_3180": 0,
                "q0": 0,
                "calibration_54": 0,
                "matrix_78": 0,
                "integrated_v4": 0,
                "etw": 0,
            },
        },
        "expected_state": {
            "compose_services": [
                "airflow-postgres",
                "airflow-scheduler",
                "airflow-webserver",
                "api",
                "control-panel",
                "control-plane-postgres",
                "grafana",
                "minio",
                "mlflow",
                "otel-collector",
                "postgres",
                "prometheus",
                "task-queue-worker",
            ],
            "api_base_url": "http://127.0.0.1:8000",
            "b0": {
                "uid": EXPECTED_B0_UID,
                "uid_basis": (
                    "tracked canonical status evidence predating r4 and immutable deployment identity"
                ),
                "image": EXPECTED_B0_IMAGE,
                "ready_url": "http://127.0.0.1:30800/ready",
                "predict_url": "http://127.0.0.1:30800/predict",
                "sample_image_uri": (
                    "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"
                ),
            },
            "prometheus_jobs": [
                "evm-api",
                "evm-b0-production",
                "evm-otel-collector",
                "evm-task-queue-worker",
                "prometheus",
            ],
            "prometheus_targets_url": "http://127.0.0.1:9090/api/v1/targets",
            "gpu_lease_path": (
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease/active.json"
            ),
            "active_job_roots": [],
            "active_claim_roots": [],
            "x1_residue_paths": [
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
                "prometheus-targets/s8-v4-x1-triton.json",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
                "prometheus-targets/s8-v4-x1-api.json",
            ],
            "x1_docker_name_filter": "name=evm-x1",
            "x1_ports": [31120, 31121, 31122],
            "x1_kubernetes_selectors": ["evm.openai.local/scenario=s8-v4-x1"],
        },
        "etw_contract": {
            "decision": (
                "existing_pinned_etw_evidence_is_admissible;"
                "fresh_capture_not_a_phase_b2_go_invariant"
            ),
            "amendment_path": str(ETW_AMENDMENT),
            "amendment_sha256": ETW_AMENDMENT_SHA256,
            "fresh_capture_required_for_phase_b2_go": False,
            "fresh_invocations": 0,
        },
        "evidence": {
            "write_mode": "create-exclusive",
            "failure_creates_completion_marker": False,
            "failure_index_is_not_success_index": True,
            "restore_only_creates_completion_marker": False,
            "success_requires_all_invariants": True,
        },
        "runtime": {
            **dict(runtime),
            "python": str(python_path.resolve()),
        },
    }


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_outer(*, bridge_sha256: str) -> str:
    return f"""[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][string]$OutputDirectory,
  [Parameter(Mandatory = $true)][ValidateSet('restore-only','fresh')][string]$Mode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedBridgeSha256 = '{bridge_sha256}'
$outerPath = $PSCommandPath
$outerExpected = $ExpectedOuterSha256.ToLowerInvariant()
$outerObserved = (Get-FileHash -LiteralPath $outerPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
if ($outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch' }}
$bridgePath = Join-Path $PSScriptRoot 'invoke-x1-phase-b2-r5-bridge.ps1'
if (-not (Test-Path -LiteralPath $bridgePath -PathType Leaf)) {{ throw 'bridge_missing' }}
$bridgeObserved = (Get-FileHash -LiteralPath $bridgePath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
if ($bridgeObserved -ne $ExpectedBridgeSha256) {{ throw 'bridge_sha256_mismatch' }}
if (Test-Path -LiteralPath $OutputDirectory) {{ throw 'output_directory_exists' }}
$reservation = Join-Path $PSScriptRoot 'r5-outer-invocation-reservation.json'
$reservationValue = [ordered]@{{ schema='s8-v4-x1-phase-b2-r5-outer-reservation/v1'; created_at=[DateTime]::UtcNow.ToString('o'); pid=$PID; mode=$Mode; output_directory=$OutputDirectory }}
$bytes = [Text.UTF8Encoding]::new($false).GetBytes(($reservationValue | ConvertTo-Json -Depth 8 -Compress) + "`n")
$stream = [IO.File]::Open($reservation,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try {{ $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }} finally {{ $stream.Dispose() }}

# R5_BRIDGE_INVOKE_EXACTLY_ONCE
& $bridgePath -ExpectedOuterSha256 $outerExpected -ObservedOuterSha256 $outerObserved -ExpectedBridgeSha256FromOuter $ExpectedBridgeSha256 -ObservedBridgeSha256 $bridgeObserved -OuterLauncherPath $outerPath -OutputDirectory $OutputDirectory -Mode $Mode
exit $LASTEXITCODE
"""


def render_bridge(
    *,
    manifest_sha256: str,
    manifest: Mapping[str, Any],
    runtime: Mapping[str, Mapping[str, Any]],
) -> str:
    revision = str(manifest["canonical_revision"])
    tree = str(manifest["canonical_tree"])
    repository = str(manifest["repository"]["path"])  # type: ignore[index]
    branch = str(manifest["repository"]["branch"])  # type: ignore[index]
    untracked = int(manifest["repository"]["preserved_untracked_count"])  # type: ignore[index]
    checkpoint = manifest["checkpoint"]
    assert isinstance(checkpoint, Mapping)
    return f"""[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ObservedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedBridgeSha256FromOuter,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ObservedBridgeSha256,
  [Parameter(Mandatory = $true)][string]$OuterLauncherPath,
  [Parameter(Mandatory = $true)][string]$OutputDirectory,
  [Parameter(Mandatory = $true)][ValidateSet('restore-only','fresh')][string]$Mode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedManifestSha256 = '{manifest_sha256}'
$PinnedRevision = '{revision}'
$PinnedTree = '{tree}'
$RepositoryRoot = {_ps_literal(repository)}
$ExpectedBranch = {_ps_literal(branch)}
$ExpectedUntrackedCount = {untracked}
$ManifestPath = Join-Path $PSScriptRoot 'phase-b2-r5-work-order.json'
$CheckpointPath = {_ps_literal(str(checkpoint["path"]))}
$ExpectedCheckpointSha256 = '{checkpoint["sha256"]}'
$CheckpointIndexPath = {_ps_literal(str(checkpoint["companion_index"]["path"]))}
$ExpectedCheckpointIndexSha256 = '{checkpoint["companion_index"]["sha256"]}'
$PythonPath = {_ps_literal(str(manifest["runtime"]["python"]))}
$CorePath = {_ps_literal(str(runtime["core"]["path"]))}
$ProcessPath = {_ps_literal(str(runtime["process"]["path"]))}
$FreshPath = {_ps_literal(str(runtime["fresh"]["path"]))}
$RunnerPath = {_ps_literal(str(runtime["runner"]["path"]))}
$ValidatorPath = {_ps_literal(str(runtime["validator"]["path"]))}
$ExpectedCoreSha256 = '{runtime["core"]["sha256"]}'
$ExpectedProcessSha256 = '{runtime["process"]["sha256"]}'
$ExpectedFreshSha256 = '{runtime["fresh"]["sha256"]}'
$ExpectedRunnerSha256 = '{runtime["runner"]["sha256"]}'
$ExpectedValidatorSha256 = '{runtime["validator"]["sha256"]}'

function Get-Sha256([string]$Path) {{
  (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}}
function Invoke-GitRead([string[]]$Arguments) {{
  $text = @(& git.exe -c "safe.directory=$RepositoryRoot" -C $RepositoryRoot @Arguments 2>&1)
  if ($LASTEXITCODE -ne 0) {{ throw "git_identity_read_failed:$($Arguments -join ',')" }}
  ($text -join [Environment]::NewLine).Trim()
}}
function Write-CreateNewJson([string]$Path,[object]$Value) {{
  $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 16 -Compress) + "`n")
  $stream = [IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
  try {{ $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }} finally {{ $stream.Dispose() }}
}}

$outerExpected = $ExpectedOuterSha256.ToLowerInvariant()
$outerObserved = $ObservedOuterSha256.ToLowerInvariant()
$bridgeExpected = $ExpectedBridgeSha256FromOuter.ToLowerInvariant()
$bridgeObserved = $ObservedBridgeSha256.ToLowerInvariant()
if ((Get-Sha256 $OuterLauncherPath) -ne $outerExpected -or $outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch_at_bridge' }}
if ((Get-Sha256 $PSCommandPath) -ne $bridgeExpected -or $bridgeObserved -ne $bridgeExpected) {{ throw 'bridge_sha256_mismatch' }}
if ((Get-Sha256 $ManifestPath) -ne $ExpectedManifestSha256) {{ throw 'manifest_sha256_mismatch' }}
if ((Get-Sha256 $CheckpointPath) -ne $ExpectedCheckpointSha256) {{ throw 'checkpoint_sha256_mismatch' }}
if ((Get-Sha256 $CheckpointIndexPath) -ne $ExpectedCheckpointIndexSha256) {{ throw 'checkpoint_index_sha256_mismatch' }}
if ((Get-Sha256 $CorePath) -ne $ExpectedCoreSha256) {{ throw 'core_sha256_mismatch' }}
if ((Get-Sha256 $ProcessPath) -ne $ExpectedProcessSha256) {{ throw 'process_sha256_mismatch' }}
if ((Get-Sha256 $FreshPath) -ne $ExpectedFreshSha256) {{ throw 'fresh_sha256_mismatch' }}
if ((Get-Sha256 $RunnerPath) -ne $ExpectedRunnerSha256) {{ throw 'runner_sha256_mismatch' }}
if ((Get-Sha256 $ValidatorPath) -ne $ExpectedValidatorSha256) {{ throw 'validator_sha256_mismatch' }}
if (Test-Path -LiteralPath $OutputDirectory) {{ throw 'output_directory_exists' }}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([string]$manifest.execution_mode -ne $Mode) {{ throw 'manifest_execution_mode_mismatch' }}
if ([string]$manifest.output.path -ne [IO.Path]::GetFullPath($OutputDirectory)) {{ throw 'manifest_output_path_mismatch' }}

$actualBranch = Invoke-GitRead @('branch','--show-current')
$actualRevision = Invoke-GitRead @('rev-parse','HEAD')
$actualTree = Invoke-GitRead @('rev-parse','HEAD^{{tree}}')
$originRevision = Invoke-GitRead @('rev-parse',"origin/$ExpectedBranch")
$remoteText = Invoke-GitRead @('ls-remote','origin',"refs/heads/$ExpectedBranch")
$remoteRevision = @($remoteText -split '\\s+')[0]
$trackedStatus = Invoke-GitRead @('status','--porcelain=v1','--untracked-files=no')
$allStatus = @((Invoke-GitRead @('status','--porcelain=v1','--untracked-files=all')) -split "`r?`n")
$untrackedCount = @($allStatus | Where-Object {{ $_ -like '?? *' }}).Count
if ($actualBranch -ne $ExpectedBranch) {{ throw 'git_branch_mismatch' }}
if ($actualRevision -ne $PinnedRevision -or $originRevision -ne $PinnedRevision -or $remoteRevision -ne $PinnedRevision) {{ throw 'git_local_origin_remote_mismatch' }}
if ($actualTree -ne $PinnedTree) {{ throw 'git_tree_mismatch' }}
if (-not [string]::IsNullOrWhiteSpace($trackedStatus)) {{ throw 'git_tracked_changes_present' }}
if ($untrackedCount -ne $ExpectedUntrackedCount) {{ throw "git_untracked_count_mismatch:$untrackedCount" }}

if (-not ('R5TokenNative' -as [type])) {{
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class R5TokenNative {{
  [DllImport("advapi32.dll", SetLastError=true)]
  private static extern bool GetTokenInformation(IntPtr token, int infoClass, out int value, int length, out int returnedLength);
  public static int ElevationType(IntPtr token) {{
    int value; int returnedLength;
    if (!GetTokenInformation(token, 18, out value, sizeof(int), out returnedLength)) {{
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    }}
    return value;
  }}
}}
'@
}}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$groups = (& whoami.exe /groups | Out-String)
$integrity = if ($groups -match 'S-1-16-16384') {{ 'System' }} elseif ($groups -match 'S-1-16-12288') {{ 'High' }} else {{ 'Other' }}
$elevationValue = [R5TokenNative]::ElevationType($identity.Token)
$elevationType = if ($elevationValue -eq 2) {{ 'Full' }} else {{ "NotFull:$elevationValue" }}
$execution = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
$codex = $null
$ancestor = $execution
for ($depth=0; $depth -lt 8 -and $null -ne $ancestor; $depth++) {{
  if ([string]$ancestor.Name -ieq 'codex.exe') {{ $codex=$ancestor; break }}
  if ([int]$ancestor.ParentProcessId -le 0) {{ break }}
  $ancestor = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$ancestor.ParentProcessId)" -ErrorAction SilentlyContinue
}}
$tokenEvidence = [ordered]@{{ captured_at=[DateTime]::UtcNow.ToString('o'); administrator=$administrator; integrity=$integrity; token_elevation_type=$elevationType; token_elevation_type_value=$elevationValue; execution_powershell=[ordered]@{{pid=[int]$execution.ProcessId;ppid=[int]$execution.ParentProcessId;session_id=[int]$execution.SessionId;path=[string]$execution.ExecutablePath}}; codex=if($null -eq $codex){{$null}}else{{[ordered]@{{pid=[int]$codex.ProcessId;ppid=[int]$codex.ParentProcessId;session_id=[int]$codex.SessionId;path=[string]$codex.ExecutablePath;command_line=[string]$codex.CommandLine}}}} }}
if (-not ($administrator -and $integrity -in @('High','System') -and $elevationType -eq 'Full')) {{
  [ordered]@{{decision='administrator_token_required';token_evidence=$tokenEvidence}} | ConvertTo-Json -Depth 10 -Compress
  exit 3
}}

$validation = @(& $ValidatorPath -ManifestPath $ManifestPath -OuterPath $OuterLauncherPath -BridgePath $PSCommandPath -ExpectedOuterSha256 $outerExpected -Mode $Mode -PreExecution 2>&1)
if ($LASTEXITCODE -ne 0) {{ throw "staging_validator_failed:$($validation -join [Environment]::NewLine)" }}
$bridgeReservation = Join-Path $PSScriptRoot 'r5-bridge-invocation-reservation.json'
Write-CreateNewJson $bridgeReservation ([ordered]@{{schema='s8-v4-x1-phase-b2-r5-bridge-reservation/v1';created_at=[DateTime]::UtcNow.ToString('o');pid=$PID;mode=$Mode;output_directory=$OutputDirectory}})
$launcherEvidence = [ordered]@{{
  schema='s8-v4-x1-phase-b2-r5-launcher-evidence/v1'
  token_evidence=$tokenEvidence
  sha_chain=[ordered]@{{outer=Get-Sha256 $OuterLauncherPath;bridge=Get-Sha256 $PSCommandPath;manifest=Get-Sha256 $ManifestPath;core=Get-Sha256 $CorePath;process=Get-Sha256 $ProcessPath;fresh=Get-Sha256 $FreshPath;runner=Get-Sha256 $RunnerPath;validator=Get-Sha256 $ValidatorPath;checkpoint=Get-Sha256 $CheckpointPath;checkpoint_index=Get-Sha256 $CheckpointIndexPath}}
  git=[ordered]@{{branch=$actualBranch;revision=$actualRevision;origin_revision=$originRevision;remote_revision=$remoteRevision;tree=$actualTree;tracked=0;untracked=$untrackedCount}}
  mode=$Mode
  invocation_counts=[ordered]@{{outer=1;bridge=1;runner=1;automatic_retry=0}}
}}
$launcherBase64 = [Convert]::ToBase64String([Text.UTF8Encoding]::new($false).GetBytes(($launcherEvidence | ConvertTo-Json -Depth 16 -Compress)))

# R5_RUNNER_INVOKE_EXACTLY_ONCE
& $PythonPath $RunnerPath --manifest $ManifestPath --checkpoint $CheckpointPath --output-directory $OutputDirectory --expected-revision $PinnedRevision --launcher-evidence-base64 $launcherBase64 --repository-root $RepositoryRoot --mode $Mode
exit $LASTEXITCODE
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create one append-only Phase B2 r5 bundle.")
    parser.add_argument("--mode", choices=("restore-only", "fresh"), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--staging-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-index", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--branch", default="codex/distributed-scale-validation-plan")
    parser.add_argument("--expected-untracked", type=int, default=4244)
    parser.add_argument("--python", type=Path, default=Path(r"F:\evm_w7_torch\python.exe"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    if args.staging_directory.exists():
        raise BundleBuildError(f"staging_directory_exists:{args.staging_directory}")
    if args.output_directory.exists():
        raise BundleBuildError(f"output_directory_exists:{args.output_directory}")
    if not args.python.is_file():
        raise BundleBuildError(f"python_missing:{args.python}")
    read_checkpoint(args.checkpoint, args.mode)
    read_checkpoint_index(args.checkpoint_index, args.mode)
    if sha256_file(ETW_AMENDMENT) != ETW_AMENDMENT_SHA256:
        raise BundleBuildError("etw_amendment_sha256_mismatch")
    source_identity = verify_source_identity(project_root, args.branch, args.expected_untracked)
    runtime = {name: source_pin(project_root, relative) for name, relative in RUNTIME_PATHS.items()}
    manifest = build_manifest(
        mode=args.mode,
        run_id=args.run_id,
        source_identity=source_identity,
        project_root=project_root,
        checkpoint=args.checkpoint,
        checkpoint_index=args.checkpoint_index,
        output_directory=args.output_directory,
        python_path=args.python,
        runtime=runtime,
    )
    args.staging_directory.mkdir(parents=True, exist_ok=False)
    manifest_path = args.staging_directory / "phase-b2-r5-work-order.json"
    write_exclusive(manifest_path, canonical_json_bytes(manifest))
    bridge_path = args.staging_directory / "invoke-x1-phase-b2-r5-bridge.ps1"
    bridge = render_bridge(
        manifest_sha256=sha256_file(manifest_path),
        manifest=manifest,
        runtime=runtime,
    )
    write_exclusive(bridge_path, bridge.encode("utf-8"))
    outer_path = args.staging_directory / "invoke-verified-x1-phase-b2-r5.ps1"
    outer = render_outer(bridge_sha256=sha256_file(bridge_path))
    write_exclusive(outer_path, outer.encode("utf-8"))
    result = {
        "schema": "s8-v4-x1-phase-b2-r5-bundle-build/v1",
        "created_at": utc_now(),
        "mode": args.mode,
        "run_id": args.run_id,
        "staging_directory": str(args.staging_directory.resolve()),
        "source_identity": source_identity,
        "files": {
            "outer": {"path": str(outer_path), "sha256": sha256_file(outer_path)},
            "bridge": {"path": str(bridge_path), "sha256": sha256_file(bridge_path)},
            "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        },
        "actual_invocations": {"outer": 0, "bridge": 0, "runner": 0},
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

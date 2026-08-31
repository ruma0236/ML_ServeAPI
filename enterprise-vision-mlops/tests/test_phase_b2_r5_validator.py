from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest


PROJECT = Path(__file__).parents[1]
VALIDATOR = PROJECT / "scripts" / "dev" / "validate_phase_b2_r5_bundle.ps1"
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
OLD_R4_REVISION = "e48c1d82938b9f64b414d58bb71c53dd258fbd78"
B0_UID = "cfdab424-dcc5-4d5f-a46f-ae7530441ef4"
B0_IMAGE = (
    "enterprise-vision-mlops-efficientnet-serving@sha256:"
    "227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8", newline="\n")


def _run(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=30,
    )


def _git(repo: Path, *args: str) -> str:
    result = _run("git", "-C", repo, *args)
    assert result.returncode == 0, (result.stdout, result.stderr, args)
    return result.stdout.strip()


def _blob_oid(repo: Path, revision: str, path: Path) -> str:
    relative = path.relative_to(repo).as_posix()
    output = _git(repo, "ls-tree", revision, "--", relative)
    return output.split()[2]


@dataclass(frozen=True)
class FixtureRepository:
    root: Path
    branch: str
    revision: str
    tree: str
    runtime: dict[str, dict[str, str]]
    checkpoint_seal: Path
    checkpoint_index: Path
    restore_report: Path
    restore_index: Path


@pytest.fixture(scope="module")
def fixture_repository(tmp_path_factory: pytest.TempPathFactory) -> FixtureRepository:
    if not POWERSHELL.is_file() or shutil.which("git") is None:
        pytest.skip("Windows PowerShell 5.1 and git are required")

    fixture_parent = tmp_path_factory.mktemp("phase_b2_r5_validator_repo")
    git_root = fixture_parent / "repo"
    root = git_root / "enterprise-vision-mlops"
    remote = fixture_parent / "remote.git"
    root.mkdir(parents=True)
    branch = "codex/r5-validator-test"
    assert _run("git", "init", "--bare", remote).returncode == 0
    init = _run("git", "init", "-b", branch, git_root)
    assert init.returncode == 0, init.stderr
    _git(git_root, "config", "user.email", "r5-validator@example.invalid")
    _git(git_root, "config", "user.name", "R5 Validator Test")
    _git(git_root, "config", "core.autocrlf", "false")

    component_paths = {
        "core": root / "src" / "evm" / "scale_validation" / "phase_b2_r5.py",
        "process": root / "src" / "evm" / "scale_validation" / "phase_b2_r5_process.py",
        "fresh": root / "src" / "evm" / "scale_validation" / "phase_b2_r5_fresh.py",
        "runner": root / "scripts" / "dev" / "run_x1_phase_b2_r5.py",
        "validator": root / "scripts" / "dev" / "validate_phase_b2_r5_bundle.ps1",
    }
    _write(root / "src" / "evm" / "__init__.py", "")
    _write(root / "src" / "evm" / "scale_validation" / "__init__.py", "")
    _write(
        component_paths["core"],
        "import os\nEXCLUSIVE_CREATE_FLAG = os.O_EXCL\nRESIDUAL_REPOLL_SECONDS = 120.0\n",
    )
    _write(
        component_paths["process"],
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class TimeoutContract:\n"
        "    kubectl_timeout_seconds: float = 8.0\n"
        "    wrapper_timeout_seconds: float = 15.0\n"
        "    restore_deadline_seconds: float = 600.0\n"
        "    residual_repoll_seconds: float = 120.0\n"
        "    stream_drain_seconds: float = 5.0\n",
    )
    _write(
        component_paths["fresh"],
        "DURATION_SECONDS = 180\nCADENCE_MS = 100\nSAMPLE_COUNT = 1800\n",
    )
    _write(
        component_paths["runner"],
        'MODES = ("restore-only", "fresh")\nMAXIMUM_INVOCATIONS = 1\n',
    )
    _write(component_paths["validator"], VALIDATOR.read_bytes())

    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "fixture r5 runtime")
    _git(git_root, "remote", "add", "origin", str(remote))
    _git(git_root, "push", "-u", "origin", branch)
    revision = _git(git_root, "rev-parse", "HEAD")
    tree = _git(git_root, "rev-parse", "HEAD^{tree}")
    assert revision != OLD_R4_REVISION

    runtime: dict[str, dict[str, str]] = {}
    for name, path in component_paths.items():
        runtime[name] = {
            "path": str(path.resolve()),
            "sha256": _sha256(path),
            "blob_oid": _blob_oid(git_root, revision, path),
        }

    # Production r4 checkpoint paths contain the old revision prefix. The
    # validator must allow that immutable historical reference while still
    # rejecting e48c1d8 as any executable/runtime revision pin.
    checkpoint_root = fixture_parent / f"r4-checkpoint-{OLD_R4_REVISION[:8]}"
    checkpoint_seal = checkpoint_root / "failure-seal.json"
    checkpoint_index = checkpoint_root / "failure-evidence-index.json"
    restore_report = checkpoint_root / "restore-only-report.json"
    restore_index = checkpoint_root / "restore-only-index.json"
    _write(checkpoint_seal, '{"decision":"manual_intervention_required"}\n')
    _write(checkpoint_index, '{"kind":"failure-only"}\n')
    _write(restore_report, '{"restore_only_pass":true,"acceptance_credit":false}\n')
    _write(restore_index, '{"kind":"restore-only","completion_marker_created":false}\n')
    return FixtureRepository(
        root=root,
        branch=branch,
        revision=revision,
        tree=tree,
        runtime=runtime,
        checkpoint_seal=checkpoint_seal,
        checkpoint_index=checkpoint_index,
        restore_report=restore_report,
        restore_index=restore_index,
    )


ManifestMutation = Callable[[dict[str, Any]], None]
TextMutation = Callable[[str], str]


def _manifest(fixture: FixtureRepository, mode: str) -> dict[str, Any]:
    runtime: dict[str, Any] = {"python": str(Path(sys.executable).resolve())}
    for name, values in fixture.runtime.items():
        runtime[name] = dict(values)

    checkpoint_path = fixture.checkpoint_seal if mode == "restore-only" else fixture.restore_report
    companion_path = fixture.checkpoint_index if mode == "restore-only" else fixture.restore_index
    calls = (
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
        "work_order_id": "r5-validator-fixture",
        "canonical_revision": fixture.revision,
        "canonical_tree": fixture.tree,
        "execution_mode": mode,
        "repository": {
            "path": str(fixture.root.resolve()),
            "branch": fixture.branch,
            "preserved_untracked_count": 0,
            "local_origin_remote_equal": True,
        },
        "runtime": runtime,
        "output": {
            "path": str((fixture.root.parent / "output-never-created").resolve()),
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
        "process_containment": {
            "provider": "windows_job_object",
            "create_suspended": True,
            "assign_before_resume": True,
            "breakaway_allowed": False,
            "kill_on_job_close": False,
            "terminate_job_object_allowed": False,
            "residual_repoll_seconds": 120.0,
            "force_termination_attempts": 0,
        },
        "expected_state": {"b0": {"uid": B0_UID, "image": B0_IMAGE}},
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
            mode: calls,
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
        "checkpoint": {
            "kind": "r4_failure_seal" if mode == "restore-only" else "r5_restore_only_index",
            "path": str(checkpoint_path.resolve()),
            "sha256": _sha256(checkpoint_path),
            "immutable": True,
            "must_not_execute": True,
            "companion_index": {
                "path": str(companion_path.resolve()),
                "sha256": _sha256(companion_path),
            },
        },
        "evidence": {
            "write_mode": "create-exclusive",
            "failure_creates_completion_marker": False,
        },
    }


def _bridge_text(
    fixture: FixtureRepository,
    manifest_sha256: str,
    manifest: dict[str, Any],
) -> str:
    values = fixture.runtime
    checkpoint = manifest["checkpoint"]
    return f"""[CmdletBinding()]
param([Parameter(Mandatory = $true)][ValidateSet('restore-only', 'fresh')][string]$Mode)
$ExpectedManifestSha256 = '{manifest_sha256}'
$ExpectedCoreSha256 = '{values["core"]["sha256"]}'
$ExpectedProcessSha256 = '{values["process"]["sha256"]}'
$ExpectedFreshSha256 = '{values["fresh"]["sha256"]}'
$ExpectedRunnerSha256 = '{values["runner"]["sha256"]}'
$ExpectedValidatorSha256 = '{values["validator"]["sha256"]}'
$PinnedRevision = '{fixture.revision}'
$PinnedTree = '{fixture.tree}'
$RepositoryRoot = '{fixture.root.resolve()}'
$ExpectedBranch = '{fixture.branch}'
$ExpectedUntrackedCount = 0
$ManifestPath = Join-Path $PSScriptRoot 'phase-b2-r5-work-order.json'
$CheckpointPath = '{checkpoint["path"]}'
$ExpectedCheckpointSha256 = '{checkpoint["sha256"]}'
$CheckpointIndexPath = '{checkpoint["companion_index"]["path"]}'
$ExpectedCheckpointIndexSha256 = '{checkpoint["companion_index"]["sha256"]}'
$CorePath = '{values["core"]["path"]}'
$ProcessPath = '{values["process"]["path"]}'
$FreshPath = '{values["fresh"]["path"]}'
$RunnerPath = '{values["runner"]["path"]}'
$ValidatorPath = '{values["validator"]["path"]}'
$PythonPath = '{Path(sys.executable).resolve()}'
function Get-Sha256([string]$Path) {{
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}}
function Write-CreateNewJson([string]$Path, [object]$Value) {{
  $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Compress) + "`n")
  $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
  try {{ $stream.Write($bytes, 0, $bytes.Length) }} finally {{ $stream.Dispose() }}
}}
if ((Get-Sha256 $ManifestPath) -ne $ExpectedManifestSha256) {{ throw 'manifest_sha256_mismatch' }}
if ((Get-Sha256 $CheckpointPath) -ne $ExpectedCheckpointSha256) {{ throw 'checkpoint_sha256_mismatch' }}
if ((Get-Sha256 $CheckpointIndexPath) -ne $ExpectedCheckpointIndexSha256) {{ throw 'checkpoint_index_sha256_mismatch' }}
if ((Get-Sha256 $CorePath) -ne $ExpectedCoreSha256) {{ throw 'core_sha256_mismatch' }}
if ((Get-Sha256 $ProcessPath) -ne $ExpectedProcessSha256) {{ throw 'process_sha256_mismatch' }}
if ((Get-Sha256 $FreshPath) -ne $ExpectedFreshSha256) {{ throw 'fresh_sha256_mismatch' }}
if ((Get-Sha256 $RunnerPath) -ne $ExpectedRunnerSha256) {{ throw 'runner_sha256_mismatch' }}
if ((Get-Sha256 $ValidatorPath) -ne $ExpectedValidatorSha256) {{ throw 'validator_sha256_mismatch' }}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([string]$manifest.execution_mode -ne $Mode) {{ throw 'manifest_execution_mode_mismatch' }}
$validation = @(& $ValidatorPath -ManifestPath $ManifestPath -OuterPath $OuterLauncherPath -BridgePath $PSCommandPath -ExpectedOuterSha256 $outerExpected -Mode $Mode -PreExecution)
$bridgeReservation = Join-Path $PSScriptRoot 'r5-bridge-invocation-reservation.json'
Write-CreateNewJson $bridgeReservation ([ordered]@{{ schema='s8-v4-x1-phase-b2-r5-bridge-reservation/v1'; mode=$Mode }})
$launcherEvidence = [ordered]@{{ sha_chain=[ordered]@{{ checkpoint=Get-Sha256 $CheckpointPath; checkpoint_index=Get-Sha256 $CheckpointIndexPath }} }}
# R5_RUNNER_INVOKE_EXACTLY_ONCE
& $PythonPath $RunnerPath --mode $Mode
"""


def _outer_text(bridge_sha256: str) -> str:
    return f"""[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][ValidateSet('restore-only', 'fresh')][string]$Mode
)
$ExpectedBridgeSha256 = '{bridge_sha256}'
$outerExpected = $ExpectedOuterSha256.ToLowerInvariant()
$outerObserved = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch' }}
$bridgePath = Join-Path $PSScriptRoot 'invoke-x1-phase-b2-r5-bridge.ps1'
$bridgeObserved = (Get-FileHash -LiteralPath $bridgePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($bridgeObserved -ne $ExpectedBridgeSha256) {{ throw 'bridge_sha256_mismatch' }}
$reservation = Join-Path $PSScriptRoot 'r5-outer-invocation-reservation.json'
$reservationValue = [ordered]@{{ schema='s8-v4-x1-phase-b2-r5-outer-reservation/v1'; mode=$Mode }}
$bytes = [Text.UTF8Encoding]::new($false).GetBytes(($reservationValue | ConvertTo-Json -Compress) + "`n")
$stream = [IO.File]::Open($reservation, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try {{ $stream.Write($bytes, 0, $bytes.Length) }} finally {{ $stream.Dispose() }}
# R5_BRIDGE_INVOKE_EXACTLY_ONCE
& $bridgePath -Mode $Mode
"""


def _build_bundle(
    root: Path,
    fixture: FixtureRepository,
    *,
    mode: str = "restore-only",
    mutate_manifest: ManifestMutation | None = None,
    mutate_bridge: TextMutation | None = None,
    mutate_outer: TextMutation | None = None,
) -> tuple[Path, str]:
    bundle = root / "bundle"
    bundle.mkdir(parents=True)
    manifest = _manifest(fixture, mode)
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    manifest_path = bundle / "phase-b2-r5-work-order.json"
    _write(manifest_path, manifest_bytes)

    bridge = _bridge_text(
        fixture,
        hashlib.sha256(manifest_bytes).hexdigest(),
        manifest,
    )
    if mutate_bridge is not None:
        bridge = mutate_bridge(bridge)
    bridge_path = bundle / "invoke-x1-phase-b2-r5-bridge.ps1"
    _write(bridge_path, bridge)

    outer = _outer_text(_sha256(bridge_path))
    if mutate_outer is not None:
        outer = mutate_outer(outer)
    outer_path = bundle / "invoke-verified-x1-phase-b2-r5.ps1"
    _write(outer_path, outer)
    reservation = {
        "schema": "s8-v4-x1-phase-b2-r5-outer-reservation/v1",
        "created_at": "2026-08-31T18:00:00Z",
        "pid": 1234,
        "mode": mode,
        "output_directory": str((fixture.root.parent / "output-never-created").resolve()),
    }
    _write(
        bundle / "r5-outer-invocation-reservation.json",
        json.dumps(reservation, sort_keys=True, separators=(",", ":")) + "\n",
    )
    return bundle, _sha256(outer_path)


def _validate(
    bundle: Path,
    outer_sha256: str,
    fixture: FixtureRepository,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    result = _run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        VALIDATOR,
        "-ManifestPath",
        bundle / "phase-b2-r5-work-order.json",
        "-OuterPath",
        bundle / "invoke-verified-x1-phase-b2-r5.ps1",
        "-BridgePath",
        bundle / "invoke-x1-phase-b2-r5-bridge.ps1",
        "-ExpectedOuterSha256",
        outer_sha256,
        "-Mode",
        json.loads((bundle / "phase-b2-r5-work-order.json").read_text(encoding="ascii"))[
            "execution_mode"
        ],
        "-PreExecution",
    )
    output_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert output_lines, (result.stdout, result.stderr)
    return result, json.loads(output_lines[-1])


def _assert_rejected(
    bundle: Path,
    outer_sha256: str,
    fixture: FixtureRepository,
    expected_error: str,
) -> None:
    result, payload = _validate(bundle, outer_sha256, fixture)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert payload["status"] == "FAIL"
    assert expected_error in payload["error"]


def test_validator_itself_has_valid_windows_powershell_ast() -> None:
    if not POWERSHELL.is_file():
        pytest.skip("Windows PowerShell 5.1 is required")
    command = (
        "$t=$null;$e=$null;"
        f"[void][Management.Automation.Language.Parser]::ParseFile('{VALIDATOR}',[ref]$t,[ref]$e);"
        "if($e.Count-ne 0){$e|ForEach-Object{$_.Message};exit 2};exit 0"
    )
    result = _run(POWERSHELL, "-NoProfile", "-Command", command)
    assert result.returncode == 0, (result.stdout, result.stderr)


@pytest.mark.parametrize("mode", ["restore-only", "fresh"])
def test_valid_r5_bundle_passes_for_each_explicit_mode(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
    mode: str,
) -> None:
    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mode=mode)
    result, payload = _validate(bundle, outer_sha, fixture_repository)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert payload["status"] == "PASS"
    assert payload["execution_mode"] == mode
    assert payload["canonical_revision"] == fixture_repository.revision


def test_self_consistent_residual_repoll_mutation_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["timeout_contract"]["residual_repoll_seconds"] = 119.0
        value["process_containment"]["residual_repoll_seconds"] = 119.0

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(
        bundle,
        outer_sha,
        fixture_repository,
        "manifest_runtime_timeout_match_residual_repoll_seconds",
    )


def test_self_consistent_timeout_order_mutation_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["timeout_contract"]["kubectl_timeout_seconds"] = 15.0

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(
        bundle,
        outer_sha,
        fixture_repository,
        "manifest_runtime_timeout_match_kubectl_timeout_seconds",
    )


def test_self_consistent_runtime_blob_mutation_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["runtime"]["core"]["blob_oid"] = "0" * 40

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(bundle, outer_sha, fixture_repository, "manifest_core_blob")


def test_self_consistent_malformed_b0_uid_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["expected_state"]["b0"]["uid"] = "not-a-uid"

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(bundle, outer_sha, fixture_repository, "b0_uid_well_formed")


def test_self_consistent_old_e48_pin_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["canonical_revision"] = OLD_R4_REVISION

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(bundle, outer_sha, fixture_repository, "old_e48_revision_rejected")


def test_restore_only_nonzero_lifecycle_count_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["call_contract"]["restore-only"]["docker_off_probe"] = 1

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(
        bundle,
        outer_sha,
        fixture_repository,
        "restore-only_call_exact_docker_off_probe",
    )


def test_nonzero_downstream_call_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["call_contract"]["downstream"]["full_stack_3180"] = 1

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(
        bundle,
        outer_sha,
        fixture_repository,
        "downstream_call_zero_full_stack_3180",
    )


def test_self_consistent_forbidden_termination_payload_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["adversarial_payload"] = "TerminateJobObject"

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(bundle, outer_sha, fixture_repository, "forbidden_absent_terminate_job_object")


def test_self_consistent_checkpoint_index_sha_mutation_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["checkpoint"]["companion_index"]["sha256"] = "0" * 64

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(
        bundle,
        outer_sha,
        fixture_repository,
        "checkpoint_companion_index_sha",
    )


def test_self_consistent_overwrite_contract_mutation_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        value["evidence"]["write_mode"] = "replace"
        value["output"]["write_mode"] = "replace"

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_manifest=mutate)
    _assert_rejected(bundle, outer_sha, fixture_repository, "evidence_create_exclusive")


def test_duplicate_outer_invocation_is_rejected_even_with_new_outer_sha(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    def duplicate(value: str) -> str:
        return value + "\n# R5_BRIDGE_INVOKE_EXACTLY_ONCE\n& $bridgePath -Mode $Mode\n"

    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository, mutate_outer=duplicate)
    _assert_rejected(bundle, outer_sha, fixture_repository, "outer_exact_one_bridge_marker")


def test_manifest_change_without_sha_chain_update_is_rejected(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository)
    manifest_path = bundle / "phase-b2-r5-work-order.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" \n")
    _assert_rejected(
        bundle,
        outer_sha,
        fixture_repository,
        "bridge_to_manifest_sha_pin",
    )


def test_missing_preexecution_outer_reservation_fails_closed(
    tmp_path: Path,
    fixture_repository: FixtureRepository,
) -> None:
    bundle, outer_sha = _build_bundle(tmp_path, fixture_repository)
    (bundle / "r5-outer-invocation-reservation.json").unlink()
    _assert_rejected(
        bundle,
        outer_sha,
        fixture_repository,
        "bundle_exact_expected_files",
    )


def test_validator_source_covers_required_adversarial_guards() -> None:
    source = VALIDATOR.read_text(encoding="utf-8")
    required_literals = (
        "ExpectedProcessSha256",
        "ExpectedFreshSha256",
        "ExpectedValidatorSha256",
        "ExpectedCheckpointIndexSha256",
        "residual_repoll_exact_120",
        "b0_uid_well_formed",
        "old_e48_revision_rejected",
        "TerminateJobObject",
        "KILL_ON_JOB_CLOSE",
        "wsl_shutdown",
        "output_must_not_exist",
        "bridge_preexecution_validator_before_reservation",
        "Get-GitBlobOid",
    )
    for literal in required_literals:
        assert literal in source

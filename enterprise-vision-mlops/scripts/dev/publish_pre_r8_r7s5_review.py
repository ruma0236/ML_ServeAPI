from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))
sys.path.insert(0, str(SCRIPT_PROJECT_ROOT / "src"))

from evm.scale_validation import phase_b2_r7s5_admission as admission  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_ci as ci  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_dual_clock as dual_clock  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_etw as etw  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_evidence as evidence  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_gate as gate  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_reservation as reservation  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_windows_wsl as windows_wsl  # noqa: E402


SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.review-publisher.v1"
VALIDATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.code-validation.v1"
COMMAND_EVIDENCE_SCHEMA = f"{VALIDATION_SCHEMA}.command-evidence"
REQUIRED_VALIDATION_COMMANDS = frozenset(
    {
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
        "total_bytes": 23_942,
        "inventory_sha256": "f50ae2fba22c41e1004a4b0d6e57258c54d57a2d43bcdb10b3b4989aa1db363b",
    },
    "r6": {
        "root": (
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
            "private/s8-v4/x1-clock-phase-b2-r6-compose-rca/"
            "x1-clock-phase-b2-r6-compose-rca-20260901T024007Z-167cb01"
        ),
        "file_count": 56,
        "total_bytes": 443_844,
        "inventory_sha256": "5e84d0ee31bbdb71569719215804ddc97116c2179dc5654e83143ef100ed8ace",
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
        "enterprise-vision-mlops/ci/pre-r8-r7s5-test-lanes.json",
        "enterprise-vision-mlops/scripts/dev/publish_pre_r8_r7s5_review.py",
        "enterprise-vision-mlops/scripts/dev/run_pre_r8_r7s5_validation.py",
        "enterprise-vision-mlops/scripts/dev/validate_pre_r8_r7s5_ci.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_admission.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_ci.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_dual_clock.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_etw.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_evidence.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_gate.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_reservation.py",
        "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s5_windows_wsl.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s1.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_admission.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_ci.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_dual_clock.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_etw.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_evidence.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_gate.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_reservation.py",
        "enterprise-vision-mlops/tests/test_phase_b2_r7s5_windows_wsl.py",
        "enterprise-vision-mlops/tests/test_publish_pre_r8_r7s5_review.py",
        "enterprise-vision-mlops/tests/test_run_pre_r8_r7s5_validation.py",
        "enterprise-vision-mlops/tests/test_scenario_workload_production.py",
        "enterprise-vision-mlops/tests/test_task_queue_process_safety.py",
    }
)


class ReviewPublisherError(RuntimeError):
    pass


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


def run_git(repository: Path, arguments: Sequence[str], *, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ReviewPublisherError(
            f"git_failed:{arguments[0]}:{result.returncode}:"
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", errors="strict").strip()


def git_snapshot(repository: Path, branch: str) -> dict[str, Any]:
    local = str(run_git(repository, ["rev-parse", "HEAD"]))
    tree = str(run_git(repository, ["rev-parse", "HEAD^{tree}"]))
    active_branch = str(run_git(repository, ["branch", "--show-current"]))
    origin = str(run_git(repository, ["rev-parse", f"refs/remotes/origin/{branch}"]))
    remote_raw = str(run_git(repository, ["ls-remote", "origin", f"refs/heads/{branch}"]))
    remote_fields = remote_raw.split()
    remote = remote_fields[0] if len(remote_fields) == 2 else ""
    tracked = str(run_git(repository, ["status", "--porcelain=v1", "--untracked-files=no"]))
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


def untracked_summary(repository: Path) -> dict[str, Any]:
    raw = run_git(repository, ["ls-files", "--others", "--exclude-standard", "-z"], binary=True)
    assert isinstance(raw, bytes)
    paths = sorted(
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in raw.split(b"\0")
        if item
    )
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
    for item in (resolved, *resolved.rglob("*")):
        attributes = getattr(os.lstat(item), "st_file_attributes", 0)
        if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise ReviewPublisherError(f"inventory_reparse_point_forbidden:{item}")
    files = sorted((item for item in resolved.rglob("*") if item.is_file()), key=str)
    inventory = [
        {
            "relative_path": item.relative_to(resolved).as_posix(),
            "bytes": item.stat().st_size,
            "sha256": sha256_file(item),
        }
        for item in files
    ]
    return {
        "root": str(resolved),
        "file_count": len(inventory),
        "total_bytes": sum(item["bytes"] for item in inventory),
        "files": inventory,
        "inventory_sha256": hashlib.sha256(canonical_json_bytes(inventory)).hexdigest(),
        "read_only_operation": True,
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
        "total_bytes": inventory["total_bytes"],
        "inventory_sha256": inventory["inventory_sha256"],
    }
    if projection != {
        "file_count": expected["file_count"],
        "total_bytes": expected["total_bytes"],
        "inventory_sha256": expected["inventory_sha256"],
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


def selected_source_inventory(repository: Path) -> dict[str, Any]:
    tracked = sorted(
        item
        for item in str(
            run_git(
                repository, ["diff", "--name-only", f"{ci.EXPECTED_BASELINE_COMMIT}..HEAD", "--"]
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
        committed = run_git(repository, ["show", f"HEAD:{relative}"], binary=True)
        assert isinstance(committed, bytes)
        entries.append(
            {
                "path": relative,
                "worktree_bytes": path.stat().st_size,
                "worktree_sha256": sha256_file(path),
                "git_blob": str(run_git(repository, ["rev-parse", f"HEAD:{relative}"])),
                "committed_bytes": len(committed),
                "committed_sha256": hashlib.sha256(committed).hexdigest(),
            }
        )
    return {
        "files": entries,
        "file_count": len(entries),
        "inventory_sha256": hashlib.sha256(canonical_json_bytes(entries)).hexdigest(),
    }


def measure_process_identity(pid: int) -> dict[str, Any]:
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
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            text=True,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewPublisherError("windows_process_identity_timeout") from exc
    if result.returncode != 0:
        raise ReviewPublisherError("windows_process_identity_read_failed")
    try:
        raw = json.loads(result.stdout)
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


def validate_token_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"codex_pid", "publisher_parent_pid"}:
        raise ReviewPublisherError("token_evidence_keys_not_exact")
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
    runtime_identity = measure_process_identity(os.getpid())
    parent_identity = measure_process_identity(parent_pid)
    codex_identity = measure_process_identity(codex_pid)
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
    return {
        "codex": {"token": codex_token, "process": codex_identity},
        "publisher_parent": {"token": parent_token, "process": parent_identity},
        "publisher_runtime": {"token": runtime_token, "process": runtime_identity},
        "launcher_settings_readback": {
            "sandbox_mode": "danger-full-access",
            "approval_policy": "never",
            "source": "codex_process_command_line_direct_readback",
        },
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


def validate_code_summary(
    value: Mapping[str, Any],
    *,
    repository: Path,
    project_root: Path,
    expected_head: str,
    expected_tree: str,
) -> dict[str, Any]:
    """Validate local, non-attested code checks against an exact live tool plan.

    This deliberately does not promote the records to external execution
    attestation.  The zero-call values are scoped to commands spawned directly
    by this validation orchestrator; descendant OS telemetry is not claimed.
    """

    from scripts.dev import run_pre_r8_r7s5_validation as validation_runner

    expected_keys = {
        "schema",
        "status",
        "repository",
        "project_root",
        "head",
        "tree",
        "command_plan",
        "command_plan_sha256",
        "commands",
        "live_call_counts",
        "live_call_observation_scope",
        "completion_marker_created",
        "success_marker_created",
        "r8_authorized",
    }
    if set(value) != expected_keys:
        raise ReviewPublisherError("code_validation_summary_keys_not_exact")
    repository = repository.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    expected_head = _hex40(expected_head, "code_validation_head")
    expected_tree = _hex40(expected_tree, "code_validation_tree")
    if (
        value.get("schema") != VALIDATION_SCHEMA
        or value.get("status") != "PASS"
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
    if plan.get("sha256") != plan_digest:
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
        python_general = Path(by_name["r7s5-focused-pytest-py311"]["argv"][0])
        python_host = Path(by_name["pinned-host-pytest-py313"]["argv"][0])
        python_ruff = Path(by_name["ruff-check-0.12.2"]["argv"][0])
        specs = validation_runner.build_command_specs(
            repository=repository,
            project_root=project_root,
            python_general=python_general,
            python_host=python_host,
            python_ruff=python_ruff,
        )
        live_plan = validation_runner.command_plan(
            repository=repository,
            project_root=project_root,
            head=expected_head,
            tree=expected_tree,
            specs=specs,
        )
    except (KeyError, OSError, TypeError, validation_runner.ValidationRunnerError) as exc:
        raise ReviewPublisherError("code_validation_live_plan_reconstruction_failed") from exc
    if plan != live_plan or plan_digest != live_plan["sha256"]:
        raise ReviewPublisherError("code_validation_live_plan_mismatch")

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
        "command_plan_sha256",
        "tool",
        "started_at_utc",
        "ended_at_utc",
        "duration_ns",
        "stdout_bytes",
        "stdout_sha256",
        "stdout_tail",
        "stderr_bytes",
        "stderr_sha256",
        "stderr_tail",
        "automatic_retry_count",
        "orchestrator_prohibited_live_command_calls",
        "live_call_observation_scope",
    }
    for index, (command, spec, planned) in enumerate(
        zip(commands, specs, live_plan["commands"], strict=True), start=1
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
        record = read_json_mapping(path, f"command_evidence_{command['name']}")
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
            "automatic_retry_count": 0,
            "orchestrator_prohibited_live_command_calls": 0,
            "live_call_observation_scope": validation_runner.VALIDATION_OBSERVATION_SCOPE,
        }
        if any(record.get(key) != expected for key, expected in fixed.items()):
            raise ReviewPublisherError("code_validation_evidence_payload_mismatch")
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
        normalized_commands.append(dict(command))
    if len(evidence_parents) != 1:
        raise ReviewPublisherError("code_validation_evidence_directory_not_exact")

    zero_calls = value.get("live_call_counts")
    if (
        not isinstance(zero_calls, dict)
        or set(zero_calls) != REQUIRED_ZERO_LIVE_CALLS
        or any(type(item) is not int or item != 0 for item in zero_calls.values())
    ):
        raise ReviewPublisherError("code_validation_live_calls_nonzero_or_unknown")
    if value.get("live_call_observation_scope") != validation_runner.VALIDATION_OBSERVATION_SCOPE:
        raise ReviewPublisherError("code_validation_live_call_scope_mismatch")
    if value.get("completion_marker_created") is not False:
        raise ReviewPublisherError("code_validation_completion_marker_forbidden")
    if value.get("success_marker_created") is not False or value.get("r8_authorized") is not False:
        raise ReviewPublisherError("code_validation_success_or_r8_authorization_forbidden")
    return {**dict(value), "commands": normalized_commands}


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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish append-only pre-r8 r7s5 review evidence")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--canonical-repository", type=Path, required=True)
    parser.add_argument("--canonical-branch", required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output-leaf", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--attempt-uuid", required=True)
    parser.add_argument("--r7s4-evidence", type=Path, required=True)
    parser.add_argument("--r6-rca", type=Path, required=True)
    parser.add_argument("--etw-amendment", type=Path, required=True)
    parser.add_argument("--ci-readback", type=Path, required=True)
    parser.add_argument("--ci-manifest", type=Path, required=True)
    parser.add_argument("--token-evidence", type=Path, required=True)
    parser.add_argument("--validation-summary", type=Path, required=True)
    parser.add_argument("--expected-untracked-count", type=int, required=True)
    parser.add_argument("--expected-untracked-path-sha256", required=True)
    parser.add_argument("--expected-untracked-content-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
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
    canonical_git = git_snapshot(canonical_repository, args.canonical_branch)
    isolated_head = str(run_git(repository, ["rev-parse", "HEAD"]))
    isolated_tree = str(run_git(repository, ["rev-parse", "HEAD^{tree}"]))
    if isolated_head != canonical_git["local_head"] or isolated_tree != canonical_git["tree"]:
        raise ReviewPublisherError("isolated_canonical_commit_tree_mismatch")
    isolated_tracked = str(
        run_git(repository, ["status", "--porcelain=v1", "--untracked-files=no"])
    )
    if isolated_tracked:
        raise ReviewPublisherError("isolated_tracked_changes_present")

    untracked = untracked_summary(canonical_repository)
    if (
        untracked["count"] != args.expected_untracked_count
        or untracked["regular_files"] != args.expected_untracked_count
        or untracked["path_inventory_sha256"] != args.expected_untracked_path_sha256
        or untracked["content_inventory_sha256"]
        != _hex64(args.expected_untracked_content_sha256, "expected_untracked_content")
    ):
        raise ReviewPublisherError("canonical_user_untracked_inventory_changed")

    token = validate_token_evidence(read_json_mapping(args.token_evidence, "token_evidence"))
    validation = validate_code_summary(
        read_json_mapping(args.validation_summary, "validation_summary"),
        repository=repository,
        project_root=project_root,
        expected_head=isolated_head,
        expected_tree=isolated_tree,
    )
    ci_validation = ci.load_and_validate_manifest(
        args.ci_manifest.resolve(strict=True), project_root=project_root
    )
    ci_manifest = ci.load_manifest(args.ci_manifest.resolve(strict=True), project_root=project_root)
    source_inventory = selected_source_inventory(repository)
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
    contracts = {
        "reservation": reservation.reservation_contract(),
        "admission": admission.admission_contract(),
        "gate": gate.gate_contract(),
        "dual_clock": dual_clock.dual_clock_contract(),
        "etw": etw.etw_contract(),
        "evidence": evidence.source_contract(),
        "windows_wsl": windows_wsl.source_contract(),
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
            "source_inventory": source_inventory,
            "contracts": contracts,
        },
        "code-validation-summary.json": validation,
        "offline-admission-decision.json": {
            "schema": f"{SCHEMA}.offline-admission-decision",
            "status": "manual_intervention_required",
            "credit": "zero_credit",
            "decision": "NO-GO",
            "gate": gate_decision,
            "etw_optional_diagnostic": etw_not_run_decision(),
            "blockers": blockers,
            "r8_calls": 0,
            "restore_only_calls": 0,
            "dual_collector_calls": 0,
            "service_lifecycle_calls": 0,
            "automatic_retry_count": 0,
            "force_kill_calls": 0,
            "completion_marker_created": False,
            "success_marker_created": False,
            "go_evidence_eligible": False,
        },
    }
    # Re-read every historical checkpoint immediately before the only publish
    # call.  We make a bounded read-back claim, not an unsupported assertion
    # that external directories can never change after publication.
    second_historical = {
        "r7s4": verify_sealed_directory(args.r7s4_evidence, "r7s4"),
        "r6": verify_sealed_directory(args.r6_rca, "r6"),
        "etw_amendment": verify_sealed_etw_amendment(args.etw_amendment),
    }
    if any(second_historical[key] != historical[key] for key in second_historical):
        raise ReviewPublisherError("historical_checkpoint_changed_during_publication_preflight")
    if untracked_summary(canonical_repository) != untracked:
        raise ReviewPublisherError("canonical_user_untracked_changed_during_publication_preflight")
    batch = evidence.publish_identity_catalogued_batch(
        args.parent,
        args.output_leaf,
        documents,
        run_uuid=args.run_uuid,
    )
    print(json.dumps(batch.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import time
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for search_path in (ROOT, SRC):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))


_EARLY_MANIFEST_PATH: Path | None = None
_EARLY_MANIFEST_BYTES: bytes | None = None


def _early_cli_value(argv: Sequence[str], option: str) -> str:
    positions = [index for index, value in enumerate(argv) if value == option]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise RuntimeError(f"pretrust_cli_option_exactly_once_required:{option}")
    value = str(argv[positions[0] + 1])
    if not value or value.startswith("--"):
        raise RuntimeError(f"pretrust_cli_option_value_required:{option}")
    return value


def _early_runtime_snapshots(
    argv: Sequence[str],
    *,
    runner_path: Path,
    expected_runtime_paths: Mapping[str, Path] | None = None,
) -> tuple[Path, bytes, dict[str, tuple[Path, bytes]]]:
    manifest_path = Path(_early_cli_value(argv, "--manifest")).resolve()
    if manifest_path.name != "phase-b2-r7-work-order.json":
        raise RuntimeError("pretrust_manifest_leaf_mismatch")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("pretrust_manifest_unreadable") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("pretrust_manifest_object_required")
    try:
        evidence_bytes = base64.b64decode(
            _early_cli_value(argv, "--launcher-evidence-base64"), validate=True
        )
        evidence = json.loads(evidence_bytes.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("pretrust_launcher_evidence_unreadable") from exc
    if not isinstance(evidence, dict) or not isinstance(evidence.get("sha_chain"), dict):
        raise RuntimeError("pretrust_launcher_sha_chain_required")
    chain = evidence["sha_chain"]
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if chain.get("manifest") != manifest_sha:
        raise RuntimeError("pretrust_manifest_sha256_mismatch")
    if evidence.get("mode") != "restore-only" or manifest.get("execution_mode") != "restore-only":
        raise RuntimeError("pretrust_restore_only_mode_required")
    if evidence.get("run_id") != manifest.get("bundle_id"):
        raise RuntimeError("pretrust_launcher_run_id_mismatch")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise RuntimeError("pretrust_runtime_mapping_required")
    expected_paths = (
        {role: Path(path).resolve() for role, path in expected_runtime_paths.items()}
        if expected_runtime_paths is not None
        else {
            "runner": runner_path.resolve(),
            "process": (SRC / "evm" / "scale_validation" / "phase_b2_r7_process.py").resolve(),
            "core": (SRC / "evm" / "scale_validation" / "phase_b2_r7.py").resolve(),
        }
    )
    if set(expected_paths) != {"runner", "process", "core"}:
        raise RuntimeError("pretrust_expected_runtime_role_set_mismatch")
    snapshots: dict[str, tuple[Path, bytes]] = {}
    for role in ("runner", "process", "core"):
        pin = runtime.get(role)
        if not isinstance(pin, dict) or set(pin) != {"path", "sha256", "blob_oid", "bytes"}:
            raise RuntimeError(f"pretrust_runtime_pin_invalid:{role}")
        path = Path(str(pin["path"])).resolve()
        if os.path.normcase(str(path)) != os.path.normcase(str(expected_paths[role])):
            raise RuntimeError(f"pretrust_runtime_path_mismatch:{role}")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"pretrust_runtime_unreadable:{role}") from exc
        measured = hashlib.sha256(payload).hexdigest()
        if (
            measured != str(pin["sha256"]).lower()
            or measured != str(chain.get(role, "")).lower()
            or isinstance(pin["bytes"], bool)
            or pin["bytes"] != len(payload)
        ):
            raise RuntimeError(f"pretrust_runtime_snapshot_mismatch:{role}")
        snapshots[role] = (path, payload)
    return manifest_path, manifest_bytes, snapshots


def _load_module_snapshot(module_name: str, path: Path, payload: bytes) -> types.ModuleType:
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = module_name.rpartition(".")[0]
    sys.modules[module_name] = module
    try:
        exec(compile(payload, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _install_verified_runtime_snapshots(argv: Sequence[str]) -> None:
    global _EARLY_MANIFEST_BYTES, _EARLY_MANIFEST_PATH
    manifest_path, manifest_bytes, snapshots = _early_runtime_snapshots(
        argv, runner_path=Path(__file__)
    )
    for role, module_name in (
        ("process", "evm.scale_validation.phase_b2_r7_process"),
        ("core", "evm.scale_validation.phase_b2_r7"),
    ):
        path, payload = snapshots[role]
        _load_module_snapshot(module_name, path, payload)
    _EARLY_MANIFEST_PATH = manifest_path
    _EARLY_MANIFEST_BYTES = manifest_bytes


if __name__ == "__main__":
    _install_verified_runtime_snapshots(sys.argv[1:])

from evm.scale_validation.phase_b2_r7 import (  # noqa: E402
    EvidenceWriter,
    HISTORICAL_DECISION_AUTHORITY,
    HISTORICAL_QUERY_SHA256,
    HISTORICAL_QUERY_TEXTS,
    R7_REQUIRED_INVARIANTS,
    RESTORE_LIFECYCLE_COUNTS,
    ReconcileRestoreHarness,
    RestoreCheckpoint,
    RestoreDeadline,
    RestoreReport,
    RestoreStage,
    TimeoutContract,
    decode_launcher_evidence,
    r7_restore_report,
    read_parent_checkpoints,
    sha256_file,
    validate_r7_manifest,
)
from evm.scale_validation.phase_b2_r7_process import (  # noqa: E402
    TimeoutContract as ProcessTimeoutContract,
    WindowsJobProcessRunner,
)


OUTER_LEAF = "invoke-verified-x1-phase-b2-r7.ps1"
BRIDGE_LEAF = "invoke-x1-phase-b2-r7-bridge.ps1"
MANIFEST_LEAF = "phase-b2-r7-work-order.json"
OUTER_RESERVATION = "r7-outer-invocation-reservation.json"
BRIDGE_RESERVATION = "r7-bridge-invocation-reservation.json"
RUNNER_RESERVATION = "r7-runner-invocation-reservation.json"
MODE = "restore-only"
CANONICAL_BRANCH = "codex/distributed-scale-validation-plan"
FULL_SHA1 = re.compile(r"^[0-9a-f]{40}$")
FULL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

EXPECTED_PROMETHEUS_JOBS = {
    "evm-api",
    "evm-task-queue-worker",
    "evm-b0-production",
    "evm-otel-collector",
    "prometheus",
}

CONTROL_PLANE_HISTORY_QUERY = HISTORICAL_QUERY_TEXTS["control_plane_task_entity_statuses"]
MLFLOW_HISTORY_QUERY = HISTORICAL_QUERY_TEXTS["mlflow_running_rows"]
KUBERNETES_FAILED_QUERY = HISTORICAL_QUERY_TEXTS["kubernetes_terminal_failed_objects"]
CONTROL_PLANE_EXECUTION_LINK_QUERY = (
    "SELECT e.entity_id,"
    "(SELECT count(*) FROM evm_control_plane.task_admission_queue q "
    "WHERE q.task_id=e.entity_id AND q.state IN "
    "('available','retry_wait','leased','runtime_pending','outcome_unknown'))+"
    "(SELECT count(*) FROM evm_control_plane.task_dispatch_effects d "
    "WHERE d.task_id=e.entity_id AND d.state IN "
    "('reserved','submitting','submitted','outcome_unknown')),"
    "(SELECT count(*) FROM evm_control_plane.lifecycle_claims c "
    "WHERE c.released_at IS NULL AND c.expires_at>clock_timestamp() AND c.run_id IN "
    "(e.entity_id,COALESCE(e.payload->>'run_id',''),"
    "COALESCE(e.payload->>'dag_run_id',''),COALESCE(e.payload->>'lifecycle_run_id',''))),"
    "(SELECT count(*) FROM evm_control_plane.task_admission_queue q "
    "WHERE q.task_id=e.entity_id AND (q.state='leased' OR "
    "(q.lease_owner IS NOT NULL AND q.lease_expires_at>clock_timestamp()))),"
    "(SELECT count(*) FROM evm_control_plane.task_admission_queue q "
    "WHERE q.task_id=e.entity_id AND q.state='outcome_unknown')+"
    "(SELECT count(*) FROM evm_control_plane.task_dispatch_effects d "
    "WHERE d.task_id=e.entity_id AND d.state='outcome_unknown') "
    "FROM evm_control_plane.entities e WHERE e.entity_kind='task_assignment' "
    "AND e.state IN ('queued','pending_confirmation','running') ORDER BY e.entity_id;"
)
KNOWN_QUEUE_STATES = (
    "available",
    "retry_wait",
    "leased",
    "runtime_pending",
    "outcome_unknown",
    "completed",
    "failed",
    "dlq",
    "expired",
    "cancelled",
)

RESTORE_STAGE_KEYS = {
    "docker_engine": RestoreStage.DOCKER_ENGINE.value,
    "compose": RestoreStage.COMPOSE.value,
    "kubernetes_api": RestoreStage.KUBERNETES_API.value,
    "node_device_plugin_gpu": RestoreStage.NODE_DEVICE_PLUGIN_GPU.value,
    "b0_identity_cuda": RestoreStage.B0_IDENTITY_CUDA.value,
    "prometheus": RestoreStage.PROMETHEUS.value,
    "api_release_identity": RestoreStage.API_RELEASE_IDENTITY.value,
    "queue_jobs_lease_residue": RestoreStage.QUEUE_JOBS_LEASE_RESIDUE.value,
}


class R7RunnerError(RuntimeError):
    """Fail-closed r7 restore-only runner error."""


class DuplicateInvocationError(R7RunnerError):
    """Raised before probes when a one-shot runner reservation already exists."""


@dataclass(frozen=True)
class PreparedExecution:
    args: argparse.Namespace
    manifest: Mapping[str, Any]
    manifest_sha256: str
    launcher_evidence: Mapping[str, Any]
    parent_payloads: Mapping[str, Mapping[str, Any]]
    restore_checkpoint: RestoreCheckpoint
    timeout_contract: TimeoutContract
    output_directory: Path
    run_id: str
    bundle_directory: Path


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R7RunnerError(f"{label}_mapping_required")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise R7RunnerError(f"{label}_sequence_required")
    return value


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise R7RunnerError(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise R7RunnerError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise R7RunnerError(f"{label}_object_required:{path}")
    return value


def _read_json_snapshot(path: Path, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise R7RunnerError(f"{label}_missing:{path}")
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7RunnerError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise R7RunnerError(f"{label}_object_required:{path}")
    return value, hashlib.sha256(payload).hexdigest()


def _read_manifest_snapshot_with_sha(path: Path) -> tuple[dict[str, Any], str]:
    resolved = path.resolve()
    if (
        _EARLY_MANIFEST_PATH is not None
        and _EARLY_MANIFEST_BYTES is not None
        and _resolved_equal(resolved, _EARLY_MANIFEST_PATH)
    ):
        try:
            value = json.loads(_EARLY_MANIFEST_BYTES.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R7RunnerError("verified_manifest_snapshot_invalid") from exc
        if not isinstance(value, dict):
            raise R7RunnerError("verified_manifest_snapshot_object_required")
        return value, hashlib.sha256(_EARLY_MANIFEST_BYTES).hexdigest()
    return _read_json_snapshot(resolved, "manifest")


def _read_manifest_snapshot(path: Path) -> dict[str, Any]:
    value, _sha256 = _read_manifest_snapshot_with_sha(path)
    return value


def _verify_etw_amendment(manifest: Mapping[str, Any]) -> None:
    contract = _mapping(manifest.get("etw_contract"), "etw_contract")
    path = Path(str(contract.get("amendment_path", ""))).resolve()
    expected_sha = str(contract.get("amendment_sha256", "")).lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        raise R7RunnerError("etw_amendment_sha256_invalid")
    if not path.is_file():
        raise R7RunnerError(f"etw_amendment_missing:{path}")
    try:
        measured_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise R7RunnerError(f"etw_amendment_unreadable:{path}") from exc
    if measured_sha != expected_sha:
        raise R7RunnerError("etw_amendment_sha256_mismatch")


def _resolved_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise DuplicateInvocationError(f"runner_reservation_exists:{path}") from exc
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("runner_reservation_write_made_no_progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_launcher_files(
    manifest_path: Path, launcher_evidence: Mapping[str, Any]
) -> dict[str, str]:
    bundle = manifest_path.resolve().parent
    paths = {
        "outer": bundle / OUTER_LEAF,
        "bridge": bundle / BRIDGE_LEAF,
        "manifest": manifest_path.resolve(),
    }
    chain = _mapping(launcher_evidence.get("sha_chain"), "launcher_sha_chain")
    measured: dict[str, str] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise R7RunnerError(f"launcher_file_missing:{name}:{path}")
        actual = sha256_file(path)
        if actual != str(chain.get(name, "")).lower():
            raise R7RunnerError(f"launcher_file_sha256_mismatch:{name}")
        measured[name] = actual
    return measured


def _verify_launcher_git(manifest: Mapping[str, Any], launcher_evidence: Mapping[str, Any]) -> None:
    git = _mapping(launcher_evidence.get("git"), "launcher_git")
    repository = _mapping(manifest.get("repository"), "manifest_repository")
    revision = str(manifest.get("canonical_revision", "")).lower()
    tree = str(manifest.get("canonical_tree", "")).lower()
    if launcher_evidence.get("mode") != MODE or manifest.get("execution_mode") != MODE:
        raise R7RunnerError("launcher_mode_mismatch")
    if any(
        str(git.get(name, "")).lower() != revision
        for name in ("revision", "origin_revision", "remote_revision")
    ):
        raise R7RunnerError("launcher_local_origin_remote_mismatch")
    if str(git.get("tree", "")).lower() != tree:
        raise R7RunnerError("launcher_tree_mismatch")
    if str(git.get("branch", "")) != CANONICAL_BRANCH:
        raise R7RunnerError("launcher_branch_mismatch")
    if int(git.get("tracked", -1)) != 0:
        raise R7RunnerError("launcher_tracked_changes_present")
    if int(git.get("untracked", -1)) != int(repository.get("preserved_untracked_count", -2)):
        raise R7RunnerError("launcher_untracked_count_mismatch")
    if (
        str(git.get("untracked_path_set_sha256", "")).lower()
        != str(repository.get("untracked_path_set_sha256", "")).lower()
    ):
        raise R7RunnerError("launcher_untracked_path_set_mismatch")


def _verify_reservation(
    path: Path,
    *,
    schema: str,
    output_directory: Path,
    run_id: str,
) -> Mapping[str, Any]:
    value = _read_json_object(path, path.stem)
    if value.get("schema") != schema:
        raise R7RunnerError(f"reservation_schema_mismatch:{path.name}")
    if value.get("mode") != MODE or value.get("run_id") != run_id:
        raise R7RunnerError(f"reservation_identity_mismatch:{path.name}")
    if not _resolved_equal(Path(str(value.get("output_directory", ""))), output_directory):
        raise R7RunnerError(f"reservation_output_mismatch:{path.name}")
    if int(value.get("pid", 0)) <= 0:
        raise R7RunnerError(f"reservation_pid_invalid:{path.name}")
    return value


def prepare_execution(args: argparse.Namespace) -> PreparedExecution:
    manifest_path = args.manifest.resolve()
    repository_root = args.repository_root.resolve()
    if manifest_path.name != MANIFEST_LEAF:
        raise R7RunnerError("r7_manifest_leaf_mismatch")
    manifest, manifest_sha256 = _read_manifest_snapshot_with_sha(manifest_path)
    timeout_contract = TimeoutContract().validate()
    _verify_etw_amendment(manifest)
    validate_r7_manifest(
        manifest,
        expected_revision=args.expected_revision,
        mode=args.mode,
        repository_root=repository_root,
        runtime_timeout=timeout_contract,
    )
    launcher = decode_launcher_evidence(args.launcher_evidence_base64, manifest)
    if launcher.get("run_id") != manifest.get("bundle_id"):
        raise R7RunnerError("launcher_run_id_mismatch")
    invocation_counts = _mapping(launcher.get("invocation_counts"), "launcher_invocation_counts")
    if dict(invocation_counts) != {
        "outer": 1,
        "bridge": 1,
        "runner": 1,
        "automatic_retry": 0,
    }:
        raise R7RunnerError("launcher_invocation_counts_mismatch")
    _verify_launcher_files(manifest_path, launcher)
    _verify_launcher_git(manifest, launcher)

    output = _mapping(manifest.get("output"), "manifest_output")
    output_directory = args.output_directory.resolve()
    if not _resolved_equal(output_directory, Path(str(output.get("path", "")))):
        raise R7RunnerError("output_argument_manifest_path_mismatch")
    if output_directory.exists():
        raise R7RunnerError(f"output_directory_exists:{output_directory}")
    revision = str(manifest.get("canonical_revision", "")).lower()
    if revision != args.expected_revision.lower() or FULL_SHA1.fullmatch(revision) is None:
        raise R7RunnerError("expected_revision_mismatch")
    run_id = str(manifest.get("bundle_id", ""))
    if not run_id or "r7" not in run_id.lower():
        raise R7RunnerError("r7_bundle_id_required")

    parent_payloads, checkpoint = read_parent_checkpoints(manifest.get("parent_checkpoints"))
    if not isinstance(checkpoint, RestoreCheckpoint):
        raise R7RunnerError("restore_checkpoint_required")

    bundle = manifest_path.parent
    bundle_contract = _mapping(manifest.get("bundle"), "manifest_bundle")
    if not _resolved_equal(bundle, Path(str(bundle_contract.get("path", "")))):
        raise R7RunnerError("bundle_argument_manifest_path_mismatch")
    _verify_reservation(
        bundle / OUTER_RESERVATION,
        schema="s8-v4-x1-phase-b2-r7-outer-reservation/v1",
        output_directory=output_directory,
        run_id=run_id,
    )
    _verify_reservation(
        bundle / BRIDGE_RESERVATION,
        schema="s8-v4-x1-phase-b2-r7-bridge-reservation/v1",
        output_directory=output_directory,
        run_id=run_id,
    )
    return PreparedExecution(
        args=args,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        launcher_evidence=launcher,
        parent_payloads=parent_payloads,
        restore_checkpoint=checkpoint,
        timeout_contract=timeout_contract,
        output_directory=output_directory,
        run_id=run_id,
        bundle_directory=bundle,
    )


def reserve_runner(prepared: PreparedExecution) -> None:
    _write_runner_reservation(
        bundle_directory=prepared.bundle_directory,
        output_directory=prepared.output_directory,
        run_id=prepared.run_id,
    )


def _write_runner_reservation(
    *, bundle_directory: Path, output_directory: Path, run_id: str
) -> None:
    _write_exclusive_json(
        bundle_directory / RUNNER_RESERVATION,
        {
            "schema": "s8-v4-x1-phase-b2-r7-runner-reservation/v1",
            "created_at_unix_ns": time.time_ns(),
            "pid": os.getpid(),
            "mode": MODE,
            "run_id": run_id,
            "output_directory": str(output_directory),
        },
    )


def reserve_runner_preflight(args: argparse.Namespace) -> None:
    """Own the one-shot runner identity before mutable full preflight work."""
    manifest_path = args.manifest.resolve()
    if manifest_path.name != MANIFEST_LEAF:
        raise R7RunnerError("r7_manifest_leaf_mismatch")
    manifest = _read_manifest_snapshot(manifest_path)
    if args.mode != MODE or manifest.get("execution_mode") != MODE:
        raise R7RunnerError("runner_preflight_mode_mismatch")
    revision = str(manifest.get("canonical_revision", "")).lower()
    if revision != str(args.expected_revision).lower() or FULL_SHA1.fullmatch(revision) is None:
        raise R7RunnerError("runner_preflight_revision_mismatch")
    run_id = str(manifest.get("bundle_id", ""))
    if not run_id or "r7" not in run_id.lower():
        raise R7RunnerError("r7_bundle_id_required")
    bundle_directory = manifest_path.parent
    bundle_contract = _mapping(manifest.get("bundle"), "manifest_bundle")
    if not _resolved_equal(bundle_directory, Path(str(bundle_contract.get("path", "")))):
        raise R7RunnerError("bundle_argument_manifest_path_mismatch")
    output_directory = args.output_directory.resolve()
    output_contract = _mapping(manifest.get("output"), "manifest_output")
    if not _resolved_equal(output_directory, Path(str(output_contract.get("path", "")))):
        raise R7RunnerError("output_argument_manifest_path_mismatch")
    if output_directory.exists():
        raise R7RunnerError(f"output_directory_exists:{output_directory}")
    _verify_reservation(
        bundle_directory / OUTER_RESERVATION,
        schema="s8-v4-x1-phase-b2-r7-outer-reservation/v1",
        output_directory=output_directory,
        run_id=run_id,
    )
    _verify_reservation(
        bundle_directory / BRIDGE_RESERVATION,
        schema="s8-v4-x1-phase-b2-r7-bridge-reservation/v1",
        output_directory=output_directory,
        run_id=run_id,
    )
    _write_runner_reservation(
        bundle_directory=bundle_directory,
        output_directory=output_directory,
        run_id=run_id,
    )


def _verify_owned_runner_reservation(prepared: PreparedExecution) -> None:
    reservation = _verify_reservation(
        prepared.bundle_directory / RUNNER_RESERVATION,
        schema="s8-v4-x1-phase-b2-r7-runner-reservation/v1",
        output_directory=prepared.output_directory,
        run_id=prepared.run_id,
    )
    if int(reservation.get("pid", -1)) != os.getpid():
        raise DuplicateInvocationError("runner_reservation_owner_mismatch")


def _process_timeout(contract: TimeoutContract) -> ProcessTimeoutContract:
    return ProcessTimeoutContract(**contract.to_dict())


class R7ProbeSet:
    """Read-only runtime gate with Job containment and no probe retries."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        contract: TimeoutContract,
        expected_revision: str,
        repository_root: Path,
        parent_payloads: Mapping[str, Mapping[str, Any]] | None = None,
        process_runner: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.manifest = dict(manifest)
        self.contract = contract.validate()
        self.expected_revision = expected_revision.lower()
        self.repository_root = repository_root.resolve()
        self.parent_payloads = dict(parent_payloads or {})
        self.expected = _mapping(self.manifest.get("expected_state"), "expected_state")
        self.docker = self._find_executable(
            "docker",
            Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
        )
        self.kubectl = self._find_executable(
            "kubectl",
            Path("C:/Program Files/Docker/Docker/resources/bin/kubectl.exe"),
        )
        self.wsl = self._find_executable(
            "wsl",
            Path("C:/Windows/System32/wsl.exe"),
        )
        self.powershell = self._find_executable(
            "powershell",
            Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"),
        )
        self.runner = process_runner or WindowsJobProcessRunner(_process_timeout(contract))
        self.clock = clock
        self.sleep = sleep

    @staticmethod
    def _find_executable(name: str, fallback: Path) -> str:
        found = shutil.which(name)
        if found:
            return found
        if fallback.is_file():
            return str(fallback)
        raise R7RunnerError(f"required_executable_missing:{name}")

    @property
    def launch_budget_seconds(self) -> float:
        return (
            self.contract.wrapper_timeout_seconds
            + self.contract.residual_repoll_seconds
            + self.contract.stream_drain_seconds
        )

    def _kubectl_command(self, *arguments: str) -> list[str]:
        seconds = int(self.contract.kubectl_timeout_seconds)
        return [self.kubectl, f"--request-timeout={seconds}s", *arguments]

    @staticmethod
    def _json_object(
        result: Mapping[str, Any], name: str
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        if not result.get("passed"):
            return None, str(result.get("last_error") or f"{name}_command_failed")
        try:
            value = json.loads(str(result.get("stdout", "")))
        except json.JSONDecodeError:
            return None, f"{name}_json_invalid"
        if not isinstance(value, Mapping):
            return None, f"{name}_json_object_required"
        return value, None

    @staticmethod
    def _parse_queue_readback(stdout: str) -> tuple[int, int, int, int, int]:
        fields = stdout.strip().split("|")
        if len(fields) != 5:
            raise ValueError("queue_field_count")
        values: list[int] = []
        for field in fields:
            value = int(field)
            if value < 0:
                raise ValueError("queue_negative_count")
            values.append(value)
        return tuple(values)  # type: ignore[return-value]

    @staticmethod
    def _parse_control_execution_links(stdout: str) -> dict[str, dict[str, int]]:
        links: dict[str, dict[str, int]] = {}
        names = (
            "active_job_count",
            "active_claim_count",
            "active_lease_count",
            "outcome_unknown_count",
        )
        for line in stdout.splitlines():
            if not line.strip():
                continue
            fields = line.split("|")
            if len(fields) != 5 or not fields[0] or fields[0] in links:
                raise ValueError("control_execution_link_fields_invalid")
            counts = [int(value) for value in fields[1:]]
            if any(value < 0 for value in counts):
                raise ValueError("control_execution_link_negative")
            links[fields[0]] = dict(zip(names, counts, strict=True))
        return links

    @staticmethod
    def _kubernetes_job_snapshot(items: Any) -> dict[str, Any]:
        if not isinstance(items, list):
            raise ValueError("kubernetes_job_items_list_required")
        active_count = 0
        terminal: list[dict[str, str]] = []
        unproven: list[dict[str, str]] = []
        for raw in items:
            if not isinstance(raw, Mapping):
                raise ValueError("kubernetes_job_item_mapping_required")
            metadata = raw.get("metadata")
            status = raw.get("status")
            if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
                raise ValueError("kubernetes_job_metadata_status_required")
            identity = {
                "uid": str(metadata.get("uid", "")),
                "namespace": str(metadata.get("namespace", "")),
                "name": str(metadata.get("name", "")),
            }
            if not all(identity.values()):
                raise ValueError("kubernetes_job_identity_required")
            raw_active = status.get("active", 0)
            if isinstance(raw_active, bool) or not isinstance(raw_active, int) or raw_active < 0:
                raise ValueError("kubernetes_job_active_nonnegative_integer_required")
            conditions = status.get("conditions", [])
            if not isinstance(conditions, list):
                raise ValueError("kubernetes_job_conditions_list_required")
            terminal_condition = any(
                isinstance(condition, Mapping)
                and str(condition.get("type")) in {"Complete", "Failed"}
                and str(condition.get("status")).lower() == "true"
                for condition in conditions
            )
            active_count += raw_active
            if raw_active > 0:
                continue
            if terminal_condition:
                terminal.append(identity)
            else:
                unproven.append(identity)

        def key(item: Mapping[str, str]) -> tuple[str, str, str]:
            return item["namespace"], item["name"], item["uid"]

        return {
            "active_count": active_count,
            "terminal": sorted(terminal, key=key),
            "unproven": sorted(unproven, key=key),
            "total": len(items),
        }

    @staticmethod
    def _temporal_queue_zero(initial: Sequence[int], final: Sequence[int]) -> bool:
        return (
            tuple(initial) == tuple(final)
            and len(initial) == 5
            and all(value == 0 for value in final)
        )

    @staticmethod
    def _temporal_jobs_zero(initial: Mapping[str, Any], final: Mapping[str, Any]) -> bool:
        return bool(
            dict(initial) == dict(final)
            and initial.get("active_count") == 0
            and not initial.get("unproven")
        )

    @staticmethod
    def _temporal_execution_links_zero(
        initial: Mapping[str, Mapping[str, int]],
        final: Mapping[str, Mapping[str, int]],
    ) -> bool:
        return bool(
            dict(initial) == dict(final)
            and all(not any(counts.values()) for counts in final.values())
        )

    @staticmethod
    def _failed_process_chain(
        results: Sequence[Mapping[str, Any]],
        *,
        last_error: str | None = None,
        invariant_names: Sequence[str] = (),
    ) -> dict[str, Any]:
        residual_pids = sorted(
            {int(pid) for result in results for pid in result.get("residual_pids", ()) or ()}
        )
        manual = bool(residual_pids) or any(
            bool(result.get("manual_intervention_required")) for result in results
        )
        error = last_error or ";".join(
            str(result.get("last_error")) for result in results if result.get("last_error")
        )
        return {
            "passed": False,
            "retryable": False,
            "last_error": error or "process_chain_failed",
            "manual_intervention_required": manual,
            "residual_pids": residual_pids,
            "invariants": {str(name): False for name in invariant_names},
            "process_evidence": [
                result["process_evidence"]
                for result in results
                if result.get("process_evidence") is not None
            ],
            "completed_process_count": sum(bool(result.get("passed")) for result in results),
            "process_chain_stopped": True,
        }

    def _run(
        self,
        deadline: RestoreDeadline,
        command: Sequence[str],
        *,
        name: str,
    ) -> dict[str, Any]:
        try:
            deadline.assert_can_launch(self.launch_budget_seconds)
            outcome = self.runner.run(command, name=name, cwd=self.repository_root)
        except Exception as exc:
            typed = getattr(exc, "to_dict", None)
            evidence = typed() if callable(typed) else None
            if not isinstance(evidence, Mapping):
                evidence = {
                    "name": name,
                    "command": list(command),
                    "runner_exception": f"{type(exc).__name__}:{exc}",
                    "child_created": None,
                    "forced_termination_attempts": 0,
                }
            residual = tuple(sorted({int(pid) for pid in evidence.get("residual_pids", ()) or ()}))
            child_created = evidence.get("child_created")
            residual_status = (
                "present" if residual else "not_created" if child_created is False else "unknown"
            )
            merged = dict(evidence)
            merged.update({"safe_for_followup": False, "residual_status": residual_status})
            return {
                "passed": False,
                "last_error": f"{name}:process_containment_exception:{type(exc).__name__}:{exc}",
                "residual_pids": list(residual),
                "residual_status": residual_status,
                "residual_process_zero": residual_status == "not_created",
                "manual_intervention_required": True,
                "timeout_manual_latch": bool(evidence.get("timed_out", False)),
                "containment_manual_latch": True,
                "process_evidence": merged,
                "stdout": "",
                "stderr": "",
            }

        residual = tuple(int(pid) for pid in getattr(outcome, "residual_pids", ()))
        timed_out = bool(getattr(outcome, "timed_out", False))
        cancelled = bool(getattr(outcome, "cancelled", False))
        uncertain = bool(
            timed_out
            or cancelled
            or getattr(outcome, "manual_intervention_required", False)
            or residual
            or not getattr(outcome, "active_process_zero", True)
            or not getattr(outcome, "streams_drained", True)
            or not getattr(outcome, "identity_coverage_complete", True)
            or int(getattr(outcome, "forced_termination_attempts", 0)) != 0
        )
        return_code = getattr(outcome, "return_code", None)
        passed = return_code == 0 and not uncertain
        return {
            "passed": passed,
            "last_error": None
            if passed
            else (
                f"{name}:return_code={return_code}:timed_out={timed_out}:"
                f"cancelled={cancelled}:residual={list(residual)}:"
                f"{str(getattr(outcome, 'stderr', ''))[-1000:]}"
            ),
            "residual_pids": list(residual),
            "residual_status": "present" if residual else "zero",
            "residual_process_zero": not uncertain and not residual,
            "manual_intervention_required": uncertain,
            "timeout_manual_latch": timed_out or cancelled,
            "process_evidence": outcome.to_dict(),
            "stdout": str(getattr(outcome, "stdout", "")),
            "stderr": str(getattr(outcome, "stderr", "")),
        }

    def docker_engine(self, deadline: RestoreDeadline) -> dict[str, Any]:
        result = self._run(
            deadline,
            [self.docker, "version", "--format", "{{json .Server}}"],
            name="r7-docker-engine-readback",
        )
        server: Any = None
        if result["passed"]:
            try:
                server = json.loads(str(result["stdout"]))
            except json.JSONDecodeError:
                result["passed"] = False
                result["last_error"] = "docker_server_json_invalid"
        result["server"] = server
        result["invariants"] = {"docker_engine": bool(result["passed"])}
        return result

    def _http_json(
        self,
        deadline: RestoreDeadline,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        deadline.assert_can_launch(self.contract.kubectl_timeout_seconds)
        started = self.clock()
        try:
            response = requests.request(
                method,
                url,
                json=None if body is None else dict(body),
                timeout=self.contract.kubectl_timeout_seconds,
            )
            try:
                payload: Any = response.json()
            except ValueError:
                payload = None
            return {
                "url": url,
                "method": method,
                "status": response.status_code,
                "body": payload,
                "body_text": response.text,
                "duration_seconds": max(0.0, self.clock() - started),
                "error": None,
            }
        except requests.RequestException as exc:
            return {
                "url": url,
                "method": method,
                "status": None,
                "body": None,
                "body_text": "",
                "duration_seconds": max(0.0, self.clock() - started),
                "error": f"{type(exc).__name__}:{exc}",
            }

    def node_device_plugin_gpu(self, deadline: RestoreDeadline) -> dict[str, Any]:
        invariant_names = (
            "node_ready_1_of_1",
            "device_plugin_ready_1_of_1",
            "gpu_capacity_1",
            "gpu_allocatable_1",
        )
        node_result = self._run(
            deadline,
            self._kubectl_command("get", "nodes", "-o", "json"),
            name="r7-kubernetes-node-readback",
        )
        nodes, node_error = self._json_object(node_result, "nodes")
        if node_error:
            return self._failed_process_chain(
                [node_result], last_error=node_error, invariant_names=invariant_names
            )
        assert nodes is not None
        items = list(nodes.get("items", []))
        node = items[0] if len(items) == 1 else {}
        conditions = {
            str(item.get("type")): str(item.get("status"))
            for item in node.get("status", {}).get("conditions", [])
        }
        capacity = str(node.get("status", {}).get("capacity", {}).get("nvidia.com/gpu", "0"))
        allocatable = str(node.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu", "0"))
        node_invariants = {
            "node_ready_1_of_1": len(items) == 1 and conditions.get("Ready") == "True",
            "gpu_capacity_1": capacity == "1",
            "gpu_allocatable_1": allocatable == "1",
        }
        if not all(node_invariants.values()):
            return {
                "passed": False,
                "last_error": f"node_gpu_invariant:{node_invariants}",
                "manual_intervention_required": False,
                "residual_pids": list(node_result["residual_pids"]),
                "invariants": {**node_invariants, "device_plugin_ready_1_of_1": False},
                "node": node,
                "process_evidence": [node_result["process_evidence"]],
                "process_chain_stopped": True,
            }
        plugin_result = self._run(
            deadline,
            self._kubectl_command(
                "-n",
                "kube-system",
                "get",
                "daemonset/nvidia-device-plugin-daemonset",
                "-o",
                "json",
            ),
            name="r7-device-plugin-readback",
        )
        plugin, plugin_error = self._json_object(plugin_result, "device_plugin")
        if plugin_error:
            return self._failed_process_chain(
                [node_result, plugin_result],
                last_error=plugin_error,
                invariant_names=invariant_names,
            )
        assert plugin is not None
        plugin_status = plugin.get("status", {})
        invariants = {
            **node_invariants,
            "device_plugin_ready_1_of_1": int(plugin_status.get("desiredNumberScheduled", 0)) == 1
            and int(plugin_status.get("numberReady", 0)) == 1,
        }
        residual = sorted(set(node_result["residual_pids"]) | set(plugin_result["residual_pids"]))
        passed = all(invariants.values()) and not residual
        return {
            "passed": passed,
            "last_error": None if passed else f"node_device_gpu_invariant:{invariants}",
            "manual_intervention_required": bool(residual),
            "invariants": invariants,
            "node": node,
            "device_plugin": plugin,
            "process_evidence": [
                node_result["process_evidence"],
                plugin_result["process_evidence"],
            ],
            "residual_pids": residual,
        }

    def b0_identity_cuda(self, deadline: RestoreDeadline) -> dict[str, Any]:
        invariant_names = (
            "b0_exact_uid",
            "b0_exact_image",
            "b0_replica_1_of_1",
            "b0_actual_cuda",
        )
        deployment_result = self._run(
            deadline,
            self._kubectl_command(
                "-n",
                "evm-production",
                "get",
                "deployment/evm-b0-production",
                "-o",
                "json",
            ),
            name="r7-b0-deployment-readback",
        )
        deployment, deployment_error = self._json_object(deployment_result, "b0_deployment")
        if deployment_error:
            return self._failed_process_chain(
                [deployment_result],
                last_error=deployment_error,
                invariant_names=invariant_names,
            )
        assert deployment is not None
        expected_b0 = _mapping(self.expected.get("b0"), "expected_b0")
        metadata = deployment.get("metadata", {})
        spec = deployment.get("spec", {})
        status = deployment.get("status", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        image = str(containers[0].get("image", "")) if len(containers) == 1 else ""
        identity_invariants = {
            "b0_exact_uid": str(metadata.get("uid", "")) == str(expected_b0["uid"]),
            "b0_exact_image": image == str(expected_b0["image"]),
            "b0_replica_1_of_1": int(spec.get("replicas", 0)) == 1
            and int(status.get("readyReplicas", 0)) == 1
            and int(status.get("availableReplicas", 0)) == 1,
        }
        if not all(identity_invariants.values()):
            invariants = {**identity_invariants, "b0_actual_cuda": False}
            return {
                "passed": False,
                "last_error": f"b0_identity_invariant:{invariants}",
                "manual_intervention_required": False,
                "invariants": invariants,
                "deployment": deployment,
                "process_evidence": [deployment_result["process_evidence"]],
                "residual_pids": list(deployment_result["residual_pids"]),
                "process_chain_stopped": True,
            }
        ready = self._http_json(deadline, "GET", str(expected_b0["ready_url"]))
        ready_body = ready["body"] if isinstance(ready["body"], Mapping) else {}
        ready_cuda = (
            ready["status"] == 200
            and ready_body.get("status") == "ok"
            and ready_body.get("device") == "cuda"
        )
        if not ready_cuda:
            invariants = {**identity_invariants, "b0_actual_cuda": False}
            return {
                "passed": False,
                "last_error": f"b0_ready_cuda_invariant:{ready}",
                "manual_intervention_required": False,
                "invariants": invariants,
                "deployment": deployment,
                "ready": ready,
                "process_evidence": [deployment_result["process_evidence"]],
                "residual_pids": list(deployment_result["residual_pids"]),
                "process_chain_stopped": True,
            }
        prediction = self._http_json(
            deadline,
            "POST",
            str(expected_b0["predict_url"]),
            body={"image_uri": str(expected_b0["sample_image_uri"])},
        )
        prediction_body = prediction["body"] if isinstance(prediction["body"], Mapping) else {}
        invariants = {
            **identity_invariants,
            "b0_actual_cuda": prediction["status"] == 200
            and prediction_body.get("device") == "cuda"
            and bool(prediction_body.get("prediction")),
        }
        passed = all(invariants.values())
        return {
            "passed": passed,
            "last_error": None if passed else f"b0_invariant:{invariants}",
            "manual_intervention_required": False,
            "invariants": invariants,
            "deployment": deployment,
            "ready": ready,
            "prediction": prediction,
            "process_evidence": [deployment_result["process_evidence"]],
            "residual_pids": list(deployment_result["residual_pids"]),
        }

    def prometheus(self, deadline: RestoreDeadline) -> dict[str, Any]:
        url = str(self.expected["prometheus_targets_url"])
        readback = self._http_json(deadline, "GET", url)
        body = readback["body"] if isinstance(readback["body"], Mapping) else {}
        targets = body.get("data", {}).get("activeTargets", [])
        jobs = sorted(str(item.get("labels", {}).get("job")) for item in targets)
        up = sum(str(item.get("health")) == "up" for item in targets)
        expected_jobs = {str(item) for item in self.expected["prometheus_jobs"]}
        passed = (
            expected_jobs == EXPECTED_PROMETHEUS_JOBS
            and readback["status"] == 200
            and len(targets) == 5
            and up == 5
            and set(jobs) == expected_jobs
        )
        return {
            "passed": passed,
            "last_error": None if passed else f"prometheus_not_exact_5_of_5:{jobs}:{up}",
            "manual_intervention_required": False,
            "invariants": {"prometheus_5_of_5": passed},
            "readback": readback,
            "jobs": jobs,
            "total": len(targets),
            "up": up,
            "residual_pids": [],
        }

    @staticmethod
    def _rows(text: str) -> list[dict[str, Any]]:
        stripped = text.strip()
        if not stripped:
            return []
        try:
            value = json.loads(stripped)
            rows = value if isinstance(value, list) else [value]
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in stripped.splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("compose_rows_objects_required")
        return rows

    def _compose_command(self, *arguments: str) -> list[str]:
        compose = _mapping(self.expected.get("compose"), "expected_compose")
        return [
            self.docker,
            "compose",
            "-p",
            str(compose["project_name"]),
            "-f",
            str(compose["config_path"]),
            *arguments,
        ]

    def _compose_ps(
        self, deadline: RestoreDeadline, *, name: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        result = self._run(
            deadline,
            self._compose_command("ps", "-a", "--format", "json"),
            name=name,
        )
        if not result["passed"]:
            return result, []
        try:
            return result, self._rows(str(result["stdout"]))
        except (ValueError, json.JSONDecodeError) as exc:
            result["passed"] = False
            result["last_error"] = f"compose_ps_json_invalid:{exc}"
            return result, []

    def _container_snapshot(
        self,
        deadline: RestoreDeadline,
        container_ids: Sequence[str],
        *,
        name: str,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        template = (
            "{{.Id}}\t{{.Image}}\t{{.Name}}\t{{.State.Status}}\t"
            "{{.State.Running}}\t{{.State.Restarting}}\t{{.RestartCount}}\t"
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\t"
            "{{.State.OOMKilled}}"
        )
        result = self._run(
            deadline,
            [self.docker, "inspect", "--format", template, *container_ids],
            name=name,
        )
        snapshots: dict[str, dict[str, Any]] = {}
        if not result["passed"]:
            return result, snapshots
        try:
            for line in str(result["stdout"]).splitlines():
                fields = line.split("\t")
                if len(fields) != 9:
                    raise ValueError("container_snapshot_field_count")
                container_id, image_id, container_name = fields[:3]
                snapshots[container_name.lstrip("/")] = {
                    "container_id": container_id,
                    "image_id": image_id,
                    "container_name": container_name.lstrip("/"),
                    "status": fields[3],
                    "running": fields[4].lower() == "true",
                    "restarting": fields[5].lower() == "true",
                    "restart_count": int(fields[6]),
                    "health": fields[7],
                    "oom_killed": fields[8].lower() == "true",
                }
        except (ValueError, IndexError) as exc:
            result["passed"] = False
            result["last_error"] = f"container_snapshot_invalid:{exc}"
            snapshots = {}
        return result, snapshots

    @staticmethod
    def _service_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        return {str(row.get("Service", "")): row for row in rows if row.get("Service")}

    def _database_readback(self, deadline: RestoreDeadline) -> dict[str, Any]:
        database = _mapping(self.expected.get("database"), "expected_database")
        instances = _mapping(database.get("instances"), "database_instances")
        process_results: list[Mapping[str, Any]] = []
        observed: dict[str, Any] = {}
        connected = True
        not_recovery = True
        for role in ("control_plane", "mlflow", "airflow"):
            spec = _mapping(instances.get(role), f"database_instance_{role}")
            query = "SELECT current_database(),pg_is_in_recovery();"
            result = self._run(
                deadline,
                [
                    self.docker,
                    "exec",
                    str(spec["container_name"]),
                    "psql",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    str(spec["user"]),
                    "-d",
                    str(spec["database"]),
                    "-At",
                    "-F",
                    "|",
                    "-c",
                    query,
                ],
                name=f"r7-database-{role}-readback",
            )
            process_results.append(result)
            parts = str(result.get("stdout", "")).strip().split("|")
            role_connected = bool(
                result.get("passed") and len(parts) == 2 and parts[0] == str(spec["database"])
            )
            role_not_recovery = role_connected and parts[1].lower() in {"f", "false"}
            connected = connected and role_connected
            not_recovery = not_recovery and role_not_recovery
            observed[role] = {
                "connected": role_connected,
                "not_in_recovery": role_not_recovery,
                "database": parts[0] if parts else None,
            }
            if result.get("manual_intervention_required"):
                return {
                    "passed": False,
                    "manual_intervention_required": True,
                    "last_error": result.get("last_error"),
                    "invariants": {
                        "postgres_3_of_3_connected": False,
                        "postgres_3_of_3_not_in_recovery": False,
                        "control_plane_migrations_exact": False,
                        "mlflow_migration_head_exact": False,
                        "airflow_migration_head_exact": False,
                    },
                    "process_evidence": [item["process_evidence"] for item in process_results],
                    "residual_pids": sorted(
                        {int(pid) for item in process_results for pid in item["residual_pids"]}
                    ),
                    "databases": observed,
                }

        migration_specs = (
            (
                "control_plane",
                "SELECT version FROM evm_control_plane.schema_migrations ORDER BY version;",
                tuple(str(item) for item in database["control_plane_schema_versions"]),
            ),
            (
                "mlflow",
                "SELECT version_num FROM alembic_version ORDER BY version_num;",
                (str(database["mlflow_migration_head"]),),
            ),
            (
                "airflow",
                "SELECT version_num FROM alembic_version ORDER BY version_num;",
                (str(database["airflow_migration_head"]),),
            ),
        )
        migration_invariants: dict[str, bool] = {}
        for role, query, expected_versions in migration_specs:
            spec = _mapping(instances[role], f"database_instance_{role}")
            result = self._run(
                deadline,
                [
                    self.docker,
                    "exec",
                    str(spec["container_name"]),
                    "psql",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    str(spec["user"]),
                    "-d",
                    str(spec["database"]),
                    "-At",
                    "-c",
                    query,
                ],
                name=f"r7-database-{role}-migration-readback",
            )
            process_results.append(result)
            if result.get("manual_intervention_required") or result.get("residual_pids"):
                return {
                    "passed": False,
                    "manual_intervention_required": True,
                    "last_error": result.get("last_error")
                    or f"r7-database-{role}-migration-process_uncertain",
                    "invariants": {
                        "postgres_3_of_3_connected": connected,
                        "postgres_3_of_3_not_in_recovery": not_recovery,
                        "control_plane_migrations_exact": False,
                        "mlflow_migration_head_exact": False,
                        "airflow_migration_head_exact": False,
                    },
                    "process_evidence": [item["process_evidence"] for item in process_results],
                    "residual_pids": sorted(
                        {
                            int(pid)
                            for item in process_results
                            for pid in item.get("residual_pids", ())
                        }
                    ),
                    "databases": observed,
                }
            versions = tuple(
                line.strip() for line in str(result.get("stdout", "")).splitlines() if line.strip()
            )
            invariant_name = {
                "control_plane": "control_plane_migrations_exact",
                "mlflow": "mlflow_migration_head_exact",
                "airflow": "airflow_migration_head_exact",
            }[role]
            migration_invariants[invariant_name] = bool(
                result.get("passed") and versions == expected_versions
            )
            observed[role]["migration_versions"] = list(versions)
            observed[role]["expected_migration_versions"] = list(expected_versions)

        invariants = {
            "postgres_3_of_3_connected": connected,
            "postgres_3_of_3_not_in_recovery": not_recovery,
            **migration_invariants,
        }
        residual = sorted(
            {int(pid) for item in process_results for pid in item.get("residual_pids", ())}
        )
        manual = any(item.get("manual_intervention_required") for item in process_results)
        return {
            "passed": all(invariants.values()) and not residual and not manual,
            "manual_intervention_required": manual or bool(residual),
            "last_error": None
            if all(invariants.values()) and not residual and not manual
            else f"database_runtime_gate_failed:{invariants}",
            "invariants": invariants,
            "process_evidence": [item["process_evidence"] for item in process_results],
            "residual_pids": residual,
            "databases": observed,
        }

    def compose(self, deadline: RestoreDeadline) -> dict[str, Any]:
        compose = _mapping(self.expected.get("compose"), "expected_compose")
        long_lived = tuple(
            str(item) for item in _sequence(compose["long_lived_services"], "long_lived")
        )
        one_shots = tuple(
            str(item) for item in _sequence(compose["one_shot_services"], "one_shots")
        )
        pins = _mapping(compose.get("service_pins"), "compose_service_pins")
        stability = _mapping(compose.get("stability"), "compose_stability")
        process_results: list[Mapping[str, Any]] = []
        compound_invariants = (
            "compose_healthy",
            "compose_exact_13_running",
            "compose_healthchecks_healthy",
            "compose_container_identity_stable",
            "compose_restart_delta_zero",
            "compose_stability_duration_met",
            "compose_one_shots_classified",
            "postgres_3_of_3_connected",
            "postgres_3_of_3_not_in_recovery",
            "control_plane_migrations_exact",
            "mlflow_migration_head_exact",
            "airflow_migration_head_exact",
        )

        config_path = Path(str(compose["config_path"])).resolve()
        canonical_config = (self.repository_root / "docker-compose.yml").resolve()
        try:
            config_exact = bool(
                _resolved_equal(config_path, canonical_config)
                and sha256_file(config_path) == str(compose["config_sha256"]).lower()
                and str(compose["project_name"]) == "enterprise-vision-mlops"
            )
        except OSError:
            config_exact = False

        ps_result, rows = self._compose_ps(deadline, name="r7-compose-ps-initial")
        process_results.append(ps_result)
        if ps_result.get("manual_intervention_required") or ps_result.get("residual_pids"):
            return self._failed_process_chain(process_results, invariant_names=compound_invariants)
        by_service = self._service_map(rows)
        exact_service_set = set(by_service) == set(long_lived) | set(one_shots)
        long_rows = {name: by_service.get(name, {}) for name in long_lived}
        exact_running = bool(
            config_exact
            and ps_result.get("passed")
            and exact_service_set
            and all(str(row.get("State", "")).lower() == "running" for row in long_rows.values())
        )
        health_ok = bool(
            exact_running
            and all(
                str(long_rows[name].get("Health", "")).lower()
                == (
                    "healthy"
                    if bool(_mapping(pins[name], f"pin_{name}")["healthcheck_expected"])
                    else ""
                )
                for name in long_lived
            )
        )
        one_shots_ok = bool(
            exact_service_set
            and all(
                str(by_service[name].get("State", "")).lower() == "exited"
                and int(by_service[name].get("ExitCode", -1)) == 0
                for name in one_shots
            )
        )
        container_ids = [
            str(_mapping(pins[name], f"pin_{name}")["container_id"]) for name in long_lived
        ]
        snapshot_result, first_snapshot = self._container_snapshot(
            deadline, container_ids, name="r7-compose-stability-000"
        )
        process_results.append(snapshot_result)
        if snapshot_result.get("manual_intervention_required") or snapshot_result.get(
            "residual_pids"
        ):
            return self._failed_process_chain(process_results, invariant_names=compound_invariants)
        expected_by_container = {
            str(_mapping(pins[name], f"pin_{name}")["container_name"]): _mapping(
                pins[name], f"pin_{name}"
            )
            for name in long_lived
        }
        identity_ok = bool(
            snapshot_result.get("passed")
            and set(first_snapshot) == set(expected_by_container)
            and all(
                first_snapshot[name]["container_id"] == str(spec["container_id"])
                and first_snapshot[name]["image_id"] == str(spec["image_id"])
                and first_snapshot[name]["running"]
                and not first_snapshot[name]["restarting"]
                and first_snapshot[name]["status"] == "running"
                and not first_snapshot[name]["oom_killed"]
                and first_snapshot[name]["health"]
                == ("healthy" if bool(spec["healthcheck_expected"]) else "none")
                for name, spec in expected_by_container.items()
            )
        )
        initial_restarts = {
            name: int(first_snapshot.get(name, {}).get("restart_count", -1))
            for name in expected_by_container
        }
        latest_snapshot = first_snapshot
        every_snapshot_valid = identity_ok
        samples = int(stability["samples"])
        interval = float(stability["interval_seconds"])
        duration_required = float(stability["duration_seconds"])
        origin = self.clock()
        observed_samples = 1 if first_snapshot else 0
        for index in range(1, samples):
            target = origin + index * interval
            delay = target - self.clock()
            if delay > 0:
                self.sleep(delay)
            sample_result, latest_snapshot = self._container_snapshot(
                deadline,
                container_ids,
                name=f"r7-compose-stability-{index:03d}",
            )
            process_results.append(sample_result)
            if not sample_result.get("passed"):
                break
            observed_samples += 1
            every_snapshot_valid = every_snapshot_valid and bool(
                set(latest_snapshot) == set(first_snapshot)
                and all(
                    latest_snapshot[name]["container_id"] == first_snapshot[name]["container_id"]
                    and latest_snapshot[name]["image_id"] == first_snapshot[name]["image_id"]
                    and latest_snapshot[name]["running"]
                    and not latest_snapshot[name]["restarting"]
                    and latest_snapshot[name]["status"] == "running"
                    and not latest_snapshot[name]["oom_killed"]
                    and latest_snapshot[name]["health"]
                    == (
                        "healthy"
                        if bool(expected_by_container[name]["healthcheck_expected"])
                        else "none"
                    )
                    for name in first_snapshot
                )
            )
        if any(
            result.get("manual_intervention_required") or result.get("residual_pids")
            for result in process_results
        ):
            return self._failed_process_chain(process_results, invariant_names=compound_invariants)
        elapsed = max(0.0, self.clock() - origin)
        restart_delta = {
            name: int(latest_snapshot.get(name, {}).get("restart_count", -1)) - initial
            for name, initial in initial_restarts.items()
        }
        stable_identity = bool(
            every_snapshot_valid
            and set(latest_snapshot) == set(first_snapshot)
            and all(
                latest_snapshot[name]["container_id"] == first_snapshot[name]["container_id"]
                and latest_snapshot[name]["image_id"] == first_snapshot[name]["image_id"]
                and latest_snapshot[name]["running"]
                and not latest_snapshot[name]["restarting"]
                and latest_snapshot[name]["status"] == "running"
                and not latest_snapshot[name]["oom_killed"]
                for name in first_snapshot
            )
        )
        restart_ok = bool(
            observed_samples == samples
            and all(
                delta == int(stability["restart_delta"]) == 0 for delta in restart_delta.values()
            )
        )
        duration_ok = observed_samples == samples and elapsed >= duration_required

        database = self._database_readback(deadline)
        invariants = {
            "compose_healthy": exact_running and health_ok,
            "compose_exact_13_running": exact_running and len(long_lived) == 13,
            "compose_healthchecks_healthy": health_ok,
            "compose_container_identity_stable": stable_identity,
            "compose_restart_delta_zero": restart_ok,
            "compose_stability_duration_met": duration_ok,
            "compose_one_shots_classified": one_shots_ok and len(one_shots) == 2,
            **dict(database["invariants"]),
        }
        residual = sorted(
            {int(pid) for item in process_results for pid in item.get("residual_pids", ())}
            | {int(pid) for pid in database.get("residual_pids", ())}
        )
        manual = bool(
            database.get("manual_intervention_required")
            or residual
            or any(item.get("manual_intervention_required") for item in process_results)
        )
        passed = all(invariants.values()) and not residual and not manual
        return {
            "passed": passed,
            "last_error": None if passed else f"compose_or_database_gate_failed:{invariants}",
            "manual_intervention_required": manual,
            "invariants": invariants,
            "services": rows,
            "initial_snapshot": first_snapshot,
            "final_snapshot": latest_snapshot,
            "restart_delta": restart_delta,
            "stability": {
                "required_samples": samples,
                "observed_samples": observed_samples,
                "required_duration_seconds": duration_required,
                "observed_duration_seconds": elapsed,
            },
            "database": database.get("databases", {}),
            "compose_config_exact": config_exact,
            "process_evidence": [
                item["process_evidence"] for item in process_results if "process_evidence" in item
            ]
            + list(database.get("process_evidence", [])),
            "residual_pids": residual,
        }

    def kubernetes_api(self, deadline: RestoreDeadline) -> dict[str, Any]:
        kubernetes = _mapping(self.expected.get("kubernetes"), "expected_kubernetes")
        confirmations = int(kubernetes.get("health_confirmation_samples", 0))
        if confirmations != 2:
            return {
                "passed": False,
                "retryable": False,
                "last_error": "kubernetes_health_confirmation_contract_mismatch",
                "manual_intervention_required": True,
                "residual_pids": [],
                "invariants": {"kubernetes_livez": False, "kubernetes_readyz": False},
                "process_evidence": [],
            }
        results: list[Mapping[str, Any]] = []
        observations: list[dict[str, Any]] = []
        live_ok = True
        ready_ok = True
        for index in range(confirmations):
            live = self._run(
                deadline,
                self._kubectl_command("get", "--raw=/livez"),
                name=f"r7-kubernetes-livez-confirmation-{index + 1}",
            )
            results.append(live)
            live_passed = bool(
                live.get("passed") and str(live.get("stdout", "")).strip().lower() == "ok"
            )
            live_ok = live_ok and live_passed
            if not live_passed:
                failure = self._failed_process_chain(
                    results,
                    last_error=live.get("last_error") or "kubernetes_livez_not_ok",
                    invariant_names=("kubernetes_livez", "kubernetes_readyz"),
                )
                failure["retryable"] = False
                failure["observations"] = observations
                return failure
            ready = self._run(
                deadline,
                self._kubectl_command("get", "--raw=/readyz"),
                name=f"r7-kubernetes-readyz-confirmation-{index + 1}",
            )
            results.append(ready)
            ready_passed = bool(
                ready.get("passed") and str(ready.get("stdout", "")).strip().lower() == "ok"
            )
            ready_ok = ready_ok and ready_passed
            observations.append(
                {
                    "confirmation": index + 1,
                    "livez": str(live.get("stdout", "")).strip(),
                    "readyz": str(ready.get("stdout", "")).strip(),
                }
            )
            if not ready_passed:
                failure = self._failed_process_chain(
                    results,
                    last_error=ready.get("last_error") or "kubernetes_readyz_not_ok",
                    invariant_names=("kubernetes_livez", "kubernetes_readyz"),
                )
                failure["retryable"] = False
                failure["invariants"] = {
                    "kubernetes_livez": live_ok,
                    "kubernetes_readyz": False,
                }
                failure["observations"] = observations
                return failure
        residual = sorted(
            {int(pid) for result in results for pid in result.get("residual_pids", ())}
        )
        passed = live_ok and ready_ok and not residual
        return {
            "passed": passed,
            "retryable": False,
            "last_error": None if passed else "kubernetes_health_confirmation_failed",
            "manual_intervention_required": bool(residual),
            "residual_pids": residual,
            "invariants": {
                "kubernetes_livez": live_ok,
                "kubernetes_readyz": ready_ok,
            },
            "observations": observations,
            "process_evidence": [result["process_evidence"] for result in results],
            "confirmation_launches": {"livez": confirmations, "readyz": confirmations},
            "automatic_retries": 0,
        }

    @staticmethod
    def _contains_scalar(value: Any, expected: str) -> bool:
        if isinstance(value, str):
            return value == expected
        if isinstance(value, Mapping):
            return any(R7ProbeSet._contains_scalar(item, expected) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(R7ProbeSet._contains_scalar(item, expected) for item in value)
        return False

    def api_release_identity(self, deadline: RestoreDeadline) -> dict[str, Any]:
        api = _mapping(self.expected.get("api"), "expected_api")
        base_url = str(api["base_url"])
        health = self._http_json(deadline, "GET", f"{base_url}/health")
        ready = self._http_json(deadline, "GET", f"{base_url}/ready")
        ready_body = ready["body"] if isinstance(ready["body"], Mapping) else {}

        process_results: list[Mapping[str, Any]] = []
        container_names = (
            str(api["api_container_name"]),
            str(api["worker_container_name"]),
        )
        container_result = self._run(
            deadline,
            [
                self.docker,
                "inspect",
                "--format",
                "{{.Id}}\t{{.Image}}\t{{.Name}}",
                *container_names,
            ],
            name="r7-api-worker-container-identity",
        )
        process_results.append(container_result)
        if container_result.get("manual_intervention_required") or container_result.get(
            "residual_pids"
        ):
            failure = self._failed_process_chain(
                process_results,
                invariant_names=(
                    "api_health_200",
                    "api_ready_200",
                    "api_revision_exact",
                    "api_runtime_revision_matches",
                    "api_container_image_exact",
                    "worker_container_image_exact",
                    "api_image_revision_exact",
                    "api_image_attestation_exact",
                ),
            )
            failure.update({"health": health, "ready": ready})
            return failure
        observed_containers: dict[str, dict[str, str]] = {}
        if container_result.get("passed"):
            try:
                for line in str(container_result.get("stdout", "")).splitlines():
                    fields = line.split("\t")
                    if len(fields) != 3:
                        raise ValueError("container_identity_field_count")
                    observed_containers[fields[2].lstrip("/")] = {
                        "container_id": fields[0],
                        "image_id": fields[1],
                    }
            except ValueError as exc:
                container_result["passed"] = False
                container_result["last_error"] = f"api_container_identity_invalid:{exc}"

        image_result = self._run(
            deadline,
            [
                self.docker,
                "image",
                "inspect",
                "--format",
                '{{.Id}}\t{{index .Config.Labels "org.opencontainers.image.revision"}}',
                str(api["image_id"]),
            ],
            name="r7-api-image-provenance",
        )
        process_results.append(image_result)
        if image_result.get("manual_intervention_required") or image_result.get("residual_pids"):
            failure = self._failed_process_chain(
                process_results,
                invariant_names=(
                    "api_health_200",
                    "api_ready_200",
                    "api_revision_exact",
                    "api_runtime_revision_matches",
                    "api_container_image_exact",
                    "worker_container_image_exact",
                    "api_image_revision_exact",
                    "api_image_attestation_exact",
                ),
            )
            failure.update({"health": health, "ready": ready})
            return failure
        image_fields = str(image_result.get("stdout", "")).strip().split("\t")
        observed_image_id = image_fields[0] if len(image_fields) == 2 else ""
        observed_revision = image_fields[1] if len(image_fields) == 2 else ""

        attestation_pin = _mapping(api.get("image_attestation"), "api_image_attestation")
        attestation_path = Path(str(attestation_pin["path"])).resolve()
        attestation_sha = str(attestation_pin["sha256"]).lower()
        try:
            attestation, measured_attestation_sha = _read_json_snapshot(
                attestation_path, "api_image_attestation"
            )
            attestation_exact = bool(
                measured_attestation_sha == attestation_sha
                and self._contains_scalar(attestation, str(api["image_id"]))
                and self._contains_scalar(attestation, self.expected_revision)
                and self._contains_scalar(attestation, str(api["source_tree"]))
            )
        except Exception as exc:
            attestation = {"read_error": f"{type(exc).__name__}:{exc}"}
            measured_attestation_sha = None
            attestation_exact = False

        expected_image = str(api["image_id"])
        api_observed = observed_containers.get(str(api["api_container_name"]), {})
        worker_observed = observed_containers.get(str(api["worker_container_name"]), {})
        invariants = {
            "api_health_200": health["status"] == 200,
            "api_ready_200": ready["status"] == 200,
            "api_revision_exact": str(ready_body.get("runtime_source_commit", ""))
            == self.expected_revision,
            "api_runtime_revision_matches": ready_body.get("runtime_revision_matches") is True,
            "api_container_image_exact": bool(
                container_result.get("passed") and api_observed.get("image_id") == expected_image
            ),
            "worker_container_image_exact": bool(
                container_result.get("passed") and worker_observed.get("image_id") == expected_image
            ),
            "api_image_revision_exact": bool(
                image_result.get("passed")
                and observed_image_id == expected_image
                and observed_revision == self.expected_revision
                and str(api["source_revision"]) == self.expected_revision
                and str(api["source_tree"]) == str(self.manifest.get("canonical_tree", "")).lower()
            ),
            "api_image_attestation_exact": attestation_exact,
        }
        residual = sorted(
            {int(pid) for result in process_results for pid in result.get("residual_pids", ())}
        )
        manual = bool(
            residual
            or any(result.get("manual_intervention_required") for result in process_results)
        )
        passed = all(invariants.values()) and not manual
        return {
            "passed": passed,
            "retryable": False,
            "last_error": None if passed else f"api_release_attestation_gate:{invariants}",
            "manual_intervention_required": manual,
            "residual_pids": residual,
            "invariants": invariants,
            "health": health,
            "ready": ready,
            "containers": observed_containers,
            "image": {
                "observed_image_id": observed_image_id,
                "observed_revision": observed_revision,
            },
            "attestation": {
                "path": str(attestation_path),
                "expected_sha256": attestation_sha,
                "measured_sha256": measured_attestation_sha,
                "payload": attestation,
            },
            "process_evidence": [result["process_evidence"] for result in process_results],
        }

    def _failed_pod_identity(self, item: Mapping[str, Any]) -> dict[str, str]:
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        owners = metadata.get("ownerReferences", [])
        owner_uid = str(owners[0].get("uid", "")) if len(owners) == 1 else ""
        return {
            "uid": str(metadata.get("uid", "")),
            "name": str(metadata.get("name", "")),
            "namespace": str(metadata.get("namespace", "")),
            "reason": str(status.get("reason", "")),
            "owner_uid": owner_uid,
        }

    @staticmethod
    def _historical_classification_exact(
        classification: Mapping[str, Any],
        *,
        observed_count: int,
        attestation_exact: bool,
    ) -> bool:
        return bool(
            attestation_exact
            and int(classification.get("observed_count", -1)) == observed_count
            and int(classification.get("executing_count", -1)) == 0
            and int(classification.get("historical_count", -1)) == observed_count
            and int(classification.get("unproven_count", -1)) == 0
            and classification.get("classification") == "historical_nonexecuting"
        )

    @staticmethod
    def _parse_utc(value: Any) -> datetime | None:
        text = str(value)
        if not text.endswith("Z"):
            return None
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00")
        except ValueError:
            return None
        return parsed.astimezone(UTC)

    def _parent_readback_timestamp(self) -> datetime | None:
        payload = self.parent_payloads.get("post_manual_on_readback")
        if not isinstance(payload, Mapping):
            return None
        for key in ("captured_at", "observed_at", "created_at", "sealed_at"):
            parsed = self._parse_utc(payload.get(key))
            if parsed is not None:
                return parsed
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            for key in ("captured_at", "observed_at", "created_at", "sealed_at"):
                parsed = self._parse_utc(metadata.get(key))
                if parsed is not None:
                    return parsed
        return None

    def _historical_attestation_exact(
        self,
        *,
        source: str,
        classification: Mapping[str, Any],
        payload: Mapping[str, Any],
        query: str,
        observed_records: Sequence[Mapping[str, Any]],
        file_sha_exact: bool,
        attestation_path: Path,
    ) -> bool:
        required_payload = {
            "source",
            "captured_at",
            "query_sha256",
            "counts",
            "classification",
            "records",
        }
        if set(payload) != required_payload or payload.get("source") != source:
            return False
        expected_query_sha = HISTORICAL_QUERY_SHA256[source]
        if query != HISTORICAL_QUERY_TEXTS[source]:
            return False
        if payload.get("query_sha256") != expected_query_sha or not file_sha_exact:
            return False
        captured = self._parse_utc(payload.get("captured_at"))
        parent_captured = self._parent_readback_timestamp()
        if captured is None or parent_captured is None or captured < parent_captured:
            return False
        counts = payload.get("counts")
        if not isinstance(counts, Mapping) or set(counts) != {
            "observed_count",
            "executing_count",
            "historical_count",
            "unproven_count",
        }:
            return False
        manifest_counts = {
            name: classification.get(name)
            for name in (
                "observed_count",
                "executing_count",
                "historical_count",
                "unproven_count",
            )
        }
        if dict(counts) != manifest_counts:
            return False
        raw_records = payload.get("records")
        if not isinstance(raw_records, list) or len(raw_records) != len(observed_records):
            return False
        observed_by_identity: dict[str, Mapping[str, Any]] = {}
        for observed in observed_records:
            identity = observed.get("identity")
            if not isinstance(identity, Mapping):
                return False
            identity_key = json.dumps(dict(identity), sort_keys=True, separators=(",", ":"))
            if identity_key in observed_by_identity:
                return False
            observed_by_identity[identity_key] = observed
        normalized: list[dict[str, Any]] = []
        for raw in raw_records:
            if not isinstance(raw, Mapping) or set(raw) != {
                "identity",
                "observed_state",
                "classification",
                "execution_proof",
            }:
                return False
            identity = raw.get("identity")
            proof = raw.get("execution_proof")
            if not isinstance(identity, Mapping) or not isinstance(proof, Mapping):
                return False
            identity_key = json.dumps(dict(identity), sort_keys=True, separators=(",", ":"))
            observed = observed_by_identity.get(identity_key)
            if observed is None or str(observed.get("observed_state", "")) != str(
                raw.get("observed_state", "")
            ):
                return False
            if set(proof) != {
                "inactivity_proven",
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
                "evidence",
            }:
                return False
            if not isinstance(proof.get("inactivity_proven"), bool):
                return False
            active_counts: list[int] = []
            for name in (
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
            ):
                value = proof.get(name)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return False
                active_counts.append(value)
            if source == "control_plane_task_entity_statuses":
                live_counts = observed.get("execution_counts")
                if not isinstance(live_counts, Mapping) or set(live_counts) != {
                    "active_job_count",
                    "active_claim_count",
                    "active_lease_count",
                    "outcome_unknown_count",
                }:
                    return False
                if any(
                    isinstance(live_counts.get(name), bool)
                    or not isinstance(live_counts.get(name), int)
                    or live_counts.get(name) != proof.get(name)
                    for name in (
                        "active_job_count",
                        "active_claim_count",
                        "active_lease_count",
                        "outcome_unknown_count",
                    )
                ):
                    return False
            evidence = proof.get("evidence")
            if not isinstance(evidence, Mapping) or set(evidence) != {"path", "sha256"}:
                return False
            proof_path = Path(str(evidence["path"])).resolve()
            proof_sha = str(evidence["sha256"]).lower()
            if (
                proof_path == attestation_path.resolve()
                or re.fullmatch(r"[0-9a-f]{64}", proof_sha) is None
            ):
                return False
            try:
                proof_payload, measured_proof_sha = _read_json_snapshot(
                    proof_path, f"{source}_proof"
                )
                if measured_proof_sha != proof_sha:
                    return False
            except Exception:
                return False
            required_proof_payload = {
                "source",
                "identity",
                "observed_state",
                "captured_at",
                "query_sha256",
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
                "inactivity_decision",
                "decision_authority",
            }
            if set(proof_payload) != required_proof_payload:
                return False
            proof_captured = self._parse_utc(proof_payload.get("captured_at"))
            if (
                proof_payload.get("source") != source
                or proof_payload.get("identity") != identity
                or proof_payload.get("observed_state") != raw.get("observed_state")
                or proof_payload.get("query_sha256") != expected_query_sha
                or proof_captured is None
                or parent_captured is None
                or proof_captured < parent_captured
                or proof_captured > captured
                or any(
                    proof_payload.get(name) != proof.get(name)
                    for name in (
                        "active_job_count",
                        "active_claim_count",
                        "active_lease_count",
                        "outcome_unknown_count",
                    )
                )
                or proof_payload.get("decision_authority") != HISTORICAL_DECISION_AUTHORITY
            ):
                return False
            record_classification = str(raw.get("classification", ""))
            observed_state = str(raw.get("observed_state", ""))
            expected_record_classification = (
                "executing"
                if any(active_counts)
                else "historical_nonexecuting"
                if proof["inactivity_proven"] is True
                else "unproven"
            )
            expected_decision = (
                "executing"
                if any(active_counts)
                else "proven_inactive"
                if proof["inactivity_proven"] is True
                else "unproven"
            )
            if any(active_counts) and proof["inactivity_proven"] is not False:
                return False
            if record_classification != expected_record_classification:
                return False
            if proof_payload.get("inactivity_decision") != expected_decision:
                return False
            normalized.append(
                {
                    "identity": dict(identity),
                    "observed_state": observed_state,
                    "classification": record_classification,
                }
            )
        expected_normalized = [
            {
                "identity": dict(_mapping(item.get("identity"), "observed_identity")),
                "observed_state": str(item.get("observed_state", "")),
                "classification": "historical_nonexecuting",
            }
            for item in observed_records
        ]

        def key(item: Mapping[str, Any]) -> str:
            return json.dumps(item, sort_keys=True, separators=(",", ":"))

        if sorted(normalized, key=key) != sorted(expected_normalized, key=key):
            return False
        derived_counts = {
            "observed_count": len(normalized),
            "executing_count": sum(item["classification"] == "executing" for item in normalized),
            "historical_count": sum(
                item["classification"] == "historical_nonexecuting" for item in normalized
            ),
            "unproven_count": sum(item["classification"] == "unproven" for item in normalized),
        }
        return bool(
            dict(counts) == derived_counts
            and payload.get("classification") == "historical_nonexecuting"
            and classification.get("classification") == "historical_nonexecuting"
            and derived_counts["executing_count"] == 0
            and derived_counts["unproven_count"] == 0
        )

    def _global_windows_residuals(
        self, deadline: RestoreDeadline
    ) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
        script = (
            "$ErrorActionPreference='Stop';"
            f"$excluded=@({os.getpid()});"
            "$names=@('python.exe','pythonw.exe','wsl.exe','bash.exe','sh.exe');"
            "$pattern='(?i)(phase[_-]b2|x1-clock-phase-b2|s8-v4-x1)';"
            "$sha=[Security.Cryptography.SHA256]::Create();"
            "$rows=@(Get-CimInstance Win32_Process | Where-Object {"
            "$names -contains $_.Name -and $_.ProcessId -notin $excluded -and "
            "$null -ne $_.CommandLine -and $_.CommandLine -match $pattern} | "
            "ForEach-Object {[ordered]@{pid=[int]$_.ProcessId;ppid=[int]$_.ParentProcessId;"
            "creation_time=[string]$_.CreationDate;name=[string]$_.Name;"
            "command_line_sha256=(-join @($sha.ComputeHash("
            "[Text.Encoding]::UTF8.GetBytes([string]$_.CommandLine)) | "
            "ForEach-Object {$_.ToString('x2')}))}});"
            "$sha.Dispose();ConvertTo-Json -Compress -Depth 5 -InputObject @($rows)"
        )
        result = self._run(
            deadline,
            [self.powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            name="r7-windows-global-residual-readback",
        )
        if not result.get("passed"):
            return result, []
        try:
            decoded = json.loads(str(result.get("stdout", "")).strip() or "[]")
            rows = decoded if isinstance(decoded, list) else [decoded]
            if not all(isinstance(row, dict) for row in rows):
                raise ValueError("windows_residual_rows_invalid")
        except (json.JSONDecodeError, ValueError) as exc:
            result["passed"] = False
            result["last_error"] = f"windows_residual_json_invalid:{exc}"
            rows = []
        return result, rows

    def _global_wsl_residuals(
        self, deadline: RestoreDeadline
    ) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
        source = r"""import hashlib,json,os,pathlib,re
pattern=re.compile(r'(phase[_-]b2|x1-clock-phase-b2|s8-v4-x1)',re.I)
excluded={os.getpid(),os.getppid()}
rows=[]
for entry in pathlib.Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    pid=int(entry.name)
    if pid in excluded:
        continue
    try:
        raw=(entry/'cmdline').read_bytes()
        if not raw:
            continue
        command=raw.replace(b'\0',b' ').decode('utf-8','replace').strip()
        if not pattern.search(command):
            continue
        stat=(entry/'stat').read_text()
        right=stat.rfind(')')
        fields=stat[right+1:].strip().split()
        rows.append({'pid':pid,'ppid':int(fields[1]),'pgrp':int(fields[2]),
                     'session':int(fields[3]),'start_time_ticks':int(fields[19]),
                     'command_line_sha256':hashlib.sha256(command.encode()).hexdigest()})
    except (FileNotFoundError,ProcessLookupError,PermissionError,ValueError,IndexError):
        continue
print(json.dumps(sorted(rows,key=lambda item:(item['pid'],item['start_time_ticks'])),
                 sort_keys=True,separators=(',',':')))
"""
        result = self._run(
            deadline,
            [self.wsl, "-d", "Ubuntu", "--exec", "python3", "-c", source],
            name="r7-wsl-global-residual-readback",
        )
        if not result.get("passed"):
            return result, []
        try:
            decoded = json.loads(str(result.get("stdout", "")).strip() or "[]")
            rows = decoded if isinstance(decoded, list) else [decoded]
            if not all(isinstance(row, dict) for row in rows):
                raise ValueError("wsl_residual_rows_invalid")
        except (json.JSONDecodeError, ValueError) as exc:
            result["passed"] = False
            result["last_error"] = f"wsl_residual_json_invalid:{exc}"
            rows = []
        return result, rows

    def queue_jobs_lease_residue(self, deadline: RestoreDeadline) -> dict[str, Any]:
        invariant_names = (
            "queue_active_zero",
            "queue_leased_zero",
            "queue_outcome_unknown_zero",
            "active_jobs_zero",
            "active_claims_zero",
            "gpu_lease_zero",
            "x1_residue_zero",
            "canonical_active_scope_exact",
            "historical_control_plane_tasks_classified",
            "historical_mlflow_running_classified",
            "historical_failed_pods_classified",
            "windows_global_residual_zero",
            "wsl_global_residual_zero",
        )
        results: list[Mapping[str, Any]] = []
        database = _mapping(self.expected.get("database"), "expected_database")
        instances = _mapping(database.get("instances"), "database_instances")
        control = _mapping(instances.get("control_plane"), "database_control_plane")
        mlflow = _mapping(instances.get("mlflow"), "database_mlflow")

        queue_sql = (
            "SELECT "
            "(SELECT count(*) FILTER (WHERE state IN "
            "('available','retry_wait','leased','runtime_pending','outcome_unknown')) "
            "FROM evm_control_plane.task_admission_queue),"
            "(SELECT count(*) FILTER (WHERE state='leased') "
            "FROM evm_control_plane.task_admission_queue),"
            "(SELECT count(*) FILTER (WHERE state='outcome_unknown') "
            "FROM evm_control_plane.task_admission_queue),"
            "(SELECT count(*) FROM evm_control_plane.lifecycle_claims "
            "WHERE released_at IS NULL AND expires_at > clock_timestamp()),"
            "(SELECT count(*) FROM evm_control_plane.task_admission_queue "
            "WHERE state NOT IN ('available','retry_wait','leased','runtime_pending',"
            "'outcome_unknown','completed','failed','dlq','expired','cancelled'));"
        )
        queue_result = self._run(
            deadline,
            [
                self.docker,
                "exec",
                str(control["container_name"]),
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(control["user"]),
                "-d",
                str(control["database"]),
                "-At",
                "-F",
                "|",
                "-c",
                queue_sql,
            ],
            name="r7-queue-claims-readback",
        )
        results.append(queue_result)
        if not queue_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        try:
            queue_values = self._parse_queue_readback(str(queue_result.get("stdout", "")))
        except ValueError as exc:
            return self._failed_process_chain(
                results,
                last_error=f"queue_readback_invalid:{exc}",
                invariant_names=invariant_names,
            )

        jobs_result = self._run(
            deadline,
            self._kubectl_command("get", "jobs", "-A", "-o", "json"),
            name="r7-active-jobs-readback",
        )
        results.append(jobs_result)
        jobs_payload, jobs_error = self._json_object(jobs_result, "active_jobs")
        if jobs_error:
            return self._failed_process_chain(
                results, last_error=jobs_error, invariant_names=invariant_names
            )
        assert jobs_payload is not None
        try:
            initial_job_snapshot = self._kubernetes_job_snapshot(jobs_payload.get("items"))
        except (TypeError, ValueError) as exc:
            return self._failed_process_chain(
                results,
                last_error=f"kubernetes_job_snapshot_invalid:{exc}",
                invariant_names=invariant_names,
            )

        try:
            active_job_roots = [Path(str(item)) for item in self.expected["active_job_roots"]]
            active_claim_roots = [Path(str(item)) for item in self.expected["active_claim_roots"]]
            invalid_marker_roots = [
                str(root)
                for root in (*active_job_roots, *active_claim_roots)
                if root.exists() and not root.is_dir()
            ]
            if invalid_marker_roots:
                raise OSError(f"marker_root_not_directory:{invalid_marker_roots}")
            file_active_jobs = sum(
                1
                for root in active_job_roots
                if root.is_dir()
                for item in root.iterdir()
                if item.is_file()
            )
            file_active_claims = sum(
                1
                for root in active_claim_roots
                if root.is_dir()
                for item in root.iterdir()
                if item.is_file()
            )
        except OSError as exc:
            return self._failed_process_chain(
                results,
                last_error=f"job_claim_marker_read_failed:{exc}",
                invariant_names=invariant_names,
            )
        lease_path = Path(str(self.expected["gpu_lease_path"]))
        residue_paths = [Path(str(item)) for item in self.expected["x1_residue_paths"]]
        present_residue_paths = [str(path) for path in residue_paths if path.exists()]

        container_result = self._run(
            deadline,
            [
                self.docker,
                "ps",
                "-a",
                "--filter",
                str(self.expected["x1_docker_name_filter"]),
                "--format",
                "{{json .}}",
            ],
            name="r7-x1-docker-residue-readback",
        )
        results.append(container_result)
        if not container_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        try:
            x1_containers = [
                json.loads(line)
                for line in str(container_result.get("stdout", "")).splitlines()
                if line.strip()
            ]
        except json.JSONDecodeError as exc:
            return self._failed_process_chain(
                results,
                last_error=f"x1_docker_residue_json_invalid:{exc}",
                invariant_names=invariant_names,
            )

        open_ports: list[int] = []
        for raw_port in self.expected["x1_ports"]:
            deadline.assert_can_launch(0.5)
            port = int(raw_port)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
                probe_socket.settimeout(0.5)
                if probe_socket.connect_ex(("127.0.0.1", port)) == 0:
                    open_ports.append(port)

        residue_queries: list[dict[str, Any]] = []
        for index, selector in enumerate(self.expected["x1_kubernetes_selectors"], start=1):
            residue_result = self._run(
                deadline,
                self._kubectl_command("get", "all", "-A", "-l", str(selector), "-o", "json"),
                name=f"r7-x1-kubernetes-residue-{index}",
            )
            results.append(residue_result)
            payload, payload_error = self._json_object(
                residue_result, f"x1_kubernetes_residue_{index}"
            )
            if payload_error:
                return self._failed_process_chain(
                    results, last_error=payload_error, invariant_names=invariant_names
                )
            assert payload is not None
            residue_queries.append(
                {"selector": str(selector), "count": len(payload.get("items", []))}
            )

        job_scope = _mapping(self.manifest.get("job_scope_contract"), "job_scope_contract")
        canonical_active = _mapping(job_scope.get("canonical_active_jobs"), "canonical_active_jobs")
        historical_observations = _mapping(
            job_scope.get("historical_observations"), "historical_observations"
        )
        canonical_scope_contract = bool(
            canonical_active.get("sources")
            == ["kubernetes_job_status_active", "manifest_active_job_file_markers"]
            and canonical_active.get("required_count") == 0
            and historical_observations.get("sources")
            == [
                "control_plane_task_entity_statuses",
                "mlflow_running_rows",
                "kubernetes_terminal_failed_objects",
            ]
            and historical_observations.get("separate_from_canonical_active_jobs") is True
            and historical_observations.get("unknown_or_unproven_blocks_restore") is True
            and historical_observations.get("deletion_required") is False
        )
        classifications = {
            str(_mapping(item, "historical_classification")["source"]): _mapping(
                item, "historical_classification"
            )
            for item in _sequence(
                job_scope.get("historical_classifications"),
                "historical_classifications",
            )
        }
        control_scope = classifications.get("control_plane_task_entity_statuses", {})
        mlflow_scope = classifications.get("mlflow_running_rows", {})
        k8s_scope = classifications.get("kubernetes_terminal_failed_objects", {})

        classification_attestations: dict[str, Any] = {}
        attestation_ok: dict[str, bool] = {}
        for source, classification in classifications.items():
            pin = _mapping(classification.get("attestation"), f"{source}_attestation")
            path = Path(str(pin["path"])).resolve()
            expected_sha = str(pin["sha256"]).lower()
            try:
                payload, actual_sha = _read_json_snapshot(path, f"{source}_attestation")
                exact = actual_sha == expected_sha
            except Exception as exc:
                payload = {"read_error": f"{type(exc).__name__}:{exc}"}
                actual_sha = None
                exact = False
            classification_attestations[source] = {
                "path": str(path),
                "expected_sha256": expected_sha,
                "measured_sha256": actual_sha,
                "payload": payload,
            }
            attestation_ok[source] = exact

        control_scope_result = self._run(
            deadline,
            [
                self.docker,
                "exec",
                str(control["container_name"]),
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(control["user"]),
                "-d",
                str(control["database"]),
                "-At",
                "-F",
                "|",
                "-c",
                CONTROL_PLANE_HISTORY_QUERY,
            ],
            name="r7-historical-control-plane-task-classification",
        )
        results.append(control_scope_result)
        if not control_scope_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        try:
            control_records: list[dict[str, Any]] = []
            control_counts = [0, 0, 0]
            state_index = {"queued": 0, "pending_confirmation": 1, "running": 2}
            for line in str(control_scope_result.get("stdout", "")).splitlines():
                if not line.strip():
                    continue
                fields = line.split("|")
                if len(fields) != 4 or fields[1] not in state_index:
                    raise ValueError("historical_control_plane_field_invalid")
                control_counts[state_index[fields[1]]] += 1
                control_records.append(
                    {
                        "identity": {
                            "entity_id": fields[0],
                            "created_at": fields[2],
                            "updated_at": fields[3],
                        },
                        "observed_state": fields[1],
                    }
                )
        except ValueError as exc:
            return self._failed_process_chain(
                results,
                last_error=f"historical_control_plane_invalid:{exc}",
                invariant_names=invariant_names,
            )
        control_link_result = self._run(
            deadline,
            [
                self.docker,
                "exec",
                str(control["container_name"]),
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(control["user"]),
                "-d",
                str(control["database"]),
                "-At",
                "-F",
                "|",
                "-c",
                CONTROL_PLANE_EXECUTION_LINK_QUERY,
            ],
            name="r7-historical-control-plane-live-execution-links",
        )
        results.append(control_link_result)
        if not control_link_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        try:
            initial_control_links = self._parse_control_execution_links(
                str(control_link_result.get("stdout", ""))
            )
            if set(initial_control_links) != {
                str(record["identity"]["entity_id"]) for record in control_records
            }:
                raise ValueError("control_execution_link_identity_set_mismatch")
            for record in control_records:
                record["execution_counts"] = initial_control_links[
                    str(record["identity"]["entity_id"])
                ]
        except (TypeError, ValueError) as exc:
            return self._failed_process_chain(
                results,
                last_error=f"historical_control_execution_links_invalid:{exc}",
                invariant_names=invariant_names,
            )
        control_scope_exact = bool(
            control_counts[2] == 0
            and self._historical_classification_exact(
                control_scope,
                observed_count=sum(control_counts),
                attestation_exact=attestation_ok.get("control_plane_task_entity_statuses", False),
            )
            and self._historical_attestation_exact(
                source="control_plane_task_entity_statuses",
                classification=control_scope,
                payload=_mapping(
                    classification_attestations["control_plane_task_entity_statuses"]["payload"],
                    "control_plane_attestation_payload",
                ),
                query=CONTROL_PLANE_HISTORY_QUERY,
                observed_records=control_records,
                file_sha_exact=attestation_ok.get("control_plane_task_entity_statuses", False),
                attestation_path=Path(
                    classification_attestations["control_plane_task_entity_statuses"]["path"]
                ),
            )
        )

        mlflow_scope_result = self._run(
            deadline,
            [
                self.docker,
                "exec",
                str(mlflow["container_name"]),
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(mlflow["user"]),
                "-d",
                str(mlflow["database"]),
                "-At",
                "-F",
                "|",
                "-c",
                MLFLOW_HISTORY_QUERY,
            ],
            name="r7-historical-mlflow-running-classification",
        )
        results.append(mlflow_scope_result)
        if not mlflow_scope_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        mlflow_records: list[dict[str, Any]] = []
        try:
            for line in str(mlflow_scope_result.get("stdout", "")).splitlines():
                if not line.strip():
                    continue
                fields = line.split("|")
                if len(fields) != 5:
                    raise ValueError("historical_mlflow_field_invalid")
                mlflow_records.append(
                    {
                        "identity": {
                            "run_id": fields[0],
                            "lifecycle_stage": fields[2],
                            "start_time": fields[3],
                            "end_time": fields[4],
                        },
                        "observed_state": fields[1],
                    }
                )
        except ValueError as exc:
            return self._failed_process_chain(
                results,
                last_error=f"historical_mlflow_invalid:{exc}",
                invariant_names=invariant_names,
            )
        observed_mlflow = sorted(str(item["identity"]["run_id"]) for item in mlflow_records)
        mlflow_scope_exact = bool(
            all(str(item["identity"]["end_time"]).strip() for item in mlflow_records)
            and self._historical_classification_exact(
                mlflow_scope,
                observed_count=len(observed_mlflow),
                attestation_exact=attestation_ok.get("mlflow_running_rows", False),
            )
            and self._historical_attestation_exact(
                source="mlflow_running_rows",
                classification=mlflow_scope,
                payload=_mapping(
                    classification_attestations["mlflow_running_rows"]["payload"],
                    "mlflow_attestation_payload",
                ),
                query=MLFLOW_HISTORY_QUERY,
                observed_records=mlflow_records,
                file_sha_exact=attestation_ok.get("mlflow_running_rows", False),
                attestation_path=Path(classification_attestations["mlflow_running_rows"]["path"]),
            )
        )

        failed_pods_result = self._run(
            deadline,
            self._kubectl_command(
                "get", "pods", "-A", "--field-selector=status.phase=Failed", "-o", "json"
            ),
            name="r7-historical-failed-pod-classification",
        )
        results.append(failed_pods_result)
        failed_pods_payload, failed_pods_error = self._json_object(
            failed_pods_result, "historical_failed_pods"
        )
        if failed_pods_error:
            return self._failed_process_chain(
                results, last_error=failed_pods_error, invariant_names=invariant_names
            )
        assert failed_pods_payload is not None
        observed_failed_pods = sorted(
            (self._failed_pod_identity(item) for item in failed_pods_payload.get("items", [])),
            key=lambda item: (item["namespace"], item["name"], item["uid"]),
        )
        failed_pod_records = [
            {
                "identity": dict(identity),
                "observed_state": "Failed",
            }
            for identity in observed_failed_pods
        ]
        kubernetes = _mapping(self.expected.get("kubernetes"), "expected_kubernetes")
        expected_failed_pods = list(kubernetes["allowed_historical_failed_pods"])
        failed_pods_exact = bool(
            observed_failed_pods == expected_failed_pods
            and self._historical_classification_exact(
                k8s_scope,
                observed_count=len(observed_failed_pods),
                attestation_exact=attestation_ok.get("kubernetes_terminal_failed_objects", False),
            )
            and self._historical_attestation_exact(
                source="kubernetes_terminal_failed_objects",
                classification=k8s_scope,
                payload=_mapping(
                    classification_attestations["kubernetes_terminal_failed_objects"]["payload"],
                    "kubernetes_attestation_payload",
                ),
                query=KUBERNETES_FAILED_QUERY,
                observed_records=failed_pod_records,
                file_sha_exact=attestation_ok.get("kubernetes_terminal_failed_objects", False),
                attestation_path=Path(
                    classification_attestations["kubernetes_terminal_failed_objects"]["path"]
                ),
            )
        )

        windows_result, windows_residuals = self._global_windows_residuals(deadline)
        results.append(windows_result)
        if not windows_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        if windows_residuals:
            failure = self._failed_process_chain(
                results,
                last_error=f"windows_global_residual_present:{len(windows_residuals)}",
                invariant_names=invariant_names,
            )
            failure.update(
                {
                    "manual_intervention_required": True,
                    "windows_global_residuals": windows_residuals,
                    "wsl_global_residuals": "not_run_after_windows_residual",
                    "subsequent_probe_launches": 0,
                }
            )
            return failure
        wsl_result, wsl_residuals = self._global_wsl_residuals(deadline)
        results.append(wsl_result)
        if not wsl_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        if wsl_residuals:
            failure = self._failed_process_chain(
                results,
                last_error=f"wsl_global_residual_present:{len(wsl_residuals)}",
                invariant_names=invariant_names,
            )
            failure.update(
                {
                    "manual_intervention_required": True,
                    "windows_global_residuals": windows_residuals,
                    "wsl_global_residuals": wsl_residuals,
                    "subsequent_probe_launches": 0,
                }
            )
            return failure

        final_queue_result = self._run(
            deadline,
            [
                self.docker,
                "exec",
                str(control["container_name"]),
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(control["user"]),
                "-d",
                str(control["database"]),
                "-At",
                "-F",
                "|",
                "-c",
                queue_sql,
            ],
            name="r7-final-queue-claims-readback",
        )
        results.append(final_queue_result)
        if not final_queue_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        try:
            final_queue_values = self._parse_queue_readback(
                str(final_queue_result.get("stdout", ""))
            )
        except ValueError as exc:
            return self._failed_process_chain(
                results,
                last_error=f"final_queue_readback_invalid:{exc}",
                invariant_names=invariant_names,
            )

        final_jobs_result = self._run(
            deadline,
            self._kubectl_command("get", "jobs", "-A", "-o", "json"),
            name="r7-final-active-jobs-readback",
        )
        results.append(final_jobs_result)
        final_jobs_payload, final_jobs_error = self._json_object(
            final_jobs_result, "final_active_jobs"
        )
        if final_jobs_error:
            return self._failed_process_chain(
                results, last_error=final_jobs_error, invariant_names=invariant_names
            )
        assert final_jobs_payload is not None
        try:
            final_job_snapshot = self._kubernetes_job_snapshot(final_jobs_payload.get("items"))
        except (TypeError, ValueError) as exc:
            return self._failed_process_chain(
                results,
                last_error=f"final_kubernetes_job_snapshot_invalid:{exc}",
                invariant_names=invariant_names,
            )

        final_control_link_result = self._run(
            deadline,
            [
                self.docker,
                "exec",
                str(control["container_name"]),
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(control["user"]),
                "-d",
                str(control["database"]),
                "-At",
                "-F",
                "|",
                "-c",
                CONTROL_PLANE_EXECUTION_LINK_QUERY,
            ],
            name="r7-final-control-plane-live-execution-links",
        )
        results.append(final_control_link_result)
        if not final_control_link_result.get("passed"):
            return self._failed_process_chain(results, invariant_names=invariant_names)
        try:
            final_control_links = self._parse_control_execution_links(
                str(final_control_link_result.get("stdout", ""))
            )
        except (TypeError, ValueError) as exc:
            return self._failed_process_chain(
                results,
                last_error=f"final_control_execution_links_invalid:{exc}",
                invariant_names=invariant_names,
            )

        try:
            final_file_active_jobs = sum(
                1
                for root in active_job_roots
                if root.is_dir()
                for item in root.iterdir()
                if item.is_file()
            )
            final_file_active_claims = sum(
                1
                for root in active_claim_roots
                if root.is_dir()
                for item in root.iterdir()
                if item.is_file()
            )
        except OSError as exc:
            return self._failed_process_chain(
                results,
                last_error=f"final_job_claim_marker_read_failed:{exc}",
                invariant_names=invariant_names,
            )
        final_lease_present = lease_path.exists()
        final_present_residue_paths = [str(path) for path in residue_paths if path.exists()]

        k8s_residue_count = sum(int(item["count"]) for item in residue_queries)
        control_execution_links_stable_zero = bool(
            self._temporal_execution_links_zero(initial_control_links, final_control_links)
            and set(final_control_links)
            == {str(record["identity"]["entity_id"]) for record in control_records}
        )
        queue_snapshots_stable = tuple(queue_values) == tuple(final_queue_values)
        queue_snapshots_stable_zero = self._temporal_queue_zero(queue_values, final_queue_values)
        job_snapshots_stable = initial_job_snapshot == final_job_snapshot
        job_snapshots_stable_zero = self._temporal_jobs_zero(
            initial_job_snapshot, final_job_snapshot
        )
        invariants = {
            "queue_active_zero": queue_snapshots_stable_zero,
            "queue_leased_zero": queue_snapshots_stable_zero,
            "queue_outcome_unknown_zero": queue_snapshots_stable_zero,
            "active_jobs_zero": job_snapshots_stable_zero
            and file_active_jobs == final_file_active_jobs == 0
            and job_snapshots_stable,
            "active_claims_zero": queue_snapshots_stable_zero
            and file_active_claims == final_file_active_claims == 0
            and queue_snapshots_stable,
            "gpu_lease_zero": not lease_path.exists() and not final_lease_present,
            "x1_residue_zero": not (
                present_residue_paths
                or final_present_residue_paths
                or k8s_residue_count
                or x1_containers
                or open_ports
            ),
            "canonical_active_scope_exact": canonical_scope_contract,
            "historical_control_plane_tasks_classified": control_scope_exact
            and control_execution_links_stable_zero,
            "historical_mlflow_running_classified": mlflow_scope_exact,
            "historical_failed_pods_classified": failed_pods_exact,
            "windows_global_residual_zero": not windows_residuals,
            "wsl_global_residual_zero": not wsl_residuals,
        }
        residual_pids = sorted(
            {int(pid) for result in results for pid in result.get("residual_pids", ())}
        )
        manual = bool(
            residual_pids
            or windows_residuals
            or wsl_residuals
            or any(result.get("manual_intervention_required") for result in results)
        )
        passed = all(invariants.values()) and not manual
        return {
            "passed": passed,
            "retryable": False,
            "last_error": None if passed else f"queue_jobs_residue_scope_gate:{invariants}",
            "manual_intervention_required": manual,
            "residual_pids": residual_pids,
            "invariants": invariants,
            "queues": {
                "initial": {
                    "active": queue_values[0],
                    "leased": queue_values[1],
                    "outcome_unknown": queue_values[2],
                    "active_claims": queue_values[3],
                    "unknown_state": queue_values[4],
                },
                "final": {
                    "active": final_queue_values[0],
                    "leased": final_queue_values[1],
                    "outcome_unknown": final_queue_values[2],
                    "active_claims": final_queue_values[3],
                    "unknown_state": final_queue_values[4],
                },
                "stable": queue_snapshots_stable,
            },
            "active_jobs": {
                "initial": initial_job_snapshot,
                "final": final_job_snapshot,
                "stable": job_snapshots_stable,
                "initial_file_markers": file_active_jobs,
                "final_file_markers": final_file_active_jobs,
            },
            "active_claims": {
                "initial_database_active": queue_values[3],
                "final_database_active": final_queue_values[3],
                "initial_file_markers": file_active_claims,
                "final_file_markers": final_file_active_claims,
            },
            "gpu_lease_path": str(lease_path),
            "residue_paths": present_residue_paths,
            "final_residue_paths": final_present_residue_paths,
            "kubernetes_residue": residue_queries,
            "docker_residue": x1_containers,
            "open_ports": open_ports,
            "historical_control_plane": {
                "observed": {
                    "queued": control_counts[0],
                    "pending_confirmation": control_counts[1],
                    "running": control_counts[2],
                },
                "expected": dict(control_scope),
                "initial_execution_links": initial_control_links,
                "final_execution_links": final_control_links,
                "execution_links_stable_zero": control_execution_links_stable_zero,
            },
            "historical_mlflow": {
                "observed_running_run_ids": observed_mlflow,
                "classification": dict(mlflow_scope),
            },
            "historical_failed_pods": {
                "observed": observed_failed_pods,
                "expected": expected_failed_pods,
            },
            "classification_attestations": classification_attestations,
            "windows_global_residuals": windows_residuals,
            "wsl_global_residuals": wsl_residuals,
            "process_evidence": [result["process_evidence"] for result in results],
        }

    def probes(self) -> dict[str, Callable[[RestoreDeadline], Mapping[str, Any]]]:
        return {
            RESTORE_STAGE_KEYS["docker_engine"]: self.docker_engine,
            RESTORE_STAGE_KEYS["compose"]: self.compose,
            RESTORE_STAGE_KEYS["kubernetes_api"]: self.kubernetes_api,
            RESTORE_STAGE_KEYS["node_device_plugin_gpu"]: self.node_device_plugin_gpu,
            RESTORE_STAGE_KEYS["b0_identity_cuda"]: self.b0_identity_cuda,
            RESTORE_STAGE_KEYS["prometheus"]: self.prometheus,
            RESTORE_STAGE_KEYS["api_release_identity"]: self.api_release_identity,
            RESTORE_STAGE_KEYS["queue_jobs_lease_residue"]: self.queue_jobs_lease_residue,
        }


def _new_probe_set(prepared: PreparedExecution, process_runner: Any | None = None) -> R7ProbeSet:
    return R7ProbeSet(
        manifest=prepared.manifest,
        contract=prepared.timeout_contract,
        expected_revision=prepared.args.expected_revision.lower(),
        repository_root=prepared.args.repository_root.resolve(),
        parent_payloads=prepared.parent_payloads,
        process_runner=process_runner,
    )


def _run_restore_harness(
    prepared: PreparedExecution,
    checkpoint: RestoreCheckpoint,
    *,
    process_runner: Any | None = None,
) -> RestoreReport:
    probes = _new_probe_set(prepared, process_runner)
    harness = ReconcileRestoreHarness(
        contract=prepared.timeout_contract,
        probes=probes.probes(),
        expected_revision=prepared.args.expected_revision.lower(),
        required_invariants=R7_REQUIRED_INVARIANTS,
        max_probe_attempts=1,
    )
    return harness.run_restore_only(checkpoint)


def _metadata(prepared: PreparedExecution) -> dict[str, Any]:
    parents = [
        {
            "role": str(item["role"]),
            "path": str(Path(str(item["path"])).resolve()),
            "sha256": str(item["sha256"]).lower(),
        }
        for item in _sequence(prepared.manifest.get("parent_checkpoints"), "parent_checkpoints")
    ]
    return {
        "run_id": prepared.run_id,
        "mode": MODE,
        "manifest": str(prepared.args.manifest.resolve()),
        "manifest_sha256": prepared.manifest_sha256,
        "bundle": str(prepared.bundle_directory),
        "canonical_revision": prepared.args.expected_revision.lower(),
        "canonical_tree": str(prepared.manifest.get("canonical_tree", "")).lower(),
        "parents": parents,
        "launcher_evidence": dict(prepared.launcher_evidence),
        "actual_launcher_invocations": {
            "outer": 1,
            "bridge": 1,
            "runner": 1,
            "automatic_retry": 0,
        },
        "actual_collector_invocations": {
            "windows_fresh_collector": 0,
            "wsl_fresh_collector": 0,
        },
        "downstream_invocations": {
            "full_stack_3180": 0,
            "q0": 0,
            "calibration_54": 0,
            "matrix_78": 0,
            "integrated_v4": 0,
            "etw": 0,
        },
        "phase_b2_executed": False,
        "completion_marker_created": False,
    }


def execute_restore_only(
    prepared: PreparedExecution,
    *,
    restore_executor: Callable[[PreparedExecution, RestoreCheckpoint], RestoreReport] | None = None,
    runner_reserved: bool = False,
) -> tuple[int, dict[str, Any]]:
    if prepared.output_directory.exists():
        raise R7RunnerError(f"output_directory_exists:{prepared.output_directory}")
    if runner_reserved:
        _verify_owned_runner_reservation(prepared)
    else:
        reserve_runner(prepared)
    executor = restore_executor or _run_restore_harness
    report = executor(prepared, prepared.restore_checkpoint)
    converted = r7_restore_report(report, prepared.run_id)
    if dict(converted.get("call_counts", {})) != RESTORE_LIFECYCLE_COUNTS:
        raise R7RunnerError("restore_only_lifecycle_calls_not_zero")
    if converted.get("phase_b2_executed") is not False:
        raise R7RunnerError("restore_only_phase_b2_execution_forbidden")
    writer = EvidenceWriter(prepared.output_directory)
    metadata = _metadata(prepared)
    if report.passed:
        try:
            evidence = writer.seal_restore_only(converted, metadata=metadata)
        except Exception as exc:
            failure_report = {
                **converted,
                "passed": False,
                "overall_pass": False,
                "restore_only_pass": False,
                "manual_intervention_required": True,
                "decision": "manual_intervention_required",
                "error": f"restore_only_success_publication_failed:{type(exc).__name__}:{exc}",
            }
            failure_evidence = writer.seal_failure(failure_report, metadata=metadata)
            return 2, {
                "decision": "manual_intervention_required",
                "report": failure_report,
                **failure_evidence,
            }
        return 0, {"decision": "restore_only_pass", "report": converted, **evidence}
    evidence = writer.seal_failure(converted, metadata=metadata)
    return 2, {
        "decision": "manual_intervention_required",
        "report": converted,
        **evidence,
    }


def _bootstrap_failure_report(args: argparse.Namespace, exc: BaseException) -> dict[str, Any]:
    return {
        "schema": "s8-v4-x1-phase-b2-r7-runner-bootstrap-failure/v1",
        "mode": MODE,
        "passed": False,
        "overall_pass": False,
        "manual_intervention_required": True,
        "decision": "manual_intervention_required",
        "error": f"{type(exc).__name__}:{exc}",
        "call_counts": dict(RESTORE_LIFECYCLE_COUNTS),
        "residual_pids": [],
        "residual_status": "unknown",
        "acceptance_credit": False,
        "phase_b2_executed": False,
        "completion_marker_created": False,
        "required_invariants": list(R7_REQUIRED_INVARIANTS),
        "success_invariants": {name: False for name in R7_REQUIRED_INVARIANTS},
    }


def _seal_bootstrap_failure(args: argparse.Namespace, exc: BaseException) -> Mapping[str, Any]:
    if isinstance(exc, DuplicateInvocationError):
        return {"failure_seal_error": "duplicate_invocation_no_new_evidence_allowed"}
    output = args.output_directory.resolve()
    if output.exists():
        return {"failure_seal_error": f"output_directory_exists:{output}"}
    try:
        manifest_path = args.manifest.resolve()
        manifest, manifest_sha256 = _read_manifest_snapshot_with_sha(manifest_path)
        manifest_output = Path(str(_mapping(manifest.get("output"), "manifest_output")["path"]))
        if not _resolved_equal(output, manifest_output):
            return {"failure_seal_error": "untrusted_output_argument_manifest_mismatch"}
        run_id = str(manifest.get("bundle_id", ""))
        reservation_path = manifest_path.parent / RUNNER_RESERVATION
        reservation = _verify_reservation(
            reservation_path,
            schema="s8-v4-x1-phase-b2-r7-runner-reservation/v1",
            output_directory=output,
            run_id=run_id,
        )
        if int(reservation.get("pid", -1)) != os.getpid():
            return {"failure_seal_error": "runner_reservation_owner_mismatch"}
        writer = EvidenceWriter(output)
        return writer.seal_failure(
            _bootstrap_failure_report(args, exc),
            metadata={
                "manifest": str(manifest_path),
                "manifest_sha256": manifest_sha256,
                "run_id": run_id,
                "runner_invocations": 1,
                "automatic_retry": 0,
                "phase_b2_executed": False,
            },
        )
    except Exception as seal_exc:
        return {"failure_seal_error": f"{type(seal_exc).__name__}:{seal_exc}"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run exactly one append-only S8-V4/X1 r7 restore-only gate."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--launcher-evidence-base64", required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=(MODE,), required=True)
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    reserve_runner_preflight(args)
    prepared = prepare_execution(args)
    return execute_restore_only(prepared, runner_reserved=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        code, result = execute(args)
        print(json.dumps(result, sort_keys=True))
        return code
    except Exception as exc:
        evidence = _seal_bootstrap_failure(args, exc)
        print(
            json.dumps(
                {**_bootstrap_failure_report(args, exc), **dict(evidence)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

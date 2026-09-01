from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import socket
import stat
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
_EARLY_CANONICAL_STAGING_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
    "staging/s8-v4/x1-clock-phase-b2-r7s1-restore"
)
_EARLY_CANONICAL_OUTPUT_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/"
    "private/s8-v4/x1-clock-phase-b2-r7s1-restore"
)
for search_path in (ROOT, SRC):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))


_EARLY_MANIFEST_PATH: Path | None = None
_EARLY_MANIFEST_BYTES: bytes | None = None


def _assert_no_reparse_ancestors(path: Path, *, label: str) -> None:
    """Reject a symlink/junction anywhere in an existing path ancestry.

    This check is intentionally available before verified project modules are
    loaded.  It is repeated at each write/launch boundary; Windows handle-based
    root identity remains the filesystem TCB for the interval between a check
    and the following operation.
    """

    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    candidate = Path(os.path.abspath(os.fspath(path)))
    while True:
        try:
            identity = candidate.stat(follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(f"{label}_ancestor_identity_unreadable:{candidate}") from exc
        else:
            if stat.S_ISLNK(identity.st_mode) or (
                getattr(identity, "st_file_attributes", 0) & reparse_flag
            ):
                raise RuntimeError(f"{label}_reparse_ancestor:{candidate}")
        if candidate.parent == candidate:
            return
        candidate = candidate.parent


def _absolute_lexical_path(path: Path | str) -> Path:
    """Normalize an absolute path without following a link or junction."""

    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _lexically_equal(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(str(_absolute_lexical_path(left))) == os.path.normcase(
        str(_absolute_lexical_path(right))
    )


def _assert_bound_run_locations(
    *, manifest_argument: Path, output_argument: Path, manifest: Mapping[str, Any], label: str
) -> None:
    """Bind the run to its sole staging/output/emergency lexical locations."""

    run_id = manifest.get("bundle_id")
    if not isinstance(run_id, str) or not run_id or "r7s1" not in run_id.lower():
        raise RuntimeError(f"{label}_r7s1_run_id_required")
    expected_staging = _EARLY_CANONICAL_STAGING_ROOT / run_id
    expected_output = _EARLY_CANONICAL_OUTPUT_ROOT / run_id
    expected_emergency = _EARLY_CANONICAL_OUTPUT_ROOT / f"{run_id}-emergency-seal"
    bundle = manifest.get("bundle")
    output = manifest.get("output")
    external = manifest.get("external_terminal_fencing")
    binding = external.get("successor_binding") if isinstance(external, Mapping) else None
    if (
        not isinstance(bundle, Mapping)
        or not isinstance(output, Mapping)
        or not isinstance(binding, Mapping)
    ):
        raise RuntimeError(f"{label}_bound_location_contract_required")
    comparisons = {
        "manifest_parent": (manifest_argument.parent, expected_staging),
        "manifest_bundle": (bundle.get("path", ""), expected_staging),
        "argument_output": (output_argument, expected_output),
        "manifest_output": (output.get("path", ""), expected_output),
        "binding_staging": (binding.get("staging_path", ""), expected_staging),
        "binding_output": (binding.get("output_path", ""), expected_output),
        "binding_emergency": (binding.get("emergency_seal_path", ""), expected_emergency),
    }
    for name, (observed, expected) in comparisons.items():
        if not _lexically_equal(observed, expected):
            raise RuntimeError(f"{label}_{name}_not_canonical")
    for name, path in (
        ("staging", expected_staging),
        ("output", expected_output),
        ("emergency", expected_emergency),
    ):
        _assert_no_reparse_ancestors(path, label=f"{label}_{name}")


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
    manifest_argument = Path(_early_cli_value(argv, "--manifest"))
    output_argument = Path(_early_cli_value(argv, "--output-directory"))
    _assert_no_reparse_ancestors(manifest_argument, label="pretrust_manifest")
    _assert_no_reparse_ancestors(output_argument, label="pretrust_output")
    manifest_path = manifest_argument.resolve()
    if manifest_path.name != "phase-b2-r7s1-work-order.json":
        raise RuntimeError("pretrust_manifest_leaf_mismatch")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("pretrust_manifest_unreadable") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("pretrust_manifest_object_required")
    _assert_bound_run_locations(
        manifest_argument=manifest_argument,
        output_argument=output_argument,
        manifest=manifest,
        label="pretrust",
    )
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
        {role: _absolute_lexical_path(path) for role, path in expected_runtime_paths.items()}
        if expected_runtime_paths is not None
        else {
            "runner": _absolute_lexical_path(runner_path),
            "process": _absolute_lexical_path(
                SRC / "evm" / "scale_validation" / "phase_b2_r7_process.py"
            ),
            "core": _absolute_lexical_path(SRC / "evm" / "scale_validation" / "phase_b2_r7s1.py"),
        }
    )
    if set(expected_paths) != {"runner", "process", "core"}:
        raise RuntimeError("pretrust_expected_runtime_role_set_mismatch")
    snapshots: dict[str, tuple[Path, bytes]] = {}
    for role in ("runner", "process", "core"):
        pin = runtime.get(role)
        if not isinstance(pin, dict) or set(pin) != {
            "path",
            "sha256",
            "worktree_blob_oid",
            "head_blob_oid",
            "bytes",
        }:
            raise RuntimeError(f"pretrust_runtime_pin_invalid:{role}")
        raw_path = Path(str(pin["path"]))
        if not raw_path.is_absolute():
            raise RuntimeError(f"pretrust_runtime_path_not_absolute:{role}")
        path = _absolute_lexical_path(raw_path)
        _assert_no_reparse_ancestors(path, label=f"pretrust_runtime_{role}")
        if not _lexically_equal(path, expected_paths[role]):
            raise RuntimeError(f"pretrust_runtime_path_mismatch:{role}")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"pretrust_runtime_unreadable:{role}") from exc
        measured = hashlib.sha256(payload).hexdigest()
        measured_worktree_blob = hashlib.sha1(
            f"blob {len(payload)}\0".encode("ascii") + payload
        ).hexdigest()
        if (
            measured != str(pin["sha256"]).lower()
            or measured != str(chain.get(role, "")).lower()
            or measured_worktree_blob != str(pin["worktree_blob_oid"]).lower()
            or re.fullmatch(r"[0-9a-f]{40}", str(pin["head_blob_oid"]).lower()) is None
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
    parent_name, _, child_name = module_name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is None:
        sys.modules.pop(module_name, None)
        raise RuntimeError(f"pretrust_snapshot_parent_package_missing:{parent_name}")
    setattr(parent, child_name, module)
    try:
        exec(compile(payload, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        if getattr(parent, child_name, None) is module:
            delattr(parent, child_name)
        raise
    return module


def _install_package_shell(module_name: str, package_path: Path) -> types.ModuleType:
    """Install a package namespace without executing an unpinned ``__init__.py``."""

    if module_name in sys.modules:
        raise RuntimeError(f"pretrust_package_already_loaded:{module_name}")
    resolved = package_path.resolve()
    if not resolved.is_dir():
        raise RuntimeError(f"pretrust_package_directory_missing:{module_name}")
    parent_name, _, child_name = module_name.rpartition(".")
    parent = None
    if parent_name:
        parent = sys.modules.get(parent_name)
        if parent is None:
            raise RuntimeError(f"pretrust_package_parent_missing:{parent_name}")
    package = types.ModuleType(module_name)
    package.__file__ = None
    package.__package__ = module_name
    package.__path__ = [str(resolved)]
    sys.modules[module_name] = package
    if parent is not None:
        setattr(parent, child_name, package)
    return package


def _install_verified_runtime_snapshots(argv: Sequence[str]) -> None:
    global _EARLY_MANIFEST_BYTES, _EARLY_MANIFEST_PATH
    manifest_path, manifest_bytes, snapshots = _early_runtime_snapshots(
        argv, runner_path=Path(__file__)
    )
    # Importing ``evm.scale_validation.<verified leaf>`` normally executes both
    # repository ``__init__.py`` files.  Those files are not executable runtime
    # leaves in the work-order SHA chain, so production uses inert package shells
    # and executes only the byte snapshots verified above.
    _install_package_shell("evm", SRC / "evm")
    _install_package_shell("evm.scale_validation", SRC / "evm" / "scale_validation")
    for role, module_name in (
        ("process", "evm.scale_validation.phase_b2_r7_process"),
        ("core", "evm.scale_validation.phase_b2_r7s1"),
    ):
        path, payload = snapshots[role]
        _load_module_snapshot(module_name, path, payload)
    _EARLY_MANIFEST_PATH = manifest_path
    _EARLY_MANIFEST_BYTES = manifest_bytes


if __name__ == "__main__":
    _install_verified_runtime_snapshots(sys.argv[1:])

from evm.scale_validation.phase_b2_r7s1 import (  # noqa: E402
    DOCKER_CLIENT_CONFIG_POLICY,
    DOCKER_CONTAINER_EXECUTION_SCOPE,
    EvidenceWriter,
    GIT_CONFIG_ORIGIN_IDENTITY,
    GIT_REPOSITORY_ATTRIBUTES_POLICY,
    GIT_REPOSITORY_CONFIG_POLICY,
    HISTORICAL_DECISION_AUTHORITY,
    HISTORICAL_QUERY_SHA256,
    HISTORICAL_QUERY_TEXTS,
    KUBERNETES_CLIENT_CONFIG_POLICY,
    R7S1_REQUIRED_INVARIANTS,
    RESTORE_LIFECYCLE_COUNTS,
    RUNTIME_COMPONENTS,
    ReconcileRestoreHarness,
    RestoreCheckpoint,
    RestoreDeadline,
    RestoreReport,
    RestoreStage,
    TimeoutContract,
    canonical_json_bytes,
    decode_launcher_evidence,
    find_verified_decision,
    r7s1_restore_report,
    read_parent_checkpoints,
    sha256_file,
    validate_r7s1_manifest,
)
from evm.scale_validation.phase_b2_r7_process import (  # noqa: E402
    TimeoutContract as ProcessTimeoutContract,
    WindowsJobProcessRunner,
    WslResidualProtocol,
)


OUTER_LEAF = "invoke-verified-x1-phase-b2-r7s1.ps1"
BRIDGE_LEAF = "invoke-x1-phase-b2-r7s1-bridge.ps1"
MANIFEST_LEAF = "phase-b2-r7s1-work-order.json"
OUTER_RESERVATION = "r7s1-outer-invocation-reservation.json"
BRIDGE_RESERVATION = "r7s1-bridge-invocation-reservation.json"
RUNNER_RESERVATION = "r7s1-runner-invocation-reservation.json"
RUNNER_INVOKE_MARKER = "R7S1_RUNNER_INVOKE_EXACTLY_ONCE"
RESERVATION_PROCESS_FIELDS = {
    "pid",
    "ppid",
    "session_id",
    "creation_filetime",
    "process_path",
    "process_path_sha256",
}
RESERVATION_BASE_FIELDS = {
    "schema",
    "created_at",
    "invocation_nonce",
    *RESERVATION_PROCESS_FIELDS,
    "run_id",
    "mode",
    "output_directory",
}
MODE = "restore-only"
CANONICAL_BRANCH = "codex/distributed-scale-validation-plan"
PINNED_GIT_PATH = Path("C:/Program Files/Git/mingw64/bin/git.exe")
PINNED_DOCKER_COMPOSE_PATH = Path("C:/Program Files/Docker/Docker/resources/bin/docker-compose.exe")
CANONICAL_GIT_REMOTE_URL = "https://github.com/ruma0236/ML_ServeAPI.git"
CANONICAL_GIT_REMOTE_URL_REDACTION = (
    "<pinned-origin-url:sha256="
    + hashlib.sha256(CANONICAL_GIT_REMOTE_URL.encode("utf-8")).hexdigest()
    + ">"
)
FULL_SHA1 = re.compile(r"^[0-9a-f]{40}$")
FULL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
HTTP_RESPONSE_MAX_BYTES = 1_048_576
HTTP_READ_CHUNK_BYTES = 65_536
GIT_CONFIG_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-git-repository-config-live-readback/v1"
GIT_ATTRIBUTES_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-git-repository-attributes-readback/v1"
DOCKER_CLIENT_CONFIG_READBACK_SCHEMA = "s8-v4-x1-phase-b2-r7s1-docker-client-config-readback/v1"
KUBERNETES_CLIENT_CONFIG_READBACK_SCHEMA = (
    "s8-v4-x1-phase-b2-r7s1-kubernetes-client-config-readback/v1"
)
_GIT_ENVIRONMENT_SCRUB_EXACT = {
    "all_proxy",
    "curl_ca_bundle",
    "editor",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "pager",
    "request_method",
    "ssh_agent_pid",
    "ssh_askpass",
    "ssh_askpass_require",
    "ssh_auth_sock",
    "ssl_cert_dir",
    "ssl_cert_file",
    "visual",
    "xdg_config_home",
}
_GIT_ENVIRONMENT_EXACT = {
    "GCM_INTERACTIVE": "never",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "NUL",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "",
    "GIT_TERMINAL_PROMPT": "0",
}

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
DATABASE_CONNECTION_QUERY = "SELECT current_database(),pg_is_in_recovery();"
DATABASE_MIGRATION_QUERIES = {
    "control_plane": "SELECT version FROM evm_control_plane.schema_migrations ORDER BY version;",
    "mlflow": "SELECT version_num FROM alembic_version ORDER BY version_num;",
    "airflow": "SELECT version_num FROM alembic_version ORDER BY version_num;",
}
QUEUE_READBACK_QUERY = (
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
CONTAINER_STABILITY_FORMAT = (
    "{{.Id}}\t{{.Image}}\t{{.Name}}\t{{.State.Status}}\t"
    "{{.State.Running}}\t{{.State.Restarting}}\t{{.RestartCount}}\t"
    "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\t"
    "{{.State.OOMKilled}}"
)
API_CONTAINER_IDENTITY_FORMAT = "{{.Id}}\t{{.Image}}\t{{.Name}}"
API_IMAGE_IDENTITY_FORMAT = '{{.Id}}\t{{index .Config.Labels "org.opencontainers.image.revision"}}'
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


class R7S1RunnerError(RuntimeError):
    """Fail-closed r7s1 restore-only runner error."""


class DuplicateInvocationError(R7S1RunnerError):
    """Raised before probes when a one-shot runner reservation already exists."""


class ReadOnlyCommandPolicyError(R7S1RunnerError):
    """Raised before process creation when a probe argv is not exactly allowlisted."""

    def __init__(self, *, name: str, role: str, argv_sha256: str) -> None:
        self.name = name
        self.role = role
        self.argv_sha256 = argv_sha256
        super().__init__(
            f"read_only_command_not_allowlisted:{role}:{name}:argv_sha256={argv_sha256}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": "redacted_by_read_only_command_policy",
            "command_role": self.role,
            "command_argv_sha256": self.argv_sha256,
            "command_policy": "exact_executable_role_and_argv/v1",
            "child_created": False,
            "timed_out": False,
            "cancelled": False,
            "residual_pids": [],
            "forced_termination_attempts": 0,
        }


class _HTTPResponsePolicyError(RuntimeError):
    """Raised when an HTTP response violates the bounded read-only probe policy."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an HTTPError instead of following it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class PreparedExecution:
    args: argparse.Namespace
    manifest: Mapping[str, Any]
    manifest_sha256: str
    validated_manifest: Mapping[str, Any]
    launcher_evidence: Mapping[str, Any]
    parent_payloads: Mapping[str, Mapping[str, Any]]
    restore_checkpoint: RestoreCheckpoint
    timeout_contract: TimeoutContract
    output_directory: Path
    run_id: str
    bundle_directory: Path


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S1RunnerError(f"{label}_mapping_required")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise R7S1RunnerError(f"{label}_sequence_required")
    return value


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise R7S1RunnerError(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise R7S1RunnerError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise R7S1RunnerError(f"{label}_object_required:{path}")
    return value


def _read_json_snapshot(path: Path, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise R7S1RunnerError(f"{label}_missing:{path}")
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7S1RunnerError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise R7S1RunnerError(f"{label}_object_required:{path}")
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
            raise R7S1RunnerError("verified_manifest_snapshot_invalid") from exc
        if not isinstance(value, dict):
            raise R7S1RunnerError("verified_manifest_snapshot_object_required")
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
        raise R7S1RunnerError("etw_amendment_sha256_invalid")
    if not path.is_file():
        raise R7S1RunnerError(f"etw_amendment_missing:{path}")
    try:
        measured_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise R7S1RunnerError(f"etw_amendment_unreadable:{path}") from exc
    if measured_sha != expected_sha:
        raise R7S1RunnerError("etw_amendment_sha256_mismatch")


def _resolved_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = (
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.wintypes.LONG),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.wintypes.WCHAR * 260),
    )


def _windows_process_table() -> dict[int, dict[str, Any]]:
    if os.name != "nt":
        raise R7S1RunnerError("windows_process_identity_requires_windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
    )
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32W),
    )
    kernel32.Process32FirstW.restype = ctypes.wintypes.BOOL
    kernel32.Process32NextW.argtypes = kernel32.Process32FirstW.argtypes
    kernel32.Process32NextW.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    table: dict[int, dict[str, Any]] = {}
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        present = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while present:
            table[int(entry.th32ProcessID)] = {
                "ppid": int(entry.th32ParentProcessID),
                "name": str(entry.szExeFile),
            }
            present = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def _windows_process_identity(
    pid: int, *, process_table: Mapping[int, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    if os.name != "nt":
        raise R7S1RunnerError("windows_process_identity_requires_windows")
    table = dict(process_table or _windows_process_table())
    snapshot = _mapping(table.get(int(pid)), f"process_snapshot_{pid}")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (
        ctypes.wintypes.DWORD,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    )
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.LPWSTR,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.FILETIME),
        ctypes.POINTER(ctypes.wintypes.FILETIME),
        ctypes.POINTER(ctypes.wintypes.FILETIME),
        ctypes.POINTER(ctypes.wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = ctypes.wintypes.BOOL
    kernel32.ProcessIdToSessionId.argtypes = (
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    )
    kernel32.ProcessIdToSessionId.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    handle = kernel32.OpenProcess(0x00001000, False, int(pid))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        capacity = ctypes.wintypes.DWORD(32768)
        path_buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(capacity)):
            raise ctypes.WinError(ctypes.get_last_error())
        creation = ctypes.wintypes.FILETIME()
        exit_time = ctypes.wintypes.FILETIME()
        kernel_time = ctypes.wintypes.FILETIME()
        user_time = ctypes.wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)
    session_id = ctypes.wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(int(pid), ctypes.byref(session_id)):
        raise ctypes.WinError(ctypes.get_last_error())
    path = Path(path_buffer.value).resolve()
    return {
        "pid": int(pid),
        "ppid": int(snapshot["ppid"]),
        "session_id": int(session_id.value),
        "creation_filetime": (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime),
        "path": str(path),
        "path_sha256": sha256_file(path),
        "name": str(snapshot["name"]),
    }


def _windows_token_evidence() -> dict[str, Any]:
    if os.name != "nt":
        raise R7S1RunnerError("windows_token_measurement_requires_windows")

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
        if required.value < ctypes.sizeof(TokenMandatoryLabel):
            raise R7S1RunnerError("token_integrity_information_size_invalid")
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
            raise R7S1RunnerError("token_integrity_sid_invalid")
        rid_pointer = advapi32.GetSidSubAuthority(label.label.sid, count_pointer.contents.value - 1)
        if not rid_pointer:
            raise R7S1RunnerError("token_integrity_rid_missing")
        integrity_rid = int(rid_pointer.contents.value)
    finally:
        kernel32.CloseHandle(token)
    integrity = (
        "System" if integrity_rid >= 0x4000 else "High" if integrity_rid >= 0x3000 else "Other"
    )
    elevation_type = {1: "Default", 2: "Full", 3: "Limited"}.get(
        int(elevation.value), f"Unknown:{int(elevation.value)}"
    )
    return {
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "administrator": bool(shell32.IsUserAnAdmin()),
        "integrity": integrity,
        "integrity_rid": integrity_rid,
        "token_elevation_type": elevation_type,
        "token_elevation_type_value": int(elevation.value),
    }


def _native_runner_environment() -> dict[str, Any]:
    table = _windows_process_table()
    current = _windows_process_identity(os.getpid(), process_table=table)
    parent = _windows_process_identity(int(current["ppid"]), process_table=table)
    ancestors: list[dict[str, Any]] = []
    visited = {int(current["pid"])}
    ancestor_pid = int(parent["pid"])
    for _depth in range(16):
        if ancestor_pid <= 0 or ancestor_pid in visited or ancestor_pid not in table:
            break
        visited.add(ancestor_pid)
        identity = _windows_process_identity(ancestor_pid, process_table=table)
        ancestors.append(identity)
        if str(identity["name"]).casefold() == "codex.exe":
            break
        ancestor_pid = int(identity["ppid"])
    codex = next((item for item in ancestors if str(item["name"]).casefold() == "codex.exe"), None)
    return {
        "token": _windows_token_evidence(),
        "runner": current,
        "parent": parent,
        "codex": codex,
        "ancestor_chain": ancestors,
    }


def _process_reservation_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pid": identity["pid"],
        "ppid": identity["ppid"],
        "session_id": identity["session_id"],
        "creation_filetime": identity["creation_filetime"],
        "process_path": identity["path"],
        "process_path_sha256": identity["path_sha256"],
    }


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
            raise R7S1RunnerError(f"launcher_file_missing:{name}:{path}")
        actual = sha256_file(path)
        if actual != str(chain.get(name, "")).lower():
            raise R7S1RunnerError(f"launcher_file_sha256_mismatch:{name}")
        measured[name] = actual
    return measured


def _verify_launcher_git(manifest: Mapping[str, Any], launcher_evidence: Mapping[str, Any]) -> None:
    git = _mapping(launcher_evidence.get("git"), "launcher_git")
    if set(git) != {
        "measurement",
        "branch",
        "revision",
        "origin_revision",
        "remote_revision",
        "tree",
        "tracked",
        "untracked",
        "untracked_path_set_sha256",
    }:
        raise R7S1RunnerError("launcher_git_deferred_fields_mismatch")
    if git.get("measurement") != "deferred_to_contained_runner":
        raise R7S1RunnerError("launcher_git_must_be_deferred_to_contained_runner")
    repository = _mapping(manifest.get("repository"), "manifest_repository")
    revision = str(manifest.get("canonical_revision", "")).lower()
    tree = str(manifest.get("canonical_tree", "")).lower()
    if launcher_evidence.get("mode") != MODE or manifest.get("execution_mode") != MODE:
        raise R7S1RunnerError("launcher_mode_mismatch")
    if any(
        str(git.get(name, "")).lower() != revision
        for name in ("revision", "origin_revision", "remote_revision")
    ):
        raise R7S1RunnerError("launcher_local_origin_remote_mismatch")
    if str(git.get("tree", "")).lower() != tree:
        raise R7S1RunnerError("launcher_tree_mismatch")
    if str(git.get("branch", "")) != CANONICAL_BRANCH:
        raise R7S1RunnerError("launcher_branch_mismatch")
    if int(git.get("tracked", -1)) != 0:
        raise R7S1RunnerError("launcher_tracked_changes_present")
    if int(git.get("untracked", -1)) != int(repository.get("preserved_untracked_count", -2)):
        raise R7S1RunnerError("launcher_untracked_count_mismatch")
    if (
        str(git.get("untracked_path_set_sha256", "")).lower()
        != str(repository.get("untracked_path_set_sha256", "")).lower()
    ):
        raise R7S1RunnerError("launcher_untracked_path_set_mismatch")


def _verify_reservation(
    path: Path,
    *,
    schema: str,
    output_directory: Path,
    run_id: str,
    expected_process_identity: Mapping[str, Any],
    expected_nonce: str | None = None,
    expected_outer_reservation_sha256: str | None = None,
) -> Mapping[str, Any]:
    value = _read_json_object(path, path.stem)
    expected_fields = set(RESERVATION_BASE_FIELDS)
    if schema.endswith("bridge-reservation/v1"):
        expected_fields.add("outer_reservation_sha256")
    if set(value) != expected_fields:
        raise R7S1RunnerError(f"reservation_fields_mismatch:{path.name}")
    if value.get("schema") != schema:
        raise R7S1RunnerError(f"reservation_schema_mismatch:{path.name}")
    if value.get("mode") != MODE or value.get("run_id") != run_id:
        raise R7S1RunnerError(f"reservation_identity_mismatch:{path.name}")
    if not _resolved_equal(Path(str(value.get("output_directory", ""))), output_directory):
        raise R7S1RunnerError(f"reservation_output_mismatch:{path.name}")
    try:
        datetime.fromisoformat(str(value["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise R7S1RunnerError(f"reservation_created_at_invalid:{path.name}") from exc
    nonce = str(value.get("invocation_nonce", ""))
    if re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
        raise R7S1RunnerError(f"reservation_nonce_invalid:{path.name}")
    if expected_nonce is not None and nonce != expected_nonce:
        raise R7S1RunnerError(f"reservation_nonce_mismatch:{path.name}")
    expected_projection = _process_reservation_identity(expected_process_identity)
    for field in RESERVATION_PROCESS_FIELDS - {"process_path"}:
        if value.get(field) != expected_projection[field]:
            raise R7S1RunnerError(f"reservation_process_identity_mismatch:{path.name}:{field}")
    if not _resolved_equal(
        Path(str(value.get("process_path", ""))),
        Path(str(expected_projection["process_path"])),
    ):
        raise R7S1RunnerError(f"reservation_process_identity_mismatch:{path.name}:process_path")
    if schema.endswith("bridge-reservation/v1"):
        observed_outer_sha = str(value.get("outer_reservation_sha256", "")).lower()
        if (
            expected_outer_reservation_sha256 is None
            or observed_outer_sha != expected_outer_reservation_sha256
        ):
            raise R7S1RunnerError("bridge_outer_reservation_sha256_mismatch")
    return value


def _verify_launcher_authority_and_parent(
    *,
    manifest: Mapping[str, Any],
    launcher: Mapping[str, Any],
    environment: Mapping[str, Any],
    invocation_nonce: str,
) -> None:
    token = _mapping(environment.get("token"), "native_token")
    if not (
        token.get("administrator") is True
        and token.get("integrity") in {"High", "System"}
        and token.get("token_elevation_type") == "Full"
        and token.get("token_elevation_type_value") == 2
    ):
        raise R7S1RunnerError(
            "administrator_token_required:"
            f"administrator={token.get('administrator')}:"
            f"integrity={token.get('integrity')}:"
            f"token_elevation_type={token.get('token_elevation_type')}"
        )
    launcher_token = _mapping(launcher.get("token_evidence"), "launcher_token_evidence")
    if set(launcher_token) != {
        "captured_at",
        "administrator",
        "integrity",
        "token_elevation_type",
        "token_elevation_type_value",
        "invocation_nonce",
        "execution_powershell",
        "codex",
    }:
        raise R7S1RunnerError("launcher_token_evidence_fields_mismatch")
    if (
        launcher_token.get("administrator") is not True
        or launcher_token.get("integrity") not in {"High", "System"}
        or launcher_token.get("token_elevation_type") != "Full"
        or launcher_token.get("token_elevation_type_value") != 2
        or launcher_token.get("invocation_nonce") != invocation_nonce
    ):
        raise R7S1RunnerError("launcher_token_or_nonce_mismatch")
    parent = _mapping(environment.get("parent"), "native_parent")
    runner = _mapping(environment.get("runner"), "native_runner")
    toolchain = _mapping(manifest.get("toolchain"), "toolchain")
    powershell_pin = _mapping(toolchain.get("powershell"), "toolchain_powershell")
    python_pin = _mapping(toolchain.get("python"), "toolchain_python")
    if (
        not _resolved_equal(Path(str(runner["path"])), Path(str(python_pin["path"])))
        or str(runner["path_sha256"]).lower() != str(python_pin["sha256"]).lower()
    ):
        raise R7S1RunnerError("runner_not_pinned_python_interpreter")
    if (
        str(parent.get("name", "")).casefold() not in {"powershell.exe", "pwsh.exe"}
        or not _resolved_equal(Path(str(parent["path"])), Path(str(powershell_pin["path"])))
        or str(parent["path_sha256"]).lower() != str(powershell_pin["sha256"]).lower()
    ):
        raise R7S1RunnerError("runner_parent_not_pinned_powershell")
    claimed_parent = _mapping(
        launcher_token.get("execution_powershell"), "launcher_execution_powershell"
    )
    if set(claimed_parent) != {
        "pid",
        "ppid",
        "session_id",
        "creation_filetime",
        "path",
        "path_sha256",
    }:
        raise R7S1RunnerError("launcher_execution_powershell_fields_mismatch")
    for field in {"pid", "ppid", "session_id", "creation_filetime", "path_sha256"}:
        if claimed_parent.get(field) != parent.get(field):
            raise R7S1RunnerError(f"launcher_execution_powershell_identity_mismatch:{field}")
    if not _resolved_equal(Path(str(claimed_parent["path"])), Path(str(parent["path"]))):
        raise R7S1RunnerError("launcher_execution_powershell_identity_mismatch:path")

    native_codex = environment.get("codex")
    claimed_codex = _mapping(launcher_token.get("codex"), "launcher_codex")
    if native_codex is None:
        raise R7S1RunnerError("live_codex_ancestor_required")
    native_codex = _mapping(native_codex, "native_codex")
    if set(claimed_codex) != {
        "pid",
        "ppid",
        "session_id",
        "creation_filetime",
        "path",
        "path_sha256",
        "command_line_sha256",
    }:
        raise R7S1RunnerError("launcher_codex_fields_mismatch")
    for field in {"pid", "ppid", "session_id", "creation_filetime", "path_sha256"}:
        if claimed_codex.get(field) != native_codex.get(field):
            raise R7S1RunnerError(f"launcher_codex_identity_mismatch:{field}")
    if not _resolved_equal(Path(str(claimed_codex["path"])), Path(str(native_codex["path"]))):
        raise R7S1RunnerError("launcher_codex_identity_mismatch:path")
    if re.fullmatch(r"[0-9a-f]{64}", str(claimed_codex["command_line_sha256"])) is None:
        raise R7S1RunnerError("launcher_codex_command_line_sha256_invalid")


def _verify_launcher_reservations(
    *,
    bundle: Path,
    output_directory: Path,
    run_id: str,
    parent_identity: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    outer_path = bundle / OUTER_RESERVATION
    outer = _verify_reservation(
        outer_path,
        schema="s8-v4-x1-phase-b2-r7s1-outer-reservation/v1",
        output_directory=output_directory,
        run_id=run_id,
        expected_process_identity=parent_identity,
    )
    outer_sha256 = sha256_file(outer_path)
    nonce = str(outer["invocation_nonce"])
    bridge = _verify_reservation(
        bundle / BRIDGE_RESERVATION,
        schema="s8-v4-x1-phase-b2-r7s1-bridge-reservation/v1",
        output_directory=output_directory,
        run_id=run_id,
        expected_process_identity=parent_identity,
        expected_nonce=nonce,
        expected_outer_reservation_sha256=outer_sha256,
    )
    return outer, bridge, nonce


def prepare_execution(args: argparse.Namespace) -> PreparedExecution:
    _assert_no_reparse_ancestors(args.manifest, label="manifest")
    _assert_no_reparse_ancestors(args.output_directory, label="output_directory")
    manifest_path = args.manifest.resolve()
    repository_root = args.repository_root.resolve()
    if manifest_path.name != MANIFEST_LEAF:
        raise R7S1RunnerError("r7s1_manifest_leaf_mismatch")
    manifest, manifest_sha256 = _read_manifest_snapshot_with_sha(manifest_path)
    _assert_bound_run_locations(
        manifest_argument=args.manifest,
        output_argument=args.output_directory,
        manifest=manifest,
        label="prepare",
    )
    timeout_contract = TimeoutContract().validate()
    _verify_etw_amendment(manifest)
    validated_manifest = validate_r7s1_manifest(
        manifest,
        expected_revision=args.expected_revision,
        mode=args.mode,
        repository_root=repository_root,
        runtime_timeout=timeout_contract,
        expected_trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
    )
    launcher = decode_launcher_evidence(args.launcher_evidence_base64, manifest)
    if launcher.get("run_id") != manifest.get("bundle_id"):
        raise R7S1RunnerError("launcher_run_id_mismatch")
    invocation_counts = _mapping(launcher.get("invocation_counts"), "launcher_invocation_counts")
    if dict(invocation_counts) != {
        "outer": 1,
        "bridge": 1,
        "runner": 1,
        "automatic_retry": 0,
    }:
        raise R7S1RunnerError("launcher_invocation_counts_mismatch")
    _verify_launcher_files(manifest_path, launcher)
    _verify_launcher_git(manifest, launcher)

    output = _mapping(manifest.get("output"), "manifest_output")
    output_directory = args.output_directory.resolve()
    if not _resolved_equal(output_directory, Path(str(output.get("path", "")))):
        raise R7S1RunnerError("output_argument_manifest_path_mismatch")
    if output_directory.exists():
        raise R7S1RunnerError(f"output_directory_exists:{output_directory}")
    revision = str(manifest.get("canonical_revision", "")).lower()
    if revision != args.expected_revision.lower() or FULL_SHA1.fullmatch(revision) is None:
        raise R7S1RunnerError("expected_revision_mismatch")
    run_id = str(manifest.get("bundle_id", ""))
    if not run_id or "r7s1" not in run_id.lower():
        raise R7S1RunnerError("r7s1_bundle_id_required")

    parent_payloads, checkpoint = read_parent_checkpoints(manifest.get("parent_checkpoints"))
    if not isinstance(checkpoint, RestoreCheckpoint):
        raise R7S1RunnerError("restore_checkpoint_required")

    bundle = manifest_path.parent
    bundle_contract = _mapping(manifest.get("bundle"), "manifest_bundle")
    if not _resolved_equal(bundle, Path(str(bundle_contract.get("path", "")))):
        raise R7S1RunnerError("bundle_argument_manifest_path_mismatch")
    environment = _native_runner_environment()
    _outer, _bridge, invocation_nonce = _verify_launcher_reservations(
        bundle=bundle,
        output_directory=output_directory,
        run_id=run_id,
        parent_identity=_mapping(environment.get("parent"), "native_parent"),
    )
    _verify_launcher_authority_and_parent(
        manifest=manifest,
        launcher=launcher,
        environment=environment,
        invocation_nonce=invocation_nonce,
    )
    return PreparedExecution(
        args=args,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        validated_manifest=validated_manifest,
        launcher_evidence=launcher,
        parent_payloads=parent_payloads,
        restore_checkpoint=checkpoint,
        timeout_contract=timeout_contract,
        output_directory=output_directory,
        run_id=run_id,
        bundle_directory=bundle,
    )


def reserve_runner(prepared: PreparedExecution) -> None:
    _assert_bound_run_locations(
        manifest_argument=prepared.args.manifest,
        output_argument=prepared.output_directory,
        manifest=prepared.manifest,
        label="runner_reservation",
    )
    _assert_no_reparse_ancestors(prepared.bundle_directory, label="bundle_directory")
    _assert_no_reparse_ancestors(prepared.output_directory, label="output_directory")
    environment = _native_runner_environment()
    _outer, _bridge, invocation_nonce = _verify_launcher_reservations(
        bundle=prepared.bundle_directory,
        output_directory=prepared.output_directory,
        run_id=prepared.run_id,
        parent_identity=_mapping(environment.get("parent"), "native_parent"),
    )
    _verify_launcher_authority_and_parent(
        manifest=prepared.manifest,
        launcher=prepared.launcher_evidence,
        environment=environment,
        invocation_nonce=invocation_nonce,
    )
    _write_runner_reservation(
        bundle_directory=prepared.bundle_directory,
        output_directory=prepared.output_directory,
        run_id=prepared.run_id,
        invocation_nonce=invocation_nonce,
        runner_identity=_mapping(environment.get("runner"), "native_runner"),
    )


def _write_runner_reservation(
    *,
    bundle_directory: Path,
    output_directory: Path,
    run_id: str,
    invocation_nonce: str,
    runner_identity: Mapping[str, Any],
) -> None:
    _assert_no_reparse_ancestors(bundle_directory, label="bundle_directory")
    _assert_no_reparse_ancestors(output_directory, label="output_directory")
    _write_exclusive_json(
        bundle_directory / RUNNER_RESERVATION,
        {
            "schema": "s8-v4-x1-phase-b2-r7s1-runner-reservation/v1",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "invocation_nonce": invocation_nonce,
            **_process_reservation_identity(runner_identity),
            "mode": MODE,
            "run_id": run_id,
            "output_directory": str(output_directory),
        },
    )


def reserve_runner_preflight(args: argparse.Namespace) -> None:
    """Own the one-shot runner identity before mutable full preflight work."""
    _assert_no_reparse_ancestors(args.manifest, label="manifest")
    _assert_no_reparse_ancestors(args.output_directory, label="output_directory")
    manifest_path = args.manifest.resolve()
    if manifest_path.name != MANIFEST_LEAF:
        raise R7S1RunnerError("r7s1_manifest_leaf_mismatch")
    manifest = _read_manifest_snapshot(manifest_path)
    _assert_bound_run_locations(
        manifest_argument=args.manifest,
        output_argument=args.output_directory,
        manifest=manifest,
        label="runner_preflight",
    )
    if re.fullmatch(r"[0-9a-fA-F]{64}", str(args.expected_trusted_checkpoint_sha256)) is None:
        raise R7S1RunnerError("expected_trusted_checkpoint_sha256_invalid")
    if args.mode != MODE or manifest.get("execution_mode") != MODE:
        raise R7S1RunnerError("runner_preflight_mode_mismatch")
    revision = str(manifest.get("canonical_revision", "")).lower()
    if revision != str(args.expected_revision).lower() or FULL_SHA1.fullmatch(revision) is None:
        raise R7S1RunnerError("runner_preflight_revision_mismatch")
    run_id = str(manifest.get("bundle_id", ""))
    if not run_id or "r7s1" not in run_id.lower():
        raise R7S1RunnerError("r7s1_bundle_id_required")
    bundle_directory = manifest_path.parent
    bundle_contract = _mapping(manifest.get("bundle"), "manifest_bundle")
    if not _resolved_equal(bundle_directory, Path(str(bundle_contract.get("path", "")))):
        raise R7S1RunnerError("bundle_argument_manifest_path_mismatch")
    output_directory = args.output_directory.resolve()
    output_contract = _mapping(manifest.get("output"), "manifest_output")
    if not _resolved_equal(output_directory, Path(str(output_contract.get("path", "")))):
        raise R7S1RunnerError("output_argument_manifest_path_mismatch")
    if output_directory.exists():
        raise R7S1RunnerError(f"output_directory_exists:{output_directory}")
    launcher = decode_launcher_evidence(args.launcher_evidence_base64, manifest)
    environment = _native_runner_environment()
    _outer, _bridge, invocation_nonce = _verify_launcher_reservations(
        bundle=bundle_directory,
        output_directory=output_directory,
        run_id=run_id,
        parent_identity=_mapping(environment.get("parent"), "native_parent"),
    )
    _verify_launcher_authority_and_parent(
        manifest=manifest,
        launcher=launcher,
        environment=environment,
        invocation_nonce=invocation_nonce,
    )
    _write_runner_reservation(
        bundle_directory=bundle_directory,
        output_directory=output_directory,
        run_id=run_id,
        invocation_nonce=invocation_nonce,
        runner_identity=_mapping(environment.get("runner"), "native_runner"),
    )


def _verify_owned_runner_reservation(prepared: PreparedExecution) -> None:
    _assert_bound_run_locations(
        manifest_argument=prepared.args.manifest,
        output_argument=prepared.output_directory,
        manifest=prepared.manifest,
        label="owned_runner_reservation",
    )
    _assert_no_reparse_ancestors(prepared.bundle_directory, label="bundle_directory")
    _assert_no_reparse_ancestors(prepared.output_directory, label="output_directory")
    environment = _native_runner_environment()
    _outer, _bridge, invocation_nonce = _verify_launcher_reservations(
        bundle=prepared.bundle_directory,
        output_directory=prepared.output_directory,
        run_id=prepared.run_id,
        parent_identity=_mapping(environment.get("parent"), "native_parent"),
    )
    _verify_launcher_authority_and_parent(
        manifest=prepared.manifest,
        launcher=prepared.launcher_evidence,
        environment=environment,
        invocation_nonce=invocation_nonce,
    )
    reservation = _verify_reservation(
        prepared.bundle_directory / RUNNER_RESERVATION,
        schema="s8-v4-x1-phase-b2-r7s1-runner-reservation/v1",
        output_directory=prepared.output_directory,
        run_id=prepared.run_id,
        expected_process_identity=_mapping(environment.get("runner"), "native_runner"),
        expected_nonce=invocation_nonce,
    )
    if int(reservation.get("pid", -1)) != os.getpid():
        raise DuplicateInvocationError("runner_reservation_owner_mismatch")


def _process_timeout(contract: TimeoutContract) -> ProcessTimeoutContract:
    return ProcessTimeoutContract(**contract.to_dict())


class R7S1ProbeSet:
    """Read-only runtime gate with Job containment and no probe retries."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        contract: TimeoutContract,
        expected_revision: str,
        repository_root: Path,
        parent_payloads: Mapping[str, Mapping[str, Any]] | None = None,
        validated_manifest: Mapping[str, Any] | None = None,
        process_runner: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.manifest = dict(manifest)
        self.contract = contract.validate()
        self.expected_revision = expected_revision.lower()
        self.repository_root = repository_root.resolve()
        self.parent_payloads = dict(parent_payloads or {})
        self.validated_manifest = dict(validated_manifest or {})
        self.expected = _mapping(self.manifest.get("expected_state"), "expected_state")
        normalized_toolchain = self.validated_manifest.get("toolchain")
        self.toolchain = _mapping(
            normalized_toolchain
            if isinstance(normalized_toolchain, Mapping)
            else self.manifest.get("toolchain"),
            "toolchain",
        )
        self._host_tool_pins = {
            role: _mapping(self.toolchain.get(role), f"toolchain_{role}")
            for role in (
                "python",
                "docker",
                "docker_compose",
                "kubectl",
                "wsl",
                "powershell",
                "git",
            )
        }
        self.python = self._verified_host_executable("python")
        self.docker = self._verified_host_executable("docker")
        self.docker_compose = self._verified_host_executable("docker_compose")
        self.kubectl = self._verified_host_executable("kubectl")
        self.wsl = self._verified_host_executable("wsl")
        self.powershell = self._verified_host_executable("powershell")
        self.git = self._verified_host_executable("git")
        if not _resolved_equal(Path(self.python), Path(sys.executable)):
            raise R7S1RunnerError("toolchain_python_not_current_interpreter")
        if not _resolved_equal(Path(self.git), PINNED_GIT_PATH):
            raise R7S1RunnerError("toolchain_git_path_not_canonical_mingw64_binary")
        if not _resolved_equal(Path(self.docker_compose), PINNED_DOCKER_COMPOSE_PATH):
            raise R7S1RunnerError("toolchain_docker_compose_path_not_canonical_standalone_binary")
        self.runner = process_runner or WindowsJobProcessRunner(_process_timeout(contract))
        self.clock = clock
        self.sleep = sleep
        self._pending_wsl_post_scan_command: tuple[str, ...] | None = None

    def _verified_host_executable(self, role: str) -> str:
        pin = _mapping(self._host_tool_pins.get(role), f"toolchain_{role}")
        if set(pin) != {"path", "sha256", "bytes", "version", "signature"}:
            raise R7S1RunnerError(f"toolchain_host_pin_fields_mismatch:{role}")
        path = Path(str(pin.get("path", "")))
        expected_sha256 = str(pin.get("sha256", "")).lower()
        expected_bytes = pin.get("bytes")
        if not path.is_absolute() or not path.is_file():
            raise R7S1RunnerError(f"toolchain_host_binary_missing:{role}")
        if (
            re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 1
        ):
            raise R7S1RunnerError(f"toolchain_host_binary_pin_invalid:{role}")
        try:
            actual_bytes = path.stat().st_size
            actual_sha256 = sha256_file(path)
        except OSError as exc:
            raise R7S1RunnerError(f"toolchain_host_binary_unreadable:{role}") from exc
        if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
            raise R7S1RunnerError(f"toolchain_host_binary_identity_mismatch:{role}")
        return str(path.resolve())

    @staticmethod
    def _captured_at() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _dynamic_docker_policy(config_path: Path) -> dict[str, Any]:
        policy = json.loads(json.dumps(DOCKER_CLIENT_CONFIG_POLICY))
        config_root = str(config_path.parent.resolve())
        policy["child_environment"]["set_variables"]["DOCKER_CONFIG"] = config_root
        policy["docker_global_arguments"] = [
            "--config",
            config_root,
            "--context",
            "desktop-linux",
        ]
        return policy

    @staticmethod
    def _dynamic_kubernetes_policy(config_path: Path) -> dict[str, Any]:
        policy = json.loads(json.dumps(KUBERNETES_CLIENT_CONFIG_POLICY))
        resolved = str(config_path.resolve())
        policy["child_environment"]["set_variables"]["KUBECONFIG"] = resolved
        policy["required_global_arguments"] = [
            "--kubeconfig",
            resolved,
            "--context",
            "docker-desktop",
            "--request-timeout=8s",
        ]
        return policy

    def _minimal_windows_child_environment(self) -> dict[str, str]:
        windows_tcb = _mapping(self.toolchain.get("windows_tcb"), "toolchain_windows_tcb")
        system32 = Path(str(windows_tcb.get("system32_path", "")))
        if not system32.is_absolute() or system32.name.casefold() != "system32":
            raise R7S1RunnerError("toolchain_windows_system32_path_invalid")
        _assert_no_reparse_ancestors(system32, label="windows_system32_child_environment")
        windows_root = str(system32.resolve().parent)
        return {"SystemRoot": windows_root, "WINDIR": windows_root}

    def _docker_environment(self, policy: Mapping[str, Any]) -> dict[str, str]:
        environment = self._minimal_windows_child_environment()
        child_policy = _mapping(policy.get("child_environment"), "docker_child_environment")
        set_variables = _mapping(
            child_policy.get("set_variables"), "docker_child_environment_set_variables"
        )
        environment.update({str(key): str(value) for key, value in set_variables.items()})
        return environment

    def _kubernetes_environment(self, policy: Mapping[str, Any]) -> dict[str, str]:
        environment = self._minimal_windows_child_environment()
        child_policy = _mapping(policy.get("child_environment"), "kubernetes_child_environment")
        set_variables = _mapping(
            child_policy.get("set_variables"), "kubernetes_child_environment_set_variables"
        )
        environment.update({str(key): str(value) for key, value in set_variables.items()})
        return environment

    def _powershell_environment(self) -> dict[str, str]:
        environment = self._minimal_windows_child_environment()
        module_root = (
            Path(environment["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"
        )
        _assert_no_reparse_ancestors(module_root, label="powershell_system_module_root")
        if not module_root.is_dir():
            raise R7S1RunnerError("powershell_system_module_root_missing")
        environment.update(
            {
                "PSModulePath": str(module_root.resolve()),
                "POWERSHELL_TELEMETRY_OPTOUT": "1",
            }
        )
        return environment

    def _wsl_environment(self) -> dict[str, str]:
        environment = self._minimal_windows_child_environment()
        environment["WSL_UTF8"] = "1"
        return environment

    @staticmethod
    def _pinned_file_bytes(pin: Mapping[str, Any], *, label: str) -> tuple[Path, bytes, str]:
        path = Path(str(pin.get("path", "")))
        expected_sha256 = str(pin.get("sha256", "")).lower()
        expected_bytes = pin.get("bytes")
        if (
            not path.is_absolute()
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 1
        ):
            raise R7S1RunnerError(f"{label}_pin_invalid")
        _assert_no_reparse_ancestors(path, label=label)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise R7S1RunnerError(f"{label}_unreadable") from exc
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if len(payload) != expected_bytes or actual_sha256 != expected_sha256:
            raise R7S1RunnerError(f"{label}_identity_mismatch")
        return path.resolve(), payload, actual_sha256

    def _verify_docker_client_config(self) -> dict[str, Any]:
        pin = _mapping(self.toolchain.get("docker_client_config"), "toolchain_docker_client_config")
        if set(pin) != {"path", "sha256", "bytes", "context_metadata", "policy", "readback"}:
            raise R7S1RunnerError("toolchain_docker_client_config_fields_mismatch")
        config_path, config_bytes, config_sha256 = self._pinned_file_bytes(
            pin, label="docker_client_config"
        )
        expected_policy = self._dynamic_docker_policy(config_path)
        policy = _mapping(pin.get("policy"), "toolchain_docker_client_config_policy")
        if dict(policy) != expected_policy:
            raise R7S1RunnerError("toolchain_docker_client_config_policy_mismatch")
        try:
            config = json.loads(config_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R7S1RunnerError("docker_client_config_json_invalid") from exc
        if not isinstance(config, dict) or list(config) != expected_policy["top_level_keys"]:
            raise R7S1RunnerError("docker_client_config_top_level_keys_mismatch")
        auths = config.get("auths")
        if not isinstance(auths, dict) or auths:
            raise R7S1RunnerError("docker_client_config_auths_must_be_empty")
        if not isinstance(config.get("credsStore"), str) or not config["credsStore"]:
            raise R7S1RunnerError("docker_client_config_credential_store_presence_mismatch")
        if config.get("currentContext") != "desktop-linux":
            raise R7S1RunnerError("docker_client_config_current_context_mismatch")

        context_pin = _mapping(
            pin.get("context_metadata"), "toolchain_docker_client_config_context_metadata"
        )
        if set(context_pin) != {"path", "sha256", "bytes"}:
            raise R7S1RunnerError("toolchain_docker_context_metadata_fields_mismatch")
        context_path, context_bytes, context_sha256 = self._pinned_file_bytes(
            context_pin, label="docker_context_metadata"
        )
        context_id = hashlib.sha256(b"desktop-linux").hexdigest()
        expected_context_path = (
            config_path.parent / "contexts" / "meta" / context_id / "meta.json"
        ).resolve()
        if not _resolved_equal(context_path, expected_context_path):
            raise R7S1RunnerError("docker_context_metadata_path_mismatch")
        try:
            context = json.loads(context_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R7S1RunnerError("docker_context_metadata_json_invalid") from exc
        if not isinstance(context, dict) or set(context) != {"Name", "Metadata", "Endpoints"}:
            raise R7S1RunnerError("docker_context_metadata_fields_mismatch")
        if context.get("Name") != "desktop-linux" or not isinstance(context.get("Metadata"), dict):
            raise R7S1RunnerError("docker_context_metadata_name_mismatch")
        endpoints = context.get("Endpoints")
        if not isinstance(endpoints, dict) or set(endpoints) != {"docker"}:
            raise R7S1RunnerError("docker_context_endpoint_set_mismatch")
        endpoint = endpoints.get("docker")
        if not isinstance(endpoint, dict) or set(endpoint) != {"Host", "SkipTLSVerify"}:
            raise R7S1RunnerError("docker_context_endpoint_fields_mismatch")
        host = endpoint.get("Host")
        endpoint_identity = _mapping(expected_policy.get("endpoint_identity"), "docker_endpoint")
        if (
            not isinstance(host, str)
            or urllib.parse.urlsplit(host).scheme.casefold() != endpoint_identity.get("scheme")
            or hashlib.sha256(host.encode("utf-8")).hexdigest()
            != endpoint_identity.get("endpoint_sha256")
            or endpoint.get("SkipTLSVerify") is not endpoint_identity.get("skip_tls_verify")
        ):
            raise R7S1RunnerError("docker_context_endpoint_identity_mismatch")

        tls_path = config_path.parent / "contexts" / "tls" / context_id
        _assert_no_reparse_ancestors(tls_path, label="docker_context_tls_material")
        if tls_path.exists():
            raise R7S1RunnerError("docker_context_tls_material_directory_must_be_absent")
        return {
            "schema": DOCKER_CLIENT_CONFIG_READBACK_SCHEMA,
            "status": "verified",
            "captured_at": self._captured_at(),
            "path": str(config_path),
            "sha256": config_sha256,
            "bytes": len(config_bytes),
            "top_level_keys": list(config),
            "auth_entries": 0,
            "credential_store_present": True,
            "credential_store_value_exposed": False,
            "current_context": "desktop-linux",
            "context_metadata": {
                "path": str(context_path),
                "sha256": context_sha256,
                "bytes": len(context_bytes),
            },
            "endpoint_identity": dict(endpoint_identity),
            "tls_material_directory_absent": True,
            "policy_sha256": hashlib.sha256(canonical_json_bytes(expected_policy)).hexdigest(),
        }

    @staticmethod
    def _kube_embedded_value(line: str, key: str) -> bytes:
        prefix = f"    {key}: "
        if not line.startswith(prefix):
            raise R7S1RunnerError(f"kubernetes_client_config_{key}_missing")
        encoded = line[len(prefix) :]
        if not encoded or any(character.isspace() for character in encoded):
            raise R7S1RunnerError(f"kubernetes_client_config_{key}_invalid")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise R7S1RunnerError(f"kubernetes_client_config_{key}_invalid") from exc
        if not decoded:
            raise R7S1RunnerError(f"kubernetes_client_config_{key}_empty")
        return decoded

    def _verify_kubernetes_client_config(self) -> dict[str, Any]:
        pin = _mapping(
            self.toolchain.get("kubernetes_client_config"),
            "toolchain_kubernetes_client_config",
        )
        if set(pin) != {"path", "sha256", "bytes", "policy", "readback"}:
            raise R7S1RunnerError("toolchain_kubernetes_client_config_fields_mismatch")
        config_path, payload, config_sha256 = self._pinned_file_bytes(
            pin, label="kubernetes_client_config"
        )
        expected_policy = self._dynamic_kubernetes_policy(config_path)
        policy = _mapping(pin.get("policy"), "toolchain_kubernetes_client_config_policy")
        if dict(policy) != expected_policy:
            raise R7S1RunnerError("toolchain_kubernetes_client_config_policy_mismatch")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise R7S1RunnerError("kubernetes_client_config_utf8_invalid") from exc
        if "\x00" in text or "\r" in text:
            raise R7S1RunnerError("kubernetes_client_config_encoding_not_canonical")
        lines = text.splitlines()
        safe_lines = [
            "apiVersion: v1",
            "clusters:",
            "- cluster:",
            None,
            "    server: https://kubernetes.docker.internal:6443",
            "  name: docker-desktop",
            "contexts:",
            "- context:",
            "    cluster: docker-desktop",
            "    user: docker-desktop",
            "  name: docker-desktop",
            "current-context: docker-desktop",
            "kind: Config",
            "users:",
            "- name: docker-desktop",
            "  user:",
            None,
            None,
        ]
        if len(lines) != len(safe_lines) or any(
            expected is not None and lines[index] != expected
            for index, expected in enumerate(safe_lines)
        ):
            raise R7S1RunnerError("kubernetes_client_config_structure_mismatch")
        self._kube_embedded_value(lines[3], "certificate-authority-data")
        self._kube_embedded_value(lines[16], "client-certificate-data")
        self._kube_embedded_value(lines[17], "client-key-data")
        lowered_keys = {
            match.group(1).casefold()
            for line in lines
            if (match := re.match(r"^\s*(?:-\s*)?([A-Za-z0-9_-]+)\s*:", line))
        }
        forbidden = {str(item).casefold() for item in expected_policy["forbidden_fields_absent"]}
        if lowered_keys.intersection(forbidden) or re.search(r"(^|\s)(?:!|&|\*|<<:)", text):
            raise R7S1RunnerError("kubernetes_client_config_forbidden_indirection")
        server = "https://kubernetes.docker.internal:6443"
        parsed_server = urllib.parse.urlsplit(server)
        server_identity = _mapping(
            expected_policy["cluster_identity"]["server_identity"],
            "kubernetes_server_identity",
        )
        if (
            parsed_server.scheme != server_identity.get("scheme")
            or parsed_server.hostname != server_identity.get("host")
            or parsed_server.port != server_identity.get("port")
            or parsed_server.username is not None
            or parsed_server.password is not None
            or parsed_server.query
            or parsed_server.fragment
            or hashlib.sha256(server.encode("utf-8")).hexdigest()
            != server_identity.get("server_sha256")
        ):
            raise R7S1RunnerError("kubernetes_client_config_server_identity_mismatch")
        return {
            "schema": KUBERNETES_CLIENT_CONFIG_READBACK_SCHEMA,
            "status": "verified",
            "captured_at": self._captured_at(),
            "path": str(config_path),
            "sha256": config_sha256,
            "bytes": len(payload),
            "current_context": "docker-desktop",
            "object_counts": dict(expected_policy["object_counts"]),
            "context_identity": dict(expected_policy["context_identity"]),
            "cluster_identity": dict(expected_policy["cluster_identity"]),
            "user_identity": dict(expected_policy["user_identity"]),
            "forbidden_fields_absent": list(expected_policy["forbidden_fields_absent"]),
            "multiple_config_merge_forbidden": True,
            "embedded_material_presence": dict(expected_policy["embedded_material_presence"]),
            "policy_sha256": hashlib.sha256(canonical_json_bytes(expected_policy)).hexdigest(),
        }

    @property
    def launch_budget_seconds(self) -> float:
        return (
            self.contract.wrapper_timeout_seconds
            + self.contract.residual_repoll_seconds
            + self.contract.stream_drain_seconds
        )

    def _kubectl_command(self, *arguments: str) -> list[str]:
        seconds = int(self.contract.kubectl_timeout_seconds)
        pin = _mapping(
            self.toolchain.get("kubernetes_client_config"),
            "toolchain_kubernetes_client_config",
        )
        config_path = Path(str(pin.get("path", "")))
        policy = self._dynamic_kubernetes_policy(config_path)
        global_arguments = list(policy["required_global_arguments"])
        if global_arguments[-1] != f"--request-timeout={seconds}s":
            raise R7S1RunnerError("kubectl_timeout_policy_mismatch")
        return [self.kubectl, *global_arguments, *arguments]

    def _docker_command(self, *arguments: str) -> list[str]:
        pin = _mapping(self.toolchain.get("docker_client_config"), "toolchain_docker_client_config")
        config_path = Path(str(pin.get("path", "")))
        policy = self._dynamic_docker_policy(config_path)
        return [self.docker, *policy["docker_global_arguments"], *arguments]

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

    @staticmethod
    def _windows_residual_script() -> str:
        return (
            "$ErrorActionPreference='Stop';"
            f"$excluded=@({os.getpid()});"
            "$names=@('python.exe','pythonw.exe','wsl.exe','bash.exe','sh.exe');"
            "$pattern='(?i)(phase[_-]b2|x1-clock-phase-b2|s8-v4-x1)';"
            "$sha=[Security.Cryptography.SHA256]::Create();"
            "$rows=@(CimCmdlets\\Get-CimInstance -ClassName Win32_Process | "
            "Microsoft.PowerShell.Core\\Where-Object {"
            "$names -contains $_.Name -and $_.ProcessId -notin $excluded -and "
            "$null -ne $_.CommandLine -and $_.CommandLine -match $pattern} | "
            "Microsoft.PowerShell.Core\\ForEach-Object {[ordered]@{"
            "pid=[int]$_.ProcessId;ppid=[int]$_.ParentProcessId;"
            "creation_time=[string]$_.CreationDate;name=[string]$_.Name;"
            "command_line_sha256=(-join @($sha.ComputeHash("
            "[Text.Encoding]::UTF8.GetBytes([string]$_.CommandLine)) | "
            "Microsoft.PowerShell.Core\\ForEach-Object {$_.ToString('x2')}))}});"
            "$sha.Dispose();Microsoft.PowerShell.Utility\\ConvertTo-Json "
            "-Compress -Depth 5 -InputObject @($rows)"
        )

    @staticmethod
    def _wsl_residual_source() -> str:
        return r"""import hashlib,json,os,pathlib,re
pattern=re.compile(r'(phase[_-]b2|x1-clock-phase-b2|s8-v4-x1)',re.I)
excluded={os.getpid(),os.getppid()}
run_uuid=os.environ.get('EVM_PHASE_B2_RUN_UUID','')
boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
self_stat=pathlib.Path('/proc/self/stat').read_text()
self_right=self_stat.rfind(')')
self_fields=self_stat[self_right+1:].strip().split()
root={'pid':os.getpid(),'ppid':int(self_fields[1]),'pgrp':int(self_fields[2]),
      'session':int(self_fields[3]),'start_time_ticks':int(self_fields[19]),
      'boot_id':boot_id}
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
print(json.dumps({'schema':'s8-v4-x1-phase-b2-r7s1-wsl-global-residual-readback/v2',
                  'run_uuid':run_uuid,'root':root,
                  'residuals':sorted(rows,key=lambda item:(item['pid'],item['start_time_ticks']))},
                 sort_keys=True,separators=(',',':')))
"""

    def _wsl_protocol(self) -> WslResidualProtocol:
        external = _mapping(
            self.manifest.get("external_terminal_fencing"), "external_terminal_fencing"
        )
        binding = _mapping(external.get("successor_binding"), "successor_binding")
        return WslResidualProtocol(str(binding["attempt_id"]))

    def _wsl_protocol_launch_command(self, protocol: WslResidualProtocol) -> list[str]:
        wsl_runtime = _mapping(self.toolchain.get("wsl_runtime"), "toolchain_wsl_runtime")
        python3 = _mapping(wsl_runtime.get("python3"), "toolchain_wsl_python3")
        command = list(
            protocol.launch_command(
                str(wsl_runtime["distro"]),
                [str(python3["realpath"]), "-c", self._wsl_residual_source()],
            )
        )
        command[0] = self.wsl
        return command

    def _wsl_protocol_scan_command(self, protocol: WslResidualProtocol) -> list[str]:
        wsl_runtime = _mapping(self.toolchain.get("wsl_runtime"), "toolchain_wsl_runtime")
        python3 = _mapping(wsl_runtime.get("python3"), "toolchain_wsl_python3")
        command = list(protocol.scan_command(str(wsl_runtime["distro"])))
        command[0] = self.wsl
        command[4] = str(python3["realpath"])
        return command

    @staticmethod
    def _git_environment() -> dict[str, str]:
        """Build a non-interactive Git environment with no inherited helpers."""

        clean = {
            key: value
            for key, value in os.environ.items()
            if not key.casefold().startswith("git_")
            and key.casefold() not in _GIT_ENVIRONMENT_SCRUB_EXACT
        }
        clean.update(_GIT_ENVIRONMENT_EXACT)
        return clean

    @staticmethod
    def _sanitize_git_evidence(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(CANONICAL_GIT_REMOTE_URL, CANONICAL_GIT_REMOTE_URL_REDACTION)
        if isinstance(value, Mapping):
            return {
                str(key): R7S1ProbeSet._sanitize_git_evidence(item) for key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple(R7S1ProbeSet._sanitize_git_evidence(item) for item in value)
        if isinstance(value, list):
            return [R7S1ProbeSet._sanitize_git_evidence(item) for item in value]
        return value

    @staticmethod
    def _parse_git_repository_config(payload: bytes) -> dict[str, str]:
        """Parse the deliberately simple canonical config without invoking Git."""

        try:
            text = payload.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise R7S1RunnerError("git_repository_config_utf8_invalid") from exc
        if "\x00" in text:
            raise R7S1RunnerError("git_repository_config_nul_forbidden")

        current_section: str | None = None
        entries: dict[str, str] = {}
        section_pattern = re.compile(
            r'^\[([A-Za-z][A-Za-z0-9.-]*)(?:[ \t]+"([^"\\\r\n]+)")?\][ \t]*$'
        )
        key_pattern = re.compile(r"^([A-Za-z][A-Za-z0-9.-]*)[ \t]*=[ \t]*(.*?)[ \t]*$")
        for ordinal, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            section_match = section_pattern.fullmatch(line)
            if section_match is not None:
                section = section_match.group(1).casefold()
                subsection = section_match.group(2)
                current_section = (
                    section if subsection is None else f"{section}.{subsection.casefold()}"
                )
                continue
            key_match = key_pattern.fullmatch(line)
            if current_section is None or key_match is None:
                raise R7S1RunnerError(f"git_repository_config_syntax_not_canonical:line={ordinal}")
            value = key_match.group(2)
            if not value or value.startswith(("!", '"')) or value.endswith("\\"):
                raise R7S1RunnerError(f"git_repository_config_value_not_canonical:line={ordinal}")
            name = f"{current_section}.{key_match.group(1).casefold()}"
            if name in entries:
                raise R7S1RunnerError(f"git_repository_config_duplicate_key:{name}")
            entries[name] = value
        return entries

    def _verify_git_repository_config(self) -> dict[str, Any]:
        pin = _mapping(
            self.toolchain.get("git_repository_config"), "toolchain_git_repository_config"
        )
        if set(pin) != {"path", "sha256", "bytes", "policy", "readback"}:
            raise R7S1RunnerError("toolchain_git_repository_config_fields_mismatch")
        policy = _mapping(pin.get("policy"), "toolchain_git_repository_config_policy")
        if dict(policy) != dict(GIT_REPOSITORY_CONFIG_POLICY):
            raise R7S1RunnerError("toolchain_git_repository_config_policy_mismatch")

        config_path = Path(str(pin.get("path", "")))
        expected_path = self.repository_root / ".git" / "config"
        if not config_path.is_absolute() or not _lexically_equal(config_path, expected_path):
            raise R7S1RunnerError("git_repository_config_not_bound_to_repository_root")
        _assert_no_reparse_ancestors(config_path, label="git_repository_config")
        try:
            payload = config_path.read_bytes()
            identity = config_path.stat(follow_symlinks=False)
        except OSError as exc:
            raise R7S1RunnerError("git_repository_config_unreadable") from exc
        expected_sha256 = str(pin.get("sha256", "")).lower()
        expected_bytes = pin.get("bytes")
        measured_sha256 = hashlib.sha256(payload).hexdigest()
        if (
            not stat.S_ISREG(identity.st_mode)
            or isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 1
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or identity.st_size != expected_bytes
            or len(payload) != expected_bytes
            or measured_sha256 != expected_sha256
        ):
            raise R7S1RunnerError("git_repository_config_identity_mismatch")

        config_worktree = config_path.with_name("config.worktree")
        _assert_no_reparse_ancestors(config_worktree, label="git_repository_config_worktree")
        if config_worktree.exists():
            raise R7S1RunnerError("git_repository_config_worktree_must_be_absent")

        entries = self._parse_git_repository_config(payload)
        allowed_names = list(_sequence(policy.get("allowed_key_names"), "git_allowed_key_names"))
        key_names = sorted(entries)
        if key_names != allowed_names:
            raise R7S1RunnerError("git_repository_config_key_policy_mismatch")

        exact_values = {
            "core.repositoryformatversion": "0",
            "core.filemode": "false",
            "core.bare": "false",
            "core.logallrefupdates": "true",
            "core.symlinks": "false",
            "core.ignorecase": "true",
            "extensions.worktreeconfig": "true",
            "remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*",
        }
        for branch in (
            "codex/local-infra-mvp",
            "codex/mac-mini-worker",
            CANONICAL_BRANCH,
            "codex/x1-resume-results-20260825-215716",
        ):
            prefix = f"branch.{branch}."
            exact_values[prefix + "remote"] = "origin"
            exact_values[prefix + "merge"] = f"refs/heads/{branch}"
        if any(entries.get(name) != value for name, value in exact_values.items()):
            raise R7S1RunnerError("git_repository_config_value_policy_mismatch")
        if not entries.get("user.name") or not entries.get("user.email"):
            raise R7S1RunnerError("git_repository_config_user_fields_empty")

        raw_origin = entries.get("remote.origin.url", "")
        try:
            origin = urllib.parse.urlsplit(raw_origin)
            origin_port = origin.port
        except ValueError as exc:
            raise R7S1RunnerError("git_repository_config_origin_url_invalid") from exc
        origin_identity = {
            "scheme": origin.scheme.casefold(),
            "host": (origin.hostname or "").casefold(),
            "path_sha256": hashlib.sha256(origin.path.encode("utf-8")).hexdigest(),
        }
        if (
            raw_origin != CANONICAL_GIT_REMOTE_URL
            or origin_identity != dict(GIT_CONFIG_ORIGIN_IDENTITY)
            or origin.username is not None
            or origin.password is not None
            or origin_port is not None
            or origin.query
            or origin.fragment
        ):
            raise R7S1RunnerError("git_repository_config_origin_policy_mismatch")

        return {
            "schema": GIT_CONFIG_READBACK_SCHEMA,
            "path": str(config_path),
            "sha256": measured_sha256,
            "bytes": len(payload),
            "key_names": key_names,
            "origin_identity": origin_identity,
            "config_worktree_absent": True,
            "policy_sha256": hashlib.sha256(
                canonical_json_bytes(dict(GIT_REPOSITORY_CONFIG_POLICY))
            ).hexdigest(),
        }

    def _verify_git_repository_attributes(self) -> dict[str, Any]:
        pin = _mapping(
            self.toolchain.get("git_repository_attributes"),
            "toolchain_git_repository_attributes",
        )
        if set(pin) != {"path", "sha256", "bytes", "policy", "readback"}:
            raise R7S1RunnerError("toolchain_git_repository_attributes_fields_mismatch")
        policy = _mapping(pin.get("policy"), "toolchain_git_repository_attributes_policy")
        if dict(policy) != dict(GIT_REPOSITORY_ATTRIBUTES_POLICY):
            raise R7S1RunnerError("toolchain_git_repository_attributes_policy_mismatch")
        attributes_path, payload, measured_sha256 = self._pinned_file_bytes(
            pin, label="git_repository_attributes"
        )
        if attributes_path.name != ".gitattributes":
            raise R7S1RunnerError("git_repository_attributes_leaf_mismatch")
        try:
            project_root = attributes_path.parent.resolve()
            project_root.relative_to(self.repository_root)
        except ValueError as exc:
            raise R7S1RunnerError("git_repository_attributes_outside_repository") from exc
        if project_root == self.repository_root:
            raise R7S1RunnerError("git_repository_attributes_project_subdirectory_required")

        top_attributes = self.repository_root / ".gitattributes"
        info_attributes = self.repository_root / ".git" / "info" / "attributes"
        for label, absent_path in (
            ("git_top_level_attributes", top_attributes),
            ("git_info_attributes", info_attributes),
        ):
            _assert_no_reparse_ancestors(absent_path, label=label)
            if absent_path.exists():
                raise R7S1RunnerError(f"{label}_must_be_absent")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise R7S1RunnerError("git_repository_attributes_utf8_invalid") from exc
        if "\x00" in text:
            raise R7S1RunnerError("git_repository_attributes_nul_forbidden")
        rules = [line.strip() for line in text.splitlines() if line.strip()]
        if len(rules) != policy.get("rule_count") or any(line.startswith("#") for line in rules):
            raise R7S1RunnerError("git_repository_attributes_rule_count_mismatch")
        patterns: list[str] = []
        for ordinal, rule in enumerate(rules, start=1):
            tokens = rule.split()
            if len(tokens) != 3 or tokens[1:] != list(policy.get("attribute_tokens", [])):
                raise R7S1RunnerError(f"git_repository_attributes_rule_not_allowlisted:{ordinal}")
            pattern = tokens[0]
            if not pattern or pattern.startswith(("!", "[attr]")):
                raise R7S1RunnerError(
                    f"git_repository_attributes_pattern_not_allowlisted:{ordinal}"
                )
            patterns.append(pattern)
        pattern_sha256 = [hashlib.sha256(item.encode("utf-8")).hexdigest() for item in patterns]
        if pattern_sha256 != list(policy.get("pattern_sha256", [])):
            raise R7S1RunnerError("git_repository_attributes_pattern_identity_mismatch")

        runtime = _mapping(self.manifest.get("runtime"), "manifest_runtime")
        if set(runtime) != set(RUNTIME_COMPONENTS):
            raise R7S1RunnerError("runtime_component_role_set_mismatch")
        for role in RUNTIME_COMPONENTS:
            component = _mapping(runtime.get(role), f"runtime_{role}")
            path = _absolute_lexical_path(str(component.get("path", "")))
            _assert_no_reparse_ancestors(path, label=f"runtime_{role}_attributes_scope")
            try:
                path.relative_to(project_root)
            except ValueError as exc:
                raise R7S1RunnerError(f"runtime_{role}_outside_attributes_root") from exc
            candidate = path.parent
            while candidate != project_root:
                nested_attributes = candidate / ".gitattributes"
                _assert_no_reparse_ancestors(
                    nested_attributes, label=f"runtime_{role}_nested_attributes"
                )
                if nested_attributes.exists():
                    raise R7S1RunnerError(f"runtime_{role}_nested_attributes_must_be_absent")
                candidate = candidate.parent
        return {
            "schema": GIT_ATTRIBUTES_READBACK_SCHEMA,
            "status": "verified",
            "captured_at": self._captured_at(),
            "path": str(attributes_path),
            "sha256": measured_sha256,
            "bytes": len(payload),
            "rule_count": len(rules),
            "pattern_sha256": pattern_sha256,
            "attribute_tokens": list(policy["attribute_tokens"]),
            "forbidden_attributes_absent": True,
            "git_top_level_attributes_absent": True,
            "git_info_attributes_absent": True,
            "system_attributes_disabled": True,
            "policy_sha256": hashlib.sha256(canonical_json_bytes(dict(policy))).hexdigest(),
        }

    def _git_remote_cwd(self) -> Path:
        windows_tcb = _mapping(self.toolchain.get("windows_tcb"), "toolchain_windows_tcb")
        system32_path = Path(str(windows_tcb.get("system32_path", "")))
        if not system32_path.is_absolute():
            raise R7S1RunnerError("git_remote_cwd_windows_tcb_path_invalid")
        cwd = system32_path.parent.resolve()
        try:
            common = Path(os.path.commonpath((str(cwd), str(self.repository_root)))).resolve()
        except ValueError:
            common = None
        if common is not None and common == self.repository_root:
            raise R7S1RunnerError("git_remote_cwd_must_be_outside_repository")
        _assert_no_reparse_ancestors(cwd, label="git_remote_cwd")
        if not cwd.is_dir():
            raise R7S1RunnerError("git_remote_cwd_missing")
        return cwd

    def _git_command(self, *arguments: str) -> list[str]:
        return [
            self.git,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.autocrlf=true",
            "-C",
            str(self.repository_root),
            *arguments,
        ]

    @staticmethod
    def _runtime_hash_name(role: str) -> str:
        if role not in RUNTIME_COMPONENTS:
            raise R7S1RunnerError(f"runtime_component_role_invalid:{role}")
        return f"r7s1-git-runtime-{role}-head-worktree-binding"

    def _runtime_component_pin(self, role: str) -> tuple[Mapping[str, Any], Path, str]:
        runtime = _mapping(self.manifest.get("runtime"), "manifest_runtime")
        if set(runtime) != set(RUNTIME_COMPONENTS) or role not in RUNTIME_COMPONENTS:
            raise R7S1RunnerError("runtime_component_role_set_mismatch")
        component = _mapping(runtime.get(role), f"runtime_{role}")
        if set(component) != {
            "path",
            "sha256",
            "worktree_blob_oid",
            "head_blob_oid",
            "bytes",
        }:
            raise R7S1RunnerError(f"runtime_{role}_fields_mismatch")
        raw_path = Path(str(component.get("path", "")))
        if not raw_path.is_absolute():
            raise R7S1RunnerError(f"runtime_{role}_path_not_absolute")
        path = _absolute_lexical_path(raw_path)
        _assert_no_reparse_ancestors(path, label=f"runtime_{role}_path")
        try:
            relative = path.relative_to(self.repository_root).as_posix()
        except ValueError as exc:
            raise R7S1RunnerError(f"runtime_{role}_path_outside_repository") from exc
        if not relative or relative.startswith("../"):
            raise R7S1RunnerError(f"runtime_{role}_relative_path_invalid")
        return component, path, relative

    def _runtime_hash_object_command(self, role: str) -> list[str]:
        _component, path, relative = self._runtime_component_pin(role)
        return self._git_command("hash-object", f"--path={relative}", str(path))

    def _verify_runtime_worktree_pin(self, role: str) -> dict[str, Any]:
        component, path, relative = self._runtime_component_pin(role)
        _assert_no_reparse_ancestors(path, label=f"runtime_{role}_worktree")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise R7S1RunnerError(f"runtime_{role}_worktree_unreadable") from exc
        sha256 = hashlib.sha256(payload).hexdigest()
        worktree_blob_oid = hashlib.sha1(
            f"blob {len(payload)}\0".encode("ascii") + payload
        ).hexdigest()
        head_blob_oid = str(component.get("head_blob_oid", "")).lower()
        if (
            sha256 != str(component.get("sha256", "")).lower()
            or worktree_blob_oid != str(component.get("worktree_blob_oid", "")).lower()
            or isinstance(component.get("bytes"), bool)
            or component.get("bytes") != len(payload)
            or FULL_SHA1.fullmatch(head_blob_oid) is None
        ):
            raise R7S1RunnerError(f"runtime_{role}_worktree_pin_mismatch")
        return {
            "role": role,
            "path": str(path),
            "repository_relative_path": relative,
            "sha256": sha256,
            "bytes": len(payload),
            "worktree_blob_oid": worktree_blob_oid,
            "expected_head_blob_oid": head_blob_oid,
            "normalization_policy": {
                "core.autocrlf": "true",
                "attributes_rechecked": True,
                "external_filters_allowed": False,
            },
        }

    def _git_remote_command(self) -> list[str]:
        return [
            self.git,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "ls-remote",
            "--exit-code",
            CANONICAL_GIT_REMOTE_URL,
            f"refs/heads/{CANONICAL_BRANCH}",
        ]

    def _psql_command(
        self,
        *,
        role: str,
        query: str,
        field_separator: bool,
    ) -> list[str]:
        database = _mapping(self.expected.get("database"), "expected_database")
        instances = _mapping(database.get("instances"), "database_instances")
        spec = _mapping(instances.get(role), f"database_instance_{role}")
        psql = _mapping(self.toolchain.get("container_psql"), "toolchain_container_psql")
        if dict(
            _mapping(psql.get("execution_scope"), "toolchain_container_psql_execution_scope")
        ) != dict(DOCKER_CONTAINER_EXECUTION_SCOPE):
            raise R7S1RunnerError("toolchain_container_psql_execution_scope_mismatch")
        command = self._docker_command(
            "exec",
            str(spec["container_name"]),
            str(psql["realpath"]),
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            str(spec["user"]),
            "-d",
            str(spec["database"]),
            "-At",
            "-X",
        )
        if field_separator:
            command.extend(("-F", "|"))
        command.extend(("-c", query))
        return command

    @staticmethod
    def _argv_sha256(command: Sequence[str]) -> str:
        normalized = json.dumps(
            [str(part) for part in command],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(normalized).hexdigest()

    def _command_role(self, command: Sequence[str]) -> str:
        if not command or not isinstance(command[0], str):
            return "invalid"
        for role in ("docker", "docker_compose", "kubectl", "wsl", "powershell", "git"):
            if command[0] == getattr(self, role):
                return role
        return "untrusted_executable"

    def _expected_read_only_command(self, name: str) -> tuple[str, ...] | None:
        git_commands = {
            "r7s1-git-branch-readback": self._git_command(
                "symbolic-ref", "--quiet", "--short", "HEAD"
            ),
            "r7s1-git-local-revision-readback": self._git_command("rev-parse", "HEAD"),
            "r7s1-git-origin-revision-readback": self._git_command(
                "rev-parse", f"refs/remotes/origin/{CANONICAL_BRANCH}"
            ),
            "r7s1-git-remote-revision-readback": self._git_remote_command(),
            "r7s1-git-tree-readback": self._git_command("rev-parse", "HEAD^{tree}"),
            "r7s1-git-tracked-readback": self._git_command(
                "status", "--porcelain=v1", "--untracked-files=no"
            ),
            "r7s1-git-untracked-readback": self._git_command(
                "-c",
                "core.quotepath=false",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ),
            **{
                self._runtime_hash_name(role): self._runtime_hash_object_command(role)
                for role in RUNTIME_COMPONENTS
            },
        }
        if name in git_commands:
            return tuple(git_commands[name])
        if name == "r7s1-docker-engine-readback":
            return tuple(self._docker_command("version", "--format", "{{json .Server}}"))

        kubectl_static = {
            "r7s1-kubernetes-node-readback": ("get", "nodes", "-o", "json"),
            "r7s1-device-plugin-readback": (
                "-n",
                "kube-system",
                "get",
                "daemonset/nvidia-device-plugin-daemonset",
                "-o",
                "json",
            ),
            "r7s1-b0-deployment-readback": (
                "-n",
                "evm-production",
                "get",
                "deployment/evm-b0-production",
                "-o",
                "json",
            ),
            "r7s1-active-jobs-readback": ("get", "jobs", "-A", "-o", "json"),
            "r7s1-historical-failed-owner-job-readback": (
                "get",
                "jobs",
                "-A",
                "-o",
                "json",
            ),
            "r7s1-final-active-jobs-readback": ("get", "jobs", "-A", "-o", "json"),
            "r7s1-historical-failed-pod-classification": (
                "get",
                "pods",
                "-A",
                "--field-selector=status.phase=Failed",
                "-o",
                "json",
            ),
        }
        if name in kubectl_static:
            return tuple(self._kubectl_command(*kubectl_static[name]))
        match = re.fullmatch(r"r7s1-kubernetes-(livez|readyz)-confirmation-([1-9][0-9]*)", name)
        if match:
            kubernetes = _mapping(self.expected.get("kubernetes"), "expected_kubernetes")
            index = int(match.group(2))
            if 1 <= index <= int(kubernetes.get("health_confirmation_samples", 0)):
                return tuple(self._kubectl_command("get", f"--raw=/{match.group(1)}"))
            return None
        match = re.fullmatch(r"r7s1-x1-kubernetes-residue-([1-9][0-9]*)", name)
        if match:
            selectors = _sequence(
                self.expected.get("x1_kubernetes_selectors"), "x1_kubernetes_selectors"
            )
            index = int(match.group(1))
            if 1 <= index <= len(selectors):
                return tuple(
                    self._kubectl_command(
                        "get", "all", "-A", "-l", str(selectors[index - 1]), "-o", "json"
                    )
                )
            return None

        compose = _mapping(self.expected.get("compose"), "expected_compose")
        if name == "r7s1-compose-ps-initial":
            return tuple(self._compose_command("ps", "-a", "--format", "json"))
        match = re.fullmatch(r"r7s1-compose-stability-([0-9]{3})", name)
        if match:
            stability = _mapping(compose.get("stability"), "compose_stability")
            index = int(match.group(1))
            if index < int(stability.get("samples", 0)):
                pins = _mapping(compose.get("service_pins"), "compose_service_pins")
                container_ids = [
                    str(_mapping(pins.get(str(service)), f"pin_{service}")["container_id"])
                    for service in _sequence(
                        compose.get("long_lived_services"), "compose_long_lived_services"
                    )
                ]
                return tuple(
                    self._docker_command(
                        "inspect", "--format", CONTAINER_STABILITY_FORMAT, *container_ids
                    )
                )
            return None

        database_roles = ("control_plane", "mlflow", "airflow")
        match = re.fullmatch(r"r7s1-database-(control_plane|mlflow|airflow)-readback", name)
        if match and match.group(1) in database_roles:
            return tuple(
                self._psql_command(
                    role=match.group(1), query=DATABASE_CONNECTION_QUERY, field_separator=True
                )
            )
        match = re.fullmatch(
            r"r7s1-database-(control_plane|mlflow|airflow)-migration-readback", name
        )
        if match and match.group(1) in database_roles:
            role = match.group(1)
            return tuple(
                self._psql_command(
                    role=role, query=DATABASE_MIGRATION_QUERIES[role], field_separator=False
                )
            )

        api = _mapping(self.expected.get("api"), "expected_api")
        if name == "r7s1-api-worker-container-identity":
            return tuple(
                self._docker_command(
                    "inspect",
                    "--format",
                    API_CONTAINER_IDENTITY_FORMAT,
                    str(api["api_container_name"]),
                    str(api["worker_container_name"]),
                )
            )
        if name == "r7s1-api-image-provenance":
            return tuple(
                self._docker_command(
                    "image",
                    "inspect",
                    "--format",
                    API_IMAGE_IDENTITY_FORMAT,
                    str(api["image_id"]),
                )
            )
        if name == "r7s1-windows-global-residual-readback":
            return (
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                self._windows_residual_script(),
            )
        if name == "r7s1-wsl-global-residual-readback":
            return tuple(self._wsl_protocol_launch_command(self._wsl_protocol()))
        if name == "r7s1-wsl-run-uuid-residual-scan":
            expected = getattr(self, "_pending_wsl_post_scan_command", None)
            return tuple(expected) if expected is not None else None
        if name in {"r7s1-queue-claims-readback", "r7s1-final-queue-claims-readback"}:
            return tuple(
                self._psql_command(
                    role="control_plane", query=QUEUE_READBACK_QUERY, field_separator=True
                )
            )
        if name == "r7s1-historical-control-plane-task-classification":
            return tuple(
                self._psql_command(
                    role="control_plane",
                    query=CONTROL_PLANE_HISTORY_QUERY,
                    field_separator=True,
                )
            )
        if name in {
            "r7s1-historical-control-plane-live-execution-links",
            "r7s1-final-control-plane-live-execution-links",
        }:
            return tuple(
                self._psql_command(
                    role="control_plane",
                    query=CONTROL_PLANE_EXECUTION_LINK_QUERY,
                    field_separator=True,
                )
            )
        if name == "r7s1-historical-mlflow-running-classification":
            return tuple(
                self._psql_command(role="mlflow", query=MLFLOW_HISTORY_QUERY, field_separator=True)
            )
        if name == "r7s1-x1-docker-residue-readback":
            return tuple(
                self._docker_command(
                    "ps",
                    "-a",
                    "--filter",
                    str(self.expected["x1_docker_name_filter"]),
                    "--format",
                    "{{json .}}",
                )
            )
        return None

    def _validate_read_only_command(self, command: Sequence[str], *, name: str) -> str:
        role = self._command_role(command)
        argv_sha256 = self._argv_sha256(command)
        expected = self._expected_read_only_command(name)
        if (
            not command
            or any(not isinstance(part, str) for part in command)
            or expected is None
            or tuple(command) != expected
            or role
            not in {
                "docker",
                "docker_compose",
                "kubectl",
                "wsl",
                "powershell",
                "git",
            }
        ):
            raise ReadOnlyCommandPolicyError(name=name, role=role, argv_sha256=argv_sha256)
        observed_executable = self._verified_host_executable(role)
        if observed_executable != command[0]:
            raise ReadOnlyCommandPolicyError(name=name, role=role, argv_sha256=argv_sha256)
        return role

    def _run(
        self,
        deadline: RestoreDeadline,
        command: Sequence[str],
        *,
        name: str,
    ) -> dict[str, Any]:
        execution_scope: dict[str, Any] | None = None
        git_config_readback: dict[str, Any] | None = None
        git_attributes_readback: dict[str, Any] | None = None
        runtime_worktree_readback: dict[str, Any] | None = None
        client_config_readback: dict[str, Any] | None = None
        client_command_policy: dict[str, Any] | None = None
        ambient_environment_policy: dict[str, Any] | None = None
        role = "unvalidated"
        child_launch_attempted = False
        try:
            role = self._validate_read_only_command(command, name=name)
            command_cwd = self.repository_root
            child_environment = None
            if role == "git":
                # The config and its absent worktree overlay are re-read before
                # every Git child, including commands that do not ordinarily
                # consult all config sections.  No Git child is used to validate
                # the config that controls Git child discovery.
                git_config_readback = self._verify_git_repository_config()
                if name != "r7s1-git-remote-revision-readback":
                    git_attributes_readback = self._verify_git_repository_attributes()
                for runtime_role in RUNTIME_COMPONENTS:
                    if name == self._runtime_hash_name(runtime_role):
                        runtime_worktree_readback = self._verify_runtime_worktree_pin(runtime_role)
                        break
                child_environment = self._git_environment()
                if name == "r7s1-git-remote-revision-readback":
                    command_cwd = self._git_remote_cwd()
            elif role in {"docker", "docker_compose"}:
                client_config_readback = self._verify_docker_client_config()
                docker_policy = self._dynamic_docker_policy(
                    Path(str(self.toolchain["docker_client_config"]["path"]))
                )
                child_environment = self._docker_environment(docker_policy)
                client_command_policy = {
                    "scope": "local_docker_engine_read_only",
                    "registry_operations_allowed": False,
                    "credential_store_invocation_count": 0,
                    "config_rechecked_before_child": True,
                    "environment_policy_sha256": hashlib.sha256(
                        canonical_json_bytes(docker_policy["child_environment"])
                    ).hexdigest(),
                }
                if role == "docker":
                    subcommand_index = 1 + len(docker_policy["docker_global_arguments"])
                    if command[subcommand_index] == "exec":
                        execution_scope = dict(DOCKER_CONTAINER_EXECUTION_SCOPE)
            elif role == "kubectl":
                client_config_readback = self._verify_kubernetes_client_config()
                kubernetes_policy = self._dynamic_kubernetes_policy(
                    Path(str(self.toolchain["kubernetes_client_config"]["path"]))
                )
                child_environment = self._kubernetes_environment(kubernetes_policy)
                client_command_policy = {
                    "scope": "pinned_kubernetes_api_read_only",
                    "multiple_config_merge_allowed": False,
                    "external_auth_helper_allowed": False,
                    "config_rechecked_before_child": True,
                    "environment_policy_sha256": hashlib.sha256(
                        canonical_json_bytes(kubernetes_policy["child_environment"])
                    ).hexdigest(),
                }
            elif role == "powershell":
                child_environment = self._powershell_environment()
                ambient_environment_policy = {
                    "scope": "windows_powershell_read_only_residual_scan",
                    "inherited_environment": False,
                    "profile_loading_allowed": False,
                    "system_module_path_only": True,
                    "module_qualified_commands": True,
                    "os_module_distribution_tcb": True,
                }
            elif role == "wsl":
                child_environment = self._wsl_environment()
                ambient_environment_policy = {
                    "scope": "wsl_uuid_process_group_residual_scan",
                    "inherited_environment": False,
                    "wslenv_present": False,
                    "exact_distro_and_argv": True,
                    "wsl_registration_kernel_rootfs_tcb": True,
                }
            deadline.assert_can_launch(self.launch_budget_seconds)
            child_launch_attempted = True
            outcome = self.runner.run(
                command,
                name=name,
                cwd=command_cwd,
                env=child_environment,
            )
        except Exception as exc:
            typed = getattr(exc, "to_dict", None)
            evidence = typed() if callable(typed) else None
            if not isinstance(evidence, Mapping):
                evidence = {
                    "name": name,
                    "command": list(command),
                    "runner_exception": f"{type(exc).__name__}:{exc}",
                    "child_created": None if child_launch_attempted else False,
                    "forced_termination_attempts": 0,
                }
            if role == "git":
                evidence = self._sanitize_git_evidence(evidence)
            residual = tuple(sorted({int(pid) for pid in evidence.get("residual_pids", ()) or ()}))
            child_created = evidence.get("child_created")
            residual_status = (
                "present" if residual else "not_created" if child_created is False else "unknown"
            )
            merged = dict(evidence)
            merged.update({"safe_for_followup": False, "residual_status": residual_status})
            if execution_scope is not None:
                merged["execution_scope"] = execution_scope
            if git_config_readback is not None:
                merged["git_repository_config"] = git_config_readback
            if git_attributes_readback is not None:
                merged["git_repository_attributes"] = git_attributes_readback
            if runtime_worktree_readback is not None:
                merged["runtime_worktree_readback"] = runtime_worktree_readback
            if client_config_readback is not None:
                merged["client_configuration"] = client_config_readback
            if client_command_policy is not None:
                merged["client_command_policy"] = client_command_policy
            if ambient_environment_policy is not None:
                merged["ambient_environment_policy"] = ambient_environment_policy
            failure = {
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
            if execution_scope is not None:
                failure["execution_scope"] = execution_scope
            if git_config_readback is not None:
                failure["git_repository_config"] = git_config_readback
            if git_attributes_readback is not None:
                failure["git_repository_attributes"] = git_attributes_readback
            if runtime_worktree_readback is not None:
                failure["runtime_worktree_readback"] = runtime_worktree_readback
            if client_config_readback is not None:
                failure["client_configuration"] = client_config_readback
            if client_command_policy is not None:
                failure["client_command_policy"] = client_command_policy
            if ambient_environment_policy is not None:
                failure["ambient_environment_policy"] = ambient_environment_policy
            return failure

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
        process_evidence = outcome.to_dict()
        stdout = str(getattr(outcome, "stdout", ""))
        stderr = str(getattr(outcome, "stderr", ""))
        if role == "git":
            process_evidence = self._sanitize_git_evidence(process_evidence)
            stdout = self._sanitize_git_evidence(stdout)
            stderr = self._sanitize_git_evidence(stderr)
            assert isinstance(stdout, str) and isinstance(stderr, str)
        if execution_scope is not None:
            process_evidence["execution_scope"] = execution_scope
        if git_config_readback is not None:
            process_evidence["git_repository_config"] = git_config_readback
        if git_attributes_readback is not None:
            process_evidence["git_repository_attributes"] = git_attributes_readback
        if runtime_worktree_readback is not None:
            process_evidence["runtime_worktree_readback"] = runtime_worktree_readback
        if client_config_readback is not None:
            process_evidence["client_configuration"] = client_config_readback
        if client_command_policy is not None:
            process_evidence["client_command_policy"] = client_command_policy
        if ambient_environment_policy is not None:
            process_evidence["ambient_environment_policy"] = ambient_environment_policy
        result = {
            "passed": passed,
            "last_error": None
            if passed
            else (
                f"{name}:return_code={return_code}:timed_out={timed_out}:"
                f"cancelled={cancelled}:residual={list(residual)}:"
                f"{stderr[-1000:]}"
            ),
            "residual_pids": list(residual),
            "residual_status": "present" if residual else "zero",
            "residual_process_zero": not uncertain and not residual,
            "manual_intervention_required": uncertain,
            "timeout_manual_latch": timed_out or cancelled,
            "process_evidence": process_evidence,
            "stdout": stdout,
            "stderr": stderr,
        }
        if execution_scope is not None:
            result["execution_scope"] = execution_scope
        if git_config_readback is not None:
            result["git_repository_config"] = git_config_readback
        if git_attributes_readback is not None:
            result["git_repository_attributes"] = git_attributes_readback
        if runtime_worktree_readback is not None:
            result["runtime_worktree_readback"] = runtime_worktree_readback
        if client_config_readback is not None:
            result["client_configuration"] = client_config_readback
        if client_command_policy is not None:
            result["client_command_policy"] = client_command_policy
        if ambient_environment_policy is not None:
            result["ambient_environment_policy"] = ambient_environment_policy
        return result

    def _repository_identity(self, deadline: RestoreDeadline) -> dict[str, Any]:
        commands = (
            (
                "r7s1-git-branch-readback",
                self._git_command("symbolic-ref", "--quiet", "--short", "HEAD"),
            ),
            ("r7s1-git-local-revision-readback", self._git_command("rev-parse", "HEAD")),
            (
                "r7s1-git-origin-revision-readback",
                self._git_command("rev-parse", f"refs/remotes/origin/{CANONICAL_BRANCH}"),
            ),
            (
                "r7s1-git-remote-revision-readback",
                self._git_remote_command(),
            ),
            ("r7s1-git-tree-readback", self._git_command("rev-parse", "HEAD^{tree}")),
            (
                "r7s1-git-tracked-readback",
                self._git_command("status", "--porcelain=v1", "--untracked-files=no"),
            ),
            (
                "r7s1-git-untracked-readback",
                self._git_command(
                    "-c",
                    "core.quotepath=false",
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                ),
            ),
            *(
                (self._runtime_hash_name(role), self._runtime_hash_object_command(role))
                for role in RUNTIME_COMPONENTS
            ),
        )
        results: list[Mapping[str, Any]] = []
        by_name: dict[str, Mapping[str, Any]] = {}
        config_checks: list[dict[str, Any]] = []
        attributes_checks: list[dict[str, Any]] = []
        for name, command in commands:
            result = self._run(deadline, command, name=name)
            results.append(result)
            by_name[name] = result
            config_readback = result.get("git_repository_config")
            if isinstance(config_readback, Mapping):
                config_checks.append({"before_command": name, **dict(config_readback)})
            attributes_readback = result.get("git_repository_attributes")
            if isinstance(attributes_readback, Mapping):
                attributes_checks.append({"before_command": name, **dict(attributes_readback)})
            if not result.get("passed"):
                failure = self._failed_process_chain(
                    results,
                    last_error=str(result.get("last_error") or "contained_git_readback_failed"),
                    invariant_names=("canonical_repository_identity_exact",),
                )
                failure["automatic_retries"] = 0
                failure["command_launches"] = {
                    item[0]: int(item[0] in by_name) for item in commands
                }
                failure["git_repository_config_checks"] = config_checks
                failure["git_repository_attributes_checks"] = attributes_checks
                return failure

        if len(config_checks) != len(commands) or any(
            {
                key: value
                for key, value in check.items()
                if key not in {"before_command", "captured_at"}
            }
            != {
                key: value
                for key, value in config_checks[0].items()
                if key not in {"before_command", "captured_at"}
            }
            for check in config_checks[1:]
        ):
            failure = self._failed_process_chain(
                results,
                last_error="git_repository_config_recheck_evidence_mismatch",
                invariant_names=("canonical_repository_identity_exact",),
            )
            failure["automatic_retries"] = 0
            failure["git_repository_config_checks"] = config_checks
            return failure

        expected_attributes_check_count = len(commands) - 1
        if len(attributes_checks) != expected_attributes_check_count or any(
            {
                key: value
                for key, value in check.items()
                if key not in {"before_command", "captured_at"}
            }
            != {
                key: value
                for key, value in attributes_checks[0].items()
                if key not in {"before_command", "captured_at"}
            }
            for check in attributes_checks[1:]
        ):
            failure = self._failed_process_chain(
                results,
                last_error="git_repository_attributes_recheck_evidence_mismatch",
                invariant_names=("canonical_repository_identity_exact",),
            )
            failure["automatic_retries"] = 0
            failure["git_repository_config_checks"] = config_checks
            failure["git_repository_attributes_checks"] = attributes_checks
            return failure

        branch = str(by_name["r7s1-git-branch-readback"].get("stdout", "")).strip()
        revision = (
            str(by_name["r7s1-git-local-revision-readback"].get("stdout", "")).strip().lower()
        )
        origin_revision = (
            str(by_name["r7s1-git-origin-revision-readback"].get("stdout", "")).strip().lower()
        )
        tree = str(by_name["r7s1-git-tree-readback"].get("stdout", "")).strip().lower()
        tracked_output = str(by_name["r7s1-git-tracked-readback"].get("stdout", ""))
        remote_lines = [
            line
            for line in str(
                by_name["r7s1-git-remote-revision-readback"].get("stdout", "")
            ).splitlines()
            if line
        ]
        remote_revision = ""
        if len(remote_lines) == 1:
            remote_fields = remote_lines[0].split("\t")
            if len(remote_fields) == 2 and remote_fields[1] == f"refs/heads/{CANONICAL_BRANCH}":
                remote_revision = remote_fields[0].lower()

        raw_untracked = str(by_name["r7s1-git-untracked-readback"].get("stdout", ""))
        parts = raw_untracked.split("\0")
        if parts and parts[-1] == "":
            parts.pop()
        untracked_valid = bool(
            "\ufffd" not in raw_untracked
            and all(record.startswith("?? ") and len(record) > 3 for record in parts)
        )
        untracked_paths = [record[3:] for record in parts] if untracked_valid else []
        untracked_valid = untracked_valid and len(untracked_paths) == len(set(untracked_paths))
        ordered_paths = sorted(untracked_paths) if untracked_valid else []
        untracked_digest = hashlib.sha256()
        for path in ordered_paths:
            untracked_digest.update(path.encode("utf-8", errors="strict"))
            untracked_digest.update(b"\0")
        repository = _mapping(self.manifest.get("repository"), "manifest_repository")
        expected_revision = str(self.manifest.get("canonical_revision", "")).lower()
        expected_tree = str(self.manifest.get("canonical_tree", "")).lower()
        expected_untracked = int(repository.get("preserved_untracked_count", -1))
        expected_untracked_digest = str(repository.get("untracked_path_set_sha256", "")).lower()
        runtime_bindings: list[dict[str, Any]] = []
        runtime_bindings_exact = True
        runtime = _mapping(self.manifest.get("runtime"), "manifest_runtime")
        for role in RUNTIME_COMPONENTS:
            name = self._runtime_hash_name(role)
            component = _mapping(runtime.get(role), f"runtime_{role}")
            result = by_name[name]
            output_lines = str(result.get("stdout", "")).splitlines()
            measured_head_blob_oid = (
                output_lines[0].strip().lower() if len(output_lines) == 1 else ""
            )
            worktree_readback = result.get("runtime_worktree_readback")
            binding_exact = bool(
                FULL_SHA1.fullmatch(measured_head_blob_oid) is not None
                and measured_head_blob_oid == str(component.get("head_blob_oid", "")).lower()
                and isinstance(worktree_readback, Mapping)
                and worktree_readback.get("role") == role
                and worktree_readback.get("expected_head_blob_oid") == measured_head_blob_oid
                and worktree_readback.get("worktree_blob_oid")
                == str(component.get("worktree_blob_oid", "")).lower()
                and worktree_readback.get("sha256") == str(component.get("sha256", "")).lower()
                and worktree_readback.get("bytes") == component.get("bytes")
            )
            runtime_bindings_exact = runtime_bindings_exact and binding_exact
            runtime_bindings.append(
                {
                    "role": role,
                    "measured_head_blob_oid": measured_head_blob_oid or None,
                    "expected_head_blob_oid": str(component.get("head_blob_oid", "")).lower(),
                    "worktree_readback": dict(worktree_readback)
                    if isinstance(worktree_readback, Mapping)
                    else None,
                    "exact": binding_exact,
                }
            )
        exact = bool(
            branch == CANONICAL_BRANCH
            and revision == expected_revision
            and origin_revision == expected_revision
            and remote_revision == expected_revision
            and tree == expected_tree
            and tracked_output == ""
            and untracked_valid
            and len(ordered_paths) == expected_untracked
            and untracked_digest.hexdigest() == expected_untracked_digest
            and runtime_bindings_exact
            and all(FULL_SHA1.fullmatch(value) is not None for value in (revision, tree))
        )
        residual_pids = sorted(
            {int(pid) for result in results for pid in result.get("residual_pids", ())}
        )
        manual = bool(
            residual_pids or any(result.get("manual_intervention_required") for result in results)
        )
        return {
            "passed": exact and not manual,
            "last_error": None if exact and not manual else "contained_git_identity_mismatch",
            "manual_intervention_required": manual,
            "residual_pids": residual_pids,
            "invariants": {"canonical_repository_identity_exact": exact and not manual},
            "identity": {
                "measurement": "windows_job_object_contained_git_readback",
                "branch": branch,
                "revision": revision,
                "origin_revision": origin_revision,
                "remote_revision": remote_revision,
                "tree": tree,
                "tracked": 0 if tracked_output == "" else "nonzero",
                "untracked": len(ordered_paths) if untracked_valid else "invalid_utf8_or_duplicate",
                "untracked_path_set_sha256": untracked_digest.hexdigest()
                if untracked_valid
                else None,
                "git_repository_config_checks": config_checks,
                "git_repository_attributes_checks": attributes_checks,
                "runtime_head_worktree_bindings": runtime_bindings,
            },
            "process_evidence": [result["process_evidence"] for result in results],
            "command_launches": {name: 1 for name, _command in commands},
            "automatic_retries": 0,
        }

    def docker_engine(self, deadline: RestoreDeadline) -> dict[str, Any]:
        repository = self._repository_identity(deadline)
        if not repository.get("passed"):
            repository["invariants"] = {
                **dict(repository.get("invariants", {})),
                "docker_engine": False,
            }
            repository["process_chain_stopped"] = True
            return repository
        result = self._run(
            deadline,
            self._docker_command("version", "--format", "{{json .Server}}"),
            name="r7s1-docker-engine-readback",
        )
        server: Any = None
        if result["passed"]:
            try:
                server = json.loads(str(result["stdout"]))
            except json.JSONDecodeError:
                result["passed"] = False
                result["last_error"] = "docker_server_json_invalid"
        result["server"] = server
        result["invariants"] = {
            "canonical_repository_identity_exact": True,
            "docker_engine": bool(result["passed"]),
        }
        result["repository_identity"] = repository.get("identity")
        result["repository_process_evidence"] = repository.get("process_evidence")
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
        operation_deadline = started + min(
            float(self.contract.kubectl_timeout_seconds),
            float(deadline.remaining_seconds),
        )
        request_body = None
        headers: dict[str, str] = {}
        if body is not None:
            request_body = json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=request_body,
            headers=headers,
            method=method,
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        response: Any | None = None
        http_error = False
        try:
            try:
                open_remaining = min(
                    operation_deadline - self.clock(),
                    deadline.remaining_seconds,
                )
                if open_remaining <= 0:
                    raise _HTTPResponsePolicyError("http_total_deadline_exceeded")
                response = opener.open(
                    request,
                    timeout=open_remaining,
                )
            except urllib.error.HTTPError as exc:
                response = exc
                if 300 <= int(exc.code) < 400:
                    return self._http_result(
                        started=started,
                        method=method,
                        url=url,
                        status=int(exc.code),
                        error="http_redirect_forbidden",
                    )
                http_error = True

            final_url = str(response.geturl())
            if final_url != url:
                raise _HTTPResponsePolicyError("http_final_url_mismatch")
            status = int(response.status if not http_error else response.code)
            body_bytes = self._read_bounded_http_body(
                response,
                deadline=deadline,
                operation_deadline=operation_deadline,
            )
            try:
                body_text = body_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _HTTPResponsePolicyError("http_response_utf8_invalid") from exc
            try:
                payload: Any = json.loads(body_text)
            except json.JSONDecodeError:
                payload = None
            return self._http_result(
                started=started,
                method=method,
                url=url,
                status=status,
                body=payload,
                body_text=body_text,
            )
        except (_HTTPResponsePolicyError, OSError, urllib.error.URLError) as exc:
            return self._http_result(
                started=started,
                method=method,
                url=url,
                status=None,
                error=f"{type(exc).__name__}:{exc}",
            )
        finally:
            if response is not None:
                response.close()

    def _http_result(
        self,
        *,
        started: float,
        method: str,
        url: str,
        status: int | None,
        body: Any = None,
        body_text: str = "",
        error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "url": url,
            "method": method,
            "status": status,
            "body": body,
            "body_text": body_text,
            "duration_seconds": max(0.0, self.clock() - started),
            "error": error,
        }

    def _read_bounded_http_body(
        self,
        response: Any,
        *,
        deadline: RestoreDeadline,
        operation_deadline: float,
    ) -> bytes:
        headers = response.headers
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            content_lengths = list(get_all("Content-Length") or [])
        else:
            content_length = headers.get("Content-Length")
            content_lengths = [] if content_length is None else [content_length]
        if len(content_lengths) > 1:
            raise _HTTPResponsePolicyError("http_content_length_ambiguous")
        expected_bytes: int | None = None
        if content_lengths:
            encoded_length = str(content_lengths[0]).strip()
            if re.fullmatch(r"(?:0|[1-9][0-9]*)", encoded_length) is None:
                raise _HTTPResponsePolicyError("http_content_length_invalid")
            expected_bytes = int(encoded_length)
            if expected_bytes > HTTP_RESPONSE_MAX_BYTES:
                raise _HTTPResponsePolicyError("http_response_too_large")

        read_one_chunk = getattr(response, "read1", None)
        if not callable(read_one_chunk):
            raise _HTTPResponsePolicyError("http_incremental_reader_unavailable")
        response_socket = self._http_response_socket(response)
        body = bytearray()
        while True:
            remaining = min(
                operation_deadline - self.clock(),
                deadline.remaining_seconds,
            )
            if remaining <= 0:
                raise _HTTPResponsePolicyError("http_total_deadline_exceeded")
            response_socket.settimeout(remaining)
            read_size = min(
                HTTP_READ_CHUNK_BYTES,
                HTTP_RESPONSE_MAX_BYTES + 1 - len(body),
            )
            chunk = read_one_chunk(read_size)
            if self.clock() > operation_deadline or deadline.remaining_seconds <= 0:
                raise _HTTPResponsePolicyError("http_total_deadline_exceeded")
            if not isinstance(chunk, bytes):
                raise _HTTPResponsePolicyError("http_response_chunk_bytes_required")
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > HTTP_RESPONSE_MAX_BYTES:
                raise _HTTPResponsePolicyError("http_response_too_large")
        if expected_bytes is not None and len(body) != expected_bytes:
            raise _HTTPResponsePolicyError("http_response_truncated")
        return bytes(body)

    @staticmethod
    def _http_response_socket(response: Any) -> Any:
        for attributes in (
            ("fp", "raw", "_sock"),
            ("fp", "fp", "raw", "_sock"),
            ("fp", "_sock"),
        ):
            candidate = response
            for attribute in attributes:
                candidate = getattr(candidate, attribute, None)
                if candidate is None:
                    break
            if callable(getattr(candidate, "settimeout", None)):
                return candidate
        raise _HTTPResponsePolicyError("http_response_timeout_control_unavailable")

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
            name="r7s1-kubernetes-node-readback",
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
            name="r7s1-device-plugin-readback",
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
            name="r7s1-b0-deployment-readback",
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
        config_path = Path(str(compose["config_path"])).resolve()
        return [
            self.docker_compose,
            "-p",
            str(compose["project_name"]),
            "-f",
            str(config_path),
            "--project-directory",
            str(config_path.parent),
            *arguments,
        ]

    def _compose_config_identity_exact(self, compose: Mapping[str, Any]) -> bool:
        """Bind Compose to the pinned project file, not the Git top-level directory."""

        runtime = _mapping(self.manifest.get("runtime"), "manifest_runtime")
        runtime_config = _mapping(runtime.get("docker_compose"), "manifest_runtime_docker_compose")
        config_path = Path(str(compose["config_path"])).resolve()
        pinned_path = Path(str(runtime_config["path"])).resolve()
        try:
            measured_sha256 = sha256_file(config_path)
        except OSError:
            return False
        return bool(
            _resolved_equal(config_path, pinned_path)
            and measured_sha256 == str(compose["config_sha256"]).lower()
            and measured_sha256 == str(runtime_config["sha256"]).lower()
        )

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
        result = self._run(
            deadline,
            self._docker_command("inspect", "--format", CONTAINER_STABILITY_FORMAT, *container_ids),
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
            query = DATABASE_CONNECTION_QUERY
            result = self._run(
                deadline,
                self._psql_command(role=role, query=query, field_separator=True),
                name=f"r7s1-database-{role}-readback",
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
                DATABASE_MIGRATION_QUERIES["control_plane"],
                tuple(str(item) for item in database["control_plane_schema_versions"]),
            ),
            (
                "mlflow",
                DATABASE_MIGRATION_QUERIES["mlflow"],
                (str(database["mlflow_migration_head"]),),
            ),
            (
                "airflow",
                DATABASE_MIGRATION_QUERIES["airflow"],
                (str(database["airflow_migration_head"]),),
            ),
        )
        migration_invariants: dict[str, bool] = {}
        for role, query, expected_versions in migration_specs:
            result = self._run(
                deadline,
                self._psql_command(role=role, query=query, field_separator=False),
                name=f"r7s1-database-{role}-migration-readback",
            )
            process_results.append(result)
            if result.get("manual_intervention_required") or result.get("residual_pids"):
                return {
                    "passed": False,
                    "manual_intervention_required": True,
                    "last_error": result.get("last_error")
                    or f"r7s1-database-{role}-migration-process_uncertain",
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

        try:
            config_exact = (
                self._compose_config_identity_exact(compose)
                and str(compose["project_name"]) == "enterprise-vision-mlops"
            )
        except (KeyError, TypeError, ValueError, R7S1RunnerError):
            config_exact = False

        ps_result, rows = self._compose_ps(deadline, name="r7s1-compose-ps-initial")
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
            deadline, container_ids, name="r7s1-compose-stability-000"
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
                name=f"r7s1-compose-stability-{index:03d}",
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
                name=f"r7s1-kubernetes-livez-confirmation-{index + 1}",
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
                name=f"r7s1-kubernetes-readyz-confirmation-{index + 1}",
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
            return any(R7S1ProbeSet._contains_scalar(item, expected) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(R7S1ProbeSet._contains_scalar(item, expected) for item in value)
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
            self._docker_command(
                "inspect",
                "--format",
                API_CONTAINER_IDENTITY_FORMAT,
                *container_names,
            ),
            name="r7s1-api-worker-container-identity",
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
            self._docker_command(
                "image",
                "inspect",
                "--format",
                API_IMAGE_IDENTITY_FORMAT,
                str(api["image_id"]),
            ),
            name="r7s1-api-image-provenance",
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

    @staticmethod
    def _required_text(value: Any, label: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{label}_required")
        return text

    @classmethod
    def _failed_job_condition_reason(cls, job: Mapping[str, Any]) -> str:
        status = job.get("status")
        if not isinstance(status, Mapping):
            raise ValueError("failed_owner_job_status_mapping_required")
        conditions = status.get("conditions")
        if not isinstance(conditions, list):
            raise ValueError("failed_owner_job_conditions_required")
        failed = [
            condition
            for condition in conditions
            if isinstance(condition, Mapping)
            and condition.get("type") == "Failed"
            and condition.get("status") == "True"
        ]
        if len(failed) != 1:
            raise ValueError("failed_owner_job_condition_exactly_once_required")
        return cls._required_text(failed[0].get("reason"), "failed_owner_job_condition_reason")

    @classmethod
    def _job_owner_index(
        cls, items: Sequence[Any]
    ) -> dict[tuple[str, str, str], Mapping[str, Any]]:
        indexed: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        for raw in items:
            if not isinstance(raw, Mapping):
                raise ValueError("owner_job_mapping_required")
            metadata = raw.get("metadata")
            if not isinstance(metadata, Mapping):
                raise ValueError("owner_job_metadata_mapping_required")
            key = (
                cls._required_text(metadata.get("namespace"), "owner_job_namespace"),
                cls._required_text(metadata.get("name"), "owner_job_name"),
                cls._required_text(metadata.get("uid"), "owner_job_uid"),
            )
            if key in indexed:
                raise ValueError("owner_job_identity_duplicate")
            indexed[key] = raw
        return indexed

    @classmethod
    def _failed_pod_identity(
        cls,
        item: Mapping[str, Any],
        owner_jobs: Mapping[tuple[str, str, str], Mapping[str, Any]],
    ) -> dict[str, Any]:
        metadata = item.get("metadata")
        status = item.get("status")
        if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
            raise ValueError("failed_pod_metadata_status_mapping_required")
        uid = cls._required_text(metadata.get("uid"), "failed_pod_uid")
        name = cls._required_text(metadata.get("name"), "failed_pod_name")
        namespace = cls._required_text(metadata.get("namespace"), "failed_pod_namespace")
        owners = metadata.get("ownerReferences")
        if not isinstance(owners, list) or len(owners) != 1 or not isinstance(owners[0], Mapping):
            raise ValueError("failed_pod_owner_exactly_once_required")
        owner = owners[0]
        owner_uid = cls._required_text(owner.get("uid"), "failed_pod_owner_uid")
        owner_name = cls._required_text(owner.get("name"), "failed_pod_owner_name")
        owner_kind = cls._required_text(owner.get("kind"), "failed_pod_owner_kind")
        if owner.get("controller") is not True:
            raise ValueError("failed_pod_owner_controller_true_required")

        if (
            owner_kind == "ReplicaSet"
            and namespace == "evm-production"
            and name.startswith("evm-b0-production-")
            and owner_name.startswith("evm-b0-production-")
        ):
            reason = cls._required_text(status.get("reason"), "failed_b0_pod_status_reason")
            reason_source = "pod.status.reason"
        elif owner_kind == "Job":
            owner_job = owner_jobs.get((namespace, owner_name, owner_uid))
            if owner_job is None:
                raise ValueError("failed_pod_owner_job_identity_mismatch")
            reason = cls._failed_job_condition_reason(owner_job)
            reason_source = "owner_job.status.conditions[type=Failed].reason"
        else:
            raise ValueError("failed_pod_owner_kind_or_b0_identity_invalid")
        return {
            "uid": uid,
            "namespace": namespace,
            "name": name,
            "reason": reason,
            "reason_source": reason_source,
            "owner_uid": owner_uid,
            "owner_kind": owner_kind,
            "owner_name": owner_name,
            "owner_controller": True,
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

    def _mlflow_terminal_fencing_exact(
        self, records: Sequence[Mapping[str, Any]]
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Bind every factually RUNNING/open row to a separately verified decision.

        Stable age and zero live links are observations, not terminal authority.  The
        r7s1 core verifies the two snapshots, two link scans, decision ordering, SHA
        pins, exact identity, future-dispatch fence, and decision authority before
        exposing a record through ``find_verified_decision``.
        """

        decisions: list[dict[str, Any]] = []
        exact = self.validated_manifest.get("historical_go") is True
        for record in records:
            identity = record.get("identity")
            if not isinstance(identity, Mapping):
                return False, decisions
            observed_state = str(record.get("observed_state", ""))
            identity_observation_exact = str(identity.get("status", "")) == observed_state
            open_running = (
                observed_state == "RUNNING" and not str(identity.get("end_time", "")).strip()
            )
            decision = find_verified_decision(
                self.validated_manifest,
                "mlflow_running_rows",
                dict(identity),
            )
            accepted = bool(
                identity_observation_exact
                and open_running
                and (
                    isinstance(decision, Mapping)
                    and decision.get("source") == "mlflow_running_rows"
                    and decision.get("identity") == dict(identity)
                    and decision.get("decision") == "proven_terminal_fenced"
                    and decision.get("verified") is True
                )
            )
            decisions.append(
                {
                    "identity": dict(identity),
                    "observed_state": observed_state,
                    "end_time_empty": not bool(str(identity.get("end_time", "")).strip()),
                    "terminal_fencing_required": open_running,
                    "verified": accepted,
                    "decision": dict(decision) if isinstance(decision, Mapping) else None,
                }
            )
            exact = exact and accepted
        return bool(exact), decisions

    def _mlflow_attestation_live_exact(
        self,
        *,
        classification: Mapping[str, Any],
        payload: Mapping[str, Any],
        observed_records: Sequence[Mapping[str, Any]],
        file_sha_exact: bool,
        attestation_path: Path,
    ) -> bool:
        """Bind a pinned r7s1 MLflow attestation to this live readback.

        The external terminal/fencing decision is the sole authority for an
        open RUNNING row. A legacy per-record ``execution_proof`` may be retained
        only when its original deep proof contract also validates; it never
        substitutes for the separately verified terminal/fencing decision.
        """

        if set(payload) != {
            "source",
            "captured_at",
            "query_sha256",
            "counts",
            "classification",
            "records",
        }:
            return False
        if (
            payload.get("source") != "mlflow_running_rows"
            or payload.get("query_sha256") != HISTORICAL_QUERY_SHA256["mlflow_running_rows"]
            or not file_sha_exact
        ):
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
        expected_counts = {
            name: classification.get(name)
            for name in (
                "observed_count",
                "executing_count",
                "historical_count",
                "unproven_count",
            )
        }
        if dict(counts) != expected_counts:
            return False
        raw_records = payload.get("records")
        if not isinstance(raw_records, list) or len(raw_records) != len(observed_records):
            return False

        def identity_key(value: Mapping[str, Any]) -> str:
            return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))

        observed_by_identity: dict[str, Mapping[str, Any]] = {}
        for observed in observed_records:
            identity = observed.get("identity")
            if not isinstance(identity, Mapping) or set(identity) != {
                "run_id",
                "status",
                "lifecycle_stage",
                "start_time",
                "end_time",
            }:
                return False
            key = identity_key(identity)
            if key in observed_by_identity:
                return False
            if str(identity.get("status")) != str(observed.get("observed_state")):
                return False
            observed_by_identity[key] = observed

        matched: set[str] = set()
        legacy_modes: set[bool] = set()
        for raw in raw_records:
            if not isinstance(raw, Mapping):
                return False
            base_fields = {
                "identity",
                "observed_state",
                "classification",
            }
            fields = set(raw)
            if fields not in (base_fields, {*base_fields, "execution_proof"}):
                return False
            legacy_modes.add("execution_proof" in fields)
            identity = raw.get("identity")
            if not isinstance(identity, Mapping):
                return False
            key = identity_key(identity)
            observed = observed_by_identity.get(key)
            if key in matched or observed is None:
                return False
            if (
                raw.get("observed_state") != observed.get("observed_state")
                or raw.get("observed_state") != identity.get("status")
                or raw.get("classification") != "historical_nonexecuting"
            ):
                return False
            matched.add(key)
        if len(legacy_modes) > 1:
            return False
        base_exact = bool(
            matched == set(observed_by_identity)
            and payload.get("classification") == "historical_nonexecuting"
            and classification.get("classification") == "historical_nonexecuting"
            and counts["observed_count"] == len(matched)
            and counts["executing_count"] == 0
            and counts["historical_count"] == len(matched)
            and counts["unproven_count"] == 0
        )
        if not base_exact:
            return False
        if legacy_modes == {True}:
            return self._historical_attestation_exact(
                source="mlflow_running_rows",
                classification=classification,
                payload=payload,
                query=MLFLOW_HISTORY_QUERY,
                observed_records=observed_records,
                file_sha_exact=file_sha_exact,
                attestation_path=attestation_path,
            )
        return True

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
        script = self._windows_residual_script()
        result = self._run(
            deadline,
            [self.powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            name="r7s1-windows-global-residual-readback",
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
        protocol = self._wsl_protocol()
        launch = self._run(
            deadline,
            self._wsl_protocol_launch_command(protocol),
            name="r7s1-wsl-global-residual-readback",
        )
        empty_root: dict[str, Any] = {
            "pid": None,
            "ppid": None,
            "pgrp": None,
            "session": None,
            "start_time_ticks": None,
            "boot_id": None,
        }
        if not launch.get("passed"):
            launch_evidence = launch.get("process_evidence")
            failed = dict(launch)
            failed["process_evidence"] = {
                "schema": "s8-v4-x1-phase-b2-r7s1-wsl-process-scope/v1",
                "scope": "wsl_uuid_process_group",
                "run_uuid": protocol.run_uuid,
                "root": empty_root,
                "launch": launch_evidence,
                "post_scan": None,
                "post_scan_records": [],
                "linux_residual_zero": False,
                "forced_termination_attempts": int(
                    _mapping(launch_evidence, "wsl_launch_evidence").get(
                        "forced_termination_attempts", 0
                    )
                )
                if isinstance(launch_evidence, Mapping)
                else 0,
                "subsequent_probe_after_residual": 0,
            }
            failed["wsl_process_scope"] = "launch_failed_post_scan_forbidden"
            return failed, []

        parse_error: str | None = None
        root = dict(empty_root)
        rows: list[dict[str, Any]] = []
        try:
            decoded = json.loads(str(launch.get("stdout", "")).strip())
            if not isinstance(decoded, dict) or set(decoded) != {
                "schema",
                "run_uuid",
                "root",
                "residuals",
            }:
                raise ValueError("wsl_residual_envelope_invalid")
            if (
                decoded["schema"] != "s8-v4-x1-phase-b2-r7s1-wsl-global-residual-readback/v2"
                or decoded["run_uuid"] != protocol.run_uuid
            ):
                raise ValueError("wsl_residual_envelope_identity_mismatch")
            raw_root = decoded["root"]
            if not isinstance(raw_root, dict) or set(raw_root) != set(root):
                raise ValueError("wsl_residual_root_fields_invalid")
            for name in ("pid", "pgrp", "session", "start_time_ticks"):
                if (
                    isinstance(raw_root[name], bool)
                    or not isinstance(raw_root[name], int)
                    or raw_root[name] < 1
                ):
                    raise ValueError(f"wsl_residual_root_{name}_invalid")
            if (
                isinstance(raw_root["ppid"], bool)
                or not isinstance(raw_root["ppid"], int)
                or raw_root["ppid"] < 0
                or not isinstance(raw_root["boot_id"], str)
                or not raw_root["boot_id"]
            ):
                raise ValueError("wsl_residual_root_identity_invalid")
            root = dict(raw_root)
            raw_rows = decoded["residuals"]
            if not isinstance(raw_rows, list):
                raise ValueError("wsl_residual_rows_invalid")
            required_row_fields = {
                "pid",
                "ppid",
                "pgrp",
                "session",
                "start_time_ticks",
                "command_line_sha256",
            }
            for row in raw_rows:
                if not isinstance(row, dict) or set(row) != required_row_fields:
                    raise ValueError("wsl_residual_row_fields_invalid")
                for name in ("pid", "pgrp", "session", "start_time_ticks"):
                    if (
                        isinstance(row[name], bool)
                        or not isinstance(row[name], int)
                        or row[name] < 1
                    ):
                        raise ValueError(f"wsl_residual_row_{name}_invalid")
                if (
                    isinstance(row["ppid"], bool)
                    or not isinstance(row["ppid"], int)
                    or row["ppid"] < 0
                    or re.fullmatch(r"[0-9a-f]{64}", str(row["command_line_sha256"])) is None
                ):
                    raise ValueError("wsl_residual_row_identity_invalid")
                rows.append(dict(row))
            stable_keys = [(row["pid"], row["start_time_ticks"]) for row in rows]
            if stable_keys != sorted(set(stable_keys)):
                raise ValueError("wsl_residual_rows_order_or_identity_invalid")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            parse_error = f"wsl_residual_json_invalid:{exc}"

        scoped_protocol = WslResidualProtocol(
            protocol.run_uuid,
            root_process_group=root["pgrp"] if parse_error is None else None,
            root_start_time_ticks=root["start_time_ticks"] if parse_error is None else None,
            boot_id=root["boot_id"] if parse_error is None else None,
        )
        scan_command = tuple(self._wsl_protocol_scan_command(scoped_protocol))
        self._pending_wsl_post_scan_command = scan_command
        try:
            post_scan = self._run(
                deadline,
                scan_command,
                name="r7s1-wsl-run-uuid-residual-scan",
            )
        finally:
            self._pending_wsl_post_scan_command = None

        protocol_records: list[dict[str, Any]] = []
        scan_error: str | None = None
        if post_scan.get("passed"):
            try:
                records = WslResidualProtocol.parse_scan_json(
                    str(post_scan.get("stdout", "")).strip() or "[]"
                )
                protocol_records = [
                    {
                        "pid": item.pid,
                        "ppid": item.ppid,
                        "pgrp": item.pgrp,
                        "session": item.session,
                        "start_time_ticks": item.start_time_ticks,
                        "boot_id": item.boot_id,
                        "run_uuid_match": item.run_uuid_match,
                        "process_group_match": item.process_group_match,
                        "cmdline_sha256": item.cmdline_sha256,
                    }
                    for item in records
                ]
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                scan_error = f"wsl_protocol_residual_json_invalid:{exc}"
        else:
            scan_error = str(post_scan.get("last_error") or "wsl_protocol_post_scan_failed")

        launch_evidence = launch.get("process_evidence")
        scan_evidence = post_scan.get("process_evidence")
        windows_residual_pids = sorted(
            {
                int(pid)
                for item in (launch, post_scan)
                for pid in item.get("residual_pids", ()) or ()
            }
        )
        forced_termination_attempts = sum(
            int(_mapping(evidence, "wsl_process_evidence").get("forced_termination_attempts", 0))
            for evidence in (launch_evidence, scan_evidence)
            if isinstance(evidence, Mapping)
        )
        linux_residual_zero = not protocol_records
        manual = bool(
            windows_residual_pids
            or protocol_records
            or launch.get("manual_intervention_required")
            or post_scan.get("manual_intervention_required")
            or parse_error
            or scan_error
            or forced_termination_attempts
        )
        aggregate = {
            "passed": not manual,
            "last_error": None
            if not manual
            else parse_error
            or scan_error
            or f"wsl_protocol_residual_present:{len(protocol_records)}",
            "manual_intervention_required": manual,
            "residual_pids": windows_residual_pids,
            "residual_status": "present" if windows_residual_pids or protocol_records else "zero",
            "residual_process_zero": not manual,
            "process_evidence": {
                "schema": "s8-v4-x1-phase-b2-r7s1-wsl-process-scope/v1",
                "scope": "wsl_uuid_process_group",
                "run_uuid": protocol.run_uuid,
                "root": root,
                "launch": launch_evidence,
                "post_scan": scan_evidence,
                "post_scan_records": protocol_records,
                "linux_residual_zero": linux_residual_zero,
                "forced_termination_attempts": forced_termination_attempts,
                "subsequent_probe_after_residual": 0,
            },
            "wsl_process_scope": "uuid_and_process_group_post_scan_complete",
            "stdout": str(launch.get("stdout", "")),
            "stderr": "\n".join(
                part
                for part in (str(launch.get("stderr", "")), str(post_scan.get("stderr", "")))
                if part
            ),
        }
        residual_rows = [*rows, *({"protocol_residual": item} for item in protocol_records)]
        return aggregate, residual_rows

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

        queue_sql = QUEUE_READBACK_QUERY
        queue_result = self._run(
            deadline,
            self._psql_command(role="control_plane", query=queue_sql, field_separator=True),
            name="r7s1-queue-claims-readback",
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
            name="r7s1-active-jobs-readback",
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
            self._docker_command(
                "ps",
                "-a",
                "--filter",
                str(self.expected["x1_docker_name_filter"]),
                "--format",
                "{{json .}}",
            ),
            name="r7s1-x1-docker-residue-readback",
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
                name=f"r7s1-x1-kubernetes-residue-{index}",
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
            self._psql_command(
                role="control_plane",
                query=CONTROL_PLANE_HISTORY_QUERY,
                field_separator=True,
            ),
            name="r7s1-historical-control-plane-task-classification",
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
            self._psql_command(
                role="control_plane",
                query=CONTROL_PLANE_EXECUTION_LINK_QUERY,
                field_separator=True,
            ),
            name="r7s1-historical-control-plane-live-execution-links",
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
            self._psql_command(role="mlflow", query=MLFLOW_HISTORY_QUERY, field_separator=True),
            name="r7s1-historical-mlflow-running-classification",
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
                            "status": fields[1],
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
        terminal_fencing_exact, mlflow_terminal_decisions = self._mlflow_terminal_fencing_exact(
            mlflow_records
        )
        mlflow_scope_exact = bool(
            terminal_fencing_exact
            and self._historical_classification_exact(
                mlflow_scope,
                observed_count=len(observed_mlflow),
                attestation_exact=attestation_ok.get("mlflow_running_rows", False),
            )
            and self._mlflow_attestation_live_exact(
                classification=mlflow_scope,
                payload=_mapping(
                    classification_attestations["mlflow_running_rows"]["payload"],
                    "mlflow_attestation_payload",
                ),
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
            name="r7s1-historical-failed-pod-classification",
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
        failed_jobs_result = self._run(
            deadline,
            self._kubectl_command("get", "jobs", "-A", "-o", "json"),
            name="r7s1-historical-failed-owner-job-readback",
        )
        results.append(failed_jobs_result)
        failed_jobs_payload, failed_jobs_error = self._json_object(
            failed_jobs_result, "historical_failed_owner_jobs"
        )
        if failed_jobs_error:
            return self._failed_process_chain(
                results, last_error=failed_jobs_error, invariant_names=invariant_names
            )
        assert failed_jobs_payload is not None
        try:
            raw_jobs = failed_jobs_payload.get("items")
            raw_pods = failed_pods_payload.get("items")
            if not isinstance(raw_jobs, list) or not isinstance(raw_pods, list):
                raise ValueError("historical_failed_items_list_required")
            owner_jobs = self._job_owner_index(raw_jobs)
            observed_failed_pods = sorted(
                (self._failed_pod_identity(item, owner_jobs) for item in raw_pods),
                key=lambda item: (item["namespace"], item["name"], item["uid"]),
            )
        except (TypeError, ValueError) as exc:
            return self._failed_process_chain(
                results,
                last_error=f"historical_failed_pod_identity_invalid:{exc}",
                invariant_names=invariant_names,
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
            failure = self._failed_process_chain(results, invariant_names=invariant_names)
            scope = wsl_result.get("process_evidence")
            failure.update(
                {
                    "windows_global_residuals": windows_residuals,
                    "wsl_global_residuals": wsl_residuals,
                    "wsl_process_scope": scope if isinstance(scope, Mapping) else "unavailable",
                    "subsequent_probe_launches": 0,
                }
            )
            return failure
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
            self._psql_command(role="control_plane", query=queue_sql, field_separator=True),
            name="r7s1-final-queue-claims-readback",
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
            name="r7s1-final-active-jobs-readback",
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
            self._psql_command(
                role="control_plane",
                query=CONTROL_PLANE_EXECUTION_LINK_QUERY,
                field_separator=True,
            ),
            name="r7s1-final-control-plane-live-execution-links",
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
                "observed_records": mlflow_records,
                "classification": dict(mlflow_scope),
                "terminal_fencing_decisions": mlflow_terminal_decisions,
                "terminal_fencing_exact": terminal_fencing_exact,
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


def _new_probe_set(prepared: PreparedExecution, process_runner: Any | None = None) -> R7S1ProbeSet:
    return R7S1ProbeSet(
        manifest=prepared.manifest,
        contract=prepared.timeout_contract,
        expected_revision=prepared.args.expected_revision.lower(),
        repository_root=prepared.args.repository_root.resolve(),
        parent_payloads=prepared.parent_payloads,
        validated_manifest=prepared.validated_manifest,
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
        required_invariants=R7S1_REQUIRED_INVARIANTS,
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


def _successor_binding_from_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    external = _mapping(manifest.get("external_terminal_fencing"), "external_terminal_fencing")
    return _mapping(external.get("successor_binding"), "successor_binding")


def _emergency_process_residue(report: Mapping[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {
            "manual_intervention_required": True,
            "residual_pids": [],
            "residual_status": "unknown_before_restore_report",
        }
    raw_pids = report.get("residual_pids", [])
    if not isinstance(raw_pids, Sequence) or isinstance(raw_pids, (str, bytes, bytearray)):
        return {
            "manual_intervention_required": True,
            "residual_pids": [],
            "residual_status": "unknown_due_to_invalid_primary_residual_evidence",
        }
    pids = [
        pid for pid in raw_pids if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
    ]
    if len(pids) != len(raw_pids):
        return {
            "manual_intervention_required": True,
            "residual_pids": sorted(set(pids)),
            "residual_status": "unknown_due_to_invalid_primary_residual_evidence",
        }
    status = report.get("residual_status")
    return {
        "manual_intervention_required": True,
        "residual_pids": sorted(set(pids)),
        "residual_status": str(status) if status not in {None, ""} else "unknown",
    }


def _attempt_emergency_seal_values(
    *,
    primary_output: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    expected_trusted_checkpoint_sha256: str,
    failed_stage: str,
    exception: BaseException,
    report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Try the one permitted upper seal publication; never retry it."""

    try:
        binding = _successor_binding_from_manifest(manifest)
        _assert_bound_run_locations(
            manifest_argument=manifest_path,
            output_argument=primary_output,
            manifest=manifest,
            label="emergency_seal",
        )
        evidence = EvidenceWriter.seal_emergency(
            primary_output=primary_output,
            successor_binding=binding,
            failed_stage=failed_stage,
            exception=exception,
            process_residue=_emergency_process_residue(report),
            manifest_identity={
                "path": str(manifest_path.resolve()),
                "sha256": manifest_sha256,
                "canonical_revision": str(manifest.get("canonical_revision", "")),
                "canonical_tree": str(manifest.get("canonical_tree", "")),
            },
            expected_trusted_checkpoint_sha256=str(expected_trusted_checkpoint_sha256).lower(),
        )
    except Exception as emergency_exc:
        return {
            "emergency_seal_created": False,
            "irrecoverable_evidence_failure": True,
            "emergency_seal_error": (
                f"emergency_seal_failed:{type(emergency_exc).__name__}:{emergency_exc}"
            ),
        }
    return {
        "emergency_seal_created": True,
        "irrecoverable_evidence_failure": False,
        **evidence,
    }


def _attempt_emergency_seal(
    prepared: PreparedExecution,
    *,
    failed_stage: str,
    exception: BaseException,
    report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return _attempt_emergency_seal_values(
        primary_output=prepared.output_directory,
        manifest_path=prepared.args.manifest,
        manifest=prepared.manifest,
        manifest_sha256=prepared.manifest_sha256,
        expected_trusted_checkpoint_sha256=prepared.args.expected_trusted_checkpoint_sha256,
        failed_stage=failed_stage,
        exception=exception,
        report=report,
    )


def _publication_failure_result(
    *,
    report: Mapping[str, Any],
    primary_exception: BaseException,
    failed_stage: str,
    emergency: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    return 2, {
        "decision": "manual_intervention_required",
        "report": dict(report),
        "primary_publication_error": (
            f"{failed_stage}:{type(primary_exception).__name__}:{primary_exception}"
        ),
        **dict(emergency),
    }


def _publication_failure_report(
    report: Mapping[str, Any], *, stage: str, exception: BaseException
) -> dict[str, Any]:
    return {
        **dict(report),
        "passed": False,
        "overall_pass": False,
        "restore_only_pass": False,
        "manual_intervention_required": True,
        "decision": "manual_intervention_required",
        "error": f"{stage}:{type(exception).__name__}:{exception}",
    }


def execute_restore_only(
    prepared: PreparedExecution,
    *,
    restore_executor: Callable[[PreparedExecution, RestoreCheckpoint], RestoreReport] | None = None,
    runner_reserved: bool = False,
) -> tuple[int, dict[str, Any]]:
    _assert_bound_run_locations(
        manifest_argument=prepared.args.manifest,
        output_argument=prepared.output_directory,
        manifest=prepared.manifest,
        label="restore_only_execution",
    )
    _assert_no_reparse_ancestors(prepared.bundle_directory, label="bundle_directory")
    _assert_no_reparse_ancestors(prepared.output_directory, label="output_directory")
    if prepared.output_directory.exists():
        raise R7S1RunnerError(f"output_directory_exists:{prepared.output_directory}")
    if runner_reserved:
        _verify_owned_runner_reservation(prepared)
    else:
        reserve_runner(prepared)
    executor = restore_executor or _run_restore_harness
    report = executor(prepared, prepared.restore_checkpoint)
    converted = r7s1_restore_report(report, prepared.run_id)
    if dict(converted.get("call_counts", {})) != RESTORE_LIFECYCLE_COUNTS:
        raise R7S1RunnerError("restore_only_lifecycle_calls_not_zero")
    if converted.get("phase_b2_executed") is not False:
        raise R7S1RunnerError("restore_only_phase_b2_execution_forbidden")
    _assert_no_reparse_ancestors(prepared.output_directory, label="output_directory_pre_writer")
    _assert_bound_run_locations(
        manifest_argument=prepared.args.manifest,
        output_argument=prepared.output_directory,
        manifest=prepared.manifest,
        label="evidence_writer",
    )
    metadata = _metadata(prepared)
    binding = _successor_binding_from_manifest(prepared.manifest)
    try:
        writer = EvidenceWriter(prepared.output_directory, successor_binding=binding)
    except Exception as exc:
        failure_report = _publication_failure_report(
            converted, stage="restore_only_writer_initialization_failed", exception=exc
        )
        emergency = _attempt_emergency_seal(
            prepared,
            failed_stage="restore_only_writer_initialization",
            exception=exc,
            report=failure_report,
        )
        return _publication_failure_result(
            report=failure_report,
            primary_exception=exc,
            failed_stage="restore_only_writer_initialization",
            emergency=emergency,
        )
    if report.passed:
        try:
            evidence = writer.seal_restore_only(converted, metadata=metadata)
        except Exception as exc:
            failure_report = _publication_failure_report(
                converted, stage="restore_only_success_publication_failed", exception=exc
            )
            try:
                failure_evidence = writer.seal_failure(failure_report, metadata=metadata)
            except Exception as failure_exc:
                emergency = _attempt_emergency_seal(
                    prepared,
                    failed_stage="restore_only_failure_seal_after_success_publication",
                    exception=failure_exc,
                    report=failure_report,
                )
                return _publication_failure_result(
                    report=failure_report,
                    primary_exception=failure_exc,
                    failed_stage="restore_only_failure_seal_after_success_publication",
                    emergency=emergency,
                )
            return 2, {
                "decision": "manual_intervention_required",
                "report": failure_report,
                **failure_evidence,
            }
        return 0, {"decision": "restore_only_pass", "report": converted, **evidence}
    try:
        evidence = writer.seal_failure(converted, metadata=metadata)
    except Exception as exc:
        emergency = _attempt_emergency_seal(
            prepared,
            failed_stage="restore_only_failure_seal",
            exception=exc,
            report=converted,
        )
        return _publication_failure_result(
            report=converted,
            primary_exception=exc,
            failed_stage="restore_only_failure_seal",
            emergency=emergency,
        )
    return 2, {
        "decision": "manual_intervention_required",
        "report": converted,
        **evidence,
    }


def _bootstrap_failure_report(args: argparse.Namespace, exc: BaseException) -> dict[str, Any]:
    return {
        "schema": "s8-v4-x1-phase-b2-r7s1-runner-bootstrap-failure/v1",
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
        "required_invariants": list(R7S1_REQUIRED_INVARIANTS),
        "success_invariants": {name: False for name in R7S1_REQUIRED_INVARIANTS},
    }


def _seal_bootstrap_failure(args: argparse.Namespace, exc: BaseException) -> Mapping[str, Any]:
    if isinstance(exc, DuplicateInvocationError):
        return {"failure_seal_error": "duplicate_invocation_no_new_evidence_allowed"}
    try:
        _assert_no_reparse_ancestors(args.output_directory, label="bootstrap_output_directory")
        output = args.output_directory.resolve()
        manifest_path = args.manifest.resolve()
        manifest, manifest_sha256 = _read_manifest_snapshot_with_sha(manifest_path)
        _assert_bound_run_locations(
            manifest_argument=args.manifest,
            output_argument=args.output_directory,
            manifest=manifest,
            label="bootstrap_failure",
        )
        manifest_output = Path(str(_mapping(manifest.get("output"), "manifest_output")["path"]))
        if not _resolved_equal(output, manifest_output):
            return {"failure_seal_error": "untrusted_output_argument_manifest_mismatch"}
        run_id = str(manifest.get("bundle_id", ""))
        reservation_path = manifest_path.parent / RUNNER_RESERVATION
        environment = _native_runner_environment()
        _outer, _bridge, invocation_nonce = _verify_launcher_reservations(
            bundle=manifest_path.parent,
            output_directory=output,
            run_id=run_id,
            parent_identity=_mapping(environment.get("parent"), "native_parent"),
        )
        reservation = _verify_reservation(
            reservation_path,
            schema="s8-v4-x1-phase-b2-r7s1-runner-reservation/v1",
            output_directory=output,
            run_id=run_id,
            expected_process_identity=_mapping(environment.get("runner"), "native_runner"),
            expected_nonce=invocation_nonce,
        )
        if int(reservation.get("pid", -1)) != os.getpid():
            return {"failure_seal_error": "runner_reservation_owner_mismatch"}
        report = _bootstrap_failure_report(args, exc)
        metadata = {
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_sha256,
            "run_id": run_id,
            "runner_invocations": 1,
            "automatic_retry": 0,
            "phase_b2_executed": False,
        }
        if output.exists():
            emergency = _attempt_emergency_seal_values(
                primary_output=output,
                manifest_path=manifest_path,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                expected_trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
                failed_stage="bootstrap_primary_output_preexisting",
                exception=exc,
                report=report,
            )
            return {
                "failure_seal_error": f"output_directory_exists:{output}",
                **emergency,
            }
        binding = _successor_binding_from_manifest(manifest)
        try:
            writer = EvidenceWriter(output, successor_binding=binding)
            return writer.seal_failure(report, metadata=metadata)
        except Exception as primary_seal_exc:
            emergency = _attempt_emergency_seal_values(
                primary_output=output,
                manifest_path=manifest_path,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                expected_trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
                failed_stage="bootstrap_failure_seal",
                exception=primary_seal_exc,
                report=report,
            )
            return {
                "failure_seal_error": f"{type(primary_seal_exc).__name__}:{primary_seal_exc}",
                **emergency,
            }
    except Exception as seal_exc:
        error = f"{type(seal_exc).__name__}:{seal_exc}"
        return {
            "failure_seal_error": error,
            "emergency_seal_created": False,
            "irrecoverable_evidence_failure": True,
            "emergency_seal_error": ("emergency_seal_unavailable_without_trusted_binding:" + error),
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run exactly one append-only S8-V4/X1 r7s1 restore-only gate."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-trusted-checkpoint-sha256", required=True)
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

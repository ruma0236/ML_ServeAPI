from __future__ import annotations

import argparse
import hashlib
import json
import math
import ntpath
import os
import re
import shutil
import stat
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))
sys.path.insert(0, str(SCRIPT_PROJECT_ROOT / "src"))

from scripts.dev import publish_pre_r8_r7s5_review as publisher  # noqa: E402
from evm.scale_validation import phase_b2_r7s3_handle_io as handle_io  # noqa: E402
from evm.scale_validation import phase_b2_r7s5_ci as ci  # noqa: E402
from evm.scale_validation.phase_b2_r7s4_handle_io import (  # noqa: E402
    DurableBoundPublication,
    DurablePublicationError,
    WindowsHandleApi,
    publish_bound_no_replace_durable,
    validate_strict_windows_leaf,
)
from evm.scale_validation.phase_b2_r7s3_process import (  # noqa: E402
    ProcessContainmentFailure,
    ProcessOutcome,
    TimeoutContract,
    WindowsJobProcessRunner,
)


SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s6.validation-runner.v1"
VALIDATION_OBSERVATION_SCOPE = "planned_and_metadata_children_windows_job_accounted_no_kill"
ENVIRONMENT_SCHEMA = f"{SCHEMA}.child-environment.v1"
TERMINAL_FAILURE_SCHEMA = f"{SCHEMA}.terminal-failure.v1"
OUTPUT_PARENT_SCHEMA = f"{SCHEMA}.output-parent.v1"
PUBLICATION_INDEX_SCHEMA = f"{SCHEMA}.publication-index.v1"
VALIDATION_WRAPPER_TIMEOUT_SECONDS = 1_800.0
VALIDATION_RESIDUAL_REPOLL_SECONDS = 120.0
VALIDATION_STREAM_DRAIN_SECONDS = 30.0
METADATA_WRAPPER_TIMEOUT_SECONDS = 30.0
METADATA_STREAM_DRAIN_SECONDS = 10.0
_SECRET_ENV_NAME_RE = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|"
    r"SIGNING[_-]?KEY|CREDENTIAL|AUTH|BEARER|COOKIE|SESSION|CERT)",
    re.IGNORECASE,
)
_SAFE_INHERITED_ENVIRONMENT_KEYS = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "COMSPEC",
        "DRIVERDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PUBLIC",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERDOMAIN_ROAMINGPROFILE",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)
_RUNNER_INJECTED_ENVIRONMENT_KEYS = (
    "EVM_PHASE_B2_JOB_CAPABILITY_COMMITMENT",
    "EVM_PHASE_B2_JOB_CAPABILITY_HANDLE",
    "EVM_PHASE_B2_JOB_CAPABILITY_NONCE",
    "EVM_PHASE_B2_RUN_UUID",
)
_SECRET_REDACTION = "<redacted-secret-like-environment-value>"
UNTRACKED_INVENTORY_SCHEMA = f"{SCHEMA}.isolated-untracked-inventory.v1"
_IMPORT_ACTIVE_UNTRACKED_BASENAMES = frozenset(
    {
        "conftest.py",
        "pytest.py",
        "ruff.py",
        "sitecustomize.py",
        "usercustomize.py",
    }
)
_IMPORT_ACTIVE_UNTRACKED_SUFFIXES = (".py", ".pyc", ".pyo", ".pyd", ".so", ".pth")
_TOOL_CONTROL_UNTRACKED_BASENAMES = frozenset(
    {
        ".ruff.toml",
        "pyproject.toml",
        "pytest.ini",
        "ruff.toml",
        "setup.cfg",
        "tox.ini",
    }
)
_IGNORED_IMPORT_ACTIVE_PATHSPECS = tuple(
    f":(icase,glob)**/*{suffix}" for suffix in _IMPORT_ACTIVE_UNTRACKED_SUFFIXES
) + tuple(f":(icase,glob)**/{name}" for name in sorted(_TOOL_CONTROL_UNTRACKED_BASENAMES))


class ValidationRunnerError(RuntimeError):
    pass


class ValidationOutputInitializationFailure(ValidationRunnerError):
    """Describe an output binding failure without removing any partial directory."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        output_path: Path,
        original_error: BaseException,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.output_path = output_path
        self.original_error = original_error


class MetadataChildError(ValidationRunnerError):
    """Carry sanitized child evidence across a terminal metadata latch."""

    def __init__(
        self,
        message: str,
        *,
        name: str,
        failure_kind: str,
        process_evidence: Mapping[str, Any],
        secret_like_output_detected: bool = False,
        evidence_recorded: bool = False,
    ) -> None:
        super().__init__(message)
        self.name = name
        self.failure_kind = failure_kind
        self.process_evidence = dict(process_evidence)
        self.secret_like_output_detected = secret_like_output_detected
        self.evidence_recorded = evidence_recorded


@dataclass(frozen=True, slots=True)
class ChildEnvironment:
    values: dict[str, str]
    commitment: dict[str, Any]
    secret_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    expected_exit_code: int = 0
    required_output_tokens: tuple[str, ...] = ()
    wrapper_timeout_seconds: float = VALIDATION_WRAPPER_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ExecutablePin:
    label: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class UntrackedInventoryPin:
    count: int
    path_list_sha256: str
    content_inventory_sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    return publisher.canonical_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return publisher.sha256_file(path)


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValidationRunnerError(f"{label}_sha256_invalid")
    return value


def validate_independent_executable_pin(
    path: Path,
    sha256: str,
    *,
    label: str,
) -> ExecutablePin:
    """Validate a caller/work-order pin without starting the executable."""

    requested = Path(path)
    if not requested.is_absolute():
        raise ValidationRunnerError(f"{label}_absolute_path_required")
    expected_sha256 = _require_sha256(sha256, label=label)
    try:
        resolved = requested.resolve(strict=True)
        metadata = resolved.lstat()
    except (OSError, RuntimeError) as exc:
        raise ValidationRunnerError(f"{label}_executable_unavailable") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not resolved.is_file()
        or requested.is_symlink()
        or file_attributes & reparse_flag
        or metadata.st_size <= 0
    ):
        raise ValidationRunnerError(f"{label}_regular_nonreparse_executable_required")
    if sha256_file(resolved) != expected_sha256:
        raise ValidationRunnerError(f"{label}_independent_sha256_mismatch")
    return ExecutablePin(label=label, path=resolved, sha256=expected_sha256)


def _executable_pins_from_args(args: argparse.Namespace) -> dict[str, ExecutablePin]:
    required = {
        "python_general": ("python_general", "python_general_sha256"),
        "python_host": ("python_host", "python_host_sha256"),
        "python_ruff": ("python_ruff", "python_ruff_sha256"),
        "git": ("git", "git_sha256"),
        "powershell": ("powershell", "powershell_sha256"),
    }
    if any(not hasattr(args, field) for fields in required.values() for field in fields):
        raise ValidationRunnerError("validation_independent_executable_pins_required")
    return {
        label: validate_independent_executable_pin(
            Path(getattr(args, path_field)),
            getattr(args, sha_field),
            label=label,
        )
        for label, (path_field, sha_field) in required.items()
    }


def _untracked_inventory_pin_from_args(args: argparse.Namespace) -> UntrackedInventoryPin:
    fields = (
        "expected_untracked_count",
        "expected_untracked_path_list_sha256",
        "expected_untracked_content_inventory_sha256",
    )
    if any(not hasattr(args, field) for field in fields):
        raise ValidationRunnerError("validation_untracked_inventory_pin_required")
    count = args.expected_untracked_count
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValidationRunnerError("validation_untracked_count_invalid")
    return UntrackedInventoryPin(
        count=count,
        path_list_sha256=_require_sha256(
            args.expected_untracked_path_list_sha256,
            label="validation_untracked_path_list",
        ),
        content_inventory_sha256=_require_sha256(
            args.expected_untracked_content_inventory_sha256,
            label="validation_untracked_content_inventory",
        ),
    )


def _normalized_path_text(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve(strict=True))))


def output_parent_commitment(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValidationRunnerError("validation_output_parent_directory_required")
    payload = {
        "schema": OUTPUT_PARENT_SCHEMA,
        "normalized_path": _normalized_path_text(resolved),
    }
    return {
        **payload,
        "sha256": sha256_bytes(canonical_json_bytes(payload)),
    }


def _validate_output_parent_gate(
    requested: Path,
    *,
    expected_path: Path,
    expected_sha256: str,
    forbidden_roots: Sequence[Path] = (),
) -> tuple[Path, dict[str, Any]]:
    requested_resolved = requested.resolve(strict=True)
    expected_resolved = expected_path.resolve(strict=True)
    if not requested_resolved.is_dir() or not expected_resolved.is_dir():
        raise ValidationRunnerError("validation_output_parent_directory_required")
    if _normalized_path_text(requested_resolved) != _normalized_path_text(expected_resolved):
        raise ValidationRunnerError("validation_output_parent_path_mismatch")
    for forbidden in forbidden_roots:
        forbidden_resolved = forbidden.resolve(strict=True)
        if (
            requested_resolved == forbidden_resolved
            or forbidden_resolved in requested_resolved.parents
        ):
            raise ValidationRunnerError("validation_output_parent_inside_forbidden_root")
    commitment = output_parent_commitment(requested_resolved)
    if not isinstance(expected_sha256, str) or expected_sha256 != commitment["sha256"]:
        raise ValidationRunnerError("validation_output_parent_sha256_mismatch")
    return requested_resolved, commitment


def _secret_environment_values(source: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value)
                for key, value in source.items()
                if _SECRET_ENV_NAME_RE.search(str(key)) and len(str(value)) >= 4
            },
            key=lambda item: (-len(item), item),
        )
    )


def _safe_path_value(executable_values: Sequence[str]) -> str:
    directories: list[str] = []
    for value in executable_values:
        directory = str(_resolved_executable(value).parent)
        if os.path.normcase(directory) not in {os.path.normcase(item) for item in directories}:
            directories.append(directory)
    windows_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if windows_root:
        for candidate in (Path(windows_root), Path(windows_root) / "System32"):
            if candidate.is_dir():
                value = str(candidate.resolve(strict=True))
                if os.path.normcase(value) not in {os.path.normcase(item) for item in directories}:
                    directories.append(value)
    if not directories:
        raise ValidationRunnerError("validation_child_path_empty")
    return os.pathsep.join(directories)


def build_child_environment(
    project_root: Path,
    executable_values: Sequence[str],
    *,
    source: Mapping[str, str] | None = None,
) -> ChildEnvironment:
    source_environment = dict(os.environ if source is None else source)
    casefolded: dict[str, str] = {}
    for key, value in source_environment.items():
        normalized_key = str(key).upper()
        if normalized_key in casefolded:
            raise ValidationRunnerError("validation_child_environment_case_collision")
        casefolded[normalized_key] = str(value)
    secret_names = sorted(
        str(key) for key in source_environment if _SECRET_ENV_NAME_RE.search(str(key))
    )
    values = {
        key: casefolded[key]
        for key in sorted(_SAFE_INHERITED_ENVIRONMENT_KEYS)
        if key in casefolded and not _SECRET_ENV_NAME_RE.search(key)
    }
    resolved_project_root = project_root.resolve(strict=True)
    values.update(
        {
            "GIT_CONFIG_GLOBAL": "NUL",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": _safe_path_value(executable_values),
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(resolved_project_root), str(resolved_project_root / "src"))
            ),
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    if any(_SECRET_ENV_NAME_RE.search(key) for key in values):
        raise ValidationRunnerError("validation_child_secret_like_environment_key_present")
    normalized_values = {key: values[key] for key in sorted(values)}
    commitment_payload = {
        "schema": ENVIRONMENT_SCHEMA,
        "pairs": [[key, normalized_values[key]] for key in normalized_values],
    }
    commitment = {
        "schema": ENVIRONMENT_SCHEMA,
        "sha256": sha256_bytes(canonical_json_bytes(commitment_payload)),
        "key_count": len(normalized_values),
        "keys": list(normalized_values),
        "removed_secret_like_variable_count": len(secret_names),
        "removed_secret_like_variable_name_sha256": sha256_bytes(
            canonical_json_bytes(secret_names)
        ),
        "values_disclosed": False,
        "runner_injected_ephemeral_keys_excluded": list(_RUNNER_INJECTED_ENVIRONMENT_KEYS),
    }
    return ChildEnvironment(
        values=normalized_values,
        commitment=commitment,
        secret_values=_secret_environment_values(source_environment),
    )


def _redact_secret_text(value: str, secret_values: Sequence[str]) -> tuple[str, bool]:
    redacted = value
    detected = False
    for secret in secret_values:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, _SECRET_REDACTION)
            detected = True
    return redacted, detected


def _sanitize_for_evidence(value: Any, secret_values: Sequence[str]) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _redact_secret_text(value, secret_values)
    if isinstance(value, list):
        detected = False
        result = []
        for item in value:
            sanitized, item_detected = _sanitize_for_evidence(item, secret_values)
            result.append(sanitized)
            detected = detected or item_detected
        return result, detected
    if isinstance(value, tuple):
        sanitized, detected = _sanitize_for_evidence(list(value), secret_values)
        return tuple(sanitized), detected
    if isinstance(value, Mapping):
        detected = False
        result: dict[str, Any] = {}
        for key, item in value.items():
            sanitized, item_detected = _sanitize_for_evidence(item, secret_values)
            result[str(key)] = sanitized
            detected = detected or item_detected
        return result, detected
    return value, False


def _directory_identity_stable(
    expected: handle_io.HandleIdentity,
    observed: handle_io.HandleIdentity,
) -> bool:
    """Compare the non-volatile identity/security fields of one open directory."""

    excluded = {"attributes", "size"}
    return all(
        expected_value == observed.to_dict()[name]
        for name, expected_value in expected.to_dict().items()
        if name not in excluded
    )


class _BoundValidationOutput:
    """Hold one no-delete-share directory handle for every validation write.

    Files are created and renamed relative to the held handle.  A junction or
    path swap therefore cannot redirect a later command record to a different
    directory.  Publication failure is terminal; no cleanup or retry occurs.
    """

    def __init__(
        self,
        *,
        path: Path,
        run_uuid: str,
        api: WindowsHandleApi,
        directory_handle: int,
        directory_identity: handle_io.HandleIdentity,
    ) -> None:
        self.path = path
        self.run_uuid = run_uuid
        self._api = api
        self._directory_handle = directory_handle
        self._directory_identity = directory_identity
        self._publication_attempts: list[dict[str, Any]] = []

    @classmethod
    def create(cls, parent: Path, output_leaf: str) -> _BoundValidationOutput:
        try:
            leaf = validate_strict_windows_leaf(output_leaf)
        except Exception as exc:
            raise ValidationRunnerError("validation_output_leaf_invalid") from exc
        parent = parent.resolve(strict=True)
        output = parent / leaf
        if output.parent != parent:
            raise ValidationRunnerError("validation_output_parent_escape")
        stage = "create_output_directory"
        try:
            output.mkdir()
        except BaseException as exc:
            message = (
                "validation_output_exists"
                if isinstance(exc, FileExistsError)
                else "validation_output_directory_create_failed"
            )
            raise ValidationOutputInitializationFailure(
                message,
                stage=stage,
                output_path=output,
                original_error=exc,
            ) from exc

        api = WindowsHandleApi()
        directory_handle: int | None = None
        stage = "open_output_directory"
        try:
            directory_handle = api.open_directory(str(output.resolve(strict=True)))
            stage = "bind_output_directory_identity"
            identity = api.identity(directory_handle)
            handle_io._reject_unsafe_directory_identity(
                identity,
                expected_path=str(output.resolve(strict=True)),
            )
            return cls(
                path=output,
                run_uuid=str(uuid.uuid4()),
                api=api,
                directory_handle=directory_handle,
                directory_identity=identity,
            )
        except BaseException as exc:
            try:
                api.close(directory_handle)
            except BaseException:
                pass
            raise ValidationOutputInitializationFailure(
                "validation_output_directory_binding_failed",
                stage=stage,
                output_path=output,
                original_error=exc,
            ) from exc

    def _require_directory_identity(self) -> handle_io.HandleIdentity:
        observed = self._api.identity(self._directory_handle)
        handle_io._reject_unsafe_directory_identity(
            observed,
            expected_path=str(self.path.resolve(strict=True)),
        )
        if not _directory_identity_stable(self._directory_identity, observed):
            raise ValidationRunnerError("validation_output_directory_identity_changed")
        return observed

    def publish(self, leaf_value: str, raw: bytes) -> DurableBoundPublication:
        if not isinstance(raw, bytes):
            raise TypeError("validation_output_publication_payload_bytes_required")
        attempt: dict[str, Any] = {
            "sequence": len(self._publication_attempts) + 1,
            "requested_leaf": leaf_value,
            "leaf": None,
            "temporary_leaf": None,
            "intended_final_path": None,
            "expected_sha256": sha256_bytes(raw),
            "expected_bytes": len(raw),
            "stage": "validate_leaf",
            "rename_completed": False,
            "publication_complete": False,
            "failure_observation": None,
            "exception_type": None,
            "retry_allowed": False,
            "cleanup_attempted": False,
        }
        self._publication_attempts.append(attempt)
        leaf = "unvalidated"
        temporary_leaf = "unvalidated"
        try:
            leaf = validate_strict_windows_leaf(leaf_value)
            temporary_leaf = validate_strict_windows_leaf(f".{leaf}.{self.run_uuid}.partial")
            attempt["leaf"] = leaf
            attempt["temporary_leaf"] = temporary_leaf
            attempt["intended_final_path"] = ntpath.join(str(self.path), leaf)
        except BaseException as exc:
            attempt["exception_type"] = f"{type(exc).__module__}.{type(exc).__qualname__}"
            raise ValidationRunnerError("validation_output_evidence_leaf_invalid") from exc
        file_handle: int | None = None
        stage = "verify_bound_directory_before_create"
        try:
            attempt["stage"] = stage
            self._require_directory_identity()
            stage = "create_relative_temporary"
            attempt["stage"] = stage
            file_handle = self._api.create_relative_file(
                self._directory_handle,
                temporary_leaf,
            )
            stage = "protect_temporary_dacl"
            attempt["stage"] = stage
            self._api.protect_dacl(file_handle)
            stage = "write_and_flush_temporary"
            attempt["stage"] = stage
            self._api.write_all(file_handle, raw)
            self._api.flush(file_handle)
            stage = "verify_temporary"
            attempt["stage"] = stage
            temporary_identity = self._api.identity(file_handle)
            expected_temporary = ntpath.join(str(self.path), temporary_leaf)
            handle_io._reject_unsafe_identity(
                temporary_identity,
                expected_path=expected_temporary,
                require_protected_dacl=True,
            )
            if temporary_identity.size != len(raw):
                raise ValidationRunnerError("validation_output_temporary_size_mismatch")
            if self._api.read_all(file_handle, temporary_identity.size) != raw:
                raise ValidationRunnerError("validation_output_temporary_readback_mismatch")
            stage = "rename_no_replace"
            attempt["stage"] = stage
            self._api.rename_no_replace(file_handle, self._directory_handle, leaf)
            attempt["rename_completed"] = True
            stage = "flush_final_and_directory"
            attempt["stage"] = stage
            self._api.flush(file_handle)
            self._api.flush_directory(self._directory_handle)
            stage = "verify_final"
            attempt["stage"] = stage
            final_identity = self._api.identity(file_handle)
            expected_final = ntpath.join(str(self.path), leaf)
            handle_io._reject_unsafe_identity(
                final_identity,
                expected_path=expected_final,
                require_protected_dacl=True,
            )
            if (
                temporary_identity.volume_serial_number != final_identity.volume_serial_number
                or temporary_identity.file_id_hex != final_identity.file_id_hex
                or temporary_identity.security_descriptor_sha256
                != final_identity.security_descriptor_sha256
                or final_identity.size != len(raw)
                or self._api.read_all(file_handle, final_identity.size) != raw
            ):
                raise ValidationRunnerError("validation_output_final_identity_or_readback_mismatch")
            final_directory_identity = self._require_directory_identity()
            publication = DurableBoundPublication(
                final_path=final_identity.final_path,
                temporary_leaf=temporary_leaf,
                sha256=sha256_bytes(raw),
                bytes=len(raw),
                identity=final_identity,
                directory_identity=final_directory_identity,
                file_flush_count=2,
                directory_flush_count=1,
                directory_flush_succeeded=True,
            )
            stage = "close_published_file_handle"
            attempt["stage"] = stage
            self._api.close(file_handle)
            file_handle = None
            attempt["stage"] = "complete"
            attempt["publication_complete"] = True
            return publication
        except BaseException as exc:
            attempt["stage"] = stage
            attempt["exception_type"] = f"{type(exc).__module__}.{type(exc).__qualname__}"
            attempt["failure_observation"] = self._publication_failure_observation(
                file_handle,
                raw=raw,
            )
            if isinstance(exc, ValidationRunnerError):
                raise
            raise ValidationRunnerError(
                f"validation_output_publication_failed:{stage}:{leaf}"
            ) from exc
        finally:
            if file_handle is not None:
                try:
                    self._api.close(file_handle)
                except BaseException:
                    pass

    def _publication_failure_observation(
        self,
        file_handle: int | None,
        *,
        raw: bytes,
    ) -> dict[str, Any]:
        observation: dict[str, Any] = {
            "status": "unknown_no_open_file_handle",
            "current_identity": None,
            "current_sha256": None,
            "current_bytes": None,
            "observation_error_type": None,
            "expected_sha256": sha256_bytes(raw),
            "expected_bytes": len(raw),
        }
        if file_handle is None:
            return observation
        try:
            identity = self._api.identity(file_handle)
            observed_raw = self._api.read_all(file_handle, identity.size)
            if len(observed_raw) != identity.size:
                raise ValidationRunnerError("validation_output_failure_observation_size_mismatch")
            observation.update(
                {
                    "status": "same_handle_observed",
                    "current_identity": identity.to_dict(),
                    "current_sha256": sha256_bytes(observed_raw),
                    "current_bytes": len(observed_raw),
                }
            )
        except BaseException as exc:
            observation["status"] = "unknown_observation_failed"
            observation["observation_error_type"] = (
                f"{type(exc).__module__}.{type(exc).__qualname__}"
            )
        return observation

    def publication_attempts(self) -> list[dict[str, Any]]:
        """Return a detached canonical snapshot of every no-retry publication attempt."""

        return json.loads(canonical_json_bytes(self._publication_attempts))

    def close(self) -> None:
        handle = self._directory_handle
        self._directory_handle = 0
        self._api.close(handle)

    def __enter__(self) -> _BoundValidationOutput:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            if getattr(self, "_directory_handle", 0):
                self.close()
        except Exception:
            # Destructors cannot turn a prior validation failure into success.
            pass


def _metadata_evidence_record(
    *,
    name: str,
    phase: str,
    outcome: ProcessOutcome,
) -> dict[str, Any]:
    return {
        "name": name,
        "phase": phase,
        "status": "PASS",
        "child_invoked": True,
        "process_containment": outcome.to_dict(),
    }


def _failed_metadata_evidence_record(
    *,
    name: str,
    phase: str,
    exc: MetadataChildError,
) -> dict[str, Any]:
    return {
        "name": name,
        "phase": phase,
        "status": "FAIL",
        "child_invoked": True,
        "failure_kind": exc.failure_kind,
        "secret_like_output_detected": exc.secret_like_output_detected,
        "process_containment": exc.process_evidence,
    }


class _MetadataEvidenceRecord(dict[str, Any]):
    """Mutable record whose terminal transition can be mirrored before unwind."""

    def __init__(self, value: Mapping[str, Any]) -> None:
        super().__init__(value)
        self._terminal_observers: list[Callable[[Mapping[str, Any]], None]] = []
        self._observer_error_types: list[str] = []

    def bind_terminal_observer(self, observer: Callable[[Mapping[str, Any]], None]) -> None:
        self._terminal_observers.append(observer)

    def replace_terminal(self, replacement: Mapping[str, Any]) -> None:
        self.clear()
        self.update(dict(replacement))
        for observer in tuple(self._terminal_observers):
            try:
                observer(self)
            except BaseException as exc:
                self._observer_error_types.append(
                    f"{type(exc).__module__}.{type(exc).__qualname__}"
                )


def _pending_metadata_evidence_record(*, name: str, phase: str) -> dict[str, Any]:
    """Register the invocation before control can cross the child-launch boundary."""

    return _MetadataEvidenceRecord(
        {
            "name": name,
            "phase": phase,
            "status": "ATTEMPTED_UNPROVEN",
            "child_invoked": True,
            "failure_kind": "terminal_outcome_not_yet_recorded",
            "exception_type": None,
            "exception_message_disclosed": False,
            "process_containment": {
                "child_launch_boundary_crossed": "unproven",
                "terminal_process_evidence_recorded": False,
                "forced_termination_attempts": 0,
                "automatic_retry_count": 0,
            },
        },
    )


def _replace_metadata_evidence_record(
    target: dict[str, Any], replacement: Mapping[str, Any]
) -> None:
    if isinstance(target, _MetadataEvidenceRecord):
        target.replace_terminal(replacement)
        return
    target.clear()
    target.update(dict(replacement))


def _metadata_child_call_count(metadata_evidence: Sequence[Mapping[str, Any]]) -> int:
    return sum(item.get("child_invoked") is True for item in metadata_evidence)


def _run_metadata_child_recorded(
    argv: Sequence[str],
    *,
    cwd: Path,
    name: str,
    child_environment: ChildEnvironment,
    expected_executable_sha256: str,
    metadata_evidence: list[dict[str, Any]] | None,
    phase: str,
    before_child: Callable[[str], None] | None = None,
) -> ProcessOutcome:
    if before_child is not None:
        before_child(name)
    pending: dict[str, Any] | None = None
    if metadata_evidence is not None:
        pending = _pending_metadata_evidence_record(name=name, phase=phase)
        metadata_evidence.append(pending)
    try:
        outcome = _run_metadata_child(
            argv,
            cwd=cwd,
            name=name,
            child_environment=child_environment,
            expected_executable_sha256=expected_executable_sha256,
        )
    except MetadataChildError as exc:
        if pending is not None:
            _replace_metadata_evidence_record(
                pending,
                _failed_metadata_evidence_record(name=name, phase=phase, exc=exc),
            )
            exc.evidence_recorded = True
        raise
    except BaseException as exc:
        if pending is not None:
            _replace_metadata_evidence_record(
                pending,
                {
                    "name": name,
                    "phase": phase,
                    "status": "FAIL",
                    "child_invoked": True,
                    "failure_kind": "unexpected_base_exception",
                    "exception_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "exception_message_disclosed": False,
                    "process_containment": {
                        "child_launch_boundary_crossed": "unproven",
                        "terminal_process_evidence_recorded": False,
                        "forced_termination_attempts": 0,
                        "automatic_retry_count": 0,
                    },
                },
            )
        raise
    if pending is not None:
        _replace_metadata_evidence_record(
            pending,
            _metadata_evidence_record(name=name, phase=phase, outcome=outcome),
        )
    return outcome


def _git(
    repository: Path,
    *args: str,
    git_executable: Path,
    expected_git_sha256: str,
    child_environment: ChildEnvironment,
    metadata_evidence: list[dict[str, Any]] | None = None,
    phase: str = "unspecified",
    strip_output: bool = True,
) -> str:
    git_pin = validate_independent_executable_pin(
        git_executable,
        expected_git_sha256,
        label="git",
    )
    name = f"git-{args[0]}"
    outcome = _run_metadata_child_recorded(
        (str(git_pin.path), *args),
        cwd=repository,
        name=name,
        child_environment=child_environment,
        expected_executable_sha256=git_pin.sha256,
        metadata_evidence=metadata_evidence,
        phase=phase,
    )
    return outcome.stdout.strip() if strip_output else outcome.stdout


def _inventory_from_untracked_paths(repository: Path, paths: Sequence[str]) -> dict[str, Any]:
    resolved_repository = repository.resolve(strict=True)
    normalized_paths = sorted(paths)
    if len(set(normalized_paths)) != len(normalized_paths):
        raise ValidationRunnerError("validation_untracked_inventory_duplicate_path")
    records: list[dict[str, Any]] = []
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for relative in normalized_paths:
        pure = PurePosixPath(relative)
        if (
            not relative
            or "\\" in relative
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValidationRunnerError("validation_untracked_inventory_path_invalid")
        lower_name = pure.name.casefold()
        if (
            lower_name in _IMPORT_ACTIVE_UNTRACKED_BASENAMES
            or lower_name in _TOOL_CONTROL_UNTRACKED_BASENAMES
            or lower_name.endswith(_IMPORT_ACTIVE_UNTRACKED_SUFFIXES)
        ):
            raise ValidationRunnerError(
                f"validation_untracked_import_active_path_forbidden:{relative}"
            )
        candidate = resolved_repository.joinpath(*pure.parts)
        try:
            before = candidate.lstat()
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValidationRunnerError("validation_untracked_inventory_file_unavailable") from exc
        if (
            resolved_repository not in resolved.parents
            or not stat.S_ISREG(before.st_mode)
            or candidate.is_symlink()
            or int(getattr(before, "st_file_attributes", 0)) & reparse_flag
        ):
            raise ValidationRunnerError("validation_untracked_inventory_unsafe_file")
        digest = sha256_file(resolved)
        after = candidate.lstat()
        if (
            before.st_size,
            before.st_mtime_ns,
            getattr(before, "st_ino", None),
            getattr(before, "st_dev", None),
        ) != (
            after.st_size,
            after.st_mtime_ns,
            getattr(after, "st_ino", None),
            getattr(after, "st_dev", None),
        ):
            raise ValidationRunnerError("validation_untracked_inventory_file_changed_during_hash")
        records.append({"path": relative, "bytes": before.st_size, "sha256": digest})
    return {
        "schema": UNTRACKED_INVENTORY_SCHEMA,
        "count": len(normalized_paths),
        "paths": normalized_paths,
        "path_list_sha256": sha256_bytes(canonical_json_bytes(normalized_paths)),
        "content_inventory": records,
        "content_inventory_sha256": sha256_bytes(canonical_json_bytes(records)),
        "import_active_shadow_path_count": 0,
    }


def _verify_isolated_untracked_inventory(
    repository: Path,
    *,
    pin: UntrackedInventoryPin,
    git_pin: ExecutablePin,
    child_environment: ChildEnvironment,
    metadata_evidence: list[dict[str, Any]] | None,
    phase: str,
) -> dict[str, Any]:
    raw_paths = _git(
        repository,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        git_executable=git_pin.path,
        expected_git_sha256=git_pin.sha256,
        child_environment=child_environment,
        metadata_evidence=metadata_evidence,
        phase=f"{phase}:untracked_path_inventory",
        strip_output=False,
    )
    if raw_paths and not raw_paths.endswith("\0"):
        raise ValidationRunnerError("validation_untracked_inventory_not_nul_terminated")
    paths = raw_paths[:-1].split("\0") if raw_paths else []
    observed = _inventory_from_untracked_paths(repository, paths)
    ignored_shadow_paths = _git(
        repository,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        "--",
        *_IGNORED_IMPORT_ACTIVE_PATHSPECS,
        git_executable=git_pin.path,
        expected_git_sha256=git_pin.sha256,
        child_environment=child_environment,
        metadata_evidence=metadata_evidence,
        phase=f"{phase}:ignored_import_active_shadow_scan",
        strip_output=False,
    )
    if ignored_shadow_paths and not ignored_shadow_paths.endswith("\0"):
        raise ValidationRunnerError("validation_ignored_shadow_scan_not_nul_terminated")
    if ignored_shadow_paths:
        raise ValidationRunnerError("validation_ignored_import_active_path_forbidden")
    mismatches: list[str] = []
    if observed["count"] != pin.count:
        mismatches.append("count")
    if observed["path_list_sha256"] != pin.path_list_sha256:
        mismatches.append("path_list_sha256")
    if observed["content_inventory_sha256"] != pin.content_inventory_sha256:
        mismatches.append("content_inventory_sha256")
    if mismatches:
        raise ValidationRunnerError(
            "validation_untracked_inventory_pin_mismatch:" + ",".join(mismatches)
        )
    return {
        **observed,
        "ignored_import_active_shadow_path_count": 0,
        "expected": {
            "count": pin.count,
            "path_list_sha256": pin.path_list_sha256,
            "content_inventory_sha256": pin.content_inventory_sha256,
        },
        "matches_expected": True,
    }


def _selected_validation_files(project_root: Path) -> list[str]:
    files = [
        "scripts/dev/publish_pre_r8_r7s5_review.py",
        "scripts/dev/run_pre_r8_r7s5_validation.py",
        "scripts/dev/validate_pre_r8_r7s5_ci.py",
        "src/evm/scale_validation/phase_b2_r7s3_process.py",
        "tests/test_phase_b2_r7s1.py",
        "tests/test_phase_b2_r7_process.py",
        "tests/test_phase_b2_r7s3_job_capability.py",
        "tests/test_phase_b2_r7s3_process.py",
        "tests/test_publish_pre_r8_r7s5_review.py",
        "tests/test_scenario_workload_production.py",
        "tests/test_task_queue_process_safety.py",
    ]
    for pattern in ("phase_b2_r7s5_*.py", "phase_b2_r7s6_*.py"):
        files.extend(
            item.relative_to(project_root).as_posix()
            for item in sorted((project_root / "src" / "evm" / "scale_validation").glob(pattern))
        )
    for pattern in ("*r7s5*.py", "*r7s6*.py"):
        files.extend(
            item.relative_to(project_root).as_posix()
            for item in sorted((project_root / "tests").glob(pattern))
        )
    result = sorted(set(files))
    if not result or any(not (project_root / item).is_file() for item in result):
        raise ValidationRunnerError("selected_validation_file_missing")
    return result


def _isolated_python_module_argv(
    executable: Path,
    project_root: Path,
    module: str,
    *module_args: str,
) -> tuple[str, ...]:
    resolved_root = project_root.resolve(strict=True)
    resolved_src = (resolved_root / "src").resolve(strict=True)
    if not module or not re.fullmatch(r"[A-Za-z0-9_.]+", module):
        raise ValidationRunnerError("validation_isolated_python_module_invalid")
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]=[{str(resolved_root)!r},{str(resolved_src)!r}];"
        f"sys.argv[0]={module!r};"
        f"runpy.run_module({module!r},run_name='__main__',alter_sys=False)"
    )
    return (str(executable), "-I", "-B", "-c", bootstrap, *module_args)


def build_command_specs(
    *,
    repository: Path,
    project_root: Path,
    python_general: Path,
    python_host: Path,
    python_ruff: Path,
    git_executable: Path,
    git_executable_sha256: str,
    powershell_executable: Path,
) -> tuple[CommandSpec, ...]:
    files = _selected_validation_files(project_root)
    r7s5_tests = sorted(
        {
            item.relative_to(project_root).as_posix()
            for pattern in ("*r7s5*.py", "*r7s6*.py")
            for item in (project_root / "tests").glob(pattern)
        }
    )
    focused = sorted(
        set(
            r7s5_tests
            + [
                "tests/test_publish_pre_r8_r7s5_review.py",
                "tests/test_phase_b2_r7_process.py",
                "tests/test_phase_b2_r7s3_job_capability.py",
                "tests/test_phase_b2_r7s3_process.py",
                "tests/test_scenario_workload_production.py",
                "tests/test_task_queue_process_safety.py",
            ]
        )
    )
    host_tests = (
        "tests/test_pre_r8_r7s2_contract_stager.py",
        "tests/test_pre_r8_r7s2_outer_launcher.py",
        "tests/test_pre_r8_r7s2_wsl_qualification.py",
    )
    powershell_root = str(repository).replace("'", "''")
    powershell_git = str(git_executable).replace("'", "''")
    embedded_git_sha256 = _require_sha256(git_executable_sha256, label="git")
    ast_script = (
        f"$ErrorActionPreference='Stop'; $root=[IO.Path]::GetFullPath('{powershell_root}'); "
        f"$git=[IO.Path]::GetFullPath('{powershell_git}'); "
        "$observedGitSha=(Get-FileHash -LiteralPath $git -Algorithm SHA256).Hash.ToLowerInvariant(); "
        f"if($observedGitSha -cne '{embedded_git_sha256}'){{throw 'git_sha256_mismatch'}}; "
        "$files=@(& $git -C $root ls-files -- '*.ps1'); "
        "if($LASTEXITCODE -ne 0){throw 'git_ls_files_failed'}; $count=0; "
        "foreach($relative in $files){$tokens=$null;$errors=$null;"
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        "[IO.Path]::Combine($root,$relative),[ref]$tokens,[ref]$errors);"
        "if($errors.Count -ne 0){throw ('ast_error:'+ $relative)};$count++};"
        "Write-Output ('powershell_ast_files='+$count);Write-Output 'powershell_ast_errors=0'"
    )
    in_memory_compile_script = (
        "import pathlib,sys;"
        "paths=[pathlib.Path(value) for value in sys.argv[1:]];"
        "[compile(path.read_bytes(),str(path),'exec',dont_inherit=True) for path in paths];"
        "print('py_compile_mode=in_memory_no_bytecode');"
        "print('py_compile_files='+str(len(paths)))"
    )
    common_pytest = ("-q", "-p", "no:cacheprovider")
    specs = (
        CommandSpec(
            "r7s5-focused-pytest-py311",
            _isolated_python_module_argv(
                python_general,
                project_root,
                "pytest",
                *common_pytest,
                *focused,
            ),
        ),
        CommandSpec(
            "full-general-pytest-py311",
            _isolated_python_module_argv(
                python_general,
                project_root,
                "pytest",
                *common_pytest,
                "tests",
                *(f"--ignore={item}" for item in host_tests),
            ),
        ),
        CommandSpec(
            "pinned-host-pytest-py313",
            _isolated_python_module_argv(
                python_host,
                project_root,
                "pytest",
                *common_pytest,
                *host_tests,
            ),
        ),
        CommandSpec(
            "ruff-check-0.12.2",
            _isolated_python_module_argv(
                python_ruff,
                project_root,
                "ruff",
                "check",
                *files,
            ),
        ),
        CommandSpec(
            "ruff-format-check-0.12.2",
            _isolated_python_module_argv(
                python_ruff,
                project_root,
                "ruff",
                "format",
                "--check",
                *files,
            ),
        ),
        CommandSpec(
            "py-compile-py311",
            (str(python_general), "-I", "-B", "-c", in_memory_compile_script, *files),
            required_output_tokens=("py_compile_mode=in_memory_no_bytecode",),
        ),
        CommandSpec(
            "powershell-ast",
            (
                str(powershell_executable),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ast_script,
            ),
        ),
        CommandSpec(
            "git-diff-check",
            (
                str(git_executable),
                "-C",
                str(repository),
                "diff",
                "--check",
                f"{ci.EXPECTED_BASELINE_COMMIT}..HEAD",
                "--",
            ),
        ),
        CommandSpec(
            "ci-manifest-validator",
            (
                str(python_general),
                "-I",
                "-B",
                "scripts/dev/validate_pre_r8_r7s5_ci.py",
                "manifest",
                "--manifest",
                "ci/pre-r8-r7s5-test-lanes.json",
                "--project-root",
                ".",
                "--lane",
                "portable",
            ),
            required_output_tokens=(
                '"status":"manual_intervention_required"',
                '"go_evidence_eligible":false',
            ),
        ),
        CommandSpec(
            "ci-active-workflow-required-rejection",
            (
                str(python_general),
                "-I",
                "-B",
                "scripts/dev/validate_pre_r8_r7s5_ci.py",
                "workflow",
                "--manifest",
                "ci/pre-r8-r7s5-test-lanes.json",
                "--project-root",
                ".",
                "--workflow",
                "../.github/workflows/enterprise-vision-mlops-ci.yml",
            ),
            expected_exit_code=2,
            required_output_tokens=(
                "workflow_action_ref_inventory_mismatch",
                '"status":"rejected"',
            ),
        ),
        CommandSpec(
            "ci-mutation-pytest",
            _isolated_python_module_argv(
                python_general,
                project_root,
                "pytest",
                *common_pytest,
                "tests/test_phase_b2_r7s5_ci.py",
            ),
        ),
    )
    if {item.name for item in specs} != publisher.REQUIRED_VALIDATION_COMMANDS:
        raise ValidationRunnerError("required_validation_command_set_mismatch")
    return specs


def command_executable_sha256_pins(
    specs: Sequence[CommandSpec],
    executable_pins: Mapping[str, ExecutablePin],
) -> dict[str, str]:
    by_path: dict[str, str] = {}
    for pin in executable_pins.values():
        key = os.path.normcase(os.path.normpath(str(pin.path)))
        prior = by_path.setdefault(key, pin.sha256)
        if prior != pin.sha256:
            raise ValidationRunnerError("validation_executable_pin_path_digest_conflict")
    result: dict[str, str] = {}
    for spec in specs:
        executable = _resolved_executable(spec.argv[0])
        key = os.path.normcase(os.path.normpath(str(executable)))
        if key not in by_path:
            raise ValidationRunnerError(
                f"validation_command_executable_not_independently_pinned:{spec.name}"
            )
        result[spec.name] = by_path[key]
    if len(result) != len(specs):
        raise ValidationRunnerError("validation_command_name_duplicate")
    return result


def _resolved_executable(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve(strict=True)
    located = shutil.which(value)
    if located is None:
        raise ValidationRunnerError(f"command_executable_not_found:{value}")
    return Path(located).resolve(strict=True)


def _absolute_command(argv: Sequence[str]) -> tuple[str, ...]:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValidationRunnerError("command_arguments_invalid")
    return (str(_resolved_executable(argv[0])), *argv[1:])


def _tool_version_argv(spec: CommandSpec, executable: Path) -> tuple[str, ...]:
    if spec.name.startswith("ruff-"):
        return _isolated_python_module_argv(
            executable,
            SCRIPT_PROJECT_ROOT,
            "ruff",
            "--version",
        )
    if "pytest" in spec.name:
        return _isolated_python_module_argv(
            executable,
            SCRIPT_PROJECT_ROOT,
            "pytest",
            "--version",
        )
    if spec.name == "powershell-ast":
        return (
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$PSVersionTable.PSVersion.ToString()",
        )
    if spec.name == "git-diff-check":
        return (str(executable), "--version")
    return (str(executable), "--version")


def _inventory_metadata_child_sequence(
    *,
    phase: str,
    git_executable: Path,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    git_path = str(git_executable.resolve(strict=True))
    return (
        (
            "git-ls-files",
            f"{phase}:untracked_path_inventory",
            (
                git_path,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ),
        ),
        (
            "git-ls-files",
            f"{phase}:ignored_import_active_shadow_scan",
            (
                git_path,
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *_IGNORED_IMPORT_ACTIVE_PATHSPECS,
            ),
        ),
    )


def _repository_snapshot_metadata_child_sequence(
    *,
    phase: str,
    git_executable: Path,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    git_path = str(git_executable.resolve(strict=True))
    return (
        *_inventory_metadata_child_sequence(phase=phase, git_executable=git_executable),
        ("git-rev-parse", f"{phase}:head", (git_path, "rev-parse", "HEAD")),
        ("git-rev-parse", f"{phase}:tree", (git_path, "rev-parse", "HEAD^{tree}")),
        (
            "git-status",
            f"{phase}:tracked",
            (git_path, "status", "--porcelain=v1", "--untracked-files=no"),
        ),
    )


def expected_success_metadata_child_sequence(
    specs: Sequence[CommandSpec],
    *,
    git_executable: Path,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Return the exact successful metadata-child ledger contract in call order."""

    sequence: list[tuple[str, str, tuple[str, ...]]] = list(
        _repository_snapshot_metadata_child_sequence(
            phase="initial_repository_preflight",
            git_executable=git_executable,
        )
    )
    for spec in specs:
        executable = _resolved_executable(spec.argv[0])
        tool_version = _tool_version_argv(spec, executable)
        runtime_version = (
            tool_version
            if spec.name in {"powershell-ast", "git-diff-check"}
            else (str(executable), "--version")
        )
        for child_name, child_phase, child_argv in (
            (
                f"tool-version-{spec.name}",
                f"command_plan:{spec.name}:tool_version",
                tool_version,
            ),
            (
                f"runtime-version-{spec.name}",
                f"command_plan:{spec.name}:runtime_version",
                runtime_version,
            ),
        ):
            sequence.extend(
                _inventory_metadata_child_sequence(
                    phase=f"command_plan:before:{child_name}",
                    git_executable=git_executable,
                )
            )
            sequence.append((child_name, child_phase, child_argv))
    for spec in specs:
        for phase in (
            f"command:{spec.name}:preflight",
            f"command:{spec.name}:immediately_before_execution",
            f"command:{spec.name}:postflight",
        ):
            if phase.endswith("immediately_before_execution"):
                sequence.extend(
                    _inventory_metadata_child_sequence(
                        phase=phase,
                        git_executable=git_executable,
                    )
                )
            else:
                sequence.extend(
                    _repository_snapshot_metadata_child_sequence(
                        phase=phase,
                        git_executable=git_executable,
                    )
                )
    return tuple(sequence)


def command_tool_identity(
    spec: CommandSpec,
    *,
    child_environment: ChildEnvironment | None = None,
    expected_executable_sha256: str | None = None,
    metadata_evidence: list[dict[str, Any]] | None = None,
    phase: str = "command_plan",
    before_metadata_child: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    executable = _resolved_executable(spec.argv[0])
    executable_sha256 = sha256_file(executable)
    if expected_executable_sha256 is not None:
        if re.fullmatch(r"[0-9a-f]{64}", expected_executable_sha256) is None:
            raise ValidationRunnerError("command_tool_expected_sha256_invalid")
        if executable_sha256 != expected_executable_sha256:
            raise ValidationRunnerError(f"command_tool_independent_pin_mismatch:{spec.name}")
    executable_bytes = executable.stat().st_size
    environment = child_environment or build_child_environment(
        SCRIPT_PROJECT_ROOT,
        (str(executable),),
    )
    version_argv = _tool_version_argv(spec, executable)
    result = _run_metadata_child_recorded(
        version_argv,
        cwd=SCRIPT_PROJECT_ROOT,
        name=f"tool-version-{spec.name}",
        child_environment=environment,
        expected_executable_sha256=executable_sha256,
        metadata_evidence=metadata_evidence,
        phase=f"{phase}:{spec.name}:tool_version",
        before_child=before_metadata_child,
    )
    version = (result.stdout + result.stderr).strip()
    if not version:
        raise ValidationRunnerError(f"command_tool_version_empty:{spec.name}")
    runtime_version_argv = (
        version_argv
        if spec.name in {"powershell-ast", "git-diff-check"}
        else (str(executable), "--version")
    )
    runtime_result = _run_metadata_child_recorded(
        runtime_version_argv,
        cwd=SCRIPT_PROJECT_ROOT,
        name=f"runtime-version-{spec.name}",
        child_environment=environment,
        expected_executable_sha256=executable_sha256,
        metadata_evidence=metadata_evidence,
        phase=f"{phase}:{spec.name}:runtime_version",
        before_child=before_metadata_child,
    )
    runtime_version = (runtime_result.stdout + runtime_result.stderr).strip()
    if not runtime_version:
        raise ValidationRunnerError(f"command_runtime_version_failed:{spec.name}")
    if spec.name.startswith("ruff-") and version != "ruff 0.12.2":
        raise ValidationRunnerError("ruff_version_not_exact_0.12.2")
    if "py311" in spec.name and not runtime_version.startswith("Python 3.11."):
        raise ValidationRunnerError(f"py311_runtime_version_mismatch:{spec.name}")
    if "py313" in spec.name and not runtime_version.startswith("Python 3.13."):
        raise ValidationRunnerError(f"py313_runtime_version_mismatch:{spec.name}")
    if (
        sha256_file(executable) != executable_sha256
        or executable.stat().st_size != executable_bytes
    ):
        raise MetadataChildError(
            f"command_tool_changed_during_metadata:{spec.name}",
            name=f"runtime-version-{spec.name}",
            failure_kind="executable_changed_across_metadata",
            process_evidence=runtime_result.to_dict(),
            evidence_recorded=True,
        )
    return {
        "path": str(executable),
        "bytes": executable_bytes,
        "sha256": executable_sha256,
        "version_argv": list(version_argv),
        "version": version,
        "runtime_version_argv": list(runtime_version_argv),
        "runtime_version": runtime_version,
        "version_process_containment": result.to_dict(),
        "runtime_version_process_containment": runtime_result.to_dict(),
        "environment_commitment": environment.commitment,
    }


def command_plan(
    *,
    repository: Path,
    project_root: Path,
    head: str,
    tree: str,
    specs: Sequence[CommandSpec],
    child_environment: ChildEnvironment | None = None,
    expected_executable_sha256_by_command: Mapping[str, str] | None = None,
    metadata_evidence: list[dict[str, Any]] | None = None,
    before_metadata_child: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    expected_pins = dict(expected_executable_sha256_by_command or {})
    spec_names = {spec.name for spec in specs}
    if set(expected_pins) != spec_names or any(
        re.fullmatch(r"[0-9a-f]{64}", value) is None for value in expected_pins.values()
    ):
        raise ValidationRunnerError("command_plan_independent_pin_mapping_invalid")
    environment = child_environment or build_child_environment(
        project_root,
        ("git", *(spec.argv[0] for spec in specs)),
    )
    commands = []
    for spec in specs:
        identity_kwargs: dict[str, Any] = {
            "child_environment": environment,
            "expected_executable_sha256": expected_pins[spec.name],
            "metadata_evidence": metadata_evidence,
            "phase": "command_plan",
            "before_metadata_child": before_metadata_child,
        }
        commands.append(
            {
                "name": spec.name,
                "argv": list(spec.argv),
                "cwd": str(project_root),
                "expected_exit_code": spec.expected_exit_code,
                "required_output_tokens": list(spec.required_output_tokens),
                "wrapper_timeout_seconds": spec.wrapper_timeout_seconds,
                "residual_repoll_seconds": VALIDATION_RESIDUAL_REPOLL_SECONDS,
                "stream_drain_seconds": VALIDATION_STREAM_DRAIN_SECONDS,
                "tool": command_tool_identity(spec, **identity_kwargs),
            }
        )
    payload = {
        "repository": str(repository),
        "project_root": str(project_root),
        "head": head,
        "tree": tree,
        "commands": commands,
        "environment_commitment": environment.commitment,
        "observation_scope": VALIDATION_OBSERVATION_SCOPE,
    }
    return {
        **payload,
        "sha256": sha256_bytes(canonical_json_bytes(payload)),
    }


def _tail(raw: bytes, limit: int = 16_384) -> str:
    return raw[-limit:].decode("utf-8", errors="replace")


def _required_tokens_present(
    required_tokens: Sequence[str],
    *,
    stdout: str,
    stderr: str,
) -> bool:
    """Every token must be complete in one stream; stream-boundary synthesis is invalid."""

    return all(token in stdout or token in stderr for token in required_tokens)


def _sanitized_process_streams(
    process: ProcessOutcome | ProcessContainmentFailure,
    secret_values: Sequence[str],
) -> tuple[str, str, dict[str, Any], bool]:
    stdout, stdout_detected = _redact_secret_text(process.stdout, secret_values)
    stderr, stderr_detected = _redact_secret_text(process.stderr, secret_values)
    evidence, nested_detected = _sanitize_for_evidence(process.to_dict(), secret_values)
    if isinstance(evidence, dict):
        evidence["stdout"] = stdout
        evidence["stderr"] = stderr
    return stdout, stderr, evidence, stdout_detected or stderr_detected or nested_detected


def _validation_timeout_contract(spec: CommandSpec) -> TimeoutContract:
    wrapper = float(spec.wrapper_timeout_seconds)
    if wrapper <= 8.0:
        raise ValidationRunnerError(f"validation_wrapper_timeout_too_small:{spec.name}")
    return TimeoutContract(
        kubectl_timeout_seconds=8.0,
        wrapper_timeout_seconds=wrapper,
        restore_deadline_seconds=(
            wrapper + VALIDATION_RESIDUAL_REPOLL_SECONDS + VALIDATION_STREAM_DRAIN_SECONDS + 10.0
        ),
        residual_repoll_seconds=VALIDATION_RESIDUAL_REPOLL_SECONDS,
        stream_drain_seconds=VALIDATION_STREAM_DRAIN_SECONDS,
    )


def _stream_cleanup_evidence_errors(value: object) -> tuple[str, ...]:
    errors: list[str] = []
    root_keys = {
        "schema",
        "reason",
        "read_handle_owner",
        "bounded_by_restore_deadline",
        "readers",
        "all_reader_threads_exited",
        "forced_termination_attempts",
    }
    if not isinstance(value, Mapping) or set(value) != root_keys:
        return ("stream_cleanup_keys",)
    if (
        value.get("schema") != "evm.phase-b2.stream-reader-cleanup.v1"
        or not isinstance(value.get("reason"), str)
        or not value["reason"]
        or value.get("read_handle_owner") != "reader_thread"
        or value.get("bounded_by_restore_deadline") is not True
        or value.get("all_reader_threads_exited") is not True
        or type(value.get("forced_termination_attempts")) is not int
        or value["forced_termination_attempts"] != 0
    ):
        errors.append("stream_cleanup_root")
    readers = value.get("readers")
    if not isinstance(readers, list) or len(readers) != 2:
        return tuple(sorted({*errors, "stream_cleanup_readers"}))
    reader_keys = {
        "stream",
        "started",
        "native_thread_id",
        "drained_before_cleanup",
        "exited_before_cleanup",
        "cancel_attempted",
        "cancel_succeeded",
        "no_pending_io",
        "cancel_error_code",
        "exited_after_cleanup",
        "thread_alive_after_cleanup",
        "read_handle_close_scope",
        "bounded_join_timeout_seconds",
    }
    observed_streams: list[str] = []
    for reader in readers:
        if not isinstance(reader, Mapping) or set(reader) != reader_keys:
            errors.append("stream_cleanup_reader_keys")
            continue
        observed_streams.append(str(reader.get("stream")))
        join_timeout = reader.get("bounded_join_timeout_seconds")
        native_thread_id = reader.get("native_thread_id")
        if (
            reader.get("started") is not True
            or isinstance(native_thread_id, bool)
            or not isinstance(native_thread_id, int)
            or native_thread_id <= 0
            or type(reader.get("drained_before_cleanup")) is not bool
            or type(reader.get("exited_before_cleanup")) is not bool
            or type(reader.get("cancel_attempted")) is not bool
            or type(reader.get("cancel_succeeded")) is not bool
            or type(reader.get("no_pending_io")) is not bool
            or reader.get("exited_after_cleanup") is not True
            or reader.get("thread_alive_after_cleanup") is not False
            or reader.get("read_handle_close_scope") != "reader_read_pipe_finally"
            or isinstance(join_timeout, bool)
            or not isinstance(join_timeout, (int, float))
            or not math.isfinite(float(join_timeout))
            or not 0.0 <= float(join_timeout) <= 0.25
            or not (
                reader.get("cancel_error_code") is None
                or isinstance(reader.get("cancel_error_code"), (int, str))
            )
        ):
            errors.append("stream_cleanup_reader_state")
    if observed_streams != ["stdout", "stderr"]:
        errors.append("stream_cleanup_stream_order")
    return tuple(sorted(set(errors)))


def _containment_evidence_errors(outcome: ProcessOutcome) -> tuple[str, ...]:
    errors: list[str] = []
    exact_true = {
        "active_process_zero": outcome.active_process_zero,
        "streams_drained": outcome.streams_drained,
        "stdout_drained": outcome.stdout_drained,
        "stderr_drained": outcome.stderr_drained,
        "identity_coverage_complete": outcome.identity_coverage_complete,
    }
    exact_false = {
        "timed_out": outcome.timed_out,
        "cancelled": outcome.cancelled,
        "manual_intervention_required": outcome.manual_intervention_required,
    }
    errors.extend(name for name, value in exact_true.items() if value is not True)
    errors.extend(name for name, value in exact_false.items() if value is not False)
    if (
        isinstance(outcome.final_active_process_count, bool)
        or outcome.final_active_process_count != 0
    ):
        errors.append("final_active_process_count")
    if outcome.residual_pids != ():
        errors.append("residual_pids")
    if (
        isinstance(outcome.forced_termination_attempts, bool)
        or outcome.forced_termination_attempts != 0
    ):
        errors.append("forced_termination_attempts")
    if isinstance(outcome.job_limit_flags, bool) or outcome.job_limit_flags != 0:
        errors.append("job_limit_flags")
    if outcome.errors != ():
        errors.append("process_errors")
    errors.extend(_stream_cleanup_evidence_errors(outcome.stream_cleanup))
    for name, value in (
        ("stdout_capture_overflow", outcome.stdout_capture_overflow),
        ("stderr_capture_overflow", outcome.stderr_capture_overflow),
    ):
        if value is not False:
            errors.append(name)
    for name, total, stream in (
        ("stdout_total_bytes", outcome.stdout_total_bytes, outcome.stdout),
        ("stderr_total_bytes", outcome.stderr_total_bytes, outcome.stderr),
    ):
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or total != len(stream.encode("utf-8"))
        ):
            errors.append(name)
    if (
        isinstance(outcome.duration_seconds, bool)
        or not isinstance(outcome.duration_seconds, (int, float))
        or not math.isfinite(float(outcome.duration_seconds))
        or outcome.duration_seconds < 0
    ):
        errors.append("duration_seconds")
    try:
        normalized_uuid = str(uuid.UUID(outcome.run_uuid))
    except (TypeError, ValueError, AttributeError):
        normalized_uuid = ""
        errors.append("run_uuid")
    if not outcome.command or not Path(outcome.command[0]).is_absolute():
        errors.append("absolute_command")
    executable_identity = outcome.executable_identity
    expected_executable_keys = {
        "path",
        "sha256",
        "bytes",
        "device",
        "file_id",
        "expected_sha256",
        "pin_required",
        "pin_match",
        "measurement_scope",
        "handle_lock_held_through_create",
        "handle_lock_share_mode",
        "handle_lock_inheritable",
        "ancestor_directory_locks_held_through_create",
        "ancestor_directory_lock_count",
        "ancestor_directory_lock_share_mode",
        "path_lock_scope",
        "pre_kernel_create_gate_required",
        "pre_kernel_create_gate_passed",
        "pre_kernel_create_gate_invocations",
        "pre_kernel_remaining_seconds",
        "pre_kernel_required_seconds",
    }
    if not isinstance(executable_identity, Mapping) or set(executable_identity) != (
        expected_executable_keys
    ):
        errors.append("executable_identity")
    else:
        executable_path = executable_identity.get("path")
        executable_sha = executable_identity.get("sha256")
        if (
            not isinstance(executable_path, str)
            or os.path.normcase(os.path.normpath(executable_path))
            != os.path.normcase(os.path.normpath(outcome.command[0]))
            or not isinstance(executable_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", executable_sha) is None
            or executable_identity.get("expected_sha256") != executable_sha
            or executable_identity.get("pin_required") is not True
            or executable_identity.get("pin_match") is not True
            or executable_identity.get("measurement_scope") != "immediately_before_CreateProcessW"
            or executable_identity.get("handle_lock_held_through_create") is not True
            or executable_identity.get("handle_lock_share_mode") != "FILE_SHARE_READ_only"
            or executable_identity.get("handle_lock_inheritable") is not False
            or executable_identity.get("ancestor_directory_locks_held_through_create") is not True
            or isinstance(executable_identity.get("ancestor_directory_lock_count"), bool)
            or not isinstance(executable_identity.get("ancestor_directory_lock_count"), int)
            or executable_identity.get("ancestor_directory_lock_count", -1) < 0
            or executable_identity.get("ancestor_directory_lock_share_mode")
            != "FILE_SHARE_READ_WRITE_no_delete"
            or executable_identity.get("path_lock_scope") != "all_nonroot_ancestors_and_leaf"
            or executable_identity.get("pre_kernel_create_gate_required") is not True
            or executable_identity.get("pre_kernel_create_gate_passed") is not True
            or type(executable_identity.get("pre_kernel_create_gate_invocations")) is not int
            or executable_identity["pre_kernel_create_gate_invocations"] != 1
            or type(executable_identity.get("pre_kernel_remaining_seconds")) not in {int, float}
            or not math.isfinite(float(executable_identity["pre_kernel_remaining_seconds"]))
            or type(executable_identity.get("pre_kernel_required_seconds")) not in {int, float}
            or not math.isfinite(float(executable_identity["pre_kernel_required_seconds"]))
            or executable_identity["pre_kernel_required_seconds"] <= 0
            or executable_identity["pre_kernel_remaining_seconds"]
            < executable_identity["pre_kernel_required_seconds"]
            or isinstance(executable_identity.get("bytes"), bool)
            or not isinstance(executable_identity.get("bytes"), int)
            or executable_identity.get("bytes", 0) <= 0
            or isinstance(executable_identity.get("device"), bool)
            or not isinstance(executable_identity.get("device"), int)
            or isinstance(executable_identity.get("file_id"), bool)
            or not isinstance(executable_identity.get("file_id"), int)
        ):
            errors.append("executable_identity")

    identities = tuple(outcome.identities)
    stable_keys = {(item.pid, item.creation_time_ns) for item in identities}
    if not identities or len(stable_keys) != len(identities):
        errors.append("identity_set")
    identity_sequences: set[int] = set()
    for identity in identities:
        if (
            isinstance(identity.pid, bool)
            or not isinstance(identity.pid, int)
            or identity.pid <= 0
            or isinstance(identity.ppid, bool)
            or not isinstance(identity.ppid, int)
            or identity.ppid <= 0
            or isinstance(identity.creation_time_ns, bool)
            or not isinstance(identity.creation_time_ns, int)
            or identity.creation_time_ns <= 0
            or not identity.creation_time_utc
            or not identity.image
            or identity.run_uuid != normalized_uuid
            or isinstance(identity.observed_sequence, bool)
            or not isinstance(identity.observed_sequence, int)
            or identity.observed_sequence <= 0
        ):
            errors.append("identity_fields")
            break
        identity_sequences.add(identity.observed_sequence)
    if len(identity_sequences) != len(identities):
        errors.append("identity_observed_sequence")

    events = tuple(outcome.events)
    accounting = tuple(outcome.accounting)
    combined = [*events, *accounting]
    sequences = [item.sequence for item in combined]
    if (
        not events
        or not accounting
        or any(isinstance(value, bool) or not isinstance(value, int) for value in sequences)
        or sorted(sequences) != list(range(1, len(sequences) + 1))
    ):
        errors.append("event_accounting_sequence")
    else:
        ordered = sorted(combined, key=lambda item: item.sequence)
        if any(
            isinstance(item.monotonic_ns, bool)
            or not isinstance(item.monotonic_ns, int)
            or item.monotonic_ns <= 0
            or not item.timestamp_utc
            for item in ordered
        ) or any(
            prior.monotonic_ns > current.monotonic_ns
            for prior, current in zip(ordered, ordered[1:], strict=False)
        ):
            errors.append("event_accounting_time")

    named_events: dict[str, Any] = {}
    for event in events:
        named_events.setdefault(event.event, event)
    required_events = (
        "job_created",
        "root_created_suspended",
        "job_membership_verified",
        "identity_observed",
        "root_resumed",
        "active_process_count_zero",
        "streams_drained",
    )
    uniquely_required = tuple(name for name in required_events if name != "identity_observed")
    if any(
        sum(event.event == name for event in events) != 1 for name in uniquely_required
    ) or not any(event.event == "identity_observed" for event in events):
        errors.append("required_events")
    else:
        required_sequences = [named_events[name].sequence for name in required_events]
        if required_sequences != sorted(required_sequences):
            errors.append("required_event_order")
        root_pid = named_events["root_created_suspended"].pid
        if (
            root_pid is None
            or named_events["job_membership_verified"].pid != root_pid
            or named_events["root_resumed"].pid != root_pid
            or not any(identity.pid == root_pid for identity in identities)
        ):
            errors.append("root_identity")
        membership = named_events["job_membership_verified"].details
        if (
            set(membership) != {"active_processes", "job_limit_flags"}
            or type(membership.get("active_processes")) is not int
            or membership.get("active_processes") != 1
            or type(membership.get("job_limit_flags")) is not int
            or membership.get("job_limit_flags") != 0
            or named_events["job_created"].pid is not None
        ):
            errors.append("membership_evidence")
    identity_events = [item for item in events if item.event == "identity_observed"]
    observed_pairs = {(item.sequence, item.pid) for item in identity_events}
    expected_pairs = {(item.observed_sequence, item.pid) for item in identities}
    if len(observed_pairs) != len(identity_events) or observed_pairs != expected_pairs:
        errors.append("identity_event_binding")

    if accounting:
        final = accounting[-1]
        if (
            isinstance(final.total_processes, bool)
            or final.total_processes != len(identities)
            or isinstance(final.active_processes, bool)
            or final.active_processes != 0
            or final.active_pids != ()
        ):
            errors.append("final_accounting")
    return tuple(sorted(set(errors)))


def _containment_cleared(outcome: ProcessOutcome) -> bool:
    """Return whether another command may start, independent of its exit code."""

    return not _containment_evidence_errors(outcome)


def _run_metadata_child(
    argv: Sequence[str],
    *,
    cwd: Path,
    name: str,
    child_environment: ChildEnvironment | None = None,
    expected_executable_sha256: str | None = None,
) -> ProcessOutcome:
    command = _absolute_command(argv)
    environment = child_environment or build_child_environment(
        SCRIPT_PROJECT_ROOT,
        (command[0],),
    )
    executable_sha256 = expected_executable_sha256 or sha256_file(Path(command[0]))
    contract = TimeoutContract(
        kubectl_timeout_seconds=8.0,
        wrapper_timeout_seconds=METADATA_WRAPPER_TIMEOUT_SECONDS,
        restore_deadline_seconds=(
            METADATA_WRAPPER_TIMEOUT_SECONDS
            + VALIDATION_RESIDUAL_REPOLL_SECONDS
            + METADATA_STREAM_DRAIN_SECONDS
            + 10.0
        ),
        residual_repoll_seconds=VALIDATION_RESIDUAL_REPOLL_SECONDS,
        stream_drain_seconds=METADATA_STREAM_DRAIN_SECONDS,
    )
    process_runner = WindowsJobProcessRunner(contract)
    try:
        outcome = process_runner.run(
            command,
            name=f"r7s6-validation-metadata-{name}",
            cwd=cwd,
            env=environment.values,
            run_uuid=None,
            expected_executable_sha256=executable_sha256,
        )
    except ProcessContainmentFailure as exc:
        sanitized, secret_detected = _sanitize_for_evidence(
            exc.to_dict(), environment.secret_values
        )
        raise MetadataChildError(
            f"metadata_process_containment_failed:{name}:{exc.stage}",
            name=name,
            failure_kind="process_containment_failure",
            process_evidence=sanitized,
            secret_like_output_detected=secret_detected,
        ) from exc
    except Exception as exc:
        raise MetadataChildError(
            f"metadata_process_outcome_unproven:{name}",
            name=name,
            failure_kind="process_outcome_unproven",
            process_evidence={
                "name": f"r7s6-validation-metadata-{name}",
                "command": list(command),
                "child_start_attempted": True,
                "process_outcome_unproven": True,
                "exception_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "exception_message_disclosed": False,
                "forced_termination_attempts": 0,
                "automatic_retry_count": 0,
            },
        ) from exc
    sanitized, secret_detected = _sanitize_for_evidence(
        outcome.to_dict(), environment.secret_values
    )
    if secret_detected:
        raise MetadataChildError(
            f"metadata_secret_like_output_detected:{name}",
            name=name,
            failure_kind="secret_like_output_detected",
            process_evidence=sanitized,
            secret_like_output_detected=True,
        )
    containment_errors = list(_containment_evidence_errors(outcome))
    if outcome.command != command:
        containment_errors.append("command_identity")
    if outcome.name != f"r7s6-validation-metadata-{name}":
        containment_errors.append("process_name")
    if containment_errors:
        raise MetadataChildError(
            f"metadata_process_containment_not_clear:{name}",
            name=name,
            failure_kind="process_containment_not_clear",
            process_evidence={
                **sanitized,
                "derived_containment_errors": sorted(set(containment_errors)),
            },
        )
    if outcome.return_code != 0:
        raise MetadataChildError(
            f"metadata_process_exit_nonzero:{name}:{outcome.return_code}",
            name=name,
            failure_kind="exit_nonzero",
            process_evidence=sanitized,
        )
    return outcome


def _run_validation_child(
    spec: CommandSpec,
    *,
    project_root: Path,
    env: dict[str, str],
    expected_executable_sha256: str,
) -> ProcessOutcome:
    runner = WindowsJobProcessRunner(_validation_timeout_contract(spec))
    return runner.run(
        _absolute_command(spec.argv),
        name=f"r7s6-validation-{spec.name}",
        cwd=project_root,
        env=env,
        run_uuid=None,
        expected_executable_sha256=expected_executable_sha256,
    )


def _repository_snapshot(
    repository: Path,
    *,
    git_pin: ExecutablePin,
    untracked_pin: UntrackedInventoryPin,
    child_environment: ChildEnvironment,
    metadata_evidence: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    untracked = _verify_isolated_untracked_inventory(
        repository,
        pin=untracked_pin,
        git_pin=git_pin,
        child_environment=child_environment,
        metadata_evidence=metadata_evidence,
        phase=phase,
    )
    return {
        "head": _git(
            repository,
            "rev-parse",
            "HEAD",
            git_executable=git_pin.path,
            expected_git_sha256=git_pin.sha256,
            child_environment=child_environment,
            metadata_evidence=metadata_evidence,
            phase=f"{phase}:head",
        ),
        "tree": _git(
            repository,
            "rev-parse",
            "HEAD^{tree}",
            git_executable=git_pin.path,
            expected_git_sha256=git_pin.sha256,
            child_environment=child_environment,
            metadata_evidence=metadata_evidence,
            phase=f"{phase}:tree",
        ),
        "tracked_clean": not _git(
            repository,
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
            git_executable=git_pin.path,
            expected_git_sha256=git_pin.sha256,
            child_environment=child_environment,
            metadata_evidence=metadata_evidence,
            phase=f"{phase}:tracked",
        ),
        "untracked_inventory": untracked,
    }


def _repository_snapshot_matches(
    value: Mapping[str, Any],
    *,
    expected_head: str,
    expected_tree: str,
) -> bool:
    return bool(
        value.get("head") == expected_head
        and value.get("tree") == expected_tree
        and value.get("tracked_clean") is True
        and isinstance(value.get("untracked_inventory"), Mapping)
        and value["untracked_inventory"].get("matches_expected") is True
    )


def _publication_receipt(value: DurableBoundPublication) -> dict[str, Any]:
    receipt = value.to_dict()
    if (
        receipt.get("sha256") is None
        or receipt.get("bytes") is None
        or receipt.get("directory_flush_succeeded") is not True
        or receipt.get("replace_if_exists") is not False
        or receipt.get("same_handle_readback") is not True
        or receipt.get("file_identity_stable_across_rename") is not True
    ):
        raise ValidationRunnerError("validation_output_publication_receipt_invalid")
    return receipt


def _publication_attempt_snapshot(output_writer: object) -> list[dict[str, Any]]:
    snapshot = getattr(output_writer, "publication_attempts", None)
    if snapshot is None:
        return []
    value = snapshot() if callable(snapshot) else snapshot
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationRunnerError("validation_output_publication_attempt_ledger_invalid")
    return json.loads(canonical_json_bytes(value))


def _partial_output_directory_observation(path: Path) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "path": str(path),
        "exists": False,
        "lstat_only": True,
        "is_directory_entry": False,
        "is_regular_file_entry": False,
        "is_symlink": False,
        "is_reparse_point": False,
        "mode": None,
        "bytes": None,
        "mtime_ns": None,
        "observation_error_type": None,
    }
    try:
        exists = os.path.lexists(path)
        metadata = os.lstat(path) if exists else None
        observation.update(
            {
                "exists": exists,
                "is_directory_entry": bool(metadata and stat.S_ISDIR(metadata.st_mode)),
                "is_regular_file_entry": bool(metadata and stat.S_ISREG(metadata.st_mode)),
                "is_symlink": bool(metadata and stat.S_ISLNK(metadata.st_mode)),
                "is_reparse_point": bool(
                    metadata
                    and int(getattr(metadata, "st_file_attributes", 0))
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                ),
                "mode": int(metadata.st_mode) if metadata else None,
                "bytes": int(metadata.st_size) if metadata else None,
                "mtime_ns": int(metadata.st_mtime_ns) if metadata else None,
            }
        )
    except BaseException as exc:
        observation["observation_error_type"] = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return observation


def _initialization_failure_leaf(output_leaf: str, *, emergency: bool) -> str:
    digest = sha256_bytes(output_leaf.encode("utf-8"))[:16]
    kind = "initialization-emergency" if emergency else "initialization-failure"
    return validate_strict_windows_leaf(f"{kind}-{digest}.json")


def _initialization_failure_payload(
    *,
    parent: Path,
    output_leaf: str,
    error: BaseException,
    specs: Sequence[CommandSpec],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
) -> tuple[dict[str, Any], Path]:
    if isinstance(error, ValidationOutputInitializationFailure):
        stage = error.stage
        output_path = error.output_path
        root_error = error.original_error
    else:
        stage = "create_output_writer"
        output_path = parent / output_leaf
        root_error = error
    partial = _partial_output_directory_observation(output_path)
    failure = {
        "schema": f"{SCHEMA}.initialization-failure.v1",
        "status": "FAIL",
        "credit": "zero_credit",
        "failure_stage": stage,
        "exception_type": f"{type(root_error).__module__}.{type(root_error).__qualname__}",
        "exception_message_disclosed": False,
        "output_leaf": output_leaf,
        "partial_output_directory": partial,
        "environment_commitment": dict(environment_commitment),
        "output_parent_commitment": dict(output_parent),
        "planned_command_count": len(specs),
        "validation_child_call_count": 0,
        "metadata_child_call_count": 0,
        "terminal_execution_latch": True,
        "followup_child_count_after_terminal_latch": 0,
        "initialization_attempt_count": 1,
        "failure_seal_attempt_count": 1,
        "emergency_seal_attempt_count": 0,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
    }
    return failure, output_path


def _publish_initialization_failure_primary(
    *,
    parent: Path,
    output_leaf: str,
    error: BaseException,
    specs: Sequence[CommandSpec],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
) -> dict[str, Any]:
    failure, output_path = _initialization_failure_payload(
        parent=parent,
        output_leaf=output_leaf,
        error=error,
        specs=specs,
        environment_commitment=environment_commitment,
        output_parent=output_parent,
    )
    raw = canonical_json_bytes(failure)
    leaf = _initialization_failure_leaf(output_leaf, emergency=False)
    run_uuid = str(uuid.uuid4())
    publication = _publication_receipt(
        publish_bound_no_replace_durable(parent, leaf, raw, run_uuid=run_uuid)
    )
    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "output_directory": str(output_path),
        "initialization_failure_path": str(parent / leaf),
        "initialization_failure_bytes": len(raw),
        "initialization_failure_sha256": sha256_bytes(raw),
        "initialization_failure_publication": publication,
        "terminal_execution_latch": True,
        "followup_child_count_after_terminal_latch": 0,
        "automatic_retry_count": 0,
        "completion_marker_created": False,
    }


def _publish_initialization_emergency_seal(
    *,
    parent: Path,
    output_leaf: str,
    initialization_error: BaseException,
    failure_seal_error: BaseException,
    specs: Sequence[CommandSpec],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        failure, output_path = _initialization_failure_payload(
            parent=parent,
            output_leaf=output_leaf,
            error=initialization_error,
            specs=specs,
            environment_commitment=environment_commitment,
            output_parent=output_parent,
        )
        raw = canonical_json_bytes(failure)
        failure_leaf = _initialization_failure_leaf(output_leaf, emergency=False)
        failed_publication = None
        if isinstance(failure_seal_error, DurablePublicationError):
            failed_publication = failure_seal_error.observation.to_dict()
        emergency = {
            **failure,
            "schema": f"{SCHEMA}.initialization-emergency-seal.v1",
            "failure_seal_exception_type": (
                f"{type(failure_seal_error).__module__}.{type(failure_seal_error).__qualname__}"
            ),
            "failure_seal_intended_path": str(parent / failure_leaf),
            "failure_seal_expected_sha256": sha256_bytes(raw),
            "failure_seal_expected_bytes": len(raw),
            "failure_seal_partial_observation": failed_publication,
            "emergency_seal_attempt_count": 1,
        }
        emergency_raw = canonical_json_bytes(emergency)
        emergency_leaf = _initialization_failure_leaf(output_leaf, emergency=True)
        emergency_publication = _publication_receipt(
            publish_bound_no_replace_durable(
                parent,
                emergency_leaf,
                emergency_raw,
                run_uuid=str(uuid.uuid4()),
            )
        )
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "output_directory": str(output_path),
            "initialization_emergency_seal_path": str(parent / emergency_leaf),
            "initialization_emergency_seal_bytes": len(emergency_raw),
            "initialization_emergency_seal_sha256": sha256_bytes(emergency_raw),
            "initialization_emergency_seal_publication": emergency_publication,
            "terminal_execution_latch": True,
            "followup_child_count_after_terminal_latch": 0,
            "automatic_retry_count": 0,
            "completion_marker_created": False,
        }
    except BaseException as emergency_error:
        raise ValidationRunnerError(
            "validation_output_initialization_failure_and_emergency_seal_failed"
        ) from emergency_error


def _publish_initialization_failure(
    *,
    parent: Path,
    output_leaf: str,
    error: BaseException,
    specs: Sequence[CommandSpec],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return _publish_initialization_failure_primary(
            parent=parent,
            output_leaf=output_leaf,
            error=error,
            specs=specs,
            environment_commitment=environment_commitment,
            output_parent=output_parent,
        )
    except BaseException as failure_seal_error:
        return _publish_initialization_emergency_seal(
            parent=parent,
            output_leaf=output_leaf,
            initialization_error=error,
            failure_seal_error=failure_seal_error,
            specs=specs,
            environment_commitment=environment_commitment,
            output_parent=output_parent,
        )


def _publish_terminal_failure(
    *,
    output_writer: _BoundValidationOutput,
    output: Path,
    stage: str,
    reason: str,
    specs: Sequence[CommandSpec],
    validation_child_call_count: int,
    command_refs: Sequence[Mapping[str, Any]],
    metadata_evidence: Sequence[Mapping[str, Any]],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
    failure_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    prior_publication_attempts = _publication_attempt_snapshot(output_writer)
    terminal = {
        "schema": TERMINAL_FAILURE_SCHEMA,
        "status": "FAIL",
        "failure_stage": stage,
        "failure_reason": reason,
        "failure_evidence": dict(failure_evidence or {}),
        "environment_commitment": dict(environment_commitment),
        "output_parent_commitment": dict(output_parent),
        "planned_command_count": len(specs),
        "validation_child_call_count": validation_child_call_count,
        "completed_command_records": list(command_refs),
        "not_run_commands": [item.name for item in specs[validation_child_call_count:]],
        "metadata_children": list(metadata_evidence),
        "metadata_child_call_count": _metadata_child_call_count(metadata_evidence),
        "publication_attempts_before_terminal_seal": prior_publication_attempts,
        "publication_attempt_count_before_terminal_seal": len(prior_publication_attempts),
        "terminal_execution_latch": True,
        "terminal_containment_latch": True,
        "followup_child_count_after_terminal_latch": 0,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
    }
    raw = canonical_json_bytes(terminal)
    path = output / "terminal-validation-failure.json"
    publication = _publication_receipt(output_writer.publish(path.name, raw))
    final_publication_attempts = _publication_attempt_snapshot(output_writer)
    result = {
        "schema": SCHEMA,
        "status": "FAIL",
        "output_directory": str(output),
        "terminal_failure_path": str(path),
        "terminal_failure_bytes": len(raw),
        "terminal_failure_sha256": sha256_bytes(raw),
        "terminal_failure_publication": publication,
        "publication_attempts": final_publication_attempts,
        "publication_attempt_count": len(final_publication_attempts),
        "terminal_execution_latch": True,
        "followup_child_count_after_terminal_latch": 0,
        "automatic_retry_count": 0,
        "completion_marker_created": False,
    }
    return result


def _publish_terminal_emergency_seal(
    *,
    output_writer: _BoundValidationOutput,
    output: Path,
    stage: str,
    original_error: BaseException,
    terminal_seal_error: BaseException,
    specs: Sequence[CommandSpec],
    validation_child_call_count: int,
    command_refs: Sequence[Mapping[str, Any]],
    metadata_evidence: Sequence[Mapping[str, Any]],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        prior_publication_attempts = _publication_attempt_snapshot(output_writer)
        publication_attempt_observation_error_type = None
    except BaseException as observation_error:
        prior_publication_attempts = []
        publication_attempt_observation_error_type = (
            f"{type(observation_error).__module__}.{type(observation_error).__qualname__}"
        )
    emergency = {
        "schema": f"{SCHEMA}.terminal-emergency-seal.v1",
        "status": "FAIL",
        "credit": "zero_credit",
        "failure_stage": stage,
        "original_exception_type": (
            f"{type(original_error).__module__}.{type(original_error).__qualname__}"
        ),
        "terminal_seal_exception_type": (
            f"{type(terminal_seal_error).__module__}.{type(terminal_seal_error).__qualname__}"
        ),
        "environment_commitment": dict(environment_commitment),
        "output_parent_commitment": dict(output_parent),
        "planned_command_count": len(specs),
        "validation_child_call_count": validation_child_call_count,
        "completed_command_records": list(command_refs),
        "not_run_commands": [item.name for item in specs[validation_child_call_count:]],
        "metadata_children": list(metadata_evidence),
        "metadata_child_call_count": _metadata_child_call_count(metadata_evidence),
        "publication_attempts_before_emergency_seal": prior_publication_attempts,
        "publication_attempt_count_before_emergency_seal": len(prior_publication_attempts),
        "publication_attempt_observation_error_type": (publication_attempt_observation_error_type),
        "failed_output_directory_observation": _partial_output_directory_observation(output),
        "emergency_publication_scope": "independent_parent_sibling_writer",
        "terminal_execution_latch": True,
        "followup_child_count_after_terminal_latch": 0,
        "terminal_seal_attempt_count": 1,
        "emergency_seal_attempt_count": 1,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
    }
    raw = canonical_json_bytes(emergency)
    sibling_leaf = validate_strict_windows_leaf(
        f"terminal-emergency-{sha256_bytes(output.name.encode('utf-8'))[:16]}.json"
    )
    path = output.parent / sibling_leaf
    try:
        publication = _publication_receipt(
            publish_bound_no_replace_durable(
                output.parent,
                sibling_leaf,
                raw,
                run_uuid=str(uuid.uuid4()),
            )
        )
    except BaseException as emergency_error:
        raise ValidationRunnerError("validation_terminal_and_emergency_seal_failed") from (
            emergency_error
        )
    try:
        final_publication_attempts = _publication_attempt_snapshot(output_writer)
    except BaseException:
        final_publication_attempts = prior_publication_attempts
    result = {
        "schema": SCHEMA,
        "status": "FAIL",
        "output_directory": str(output),
        "terminal_emergency_seal_path": str(path),
        "terminal_emergency_seal_bytes": len(raw),
        "terminal_emergency_seal_sha256": sha256_bytes(raw),
        "terminal_emergency_seal_publication": publication,
        "publication_attempts": final_publication_attempts,
        "publication_attempt_count": len(final_publication_attempts),
        "terminal_execution_latch": True,
        "followup_child_count_after_terminal_latch": 0,
        "automatic_retry_count": 0,
        "completion_marker_created": False,
    }
    return result


def _publish_post_writer_emergency_seal(
    *,
    parent: Path,
    output_leaf: str,
    error: BaseException,
    specs: Sequence[CommandSpec],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
    output_writer: _BoundValidationOutput,
) -> dict[str, Any]:
    """Best-effort sibling seal for failures after output admission.

    This writer is deliberately independent of the admitted output-directory
    handle.  Its own failure remains an explicit, unguaranteed terminal case;
    callers must never treat it as a retry or as evidence of success.
    """

    try:
        try:
            publication_attempts = _publication_attempt_snapshot(output_writer)
            publication_attempt_observation_error_type = None
        except BaseException as observation_error:
            publication_attempts = []
            publication_attempt_observation_error_type = (
                f"{type(observation_error).__module__}.{type(observation_error).__qualname__}"
            )
        output_path = parent / output_leaf
        emergency = {
            "schema": f"{SCHEMA}.post-writer-emergency-seal.v1",
            "status": "FAIL",
            "credit": "zero_credit",
            "failure_stage": "post_writer_admission_before_or_outside_terminal_boundary",
            "exception_type": f"{type(error).__module__}.{type(error).__qualname__}",
            "exception_message_disclosed": False,
            "output_leaf": output_leaf,
            "failed_output_directory_observation": _partial_output_directory_observation(
                output_path
            ),
            "environment_commitment": dict(environment_commitment),
            "output_parent_commitment": dict(output_parent),
            "planned_command_count": len(specs),
            "validation_child_call_count": "unproven",
            "metadata_child_call_count": "unproven",
            "publication_attempts_before_emergency_seal": publication_attempts,
            "publication_attempt_count_before_emergency_seal": len(publication_attempts),
            "publication_attempt_observation_error_type": (
                publication_attempt_observation_error_type
            ),
            "terminal_execution_latch": True,
            "followup_child_count_after_terminal_latch": 0,
            "terminal_seal_attempt_state": "unproven",
            "emergency_seal_attempt_count": 1,
            "automatic_retry_count": 0,
            "forced_termination_attempts": 0,
            "completion_marker_created": False,
            "success_marker_created": False,
            "r8_authorized": False,
        }
        raw = canonical_json_bytes(emergency)
        sibling_leaf = validate_strict_windows_leaf(
            f"post-writer-emergency-{sha256_bytes(output_leaf.encode('utf-8'))[:16]}.json"
        )
        publication = _publication_receipt(
            publish_bound_no_replace_durable(
                parent,
                sibling_leaf,
                raw,
                run_uuid=str(uuid.uuid4()),
            )
        )
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "output_directory": str(output_path),
            "post_writer_emergency_seal_path": str(parent / sibling_leaf),
            "post_writer_emergency_seal_bytes": len(raw),
            "post_writer_emergency_seal_sha256": sha256_bytes(raw),
            "post_writer_emergency_seal_publication": publication,
            "terminal_execution_latch": True,
            "followup_child_count_after_terminal_latch": 0,
            "automatic_retry_count": 0,
            "completion_marker_created": False,
        }
    except BaseException as emergency_error:
        raise ValidationRunnerError("validation_post_writer_emergency_seal_failed") from (
            emergency_error
        )


def _dispatch_validation_failure(
    *,
    state: dict[str, bool],
    parent: Path,
    output_leaf: str,
    error: BaseException,
    specs: Sequence[CommandSpec],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
    output_writer: _BoundValidationOutput | None,
) -> dict[str, Any]:
    """Enter the ordinary failure publisher exactly once.

    ``state`` is mutated inside this callee so an interruption at the caller's
    dispatch CALL is distinguishable from an exception after this continuation
    actually began.  The caller may then enter a distinct emergency writer, but
    must never retry this ordinary dispatcher.
    """

    if state.get("entered") is True:
        raise ValidationRunnerError("validation_failure_dispatch_reentry_forbidden")
    state["entered"] = True
    if output_writer is None:
        return _publish_initialization_failure(
            parent=parent,
            output_leaf=output_leaf,
            error=error,
            specs=specs,
            environment_commitment=environment_commitment,
            output_parent=output_parent,
        )
    return _publish_post_writer_emergency_seal(
        parent=parent,
        output_leaf=output_leaf,
        error=error,
        specs=specs,
        environment_commitment=environment_commitment,
        output_parent=output_parent,
        output_writer=output_writer,
    )


def _publish_dispatch_boundary_emergency_seal(
    *,
    parent: Path,
    output_leaf: str,
    original_error: BaseException,
    dispatch_error: BaseException,
    specs: Sequence[CommandSpec],
    environment_commitment: Mapping[str, Any],
    output_parent: Mapping[str, Any],
    output_writer: _BoundValidationOutput | None,
) -> dict[str, Any]:
    """Use a distinct sibling after the ordinary failure dispatcher entered.

    This is deliberately not a retry of the initialization/post-writer seal.
    Failure of this independent writer is the honestly unguaranteed terminal
    case described by :func:`validation_failure_seal_contract`.
    """

    try:
        output_path = parent / output_leaf
        publication_attempts: list[dict[str, Any]] = []
        publication_attempt_observation_error_type: str | None = None
        if output_writer is not None:
            try:
                publication_attempts = _publication_attempt_snapshot(output_writer)
            except BaseException as observation_error:
                publication_attempt_observation_error_type = (
                    f"{type(observation_error).__module__}.{type(observation_error).__qualname__}"
                )
        payload = {
            "schema": f"{SCHEMA}.dispatch-boundary-emergency-seal.v1",
            "status": "FAIL",
            "credit": "zero_credit",
            "failure_stage": "validation_failure_dispatch_boundary",
            "original_exception_type": (
                f"{type(original_error).__module__}.{type(original_error).__qualname__}"
            ),
            "dispatch_exception_type": (
                f"{type(dispatch_error).__module__}.{type(dispatch_error).__qualname__}"
            ),
            "exception_messages_disclosed": False,
            "output_leaf": output_leaf,
            "failed_output_directory_observation": _partial_output_directory_observation(
                output_path
            ),
            "environment_commitment": dict(environment_commitment),
            "output_parent_commitment": dict(output_parent),
            "planned_command_count": len(specs),
            "validation_child_call_count": "unproven",
            "metadata_child_call_count": "unproven",
            "ordinary_failure_dispatch_entered": True,
            "ordinary_failure_dispatch_retry_count": 0,
            "publication_attempts_before_emergency_seal": publication_attempts,
            "publication_attempt_count_before_emergency_seal": len(publication_attempts),
            "publication_attempt_observation_error_type": (
                publication_attempt_observation_error_type
            ),
            "terminal_execution_latch": True,
            "followup_child_count_after_terminal_latch": 0,
            "emergency_seal_attempt_count": 1,
            "automatic_retry_count": 0,
            "forced_termination_attempts": 0,
            "completion_marker_created": False,
            "success_marker_created": False,
            "r8_authorized": False,
        }
        raw = canonical_json_bytes(payload)
        sibling_leaf = validate_strict_windows_leaf(
            f"dispatch-boundary-emergency-{sha256_bytes(output_leaf.encode('utf-8'))[:16]}.json"
        )
        publication = _publication_receipt(
            publish_bound_no_replace_durable(
                parent,
                sibling_leaf,
                raw,
                run_uuid=str(uuid.uuid4()),
            )
        )
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "output_directory": str(output_path),
            "dispatch_boundary_emergency_seal_path": str(parent / sibling_leaf),
            "dispatch_boundary_emergency_seal_bytes": len(raw),
            "dispatch_boundary_emergency_seal_sha256": sha256_bytes(raw),
            "dispatch_boundary_emergency_seal_publication": publication,
            "terminal_execution_latch": True,
            "followup_child_count_after_terminal_latch": 0,
            "automatic_retry_count": 0,
            "completion_marker_created": False,
        }
    except BaseException as emergency_error:
        raise ValidationRunnerError(
            "validation_failure_dispatch_and_boundary_emergency_seal_failed"
        ) from emergency_error


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    repository = args.repository.resolve(strict=True)
    project_root = args.project_root.resolve(strict=True)
    if project_root != SCRIPT_PROJECT_ROOT:
        raise ValidationRunnerError("validation_script_project_origin_mismatch")
    publisher_origin = Path(publisher.__file__).resolve(strict=True)
    if SCRIPT_PROJECT_ROOT not in publisher_origin.parents:
        raise ValidationRunnerError("validation_publisher_module_origin_mismatch")
    if repository not in project_root.parents:
        raise ValidationRunnerError("project_root_not_inside_repository")
    executable_pins = _executable_pins_from_args(args)
    untracked_pin = _untracked_inventory_pin_from_args(args)
    try:
        output_leaf = validate_strict_windows_leaf(args.output_leaf)
    except Exception as exc:
        raise ValidationRunnerError("validation_output_leaf_invalid") from exc
    specs = build_command_specs(
        repository=repository,
        project_root=project_root,
        python_general=executable_pins["python_general"].path,
        python_host=executable_pins["python_host"].path,
        python_ruff=executable_pins["python_ruff"].path,
        git_executable=executable_pins["git"].path,
        git_executable_sha256=executable_pins["git"].sha256,
        powershell_executable=executable_pins["powershell"].path,
    )
    command_sha256_pins = command_executable_sha256_pins(specs, executable_pins)
    child_environment = build_child_environment(
        project_root,
        tuple(str(pin.path) for pin in executable_pins.values()),
    )
    if not hasattr(args, "expected_output_parent") or not hasattr(
        args, "expected_output_parent_sha256"
    ):
        raise ValidationRunnerError("validation_output_parent_gate_required")
    parent, parent_commitment = _validate_output_parent_gate(
        args.output_parent,
        expected_path=args.expected_output_parent,
        expected_sha256=args.expected_output_parent_sha256,
        forbidden_roots=(repository, project_root),
    )
    output_writer: _BoundValidationOutput | None = None
    failure_cause: BaseException | None = None
    dispatch_state = {"entered": False}
    try:
        try:
            output_writer = _BoundValidationOutput.create(parent, output_leaf)
            return _run_validation_with_bound_output(
                args=args,
                repository=repository,
                project_root=project_root,
                executable_pins=executable_pins,
                untracked_pin=untracked_pin,
                specs=specs,
                command_sha256_pins=command_sha256_pins,
                child_environment=child_environment,
                parent_commitment=parent_commitment,
                output_writer=output_writer,
            )
        except BaseException as exc:
            failure_cause = exc  # validation-failure-handler-continuation
        if failure_cause is None:
            raise ValidationRunnerError("validation_failure_cause_missing")
        dispatched = _dispatch_validation_failure(  # validation-failure-dispatch-call
            state=dispatch_state,
            parent=parent,
            output_leaf=output_leaf,
            error=failure_cause,
            specs=specs,
            environment_commitment=child_environment.commitment,
            output_parent=parent_commitment,
            output_writer=output_writer,
        )
    except BaseException as dispatch_error:
        effective_error = failure_cause if failure_cause is not None else dispatch_error
        if dispatch_state["entered"] is not True:
            return _dispatch_validation_failure(
                state=dispatch_state,
                parent=parent,
                output_leaf=output_leaf,
                error=effective_error,
                specs=specs,
                environment_commitment=child_environment.commitment,
                output_parent=parent_commitment,
                output_writer=output_writer,
            )
        return _publish_dispatch_boundary_emergency_seal(
            parent=parent,
            output_leaf=output_leaf,
            original_error=effective_error,
            dispatch_error=dispatch_error,
            specs=specs,
            environment_commitment=child_environment.commitment,
            output_parent=parent_commitment,
            output_writer=output_writer,
        )
    else:
        return dispatched
    finally:
        if output_writer is not None:
            try:
                output_writer.close()
            except BaseException:
                pass


def _run_validation_with_bound_output(
    *,
    args: argparse.Namespace,
    repository: Path,
    project_root: Path,
    executable_pins: Mapping[str, ExecutablePin],
    untracked_pin: UntrackedInventoryPin,
    specs: Sequence[CommandSpec],
    command_sha256_pins: Mapping[str, str],
    child_environment: ChildEnvironment,
    parent_commitment: Mapping[str, Any],
    output_writer: _BoundValidationOutput,
) -> dict[str, Any]:
    output = output_writer.path
    command_refs: list[dict[str, Any]] = []
    metadata_evidence: list[dict[str, Any]] = []
    validation_child_call_count = 0
    failure_stage = "initial_repository_preflight"
    terminal_seal_attempted = False

    def terminal_once(**kwargs: Any) -> dict[str, Any]:
        nonlocal terminal_seal_attempted
        if terminal_seal_attempted:
            raise ValidationRunnerError("validation_terminal_seal_reentry_forbidden")
        terminal_seal_attempted = True
        return _publish_terminal_failure(**kwargs)

    def metadata_terminal(
        exc: MetadataChildError,
        *,
        stage: str,
        extra_failure_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        failed_metadata = {
            "name": exc.name,
            "phase": stage,
            "status": "FAIL",
            "child_invoked": True,
            "failure_kind": exc.failure_kind,
            "secret_like_output_detected": exc.secret_like_output_detected,
            "process_containment": exc.process_evidence,
        }
        if not exc.evidence_recorded:
            metadata_evidence.append(failed_metadata)
        failure_evidence = {"metadata_failure": failed_metadata}
        if extra_failure_evidence:
            failure_evidence.update(extra_failure_evidence)
        return terminal_once(
            output_writer=output_writer,
            output=output,
            stage=stage,
            reason=exc.failure_kind,
            specs=specs,
            validation_child_call_count=validation_child_call_count,
            command_refs=command_refs,
            metadata_evidence=metadata_evidence,
            environment_commitment=child_environment.commitment,
            output_parent=parent_commitment,
            failure_evidence=failure_evidence,
        )

    try:

        def verify_untracked_before_metadata_child(child_name: str) -> None:
            _verify_isolated_untracked_inventory(
                repository,
                pin=untracked_pin,
                git_pin=executable_pins["git"],
                child_environment=child_environment,
                metadata_evidence=metadata_evidence,
                phase=f"command_plan:before:{child_name}",
            )

        try:
            initial_repository = _repository_snapshot(
                repository,
                git_pin=executable_pins["git"],
                untracked_pin=untracked_pin,
                child_environment=child_environment,
                metadata_evidence=metadata_evidence,
                phase="initial_repository_preflight",
            )
        except MetadataChildError as exc:
            return metadata_terminal(exc, stage="initial_repository_metadata")
        if not _repository_snapshot_matches(
            initial_repository,
            expected_head=args.expected_head,
            expected_tree=args.expected_tree,
        ):
            return terminal_once(
                output_writer=output_writer,
                output=output,
                stage="initial_repository_preflight",
                reason="repository_identity_or_cleanliness_mismatch",
                specs=specs,
                validation_child_call_count=validation_child_call_count,
                command_refs=command_refs,
                metadata_evidence=metadata_evidence,
                environment_commitment=child_environment.commitment,
                output_parent=parent_commitment,
                failure_evidence={"repository_observation": initial_repository},
            )
        try:
            failure_stage = "command_plan"
            plan = command_plan(
                repository=repository,
                project_root=project_root,
                head=args.expected_head,
                tree=args.expected_tree,
                specs=specs,
                child_environment=child_environment,
                expected_executable_sha256_by_command=command_sha256_pins,
                metadata_evidence=metadata_evidence,
                before_metadata_child=verify_untracked_before_metadata_child,
            )
        except MetadataChildError as exc:
            return metadata_terminal(exc, stage="command_plan_metadata")

        all_passed = True
        terminal_latched = False
        terminal_latch_reason: str | None = None
        for index, (spec, planned) in enumerate(zip(specs, plan["commands"], strict=True), start=1):
            failure_stage = f"command:{spec.name}:preflight"
            try:
                before = _repository_snapshot(
                    repository,
                    git_pin=executable_pins["git"],
                    untracked_pin=untracked_pin,
                    child_environment=child_environment,
                    metadata_evidence=metadata_evidence,
                    phase=f"command:{spec.name}:preflight",
                )
            except MetadataChildError as exc:
                return metadata_terminal(exc, stage=f"command:{spec.name}:preflight_metadata")
            if not _repository_snapshot_matches(
                before,
                expected_head=args.expected_head,
                expected_tree=args.expected_tree,
            ):
                return terminal_once(
                    output_writer=output_writer,
                    output=output,
                    stage=f"command:{spec.name}:repository_preflight",
                    reason="repository_identity_or_cleanliness_mismatch",
                    specs=specs,
                    validation_child_call_count=validation_child_call_count,
                    command_refs=command_refs,
                    metadata_evidence=metadata_evidence,
                    environment_commitment=child_environment.commitment,
                    output_parent=parent_commitment,
                    failure_evidence={"repository_observation": before},
                )

            started = datetime.now(UTC)
            start_ns = time.monotonic_ns()
            containment_failure: ProcessContainmentFailure | None = None
            outcome: ProcessOutcome | None = None
            failure_stage = f"command:{spec.name}:execution"
            try:
                _verify_isolated_untracked_inventory(
                    repository,
                    pin=untracked_pin,
                    git_pin=executable_pins["git"],
                    child_environment=child_environment,
                    metadata_evidence=metadata_evidence,
                    phase=f"command:{spec.name}:immediately_before_execution",
                )
                executable = validate_independent_executable_pin(
                    Path(spec.argv[0]),
                    command_sha256_pins[spec.name],
                    label=f"validation_command_{spec.name}",
                )
                if planned["tool"]["sha256"] != executable.sha256:
                    raise ValidationRunnerError(
                        f"validation_planned_independent_sha256_mismatch:{spec.name}"
                    )
                validation_child_call_count += 1
                outcome = _run_validation_child(
                    spec,
                    project_root=project_root,
                    env=child_environment.values,
                    expected_executable_sha256=command_sha256_pins[spec.name],
                )
            except MetadataChildError as exc:
                return metadata_terminal(
                    exc,
                    stage=f"command:{spec.name}:immediate_inventory_metadata",
                )
            except ProcessContainmentFailure as exc:
                containment_failure = exc
            duration_ns = time.monotonic_ns() - start_ns
            ended = datetime.now(UTC)
            process = outcome if outcome is not None else containment_failure
            assert process is not None
            stdout, stderr, process_evidence, secret_output_detected = _sanitized_process_streams(
                process, child_environment.secret_values
            )
            containment_errors = list(
                _containment_evidence_errors(outcome) if outcome is not None else ("failure",)
            )
            if outcome is not None:
                if outcome.command != _absolute_command(spec.argv):
                    containment_errors.append("command_identity")
                if outcome.name != f"r7s6-validation-{spec.name}":
                    containment_errors.append("process_name")
                if outcome.executable_identity.get("sha256") != planned["tool"]["sha256"]:
                    containment_errors.append("planned_executable_sha256")
            containment_errors = sorted(set(containment_errors))
            containment_clear = outcome is not None and not containment_errors

            after: dict[str, Any] | None = None
            if containment_clear and not secret_output_detected:
                try:
                    after = _repository_snapshot(
                        repository,
                        git_pin=executable_pins["git"],
                        untracked_pin=untracked_pin,
                        child_environment=child_environment,
                        metadata_evidence=metadata_evidence,
                        phase=f"command:{spec.name}:postflight",
                    )
                except MetadataChildError as exc:
                    return metadata_terminal(
                        exc,
                        stage=f"command:{spec.name}:postflight_metadata",
                        extra_failure_evidence={
                            "validation_command_name": spec.name,
                            "validation_process_containment": process_evidence,
                        },
                    )
            identity_stable = bool(
                after is not None
                and _repository_snapshot_matches(
                    after,
                    expected_head=args.expected_head,
                    expected_tree=args.expected_tree,
                )
                and before == after
            )
            required_tokens_present = _required_tokens_present(
                spec.required_output_tokens,
                stdout=stdout,
                stderr=stderr,
            )
            return_code = process.return_code
            passed = bool(
                return_code == spec.expected_exit_code
                and containment_clear
                and identity_stable
                and required_tokens_present
                and not secret_output_detected
            )
            all_passed = all_passed and passed
            stdout_bytes = stdout.encode("utf-8")
            stderr_bytes = stderr.encode("utf-8")
            record = {
                "schema": publisher.COMMAND_EVIDENCE_SCHEMA,
                "name": spec.name,
                "status": "PASS" if passed else "FAIL",
                "exit_code": return_code,
                "expected_exit_code": spec.expected_exit_code,
                "argv": list(spec.argv),
                "cwd": str(project_root),
                "repository": str(repository),
                "repository_head_before": before["head"],
                "repository_head_after": after["head"] if after is not None else "not_run",
                "repository_tree_before": before["tree"],
                "repository_tree_after": after["tree"] if after is not None else "not_run",
                "tracked_clean_before": before["tracked_clean"],
                "tracked_clean_after": after["tracked_clean"] if after is not None else None,
                "untracked_inventory_before": before["untracked_inventory"],
                "untracked_inventory_after": (
                    after["untracked_inventory"] if after is not None else None
                ),
                "command_plan_sha256": plan["sha256"],
                "tool": planned["tool"],
                "environment_commitment": child_environment.commitment,
                "output_parent_commitment": parent_commitment,
                "started_at_utc": started.isoformat().replace("+00:00", "Z"),
                "ended_at_utc": ended.isoformat().replace("+00:00", "Z"),
                "duration_ns": duration_ns,
                "stdout_bytes": len(stdout_bytes),
                "stdout_sha256": sha256_bytes(stdout_bytes),
                "stdout_tail": _tail(stdout_bytes),
                "stderr_bytes": len(stderr_bytes),
                "stderr_sha256": sha256_bytes(stderr_bytes),
                "stderr_tail": _tail(stderr_bytes),
                "stream_encoding": "utf-8",
                "stream_hash_scope": "decoded_text_reencoded_utf8_not_raw_pipe_bytes",
                "stream_boundary_token_synthesis_allowed": False,
                "required_tokens_present_in_individual_streams": required_tokens_present,
                "secret_like_output_detected": secret_output_detected,
                "process_containment": process_evidence,
                "derived_containment_errors": list(containment_errors),
                "containment_cleared_before_followup": containment_clear,
                "followup_child_count_after_containment_latch": 0,
                "forced_termination_attempts": process_evidence.get(
                    "forced_termination_attempts", 0
                ),
                "automatic_retry_count": 0,
                "orchestrator_prohibited_live_command_calls": 0,
                "live_call_observation_scope": VALIDATION_OBSERVATION_SCOPE,
            }
            raw = canonical_json_bytes(record)
            path = output / f"{index:02d}-{spec.name}.json"
            failure_stage = f"command:{spec.name}:evidence_publication"
            publication = _publication_receipt(output_writer.publish(path.name, raw))
            command_refs.append(
                {
                    "name": spec.name,
                    "status": record["status"],
                    "exit_code": return_code,
                    "expected_exit_code": spec.expected_exit_code,
                    "evidence_path": str(path),
                    "evidence_bytes": len(raw),
                    "evidence_sha256": sha256_bytes(raw),
                    "publication": publication,
                }
            )
            if not containment_clear:
                terminal_latched = True
                terminal_latch_reason = "process_containment_not_clear"
            elif secret_output_detected:
                terminal_latched = True
                terminal_latch_reason = "secret_like_output_detected"
            elif not identity_stable:
                terminal_latched = True
                terminal_latch_reason = "repository_postflight_mismatch"
            if terminal_latched:
                all_passed = False
                break

        failure_stage = "summary_serialization"
        summary = {
            "schema": publisher.VALIDATION_SCHEMA,
            "status": "PASS" if all_passed else "FAIL",
            "repository": str(repository),
            "project_root": str(project_root),
            "head": args.expected_head,
            "tree": args.expected_tree,
            "command_plan": plan,
            "command_plan_sha256": plan["sha256"],
            "environment_commitment": child_environment.commitment,
            "output_parent_commitment": parent_commitment,
            "independent_executable_pins": {
                name: {"path": str(pin.path), "sha256": pin.sha256}
                for name, pin in sorted(executable_pins.items())
            },
            "expected_untracked_inventory": {
                "count": untracked_pin.count,
                "path_list_sha256": untracked_pin.path_list_sha256,
                "content_inventory_sha256": untracked_pin.content_inventory_sha256,
            },
            "metadata_children": metadata_evidence,
            "metadata_child_call_count": _metadata_child_call_count(metadata_evidence),
            "commands": command_refs,
            "planned_command_count": len(specs),
            "executed_command_count": len(command_refs),
            "validation_child_call_count": validation_child_call_count,
            "not_run_commands": [spec.name for spec in specs[len(command_refs) :]],
            "terminal_containment_latch": terminal_latched,
            "terminal_latch_reason": terminal_latch_reason,
            "followup_child_count_after_containment_latch": 0,
            "live_call_counts": {name: 0 for name in publisher.REQUIRED_ZERO_LIVE_CALLS},
            "live_call_observation_scope": VALIDATION_OBSERVATION_SCOPE,
            "completion_marker_created": False,
            "success_marker_created": False,
            "r8_authorized": False,
        }
        raw_summary = canonical_json_bytes(summary)
        summary_path = output / "code-validation-summary.json"
        failure_stage = "summary_publication"
        summary_publication = _publication_receipt(
            output_writer.publish(summary_path.name, raw_summary)
        )
        failure_stage = "publication_index_serialization"
        publication_index = {
            "schema": PUBLICATION_INDEX_SCHEMA,
            "status": summary["status"],
            "summary": {
                "path": str(summary_path),
                "bytes": len(raw_summary),
                "sha256": sha256_bytes(raw_summary),
                "publication": summary_publication,
            },
            "environment_commitment": child_environment.commitment,
            "output_parent_commitment": parent_commitment,
            "metadata_child_call_count": _metadata_child_call_count(metadata_evidence),
            "command_publication_receipts_bound_through_summary": True,
            "completion_marker_created": False,
            "success_marker_created": False,
            "self_publication_receipt_embedded": False,
            "self_publication_receipt_scope": (
                "outer_result_only_non_self_referential_by_construction"
            ),
        }
        raw_publication_index = canonical_json_bytes(publication_index)
        publication_index_path = output / "code-validation-publication-index.json"
        failure_stage = "publication_index_publication"
        publication_index_receipt = _publication_receipt(
            output_writer.publish(publication_index_path.name, raw_publication_index)
        )
        result_record = {
            "schema": SCHEMA,
            "status": summary["status"],
            "output_directory": str(output),
            "summary_path": str(summary_path),
            "summary_bytes": len(raw_summary),
            "summary_sha256": sha256_bytes(raw_summary),
            "summary_publication": summary_publication,
            "publication_index_path": str(publication_index_path),
            "publication_index_bytes": len(raw_publication_index),
            "publication_index_sha256": sha256_bytes(raw_publication_index),
            "publication_index_publication": publication_index_receipt,
            "environment_commitment": child_environment.commitment,
            "output_parent_commitment": parent_commitment,
            "metadata_child_call_count": _metadata_child_call_count(metadata_evidence),
            "automatic_retry_count": 0,
            "orchestrator_prohibited_live_command_calls": 0,
            "live_call_observation_scope": VALIDATION_OBSERVATION_SCOPE,
            "completion_marker_created": False,
        }
        return result_record
    except BaseException as exc:
        if terminal_seal_attempted:
            return _publish_terminal_emergency_seal(
                output_writer=output_writer,
                output=output,
                stage=failure_stage,
                original_error=exc,
                terminal_seal_error=exc,
                specs=specs,
                validation_child_call_count=validation_child_call_count,
                command_refs=command_refs,
                metadata_evidence=metadata_evidence,
                environment_commitment=child_environment.commitment,
                output_parent=parent_commitment,
            )
        try:
            return terminal_once(
                output_writer=output_writer,
                output=output,
                stage=failure_stage,
                reason="unexpected_validation_or_publication_exception",
                specs=specs,
                validation_child_call_count=validation_child_call_count,
                command_refs=command_refs,
                metadata_evidence=metadata_evidence,
                environment_commitment=child_environment.commitment,
                output_parent=parent_commitment,
                failure_evidence={
                    "exception_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "exception_message_disclosed": False,
                },
            )
        except BaseException as terminal_error:
            return _publish_terminal_emergency_seal(
                output_writer=output_writer,
                output=output,
                stage=failure_stage,
                original_error=exc,
                terminal_seal_error=terminal_error,
                specs=specs,
                validation_child_call_count=validation_child_call_count,
                command_refs=command_refs,
                metadata_evidence=metadata_evidence,
                environment_commitment=child_environment.commitment,
                output_parent=parent_commitment,
            )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run append-only offline r7s5 code validation")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    parser.add_argument("--expected-output-parent", type=Path, required=True)
    parser.add_argument("--expected-output-parent-sha256", required=True)
    parser.add_argument("--output-leaf", required=True)
    parser.add_argument("--python-general", type=Path, required=True)
    parser.add_argument("--python-general-sha256", required=True)
    parser.add_argument("--python-host", type=Path, required=True)
    parser.add_argument("--python-host-sha256", required=True)
    parser.add_argument("--python-ruff", type=Path, required=True)
    parser.add_argument("--python-ruff-sha256", required=True)
    parser.add_argument("--git", type=Path, required=True)
    parser.add_argument("--git-sha256", required=True)
    parser.add_argument("--powershell", type=Path, required=True)
    parser.add_argument("--powershell-sha256", required=True)
    parser.add_argument("--expected-untracked-count", type=int, required=True)
    parser.add_argument("--expected-untracked-path-list-sha256", required=True)
    parser.add_argument("--expected-untracked-content-inventory-sha256", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-tree", required=True)
    return parser.parse_args(argv)


def _require_isolated_no_bytecode_startup() -> None:
    if sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1:
        raise ValidationRunnerError("validation_runner_requires_python_I_B_startup")


def _best_effort_emit_result(result: Mapping[str, Any]) -> None:
    """Keep console availability outside the evidence/publication transaction."""

    try:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except BaseException:
        pass


def validation_failure_seal_contract() -> dict[str, Any]:
    return {
        "scope": "after_output_parent_admission",
        "parse_path_pin_spec_and_untrusted_parent_admission_failures_durably_sealed": False,
        "output_writer_initialization_failure_sibling_seal_attempted": True,
        "post_writer_setup_or_outer_terminal_escape_sibling_seal_attempted": True,
        "post_writer_base_exception_terminal_seal_attempted": True,
        "failure_seal_failure_emergency_sibling_attempted": True,
        "automatic_retry_count": 0,
        "durable_record_after_independent_emergency_writer_failure_guaranteed": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    _require_isolated_no_bytecode_startup()
    result = run_validation(parse_args(argv))
    _best_effort_emit_result(result)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

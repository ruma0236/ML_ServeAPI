from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for search_path in (ROOT, SRC):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from scripts.dev.run_x1_phase_b2_r4 import (  # noqa: E402
    REQUIRED_INVARIANTS as R4_REQUIRED_INVARIANTS,
    RestoreOnlyProbeSet,
)
from evm.scale_validation.phase_b2_r5 import (  # noqa: E402
    EvidenceWriter,
    LifecycleTimeoutContract,
    ReconcileRestoreHarness,
    RESTORE_LIFECYCLE_COUNTS,
    RestoreCheckpoint,
    RestoreDeadline,
    RestoreReport,
    TimeoutContract,
    decode_launcher_evidence,
    r5_restore_report,
    read_checkpoint_pair,
    sha256_file,
    validate_r5_manifest,
)
from evm.scale_validation.phase_b2_r5_fresh import (  # noqa: E402
    REQUIRED_RUNTIME_INVARIANTS,
    FreshContext,
    SampleRequest,
    StepResult,
    run_fresh,
    write_fresh_evidence,
)
from evm.scale_validation.phase_b2_r5_process import (  # noqa: E402
    TimeoutContract as ProcessTimeoutContract,
    WindowsJobProcessRunner,
    WslResidualProtocol,
)


R5_RESTORE_INVARIANTS = (
    *R4_REQUIRED_INVARIANTS,
    "compose_13_of_13",
    "kubernetes_livez",
)
OUTER_LEAF = "invoke-verified-x1-phase-b2-r5.ps1"
BRIDGE_LEAF = "invoke-x1-phase-b2-r5-bridge.ps1"
WSL_DISTRIBUTION = "Ubuntu"


class R5RunnerError(RuntimeError):
    """Fail-closed r5 integration error."""


class AttemptBudget:
    """One monotonic deadline shared by every fresh callback and child."""

    def __init__(
        self,
        total_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if total_seconds <= 0:
            raise ValueError("attempt_budget_must_be_positive")
        self.total_seconds = float(total_seconds)
        self.clock = clock
        self.started_monotonic = float(clock())
        self.deadline_monotonic = self.started_monotonic + self.total_seconds
        self.checks: list[dict[str, Any]] = []

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - float(self.clock()))

    def assert_can_start(self, name: str, required_seconds: float) -> dict[str, Any]:
        if required_seconds <= 0:
            raise ValueError("attempt_child_budget_must_be_positive")
        remaining = self.remaining_seconds
        allowed = remaining >= float(required_seconds)
        evidence = {
            "name": name,
            "checked_monotonic": float(self.clock()),
            "deadline_monotonic": self.deadline_monotonic,
            "remaining_seconds": remaining,
            "required_seconds": float(required_seconds),
            "allowed": allowed,
        }
        self.checks.append(evidence)
        if not allowed:
            raise R5RunnerError(
                f"fresh_attempt_budget_prevents_child:{name}:"
                f"remaining={remaining:.6f}:required={float(required_seconds):.6f}"
            )
        return evidence


@dataclass(frozen=True)
class PreparedExecution:
    args: argparse.Namespace
    manifest: Mapping[str, Any]
    launcher_evidence: Mapping[str, Any]
    checkpoint_payload: Mapping[str, Any]
    checkpoint_index: Mapping[str, Any]
    restore_checkpoint: RestoreCheckpoint | None
    timeout_contract: TimeoutContract
    lifecycle_timeout_contract: LifecycleTimeoutContract
    output_directory: Path
    run_id: str


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise R5RunnerError(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise R5RunnerError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise R5RunnerError(f"{label}_object_required:{path}")
    return value


def _resolved_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R5RunnerError(f"{label}_mapping_required")
    return value


def _verify_launcher_files(
    manifest_path: Path,
    launcher_evidence: Mapping[str, Any],
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
            raise R5RunnerError(f"launcher_file_missing:{name}:{path}")
        actual = sha256_file(path)
        if actual != str(chain.get(name, "")).lower():
            raise R5RunnerError(f"launcher_file_sha256_mismatch:{name}")
        measured[name] = actual
    return measured


def _verify_launcher_git(manifest: Mapping[str, Any], launcher_evidence: Mapping[str, Any]) -> None:
    git = _mapping(launcher_evidence.get("git"), "launcher_git")
    repository = _mapping(manifest.get("repository"), "manifest_repository")
    revision = str(manifest.get("canonical_revision", "")).lower()
    tree = str(manifest.get("canonical_tree", "")).lower()
    if launcher_evidence.get("mode") != manifest.get("execution_mode"):
        raise R5RunnerError("launcher_mode_mismatch")
    if any(
        str(git.get(name, "")).lower() != revision
        for name in ("revision", "origin_revision", "remote_revision")
    ):
        raise R5RunnerError("launcher_local_origin_remote_mismatch")
    if str(git.get("tree", "")).lower() != tree:
        raise R5RunnerError("launcher_tree_mismatch")
    if str(git.get("branch", "")) != str(repository.get("branch", "")):
        raise R5RunnerError("launcher_branch_mismatch")
    if int(git.get("tracked", -1)) != 0:
        raise R5RunnerError("launcher_tracked_changes_present")
    if int(git.get("untracked", -1)) != int(repository.get("preserved_untracked_count", -2)):
        raise R5RunnerError("launcher_untracked_count_mismatch")


def _verify_checkpoint_index_reference(
    primary_path: Path,
    primary_sha256: str,
    companion: Mapping[str, Any],
) -> None:
    files = companion.get("files")
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
        raise R5RunnerError("checkpoint_index_files_sequence_required")
    primary_leaf = primary_path.name
    references = [
        item
        for item in files
        if isinstance(item, Mapping)
        and Path(str(item.get("path", ""))).name == primary_leaf
        and str(item.get("sha256", "")).lower() == primary_sha256
    ]
    if len(references) != 1:
        raise R5RunnerError("checkpoint_index_primary_reference_exact_once_required")


def prepare_execution(args: argparse.Namespace) -> PreparedExecution:
    manifest_path = args.manifest.resolve()
    repository_root = args.repository_root.resolve()
    manifest = _read_json_object(manifest_path, "manifest")
    timeout_contract = TimeoutContract().validate()
    lifecycle_contract = LifecycleTimeoutContract().validate()
    validate_r5_manifest(
        manifest,
        expected_revision=args.expected_revision,
        mode=args.mode,
        repository_root=repository_root,
        runtime_timeout=timeout_contract,
        lifecycle_timeout=lifecycle_contract,
    )
    launcher = decode_launcher_evidence(args.launcher_evidence_base64, manifest)
    _verify_launcher_files(manifest_path, launcher)
    _verify_launcher_git(manifest, launcher)

    checkpoint = _mapping(manifest.get("checkpoint"), "manifest_checkpoint")
    companion = _mapping(checkpoint.get("companion_index"), "manifest_checkpoint_index")
    checkpoint_path = Path(str(checkpoint.get("path", ""))).resolve()
    checkpoint_index_path = Path(str(companion.get("path", ""))).resolve()
    if not _resolved_equal(args.checkpoint, checkpoint_path):
        raise R5RunnerError("checkpoint_argument_manifest_path_mismatch")
    checkpoint_payload, checkpoint_index, restore_checkpoint = read_checkpoint_pair(
        checkpoint_path,
        str(checkpoint.get("sha256", "")),
        checkpoint_index_path,
        str(companion.get("sha256", "")),
        mode=args.mode,
    )
    _verify_checkpoint_index_reference(
        checkpoint_path,
        str(checkpoint.get("sha256", "")).lower(),
        checkpoint_index,
    )

    output = _mapping(manifest.get("output"), "manifest_output")
    output_directory = args.output_directory.resolve()
    if not _resolved_equal(output_directory, Path(str(output.get("path", "")))):
        raise R5RunnerError("output_argument_manifest_path_mismatch")
    if output_directory.exists():
        raise R5RunnerError(f"output_directory_exists:{output_directory}")
    if str(manifest.get("canonical_revision", "")).lower() != args.expected_revision.lower():
        raise R5RunnerError("expected_revision_mismatch")
    run_id = str(manifest.get("bundle_id", ""))
    if not run_id:
        raise R5RunnerError("bundle_id_required")
    return PreparedExecution(
        args=args,
        manifest=manifest,
        launcher_evidence=launcher,
        checkpoint_payload=checkpoint_payload,
        checkpoint_index=checkpoint_index,
        restore_checkpoint=restore_checkpoint,
        timeout_contract=timeout_contract,
        lifecycle_timeout_contract=lifecycle_contract,
        output_directory=output_directory,
        run_id=run_id,
    )


def _process_timeout(contract: TimeoutContract) -> ProcessTimeoutContract:
    return ProcessTimeoutContract(**contract.to_dict())


class R5ProbeSet(RestoreOnlyProbeSet):
    """R4 read-only probes with Job containment and explicit r5 aliases."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        contract: TimeoutContract,
        expected_revision: str,
        repository_root: Path,
        process_runner: Any | None = None,
    ) -> None:
        super().__init__(
            manifest=manifest,
            contract=contract,
            expected_revision=expected_revision,
            repository_root=repository_root,
        )
        self.runner = process_runner or WindowsJobProcessRunner(_process_timeout(contract))

    def _run(
        self,
        deadline: RestoreDeadline,
        command: Sequence[str],
        *,
        name: str,
    ) -> dict[str, Any]:
        """Treat unknown Job state as a manual latch, never a retryable probe miss."""

        try:
            deadline.assert_can_launch(self.launch_budget_seconds)
            outcome = self.runner.run(command, name=name, cwd=self.repository_root)
        except Exception as exc:
            error = f"{name}:process_containment_exception:{type(exc).__name__}:{exc}"
            failure_to_dict = getattr(exc, "to_dict", None)
            typed_evidence = failure_to_dict() if callable(failure_to_dict) else None
            if not isinstance(typed_evidence, Mapping):
                typed_evidence = None
            child_created = (
                bool(
                    typed_evidence.get(
                        "child_created",
                        getattr(exc, "child_created", False),
                    )
                )
                if typed_evidence is not None
                else None
            )
            raw_residual = (
                typed_evidence.get(
                    "residual_pids",
                    getattr(exc, "residual_pids", ()),
                )
                if typed_evidence is not None
                else ()
            )
            residual = tuple(sorted({int(pid) for pid in raw_residual or ()}))
            if residual:
                residual_status = "present"
            elif child_created is False:
                residual_status = "not_created"
            else:
                residual_status = "unknown"
            process_evidence = (
                dict(typed_evidence)
                if typed_evidence is not None
                else {
                    "name": name,
                    "command": list(command),
                    "runner_exception": f"{type(exc).__name__}:{exc}",
                    "child_created": None,
                    "forced_termination_attempts": 0,
                }
            )
            nested_process = process_evidence.get("process_evidence")
            if isinstance(nested_process, Mapping):
                for field_name in (
                    "child_created",
                    "no_child_created",
                    "root_pid",
                    "job_membership_verified",
                    "root_resumed",
                ):
                    if field_name in nested_process:
                        process_evidence.setdefault(field_name, nested_process[field_name])
            process_evidence.update(
                {
                    "safe_for_followup": False,
                    "residual_status": residual_status,
                }
            )
            exception_timed_out = bool(
                (
                    typed_evidence.get("timed_out", getattr(exc, "timed_out", False))
                    if typed_evidence is not None
                    else getattr(exc, "timed_out", False)
                )
            )
            return {
                "passed": False,
                "last_error": error,
                "residual_pids": list(residual),
                "residual_status": residual_status,
                "residual_process_zero": residual_status == "not_created",
                "manual_intervention_required": True,
                "timeout_manual_latch": exception_timed_out,
                "containment_manual_latch": True,
                "process_evidence": process_evidence,
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
        error = None
        if not passed:
            error = (
                f"{name}:return_code={return_code}:timed_out={timed_out}:"
                f"cancelled={cancelled}:residual={list(residual)}:"
                f"{str(getattr(outcome, 'stderr', ''))[-1000:]}"
            )
        return {
            "passed": passed,
            "last_error": error,
            "residual_pids": list(residual),
            "residual_status": "present" if residual else "zero",
            "residual_process_zero": not uncertain and not residual,
            "manual_intervention_required": uncertain,
            "timeout_manual_latch": timed_out or cancelled,
            "process_evidence": outcome.to_dict(),
            "stdout": str(getattr(outcome, "stdout", "")),
            "stderr": str(getattr(outcome, "stderr", "")),
        }

    def compose(self, deadline: RestoreDeadline) -> dict[str, Any]:
        result = super().compose(deadline)
        invariants = dict(result.get("invariants", {}))
        invariants["compose_13_of_13"] = invariants.get("compose_healthy") is True
        result["invariants"] = invariants
        result["passed"] = bool(result.get("passed") and invariants["compose_13_of_13"])
        return result

    def kubernetes_api(self, deadline: RestoreDeadline) -> dict[str, Any]:
        live_result = self._run(
            deadline,
            self._kubectl_command("get", "--raw=/livez"),
            name="kubernetes-livez",
        )
        live = bool(
            live_result.get("passed") and str(live_result.get("stdout", "")).strip().lower() == "ok"
        )
        if not live:
            failure = self._failed_process_chain(
                [live_result],
                last_error=live_result.get("last_error") or "livez_not_ok",
                invariant_names=("kubernetes_livez", "kubernetes_readyz"),
            )
            failure["retryable"] = not failure["manual_intervention_required"] and any(
                marker in str(failure["last_error"]).lower()
                for marker in ("eof", "connection refused", "i/o timeout", "tls handshake")
            )
            return failure
        ready_result = self._run(
            deadline,
            self._kubectl_command("get", "--raw=/readyz"),
            name="kubernetes-readyz",
        )
        ready = bool(
            ready_result.get("passed")
            and str(ready_result.get("stdout", "")).strip().lower() == "ok"
        )
        if not ready:
            failure = self._failed_process_chain(
                [live_result, ready_result],
                last_error=ready_result.get("last_error") or "readyz_not_ok",
                invariant_names=("kubernetes_livez", "kubernetes_readyz"),
            )
            failure["invariants"] = {
                "kubernetes_livez": True,
                "kubernetes_readyz": False,
            }
            failure["retryable"] = not failure["manual_intervention_required"] and any(
                marker in str(failure["last_error"]).lower()
                for marker in ("eof", "connection refused", "i/o timeout", "tls handshake")
            )
            return failure
        return {
            "passed": True,
            "last_error": None,
            "invariants": {"kubernetes_livez": True, "kubernetes_readyz": True},
            "process_evidence": [
                live_result["process_evidence"],
                ready_result["process_evidence"],
            ],
            "residual_pids": [],
        }


def _new_probe_set(prepared: PreparedExecution, process_runner: Any | None = None) -> R5ProbeSet:
    return R5ProbeSet(
        manifest=prepared.manifest,
        contract=prepared.timeout_contract,
        expected_revision=prepared.args.expected_revision.lower(),
        repository_root=prepared.args.repository_root.resolve(),
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
        required_invariants=R5_RESTORE_INVARIANTS,
        max_probe_attempts=3,
    )
    return harness.run_restore_only(checkpoint)


def _metadata(prepared: PreparedExecution) -> dict[str, Any]:
    checkpoint = _mapping(prepared.manifest.get("checkpoint"), "manifest_checkpoint")
    companion = _mapping(checkpoint.get("companion_index"), "manifest_checkpoint_index")
    return {
        "run_id": prepared.run_id,
        "mode": prepared.args.mode,
        "manifest": str(prepared.args.manifest.resolve()),
        "manifest_sha256": sha256_file(prepared.args.manifest.resolve()),
        "checkpoint": str(Path(str(checkpoint["path"])).resolve()),
        "checkpoint_sha256": str(checkpoint["sha256"]),
        "checkpoint_index": str(Path(str(companion["path"])).resolve()),
        "checkpoint_index_sha256": str(companion["sha256"]),
        "canonical_revision": prepared.args.expected_revision.lower(),
        "launcher_evidence": dict(prepared.launcher_evidence),
        "actual_launcher_invocations": {
            "outer": 1,
            "bridge": 1,
            "runner": 1,
            "automatic_retry": 0,
        },
        "downstream_invocations": {
            "full_stack_3180": 0,
            "q0": 0,
            "calibration_54": 0,
            "matrix_78": 0,
            "integrated_v4": 0,
            "etw": 0,
        },
    }


def execute_restore_only(
    prepared: PreparedExecution,
    *,
    restore_executor: Callable[[PreparedExecution, RestoreCheckpoint], RestoreReport] | None = None,
) -> tuple[int, dict[str, Any]]:
    if prepared.output_directory.exists():
        raise R5RunnerError(f"output_directory_exists:{prepared.output_directory}")
    if prepared.restore_checkpoint is None:
        raise R5RunnerError("restore_checkpoint_required")
    executor = restore_executor or _run_restore_harness
    report = executor(prepared, prepared.restore_checkpoint)
    converted = r5_restore_report(report, prepared.run_id)
    if dict(converted.get("call_counts", {})) != RESTORE_LIFECYCLE_COUNTS:
        raise R5RunnerError("restore_only_lifecycle_calls_not_zero")
    writer = EvidenceWriter(prepared.output_directory)
    metadata = _metadata(prepared)
    if report.passed:
        evidence = writer.seal_restore_only(converted, metadata=metadata)
        return 0, {"decision": "restore_only_pass", "report": converted, **evidence}
    evidence = writer.seal_failure(converted, metadata=metadata)
    return 2, {
        "decision": "manual_intervention_required",
        "report": converted,
        **evidence,
    }


def _outcome_step(name: str, outcome: Any) -> StepResult:
    passed = bool(getattr(outcome, "safe_for_followup", False))
    residual = tuple(int(pid) for pid in getattr(outcome, "residual_pids", ()))
    timed_out = bool(getattr(outcome, "timed_out", False))
    manual = bool(getattr(outcome, "manual_intervention_required", False))
    return_code = getattr(outcome, "return_code", None)
    error = None
    if not passed:
        error = f"{name}:return_code={return_code}:timed_out={timed_out}:residual={list(residual)}"
    return StepResult(
        name=name,
        passed=passed,
        timed_out=timed_out,
        manual_intervention_required=manual or timed_out or bool(residual),
        residual_pids=residual,
        error=error,
        details={"process_evidence": outcome.to_dict()},
    )


def _windows_sample(request: SampleRequest) -> Mapping[str, Any]:
    raw_before = time.perf_counter_ns()
    realtime = time.time_ns()
    raw_after = time.perf_counter_ns()
    monotonic = time.monotonic_ns()
    if sys.platform == "win32":
        auxiliary = int(ctypes.windll.kernel32.GetTickCount64()) * 1_000_000  # type: ignore[attr-defined]
    else:
        auxiliary = monotonic
    return {
        "domain": request.domain,
        "sequence": request.sequence,
        "raw_before_ns": raw_before,
        "realtime_unix_ns": realtime,
        "raw_after_ns": raw_after,
        "monotonic_ns": monotonic,
        "auxiliary_monotonic_ns": auxiliary,
    }


def _windows_to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1 or not drive.isalpha():
        raise R5RunnerError(f"wsl_spool_drive_path_required:{resolved}")
    tail = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive}/{tail}"


WSL_SAMPLER_SOURCE = r"""import json
import os
import pathlib
import sys
import time

spool, run_uuid, count_text, cadence_text = sys.argv[1:]
count = int(count_text)
cadence = int(cadence_text)
stat = pathlib.Path('/proc/self/stat').read_text()
right = stat.rfind(')')
fields = stat[right + 1:].strip().split()
metadata = {
    'kind': 'metadata',
    'run_uuid': run_uuid,
    'pid': os.getpid(),
    'ppid': os.getppid(),
    'pgrp': os.getpgrp(),
    'session': os.getsid(0),
    'start_time_ticks': int(fields[19]),
    'boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
}
raw_clock = getattr(time, 'CLOCK_MONOTONIC_RAW', time.CLOCK_MONOTONIC)
aux_clock = getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)
origin = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
with open(spool, 'x', encoding='utf-8', buffering=1) as stream:
    stream.write(json.dumps(metadata, sort_keys=True, separators=(',', ':')) + '\n')
    for sequence in range(count):
        target = origin + sequence * cadence
        while True:
            now = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            if now >= target:
                break
            time.sleep((target - now) / 1_000_000_000)
        before = time.clock_gettime_ns(raw_clock)
        realtime = time.time_ns()
        after = time.clock_gettime_ns(raw_clock)
        sample = {
            'kind': 'sample',
            'sequence': sequence,
            'raw_before_ns': before,
            'realtime_unix_ns': realtime,
            'raw_after_ns': after,
            'monotonic_ns': time.clock_gettime_ns(time.CLOCK_MONOTONIC),
            'auxiliary_monotonic_ns': time.clock_gettime_ns(aux_clock),
        }
        stream.write(json.dumps(sample, sort_keys=True, separators=(',', ':')) + '\n')
"""


class WslClockStream:
    """Live WSL raw sampler with a preserved append-only spool and /proc gate."""

    def __init__(
        self,
        *,
        output_directory: Path,
        process_runner: Any,
        scan_runner: Any,
        distribution: str = WSL_DISTRIBUTION,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        before_child: Callable[[str, float], Mapping[str, Any]] | None = None,
        scan_budget_seconds: float = 140.0,
    ) -> None:
        self.output_directory = output_directory
        self.process_runner = process_runner
        self.scan_runner = scan_runner
        self.distribution = distribution
        self.sleep = sleep
        self.clock = clock
        self.before_child = before_child
        self.scan_budget_seconds = float(scan_budget_seconds)
        self.run_uuid: str | None = None
        self.protocol: WslResidualProtocol | None = None
        self.spool_directory: Path | None = None
        self.spool_path: Path | None = None
        self.metadata: dict[str, Any] | None = None
        self.details: dict[str, Any] = {}
        self._offset = 0
        self._thread: threading.Thread | None = None
        self._outcome: Any | None = None
        self._error: BaseException | None = None
        self._finished = False

    @property
    def started(self) -> bool:
        return self._thread is not None

    @property
    def finished(self) -> bool:
        return self._finished

    def _worker(self, command: Sequence[str]) -> None:
        try:
            self._outcome = self.process_runner.run(
                command,
                name="wsl-live-clock-sampler",
                cwd=ROOT,
                run_uuid=self.run_uuid,
            )
        except BaseException as exc:  # retained for the orchestrating thread
            self._error = exc

    def _next_line(self, timeout_seconds: float) -> Mapping[str, Any]:
        if self.spool_path is None:
            raise R5RunnerError("wsl_spool_not_initialized")
        deadline = self.clock() + timeout_seconds
        while self.clock() < deadline:
            if self.spool_path.is_file():
                with self.spool_path.open("rb") as stream:
                    stream.seek(self._offset)
                    line = stream.readline()
                    if line.endswith(b"\n"):
                        self._offset = stream.tell()
                        try:
                            value = json.loads(line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            raise R5RunnerError("wsl_spool_json_invalid") from exc
                        if not isinstance(value, Mapping):
                            raise R5RunnerError("wsl_spool_object_required")
                        return value
            if self._error is not None:
                raise R5RunnerError(f"wsl_sampler_worker_failed:{self._error}")
            if (
                self._thread is not None
                and not self._thread.is_alive()
                and self._outcome is not None
            ):
                raise R5RunnerError("wsl_sampler_ended_before_next_record")
            self.sleep(0.01)
        raise R5RunnerError("wsl_sampler_record_timeout")

    def start(self, context: FreshContext) -> None:
        if self._thread is not None:
            raise R5RunnerError("wsl_sampler_duplicate_start")
        self.run_uuid = context.run_uuid
        self.protocol = WslResidualProtocol(context.run_uuid)
        self.spool_directory = self.output_directory.parent / (
            f"{self.output_directory.name}-wsl-spool-{context.run_uuid}"
        )
        self.spool_directory.mkdir(parents=True, exist_ok=False)
        self.spool_path = self.spool_directory / "wsl-live-raw.jsonl"
        command = self.protocol.launch_command(
            self.distribution,
            (
                "python3",
                "-c",
                WSL_SAMPLER_SOURCE,
                _windows_to_wsl_path(self.spool_path),
                context.run_uuid,
                str(context.contract.sample_count),
                str(context.contract.cadence_ns),
            ),
        )
        self._thread = threading.Thread(
            target=self._worker,
            args=(command,),
            name="r5-wsl-live-clock-job",
            daemon=True,
        )
        self._thread.start()
        metadata = dict(self._next_line(15.0))
        required = {
            "kind",
            "run_uuid",
            "pid",
            "ppid",
            "pgrp",
            "session",
            "start_time_ticks",
            "boot_id",
        }
        if set(metadata) != required or metadata["kind"] != "metadata":
            raise R5RunnerError("wsl_sampler_metadata_schema_mismatch")
        if metadata["run_uuid"] != context.run_uuid:
            raise R5RunnerError("wsl_sampler_run_uuid_mismatch")
        if any(int(metadata[name]) <= 0 for name in ("pid", "pgrp", "session", "start_time_ticks")):
            raise R5RunnerError("wsl_sampler_process_identity_invalid")
        self.metadata = metadata
        self.details.update(
            {
                "wsl_run_uuid": context.run_uuid,
                "wsl_process_group": int(metadata["pgrp"]),
                "wsl_session": int(metadata["session"]),
                "wsl_start_time_ticks": int(metadata["start_time_ticks"]),
                "wsl_boot_id": str(metadata["boot_id"]),
                "wsl_spool": str(self.spool_path),
            }
        )

    def _finish(self, *, reason: str) -> None:
        if self._finished:
            return
        if self._thread is None or self.protocol is None or self.run_uuid is None:
            raise R5RunnerError("wsl_sampler_finish_before_start")
        self._thread.join(timeout=340.0)
        if self._thread.is_alive():
            raise R5RunnerError("wsl_sampler_job_thread_not_bounded")
        if self._error is not None:
            raise R5RunnerError(f"wsl_sampler_worker_failed:{self._error}")
        outcome = self._outcome
        if outcome is None:
            raise R5RunnerError("wsl_sampler_outcome_missing")
        self.details["wsl_job_process_evidence"] = outcome.to_dict()
        self.details["wsl_finalize_reason"] = reason
        if not bool(getattr(outcome, "safe_for_followup", False)):
            self.details["wsl_proc_scan_skipped"] = "windows_job_not_safe_for_followup"
            raise R5RunnerError("wsl_sampler_job_not_safe_for_followup")

        self.details["wsl_metadata_available"] = self.metadata is not None
        protocol = (
            WslResidualProtocol(
                str(self.run_uuid),
                root_process_group=int(self.metadata["pgrp"]),
                root_start_time_ticks=int(self.metadata["start_time_ticks"]),
                boot_id=str(self.metadata["boot_id"]),
            )
            if self.metadata is not None
            else WslResidualProtocol(str(self.run_uuid))
        )
        if self.before_child is not None:
            self.details["wsl_proc_scan_budget"] = dict(
                self.before_child("wsl_proc_residual_readback", self.scan_budget_seconds)
            )
        scanner_uuid = str(uuid.uuid4())
        scan = self.scan_runner.run(
            protocol.scan_command(self.distribution),
            name="wsl-proc-residual-readback",
            cwd=ROOT,
            run_uuid=scanner_uuid,
        )
        self.details["wsl_proc_scan_process_evidence"] = scan.to_dict()
        if not bool(getattr(scan, "safe_for_followup", False)):
            raise R5RunnerError("wsl_proc_scan_not_safe_for_followup")
        records = protocol.parse_scan_json(str(getattr(scan, "stdout", "")))
        residual = [record for record in records if protocol.is_residual(record)]
        self.details["wsl_proc_residuals"] = [record.__dict__ for record in residual]
        if residual:
            raise R5RunnerError("wsl_proc_residual_process_present")
        if self.spool_path is None:
            raise R5RunnerError("wsl_spool_missing_at_finish")
        self.details["wsl_spool_sha256"] = sha256_file(self.spool_path)
        self.details["wsl_spool_bytes"] = self.spool_path.stat().st_size
        self._finished = True

    def finalize_after_collection_failure(self) -> None:
        """Boundedly collect Job and /proc evidence without restoring services."""

        if not self.started or self.finished:
            return
        self._finish(reason="collection_failure")

    def sample(self, request: SampleRequest) -> Mapping[str, Any]:
        value = dict(self._next_line(8.0))
        if value.pop("kind", None) != "sample":
            raise R5RunnerError("wsl_sample_kind_mismatch")
        if int(value.get("sequence", -1)) != request.sequence:
            raise R5RunnerError("wsl_sample_sequence_mismatch")
        value["domain"] = request.domain
        if request.sequence == 1_799:
            self._finish(reason="collection_complete")
        return value


def _fresh_invariants(report: RestoreReport) -> dict[str, bool]:
    source = dict(report.success_invariants)
    mapped = {name: bool(source.get(name, False)) for name in REQUIRED_RUNTIME_INVARIANTS}
    mapped["compose_13_of_13"] = bool(
        source.get("compose_13_of_13", source.get("compose_healthy", False))
    )
    mapped["kubernetes_livez"] = bool(source.get("kubernetes_livez", False))
    mapped["residual_pid_zero"] = not report.residual_pids
    return mapped


def execute_fresh(
    prepared: PreparedExecution,
    *,
    fresh_executor: Callable[..., Any] = run_fresh,
    evidence_writer: Callable[..., Mapping[str, Any]] = write_fresh_evidence,
    process_runner_factory: Callable[[ProcessTimeoutContract], Any] = WindowsJobProcessRunner,
    wsl_stream_factory: Callable[..., Any] = WslClockStream,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, Any]]:
    if prepared.output_directory.exists():
        raise R5RunnerError(f"output_directory_exists:{prepared.output_directory}")
    if prepared.restore_checkpoint is not None:
        raise R5RunnerError("fresh_restore_checkpoint_must_be_none")
    lifecycle = prepared.lifecycle_timeout_contract
    attempt_budget = AttemptBudget(
        lifecycle.attempt_deadline_seconds,
        clock=monotonic_clock,
    )
    repository = prepared.args.repository_root.resolve()
    docker = R5ProbeSet._find_executable(
        "docker", Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
    )
    probe_runner = process_runner_factory(_process_timeout(prepared.timeout_contract))
    compose_runner = process_runner_factory(
        ProcessTimeoutContract(
            kubectl_timeout_seconds=8.0,
            wrapper_timeout_seconds=lifecycle.compose_wrapper_seconds,
            restore_deadline_seconds=lifecycle.attempt_deadline_seconds,
            residual_repoll_seconds=prepared.timeout_contract.residual_repoll_seconds,
            stream_drain_seconds=prepared.timeout_contract.stream_drain_seconds,
        )
    )
    desktop_runner = process_runner_factory(
        ProcessTimeoutContract(
            kubectl_timeout_seconds=8.0,
            wrapper_timeout_seconds=lifecycle.desktop_wrapper_seconds,
            restore_deadline_seconds=lifecycle.attempt_deadline_seconds,
            residual_repoll_seconds=prepared.timeout_contract.residual_repoll_seconds,
            stream_drain_seconds=prepared.timeout_contract.stream_drain_seconds,
        )
    )
    sampler_runner = process_runner_factory(
        ProcessTimeoutContract(
            kubectl_timeout_seconds=8.0,
            wrapper_timeout_seconds=lifecycle.sampler_wrapper_seconds,
            restore_deadline_seconds=lifecycle.attempt_deadline_seconds,
            residual_repoll_seconds=prepared.timeout_contract.residual_repoll_seconds,
            stream_drain_seconds=prepared.timeout_contract.stream_drain_seconds,
        )
    )
    stream = wsl_stream_factory(
        output_directory=prepared.output_directory,
        process_runner=sampler_runner,
        scan_runner=probe_runner,
        before_child=attempt_budget.assert_can_start,
        scan_budget_seconds=(
            prepared.timeout_contract.wrapper_timeout_seconds
            + prepared.timeout_contract.residual_repoll_seconds
            + prepared.timeout_contract.stream_drain_seconds
        ),
    )
    latest_restore: dict[str, RestoreReport] = {}

    def restore_probe(name: str, context: FreshContext) -> StepResult:
        budget = attempt_budget.assert_can_start(
            name,
            prepared.timeout_contract.restore_deadline_seconds,
        )
        checkpoint = RestoreCheckpoint(
            source=f"r5_fresh_{name}",
            historical_call_counts=RESTORE_LIFECYCLE_COUNTS,
            previous_attempt_failed=False,
        )
        report = _run_restore_harness(prepared, checkpoint, process_runner=probe_runner)
        latest_restore[name] = report
        return StepResult(
            name=name,
            passed=report.passed,
            manual_intervention_required=report.manual_intervention_required,
            residual_pids=report.residual_pids,
            error=None if report.passed else report.last_error,
            details={
                "restore_report": report.to_dict(),
                "run_uuid": context.run_uuid,
                "attempt_budget": budget,
            },
        )

    def lifecycle_step(
        name: str,
        runner: Any,
        command: Sequence[str],
        *,
        required_budget_seconds: float,
        start_wsl: bool = False,
    ) -> Callable[[FreshContext], StepResult]:
        def invoke(context: FreshContext) -> StepResult:
            budget = attempt_budget.assert_can_start(name, required_budget_seconds)
            outcome = runner.run(
                command,
                name=name,
                cwd=repository,
                run_uuid=context.run_uuid,
            )
            step = _outcome_step(name, outcome)
            step_details = {**dict(step.details), "attempt_budget": budget}
            if step.clean_pass and start_wsl:
                try:
                    sampler_budget = attempt_budget.assert_can_start(
                        "wsl_live_clock_sampler",
                        lifecycle.sampler_wrapper_seconds
                        + prepared.timeout_contract.residual_repoll_seconds
                        + prepared.timeout_contract.stream_drain_seconds,
                    )
                    stream.start(context)
                except Exception as exc:
                    return StepResult(
                        name=name,
                        passed=False,
                        manual_intervention_required=True,
                        error=f"wsl_sampler_start_failed:{type(exc).__name__}:{exc}",
                        details={
                            **step_details,
                            "wsl_sampler_budget": sampler_budget,
                            "wsl_sampler": dict(stream.details),
                        },
                    )
                return StepResult(
                    name=name,
                    passed=True,
                    details={
                        **step_details,
                        "wsl_sampler_budget": sampler_budget,
                        "wsl_sampler": stream.details,
                    },
                )
            return StepResult(
                name=step.name,
                passed=step.passed,
                timed_out=step.timed_out,
                manual_intervention_required=step.manual_intervention_required,
                residual_pids=step.residual_pids,
                error=step.error,
                details=step_details,
            )

        return invoke

    compose_file = str(repository / "docker-compose.yml")
    lifecycle_callbacks = {
        "compose_stop": lifecycle_step(
            "compose_stop",
            compose_runner,
            (docker, "compose", "-f", compose_file, "stop", "--timeout", "120"),
            required_budget_seconds=(
                lifecycle.compose_wrapper_seconds
                + prepared.timeout_contract.residual_repoll_seconds
                + prepared.timeout_contract.stream_drain_seconds
            ),
        ),
        "desktop_stop": lifecycle_step(
            "desktop_stop",
            desktop_runner,
            (docker, "desktop", "stop", "--timeout", "300"),
            required_budget_seconds=(
                lifecycle.desktop_wrapper_seconds
                + prepared.timeout_contract.residual_repoll_seconds
                + prepared.timeout_contract.stream_drain_seconds
            ),
            start_wsl=True,
        ),
        "desktop_start": lifecycle_step(
            "desktop_start",
            desktop_runner,
            (docker, "desktop", "start", "--timeout", "300"),
            required_budget_seconds=(
                lifecycle.desktop_wrapper_seconds
                + prepared.timeout_contract.residual_repoll_seconds
                + prepared.timeout_contract.stream_drain_seconds
            ),
        ),
        "compose_start": lifecycle_step(
            "compose_start",
            compose_runner,
            (
                docker,
                "compose",
                "-f",
                compose_file,
                "start",
                "--wait",
                "--wait-timeout",
                "120",
            ),
            required_budget_seconds=(
                lifecycle.compose_wrapper_seconds
                + prepared.timeout_contract.residual_repoll_seconds
                + prepared.timeout_contract.stream_drain_seconds
            ),
        ),
    }

    execution = fresh_executor(
        preflight=lambda context: restore_probe("preflight", context),
        lifecycle_callbacks=lifecycle_callbacks,
        windows_sampler=_windows_sample,
        wsl_sampler=stream.sample,
        recovery=lambda context: restore_probe("recovery", context),
        invariant_probe=lambda _context: _fresh_invariants(latest_restore["recovery"]),
    )
    if bool(getattr(stream, "started", False)) and not bool(getattr(stream, "finished", False)):
        try:
            stream.finalize_after_collection_failure()
        except Exception as exc:
            stream.details["wsl_finalize_error"] = f"{type(exc).__name__}:{exc}"
            if execution.success_eligible:
                raise R5RunnerError("fresh_success_forbidden_after_wsl_finalize_failure") from exc
    evidence = dict(
        evidence_writer(
            prepared.output_directory,
            execution,
            metadata={
                **_metadata(prepared),
                "restore_report_synthesized": False,
                "wsl_sampler": dict(stream.details),
                "attempt_budget": {
                    "total_seconds": attempt_budget.total_seconds,
                    "started_monotonic": attempt_budget.started_monotonic,
                    "deadline_monotonic": attempt_budget.deadline_monotonic,
                    "remaining_seconds": attempt_budget.remaining_seconds,
                    "checks": list(attempt_budget.checks),
                },
            },
        )
    )
    result = {
        "decision": execution.report.decision,
        "report": execution.report.to_dict(),
        **evidence,
    }
    return (0 if execution.success_eligible else 2), result


def _bootstrap_failure_report(args: argparse.Namespace, exc: BaseException) -> dict[str, Any]:
    return {
        "schema": "s8-v4-x1-phase-b2-r5-runner-bootstrap-failure/v1",
        "mode": args.mode,
        "passed": False,
        "manual_intervention_required": True,
        "decision": "manual_intervention_required",
        "error": f"{type(exc).__name__}:{exc}",
        "call_counts": dict(RESTORE_LIFECYCLE_COUNTS),
        "residual_pids": [],
        "completion_marker_created": False,
    }


def _seal_bootstrap_failure(args: argparse.Namespace, exc: BaseException) -> Mapping[str, Any]:
    output = args.output_directory.resolve()
    if output.exists():
        return {"failure_seal_error": f"output_directory_exists:{output}"}
    try:
        manifest = _read_json_object(args.manifest.resolve(), "manifest")
        manifest_output = Path(str(_mapping(manifest.get("output"), "manifest_output")["path"]))
        if not _resolved_equal(output, manifest_output):
            return {"failure_seal_error": "untrusted_output_argument_manifest_mismatch"}
        writer = EvidenceWriter(output)
        return writer.seal_failure(
            _bootstrap_failure_report(args, exc),
            metadata={
                "manifest": str(args.manifest.resolve()),
                "checkpoint": str(args.checkpoint.resolve()),
                "runner_invocations": 1,
                "automatic_retry": 0,
            },
        )
    except Exception as seal_exc:
        return {"failure_seal_error": f"{type(seal_exc).__name__}:{seal_exc}"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one append-only S8-V4/X1 Phase B2 r5 attempt."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--launcher-evidence-base64", required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("restore-only", "fresh"), required=True)
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    prepared = prepare_execution(args)
    if args.mode == "restore-only":
        return execute_restore_only(prepared)
    return execute_fresh(prepared)


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
                {
                    **_bootstrap_failure_report(args, exc),
                    **dict(evidence),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

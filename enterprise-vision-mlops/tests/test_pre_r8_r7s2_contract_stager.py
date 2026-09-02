from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only r7s2 contract staging tests", allow_module_level=True)

from scripts.dev import qualify_wsl_process_group_r7s2 as qualifier
from scripts.dev import stage_wsl_process_group_r7s2 as stager


PROJECT = Path(__file__).parents[1]
GIT_ROOT = PROJECT.parent
SOURCE_COMMIT, SOURCE_TREE = subprocess.check_output(
    [
        r"C:\Program Files\Git\mingw64\bin\git.exe",
        "-C",
        str(GIT_ROOT),
        "rev-parse",
        "HEAD",
        "HEAD^{tree}",
    ],
    text=True,
).splitlines()
RUN_ID = "pre-r8-r7s2-wsl-20260901T160000Z-cafebabe"
RUN_UUID = "11223344-5566-4788-899a-bbccddeeff00"
ATTEMPT_ID = "aabbccdd-eeff-4111-8222-334455667788"
BOOT_ID = "12345678-1234-4234-8234-123456789abc"
ADMIN_TOKEN = {
    "captured_at_utc": "2026-09-01T12:00:00+00:00",
    "pid": 100,
    "ppid": 50,
    "session_id": 1,
    "path": str(Path(sys.executable).resolve()),
    "path_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
    "administrator": True,
    "integrity": "High",
    "integrity_rid": 0x3000,
    "token_elevation_type": "Full",
    "token_elevation_type_value": 2,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parent_map(tmp_path: Path) -> tuple[Path, str]:
    parents = copy.deepcopy(stager.CANONICAL_PARENT_PINS)
    parent_map = tmp_path / "parent-map.json"
    parent_map.write_text(
        json.dumps(
            {"schema": stager.PARENT_MAP_SCHEMA, "parents": parents},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return parent_map, _sha(parent_map)


def _request(tmp_path: Path, **updates: Any) -> stager.StageRequest:
    parent_map, parent_sha = _parent_map(tmp_path)
    values: dict[str, Any] = {
        "qualification_id": RUN_ID,
        "run_uuid": RUN_UUID,
        "attempt_id": ATTEMPT_ID,
        "expected_source_commit": SOURCE_COMMIT,
        "expected_source_tree": SOURCE_TREE,
        "expected_qualification_sha256": _sha(
            PROJECT / "scripts" / "dev" / "qualify_wsl_process_group_r7s2.py"
        ),
        "expected_process_module_sha256": _sha(
            PROJECT / "src" / "evm" / "scale_validation" / "phase_b2_r7_process.py"
        ),
        "expected_r7s1_runner_sha256": _sha(
            PROJECT / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py"
        ),
        "expected_stager_sha256": _sha(
            PROJECT / "scripts" / "dev" / "stage_wsl_process_group_r7s2.py"
        ),
        "expected_outer_sha256": _sha(
            PROJECT / "scripts" / "dev" / "invoke_wsl_process_group_r7s2.py"
        ),
        "parent_map_path": parent_map,
        "expected_parent_map_sha256": parent_sha,
    }
    values.update(updates)
    return stager.StageRequest(**values)


def _linux_payload() -> dict[str, Any]:
    return {
        "schema": stager.LINUX_DISCOVERY_SCHEMA,
        "status": "observed",
        "distro": "Ubuntu",
        "kernel_release": "6.6.87.2-microsoft-standard-WSL2",
        "distro_version": "Ubuntu 24.04.3 LTS",
        "boot_id": BOOT_ID,
        "rootfs_identity": "1" * 64,
        "os_release_sha256": "2" * 64,
        "machine_id_sha256": "3" * 64,
        "binaries": {
            "python3": {
                "candidate_path": "/usr/bin/python3",
                "realpath": "/usr/bin/python3.12",
                "sha256": "4" * 64,
                "bytes": 100,
                "version": "3.12.3",
            },
            "env": {
                "candidate_path": "/usr/bin/env",
                "realpath": "/usr/bin/env",
                "sha256": "5" * 64,
                "bytes": 101,
                "version": "env (GNU coreutils) 9.4",
            },
            "setsid": {
                "candidate_path": "/usr/bin/setsid",
                "realpath": "/usr/bin/setsid",
                "sha256": "6" * 64,
                "bytes": 102,
                "version": "setsid from util-linux 2.39.3",
            },
        },
    }


class FakeOutcome:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.value)


def _success_job_evidence(command: Any) -> dict[str, Any]:
    event_names = [
        "job_created",
        "root_created_suspended",
        "job_membership_verified",
        "identity_observed",
        "root_resumed",
        "active_process_count_zero",
    ]
    events = []
    for sequence, event in enumerate(event_names, 1):
        details: dict[str, Any] = {}
        if event == "job_created":
            details = {"run_uuid": RUN_UUID}
        elif event == "job_membership_verified":
            details = {"active_processes": 1, "job_limit_flags": 0}
        events.append(
            {
                "sequence": sequence,
                "event": event,
                "monotonic_ns": sequence,
                "timestamp_utc": "2026-09-01T12:00:01+00:00",
                "pid": 1000 if event in event_names[1:5] else None,
                "details": details,
            }
        )
    accounting = [
        {
            "sequence": 7,
            "monotonic_ns": 7,
            "timestamp_utc": "2026-09-01T12:00:02+00:00",
            "total_processes": 1,
            "active_processes": 0,
            "total_terminated_processes": 0,
            "active_pids": [],
        },
        {
            "sequence": 8,
            "monotonic_ns": 8,
            "timestamp_utc": "2026-09-01T12:00:02+00:00",
            "total_processes": 1,
            "active_processes": 0,
            "total_terminated_processes": 0,
            "active_pids": [],
        },
    ]
    events.append(
        {
            "sequence": 9,
            "event": "streams_drained",
            "monotonic_ns": 9,
            "timestamp_utc": "2026-09-01T12:00:02+00:00",
            "pid": None,
            "details": {},
        }
    )
    return {
        "events": events,
        "accounting": accounting,
        "identities": [
            {
                "pid": 1000,
                "ppid": 999,
                "creation_time_ns": 1,
                "creation_time_utc": "2026-09-01T12:00:01+00:00",
                "image": str(command[0]),
                "run_uuid": RUN_UUID,
                "observed_sequence": 4,
            }
        ],
    }


def _safe(stdout: str, *, name: str, command: Any) -> dict[str, Any]:
    return {
        "name": name,
        "run_uuid": RUN_UUID,
        "command": list(command),
        "started_at_utc": "2026-09-01T12:00:01+00:00",
        "ended_at_utc": "2026-09-01T12:00:02+00:00",
        "duration_seconds": 1.0,
        "safe_for_followup": True,
        "forced_termination_attempts": 0,
        "active_process_zero": True,
        "final_active_process_count": 0,
        "job_limit_flags": 0,
        "cancelled": False,
        "manual_intervention_required": False,
        "streams_drained": True,
        "stdout_drained": True,
        "stderr_drained": True,
        "identity_coverage_complete": True,
        "timed_out": False,
        "return_code": 0,
        "residual_pids": [],
        "stdout": stdout,
        "stderr": "",
        **_success_job_evidence(command),
        "errors": [],
    }


class FakeRunner:
    def __init__(
        self, *, unsafe_linux: bool = False, git_identity: list[str] | None = None
    ) -> None:
        self.unsafe_linux = unsafe_linux
        self.git_identity = git_identity or [SOURCE_COMMIT, SOURCE_TREE]
        self.names: list[str] = []

    def run(self, _command: Any, *, name: str, **_kwargs: Any) -> FakeOutcome:
        self.names.append(name)

        def safe(stdout: str) -> dict[str, Any]:
            return _safe(stdout, name=name, command=_command)

        if name.endswith("git-identity"):
            return FakeOutcome(safe("\n".join(self.git_identity) + "\n"))
        if name.endswith("git-status"):
            return FakeOutcome(safe(""))
        if name.endswith("git-source-ls-tree"):
            records = []
            for relative in sorted(
                stager._source_relative_path(path)
                for path in (
                    stager.QUALIFICATION_SCRIPT,
                    stager.PROCESS_MODULE,
                    stager.R7S1_RUNNER,
                    stager.STAGER_SCRIPT,
                    stager.OUTER_LAUNCHER,
                )
            ):
                raw = (stager.GIT_ROOT / Path(relative)).read_bytes()
                normalized = stager._lf_normalized_source(raw)
                records.append(f"100644 blob {stager._git_blob_oid(normalized)}\t{relative}\0")
            return FakeOutcome(safe("".join(records)))
        if name.endswith("git-source-ls-files"):
            records = []
            for relative in sorted(
                stager._source_relative_path(path)
                for path in (
                    stager.QUALIFICATION_SCRIPT,
                    stager.PROCESS_MODULE,
                    stager.R7S1_RUNNER,
                    stager.STAGER_SCRIPT,
                    stager.OUTER_LAUNCHER,
                )
            ):
                raw = (stager.GIT_ROOT / Path(relative)).read_bytes()
                normalized = stager._lf_normalized_source(raw)
                records.append(f"H 100644 {stager._git_blob_oid(normalized)} 0\t{relative}\0")
            return FakeOutcome(safe("".join(records)))
        if "git-source-hash-object-" in name:
            raw = Path(str(_command[-1])).read_bytes()
            normalized = stager._lf_normalized_source(raw)
            return FakeOutcome(safe(stager._git_blob_oid(normalized) + "\n"))
        if "ubuntu-verbose" in name:
            return FakeOutcome(safe("  NAME STATE VERSION\n* Ubuntu Running 2\n"))
        if "ubuntu-running" in name:
            return FakeOutcome(safe("Ubuntu\n"))
        if "linux-identity-readback" in name:
            if self.unsafe_linux:
                value = safe("")
                value.update(
                    {
                        "safe_for_followup": False,
                        "active_process_zero": False,
                        "timed_out": True,
                        "residual_pids": [7331],
                    }
                )
                return FakeOutcome(value)
            return FakeOutcome(
                safe(json.dumps(_linux_payload(), sort_keys=True, separators=(",", ":")) + "\n")
            )
        raise AssertionError(f"unexpected command name: {name}")


@pytest.fixture(scope="module")
def measured_host_pins() -> dict[str, dict[str, Any]]:
    return stager._measure_host_pins()


def _test_stager_bootstrap_attestation(request: stager.StageRequest) -> dict[str, Any]:
    raw = Path(stager.__file__).read_bytes()
    normalized = stager._lf_normalized_source(raw)
    stager_args = [
        "--qualification-id",
        request.qualification_id,
        "--run-uuid",
        request.run_uuid,
        "--attempt-id",
        request.attempt_id,
        "--expected-source-commit",
        request.expected_source_commit,
        "--expected-source-tree",
        request.expected_source_tree,
        "--expected-qualification-sha256",
        request.expected_qualification_sha256,
        "--expected-process-module-sha256",
        request.expected_process_module_sha256,
        "--expected-r7s1-runner-sha256",
        request.expected_r7s1_runner_sha256,
        "--expected-stager-sha256",
        request.expected_stager_sha256,
        "--expected-outer-sha256",
        request.expected_outer_sha256,
        "--parent-map",
        str(request.parent_map_path),
        "--expected-parent-map-sha256",
        request.expected_parent_map_sha256,
        "--execute-stage-non-credit-once",
    ]
    attestation = {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.stager-bootstrap-attestation.v1",
        "stager_path": str(Path(stager.__file__)),
        "stager_sha256": hashlib.sha256(raw).hexdigest(),
        "stager_bytes": len(raw),
        "stager_lf_sha256": hashlib.sha256(normalized).hexdigest(),
        "stager_blob_oid": stager._git_blob_oid(normalized),
        "stager_argv_sha256": hashlib.sha256(
            json.dumps(stager_args, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "bootstrap_source_sha256": stager.STAGER_BOOTSTRAP_SOURCE_SHA256,
    }
    return attestation


def _stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
    *,
    runner: FakeRunner | None = None,
    request: stager.StageRequest | None = None,
) -> tuple[dict[str, Any], stager.StageRequest, FakeRunner, Path]:
    root = tmp_path / "evidence"
    root.mkdir()
    request = request or _request(tmp_path)
    runner = runner or FakeRunner()
    monkeypatch.setattr(stager, "_measure_host_pins", lambda: copy.deepcopy(measured_host_pins))
    result = stager._stage_once_impl(
        request,
        bootstrap_attestation=_test_stager_bootstrap_attestation(request),
        evidence_root=root,
        token_measure=lambda: dict(ADMIN_TOKEN),
        metadata_runner=runner,
        linux_runner=runner,
    )
    return result, request, runner, root


def test_success_stages_contract_and_revision_bound_index_without_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
) -> None:
    result, request, runner, root = _stage(tmp_path, monkeypatch, measured_host_pins)
    assert result["passed"] is True
    assert result["status"] == "staged_non_credit_not_executed"
    assert len(runner.names) == 14
    assert sum("linux-identity-readback" in name for name in runner.names) == 1
    staging = root / f"c-{request.attempt_id.replace('-', '')[:8]}"
    contract_path = staging / "qualification-contract.json"
    index_path = staging / "staging-index.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert contract["source_identity"]["commit"] == SOURCE_COMMIT
    assert contract["source_identity"]["tree"] == SOURCE_TREE
    assert contract["source_identity"]["stager"]["sha256"] == request.expected_stager_sha256
    assert contract["host_binaries"]["store_wsl"]["version"] == "2.7.12.0"
    assert result["index"]["sha256"] == _sha(index_path)
    assert contract["staging_attestation"]["preauthorization_index"]["sha256"] == _sha(
        staging / "preauthorization-index.json"
    )
    assert index["source_identity"]["commit"] == SOURCE_COMMIT
    assert index["source_identity"]["tree"] == SOURCE_TREE
    assert index["qualification_started"] is False
    bootstrap_sha = hashlib.sha256(stager.OUTER_BOOTSTRAP_SOURCE.encode()).hexdigest()
    assert bootstrap_sha == qualifier.OUTER_BOOTSTRAP_SOURCE_SHA256
    assert index["bootstrap"]["source_sha256"] == bootstrap_sha
    assert contract["outer_timeout_contract"] == stager.OUTER_TIMEOUT_CONTRACT
    assert index["outer_timeout_contract"] == stager.OUTER_TIMEOUT_CONTRACT
    command = result["outer_command"]
    assert command[:6] == [
        measured_host_pins["python"]["path"],
        "-I",
        "-S",
        "-B",
        "-c",
        stager.OUTER_BOOTSTRAP_SOURCE,
    ]
    assert command.count("--expected-source-commit") == 1
    assert command.count("--expected-source-tree") == 1
    assert command.count("--execute-non-credit-once") == 1
    preauthorization_path = staging / "preauthorization-index.json"
    preauthorization = json.loads(preauthorization_path.read_text(encoding="utf-8"))
    assert preauthorization["call_counts"]["linux_identity_readback"] == 1
    assert preauthorization["call_counts"]["automatic_retries"] == 0
    assert preauthorization["call_counts"]["forced_termination_attempts"] == 0
    assert preauthorization["call_counts"]["wsl_shutdown_calls"] == 0
    assert preauthorization["call_counts"]["docker_kubernetes_service_mutations"] == 0
    assert not (root / f"wsl-{request.attempt_id.replace('-', '')[:8]}").exists()
    loaded = qualifier.load_contract(
        contract_path,
        expected_sha256=_sha(contract_path),
        expected_evidence_root=root,
        launch_index_path=index_path,
        expected_launch_index_sha256=_sha(index_path),
    )
    assert loaded.source_identity["commit"] == SOURCE_COMMIT


def test_stale_wsl_2_7_11_pin_is_rejected_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    request = _request(tmp_path)
    changed = copy.deepcopy(stager.HOST_EXPECTATIONS)
    changed["store_wsl"]["version"] = "2.7.11.0"
    monkeypatch.setattr(stager, "HOST_EXPECTATIONS", changed)
    monkeypatch.setattr(stager, "CANONICAL_EVIDENCE_ROOT", root)
    monkeypatch.setattr(stager, "_measure_admin_token", lambda: dict(ADMIN_TOKEN))
    with pytest.raises(stager.R7S2StagerError, match="stale_wsl_2_7_11_pin_forbidden"):
        stager._stage_once_impl(
            request,
            bootstrap_attestation=_test_stager_bootstrap_attestation(request),
            evidence_root=root,
            token_measure=lambda: dict(ADMIN_TOKEN),
            metadata_runner=FakeRunner(),
            linux_runner=FakeRunner(),
        )
    assert not list(root.iterdir())


@pytest.mark.parametrize(
    "field",
    [
        "expected_qualification_sha256",
        "expected_process_module_sha256",
        "expected_r7s1_runner_sha256",
        "expected_outer_sha256",
    ],
)
def test_source_sha_repin_is_rejected_before_child_or_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
    field: str,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    request = _request(tmp_path, **{field: "f" * 64})
    runner = FakeRunner()
    monkeypatch.setattr(stager, "_measure_host_pins", lambda: copy.deepcopy(measured_host_pins))
    monkeypatch.setattr(stager, "CANONICAL_EVIDENCE_ROOT", root)
    monkeypatch.setattr(stager, "_measure_admin_token", lambda: dict(ADMIN_TOKEN))
    monkeypatch.setattr(stager, "_runner_factory", lambda *_args, **_kwargs: runner)
    with pytest.raises(stager.R7S2StagerError, match="pinned_file_sha256_mismatch"):
        stager._stage_once_impl(
            request,
            bootstrap_attestation=_test_stager_bootstrap_attestation(request),
            evidence_root=root,
            token_measure=lambda: dict(ADMIN_TOKEN),
            metadata_runner=runner,
            linux_runner=runner,
        )
    assert runner.names == []
    assert not list(root.iterdir())


def test_self_consistent_commit_tree_repin_is_rejected_by_loader_before_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
) -> None:
    request = _request(
        tmp_path,
        expected_source_commit="a" * 40,
        expected_source_tree="b" * 40,
    )
    runner = FakeRunner()
    result, request, runner, root = _stage(
        tmp_path,
        monkeypatch,
        measured_host_pins,
        runner=runner,
        request=request,
    )
    assert result["passed"] is False
    assert not (
        root / f"c-{request.attempt_id.replace('-', '')[:8]}" / "qualification-contract.json"
    ).exists()
    seal = json.loads(
        (
            root / f"c-{request.attempt_id.replace('-', '')[:8]}" / "staging-failure-seal.json"
        ).read_text(encoding="utf-8")
    )
    assert seal["failed_stage"] == "git_identity"
    assert "source_commit_tree_mismatch" in seal["exception"]


def test_linux_timeout_residual_stops_post_readback_and_creates_no_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
) -> None:
    runner = FakeRunner(unsafe_linux=True)
    result, request, runner, root = _stage(tmp_path, monkeypatch, measured_host_pins, runner=runner)
    assert result["passed"] is False
    assert len(runner.names) == 12
    assert not any("post" in name for name in runner.names)
    staging = root / f"c-{request.attempt_id.replace('-', '')[:8]}"
    assert not (staging / "qualification-contract.json").exists()
    seal = json.loads((staging / "staging-failure-seal.json").read_text(encoding="utf-8"))
    assert seal["failed_stage"] == "linux_identity_readback"
    assert seal["residual_pids"] == [7331]
    assert seal["automatic_retry_count"] == 0
    assert seal["forced_termination_attempts"] == 0
    assert seal["call_counts"]["linux_identity_readback"] == 1
    assert seal["call_counts"]["ubuntu_verbose_post"] == 0
    assert seal["call_counts"]["ubuntu_running_post"] == 0


@pytest.mark.parametrize(
    ("failed_leaf", "expected_stage", "contract_exists"),
    [
        ("qualification-contract.json", "qualification_contract_publication", False),
        ("staging-index.json", "final_launch_index_publication", True),
    ],
)
def test_contract_writer_or_index_failure_is_sealed_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
    failed_leaf: str,
    expected_stage: str,
    contract_exists: bool,
) -> None:
    real_publish = stager._atomic_exclusive_json
    attempts = 0

    def fail_selected(path: Path, value: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        if path.name == failed_leaf:
            attempts += 1
            raise PermissionError("synthetic_writer_permission_failure")
        return real_publish(path, value)

    monkeypatch.setattr(stager, "_atomic_exclusive_json", fail_selected)
    result, request, _runner, root = _stage(tmp_path, monkeypatch, measured_host_pins)
    assert result["passed"] is False
    assert attempts == 1
    staging = root / f"c-{request.attempt_id.replace('-', '')[:8]}"
    assert (staging / "qualification-contract.json").exists() is contract_exists
    seal = json.loads((staging / "staging-failure-seal.json").read_text(encoding="utf-8"))
    assert seal["failed_stage"] == expected_stage
    assert seal["automatic_retry_count"] == 0
    assert seal["qualification_started"] is False


def test_reservation_permission_failure_uses_unique_parent_emergency_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
) -> None:
    real_publish = stager._atomic_exclusive_json
    attempts = 0

    def fail_reservation(path: Path, value: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        if path.name == "staging-reservation.json":
            attempts += 1
            raise PermissionError("synthetic_reservation_permission_failure")
        return real_publish(path, value)

    monkeypatch.setattr(stager, "_atomic_exclusive_json", fail_reservation)
    result, request, runner, root = _stage(tmp_path, monkeypatch, measured_host_pins)
    assert result["passed"] is False
    assert attempts == 1
    assert runner.names == []
    emergency = root / f"c-{request.attempt_id.replace('-', '')[:8]}-emergency-seal"
    payload = json.loads((emergency / "emergency-seal.json").read_text(encoding="utf-8"))
    assert payload["failed_stage"] == "staging_reservation_publication"
    assert payload["automatic_retry_count"] == 0
    assert payload["forced_termination_attempts"] == 0


def test_concurrent_or_replayed_attempt_id_is_rejected_without_child_or_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_host_pins: dict[str, dict[str, Any]],
) -> None:
    result, request, _runner, root = _stage(tmp_path, monkeypatch, measured_host_pins)
    assert result["passed"] is True
    staging = root / f"c-{request.attempt_id.replace('-', '')[:8]}"
    before = {path.name: path.read_bytes() for path in staging.iterdir()}
    replay_runner = FakeRunner()
    monkeypatch.setattr(stager, "_runner_factory", lambda *_args, **_kwargs: replay_runner)
    with pytest.raises(FileExistsError):
        stager._stage_once_impl(
            request,
            bootstrap_attestation=_test_stager_bootstrap_attestation(request),
            evidence_root=root,
            token_measure=lambda: dict(ADMIN_TOKEN),
            metadata_runner=replay_runner,
            linux_runner=replay_runner,
        )
    assert replay_runner.names == []
    assert {path.name: path.read_bytes() for path in staging.iterdir()} == before


def test_non_admin_token_creates_no_staging_or_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    request = _request(tmp_path)
    token = {**ADMIN_TOKEN, "administrator": False, "integrity": "Medium"}
    runner = FakeRunner()
    monkeypatch.setattr(stager, "CANONICAL_EVIDENCE_ROOT", root)
    monkeypatch.setattr(stager, "_measure_admin_token", lambda: token)
    monkeypatch.setattr(stager, "_runner_factory", lambda *_args, **_kwargs: runner)
    with pytest.raises(stager.R7S2StagerError, match="administrator_token_required"):
        stager._stage_once_impl(
            request,
            bootstrap_attestation=_test_stager_bootstrap_attestation(request),
            evidence_root=root,
            token_measure=lambda: token,
            metadata_runner=runner,
            linux_runner=runner,
        )
    assert runner.names == []
    assert not list(root.iterdir())


def test_source_has_no_force_retry_lifecycle_or_shell_execution_path() -> None:
    source = Path(stager.__file__).read_text(encoding="utf-8")
    forbidden = (
        "Terminate" + "JobObject",
        "task" + "kill",
        "wsl --" + "shutdown",
        "docker compose",
        "kubectl.exe",
        ".ki" + "ll(",
        ".termi" + "nate(",
        "git reset",
        "git clean",
    )
    for token in forbidden:
        assert token not in source
    assert '"automatic_retries": 0' in source
    assert '"forced_termination_attempts": 0' in source
    assert '"wsl_shutdown_calls": 0' in source
    assert '"docker_kubernetes_service_mutations": 0' in source


def test_production_stage_is_root_anchor_blocked_before_write_or_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path)
    child_calls: list[str] = []
    monkeypatch.setattr(stager, "CANONICAL_EVIDENCE_ROOT", tmp_path / "absent-evidence")
    monkeypatch.setattr(
        stager,
        "_require_stager_bootstrap_attestation",
        lambda: child_calls.append("bootstrap") or {},
    )
    monkeypatch.setattr(
        stager, "_runner_factory", lambda *_args, **_kwargs: child_calls.append("runner")
    )
    assert stager.R7S2_OOB_ROOT_ANCHOR_IMPLEMENTED is False
    with pytest.raises(stager.R7S2StagerError, match="r7s2_out_of_band_root_anchor_required"):
        stager.stage_once(request)
    assert child_calls == []
    assert not stager.CANONICAL_EVIDENCE_ROOT.exists()


@pytest.mark.parametrize(
    "stdout",
    [
        "A" * 40 + "\n" + "b" * 40 + "\n",
        "a" * 40 + " \n" + "b" * 40 + "\n",
        "a" * 40 + "\n" + "b" * 40,
        "a" * 40 + "\n" + "b" * 40 + "\nextra\n",
        "a" * 40 + "\n" + "b" * 40 + "\n\ufffd",
    ],
)
def test_git_identity_exact_stream_grammar_rejects_noncanonical(stdout: str) -> None:
    outcome = {"stdout": stdout, "stderr": ""}
    with pytest.raises(stager.R7S2StagerError):
        stager._require_git_identity(outcome, "a" * 40, "b" * 40)


@pytest.mark.parametrize(
    "stdout",
    [
        "100644  blob " + "a" * 40 + "\tpath.py\0",
        "100644 blob " + "A" * 40 + "\tpath.py\0",
        "100644 blob " + "a" * 40 + "\tpath.py",
        "100644 blob " + "a" * 40 + "\tpath.py\0\0",
        "100644 blob " + "a" * 40 + "\tpath.py\0extra",
    ],
)
def test_git_ls_tree_exact_nul_grammar_rejects_mutations(stdout: str) -> None:
    with pytest.raises(stager.R7S2StagerError, match="git_ls_tree"):
        stager._parse_ls_tree_records(stdout)


@pytest.mark.parametrize(
    "stdout",
    [
        "H 100644 " + "A" * 40 + " 0\tpath.py\0",
        "H 100644 " + "a" * 40 + " 0\tpath.py",
        "H 100644 " + "a" * 40 + " 0\tpath.py\0\0",
        "H  100644 " + "a" * 40 + " 0\tpath.py\0",
        "S 100644 " + "a" * 40 + " 0\tpath.py\0",
        "H 100644 " + "a" * 40 + " 1\tpath.py\0",
        "H 100644 " + "a" * 40 + " 0\tpath.py\0H 100644 " + "a" * 40 + " 0\tpath.py\0",
    ],
)
def test_git_ls_files_exact_nul_grammar_rejects_mutations(stdout: str) -> None:
    with pytest.raises(stager.R7S2StagerError, match="git_ls_files"):
        stager._parse_ls_files_records(stdout)


@pytest.mark.parametrize(
    "value",
    [
        "  NAME STATE VERSION\n* Ubuntu Stopped 2\n",
        "  NAME STATE VERSION\n* Ubuntu Running 1\n",
        "  NAME STATE VERSION\n* Ubuntu Running 2\nUbuntu Running 2\n",
        "  NAME STATE VERSION\n* Ubuntu Running 2\x00\n",
        "  NAME STATE VERSION\n* Ubuntu Running 2\ufffd\n",
    ],
)
def test_wsl_verbose_exact_running_v2_grammar_rejects_mutations(value: str) -> None:
    with pytest.raises(stager.R7S2StagerError):
        stager._require_ubuntu_verbose_running(value, "test_wsl_verbose")


def test_linux_json_rejects_duplicate_nonfinite_and_noncanonical() -> None:
    for value in (
        '{"a":1,"a":1}\n',
        '{"a":NaN}\n',
        '{ "a":1}\n',
        '{"a":1}\ntrailing\n',
        '{"a":"\ufffd"}\n',
    ):
        with pytest.raises(stager.R7S2StagerError):
            stager._canonical_json_line(value, "test_linux_json")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"same":1,"same":1}\n',
        b'{"value":NaN}\n',
        b'{"value":1}\ntrailing',
        b'{"value":"\xff"}\n',
        b'{ "value":1}\n',
    ],
)
def test_stager_root_json_loader_rejects_ambiguous_or_noncanonical(raw: bytes) -> None:
    with pytest.raises(stager.R7S2StagerError):
        stager._strict_json_bytes(raw, "test_root", canonical_owned=True)


def test_stager_bootstrap_uses_distinct_exact_runner_and_stager_sha_positions(
    tmp_path: Path,
) -> None:
    fake_stager = tmp_path / "verified-stager.py"
    good = tmp_path / "verified-buffer-executed.txt"
    unverified = tmp_path / "post-verify-swap-executed.txt"
    good_source = (
        "from pathlib import Path\n"
        f"Path({str(good)!r}).write_text('good', encoding='utf-8')\n"
        "print('STAGER_BOOTSTRAP_OK')\n"
    )
    malicious_source = (
        f"from pathlib import Path\nPath({str(unverified)!r}).write_text('bad', encoding='utf-8')\n"
    )
    # The on-disk path is hostile.  The harness returns the verified bytes for
    # the first open and hostile bytes for every later open.  A regression to
    # verify(path); exec(open(path)) therefore runs the sentinel, while the
    # intended one-buffer implementation performs exactly one target read.
    fake_stager.write_text(malicious_source, encoding="utf-8", newline="\n")
    raw = good_source.encode("utf-8")
    raw_sha = hashlib.sha256(raw).hexdigest()
    blob = stager._git_blob_oid(raw)
    runner_sha = "7" * 64
    option_values = [
        ("--qualification-id", RUN_ID),
        ("--run-uuid", RUN_UUID),
        ("--attempt-id", ATTEMPT_ID),
        ("--expected-source-commit", SOURCE_COMMIT),
        ("--expected-source-tree", SOURCE_TREE),
        ("--expected-qualification-sha256", "1" * 64),
        ("--expected-process-module-sha256", "2" * 64),
        ("--expected-r7s1-runner-sha256", runner_sha),
        ("--expected-stager-sha256", raw_sha),
        ("--expected-outer-sha256", "3" * 64),
        ("--parent-map", str(tmp_path / "parent-map.json")),
        ("--expected-parent-map-sha256", "4" * 64),
    ]
    stager_args = [item for pair in option_values for item in pair] + [
        "--execute-stage-non-credit-once"
    ]
    assert stager_args[15] == runner_sha
    assert stager_args[17] == raw_sha
    harness = (
        "import builtins,io,os,sys\n"
        f"bootstrap={stager.STAGER_BOOTSTRAP_SOURCE!r}\n"
        f"good={raw!r}\n"
        f"bad={malicious_source.encode('utf-8')!r}\n"
        "target=os.path.normcase(os.path.abspath(sys.argv[1]))\n"
        "sys.orig_argv=[sys.executable,'-I','-S','-B','-c',bootstrap,*sys.argv[1:]]\n"
        "real_open=builtins.open\n"
        "target_reads=0\n"
        "def controlled_open(path,mode='r',*args,**kwargs):\n"
        " global target_reads\n"
        " candidate=os.path.normcase(os.path.abspath(os.fspath(path))) if isinstance(path,(str,bytes,os.PathLike)) else None\n"
        " if candidate==target and mode=='rb':\n"
        "  target_reads+=1\n"
        "  return io.BytesIO(good if target_reads==1 else bad)\n"
        " return real_open(path,mode,*args,**kwargs)\n"
        "builtins.open=controlled_open\n"
        "try:\n"
        " exec(compile(bootstrap,'<trusted-stager-bootstrap>','exec'),globals(),globals())\n"
        "finally:\n"
        " builtins.open=real_open\n"
        "print('TARGET_READS='+str(target_reads))\n"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            harness,
            str(fake_stager),
            raw_sha,
            str(len(raw)),
            raw_sha,
            blob,
            *stager_args,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "STAGER_BOOTSTRAP_OK\nTARGET_READS=1\n"
    assert good.read_text(encoding="utf-8") == "good"
    assert not unverified.exists()


def test_stager_bootstrap_attestation_rejects_source_argv_replay_and_domain_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path)
    stager_args = [
        "--qualification-id",
        request.qualification_id,
        "--run-uuid",
        request.run_uuid,
        "--attempt-id",
        request.attempt_id,
        "--expected-source-commit",
        request.expected_source_commit,
        "--expected-source-tree",
        request.expected_source_tree,
        "--expected-qualification-sha256",
        request.expected_qualification_sha256,
        "--expected-process-module-sha256",
        request.expected_process_module_sha256,
        "--expected-r7s1-runner-sha256",
        request.expected_r7s1_runner_sha256,
        "--expected-stager-sha256",
        request.expected_stager_sha256,
        "--expected-outer-sha256",
        request.expected_outer_sha256,
        "--parent-map",
        str(request.parent_map_path),
        "--expected-parent-map-sha256",
        request.expected_parent_map_sha256,
        "--execute-stage-non-credit-once",
    ]
    valid = _test_stager_bootstrap_attestation(request)
    monkeypatch.setattr(
        stager.sys,
        "orig_argv",
        [sys.executable, "-I", "-S", "-B", "-c", stager.STAGER_BOOTSTRAP_SOURCE],
    )
    monkeypatch.setattr(stager.sys, "argv", [str(stager.STAGER_INVOCATION_PATH), *stager_args])
    monkeypatch.setitem(
        stager.__dict__, "__evm_r7s2_stager_bootstrap_attestation__", copy.deepcopy(valid)
    )
    assert stager._require_stager_bootstrap_attestation() == valid

    for mutated in (
        {**valid, "bootstrap_source_sha256": "f" * 64},
        {**valid, "stager_argv_sha256": "e" * 64},
        {"schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s2.bootstrap-attestation.v1"},
    ):
        monkeypatch.setitem(stager.__dict__, "__evm_r7s2_stager_bootstrap_attestation__", mutated)
        with pytest.raises(stager.R7S2StagerError):
            stager._require_stager_bootstrap_attestation()

    monkeypatch.setitem(
        stager.__dict__, "__evm_r7s2_stager_bootstrap_attestation__", copy.deepcopy(valid)
    )
    monkeypatch.setattr(
        stager.sys, "argv", [str(stager.STAGER_INVOCATION_PATH), *stager_args, "--replayed"]
    )
    with pytest.raises(stager.R7S2StagerError):
        stager._require_stager_bootstrap_attestation()


@pytest.mark.parametrize(
    "mutation", ["event_reorder", "sequence_gap", "final_nonzero", "limit_terminated"]
)
def test_success_job_timeline_rejects_core_mutations(mutation: str) -> None:
    command = [r"C:\Windows\System32\wsl.exe", "--status"]
    outcome = _safe("", name="timeline-test", command=command)
    if mutation == "event_reorder":
        outcome["events"][1], outcome["events"][2] = (
            outcome["events"][2],
            outcome["events"][1],
        )
    elif mutation == "sequence_gap":
        outcome["accounting"][0]["sequence"] = 70
    elif mutation == "final_nonzero":
        outcome["accounting"][-1]["active_processes"] = 1
        outcome["accounting"][-1]["active_pids"] = [1000]
    else:
        outcome["accounting"][-1]["total_terminated_processes"] = 1
    with pytest.raises(stager.R7S2StagerError):
        stager._validate_success_job_timeline(
            outcome,
            label="timeline-test",
            run_uuid=RUN_UUID,
            expected_image=command[0],
        )

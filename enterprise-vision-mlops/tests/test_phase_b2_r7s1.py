from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evm.scale_validation import phase_b2_r7 as r7
from evm.scale_validation import phase_b2_r7s1 as r7s1
from test_phase_b2_r7 import manifest as r7_manifest


SHA = "c" * 64
TARGET = {
    "run_id": "9bd54156084842ca93bce35a44a0cea7",
    "status": "RUNNING",
    "lifecycle_stage": "active",
    "start_time": "1783653474422",
    "end_time": "",
}
RUN_ID = "x1-phase-b2-r7s1-test-restore"
ATTEMPT_ID = "1d2f8c8d-3534-4b6f-9129-8dfedc76a471"
BINDING = {
    "run_id": RUN_ID,
    "attempt_id": ATTEMPT_ID,
    "commit": "a" * 40,
    "tree": "b" * 40,
    "nonce": "e" * 64,
    "parent_map_sha256": "f" * 64,
    "staging_path": str((r7s1.CANONICAL_STAGING_ROOT / RUN_ID).resolve()),
    "output_path": str((r7s1.CANONICAL_OUTPUT_ROOT / RUN_ID).resolve()),
    "emergency_seal_path": str((r7s1.CANONICAL_OUTPUT_ROOT / f"{RUN_ID}-emergency-seal").resolve()),
}


def _write_json(path: Path, value: object) -> dict[str, str]:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _write_source(path: Path, value: dict[str, object]) -> dict[str, object]:
    pin: dict[str, object] = _write_json(path, value)
    pin.update(
        {
            "schema": value["schema"],
            "captured_at": value["captured_at"],
            "ordinal": value["ordinal"],
            "source_revision": value["source_revision"],
        }
    )
    return pin


def _safe_command(name: str, index: int) -> dict[str, object]:
    run_uuid = str(uuid.UUID(int=10_000 + index))
    pid = 20_000 + index
    if name == "windows_global_residuals":
        argv = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$excluded=@(123);read-only",
        ]
    elif name == "windows_run_links":
        argv = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$excluded=@($PID,123,456);read-only",
        ]
    else:
        argv = [f"{name}.exe", "read-only"]
    empty_sha = hashlib.sha256(b"").hexdigest()
    return {
        "accounting": [
            {
                "active_pids": [pid],
                "active_processes": 1,
                "monotonic_ns": 2,
                "sequence": 2,
                "timestamp_utc": "2026-09-01T00:00:00.200000+00:00",
                "total_processes": 1,
                "total_terminated_processes": 0,
            },
            {
                "active_pids": [],
                "active_processes": 0,
                "monotonic_ns": 4,
                "sequence": 4,
                "timestamp_utc": "2026-09-01T00:00:00.900000+00:00",
                "total_processes": 1,
                "total_terminated_processes": 0,
            },
        ],
        "active_process_zero": True,
        "cancelled": False,
        "command": argv,
        "duration_seconds": 1.0,
        "ended_at_utc": "2026-09-01T00:00:01+00:00",
        "errors": [],
        "events": [
            {
                "details": {"run_uuid": run_uuid},
                "event": "job_created",
                "monotonic_ns": 1,
                "pid": None,
                "sequence": 1,
                "timestamp_utc": "2026-09-01T00:00:00.100000+00:00",
            },
            {
                "details": {"run_uuid": run_uuid},
                "event": "job_new_process",
                "monotonic_ns": 3,
                "pid": pid,
                "sequence": 3,
                "timestamp_utc": "2026-09-01T00:00:00.300000+00:00",
            },
        ],
        "final_active_process_count": 0,
        "forced_termination_attempts": 0,
        "identities": [
            {
                "creation_time_ns": 1_000_000 + index,
                "creation_time_utc": "2026-09-01T00:00:00.150000+00:00",
                "image": f"C:\\test\\{name}.exe",
                "observed_sequence": 3,
                "pid": pid,
                "ppid": 100,
                "run_uuid": run_uuid,
            }
        ],
        "identity_coverage_complete": True,
        "job_limit_flags": 0,
        "manual_intervention_required": False,
        "name": name,
        "residual_pids": [],
        "return_code": 0,
        "run_uuid": run_uuid,
        "safe_for_followup": True,
        "safe_for_followup_gate": True,
        "started_at_utc": "2026-09-01T00:00:00+00:00",
        "stderr": {"bytes": 0, "redacted": True, "sha256": empty_sha},
        "stderr_drained": True,
        "stdout": {"bytes": 0, "redacted": True, "sha256": empty_sha},
        "stdout_drained": True,
        "streams_drained": True,
        "timed_out": False,
    }


@pytest.fixture(autouse=True)
def _pin_synthetic_observation_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_commands = [
        _safe_command(name, index)
        for index, name in enumerate(r7s1.SNAPSHOT_COMMAND_NAMES, start=1)
    ]
    link_commands = [
        _safe_command(name, index + 100)
        for index, name in enumerate(r7s1.LINK_SCAN_COMMAND_NAMES, start=1)
    ]
    monkeypatch.setattr(
        r7s1,
        "SNAPSHOT_ARGV_SHA256",
        {
            command["name"]: r7s1._normalized_observation_argv_sha256(
                command["name"], command["command"], "test"
            )
            for command in snapshot_commands
        },
    )
    monkeypatch.setattr(
        r7s1,
        "LINK_SCAN_ARGV_SHA256",
        {
            command["name"]: r7s1._normalized_observation_argv_sha256(
                command["name"], command["command"], "test"
            )
            for command in link_commands
        },
    )


def _snapshot(ordinal: int, captured_at: str) -> dict[str, object]:
    return {
        "schema": r7s1.SNAPSHOT_SCHEMA,
        "captured_at": captured_at,
        "ordinal": ordinal,
        "all_commands_safe": True,
        "automatic_retry_count": 0,
        "command_count": len(r7s1.SNAPSHOT_COMMAND_NAMES),
        "commands": [
            _safe_command(name, index)
            for index, name in enumerate(r7s1.SNAPSHOT_COMMAND_NAMES, start=1)
        ],
        "expected_command_count": len(r7s1.SNAPSHOT_COMMAND_NAMES),
        "process_containment": {
            "type": "windows_job_object",
            "create_suspended_before_assignment": True,
            "kill_on_job_close": False,
            "terminate_job_object_calls": 0,
            "forced_termination_attempts": 0,
        },
        "query_sha256": dict(r7s1.SNAPSHOT_QUERY_SHA256),
        "read_only": True,
        "repository": r7s1.SNAPSHOT_REPOSITORY,
        "service_mutation_count": 0,
        "source_revision": r7s1.OBSERVATION_SOURCE_REVISION,
        "stopped_after": None,
        "observed": {
            "compose_project_containers": [],
            "control_plane_execution_links": [],
            "control_plane_history": [],
            "kubernetes_failed_pods": [],
            "kubernetes_jobs": [],
            "mlflow_activity": [
                {
                    **TARGET,
                    "metric_count": 7,
                    "last_metric_timestamp": "1783653475809",
                    "parameter_count": 13,
                    "tag_count": 5,
                }
            ],
            "queue_claims": {
                "active": 0,
                "active_claims": 0,
                "leased": 0,
                "outcome_unknown": 0,
                "unknown_state": 0,
            },
            "windows_global_residuals": [],
            "wsl_global_residuals": [],
        },
    }


def _scan(ordinal: int, captured_at: str) -> dict[str, object]:
    control_plane_tables = (
        "entities",
        "idempotency_keys",
        "lifecycle_claims",
        "s6bm_causal_events",
        "s6bm_route_revisions",
        "side_effect_outbox",
        "task_admission_queue",
        "task_dispatch_effects",
    )
    airflow_tables = (
        "dag_run",
        "rendered_task_instance_fields",
        "task_instance",
        "xcom",
    )
    return {
        "schema": r7s1.LINK_SCAN_SCHEMA,
        "captured_at": captured_at,
        "ordinal": ordinal,
        "target_run_id": TARGET["run_id"],
        "all_commands_safe": True,
        "all_exact_links_zero": True,
        "automatic_retry_count": 0,
        "command_count": len(r7s1.LINK_SCAN_COMMAND_NAMES),
        "commands": [
            _safe_command(name, index + 100)
            for index, name in enumerate(r7s1.LINK_SCAN_COMMAND_NAMES, start=1)
        ],
        "expected_command_count": len(r7s1.LINK_SCAN_COMMAND_NAMES),
        "forced_termination_attempts": 0,
        "query_sha256": dict(r7s1.LINK_SCAN_QUERY_SHA256),
        "read_only": True,
        "service_mutation_count": 0,
        "source_revision": r7s1.OBSERVATION_SOURCE_REVISION,
        "stopped_after": None,
        "observed": {
            "control_plane_run_links": [
                {"table": table, "identity_matches": 0, "payload_matches": 0}
                for table in control_plane_tables
            ],
            "airflow_run_links": [
                {
                    "table": table,
                    "identity_matches": 0,
                    "payload_matches": 0,
                    "active_matches": 0,
                }
                for table in airflow_tables
            ],
            "docker_run_links": {
                "matches": [],
                "matching_count": 0,
                "observed_count": 19,
            },
            "kubernetes_run_links": {
                "matches": [],
                "matching_count": 0,
                "observed_count": 163,
            },
            "windows_run_links": {"matches": [], "matching_count": 0},
            "wsl_run_links": {"matches": [], "matching_count": 0},
        },
    }


def _external_contract(
    tmp_path: Path,
    *,
    with_decision: bool,
    decision_mutation: tuple[str, object] | None = None,
) -> dict[str, object]:
    snapshot_payloads = [
        _snapshot(1, "2026-09-01T00:00:00Z"),
        _snapshot(2, "2026-09-01T00:01:00Z"),
    ]
    snapshots = [
        _write_source(tmp_path / "snapshot-1.json", snapshot_payloads[0]),
        _write_source(tmp_path / "snapshot-2.json", snapshot_payloads[1]),
    ]
    scans = [
        _write_source(tmp_path / "scan-1.json", _scan(1, "2026-09-01T00:00:10Z")),
        _write_source(tmp_path / "scan-2.json", _scan(2, "2026-09-01T00:01:10Z")),
    ]
    decision_pin = None
    checkpoint_pin = None
    if with_decision:
        activity_sha256 = [
            hashlib.sha256(
                r7s1.canonical_json_bytes(payload["observed"]["mlflow_activity"][0])
            ).hexdigest()
            for payload in snapshot_payloads
        ]
        support = {
            "historical_snapshot_1": snapshots[0]["sha256"],
            "historical_snapshot_2": snapshots[1]["sha256"],
            "exact_link_scan_1": scans[0]["sha256"],
            "exact_link_scan_2": scans[1]["sha256"],
            "successor_binding_sha256": hashlib.sha256(
                r7s1.canonical_json_bytes(BINDING)
            ).hexdigest(),
            "historical_snapshot_1_target_activity_sha256": activity_sha256[0],
            "historical_snapshot_2_target_activity_sha256": activity_sha256[1],
        }
        decision: dict[str, object] = {
            "schema": r7s1.TERMINAL_FENCING_DECISION_SCHEMA,
            "target_source": "mlflow_running_rows",
            "target_identity": dict(TARGET),
            "successor_binding": dict(BINDING),
            "decision": "proven_terminal_fenced",
            "decision_authority": r7s1.EXTERNAL_DECISION_AUTHORITY,
            "issued_at": "2026-09-01T00:02:00Z",
            "future_dispatch_fenced": True,
            "supporting_sha256": dict(support),
        }
        if decision_mutation is not None:
            decision[decision_mutation[0]] = decision_mutation[1]
        decision_pin = _write_json(tmp_path / "decision.json", decision)
        decision_pin["schema"] = r7s1.TERMINAL_FENCING_DECISION_SCHEMA
        checkpoint = {
            "schema": r7s1.TRUSTED_CHECKPOINT_SCHEMA,
            "checkpointed_at": "2026-09-01T00:02:20Z",
            "expires_at": "2026-09-01T01:00:00Z",
            "decision_authority": r7s1.EXTERNAL_DECISION_AUTHORITY,
            "independent_approval": {
                "source": "external-review-system",
                "reviewer_identity": "reviewer-1",
                "approval_id": "approval-1",
            },
            "successor_binding": dict(BINDING),
            "target_source": "mlflow_running_rows",
            "target_identity_sha256": hashlib.sha256(r7s1.canonical_json_bytes(TARGET)).hexdigest(),
            "decision_sha256": decision_pin["sha256"],
            "supporting_sha256": dict(support),
            "fence_readback": {
                "target_run_id": TARGET["run_id"],
                "future_dispatch_fenced": True,
                "fence_state": "fenced",
                "read_back_at": "2026-09-01T00:02:10Z",
            },
        }
        checkpoint_pin = _write_json(tmp_path / "checkpoint.json", checkpoint)
        checkpoint_pin["schema"] = r7s1.TRUSTED_CHECKPOINT_SCHEMA
    return {
        "target_source": "mlflow_running_rows",
        "target_identity": dict(TARGET),
        "successor_binding": dict(BINDING),
        "decision_authority": r7s1.EXTERNAL_DECISION_AUTHORITY,
        "snapshots": snapshots,
        "exact_link_scans": scans,
        "terminal_decision": decision_pin,
        "trusted_checkpoint": checkpoint_pin,
    }


def _validate_external(
    contract: dict[str, object], *, trust_checkpoint: bool = True
) -> dict[str, object]:
    checkpoint = contract["trusted_checkpoint"]
    expected_sha = checkpoint["sha256"] if trust_checkpoint and checkpoint is not None else None
    return r7s1.validate_external_terminal_fencing(
        contract,
        verify_files=True,
        expected_trusted_checkpoint_sha256=expected_sha,
        validation_time=datetime(2026, 9, 1, 0, 3, tzinfo=UTC),
    )


def _typed_pods() -> list[dict[str, object]]:
    return [
        dict(zip(r7s1.FAILED_POD_IDENTITY_FIELDS, identity, strict=True))
        for identity in r7s1.EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES
    ]


def _toolchain(tmp_path: Path) -> dict[str, object]:
    signature = {"status": "valid", "subject": "test signer", "thumbprint": "f" * 40}

    def host(name: str) -> dict[str, object]:
        if name == "docker_compose":
            path = r7s1.DOCKER_COMPOSE_EXECUTABLE
            return {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
                "version": "test-compose",
                "signature": dict(signature),
            }
        return {
            "path": str((tmp_path / f"{name}.exe").resolve()),
            "sha256": SHA,
            "bytes": 1,
            "version": "1.0",
            "signature": dict(signature),
        }

    def artifact(name: str, schema: str) -> dict[str, str]:
        return {"path": str((tmp_path / f"{name}.json").resolve()), "sha256": SHA, "schema": schema}

    return {
        **{name: host(name) for name in r7s1.HOST_TOOLCHAIN_ROLES},
        "docker_client_config": {
            "path": str(r7s1.CANONICAL_DOCKER_CLIENT_CONFIG_PATH),
            "sha256": r7s1.CANONICAL_DOCKER_CLIENT_CONFIG_SHA256,
            "bytes": r7s1.CANONICAL_DOCKER_CLIENT_CONFIG_BYTES,
            "context_metadata": {
                "path": str(r7s1.CANONICAL_DOCKER_CONTEXT_METADATA_PATH),
                "sha256": r7s1.CANONICAL_DOCKER_CONTEXT_METADATA_SHA256,
                "bytes": r7s1.CANONICAL_DOCKER_CONTEXT_METADATA_BYTES,
            },
            "policy": copy.deepcopy(r7s1.DOCKER_CLIENT_CONFIG_POLICY),
            "readback": artifact(
                "docker-client-config",
                "s8-v4-x1-phase-b2-r7s1-docker-client-config-readback/v1",
            ),
        },
        "git_repository_attributes": {
            "path": str(r7s1.CANONICAL_GIT_ATTRIBUTES_PATH),
            "sha256": r7s1.CANONICAL_GIT_ATTRIBUTES_SHA256,
            "bytes": r7s1.CANONICAL_GIT_ATTRIBUTES_BYTES,
            "policy": copy.deepcopy(r7s1.GIT_REPOSITORY_ATTRIBUTES_POLICY),
            "readback": artifact(
                "git-attributes",
                "s8-v4-x1-phase-b2-r7s1-git-repository-attributes-readback/v1",
            ),
        },
        "git_repository_config": {
            "path": str(r7s1.CANONICAL_GIT_CONFIG_PATH),
            "sha256": r7s1.CANONICAL_GIT_CONFIG_SHA256,
            "bytes": r7s1.CANONICAL_GIT_CONFIG_BYTES,
            "policy": copy.deepcopy(r7s1.GIT_REPOSITORY_CONFIG_POLICY),
            "readback": artifact(
                "git-config",
                "s8-v4-x1-phase-b2-r7s1-git-repository-config-readback/v1",
            ),
        },
        "kubernetes_client_config": {
            "path": str(r7s1.CANONICAL_KUBERNETES_CLIENT_CONFIG_PATH),
            "sha256": r7s1.CANONICAL_KUBERNETES_CLIENT_CONFIG_SHA256,
            "bytes": r7s1.CANONICAL_KUBERNETES_CLIENT_CONFIG_BYTES,
            "policy": copy.deepcopy(r7s1.KUBERNETES_CLIENT_CONFIG_POLICY),
            "readback": artifact(
                "kubernetes-client-config",
                "s8-v4-x1-phase-b2-r7s1-kubernetes-client-config-readback/v1",
            ),
        },
        "python_distribution": {
            "implementation": "CPython",
            "name": "test-python",
            "version": "3.13.0",
            "base_prefix": str((tmp_path / "python-base").resolve()),
            "distribution_tree_sha256": SHA,
            "file_count": 10,
            "included_roots": ["*.exe", "*.dll", "python*.zip", "DLLs/**", "Lib/**"],
            "excluded_roots": [
                "Lib/site-packages/**",
                "**/__pycache__/**",
                "**/*.pyc",
                "**/*.pyo",
            ],
            "tree_encoding": (
                "ordinal-relative-posix-utf8-nul-size-nul-sha256-nul;"
                "include=*.exe,*.dll,python*.zip,DLLs/**,Lib/**;"
                "exclude=Lib/site-packages/**,**/__pycache__/**,**/*.pyc,**/*.pyo"
            ),
            "evidence": artifact(
                "python-distribution",
                "s8-v4-x1-phase-b2-r7s1-python-distribution-readback/v1",
            ),
        },
        "git_distribution": {
            "root": str(Path("C:/Program Files/Git").resolve()),
            "distribution_tree_sha256": SHA,
            "file_count": 1,
            "tree_encoding": (
                "ordinal-relative-posix-utf8-nul-size-nul-sha256-nul;"
                "all-regular-files;reparse=reject"
            ),
            "evidence": artifact(
                "git-distribution",
                "s8-v4-x1-phase-b2-r7s1-git-distribution-readback/v1",
            ),
        },
        "windows_tcb": {
            "build": "test-build",
            "system32_path": str((tmp_path / "System32").resolve()),
            "kernel": host("ntoskrnl"),
            "evidence": artifact("windows-tcb", "s8-v4-x1-phase-b2-r7s1-windows-tcb-readback/v1"),
        },
        "wsl_runtime": {
            "distro": "docker-desktop",
            "kernel_release": "test-kernel",
            "rootfs_identity": "test-rootfs",
            "python3": {
                "realpath": "/usr/bin/python3.13",
                "sha256": SHA,
                "bytes": 1,
                "version": "3.13.0",
            },
            "readback": artifact("wsl", "s8-v4-x1-phase-b2-r7s1-wsl-runtime-readback/v1"),
        },
        "container_psql": {
            "container_name": "evm-postgres",
            "image_digest": "sha256:" + "d" * 64,
            "realpath": "/usr/bin/psql",
            "sha256": SHA,
            "bytes": 1,
            "version": "17.0",
            "execution_scope": dict(r7s1.DOCKER_CONTAINER_EXECUTION_SCOPE),
            "readback": artifact("psql", "s8-v4-x1-phase-b2-r7s1-container-psql-readback/v1"),
        },
    }


def _materialized_toolchain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    attributes_path = (tmp_path / "project" / ".gitattributes").resolve()
    attributes_path.parent.mkdir(parents=True)
    attributes_payload = (Path(__file__).parents[1] / ".gitattributes").read_bytes()
    assert len(attributes_payload) == r7s1.CANONICAL_GIT_ATTRIBUTES_BYTES
    assert hashlib.sha256(attributes_payload).hexdigest() == r7s1.CANONICAL_GIT_ATTRIBUTES_SHA256
    attributes_path.write_bytes(attributes_payload)
    monkeypatch.setattr(r7s1, "CANONICAL_GIT_ATTRIBUTES_PATH", attributes_path)
    # Isolate both required-absence checks from the ambient canonical checkout.
    # Later hardening legitimately added a top-level .gitattributes there, which
    # must not make this mutation test fail before it reaches its target field.
    monkeypatch.setattr(
        r7s1,
        "CANONICAL_GIT_TOP_ATTRIBUTES_PATH",
        (tmp_path / "absent-top-level.gitattributes").resolve(),
    )
    monkeypatch.setattr(
        r7s1,
        "CANONICAL_GIT_INFO_ATTRIBUTES_PATH",
        (tmp_path / "absent-info-attributes").resolve(),
    )
    toolchain = _toolchain(tmp_path)
    for role in r7s1.HOST_TOOLCHAIN_ROLES:
        pin = toolchain[role]
        if role == "docker_compose":
            continue
        path = Path(pin["path"])
        path.write_bytes(role.encode())
        pin["bytes"] = path.stat().st_size
        pin["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    kernel = toolchain["windows_tcb"]["kernel"]
    kernel_path = Path(kernel["path"])
    kernel_path.write_bytes(b"kernel")
    kernel["bytes"] = kernel_path.stat().st_size
    kernel["sha256"] = hashlib.sha256(kernel_path.read_bytes()).hexdigest()

    evidence_payloads = {
        "docker_client_config": {
            "schema": toolchain["docker_client_config"]["readback"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            "path": toolchain["docker_client_config"]["path"],
            "sha256": toolchain["docker_client_config"]["sha256"],
            "bytes": toolchain["docker_client_config"]["bytes"],
            "top_level_keys": ["auths", "credsStore", "currentContext"],
            "auth_entries": 0,
            "credential_store_present": True,
            "credential_store_value_exposed": False,
            "current_context": "desktop-linux",
            "context_metadata": copy.deepcopy(
                toolchain["docker_client_config"]["context_metadata"]
            ),
            "endpoint_identity": dict(r7s1.DOCKER_CONTEXT_ENDPOINT_IDENTITY),
            "tls_material_directory_absent": True,
            "policy_sha256": hashlib.sha256(
                r7s1.canonical_json_bytes(r7s1.DOCKER_CLIENT_CONFIG_POLICY)
            ).hexdigest(),
        },
        "python_distribution": {
            "schema": toolchain["python_distribution"]["evidence"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            **{
                key: toolchain["python_distribution"][key]
                for key in (
                    "implementation",
                    "name",
                    "version",
                    "base_prefix",
                    "distribution_tree_sha256",
                    "file_count",
                    "tree_encoding",
                    "included_roots",
                    "excluded_roots",
                )
            },
        },
        "git_distribution": {
            "schema": toolchain["git_distribution"]["evidence"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            **{
                key: toolchain["git_distribution"][key]
                for key in ("root", "distribution_tree_sha256", "file_count", "tree_encoding")
            },
            "volume_identity": "test-volume",
            "filesystem_identity": "test-filesystem",
            "reparse_entries": 0,
        },
        "git_repository_attributes": {
            "schema": toolchain["git_repository_attributes"]["readback"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            "path": toolchain["git_repository_attributes"]["path"],
            "sha256": toolchain["git_repository_attributes"]["sha256"],
            "bytes": toolchain["git_repository_attributes"]["bytes"],
            "rule_count": 20,
            "pattern_sha256": list(r7s1.GIT_ATTRIBUTES_PATTERN_SHA256),
            "attribute_tokens": ["text", "eol=lf"],
            "forbidden_attributes_absent": True,
            "git_top_level_attributes_absent": True,
            "git_info_attributes_absent": True,
            "system_attributes_disabled": True,
            "policy_sha256": hashlib.sha256(
                r7s1.canonical_json_bytes(r7s1.GIT_REPOSITORY_ATTRIBUTES_POLICY)
            ).hexdigest(),
        },
        "git_repository_config": {
            "schema": toolchain["git_repository_config"]["readback"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            "path": toolchain["git_repository_config"]["path"],
            "sha256": toolchain["git_repository_config"]["sha256"],
            "bytes": toolchain["git_repository_config"]["bytes"],
            "key_names": list(r7s1.GIT_CONFIG_ALLOWED_KEY_NAMES),
            "origin_identity": dict(r7s1.GIT_CONFIG_ORIGIN_IDENTITY),
            "config_worktree_absent": True,
            "policy_sha256": hashlib.sha256(
                r7s1.canonical_json_bytes(r7s1.GIT_REPOSITORY_CONFIG_POLICY)
            ).hexdigest(),
        },
        "kubernetes_client_config": {
            "schema": toolchain["kubernetes_client_config"]["readback"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            "path": toolchain["kubernetes_client_config"]["path"],
            "sha256": toolchain["kubernetes_client_config"]["sha256"],
            "bytes": toolchain["kubernetes_client_config"]["bytes"],
            "current_context": "docker-desktop",
            "object_counts": {"contexts": 1, "clusters": 1, "users": 1},
            "context_identity": copy.deepcopy(
                r7s1.KUBERNETES_CLIENT_CONFIG_POLICY["context_identity"]
            ),
            "cluster_identity": copy.deepcopy(
                r7s1.KUBERNETES_CLIENT_CONFIG_POLICY["cluster_identity"]
            ),
            "user_identity": copy.deepcopy(r7s1.KUBERNETES_CLIENT_CONFIG_POLICY["user_identity"]),
            "forbidden_fields_absent": list(
                r7s1.KUBERNETES_CLIENT_CONFIG_POLICY["forbidden_fields_absent"]
            ),
            "multiple_config_merge_forbidden": True,
            "embedded_material_presence": copy.deepcopy(
                r7s1.KUBERNETES_CLIENT_CONFIG_POLICY["embedded_material_presence"]
            ),
            "policy_sha256": hashlib.sha256(
                r7s1.canonical_json_bytes(r7s1.KUBERNETES_CLIENT_CONFIG_POLICY)
            ).hexdigest(),
        },
        "windows_tcb": {
            "schema": toolchain["windows_tcb"]["evidence"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            "build": toolchain["windows_tcb"]["build"],
            "system32_path": toolchain["windows_tcb"]["system32_path"],
            "kernel": dict(kernel),
        },
        "wsl_runtime": {
            "schema": toolchain["wsl_runtime"]["readback"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            **{
                key: toolchain["wsl_runtime"][key]
                for key in ("distro", "kernel_release", "rootfs_identity", "python3")
            },
        },
        "container_psql": {
            "schema": toolchain["container_psql"]["readback"]["schema"],
            "status": "verified",
            "captured_at": "2026-09-01T00:00:00Z",
            **{
                key: toolchain["container_psql"][key]
                for key in (
                    "container_name",
                    "image_digest",
                    "realpath",
                    "sha256",
                    "bytes",
                    "version",
                    "execution_scope",
                )
            },
        },
    }
    for label, payload in evidence_payloads.items():
        pin_key = (
            "evidence"
            if label
            in {
                "python_distribution",
                "git_distribution",
                "windows_tcb",
            }
            else "readback"
        )
        pin = _write_json(Path(toolchain[label][pin_key]["path"]), payload)
        pin["schema"] = payload["schema"]
        toolchain[label][pin_key] = pin
    return toolchain


def _manifest(tmp_path: Path) -> dict[str, object]:
    value = r7_manifest()
    value["schema_version"] = r7s1.SCHEMA_VERSION
    value["work_order_id"] = r7s1.WORK_ORDER_ID
    value["bundle_id"] = RUN_ID
    value["created_at"] = "2026-09-01T00:02:30Z"
    value["bundle"] = {"path": BINDING["staging_path"]}
    for component in value["runtime"].values():
        legacy_blob_oid = component.pop("blob_oid")
        component["worktree_blob_oid"] = legacy_blob_oid
        component["head_blob_oid"] = legacy_blob_oid
    value["process_containment"] = copy.deepcopy(r7s1.PROCESS_CONTAINMENT_CONTRACT)
    value["output"] = {
        "path": BINDING["output_path"],
        "must_not_exist_before_runner": True,
        "write_mode": "create-exclusive",
    }
    parents = value["parent_checkpoints"]
    assert isinstance(parents, list)
    for parent in parents:
        parent["schema"] = r7s1.PARENT_CHECKPOINT_SCHEMAS[parent["role"]]
        parent["run_id"] = f"{parent['role']}-run"
    for role in r7s1.PARENT_CHECKPOINT_ROLES[len(r7.PARENT_CHECKPOINT_ROLES) :]:
        parents.append(
            {
                "role": role,
                "kind": role,
                "path": str(tmp_path / "parents" / f"{role}.json"),
                "sha256": SHA,
                "immutable": True,
                "must_not_execute": True,
                "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS[role],
                "run_id": f"{role}-run",
            }
        )
    expected_state = value["expected_state"]
    assert isinstance(expected_state, dict)
    expected_state["api"]["base_url"] = r7s1.EXPECTED_API_BASE_URL
    expected_state["api_base_url"] = r7s1.EXPECTED_API_BASE_URL
    expected_state["b0"] = dict(r7s1.EXPECTED_B0)
    expected_state["prometheus_jobs"] = list(r7s1.PROMETHEUS_JOBS)
    expected_state["prometheus_targets_url"] = r7s1.EXPECTED_PROMETHEUS_TARGETS_URL
    expected_state["gpu_lease_path"] = r7s1.EXPECTED_GPU_LEASE_PATH
    expected_state["active_job_roots"] = []
    expected_state["active_claim_roots"] = []
    expected_state["x1_residue_paths"] = list(r7s1.EXPECTED_X1_RESIDUE_PATHS)
    kubernetes = expected_state["kubernetes"]
    assert isinstance(kubernetes, dict)
    kubernetes["allowed_historical_failed_pods"] = _typed_pods()
    job_scope = value["job_scope_contract"]
    assert isinstance(job_scope, dict)
    classifications = job_scope["historical_classifications"]
    assert isinstance(classifications, list)
    classifications[2].update(
        {
            "observed_count": 14,
            "executing_count": 0,
            "historical_count": 14,
            "unproven_count": 0,
            "classification": "historical_nonexecuting",
        }
    )
    normalized_parents = r7s1._validate_parent_entries(parents)
    binding = {**BINDING, "parent_map_sha256": r7s1.parent_map_sha256(normalized_parents)}
    value["external_terminal_fencing"] = {
        "target_source": "mlflow_running_rows",
        "target_identity": dict(TARGET),
        "successor_binding": binding,
        "decision_authority": r7s1.EXTERNAL_DECISION_AUTHORITY,
        "snapshots": [
            {
                "path": str(tmp_path / "snapshot-1.json"),
                "sha256": SHA,
                "schema": r7s1.SNAPSHOT_SCHEMA,
                "captured_at": "2026-09-01T00:00:00Z",
                "ordinal": 1,
                "source_revision": r7s1.OBSERVATION_SOURCE_REVISION,
            },
            {
                "path": str(tmp_path / "snapshot-2.json"),
                "sha256": SHA,
                "schema": r7s1.SNAPSHOT_SCHEMA,
                "captured_at": "2026-09-01T00:01:00Z",
                "ordinal": 2,
                "source_revision": r7s1.OBSERVATION_SOURCE_REVISION,
            },
        ],
        "exact_link_scans": [
            {
                "path": str(tmp_path / "scan-1.json"),
                "sha256": SHA,
                "schema": r7s1.LINK_SCAN_SCHEMA,
                "captured_at": "2026-09-01T00:00:10Z",
                "ordinal": 1,
                "source_revision": r7s1.OBSERVATION_SOURCE_REVISION,
            },
            {
                "path": str(tmp_path / "scan-2.json"),
                "sha256": SHA,
                "schema": r7s1.LINK_SCAN_SCHEMA,
                "captured_at": "2026-09-01T00:01:10Z",
                "ordinal": 2,
                "source_revision": r7s1.OBSERVATION_SOURCE_REVISION,
            },
        ],
        "terminal_decision": None,
        "trusted_checkpoint": None,
    }
    value["toolchain"] = _toolchain(tmp_path)
    return value


def test_successor_manifest_is_distinct_exact_ten_parent_and_fail_closed(tmp_path: Path) -> None:
    validated = r7s1.validate_r7s1_manifest(
        _manifest(tmp_path),
        expected_revision="a" * 40,
        verify_attestations=False,
        expected_trusted_checkpoint_sha256=None,
    )
    assert validated["schema_version"] == r7s1.SCHEMA_VERSION
    assert tuple(validated["parents"]) == r7s1.PARENT_CHECKPOINT_ROLES
    assert len(validated["typed_historical_failed_pods"]) == 14
    assert validated["historical_decisions"][0]["decision"] == "unproven"
    assert validated["historical_go"] is False


def test_running_empty_end_time_with_two_zero_link_scans_remains_unproven(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=False)
    result = _validate_external(contract)
    assert result["identity"]["status"] == "RUNNING"
    assert result["identity"]["end_time"] == ""
    assert result["decision"] == "unproven"
    assert result["verified"] is False


def test_exact_external_terminal_fencing_decision_releases_only_exact_identity(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=True)
    result = _validate_external(contract)
    assert result["verified"] is True
    assert result["decision"] == "proven_terminal_fenced"
    validated = {"historical_decisions": [result]}
    assert r7s1.find_verified_decision(validated, "mlflow_running_rows", TARGET) == result
    other = {**TARGET, "run_id": "different"}
    assert r7s1.find_verified_decision(validated, "mlflow_running_rows", other) is None


def test_terminal_decision_without_out_of_band_checkpoint_sha_remains_unproven(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=True)
    result = _validate_external(contract, trust_checkpoint=False)
    assert result["verified"] is False
    assert result["decision"] == "unproven"
    with pytest.raises(r7s1.R7S1ContractError, match="out_of_band_sha"):
        r7s1.validate_external_terminal_fencing(
            contract,
            verify_files=True,
            expected_trusted_checkpoint_sha256="0" * 64,
            validation_time=datetime(2026, 9, 1, 0, 3, tzinfo=UTC),
        )


def test_successor_binding_replay_is_rejected_even_with_same_observation_sha(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=True)
    checkpoint = contract["trusted_checkpoint"]
    other_run_id = "different-r7s1-successor"
    expected_binding = {
        **BINDING,
        "run_id": other_run_id,
        "staging_path": str((r7s1.CANONICAL_STAGING_ROOT / other_run_id).resolve()),
        "output_path": str((r7s1.CANONICAL_OUTPUT_ROOT / other_run_id).resolve()),
        "emergency_seal_path": str(
            (r7s1.CANONICAL_OUTPUT_ROOT / f"{other_run_id}-emergency-seal").resolve()
        ),
    }
    with pytest.raises(r7s1.R7S1ContractError, match="successor_binding_mismatch"):
        r7s1.validate_external_terminal_fencing(
            contract,
            verify_files=True,
            expected_trusted_checkpoint_sha256=checkpoint["sha256"],
            expected_successor_binding=expected_binding,
            validation_time=datetime(2026, 9, 1, 0, 3, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("attempt_id", "not-a-uuid", "canonical_uuid"),
        (
            "staging_path",
            str(Path("F:/alternate/r7s1/x1-phase-b2-r7s1-test-restore").resolve()),
            "canonical_staging_path",
        ),
        (
            "output_path",
            str(Path("F:/alternate/r7s1/x1-phase-b2-r7s1-test-restore").resolve()),
            "canonical_output_path",
        ),
        (
            "staging_path",
            str((r7s1.CANONICAL_STAGING_ROOT / "wrong-r7s1-leaf").resolve()),
            "canonical_staging_path",
        ),
        (
            "output_path",
            str((r7s1.CANONICAL_OUTPUT_ROOT / "wrong-r7s1-leaf").resolve()),
            "canonical_output_path",
        ),
        (
            "staging_path",
            str(r7s1.CANONICAL_STAGING_ROOT / RUN_ID / ".." / RUN_ID),
            "absolute_normalized_path",
        ),
        (
            "emergency_seal_path",
            str((r7s1.CANONICAL_OUTPUT_ROOT / "alternate-r7s1-emergency-seal").resolve()),
            "canonical_emergency_seal_path",
        ),
    ],
)
def test_successor_attempt_and_canonical_paths_are_exact(
    field: str, value: str, match: str
) -> None:
    mutated = {**BINDING, field: value}
    with pytest.raises(r7s1.R7S1ContractError, match=match):
        r7s1._successor_binding(mutated, "test_successor")

    missing = dict(BINDING)
    missing.pop("attempt_id")
    with pytest.raises(r7s1.R7S1ContractError, match="fields_mismatch"):
        r7s1._successor_binding(missing, "test_successor")


def test_manifest_paths_are_exact_projection_of_successor_binding(tmp_path: Path) -> None:
    for field, value in (
        ("bundle", {"path": str((r7s1.CANONICAL_STAGING_ROOT / "alternate-r7s1").resolve())}),
        (
            "output",
            {
                "path": str((r7s1.CANONICAL_OUTPUT_ROOT / "alternate-r7s1").resolve()),
                "must_not_exist_before_runner": True,
                "write_mode": "create-exclusive",
            },
        ),
    ):
        manifest = _manifest(tmp_path)
        manifest[field] = value
        with pytest.raises(r7s1.R7S1ContractError, match="canonical_.*_path|successor_binding"):
            r7s1.validate_r7s1_manifest(
                manifest,
                expected_revision="a" * 40,
                verify_attestations=False,
                expected_trusted_checkpoint_sha256=None,
            )


def test_self_consistent_successor_repin_still_requires_original_out_of_band_checkpoint(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=True)
    original_checkpoint_sha = contract["trusted_checkpoint"]["sha256"]
    mutated_binding = {**BINDING, "attempt_id": "4545d8b6-8c56-4688-92e0-1411bf99a528"}
    mutated_binding_sha = hashlib.sha256(r7s1.canonical_json_bytes(mutated_binding)).hexdigest()
    contract["successor_binding"] = dict(mutated_binding)

    decision_path = Path(contract["terminal_decision"]["path"])
    decision = json.loads(decision_path.read_text())
    decision["successor_binding"] = dict(mutated_binding)
    decision["supporting_sha256"]["successor_binding_sha256"] = mutated_binding_sha
    decision_pin = _write_json(decision_path, decision)
    decision_pin["schema"] = r7s1.TERMINAL_FENCING_DECISION_SCHEMA
    contract["terminal_decision"] = decision_pin

    checkpoint_path = Path(contract["trusted_checkpoint"]["path"])
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["successor_binding"] = dict(mutated_binding)
    checkpoint["supporting_sha256"]["successor_binding_sha256"] = mutated_binding_sha
    checkpoint["decision_sha256"] = decision_pin["sha256"]
    checkpoint_pin = _write_json(checkpoint_path, checkpoint)
    checkpoint_pin["schema"] = r7s1.TRUSTED_CHECKPOINT_SCHEMA
    contract["trusted_checkpoint"] = checkpoint_pin

    with pytest.raises(r7s1.R7S1ContractError, match="out_of_band_sha"):
        r7s1.validate_external_terminal_fencing(
            contract,
            verify_files=True,
            expected_trusted_checkpoint_sha256=original_checkpoint_sha,
            expected_successor_binding=mutated_binding,
            validation_time=datetime(2026, 9, 1, 0, 3, tzinfo=UTC),
        )


def test_source_pin_metadata_self_consistent_manifest_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=False)
    contract["snapshots"][0]["captured_at"] = "2026-09-01T00:00:01Z"
    with pytest.raises(r7s1.R7S1ContractError, match="pin_metadata_readback"):
        _validate_external(contract)


def test_snapshot_target_activity_change_is_not_terminal_inactivity_evidence(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=False)
    path = Path(contract["snapshots"][1]["path"])
    payload = json.loads(path.read_text())
    payload["observed"]["mlflow_activity"][0]["metric_count"] += 1
    contract["snapshots"][1] = _write_source(path, payload)
    with pytest.raises(r7s1.R7S1ContractError, match="target_activity_changed"):
        _validate_external(contract)


def test_observation_command_argv_and_derived_identity_coverage_are_immutable(
    tmp_path: Path,
) -> None:
    contract = _external_contract(tmp_path, with_decision=False)
    path = Path(contract["snapshots"][0]["path"])
    payload = json.loads(path.read_text())
    payload["commands"][0]["command"][-1] = "mutating-command"
    contract["snapshots"][0] = _write_source(path, payload)
    with pytest.raises(r7s1.R7S1ContractError, match="command_argv_mismatch"):
        _validate_external(contract)

    contract = _external_contract(tmp_path, with_decision=False)
    path = Path(contract["snapshots"][0]["path"])
    payload = json.loads(path.read_text())
    payload["commands"][0]["accounting"][0]["total_processes"] = 2
    payload["commands"][0]["accounting"][1]["total_processes"] = 2
    contract["snapshots"][0] = _write_source(path, payload)
    with pytest.raises(r7s1.R7S1ContractError, match="identity_coverage_not_derived"):
        _validate_external(contract)


def test_source_revision_split_and_future_checkpoint_are_rejected(tmp_path: Path) -> None:
    contract = _external_contract(tmp_path, with_decision=False)
    scan = _scan(2, "2026-09-01T00:02:00Z")
    scan["source_revision"] = "b" * 40
    contract["exact_link_scans"][1] = _write_source(tmp_path / "scan-2.json", scan)
    with pytest.raises(r7s1.R7S1ContractError, match="source_revision_mismatch"):
        _validate_external(contract)

    contract = _external_contract(tmp_path, with_decision=True)
    checkpoint_path = Path(contract["trusted_checkpoint"]["path"])
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["checkpointed_at"] = "2026-09-01T00:04:00Z"
    contract["trusted_checkpoint"] = _write_json(checkpoint_path, checkpoint)
    contract["trusted_checkpoint"]["schema"] = r7s1.TRUSTED_CHECKPOINT_SCHEMA
    with pytest.raises(r7s1.R7S1ContractError, match="checkpoint_from_future"):
        _validate_external(contract)


def test_runtime_gap_and_freshness_are_fail_closed(tmp_path: Path) -> None:
    contract = _external_contract(tmp_path, with_decision=False)
    second = _snapshot(2, "2026-09-01T00:00:20Z")
    contract["snapshots"][1] = _write_source(tmp_path / "snapshot-2.json", second)
    with pytest.raises(r7s1.R7S1ContractError, match="minimum_gap"):
        _validate_external(contract)
    contract = _external_contract(tmp_path, with_decision=False)
    with pytest.raises(r7s1.R7S1ContractError, match="runtime_max_age"):
        r7s1.validate_external_terminal_fencing(
            contract,
            verify_files=True,
            expected_trusted_checkpoint_sha256=None,
            validation_time=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("decision", "proven_inactive", "exact_value"),
        ("decision_authority", "self-review", "authority"),
        ("future_dispatch_fenced", False, "future_dispatch_fence"),
        ("issued_at", "2026-09-01T00:00:30Z", "must_follow_all_observations"),
    ],
)
def test_external_decision_boundary_mutations_fail_closed(
    tmp_path: Path, field: str, value: object, match: str
) -> None:
    contract = _external_contract(tmp_path, with_decision=True, decision_mutation=(field, value))
    with pytest.raises(r7s1.R7S1ContractError, match=match):
        _validate_external(contract)


def test_snapshot_replay_or_ordinal_swap_is_rejected(tmp_path: Path) -> None:
    contract = _external_contract(tmp_path, with_decision=False)
    contract["snapshots"][1] = copy.deepcopy(contract["snapshots"][0])
    with pytest.raises(r7s1.R7S1ContractError, match="schema_or_ordinal|paths_must_be_distinct"):
        _validate_external(contract)


def test_decision_support_chain_mutation_is_rejected(tmp_path: Path) -> None:
    contract = _external_contract(tmp_path, with_decision=True)
    decision_path = Path(contract["terminal_decision"]["path"])
    decision = json.loads(decision_path.read_text())
    decision["supporting_sha256"]["historical_snapshot_1"] = "0" * 64
    contract["terminal_decision"] = _write_json(decision_path, decision)
    contract["terminal_decision"]["schema"] = r7s1.TERMINAL_FENCING_DECISION_SCHEMA
    with pytest.raises(r7s1.R7S1ContractError, match="support_chain"):
        _validate_external(contract)


def test_typed_failed_pod_reason_source_and_exact_count_are_immutable(tmp_path: Path) -> None:
    value = _manifest(tmp_path)
    pods = value["expected_state"]["kubernetes"]["allowed_historical_failed_pods"]
    pods[11]["reason_source"] = "pod.status.reason"
    with pytest.raises(r7s1.R7S1ContractError, match="b0_reason_contract"):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda state: state["b0"].update(uid=str(uuid.uuid4())), "b0_identity_or_endpoint"),
        (lambda state: state["b0"].update(image="other@sha256:" + "0" * 64), "b0_identity"),
        (lambda state: state["b0"].update(ready_url="http://127.0.0.1:9/ready"), "b0_identity"),
        (
            lambda state: state["b0"].update(predict_url="http://127.0.0.1:9/predict"),
            "b0_identity",
        ),
        (lambda state: state["b0"].update(sample_image_uri="/tmp/replayed.jpg"), "b0_identity"),
        (lambda state: state["b0"].pop("uid_basis"), "b0_fields"),
        (
            lambda state: (
                state["api"].update(base_url="http://127.0.0.1:9"),
                state.update(api_base_url="http://127.0.0.1:9"),
            ),
            "api_base_url_mismatch",
        ),
        (
            lambda state: state.update(prometheus_jobs=[*reversed(state["prometheus_jobs"])]),
            "prometheus_jobs_mismatch",
        ),
        (
            lambda state: state.update(prometheus_targets_url="http://127.0.0.1:9/api/v1/targets"),
            "prometheus_targets_url_mismatch",
        ),
        (lambda state: state.update(gpu_lease_path="F:/alternate/active.json"), "gpu_lease"),
        (lambda state: state.update(active_job_roots=["F:/alternate"]), "active_job_roots"),
        (lambda state: state.update(active_claim_roots=["F:/alternate"]), "active_claim_roots"),
        (
            lambda state: state.update(x1_residue_paths=[*reversed(state["x1_residue_paths"])]),
            "x1_residue_paths_mismatch",
        ),
    ],
)
def test_expected_state_self_consistent_endpoint_and_identity_repins_are_rejected(
    tmp_path: Path, mutation: object, match: str
) -> None:
    value = _manifest(tmp_path)
    mutation(value["expected_state"])  # type: ignore[operator]
    with pytest.raises(r7s1.R7S1ContractError, match=match):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )


def test_toolchain_unknown_signature_and_wsl_python_unknown_are_rejected(
    tmp_path: Path,
) -> None:
    value = _manifest(tmp_path)
    value["toolchain"]["git"]["signature"]["status"] = "unknown"
    with pytest.raises(r7s1.R7S1ContractError, match="valid_signature_required"):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )
    value = _manifest(tmp_path)
    value["toolchain"]["wsl_runtime"]["python3"]["version"] = ""
    with pytest.raises(r7s1.R7S1ContractError, match="version_nonempty_required"):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )


def test_docker_compose_is_a_direct_exact_host_toolchain_role(tmp_path: Path) -> None:
    value = _manifest(tmp_path)
    assert "docker_compose" in r7s1.HOST_TOOLCHAIN_ROLES
    assert value["toolchain"]["docker_compose"]["path"] == str(r7s1.DOCKER_COMPOSE_EXECUTABLE)

    missing = _manifest(tmp_path)
    missing["toolchain"].pop("docker_compose")
    with pytest.raises(r7s1.R7S1ContractError, match="toolchain_role_set_mismatch"):
        r7s1.validate_toolchain_contract(missing["toolchain"], verify_files=False)

    repinned = _manifest(tmp_path)
    repinned["toolchain"]["docker_compose"]["path"] = str(
        (tmp_path / "docker-compose.exe").resolve()
    )
    with pytest.raises(r7s1.R7S1ContractError, match="docker_compose_path_mismatch"):
        r7s1.validate_toolchain_contract(repinned["toolchain"], verify_files=False)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda config: config.update(path=str(Path("C:/alternate/.git/config").resolve())),
            "git_repository_config_path_mismatch",
        ),
        (
            lambda config: config.update(sha256="0" * 64),
            "git_repository_config_sha256_mismatch",
        ),
        (
            lambda config: config.update(bytes=r7s1.CANONICAL_GIT_CONFIG_BYTES + 1),
            "git_repository_config_bytes_mismatch",
        ),
        (
            lambda config: config["policy"]["allowed_key_names"].append("include.path"),
            "git_repository_config_policy_mismatch",
        ),
        (
            lambda config: config["policy"].update(config_worktree_absent=False),
            "git_repository_config_policy_mismatch",
        ),
        (
            lambda config: config["policy"]["origin_identity"].update(host="alternate.invalid"),
            "git_repository_config_policy_mismatch",
        ),
    ],
)
def test_git_repository_config_pin_and_safe_policy_mutations_are_rejected(
    tmp_path: Path, mutation: object, match: str
) -> None:
    value = _manifest(tmp_path)
    mutation(value["toolchain"]["git_repository_config"])  # type: ignore[index,operator]
    with pytest.raises(r7s1.R7S1ContractError, match=match):
        r7s1.validate_toolchain_contract(value["toolchain"], verify_files=False)


def test_git_repository_config_role_is_mandatory(tmp_path: Path) -> None:
    value = _manifest(tmp_path)
    value["toolchain"].pop("git_repository_config")  # type: ignore[union-attr]
    with pytest.raises(r7s1.R7S1ContractError, match="toolchain_role_set_mismatch"):
        r7s1.validate_toolchain_contract(value["toolchain"], verify_files=False)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda attributes: attributes.update(sha256="0" * 64),
            "git_repository_attributes_sha256_mismatch",
        ),
        (
            lambda attributes: attributes.update(bytes=r7s1.CANONICAL_GIT_ATTRIBUTES_BYTES + 1),
            "git_repository_attributes_bytes_mismatch",
        ),
        (
            lambda attributes: attributes["policy"].update(rule_count=21),
            "git_repository_attributes_policy_mismatch",
        ),
        (
            lambda attributes: attributes["policy"]["attribute_tokens"].append("filter=driver"),
            "git_repository_attributes_policy_mismatch",
        ),
        (
            lambda attributes: attributes["policy"].update(git_info_attributes_absent=False),
            "git_repository_attributes_policy_mismatch",
        ),
    ],
)
def test_git_repository_attributes_pin_and_safe_policy_mutations_are_rejected(
    tmp_path: Path, mutation: object, match: str
) -> None:
    value = _manifest(tmp_path)
    mutation(value["toolchain"]["git_repository_attributes"])  # type: ignore[index,operator]
    with pytest.raises(r7s1.R7S1ContractError, match=match):
        r7s1.validate_toolchain_contract(value["toolchain"], verify_files=False)


def test_git_repository_attributes_role_is_mandatory(tmp_path: Path) -> None:
    value = _manifest(tmp_path)
    value["toolchain"].pop("git_repository_attributes")  # type: ignore[union-attr]
    with pytest.raises(r7s1.R7S1ContractError, match="toolchain_role_set_mismatch"):
        r7s1.validate_toolchain_contract(value["toolchain"], verify_files=False)


@pytest.mark.parametrize(
    ("role", "mutation", "match"),
    [
        (
            "docker_client_config",
            lambda config: config.update(sha256="0" * 64),
            "docker_client_config_sha256_mismatch",
        ),
        (
            "docker_client_config",
            lambda config: config["context_metadata"].update(sha256="0" * 64),
            "docker_context_metadata_identity_mismatch",
        ),
        (
            "docker_client_config",
            lambda config: config["policy"].update(registry_operations_allowed=True),
            "docker_client_config_policy_mismatch",
        ),
        (
            "docker_client_config",
            lambda config: config["policy"].update(tls_material_directory_absent=False),
            "docker_client_config_policy_mismatch",
        ),
        (
            "kubernetes_client_config",
            lambda config: config.update(sha256="0" * 64),
            "kubernetes_client_config_sha256_mismatch",
        ),
        (
            "kubernetes_client_config",
            lambda config: config["policy"].update(current_context="alternate"),
            "kubernetes_client_config_policy_mismatch",
        ),
        (
            "kubernetes_client_config",
            lambda config: config["policy"]["forbidden_fields_absent"].remove("exec"),
            "kubernetes_client_config_policy_mismatch",
        ),
        (
            "kubernetes_client_config",
            lambda config: config["policy"]["embedded_material_presence"].update(
                serialized_values=True
            ),
            "kubernetes_client_config_policy_mismatch",
        ),
    ],
)
def test_client_config_pin_policy_and_secret_boundary_mutations_are_rejected(
    tmp_path: Path, role: str, mutation: object, match: str
) -> None:
    value = _manifest(tmp_path)
    mutation(value["toolchain"][role])  # type: ignore[index,operator]
    with pytest.raises(r7s1.R7S1ContractError, match=match):
        r7s1.validate_toolchain_contract(value["toolchain"], verify_files=False)


@pytest.mark.parametrize("role", ["docker_client_config", "kubernetes_client_config"])
def test_client_config_roles_are_mandatory(tmp_path: Path, role: str) -> None:
    value = _manifest(tmp_path)
    value["toolchain"].pop(role)  # type: ignore[union-attr]
    with pytest.raises(r7s1.R7S1ContractError, match="toolchain_role_set_mismatch"):
        r7s1.validate_toolchain_contract(value["toolchain"], verify_files=False)


def test_git_attributes_policy_binds_exact_twenty_rule_projection() -> None:
    assert r7s1.CANONICAL_GIT_ATTRIBUTES_BYTES == 873
    assert r7s1.CANONICAL_GIT_ATTRIBUTES_SHA256 == (
        "b88aa1f439520fb303392a13f0a0a07642c8a5449bd7c409597ebd791f6d4c28"
    )
    assert len(r7s1.GIT_ATTRIBUTES_PATTERN_SHA256) == 20
    assert r7s1.GIT_REPOSITORY_ATTRIBUTES_POLICY["rule_count"] == 20
    assert (
        hashlib.sha256(r7s1.canonical_json_bytes(r7s1.GIT_REPOSITORY_ATTRIBUTES_POLICY)).hexdigest()
        == "d55970cd3e48ec400efcd4ac07930763128f87c65a5d104b40e032530a56420c"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda scopes: scopes["windows"].update(wsl_linux_descendants_job_accounted=True),
        lambda scopes: scopes["windows"].update(container_linux_descendants_job_accounted=True),
        lambda scopes: scopes["wsl"].update(linux_descendants_job_accounted=True),
        lambda scopes: scopes["wsl"].update(scope="windows_job_object"),
        lambda scopes: scopes["docker_container_exec"].update(
            linux_container_descendants_job_accounted=True
        ),
    ],
)
def test_process_containment_domain_scope_mutations_are_rejected(
    tmp_path: Path, mutation: object
) -> None:
    value = _manifest(tmp_path)
    mutation(value["process_containment"]["scope_boundaries"])  # type: ignore[index,operator]
    with pytest.raises(r7s1.R7S1ContractError, match="process_containment_contract_mismatch"):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )


@pytest.mark.parametrize(
    ("field", "mutated"),
    [
        ("linux_container_descendants_job_accounted", True),
        ("command_policy", "exact_read_only_psql_select_allowlist"),
    ],
)
def test_container_psql_toolchain_rejects_scope_or_psqlrc_policy_weakening(
    tmp_path: Path, field: str, mutated: object
) -> None:
    value = _manifest(tmp_path)
    value["toolchain"]["container_psql"]["execution_scope"][field] = mutated  # type: ignore[index]
    with pytest.raises(r7s1.R7S1ContractError, match="container_psql_execution_scope_mismatch"):
        r7s1.validate_toolchain_contract(value["toolchain"], verify_files=False)


@pytest.mark.parametrize(
    ("label", "field", "value"),
    [
        (
            "docker_client_config",
            "credential_store_value_exposed",
            True,
        ),
        ("python_distribution", "distribution_tree_sha256", "0" * 64),
        (
            "git_repository_config",
            "key_names",
            [*r7s1.GIT_CONFIG_ALLOWED_KEY_NAMES, "credential.helper"],
        ),
        (
            "git_repository_attributes",
            "forbidden_attributes_absent",
            False,
        ),
        (
            "kubernetes_client_config",
            "forbidden_fields_absent",
            ["exec"],
        ),
        ("windows_tcb", "build", "mutated-build"),
        ("wsl_runtime", "kernel_release", "mutated-kernel"),
        ("container_psql", "version", "mutated-psql"),
        (
            "container_psql",
            "execution_scope",
            {
                **r7s1.DOCKER_CONTAINER_EXECUTION_SCOPE,
                "linux_container_descendants_job_accounted": True,
            },
        ),
    ],
)
def test_toolchain_readbacks_are_exactly_bound_to_manifest_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    field: str,
    value: object,
) -> None:
    toolchain = _materialized_toolchain(tmp_path, monkeypatch)
    r7s1.validate_toolchain_contract(toolchain, verify_files=True)
    pin_key = (
        "evidence"
        if label
        in {
            "python_distribution",
            "git_distribution",
            "windows_tcb",
        }
        else "readback"
    )
    pin = toolchain[label][pin_key]
    path = Path(pin["path"])
    payload = json.loads(path.read_text())
    payload[field] = value
    replacement = _write_json(path, payload)
    replacement["schema"] = pin["schema"]
    toolchain[label][pin_key] = replacement
    with pytest.raises(r7s1.R7S1ContractError, match="projection_mismatch"):
        r7s1.validate_toolchain_contract(toolchain, verify_files=True)


def test_parent_role_removal_and_reordering_are_rejected(tmp_path: Path) -> None:
    value = _manifest(tmp_path)
    value["parent_checkpoints"].pop()
    with pytest.raises(r7s1.R7S1ContractError, match="count_mismatch"):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )
    value = _manifest(tmp_path)
    value["parent_checkpoints"][8], value["parent_checkpoints"][9] = (
        value["parent_checkpoints"][9],
        value["parent_checkpoints"][8],
    )
    with pytest.raises(r7s1.R7S1ContractError, match="order_mismatch"):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )


def _sealed_parent_chain() -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    r5_run = "x1-r5-failed"
    r6_run = "x1-r6-failed"
    r7_run = "x1-r7-failed"
    run_ids = {
        **{role: r5_run for role in r7s1.PARENT_CHECKPOINT_ROLES[:2]},
        **{role: r6_run for role in r7s1.PARENT_CHECKPOINT_ROLES[2:7]},
        **{role: r7_run for role in r7s1.PARENT_CHECKPOINT_ROLES[7:]},
    }
    entries = [
        {
            "role": role,
            "kind": role,
            "path": str(
                Path(
                    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/"
                    "scale_validation/private/s8-v4/test-r7s1-parents"
                )
                / f"{index:02d}-{role}.json"
            ),
            "sha256": f"{index + 1:064x}",
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS[role],
            "run_id": run_ids[role],
            "immutable": True,
            "must_not_execute": True,
        }
        for index, role in enumerate(r7s1.PARENT_CHECKPOINT_ROLES)
    ]
    pins = {entry["role"]: entry for entry in entries}
    payloads: dict[str, dict[str, object]] = {
        "r5_failure_seal": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r5_failure_seal"],
            "failure_only": True,
            "acceptance_credit": False,
            "decision": "manual_intervention_required",
            "success_marker_created": False,
            "report": {
                "run_id": r5_run,
                "passed": False,
                "overall_pass": False,
                "phase_b2_executed": False,
                "completion_marker_created": False,
                "call_counts": dict(r7s1.RESTORE_LIFECYCLE_COUNTS),
            },
        },
        "r5_failure_index": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r5_failure_index"],
            "failure_only": True,
            "acceptance_credit": False,
            "is_success_index": False,
            "completion_marker_created": False,
            "files": [
                {
                    "path": "failure-seal.json",
                    "sha256": pins["r5_failure_seal"]["sha256"],
                    "bytes": 1,
                }
            ],
        },
        "r6_compose_rca": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r6_compose_rca"],
            "run_identity": r6_run,
            "decision": "manual_intervention_required",
            "credit": "zero_credit",
            "go": False,
            "completion_marker_created": False,
            "r6_restore_only": {
                "bundle_created": False,
                "executed": False,
                "outer_calls": 0,
                "bridge_calls": 0,
                "runner_calls": 0,
                "retries": 0,
            },
        },
        "r6_failure_seal_amendment": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r6_failure_seal_amendment"],
            "base_rca_sha256": pins["r6_compose_rca"]["sha256"],
            "decision": "manual_intervention_required",
            "result": "no_go",
            "credit": "zero_credit",
            "r6_restore_only_executed": False,
            "completion_marker_created": False,
        },
        "r6_final_index": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r6_final_index"],
            "decision": "manual_intervention_required",
            "completion_marker": None,
            "seal_amendment": {"sha256": pins["r6_failure_seal_amendment"]["sha256"]},
        },
        "post_manual_on_readback": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["post_manual_on_readback"],
            "decision": "manual_intervention_required",
            "result": "no_go",
            "r6_restore_only_calls": 0,
            "completion_marker_created": False,
        },
        "post_manual_on_index": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["post_manual_on_index"],
            "decision": "manual_intervention_required",
            "completion_marker": None,
            "previous_index": {"sha256": pins["r6_final_index"]["sha256"]},
            "final_runtime_readback": {"sha256": pins["post_manual_on_readback"]["sha256"]},
        },
        "r7_failure_seal": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r7_failure_seal"],
            "run_identity": r7_run,
            "decision": "NO-GO",
            "credit": "zero-credit",
            "manual_intervention_required": True,
            "completion_marker_created": False,
            "seal_is_final_commit_record": True,
            "pinned_evidence": {"failure_index_sha256": pins["r7_failure_index"]["sha256"]},
        },
        "r7_failure_index": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r7_failure_index"],
            "run_identity": r7_run,
            "decision": "NO-GO",
            "credit": "zero-credit",
            "manual_intervention_required": True,
            "completion_marker_present": False,
            "success_private_index_present": False,
            "failure_seal_expected_last": True,
        },
        "r7_post_seal_residual_amendment": {
            "schema": r7s1.PARENT_CHECKPOINT_SCHEMAS["r7_post_seal_residual_amendment"],
            "parent_failure_seal_sha256": pins["r7_failure_seal"]["sha256"],
            "decision": "manual_intervention_required",
            "additional_automatic_work_authorized": False,
        },
    }
    return entries, payloads


def test_actual_parent_chain_direction_and_inherited_run_identity_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries, payloads = _sealed_parent_chain()
    by_path = {Path(str(entry["path"])).resolve(): entry for entry in entries}
    monkeypatch.setattr(Path, "is_file", lambda self: self.resolve() in by_path)
    monkeypatch.setattr(
        r7s1,
        "_read_json_snapshot",
        lambda path: (
            payloads[str(by_path[path.resolve()]["role"])],
            str(by_path[path.resolve()]["sha256"]),
        ),
    )
    _, checkpoint = r7s1.read_parent_checkpoints(entries)
    assert checkpoint.previous_attempt_failed is True

    payloads["r7_failure_seal"]["pinned_evidence"] = {"failure_index_sha256": SHA}
    with pytest.raises(r7s1.R7S1ContractError, match="r7_failure_seal_chain"):
        r7s1.read_parent_checkpoints(entries)

    entries, payloads = _sealed_parent_chain()
    by_path = {Path(str(entry["path"])).resolve(): entry for entry in entries}
    entries[5]["run_id"] = "self-consistently-mutated-r6-run"
    with pytest.raises(r7s1.R7S1ContractError, match="parent_checkpoint_run_id_mismatch"):
        r7s1.read_parent_checkpoints(entries)


def test_parent_schema_role_swap_is_rejected_before_readback(tmp_path: Path) -> None:
    value = _manifest(tmp_path)
    first = value["parent_checkpoints"][0]
    second = value["parent_checkpoints"][1]
    first["schema"], second["schema"] = second["schema"], first["schema"]
    with pytest.raises(r7s1.R7S1ContractError, match="parent_checkpoint_schema_mismatch"):
        r7s1.validate_r7s1_manifest(
            value,
            expected_revision="a" * 40,
            verify_attestations=False,
            expected_trusted_checkpoint_sha256=None,
        )


def _local_successor_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    staging_root = (tmp_path / "canonical-staging").resolve()
    output_root = (tmp_path / "canonical-output").resolve()
    monkeypatch.setattr(r7s1, "CANONICAL_STAGING_ROOT", staging_root)
    monkeypatch.setattr(r7s1, "CANONICAL_OUTPUT_ROOT", output_root)
    run_id = "x1-phase-b2-r7s1-emergency-test"
    return {
        "run_id": run_id,
        "attempt_id": "a6ed2ff0-2e5f-4b03-b0e4-12f8b04ba97d",
        "commit": "1" * 40,
        "tree": "2" * 40,
        "nonce": "3" * 64,
        "parent_map_sha256": "4" * 64,
        "staging_path": str((staging_root / run_id).resolve()),
        "output_path": str((output_root / run_id).resolve()),
        "emergency_seal_path": str((output_root / f"{run_id}-emergency-seal").resolve()),
    }


def _local_emergency_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], r7s1.EvidenceWriter, dict[str, str]]:
    binding = _local_successor_binding(tmp_path, monkeypatch)
    manifest_path = Path(binding["staging_path"]) / "phase-b2-r7s1-work-order.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(b'{"work_order":"r7s1"}')
    writer = r7s1.EvidenceWriter(Path(binding["output_path"]), successor_binding=binding)
    manifest_identity = {
        "path": str(manifest_path.resolve()),
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "canonical_revision": binding["commit"],
        "canonical_tree": binding["tree"],
    }
    return binding, writer, manifest_identity


def _emergency_residue() -> dict[str, object]:
    return {
        "manual_intervention_required": True,
        "residual_pids": [],
        "residual_status": "none_observed",
    }


def test_r7s1_writer_uses_distinct_failure_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = _local_successor_binding(tmp_path, monkeypatch)
    writer = r7s1.EvidenceWriter(Path(binding["output_path"]), successor_binding=binding)
    result = writer.seal_failure({"passed": False})
    output = Path(binding["output_path"])
    seal = json.loads((output / "failure-seal.json").read_text())
    index = json.loads((output / "failure-evidence-index.json").read_text())
    assert seal["schema"] == "s8-v4-x1-phase-b2-r7s1-failure-seal/v1"
    assert index["schema"] == "s8-v4-x1-phase-b2-r7s1-failure-evidence-index/v1"
    assert (
        result["failure_seal"]["sha256"]
        == hashlib.sha256((output / "failure-seal.json").read_bytes()).hexdigest()
    )


def test_emergency_seal_records_actual_partial_artifacts_and_is_failure_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding, writer, manifest_identity = _local_emergency_context(tmp_path, monkeypatch)
    writer.write_bytes("failure-evidence-index.json", b'{"sha256":"untrusted-claim"}')
    partial_source = writer.root / ".failure-seal.json.publish-source"
    partial_source.write_bytes(b"partial-seal-payload")

    result = r7s1.EvidenceWriter.seal_emergency(
        primary_output=writer.root,
        successor_binding=binding,
        failed_stage="ordinary_failure_seal_publication",
        exception=OSError("atomic publication failed"),
        process_residue=_emergency_residue(),
        manifest_identity=manifest_identity,
        expected_trusted_checkpoint_sha256="5" * 64,
    )

    emergency = Path(result["emergency_directory"])
    seal_path = emergency / "emergency-failure-seal.json"
    payload = json.loads(seal_path.read_text(encoding="utf-8"))
    assert payload["emergency_only"] is True
    assert payload["failure_only"] is True
    assert payload["manual_intervention_required"] is True
    assert payload["success_marker_created"] is False
    assert payload["completion_marker_created"] is False
    assert payload["phase_b2_executed"] is False
    assert payload["automatic_retry"] == 0
    assert payload["successor_binding"] == binding
    assert (
        payload["successor_binding_sha256"]
        == hashlib.sha256(r7s1.canonical_json_bytes(binding)).hexdigest()
    )
    assert payload["partial_inventory_error"] is None
    assert [item["path"] for item in payload["partial_artifacts"]] == sorted(
        [
            ".failure-evidence-index.json.publish-source",
            ".failure-seal.json.publish-source",
            "failure-evidence-index.json",
        ]
    )
    measured = {item["path"]: item["sha256"] for item in payload["partial_artifacts"]}
    assert (
        measured["failure-evidence-index.json"]
        == hashlib.sha256((writer.root / "failure-evidence-index.json").read_bytes()).hexdigest()
    )
    assert measured["failure-evidence-index.json"] != "untrusted-claim"
    assert result["emergency_seal"]["sha256"] == hashlib.sha256(seal_path.read_bytes()).hexdigest()


def test_emergency_seal_commits_after_ordinary_failure_seal_left_partial_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding, writer, manifest_identity = _local_emergency_context(tmp_path, monkeypatch)
    original_write_bytes = writer.write_bytes

    def fail_commit_record(name: str, payload: bytes) -> dict[str, object]:
        if name == "failure-seal.json":
            raise OSError("forced_failure_seal_commit_error")
        return original_write_bytes(name, payload)

    monkeypatch.setattr(writer, "write_bytes", fail_commit_record)
    with pytest.raises(OSError, match="forced_failure_seal_commit_error"):
        writer.seal_failure({"passed": False})
    assert (writer.root / "failure-evidence-index.json").is_file()
    assert not (writer.root / "failure-seal.json").exists()

    result = r7s1.EvidenceWriter.seal_emergency(
        primary_output=writer.root,
        successor_binding=binding,
        failed_stage="ordinary_failure_seal_publication",
        exception=OSError("forced_failure_seal_commit_error"),
        process_residue=_emergency_residue(),
        manifest_identity=manifest_identity,
        expected_trusted_checkpoint_sha256="5" * 64,
    )
    payload = json.loads(
        (Path(result["emergency_directory"]) / "emergency-failure-seal.json").read_text(
            encoding="utf-8"
        )
    )
    partial_names = {item["path"] for item in payload["partial_artifacts"]}
    assert "failure-evidence-index.json" in partial_names
    assert ".failure-evidence-index.json.publish-source" in partial_names
    assert "failure-seal.json" not in partial_names


def test_emergency_seal_is_create_exclusive_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding, writer, manifest_identity = _local_emergency_context(tmp_path, monkeypatch)
    arguments = {
        "primary_output": writer.root,
        "successor_binding": binding,
        "failed_stage": "failure_index_publication",
        "exception": OSError("first failure"),
        "process_residue": _emergency_residue(),
        "manifest_identity": manifest_identity,
        "expected_trusted_checkpoint_sha256": "5" * 64,
    }
    result = r7s1.EvidenceWriter.seal_emergency(**arguments)
    seal_path = Path(result["emergency_directory"]) / "emergency-failure-seal.json"
    first_sha = hashlib.sha256(seal_path.read_bytes()).hexdigest()
    with pytest.raises(r7s1.R7S1EmergencySealError, match="emergency_seal_failed"):
        r7s1.EvidenceWriter.seal_emergency(**arguments)
    assert hashlib.sha256(seal_path.read_bytes()).hexdigest() == first_sha


@pytest.mark.parametrize("failure_operation", ["open", "write", "link"])
def test_emergency_seal_publication_failures_never_create_final_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_operation: str,
) -> None:
    binding, writer, manifest_identity = _local_emergency_context(tmp_path, monkeypatch)

    def denied(*_args: object, **_kwargs: object) -> object:
        raise PermissionError(f"forced_{failure_operation}_failure")

    monkeypatch.setattr(r7s1.os, failure_operation, denied)
    with pytest.raises(
        r7s1.R7S1EmergencySealError,
        match=rf"emergency_seal_failed:PermissionError:forced_{failure_operation}_failure",
    ):
        r7s1.EvidenceWriter.seal_emergency(
            primary_output=writer.root,
            successor_binding=binding,
            failed_stage="ordinary_failure_seal_publication",
            exception=OSError("ordinary seal failed"),
            process_residue=_emergency_residue(),
            manifest_identity=manifest_identity,
            expected_trusted_checkpoint_sha256="5" * 64,
        )
    assert not (Path(binding["emergency_seal_path"]) / "emergency-failure-seal.json").exists()


def test_writer_rejects_reparse_point_in_existing_ancestor_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = _local_successor_binding(tmp_path, monkeypatch)
    output_parent = Path(binding["output_path"]).parent
    output_parent.mkdir(parents=True)
    real_lstat = r7s1.os.lstat

    class ReparseStatus:
        def __init__(self, measured: object) -> None:
            self.st_mode = measured.st_mode  # type: ignore[attr-defined]
            self.st_file_attributes = getattr(measured, "st_file_attributes", 0) | 0x400

    def marked_lstat(path: object) -> object:
        measured = real_lstat(path)
        if Path(path).resolve() == output_parent.resolve():
            return ReparseStatus(measured)
        return measured

    monkeypatch.setattr(r7s1.os, "lstat", marked_lstat)
    with pytest.raises(r7s1.R7S1ContractError, match="reparse_point_forbidden"):
        r7s1.EvidenceWriter(Path(binding["output_path"]), successor_binding=binding)


def test_emergency_publisher_rechecks_existing_target_reparse_before_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding, writer, manifest_identity = _local_emergency_context(tmp_path, monkeypatch)
    emergency_path = Path(binding["emergency_seal_path"])
    real_lstat = r7s1.os.lstat
    template_status = real_lstat(emergency_path.parent)

    class ReparseStatus:
        st_mode = template_status.st_mode
        st_file_attributes = getattr(template_status, "st_file_attributes", 0) | 0x400

    def marked_lstat(path: object) -> object:
        if Path(path) == emergency_path:
            return ReparseStatus()
        return real_lstat(path)

    monkeypatch.setattr(r7s1.os, "lstat", marked_lstat)
    with pytest.raises(r7s1.R7S1EmergencySealError, match="reparse_point_forbidden"):
        r7s1.EvidenceWriter.seal_emergency(
            primary_output=writer.root,
            successor_binding=binding,
            failed_stage="failure_index",
            exception=OSError("ordinary failure"),
            process_residue=_emergency_residue(),
            manifest_identity=manifest_identity,
            expected_trusted_checkpoint_sha256="5" * 64,
        )
    assert not emergency_path.exists()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda identity, binding: identity.update(
                path=str(Path(binding["staging_path"]) / "alternate.json")
            ),
            "emergency_manifest_path_mismatch",
        ),
        (
            lambda identity, _binding: identity.update(canonical_revision="9" * 40),
            "emergency_manifest_source_identity_mismatch",
        ),
    ],
)
def test_emergency_manifest_identity_mutations_are_rejected_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: object,
    match: str,
) -> None:
    binding, writer, manifest_identity = _local_emergency_context(tmp_path, monkeypatch)
    mutation(manifest_identity, binding)  # type: ignore[operator]
    with pytest.raises(r7s1.R7S1EmergencySealError, match=match):
        r7s1.EvidenceWriter.seal_emergency(
            primary_output=writer.root,
            successor_binding=binding,
            failed_stage="failure_seal",
            exception=OSError("ordinary failure"),
            process_residue=_emergency_residue(),
            manifest_identity=manifest_identity,
            expected_trusted_checkpoint_sha256="5" * 64,
        )
    assert not Path(binding["emergency_seal_path"]).exists()


def test_projected_runtime_imports_and_validates_without_r7_core(tmp_path: Path) -> None:
    package = tmp_path / "projected"
    module_root = package / "evm" / "scale_validation"
    module_root.mkdir(parents=True)
    (package / "evm" / "__init__.py").write_text("", encoding="utf-8")
    (module_root / "__init__.py").write_text("", encoding="utf-8")
    source_root = Path(r7s1.__file__).resolve().parent
    shutil.copy2(source_root / "phase_b2_r7s1.py", module_root)
    shutil.copy2(source_root / "phase_b2_r7_process.py", module_root)
    assert not (module_root / "phase_b2_r7.py").exists()
    source = (module_root / "phase_b2_r7s1.py").read_text(encoding="utf-8")
    assert "from evm.scale_validation import phase_b2_r7" not in source
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path)), encoding="utf-8")
    code = (
        "import json,sys;"
        f"sys.path.insert(0,{str(package)!r});"
        "from evm.scale_validation import phase_b2_r7s1 as core;"
        f"m=json.load(open({str(manifest_path)!r},encoding='utf-8'));"
        "v=core.validate_r7s1_manifest(m,expected_revision='a'*40,"
        "verify_attestations=False,expected_trusted_checkpoint_sha256=None);"
        "assert v['historical_go'] is False;"
        "assert len(v['parents'])==10"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_core_never_spawns_and_worktree_blob_oid_is_computed_in_process(tmp_path: Path) -> None:
    source = Path(r7s1.__file__).read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert "subprocess." not in source
    payload = tmp_path / "runtime.py"
    payload.write_bytes(b"print('contained')\n")
    expected = hashlib.sha1(
        f"blob {payload.stat().st_size}\0".encode("ascii") + payload.read_bytes(),
        usedforsecurity=False,
    ).hexdigest()
    assert r7s1.git_worktree_blob_oid(tmp_path, payload) == expected


def test_runtime_pins_distinguish_crlf_worktree_blob_from_head_blob_and_reject_swap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()

    def blob_oid(payload: bytes) -> str:
        return hashlib.sha1(
            f"blob {len(payload)}\0".encode("ascii") + payload,
            usedforsecurity=False,
        ).hexdigest()

    runtime: dict[str, dict[str, object]] = {}
    for index, name in enumerate(r7s1.RUNTIME_COMPONENTS):
        raw = b"first\r\nsecond\r\n" if index == 0 else f"component-{name}\n".encode("utf-8")
        head = raw.replace(b"\r\n", b"\n")
        path = root / f"{name}.py"
        path.write_bytes(raw)
        runtime[name] = {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "worktree_blob_oid": blob_oid(raw),
            "head_blob_oid": blob_oid(head),
            "bytes": len(raw),
        }
    measured = r7s1.validate_runtime_pins({"runtime": runtime}, root)
    first = r7s1.RUNTIME_COMPONENTS[0]
    assert measured[first]["worktree_blob_oid"] != measured[first]["head_blob_oid"]

    swapped = copy.deepcopy(runtime)
    swapped[first]["worktree_blob_oid"], swapped[first]["head_blob_oid"] = (
        swapped[first]["head_blob_oid"],
        swapped[first]["worktree_blob_oid"],
    )
    with pytest.raises(r7s1.R7S1ContractError, match="worktree_blob_oid_mismatch"):
        r7s1.validate_runtime_pins({"runtime": swapped}, root)

    legacy = copy.deepcopy(runtime)
    legacy[first]["blob_oid"] = legacy[first].pop("worktree_blob_oid")
    with pytest.raises(r7s1.R7S1ContractError, match="runtime_.*_fields_mismatch"):
        r7s1.validate_runtime_pins({"runtime": legacy}, root)

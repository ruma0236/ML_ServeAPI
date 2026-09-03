from __future__ import annotations

import base64
import dis
import hashlib
import inspect
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from scripts.dev import publish_pre_r8_r7s5_review as review
from scripts.dev import run_pre_r8_r7s5_validation as runner


def _clean_process_outcome(
    *,
    name: str,
    command: list[str],
    pid: int,
    stdout: str = "",
    stderr: str = "",
    return_code: int = 0,
    executable_sha256: str = "3" * 64,
    executable_bytes: int = 1,
) -> dict[str, object]:
    run_uuid = f"10000000-0000-4000-8000-{pid:012d}"
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    return {
        "name": name,
        "run_uuid": run_uuid,
        "command": command,
        "started_at_utc": "2026-09-02T00:00:00+00:00",
        "ended_at_utc": "2026-09-02T00:00:01+00:00",
        "duration_seconds": 1.0,
        "timed_out": False,
        "cancelled": False,
        "return_code": return_code,
        "manual_intervention_required": False,
        "residual_pids": [],
        "stdout": stdout,
        "stderr": stderr,
        "stdout_drained": True,
        "stderr_drained": True,
        "streams_drained": True,
        "active_process_zero": True,
        "final_active_process_count": 0,
        "identity_coverage_complete": True,
        "safe_for_followup": return_code == 0,
        "forced_termination_attempts": 0,
        "job_limit_flags": 0,
        "executable_identity": {
            "path": command[0],
            "sha256": executable_sha256,
            "bytes": executable_bytes,
            "device": 1,
            "file_id": pid,
            "expected_sha256": executable_sha256,
            "pin_required": True,
            "pin_match": True,
            "measurement_scope": "immediately_before_CreateProcessW",
            "handle_lock_held_through_create": True,
            "handle_lock_share_mode": "FILE_SHARE_READ_only",
            "handle_lock_inheritable": False,
            "ancestor_directory_locks_held_through_create": True,
            "ancestor_directory_lock_count": 4,
            "ancestor_directory_lock_share_mode": "FILE_SHARE_READ_WRITE_no_delete",
            "path_lock_scope": "all_nonroot_ancestors_and_leaf",
            "pre_kernel_create_gate_required": True,
            "pre_kernel_create_gate_passed": True,
            "pre_kernel_create_gate_invocations": 1,
            "pre_kernel_remaining_seconds": 3_000.0,
            "pre_kernel_required_seconds": 2_000.0,
        },
        "stream_capture_limit_bytes": review.DEFAULT_MAX_STREAM_BYTES,
        "stdout_total_bytes": len(stdout_bytes),
        "stderr_total_bytes": len(stderr_bytes),
        "stdout_capture_overflow": False,
        "stderr_capture_overflow": False,
        "stream_cleanup": {
            "schema": "evm.phase-b2.stream-reader-cleanup.v1",
            "reason": "stream_drain_gate",
            "read_handle_owner": "reader_thread",
            "bounded_by_restore_deadline": True,
            "readers": [
                {
                    "stream": stream,
                    "started": True,
                    "native_thread_id": native_id,
                    "drained_before_cleanup": True,
                    "exited_before_cleanup": True,
                    "cancel_attempted": False,
                    "cancel_succeeded": False,
                    "no_pending_io": False,
                    "cancel_error_code": None,
                    "exited_after_cleanup": True,
                    "thread_alive_after_cleanup": False,
                    "read_handle_close_scope": "reader_read_pipe_finally",
                    "bounded_join_timeout_seconds": 0.0,
                }
                for stream, native_id in (("stdout", 101), ("stderr", 102))
            ],
            "all_reader_threads_exited": True,
            "forced_termination_attempts": 0,
        },
        "identities": [
            {
                "pid": pid,
                "ppid": 900,
                "creation_time_ns": 1_000_000 + pid,
                "creation_time_utc": "2026-09-02T00:00:00+00:00",
                "image": command[0],
                "run_uuid": run_uuid,
                "observed_sequence": 4,
            }
        ],
        "events": [
            {
                "sequence": sequence,
                "event": event,
                "monotonic_ns": sequence,
                "timestamp_utc": (
                    "2026-09-02T00:00:01+00:00" if sequence == 9 else "2026-09-02T00:00:00+00:00"
                ),
                "pid": event_pid,
                "details": (
                    {"active_processes": 1, "job_limit_flags": 0}
                    if event == "job_membership_verified"
                    else {}
                ),
            }
            for sequence, event, event_pid in (
                (1, "job_created", None),
                (2, "root_created_suspended", pid),
                (3, "job_membership_verified", pid),
                (4, "identity_observed", pid),
                (5, "root_resumed", pid),
                (7, "active_process_count_zero", None),
                (9, "streams_drained", None),
            )
        ],
        "accounting": [
            {
                "sequence": 6,
                "monotonic_ns": 6,
                "timestamp_utc": "2026-09-02T00:00:00+00:00",
                "total_processes": 1,
                "active_processes": 1,
                "total_terminated_processes": 0,
                "active_pids": [pid],
            },
            {
                "sequence": 8,
                "monotonic_ns": 8,
                "timestamp_utc": "2026-09-02T00:00:01+00:00",
                "total_processes": 1,
                "active_processes": 0,
                "total_terminated_processes": 0,
                "active_pids": [],
            },
        ],
        "errors": [],
    }


def _environment_commitment() -> dict[str, object]:
    keys = ["PATH", "PYTHONUTF8"]
    return {
        "schema": runner.ENVIRONMENT_SCHEMA,
        "sha256": "4" * 64,
        "key_count": len(keys),
        "keys": keys,
        "removed_secret_like_variable_count": 0,
        "removed_secret_like_variable_name_sha256": hashlib.sha256(
            review.canonical_json_bytes([])
        ).hexdigest(),
        "values_disclosed": False,
        "runner_injected_ephemeral_keys_excluded": list(runner._RUNNER_INJECTED_ENVIRONMENT_KEYS),
    }


def _output_parent_commitment(path: Path) -> dict[str, object]:
    normalized = os.path.normcase(os.path.normpath(str(path.resolve(strict=True))))
    payload = {
        "schema": runner.OUTPUT_PARENT_SCHEMA,
        "normalized_path": normalized,
    }
    return {
        **payload,
        "sha256": hashlib.sha256(review.canonical_json_bytes(payload)).hexdigest(),
    }


def _runner_untracked_inventory(repository: Path) -> dict[str, object]:
    observed = runner._inventory_from_untracked_paths(repository, [])
    expected = {
        "count": observed["count"],
        "path_list_sha256": observed["path_list_sha256"],
        "content_inventory_sha256": observed["content_inventory_sha256"],
    }
    return {
        **observed,
        "ignored_import_active_shadow_path_count": 0,
        "expected": expected,
        "matches_expected": True,
    }


def _handle_identity(path: Path, *, size: int, is_directory: bool) -> dict[str, object]:
    return {
        "final_path": str(path.resolve(strict=True)),
        "volume_serial_number": 1,
        "file_id_hex": "01" if is_directory else "02",
        "size": size,
        "link_count": 1,
        "attributes": 0x10 if is_directory else 0,
        "reparse_tag": 0,
        "file_type": 1,
        "owner_sid": "S-1-5-32-544",
        "security_descriptor_sha256": "5" * 64,
        "dacl_present": True,
        "dacl_protected": not is_directory,
    }


def _publication_receipt(path: Path, raw: bytes) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    parent = resolved.parent
    return {
        "final_path": str(resolved),
        "temporary_leaf": f".{resolved.name}.10000000-0000-4000-8000-000000000000.partial",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "identity": _handle_identity(resolved, size=len(raw), is_directory=False),
        "directory_identity": _handle_identity(parent, size=0, is_directory=True),
        "file_flush_count": 2,
        "directory_flush_count": 1,
        "directory_flush_succeeded": True,
        "replace_if_exists": False,
        "same_handle_readback": True,
        "file_identity_stable_across_rename": True,
        "power_loss_durability_proven": False,
        "same_token_hostile_admin_protected": False,
        "go_evidence_eligible": False,
    }


def _tool_identity(
    spec: runner.CommandSpec, index: int, *, live: bool = False
) -> dict[str, object]:
    candidate = Path(spec.argv[0])
    tool = (
        candidate.resolve(strict=True) if candidate.is_absolute() else (Path.cwd() / "trusted-tool")
    )
    tool_path = str(tool)
    tool_raw = tool.read_bytes() if tool.is_file() else b"x"
    tool_sha256 = hashlib.sha256(tool_raw).hexdigest()
    tool_bytes = len(tool_raw)
    version_argv = [tool_path, "--version"]
    runtime_version_argv = [tool_path, "--version"]
    offset = 50_000 if live else 10_000
    assert spec.work_order_tool_role is not None
    work_order_binding = runner.work_order_tool_binding(
        spec.work_order_tool_role, tool, tool_sha256
    )
    module_binding = (
        work_order_binding.get("python_tool_module")
        if spec.python_tool_distribution is not None
        else None
    )
    return {
        "path": tool_path,
        "bytes": tool_bytes,
        "sha256": tool_sha256,
        "version_argv": version_argv,
        "version": "trusted tool 1",
        "runtime_version_argv": runtime_version_argv,
        "runtime_version": "trusted runtime 1",
        "version_process_containment": _clean_process_outcome(
            name=f"r7s6-validation-metadata-tool-version-{spec.name}",
            command=version_argv,
            pid=offset + index * 2,
            stdout="trusted tool 1\n",
            executable_sha256=tool_sha256,
            executable_bytes=tool_bytes,
        ),
        "runtime_version_process_containment": _clean_process_outcome(
            name=f"r7s6-validation-metadata-runtime-version-{spec.name}",
            command=runtime_version_argv,
            pid=offset + index * 2 + 1,
            stdout="trusted runtime 1\n",
            executable_sha256=tool_sha256,
            executable_bytes=tool_bytes,
        ),
        "environment_commitment": _environment_commitment(),
        "python_tool_module": module_binding,
        "work_order_binding_role": spec.work_order_tool_role,
        "work_order_binding_sha256": hashlib.sha256(
            review.canonical_json_bytes(work_order_binding)
        ).hexdigest(),
        "work_order_module_binding_sha256": (
            hashlib.sha256(review.canonical_json_bytes(module_binding)).hexdigest()
            if module_binding is not None
            else None
        ),
    }


def _code_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, object], dict[str, object]]:
    repository = tmp_path / "repository"
    project_root = repository / "enterprise-vision-mlops"
    evidence_root = tmp_path / "evidence"
    project_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    head = "1" * 40
    tree = "2" * 40
    trusted_tool = tmp_path / "trusted-tool.exe"
    trusted_tool.write_bytes(b"trusted-test-tool")
    (tmp_path / "Lib" / "site-packages").mkdir(parents=True)
    trusted_tool_sha256 = hashlib.sha256(trusted_tool.read_bytes()).hexdigest()
    monkeypatch.setattr(
        runner,
        "python_tool_module_binding",
        lambda _executable, distribution: {
            "distribution": distribution,
            "version": "test-only",
            "module_origins": {},
            "content_inventory_sha256": "0" * 64,
            "authority_scope": "test_only_internal_non_authoritative",
        },
    )
    trusted_outer = Path(review.__file__).with_name("invoke_pre_r8_r7s7_review.ps1")
    trusted_outer_sha256 = review.sha256_file(trusted_outer)
    specs = tuple(
        runner.CommandSpec(
            name,
            (str(trusted_tool), name),
            expected_exit_code=2 if name == "ci-active-workflow-required-rejection" else 0,
            required_output_tokens=(
                ("required-output-token",)
                if name == "ci-active-workflow-required-rejection"
                else ()
            ),
            python_tool_distribution=runner.WORK_ORDER_TOOL_CONTRACT_BY_COMMAND[name][1],
            work_order_tool_role=runner.WORK_ORDER_TOOL_CONTRACT_BY_COMMAND[name][0],
        )
        for name in sorted(review.REQUIRED_VALIDATION_COMMANDS)
    )
    environment_commitment = _environment_commitment()
    output_parent_commitment = _output_parent_commitment(tmp_path)
    tools = {spec.name: _tool_identity(spec, index) for index, spec in enumerate(specs, start=1)}
    plan_payload = {
        "repository": str(repository),
        "project_root": str(project_root),
        "head": head,
        "tree": tree,
        "commands": [
            {
                "name": spec.name,
                "argv": list(spec.argv),
                "cwd": str(project_root),
                "expected_exit_code": spec.expected_exit_code,
                "required_output_tokens": list(spec.required_output_tokens),
                "wrapper_timeout_seconds": spec.wrapper_timeout_seconds,
                "residual_repoll_seconds": runner.VALIDATION_RESIDUAL_REPOLL_SECONDS,
                "stream_drain_seconds": runner.VALIDATION_STREAM_DRAIN_SECONDS,
                "tool": tools[spec.name],
            }
            for spec in specs
        ],
        "environment_commitment": environment_commitment,
        "observation_scope": runner.VALIDATION_OBSERVATION_SCOPE,
    }
    plan = {
        **plan_payload,
        "sha256": hashlib.sha256(review.canonical_json_bytes(plan_payload)).hexdigest(),
    }
    live_plan_payload = json.loads(json.dumps(plan_payload))
    for index, (live_command, spec) in enumerate(
        zip(live_plan_payload["commands"], specs, strict=True), start=1
    ):
        live_command["tool"] = _tool_identity(spec, index, live=True)
    expected_live_plan = {
        **live_plan_payload,
        "sha256": hashlib.sha256(review.canonical_json_bytes(live_plan_payload)).hexdigest(),
    }
    monkeypatch.setattr(runner, "build_command_specs", lambda **_kwargs: specs)

    def live_inventory_git_child(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        purpose: str,
        expected_return_code: int = 0,
        expected_executable_sha256: str | None = None,
    ) -> SimpleNamespace:
        assert argv
        assert cwd == repository
        assert expected_return_code == 0
        review._append_publisher_child_execution(
            review._redacted_publisher_child_evidence(
                {"stdout": "", "stderr": "", "synthetic_test_double": True},
                purpose=purpose,
                clean=True,
                environment_commitment=environment_commitment,
                expected_executable_sha256=(expected_executable_sha256 or trusted_tool_sha256),
                secret_like_output_detected=False,
            )
        )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(review, "_run_publisher_child", live_inventory_git_child)

    def live_command_plan(**call_kwargs: Any) -> dict[str, Any]:
        metadata_evidence = call_kwargs.get("metadata_evidence")
        before_metadata_child = call_kwargs.get("before_metadata_child")
        assert isinstance(metadata_evidence, list)
        assert callable(before_metadata_child)
        assert (
            call_kwargs.get("expected_work_order_tool_bindings") == work_order["tool_file_bindings"]
        )
        for command, spec in zip(expected_live_plan["commands"], specs, strict=True):
            tool = command["tool"]
            for record in (
                {
                    "name": f"tool-version-{spec.name}",
                    "phase": f"command_plan:{spec.name}:tool_version",
                    "status": "PASS",
                    "child_invoked": True,
                    "process_containment": json.loads(
                        json.dumps(tool["version_process_containment"])
                    ),
                },
                {
                    "name": f"runtime-version-{spec.name}",
                    "phase": f"command_plan:{spec.name}:runtime_version",
                    "status": "PASS",
                    "child_invoked": True,
                    "process_containment": json.loads(
                        json.dumps(tool["runtime_version_process_containment"])
                    ),
                },
            ):
                before_metadata_child(record["name"])
                metadata_evidence.append(record)
        return json.loads(json.dumps(expected_live_plan))

    monkeypatch.setattr(runner, "command_plan", live_command_plan)
    git_tool = tools["git-diff-check"]
    git_path = Path(str(git_tool["path"]))
    metadata_specs = runner.expected_success_metadata_child_sequence(
        specs,
        git_executable=git_path,
    )
    metadata_children = [
        {
            "name": name,
            "phase": phase,
            "status": "PASS",
            "child_invoked": True,
            "process_containment": _clean_process_outcome(
                name=f"r7s6-validation-metadata-{name}",
                command=list(arguments),
                pid=70_000 + index,
                executable_sha256=str(git_tool["sha256"]),
                executable_bytes=int(git_tool["bytes"]),
            ),
        }
        for index, (name, phase, arguments) in enumerate(metadata_specs, start=1)
    ]
    untracked_inventory = _runner_untracked_inventory(repository)
    commands: list[dict[str, object]] = []
    for index, spec in enumerate(specs, start=1):
        path = evidence_root / f"{index:02d}-{spec.name}.json"
        stdout_text = "required-output-token\n" if spec.required_output_tokens else ""
        stdout_raw = stdout_text.encode("utf-8")
        tool_path = str(tools[spec.name]["path"])
        process_evidence = _clean_process_outcome(
            name=f"r7s6-validation-{spec.name}",
            command=[tool_path, *spec.argv[1:]],
            pid=1_000 + index,
            stdout=stdout_text,
            return_code=spec.expected_exit_code,
            executable_sha256=str(tools[spec.name]["sha256"]),
            executable_bytes=int(tools[spec.name]["bytes"]),
        )
        raw = review.canonical_json_bytes(
            {
                "schema": review.COMMAND_EVIDENCE_SCHEMA,
                "name": spec.name,
                "status": "PASS",
                "exit_code": spec.expected_exit_code,
                "expected_exit_code": spec.expected_exit_code,
                "argv": list(spec.argv),
                "cwd": str(project_root),
                "repository": str(repository),
                "repository_head_before": head,
                "repository_head_after": head,
                "repository_tree_before": tree,
                "repository_tree_after": tree,
                "tracked_clean_before": True,
                "tracked_clean_after": True,
                "untracked_inventory_before": untracked_inventory,
                "untracked_inventory_after": untracked_inventory,
                "command_plan_sha256": plan["sha256"],
                "tool": tools[spec.name],
                "environment_commitment": environment_commitment,
                "output_parent_commitment": output_parent_commitment,
                "started_at_utc": "2026-09-02T00:00:00Z",
                "ended_at_utc": "2026-09-02T00:00:01Z",
                "duration_ns": 1_000_000_000,
                "stdout_bytes": len(stdout_raw),
                "stdout_sha256": hashlib.sha256(stdout_raw).hexdigest(),
                "stdout_tail": stdout_text,
                "stderr_bytes": 0,
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "stderr_tail": "",
                "stream_encoding": "utf-8",
                "stream_hash_scope": "decoded_text_reencoded_utf8_not_raw_pipe_bytes",
                "stream_boundary_token_synthesis_allowed": False,
                "required_tokens_present_in_individual_streams": True,
                "secret_like_output_detected": False,
                "process_containment": process_evidence,
                "derived_containment_errors": [],
                "containment_cleared_before_followup": True,
                "followup_child_count_after_containment_latch": 0,
                "forced_termination_attempts": 0,
                "automatic_retry_count": 0,
                "orchestrator_prohibited_live_command_calls": 0,
                "live_call_observation_scope": runner.VALIDATION_OBSERVATION_SCOPE,
            }
        )
        path.write_bytes(raw)
        commands.append(
            {
                "name": spec.name,
                "status": "PASS",
                "exit_code": spec.expected_exit_code,
                "expected_exit_code": spec.expected_exit_code,
                "evidence_path": str(path),
                "evidence_bytes": len(raw),
                "evidence_sha256": hashlib.sha256(raw).hexdigest(),
                "publication": _publication_receipt(path, raw),
            }
        )
    now = datetime.now(UTC)
    work_order = {
        "schema": runner.WORK_ORDER_SCHEMA,
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "immutable_checkout_namespace_authority": False,
        "runtime_stdlib_native_closure_verified": False,
        "validation_run_uuid": "10000000-0000-4000-8000-000000000001",
        "validation_attempt_uuid": "20000000-0000-4000-8000-000000000002",
        "handoff_challenge_sha256": "c" * 64,
        "issued_at_utc": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at_utc": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "expected_head": head,
        "expected_tree": tree,
        "tool_file_bindings": {
            name: runner.work_order_tool_binding(name, trusted_tool, trusted_tool_sha256)
            for name in ("git", "powershell", "python_general", "python_host", "python_ruff")
        },
        "code_file_bindings": runner.work_order_code_file_bindings(
            trusted_outer, trusted_outer_sha256
        ),
        "command_invocation_sha256": runner.command_invocation_commitment(specs),
        "pycache_prefix": str(
            runner.validation_pycache_prefix(tmp_path, "10000000-0000-4000-8000-000000000001")
        ),
    }
    work_order_path = tmp_path / "external-work-order.json"
    work_order_raw = review.canonical_json_bytes(work_order)
    work_order_path.write_bytes(work_order_raw)
    telemetry_payload = {
        "schema": runner.LIVE_TELEMETRY_SCHEMA,
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "observation_state": "unknown",
        "observation_scope": "internal_non_authoritative",
        "collector_authority_verified": False,
        "counts": {name: None for name in review.REQUIRED_ZERO_LIVE_CALLS},
        "raw_events_sha256": hashlib.sha256(review.canonical_json_bytes([])).hexdigest(),
    }
    telemetry_path = tmp_path / "live-call-telemetry.json"
    telemetry_raw = review.canonical_json_bytes(telemetry_payload)
    telemetry_path.write_bytes(telemetry_raw)
    value = {
        "schema": review.VALIDATION_SCHEMA,
        "status": "PASS",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "evidence_scope": "internal_non_authoritative",
        "go_evidence_eligible": False,
        "runtime_identity_stability": "unproven",
        "immutable_checkout_namespace_authority": False,
        "runtime_stdlib_native_closure_verified": False,
        "validation_run_uuid": work_order["validation_run_uuid"],
        "validation_attempt_uuid": work_order["validation_attempt_uuid"],
        "handoff_challenge_sha256": work_order["handoff_challenge_sha256"],
        "issued_at_utc": work_order["issued_at_utc"],
        "completed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "expires_at_utc": work_order["expires_at_utc"],
        "external_work_order_binding": {
            "path": str(work_order_path.resolve()),
            "bytes": len(work_order_raw),
            "sha256": hashlib.sha256(work_order_raw).hexdigest(),
            "authority_scope": "internal_non_authoritative",
            "authority_verified": False,
            "payload": work_order,
        },
        "replay_consumption": {
            "status": "not_consumed",
            "adapter_scope": "none",
            "authority_verified": False,
            "replay_key": review.validation_replay_key(
                validation_run_uuid=str(work_order["validation_run_uuid"]),
                validation_attempt_uuid=str(work_order["validation_attempt_uuid"]),
                handoff_challenge_sha256=str(work_order["handoff_challenge_sha256"]),
                work_order_sha256=hashlib.sha256(work_order_raw).hexdigest(),
            ),
        },
        "repository": str(repository),
        "project_root": str(project_root),
        "head": head,
        "tree": tree,
        "command_plan": json.loads(json.dumps(plan)),
        "command_plan_sha256": plan["sha256"],
        "environment_commitment": environment_commitment,
        "output_parent_commitment": output_parent_commitment,
        "independent_executable_pins": {
            name: {"path": str(trusted_tool.resolve()), "sha256": trusted_tool_sha256}
            for name in ("git", "powershell", "python_general", "python_host", "python_ruff")
        },
        "expected_untracked_inventory": untracked_inventory["expected"],
        "metadata_children": metadata_children,
        "metadata_child_call_count": len(metadata_children),
        "commands": commands,
        "planned_command_count": len(specs),
        "executed_command_count": len(specs),
        "validation_child_call_count": len(specs),
        "not_run_commands": [],
        "terminal_containment_latch": False,
        "terminal_latch_reason": None,
        "followup_child_count_after_containment_latch": 0,
        "live_call_telemetry": {
            "path": str(telemetry_path.resolve()),
            "bytes": len(telemetry_raw),
            "sha256": hashlib.sha256(telemetry_raw).hexdigest(),
            "payload": telemetry_payload,
        },
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
    }
    kwargs = {
        "repository": repository,
        "project_root": project_root,
        "expected_head": head,
        "expected_tree": tree,
        "python_general": trusted_tool,
        "python_general_sha256": trusted_tool_sha256,
        "python_host": trusted_tool,
        "python_host_sha256": trusted_tool_sha256,
        "python_ruff": trusted_tool,
        "python_ruff_sha256": trusted_tool_sha256,
        "git_executable": trusted_tool,
        "git_executable_sha256": trusted_tool_sha256,
        "powershell_executable": trusted_tool,
        "powershell_executable_sha256": trusted_tool_sha256,
        "expected_untracked_count": 0,
        "expected_untracked_path_list_sha256": str(untracked_inventory["path_list_sha256"]),
        "expected_untracked_content_inventory_sha256": str(
            untracked_inventory["content_inventory_sha256"]
        ),
        "expected_summary_sha256": hashlib.sha256(review.canonical_json_bytes(value)).hexdigest(),
        "external_work_order": work_order_path,
        "external_work_order_sha256": hashlib.sha256(work_order_raw).hexdigest(),
        "trusted_outer": trusted_outer,
        "trusted_outer_sha256": trusted_outer_sha256,
    }
    return value, kwargs


def _write_validation_terminal_files(
    value: dict[str, object],
) -> tuple[Path, Path, dict[str, object]]:
    evidence_directory = Path(value["commands"][0]["evidence_path"]).parent
    summary_path = evidence_directory / review.VALIDATION_SUMMARY_LEAF
    summary_raw = review.canonical_json_bytes(value)
    summary_path.write_bytes(summary_raw)
    index = {
        "schema": runner.PUBLICATION_INDEX_SCHEMA,
        "status": "PASS",
        "summary": {
            "path": str(summary_path),
            "bytes": len(summary_raw),
            "sha256": hashlib.sha256(summary_raw).hexdigest(),
            "publication": _publication_receipt(summary_path, summary_raw),
        },
        "environment_commitment": value["environment_commitment"],
        "output_parent_commitment": value["output_parent_commitment"],
        "metadata_child_call_count": value["metadata_child_call_count"],
        "command_publication_receipts_bound_through_summary": True,
        "completion_marker_created": False,
        "success_marker_created": False,
        "self_publication_receipt_embedded": False,
        "self_publication_receipt_scope": (
            "outer_result_only_non_self_referential_by_construction"
        ),
    }
    index_path = evidence_directory / review.VALIDATION_PUBLICATION_INDEX_LEAF
    index_path.write_bytes(review.canonical_json_bytes(index))
    return summary_path, index_path, index


def _rewrite_first_command_evidence(
    value: dict[str, object],
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    command = value["commands"][0]
    path = Path(command["evidence_path"])
    record = json.loads(path.read_bytes())
    mutation(record)
    raw = review.canonical_json_bytes(record)
    path.write_bytes(raw)
    command["evidence_bytes"] = len(raw)
    command["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
    command["publication"] = _publication_receipt(path, raw)


def _rewrite_all_command_evidence(
    value: dict[str, object],
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    for command in value["commands"]:
        path = Path(command["evidence_path"])
        record = json.loads(path.read_bytes())
        mutation(record)
        raw = review.canonical_json_bytes(record)
        path.write_bytes(raw)
        command["evidence_bytes"] = len(raw)
        command["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
        command["publication"] = _publication_receipt(path, raw)


def _repin_command_plan(value: dict[str, object]) -> None:
    plan = value["command_plan"]
    payload = {key: item for key, item in plan.items() if key != "sha256"}
    digest = hashlib.sha256(review.canonical_json_bytes(payload)).hexdigest()
    plan["sha256"] = digest
    value["command_plan_sha256"] = digest


def test_directory_inventory_is_sorted_complete_and_content_bound(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "b.json").write_bytes(b"b")
    (tmp_path / "nested" / "a.json").write_bytes(b"a")
    result = review.directory_inventory(tmp_path)
    assert result["file_count"] == 2
    assert result["directory_count"] == 1
    assert result["entry_count"] == 3
    assert result["total_bytes"] == 2
    assert [item["relative_path"] for item in result["files"]] == [
        "b.json",
        "nested/a.json",
    ]
    assert all(len(item["sha256"]) == 64 for item in result["files"])
    assert result["directories"] == [{"relative_path": "nested", "type": "directory"}]
    assert len(result["inventory_sha256"]) == 64
    assert len(result["tree_inventory_sha256"]) == 64
    assert result["read_only_operation"] is True


def test_primary_inventory_readback_is_bound_to_handle_verified_leaf_bytes_and_sha(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    original = b'{"sealed":true}\n'
    artifact = primary / "evidence.json"
    artifact.write_bytes(original)
    batch = SimpleNamespace(
        output_directory=primary,
        final_verification=SimpleNamespace(
            inventory=(
                {
                    "leaf": "evidence.json",
                    "status": "handle_bound_read_back",
                    "bytes": len(original),
                    "sha256": hashlib.sha256(original).hexdigest(),
                },
            )
        ),
    )

    verified = review.verify_primary_inventory_readback(
        batch,
        review.directory_inventory(primary),
    )
    assert verified["status"] == "PASS"
    assert verified["exact_match"] is True

    # Simulate mutation after the evidence writer released its final handles.
    artifact.write_bytes(b'{"sealed":false}\n')
    mutated = review.verify_primary_inventory_readback(
        batch,
        review.directory_inventory(primary),
    )
    assert mutated["status"] == "FAIL"
    assert mutated["exact_match"] is False
    assert mutated["expected_inventory_sha256"] != mutated["observed_inventory_sha256"]

    artifact.write_bytes(original)
    (primary / "unexpected-empty-directory").mkdir()
    directory_mutated = review.verify_primary_inventory_readback(
        batch,
        review.directory_inventory(primary),
    )
    assert directory_mutated["status"] == "FAIL"
    assert directory_mutated["exact_match"] is False
    assert directory_mutated["observed_directory_count"] == 1
    assert (
        directory_mutated["expected_tree_inventory_sha256"]
        != directory_mutated["observed_tree_inventory_sha256"]
    )


def test_untracked_path_inventory_preserves_nul_sorted_baseline_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = b"zeta\0alpha\0"

    def fake_run_git(
        repository: Path, arguments: list[str], *, binary: bool = False, **_kwargs: object
    ) -> str | bytes:
        assert repository == tmp_path
        assert arguments == ["ls-files", "--others", "--exclude-standard", "-z"]
        assert binary is True
        return raw

    monkeypatch.setattr(review, "run_git", fake_run_git)
    result = review.untracked_summary(tmp_path)
    expected = hashlib.sha256(b"alpha\0zeta\0").hexdigest()
    assert result["count"] == 2
    assert result["regular_files"] == 0
    assert result["path_inventory_sha256"] == expected
    assert result["path_inventory_encoding"] == "utf-8-nul-sorted"
    assert result["paths_persisted_in_evidence"] is False


def test_untracked_content_inventory_detects_same_path_same_size_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "owned.txt").write_bytes(b"alpha")
    monkeypatch.setattr(review, "run_git", lambda *_args, **_kwargs: b"owned.txt\0")
    first = review.untracked_summary(tmp_path)
    (tmp_path / "owned.txt").write_bytes(b"omega")
    second = review.untracked_summary(tmp_path)
    assert first["path_inventory_sha256"] == second["path_inventory_sha256"]
    assert first["bytes"] == second["bytes"] == 5
    assert first["content_inventory_sha256"] != second["content_inventory_sha256"]


@pytest.mark.parametrize(
    "relative_path",
    [
        "pluggy.py",
        "evm/__init__.pyc",
        "native/module.pyd",
        "nested/pytest.ini",
    ],
)
def test_untracked_summary_rejects_any_import_active_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative_path: str,
) -> None:
    monkeypatch.setattr(
        review,
        "run_git",
        lambda *_args, **_kwargs: relative_path.encode() + b"\0",
    )

    with pytest.raises(review.ReviewPublisherError, match="untracked_import_shadow"):
        review.untracked_summary(tmp_path, reject_import_active=True)


def test_untracked_summary_rejects_ignored_sourceless_import_shadow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run_git(_repository: Path, arguments: list[str], **_kwargs: object) -> bytes:
        calls.append(arguments)
        if "--ignored" in arguments:
            return b"sitecustomize.pyc\0"
        return b""

    monkeypatch.setattr(review, "run_git", fake_run_git)

    with pytest.raises(review.ReviewPublisherError, match="ignored_import_shadow"):
        review.untracked_summary(tmp_path, reject_import_active=True)
    assert len(calls) == 2
    assert "--ignored" in calls[1]


@pytest.mark.parametrize(
    ("binary", "stdout", "expected"),
    [
        (False, "abc\n", "abc"),
        (True, "abc\0", b"abc\0"),
    ],
)
def test_run_git_delegates_to_no_kill_publisher_job_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    binary: bool,
    stdout: str,
    expected: str | bytes,
) -> None:
    observed: dict[str, object] = {}

    def fake_run_child(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        purpose: str,
        expected_return_code: int = 0,
        expected_executable_sha256: str | None = None,
    ) -> SimpleNamespace:
        observed.update(
            argv=argv,
            cwd=cwd,
            purpose=purpose,
            expected_return_code=expected_return_code,
            expected_executable_sha256=expected_executable_sha256,
        )
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(review, "_run_publisher_child", fake_run_child)
    assert review.run_git(tmp_path, ["status"], binary=binary) == expected
    assert observed == {
        "argv": ("git", "status"),
        "cwd": tmp_path,
        "purpose": f"git-status-{'binary' if binary else 'text'}",
        "expected_return_code": 0,
        "expected_executable_sha256": None,
    }


def test_publisher_console_emission_failure_is_non_transactional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt_print(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.print", interrupt_print)
    review._best_effort_emit_result({"durable_evidence_complete": True})


def test_independent_executable_pin_accepts_stable_hardlink_and_repeated_readback(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "trusted.exe"
    hardlink = tmp_path / "trusted-hardlink.exe"
    executable.write_bytes(b"stable-hardlinked-executable")
    try:
        os.link(executable, hardlink)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    expected_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()

    first = review.validate_independent_executable_pin(
        hardlink,
        expected_sha256,
        label="trusted_hardlink",
    )
    second = review.validate_independent_executable_pin(
        hardlink,
        expected_sha256,
        label="trusted_hardlink_readback",
    )

    assert first["sha256"] == second["sha256"] == expected_sha256
    assert first["identity"] == second["identity"]
    assert first["identity"]["link_count"] >= 2
    assert first["identity_stable_across_sha256"] is True
    assert first["hardlink_allowed_with_launch_lock"] is True
    assert first["launch_lock_contract"] == "FILE_SHARE_READ_only_through_CreateProcessW"


@pytest.mark.parametrize("field", ["st_ino", "st_nlink", "st_size"])
def test_independent_executable_pin_rejects_identity_or_metadata_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
) -> None:
    executable = tmp_path / "trusted.exe"
    executable.write_bytes(b"stable-executable")
    expected_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    real_fstat = os.fstat
    calls = 0

    def drifting_fstat(file_descriptor: int) -> os.stat_result | SimpleNamespace:
        nonlocal calls
        observed = real_fstat(file_descriptor)
        calls += 1
        if calls != 2:
            return observed
        fields = {
            "st_dev": observed.st_dev,
            "st_ino": observed.st_ino,
            "st_mode": observed.st_mode,
            "st_nlink": observed.st_nlink,
            "st_size": observed.st_size,
            "st_mtime_ns": observed.st_mtime_ns,
            "st_ctime_ns": observed.st_ctime_ns,
            "st_file_attributes": getattr(observed, "st_file_attributes", 0),
            "st_reparse_tag": getattr(observed, "st_reparse_tag", 0),
        }
        fields[field] += 1
        return SimpleNamespace(**fields)

    monkeypatch.setattr(review.os, "fstat", drifting_fstat)

    with pytest.raises(review.ReviewPublisherError, match="identity_changed_during_sha256"):
        review.validate_independent_executable_pin(
            executable,
            expected_sha256,
            label="drifting_executable",
        )


def test_publisher_child_independent_pin_mismatch_runs_no_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "trusted.exe"
    executable.write_bytes(b"trusted")
    calls: list[object] = []
    monkeypatch.setattr(
        review, "WindowsJobProcessRunner", lambda *_args, **_kwargs: calls.append(1)
    )

    with pytest.raises(review.ReviewPublisherError, match="independent_pin_mismatch"):
        review._run_publisher_child(
            (str(executable), "--version"),
            cwd=tmp_path,
            purpose="independent-pin-mutation",
            expected_executable_sha256="0" * 64,
        )

    assert calls == []


def test_publisher_child_clean_requires_exact_job_accounting() -> None:
    command = (str((Path.cwd() / "trusted-tool").resolve()),)
    executable_sha256 = "3" * 64
    run_uuid = "10000000-0000-4000-8000-000000000101"
    identity = SimpleNamespace(
        pid=101,
        creation_time_ns=202,
        image=command[0],
        run_uuid=run_uuid,
        observed_sequence=4,
    )
    events = tuple(
        SimpleNamespace(sequence=sequence, event=event, pid=pid)
        for sequence, event, pid in (
            (1, "job_created", None),
            (2, "root_created_suspended", 101),
            (3, "job_membership_verified", 101),
            (4, "identity_observed", 101),
            (5, "root_resumed", 101),
            (7, "active_process_count_zero", None),
            (9, "streams_drained", None),
        )
    )
    running = SimpleNamespace(
        sequence=1,
        monotonic_ns=1,
        total_processes=1,
        active_processes=1,
        total_terminated_processes=0,
        active_pids=(101,),
    )
    complete = SimpleNamespace(
        sequence=2,
        monotonic_ns=2,
        total_processes=1,
        active_processes=0,
        total_terminated_processes=0,
        active_pids=(),
    )
    outcome = SimpleNamespace(
        timed_out=False,
        cancelled=False,
        manual_intervention_required=False,
        stdout_drained=True,
        stderr_drained=True,
        streams_drained=True,
        active_process_zero=True,
        identity_coverage_complete=True,
        safe_for_followup=True,
        return_code=0,
        final_active_process_count=0,
        forced_termination_attempts=0,
        job_limit_flags=0,
        executable_identity={
            "path": command[0],
            "sha256": executable_sha256,
            "bytes": 1,
            "device": 1,
            "file_id": 101,
            "expected_sha256": executable_sha256,
            "pin_required": True,
            "pin_match": True,
            "measurement_scope": "immediately_before_CreateProcessW",
            "handle_lock_held_through_create": True,
            "handle_lock_share_mode": "FILE_SHARE_READ_only",
            "handle_lock_inheritable": False,
            "pre_kernel_create_gate_required": True,
            "pre_kernel_create_gate_passed": True,
            "pre_kernel_create_gate_invocations": 1,
            "pre_kernel_remaining_seconds": 300.0,
            "pre_kernel_required_seconds": 270.25,
        },
        stream_capture_limit_bytes=review.DEFAULT_MAX_STREAM_BYTES,
        stdout_total_bytes=0,
        stderr_total_bytes=0,
        stdout_capture_overflow=False,
        stderr_capture_overflow=False,
        stdout="",
        stderr="",
        residual_pids=(),
        errors=(),
        command=command,
        run_uuid=run_uuid,
        identities=(identity,),
        events=events,
        accounting=(running, complete),
    )
    assert (
        review._publisher_child_clean(
            outcome,
            expected_return_code=0,
            expected_executable_sha256=executable_sha256,
        )
        is True
    )
    complete.active_pids = (999,)
    assert (
        review._publisher_child_clean(
            outcome,
            expected_return_code=0,
            expected_executable_sha256=executable_sha256,
        )
        is False
    )


def test_publisher_child_summary_redacts_streams_and_declares_no_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = review._redacted_publisher_child_evidence(
        {"stdout": "secret", "stderr": "", "run_uuid": "run"},
        purpose="test",
        clean=True,
        environment_commitment=_environment_commitment(),
        expected_executable_sha256="3" * 64,
        secret_like_output_detected=False,
    )
    entry["execution_sequence"] = 1
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [entry])
    summary = review.publisher_child_containment_summary()
    assert summary["all_children_cleanly_contained"] is True
    assert summary["subprocess_timeout_force_kill_calls"] == 0
    assert summary["terminate_job_object_calls"] == 0
    assert summary["kill_on_job_close"] is False
    assert summary["terminal_observation"] is False
    assert summary["execution_sequence_complete"] is True
    assert "stdout" not in summary["children"][0]["process"]
    terminal = review.publisher_child_containment_observation(terminal=True)
    assert terminal["terminal_observation"] is True
    assert terminal["all_children_cleanly_contained"] is True
    assert summary["children"][0]["process"]["stdout_persisted"] is False


def test_publisher_child_attempt_is_registered_before_base_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class InterruptingRunner:
        def __init__(self, _contract: review.TimeoutContract) -> None:
            pass

        def run(self, *_args: object, **_kwargs: object) -> object:
            raise KeyboardInterrupt("sensitive publisher interruption")

    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    monkeypatch.setattr(review, "WindowsJobProcessRunner", InterruptingRunner)
    executable = Path(sys.executable).resolve()

    with pytest.raises(KeyboardInterrupt):
        review._run_publisher_child(
            (str(executable), "--version"),
            cwd=tmp_path,
            purpose="interrupted-child",
            expected_executable_sha256=review.sha256_file(executable),
        )

    observation = review.publisher_child_containment_observation(terminal=True)
    assert observation["child_count"] == 1
    assert observation["execution_sequence_complete"] is True
    assert observation["all_children_cleanly_contained"] is False
    assert observation["children"][0]["purpose"] == "interrupted-child"
    assert observation["children"][0]["process"]["terminal_process_evidence_recorded"] is False
    assert "sensitive publisher interruption" not in str(observation)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object qualification")
def test_publisher_child_resolves_relative_root_in_real_windows_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    outcome = review._run_publisher_child(
        (
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::Out.Write('publisher-job-ok')",
        ),
        cwd=tmp_path,
        purpose="relative-root-qualification",
    )
    assert outcome.stdout == "publisher-job-ok"
    assert Path(outcome.command[0]).is_absolute()
    summary = review.publisher_child_containment_summary()
    assert summary["child_count"] == 1
    assert summary["all_children_cleanly_contained"] is True


def test_code_summary_requires_raw_unobserved_live_call_telemetry_and_no_go(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    assert review.validate_code_summary(value, **kwargs) == value
    value["live_call_telemetry"]["payload"]["counts"]["r8"] = 0
    with pytest.raises(review.ReviewPublisherError, match="telemetry_unobserved"):
        review.validate_code_summary(value, **kwargs)

    for leaf, field, replacement, error in (
        (
            "role",
            "work_order_binding_role",
            "python_host",
            "validation_tool_work_order_role_mismatch",
        ),
        (
            "binding-sha",
            "work_order_binding_sha256",
            "f" * 64,
            "validation_tool_work_order_binding_mismatch",
        ),
        (
            "module-sha",
            "work_order_module_binding_sha256",
            "e" * 64,
            "validation_tool_work_order_binding_mismatch",
        ),
    ):
        tampered, tampered_kwargs = _code_summary(monkeypatch, tmp_path / leaf)
        tampered["command_plan"]["commands"][0]["tool"][field] = replacement
        _repin_command_plan(tampered)
        tampered_kwargs["expected_summary_sha256"] = hashlib.sha256(
            review.canonical_json_bytes(tampered)
        ).hexdigest()
        with pytest.raises(review.ReviewPublisherError, match=error):
            review.validate_code_summary(tampered, **tampered_kwargs)


def test_validation_handoff_replay_and_self_consistent_local_repin_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    work_order_path = Path(kwargs["external_work_order"])
    work_order = review.read_json_mapping(work_order_path, "external_work_order")
    work_order["handoff_challenge_sha256"] = "d" * 64
    tampered_raw = review.canonical_json_bytes(work_order)
    work_order_path.write_bytes(tampered_raw)
    value["handoff_challenge_sha256"] = "d" * 64
    value["external_work_order_binding"]["payload"] = work_order
    value["external_work_order_binding"]["bytes"] = len(tampered_raw)
    value["external_work_order_binding"]["sha256"] = hashlib.sha256(tampered_raw).hexdigest()
    kwargs["expected_summary_sha256"] = hashlib.sha256(
        review.canonical_json_bytes(value)
    ).hexdigest()
    with pytest.raises(review.ReviewPublisherError, match="external_work_order_invalid"):
        review.validate_code_summary(value, **kwargs)

    fresh, fresh_kwargs = _code_summary(monkeypatch, tmp_path / "production")
    with pytest.raises(review.ReviewPublisherError, match="separate_external_authority"):
        review.validate_code_summary(
            fresh,
            **fresh_kwargs,
            production_authority_verified=True,
        )


def test_external_worm_replay_consume_boundary_is_exact_and_fail_closed() -> None:
    class Adapter:
        authority_scope = "production_external_worm"
        authority_verified = True
        worm_storage = True
        calls = 0

        def consume_once(self, replay_key: str, summary_sha256: str) -> dict[str, object]:
            self.calls += 1
            return {
                "status": "consumed",
                "replay_key": replay_key,
                "summary_sha256": summary_sha256,
                "authority_scope": self.authority_scope,
                "worm_storage": self.worm_storage,
            }

    replay_key = "a" * 64
    summary_sha256 = "b" * 64
    with pytest.raises(review.ReviewPublisherError, match="adapter_unprovisioned"):
        review.consume_replay_once(
            None,
            replay_key=replay_key,
            summary_sha256=summary_sha256,
        )
    adapter = Adapter()
    with pytest.raises(review.ReviewPublisherError, match="adapter_unprovisioned"):
        review.consume_replay_once(
            adapter,
            replay_key=replay_key,
            summary_sha256=summary_sha256,
        )
    assert adapter.calls == 0
    receipt = review._consume_replay_once_for_test(
        adapter,
        replay_key=replay_key,
        summary_sha256=summary_sha256,
    )
    assert receipt["authority_scope"] == "test_only_internal_non_authoritative"
    assert receipt["authority_verified"] is False
    assert receipt["production_go_enabled"] is False
    assert receipt["adapter_receipt"]["status"] == "consumed"
    assert adapter.calls == 1


def test_production_entry_guard_precedes_project_import_and_requires_no_site() -> None:
    source = Path(review.__file__).read_text(encoding="utf-8")
    guard = source.index("review_requires_python_I_B_S_before_project_import")
    first_project_import = source.index("from evm.scale_validation")
    assert guard < first_project_import
    assert "sys.flags.no_site != 1" in source[:first_project_import]
    outer = (
        Path(review.__file__).with_name("invoke_pre_r8_r7s7_review.ps1").read_text(encoding="utf-8")
    )
    assert "-I -B -S -X" in outer
    assert "[IO.FileShare]::Read" in outer
    assert "ComputeHash($Stream)" in outer
    assert "trusted_outer_module_origin_mismatch" in outer
    assert "publisher.parents[2] != root" in outer
    assert "python_site_packages_work_order_binding_ambiguous" in outer
    assert "*stdlib_paths, str(root / 'src'), str(root), str(site_packages)" in outer
    assert "external_work_order_code_file_binding_set_not_exact" in outer
    assert "$CodeLocks += $Lock" in outer
    assert "python_tool_content_relative_path_invalid" in outer
    assert "python_tool_content_duplicate_path" in outer
    assert "python_tool_content_path_escape" in outer
    assert "python_tool_content_aggregate_mismatch" in outer
    assert "$ToolContentLocks += $ContentLock" in outer
    assert "GetFinalPathNameByHandle" in outer
    assert "GetFileInformationByHandle" in outer
    assert "Add-RetainedDirectoryChain" in outer
    assert "Assert-RetainedPinnedFileUnchanged" in outer
    assert "external_work_order_code_file_role_path_mismatch" in outer
    assert "ls-files --others --exclude-standard -z" in outer
    assert "ls-files --others --ignored --exclude-standard -z" in outer
    assert outer.count("ls-files --others --ignored --exclude-standard -z") == 2
    assert "prelaunch_import_shadow_forbidden" in outer
    assert "internal_unproven_runtime_closure_contract_invalid" in outer
    assert "__evm_internal_non_authoritative_outer__" in outer
    assert "outer_invocation_authority_unproven=True" in outer
    assert "pyproject.toml" in outer
    for required_binding in (
        "evm_init",
        "scale_validation_init",
        "phase_b2_r7s3_handle_io",
        "phase_b2_r7s4_authority",
        "phase_b2_r7s4_evidence",
        "phase_b2_r7s5_evidence",
    ):
        assert f"'{required_binding}'" in outer
    assert "ConvertFrom-Json" in outer


def test_trusted_outer_streams_python_source_over_stdin() -> None:
    outer = (
        Path(review.__file__).with_name("invoke_pre_r8_r7s7_review.ps1").read_text(encoding="utf-8")
    )
    assert "-c $SiteIdentityScript" not in outer
    assert "-c $Bootstrap" not in outer
    assert (
        '$SiteIdentityScript | & $Python -I -B -S -X "pycache_prefix=$PycachePrefix" - '
        "$SitePackages 2>&1"
    ) in outer
    assert (
        '$Bootstrap | & $Python -I -B -S -X "pycache_prefix=$PycachePrefix" - '
        "$Root $Publisher $Runner $SitePackages $PycachePrefix @BoundPublisherArguments"
    ) in outer
    assert "$SiteIdentityExitCode = $global:LASTEXITCODE" in outer
    assert "$PublisherExitCode = $global:LASTEXITCODE" in outer
    assert outer.count("$global:LASTEXITCODE = $null") == 2
    assert "$LASTEXITCODE = $null" not in outer.replace("$global:LASTEXITCODE = $null", "")
    assert "python_site_packages_identity_process_not_started" in outer
    assert "trusted_outer_publisher_process_not_started" in outer
    assert outer.count("$ErrorActionPreference = 'Continue'") == 2
    assert outer.count("$ErrorActionPreference = $SavedErrorActionPreference") == 2
    assert outer.count("$OutputEncoding = [Text.UTF8Encoding]::new($false)") == 2
    assert outer.count("$OutputEncoding = $SavedOutputEncoding") == 2
    assert "python_site_packages_identity_source_must_be_ascii" in outer
    assert "trusted_outer_bootstrap_source_must_be_ascii" in outer
    site_invoke = outer.index("$SiteIdentityRaw = @(")
    site_reset = outer.rindex("$global:LASTEXITCODE = $null", 0, site_invoke)
    site_capture = outer.index("$SiteIdentityExitCode = $global:LASTEXITCODE", site_invoke)
    site_null_gate = outer.index("$null -eq $SiteIdentityExitCode", site_capture)
    assert site_reset < site_invoke < site_capture < site_null_gate
    publisher_invoke = outer.index("$Bootstrap | & $Python")
    publisher_reset = outer.rindex("$global:LASTEXITCODE = $null", 0, publisher_invoke)
    publisher_capture = outer.index("$PublisherExitCode = $global:LASTEXITCODE", publisher_invoke)
    publisher_null_gate = outer.index("$null -eq $PublisherExitCode", publisher_capture)
    assert publisher_reset < publisher_invoke < publisher_capture < publisher_null_gate


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell native-pipeline semantics")
def test_windows_powershell_preserves_multiline_python_source_over_stdin() -> None:
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    script = r"""& {
$ErrorActionPreference = 'Stop'
$Python = $env:EVM_TEST_PINNED_PYTHON
$OutputEncoding = [Text.UnicodeEncoding]::new($false, $false)
$Source = @'
import json
value = {"quoted": "value with spaces", "items": [1, 2]}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
'@
$SavedErrorActionPreference = $ErrorActionPreference
$SavedOutputEncoding = $OutputEncoding
$ErrorActionPreference = 'Continue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $global:LASTEXITCODE = $null
    $Output = @($Source | & $Python -I -B -S - 2>&1)
    $ExitCode = $global:LASTEXITCODE
}
finally {
    $OutputEncoding = $SavedOutputEncoding
    $ErrorActionPreference = $SavedErrorActionPreference
}
if ($null -eq $ExitCode -or $ExitCode -ne 0 -or $Output.Count -ne 1) {
    exit 91
}
$FailureSource = @'
import sys
sys.stderr.write("expected-stderr\n")
raise SystemExit(37)
'@
$SavedErrorActionPreference = $ErrorActionPreference
$SavedOutputEncoding = $OutputEncoding
$ErrorActionPreference = 'Continue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $global:LASTEXITCODE = $null
    $FailureOutput = @($FailureSource | & $Python -I -B -S - 2>&1)
    $FailureExitCode = $global:LASTEXITCODE
}
finally {
    $OutputEncoding = $SavedOutputEncoding
    $ErrorActionPreference = $SavedErrorActionPreference
}
$FailureText = [string]::Join("`n", @($FailureOutput | ForEach-Object { [string]$_ }))
if ($FailureExitCode -ne 37 -or $FailureText -notmatch 'expected-stderr') {
    exit 92
}
$MissingExecutable = Join-Path $env:TEMP ('evm-missing-' + [Guid]::NewGuid().ToString('N') + '.exe')
$global:LASTEXITCODE = 0
$MissingFailedClosed = $false
try {
    $SavedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $global:LASTEXITCODE = $null
        $MissingOutput = @(& $MissingExecutable 2>&1)
        $MissingExitCode = $global:LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $SavedErrorActionPreference
    }
    if ($null -eq $MissingExitCode) {
        $MissingFailedClosed = $true
    }
}
catch {
    $MissingFailedClosed = $true
}
if (-not $MissingFailedClosed) {
    exit 93
}
if ($ErrorActionPreference -cne 'Stop') {
    exit 94
}
if ($OutputEncoding.CodePage -ne 1200) {
    exit 95
}
[Console]::Out.WriteLine([string]$Output[0])
}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    environment = dict(os.environ)
    environment["EVM_TEST_PINNED_PYTHON"] = str(Path(sys.executable).resolve())
    completed = subprocess.run(
        (str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"items": [1, 2], "quoted": "value with spaces"}


def test_env_spoof_cannot_reach_publication_or_child_dispatch(tmp_path: Path) -> None:
    publisher_path = Path(review.__file__).resolve()
    pycache_prefix = tmp_path / "must-remain-absent"
    output = tmp_path / "must-not-be-created"
    environment = dict(os.environ)
    environment["EVM_PRE_R8_REVIEW_ENTRY_AUTHORITY_SCOPE"] = (
        "trusted_outer_internal_non_authoritative"
    )
    completed = subprocess.run(
        (
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={pycache_prefix}",
            str(publisher_path),
            "--output-leaf",
            str(output),
        ),
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "review_os_bound_outer_capability_unprovisioned" in (completed.stdout + completed.stderr)
    assert not output.exists()
    assert not pycache_prefix.exists()
    with pytest.raises(review.ReviewPublisherError, match="capability_unprovisioned"):
        review.main([])
    with pytest.raises(review.ReviewPublisherError, match="unproven_latch_required"):
        review._main_internal_non_authoritative([], outer_invocation_authority_unproven=False)


def test_git_ignored_nul_inventory_detects_root_scripts_shadow(tmp_path: Path) -> None:
    git = runner._resolved_executable("git.exe")
    subprocess.run((str(git), "init", "-q"), cwd=tmp_path, check=True)
    (tmp_path / ".git" / "info" / "exclude").write_text("scripts.py\n", encoding="utf-8")
    (tmp_path / "scripts.py").write_text("raise RuntimeError('shadow')\n", encoding="utf-8")
    ordinary = subprocess.run(
        (str(git), "ls-files", "--others", "--exclude-standard", "-z", "--", ".", "src"),
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    ignored = subprocess.run(
        (
            str(git),
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
            "--",
            ".",
            "src",
        ),
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    assert b"scripts.py\0" not in ordinary
    assert b"scripts.py\0" in ignored


@pytest.mark.skipif(os.name != "nt", reason="trusted outer is Windows PowerShell")
def test_trusted_outer_rejects_self_consistent_arbitrary_site_packages_path(
    tmp_path: Path,
) -> None:
    outer = Path(review.__file__).with_name("invoke_pre_r8_r7s7_review.ps1").resolve()
    publisher_path = Path(review.__file__).resolve()
    runner_path = Path(runner.__file__).resolve()
    python_path = Path(sys.executable).resolve()
    powershell = runner._resolved_executable("powershell.exe")
    work_order = {
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "immutable_checkout_namespace_authority": False,
        "runtime_stdlib_native_closure_verified": False,
        "validation_run_uuid": "10000000-0000-4000-8000-000000000001",
        "pycache_prefix": str(
            tmp_path.parent / ".pre-r8-r7s7-pycache-10000000-0000-4000-8000-000000000001"
        ),
        "tool_file_bindings": {
            "python_general": {
                "path": str(python_path),
                "site_packages": {
                    "path": str(tmp_path.resolve()),
                    "device": 0,
                    "file_id": 0,
                    "creation_time_ns": 0,
                    "pth_processing": "disabled_by_python_no_site",
                },
            }
        },
    }
    work_order_path = tmp_path / "self-consistent-arbitrary-site.json"
    work_order_raw = review.canonical_json_bytes(work_order)
    work_order_path.write_bytes(work_order_raw)
    invocation = (
        str(powershell),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(outer),
        "-ExpectedOuterSha256",
        review.sha256_file(outer),
        "-PythonPath",
        str(python_path),
        "-PythonSha256",
        review.sha256_file(python_path),
        "-PowerShellSha256",
        review.sha256_file(powershell),
        "-PublisherPath",
        str(publisher_path),
        "-PublisherSha256",
        review.sha256_file(publisher_path),
        "-RunnerPath",
        str(runner_path),
        "-RunnerSha256",
        review.sha256_file(runner_path),
        "-ProjectRoot",
        str(review.SCRIPT_PROJECT_ROOT),
        "-ExternalWorkOrder",
        str(work_order_path),
        "-ExternalWorkOrderSha256",
        hashlib.sha256(work_order_raw).hexdigest(),
    )
    completed = subprocess.run(
        invocation,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "python_site_packages_not_derived_from_pinned_python" in (
        completed.stdout + completed.stderr
    )


@pytest.mark.skipif(os.name != "nt", reason="trusted outer is Windows PowerShell")
def test_trusted_outer_accepts_exact_tool_content_inventory_before_publisher_dispatch(
    tmp_path: Path,
) -> None:
    outer = Path(review.__file__).with_name("invoke_pre_r8_r7s7_review.ps1").resolve()
    publisher_path = Path(review.__file__).resolve()
    runner_path = Path(runner.__file__).resolve()
    python_path = Path(sys.executable).resolve()
    powershell = runner._resolved_executable("powershell.exe")
    git = runner._resolved_executable("git.exe")
    batch = tmp_path / "internal-inputs"
    batch.mkdir()
    run_uuid = "50000000-0000-4000-8000-000000000005"
    python_sha = review.sha256_file(python_path)
    tool_paths = {
        "python_general": python_path,
        "python_host": python_path,
        "python_ruff": python_path,
        "git": git,
        "powershell": powershell,
    }
    work_order = {
        "schema": runner.WORK_ORDER_SCHEMA,
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "immutable_checkout_namespace_authority": False,
        "runtime_stdlib_native_closure_verified": False,
        "validation_run_uuid": run_uuid,
        "pycache_prefix": str(tmp_path / f".pre-r8-r7s7-pycache-{run_uuid}"),
        "tool_file_bindings": {
            name: runner.work_order_tool_binding(
                name, path, python_sha if name.startswith("python_") else review.sha256_file(path)
            )
            for name, path in tool_paths.items()
        },
        "code_file_bindings": runner.work_order_code_file_bindings(
            outer, review.sha256_file(outer)
        ),
    }
    work_order_path = batch / "external-work-order.json"
    raw = review.canonical_json_bytes(work_order)
    work_order_path.write_bytes(raw)
    invocation = (
        str(powershell),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(outer),
        "-ExpectedOuterSha256",
        review.sha256_file(outer),
        "-PythonPath",
        str(python_path),
        "-PythonSha256",
        python_sha,
        "-PowerShellSha256",
        review.sha256_file(powershell),
        "-PublisherPath",
        str(publisher_path),
        "-PublisherSha256",
        review.sha256_file(publisher_path),
        "-RunnerPath",
        str(runner_path),
        "-RunnerSha256",
        review.sha256_file(runner_path),
        "-ProjectRoot",
        str(review.SCRIPT_PROJECT_ROOT),
        "-ExternalWorkOrder",
        str(work_order_path),
        "-ExternalWorkOrderSha256",
        hashlib.sha256(raw).hexdigest(),
    )
    completed = subprocess.run(
        invocation,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "python_tool_content_aggregate_mismatch" not in combined
    assert "python_tool_content_file_count_mismatch" not in combined
    assert "external_work_order_code_file_binding_set_not_exact" not in combined
    assert (
        "preimport_untracked_import_shadow_forbidden" in combined
        or "the following arguments are required" in combined
        or "review_os_bound_outer_capability_unprovisioned" in combined
    )

    shrunken = json.loads(json.dumps(work_order))
    shrunken["tool_file_bindings"]["python_general"]["python_tool_module"][
        "dependency_distributions"
    ].pop()
    shrunken_raw = review.canonical_json_bytes(shrunken)
    work_order_path.write_bytes(shrunken_raw)
    shrunken_invocation = (*invocation[:-1], hashlib.sha256(shrunken_raw).hexdigest())
    shrunken_result = subprocess.run(
        shrunken_invocation,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shrunken_result.returncode != 0
    assert "python_tool_dependency_closure_not_exact" in (
        shrunken_result.stdout + shrunken_result.stderr
    )

    work_order["code_file_bindings"]["evm_init"] = dict(
        work_order["code_file_bindings"]["publisher"]
    )
    tampered_raw = review.canonical_json_bytes(work_order)
    work_order_path.write_bytes(tampered_raw)
    tampered_invocation = (*invocation[:-1], hashlib.sha256(tampered_raw).hexdigest())
    tampered = subprocess.run(
        tampered_invocation,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tampered.returncode != 0
    assert "external_work_order_code_file_role_path_mismatch" in (tampered.stdout + tampered.stderr)


def test_live_command_plan_metadata_children_are_in_terminal_publisher_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    value, kwargs = _code_summary(monkeypatch, tmp_path)

    review.validate_code_summary(value, **kwargs)

    expected_count = 6 * int(value["planned_command_count"])
    observation = review.publisher_child_containment_observation(terminal=True)
    assert observation["child_count"] == expected_count
    assert observation["all_children_cleanly_contained"] is True
    purposes = [item["purpose"] for item in observation["children"]]
    assert len(purposes) == len(set(purposes))
    metadata_purposes = [purpose for purpose in purposes if ":command_plan:" in purpose]
    inventory_purposes = [purpose for purpose in purposes if ":before:" in purpose]
    assert len(metadata_purposes) == 2 * int(value["planned_command_count"])
    assert len(inventory_purposes) == 4 * int(value["planned_command_count"])
    assert all(item["process"]["stdout_persisted"] is False for item in observation["children"])


def test_code_summary_plan_reconstruction_uses_work_order_run_specific_pycache_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    work_order_prefix = Path(value["external_work_order_binding"]["payload"]["pycache_prefix"])
    assert work_order_prefix.name.endswith(str(value["validation_run_uuid"]))
    delegated = runner.build_command_specs
    observed: dict[str, object] = {}

    def capture_specs(**call_kwargs: object) -> tuple[runner.CommandSpec, ...]:
        observed.update(call_kwargs)
        return delegated(**call_kwargs)

    monkeypatch.setattr(runner, "build_command_specs", capture_specs)
    assert review.validate_code_summary(value, **kwargs) == value
    assert observed["pycache_prefix"] == work_order_prefix


def test_live_command_plan_interruption_retains_immediate_unclean_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    specs = runner.build_command_specs(
        repository=Path(kwargs["repository"]),
        project_root=Path(kwargs["project_root"]),
        python_general=Path(kwargs["python_general"]),
        python_host=Path(kwargs["python_host"]),
        python_ruff=Path(kwargs["python_ruff"]),
        git_executable=Path(kwargs["git_executable"]),
        git_executable_sha256=str(kwargs["git_executable_sha256"]),
        powershell_executable=Path(kwargs["powershell_executable"]),
    )

    def interrupted_plan(**call_kwargs: Any) -> dict[str, Any]:
        metadata_evidence = call_kwargs["metadata_evidence"]
        before_child = call_kwargs["before_metadata_child"]
        child_name = f"tool-version-{specs[0].name}"
        before_child(child_name)
        metadata_evidence.append(
            runner._pending_metadata_evidence_record(
                name=child_name,
                phase=f"command_plan:{specs[0].name}:tool_version",
            )
        )
        raise KeyboardInterrupt("sensitive plan interruption")

    monkeypatch.setattr(runner, "command_plan", interrupted_plan)

    with pytest.raises(
        review.ReviewPublisherError,
        match="code_validation_live_plan_reconstruction_failed",
    ):
        review.validate_code_summary(value, **kwargs)

    observation = review.publisher_child_containment_observation(terminal=True)
    assert observation["child_count"] == 3
    assert observation["children"][-1]["clean_containment_verified"] is False
    assert observation["children"][-1]["process"]["terminal_process_evidence_recorded"] is False
    assert "sensitive plan interruption" not in str(observation)


def test_live_metadata_terminal_failure_is_mirrored_before_command_plan_unwind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    specs = runner.build_command_specs(
        repository=Path(kwargs["repository"]),
        project_root=Path(kwargs["project_root"]),
        python_general=Path(kwargs["python_general"]),
        python_host=Path(kwargs["python_host"]),
        python_ruff=Path(kwargs["python_ruff"]),
        git_executable=Path(kwargs["git_executable"]),
        git_executable_sha256=str(kwargs["git_executable_sha256"]),
        powershell_executable=Path(kwargs["powershell_executable"]),
    )

    def failed_plan(**call_kwargs: Any) -> dict[str, Any]:
        metadata_evidence = call_kwargs["metadata_evidence"]
        before_child = call_kwargs["before_metadata_child"]
        child_name = f"tool-version-{specs[0].name}"
        phase = f"command_plan:{specs[0].name}:tool_version"
        before_child(child_name)
        record = runner._pending_metadata_evidence_record(name=child_name, phase=phase)
        metadata_evidence.append(record)
        failure = runner.MetadataChildError(
            "synthetic detailed containment failure",
            name=child_name,
            failure_kind="process_containment_failure",
            process_evidence={
                "stdout": "",
                "stderr": "",
                "terminal_process_evidence_recorded": True,
                "manual_intervention_required": True,
                "residual_pids": [4242],
                "forced_termination_attempts": 0,
                "automatic_retry_count": 0,
            },
        )
        runner._replace_metadata_evidence_record(
            record,
            runner._failed_metadata_evidence_record(
                name=child_name,
                phase=phase,
                exc=failure,
            ),
        )
        raise failure

    monkeypatch.setattr(runner, "command_plan", failed_plan)

    with pytest.raises(
        review.ReviewPublisherError,
        match="code_validation_live_plan_reconstruction_failed",
    ):
        review.validate_code_summary(value, **kwargs)

    terminal = review.publisher_child_containment_observation(terminal=True)
    mirrored = terminal["children"][-1]
    assert mirrored["clean_containment_verified"] is False
    assert mirrored["metadata_status"] == "FAIL"
    assert mirrored["metadata_failure_kind"] == "process_containment_failure"
    assert mirrored["process"]["terminal_process_evidence_recorded"] is True
    assert mirrored["process"]["residual_pids"] == [4242]


def test_expected_primary_and_terminal_publisher_purpose_plans_are_exact() -> None:
    token = {
        "publisher_runtime": {"process": {"pid": 101}},
        "publisher_parent": {"process": {"pid": 102}},
        "codex": {"process": {"pid": 103}},
    }
    validation = {
        "command_plan": {
            "commands": [{"name": name} for name in sorted(review.REQUIRED_VALIDATION_COMMANDS)]
        }
    }

    primary = review._expected_primary_publisher_purpose_plan(
        token=token,
        validation=validation,
    )
    terminal = review._expected_terminal_publisher_purpose_plan(
        token=token,
        validation=validation,
    )

    assert len(primary) == review.EXPECTED_PRIMARY_PUBLISHER_CHILD_COUNT == 231
    assert len(terminal) == review.EXPECTED_TERMINAL_PUBLISHER_CHILD_COUNT == 243
    assert terminal[: len(primary)] == primary


def test_run_publisher_simulates_exact_primary_231_and_terminal_243_ledgers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = review.SCRIPT_PROJECT_ROOT
    repository = project_root.parent
    canonical_repository = tmp_path / "canonical"
    review_parent = tmp_path / "review-parent"
    validation_summary = tmp_path / "validation" / "code-validation-summary.json"
    token_evidence = tmp_path / "token.json"
    ci_manifest_path = tmp_path / "ci-manifest.json"
    reference_paths = [
        tmp_path / "r7s4",
        tmp_path / "r6",
        tmp_path / "etw",
        tmp_path / "ci-readback",
    ]
    for directory in (
        canonical_repository,
        review_parent,
        validation_summary.parent,
        *reference_paths,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    validation_summary.write_text("{}\n", encoding="utf-8")
    token_evidence.write_text("{}\n", encoding="utf-8")
    ci_manifest_path.write_text("{}\n", encoding="utf-8")

    head = "1" * 40
    tree = "2" * 40
    executable = Path(sys.executable).resolve()
    executable_sha256 = review.sha256_file(executable)
    empty_path_inventory_sha256 = hashlib.sha256(b"\0").hexdigest()
    empty_list_sha256 = hashlib.sha256(review.canonical_json_bytes([])).hexdigest()
    empty_untracked = {
        "count": 0,
        "regular_files": 0,
        "bytes": 0,
        "path_inventory_sha256": empty_path_inventory_sha256,
        "path_inventory_encoding": "utf-8-nul-sorted",
        "path_list_sha256": empty_list_sha256,
        "path_list_encoding": "canonical-json-sorted-paths",
        "content_inventory_sha256": empty_list_sha256,
        "content_inventory_encoding": "canonical-json-path-bytes-sha256",
        "paths_persisted_in_evidence": False,
    }
    token = {
        "publisher_runtime": {"process": {"pid": 101}},
        "publisher_parent": {"process": {"pid": 102}},
        "codex": {"process": {"pid": 103}},
    }
    command_names = sorted(review.REQUIRED_VALIDATION_COMMANDS)
    validation = {
        "status": "PASS",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "evidence_scope": "internal_non_authoritative",
        "go_evidence_eligible": False,
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
        "command_plan": {"commands": [{"name": name} for name in command_names]},
        "live_call_telemetry": {
            "sha256": "f" * 64,
            "payload": {
                "observation_state": "unknown",
                "counts": {name: None for name in review.REQUIRED_ZERO_LIVE_CALLS},
            },
        },
    }
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    monkeypatch.setattr(review, "_PUBLISHER_FAILURE_CONTEXT", {})

    def append_purpose(purpose: str) -> None:
        review._append_publisher_child_execution(
            review._redacted_publisher_child_evidence(
                {"stdout": "", "stderr": "", "simulated_integration_child": True},
                purpose=purpose,
                clean=True,
                environment_commitment=_environment_commitment(),
                expected_executable_sha256=executable_sha256,
                secret_like_output_detected=False,
            )
        )

    monkeypatch.setattr(
        review,
        "validate_independent_executable_pin",
        lambda path, expected_sha256, **_kwargs: {
            "path": str(Path(path).resolve()),
            "sha256": expected_sha256,
            "bytes": executable.stat().st_size,
        },
    )
    parent_pin = {"schema": review.REVIEW_PARENT_SCHEMA, "sha256": "a" * 64}
    monkeypatch.setattr(
        review,
        "validate_review_parent_gate",
        lambda *_args, **_kwargs: (review_parent, parent_pin),
    )
    monkeypatch.setattr(review, "require_disjoint_review_batch_namespaces", lambda *_a: None)

    def untracked_summary_stub(
        _repository: Path,
        *,
        reject_import_active: bool = False,
        purpose_context: str | None = None,
        **_kwargs: object,
    ) -> dict[str, object]:
        for purpose in review._untracked_purpose_plan(
            reject_import_active=reject_import_active,
            purpose_context=purpose_context,
        ):
            append_purpose(purpose)
        return dict(empty_untracked)

    monkeypatch.setattr(review, "untracked_summary", untracked_summary_stub)

    def git_snapshot_stub(_repository: Path, branch: str, **_kwargs: object) -> dict[str, object]:
        for purpose in review._git_snapshot_purpose_plan():
            append_purpose(purpose)
        return {
            "repository": str(canonical_repository),
            "branch": branch,
            "local_head": head,
            "origin_tracking_head": head,
            "remote_head": head,
            "tree": tree,
            "tracked_changes": 0,
        }

    monkeypatch.setattr(review, "git_snapshot", git_snapshot_stub)

    def run_git_stub(
        _repository: Path,
        arguments: list[str],
        *,
        binary: bool = False,
        purpose_context: str | None = None,
        **_kwargs: object,
    ) -> str | bytes:
        append_purpose(
            review._git_child_purpose(
                arguments[0],
                binary=binary,
                purpose_context=purpose_context,
            )
        )
        if binary:
            return b""
        if arguments[:2] == ["rev-parse", "HEAD^{tree}"]:
            return tree
        if arguments[0] == "status":
            return ""
        return head

    monkeypatch.setattr(review, "run_git", run_git_stub)

    def validate_token_stub(*_args: object, **_kwargs: object) -> dict[str, object]:
        for purpose in ("process-identity-101", "process-identity-102", "process-identity-103"):
            append_purpose(purpose)
        return token

    monkeypatch.setattr(review, "validate_token_evidence", validate_token_stub)
    monkeypatch.setattr(review, "read_json_mapping", lambda *_a, **_k: {})
    monkeypatch.setattr(
        review,
        "validate_code_validation_publication_index",
        lambda *_a, **_k: {"status": "PASS"},
    )

    def validate_code_summary_stub(*_args: object, **_kwargs: object) -> dict[str, object]:
        for purpose in review._live_command_plan_purpose_plan(command_names):
            append_purpose(purpose)
        return validation

    monkeypatch.setattr(review, "validate_code_summary", validate_code_summary_stub)

    def selected_source_stub(*_args: object, **_kwargs: object) -> dict[str, object]:
        for purpose in review._selected_source_purpose_plan():
            append_purpose(purpose)
        return {"inventory_sha256": "b" * 64, "file_count": 47, "files": []}

    monkeypatch.setattr(review, "selected_source_inventory", selected_source_stub)
    monkeypatch.setattr(
        review.ci,
        "load_and_validate_manifest",
        lambda *_a, **_k: {"remaining_blockers": []},
    )
    monkeypatch.setattr(review.ci, "load_manifest", lambda *_a, **_k: {})
    monkeypatch.setattr(
        review.gate,
        "evaluate_r7s5_gate",
        lambda **_k: SimpleNamespace(
            to_dict=lambda: {
                "decision": "NO-GO",
                "blockers": [],
                "downstream_calls": dict(review.gate.ZERO_DOWNSTREAM_CALLS),
            }
        ),
    )
    monkeypatch.setattr(review, "verify_sealed_directory", lambda _p, label: {"label": label})
    monkeypatch.setattr(review, "verify_sealed_etw_amendment", lambda _p: {"label": "etw"})
    monkeypatch.setattr(review, "verify_ci_readback", lambda *_a, **_k: {"status": "PASS"})
    monkeypatch.setattr(review, "directory_inventory", lambda _p: {"entries": []})
    monkeypatch.setattr(
        review,
        "verify_primary_inventory_readback",
        lambda *_a, **_k: {"exact_match": True},
    )
    monkeypatch.setattr(review, "_best_effort_emit_result", lambda _result: None)

    published: list[dict[str, object]] = []

    class Batch:
        def __init__(self, output_directory: Path, run_uuid: str) -> None:
            self.output_directory = output_directory
            self.run_uuid = run_uuid

        def to_dict(self) -> dict[str, object]:
            return {
                "output_directory": str(self.output_directory),
                "run_uuid": self.run_uuid,
            }

    def publish_stub(
        parent: Path,
        output_leaf: str,
        documents: dict[str, object],
        *,
        run_uuid: str,
    ) -> Batch:
        published.append({"leaf": output_leaf, "documents": documents, "run_uuid": run_uuid})
        return Batch(parent / output_leaf, run_uuid)

    monkeypatch.setattr(review.evidence, "publish_pre_serialized_batch", publish_stub)
    args = SimpleNamespace(
        repository=repository,
        project_root=project_root,
        canonical_repository=canonical_repository,
        canonical_branch="canonical",
        parent=review_parent,
        expected_parent=review_parent,
        expected_parent_sha256="a" * 64,
        output_leaf="primary",
        post_output_leaf="post",
        run_uuid="10000000-0000-4000-8000-000000000001",
        attempt_uuid="20000000-0000-4000-8000-000000000002",
        r7s4_evidence=reference_paths[0],
        r6_rca=reference_paths[1],
        etw_amendment=reference_paths[2],
        ci_readback=reference_paths[3],
        ci_manifest=ci_manifest_path,
        token_evidence=token_evidence,
        lineage_work_order=token_evidence,
        lineage_work_order_sha256=hashlib.sha256(token_evidence.read_bytes()).hexdigest(),
        validation_summary=validation_summary,
        expected_validation_summary_sha256=hashlib.sha256(
            validation_summary.read_bytes()
        ).hexdigest(),
        external_work_order=validation_summary,
        external_work_order_sha256=hashlib.sha256(validation_summary.read_bytes()).hexdigest(),
        trusted_outer=validation_summary,
        trusted_outer_sha256=hashlib.sha256(validation_summary.read_bytes()).hexdigest(),
        python_general=executable,
        python_general_sha256=executable_sha256,
        python_host=executable,
        python_host_sha256=executable_sha256,
        python_ruff=executable,
        python_ruff_sha256=executable_sha256,
        git_executable=executable,
        git_executable_sha256=executable_sha256,
        powershell_executable=executable,
        powershell_executable_sha256=executable_sha256,
        expected_untracked_count=0,
        expected_untracked_path_sha256=empty_path_inventory_sha256,
        expected_untracked_content_sha256=empty_list_sha256,
        expected_isolated_untracked_count=0,
        expected_isolated_untracked_path_list_sha256=empty_list_sha256,
        expected_isolated_untracked_content_inventory_sha256=empty_list_sha256,
    )

    assert review._run_publisher(args) == 0
    assert len(published) == 2
    primary_summary = published[0]["documents"]["publisher-child-containment-summary.json"]
    offline = published[0]["documents"]["offline-admission-decision.json"]
    reviewer_report = published[0]["documents"][review.REVIEWER_PENDING_REPORT_LEAF]
    no_go_seal = published[0]["documents"][review.NO_GO_SEAL_LEAF]
    terminal_summary = published[1]["documents"]["publisher-child-terminal-containment.json"]
    assert primary_summary["child_count"] == 231
    assert primary_summary["purpose_plan_exact"] is True
    assert terminal_summary["child_count"] == 243
    assert terminal_summary["purpose_plan_exact"] is True
    assert offline["whole_system_live_call_telemetry"]["observation_state"] == "unknown"
    assert all(
        value is None for value in offline["whole_system_live_call_telemetry"]["counts"].values()
    )
    assert offline["whole_system_live_call_telemetry"]["raw_telemetry_sha256"]
    assert offline["publisher_local_dispatch_telemetry"] == {
        "observation_scope": "this_publisher_process_only",
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
    }
    assert reviewer_report["status"] == "manual_intervention_required"
    assert reviewer_report["review_state"] == "reviewer_pending"
    assert reviewer_report["decision"] == "NO-GO"
    assert reviewer_report["credit"] == "zero_credit"
    assert reviewer_report["authority_scope"] == "internal_non_authoritative"
    assert reviewer_report["authority_verified"] is False
    assert reviewer_report["external_approval_receipt_created"] is False
    assert reviewer_report["external_worm_receipt_created"] is False
    assert reviewer_report["completion_marker_created"] is False
    assert reviewer_report["success_marker_created"] is False
    assert reviewer_report["success_index_created"] is False
    assert reviewer_report["r8_authorized"] is False
    assert all(value == "not_run" for value in reviewer_report["qualification_states"].values())
    assert reviewer_report["whole_system_live_call_telemetry"]["observation_state"] == ("unknown")
    base_documents = {
        leaf: value
        for leaf, value in published[0]["documents"].items()
        if leaf not in {review.REVIEWER_PENDING_REPORT_LEAF, review.NO_GO_SEAL_LEAF}
    }
    assert reviewer_report["bound_documents"] == {
        leaf: review._canonical_document_reference(value)
        for leaf, value in sorted(base_documents.items())
    }
    assert no_go_seal["status"] == "manual_intervention_required"
    assert no_go_seal["review_state"] == "reviewer_pending"
    assert no_go_seal["decision"] == "NO-GO"
    assert no_go_seal["credit"] == "zero_credit"
    assert no_go_seal["seal_semantics"] == (
        "append_only_reviewer_pending_no_go_not_success_evidence"
    )
    assert no_go_seal["authority_verified"] is False
    assert no_go_seal["automatic_retry_count"] == 0
    assert no_go_seal["forced_termination_attempts"] == 0
    assert no_go_seal["completion_marker_created"] is False
    assert no_go_seal["success_marker_created"] is False
    assert no_go_seal["success_index_created"] is False
    assert no_go_seal["r8_authorized"] is False
    assert no_go_seal["sealed_documents"] == {
        **reviewer_report["bound_documents"],
        review.REVIEWER_PENDING_REPORT_LEAF: review._canonical_document_reference(reviewer_report),
    }
    assert not {
        "r8_calls",
        "restore_only_calls",
        "dual_collector_calls",
        "service_lifecycle_calls",
        "force_kill_calls",
    } & set(offline)


@pytest.mark.parametrize(
    "mutation",
    ["validation_go", "validation_external_authority", "telemetry_claimed_zero"],
)
def test_explicit_reviewer_no_go_documents_reject_promotion_or_unobserved_zero_claim(
    mutation: str,
) -> None:
    validation = {
        "status": "PASS",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "evidence_scope": "internal_non_authoritative",
        "go_evidence_eligible": False,
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
    }
    telemetry = {
        "observation_state": "unknown",
        "observation_scope": "external_collector_not_provisioned",
        "counts": {name: None for name in review.REQUIRED_ZERO_LIVE_CALLS},
        "raw_telemetry_sha256": "f" * 64,
    }
    offline = {
        "status": "manual_intervention_required",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "go_evidence_eligible": False,
        "completion_marker_created": False,
        "success_marker_created": False,
        "whole_system_live_call_telemetry": telemetry,
    }
    if mutation == "validation_go":
        validation["decision"] = "GO"
    elif mutation == "validation_external_authority":
        validation["evidence_scope"] = "external_authoritative"
    else:
        telemetry["counts"] = {name: 0 for name in review.REQUIRED_ZERO_LIVE_CALLS}

    with pytest.raises(
        review.ReviewPublisherError,
        match=(
            "review_report_whole_system_telemetry_must_remain_unknown"
            if mutation == "telemetry_claimed_zero"
            else "review_report_fail_closed_source_state_required"
        ),
    ):
        review.reviewer_pending_no_go_documents(
            run_uuid="10000000-0000-4000-8000-000000000001",
            attempt_uuid="20000000-0000-4000-8000-000000000002",
            commit="1" * 40,
            tree="2" * 40,
            blockers=["external_authority_unproven"],
            base_documents={
                review.VALIDATION_SUMMARY_LEAF: validation,
                "offline-admission-decision.json": offline,
            },
        )


def test_publisher_failure_batch_redacts_exception_and_seals_terminal_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_uuid = "10000000-0000-4000-8000-000000000001"
    args = SimpleNamespace(output_leaf="primary", post_output_leaf="post")
    parent_commitment = {"schema": "parent", "sha256": "a" * 64}
    monkeypatch.setattr(
        review,
        "_PUBLISHER_FAILURE_CONTEXT",
        {
            "armed": True,
            "stage": "code_validation_live_plan",
            "parent": tmp_path,
            "parent_commitment": parent_commitment,
            "output_leaf": "failure",
            "run_uuid": run_uuid,
        },
    )
    captured: dict[str, object] = {}

    class Batch:
        def to_dict(self) -> dict[str, object]:
            return {"output_directory": str(tmp_path / "failure")}

    def publish(
        parent: Path,
        output_leaf: str,
        documents: dict[str, object],
        *,
        run_uuid: str,
    ) -> Batch:
        captured.update(
            parent=parent,
            output_leaf=output_leaf,
            documents=documents,
            run_uuid=run_uuid,
        )
        return Batch()

    monkeypatch.setattr(review.evidence, "publish_pre_serialized_batch", publish)
    result = review._publish_publisher_failure_batch(
        args,
        KeyboardInterrupt("sensitive failure text"),
    )

    report = captured["documents"]["publisher-failure-report.json"]
    assert report["failure_stage"] == "code_validation_live_plan"
    assert report["exception_type"] == "builtins.KeyboardInterrupt"
    assert report["exception_message_disclosed"] is False
    assert report["completion_marker_created"] is False
    assert result["decision"] == "NO-GO"
    assert "sensitive failure text" not in str(captured)


def test_publication_checkpoint_is_canonical_and_failure_batch_preserves_atomic_cause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_uuid = "10000000-0000-4000-8000-000000000001"
    batch_payload = {
        "output_directory": str(tmp_path / "primary"),
        "run_uuid": run_uuid,
        "document_publications": [{"path": "a.json", "sha256": "1" * 64}],
    }

    class Batch:
        def to_dict(self) -> dict[str, object]:
            return batch_payload

    class AtomicPublicationFailure(review.evidence.R7S6EvidencePublicationError):
        def __init__(self) -> None:
            RuntimeError.__init__(self, "must not be copied")

        def to_dict(self) -> dict[str, object]:
            return {
                "schema": "atomic-publication-failure",
                "status": "manual_intervention_required",
                "stage": "post_rename_verification",
                "partial_inventory": [{"path": "partial.json", "sha256": "2" * 64}],
                "failure_seal_directory": str(tmp_path / "atomic-failure"),
                "emergency_seal_directory": None,
            }

    context: dict[str, object] = {
        "armed": True,
        "stage": "postpublication_atomic_publication",
        "parent": tmp_path,
        "parent_commitment": {"sha256": "a" * 64},
        "output_leaf": "generic-failure",
        "run_uuid": run_uuid,
        "publication_state": {
            "primary": "published",
            "postpublication": "failed",
        },
    }
    monkeypatch.setattr(review, "_PUBLISHER_FAILURE_CONTEXT", context)
    review._record_publication_checkpoint("primary", Batch(), run_uuid=run_uuid)
    checkpoint = context["primary_publication_checkpoint"]
    assert isinstance(checkpoint, dict)
    expected_raw = review.canonical_json_bytes(batch_payload)
    assert checkpoint["batch"] == batch_payload
    assert checkpoint["batch_bytes"] == len(expected_raw)
    assert checkpoint["batch_sha256"] == hashlib.sha256(expected_raw).hexdigest()

    captured: dict[str, object] = {}

    class FailureBatch:
        def to_dict(self) -> dict[str, object]:
            return {"output_directory": str(tmp_path / "generic-failure")}

    def publish(
        _parent: Path,
        _leaf: str,
        documents: dict[str, object],
        *,
        run_uuid: str,
    ) -> FailureBatch:
        captured["documents"] = documents
        captured["run_uuid"] = run_uuid
        return FailureBatch()

    monkeypatch.setattr(review.evidence, "publish_pre_serialized_batch", publish)
    review._publish_publisher_failure_batch(
        SimpleNamespace(output_leaf="primary", post_output_leaf="post"),
        AtomicPublicationFailure(),
    )

    report = captured["documents"]["publisher-failure-report.json"]
    assert report["publication_state"] == context["publication_state"]
    assert report["primary_publication_checkpoint"] == checkpoint
    assert report["original_atomic_publication_failure"]["stage"] == ("post_rename_verification")
    assert report["original_atomic_publication_failure"]["partial_inventory"] == [
        {"path": "partial.json", "sha256": "2" * 64}
    ]
    assert "must not be copied" not in str(captured)


def test_publication_checkpoint_serialization_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_uuid = "10000000-0000-4000-8000-000000000001"
    context: dict[str, object] = {"armed": True}
    monkeypatch.setattr(review, "_PUBLISHER_FAILURE_CONTEXT", context)

    class BrokenBatch:
        def to_dict(self) -> dict[str, object]:
            raise KeyboardInterrupt("sensitive checkpoint failure")

    review._record_publication_checkpoint("primary", BrokenBatch(), run_uuid=run_uuid)

    checkpoint = context["primary_publication_checkpoint"]
    assert checkpoint == {
        "status": "unproven",
        "run_uuid": run_uuid,
        "exception_type": "builtins.KeyboardInterrupt",
        "exception_message_disclosed": False,
    }
    assert "sensitive checkpoint failure" not in str(checkpoint)


def test_publisher_failure_batch_marks_partial_clean_prefix_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = ["one", "two", "three"]
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    for purpose in expected[:2]:
        review._append_publisher_child_execution(
            review._redacted_publisher_child_evidence(
                {"stdout": "", "stderr": ""},
                purpose=purpose,
                clean=True,
                environment_commitment=_environment_commitment(),
                expected_executable_sha256="a" * 64,
                secret_like_output_detected=False,
            )
        )
    monkeypatch.setattr(
        review,
        "_PUBLISHER_FAILURE_CONTEXT",
        {
            "armed": True,
            "stage": "test",
            "parent": tmp_path,
            "parent_commitment": {"sha256": "a" * 64},
            "output_leaf": "failure",
            "run_uuid": "10000000-0000-4000-8000-000000000001",
            "expected_purpose_count": len(expected),
            "expected_purposes": expected,
        },
    )
    captured: dict[str, object] = {}

    class Batch:
        def to_dict(self) -> dict[str, object]:
            return {}

    def publish(
        _parent: Path,
        _leaf: str,
        documents: dict[str, object],
        *,
        run_uuid: str,
    ) -> Batch:
        captured["documents"] = documents
        captured["run_uuid"] = run_uuid
        return Batch()

    monkeypatch.setattr(review.evidence, "publish_pre_serialized_batch", publish)
    review._publish_publisher_failure_batch(
        SimpleNamespace(output_leaf="a", post_output_leaf="b"), RuntimeError()
    )

    child = captured["documents"]["publisher-failure-report.json"][
        "publisher_child_terminal_containment"
    ]
    assert child["workflow_execution_complete"] is False
    assert child["all_children_cleanly_contained"] is False
    assert child["purpose_plan_exact"] is False
    assert child["first_missing_execution_sequence"] == 3
    assert child["purpose_plan_comparison"]["exact_prefix_length"] == 2
    assert child["purpose_plan_comparison"]["first_expected_purpose"] == "three"
    assert child["purpose_plan_comparison"]["first_observed_purpose"] is None


@pytest.mark.skipif(os.name != "nt", reason="production atomic evidence writer is Windows-only")
def test_publisher_failure_batch_real_atomic_writer_readback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_uuid = "10000000-0000-4000-8000-000000000001"
    monkeypatch.setattr(review, "_PUBLISHER_CHILD_EXECUTIONS", [])
    monkeypatch.setattr(
        review,
        "_PUBLISHER_FAILURE_CONTEXT",
        {
            "armed": True,
            "stage": "real_writer_test",
            "parent": tmp_path,
            "parent_commitment": {"sha256": "a" * 64},
            "output_leaf": "publisher-failure-real-writer",
            "run_uuid": run_uuid,
            "expected_purpose_count": 1,
            "expected_purposes": ["never-executed"],
        },
    )

    result = review._publish_publisher_failure_batch(
        SimpleNamespace(output_leaf="primary", post_output_leaf="post"),
        KeyboardInterrupt("not persisted"),
    )

    output = Path(result["failure_batch"]["output_directory"])
    report_path = output / "publisher-failure-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["decision"] == "NO-GO"
    assert report["completion_marker_created"] is False
    assert report["success_marker_created"] is False
    assert report["publisher_child_terminal_containment"]["workflow_execution_complete"] is False
    assert (output / review.evidence.IDENTITY_MANIFEST_LEAF).is_file()
    assert (output / review.evidence.IDENTITY_INDEX_LEAF).is_file()
    assert not (output / "completion-marker.json").exists()
    assert "not persisted" not in report_path.read_text(encoding="utf-8")


def test_failure_seal_contracts_do_not_overclaim_pre_admission_or_tertiary_durability() -> None:
    publisher_contract = review.publisher_failure_seal_contract()
    runner_contract = runner.validation_failure_seal_contract()

    assert (
        publisher_contract["parse_and_untrusted_parent_admission_failures_durably_sealed"] is False
    )
    assert (
        publisher_contract["durable_record_after_independent_emergency_writer_failure_guaranteed"]
        is False
    )
    assert (
        runner_contract[
            "parse_path_pin_spec_and_untrusted_parent_admission_failures_durably_sealed"
        ]
        is False
    )
    assert (
        runner_contract["durable_record_after_independent_emergency_writer_failure_guaranteed"]
        is False
    )


def test_main_routes_post_admission_base_exception_to_failure_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = SimpleNamespace()
    captured: dict[str, object] = {}

    monkeypatch.setattr(review, "parse_args", lambda _argv=None: args)

    def interrupted(_args: object) -> int:
        raise KeyboardInterrupt("sensitive main interruption")

    def seal(_args: object, cause: BaseException) -> dict[str, object]:
        captured["cause_type"] = type(cause).__name__
        return {"decision": "NO-GO"}

    monkeypatch.setattr(review, "_run_publisher", interrupted)
    monkeypatch.setattr(review, "_publish_publisher_failure_batch", seal)

    with pytest.raises(KeyboardInterrupt):
        review._main_internal_non_authoritative([], outer_invocation_authority_unproven=True)

    assert captured == {"cause_type": "KeyboardInterrupt"}


def _marked_source_line(function: object, marker: str) -> int:
    source, start = inspect.getsourcelines(function)
    matches = [start + index for index, line in enumerate(source) if marker in line]
    assert len(matches) == 1
    return matches[0]


def _line_offsets(function: object, line: int) -> list[int]:
    return [
        instruction.offset
        for instruction in dis.get_instructions(function)
        if instruction.positions is not None and instruction.positions.lineno == line
    ]


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    "marker",
    ["publisher-failure-handler-continuation", "publisher-failure-dispatch-call"],
)
def test_publisher_dispatch_trace_interrupt_is_sealed_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
    marker: str,
) -> None:
    args = SimpleNamespace()
    seal_path = tmp_path / "publisher-failure.json"
    seal_calls = 0

    def failed_run(_args: object) -> int:
        raise RuntimeError("must not persist")

    def seal_once(_args: object, cause: BaseException) -> dict[str, object]:
        nonlocal seal_calls
        seal_calls += 1
        payload = {
            "status": "manual_intervention_required",
            "decision": "NO-GO",
            "exception_type": f"{type(cause).__module__}.{type(cause).__qualname__}",
            "automatic_retry_count": 0,
            "completion_marker_created": False,
            "success_marker_created": False,
        }
        with seal_path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
        return payload

    monkeypatch.setattr(review, "parse_args", lambda _argv=None: args)
    monkeypatch.setattr(review, "_run_publisher", failed_run)
    monkeypatch.setattr(review, "_publish_publisher_failure_batch", seal_once)
    monkeypatch.setattr(review, "_best_effort_emit_result", lambda _value: None)
    target_line = _marked_source_line(review._main_internal_non_authoritative, marker)

    def trace(frame: object, event: str, _arg: object) -> object:
        if (
            getattr(frame, "f_code", None) is review.main.__code__
            and event == "line"
            and getattr(frame, "f_lineno", None) == target_line
        ):
            sys.settrace(None)
            raise interrupt_type("must not persist")
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(BaseException):
            review._main_internal_non_authoritative([], outer_invocation_authority_unproven=True)
    finally:
        sys.settrace(None)

    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    assert seal_calls == 1
    assert seal["decision"] == "NO-GO"
    assert seal["automatic_retry_count"] == 0
    assert seal["completion_marker_created"] is False
    assert seal["success_marker_created"] is False
    assert len(list(tmp_path.iterdir())) == 1
    assert "must not persist" not in seal_path.read_text(encoding="utf-8")


def test_publisher_dispatch_call_and_handler_are_outer_exception_protected() -> None:
    function = review._main_internal_non_authoritative
    entries = dis.Bytecode(function).exception_entries
    dispatch_line = _marked_source_line(function, "publisher-failure-dispatch-call")
    handler_line = _marked_source_line(function, "publisher-failure-handler-continuation")

    dispatch_offsets = _line_offsets(function, dispatch_line)
    handler_offsets = _line_offsets(function, handler_line)
    call_offsets = {
        instruction.offset
        for instruction in dis.get_instructions(function)
        if instruction.opname == "CALL" and instruction.offset in dispatch_offsets
    }
    assert call_offsets
    assert handler_offsets
    for offset in (*call_offsets, *handler_offsets):
        assert any(entry.start <= offset < entry.end for entry in entries)


def test_publisher_atomic_failure_is_not_retried_after_dispatch_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = SimpleNamespace()
    seal_calls = 0
    emitted: list[dict[str, object]] = []

    class AlreadySealedFailure(review.evidence.R7S6EvidencePublicationError):
        def __init__(self) -> None:
            RuntimeError.__init__(self, "must not persist")

        def to_dict(self) -> dict[str, object]:
            return {
                "status": "manual_intervention_required",
                "failure_seal_directory": "sealed-failure",
                "emergency_seal_directory": None,
            }

    def failed_run(_args: object) -> int:
        raise RuntimeError("original failure")

    def already_sealed(_args: object, _cause: BaseException) -> dict[str, object]:
        nonlocal seal_calls
        seal_calls += 1
        raise AlreadySealedFailure()

    monkeypatch.setattr(review, "parse_args", lambda _argv=None: args)
    monkeypatch.setattr(review, "_run_publisher", failed_run)
    monkeypatch.setattr(review, "_publish_publisher_failure_batch", already_sealed)
    monkeypatch.setattr(
        review, "_best_effort_emit_result", lambda value: emitted.append(dict(value))
    )

    with pytest.raises(RuntimeError, match="original failure"):
        review._main_internal_non_authoritative([], outer_invocation_authority_unproven=True)

    assert seal_calls == 1
    assert len(emitted) == 1
    assert emitted[0]["failure_dispatch_retry_count"] == 0
    assert emitted[0]["atomic_failure_or_emergency_seal"] == {
        "status": "manual_intervention_required",
        "failure_seal_directory": "sealed-failure",
        "emergency_seal_directory": None,
    }
    assert emitted[0]["completion_marker_created"] is False


def test_code_summary_rejects_metadata_child_count_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    value["metadata_child_call_count"] -= 1

    with pytest.raises(review.ReviewPublisherError, match="metadata_children_not_exact"):
        review.validate_code_summary(value, **kwargs)


def test_code_summary_rejects_rehashed_untracked_inventory_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    _rewrite_first_command_evidence(
        value,
        lambda record: record["untracked_inventory_after"].__setitem__(
            "ignored_import_active_shadow_path_count", 1
        ),
    )

    with pytest.raises(review.ReviewPublisherError, match="untracked_inventory_mismatch"):
        review.validate_code_summary(value, **kwargs)


def test_code_summary_rejects_rehashed_noncanonical_command_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    command = value["commands"][0]
    path = Path(command["evidence_path"])
    record = json.loads(path.read_bytes())
    raw = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=False) + "\r\n").encode()
    path.write_bytes(raw)
    command["evidence_bytes"] = len(raw)
    command["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
    command["publication"] = _publication_receipt(path, raw)

    with pytest.raises(review.ReviewPublisherError, match="command_evidence_not_canonical"):
        review.validate_code_summary(value, **kwargs)


def test_validation_publication_index_binds_canonical_summary_and_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    validated = review.validate_code_summary(value, **kwargs)
    summary_path, index_path, _ = _write_validation_terminal_files(validated)

    observed = review.validate_code_validation_publication_index(
        index_path,
        summary_path=summary_path,
        validated_summary=validated,
    )

    assert observed["index_sha256"] == review.sha256_file(index_path)
    assert observed["index_self_publication_receipt_available"] is False
    assert observed["go_evidence_eligible"] is False


def test_validation_publication_index_rejects_sibling_directory_splice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    validated = review.validate_code_summary(value, **kwargs)
    _, _, index = _write_validation_terminal_files(validated)
    sibling = tmp_path / "spliced-terminal"
    sibling.mkdir()
    summary_path = sibling / review.VALIDATION_SUMMARY_LEAF
    summary_raw = review.canonical_json_bytes(validated)
    summary_path.write_bytes(summary_raw)
    index["summary"] = {
        "path": str(summary_path),
        "bytes": len(summary_raw),
        "sha256": hashlib.sha256(summary_raw).hexdigest(),
        "publication": _publication_receipt(summary_path, summary_raw),
    }
    index_path = sibling / review.VALIDATION_PUBLICATION_INDEX_LEAF
    index_path.write_bytes(review.canonical_json_bytes(index))

    with pytest.raises(review.ReviewPublisherError, match="publication_directory_splice"):
        review.validate_code_validation_publication_index(
            index_path,
            summary_path=summary_path,
            validated_summary=validated,
        )


@pytest.mark.skipif(os.name != "nt", reason="Win32 handle paths are host-specific")
def test_publication_validator_accepts_extended_length_win32_handle_path(tmp_path: Path) -> None:
    raw = b'{"receipt":"handle-bound"}\n'
    with runner._BoundValidationOutput.create(tmp_path, "receipt-output") as output:
        publication = output.publish("receipt.json", raw)
        receipt = runner._publication_receipt(publication)

        observed = review._validate_publication_receipt(
            receipt,
            expected_path=output.path / "receipt.json",
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            expected_bytes=len(raw),
        )

    assert str(observed["identity"]["final_path"]).startswith("\\\\?\\")


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda value: value["summary"]["publication"].__setitem__("directory_flush_count", 0),
            "publication_receipt_invalid",
        ),
        (
            lambda value: value.__setitem__(
                "environment_commitment", {**value["environment_commitment"], "sha256": "f" * 64}
            ),
            "publication_index_payload_mismatch",
        ),
        (
            lambda value: value.__setitem__("self_publication_receipt_embedded", True),
            "publication_index_payload_mismatch",
        ),
        (
            lambda value: value.__setitem__("metadata_child_call_count", 0),
            "publication_index_payload_mismatch",
        ),
    ],
)
def test_validation_publication_index_rejects_terminal_chain_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    validated = review.validate_code_summary(value, **kwargs)
    summary_path, index_path, index = _write_validation_terminal_files(validated)
    mutation(index)
    index_path.write_bytes(review.canonical_json_bytes(index))

    with pytest.raises(review.ReviewPublisherError, match=error):
        review.validate_code_validation_publication_index(
            index_path,
            summary_path=summary_path,
            validated_summary=validated,
        )


def test_code_summary_rejects_bool_exit_code_and_self_claimed_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    value["commands"][0]["exit_code"] = False
    value["completion_marker_created"] = True
    with pytest.raises(review.ReviewPublisherError, match="command_not_exact_pass"):
        review.validate_code_summary(value, **kwargs)


def test_code_summary_rejects_invented_command_bad_sha_and_partial_zero_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    invented, invented_kwargs = _code_summary(monkeypatch, tmp_path)
    invented["commands"][0]["name"] = "invented"
    with pytest.raises(review.ReviewPublisherError):
        review.validate_code_summary(invented, **invented_kwargs)

    bad_sha, bad_sha_kwargs = _code_summary(monkeypatch, tmp_path / "bad-sha")
    bad_sha["commands"][0]["evidence_sha256"] = "z" * 64
    with pytest.raises(review.ReviewPublisherError, match="sha256_invalid"):
        review.validate_code_summary(bad_sha, **bad_sha_kwargs)

    partial, partial_kwargs = _code_summary(monkeypatch, tmp_path / "partial")
    del partial["live_call_telemetry"]["payload"]["counts"]["r8"]
    with pytest.raises(review.ReviewPublisherError, match="telemetry_unobserved"):
        review.validate_code_summary(partial, **partial_kwargs)


def test_code_summary_rejects_stale_identity_plan_tool_and_minimal_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stale, kwargs = _code_summary(monkeypatch, tmp_path / "stale")
    stale["head"] = "4" * 40
    with pytest.raises(review.ReviewPublisherError, match="identity_mismatch"):
        review.validate_code_summary(stale, **kwargs)

    plan_tamper, kwargs = _code_summary(monkeypatch, tmp_path / "plan")
    plan_tamper["command_plan"]["commands"][0]["argv"].append("--tampered")
    _repin_command_plan(plan_tamper)
    with pytest.raises(review.ReviewPublisherError, match="static_plan_mismatch"):
        review.validate_code_summary(plan_tamper, **kwargs)

    tool_tamper, kwargs = _code_summary(monkeypatch, tmp_path / "tool")
    tool_tamper["command_plan"]["commands"][0]["tool"]["version"] = "forged"
    tool_version_process = tool_tamper["command_plan"]["commands"][0]["tool"][
        "version_process_containment"
    ]
    tool_version_process["stdout"] = "forged\n"
    tool_version_process["stdout_total_bytes"] = len(tool_version_process["stdout"].encode("utf-8"))
    _repin_command_plan(tool_tamper)
    _rewrite_first_command_evidence(
        tool_tamper,
        lambda record: record.update(
            tool=json.loads(json.dumps(tool_tamper["command_plan"]["commands"][0]["tool"])),
            command_plan_sha256=tool_tamper["command_plan_sha256"],
        ),
    )
    with pytest.raises(review.ReviewPublisherError, match="evidence_payload_mismatch"):
        review.validate_code_summary(tool_tamper, **kwargs)

    minimal, kwargs = _code_summary(monkeypatch, tmp_path / "minimal")
    first = Path(minimal["commands"][0]["evidence_path"])
    raw = review.canonical_json_bytes(
        {
            "schema": review.COMMAND_EVIDENCE_SCHEMA,
            "name": minimal["commands"][0]["name"],
            "status": "PASS",
            "exit_code": 0,
        }
    )
    first.write_bytes(raw)
    minimal["commands"][0]["evidence_bytes"] = len(raw)
    minimal["commands"][0]["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
    minimal["commands"][0]["publication"] = _publication_receipt(first, raw)
    with pytest.raises(review.ReviewPublisherError, match="evidence_keys_not_exact"):
        review.validate_code_summary(minimal, **kwargs)


def test_code_summary_recomputes_submitted_plan_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    summary["command_plan"]["commands"][0]["argv"].append("--unbound")
    with pytest.raises(review.ReviewPublisherError, match="command_plan_digest_mismatch"):
        review.validate_code_summary(summary, **kwargs)


def test_code_summary_validates_nondeterministic_metadata_containment_before_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    process = summary["command_plan"]["commands"][0]["tool"]["version_process_containment"]
    process["job_limit_flags"] = False
    _repin_command_plan(summary)
    with pytest.raises(review.ReviewPublisherError, match="integer_not_exact"):
        review.validate_code_summary(summary, **kwargs)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda tool: tool.pop("runtime_version_process_containment"),
            "tool_identity_keys_not_exact",
        ),
        (
            lambda tool: tool["version_process_containment"].update(name="forged"),
            "not_cleanly_contained",
        ),
        (
            lambda tool: tool["version_process_containment"]["command"].append("--forged"),
            "not_cleanly_contained",
        ),
        (
            lambda tool: tool["version_process_containment"]["identities"][0].update(
                image="forged-tool.exe"
            ),
            "root_image_mismatch",
        ),
        (
            lambda tool: tool["version_process_containment"].update(
                stdout="forged\n",
                stdout_total_bytes=len("forged\n".encode("utf-8")),
            ),
            "metadata_output_mismatch",
        ),
        (
            lambda tool: tool["version_process_containment"]["accounting"][-1].update(
                total_processes=2,
                total_terminated_processes=2,
            ),
            "identity_coverage_mismatch",
        ),
    ],
)
def test_code_summary_rejects_metadata_job_evidence_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
    error: str,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    tool = summary["command_plan"]["commands"][0]["tool"]
    mutation(tool)
    _repin_command_plan(summary)
    with pytest.raises(review.ReviewPublisherError, match=error):
        review.validate_code_summary(summary, **kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timed_out", True),
        ("manual_intervention_required", True),
        ("residual_pids", [4321]),
        ("streams_drained", False),
        ("active_process_zero", False),
        ("identity_coverage_complete", False),
        ("forced_termination_attempts", 1),
    ],
)
def test_code_summary_rejects_containment_failure_even_when_evidence_is_rehashed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    _rewrite_first_command_evidence(
        summary,
        lambda record: record["process_containment"].__setitem__(field, value),
    )
    with pytest.raises(
        review.ReviewPublisherError,
        match="boolean_not_exact|integer_not_exact|not_cleanly_contained",
    ):
        review.validate_code_summary(summary, **kwargs)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("timed_out", 0, "boolean_not_exact"),
        ("job_limit_flags", False, "integer_not_exact"),
    ],
)
def test_code_summary_rejects_numeric_bool_process_claims(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
    error: str,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    _rewrite_first_command_evidence(
        summary,
        lambda record: record["process_containment"].__setitem__(field, value),
    )
    with pytest.raises(review.ReviewPublisherError, match=error):
        review.validate_code_summary(summary, **kwargs)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda record: record["process_containment"]["accounting"][-1].update(
                total_processes=2,
                total_terminated_processes=2,
            ),
            "identity_coverage_mismatch",
        ),
        (
            lambda record: record["process_containment"]["events"][4].update(pid=9999),
            "root_pid_event_mismatch",
        ),
        (
            lambda record: record["process_containment"]["identities"][0].update(
                observed_sequence=3
            ),
            "root_identity_sequence_mismatch",
        ),
        (
            lambda record: record["process_containment"]["identities"][0].update(
                image="substituted-tool.exe"
            ),
            "root_image_mismatch",
        ),
        (
            lambda record: record["process_containment"]["accounting"][-1].update(sequence=10),
            "global_sequence_invalid",
        ),
        (
            lambda record: record["process_containment"]["identities"][0].update(ppid=0),
            "identity_invalid",
        ),
        (
            lambda record: record["process_containment"]["identities"].append(
                {
                    **record["process_containment"]["identities"][0],
                    "pid": 102,
                    "creation_time_ns": 1_000_102,
                }
            ),
            "identity_sequence_reused",
        ),
        (
            lambda record: record["process_containment"]["events"][2].update(details={}),
            "job_membership_details_invalid",
        ),
        (
            lambda record: record["process_containment"]["events"].append(
                {
                    "sequence": 10,
                    "event": "identity_observed",
                    "monotonic_ns": 10,
                    "timestamp_utc": "2026-09-02T00:00:01+00:00",
                    "pid": record["process_containment"]["identities"][0]["pid"],
                    "details": {},
                }
            ),
            "identity_event_binding_mismatch",
        ),
    ],
)
def test_code_summary_recomputes_process_identity_and_event_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    _rewrite_first_command_evidence(summary, mutation)
    with pytest.raises(review.ReviewPublisherError, match=error):
        review.validate_code_summary(summary, **kwargs)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda record: record["process_containment"]["accounting"][0].update(
                active_processes=0
            ),
            "accounting_relationship_invalid",
        ),
        (
            lambda record: record["process_containment"]["accounting"][0].update(
                active_pids=[9999]
            ),
            "accounting_identity_mismatch",
        ),
        (
            lambda record: record["process_containment"]["accounting"][0].update(total_processes=2),
            "accounting_relationship_invalid",
        ),
        (
            lambda record: record["process_containment"]["accounting"][-1].update(monotonic_ns=1),
            "monotonic_order_invalid",
        ),
        (
            lambda record: record["process_containment"]["events"][4].update(
                timestamp_utc="2026-09-02T00:00:01+00:00"
            ),
            "wall_clock_order_invalid",
        ),
        (
            lambda record: record.update(started_at_utc="2026-09-02T00:00:00.5Z"),
            "command_process_bracket_invalid",
        ),
    ],
)
def test_code_summary_rejects_process_accounting_and_time_relationship_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    _rewrite_first_command_evidence(summary, mutation)
    with pytest.raises(review.ReviewPublisherError, match=error):
        review.validate_code_summary(summary, **kwargs)


def test_code_summary_cross_binds_process_stream_hash_bytes_and_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    _rewrite_first_command_evidence(
        summary,
        lambda record: record["process_containment"].update(
            stdout="required-output-token\nforged\n"
        ),
    )
    with pytest.raises(review.ReviewPublisherError, match="stream_binding_mismatch"):
        review.validate_code_summary(summary, **kwargs)


def test_code_summary_rechecks_required_output_after_self_consistent_stream_repin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)

    def remove_required_token(record: dict[str, Any]) -> None:
        record["process_containment"]["stdout"] = ""
        record["process_containment"]["stdout_total_bytes"] = 0
        record["stdout_bytes"] = 0
        record["stdout_sha256"] = hashlib.sha256(b"").hexdigest()
        record["stdout_tail"] = ""

    _rewrite_first_command_evidence(summary, remove_required_token)
    with pytest.raises(review.ReviewPublisherError, match="required_output_token_missing"):
        review.validate_code_summary(summary, **kwargs)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda process: process["executable_identity"].update(sha256="6" * 64),
            "executable_launch_pin_invalid",
        ),
        (
            lambda process: process.update(
                stream_capture_limit_bytes=review.DEFAULT_MAX_STREAM_BYTES - 1
            ),
            "integer_not_exact",
        ),
        (
            lambda process: process.update(stdout_capture_overflow=True),
            "boolean_not_exact",
        ),
        (
            lambda process: process.update(stdout_total_bytes=99),
            "stream_binding_mismatch",
        ),
    ],
)
def test_code_summary_rejects_launch_pin_and_bounded_stream_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    _rewrite_first_command_evidence(
        summary,
        lambda record: mutation(record["process_containment"]),
    )
    with pytest.raises(review.ReviewPublisherError, match=error):
        review.validate_code_summary(summary, **kwargs)


def test_code_summary_rejects_self_consistent_environment_commitment_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    tampered = dict(summary["environment_commitment"])
    tampered["sha256"] = "6" * 64
    summary["environment_commitment"] = tampered
    summary["command_plan"]["environment_commitment"] = tampered
    for command in summary["command_plan"]["commands"]:
        command["tool"]["environment_commitment"] = tampered
    _repin_command_plan(summary)
    _rewrite_all_command_evidence(
        summary,
        lambda record: (
            record.update(environment_commitment=tampered),
            record["tool"].update(environment_commitment=tampered),
            record.update(command_plan_sha256=summary["command_plan_sha256"]),
        ),
    )
    with pytest.raises(review.ReviewPublisherError, match="live_plan_mismatch"):
        review.validate_code_summary(summary, **kwargs)


def test_code_summary_rejects_rehashed_output_parent_redirection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    redirected_parent = tmp_path / "redirected-parent"
    redirected_parent.mkdir()
    tampered = _output_parent_commitment(redirected_parent)
    summary["output_parent_commitment"] = tampered
    _rewrite_all_command_evidence(
        summary,
        lambda record: record.__setitem__("output_parent_commitment", tampered),
    )
    with pytest.raises(review.ReviewPublisherError, match="output_parent_commitment_path_mismatch"):
        review.validate_code_summary(summary, **kwargs)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda receipt: receipt["identity"].update(link_count=2),
            "handle_identity_invalid",
        ),
        (
            lambda receipt: receipt["directory_identity"].update(volume_serial_number=2),
            "cross_volume_identity",
        ),
        (
            lambda receipt: receipt.update(go_evidence_eligible=True),
            "publication_receipt_invalid",
        ),
    ],
)
def test_code_summary_rejects_publication_receipt_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    summary, kwargs = _code_summary(monkeypatch, tmp_path)
    mutation(summary["commands"][0]["publication"])
    with pytest.raises(review.ReviewPublisherError, match=error):
        review.validate_code_summary(summary, **kwargs)


def test_code_summary_rejects_missing_process_event_and_partial_command_closure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_event, kwargs = _code_summary(monkeypatch, tmp_path / "event")
    _rewrite_first_command_evidence(
        missing_event,
        lambda record: record["process_containment"]["events"].pop(),
    )
    with pytest.raises(review.ReviewPublisherError, match="required_event_missing"):
        review.validate_code_summary(missing_event, **kwargs)

    partial, kwargs = _code_summary(monkeypatch, tmp_path / "closure")
    partial["executed_command_count"] -= 1
    partial["not_run_commands"] = ["ci-mutation-pytest"]
    partial["terminal_containment_latch"] = True
    with pytest.raises(review.ReviewPublisherError, match="containment_closure_not_exact"):
        review.validate_code_summary(partial, **kwargs)


def test_read_json_rejects_duplicate_keys_and_nonfinite(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"a":1,"a":2}\n')
    with pytest.raises(review.ReviewPublisherError, match="duplicate_json_key"):
        review.read_json_mapping(duplicate, "duplicate")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_bytes(b'{"a":NaN}\n')
    with pytest.raises(ValueError):
        review.read_json_mapping(nonfinite, "nonfinite")


def test_etw_not_run_decision_is_zero_call_non_credit() -> None:
    value = review.etw_not_run_decision()
    assert value["status"] == "not_run"
    assert value["decision"] == "NO-GO"
    assert value["qualified_non_credit"] is False
    assert value["go"] is False
    assert all(item == 0 for item in value["downstream_calls"].values())


@pytest.mark.skipif(os.name != "nt", reason="Win32 token read-back")
def test_publisher_process_token_is_measured_from_win32() -> None:
    token = review.measure_current_token()
    assert token["administrator"] is True
    assert token["integrity"] in {"High", "System"}
    assert token["token_elevation_type"] == "Full"
    assert token["token_elevation_value"] == 2
    assert token["measurement"] == "win32-current-process-token"


@pytest.mark.skipif(os.name != "nt", reason="Win32 process ancestry read-back")
def test_token_evidence_is_directly_bound_to_runtime_parent_and_codex_ancestor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_paths = {
        10: r"C:\trusted\codex.exe",
        20: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        30: r"C:\trusted\python.exe",
    }

    def token(pid: int) -> dict[str, object]:
        return {
            "pid": pid,
            "path": token_paths[pid],
            "session_id": 1,
            "administrator": True,
            "administrator_group_member": True,
            "integrity": "High",
            "integrity_rid": 12288,
            "token_elevation_type": "Full",
            "token_elevation_value": 2,
            "measurement": "win32-current-process-token",
        }

    identities = {
        10: {
            "pid": 10,
            "ppid": 1,
            "creation_time_utc": "2026-09-02T00:00:00Z",
            "path": token_paths[10],
            "danger_full_access_flag_present": True,
            "approval_never_flag_present": True,
        },
        20: {
            "pid": 20,
            "ppid": 10,
            "creation_time_utc": "2026-09-02T00:00:01Z",
            "path": token_paths[20],
            "danger_full_access_flag_present": False,
            "approval_never_flag_present": False,
        },
        30: {
            "pid": 30,
            "ppid": 20,
            "creation_time_utc": "2026-09-02T00:00:02Z",
            "path": token_paths[30],
            "danger_full_access_flag_present": False,
            "approval_never_flag_present": False,
        },
    }
    monkeypatch.setattr(review.os, "getpid", lambda: 30)
    monkeypatch.setattr(review.os, "getppid", lambda: 20)
    monkeypatch.setattr(review, "measure_current_token", lambda: token(30))
    monkeypatch.setattr(review, "measure_process_token", token)
    monkeypatch.setattr(review, "measure_process_identity", lambda pid, **_kwargs: identities[pid])
    file_bindings = {
        path: {
            "path": path,
            "sha256": hashlib.sha256(path.encode()).hexdigest(),
            "bytes": len(path),
            "device": 1,
            "file_id": index,
            "creation_time_ns": index,
        }
        for index, path in enumerate(token_paths.values(), start=1)
    }
    monkeypatch.setattr(
        review,
        "_process_file_binding",
        lambda identity: file_bindings[str(identity["path"])],
    )
    lineage = {
        "schema": f"{review.SCHEMA}.lineage-work-order.v2",
        "authority_scope": "internal_non_authoritative",
        "authority_verified": False,
        "executable_bindings": {
            "codex": file_bindings[token_paths[10]],
            "powershell_parent": file_bindings[token_paths[20]],
            "publisher_python": file_bindings[token_paths[30]],
        },
    }
    lineage_path = tmp_path / "lineage.json"
    lineage_raw = review.canonical_json_bytes(lineage)
    lineage_path.write_bytes(lineage_raw)
    result = review.validate_token_evidence(
        {
            "authority_scope": "internal_non_authoritative",
            "authority_verified": False,
            "codex_pid": 10,
            "publisher_parent_pid": 20,
        },
        lineage_work_order=lineage_path,
        lineage_work_order_sha256=hashlib.sha256(lineage_raw).hexdigest(),
    )
    assert result["codex"]["process"]["danger_full_access_flag_present"] is True
    assert result["codex"]["process"]["approval_never_flag_present"] is True
    assert result["publisher_runtime"]["process"]["ppid"] == 20
    assert result["launcher_settings_readback"] == {
        "sandbox_mode": "danger-full-access",
        "approval_policy": "never",
        "source": "codex_process_command_line_direct_readback",
    }
    assert result["lineage_authority_verified"] is False
    assert result["lineage_work_order_binding"]["future_process_identity_preissued"] is False
    assert result["lineage_observation_scope"] == "live_direct_internal_non_authoritative"


def test_canonical_json_is_stable_lf_and_rejects_nan() -> None:
    first = review.canonical_json_bytes({"b": 2, "a": 1})
    second = review.canonical_json_bytes({"a": 1, "b": 2})
    assert first == second == b'{"a":1,"b":2}\n'
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    with pytest.raises(ValueError):
        review.canonical_json_bytes({"bad": float("nan")})


def test_historical_r6_projection_remains_no_go_zero_execution() -> None:
    value = json.loads(review.canonical_json_bytes(review.R6_PROJECTION))
    assert value["decision"] == "manual_intervention_required"
    assert value["credit"] == "zero_credit"
    assert value["go"] is False
    assert value["r6_restore_only"] == {
        "bridge_calls": 0,
        "bundle_created": False,
        "executed": False,
        "outer_calls": 0,
        "retries": 0,
        "runner_calls": 0,
    }


def test_selected_source_contract_includes_every_task_modified_path() -> None:
    required = review.REQUIRED_SELECTED_SOURCE_PATHS
    assert ".gitattributes" in required
    assert "enterprise-vision-mlops/tests/test_phase_b2_r7s1.py" in required
    assert "enterprise-vision-mlops/ci/pre-r8-r7s5-test-lanes.json" in required
    assert "enterprise-vision-mlops/tests/test_scenario_workload_production.py" in required
    assert "enterprise-vision-mlops/tests/test_task_queue_process_safety.py" in required
    assert "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s6_evidence.py" in required
    assert "enterprise-vision-mlops/tests/test_phase_b2_r7s6_evidence.py" in required
    assert "enterprise-vision-mlops/docs/status/2026-08-24-s8-v4-progress-ledger.jsonl" in required
    assert "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s3_process.py" in required
    assert "enterprise-vision-mlops/tests/test_phase_b2_r7s3_job_capability.py" in required
    assert "enterprise-vision-mlops/tests/test_phase_b2_r7s3_process.py" in required
    assert "enterprise-vision-mlops/scripts/dev/invoke_pre_r8_r7s7_review.ps1" in required
    assert (
        "enterprise-vision-mlops/scripts/dev/invoke_pre_r8_r7s7_windows_qualification.ps1"
        in required
    )
    assert (
        "enterprise-vision-mlops/scripts/dev/prepare_pre_r8_r7s7_windows_qualification.py"
        in required
    )
    assert "enterprise-vision-mlops/scripts/dev/qualify_pre_r8_r7s7_windows.py" in required
    assert "enterprise-vision-mlops/src/evm/scale_validation/phase_b2_r7s7_admission.py" in required
    assert (
        "enterprise-vision-mlops/src/evm/scale_validation/"
        "phase_b2_r7s7_qualification_work_order.py" in required
    )
    assert "enterprise-vision-mlops/tests/test_phase_b2_r7s4_authority.py" in required
    assert "enterprise-vision-mlops/tests/test_phase_b2_r7s7_admission.py" in required
    assert (
        "enterprise-vision-mlops/tests/test_phase_b2_r7s7_qualification_work_order.py" in required
    )
    assert (
        "enterprise-vision-mlops/tests/test_prepare_pre_r8_r7s7_windows_qualification.py"
        in required
    )
    assert "enterprise-vision-mlops/tests/test_qualify_pre_r8_r7s7_windows.py" in required
    assert "enterprise-vision-mlops/scripts/dev/validate_pre_r8_r7s4_ci_bootstrap.py" in required
    assert "enterprise-vision-mlops/tests/test_pre_r8_r7s4_ci_bootstrap.py" in required
    assert len(required) == 47


def test_selected_source_inventory_requires_git_filtered_worktree_commit_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    relative = "enterprise-vision-mlops/example.py"
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"alpha\r\n")
    monkeypatch.setattr(review, "REQUIRED_SELECTED_SOURCE_PATHS", frozenset({relative}))

    def fake_run_git(
        repository: Path, arguments: list[str], *, binary: bool = False, **_kwargs: object
    ) -> str | bytes:
        assert repository == tmp_path
        if arguments[0] == "diff":
            assert binary is False
            return relative
        if arguments == ["rev-parse", f"HEAD:{relative}"]:
            assert binary is False
            return "a" * 40
        if arguments == ["hash-object", f"--path={relative}", "--", relative]:
            assert binary is False
            return "a" * 40 if candidate.read_bytes() == b"alpha\r\n" else "b" * 40
        if arguments == ["cat-file", "-s", "a" * 40]:
            assert binary is False
            return "6"
        raise AssertionError(arguments)

    monkeypatch.setattr(review, "run_git", fake_run_git)
    inventory = review.selected_source_inventory(tmp_path)
    entry = inventory["files"][0]
    assert entry["worktree_bytes"] == 7
    assert entry["committed_bytes"] == 6
    assert entry["committed_git_blob"] == entry["git_filtered_worktree_blob"] == "a" * 40
    assert entry["git_filtered_worktree_matches_committed"] is True
    assert entry["raw_worktree_bytes_may_reflect_checkout_filters"] is True

    candidate.write_bytes(b"omega\n")
    with pytest.raises(
        review.ReviewPublisherError,
        match="selected_source_worktree_commit_mismatch",
    ):
        review.selected_source_inventory(tmp_path)


def test_review_parent_gate_requires_exact_external_path_sha_and_safe_leaf(tmp_path: Path) -> None:
    parent = tmp_path / "external-evidence"
    parent.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    commitment = review.review_parent_commitment(parent)

    observed, pin = review.validate_review_parent_gate(
        parent,
        expected_path=parent,
        expected_sha256=commitment["sha256"],
        output_leaf="new-review-output",
        forbidden_roots=(protected,),
    )

    assert observed == parent.resolve(strict=True)
    assert pin == commitment
    with pytest.raises(review.ReviewPublisherError, match="sha256_mismatch"):
        review.validate_review_parent_gate(
            parent,
            expected_path=parent,
            expected_sha256="f" * 64,
            output_leaf="new-review-output",
            forbidden_roots=(protected,),
        )
    with pytest.raises(review.ReviewPublisherError, match="output_leaf_invalid"):
        review.validate_review_parent_gate(
            parent,
            expected_path=parent,
            expected_sha256=commitment["sha256"],
            output_leaf="../escape",
            forbidden_roots=(protected,),
        )


def test_review_parent_gate_rejects_target_overlap_with_protected_root(tmp_path: Path) -> None:
    parent = tmp_path / "repository"
    parent.mkdir()
    commitment = review.review_parent_commitment(parent)

    with pytest.raises(review.ReviewPublisherError, match="overlaps_protected_root"):
        review.validate_review_parent_gate(
            parent,
            expected_path=parent,
            expected_sha256=commitment["sha256"],
            output_leaf="new-untracked-output",
            forbidden_roots=(parent,),
        )


def test_publisher_main_wires_parent_gate_and_postpublication_readback() -> None:
    source = Path(review.__file__).read_text(encoding="utf-8")
    assert "review_parent, review_parent_pin = validate_review_parent_gate(" in source
    run_offset = source.index("def _run_publisher(")
    namespace_gate = source.index("\n    require_disjoint_review_batch_namespaces(", run_offset)
    primary_publish = source.index("batch = evidence.publish_pre_serialized_batch(", run_offset)
    assert namespace_gate < primary_publish
    internal_entry_offset = source.index("def _main_internal_non_authoritative(")
    public_entry_offset = source.index("def main(")
    assert "except BaseException as exc:" in source[internal_entry_offset:public_entry_offset]
    assert "review_os_bound_outer_capability_unprovisioned" in source[public_entry_offset:]
    assert "_dispatch_publisher_failure(" in source[internal_entry_offset:public_entry_offset]
    assert "canonical_post = git_snapshot(" in source
    assert "untracked_post = untracked_summary(" in source
    assert "primary_inventory_verification = verify_primary_inventory_readback(" in source
    assert 'primary_inventory_verification["exact_match"] is True' in source
    assert '"all_prepublication_baselines_preserved": True' in source


@pytest.mark.parametrize(
    ("primary_leaf", "postpublication_leaf"),
    [
        ("Review-A", "review-a"),
        (
            ".post.20000000-0000-4000-8000-000000000002.r7s6-staging",
            "post",
        ),
    ],
)
def test_primary_and_postpublication_namespace_aliases_are_rejected_before_write(
    tmp_path: Path,
    primary_leaf: str,
    postpublication_leaf: str,
) -> None:
    before = list(tmp_path.iterdir())

    with pytest.raises(
        review.ReviewPublisherError,
        match="primary_postpublication_namespace_collision",
    ):
        review.require_disjoint_review_batch_namespaces(
            tmp_path,
            primary_leaf,
            "10000000-0000-4000-8000-000000000001",
            postpublication_leaf,
            "20000000-0000-4000-8000-000000000002",
        )

    assert list(tmp_path.iterdir()) == before


def test_existing_postpublication_control_path_is_rejected_before_primary_write(
    tmp_path: Path,
) -> None:
    primary_uuid = "10000000-0000-4000-8000-000000000001"
    post_uuid = "20000000-0000-4000-8000-000000000002"
    post_namespace = review.evidence.planned_parent_directory_leaves("post", post_uuid)
    existing = tmp_path / post_namespace["reservation"]
    existing.mkdir()
    sentinel = existing / "preserve.txt"
    sentinel.write_bytes(b"preserve")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with pytest.raises(
        review.ReviewPublisherError,
        match="batch_namespace_path_exists:postpublication:reservation",
    ):
        review.require_disjoint_review_batch_namespaces(
            tmp_path,
            "primary",
            primary_uuid,
            "post",
            post_uuid,
        )

    assert sentinel.read_bytes() == b"preserve"
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_isolated_repository_prepublication_guard_accepts_exact_clean_readback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    head = "1" * 40
    tree = "2" * 40
    results = {
        ("rev-parse", "HEAD"): head,
        ("rev-parse", "HEAD^{tree}"): tree,
        ("status", "--porcelain=v1", "--untracked-files=no"): "",
    }

    def fake_run_git(
        repository: Path, arguments: list[str], *, binary: bool = False, **_kwargs: object
    ) -> str | bytes:
        assert repository == tmp_path
        assert binary is False
        return results[tuple(arguments)]

    monkeypatch.setattr(review, "run_git", fake_run_git)
    assert review.verify_isolated_repository_prepublication(
        tmp_path,
        expected_head=head,
        expected_tree=tree,
    ) == {"head": head, "tree": tree, "tracked_changes": 0}


@pytest.mark.parametrize(
    ("command", "observed", "error"),
    [
        (("rev-parse", "HEAD"), "3" * 40, "isolated_head_changed"),
        (("rev-parse", "HEAD^{tree}"), "4" * 40, "isolated_tree_changed"),
        (
            ("status", "--porcelain=v1", "--untracked-files=no"),
            " M enterprise-vision-mlops/example.py",
            "isolated_tracked_changes_present",
        ),
    ],
)
def test_isolated_repository_prepublication_guard_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: tuple[str, ...],
    observed: str,
    error: str,
) -> None:
    head = "1" * 40
    tree = "2" * 40
    results = {
        ("rev-parse", "HEAD"): head,
        ("rev-parse", "HEAD^{tree}"): tree,
        ("status", "--porcelain=v1", "--untracked-files=no"): "",
    }
    results[command] = observed

    def fake_run_git(
        repository: Path, arguments: list[str], *, binary: bool = False, **_kwargs: object
    ) -> str | bytes:
        assert repository == tmp_path
        assert binary is False
        return results[tuple(arguments)]

    monkeypatch.setattr(review, "run_git", fake_run_git)
    with pytest.raises(review.ReviewPublisherError, match=error):
        review.verify_isolated_repository_prepublication(
            tmp_path,
            expected_head=head,
            expected_tree=tree,
        )


def test_review_publisher_uses_r7s6_pre_serialized_evidence_contract() -> None:
    contract = review.evidence.source_contract()
    assert contract["schema"].endswith("pre-r8-r7s6.evidence-writer.v1")
    assert contract["all_success_json_serialized_before_final_output_directory"] is True
    source = Path(review.__file__).read_text(encoding="utf-8")
    assert "evidence.publish_pre_serialized_batch(" in source
    assert "evidence.publish_identity_catalogued_batch(" not in source


def test_historical_directory_requires_exact_sealed_root_and_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    (sealed / "evidence.json").write_bytes(b"sealed\n")
    inventory = review.directory_inventory(sealed)
    monkeypatch.setitem(
        review.SEALED_HISTORICAL_DIRECTORIES,
        "r7s4",
        {
            "root": str(sealed),
            "file_count": inventory["file_count"],
            "directory_count": inventory["directory_count"],
            "entry_count": inventory["entry_count"],
            "total_bytes": inventory["total_bytes"],
            "inventory_sha256": inventory["inventory_sha256"],
            "tree_inventory_sha256": inventory["tree_inventory_sha256"],
        },
    )
    assert review.verify_sealed_directory(sealed, "r7s4")["sealed_reference_verified"] is True

    substitute = tmp_path / "substitute"
    substitute.mkdir()
    (substitute / "evidence.json").write_bytes(b"sealed\n")
    with pytest.raises(review.ReviewPublisherError, match="sealed_root_mismatch"):
        review.verify_sealed_directory(substitute, "r7s4")

    (sealed / "evidence.json").write_bytes(b"changed\n")
    with pytest.raises(review.ReviewPublisherError, match="sealed_inventory_mismatch"):
        review.verify_sealed_directory(sealed, "r7s4")


def test_historical_empty_directory_addition_changes_sealed_tree_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    (sealed / "evidence.json").write_bytes(b"sealed\n")
    inventory = review.directory_inventory(sealed)
    monkeypatch.setitem(
        review.SEALED_HISTORICAL_DIRECTORIES,
        "r7s4",
        {
            "root": str(sealed),
            "file_count": inventory["file_count"],
            "directory_count": inventory["directory_count"],
            "entry_count": inventory["entry_count"],
            "total_bytes": inventory["total_bytes"],
            "inventory_sha256": inventory["inventory_sha256"],
            "tree_inventory_sha256": inventory["tree_inventory_sha256"],
        },
    )

    (sealed / "unexpected-empty").mkdir()
    with pytest.raises(review.ReviewPublisherError, match="sealed_inventory_mismatch"):
        review.verify_sealed_directory(sealed, "r7s4")


def test_ci_readback_is_cross_bound_to_manifest_artifact_hashes(tmp_path: Path) -> None:
    run_id = "123"
    artifact_id = 456
    files = {
        "evm-python-tests.xml": b"xml\n",
        f"run-{run_id}-artifact-{artifact_id}.zip": b"zip\n",
        f"run-{run_id}-nodeid-inventory.json": b"{}\n",
    }
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    manifest = {
        "hosted_failure_observation": {
            "run_id": run_id,
            "artifact_id": artifact_id,
            "junit_xml_bytes": len(files["evm-python-tests.xml"]),
            "junit_xml_sha256": hashlib.sha256(files["evm-python-tests.xml"]).hexdigest(),
            "artifact_archive_bytes": len(files[f"run-{run_id}-artifact-{artifact_id}.zip"]),
            "artifact_archive_sha256": hashlib.sha256(
                files[f"run-{run_id}-artifact-{artifact_id}.zip"]
            ).hexdigest(),
            "nodeid_inventory_readback_sha256": hashlib.sha256(
                files[f"run-{run_id}-nodeid-inventory.json"]
            ).hexdigest(),
        }
    }
    assert review.verify_ci_readback(tmp_path, manifest)["manifest_artifact_identity_verified"]
    unexpected_empty = tmp_path / "unexpected-empty"
    unexpected_empty.mkdir()
    with pytest.raises(review.ReviewPublisherError, match="ci_readback_directory_tree_not_flat"):
        review.verify_ci_readback(tmp_path, manifest)
    unexpected_empty.rmdir()
    (tmp_path / "evm-python-tests.xml").write_bytes(b"tampered\n")
    with pytest.raises(review.ReviewPublisherError, match="ci_readback_bytes_mismatch"):
        review.verify_ci_readback(tmp_path, manifest)

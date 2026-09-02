from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.dev import publish_pre_r8_r7s5_review as review
from scripts.dev import run_pre_r8_r7s5_validation as runner


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
    specs = tuple(
        runner.CommandSpec(
            name,
            ("trusted-tool", name),
            expected_exit_code=2 if name == "ci-active-workflow-required-rejection" else 0,
        )
        for name in sorted(review.REQUIRED_VALIDATION_COMMANDS)
    )
    tool = {
        "path": "trusted-tool",
        "bytes": 1,
        "sha256": "3" * 64,
        "version_argv": ["trusted-tool", "--version"],
        "version": "trusted tool 1",
        "runtime_version_argv": ["trusted-tool", "--version"],
        "runtime_version": "trusted runtime 1",
    }
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
                "tool": tool,
            }
            for spec in specs
        ],
        "observation_scope": runner.VALIDATION_OBSERVATION_SCOPE,
    }
    plan = {
        **plan_payload,
        "sha256": hashlib.sha256(review.canonical_json_bytes(plan_payload)).hexdigest(),
    }
    expected_live_plan = json.loads(json.dumps(plan))
    monkeypatch.setattr(runner, "build_command_specs", lambda **_kwargs: specs)
    monkeypatch.setattr(
        runner,
        "command_plan",
        lambda **_kwargs: json.loads(json.dumps(expected_live_plan)),
    )
    commands: list[dict[str, object]] = []
    for index, spec in enumerate(specs, start=1):
        path = evidence_root / f"{index:02d}-{spec.name}.json"
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
                "command_plan_sha256": plan["sha256"],
                "tool": tool,
                "started_at_utc": "2026-09-02T00:00:00Z",
                "ended_at_utc": "2026-09-02T00:00:01Z",
                "duration_ns": 1_000_000_000,
                "stdout_bytes": 0,
                "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                "stdout_tail": "",
                "stderr_bytes": 0,
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "stderr_tail": "",
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
            }
        )
    value = {
        "schema": review.VALIDATION_SCHEMA,
        "status": "PASS",
        "repository": str(repository),
        "project_root": str(project_root),
        "head": head,
        "tree": tree,
        "command_plan": json.loads(json.dumps(plan)),
        "command_plan_sha256": plan["sha256"],
        "commands": commands,
        "live_call_counts": {name: 0 for name in review.REQUIRED_ZERO_LIVE_CALLS},
        "live_call_observation_scope": runner.VALIDATION_OBSERVATION_SCOPE,
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
    }
    kwargs = {
        "repository": repository,
        "project_root": project_root,
        "expected_head": head,
        "expected_tree": tree,
    }
    return value, kwargs


def test_directory_inventory_is_sorted_complete_and_content_bound(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "b.json").write_bytes(b"b")
    (tmp_path / "nested" / "a.json").write_bytes(b"a")
    result = review.directory_inventory(tmp_path)
    assert result["file_count"] == 2
    assert result["total_bytes"] == 2
    assert [item["relative_path"] for item in result["files"]] == [
        "b.json",
        "nested/a.json",
    ]
    assert all(len(item["sha256"]) == 64 for item in result["files"])
    assert len(result["inventory_sha256"]) == 64
    assert result["read_only_operation"] is True


def test_untracked_path_inventory_preserves_nul_sorted_baseline_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = b"zeta\0alpha\0"

    def fake_run_git(
        repository: Path, arguments: list[str], *, binary: bool = False
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


def test_code_summary_requires_exact_pass_zero_live_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, kwargs = _code_summary(monkeypatch, tmp_path)
    assert review.validate_code_summary(value, **kwargs) == value
    value["live_call_counts"]["r8"] = 1
    with pytest.raises(review.ReviewPublisherError, match="live_calls_nonzero"):
        review.validate_code_summary(value, **kwargs)


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
    partial["live_call_counts"] = {"r8": 0}
    with pytest.raises(review.ReviewPublisherError, match="live_calls_nonzero_or_unknown"):
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
    with pytest.raises(review.ReviewPublisherError, match="live_plan_mismatch"):
        review.validate_code_summary(plan_tamper, **kwargs)

    tool_tamper, kwargs = _code_summary(monkeypatch, tmp_path / "tool")
    tool_tamper["command_plan"]["commands"][0]["tool"]["version"] = "forged"
    with pytest.raises(review.ReviewPublisherError, match="live_plan_mismatch"):
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
    with pytest.raises(review.ReviewPublisherError, match="evidence_keys_not_exact"):
        review.validate_code_summary(minimal, **kwargs)


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
def test_token_evidence_is_directly_bound_to_runtime_parent_and_codex_ancestor() -> None:
    runtime = review.measure_process_identity(os.getpid())
    parent = review.measure_process_identity(runtime["ppid"])
    result = review.validate_token_evidence(
        {"codex_pid": parent["ppid"], "publisher_parent_pid": runtime["ppid"]}
    )
    assert result["codex"]["process"]["danger_full_access_flag_present"] is True
    assert result["codex"]["process"]["approval_never_flag_present"] is True
    assert result["publisher_runtime"]["process"]["ppid"] == runtime["ppid"]
    assert result["launcher_settings_readback"] == {
        "sandbox_mode": "danger-full-access",
        "approval_policy": "never",
        "source": "codex_process_command_line_direct_readback",
    }


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
    assert "enterprise-vision-mlops/tests/test_phase_b2_r7s1.py" in required
    assert "enterprise-vision-mlops/ci/pre-r8-r7s5-test-lanes.json" in required
    assert "enterprise-vision-mlops/tests/test_scenario_workload_production.py" in required
    assert "enterprise-vision-mlops/tests/test_task_queue_process_safety.py" in required
    assert len(required) == 25


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
            "total_bytes": inventory["total_bytes"],
            "inventory_sha256": inventory["inventory_sha256"],
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
    (tmp_path / "evm-python-tests.xml").write_bytes(b"tampered\n")
    with pytest.raises(review.ReviewPublisherError, match="ci_readback_bytes_mismatch"):
        review.verify_ci_readback(tmp_path, manifest)

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

from evm.scale_validation import phase_b2_r5 as r5


REVISION = "a" * 40
TREE = "b" * 40
SHA = "c" * 64


def manifest(mode: str = "restore-only") -> dict[str, object]:
    calls = (
        dict(r5.RESTORE_LIFECYCLE_COUNTS)
        if mode == "restore-only"
        else dict(r5.FRESH_LIFECYCLE_COUNTS)
    )
    return {
        "schema_version": "evm.s8_v4.x1_phase_b2_r5_work_order.v1",
        "execution_mode": mode,
        "canonical_revision": REVISION,
        "canonical_tree": TREE,
        "timeout_contract": r5.TimeoutContract().to_dict(),
        "lifecycle_timeout_contract": r5.LifecycleTimeoutContract().to_dict(),
        "process_containment": {
            "provider": "windows_job_object",
            "create_suspended": True,
            "assign_before_resume": True,
            "breakaway_allowed": False,
            "kill_on_job_close": False,
            "terminate_job_object_allowed": False,
            "job_accounting_authoritative": True,
            "stdio_drain_before_followup": True,
            "residual_repoll_seconds": 120,
            "force_termination_attempts": 0,
            "wsl_run_uuid_and_process_group": True,
            "wsl_proc_residual_check": True,
        },
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
            "downstream": dict(r5.DOWNSTREAM_COUNTS),
            "launcher": {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0},
        },
        "expected_state": {"b0": {"uid": r5.EXPECTED_B0_UID}},
        "etw_contract": {
            "fresh_capture_required_for_phase_b2_go": False,
            "fresh_invocations": 0,
            "amendment_sha256": r5.EXPECTED_ETW_AMENDMENT_SHA256,
        },
        "evidence": {
            "write_mode": "create-exclusive",
            "failure_creates_completion_marker": False,
            "success_requires_all_invariants": True,
        },
        "checkpoint": {
            "kind": "r4_failure_seal" if mode == "restore-only" else "r5_restore_only_index",
            "path": "checkpoint.json",
            "sha256": SHA,
            "companion_index": {"path": "checkpoint-index.json", "sha256": SHA},
        },
        "runtime": {},
    }


def test_manifest_contract_accepts_exact_restore_and_fresh() -> None:
    assert (
        r5.validate_r5_manifest(manifest(), expected_revision=REVISION, mode="restore-only")[
            "b0_uid"
        ]
        == r5.EXPECTED_B0_UID
    )
    assert (
        r5.validate_r5_manifest(manifest("fresh"), expected_revision=REVISION, mode="fresh")["mode"]
        == "fresh"
    )


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda value: value.update(canonical_revision=r5.OLD_R4_REVISION), "reuse"),
        (
            lambda value: value["expected_state"]["b0"].update(  # type: ignore[index]
                uid="cfdab424-dcc5-4d5f-ae7530441ef4"
            ),
            "valid_uuid",
        ),
        (
            lambda value: value["timeout_contract"].update(  # type: ignore[union-attr]
                residual_repoll_seconds=4.0
            ),
            "residual_repoll",
        ),
        (
            lambda value: value["process_containment"].update(  # type: ignore[union-attr]
                assign_before_resume=False
            ),
            "containment",
        ),
        (
            lambda value: value["call_contract"]["restore-only"].update(  # type: ignore[index,union-attr]
                compose_stop=1
            ),
            "exact_counts",
        ),
    ],
)
def test_manifest_mutations_fail_closed(mutator: object, match: str) -> None:
    value = manifest()
    mutator(value)  # type: ignore[operator]
    with pytest.raises(r5.R5ContractError, match=match):
        r5.validate_r5_manifest(
            value, expected_revision=value["canonical_revision"], mode="restore-only"
        )


def test_runtime_pin_checks_sha_and_blob(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "r5-test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Phase B2 r5 test"],
        check=True,
    )
    value = manifest()
    runtime: dict[str, object] = {}
    for name in r5.RUNTIME_COMPONENTS:
        path = tmp_path / f"{name}.py"
        path.write_text(f"# {name}\n", encoding="utf-8")
        runtime[name] = {
            "path": str(path),
            "sha256": r5.sha256_file(path),
            "blob_oid": "pending",
        }
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    for name in r5.RUNTIME_COMPONENTS:
        path = tmp_path / f"{name}.py"
        runtime[name]["blob_oid"] = r5.git_head_blob_oid(tmp_path, path)  # type: ignore[index]
    value["runtime"] = runtime
    measured = r5.validate_runtime_pins(value, tmp_path)
    assert set(measured) == set(r5.RUNTIME_COMPONENTS)
    (tmp_path / "runner.py").write_text("# mutation\n", encoding="utf-8")
    with pytest.raises(r5.R5ContractError, match="runner_sha256_mismatch"):
        r5.validate_runtime_pins(value, tmp_path)


def test_launcher_evidence_requires_full_token_chain_and_exact_counts() -> None:
    value = manifest()
    runtime = value["runtime"]
    assert isinstance(runtime, dict)
    for name in r5.RUNTIME_COMPONENTS:
        runtime[name] = {"sha256": SHA}
    evidence = {
        "schema": "s8-v4-x1-phase-b2-r5-launcher-evidence/v1",
        "token_evidence": {
            "administrator": True,
            "integrity": "High",
            "token_elevation_type": "Full",
        },
        "sha_chain": {
            **{
                name: SHA
                for name in (
                    "outer",
                    "bridge",
                    "manifest",
                    "checkpoint",
                    "checkpoint_index",
                )
            },
            **{name: SHA for name in r5.RUNTIME_COMPONENTS},
        },
        "invocation_counts": {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0},
    }
    encoded = base64.b64encode(json.dumps(evidence).encode()).decode()
    assert r5.decode_launcher_evidence(encoded, value)["token_evidence"]["administrator"]
    evidence["invocation_counts"]["runner"] = 2
    encoded = base64.b64encode(json.dumps(evidence).encode()).decode()
    with pytest.raises(r5.R5ContractError, match="exact_counts"):
        r5.decode_launcher_evidence(encoded, value)


def test_read_r4_failure_checkpoint_without_reexecution(tmp_path: Path) -> None:
    checkpoint = tmp_path / "failure-seal.json"
    checkpoint.write_text(
        json.dumps(
            {
                "failure_only": True,
                "acceptance_credit": False,
                "success_marker_created": False,
                "report": {"call_counts": dict(r5.RESTORE_LIFECYCLE_COUNTS)},
            }
        ),
        encoding="utf-8",
    )
    _, restored = r5.read_checkpoint(checkpoint, r5.sha256_file(checkpoint), mode="restore-only")
    assert restored is not None
    assert restored.source == "r4_failure_seal_checkpoint"


def test_failure_evidence_is_append_only_and_never_creates_marker(tmp_path: Path) -> None:
    output = tmp_path / "failure"
    writer = r5.EvidenceWriter(output)
    result = writer.seal_failure({"passed": False})
    assert result["failure_seal"]["sha256"]
    assert not (output / "completion-marker.json").exists()
    with pytest.raises(r5.R5EvidenceExistsError):
        r5.EvidenceWriter(output)
    with pytest.raises(r5.R5EvidenceExistsError):
        writer.seal_failure({"passed": False})


def test_restore_only_pass_has_no_phase_b2_marker(tmp_path: Path) -> None:
    output = tmp_path / "restore"
    writer = r5.EvidenceWriter(output)
    report = {
        "passed": True,
        "restore_only_pass": True,
        "phase_b2_executed": False,
        "residual_pids": [],
    }
    result = writer.seal_restore_only(report)
    assert result["restore_only_index"]["sha256"]
    assert not (output / "completion-marker.json").exists()


def test_r5_restore_continues_after_safe_invariant_failure() -> None:
    calls: list[str] = []

    def probe(name: str, passed: bool):
        def invoke(_deadline: object) -> dict[str, object]:
            calls.append(name)
            return {
                "passed": passed,
                "last_error": None if passed else f"{name}_failed",
                "invariants": {name: passed},
            }

        return invoke

    probes = {
        stage.value: probe(stage.value, stage.value != "compose")
        for stage in r5.RESTORE_STAGE_ORDER
    }
    harness = r5.ReconcileRestoreHarness(
        probes=probes,
        required_invariants=tuple(stage.value for stage in r5.RESTORE_STAGE_ORDER),
    )
    report = harness.run_restore_only(r5.RestoreCheckpoint("r4_failure_seal_checkpoint", {}, True))
    assert calls == [stage.value for stage in r5.RESTORE_STAGE_ORDER]
    assert not report.passed
    assert report.manual_intervention_required
    assert report.last_error == "compose_failed"
    assert report.call_counts == r5.RESTORE_LIFECYCLE_COUNTS


def test_r5_restore_stops_all_followup_after_residual_latch() -> None:
    calls: list[str] = []

    def first(_deadline: object) -> dict[str, object]:
        calls.append("first")
        return {
            "passed": False,
            "manual_intervention_required": True,
            "residual_pids": [991],
            "last_error": "residual_process",
        }

    def forbidden_followup(_deadline: object) -> bool:
        calls.append("followup")
        return True

    probes = {stage.value: forbidden_followup for stage in r5.RESTORE_STAGE_ORDER}
    probes[r5.RESTORE_STAGE_ORDER[0].value] = first
    harness = r5.ReconcileRestoreHarness(probes=probes)
    report = harness.run_restore_only(r5.RestoreCheckpoint("r4_failure_seal_checkpoint", {}, True))
    assert calls == ["first"]
    assert report.residual_pids == (991,)
    assert report.manual_intervention_required


def fresh_report() -> dict[str, object]:
    return {
        "mode": "fresh",
        "decision": "phase_b2_pass",
        "passed": True,
        "actual_raw_samples_collected": True,
        "restore_report_synthesized": False,
        "sample_counts": {"windows": 1800, "wsl": 1800},
        "clock_metrics": {name: 0 for name in r5.ZERO_METRICS},
        "call_counts": dict(r5.FRESH_LIFECYCLE_COUNTS),
        "residual_pids": [],
        "manual_intervention_required": False,
        "recovery_invariants": {"all_runtime": True},
    }


def test_report_only_writer_cannot_create_fresh_success(tmp_path: Path) -> None:
    output = tmp_path / "fresh"
    writer = r5.EvidenceWriter(output)
    writer.write_json("windows-raw-samples.json", [{"sequence": value} for value in range(1800)])
    writer.write_json("wsl-raw-samples.json", [{"sequence": value} for value in range(1800)])
    with pytest.raises(r5.R5SuccessInvariantError, match="validated_live_execution"):
        writer.seal_fresh_success(fresh_report())
    assert not (output / "private-evidence-index.json").exists()
    assert not (output / "completion-marker.json").exists()


@pytest.mark.parametrize(
    ("field", "value", "_match"),
    [
        ("actual_raw_samples_collected", False, "actual_raw"),
        ("restore_report_synthesized", True, "synthesis"),
        ("manual_intervention_required", True, "manual"),
    ],
)
def test_fresh_success_rejects_false_claims(field: str, value: object, _match: str) -> None:
    report = fresh_report()
    report[field] = value
    with pytest.raises(r5.R5SuccessInvariantError, match="validated_live_execution"):
        r5.validate_fresh_success(report)

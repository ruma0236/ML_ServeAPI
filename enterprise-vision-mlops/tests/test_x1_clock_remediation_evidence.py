from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evm.scale_validation.clock_remediation import (
    ClockRemediationThresholds,
    analyze_remediation_window,
)
from evm.scale_validation.clock_remediation_evidence import (
    ClockRemediationEvidenceError,
    canonical_bytes,
    project_no_go,
    validate_no_go,
    write_private_index,
)


def _runtime_samples(domain: str, *, step: bool = False) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    wall_step = 0
    for sequence in range(1_800):
        raw = sequence * 100_000_000
        if step and sequence in {67, 415, 763, 1111, 1459}:
            wall_step += 2_300_000_000
        rows.append(
            {
                "auxiliary_monotonic_ns": raw + 500,
                "domain": domain,
                "monotonic_ns": raw + 500,
                "raw_after_ns": raw + 1_000,
                "raw_before_ns": raw,
                "realtime_unix_ns": 1_700_000_000_000_000_000 + raw + wall_step,
                "sequence": sequence,
            }
        )
    return rows


def _write(path: Path, payload: object) -> None:
    path.write_bytes(canonical_bytes(payload))


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    private = tmp_path / "private"
    private.mkdir()
    thresholds = ClockRemediationThresholds()
    raw = {
        "windows_host": _runtime_samples("windows_host"),
        "wsl_ubuntu": _runtime_samples("wsl_ubuntu", step=True),
    }
    analysis = analyze_remediation_window(
        mode="docker-off",
        os_domains=raw,
        database_samples=None,
        thresholds=thresholds,
    )
    _write(
        private / "postrestart-docker-off-01.json",
        {
            "analysis": analysis,
            "contract": {"cadence_ms": 100, "duration_seconds": 180},
            "passed": False,
            "raw": raw,
        },
    )
    _write(private / "prechange-service-correlation.json", {"service_change_eligible": False})
    _write(
        private / "wsl-update-timeout-and-shutdown.json",
        {
            "timesync_service_changed": False,
            "wsl_shutdown": {"exit_code": 0},
            "wsl_update": {
                "bounded_wait_seconds": 180,
                "result": "interrupted_after_no_output",
            },
        },
    )
    collector = b"#Requires -RunAsAdministrator\nfixture\n"
    (private / "official-collect-wsl-logs.ps1").write_bytes(collector)
    import hashlib

    _write(
        private / "official-wsl-etw-collection-status.json",
        {
            "collector": {
                "requires_run_as_administrator": True,
                "sha256": hashlib.sha256(collector).hexdigest(),
            },
            "decision": "not_executed_requires_administrator",
            "service_configuration_changed": False,
        },
    )
    _write(
        private / "final-runtime-readiness.json",
        {
            "kubernetes": {"temporary_s6bm_x1_triton_resources": []},
            "postgresql": {"is_in_recovery": False, "temporary_schemas": []},
            "preflight": {
                "b0": {"passed": True},
                "kubernetes": {"gpu_allocatable": "1", "gpu_capacity": "1"},
                "prometheus": {"total": 5, "up": 5},
                "queues": {"active": 0, "leased": 0, "outcome_unknown": 0},
                "x1_runtime_absent": True,
            },
            "temporary_s6bm_x1_triton_containers": [],
        },
    )
    write_private_index(private)
    public = project_no_go(
        private,
        source_revision="a" * 40,
        source_tree="b" * 40,
        test_results={"focused_passed": 1},
    )
    return private, public


def test_no_go_projection_recomputes_raw_and_cleanup(tmp_path: Path) -> None:
    private, public = _fixture(tmp_path)

    validated = validate_no_go(public, private)

    assert validated["decision"] == "x1_clock_remediation_no_go"
    assert validated["docker_off_gate"]["windows_host_discontinuity_count"] == 0
    assert validated["docker_off_gate"]["wsl_discontinuity_count"] == 5
    assert validated["docker_off_gate"]["step_interval_ns"] == [34_800_000_000] * 4


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("decision",), "go"),
        (("execution_boundary", "full_stack_windows_executed"), 3),
        (("execution_boundary", "q0_started"), True),
        (("docker_off_gate", "wsl_discontinuity_count"), 0),
        (("runtime_cleanup", "queue_active"), 1),
    ],
)
def test_no_go_projection_rejects_public_mutation(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    private, public = _fixture(tmp_path)
    mutated = copy.deepcopy(public)
    target = mutated
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ClockRemediationEvidenceError, match="clock_public_projection"):
        validate_no_go(mutated, private)


def test_private_index_rejects_artifact_removal(tmp_path: Path) -> None:
    private, public = _fixture(tmp_path)
    (private / "prechange-service-correlation.json").unlink()

    with pytest.raises(ClockRemediationEvidenceError, match="clock_private_index"):
        validate_no_go(public, private)


def test_no_go_rejects_post_gate_execution_artifact(tmp_path: Path) -> None:
    private, public = _fixture(tmp_path)
    (private / "private-evidence-index.json").unlink()
    _write(private / "postrestart-full-stack-01.json", {"passed": True})
    write_private_index(private)

    with pytest.raises(ClockRemediationEvidenceError, match="clock_post_gate_execution"):
        validate_no_go(public, private)


def test_raw_analysis_cannot_hide_clock_step(tmp_path: Path) -> None:
    private, public = _fixture(tmp_path)
    path = private / "postrestart-docker-off-01.json"
    payload = json.loads(path.read_bytes())
    payload["analysis"]["os_domains"]["wsl_ubuntu"]["offset_step_count"] = 0
    _write(path, payload)
    (private / "private-evidence-index.json").unlink()
    write_private_index(private)

    with pytest.raises(ClockRemediationEvidenceError, match="clock_docker_off_raw_projection"):
        validate_no_go(public, private)

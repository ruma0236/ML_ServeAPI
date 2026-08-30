from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from evm.scale_validation.clock_remediation import (
    ClockRemediationThresholds,
    analyze_remediation_window,
)


RUN_ID = "x1-clock-remediation-20260830T113944Z-34eec036"
DOCKER_OFF_FILE = "postrestart-docker-off-01.json"
SERVICE_CORRELATION_FILE = "prechange-service-correlation.json"
WSL_UPDATE_FILE = "wsl-update-timeout-and-shutdown.json"
ETW_STATUS_FILE = "official-wsl-etw-collection-status.json"
ETW_COLLECTOR_FILE = "official-collect-wsl-logs.ps1"
RUNTIME_READINESS_FILE = "final-runtime-readiness.json"
PRIVATE_INDEX_FILE = "private-evidence-index.json"


class ClockRemediationEvidenceError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_bytes(value: Any) -> bytes:
    return (canonical(value) + "\n").encode("ascii")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ClockRemediationEvidenceError(f"clock_evidence_json:{path.name}") from exc
    if not isinstance(payload, dict):
        raise ClockRemediationEvidenceError(f"clock_evidence_mapping:{path.name}")
    return payload


def private_entries(private_root: Path) -> list[dict[str, Any]]:
    root = private_root.resolve()
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.name == PRIVATE_INDEX_FILE:
            continue
        if path.is_symlink():
            raise ClockRemediationEvidenceError(f"clock_private_nonregular:{path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ClockRemediationEvidenceError(f"clock_private_nonregular:{path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ClockRemediationEvidenceError(f"clock_private_escape:{path}") from exc
        entries.append(
            {
                "bytes": resolved.stat().st_size,
                "path": relative,
                "sha256": sha256_file(resolved),
            }
        )
    return entries


def project_private_index(private_root: Path) -> dict[str, Any]:
    entries = private_entries(private_root)
    return {
        "aggregate_sha256": hashlib.sha256(canonical(entries).encode("ascii")).hexdigest(),
        "artifact_count": len(entries),
        "entries": entries,
        "run_id": RUN_ID,
        "schema_version": "evm.s8_v4.x1_clock_remediation_private_index.v1",
        "total_bytes": sum(int(entry["bytes"]) for entry in entries),
    }


def write_private_index(private_root: Path) -> dict[str, Any]:
    output = private_root / PRIVATE_INDEX_FILE
    payload = project_private_index(private_root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(output, flags)
    try:
        os.write(descriptor, canonical_bytes(payload))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return payload


def validate_private_index(private_root: Path) -> dict[str, Any]:
    observed = _load_json(private_root / PRIVATE_INDEX_FILE)
    expected = project_private_index(private_root)
    if observed != expected:
        raise ClockRemediationEvidenceError("clock_private_index")
    return observed


def _required_entry(index: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    entries = index.get("entries")
    if not isinstance(entries, list):
        raise ClockRemediationEvidenceError("clock_private_entries")
    matches = [
        entry for entry in entries if isinstance(entry, Mapping) and entry.get("path") == path
    ]
    if len(matches) != 1:
        raise ClockRemediationEvidenceError(f"clock_private_required:{path}")
    return matches[0]


def _assert_no_post_gate_execution(index: Mapping[str, Any]) -> None:
    entries = index.get("entries")
    if not isinstance(entries, list):
        raise ClockRemediationEvidenceError("clock_private_entries")
    prohibited_prefixes = (
        "postrestart-full-stack-",
        "postremediation-full-stack-",
        "q0-",
        "calibration-",
        "x1-78-",
        "integrated-v4-",
    )
    prohibited = [
        str(entry.get("path"))
        for entry in entries
        if isinstance(entry, Mapping)
        and Path(str(entry.get("path") or "")).name.startswith(prohibited_prefixes)
    ]
    if prohibited:
        raise ClockRemediationEvidenceError(f"clock_post_gate_execution:{prohibited}")


def _docker_off_projection(private_root: Path) -> dict[str, Any]:
    path = private_root / DOCKER_OFF_FILE
    payload = _load_json(path)
    contract = payload.get("contract")
    raw = payload.get("raw")
    if not isinstance(contract, Mapping) or not isinstance(raw, Mapping):
        raise ClockRemediationEvidenceError("clock_docker_off_shape")
    if contract.get("duration_seconds") != 180 or contract.get("cadence_ms") != 100:
        raise ClockRemediationEvidenceError("clock_docker_off_frozen_window")
    thresholds = ClockRemediationThresholds(
        sample_count=1_800,
        cadence_ns=100_000_000,
    )
    recomputed = analyze_remediation_window(
        mode="docker-off",
        os_domains={
            "windows_host": raw.get("windows_host"),
            "wsl_ubuntu": raw.get("wsl_ubuntu"),
        },
        database_samples=None,
        thresholds=thresholds,
    )
    if payload.get("analysis") != recomputed or payload.get("passed") is not False:
        raise ClockRemediationEvidenceError("clock_docker_off_raw_projection")
    windows = recomputed["os_domains"]["windows_host"]
    wsl = recomputed["os_domains"]["wsl_ubuntu"]
    sequences = [int(step["sequence"]) for step in wsl["offset_steps"]]
    intervals = [
        (current - previous) * thresholds.cadence_ns
        for previous, current in zip(sequences, sequences[1:], strict=False)
    ]
    return {
        "backward_wall_step_count": {
            "windows_host": windows["backward_wall_step_count"],
            "wsl_ubuntu": wsl["backward_wall_step_count"],
        },
        "cadence_ns": thresholds.cadence_ns,
        "mode": "docker-off",
        "passed": False,
        "sample_count_per_domain": thresholds.sample_count,
        "step_interval_ns": intervals,
        "step_sequences": sequences,
        "unclassified_sampler_gap_count": {
            "windows_host": windows["unclassified_sampler_gap_count"],
            "wsl_ubuntu": wsl["unclassified_sampler_gap_count"],
        },
        "windows_host_discontinuity_count": windows["offset_step_count"],
        "wsl_discontinuity_count": wsl["offset_step_count"],
        "wsl_offset_change_ns": [int(step["offset_change_ns"]) for step in wsl["offset_steps"]],
    }


def _runtime_cleanup(private_root: Path) -> dict[str, Any]:
    payload = _load_json(private_root / RUNTIME_READINESS_FILE)
    preflight = payload.get("preflight")
    postgresql = payload.get("postgresql")
    kubernetes = payload.get("kubernetes")
    if not all(isinstance(item, Mapping) for item in (preflight, postgresql, kubernetes)):
        raise ClockRemediationEvidenceError("clock_cleanup_shape")
    queues = preflight.get("queues")
    prometheus = preflight.get("prometheus")
    b0 = preflight.get("b0")
    kube_state = preflight.get("kubernetes")
    if queues != {"active": 0, "leased": 0, "outcome_unknown": 0}:
        raise ClockRemediationEvidenceError("clock_cleanup_queue")
    if (
        not isinstance(prometheus, Mapping)
        or prometheus.get("total") != 5
        or prometheus.get("up") != 5
    ):
        raise ClockRemediationEvidenceError("clock_cleanup_prometheus")
    if not isinstance(b0, Mapping) or b0.get("passed") is not True:
        raise ClockRemediationEvidenceError("clock_cleanup_b0")
    if not isinstance(kube_state, Mapping) or (
        kube_state.get("gpu_capacity"),
        kube_state.get("gpu_allocatable"),
    ) != ("1", "1"):
        raise ClockRemediationEvidenceError("clock_cleanup_gpu")
    if postgresql.get("is_in_recovery") is not False or postgresql.get("temporary_schemas") != []:
        raise ClockRemediationEvidenceError("clock_cleanup_postgresql")
    if payload.get("temporary_s6bm_x1_triton_containers") != []:
        raise ClockRemediationEvidenceError("clock_cleanup_container")
    if kubernetes.get("temporary_s6bm_x1_triton_resources") != []:
        raise ClockRemediationEvidenceError("clock_cleanup_kubernetes")
    if preflight.get("x1_runtime_absent") is not True:
        raise ClockRemediationEvidenceError("clock_cleanup_x1")
    return {
        "b0_actual_cuda": True,
        "gpu_allocatable": 1,
        "gpu_capacity": 1,
        "postgresql_is_in_recovery": False,
        "prometheus_total": 5,
        "prometheus_up": 5,
        "queue_active": 0,
        "queue_leased": 0,
        "queue_outcome_unknown": 0,
        "temporary_container_count": 0,
        "temporary_kubernetes_resource_count": 0,
        "temporary_schema_count": 0,
        "x1_runtime_absent": True,
    }


def project_no_go(
    private_root: Path,
    *,
    source_revision: str,
    source_tree: str,
    test_results: Mapping[str, Any],
) -> dict[str, Any]:
    index = validate_private_index(private_root)
    _assert_no_post_gate_execution(index)
    docker_off = _docker_off_projection(private_root)
    service = _load_json(private_root / SERVICE_CORRELATION_FILE)
    update = _load_json(private_root / WSL_UPDATE_FILE)
    etw = _load_json(private_root / ETW_STATUS_FILE)
    if service.get("service_change_eligible") is not False:
        raise ClockRemediationEvidenceError("clock_service_correlation")
    if update.get("timesync_service_changed") is not False:
        raise ClockRemediationEvidenceError("clock_service_changed")
    if update.get("wsl_update", {}).get("result") != "interrupted_after_no_output":
        raise ClockRemediationEvidenceError("clock_wsl_update_timeout")
    if update.get("wsl_update", {}).get("bounded_wait_seconds") != 180:
        raise ClockRemediationEvidenceError("clock_wsl_update_bound")
    if update.get("wsl_shutdown", {}).get("exit_code") != 0:
        raise ClockRemediationEvidenceError("clock_wsl_shutdown")
    if etw.get("decision") != "not_executed_requires_administrator":
        raise ClockRemediationEvidenceError("clock_etw_decision")
    if etw.get("service_configuration_changed") is not False:
        raise ClockRemediationEvidenceError("clock_etw_service_changed")
    collector = etw.get("collector")
    if (
        not isinstance(collector, Mapping)
        or collector.get("requires_run_as_administrator") is not True
    ):
        raise ClockRemediationEvidenceError("clock_etw_contract")
    collector_path = private_root / ETW_COLLECTOR_FILE
    if collector.get("sha256") != sha256_file(collector_path):
        raise ClockRemediationEvidenceError("clock_etw_collector_sha")
    if not collector_path.read_bytes().startswith(b"#Requires -RunAsAdministrator"):
        raise ClockRemediationEvidenceError("clock_etw_collector_header")
    cleanup = _runtime_cleanup(private_root)
    required = (
        DOCKER_OFF_FILE,
        SERVICE_CORRELATION_FILE,
        WSL_UPDATE_FILE,
        ETW_STATUS_FILE,
        ETW_COLLECTOR_FILE,
        RUNTIME_READINESS_FILE,
    )
    evidence = {
        path: {
            "bytes": _required_entry(index, path)["bytes"],
            "sha256": _required_entry(index, path)["sha256"],
        }
        for path in required
    }
    return {
        "acceptance_credit": False,
        "claim_boundary": (
            "One Windows/WSL2 physical node, one RTX 4080, Docker Desktop, one local "
            "PostgreSQL control plane, and controlled diagnostics; no production SLA, HA/DR, "
            "multi-node or multi-GPU claim, and no attribution to a specific time service."
        ),
        "credit": "non_credit",
        "decision": "x1_clock_remediation_no_go",
        "docker_off_gate": docker_off,
        "evidence": evidence,
        "execution_boundary": {
            "calibration_started": False,
            "full_stack_windows_executed": 0,
            "integrated_v4_started": False,
            "q0_started": False,
            "runs_78_started": False,
        },
        "official_wsl_etw": {
            "archive_created": False,
            "collector_sha256": collector["sha256"],
            "decision": etw["decision"],
            "session_administrator": False,
        },
        "private_evidence": {
            "aggregate_sha256": index["aggregate_sha256"],
            "artifact_count": index["artifact_count"],
            "index_sha256": sha256_file(private_root / PRIVATE_INDEX_FILE),
            "run_id": RUN_ID,
            "total_bytes": index["total_bytes"],
        },
        "remediation": {
            "guest_time_service_changed": False,
            "hyper_v_implicit_sync_changed": False,
            "root_cause": "runtime_domain_discontinuity_observed_exact_mechanism_unknown",
            "service_event_correlation": False,
            "wsl_shutdown_exit_code": 0,
            "wsl_update_result": "180s_no_output_timeout",
        },
        "runtime_cleanup": cleanup,
        "schema_version": "evm.s8_v4.x1_clock_remediation_no_go.v3",
        "source_identity": {
            "revision": source_revision,
            "tree_sha": source_tree,
        },
        "status": "remediation_required",
        "tests": dict(test_results),
    }


def validate_no_go(public: Mapping[str, Any], private_root: Path) -> dict[str, Any]:
    source = public.get("source_identity")
    tests = public.get("tests")
    if not isinstance(source, Mapping) or not isinstance(tests, Mapping):
        raise ClockRemediationEvidenceError("clock_public_shape")
    expected = project_no_go(
        private_root,
        source_revision=str(source.get("revision") or ""),
        source_tree=str(source.get("tree_sha") or ""),
        test_results=tests,
    )
    if dict(public) != expected:
        raise ClockRemediationEvidenceError("clock_public_projection")
    return expected

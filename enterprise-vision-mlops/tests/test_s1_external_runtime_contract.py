from __future__ import annotations

from pathlib import Path

import pytest

from evm.scale_validation.s1_runtime import (
    HttpSpec,
    build_sweep_specs,
    distribute,
    prometheus_value,
    runtime_environment,
    supervisor_command,
    trace_id,
    traceparent,
)


def fixtures() -> tuple[list[dict[str, str]], list[str]]:
    approvals = [
        {
            "run_id": f"approval-run-{index}",
            "candidate_id": f"candidate-{index}",
            "model_digest": f"{index + 1:064x}",
            "ct_evaluation_id": f"ct-{index}",
        }
        for index in range(3)
    ]
    return approvals, [f"retry-run-{index}" for index in range(3)]


@pytest.mark.parametrize("target", [100, 250, 500])
def test_required_sweep_distributes_every_external_route(target: int) -> None:
    approvals, retries = fixtures()
    counts = distribute(target)
    specs = build_sweep_specs(
        target=target,
        suite_id="contract",
        profile_id="profile",
        profile_version=1,
        approval_fixtures=approvals,
        retry_run_ids=retries,
    )

    assert len(specs) == target
    assert sum(counts.values()) == target
    assert all(counts[operation] > 0 for operation in ("create", "approve", "cancel", "retry"))
    assert {spec.operation for spec in specs} == {"create", "approve", "cancel", "retry"}
    assert all(isinstance(spec, HttpSpec) for spec in specs)


def test_trace_identity_is_deterministic_and_w3c_shaped() -> None:
    first = traceparent("same-request")
    second = traceparent("same-request")

    assert first == second
    assert first.startswith("00-") and first.endswith("-01")
    assert len(trace_id(first)) == 32


def test_prometheus_peak_parser_requires_exact_metric() -> None:
    metrics = "evm_http_server_in_flight 0\nevm_http_server_peak_in_flight 500\n"

    assert prometheus_value(metrics, "evm_http_server_peak_in_flight") == 500
    with pytest.raises(RuntimeError, match="prometheus_metric_missing"):
        prometheus_value(metrics, "missing_metric")


def test_runtime_environment_isolates_control_plane_schema_and_roots(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    environment = runtime_environment(
        root=root,
        data_root=tmp_path / "data",
        profile_root=profile_root,
        database_url="postgresql://example.invalid/control",
        schema="evm_s1_contract_test",
        revision="a" * 40,
        branch="codex/test",
        pool_max_size=8,
        acquire_timeout_seconds=1.0,
    )

    assert environment["EVM_CONTROL_PLANE_STORE_MODE"] == "postgres"
    assert environment["EVM_CONTROL_PLANE_DATABASE_SCHEMA"] == "evm_s1_contract_test"
    assert environment["EVM_CONTROL_PLANE_POOL_MAX_SIZE"] == "8"
    assert Path(environment["EVM_LIFECYCLE_RUN_ROOT"]).is_relative_to(tmp_path)
    assert Path(environment["EVM_LIFECYCLE_HOST_ROOT"]).is_relative_to(tmp_path)
    assert Path(environment["EVM_HOST_DATA_ROOT"]).is_relative_to(tmp_path)
    assert environment["EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH"] == "false"


def test_worker_launch_contract_preserves_runtime_scope_and_lease_timing() -> None:
    root = Path(__file__).resolve().parents[1]
    worker_launcher = (root / "scripts/dev/start_lifecycle_worker.ps1").read_text(encoding="utf-8")
    supervisor = (root / "scripts/dev/start_host_runtime_supervisor.ps1").read_text(
        encoding="utf-8"
    )

    assert "EVM_LIFECYCLE_CLAIM_TTL_SECONDS" in worker_launcher
    assert "EVM_LIFECYCLE_HEARTBEAT_INTERVAL_SECONDS" in worker_launcher
    assert '"--runtime-scope", $env:EVM_RUNTIME_PROCESS_MARKER' in worker_launcher
    assert "$LifecycleCommandMarker" in supervisor

    command = supervisor_command(root)
    assert "-Run" in command
    assert "-NoKubernetesObserver" in command

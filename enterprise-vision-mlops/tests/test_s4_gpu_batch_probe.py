from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest

from evm.control_panel.scenario_workloads import (
    GpuBatchProbeDescriptor,
    GpuBatchProbeRequest,
    ScenarioWorkloadError,
    acquire_scale_validation_gpu_lease,
    assert_scale_validation_gpu_lease_owner,
    read_active_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.model_runtime.gpu_batch_probe import (
    BatchInferenceResult,
    GpuBatchExecutionConfig,
    GpuBatchProbeError,
    GpuBatchProbeExecutor,
)


class FakeBackend:
    descriptor = GpuBatchProbeDescriptor(
        dataset_version="uci-higgs-2014-s3-v1",
        dataset_identity_sha256="a" * 64,
        split_manifest_sha256="b" * 64,
        model_identity_sha256="c" * 64,
        artifact_sha256="d" * 64,
        framework="test-torch",
        cuda_runtime="test-cuda",
        source_revision="e" * 40,
    )

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def infer(self, features: list[list[float]], instance_id: int) -> BatchInferenceResult:
        self.batch_sizes.append(len(features))
        return BatchInferenceResult(
            probabilities=[0.75 for _ in features],
            h2d_ms=0.1,
            inference_ms=0.2,
            d2h_ms=0.1,
            allocated_vram_bytes=1024,
            reserved_vram_bytes=2048,
            peak_vram_bytes=4096,
        )


def config(
    tmp_path: Path, *, batch_size: int = 4, max_delay_ms: int = 20
) -> GpuBatchExecutionConfig:
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    return GpuBatchExecutionConfig(
        enabled=True,
        registry_path=registry,
        batch_size=batch_size,
        max_delay_ms=max_delay_ms,
        instance_count=1,
        max_outstanding=8,
        max_outstanding_bytes=65536,
        max_request_bytes=8192,
        admission_wait_seconds=0.05,
        request_timeout_seconds=2,
        retry_after_seconds=1,
        lease_run_id="s4-test",
        lease_id="lease-test",
        lease_fencing_token="fence-test",
    )


def request() -> GpuBatchProbeRequest:
    return GpuBatchProbeRequest(
        dataset_identity_sha256="a" * 64,
        model_identity_sha256="c" * 64,
        features=[0.0] * 28,
    )


def test_scale_validation_lease_uses_existing_gpu_lease_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_SCENARIO_GPU_LEASE_ROOT", str(tmp_path))
    lease = acquire_scale_validation_gpu_lease(
        "s4-training-test",
        source_commit="a" * 40,
        purpose="scale_validation_training",
        owner_pid=42,
    )

    assert read_active_gpu_lease() == lease
    assert_scale_validation_gpu_lease_owner(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        purpose="scale_validation_training",
    )

    released = release_scale_validation_gpu_lease(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        reason="test complete",
    )
    assert released.state == "released"
    assert read_active_gpu_lease() is None


def test_s7_scale_validation_lease_preserves_family_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_SCENARIO_GPU_LEASE_ROOT", str(tmp_path))
    lease = acquire_scale_validation_gpu_lease(
        "s7-vlm-profile-01",
        source_commit="a" * 40,
        purpose="scale_validation_inference",
        scenario_id="S7",
        model_family="vlm",
    )

    asserted = assert_scale_validation_gpu_lease_owner(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        purpose="scale_validation_inference",
        scenario_id="S7",
        model_family="vlm",
    )
    assert asserted.scenario_id == "S7"
    assert asserted.model_family == "vlm"

    with pytest.raises(ScenarioWorkloadError):
        assert_scale_validation_gpu_lease_owner(
            run_id=lease.run_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            purpose="scale_validation_inference",
            scenario_id="S7",
            model_family="llm",
        )

    release_scale_validation_gpu_lease(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        reason="s7_test_complete",
    )


def test_e0_scale_validation_lease_is_exact_and_fail_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_SCENARIO_GPU_LEASE_ROOT", str(tmp_path))
    lease = acquire_scale_validation_gpu_lease(
        "s8-v4-e0-repetition-1",
        source_commit="a" * 40,
        purpose="scale_validation_inference",
        scenario_id="E0",
        model_family="tabular",
    )
    asserted = assert_scale_validation_gpu_lease_owner(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        purpose="scale_validation_inference",
        scenario_id="E0",
        model_family="tabular",
    )
    assert asserted == lease
    release_scale_validation_gpu_lease(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        reason="e0_test_complete",
    )
    with pytest.raises(ScenarioWorkloadError) as exc_info:
        acquire_scale_validation_gpu_lease(
            "s8-v4-e0-wrong-family",
            source_commit="a" * 40,
            purpose="scale_validation_inference",
            scenario_id="E0",
            model_family="llm",
        )
    assert exc_info.value.code == "scale_validation_gpu_lease_identity_invalid"
    assert exc_info.value.status_code == 422


def test_gpu_executor_forms_one_bounded_batch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "evm.model_runtime.gpu_batch_probe.assert_scale_validation_gpu_lease_owner",
        lambda **_kwargs: None,
    )
    backend = FakeBackend()
    executor = GpuBatchProbeExecutor(config(tmp_path), backend=backend)

    async def exercise() -> list:
        results = await asyncio.gather(*(executor.execute(request()) for _ in range(4)))
        await executor.shutdown()
        return results

    responses = asyncio.run(exercise())

    assert backend.batch_sizes == [4]
    assert {item.runtime.formed_batch_size for item in responses} == {4}
    assert all(item.runtime.configured_batch_size == 4 for item in responses)
    assert all(item.prediction == 1 for item in responses)


def test_gpu_executor_preserves_each_request_trace_inside_shared_batch(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "evm.model_runtime.gpu_batch_probe.assert_scale_validation_gpu_lease_owner",
        lambda **_kwargs: None,
    )
    parents = iter(["trace-1", "trace-2", "trace-3", "trace-4"])
    observed: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "evm.model_runtime.gpu_batch_probe.current_trace_context",
        lambda: next(parents),
    )

    @contextmanager
    def capture_span(name, *, parent, **_kwargs):
        observed.append((name, parent))
        yield

    monkeypatch.setattr("evm.model_runtime.gpu_batch_probe.trace_span", capture_span)
    executor = GpuBatchProbeExecutor(config(tmp_path), backend=FakeBackend())

    async def exercise() -> None:
        await asyncio.gather(*(executor.execute(request()) for _ in range(4)))
        await executor.shutdown()

    asyncio.run(exercise())

    assert observed.count(("s4.gpu_batch.compute", "trace-1")) == 1
    assert {parent for name, parent in observed if name == "s4.gpu_batch.worker"} == {
        "trace-1",
        "trace-2",
        "trace-3",
        "trace-4",
    }


def test_gpu_executor_fails_closed_on_identity_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "evm.model_runtime.gpu_batch_probe.assert_scale_validation_gpu_lease_owner",
        lambda **_kwargs: None,
    )
    executor = GpuBatchProbeExecutor(config(tmp_path), backend=FakeBackend())
    invalid = request().model_copy(update={"model_identity_sha256": "f" * 64})

    with pytest.raises(GpuBatchProbeError, match="Request identity"):
        asyncio.run(executor.execute(invalid))


def test_gpu_executor_requires_exact_inference_lease(tmp_path: Path, monkeypatch) -> None:
    def reject(**_kwargs):
        raise RuntimeError("stale lease")

    monkeypatch.setattr(
        "evm.model_runtime.gpu_batch_probe.assert_scale_validation_gpu_lease_owner",
        reject,
    )
    executor = GpuBatchProbeExecutor(config(tmp_path), backend=FakeBackend())

    with pytest.raises(RuntimeError, match="stale lease"):
        asyncio.run(executor.execute(request()))

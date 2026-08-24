from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import torch

from evm.scale_validation import e0_evidence
from evm.scale_validation.e0_evidence import (
    E0EvidenceValidationError,
    SOURCE_PATHS,
    validate_e0_evidence,
)
from evm.scale_validation.e0_runtime import (
    ATTEMPT_SCHEMA_VERSION,
    CLAIM_BOUNDARY,
    SCHEMA_VERSION,
    E0RuntimeConfig,
    E0RuntimeError,
    analyze_attempts,
    canonical,
    canonical_sha256,
    project_attempt,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_e0_environment_v1.toml"


def raw_attempt(config: E0RuntimeConfig, repetition: int) -> dict[str, object]:
    return {
        "schema_version": ATTEMPT_SCHEMA_VERSION,
        "attempt_id": f"e0-{repetition}-fixture",
        "repetition": repetition,
        "credit": "credit",
        "started_at": "2026-08-25T00:00:00Z",
        "finished_at": "2026-08-25T00:00:10Z",
        "environment": {
            "host_gpu": {"uuid": "GPU-fixture", "name": config.expected_gpu_name},
            "container_gpu": {"uuid": "GPU-fixture", "name": config.expected_gpu_name},
        },
        "image": {"repo_digest": config.triton_image_digest},
        "model": {
            "name": config.model_name,
            "version": config.model_version,
            "backend": config.backend,
            "repository_sha256": "1" * 64,
            "artifact_sha256": "2" * 64,
            "config_sha256": "3" * 64,
        },
        "runtime": {
            "server_live": True,
            "server_ready": True,
            "model_ready": True,
            "ready_seconds": 2.0,
        },
        "inference": {
            "transport_ok": True,
            "output": list(config.expected_output),
            "gpu_instance_kind": "KIND_GPU",
            "cpu_fallback_detected": False,
        },
        "metrics": {
            "direct_endpoint_ok": True,
            "prometheus_target_up": True,
            "prometheus_model_metric_queryable": True,
            "prometheus_up_seconds": 1.0,
            "triton_success_count": 1.0,
            "triton_compute_infer_count": 2.0,
            "gpu_memory_used_bytes": 1024.0,
        },
        "profiler": {
            "tool": "nsight-systems",
            "version": "fixture",
            "parseable": True,
            "cuda_kernel_count": 1,
            "timeline_sha256": "4" * 64,
        },
        "cleanup": {
            "elapsed_seconds": 3.0,
            "preflight_total_vram_mib": 16376.0,
            "vram_delta_mib": 1.0,
            "container_absent": True,
            "port_listeners_absent": True,
            "gpu_context_absent": True,
            "lease_absent": True,
            "prometheus_target_absent": True,
            "temporary_kubernetes_resources_absent": True,
            "queue_active_zero": True,
            "queue_leased_zero": True,
            "queue_outcome_unknown_zero": True,
            "b0_ready": True,
            "b0_cuda_inference": True,
            "orphan_count": 0,
        },
    }


def test_e0_attempt_projection_and_acceptance() -> None:
    config = E0RuntimeConfig.from_path(CONFIG)
    projected = [
        project_attempt(raw_attempt(config, repetition), config) for repetition in range(1, 4)
    ]
    assert all(item["passed"] for item in projected)
    analysis = analyze_attempts(projected, config)
    assert analysis["evidence_ready"] is True
    assert all(analysis["acceptance"].values())


def test_e0_attempt_fails_closed_on_identity_and_nonfinite() -> None:
    config = E0RuntimeConfig.from_path(CONFIG)
    identity = raw_attempt(config, 1)
    identity["environment"]["container_gpu"]["uuid"] = "GPU-wrong"  # type: ignore[index]
    with pytest.raises(E0RuntimeError, match="gpu_uuid_mismatch"):
        project_attempt(identity, config)
    nonfinite = raw_attempt(config, 1)
    nonfinite["runtime"]["ready_seconds"] = float("inf")  # type: ignore[index]
    with pytest.raises(E0RuntimeError, match="non_finite"):
        project_attempt(nonfinite, config)


def test_e0_attempt_does_not_credit_profiler_or_cleanup_mutation() -> None:
    config = E0RuntimeConfig.from_path(CONFIG)
    profiler = raw_attempt(config, 1)
    profiler["profiler"]["cuda_kernel_count"] = 0  # type: ignore[index]
    assert project_attempt(profiler, config)["passed"] is False
    cleanup = raw_attempt(config, 1)
    cleanup["cleanup"]["vram_delta_mib"] = 4096.0  # type: ignore[index]
    assert project_attempt(cleanup, config)["passed"] is False


def test_e0_evidence_recomputes_private_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = E0RuntimeConfig.from_path(CONFIG)
    revision = "a" * 40
    blob = {"path": "fixture", "blob_oid": "b" * 40, "sha256": "c" * 64}
    monkeypatch.setattr(e0_evidence, "_is_ancestor", lambda *_args: True)
    monkeypatch.setattr(e0_evidence, "git_blob_identity", lambda *_args: blob)
    attempts = []
    for repetition in range(1, 4):
        raw = raw_attempt(config, repetition)
        path = tmp_path / "attempts" / str(repetition) / "attempt-private.json"
        path.parent.mkdir(parents=True)
        path.write_text(canonical(raw) + "\n", encoding="utf-8", newline="\n")
        attempts.append(
            {
                "summary": project_attempt(raw, config),
                "private_evidence": {
                    "path": path.relative_to(tmp_path).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                },
            }
        )
    entries = [
        {
            "path": path.relative_to(tmp_path).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(tmp_path.rglob("attempt-private.json"))
    ]
    index = {
        "schema_version": "evm.s8_v4.e0_private_index.v1",
        "entries": entries,
        "aggregate_sha256": canonical_sha256(entries),
    }
    index_path = tmp_path / "private-evidence-index.json"
    index_path.write_text(canonical(index) + "\n", encoding="utf-8", newline="\n")
    analysis = analyze_attempts([item["summary"] for item in attempts], config)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "review_pending",
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "claim_boundary": CLAIM_BOUNDARY,
        "runtime_contract": config.public_dict(),
        "source_identity": {
            "runtime_revision": revision,
            "git_blobs": {label: blob for label in SOURCE_PATHS},
        },
        "attempts": attempts,
        "analysis": analysis,
        "acceptance": analysis["acceptance"],
        "alignment": {
            "definition_alignment": True,
            "experiment_purpose_alignment": True,
            "validation_purpose_alignment": True,
            "test_purpose_alignment": True,
        },
        "private_evidence": {
            "index_sha256": sha256_file(index_path),
            "aggregate_sha256": canonical_sha256(entries),
            "artifact_count": len(entries),
        },
    }
    result = validate_e0_evidence(
        payload,
        config=config,
        private_root=tmp_path,
        project_root=ROOT,
        validation_revision=revision,
    )
    assert result["valid"] is True
    mutated = copy.deepcopy(payload)
    mutated["attempts"][0]["summary"]["ready_seconds"] = 999
    with pytest.raises(E0EvidenceValidationError, match="attempt_projection"):
        validate_e0_evidence(
            mutated,
            config=config,
            private_root=tmp_path,
            project_root=ROOT,
            validation_revision=revision,
        )


def test_e0_generator_is_deterministic(tmp_path: Path) -> None:
    import subprocess
    import sys

    roots = [tmp_path / "a", tmp_path / "b"]
    manifests = []
    for root in roots:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/dev/generate_e0_triton_model.py"),
                "--output",
                str(root),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        manifests.append(json.loads(completed.stdout))
    assert manifests[0] == manifests[1]
    artifact = roots[0] / "e0_cuda_linear/1/model.pt"
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == manifests[0]["artifact_sha256"]
    loaded = torch.jit.load(artifact)
    actual = loaded(torch.tensor([[1.0, 2.0, 3.0, 4.0]])).tolist()[0]
    assert actual == [3.0, 5.0, 7.0, 9.0]
    assert manifests[0]["backend"] == "pytorch"

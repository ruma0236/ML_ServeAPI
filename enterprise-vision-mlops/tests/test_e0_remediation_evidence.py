from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.scale_validation.e0_remediation_evidence import (
    E0RemediationEvidenceError,
    _regression_summary,
    validate_private_index,
    write_private_index,
)
from evm.scale_validation.e0_strict_evidence import public_strict_projection


def test_public_strict_projection_excludes_private_runtime_identity() -> None:
    strict = {
        "schema_version": "strict",
        "strict_contract": {},
        "acceptance": {"E0-AC-01": True},
        "evidence_ready": True,
        "final_cleanup": {},
        "claim_boundary": "local",
        "attempts": [
            {
                "attempt_id": "private-attempt",
                "repetition": 1,
                "credit": "credit",
                "model_identity": {"artifact_sha256": "a" * 64},
                "gpu_uuid": "GPU-private",
                "request_count": 100,
                "output_correct": True,
                "gpu_instance_proven": True,
                "direct_metrics": {"request_success_count": 100},
                "prometheus": {
                    "request_success": {
                        "metric_identity": {
                            "attempt_id": "private-attempt",
                            "gpu_uuid": "GPU-private",
                        },
                        "sample_count": 2,
                        "maximum": 100,
                    }
                },
                "profiler": {
                    "qualification_method": "cupti-activity",
                    "kernel_records": [
                        {
                            "kernel_name": "private-kernel",
                            "start": 1,
                            "end": 2,
                            "device": 0,
                            "stream": 7,
                        }
                    ],
                    "context_collected": False,
                    "triton_inference_traced": False,
                    "claim_boundary": "standalone",
                },
                "cleanup": {"orphan_count": 0},
                "acceptance": {"E0-AC-01": True},
            }
        ],
    }
    public = public_strict_projection(strict)
    encoded = json.dumps(public)
    assert "private-attempt" not in encoded
    assert "GPU-private" not in encoded
    assert "private-kernel" not in encoded
    assert public["attempts"][0]["attempt_identity_sha256"]


def test_private_remediation_index_rehashes_actual_bytes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    write_private_index(tmp_path, generated_at="2026-08-25T00:00:00Z")
    assert validate_private_index(tmp_path)["artifact_count"] == 1
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(E0RemediationEvidenceError, match="private_index"):
        validate_private_index(tmp_path)


def test_regression_summary_rejects_failure_and_parses_zero_skip() -> None:
    assert _regression_summary("full_python", "935 passed, 2 warnings in 1.0s\n") == {
        "result": "passed",
        "tests_passed": 935,
        "tests_skipped": 0,
        "skipped_nodes": [],
    }
    with pytest.raises(E0RemediationEvidenceError, match="failure_text"):
        _regression_summary("full_python", "1 passed\nFAILED test_example\n")

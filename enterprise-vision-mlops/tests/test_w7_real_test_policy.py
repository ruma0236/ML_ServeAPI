from __future__ import annotations

from evm.control_panel.real_test_policy import (
    ClosureRecord,
    forbidden_evidence_claims,
    validate_real_test_policy,
)
from evm.control_panel.schemas import (
    CycleRun,
    DatasetVersion,
    ModelCandidate,
    ModelExperimentMatrix,
    ModelVersion,
    RealTestPolicy,
    ResourceRef,
    ServingState,
)


def _cycle(policy: RealTestPolicy, *, placeholder: bool = False) -> CycleRun:
    return CycleRun(
        cycle_id="cycle-w7-test",
        status="running",
        started_at="2026-07-09T00:00:00Z",
        stages=[],
        dataset=DatasetVersion(
            dataset_id="visa-open-data",
            version="visa-open-data-f1f1c9ee9922",
            record_count=10821,
            storage_uri="s3://validated/visa-open-data-f1f1c9ee9922",
            quality_status="pass",
        ),
        model=ModelVersion(
            model_name="vision-baseline",
            version="10",
            stage="Shadow",
            model_type="image_feature_centroid",
            registry_uri="artifacts/registry/vision-baseline/latest.json",
        ),
        serving=ServingState(
            status="pass",
            endpoint="http://localhost:8000",
            model_loaded=True,
            model_version="10",
            placeholder=placeholder,
        ),
        resources=[ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-api")],
        model_matrix=ModelExperimentMatrix(
            matrix_id="w7-efficientnet-real-test-matrix",
            status="queued",
            execution_mode="parallel",
            framework="torch",
            real_test_policy=policy,
            candidates=[
                ModelCandidate(
                    candidate_id="effnet-b0-img224-freeze-adamw",
                    framework="torch",
                    architecture="efficientnet-b0",
                    backbone="torchvision.models.efficientnet_b0",
                    status="queued",
                    dataset_version="visa-open-data-f1f1c9ee9922",
                    resource_profile="gpu-rtx4080-b0-parallel",
                    conditions={},
                )
            ],
        ),
    )


def _strict_policy() -> RealTestPolicy:
    return RealTestPolicy(
        mock_allowed=False,
        smoke_allowed=False,
        requires_real_dataset=True,
        requires_real_training=True,
        minimum_records=10821,
        dataset_version="visa-open-data-f1f1c9ee9922",
    )


def test_w7_real_test_policy_allows_strict_policy_with_guarded_language():
    records = [
        ClosureRecord(
            source_id="EVM-238-A",
            title="W7 real-test policy guard",
            status="Done",
            sprint="2026-07-W7",
            evidence="guard blocks mock adapters, placeholder predictions, synthetic-only fixtures, and smoke-only evidence",
        )
    ]

    report = validate_real_test_policy(_cycle(_strict_policy()), records)

    assert report["valid"] is True
    assert report["checked_done_records"] == 1
    assert report["violations"] == []


def test_w7_real_test_policy_blocks_mock_or_smoke_allowed_flags():
    weak_policy = RealTestPolicy(
        mock_allowed=True,
        smoke_allowed=True,
        requires_real_dataset=False,
        requires_real_training=False,
    )

    report = validate_real_test_policy(_cycle(weak_policy))

    assert report["valid"] is False
    assert {violation["code"] for violation in report["violations"]} == {
        "mock_allowed_true",
        "smoke_allowed_true",
        "real_dataset_not_required",
        "real_training_not_required",
    }


def test_w7_real_test_policy_blocks_placeholder_serving():
    report = validate_real_test_policy(_cycle(_strict_policy(), placeholder=True))

    assert report["valid"] is False
    assert report["violations"][0]["code"] == "placeholder_serving_active"


def test_w7_real_test_policy_blocks_done_records_that_use_forbidden_evidence():
    records = [
        ClosureRecord(
            source_id="EVM-237",
            title="Torch EfficientNet-B0/B7 Real Model Matrix",
            status="Done",
            sprint="2026-07-W7",
            evidence="completed from smoke-only check and mock adapter",
        )
    ]

    report = validate_real_test_policy(_cycle(_strict_policy()), records)

    assert report["valid"] is False
    violation = report["violations"][0]
    assert violation["code"] == "forbidden_done_evidence"
    assert violation["source_id"] == "EVM-237"
    assert violation["terms"] == ["mock adapter", "smoke-only"]


def test_forbidden_evidence_claims_ignores_explicit_guard_language():
    assert forbidden_evidence_claims("guard blocks mock adapter and smoke-only evidence") == []
    assert forbidden_evidence_claims("completed by smoke-only check") == ["smoke-only"]

from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.promotion_policy import evaluate_promotion_policy
from evm.control_panel.schemas import PromotionPolicyInput


FIXTURE_ROOT = Path("tests/fixtures/promotion")
POLICY_PATH = Path("configs/promotion_policy.toml")


def fixture(name: str) -> PromotionPolicyInput:
    return PromotionPolicyInput.model_validate(
        json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    )


def test_staging_allows_only_when_namespace_readiness_ci_and_ownership_pass():
    decision = evaluate_promotion_policy(
        fixture("staging-pass.json"),
        policy_path=POLICY_PATH,
        evaluated_at="2026-07-10T10:00:00Z",
    )

    assert decision.decision == "allow"
    assert decision.status == "pass"
    assert decision.reason_codes == []
    assert decision.required_checks == ["ownership", "namespace", "readiness", "ci"]
    assert all(check.status == "pass" for check in decision.checks if check.required)


def test_production_without_approver_is_pending_not_allowed():
    decision = evaluate_promotion_policy(
        fixture("production-missing-approval.json"),
        policy_path=POLICY_PATH,
        evaluated_at="2026-07-10T10:00:00Z",
    )

    assert decision.decision == "pending_approval"
    assert decision.status == "queued"
    assert decision.reason_codes == ["approver_required"]
    assert decision.required_approvals == ["ml-platform", "ai-infra-sre", "release-manager"]


def test_production_rejects_same_requester_and_approver():
    inputs = fixture("production-missing-approval.json").model_copy(
        update={"approver": "ml-platform"}
    )

    decision = evaluate_promotion_policy(inputs, policy_path=POLICY_PATH)

    assert decision.decision == "blocked"
    assert decision.reason_codes == ["requester_approver_conflict"]


def test_namespace_mismatch_blocks_with_deterministic_decision_id():
    inputs = fixture("staging-pass.json").model_copy(update={"target_namespace": "evm-platform"})

    first = evaluate_promotion_policy(
        inputs,
        policy_path=POLICY_PATH,
        evaluated_at="2026-07-10T10:00:00Z",
    )
    second = evaluate_promotion_policy(
        inputs,
        policy_path=POLICY_PATH,
        evaluated_at="2026-07-10T10:05:00Z",
    )

    assert first.decision == "blocked"
    assert first.reason_codes == ["namespace_not_allowed"]
    assert first.decision_id == second.decision_id
    assert first.input_digest == second.input_digest


def test_persisted_audit_contains_policy_decision_and_evaluated_inputs(tmp_path):
    inputs = fixture("staging-pass.json")

    decision = evaluate_promotion_policy(
        inputs,
        policy_path=POLICY_PATH,
        evidence_root=tmp_path,
        persist=True,
    )
    audit = json.loads(
        (tmp_path / decision.decision_id / "policy_decision.json").read_text(encoding="utf-8")
    )

    assert decision.audit_uri
    assert audit["decision"]["decision_id"] == decision.decision_id
    assert audit["evaluated_inputs"]["target_namespace"] == "evm-staging"
    assert (tmp_path / "latest_promotion_policy.json").exists()

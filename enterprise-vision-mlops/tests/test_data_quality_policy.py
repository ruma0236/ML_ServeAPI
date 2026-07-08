from __future__ import annotations

from evm.data_quality.policy import load_quality_policy


def test_quality_policy_override_can_block_warning_code(tmp_path):
    policy_path = tmp_path / "quality_policy.toml"
    policy_path.write_text(
        "\n".join(
            [
                "[policy]",
                'id = "test_policy"',
                'version = "v1"',
                'fail_levels = ["error"]',
                "",
                "[severity]",
                'duplicate_content_hash = "error"',
            ]
        ),
        encoding="utf-8",
    )

    policy = load_quality_policy(policy_path)
    issue = policy.issue("warn", "duplicate_content_hash", "duplicate sample")
    decision = policy.evaluate([issue])

    assert issue.level == "error"
    assert decision.status == "fail"
    assert decision.blocking_count == 1


def test_quality_policy_warns_without_blocking_by_default():
    policy = load_quality_policy(
        None,
        severity_defaults={"duplicate_content_hash": "warn"},
    )
    issue = policy.issue("warn", "duplicate_content_hash", "duplicate sample")
    decision = policy.evaluate([issue])

    assert decision.status == "pass"
    assert decision.decision == "pass_with_warnings"
    assert decision.warning_count == 1

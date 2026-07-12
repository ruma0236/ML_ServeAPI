from pathlib import Path

from scripts.dev.validate_scale_serving_decision import validate


def test_scale_serving_decision_assigns_one_tool_to_each_operational_role():
    report = validate(Path("configs/scale_serving_decision.toml"))

    assert report["status"] == "pass"
    assert report["selected_pilot"] == "kserve-triton"
    assert report["candidate_count"] == 5
    assert report["required_phase_count"] == 6
    assert report["design_only"] is True
    assert report["runtime_execution_claimed"] is False

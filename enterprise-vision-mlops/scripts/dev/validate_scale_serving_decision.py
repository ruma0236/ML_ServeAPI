from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path


REQUIRED_CANDIDATES = {"vllm", "triton", "kserve", "ray-serve", "kueue"}
REQUIRED_PHASES = {
    "artifact_export",
    "protocol_conformance",
    "load_benchmark",
    "staging_canary",
    "failure_recovery",
    "rollback",
}


def validate(path: Path) -> dict[str, object]:
    with path.open("rb") as fp:
        decision = tomllib.load(fp)
    blockers: list[str] = []
    candidates = {item.get("name"): item for item in decision.get("candidates", [])}
    roles = decision.get("roles", {})
    policy = decision.get("decision", {})
    acceptance = decision.get("acceptance", {})

    if set(candidates) != REQUIRED_CANDIDATES:
        blockers.append("candidate_matrix_incomplete")
    if roles.get("vision_model_runtime") != "triton":
        blockers.append("vision_runtime_selection_invalid")
    if roles.get("online_control_plane") != "kserve":
        blockers.append("online_control_plane_selection_invalid")
    if roles.get("generative_vlm_runtime") != "vllm":
        blockers.append("generative_runtime_selection_invalid")
    if roles.get("batch_and_training_admission") != "kueue":
        blockers.append("batch_admission_selection_invalid")
    if policy.get("vllm_for_efficientnet") is not False:
        blockers.append("vllm_must_not_serve_efficientnet")
    if policy.get("kueue_on_online_request_path") is not False:
        blockers.append("kueue_must_not_be_on_online_request_path")
    if policy.get("production_cutover") != "blocked_until_benchmark_and_rollback_proof":
        blockers.append("production_cutover_gate_missing")
    if set(acceptance.get("required_phases", [])) != REQUIRED_PHASES:
        blockers.append("pilot_acceptance_phases_incomplete")
    if decision.get("design_only") is not True or decision.get("runtime_execution_claimed"):
        blockers.append("design_runtime_boundary_invalid")

    return {
        "schema_version": "evm.scale_serving.validation.v1",
        "status": "pass" if not blockers else "blocked",
        "selected_pilot": policy.get("selected_pilot"),
        "candidate_count": len(candidates),
        "required_phase_count": len(acceptance.get("required_phases", [])),
        "design_only": decision.get("design_only"),
        "runtime_execution_claimed": decision.get("runtime_execution_claimed"),
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/scale_serving_decision.toml")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = validate(Path(args.config))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

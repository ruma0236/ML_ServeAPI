from __future__ import annotations

import argparse
import json
from pathlib import Path

from evm.operations.scenario_b_replay_runtime import execute_real_replay


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Scenario B real VisA/CUDA replay.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidate-summary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--stable-readiness-url", required=True)
    parser.add_argument("--stable-predict-url", required=True)
    parser.add_argument("--prometheus-targets-url", required=True)
    parser.add_argument("--prometheus-job", required=True)
    parser.add_argument("--prometheus-instance", required=True)
    parser.add_argument("--host-data-root", required=True)
    parser.add_argument("--warmup-requests", type=int, default=10)
    parser.add_argument("--inject-error-count", type=int, default=0)
    parser.add_argument(
        "--expect-state",
        choices=("blocked_admission", "canary_passed", "rolled_back"),
        required=True,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result, index_path = execute_real_replay(
        run_id=args.run_id,
        config_path=args.config,
        candidate_summary_path=args.candidate_summary,
        model_path=args.model,
        manifest_path=args.manifest,
        evidence_root=args.evidence_root,
        stable_readiness_url=args.stable_readiness_url,
        stable_predict_url=args.stable_predict_url,
        prometheus_targets_url=args.prometheus_targets_url,
        prometheus_job=args.prometheus_job,
        prometheus_instance=args.prometheus_instance,
        host_data_root=args.host_data_root,
        warmup_requests=args.warmup_requests,
        inject_error_count=args.inject_error_count,
    )
    if result.decision.state != args.expect_state:
        raise SystemExit(
            f"unexpected decision: expected={args.expect_state} actual={result.decision.state}"
        )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "decision": result.decision.model_dump(mode="json"),
                "metric_window": (
                    result.metric_window.model_dump(mode="json")
                    if result.metric_window
                    else None
                ),
                "rollback": result.rollback.model_dump(mode="json"),
                "evidence_index": str(index_path),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()


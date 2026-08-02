from __future__ import annotations

import argparse
import json
from pathlib import Path

from evm.operations.scenario_b_replay_runtime import (
    ReplayExecutionContext,
    execute_real_replay,
)


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
    parser.add_argument("--expect-blocker", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--source-dirty", action="store_true")
    parser.add_argument("--api-revision", required=True)
    parser.add_argument("--worker-revision", required=True)
    parser.add_argument("--observer-revision", required=True)
    parser.add_argument("--cluster-context", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--target-namespace", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--target-uid", required=True)
    parser.add_argument("--actor", default="codex-scenario-b-runner")
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
        expected_state=args.expect_state,
        expected_blocker=args.expect_blocker,
        execution_context=ReplayExecutionContext(
            source_commit=args.source_commit,
            source_branch=args.source_branch,
            source_dirty=args.source_dirty,
            api_revision=args.api_revision,
            worker_revision=args.worker_revision,
            observer_revision=args.observer_revision,
            cluster_context=args.cluster_context,
            node=args.node,
            target_namespace=args.target_namespace,
            target_name=args.target_name,
            target_uid=args.target_uid,
            actor=args.actor,
        ),
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
                    result.metric_window.model_dump(mode="json") if result.metric_window else None
                ),
                "rollback": result.rollback.model_dump(mode="json"),
                "evidence_index": str(index_path),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

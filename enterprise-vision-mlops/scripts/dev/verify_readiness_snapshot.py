from __future__ import annotations

import argparse
import json
from pathlib import Path

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.readiness_evaluator import runtime_path
from evm.core.config import get_nested, load_config, map_runtime_data_path, resolve_path
from evm.core.readiness_snapshot import read_json, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify that model readiness is isolated from mutable latest data evidence.",
    )
    parser.add_argument("--config", default="configs/local_visa.toml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-mutable-mismatch", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    dataset_metadata_value = str(
        get_nested(
            config,
            "pipelines.data_validation.dataset_metadata",
            "data/validated/visa/dataset_version.json",
        )
    )
    dataset_metadata = map_runtime_data_path(dataset_metadata_value)
    if not dataset_metadata.is_absolute():
        dataset_metadata = resolve_path(config, dataset_metadata)
    dataset_metadata = runtime_path(dataset_metadata)
    mutable_dataset = read_json(dataset_metadata)
    cycle = build_latest_cycle(args.config)
    evaluation = cycle.readiness_evaluation
    if evaluation is None:
        raise SystemExit("CycleRun.readiness_evaluation is missing")

    immutable_check_ids = {
        "dataset_metadata",
        "source_shard_index",
        "quality_gate",
    }
    immutable_checks = [
        check
        for check in evaluation.checks
        if check.check_id in immutable_check_ids
    ]
    selected_version = evaluation.dataset_version
    mutable_version = str(mutable_dataset.get("dataset_version") or "")
    mutable_mismatch = mutable_version != selected_version
    blockers: list[str] = []
    if evaluation.decision != "ready" or evaluation.blockers:
        blockers.append("readiness_not_ready")
    if len(immutable_checks) != len(immutable_check_ids):
        blockers.append("immutable_check_set_incomplete")
    if any(
        not check.evidence_uri or "/_readiness_inputs/" not in check.evidence_uri
        for check in immutable_checks
    ):
        blockers.append("mutable_evidence_uri_detected")
    if args.require_mutable_mismatch and not mutable_mismatch:
        blockers.append("mutable_latest_did_not_change_for_isolation_proof")

    candidate_check = next(
        check
        for check in evaluation.checks
        if check.check_id == "evaluation_report"
    )
    candidate_summary = (
        read_json(runtime_path(candidate_check.evidence_uri))
        if candidate_check.evidence_uri
        else {}
    )
    report = {
        "schema_version": "evm.post_w7.readiness_snapshot_verification.v1",
        "status": "pass" if not blockers else "blocked",
        "candidate_id": evaluation.candidate_id,
        "selected_dataset_version": selected_version,
        "mutable_latest_dataset_version": mutable_version,
        "mutable_latest_mismatch_observed": mutable_mismatch,
        "readiness_evaluation_id": evaluation.evaluation_id,
        "readiness_decision": evaluation.decision,
        "readiness_blockers": evaluation.blockers,
        "immutable_checks": [
            {
                "check_id": check.check_id,
                "status": check.status,
                "evidence_uri": check.evidence_uri,
                "evidence_digest": check.evidence_digest,
            }
            for check in immutable_checks
        ],
        "snapshot_manifest": candidate_summary.get("readiness_snapshot_manifest"),
        "snapshot_manifest_sha256": candidate_summary.get(
            "readiness_snapshot_manifest_sha256"
        ),
        "verification_blockers": blockers,
    }
    output = Path(args.output)
    write_json(output, report)
    print(json.dumps(report, indent=2))
    if blockers:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

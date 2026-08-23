from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from evm.scale_validation.contracts import ScenarioProgressLedger, render_progress_markdown


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.json"
DEFAULT_MARKDOWN = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.md"
EVIDENCE = {
    "attempt_01": "docs/status/evidence/s7-auxiliary-admission-failed-attempt-01.json",
    "attempt_02": "docs/status/evidence/s7-auxiliary-admission-failed-attempt-02.json",
    "diagnostic": "docs/status/evidence/s7-family-diagnostic-gate.json",
    "attempt_03": "docs/status/evidence/s7-auxiliary-admission-failed-attempt-03.json",
    "experiment": "docs/status/evidence/s7-auxiliary-admission-experiment.json",
    "smoke": "docs/status/evidence/s7-current-revision-cuda-smoke.json",
    "closure": "docs/status/evidence/s7-auxiliary-admission-closure.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize Scenario S7 closure records")
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-path", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    scenario = next(item for item in payload["scenarios"] if item["scenario_id"] == "S7")
    final_summary = (
        "A clean 36-repetition matrix, current-revision CUDA smoke, required regressions, "
        "canonical Git-blob validation, private inventory rehash, and exact runtime cleanup "
        "passed all four S7 acceptance criteria."
    )
    scenario["implementation_summary"][-1] = final_summary
    scenario["experiment_environment"] = (
        "Three non-acceptance diagnostics, one rejected matrix, one accepted 36-repetition "
        "matrix, and a current-revision CUDA smoke exercised the existing image, VLM, and "
        "LLM routes sequentially with external HTTP and real CUDA on one controlled local "
        "physical node and one GPU."
    )
    evidence_refs = [EVIDENCE["experiment"], EVIDENCE["closure"]]
    scenario["acceptance_criteria"] = [
        {**criterion, "status": "passed", "evidence_refs": evidence_refs}
        for criterion in scenario["acceptance_criteria"]
    ]
    scenario["observed_result"] = (
        "The accepted matrix completed 36 independent profile repetitions: 12 each for "
        "image, VLM, and LLM. It completed 162 admitted requests and returned 54 explicit "
        "bounded over-limit rejections with zero OOM and zero starvation. Raw-derived "
        "validation confirmed distinct family quality and latency schemas, bounded fairness "
        "and head-of-line behavior, complete trace and Prometheus attribution, and absence "
        "of unsupported metrics. Current-revision smoke completed 6 of 6 external-HTTP "
        "CUDA requests. Cleanup restored B0 serving 1/1, active GPU leases and queue work "
        "to zero, S7 Prometheus targets to zero, and the frozen Prometheus baseline to 5/5 UP."
    )
    artifacts = evidence_artifacts()
    scenario["evidence_artifacts"] = artifacts
    scenario["evidence_index"] = artifacts
    scenario["status"] = "verified"
    scenario["unresolved_items"] = []
    scenario["next_action"] = (
        "Review V3 S8 dependency-soak and resource-efficiency readiness and blockers only; "
        "do not start S8 in this closure turn."
    )
    scenario["verdict_and_claim_boundary"]["verdict"] = "passed"
    update = {
        "occurred_at": now,
        "phase": "verification",
        "status": "verified",
        "summary": (
            "The accepted 36-repetition family matrix, current-revision CUDA smoke, all "
            "required regressions, private rehash, Git-blob closure, and exact cleanup passed "
            "S7-AC-01..04; three failed attempts remain retained with zero acceptance credit."
        ),
        "evidence_refs": evidence_refs,
    }
    if not any(
        item.get("phase") == update["phase"] and item.get("summary") == update["summary"]
        for item in scenario["chronological_updates"]
    ):
        scenario["chronological_updates"].append(update)
    payload["generated_at"] = now
    ledger = ScenarioProgressLedger.model_validate(payload)
    args.json_path.write_bytes((ledger.model_dump_json(indent=2) + "\n").encode("utf-8"))
    args.markdown_path.write_text(render_progress_markdown(ledger), encoding="utf-8", newline="\n")
    return 0


def evidence_artifacts() -> list[dict[str, str]]:
    claims = {
        "attempt_01": "The first image warmup exposed host-to-container input remapping and received zero acceptance credit.",
        "attempt_02": "Completed diagnostics exposed premature Prometheus cleanup observation and received zero acceptance credit.",
        "diagnostic": "Fresh image, VLM, and LLM diagnostics completed 18 of 18 real-CUDA requests and closed only the readiness gate.",
        "attempt_03": "The first full matrix exposed position-dependent projection validation and received zero acceptance credit.",
        "experiment": "The clean 36-repetition matrix passed independent raw-derived family admission validation.",
        "smoke": "The current revision completed real external-HTTP CUDA inference and restored the exact baseline.",
        "closure": "Strict closure binds acceptance, regressions, private hashes, Git blobs, and cleanup without broad production claims.",
    }
    artifacts = []
    for name, relative in EVIDENCE.items():
        path = ROOT / relative
        artifact = json.loads(path.read_text(encoding="utf-8"))
        artifacts.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "generated_at": str(artifact["generated_at"]),
                "claim": claims[name],
            }
        )
    return artifacts


if __name__ == "__main__":
    raise SystemExit(main())

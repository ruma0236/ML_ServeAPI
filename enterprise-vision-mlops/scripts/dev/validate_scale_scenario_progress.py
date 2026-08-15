from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.contracts import (  # noqa: E402
    BenchmarkEvidence,
    EvidenceArtifact,
    ScenarioProgressLedger,
    render_progress_markdown,
)
from evm.scale_validation.evidence import (  # noqa: E402
    PublicEvidenceIntegrityError,
    git_blob_loader,
    verify_public_artifacts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the public S0-S8 progress ledger.")
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument(
        "--git-revision",
        help="Rehash canonical public evidence from this Git revision instead of the worktree.",
    )
    return parser.parse_args()


def ledger_artifacts(ledger: ScenarioProgressLedger) -> list[EvidenceArtifact]:
    return [
        artifact
        for scenario in ledger.scenarios
        for artifact in scenario.evidence_artifacts
    ]


def worktree_loader(path: str) -> bytes:
    return (ROOT / path).read_bytes()


def validate_embedded_benchmark_hashes(
    *,
    load_bytes,
) -> int:
    benchmark_path = "docs/status/evidence/s0-low-load-benchmark-evidence.json"
    benchmark = BenchmarkEvidence.model_validate_json(load_bytes(benchmark_path))
    artifacts = [
        artifact
        for control in benchmark.control_runs
        for artifact in control.evidence_artifacts
    ]
    artifacts.append(benchmark.trace_propagation.trace_artifact)
    return len(verify_public_artifacts(artifacts, load_bytes=load_bytes))


def main() -> int:
    args = parse_args()
    try:
        ledger = ScenarioProgressLedger.model_validate_json(
            args.progress.read_text(encoding="utf-8")
        )
        if args.markdown:
            expected = render_progress_markdown(ledger)
            observed = args.markdown.read_text(encoding="utf-8")
            if observed != expected:
                raise ValueError("Markdown progress does not match the canonical JSON ledger")
        load_bytes = (
            git_blob_loader(ROOT, args.git_revision)
            if args.git_revision
            else worktree_loader
        )
        verified_artifacts = verify_public_artifacts(
            ledger_artifacts(ledger),
            load_bytes=load_bytes,
        )
        embedded_artifacts = validate_embedded_benchmark_hashes(load_bytes=load_bytes)
    except (
        OSError,
        subprocess.CalledProcessError,
        ValidationError,
        ValueError,
        PublicEvidenceIntegrityError,
    ) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=True))
        return 2

    print(
        json.dumps(
            {
                "status": "valid",
                "scenario_count": len(ledger.scenarios),
                "statuses": {
                    scenario.scenario_id: scenario.status for scenario in ledger.scenarios
                },
                "hash_source": args.git_revision or "worktree",
                "verified_public_artifacts": len(verified_artifacts),
                "verified_embedded_artifacts": embedded_artifacts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

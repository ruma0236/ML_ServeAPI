from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.contracts import (  # noqa: E402
    ScenarioProgressLedger,
    render_progress_markdown,
)


JSON_PATH = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.json"
MARKDOWN_PATH = ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.md"


def main() -> int:
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updates = {
        "S5": {
            "summary": (
                "Independent post-closure audit reopened S5: the historical 30-point "
                "matrix remains immutable, but closure v1 trusted regression, runtime-smoke, "
                "and cleanup summaries instead of independently validating their raw logs "
                "and current-revision measurements."
            ),
            "unresolved": [
                "Generate and validate canonical current-revision S5 runtime smoke evidence.",
                "Bind command, exit code, test count, and private log hashes for required regressions.",
                "Reclose from the unchanged 30-point matrix only after mutation tests and cleanup pass.",
            ],
        },
        "S7": {
            "summary": (
                "Independent post-closure audit reopened S7: closure v1 did not recompute "
                "its numerical totals, conflated 54 intentional over-limit pre-admission "
                "rejections with selected/admitted starvation, and did not bind family asset "
                "provenance or observed LLM 4-bit readiness."
            ),
            "unresolved": [
                "Reproject the immutable 36-run matrix with scoped starvation and rejection accounting.",
                "Bind dataset/model license, provenance, and cache manifests for all families.",
                "Capture all-family current-revision CUDA ready identity and observed LLM 4-bit loading.",
                "Reclose only after numerical mutation tests, regressions, and exact cleanup pass.",
            ],
        },
    }
    for scenario in payload["scenarios"]:
        scenario_id = scenario["scenario_id"]
        if scenario_id not in updates:
            continue
        update = updates[scenario_id]
        scenario["status"] = "implementing"
        scenario["observed_result"] = None
        scenario["unresolved_items"] = update["unresolved"]
        scenario["next_action"] = update["unresolved"][0]
        scenario["verdict_and_claim_boundary"]["verdict"] = "not_run"
        for criterion in scenario["acceptance_criteria"]:
            criterion["status"] = "pending"
        scenario["implementation_summary"].append(update["summary"])
        scenario["chronological_updates"].append(
            {
                "occurred_at": now,
                "phase": "verification",
                "status": "implementing",
                "summary": update["summary"],
                "evidence_refs": [
                    "docs/status/evidence/s5-spark-data-scale-closure.json"
                    if scenario_id == "S5"
                    else "docs/status/evidence/s7-auxiliary-admission-closure.json"
                ],
            }
        )
    payload["generated_at"] = now
    ledger = ScenarioProgressLedger.model_validate(payload)
    JSON_PATH.write_bytes((ledger.model_dump_json(indent=2) + "\n").encode("utf-8"))
    MARKDOWN_PATH.write_text(
        render_progress_markdown(ledger), encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

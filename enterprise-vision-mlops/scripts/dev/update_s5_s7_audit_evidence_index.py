from __future__ import annotations

import hashlib
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
NEW_S7_ARTIFACTS = {
    "docs/status/evidence/s7-post-closure-smoke-attempt-01.json": (
        "The first post-closure current-revision smoke stopped before runtime mutation "
        "because the image curation manifest had been regenerated; it receives zero credit."
    ),
    "docs/status/evidence/s7-auxiliary-admission-reprojection.json": (
        "The immutable 36-run matrix is reprojected with selected/admitted starvation "
        "separated from intentional over-limit pre-admission rejection."
    ),
}


def main() -> int:
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for scenario in payload["scenarios"]:
        if scenario["scenario_id"] not in {"S5", "S7"}:
            continue
        by_path = {item["path"]: item for item in scenario["evidence_artifacts"]}
        if scenario["scenario_id"] == "S7":
            for relative, claim in NEW_S7_ARTIFACTS.items():
                by_path.setdefault(
                    relative,
                    {"path": relative, "sha256": "0" * 64, "generated_at": now, "claim": claim},
                )
        for relative, item in by_path.items():
            path = ROOT / relative
            if not path.is_file():
                continue
            raw = path.read_bytes()
            item["sha256"] = hashlib.sha256(raw).hexdigest()
            document = json.loads(raw)
            item["generated_at"] = str(document.get("generated_at") or now)
        artifacts = sorted(by_path.values(), key=lambda item: item["path"])
        scenario["evidence_artifacts"] = artifacts
        scenario["evidence_index"] = artifacts
    payload["generated_at"] = now
    ledger = ScenarioProgressLedger.model_validate(payload)
    JSON_PATH.write_bytes((ledger.model_dump_json(indent=2) + "\n").encode("utf-8"))
    MARKDOWN_PATH.write_text(
        render_progress_markdown(ledger), encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

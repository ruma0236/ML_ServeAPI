from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.contracts import (  # noqa: E402
    ScenarioProgressLedger,
    render_progress_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the public S0-S8 progress ledger.")
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    return parser.parse_args()


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
    except (OSError, ValidationError, ValueError) as exc:
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
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

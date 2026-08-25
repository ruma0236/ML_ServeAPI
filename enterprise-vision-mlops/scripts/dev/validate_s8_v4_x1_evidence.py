from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.x1_evidence import validate_x1_evidence
from evm.scale_validation.x1_runtime import X1RuntimeConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S8-V4 X1 evidence.")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_x1_concurrency_v2.toml",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "docs/status/2026-08-24-s8-v4-progress-ledger.jsonl",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw = args.evidence.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise RuntimeError("x1_public_evidence_not_canonical_lf")
    payload = json.loads(raw)
    result = validate_x1_evidence(
        payload,
        config=X1RuntimeConfig.from_path(args.config),
        project_root=ROOT,
        ledger_path=args.ledger,
    )
    result["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

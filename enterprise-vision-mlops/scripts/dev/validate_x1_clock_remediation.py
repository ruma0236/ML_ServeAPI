from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.clock_remediation_evidence import (  # noqa: E402
    validate_cleanup_correction,
    validate_no_go,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--correction-evidence", type=Path)
    parser.add_argument("--correction-private-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.evidence.read_bytes())
    projected = validate_no_go(payload, args.private_root)
    correction = None
    if (args.correction_evidence is None) != (args.correction_private_root is None):
        raise ValueError("correction evidence and private root must be provided together")
    if args.correction_evidence is not None and args.correction_private_root is not None:
        correction_payload = json.loads(args.correction_evidence.read_bytes())
        correction = validate_cleanup_correction(
            correction_payload,
            args.correction_private_root,
        )
    print(
        json.dumps(
            {
                "decision": projected["decision"],
                "private_evidence": projected["private_evidence"],
                "cleanup_correction": None
                if correction is None
                else correction["private_correction"],
                "valid": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

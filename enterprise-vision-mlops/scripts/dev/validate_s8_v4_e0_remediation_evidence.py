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

from evm.scale_validation.e0_remediation_evidence import (  # noqa: E402
    validate_remediation_review,
)
from evm.scale_validation.e0_runtime import E0RuntimeConfig, canonical  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate strict E0 remediation evidence.")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-e0-remediation-review.json",
    )
    parser.add_argument(
        "--original-evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-e0-environment-experiment.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_e0_environment_v1.toml",
    )
    parser.add_argument("--original-private-root", type=Path, required=True)
    parser.add_argument("--remediation-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw = args.evidence.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise RuntimeError("e0_remediation_public_evidence_not_canonical_lf")
    result = validate_remediation_review(
        json.loads(raw),
        original_evidence_path=args.original_evidence,
        config=E0RuntimeConfig.from_path(args.config),
        original_private_root=args.original_private_root,
        remediation_root=args.remediation_root,
        project_root=ROOT,
    )
    print(
        canonical(
            {
                **result,
                "evidence_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

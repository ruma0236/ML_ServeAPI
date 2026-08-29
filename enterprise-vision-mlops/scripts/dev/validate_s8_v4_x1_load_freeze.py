from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.x1_contract import X1Contract  # noqa: E402
from evm.scale_validation.x1_load_freeze_validation import (  # noqa: E402
    validate_x1_load_freeze,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Independently reproject canonical S8-V4 X1 load-freeze evidence."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument(
        "--public-evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-x1-load-freeze-v1.json",
    )
    parser.add_argument("--private-suite-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = X1Contract.from_path(
        args.config,
        source_root=ROOT,
        data_root=args.data_root,
    )
    result = validate_x1_load_freeze(
        public_path=args.public_evidence,
        private_root=args.private_suite_root,
        contract=contract,
        source_root=ROOT,
    )
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

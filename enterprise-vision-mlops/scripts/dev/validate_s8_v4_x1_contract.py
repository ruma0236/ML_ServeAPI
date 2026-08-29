from __future__ import annotations

import argparse
import json
from pathlib import Path

from evm.scale_validation.x1_contract import canonical_sha256
from evm.scale_validation.x1_contract_validation import (
    amendment_sha256,
    validate_contract_files,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the frozen canonical X1 contract")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--amendment", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract, _amendment, mutation = validate_contract_files(
        config_path=args.config,
        amendment_path=args.amendment,
        source_root=args.source_root,
        data_root=args.data_root,
    )
    result = {
        "schema_version": "evm.s8_v4.x1_contract_validation.v1",
        "status": "passed",
        "acceptance_credit": False,
        "config_sha256": contract.sha256,
        "amendment_sha256": amendment_sha256(args.amendment),
        "snapshot": contract.public_snapshot(),
        "snapshot_sha256": canonical_sha256(contract.public_snapshot()),
        "mutation": mutation,
        "next_gate": "fresh_artifact_q0_and_non_credit_capacity_calibration",
    }
    rendered = (
        json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="ascii", newline="")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

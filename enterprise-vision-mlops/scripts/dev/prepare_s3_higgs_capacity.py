from __future__ import annotations

import argparse
import json
from pathlib import Path

from evm.scale_validation.s3_higgs import (
    load_higgs_preparation_config,
    prepare_higgs_capacity,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare governed HIGGS samples and five S3 CPU probe artifacts."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--source-branch", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = prepare_higgs_capacity(
        load_higgs_preparation_config(
            args.config,
            data_root=args.data_root,
            source_revision=args.source_revision,
            source_branch=args.source_branch,
        )
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

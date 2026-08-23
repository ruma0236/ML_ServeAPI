from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.s4_runtime import S4RuntimeConfig, train_tiny_mlp  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the governed S4 Tiny MLP on CUDA.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "s4_gpu_batching_runtime.toml",
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--lease-run-id", required=True)
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--fencing-token", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = S4RuntimeConfig.from_path(args.config, data_root=args.data_root)
    result = train_tiny_mlp(
        config,
        source_revision=args.source_revision,
        lease_run_id=args.lease_run_id,
        lease_id=args.lease_id,
        fencing_token=args.fencing_token,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

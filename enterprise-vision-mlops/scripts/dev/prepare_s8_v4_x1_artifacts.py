from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.x1_artifacts import prepare_x1_artifacts  # noqa: E402
from evm.scale_validation.x1_contract import X1Contract  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare fresh canonical X1 model artifacts.")
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml"
    )
    parser.add_argument(
        "--data-root", type=Path, default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--lease-run-id", required=True)
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--fencing-token", required=True)
    return parser.parse_args()


def git_value(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def main() -> int:
    args = parse_args()
    revision = git_value("rev-parse", "HEAD")
    tree = git_value("rev-parse", "HEAD^{tree}")
    contract = X1Contract.from_path(args.config, source_root=ROOT, data_root=args.data_root)
    result = prepare_x1_artifacts(
        contract,
        output_root=args.output_root,
        source_revision=revision,
        source_tree=tree,
        lease_run_id=args.lease_run_id,
        lease_id=args.lease_id,
        fencing_token=args.fencing_token,
    )
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.s5_runtime import (  # noqa: E402
    S5RuntimeConfig,
    prepare_criteo_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare governed Criteo S5 shards.")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "s5_spark_data_scale.toml",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.getenv(
                "EVM_DATA_ROOT",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
            )
        ),
    )
    args = parser.parse_args()
    config = S5RuntimeConfig.from_path(args.config, data_root=args.data_root)
    result = prepare_criteo_dataset(config)
    result["preparation_source_revision"] = _git("rev-parse", "HEAD")
    result["preparation_source_branch"] = _git("branch", "--show-current")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())

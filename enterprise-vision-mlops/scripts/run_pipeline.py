from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


PIPELINE_MODULES = {
    "data-ingest": "evm.pipelines.data_ingestion.run",
    "data-validate": "evm.pipelines.data_validation.run",
    "train": "evm.pipelines.training.run",
    "register-model": "evm.pipelines.model_registry.run",
    "deploy-check": "evm.pipelines.deployment.run",
    "monitor-check": "evm.pipelines.monitoring.run",
}


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))

    parser = argparse.ArgumentParser(description="Run a modular MLOps pipeline.")
    parser.add_argument("pipeline", choices=PIPELINE_MODULES.keys())
    parser.add_argument("--config", default="configs/local.toml")
    args = parser.parse_args()

    module = importlib.import_module(PIPELINE_MODULES[args.pipeline])
    summary = module.run(args.config)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

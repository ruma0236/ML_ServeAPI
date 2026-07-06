from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


PIPELINE_MODULES = {
    "domain-pack-check": "evm.pipelines.domain_pack_check.run",
    "dataset-intake-audit": "evm.pipelines.dataset_intake_audit.run",
    "object-store-bootstrap": "evm.pipelines.object_storage_bootstrap.run",
    "data-ingest": "evm.pipelines.data_ingestion.run",
    "data-validate": "evm.pipelines.data_validation.run",
    "image-quality": "evm.pipelines.image_quality.run",
    "dataset-shards": "evm.pipelines.dataset_shards.run",
    "vlm-contract": "evm.pipelines.vlm_contract.run",
    "vlm-batch-eval": "evm.pipelines.vlm_batch_eval.run",
    "vlm-reliability": "evm.pipelines.vlm_reliability.run",
    "vlm-rca": "evm.pipelines.vlm_rca.run",
    "vlm-observability": "evm.pipelines.vlm_observability.run",
    "train": "evm.pipelines.training.run",
    "register-model": "evm.pipelines.model_registry.run",
    "deploy-check": "evm.pipelines.deployment.run",
    "monitor-check": "evm.pipelines.monitoring.run",
    "remote-inventory": "evm.pipelines.remote_workers.run",
    "remote-job": "evm.pipelines.remote_job.run",
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

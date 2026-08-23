from __future__ import annotations

from pathlib import Path
from typing import Any

from evm.core.pipeline import build_context, write_json, write_markdown_report
from evm.scale_validation.s5_runtime import (
    S5RuntimeConfig,
    execute_columnar_control,
    prepare_criteo_dataset,
)


def run(config_path: str | Path) -> dict[str, Any]:
    ctx = build_context("spark-data-scale", config_path)
    pipeline = ctx.pipeline_config()
    runtime_config_path = Path(
        str(pipeline.get("runtime_config") or "configs/s5_spark_data_scale.toml")
    )
    if not runtime_config_path.is_absolute():
        runtime_config_path = ctx.project_root / runtime_config_path
    data_root = ctx.path(str(ctx.config["paths"]["external_storage_root"]))
    runtime = S5RuntimeConfig.from_path(runtime_config_path, data_root=data_root)
    manifest = prepare_criteo_dataset(runtime)
    stage = str(pipeline.get("stage") or "small")
    repetition = int(pipeline.get("repetition") or 1)
    result = execute_columnar_control(
        config=runtime,
        manifest=manifest,
        stage=stage,
        repetition=repetition,
        logical_output_id=f"pipeline-{ctx.run_id}-{stage}",
    )
    report = {
        "schema_version": "evm.spark_data_scale_pipeline.v1",
        "status": "passed",
        "execution_engine": "single_process_columnar",
        "stage": stage,
        "result": result,
        "trace": ctx.trace.to_dict(),
        "claim_boundary": runtime.claim_boundary,
    }
    report_path = ctx.run_dir / "spark_data_scale.json"
    write_json(report_path, report)
    write_markdown_report(
        ctx,
        "Spark Data Scale",
        {
            "status": report["status"],
            "execution_engine": report["execution_engine"],
            "stage": stage,
            "records_per_second": result["records_per_second"],
            "missing_records": result["missing_records"],
            "duplicate_records": result["duplicate_records"],
        },
        extra_lines=[
            "## Claim Boundary",
            "",
            runtime.claim_boundary,
        ],
    )
    return {**report, "report_path": str(report_path)}

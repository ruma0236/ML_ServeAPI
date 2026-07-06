from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.pipeline import (
    build_context,
    display_path,
    read_jsonl,
    utc_now,
    write_json,
    write_jsonl,
    write_markdown_report,
)
from evm.core.vlm import MockVlmAdapter, classify_request, p95, validate_vlm_response


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_shard_path(shard_path: str, shard_index_path: Path) -> Path:
    path = Path(shard_path)
    if path.is_absolute():
        return path
    return shard_index_path.parent / path


def _request_for_record(
    record: dict[str, Any],
    *,
    trace_id: str,
    batch_id: str,
    question: str,
    prompt_version: str,
    model_version: str,
) -> dict[str, Any]:
    sample_id = str(record.get("sample_id") or record.get("id") or "")
    request_type = classify_request(question, {"request_type": record.get("request_type", "")})
    return {
        "request_id": f"{batch_id}:{sample_id}",
        "trace_id": trace_id,
        "dataset_id": record.get("dataset_id", ""),
        "dataset_version": record.get("dataset_version", ""),
        "sample_id": sample_id,
        "batch_id": batch_id,
        "image_uri": record.get("image_uri", ""),
        "question": question,
        "request_type": request_type,
        "prompt_version": prompt_version,
        "model_version": model_version,
        "label": record.get("label", ""),
        "defect_type": record.get("defect_type", ""),
        "severity": record.get("severity", ""),
        "metadata": {
            "split": record.get("split", ""),
            "class_name": record.get("class_name", ""),
            "content_sha256": record.get("content_sha256", ""),
        },
    }


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("vlm_batch_eval", config_path)
    cfg = ctx.pipeline_config()
    shard_index_path = ctx.path(str(cfg.get("shard_index", "data/validated/shards/shard_index.json")))
    output_dir = ctx.path(str(cfg.get("output_dir", "artifacts/vlm/batch_outputs")))
    summary_path = ctx.path(str(cfg.get("summary_path", "artifacts/vlm/latest_batch_summary.json")))
    prompt_version = str(cfg.get("prompt_version", "mvi-default-v1"))
    model_version = str(cfg.get("model_version", "mock-vlm-2026.07"))
    question = str(cfg.get("question", "Inspect the image and return defect findings."))
    max_records = int(cfg.get("max_records", 0))
    batch_id = ctx.run_id
    run_output_dir = output_dir / batch_id
    output_path = run_output_dir / "vlm_outputs.jsonl"
    schema_report_path = run_output_dir / "schema_validation_report.json"

    shard_index = _load_json(shard_index_path)
    adapter = MockVlmAdapter(model_version=model_version)
    outputs: list[dict[str, Any]] = []
    processed = 0
    for shard in shard_index.get("shards", []):
        shard_path = _resolve_shard_path(str(shard.get("path", "")), shard_index_path)
        for record in read_jsonl(shard_path):
            if max_records and processed >= max_records:
                break
            request = _request_for_record(
                record,
                trace_id=ctx.trace.trace_id,
                batch_id=batch_id,
                question=question,
                prompt_version=prompt_version,
                model_version=model_version,
            )
            response = adapter.infer(request)
            validation = validate_vlm_response(response)
            response["schema_valid"] = bool(validation["schema_valid"])
            outputs.append(
                {
                    "batch_id": batch_id,
                    "shard_id": shard.get("shard_id", ""),
                    "split": record.get("split", ""),
                    "sample_id": request["sample_id"],
                    "request": request,
                    "response": response,
                    "schema_validation": validation,
                    "observed_at": utc_now(),
                }
            )
            processed += 1
        if max_records and processed >= max_records:
            break

    latencies = [float(item["response"].get("latency_ms", 0.0) or 0.0) for item in outputs]
    schema_valid_count = sum(1 for item in outputs if item["response"].get("schema_valid") is True)
    error_types = Counter(str(item["response"].get("error_type", "") or "none") for item in outputs)
    split_counts = Counter(str(item.get("split", "")) for item in outputs if item.get("split"))
    schema_report = {
        "batch_id": batch_id,
        "output_path": display_path(output_path, ctx.project_root),
        "records": len(outputs),
        "schema_valid_count": schema_valid_count,
        "schema_invalid_count": len(outputs) - schema_valid_count,
        "schema_valid_rate": round(schema_valid_count / len(outputs), 6) if outputs else 0.0,
        "p95_latency_ms": p95(latencies),
        "error_types": dict(error_types),
        "split_counts": dict(split_counts),
        "trace": ctx.trace.to_dict(),
    }
    summary = {
        **schema_report,
        "status": "pass" if schema_report["schema_valid_rate"] >= 0.98 else "fail",
        "prompt_version": prompt_version,
        "model_version": model_version,
        "shard_index": display_path(shard_index_path, ctx.project_root),
        "schema_report_path": display_path(schema_report_path, ctx.project_root),
    }
    write_jsonl(output_path, outputs)
    write_json(schema_report_path, schema_report)
    write_json(summary_path, summary)
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "VLM Batch Evaluation",
        {
            "status": summary["status"],
            "records": len(outputs),
            "schema_valid_rate": summary["schema_valid_rate"],
            "p95_latency_ms": summary["p95_latency_ms"],
            "output_path": summary["output_path"],
        },
        [
            "",
            "## Contract",
            "",
            "- Input: deterministic dataset shard index.",
            "- Output: JSONL VLM request/response records and schema validation report.",
            "- Adapter: mock VLM adapter, replaceable by real endpoint later.",
        ],
    )
    if summary["status"] != "pass":
        raise RuntimeError(f"VLM batch evaluation failed schema gate: {summary}")
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

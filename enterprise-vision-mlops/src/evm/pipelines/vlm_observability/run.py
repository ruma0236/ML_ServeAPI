from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.pipeline import build_context, display_path, write_json, write_markdown_report


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _metric_line(name: str, value: int | float, labels: dict[str, str] | None = None) -> str:
    labels = labels or {}
    label_text = ""
    if labels:
        serialized = ",".join(f'{key}="{val}"' for key, val in sorted(labels.items()))
        label_text = f"{{{serialized}}}"
    return f"{name}{label_text} {value}"


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("vlm_observability", config_path)
    cfg = ctx.pipeline_config()
    quality_report_path = ctx.path(str(cfg.get("quality_report", "data/validated/mvi_quality_report.json")))
    shard_index_path = ctx.path(str(cfg.get("shard_index", "data/validated/shards/shard_index.json")))
    batch_summary_path = ctx.path(str(cfg.get("batch_summary", "artifacts/vlm/latest_batch_summary.json")))
    gate_report_path = ctx.path(str(cfg.get("gate_report_path", "artifacts/vlm/reliability/gate_report.json")))
    audit_summary_path = ctx.path(str(cfg.get("audit_summary", "artifacts/vlm/audit/audit_summary.json")))
    benchmark_report_path = ctx.path(
        str(cfg.get("benchmark_report", "artifacts/vlm/observability/benchmark_report.json"))
    )
    slo_report_path = ctx.path(str(cfg.get("slo_report", "artifacts/vlm/observability/slo_report.md")))
    metrics_export_path = ctx.path(
        str(cfg.get("metrics_export", "artifacts/vlm/observability/vlm_metrics.prom"))
    )

    quality = _load_json(quality_report_path)
    shards = _load_json(shard_index_path)
    batch = _load_json(batch_summary_path)
    gate = _load_json(gate_report_path)
    audit = _load_json(audit_summary_path)
    benchmark = {
        "schema_version": "evm.vlm_observability.v1",
        "status": "pass" if gate.get("status") == "pass" and batch.get("status") == "pass" else "warn",
        "dataset_quality": {
            "record_count": quality.get("record_count", 0),
            "error_count": quality.get("error_count", 0),
            "warning_count": quality.get("warning_count", 0),
            "duplicate_content_hashes": quality.get("duplicate_content_hashes", 0),
        },
        "sharding": {
            "record_count": shards.get("record_count", 0),
            "shard_count": shards.get("shard_count", 0),
            "split_counts": shards.get("split_counts", {}),
        },
        "vlm_batch": {
            "records": batch.get("records", 0),
            "schema_valid_rate": batch.get("schema_valid_rate", 0.0),
            "p95_latency_ms": batch.get("p95_latency_ms", 0.0),
            "error_types": batch.get("error_types", {}),
        },
        "reliability": {
            "promotion_decision": gate.get("promotion_decision", ""),
            "blocking_failures": len(gate.get("blocking_failures", [])),
            "warning_failures": len(gate.get("warning_failures", [])),
        },
        "audit": {
            "event_count": audit.get("event_count", 0),
            "event_counts": audit.get("event_counts", {}),
        },
        "trace": ctx.trace.to_dict(),
    }
    metrics_lines = [
        "# HELP evm_vlm_schema_valid_rate Latest VLM schema validity rate.",
        "# TYPE evm_vlm_schema_valid_rate gauge",
        _metric_line("evm_vlm_schema_valid_rate", float(batch.get("schema_valid_rate", 0.0))),
        "# HELP evm_vlm_p95_latency_ms Latest VLM p95 latency in milliseconds.",
        "# TYPE evm_vlm_p95_latency_ms gauge",
        _metric_line("evm_vlm_p95_latency_ms", float(batch.get("p95_latency_ms", 0.0))),
        "# HELP evm_vlm_quality_error_count Latest image quality fatal error count.",
        "# TYPE evm_vlm_quality_error_count gauge",
        _metric_line("evm_vlm_quality_error_count", int(quality.get("error_count", 0))),
        "# HELP evm_vlm_audit_event_count Latest VLM audit event count.",
        "# TYPE evm_vlm_audit_event_count gauge",
        _metric_line("evm_vlm_audit_event_count", int(audit.get("event_count", 0))),
    ]
    slo_lines = [
        "# VLM Workload SLO Report",
        "",
        "| SLO | Target | Observed | Status |",
        "|---|---:|---:|---|",
        (
            f"| Schema valid rate | >= 0.98 | {float(batch.get('schema_valid_rate', 0.0)):.3f} | "
            f"{'pass' if float(batch.get('schema_valid_rate', 0.0)) >= 0.98 else 'fail'} |"
        ),
        (
            f"| p95 latency ms | <= 5000 | {float(batch.get('p95_latency_ms', 0.0)):.3f} | "
            f"{'pass' if float(batch.get('p95_latency_ms', 0.0)) <= 5000 else 'warn'} |"
        ),
        (
            f"| Image quality fatal errors | == 0 | {int(quality.get('error_count', 0))} | "
            f"{'pass' if int(quality.get('error_count', 0)) == 0 else 'fail'} |"
        ),
        (
            f"| Promotion blocking failures | == 0 | {len(gate.get('blocking_failures', []))} | "
            f"{'pass' if len(gate.get('blocking_failures', [])) == 0 else 'fail'} |"
        ),
        "",
        "## Recovery Rules",
        "",
        "- Schema failures block promotion and route candidate output to RCA.",
        "- Quality fatal errors block batch promotion until the manifest is repaired.",
        "- Latency warnings do not block mock-adapter promotion but must be reviewed before real endpoint cutover.",
        "- Bad prompt candidates must remain blocked by the regression gate.",
    ]
    write_json(benchmark_report_path, benchmark)
    metrics_export_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_export_path.write_text("\n".join(metrics_lines) + "\n", encoding="utf-8")
    slo_report_path.parent.mkdir(parents=True, exist_ok=True)
    slo_report_path.write_text("\n".join(slo_lines) + "\n", encoding="utf-8")
    summary = {
        **benchmark,
        "benchmark_report": display_path(benchmark_report_path, ctx.project_root),
        "slo_report": display_path(slo_report_path, ctx.project_root),
        "metrics_export": display_path(metrics_export_path, ctx.project_root),
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "VLM Observability Evidence",
        {
            "status": benchmark["status"],
            "schema_valid_rate": batch.get("schema_valid_rate", 0.0),
            "p95_latency_ms": batch.get("p95_latency_ms", 0.0),
            "quality_error_count": quality.get("error_count", 0),
            "benchmark_report": summary["benchmark_report"],
        },
        [
            "",
            "## Contract",
            "",
            "- Input: quality, shard, batch, reliability, and audit reports.",
            "- Output: benchmark JSON, Prometheus text metrics, and SLO report.",
            "- Dashboard: Grafana provisioning can visualize the exported metrics once scraped.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

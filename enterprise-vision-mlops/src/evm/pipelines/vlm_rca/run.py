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


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _event(event_type: str, trace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "evm.audit_event.v1",
        "event_type": event_type,
        "trace_id": trace_id,
        "event_time": utc_now(),
        "payload": payload,
    }


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("vlm_rca", config_path)
    cfg = ctx.pipeline_config()
    batch_summary_path = ctx.path(str(cfg.get("batch_summary", "artifacts/vlm/latest_batch_summary.json")))
    gate_report_path = ctx.path(str(cfg.get("gate_report_path", "artifacts/vlm/reliability/gate_report.json")))
    audit_dir = ctx.path(str(cfg.get("audit_dir", "artifacts/vlm/audit")))
    audit_events_path = audit_dir / "audit_events.jsonl"
    failure_scenarios_path = audit_dir / "failure_scenarios.json"
    audit_summary_path = audit_dir / "audit_summary.json"

    batch_summary = _load_json(batch_summary_path)
    gate_report = _load_json(gate_report_path)
    output_path = Path(str(batch_summary.get("output_path", "")))
    outputs = read_jsonl(output_path)
    events: list[dict[str, Any]] = []
    trace_id = ctx.trace.trace_id
    for item in outputs:
        request = item.get("request", {})
        response = item.get("response", {})
        events.append(
            _event(
                "vlm_request",
                str(request.get("trace_id") or trace_id),
                {
                    "request_id": request.get("request_id", ""),
                    "sample_id": request.get("sample_id", ""),
                    "batch_id": request.get("batch_id", ""),
                    "prompt_version": request.get("prompt_version", ""),
                    "model_version": request.get("model_version", ""),
                    "dataset_version": request.get("dataset_version", ""),
                },
            )
        )
        events.append(
            _event(
                "vlm_response",
                str(request.get("trace_id") or trace_id),
                {
                    "request_id": request.get("request_id", ""),
                    "sample_id": request.get("sample_id", ""),
                    "schema_valid": response.get("schema_valid"),
                    "defect_detected": response.get("defect_detected"),
                    "defect_type": response.get("defect_type"),
                    "latency_ms": response.get("latency_ms"),
                    "error_type": response.get("error_type", ""),
                },
            )
        )
    for gate in gate_report.get("gates", []):
        events.append(_event("promotion_gate", trace_id, gate))

    failure_scenarios = [
        {
            "scenario_id": "bad_prompt_candidate",
            "status": "reproduced",
            "evidence": gate_report.get("bad_candidate", {}),
            "rca_path": "prompt_registry -> bad_candidate -> schema_validity gate",
        },
        {
            "scenario_id": "corrupt_or_drifted_image",
            "status": "covered_by_quality_pipeline",
            "evidence": "image_quality diagnostics and drift proxy report",
            "rca_path": "quality_manifest -> sample_id -> batch output",
        },
        {
            "scenario_id": "schema_invalid_output",
            "status": "reproduced",
            "evidence": gate_report.get("bad_candidate", {}).get("schema_validation", {}),
            "rca_path": "batch output -> schema_validation -> promotion gate",
        },
        {
            "scenario_id": "model_endpoint_failure",
            "status": "simulated",
            "evidence": {
                "error_type": "model_endpoint_unavailable",
                "recommended_action": "fallback_to_last_promoted_candidate",
            },
            "rca_path": "adapter backend -> error_type -> rollback decision",
        },
    ]
    for scenario in failure_scenarios:
        events.append(_event("failure_scenario", trace_id, scenario))

    event_counts = Counter(event["event_type"] for event in events)
    summary = {
        "schema_version": "evm.vlm_rca.v1",
        "status": "pass",
        "audit_events": display_path(audit_events_path, ctx.project_root),
        "failure_scenarios": display_path(failure_scenarios_path, ctx.project_root),
        "event_count": len(events),
        "event_counts": dict(event_counts),
        "scenario_count": len(failure_scenarios),
        "batch_summary": display_path(batch_summary_path, ctx.project_root),
        "gate_report": display_path(gate_report_path, ctx.project_root),
        "trace": ctx.trace.to_dict(),
    }
    write_jsonl(audit_events_path, events)
    write_json(failure_scenarios_path, {"scenarios": failure_scenarios, "trace": ctx.trace.to_dict()})
    write_json(audit_summary_path, summary)
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "VLM Audit And RCA",
        {
            "status": summary["status"],
            "event_count": len(events),
            "scenario_count": len(failure_scenarios),
            "audit_events": summary["audit_events"],
        },
        [
            "",
            "## Contract",
            "",
            "- Input: VLM batch summary and reliability gate report.",
            "- Output: audit event JSONL, failure scenario evidence, RCA join path.",
            "- Join root: `trace_id`, then request_id/sample_id/batch_id.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

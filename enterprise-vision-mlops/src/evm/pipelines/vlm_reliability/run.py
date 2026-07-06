from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.pipeline import build_context, display_path, utc_now, write_json, write_markdown_report
from evm.core.vlm import MockVlmAdapter, validate_vlm_response


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _gate(name: str, passed: bool, observed: float | int, threshold: float | int, severity: str) -> dict[str, Any]:
    return {
        "gate": name,
        "passed": passed,
        "observed": observed,
        "threshold": threshold,
        "severity": severity,
    }


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("vlm_reliability", config_path)
    cfg = ctx.pipeline_config()
    batch_summary_path = ctx.path(str(cfg.get("batch_summary", "artifacts/vlm/latest_batch_summary.json")))
    registry_dir = ctx.path(str(cfg.get("registry_dir", "artifacts/registry/vlm")))
    gate_report_path = ctx.path(str(cfg.get("gate_report_path", "artifacts/vlm/reliability/gate_report.json")))
    schema_valid_threshold = float(cfg.get("schema_valid_threshold", 0.98))
    p95_latency_ms_threshold = float(cfg.get("p95_latency_ms_threshold", 5000))

    batch_summary = _load_json(batch_summary_path)
    prompt_registry = {
        "schema_version": "evm.prompt_registry.v1",
        "updated_at": utc_now(),
        "promoted_prompt": "mvi-default-v1",
        "candidates": [
            {
                "prompt_version": "mvi-default-v1",
                "status": "candidate",
                "purpose": "Structured manufacturing visual inspection output.",
            },
            {
                "prompt_version": "mvi-bad-candidate-v1",
                "status": "blocked_test_candidate",
                "purpose": "Intentionally omits required fields to validate regression gate.",
            },
        ],
    }
    model_registry = {
        "schema_version": "evm.vlm_model_registry.v1",
        "updated_at": utc_now(),
        "promoted_model": "mock-vlm-2026.07",
        "candidates": [
            {
                "model_version": "mock-vlm-2026.07",
                "adapter_backend": "local_mock",
                "status": "candidate",
            }
        ],
    }
    adapter = MockVlmAdapter(model_version="mock-vlm-2026.07")
    bad_response = adapter.infer(
        {
            "request_id": "gate-test:bad-prompt",
            "trace_id": ctx.trace.trace_id,
            "sample_id": "bad-prompt",
        },
        candidate="bad_prompt",
    )
    bad_validation = validate_vlm_response(bad_response)
    bad_candidate_blocked = not bool(bad_validation["schema_valid"])
    gates = [
        _gate(
            "schema_validity",
            float(batch_summary.get("schema_valid_rate", 0.0)) >= schema_valid_threshold,
            float(batch_summary.get("schema_valid_rate", 0.0)),
            schema_valid_threshold,
            "block",
        ),
        _gate(
            "bad_prompt_regression",
            bad_candidate_blocked,
            1 if bad_candidate_blocked else 0,
            1,
            "block",
        ),
        _gate(
            "latency_budget",
            float(batch_summary.get("p95_latency_ms", 0.0)) <= p95_latency_ms_threshold,
            float(batch_summary.get("p95_latency_ms", 0.0)),
            p95_latency_ms_threshold,
            "warn",
        ),
    ]
    blocking_failures = [gate for gate in gates if gate["severity"] == "block" and not gate["passed"]]
    warning_failures = [gate for gate in gates if gate["severity"] == "warn" and not gate["passed"]]
    promotion_decision = "promote_candidate" if not blocking_failures else "block_candidate"
    registry_dir.mkdir(parents=True, exist_ok=True)
    prompt_registry_path = registry_dir / "prompt_registry.json"
    model_registry_path = registry_dir / "model_registry.json"
    latest_decision_path = registry_dir / "latest_promotion_decision.json"
    report = {
        "schema_version": "evm.vlm_reliability_gate.v1",
        "status": "pass" if not blocking_failures else "fail",
        "promotion_decision": promotion_decision,
        "batch_summary": display_path(batch_summary_path, ctx.project_root),
        "prompt_registry": display_path(prompt_registry_path, ctx.project_root),
        "model_registry": display_path(model_registry_path, ctx.project_root),
        "latest_decision": display_path(latest_decision_path, ctx.project_root),
        "gates": gates,
        "blocking_failures": blocking_failures,
        "warning_failures": warning_failures,
        "bad_candidate": {
            "response": bad_response,
            "schema_validation": bad_validation,
            "blocked": bad_candidate_blocked,
        },
        "trace": ctx.trace.to_dict(),
    }
    write_json(prompt_registry_path, prompt_registry)
    write_json(model_registry_path, model_registry)
    write_json(latest_decision_path, report)
    write_json(gate_report_path, report)
    write_json(ctx.run_dir / "summary.json", report)
    write_markdown_report(
        ctx,
        "VLM Reliability Gate",
        {
            "status": report["status"],
            "promotion_decision": promotion_decision,
            "blocking_failures": len(blocking_failures),
            "warning_failures": len(warning_failures),
            "bad_candidate_blocked": bad_candidate_blocked,
        },
        [
            "",
            "## Contract",
            "",
            "- Input: latest VLM batch summary.",
            "- Output: prompt/model registries and promotion gate report.",
            "- Gate: intentionally bad prompt candidate must be blocked.",
        ],
    )
    if blocking_failures:
        raise RuntimeError(f"VLM reliability gate failed: {blocking_failures}")
    return report


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

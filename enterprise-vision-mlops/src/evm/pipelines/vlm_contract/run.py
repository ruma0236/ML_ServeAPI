from __future__ import annotations

from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.domain_pack import (
    REQUIRED_REQUEST_FIELDS,
    REQUIRED_RESPONSE_FIELDS,
    load_domain_pack,
)
from evm.core.pipeline import build_context, display_path, write_json, write_markdown_report
from evm.core.vlm import classify_request


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("vlm_contract", config_path)
    cfg = ctx.pipeline_config()
    domain_pack_path, domain_pack = load_domain_pack(
        ctx.config,
        str(cfg.get("domain_pack", "domain_packs/manufacturing_visual_inspection/domain_pack.toml")),
    )
    contract_path = ctx.path(str(cfg.get("contract_path", "artifacts/vlm/contracts/vlm_contract.json")))
    router_report_path = ctx.path(
        str(cfg.get("router_report_path", "artifacts/vlm/contracts/router_report.json"))
    )
    request_fields = set(domain_pack.get("request_schema", {}).get("required_fields", []))
    response_fields = set(domain_pack.get("response_schema", {}).get("required_fields", []))
    request_missing = sorted(REQUIRED_REQUEST_FIELDS - request_fields)
    response_missing = sorted(REQUIRED_RESPONSE_FIELDS - response_fields)

    router_cases = [
        {
            "question": "Inspect the product image and return defect findings.",
            "expected": "visual_inspection",
        },
        {"question": "Describe this product image.", "expected": "caption"},
        {"question": "Is there a scratch near the edge?", "expected": "visual_inspection"},
        {"question": "Summarize the maintenance log.", "expected": "unsupported"},
    ]
    router_results = [
        {**case, "actual": classify_request(case["question"])}
        for case in router_cases
    ]
    router_pass = all(item["actual"] == item["expected"] for item in router_results)
    adapter_contract = {
        "schema_version": "evm.vlm_contract.v1",
        "domain_pack": display_path(domain_pack_path, ctx.project_root),
        "domain_pack_id": str(domain_pack.get("domain_pack", {}).get("id", "")),
        "adapter_backend": str(cfg.get("adapter_backend", "mock")),
        "prompt_version": str(cfg.get("prompt_version", "mvi-default-v1")),
        "model_version": str(cfg.get("model_version", "mock-vlm-2026.07")),
        "request_required_fields": sorted(request_fields),
        "response_required_fields": sorted(response_fields),
        "request_missing_fields": request_missing,
        "response_missing_fields": response_missing,
        "request_types": domain_pack.get("request_schema", {}).get("request_types", []),
        "status": "pass" if not request_missing and not response_missing and router_pass else "fail",
        "trace": ctx.trace.to_dict(),
    }
    router_report = {
        "status": "pass" if router_pass else "fail",
        "router_results": router_results,
        "trace": ctx.trace.to_dict(),
    }
    write_json(contract_path, adapter_contract)
    write_json(router_report_path, router_report)
    summary = {
        **adapter_contract,
        "contract_path": display_path(contract_path, ctx.project_root),
        "router_report_path": display_path(router_report_path, ctx.project_root),
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "VLM Adapter Contract",
        {
            "status": summary["status"],
            "adapter_backend": summary["adapter_backend"],
            "request_missing_fields": len(request_missing),
            "response_missing_fields": len(response_missing),
            "router_status": router_report["status"],
        },
        [
            "",
            "## Contract",
            "",
            "- Input: domain pack request/response schema.",
            "- Output: adapter contract and router classification report.",
            "- Next: `vlm_batch_eval` uses the mock adapter with this contract.",
        ],
    )
    if summary["status"] != "pass":
        raise RuntimeError(f"VLM contract check failed: {summary}")
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

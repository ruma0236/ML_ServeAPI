from __future__ import annotations

from collections.abc import Sequence

from evm.core.domain_pack import load_domain_pack, summarize_domain_pack, validate_domain_pack
from evm.core.pipeline import build_context, write_json, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("domain_pack_check", config_path)
    cfg = ctx.pipeline_config()
    domain_pack_path = str(
        cfg.get("domain_pack", "domain_packs/manufacturing_visual_inspection/domain_pack.toml")
    )

    resolved_path, pack = load_domain_pack(ctx.config, domain_pack_path)
    diagnostics = validate_domain_pack(pack, resolved_path)
    summary = summarize_domain_pack(resolved_path, pack, diagnostics)
    summary["trace_id"] = ctx.trace.trace_id
    summary["pipeline_run_id"] = ctx.run_id

    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Domain Pack Check Pipeline",
        summary,
        [
            "",
            "## Contract",
            "",
            "- Input: TOML domain pack policy file.",
            "- Output: validation summary for dataset, manifest, adapter, evaluation, gate, and RCA policy.",
            "- Next: data ingestion and validation consume the selected domain pack policy.",
        ],
    )

    if summary["status"] != "pass":
        raise RuntimeError(f"Domain pack validation failed: {resolved_path}")
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

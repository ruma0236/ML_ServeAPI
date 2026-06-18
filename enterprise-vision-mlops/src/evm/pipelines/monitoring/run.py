from __future__ import annotations

from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.http import request_json
from evm.core.pipeline import build_context, write_json, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("monitoring", config_path)
    prometheus_url = str(get_nested(ctx.config, "monitoring.prometheus_url", "http://localhost:9090")).rstrip("/")
    targets_endpoint = str(ctx.pipeline_config().get("prometheus_targets_endpoint", "/api/v1/targets"))

    targets_status, payload = request_json("GET", f"{prometheus_url}{targets_endpoint}")
    active_targets = []
    if isinstance(payload, dict):
        active_targets = payload.get("data", {}).get("activeTargets", [])

    target_summary = [
        {
            "scrape_url": target.get("scrapeUrl"),
            "health": target.get("health"),
            "last_error": target.get("lastError"),
        }
        for target in active_targets
    ]
    healthy_targets = sum(1 for target in target_summary if target.get("health") == "up")

    summary = {
        "prometheus_url": prometheus_url,
        "targets_status": targets_status,
        "active_targets": len(target_summary),
        "healthy_targets": healthy_targets,
        "targets": target_summary,
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Monitoring Pipeline",
        {
            "prometheus_url": prometheus_url,
            "targets_status": targets_status,
            "active_targets": len(target_summary),
            "healthy_targets": healthy_targets,
        },
        [
            "",
            "## Contract",
            "",
            "- Input: running API and Prometheus stack.",
            "- Output: target health and metrics collection report.",
            "- Next: add alert rules, SLOs, and drift dashboards.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

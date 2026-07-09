from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.config import get_nested
from evm.core.pipeline import build_context, display_path, utc_now, write_json, write_markdown_report


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_summary(root: Path, pattern: str = "*/summary.json") -> tuple[Path | None, dict[str, Any]]:
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None, {}
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return latest, _read_json(latest)


def _svg_header(width: int, height: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,Helvetica,sans-serif;fill:#172026}.title{font-size:22px;font-weight:700}.label{font-size:13px}.small{font-size:11px}.value{font-size:18px;font-weight:700}</style>',
    ]


def _write_svg(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines + ["</svg>", ""]), encoding="utf-8")


def _metrics_svg(path: Path, metrics: dict[str, Any]) -> None:
    metric_order = ["accuracy", "precision", "recall", "f1", "auroc"]
    width = 860
    height = 360
    lines = _svg_header(width, height)
    lines.append('<text x="32" y="42" class="title">W5 Model Evaluation Metrics</text>')
    max_bar_width = 560
    start_y = 86
    colors = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c"]
    for idx, name in enumerate(metric_order):
        value = float(metrics.get(name, 0.0) or 0.0)
        y = start_y + idx * 48
        bar_width = max(1, int(max_bar_width * max(0.0, min(1.0, value))))
        lines.append(f'<text x="40" y="{y + 20}" class="label">{name}</text>')
        lines.append(f'<rect x="140" y="{y}" width="{max_bar_width}" height="26" rx="4" fill="#e5e7eb"/>')
        lines.append(f'<rect x="140" y="{y}" width="{bar_width}" height="26" rx="4" fill="{colors[idx]}"/>')
        lines.append(f'<text x="720" y="{y + 20}" class="value">{value:.3f}</text>')
    lines.append('<text x="40" y="332" class="small">Source: registry source_model.metrics from the latest W5 model artifact.</text>')
    _write_svg(path, lines)


def _confusion_svg(path: Path, model: dict[str, Any]) -> None:
    selected_split = str(model.get("evaluation", {}).get("selected_split", "test"))
    evaluation = model.get("evaluation", {}).get(selected_split, {})
    confusion = evaluation.get("confusion_matrix", {}) if isinstance(evaluation, dict) else {}
    classes = sorted({*model.get("classes", []), *confusion.keys()})
    for row in confusion.values():
        if isinstance(row, dict):
            classes.extend(key for key in row if key not in classes)
    classes = sorted(classes)
    width = 720
    height = 460
    lines = _svg_header(width, height)
    lines.append(f'<text x="32" y="42" class="title">Confusion Matrix ({selected_split})</text>')
    if not classes:
        lines.append('<text x="40" y="110" class="label">No confusion matrix available.</text>')
        _write_svg(path, lines)
        return
    max_value = max(
        [int(confusion.get(actual, {}).get(pred, 0) or 0) for actual in classes for pred in classes]
        or [1]
    )
    cell = 118
    x0 = 180
    y0 = 100
    for idx, pred in enumerate(classes):
        lines.append(f'<text x="{x0 + idx * cell + 20}" y="82" class="label">pred: {pred}</text>')
    for row_idx, actual in enumerate(classes):
        y = y0 + row_idx * cell
        lines.append(f'<text x="40" y="{y + 64}" class="label">actual: {actual}</text>')
        for col_idx, pred in enumerate(classes):
            value = int(confusion.get(actual, {}).get(pred, 0) or 0)
            intensity = value / max_value if max_value else 0.0
            blue = int(245 - intensity * 120)
            green = int(248 - intensity * 100)
            red = int(239 - intensity * 200)
            fill = f"rgb({red},{green},{blue})"
            x = x0 + col_idx * cell
            lines.append(f'<rect x="{x}" y="{y}" width="{cell - 10}" height="{cell - 10}" rx="6" fill="{fill}" stroke="#64748b"/>')
            lines.append(f'<text x="{x + 42}" y="{y + 58}" class="value">{value}</text>')
    lines.append('<text x="40" y="420" class="small">Darker cells indicate larger counts. Source: latest model evaluation artifact.</text>')
    _write_svg(path, lines)


def _topology_svg(path: Path, local_resource: dict[str, Any], remote_resource: dict[str, Any]) -> None:
    gpu = local_resource.get("gpu", [{}])
    gpu_name = str(gpu[0].get("name", "GPU not detected")) if gpu else "GPU not detected"
    remote_fields = remote_resource.get("fields", {}) if isinstance(remote_resource, dict) else {}
    width = 980
    height = 430
    lines = _svg_header(width, height)
    lines.append('<text x="32" y="42" class="title">W5 Local and Remote Resource Topology</text>')
    boxes = [
        (40, 100, 260, 170, "#dbeafe", "Windows primary node", [
            "Role: train, registry, serving",
            f"CPU threads: {local_resource.get('cpu_count', 'unknown')}",
            f"GPU: {gpu_name}",
            "Data/artifacts: F: drive",
        ]),
        (360, 100, 260, 170, "#dcfce7", "Docker MLOps stack", [
            "Airflow, MLflow, API",
            "MinIO, Prometheus, Grafana",
            "Data mount: /mnt/evm-data",
            "Registry mount: /app/artifacts",
        ]),
        (680, 100, 260, 170, "#fef3c7", "Mac mini M4 Pro", [
            "Role: ARM64 remote evaluator",
            f"Arch: {remote_fields.get('architecture', 'unknown')}",
            f"CPU: {remote_fields.get('cpu_count', 'unknown')}",
            f"Memory bytes: {remote_fields.get('memory_bytes', 'unknown')}",
        ]),
    ]
    for x, y, w, h, fill, title, body in boxes:
        lines.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="#334155"/>')
        lines.append(f'<text x="{x + 18}" y="{y + 32}" class="value">{title}</text>')
        for idx, item in enumerate(body):
            lines.append(f'<text x="{x + 18}" y="{y + 66 + idx * 24}" class="label">{item}</text>')
    lines.append('<path d="M300 185 L360 185" stroke="#334155" stroke-width="3" marker-end="url(#arrow)"/>')
    lines.append('<path d="M620 185 L680 185" stroke="#334155" stroke-width="3" marker-end="url(#arrow)"/>')
    lines.append('<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#334155"/></marker></defs>')
    lines.append('<text x="40" y="335" class="label">Resource policy: CPU feature model is trained locally; GPU availability is captured for deep/VLM workloads; Mac mini validates ARM64 execution and remote artifact collection.</text>')
    _write_svg(path, lines)


def _lifecycle_svg(path: Path, registry: dict[str, Any], deployment: dict[str, Any], monitoring: dict[str, Any]) -> None:
    version = registry.get("version", "unknown")
    steps = [
        ("Data", "VisA validated"),
        ("Quality", "DQ gate"),
        ("Train", "feature model"),
        ("Registry", f"v{version}"),
        ("Serve", f"HTTP {deployment.get('predict_status', 'n/a')}"),
        ("Monitor", f"{monitoring.get('healthy_targets', 'n/a')} targets up"),
        ("Remote", "Mac mini eval"),
    ]
    width = 1100
    height = 300
    lines = _svg_header(width, height)
    lines.append('<text x="32" y="42" class="title">W5 Model Lifecycle Evidence Flow</text>')
    x = 42
    y = 120
    box_w = 130
    for idx, (title, sub) in enumerate(steps):
        fill = "#e0f2fe" if idx % 2 == 0 else "#ecfdf5"
        lines.append(f'<rect x="{x}" y="{y}" width="{box_w}" height="82" rx="8" fill="{fill}" stroke="#334155"/>')
        lines.append(f'<text x="{x + 16}" y="{y + 32}" class="value">{title}</text>')
        lines.append(f'<text x="{x + 16}" y="{y + 58}" class="label">{sub}</text>')
        if idx < len(steps) - 1:
            lines.append(f'<path d="M{x + box_w} {y + 41} L{x + box_w + 28} {y + 41}" stroke="#334155" stroke-width="3" marker-end="url(#arrow)"/>')
        x += box_w + 42
    lines.append('<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#334155"/></marker></defs>')
    lines.append('<text x="42" y="248" class="small">Evidence links are recorded in the generated Markdown report and F-drive run artifacts.</text>')
    _write_svg(path, lines)


def _md_link(path: Path, base_dir: Path) -> str:
    return os.path.relpath(path, base_dir).replace("\\", "/")


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("w5_verification", config_path)
    cfg = ctx.pipeline_config()
    model_name = str(get_nested(ctx.config, "serving.model_name", "vision-baseline"))
    models_root = ctx.path(get_nested(ctx.config, "paths.models_root", "artifacts/models"))
    registry_root = ctx.path(get_nested(ctx.config, "paths.registry_root", "artifacts/registry"))
    artifacts_root = ctx.path(get_nested(ctx.config, "paths.artifacts_root", "artifacts"))
    model_path = ctx.path(str(cfg.get("model_path", models_root / model_name / "model.json")))
    registry_latest = ctx.path(str(cfg.get("registry_latest", registry_root / model_name / "latest.json")))
    output_doc = ctx.path(
        str(cfg.get("output_doc", "docs/reviews/2026-07-09-w5-real-model-lifecycle-verification.md"))
    )
    asset_dir = ctx.path(str(cfg.get("asset_dir", "docs/assets/w5-real-model-lifecycle")))

    model = _read_json(model_path)
    registry = _read_json(registry_latest)
    deployment_path, deployment = _latest_summary(artifacts_root / "runs" / "deployment")
    monitoring_path, monitoring = _latest_summary(artifacts_root / "runs" / "monitoring")
    remote_path, remote = _latest_summary(artifacts_root / "runs" / "remote_job")
    remote_resource = remote.get("resource_report", {}) if isinstance(remote, dict) else {}

    metrics_svg = asset_dir / "w5-model-metrics.svg"
    confusion_svg = asset_dir / "w5-confusion-matrix.svg"
    topology_svg = asset_dir / "w5-resource-topology.svg"
    lifecycle_svg = asset_dir / "w5-lifecycle-flow.svg"
    _metrics_svg(metrics_svg, model.get("metrics", {}))
    _confusion_svg(confusion_svg, model)
    _topology_svg(topology_svg, model.get("resource_profile", {}), remote_resource)
    _lifecycle_svg(lifecycle_svg, registry, deployment, monitoring)

    metrics = model.get("metrics", {})
    lifecycle = model.get("lifecycle", {})
    resource_profile = model.get("resource_profile", {})
    gpu = resource_profile.get("gpu", [])
    remote_fields = remote_resource.get("fields", {}) if isinstance(remote_resource, dict) else {}
    lines = [
        "# W5 Real Model Lifecycle Verification",
        "",
        f"- Generated at: `{utc_now()}`",
        f"- Pipeline run id: `{ctx.run_id}`",
        f"- Dataset version: `{model.get('dataset', {}).get('dataset_version', '')}`",
        f"- Model: `{model.get('model_name', model_name)}` / `{model.get('model_type', '')}`",
        f"- Registry version: `{registry.get('version', '')}` / stage `{registry.get('stage', '')}`",
        f"- Lifecycle state: `{lifecycle.get('state', '')}` / gate `{lifecycle.get('promotion_gate', '')}`",
        "",
        "## Visual Evidence",
        "",
        f"![W5 lifecycle flow]({_md_link(lifecycle_svg, output_doc.parent)})",
        "",
        f"![W5 model metrics]({_md_link(metrics_svg, output_doc.parent)})",
        "",
        f"![W5 confusion matrix]({_md_link(confusion_svg, output_doc.parent)})",
        "",
        f"![W5 resource topology]({_md_link(topology_svg, output_doc.parent)})",
        "",
        "## Verification Summary",
        "",
        f"- Records used for training: `{model.get('records_used', 0)}` of `{model.get('records_seen', 0)}`.",
        f"- Selected evaluation split: `{model.get('evaluation', {}).get('selected_split', '')}`.",
        f"- Accuracy: `{float(metrics.get('accuracy', 0.0)):.6f}`.",
        f"- Precision / recall / F1: `{float(metrics.get('precision', 0.0)):.6f}` / `{float(metrics.get('recall', 0.0)):.6f}` / `{float(metrics.get('f1', 0.0)):.6f}`.",
        f"- AUROC: `{float(metrics.get('auroc', 0.0)):.6f}`.",
        f"- Deployment contract: `contract_ok={deployment.get('contract_ok', '')}`, predict status `{deployment.get('predict_status', '')}`, feature source `{deployment.get('predict_feature_source', '')}`.",
        f"- Monitoring: `{monitoring.get('healthy_targets', '')}` healthy Prometheus targets of `{monitoring.get('active_targets', '')}` active targets.",
        f"- Mac mini remote job: `{remote.get('status', '')}`, architecture `{remote_fields.get('architecture', '')}`, CPU `{remote_fields.get('cpu_count', '')}`, memory bytes `{remote_fields.get('memory_bytes', '')}`.",
        "",
        "## Resource Use",
        "",
        f"- Primary data/artifact storage: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops`.",
        f"- Local accelerator detected: `{bool(resource_profile.get('gpu_detected', False))}`.",
        f"- Local GPU: `{gpu[0].get('name', '') if gpu else ''}`.",
        f"- Model training accelerator used: `{resource_profile.get('accelerator_used', '')}`.",
        "- Current W5 feature classifier is CPU-bound by design; GPU is reserved and monitored for deep VLM/multimodal training or GPU-backed serving stages.",
        "- Mac mini M4 Pro is connected over Tailscale/SSH as an ARM64 remote evaluator and compatibility runner, not as the primary GPU trainer.",
        "",
        "## Evidence Files",
        "",
        f"- Model artifact: `{model_path}`",
        f"- Registry latest: `{registry_latest}`",
        f"- Deployment summary: `{deployment_path}`",
        f"- Monitoring summary: `{monitoring_path}`",
        f"- Remote job summary: `{remote_path}`",
        "",
        "## Reviewer Notes",
        "",
        "- This closes the W4 gap where the registry-serving path could be proven only with a majority-class artifact.",
        "- W5 now has an actual trainable image-feature model, registry versioning, API inference, Prometheus scrape verification, and Mac mini remote execution evidence.",
        "- Model quality is intentionally reported as-is; the current classifier is a lifecycle proof model, not the final VLM/multimodal target model.",
    ]
    output_doc.parent.mkdir(parents=True, exist_ok=True)
    output_doc.write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary = {
        "output_doc": display_path(output_doc, ctx.project_root),
        "asset_dir": display_path(asset_dir, ctx.project_root),
        "model_path": str(model_path),
        "registry_latest": str(registry_latest),
        "registry_version": registry.get("version", ""),
        "model_type": model.get("model_type", ""),
        "records_used": model.get("records_used", 0),
        "accuracy": round(float(metrics.get("accuracy", 0.0)), 6),
        "deployment_contract_ok": deployment.get("contract_ok", False),
        "healthy_targets": monitoring.get("healthy_targets", 0),
        "remote_job_status": remote.get("status", ""),
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.run_id,
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "W5 Verification Pipeline",
        summary,
        [
            "",
            "## Contract",
            "",
            "- Input: latest model, registry, deployment, monitoring, and remote-job artifacts.",
            "- Output: reviewer-facing Markdown verification package with SVG visual evidence.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

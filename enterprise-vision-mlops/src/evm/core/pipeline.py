from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.core.config import get_nested, load_config, resolve_path
from evm.core.traceability import TraceContext


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}"


@dataclass(frozen=True)
class PipelineContext:
    name: str
    run_id: str
    trace: TraceContext
    config: dict[str, Any]
    project_root: Path
    run_dir: Path
    report_dir: Path

    def pipeline_config(self) -> dict[str, Any]:
        return get_nested(self.config, f"pipelines.{self.name}", {})

    def path(self, value: str | Path) -> Path:
        return resolve_path(self.config, value)


def build_context(pipeline_name: str, config_path: str | Path) -> PipelineContext:
    config = load_config(config_path)
    project_root = Path(str(config["_project_root"]))
    artifacts_root = resolve_path(config, get_nested(config, "paths.artifacts_root", "artifacts"))
    rid = run_id(pipeline_name.replace("_", "-"))
    run_dir = artifacts_root / "runs" / pipeline_name / rid
    report_dir = resolve_path(config, get_nested(config, "paths.reports_root", "artifacts/reports"))
    run_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceContext.from_environment(pipeline_name, rid)
    (run_dir / "trace.json").write_text(
        json.dumps(trace.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return PipelineContext(
        name=pipeline_name,
        run_id=rid,
        trace=trace,
        config=config,
        project_root=project_root,
        run_dir=run_dir,
        report_dir=report_dir,
    )


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for record in records:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_markdown_report(
    ctx: PipelineContext,
    title: str,
    summary: dict[str, Any],
    extra_lines: list[str] | None = None,
) -> Path:
    report_path = ctx.report_dir / f"{ctx.name}.md"
    lines = [
        f"# {title}",
        "",
        f"- Last updated: `{utc_now()}`",
        f"- Latest run id: `{ctx.run_id}`",
        f"- Trace id: `{ctx.trace.trace_id}`",
        f"- Pipeline: `{ctx.name}`",
        "",
        "## Traceability",
        "",
        f"- `pipeline_run_id`: `{ctx.trace.pipeline_run_id}`",
        f"- `airflow_dag_id`: `{ctx.trace.airflow_dag_id}`",
        f"- `airflow_dag_run_id`: `{ctx.trace.airflow_dag_run_id}`",
        f"- `airflow_task_id`: `{ctx.trace.airflow_task_id}`",
        f"- `airflow_try_number`: `{ctx.trace.airflow_try_number}`",
        f"- `git_commit`: `{ctx.trace.git_commit}`",
        f"- `git_branch`: `{ctx.trace.git_branch}`",
        "",
        "## Latest Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- `{key}`: `{value}`")
    if extra_lines:
        lines.extend(["", *extra_lines])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_LABELS = ["evm", "enterprise-mlops"]


@dataclass
class JiraItem:
    source_id: str
    summary: str
    kind: str
    status: str
    target: str
    acceptance: str
    source_file: str
    due_date: str | None = None
    week: str | None = None
    output: str | None = None
    phase: str | None = None
    parent_id: str | None = None
    labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    project_key: str
    epic_type: str
    task_type: str


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def strip_markup(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("`") and cleaned.endswith("`"):
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def label_for(value: str) -> str:
    label = strip_markup(value).lower()
    label = re.sub(r"[^a-z0-9-]+", "-", label)
    label = re.sub(r"-+", "-", label).strip("-")
    return label


def parse_week_ranges(schedule_path: Path) -> dict[str, tuple[str, str]]:
    weeks: dict[str, tuple[str, str]] = {}
    for line in schedule_path.read_text(encoding="utf-8").splitlines():
        cells = split_table_row(line) if line.lstrip().startswith("|") else []
        if len(cells) >= 2 and re.fullmatch(r"W\d+", cells[0]):
            dates = [part.strip() for part in cells[1].split("~")]
            if len(dates) == 2:
                weeks[cells[0]] = (dates[0], dates[1])
    return weeks


def due_date_from_target(target: str, week_ranges: dict[str, tuple[str, str]]) -> str | None:
    target = target.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        return target

    exact_week = re.fullmatch(r"(\d{4})-\d{2}-(W\d+)", target)
    if exact_week and exact_week.group(2) in week_ranges:
        return week_ranges[exact_week.group(2)][1]

    week_span = re.search(r"(W\d+)\s+to\s+(W\d+)", target)
    if week_span and week_span.group(2) in week_ranges:
        return week_ranges[week_span.group(2)][1]

    return None


def infer_parent_id(phase: str | None) -> str | None:
    if not phase:
        return None
    match = re.search(r"Phase\s+(\d+)", phase)
    if not match:
        return None
    return f"EVM-EPIC-{int(match.group(1)):02d}"


def parse_issue_register(path: Path, week_ranges: dict[str, tuple[str, str]]) -> list[JiraItem]:
    items: list[JiraItem] = []
    section = ""
    phase: str | None = None
    headers: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line.removeprefix("## ").strip()
            phase = None
            headers = []
            continue
        if section == "Backlog" and line.startswith("### "):
            phase = line.removeprefix("### ").strip()
            headers = []
            continue
        if not line.lstrip().startswith("|"):
            continue

        cells = split_table_row(line)
        if is_separator_row(cells):
            continue
        if cells and cells[0] == "ID":
            headers = cells
            continue
        if not headers or not cells:
            continue

        row = {headers[idx]: cells[idx] for idx in range(min(len(headers), len(cells)))}
        source_id = strip_markup(row.get("ID", ""))
        if not source_id.startswith("EVM-"):
            continue

        if section == "Epic Register":
            target = row.get("Target Window", "")
            item = JiraItem(
                source_id=source_id,
                summary=strip_markup(row.get("Epic", "")),
                kind="Epic",
                status=strip_markup(row.get("Status", "")),
                target=strip_markup(target),
                acceptance=strip_markup(row.get("Outcome", "")),
                source_file=str(path.as_posix()),
                due_date=due_date_from_target(strip_markup(target), week_ranges),
                labels=[label_for(source_id), "epic"],
            )
            items.append(item)
            continue

        if section == "Backlog" and phase:
            target = row.get("Due", row.get("Target", ""))
            item = JiraItem(
                source_id=source_id,
                summary=strip_markup(row.get("Task", "")),
                kind="Task",
                status=strip_markup(row.get("Status", "")),
                target=strip_markup(target),
                acceptance=strip_markup(row.get("Acceptance Criteria", row.get("Evidence", ""))),
                source_file=str(path.as_posix()),
                due_date=due_date_from_target(strip_markup(target), week_ranges),
                phase=phase,
                parent_id=infer_parent_id(phase),
                labels=[label_for(source_id), "task"],
            )
            items.append(item)

    return items


def parse_schedule_tasks(path: Path) -> list[JiraItem]:
    items: list[JiraItem] = []
    week: str | None = None
    week_end: str | None = None
    headers: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+\d+\.\s+(W\d+):\s+(\d{4}-\d{2}-\d{2})\s+~\s+(\d{4}-\d{2}-\d{2})", line)
        if heading:
            week = heading.group(1)
            week_end = heading.group(3)
            headers = []
            continue
        if line.startswith("## "):
            week = None
            week_end = None
            headers = []
            continue
        if not week or not line.lstrip().startswith("|"):
            continue

        cells = split_table_row(line)
        if is_separator_row(cells):
            continue
        if cells and cells[0] == "ID":
            headers = cells
            continue
        if not headers or not cells:
            continue

        row = {headers[idx]: cells[idx] for idx in range(min(len(headers), len(cells)))}
        source_id = strip_markup(row.get("ID", ""))
        if not source_id.startswith("EVM-"):
            continue

        status = "Next" if week == "W0" else "Planned"
        items.append(
            JiraItem(
                source_id=source_id,
                summary=strip_markup(row.get("Task", "")),
                kind="Task",
                status=status,
                target=week or "",
                acceptance=strip_markup(row.get("Output", "")),
                source_file=str(path.as_posix()),
                due_date=week_end,
                week=week,
                output=strip_markup(row.get("Output", "")),
                labels=[label_for(source_id), "task", label_for(week)],
            )
        )

    return items


def load_items(project_root: Path) -> list[JiraItem]:
    issue_register = project_root / "docs" / "issues" / "issue-register.md"
    schedule = project_root / "docs" / "agenda" / "enterprise-mlops-accelerated-weekly-schedule.md"
    week_ranges = parse_week_ranges(schedule)
    merged: dict[str, JiraItem] = {}

    for item in parse_issue_register(issue_register, week_ranges):
        merged[item.source_id] = item

    for scheduled in parse_schedule_tasks(schedule):
        current = merged.get(scheduled.source_id)
        if current:
            current.week = current.week or scheduled.week
            current.output = current.output or scheduled.output
            current.due_date = current.due_date or scheduled.due_date
            current.labels = sorted(set(current.labels + scheduled.labels))
            continue
        merged[scheduled.source_id] = scheduled

    return sorted(merged.values(), key=item_sort_key)


def item_sort_key(item: JiraItem) -> tuple[int, str, str]:
    if item.kind == "Epic":
        return (0, item.source_id, item.due_date or "9999")
    return (1, item.due_date or "9999", item.source_id)


def paragraph(text: str) -> dict[str, Any]:
    if not text:
        text = "TBD"
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def adf_from_lines(lines: list[str]) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": [paragraph(line) for line in lines if line.strip()]}


def issue_description(item: JiraItem) -> dict[str, Any]:
    lines = [
        f"Source ID: {item.source_id}",
        f"Kind: {item.kind}",
        f"Status: {item.status}",
        f"Target: {item.target or 'TBD'}",
    ]
    if item.week:
        lines.append(f"Week: {item.week}")
    if item.phase:
        lines.append(f"Phase: {item.phase}")
    if item.due_date:
        lines.append(f"Due date: {item.due_date}")
    if item.acceptance:
        lines.append(f"Acceptance / outcome: {item.acceptance}")
    if item.output and item.output != item.acceptance:
        lines.append(f"Scheduled output: {item.output}")
    lines.extend(
        [
            f"Source file: {item.source_file}",
            "Sync policy: Git Markdown is the planning source; Jira is the visible execution board.",
        ]
    )
    return adf_from_lines(lines)


def load_config(args: argparse.Namespace) -> JiraConfig:
    missing = []
    env = {
        "JIRA_BASE_URL": os.getenv("JIRA_BASE_URL", "").rstrip("/"),
        "JIRA_EMAIL": os.getenv("JIRA_EMAIL", ""),
        "JIRA_API_TOKEN": os.getenv("JIRA_API_TOKEN", ""),
        "JIRA_PROJECT_KEY": args.project_key or os.getenv("JIRA_PROJECT_KEY", ""),
    }
    for key, value in env.items():
        if not value:
            missing.append(key)
    if missing:
        raise RuntimeError(f"Missing Jira configuration: {', '.join(missing)}")

    return JiraConfig(
        base_url=env["JIRA_BASE_URL"],
        email=env["JIRA_EMAIL"],
        api_token=env["JIRA_API_TOKEN"],
        project_key=env["JIRA_PROJECT_KEY"],
        epic_type=os.getenv("JIRA_EPIC_ISSUE_TYPE", "Epic"),
        task_type=os.getenv("JIRA_TASK_ISSUE_TYPE", "Task"),
    )


def auth_header(config: JiraConfig) -> str:
    token = base64.b64encode(f"{config.email}:{config.api_token}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def jira_request(
    config: JiraConfig,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{config.base_url}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": auth_header(config),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Jira API failed: {exc.code} {exc.reason}\n{body}") from exc


def labels_for_item(item: JiraItem, extra_labels: list[str]) -> list[str]:
    labels = set(DEFAULT_LABELS + extra_labels + item.labels)
    labels.add(label_for(item.source_id))
    labels.add("july-cut" if item.kind != "Epic" else "planning")
    return sorted(label for label in labels if label)


def issue_payload(
    config: JiraConfig,
    item: JiraItem,
    extra_labels: list[str],
    parent_key: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "project": {"key": config.project_key},
        "summary": f"[{item.source_id}] {item.summary}",
        "description": issue_description(item),
        "issuetype": {"name": config.epic_type if item.kind == "Epic" else config.task_type},
        "labels": labels_for_item(item, extra_labels),
    }
    if item.due_date:
        fields["duedate"] = item.due_date
    if parent_key:
        fields["parent"] = {"key": parent_key}
    return {"fields": fields}


def search_issue(config: JiraConfig, item: JiraItem) -> dict[str, Any] | None:
    label = label_for(item.source_id)
    jql = f'project = "{config.project_key}" AND labels = "{label}" ORDER BY created ASC'
    result = jira_request(
        config,
        "POST",
        "/rest/api/3/search/jql",
        {"jql": jql, "maxResults": 1, "fields": ["summary", "status", "labels"]},
    )
    issues = result.get("issues") or result.get("values") or []
    return issues[0] if issues else None


def create_or_update_issue(
    config: JiraConfig,
    item: JiraItem,
    extra_labels: list[str],
    parent_key: str | None,
) -> dict[str, str]:
    existing = search_issue(config, item)
    payload = issue_payload(config, item, extra_labels, parent_key)
    if existing:
        key = existing["key"]
        jira_request(config, "PUT", f"/rest/api/3/issue/{urllib.parse.quote(key)}", payload)
        return {"id": item.source_id, "action": "updated", "key": key}
    created = jira_request(config, "POST", "/rest/api/3/issue", payload)
    return {"id": item.source_id, "action": "created", "key": created.get("key", "")}


def filter_items(items: list[JiraItem], args: argparse.Namespace) -> list[JiraItem]:
    filtered = items
    if not args.include_done:
        filtered = [item for item in filtered if item.status != "Done"]
    if not args.include_deferred:
        filtered = [item for item in filtered if item.status != "Deferred"]

    mode = args.mode
    if mode == "epics":
        return [item for item in filtered if item.kind == "Epic"]
    if mode == "tasks":
        return [item for item in filtered if item.kind != "Epic"]
    return filtered


def parse_extra_labels(value: str) -> list[str]:
    return [label_for(item) for item in value.split(",") if item.strip()]


def dry_run(items: list[JiraItem], args: argparse.Namespace) -> int:
    extra_labels = parse_extra_labels(args.labels)
    result = {
        "project_key": args.project_key or os.getenv("JIRA_PROJECT_KEY") or "TBD",
        "mode": args.mode,
        "total": len(items),
        "items": [
            {
                "source_id": item.source_id,
                "kind": item.kind,
                "summary": item.summary,
                "status": item.status,
                "target": item.target,
                "due_date": item.due_date,
                "week": item.week,
                "labels": labels_for_item(item, extra_labels),
            }
            for item in items
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def sync(items: list[JiraItem], args: argparse.Namespace) -> int:
    config = load_config(args)
    extra_labels = parse_extra_labels(args.labels)
    results: list[dict[str, str]] = []
    epic_keys: dict[str, str] = {}

    jira_request(config, "GET", f"/rest/api/3/project/{urllib.parse.quote(config.project_key)}")

    for item in [candidate for candidate in items if candidate.kind == "Epic"]:
        result = create_or_update_issue(config, item, extra_labels, None)
        results.append(result)
        if result.get("key"):
            epic_keys[item.source_id] = result["key"]

    for item in [candidate for candidate in items if candidate.kind != "Epic"]:
        parent_key = epic_keys.get(item.parent_id or "") if args.parent_mode == "parent-field" else None
        results.append(create_or_update_issue(config, item, extra_labels, parent_key))

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync EVM Markdown roadmap and issue register to Jira.")
    parser.add_argument("--project-root", default=".", help="enterprise-vision-mlops project root.")
    parser.add_argument("--project-key", help="Jira project key. Defaults to JIRA_PROJECT_KEY.")
    parser.add_argument("--mode", choices=["all", "epics", "tasks"], default="all")
    parser.add_argument("--labels", default="", help="Comma-separated extra Jira labels.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned Jira payload summary.")
    parser.add_argument("--include-done", action="store_true", help="Include already completed baseline items.")
    parser.add_argument("--include-deferred", action="store_true", help="Include post-July deferred items.")
    parser.add_argument(
        "--parent-mode",
        choices=["none", "parent-field"],
        default="none",
        help="Use parent-field only when the Jira project supports Epic parent assignment.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        project_root = Path(args.project_root).resolve()
        items = filter_items(load_items(project_root), args)
        if args.dry_run:
            return dry_run(items, args)
        return sync(items, args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

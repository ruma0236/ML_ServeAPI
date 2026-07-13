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
    board_id: int | None
    sprint_prefix: str


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


def week_from_target(target: str, week_ranges: dict[str, tuple[str, str]]) -> str | None:
    target = target.strip()
    exact_week = re.fullmatch(r"(\d{4})-\d{2}-(W\d+)", target)
    if exact_week and exact_week.group(2) in week_ranges:
        return exact_week.group(2)

    week_span = re.search(r"(W\d+)\s+to\s+(W\d+)", target)
    if week_span and week_span.group(2) in week_ranges:
        return week_span.group(2)

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        for week, (start_date, end_date) in week_ranges.items():
            if start_date <= target <= end_date:
                return week

    return None


def week_from_phase(phase: str | None) -> str | None:
    if not phase:
        return None
    normalized = phase.lower()
    if "current-week" in normalized or "2026-07-06" in normalized:
        return "W4"
    if "phase 15" in normalized or "model lifecycle" in normalized:
        return "W5"
    if "phase 16" in normalized or "large-scale data" in normalized:
        return "W6"
    if "phase 17" in normalized or "agentops" in normalized:
        return "W7"
    if "phase 20" in normalized or "operator-centered reproducible" in normalized:
        return "W8"
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
                week=week_from_target(strip_markup(target), week_ranges),
                labels=[label_for(source_id), "epic"],
            )
            items.append(item)
            continue

        if section == "Backlog" and phase:
            target = row.get("Due", row.get("Target", ""))
            inferred_week = week_from_phase(phase) or week_from_target(strip_markup(target), week_ranges)
            item = JiraItem(
                source_id=source_id,
                summary=strip_markup(row.get("Task", "")),
                kind="Task",
                status=strip_markup(row.get("Status", "")),
                target=strip_markup(target),
                acceptance=strip_markup(row.get("Acceptance Criteria", row.get("Evidence", ""))),
                source_file=str(path.as_posix()),
                due_date=due_date_from_target(strip_markup(target), week_ranges),
                week=inferred_week,
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
        "JIRA_BASE_URL": normalize_base_url(os.getenv("JIRA_BASE_URL", "")),
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
        board_id=int(os.getenv("JIRA_BOARD_ID")) if os.getenv("JIRA_BOARD_ID") else None,
        sprint_prefix=os.getenv("JIRA_SPRINT_PREFIX", "EVM"),
    )


def normalize_base_url(value: str) -> str:
    stripped = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme and parsed.netloc and parsed.netloc.endswith(".atlassian.net"):
        return f"{parsed.scheme}://{parsed.netloc}"
    return stripped


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


def require_board_id(config: JiraConfig) -> int:
    if config.board_id is None:
        raise RuntimeError("Set JIRA_BOARD_ID before syncing or assigning Jira sprints.")
    return config.board_id


def sprint_name(config: JiraConfig, week: str, start_date: str, end_date: str) -> str:
    return f"{config.sprint_prefix} {week} {start_date}~{end_date}"


def sprint_goal(week: str) -> str:
    goals = {
        "W0": "Airflow foundation",
        "W1": "Full DAG and MLflow linkage",
        "W2": "MinIO and Parquet data platform",
        "W3": "Registry-driven serving and remote worker jobs",
        "W4": "Current-week enterprise VLM MLOps completion",
        "W5": "Real model lifecycle, serving, drift, and remote validation",
        "W6": "Accelerated data platform and Kubernetes runtime foundation",
        "W7": "Animated enterprise Control Panel, Kubernetes smoke proof, task/resource control, and serving-scale handoff",
        "W8": "Operator-centered reproducible Control Plane with purpose-based UX, immutable Run Blueprints, deterministic replay, and guarded experiment execution",
    }
    return goals.get(week, "Enterprise MLOps weekly execution")


def jira_datetime(date_value: str, end_of_day: bool = False) -> str:
    time_part = "23:59:59.000+09:00" if end_of_day else "00:00:00.000+09:00"
    return f"{date_value}T{time_part}"


def list_sprints(config: JiraConfig) -> list[dict[str, Any]]:
    board_id = require_board_id(config)
    sprints: list[dict[str, Any]] = []
    start_at = 0
    while True:
        result = jira_request(
            config,
            "GET",
            f"/rest/agile/1.0/board/{board_id}/sprint?state=active,future&startAt={start_at}&maxResults=50",
        )
        values = result.get("values", [])
        sprints.extend(values)
        if result.get("isLast", True) or not values:
            return sprints
        start_at += len(values)


def ensure_weekly_sprints(
    config: JiraConfig,
    week_ranges: dict[str, tuple[str, str]],
) -> dict[str, int]:
    board_id = require_board_id(config)
    existing = {str(sprint.get("name")): sprint for sprint in list_sprints(config)}
    sprint_ids: dict[str, int] = {}

    for week, (start_date, end_date) in week_ranges.items():
        name = sprint_name(config, week, start_date, end_date)
        if name in existing:
            sprint_ids[week] = int(existing[name]["id"])
            continue
        created = jira_request(
            config,
            "POST",
            "/rest/agile/1.0/sprint",
            {
                "name": name,
                "originBoardId": board_id,
                "startDate": jira_datetime(start_date),
                "endDate": jira_datetime(end_date, end_of_day=True),
                "goal": sprint_goal(week),
            },
        )
        sprint_ids[week] = int(created["id"])
    return sprint_ids


def move_issues_to_sprint(config: JiraConfig, sprint_id: int, issue_keys: list[str]) -> None:
    for offset in range(0, len(issue_keys), 50):
        chunk = issue_keys[offset : offset + 50]
        if not chunk:
            continue
        jira_request(
            config,
            "POST",
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            {"issues": chunk},
        )


def transition_targets_for_status(status: str) -> list[str]:
    defaults = {
        "Next": ["selected for development", "to do", "해야 할 일"],
        "In Progress": ["in progress", "진행 중"],
        "Blocked": ["blocked", "차단됨"],
        "Done": ["done", "완료"],
    }
    targets = list(defaults.get(status, []))
    env_map = os.getenv("JIRA_STATUS_TRANSITION_MAP", "")
    if env_map:
        try:
            parsed = json.loads(env_map)
            targets.extend(str(target) for target in parsed.get(status, []))
        except json.JSONDecodeError as exc:
            raise RuntimeError("JIRA_STATUS_TRANSITION_MAP must be valid JSON.") from exc
    return list(dict.fromkeys(str(target).lower() for target in targets))


def transition_issue_to_item_status(config: JiraConfig, issue_key: str, status: str) -> dict[str, str]:
    targets = transition_targets_for_status(status)
    if not targets:
        return {"key": issue_key, "transition": "skipped", "reason": f"no target for status {status}"}

    result = jira_request(
        config,
        "GET",
        f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/transitions",
    )
    for transition in result.get("transitions", []):
        name = str(transition.get("name", "")).lower()
        if name in targets:
            jira_request(
                config,
                "POST",
                f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/transitions",
                {"transition": {"id": transition["id"]}},
            )
            return {"key": issue_key, "transition": transition.get("name", "")}

    return {"key": issue_key, "transition": "skipped", "reason": f"no matching transition for {status}"}


def labels_for_item(item: JiraItem, extra_labels: list[str]) -> list[str]:
    labels = set(DEFAULT_LABELS + extra_labels + item.labels)
    labels.add(label_for(item.source_id))
    if item.week:
        labels.add(label_for(item.week))
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
    if issues:
        return issues[0]

    # Older issues were created before source-id labels were standardized. The
    # exact generated summary is a stable fallback and prevents duplicate Jira
    # work items when those records are brought under automated synchronization.
    summary = f"[{item.source_id}] {item.summary}".replace('"', '\\"')
    fallback = jira_request(
        config,
        "POST",
        "/rest/api/3/search/jql",
        {
            "jql": f'project = "{config.project_key}" AND summary ~ "\\\"{summary}\\\"" ORDER BY created ASC',
            "maxResults": 10,
            "fields": ["summary", "status", "labels"],
        },
    )
    candidates = fallback.get("issues") or fallback.get("values") or []
    return next(
        (candidate for candidate in candidates if candidate.get("fields", {}).get("summary") == summary),
        None,
    )


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
    if args.source_id:
        selected = {item.strip().upper() for item in args.source_id.split(",") if item.strip()}
        filtered = [item for item in filtered if item.source_id.upper() in selected]
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
    week_ranges = parse_week_ranges(Path(args.project_root).resolve() / "docs" / "agenda" / "enterprise-mlops-accelerated-weekly-schedule.md")
    if args.source_id and args.assign_sprints:
        requested_weeks = {item.week for item in items if item.week}
        week_ranges = {week: dates for week, dates in week_ranges.items() if week in requested_weeks}
    result = {
        "project_key": args.project_key or os.getenv("JIRA_PROJECT_KEY") or "TBD",
        "mode": args.mode,
        "sync_sprints": args.sync_sprints,
        "assign_sprints": args.assign_sprints,
        "transition_statuses": args.transition_statuses,
        "total": len(items),
        "planned_sprints": [
            {"week": week, "start_date": dates[0], "end_date": dates[1]}
            for week, dates in week_ranges.items()
        ]
        if args.sync_sprints or args.assign_sprints
        else [],
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
    results: list[dict[str, Any]] = []
    epic_keys: dict[str, str] = {}
    issue_keys_by_week: dict[str, list[str]] = {}
    sprint_ids: dict[str, int] = {}

    jira_request(config, "GET", f"/rest/api/3/project/{urllib.parse.quote(config.project_key)}")

    if args.sync_sprints or args.assign_sprints:
        schedule = Path(args.project_root).resolve() / "docs" / "agenda" / "enterprise-mlops-accelerated-weekly-schedule.md"
        week_ranges = parse_week_ranges(schedule)
        if args.source_id and args.assign_sprints:
            requested_weeks = {item.week for item in items if item.week}
            week_ranges = {week: dates for week, dates in week_ranges.items() if week in requested_weeks}
        sprint_ids = ensure_weekly_sprints(config, week_ranges)
        results.append({"action": "sprints-synced", "sprints": sprint_ids})

    for item in [candidate for candidate in items if candidate.kind == "Epic"]:
        result = create_or_update_issue(config, item, extra_labels, None)
        results.append(result)
        if result.get("key"):
            epic_keys[item.source_id] = result["key"]
            if args.transition_statuses:
                results.append(transition_issue_to_item_status(config, result["key"], item.status))

    for item in [candidate for candidate in items if candidate.kind != "Epic"]:
        parent_key = epic_keys.get(item.parent_id or "") if args.parent_mode == "parent-field" else None
        result = create_or_update_issue(config, item, extra_labels, parent_key)
        results.append(result)
        key = result.get("key")
        if key and item.week:
            issue_keys_by_week.setdefault(item.week, []).append(key)
        if key and args.transition_statuses:
            results.append(transition_issue_to_item_status(config, key, item.status))

    if args.assign_sprints:
        for week, issue_keys in issue_keys_by_week.items():
            sprint_id = sprint_ids.get(week)
            if sprint_id:
                move_issues_to_sprint(config, sprint_id, issue_keys)
                results.append({"action": "issues-assigned-to-sprint", "week": week, "sprint_id": sprint_id, "count": len(issue_keys)})

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync EVM Markdown roadmap and issue register to Jira.")
    parser.add_argument("--project-root", default=".", help="enterprise-vision-mlops project root.")
    parser.add_argument("--project-key", help="Jira project key. Defaults to JIRA_PROJECT_KEY.")
    parser.add_argument("--mode", choices=["all", "epics", "tasks"], default="all")
    parser.add_argument("--labels", default="", help="Comma-separated extra Jira labels.")
    parser.add_argument("--source-id", help="Comma-separated source ids to sync, for example EVM-021,EVM-022.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned Jira payload summary.")
    parser.add_argument("--include-done", action="store_true", help="Include already completed baseline items.")
    parser.add_argument("--include-deferred", action="store_true", help="Include post-July deferred items.")
    parser.add_argument("--sync-sprints", action="store_true", help="Create or reuse Jira sprints from the weekly schedule.")
    parser.add_argument("--assign-sprints", action="store_true", help="Move weekly tasks into the matching Jira sprint.")
    parser.add_argument("--transition-statuses", action="store_true", help="Transition Jira issues based on Markdown status when possible.")
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

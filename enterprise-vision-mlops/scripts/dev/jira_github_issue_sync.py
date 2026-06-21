from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from jira_sync import (
    JiraItem,
    create_or_update_issue,
    label_for,
    labels_for_item,
    load_config,
    parse_extra_labels,
    transition_issue_to_item_status,
)


def label_names(issue: dict) -> list[str]:
    return [str(label.get("name", "")) for label in issue.get("labels", [])]


def extract_source_id(issue: dict) -> str:
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    labels = label_names(issue)

    for value in [title, body, *labels]:
        match = re.search(r"\b(EVM-[A-Z0-9-]+)\b", value, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()

    return f"GH-ISSUE-{issue.get('number')}"


def status_from_github_issue(issue: dict) -> str:
    labels = {name.lower() for name in label_names(issue)}
    if issue.get("state") == "closed":
        return "Done"
    if "blocked" in labels:
        return "Blocked"
    if "in-progress" in labels or "in_progress" in labels or "doing" in labels:
        return "In Progress"
    return "Next"


def clean_summary(issue: dict, source_id: str) -> str:
    title = issue.get("title") or f"GitHub issue {issue.get('number')}"
    return re.sub(rf"^\[{re.escape(source_id)}\]\s*", "", title, flags=re.IGNORECASE).strip()


def item_from_issue(issue: dict, repo: str | None) -> JiraItem:
    source_id = extract_source_id(issue)
    labels = [
        "github-issue",
        f"gh-issue-{issue.get('number')}",
        label_for(source_id),
    ]
    if repo:
        labels.append(label_for(repo))

    return JiraItem(
        source_id=source_id,
        summary=clean_summary(issue, source_id),
        kind="Task",
        status=status_from_github_issue(issue),
        target="GitHub Issue",
        acceptance=(
            f"GitHub issue: {issue.get('html_url')}\n"
            f"State: {issue.get('state')}\n"
            f"Labels: {', '.join(label_names(issue)) or 'none'}"
        ),
        source_file=issue.get("html_url") or "GitHub issue event",
        labels=labels,
    )


def load_event(path: str) -> dict:
    if not path:
        raise RuntimeError("GitHub event path is required.")
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if "issue" not in payload:
        raise RuntimeError("GitHub event payload does not contain an issue.")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync one GitHub issue event to Jira.")
    parser.add_argument("--github-event-path", default=os.getenv("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--project-key", help="Jira project key. Defaults to JIRA_PROJECT_KEY.")
    parser.add_argument("--labels", default="", help="Comma-separated extra Jira labels.")
    parser.add_argument("--transition-statuses", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        payload = load_event(args.github_event_path)
        issue = payload["issue"]
        repo = (payload.get("repository") or {}).get("full_name")
        item = item_from_issue(issue, repo)
        extra_labels = parse_extra_labels(args.labels)

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "source_id": item.source_id,
                        "summary": item.summary,
                        "status": item.status,
                        "labels": labels_for_item(item, extra_labels),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        config = load_config(args)
        result = create_or_update_issue(config, item, extra_labels, None)
        output: list[dict[str, str]] = [result]
        if args.transition_statuses and result.get("key"):
            output.append(transition_issue_to_item_status(config, result["key"], item.status))
        print(json.dumps({"results": output}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

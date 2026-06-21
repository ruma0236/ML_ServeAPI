from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import quote
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LABELS = ["mlops", "bug", "codex-managed"]
LABEL_DEFINITIONS = {
    "mlops": {"color": "0E8A16", "description": "MLOps platform work"},
    "bug": {"color": "D73A4A", "description": "Something is not working"},
    "codex-managed": {"color": "5319E7", "description": "Created or managed by Codex workflow"},
}


@dataclass(frozen=True)
class GitContext:
    repo: str
    branch: str
    commit: str


def run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def infer_repo(remote_url: str) -> str:
    value = remote_url.strip()
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/")
        return value.removesuffix(".git")
    if value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
        return value.removesuffix(".git")
    raise ValueError(f"Cannot infer GitHub owner/repo from remote URL: {remote_url}")


def git_context(cwd: Path, repo_override: str | None = None) -> GitContext:
    remote = run_git(["remote", "get-url", "origin"], cwd)
    branch = run_git(["branch", "--show-current"], cwd)
    commit = run_git(["rev-parse", "--short", "HEAD"], cwd)
    return GitContext(repo=repo_override or infer_repo(remote), branch=branch, commit=commit)


def read_optional_file(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def normalize_labels(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_LABELS
    labels = [item.strip() for item in value.split(",") if item.strip()]
    return labels or DEFAULT_LABELS


def bug_body(args: argparse.Namespace, ctx: GitContext) -> str:
    logs = read_optional_file(args.logs_file)
    validation = read_optional_file(args.validation_file) or args.validation
    body = f"""## Summary

{args.summary}

## Context

- Issue ID: `{args.issue_id}`
- Branch: `{ctx.branch}`
- Commit: `{ctx.commit}`
- Repository: `{ctx.repo}`

## Reproduction

```bash
{args.reproduction}
```

## Observed Behavior

{args.observed}

## Expected Behavior

{args.expected}

## Initial Triage

{args.triage or "TBD"}

## Validation Plan

```bash
{validation or "TBD"}
```
"""
    if logs:
        body += f"""

## Logs

```text
{logs}
```
"""
    body += """

## Resolution Log

- Root cause: TBD
- Fix: TBD
- Verification: TBD
- Residual risk: TBD
"""
    return body.strip() + "\n"


def resolution_body(args: argparse.Namespace) -> str:
    verification = read_optional_file(args.verification_file) or args.verification
    body = f"""## Resolution

### Root Cause

{args.root_cause}

### Fix

{args.fix}

### Verification

```bash
{verification}
```

### Residual Risk

{args.residual_risk or "None currently known."}
"""
    return body.strip() + "\n"


def github_request(
    method: str,
    repo: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}{path}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API failed: {exc.code} {exc.reason}\n{body}") from exc


def ensure_labels(repo: str, token: str, labels: list[str]) -> None:
    for label in labels:
        encoded = quote(label, safe="")
        try:
            github_request("GET", repo, f"/labels/{encoded}", token)
            continue
        except RuntimeError as exc:
            if "GitHub API failed: 404" not in str(exc):
                raise

        definition = LABEL_DEFINITIONS.get(
            label,
            {"color": "C5DEF5", "description": "Managed by repository automation"},
        )
        github_request(
            "POST",
            repo,
            "/labels",
            token,
            {"name": label, **definition},
        )


def require_token() -> str:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN or GH_TOKEN before calling GitHub API.")
    return token


def create_bug(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    ctx = git_context(cwd, args.repo)
    title = args.title or f"[{args.issue_id}] {args.summary}"
    body = bug_body(args, ctx)
    labels = normalize_labels(args.labels)
    payload = {"title": title, "body": body, "labels": labels}

    if args.dry_run:
        print(
            json.dumps(
                {"repo": ctx.repo, "ensure_labels": not args.skip_label_sync, "payload": payload},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    token = require_token()
    if labels and not args.skip_label_sync:
        ensure_labels(ctx.repo, token, labels)
    issue = github_request("POST", ctx.repo, "/issues", token, payload)
    print(json.dumps({"number": issue.get("number"), "url": issue.get("html_url")}, indent=2))
    return 0


def comment_issue(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    ctx = git_context(cwd, args.repo)
    body = read_optional_file(args.body_file) or args.body
    if not body:
        raise RuntimeError("Comment body is required.")
    if args.dry_run:
        print(json.dumps({"repo": ctx.repo, "issue": args.issue_number, "body": body}, indent=2))
        return 0
    comment = github_request(
        "POST",
        ctx.repo,
        f"/issues/{args.issue_number}/comments",
        require_token(),
        {"body": body},
    )
    print(json.dumps({"url": comment.get("html_url")}, indent=2))
    return 0


def resolve_issue(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    ctx = git_context(cwd, args.repo)
    body = resolution_body(args)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "repo": ctx.repo,
                    "issue": args.issue_number,
                    "comment": body,
                    "close": args.close,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    token = require_token()
    comment = github_request(
        "POST",
        ctx.repo,
        f"/issues/{args.issue_number}/comments",
        token,
        {"body": body},
    )
    result: dict[str, Any] = {"comment_url": comment.get("html_url")}
    if args.close:
        issue = github_request(
            "PATCH",
            ctx.repo,
            f"/issues/{args.issue_number}",
            token,
            {"state": "closed"},
        )
        result["issue_state"] = issue.get("state")
        result["issue_url"] = issue.get("html_url")
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and resolve GitHub issues for MLOps work.")
    parser.add_argument("--cwd", default=".", help="Git repository root or child directory.")
    parser.add_argument("--repo", help="GitHub owner/repo override.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-bug", help="Create a GitHub bug issue.")
    create.add_argument("--issue-id", required=True)
    create.add_argument("--summary", required=True)
    create.add_argument("--title")
    create.add_argument("--reproduction", required=True)
    create.add_argument("--observed", required=True)
    create.add_argument("--expected", required=True)
    create.add_argument("--triage", default="")
    create.add_argument("--validation", default="")
    create.add_argument("--validation-file")
    create.add_argument("--logs-file")
    create.add_argument("--labels", default="mlops,bug,codex-managed")
    create.add_argument("--skip-label-sync", action="store_true")
    create.add_argument("--dry-run", action="store_true")
    create.set_defaults(func=create_bug)

    comment = subparsers.add_parser("comment", help="Add a comment to a GitHub issue.")
    comment.add_argument("--issue-number", required=True, type=int)
    comment.add_argument("--body", default="")
    comment.add_argument("--body-file")
    comment.add_argument("--dry-run", action="store_true")
    comment.set_defaults(func=comment_issue)

    resolve = subparsers.add_parser("resolve", help="Comment on and optionally close an issue.")
    resolve.add_argument("--issue-number", required=True, type=int)
    resolve.add_argument("--root-cause", required=True)
    resolve.add_argument("--fix", required=True)
    resolve.add_argument("--verification", required=True)
    resolve.add_argument("--verification-file")
    resolve.add_argument("--residual-risk", default="")
    resolve.add_argument("--close", action="store_true")
    resolve.add_argument("--dry-run", action="store_true")
    resolve.set_defaults(func=resolve_issue)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

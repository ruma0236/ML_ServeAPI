"""R7 local-code closure only; never invokes production admission or R8 runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from evm.scale_validation import r7_local_closure


def _candidate(project: Path, git: Path) -> tuple[str, str]:
    if not git.is_absolute() or not git.is_file():
        raise ValueError("absolute_git_executable_required")
    module = Path(r7_local_closure.__file__).resolve()
    if not module.is_relative_to(project / "src"):
        raise ValueError("closure_module_origin_mismatch")

    def read(*args: str) -> str:
        result = subprocess.run(
            [str(git), "-C", str(project), *args],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise ValueError(f"git_readback_failed:{args[0]}:{result.returncode}")
        return result.stdout.decode("utf-8").strip()

    commit, tree = read("rev-parse", "HEAD", "HEAD^{tree}").splitlines()
    # User untracked files are permitted, but the executing closure code itself
    # must belong to this candidate, not an untracked locally injected tool.
    for source in (module, Path(__file__).resolve()):
        relative = source.relative_to(project).as_posix()
        if read("ls-files", "--error-unmatch", "--", relative) != relative:
            raise ValueError("closure_executable_source_not_tracked")
    if read("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked_candidate_not_clean")
    return commit, tree


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("assess", "publish"))
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--git-executable", required=True, type=Path)
    parser.add_argument("--expected-git-sha256", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--expected-review-sha256")
    parser.add_argument("--output-parent", type=Path)
    args = parser.parse_args(argv)
    try:
        project = args.project_root.resolve(strict=True)
        if hashlib.sha256(args.git_executable.read_bytes()).hexdigest() != (
            args.expected_git_sha256
        ):
            raise ValueError("git_executable_hash_mismatch")
        commit, tree = _candidate(project, args.git_executable)
        now = datetime.now(timezone.utc)
        if args.action == "assess":
            result = r7_local_closure.assess(
                args.manifest,
                expected_commit=commit,
                expected_tree=tree,
                now=now,
            )
        else:
            if not (args.review and args.expected_review_sha256 and args.output_parent):
                raise ValueError("review_pin_and_output_parent_required")
            result = r7_local_closure.publish_local_closure(
                args.output_parent,
                args.manifest,
                args.review,
                args.expected_review_sha256,
                expected_commit=commit,
                expected_tree=tree,
                now=now,
            )
        if _candidate(project, args.git_executable) != (commit, tree):
            raise ValueError("candidate_changed_during_closure")
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(
                {
                    "r7s5_decision": "NO-GO",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "publication_failure_observation": (
                        exc.observation.to_dict() if hasattr(exc, "observation") else None
                    ),
                    "automatic_retry": False,
                    "production_authority": False,
                    "r8_runtime_started": False,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

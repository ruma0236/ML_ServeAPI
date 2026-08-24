from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.e0_remediation_evidence import (  # noqa: E402
    build_remediation_review,
    validate_remediation_review,
    write_private_index,
)
from evm.scale_validation.e0_runtime import E0RuntimeConfig, canonical  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write strict E0 remediation evidence.")
    parser.add_argument(
        "--original-evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-e0-environment-experiment.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_e0_environment_v1.toml",
    )
    parser.add_argument("--original-private-root", type=Path, required=True)
    parser.add_argument("--remediation-root", type=Path, required=True)
    parser.add_argument("--current-smoke", required=True)
    parser.add_argument("--failed-smoke", required=True)
    parser.add_argument("--accepted-regressions", required=True)
    parser.add_argument("--failed-regressions", required=True)
    parser.add_argument("--focused-e0", required=True)
    parser.add_argument("--mutation-regressions", required=True)
    parser.add_argument("--strict-validator", required=True)
    parser.add_argument("--changed-files-lint", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-e0-remediation-review.json",
    )
    return parser.parse_args()


def git_value(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    args = parse_args()
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    revision = git_value("rev-parse", "HEAD")
    references = {
        "historical_prometheus": "historical-prometheus.json",
        "current_smoke": args.current_smoke,
        "failed_smoke": args.failed_smoke,
        "accepted_regressions": args.accepted_regressions,
        "failed_regressions": args.failed_regressions,
        "focused_e0": args.focused_e0,
        "mutation_regressions": args.mutation_regressions,
        "strict_validator": args.strict_validator,
        "changed_files_lint": args.changed_files_lint,
    }
    write_private_index(args.remediation_root, generated_at=generated_at)
    payload = build_remediation_review(
        generated_at=generated_at,
        original_evidence_path=args.original_evidence,
        config=E0RuntimeConfig.from_path(args.config),
        original_private_root=args.original_private_root,
        remediation_root=args.remediation_root,
        project_root=ROOT,
        validation_revision=revision,
        references=references,
    )
    args.output.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")
    result = validate_remediation_review(
        json.loads(args.output.read_bytes()),
        original_evidence_path=args.original_evidence,
        config=E0RuntimeConfig.from_path(args.config),
        original_private_root=args.original_private_root,
        remediation_root=args.remediation_root,
        project_root=ROOT,
    )
    print(
        canonical(
            {
                **result,
                "output": args.output.relative_to(ROOT).as_posix(),
                "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

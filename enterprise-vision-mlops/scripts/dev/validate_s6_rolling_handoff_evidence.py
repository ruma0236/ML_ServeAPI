from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.s6_evidence import (  # noqa: E402
    S6EvidenceValidationError,
    validate_s6_experiment,
)
from evm.scale_validation.s6_runtime import S6RuntimeConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S6 rolling/handoff evidence.")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s6-rolling-handoff-experiment.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s6_rolling_handoff.toml",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--git-revision")
    return parser.parse_args()


def load_bytes(path: Path, git_revision: str | None, git_root: Path) -> bytes:
    if not git_revision:
        return path.read_bytes()
    relative = path.resolve().relative_to(git_root).as_posix()
    return subprocess.run(
        ["git", "show", f"{git_revision}:{relative}"],
        cwd=git_root,
        check=True,
        capture_output=True,
    ).stdout


def main() -> int:
    args = parse_args()
    git_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve()
    raw = load_bytes(args.evidence, args.git_revision, git_root)
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        raise S6EvidenceValidationError("s6_evidence_not_canonical_lf")
    payload = json.loads(raw)
    config = S6RuntimeConfig.from_path(args.config, data_root=args.data_root)
    result = validate_s6_experiment(
        payload,
        config=config,
        private_root=args.private_root,
        git_root=git_root,
        validation_revision=args.git_revision or "HEAD",
    )
    result["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
    result["hash_source"] = args.git_revision or "worktree"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

from evm.scale_validation.s3_evidence import (  # noqa: E402
    S3EvidenceValidationError,
    validate_s3_capacity_closure,
    validate_s3_capacity_evidence,
)
from evm.scale_validation.s3_runtime import S3RuntimeConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S3 capacity evidence.")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s3-capacity-experiment.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s3_capacity_runtime.toml",
    )
    parser.add_argument(
        "--closure",
        type=Path,
        default=ROOT / "docs/status/evidence/s3-capacity-closure.json",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument("--git-revision")
    return parser.parse_args()


def load_evidence(path: Path, git_revision: str | None) -> bytes:
    if not git_revision:
        return path.read_bytes()
    git_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve()
    relative = path.resolve().relative_to(git_root).as_posix()
    return subprocess.run(
        ["git", "show", f"{git_revision}:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def main() -> int:
    args = parse_args()
    raw = load_evidence(args.evidence, args.git_revision)
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        raise S3EvidenceValidationError("s3_capacity_evidence_not_canonical_lf")
    payload = json.loads(raw)
    config = S3RuntimeConfig.from_path(args.config, data_root=args.data_root)
    result = validate_s3_capacity_evidence(payload, config=config)
    evidence_sha256 = hashlib.sha256(raw).hexdigest()
    result["evidence_sha256"] = evidence_sha256
    closure_raw = load_evidence(args.closure, args.git_revision)
    if b"\r\n" in closure_raw or not closure_raw.endswith(b"\n"):
        raise S3EvidenceValidationError("s3_capacity_closure_not_canonical_lf")
    closure_result = validate_s3_capacity_closure(
        json.loads(closure_raw),
        experiment=payload,
        experiment_sha256=evidence_sha256,
        config=config,
    )
    result["closure"] = closure_result
    result["closure_sha256"] = hashlib.sha256(closure_raw).hexdigest()
    result["hash_source"] = args.git_revision or "worktree"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

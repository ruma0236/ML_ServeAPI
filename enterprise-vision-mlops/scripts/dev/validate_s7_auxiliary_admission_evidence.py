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

from evm.scale_validation.s7_evidence import (  # noqa: E402
    S7EvidenceValidationError,
    validate_s7_closure,
    validate_s7_experiment,
)
from evm.scale_validation.s7_runtime import S7RuntimeConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S7 auxiliary admission evidence.")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s7-auxiliary-admission-reprojection.json",
    )
    parser.add_argument(
        "--closure",
        type=Path,
        default=ROOT / "docs/status/evidence/s7-auxiliary-admission-closure.json",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "configs/s7_family_admission.toml")
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument(
        "--runtime-smoke",
        type=Path,
        default=ROOT / "docs/status/evidence/s7-current-revision-cuda-smoke.json",
    )
    parser.add_argument("--runtime-smoke-private-root", type=Path)
    parser.add_argument(
        "--regression-evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s7-reclosure-regression-evidence.json",
    )
    parser.add_argument("--regression-root", type=Path)
    parser.add_argument("--git-revision")
    parser.add_argument("--require-closure", action="store_true")
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
    require_canonical(raw, "s7_evidence")
    payload = json.loads(raw)
    config = S7RuntimeConfig.from_path(args.config)
    result = validate_s7_experiment(
        payload,
        config=config,
        private_root=args.private_root,
        git_root=git_root,
        validation_revision=args.git_revision or "HEAD",
    )
    result["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
    if args.require_closure:
        if args.runtime_smoke_private_root is None or args.regression_root is None:
            raise S7EvidenceValidationError(
                "s7_closure_requires_runtime_smoke_private_root_and_regression_root"
            )
        closure_raw = load_bytes(args.closure, args.git_revision, git_root)
        require_canonical(closure_raw, "s7_closure")
        smoke_raw = load_bytes(args.runtime_smoke, args.git_revision, git_root)
        regression_raw = load_bytes(args.regression_evidence, args.git_revision, git_root)
        require_canonical(smoke_raw, "s7_runtime_smoke")
        require_canonical(regression_raw, "s7_regression")
        result["closure"] = validate_s7_closure(
            json.loads(closure_raw),
            experiment=payload,
            experiment_sha256=result["evidence_sha256"],
            config=config,
            private_root=args.private_root,
            runtime_smoke=json.loads(smoke_raw),
            runtime_smoke_sha256=hashlib.sha256(smoke_raw).hexdigest(),
            runtime_smoke_private_root=args.runtime_smoke_private_root,
            regression_evidence=json.loads(regression_raw),
            regression_evidence_sha256=hashlib.sha256(regression_raw).hexdigest(),
            regression_root=args.regression_root,
            git_root=git_root,
            validation_revision=args.git_revision or "HEAD",
        )
        result["closure_sha256"] = hashlib.sha256(closure_raw).hexdigest()
    result["hash_source"] = args.git_revision or "worktree"
    print(json.dumps(result, sort_keys=True))
    return 0


def require_canonical(raw: bytes, label: str) -> None:
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        raise S7EvidenceValidationError(f"{label}_not_canonical_lf")


if __name__ == "__main__":
    raise SystemExit(main())

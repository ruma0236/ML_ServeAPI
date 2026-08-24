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

from evm.scale_validation.s8_evidence import validate_s8_experiment  # noqa: E402
from evm.scale_validation.s8_runtime import S8RuntimeConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S8 dependency-soak evidence.")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-dependency-soak-experiment.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_dependency_soak_v6.toml",
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--git-revision")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.git_revision:
        git_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        relative = args.evidence.resolve().relative_to(git_root.resolve()).as_posix()
        raw = subprocess.run(
            ["git", "show", f"{args.git_revision}:{relative}"],
            cwd=git_root,
            capture_output=True,
            check=True,
        ).stdout
    else:
        raw = args.evidence.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise RuntimeError("s8_public_evidence_not_canonical_lf")
    payload = json.loads(raw)
    result = validate_s8_experiment(
        payload,
        config=S8RuntimeConfig.from_path(args.config),
        private_root=args.private_root,
        project_root=ROOT,
        validation_revision=args.git_revision or "HEAD",
    )
    result["evidence_sha256"] = hashlib.sha256(raw).hexdigest()
    result["hash_source"] = args.git_revision or "worktree"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

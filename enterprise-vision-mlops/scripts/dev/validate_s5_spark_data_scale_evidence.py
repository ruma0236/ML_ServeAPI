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

from evm.scale_validation.s5_evidence import (  # noqa: E402
    S5EvidenceValidationError,
    validate_s5_spark_data_scale_closure,
    validate_s5_spark_data_scale_evidence,
)
from evm.scale_validation.s5_runtime import S5RuntimeConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S5 Spark scale evidence.")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s5-spark-data-scale-experiment.json",
    )
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/s5_spark_data_scale.toml"
    )
    parser.add_argument(
        "--closure",
        type=Path,
        default=ROOT / "docs/status/evidence/s5-spark-data-scale-closure.json",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument("--private-root", type=Path)
    parser.add_argument(
        "--runtime-smoke",
        type=Path,
        default=ROOT / "docs/status/evidence/s5-current-revision-runtime-smoke.json",
    )
    parser.add_argument("--runtime-smoke-private-root", type=Path)
    parser.add_argument(
        "--regression-evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s5-reclosure-regression-evidence.json",
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
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        raise S5EvidenceValidationError("s5_evidence_not_canonical_lf")
    payload = json.loads(raw)
    config = S5RuntimeConfig.from_path(args.config, data_root=args.data_root)
    private_root = args.private_root or config.private_root / str(payload.get("suite_id"))
    result = validate_s5_spark_data_scale_evidence(
        payload,
        config=config,
        git_root=git_root,
        validation_revision=args.git_revision or "HEAD",
        private_root=private_root,
    )
    evidence_sha256 = hashlib.sha256(raw).hexdigest()
    result["evidence_sha256"] = evidence_sha256
    if args.require_closure:
        closure_raw = load_bytes(args.closure, args.git_revision, git_root)
        if b"\r\n" in closure_raw or not closure_raw.endswith(b"\n"):
            raise S5EvidenceValidationError("s5_closure_not_canonical_lf")
        smoke_raw = load_bytes(args.runtime_smoke, args.git_revision, git_root)
        regression_raw = load_bytes(args.regression_evidence, args.git_revision, git_root)
        for label, supporting_raw in (
            ("s5_runtime_smoke", smoke_raw),
            ("s5_regression", regression_raw),
        ):
            if b"\r\n" in supporting_raw or not supporting_raw.endswith(b"\n"):
                raise S5EvidenceValidationError(f"{label}_not_canonical_lf")
        smoke_payload = json.loads(smoke_raw)
        runtime_smoke_private_root = args.runtime_smoke_private_root or (
            args.data_root
            / "artifacts/scale_validation/private/s5"
            / str(smoke_payload.get("suite_id") or "")
        )
        result["closure"] = validate_s5_spark_data_scale_closure(
            json.loads(closure_raw),
            experiment=payload,
            experiment_sha256=evidence_sha256,
            config=config,
            git_root=git_root,
            validation_revision=args.git_revision or "HEAD",
            private_root=private_root,
            runtime_smoke=smoke_payload,
            runtime_smoke_sha256=hashlib.sha256(smoke_raw).hexdigest(),
            runtime_smoke_private_root=runtime_smoke_private_root,
            regression_evidence=json.loads(regression_raw),
            regression_evidence_sha256=hashlib.sha256(regression_raw).hexdigest(),
            regression_root=args.regression_root,
        )
        result["closure_sha256"] = hashlib.sha256(closure_raw).hexdigest()
    result["hash_source"] = args.git_revision or "worktree"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

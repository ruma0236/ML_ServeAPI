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

from evm.scale_validation.e0_evidence import validate_e0_evidence  # noqa: E402
from evm.scale_validation.e0_runtime import E0RuntimeConfig, canonical  # noqa: E402
from evm.scale_validation.e0_strict_evidence import (  # noqa: E402
    strict_revalidate_e0_runtime,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strictly revalidate immutable E0 evidence.")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-e0-environment-experiment.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_e0_environment_v1.toml",
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--prometheus-history", type=Path, required=True)
    parser.add_argument("--private-projection", type=Path)
    parser.add_argument("--validation-revision", default="HEAD")
    return parser.parse_args()


def git_value(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    args = parse_args()
    evidence_bytes = args.evidence.read_bytes()
    if b"\r" in evidence_bytes or not evidence_bytes.endswith(b"\n"):
        raise RuntimeError("e0_public_evidence_not_canonical_lf")
    experiment = json.loads(evidence_bytes)
    config = E0RuntimeConfig.from_path(args.config)
    historical = validate_e0_evidence(
        experiment,
        config=config,
        private_root=args.private_root,
        project_root=ROOT,
        validation_revision=args.validation_revision,
    )
    prometheus_bytes = args.prometheus_history.read_bytes()
    strict = strict_revalidate_e0_runtime(
        experiment,
        config=config,
        private_root=args.private_root,
        prometheus_history=json.loads(prometheus_bytes),
    )
    projection = {
        "schema_version": "evm.s8_v4.e0_strict_private_projection.v2",
        "validation_revision": git_value("rev-parse", args.validation_revision),
        "validation_tree_sha": git_value("rev-parse", f"{args.validation_revision}^{{tree}}"),
        "historical_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "prometheus_history_sha256": hashlib.sha256(prometheus_bytes).hexdigest(),
        "historical_validation": historical,
        "strict_revalidation": strict,
    }
    if args.private_projection is not None:
        args.private_projection.parent.mkdir(parents=True, exist_ok=True)
        args.private_projection.write_text(
            canonical(projection) + "\n", encoding="utf-8", newline="\n"
        )
    print(
        canonical(
            {
                "valid": True,
                "historical_attempt_count": historical["attempt_count"],
                "strict_attempt_count": len(strict["attempts"]),
                "acceptance": strict["acceptance"],
                "evidence_ready": strict["evidence_ready"],
                "historical_evidence_sha256": projection["historical_evidence_sha256"],
                "prometheus_history_sha256": projection["prometheus_history_sha256"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

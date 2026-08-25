from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.x1_resume_testbed import (  # noqa: E402
    X1ResumeConfig,
    canonical,
    generate_report,
    sha256_file,
    validate_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate X1 Resume Testbed v1 evidence.")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/s8_v4_x1_resume_testbed_v1.toml"
    )
    parser.add_argument("--private-suite-root", type=Path)
    parser.add_argument("--model-repository-root", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--report-output", type=Path)
    args = parser.parse_args()
    raw = args.evidence.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise RuntimeError("x1_resume_evidence_not_canonical_lf")
    payload = json.loads(raw)
    config = X1ResumeConfig.from_path(args.config)
    if (args.private_suite_root is None) != (args.model_repository_root is None):
        raise RuntimeError("x1_resume_private_validation_paths_incomplete")
    result = validate_evidence(
        payload,
        config,
        private_suite_root=args.private_suite_root,
        model_repository_root=args.model_repository_root,
        source_root=args.source_root if args.private_suite_root is not None else None,
    )
    result["evidence_sha256"] = sha256_file(args.evidence)
    if args.report_output:
        if args.private_suite_root is None:
            raise RuntimeError("x1_resume_report_requires_private_validation")
        if args.report_output.exists():
            raise RuntimeError("x1_resume_report_output_exists")
        from evm.scale_validation.x1_resume_testbed import canonical_write

        canonical_write(
            args.report_output,
            generate_report(
                payload,
                config,
                private_suite_root=args.private_suite_root,
                model_repository_root=args.model_repository_root,
                source_root=args.source_root,
            ),
        )
        result["report_output"] = str(args.report_output)
    print(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.x1_resume_testbed import (  # noqa: E402
    DEFAULT_CONFIG_RELATIVE_PATH,
    X1ResumeConfig,
    canonical,
    canonical_write_once,
    ensure_distinct_output_targets,
    generate_report,
    load_canonical_json,
    require_default_config_path,
    sha256_file,
    validate_evidence,
    validate_report_binding,
    validate_result_git_binding,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate X1 Resume Testbed v1 evidence.")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / DEFAULT_CONFIG_RELATIVE_PATH)
    parser.add_argument("--private-suite-root", type=Path)
    parser.add_argument("--model-repository-root", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--report", "--report-input", dest="report", type=Path)
    parser.add_argument("--require-git-binding", action="store_true")
    parser.add_argument("--result-revision", default="HEAD")
    args = parser.parse_args()
    payload = load_canonical_json(args.evidence, label="evidence")
    config = X1ResumeConfig.from_path(require_default_config_path(args.config, ROOT))
    private_paths = (
        args.private_suite_root,
        args.model_repository_root,
        args.data_root,
    )
    if any(value is None for value in private_paths) and any(
        value is not None for value in private_paths
    ):
        raise RuntimeError("x1_resume_private_validation_paths_incomplete")
    result = validate_evidence(
        payload,
        config,
        private_suite_root=args.private_suite_root,
        model_repository_root=args.model_repository_root,
        source_root=args.source_root if args.private_suite_root is not None else None,
        data_root=args.data_root,
    )
    result["evidence_sha256"] = sha256_file(args.evidence)
    if args.report:
        if args.private_suite_root is None:
            raise RuntimeError("x1_resume_report_requires_private_validation")
        report_payload = load_canonical_json(args.report, label="report")
        validate_report_binding(
            report_payload,
            payload,
            config,
            evidence_path=args.evidence,
            private_suite_root=args.private_suite_root,
            model_repository_root=args.model_repository_root,
            source_root=args.source_root,
            data_root=args.data_root,
        )
        result["report_binding_valid"] = True
        result["report_sha256"] = sha256_file(args.report)
        if args.require_git_binding:
            result["git_binding"] = validate_result_git_binding(
                evidence_path=args.evidence,
                report_path=args.report,
                source_root=args.source_root,
                source_revision=str(dict(payload.get("source_identity", {})).get("revision") or ""),
                result_revision=args.result_revision,
            )
    elif args.require_git_binding:
        raise RuntimeError("x1_resume_git_binding_requires_report")
    if args.report_output:
        if args.private_suite_root is None:
            raise RuntimeError("x1_resume_report_requires_private_validation")
        targets = [args.evidence, args.report_output]
        if args.report:
            targets.append(args.report)
        ensure_distinct_output_targets(*targets)
        canonical_write_once(
            args.report_output,
            generate_report(
                payload,
                config,
                evidence_path=args.evidence,
                private_suite_root=args.private_suite_root,
                model_repository_root=args.model_repository_root,
                source_root=args.source_root,
                data_root=args.data_root,
            ),
        )
        result["report_output"] = str(args.report_output)
    print(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

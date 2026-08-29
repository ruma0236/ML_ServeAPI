from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

from evm.scale_validation.s6bm_runtime import canonical, sha256_file  # noqa: E402
from validate_s8_v4_s6bm import validate as validate_experiment  # noqa: E402


REQUIRED_CLEANUP = (
    "b0_uid_exact",
    "b0_image_exact",
    "b0_cuda_inference",
    "container_absent",
    "ports_absent",
    "prometheus_targets_restored",
    "temporary_prometheus_targets_absent",
    "gpu_lease_absent",
    "queue_active_zero",
    "queue_leased_zero",
    "queue_outcome_unknown_zero",
    "vram_restored",
)
EXECUTION_PATHS = (
    "enterprise-vision-mlops/apps/api/main.py",
    "enterprise-vision-mlops/src/evm/model_runtime/triton_blue_green.py",
    "enterprise-vision-mlops/src/evm/scale_validation/s6bm_causal.py",
    "enterprise-vision-mlops/src/evm/scale_validation/s6bm_observability.py",
    "enterprise-vision-mlops/src/evm/scale_validation/s6bm_runtime.py",
    "enterprise-vision-mlops/scripts/dev/run_s8_v4_s6bm_experiment.py",
    "enterprise-vision-mlops/configs/s8_v4_s6bm_blue_green_v4.toml",
)
VALIDATION_PATHS = (
    "enterprise-vision-mlops/scripts/dev/validate_s8_v4_s6bm.py",
    "enterprise-vision-mlops/scripts/dev/validate_s8_v4_s6bm_continuity_qualification.py",
    "enterprise-vision-mlops/scripts/dev/validate_s8_v4_s6bm_strict_v4_qualification.py",
    "enterprise-vision-mlops/scripts/dev/write_s8_v4_s6bm_review.py",
)


class S6BMReviewError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write S6B-M source-local review handoff.")
    parser.add_argument(
        "--experiment",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-experiment-v4.json",
    )
    parser.add_argument(
        "--mutation",
        type=Path,
        default=(ROOT / "docs/status/evidence/s8-v4-s6bm-integrated-mutation-validation-v4.json"),
    )
    parser.add_argument(
        "--strict-validation",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-integrated-validation-v4.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_s6bm_blue_green_v4.toml",
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--ordinary-qualification-root", type=Path, required=True)
    parser.add_argument("--continuity-qualification-root", type=Path, required=True)
    parser.add_argument("--regression-root", type=Path, required=True)
    parser.add_argument("--regression-failure-rca", type=Path, required=True)
    parser.add_argument(
        "--history-amendment",
        type=Path,
        default=(ROOT / "docs/status/evidence/s8-v4-s6bm-historical-partial-run-amendment-v3.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-review-handoff-v4.json",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise S6BMReviewError(f"noncanonical_json:{path.name}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise S6BMReviewError(f"json_object_required:{path.name}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")


def git(*arguments: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise S6BMReviewError(
            f"git_command:{' '.join(arguments)}:{result.stderr.decode(errors='replace')}"
        )
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", errors="replace").strip()


def blob_identity(revision: str, path: str) -> dict[str, Any]:
    payload = git("show", f"{revision}:{path}", binary=True)
    assert isinstance(payload, bytes)
    oid = git("rev-parse", f"{revision}:{path}")
    assert isinstance(oid, str)
    return {
        "path": path.removeprefix("enterprise-vision-mlops/"),
        "blob_oid": oid,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def pytest_counts(text: str, *, occurrence: int = -1) -> tuple[int, int]:
    matches = list(re.finditer(r"(\d+) passed(?:, (\d+) skipped)?", text))
    if not matches:
        raise S6BMReviewError("pytest_count_missing")
    match = matches[occurrence]
    return int(match.group(1)), int(match.group(2) or 0)


def validate_regressions(root: Path, revision: str) -> list[dict[str, Any]]:
    expected = {
        "changed_file_lint",
        "focused_s6bm",
        "focused_s8_closure",
        "real_postgresql",
        "lifecycle_host_e2e",
        "s0_s8_status_evidence",
        "full_python",
        "control_panel",
        "frontend_production_build",
    }
    observed: list[dict[str, Any]] = []
    for suite_id in sorted(expected):
        log_path = root / f"{suite_id}.log"
        meta_path = root / f"{suite_id}.meta.json"
        meta = read_json(meta_path)
        if meta.get("source_revision") != revision or int(meta.get("exit_code", -1)) != 0:
            raise S6BMReviewError(f"regression_identity_or_exit:{suite_id}")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        item: dict[str, Any] = {
            "suite_id": suite_id,
            "public_command": meta["public_command"],
            "exit_code": 0,
            "log_sha256": sha256_file(log_path),
            "meta_sha256": sha256_file(meta_path),
        }
        if suite_id == "changed_file_lint":
            if "All checks passed!" not in text:
                raise S6BMReviewError("lint_result")
        elif suite_id == "frontend_production_build":
            match = re.search(r"(\d+) modules transformed", text)
            if match is None:
                raise S6BMReviewError("frontend_module_count")
            item["modules_transformed"] = int(match.group(1))
        elif suite_id == "control_panel":
            passed, skipped = pytest_counts(text, occurrence=0)
            ui = re.search(r"Tests\s+(\d+) passed", text)
            files = re.search(r"Test Files\s+(\d+) passed", text)
            if ui is None or files is None or skipped != 0:
                raise S6BMReviewError("control_panel_counts")
            item.update(
                python_passed=passed,
                python_skipped=skipped,
                ui_passed=int(ui.group(1)),
                ui_files_passed=int(files.group(1)),
            )
        else:
            passed, skipped = pytest_counts(text)
            if skipped != 0:
                raise S6BMReviewError(f"required_regression_skipped:{suite_id}:{skipped}")
            item.update(tests_passed=passed, tests_skipped=skipped)
        observed.append(item)
    return observed


def validate_indexed_root(root: Path, *, allowed_extra: str) -> dict[str, Any]:
    index_path = root / "private-evidence-index.json"
    index = read_json(index_path)
    entries = list(index.get("entries", []))
    if (
        index.get("schema_version") != "evm.s8_v4.s6bm_private_index.v1"
        or int(index.get("artifact_count", -1)) != len(entries)
        or len({str(item.get("path", "")) for item in entries}) != len(entries)
    ):
        raise S6BMReviewError("private_index_contract")
    for item in entries:
        relative = Path(str(item.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise S6BMReviewError("private_index_path")
        path = root / relative
        if (
            not path.is_file()
            or type(item.get("bytes")) is not int
            or item["bytes"] != path.stat().st_size
            or item.get("sha256") != sha256_file(path)
        ):
            raise S6BMReviewError(f"private_index_entry:{relative.as_posix()}")
    expected_paths = {str(item["path"]) for item in entries}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.name != "private-evidence-index.json"
        and path.name != allowed_extra
    }
    if actual_paths != expected_paths:
        raise S6BMReviewError("private_index_file_set")
    aggregate = hashlib.sha256(canonical(entries).encode("ascii")).hexdigest()
    total_bytes = sum(int(item["bytes"]) for item in entries)
    if (
        index.get("aggregate_sha256") != aggregate
        or int(index.get("total_bytes", -1)) != total_bytes
    ):
        raise S6BMReviewError("private_index_projection")
    return {
        "artifact_count": len(entries),
        "total_bytes": total_bytes,
        "aggregate_sha256": aggregate,
        "index_sha256": sha256_file(index_path),
    }


def validate_qualifications(
    ordinary_root: Path,
    continuity_root: Path,
    runtime_revision: str,
) -> dict[str, Any]:
    causal_path = ordinary_root / "causal-qualification.json"
    strict_path = ordinary_root / "strict-v4-validation.json"
    causal = read_json(causal_path)
    strict = read_json(strict_path)
    causal_requests = dict(causal.get("requests", {}))
    if (
        causal.get("schema_version") != "evm.s8_v4.s6bm_causal_qualification.v1"
        or causal.get("source_revision") != runtime_revision
        or causal.get("credit") != "non_credit"
        or causal.get("acceptance_credit") is not False
        or causal_requests
        != {
            "logical": 1,
            "accepted": 1,
            "terminal": 1,
            "lost": 0,
            "duplicate_effect": 0,
            "http_5xx": 0,
            "transport_failure": 0,
            "wrong_version": 0,
        }
        or any(dict(causal.get("cleanup", {})).get(key) is not True for key in REQUIRED_CLEANUP)
    ):
        raise S6BMReviewError("ordinary_qualification")
    strict_negative = dict(strict.get("strict_v4_negative", {}))
    historical = dict(strict.get("historical_mutation_prefix", {}))
    if (
        strict.get("schema_version") != "evm.s8_v4.s6bm_strict_v4_qualification_mutations.v1"
        or strict.get("source_revision") != runtime_revision
        or strict.get("passed") is not True
        or strict.get("credit") != "non_credit"
        or strict.get("acceptance_credit") is not False
        or strict.get("accepted_matrix_started") is not False
        or strict.get("qualification_sha256") != sha256_file(causal_path)
        or dict(strict.get("positive", {})).get("passed") is not True
        or int(dict(strict.get("positive", {})).get("count", 0)) != 1
        or int(strict_negative.get("count", 0)) != 49
        or int(strict_negative.get("rejected", 0)) != 49
        or any(item.get("rejected") is not True for item in strict_negative.get("cases", []))
        or int(historical.get("case_count", 0)) != 28
        or historical.get("preserved") is not True
    ):
        raise S6BMReviewError("strict_qualification")

    continuity_path = continuity_root / "continuity-qualification.json"
    continuity_validation_path = continuity_root / "continuity-validation.json"
    continuity = read_json(continuity_path)
    continuity_validation = read_json(continuity_validation_path)
    requests = dict(continuity.get("requests", {}))
    if (
        continuity.get("schema_version") != "evm.s8_v4.s6bm_success_private.v1"
        or continuity.get("source_revision") != runtime_revision
        or continuity.get("credit") != "non_credit"
        or continuity.get("acceptance_credit") is not False
        or requests.get("logical") != 1000
        or requests.get("accepted") != 1000
        or requests.get("terminal") != 1000
        or any(
            requests.get(key) != 0
            for key in (
                "lost",
                "duplicate_effect",
                "http_5xx",
                "transport_failure",
                "wrong_version",
            )
        )
        or any(dict(continuity.get("cleanup", {})).get(key) is not True for key in REQUIRED_CLEANUP)
    ):
        raise S6BMReviewError("continuity_qualification")
    negative = dict(continuity_validation.get("negative", {}))
    if (
        continuity_validation.get("schema_version") != "evm.s8_v4.s6bm_continuity_mutations.v1"
        or continuity_validation.get("source_revision") != runtime_revision
        or continuity_validation.get("passed") is not True
        or continuity_validation.get("credit") != "non_credit"
        or continuity_validation.get("acceptance_credit") is not False
        or continuity_validation.get("accepted_matrix_started") is not False
        or continuity_validation.get("qualification_sha256") != sha256_file(continuity_path)
        or dict(continuity_validation.get("positive", {})).get("passed") is not True
        or int(dict(continuity_validation.get("positive", {})).get("count", 0)) != 1
        or int(negative.get("count", 0)) != 24
        or int(negative.get("rejected", 0)) != 24
        or any(item.get("rejected") is not True for item in negative.get("cases", []))
    ):
        raise S6BMReviewError("continuity_validation")
    return {
        "ordinary": {
            "attempt_id": causal["attempt_id"],
            "qualification_sha256": sha256_file(causal_path),
            "validation_sha256": sha256_file(strict_path),
            "strict_negative_rejected": 49,
            "historical_negative_rejected": 28,
            "private": validate_indexed_root(
                ordinary_root, allowed_extra="strict-v4-validation.json"
            ),
        },
        "continuity": {
            "attempt_id": continuity["attempt_id"],
            "qualification_sha256": sha256_file(continuity_path),
            "validation_sha256": sha256_file(continuity_validation_path),
            "logical_requests": 1000,
            "negative_rejected": 24,
            "private": validate_indexed_root(
                continuity_root, allowed_extra="continuity-validation.json"
            ),
        },
    }


def validate_current_runtime(
    experiment: dict[str, Any], validation_revision: str
) -> dict[str, Any]:
    analysis = dict(experiment.get("analysis", {}))
    attempts = list(analysis.get("success_attempts", []))
    if (
        analysis.get("evidence_ready") is not True
        or {int(item.get("repetition", 0)) for item in attempts} != {1, 2, 3}
        or len(attempts) != 3
        or any(item.get("passed") is not True for item in attempts)
        or any(int(item.get("logical_requests", 0)) != 1000 for item in attempts)
    ):
        raise S6BMReviewError("current_runtime_projection")
    cleanup = dict(experiment.get("cleanup", {}))
    if any(cleanup.get(key) is not True for key in REQUIRED_CLEANUP):
        raise S6BMReviewError("current_runtime_cleanup")
    smoke_revision = str(dict(experiment.get("source_identity", {})).get("revision", ""))
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", smoke_revision, validation_revision],
        cwd=ROOT,
        check=False,
    )
    if ancestry.returncode != 0:
        raise S6BMReviewError("current_runtime_revision_ancestry")
    return {
        "suite_id": experiment["suite_id"],
        "source_revision": smoke_revision,
        "successful_repetitions": 3,
        "logical_requests": 3000,
        "attempts": attempts,
        "cleanup_passed": True,
    }


def main() -> int:
    args = parse_args()
    revision = str(git("rev-parse", "HEAD"))
    tree_sha = str(git("rev-parse", "HEAD^{tree}"))
    experiment = read_json(args.experiment)
    strict = validate_experiment(args.experiment, args.config, args.private_root)
    strict_validation = read_json(args.strict_validation)
    if (
        strict_validation.get("schema_version") != "evm.s8_v4.s6bm_strict_v3_validation.v1"
        or strict_validation.get("status") != "review_pending"
        or strict_validation.get("credit") != "non_credit_reviewer_pending"
        or strict_validation.get("reviewer_sign_off") != "pending"
        or strict_validation.get("acceptance") != strict["acceptance"]
        or strict_validation.get("strict_raw_drain_timelines")
        != strict["strict_raw_drain_timelines"]
        or strict_validation.get("private_aggregate_sha256") != strict["private_aggregate_sha256"]
    ):
        raise S6BMReviewError("strict_validation_projection")
    mutation = read_json(args.mutation)
    expected_mutations = {
        "loss",
        "duplicate_request_identity",
        "wrong_model_digest",
        "trace_gap",
        "phase_order",
        "premature_drain",
        "rollback_mismatch",
        "illegal_owner_overlap",
        "cleanup_residue",
        "physical_model_residue",
        "wrong_digest_fail_open",
        "orphan",
        "readiness_route_switch",
        "canary_not_observed",
        "vram_not_over_capacity",
        "direct_metrics_absent",
        "prometheus_counts_zero",
        "duplicate_repetition_full_analysis",
        "repetition_out_of_contract",
        "offered_identity_substitution",
        "unbound_trace_id",
        "trace_artifact_absent",
        "metric_label_substitution",
        "attempt_mix",
        "hold_completion_before_switch",
        "unload_before_last_blue_completion",
        "unload_completed_before_last_blue_completion",
        "span_request_effect_timeline_mismatch",
    }
    mutation_cases = list(mutation.get("cases", []))
    if (
        mutation.get("passed") is not True
        or int(mutation.get("positive", 0)) != 1
        or int(mutation.get("negative", 0)) != len(expected_mutations)
        or int(mutation.get("negative_rejected", 0)) != len(expected_mutations)
        or {str(item.get("mutation", "")) for item in mutation_cases} != expected_mutations
        or any(item.get("rejected") is not True for item in mutation_cases)
    ):
        raise S6BMReviewError("mutation_result")
    runtime_revision = str(dict(experiment.get("source_identity", {})).get("revision", ""))
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", runtime_revision, revision],
        cwd=ROOT,
        check=False,
    ).returncode:
        raise S6BMReviewError("runtime_revision_ancestry")
    for path in EXECUTION_PATHS:
        if subprocess.run(
            ["git", "diff", "--quiet", runtime_revision, revision, "--", path],
            cwd=ROOT.parent,
            check=False,
        ).returncode:
            raise S6BMReviewError(f"runtime_blob_changed:{path}")
    regressions = validate_regressions(args.regression_root, revision)
    runtime_proof = validate_current_runtime(experiment, revision)
    qualifications = validate_qualifications(
        args.ordinary_qualification_root,
        args.continuity_qualification_root,
        runtime_revision,
    )
    regression_failure = read_json(args.regression_failure_rca)
    if (
        regression_failure.get("credit") != "zero_credit_regression_attempt"
        or regression_failure.get("acceptance_credit") is not False
        or regression_failure.get("status") != "remediation_required"
    ):
        raise S6BMReviewError("regression_failure_credit")
    history_amendment = read_json(args.history_amendment)
    if (
        history_amendment.get("credit") != "zero_credit"
        or history_amendment.get("acceptance_credit") is not False
        or history_amendment.get("independently_verifiable_executed_requests") != "unknown"
    ):
        raise S6BMReviewError("history_amendment")
    handoff = {
        "schema_version": "evm.s8_v4.s6bm_review_handoff.v4",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "review_pending",
        "evidence_ready": True,
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "source_identity": {
            "runtime_revision": runtime_revision,
            "validation_revision": revision,
            "validation_tree_sha": tree_sha,
            "runtime_git_blobs": {
                path: blob_identity(runtime_revision, path) for path in EXECUTION_PATHS
            },
            "validation_git_blobs": {
                path: blob_identity(revision, path) for path in VALIDATION_PATHS
            },
        },
        "alignment": {
            "definition_alignment": True,
            "experiment_purpose_alignment": True,
            "validation_purpose_alignment": True,
            "test_purpose_alignment": True,
        },
        "acceptance": strict["acceptance"],
        "matrix": {
            "baseline_repetitions": strict["baseline_repetitions"],
            "successful_transition_repetitions": 3,
            "successful_logical_requests": 3000,
            "wrong_digest_repetitions": 3,
            "supplementary_fault_repetitions": 12,
            "total_cells": 21,
        },
        "evidence": {
            "experiment_path": args.experiment.relative_to(ROOT).as_posix(),
            "experiment_sha256": strict["experiment_sha256"],
            "mutation_path": args.mutation.relative_to(ROOT).as_posix(),
            "mutation_sha256": sha256_file(args.mutation),
            "mutation_negative_rejected": len(expected_mutations),
            "strict_validation_path": args.strict_validation.relative_to(ROOT).as_posix(),
            "strict_validation_sha256": sha256_file(args.strict_validation),
            "strict_raw_drain_timelines": strict["strict_raw_drain_timelines"],
            "private_artifacts": strict["private_artifacts"],
            "private_aggregate_sha256": strict["private_aggregate_sha256"],
            "qualification_gates": qualifications,
            "history_amendment_path": args.history_amendment.relative_to(ROOT).as_posix(),
            "history_amendment_sha256": sha256_file(args.history_amendment),
            "regression_failure_rca_path": args.regression_failure_rca.relative_to(ROOT).as_posix(),
            "regression_failure_rca_sha256": sha256_file(args.regression_failure_rca),
        },
        "current_revision_runtime_proof": runtime_proof,
        "regressions": regressions,
        "failed_attempts": {
            "runtime_failures": len(experiment.get("failed_attempts", [])),
            "regression_diagnostics": 1,
            "all_excluded_from_credit": True,
            "regression_failure_sha256": sha256_file(args.regression_failure_rca),
            "historical_181425_executed_requests": "unknown_independently_unverifiable",
        },
        "cleanup": experiment["cleanup"],
        "claim_boundary": experiment["claim_boundary"],
        "unresolved_items": [
            "Independent source-local reviewer sign-off remains pending.",
            "Historical S2 Infinity validator debt remains a separate V4 closure backlog.",
        ],
        "next_action": "Independent source-local review; do not start X1 or Integrated V4.",
    }
    if not all(handoff["acceptance"].values()):
        raise S6BMReviewError("acceptance_not_all_passed")
    write_json(args.output, handoff)
    print(
        canonical(
            {
                "output": str(args.output),
                "sha256": sha256_file(args.output),
                "status": handoff["status"],
                "acceptance": handoff["acceptance"],
                "regression_suites": len(regressions),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

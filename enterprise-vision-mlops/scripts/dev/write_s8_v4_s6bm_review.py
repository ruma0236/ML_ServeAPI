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
RUNTIME_PATHS = (
    "enterprise-vision-mlops/src/evm/model_runtime/triton_blue_green.py",
    "enterprise-vision-mlops/src/evm/scale_validation/s6bm_runtime.py",
    "enterprise-vision-mlops/scripts/dev/run_s8_v4_s6bm_experiment.py",
    "enterprise-vision-mlops/configs/s8_v4_s6bm_blue_green_v1.toml",
)


class S6BMReviewError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write S6B-M source-local review handoff.")
    parser.add_argument(
        "--experiment",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-experiment.json",
    )
    parser.add_argument(
        "--mutation",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-mutation-validation.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml",
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--regression-root", type=Path, required=True)
    parser.add_argument("--validator-failure", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-review-handoff.json",
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


def validate_smoke(path: Path, validation_revision: str) -> dict[str, Any]:
    smoke = read_json(path)
    if (
        smoke.get("passed") is not True
        or smoke.get("credit") != "non_credit"
        or smoke.get("acceptance_credit") is not False
        or smoke.get("diagnostic_only") is not True
        or smoke.get("failure") is not None
        or smoke.get("cleanup_errors") != []
    ):
        raise S6BMReviewError("smoke_state")
    projection = dict(smoke.get("projection", {}))
    if projection.get("passed") is not True or int(projection.get("logical_requests", 0)) != 1000:
        raise S6BMReviewError("smoke_projection")
    cleanup = dict(smoke.get("cleanup", {}))
    if any(cleanup.get(key) is not True for key in REQUIRED_CLEANUP):
        raise S6BMReviewError("smoke_cleanup")
    smoke_revision = str(dict(smoke.get("source_identity", {})).get("revision", ""))
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", smoke_revision, validation_revision],
        cwd=ROOT,
        check=False,
    )
    if ancestry.returncode != 0:
        raise S6BMReviewError("smoke_revision_ancestry")
    return {
        "smoke_id": smoke["smoke_id"],
        "source_revision": smoke_revision,
        "sha256": sha256_file(path),
        "logical_requests": projection["logical_requests"],
        "p95_ms": projection["p95_ms"],
        "p99_ms": projection["p99_ms"],
        "max_inter_completion_gap_ms": projection["max_inter_completion_gap_ms"],
        "transition_seconds": projection["transition_seconds"],
        "rollback_seconds": projection["rollback_seconds"],
        "cleanup_passed": True,
    }


def main() -> int:
    args = parse_args()
    revision = str(git("rev-parse", "HEAD"))
    tree_sha = str(git("rev-parse", "HEAD^{tree}"))
    experiment = read_json(args.experiment)
    strict = validate_experiment(args.experiment, args.config, args.private_root)
    mutation = read_json(args.mutation)
    if (
        mutation.get("passed") is not True
        or int(mutation.get("positive", 0)) != 1
        or int(mutation.get("negative", 0)) != 15
        or int(mutation.get("negative_rejected", 0)) != 15
        or any(item.get("rejected") is not True for item in mutation.get("cases", []))
    ):
        raise S6BMReviewError("mutation_result")
    runtime_revision = str(dict(experiment.get("source_identity", {})).get("revision", ""))
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", runtime_revision, revision],
        cwd=ROOT,
        check=False,
    ).returncode:
        raise S6BMReviewError("runtime_revision_ancestry")
    for path in RUNTIME_PATHS:
        if subprocess.run(
            ["git", "diff", "--quiet", runtime_revision, revision, "--", path],
            cwd=ROOT.parent,
            check=False,
        ).returncode:
            raise S6BMReviewError(f"runtime_blob_changed:{path}")
    regressions = validate_regressions(args.regression_root, revision)
    smoke = validate_smoke(args.smoke, revision)
    failed_validator = read_json(args.validator_failure)
    if (
        failed_validator.get("credit") != "non_credit"
        or failed_validator.get("acceptance_credit") is not False
    ):
        raise S6BMReviewError("validator_failure_credit")
    git_paths = [*RUNTIME_PATHS, "enterprise-vision-mlops/scripts/dev/validate_s8_v4_s6bm.py"]
    handoff = {
        "schema_version": "evm.s8_v4.s6bm_review_handoff.v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "review_pending",
        "evidence_ready": True,
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "source_identity": {
            "runtime_revision": runtime_revision,
            "validation_revision": revision,
            "validation_tree_sha": tree_sha,
            "git_blobs": {path: blob_identity(revision, path) for path in git_paths},
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
        },
        "evidence": {
            "experiment_path": args.experiment.relative_to(ROOT).as_posix(),
            "experiment_sha256": strict["experiment_sha256"],
            "mutation_path": args.mutation.relative_to(ROOT).as_posix(),
            "mutation_sha256": sha256_file(args.mutation),
            "private_artifacts": strict["private_artifacts"],
            "private_aggregate_sha256": strict["private_aggregate_sha256"],
        },
        "current_revision_smoke": smoke,
        "regressions": regressions,
        "failed_attempts": {
            "runtime_failures": len(experiment.get("failed_attempts", [])),
            "validator_diagnostics": 1,
            "all_excluded_from_credit": True,
            "validator_failure_sha256": sha256_file(args.validator_failure),
        },
        "cleanup": experiment["cleanup"],
        "claim_boundary": experiment["claim_boundary"],
        "unresolved_items": [
            "Independent source-local reviewer sign-off remains pending.",
            "Historical S2 Infinity validator debt remains a separate V4 closure backlog.",
        ],
        "next_action": "Independent source-local review; do not start X1.",
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

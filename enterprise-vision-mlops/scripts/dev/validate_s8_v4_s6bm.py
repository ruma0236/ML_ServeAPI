from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.s6bm_runtime import (  # noqa: E402
    CLAIM_BOUNDARY,
    S6BMConfig,
    S6BMRuntimeError,
    analyze_attempts,
    canonical,
    canonical_sha256,
    project_fault_attempt,
    project_success_attempt,
    sha256_file,
)


class S6BMValidationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S8-V4 S6B-M evidence")
    parser.add_argument(
        "--experiment",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-experiment.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml",
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--mutation-output", type=Path)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise S6BMValidationError(f"noncanonical_json:{path.name}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise S6BMValidationError(f"json_object_required:{path.name}")
    return payload


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")


def git_bytes(revision: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise S6BMValidationError(f"git_blob_missing:{revision}:{relative}")
    return result.stdout


def git_text(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise S6BMValidationError(f"git_command:{' '.join(arguments)}:{result.stderr}")
    return result.stdout.strip()


def private_index(root: Path) -> dict[str, Any]:
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "private-evidence-index.json":
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": "evm.s8_v4.s6bm_private_index.v1",
        "artifact_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "aggregate_sha256": hashlib.sha256(canonical(entries).encode("ascii")).hexdigest(),
        "entries": entries,
    }


def load_attempts(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baselines = [read_json(path) for path in sorted((root / "baseline").glob("*.json"))]
    accepted = [
        read_json(path)
        for path in sorted((root / "successful-transition").glob("*.json"))
    ]
    accepted.extend(read_json(path) for path in sorted((root / "faults").rglob("*.json")))
    return baselines, accepted


def validate_baselines(
    baselines: list[Mapping[str, Any]], config: S6BMConfig, source_revision: str
) -> None:
    expected = int(config.procedure["baseline_repetitions"])
    if len(baselines) != expected:
        raise S6BMValidationError(f"baseline_repetitions:{len(baselines)}")
    for repetition, baseline in enumerate(baselines, start=1):
        if (
            baseline.get("credit") != "non_credit"
            or int(baseline.get("repetition", 0)) != repetition
            or baseline.get("source_revision") != source_revision
        ):
            raise S6BMValidationError(f"baseline_identity:{repetition}")
        records = list(baseline.get("request_records", []))
        if len(records) != int(config.procedure["baseline_requests"]):
            raise S6BMValidationError(f"baseline_requests:{repetition}")
        for item in records:
            if (
                item.get("outcome") != "completed"
                or int(item.get("status_code", 0)) != 200
                or item.get("model_role") != "blue"
                or item.get("artifact_sha256") != config.blue.artifact_sha256
                or list(item.get("output", [])) != list(config.blue.expected_output)
            ):
                raise S6BMValidationError(f"baseline_record:{repetition}")


def validate_cleanup(cleanup: Mapping[str, Any]) -> None:
    required = {
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
    }
    if any(cleanup.get(key) is not True for key in required):
        raise S6BMValidationError("final_cleanup")


def validate(
    experiment_path: Path, config_path: Path, private_root: Path
) -> dict[str, Any]:
    experiment = read_json(experiment_path)
    config = S6BMConfig.from_path(config_path)
    if (
        experiment.get("schema_version") != "evm.s8_v4.s6bm_experiment.v1"
        or experiment.get("status") != "evidence_ready"
        or experiment.get("credit") != "non_credit_reviewer_pending"
        or experiment.get("reviewer_sign_off") != "pending"
        or experiment.get("claim_boundary") != CLAIM_BOUNDARY
    ):
        raise S6BMValidationError("experiment_review_state")
    source = dict(experiment.get("source_identity", {}))
    revision = str(source.get("revision", ""))
    if len(revision) != 40 or git_text("cat-file", "-t", revision) != "commit":
        raise S6BMValidationError("source_revision")
    if git_text("rev-parse", f"{revision}^{{tree}}") != source.get("tree_sha"):
        raise S6BMValidationError("source_tree")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"], cwd=ROOT
    ).returncode != 0:
        raise S6BMValidationError("source_ancestry")
    config_relative = config_path.resolve().relative_to(ROOT.resolve()).as_posix()
    config_git_sha = hashlib.sha256(git_bytes(revision, config_relative)).hexdigest()
    contract = dict(experiment.get("contract", {}))
    if contract.get("config_sha256") != config_git_sha:
        raise S6BMValidationError("config_git_blob_sha")
    if contract.get("snapshot_sha256") != canonical_sha256(config.public_snapshot()):
        raise S6BMValidationError("config_snapshot_sha")

    observed_index = private_index(private_root)
    recorded_index = read_json(private_root / "private-evidence-index.json")
    if observed_index != recorded_index:
        raise S6BMValidationError("private_index_projection")
    public_private = dict(experiment.get("private_evidence", {}))
    expected_public = {
        "artifact_count": observed_index["artifact_count"],
        "total_bytes": observed_index["total_bytes"],
        "aggregate_sha256": observed_index["aggregate_sha256"],
        "index_sha256": sha256_file(private_root / "private-evidence-index.json"),
    }
    if any(public_private.get(key) != value for key, value in expected_public.items()):
        raise S6BMValidationError("private_public_projection")

    baselines, attempts = load_attempts(private_root)
    validate_baselines(baselines, config, revision)
    if any(item.get("source_revision") != revision for item in attempts):
        raise S6BMValidationError("attempt_source_revision")
    analysis = analyze_attempts(attempts, config)
    if analysis != experiment.get("analysis") or not analysis["evidence_ready"]:
        raise S6BMValidationError("analysis_projection")
    matrix = dict(experiment.get("matrix", {}))
    if int(matrix.get("baseline_repetitions", 0)) != len(baselines):
        raise S6BMValidationError("matrix_baseline")
    if int(matrix.get("successful_transition_repetitions", 0)) != 3:
        raise S6BMValidationError("matrix_success")
    expected_faults = {
        profile: 3
        for profile in (
            "wrong_digest",
            "green_load_failure",
            "green_readiness_failure",
            "green_canary_failure",
            "vram_preflight_rejection",
        )
    }
    if dict(matrix.get("fault_repetitions", {})) != expected_faults:
        raise S6BMValidationError("matrix_faults")
    failures = list(experiment.get("failed_attempts", []))
    if any(
        item.get("credit") != "zero_credit"
        or int(item.get("acceptance_credit_requests", -1)) != 0
        or len(str(item.get("evidence_sha256", ""))) != 64
        for item in failures
    ):
        raise S6BMValidationError("historical_failure_credit_boundary")
    validate_cleanup(dict(experiment.get("cleanup", {})))
    return {
        "valid": True,
        "source_revision": revision,
        "acceptance": analysis["acceptance"],
        "supplementary_guards_passed": analysis["supplementary_guards_passed"],
        "baseline_repetitions": len(baselines),
        "accepted_attempts": len(attempts),
        "private_artifacts": observed_index["artifact_count"],
        "private_aggregate_sha256": observed_index["aggregate_sha256"],
        "experiment_sha256": sha256_file(experiment_path),
    }


def mutation_result(
    name: str,
    attempt: Mapping[str, Any],
    mutate: Callable[[dict[str, Any]], None],
    config: S6BMConfig,
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(attempt))
    mutate(candidate)
    try:
        if candidate.get("profile") == "successful_transition":
            project_success_attempt(candidate, config)
        else:
            project_fault_attempt(candidate, config, str(candidate["profile"]))
    except (S6BMRuntimeError, KeyError, TypeError, ValueError) as exc:
        return {"mutation": name, "rejected": True, "reason": str(exc)}
    return {"mutation": name, "rejected": False, "reason": "validator_fail_open"}


def run_mutations(private_root: Path, config: S6BMConfig) -> dict[str, Any]:
    _baselines, attempts = load_attempts(private_root)
    success = next(item for item in attempts if item["profile"] == "successful_transition")
    wrong = next(item for item in attempts if item["profile"] == "wrong_digest")
    canary = next(item for item in attempts if item["profile"] == "green_canary_failure")
    vram = next(item for item in attempts if item["profile"] == "vram_preflight_rejection")
    cases = [
        mutation_result(
            "loss",
            success,
            lambda item: item["request_records"].pop(),
            config,
        ),
        mutation_result(
            "duplicate_request_identity",
            success,
            lambda item: item["request_records"][1].update(
                request_id=item["request_records"][0]["request_id"]
            ),
            config,
        ),
        mutation_result(
            "wrong_model_digest",
            success,
            lambda item: item["request_records"][0].update(artifact_sha256="f" * 64),
            config,
        ),
        mutation_result(
            "trace_gap",
            success,
            lambda item: item["request_records"][0].update(trace_id="0"),
            config,
        ),
        mutation_result(
            "phase_order",
            success,
            lambda item: item["phase_timeline"].reverse(),
            config,
        ),
        mutation_result(
            "premature_drain",
            success,
            lambda item: item.update(blue_in_flight_before_unload=1),
            config,
        ),
        mutation_result(
            "rollback_mismatch",
            success,
            lambda item: item.update(rollback_exact_blue=False),
            config,
        ),
        mutation_result(
            "illegal_owner_overlap",
            success,
            lambda item: item.update(illegal_owner_overlap=1),
            config,
        ),
        mutation_result(
            "cleanup_residue",
            success,
            lambda item: item["cleanup"].update(green_unloaded=False),
            config,
        ),
        mutation_result(
            "physical_model_residue",
            success,
            lambda item: item["physical_model_state"].update(
                green_unloaded_not_ready=False
            ),
            config,
        ),
        mutation_result(
            "wrong_digest_fail_open",
            wrong,
            lambda item: item["rejection"].update(status_code=200),
            config,
        ),
        mutation_result(
            "orphan",
            wrong,
            lambda item: item.update(orphan_count=1),
            config,
        ),
        mutation_result(
            "readiness_route_switch",
            wrong,
            lambda item: item.update(route_switch_count=1),
            config,
        ),
        mutation_result(
            "canary_not_observed",
            canary,
            lambda item: item["fault_observation"].update(canary_mismatch=False),
            config,
        ),
        mutation_result(
            "vram_not_over_capacity",
            vram,
            lambda item: item["fault_observation"].update(required_vram_mib=1.0),
            config,
        ),
    ]
    return {
        "schema_version": "evm.s8_v4.s6bm_mutation_validation.v1",
        "positive": 1,
        "negative": len(cases),
        "negative_rejected": sum(item["rejected"] is True for item in cases),
        "passed": all(item["rejected"] is True for item in cases),
        "cases": cases,
    }


def main() -> int:
    args = parse_args()
    result = validate(args.experiment, args.config, args.private_root)
    if args.mutation_output is not None:
        mutations = run_mutations(args.private_root, S6BMConfig.from_path(args.config))
        canonical_write(args.mutation_output, mutations)
        if not mutations["passed"]:
            raise S6BMValidationError("mutation_validation_fail_open")
        result["mutation_validation"] = {
            "positive": mutations["positive"],
            "negative": mutations["negative"],
            "negative_rejected": mutations["negative_rejected"],
            "sha256": sha256_file(args.mutation_output),
        }
    print(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

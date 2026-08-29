from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
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
from evm.scale_validation.s6bm_observability import (  # noqa: E402
    S6BMObservabilityError,
    validate_observability_bundle,
)


class S6BMValidationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate S8-V4 S6B-M evidence")
    parser.add_argument(
        "--experiment",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-experiment-v2.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml",
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--mutation-output", type=Path)
    parser.add_argument("--validation-output", type=Path)
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


def git_bytes(revision: str, path: Path) -> bytes:
    git_root = Path(git_text("rev-parse", "--show-toplevel")).resolve()
    relative = path.resolve().relative_to(git_root).as_posix()
    result = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=git_root,
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
    accepted = [read_json(path) for path in sorted((root / "successful-transition").glob("*.json"))]
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


def validate(experiment_path: Path, config_path: Path, private_root: Path) -> dict[str, Any]:
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
    if (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "HEAD"], cwd=ROOT
        ).returncode
        != 0
    ):
        raise S6BMValidationError("source_ancestry")
    config_git_sha = hashlib.sha256(git_bytes(revision, config_path)).hexdigest()
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
    success_attempts = [item for item in attempts if item.get("profile") == "successful_transition"]
    drain_timelines: list[dict[str, Any]] = []
    for attempt in success_attempts:
        try:
            observability = validate_observability_bundle(
                private_root, attempt, config, require_drain_timeline=True
            )
        except S6BMObservabilityError as exc:
            raise S6BMValidationError(f"observability:{exc}") from exc
        if (
            observability["accepted_requests"]
            != int(config.procedure["logical_requests_per_transition"])
            or observability["trace_correlation_complete"] is not True
            or observability["metric_delta_complete"] is not True
        ):
            raise S6BMValidationError("observability_acceptance_projection")
        drain_timelines.append(
            {
                "attempt_id": str(attempt["attempt_id"]),
                "repetition": int(attempt["repetition"]),
                **dict(observability["raw_drain_timeline"]),
            }
        )
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
        "strict_raw_drain_timelines": drain_timelines,
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


def analysis_mutation_result(
    name: str,
    attempts: list[Mapping[str, Any]],
    mutate: Callable[[list[dict[str, Any]]], None],
    config: S6BMConfig,
) -> dict[str, Any]:
    candidate = copy.deepcopy([dict(item) for item in attempts])
    mutate(candidate)
    try:
        analyze_attempts(candidate, config)
    except (S6BMRuntimeError, KeyError, TypeError, ValueError) as exc:
        return {"mutation": name, "rejected": True, "reason": str(exc)}
    return {"mutation": name, "rejected": False, "reason": "validator_fail_open"}


def _copy_observability_artifacts(
    source_root: Path, target_root: Path, attempt: Mapping[str, Any]
) -> None:
    artifacts = dict(dict(attempt.get("observability", {})).get("artifacts", {}))
    for reference in artifacts.values():
        relative = Path(str(dict(reference).get("path", "")))
        source = source_root / relative
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _rewrite_json_artifact(
    root: Path,
    attempt: dict[str, Any],
    key: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    reference = dict(attempt["observability"]["artifacts"][key])
    path = root / str(reference["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    canonical_write(path, payload)
    attempt["observability"]["artifacts"][key].update(
        sha256=sha256_file(path), bytes=path.stat().st_size
    )


def observability_mutation_result(
    name: str,
    attempt: Mapping[str, Any],
    private_root: Path,
    config: S6BMConfig,
    mutate: Callable[[Path, dict[str, Any]], None],
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(attempt))
    with tempfile.TemporaryDirectory(prefix="s6bm-observability-mutation-") as raw_root:
        root = Path(raw_root)
        _copy_observability_artifacts(private_root, root, candidate)
        mutate(root, candidate)
        try:
            validate_observability_bundle(root, candidate, config, require_drain_timeline=True)
        except (S6BMObservabilityError, KeyError, TypeError, ValueError) as exc:
            return {"mutation": name, "rejected": True, "reason": str(exc)}
    return {"mutation": name, "rejected": False, "reason": "validator_fail_open"}


def _remove_direct_metrics(root: Path, attempt: dict[str, Any]) -> None:
    reference = dict(attempt["observability"]["artifacts"]["api_metrics_after"])
    (root / str(reference["path"])).unlink()


def _zero_prometheus_count(root: Path, attempt: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        result = payload["queries"]["api_blue_completed"]["response"]["data"]["result"]
        result[0]["value"][1] = "0"

    _rewrite_json_artifact(root, attempt, "prometheus_after", mutate)


def _substitute_trace_attribute(root: Path, attempt: dict[str, Any], key: str, value: str) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        for entry in payload["entries"]:
            attributes = entry["span"].get("attributes", [])
            for attribute in attributes:
                if attribute.get("key") == key:
                    attribute["value"] = {"stringValue": value}
                    return
        raise S6BMValidationError(f"trace_attribute_not_found:{key}")

    _rewrite_json_artifact(root, attempt, "trace_export", mutate)


def _remove_trace_artifact(root: Path, attempt: dict[str, Any]) -> None:
    reference = dict(attempt["observability"]["artifacts"]["trace_export"])
    (root / str(reference["path"])).unlink()


def _substitute_metric_label(root: Path, attempt: dict[str, Any]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        result = payload["queries"]["api_blue_completed"]["response"]["data"]["result"]
        result[0]["metric"]["model_name"] = "substituted-model"

    _rewrite_json_artifact(root, attempt, "prometheus_after", mutate)


def _latency_projection(attempt: dict[str, Any]) -> None:
    records = list(attempt["request_records"])
    latencies = sorted(float(item["elapsed_ms"]) for item in records)

    def percentile(value: float) -> float:
        position = (len(latencies) - 1) * value
        lower = int(position)
        upper = min(lower + 1, len(latencies) - 1)
        return latencies[lower] + (latencies[upper] - latencies[lower]) * (position - lower)

    completions = sorted(float(item["completed_monotonic"]) for item in records)
    gaps = [
        (current - previous) * 1000.0
        for previous, current in zip(completions, completions[1:], strict=False)
    ]
    attempt["latency"] = {
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_inter_completion_gap_ms": max(gaps, default=0.0),
    }


def _hold_record(attempt: Mapping[str, Any]) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in attempt.get("request_records", [])
        if "-hold-blue-" in str(item.get("request_id", ""))
    ]
    if len(matches) != 1:
        raise S6BMValidationError(f"hold_record_cardinality:{len(matches)}")
    return matches[0]


def _rewrite_trace_times(
    root: Path, attempt: dict[str, Any], trace_id: str, delta_nanoseconds: int
) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        changed = 0
        for entry in payload["entries"]:
            span = entry["span"]
            if span.get("traceId") != trace_id:
                continue
            span["startTimeUnixNano"] = str(int(span["startTimeUnixNano"]) + delta_nanoseconds)
            span["endTimeUnixNano"] = str(int(span["endTimeUnixNano"]) + delta_nanoseconds)
            changed += 1
        if changed != 7:
            raise S6BMValidationError(f"hold_trace_cardinality:{changed}")

    _rewrite_json_artifact(root, attempt, "trace_export", mutate)


def _move_hold_completion_before_switch(root: Path, attempt: dict[str, Any]) -> None:
    hold = _hold_record(attempt)
    switch = next(
        float(item["monotonic_seconds"])
        for item in attempt["phase_timeline"]
        if item["phase"] == "green_active"
    )
    old_completed = float(hold["completed_monotonic"])
    duration = old_completed - float(hold["attempted_monotonic"])
    new_completed = switch - 0.001
    for record in attempt["request_records"]:
        if record["request_id"] == hold["request_id"]:
            record["completed_monotonic"] = new_completed
            record["attempted_monotonic"] = new_completed - duration
            record["elapsed_ms"] = duration * 1000.0
            break
    _rewrite_trace_times(
        root,
        attempt,
        str(hold["trace_id"]),
        int((new_completed - old_completed) * 1_000_000_000),
    )
    _latency_projection(attempt)


def _move_unload_before_last_blue_completion(attempt: dict[str, Any]) -> None:
    hold = _hold_record(attempt)
    new_boundary = float(hold["completed_monotonic"]) - 0.001
    for phase in attempt["phase_timeline"]:
        if phase["phase"] == "green_only":
            phase["monotonic_seconds"] = new_boundary
            return
    raise S6BMValidationError("green_only_phase_absent")


def _move_pre_unload_gate_before_hold_completion(root: Path, attempt: dict[str, Any]) -> None:
    hold = _hold_record(attempt)
    reference = dict(attempt["observability"]["artifacts"]["prometheus_before_blue_unload"])
    path = root / str(reference["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    trace_reference = dict(attempt["observability"]["artifacts"]["trace_export"])
    trace_payload = json.loads((root / str(trace_reference["path"])).read_text(encoding="utf-8"))
    server = next(
        entry["span"]
        for entry in trace_payload["entries"]
        if entry["span"].get("traceId") == hold["trace_id"]
        and entry["span"].get("name")
        == "POST /control-panel/v1/scenario-workloads/triton-blue-green/predict"
    )
    gate_nanoseconds = int(server["endTimeUnixNano"]) - 1_000_000
    payload["captured_at"] = (
        datetime.fromtimestamp(gate_nanoseconds / 1_000_000_000, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    canonical_write(path, payload)
    attempt["observability"]["artifacts"]["prometheus_before_blue_unload"].update(
        sha256=sha256_file(path), bytes=path.stat().st_size
    )


def _move_hold_spans_after_pre_unload(root: Path, attempt: dict[str, Any]) -> None:
    hold = _hold_record(attempt)
    _rewrite_trace_times(root, attempt, str(hold["trace_id"]), 60_000_000_000)


def run_mutations(private_root: Path, config: S6BMConfig) -> dict[str, Any]:
    _baselines, attempts = load_attempts(private_root)
    success = next(item for item in attempts if item["profile"] == "successful_transition")
    wrong = next(item for item in attempts if item["profile"] == "wrong_digest")
    canary = next(item for item in attempts if item["profile"] == "green_canary_failure")
    vram = next(item for item in attempts if item["profile"] == "vram_preflight_rejection")
    validate_observability_bundle(private_root, success, config, require_drain_timeline=True)
    analyze_attempts(attempts, config)
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
            lambda item: item["physical_model_state"].update(green_unloaded_not_ready=False),
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
        observability_mutation_result(
            "direct_metrics_absent",
            success,
            private_root,
            config,
            _remove_direct_metrics,
        ),
        observability_mutation_result(
            "prometheus_counts_zero",
            success,
            private_root,
            config,
            _zero_prometheus_count,
        ),
        analysis_mutation_result(
            "duplicate_repetition_full_analysis",
            attempts,
            lambda items: items[1].update(repetition=items[0]["repetition"]),
            config,
        ),
        analysis_mutation_result(
            "repetition_out_of_contract",
            attempts,
            lambda items: items[0].update(repetition=4),
            config,
        ),
        mutation_result(
            "offered_identity_substitution",
            success,
            lambda item: item["request_records"][0]["offered_identity"].update(
                model_name="substituted-model"
            ),
            config,
        ),
        observability_mutation_result(
            "unbound_trace_id",
            success,
            private_root,
            config,
            lambda root, item: _substitute_trace_attribute(
                root, item, "evm.request.id", "unbound-request-id"
            ),
        ),
        observability_mutation_result(
            "trace_artifact_absent",
            success,
            private_root,
            config,
            _remove_trace_artifact,
        ),
        observability_mutation_result(
            "metric_label_substitution",
            success,
            private_root,
            config,
            _substitute_metric_label,
        ),
        observability_mutation_result(
            "attempt_mix",
            success,
            private_root,
            config,
            lambda root, item: _substitute_trace_attribute(
                root, item, "evm.attempt.id", "s6bm-mixed-attempt"
            ),
        ),
        observability_mutation_result(
            "hold_completion_before_switch",
            success,
            private_root,
            config,
            _move_hold_completion_before_switch,
        ),
        observability_mutation_result(
            "unload_before_last_blue_completion",
            success,
            private_root,
            config,
            _move_pre_unload_gate_before_hold_completion,
        ),
        mutation_result(
            "unload_completed_before_last_blue_completion",
            success,
            _move_unload_before_last_blue_completion,
            config,
        ),
        observability_mutation_result(
            "span_request_effect_timeline_mismatch",
            success,
            private_root,
            config,
            _move_hold_spans_after_pre_unload,
        ),
    ]
    return {
        "schema_version": "evm.s8_v4.s6bm_mutation_validation.v2",
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
    if args.validation_output is not None:
        validation = {
            "schema_version": "evm.s8_v4.s6bm_strict_v3_validation.v1",
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "review_pending",
            "credit": "non_credit_reviewer_pending",
            "reviewer_sign_off": "pending",
            **result,
        }
        canonical_write(args.validation_output, validation)
        result["validation_output"] = {
            "path": str(args.validation_output),
            "sha256": sha256_file(args.validation_output),
        }
    print(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evm.scale_validation.x1_runtime import (
    ACCEPTED_PHASE_COUNTS,
    EXPECTED_MODELS,
    NON_CREDIT_PHASES,
    NON_CREDIT_SUCCESS_COUNTS,
    X1RuntimeConfig,
    X1RuntimeError,
    resolve_verified_s6bm_dependency,
)

EVIDENCE_SCHEMA_VERSION = "evm.s8_v4.x1_experiment.v2"


class X1EvidenceValidationError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_run_identity(
    run: Mapping[str, Any], *, config: X1RuntimeConfig, errors: list[str]
) -> None:
    phase = str(run.get("phase") or "")
    if phase not in set(ACCEPTED_PHASE_COUNTS) | set(NON_CREDIT_PHASES):
        errors.append(f"run_phase:{phase}")
    if not str(run.get("attempt_id") or ""):
        errors.append("run_attempt_id")
    if not str(run.get("point_id") or ""):
        errors.append("run_point_id")
    if not isinstance(run.get("run_index"), int) or int(run.get("run_index", 0)) < 1:
        errors.append("run_index")
    model_ids = tuple(run.get("model_ids", []))
    if phase in {"P0", "Q0"}:
        if (
            not model_ids
            or len(model_ids) != len(set(model_ids))
            or any(model_id not in EXPECTED_MODELS for model_id in model_ids)
        ):
            errors.append("run_model_set")
    elif model_ids != EXPECTED_MODELS:
        errors.append("run_model_set")
    allowed_credit = (
        {"credit", "zero_credit"}
        if phase in ACCEPTED_PHASE_COUNTS
        else {"non_credit", "zero_credit"}
    )
    if run.get("credit") not in allowed_credit:
        errors.append(f"run_credit:{phase}")
    if run.get("acceptance_credit") is not False:
        errors.append(f"run_acceptance_credit:{phase}")
    if phase == "Q0" and run.get("credit") == "non_credit":
        if len(model_ids) != 1:
            errors.append("q0_single_model_identity")
        if run.get("cuda_activity_observed") is not True:
            errors.append("q0_cuda_activity")
        if run.get("cpu_fallback_observed") is not False:
            errors.append("q0_cpu_fallback")
    if phase == "P0" and run.get("credit") == "non_credit" and len(model_ids) != 1:
        errors.append("p0_single_model_identity")
    if phase == "P5" and run.get("point_id") not in config.p5_point_ids:
        errors.append("p5_point_id")


def _validate_partial_accepted_runs(
    runs: list[Mapping[str, Any]], *, config: X1RuntimeConfig, errors: list[str]
) -> None:
    accepted = [item for item in runs if item.get("credit") == "credit"]
    counts = Counter(str(item.get("phase") or "") for item in accepted)
    if any(counts[phase] > total for phase, total in ACCEPTED_PHASE_COUNTS.items()):
        errors.append("accepted_phase_overflow")
    identities = [
        (str(item.get("phase")), str(item.get("point_id")), int(item.get("run_index", 0)))
        for item in accepted
    ]
    if len(identities) != len(set(identities)):
        errors.append("accepted_run_identity_duplicate")
    for item in accepted:
        phase = str(item.get("phase") or "")
        run_index = int(item.get("run_index", 0))
        if run_index not in {1, 2, 3}:
            errors.append(f"{phase.lower()}_run_index")
    for phase in ("P2", "P3", "P4"):
        points = {str(item.get("point_id")) for item in accepted if item.get("phase") == phase}
        if len(points) > 1:
            errors.append(f"{phase.lower()}_point_set")
    if any(
        item.get("phase") == "P5" and item.get("point_id") not in config.p5_point_ids
        for item in accepted
    ):
        errors.append("p5_exact_point_set")


def _validate_exact_accepted_runs(
    runs: list[Mapping[str, Any]], *, config: X1RuntimeConfig, errors: list[str]
) -> None:
    accepted = [item for item in runs if item.get("credit") == "credit"]
    _validate_partial_accepted_runs(runs, config=config, errors=errors)
    counts = Counter(str(item.get("phase") or "") for item in accepted)
    if dict(counts) != ACCEPTED_PHASE_COUNTS:
        errors.append("accepted_phase_arithmetic")
    if len(accepted) != config.accepted_total_runs:
        errors.append("accepted_total_arithmetic")
    identities = [
        (str(item.get("phase")), str(item.get("point_id")), int(item.get("run_index", 0)))
        for item in accepted
    ]
    if len(identities) != len(set(identities)):
        errors.append("accepted_run_identity_duplicate")

    by_phase_point: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for item in accepted:
        by_phase_point[str(item.get("phase"))][str(item.get("point_id"))].add(
            int(item.get("run_index", 0))
        )
    for phase in ("P2", "P3", "P4"):
        points = by_phase_point.get(phase, {})
        if len(points) != 1 or next(iter(points.values()), set()) != {1, 2, 3}:
            errors.append(f"{phase.lower()}_exact_repetitions")
    p5 = by_phase_point.get("P5", {})
    if set(p5) != set(config.p5_point_ids):
        errors.append("p5_exact_point_set")
    elif any(indices != {1, 2, 3} for indices in p5.values()):
        errors.append("p5_exact_repetitions")


def _validate_exact_noncredit_gate(runs: list[Mapping[str, Any]], *, errors: list[str]) -> None:
    successful = [item for item in runs if item.get("credit") == "non_credit"]
    counts = Counter(str(item.get("phase") or "") for item in successful)
    if dict(counts) != NON_CREDIT_SUCCESS_COUNTS:
        errors.append("non_credit_phase_arithmetic")

    q0 = [item for item in successful if item.get("phase") == "Q0"]
    if Counter(tuple(item.get("model_ids", [])) for item in q0) != Counter(
        {(model_id,): 1 for model_id in EXPECTED_MODELS}
    ):
        errors.append("q0_exact_model_set")
    p0 = [item for item in successful if item.get("phase") == "P0"]
    p0_counts = Counter(tuple(item.get("model_ids", [])) for item in p0)
    if p0_counts != Counter({(model_id,): 3 for model_id in EXPECTED_MODELS}):
        errors.append("p0_exact_model_repetitions")


def validate_x1_evidence(
    payload: Mapping[str, Any],
    *,
    config: X1RuntimeConfig,
    project_root: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version")
    if payload.get("work_item") != "X1":
        errors.append("work_item")
    status = str(payload.get("status") or "")
    if status not in {"planned", "running", "review_pending"}:
        errors.append("status")
    if payload.get("acceptance_credit") is not False:
        errors.append("acceptance_credit_must_be_false")

    raw_runs = payload.get("runs", [])
    if not isinstance(raw_runs, list) or any(not isinstance(item, Mapping) for item in raw_runs):
        errors.append("runs_mapping")
        runs: list[Mapping[str, Any]] = []
    else:
        runs = list(raw_runs)
    for run in runs:
        _validate_run_identity(run, config=config, errors=errors)

    accepted = [item for item in runs if item.get("credit") == "credit"]
    if status == "planned":
        if accepted:
            errors.append("planned_accepted_runs_forbidden")
        if payload.get("dependency") not in (None, {}):
            errors.append("planned_dependency_must_be_unfrozen")
    else:
        try:
            dependency = resolve_verified_s6bm_dependency(
                project_root=project_root, ledger_path=ledger_path
            )
        except X1RuntimeError as exc:
            errors.append(f"dependency:{exc}")
        else:
            if canonical(payload.get("dependency")) != canonical(dependency):
                errors.append("dependency_tuple")
        if accepted:
            _validate_exact_accepted_runs(runs, config=config, errors=errors)
        elif status == "review_pending":
            errors.append("review_pending_exact_run_set_absent")

    if status == "review_pending":
        _validate_exact_accepted_runs(runs, config=config, errors=errors)
        _validate_exact_noncredit_gate(runs, errors=errors)
        if payload.get("reviewer_sign_off") != "pending":
            errors.append("reviewer_sign_off")

    if errors:
        raise X1EvidenceValidationError("x1_evidence_invalid:" + ",".join(sorted(set(errors))))
    return {
        "valid": True,
        "status": status,
        "accepted_run_count": len(accepted),
        "accepted_phase_counts": dict(Counter(str(item.get("phase")) for item in accepted)),
        "non_credit_phase_counts": dict(
            Counter(str(item.get("phase")) for item in runs if item.get("credit") == "non_credit")
        ),
        "dependency_verified": status != "planned",
        "reviewer_sign_off": payload.get("reviewer_sign_off", "not_applicable"),
    }

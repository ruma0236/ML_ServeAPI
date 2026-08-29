from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from evm.scale_validation.x1_contract import (
    API_REPLICAS,
    CLAIM_BOUNDARY,
    CONTRACT_BASE_REVISION,
    CPU_WORKERS,
    KERNEL_OVERLAP_FALLBACK,
    MODEL_IDS,
    PRELIMINARY_AMENDMENT,
    PRELIMINARY_BRANCH,
    PRELIMINARY_SUITE_ID,
    X1Contract,
    X1ContractError,
    canonical_sha256,
    sha256_file,
)


class X1ContractValidationError(RuntimeError):
    pass


def load_canonical_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise X1ContractValidationError("x1_json_not_canonical_lf")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise X1ContractValidationError("x1_json_parse") from exc
    if not isinstance(payload, dict):
        raise X1ContractValidationError("x1_json_root")
    expected = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    if raw != expected:
        raise X1ContractValidationError("x1_json_not_canonical")
    return payload


def validate_contract_amendment(payload: Mapping[str, Any], contract: X1Contract) -> None:
    contract.assert_unchanged()
    expected = {
        "acceptance_credit": False,
        "amendment_id": "s8-v4-x1-model-and-topology-contract-v1",
        "claim_boundary": CLAIM_BOUNDARY,
        "credit": "non_credit",
        "definition": {
            "models": list(MODEL_IDS),
            "topology": {
                "api_replicas": list(API_REPLICAS),
                "client_driver_workers": 16,
                "client_lanes": 4,
                "cpu_workers": list(CPU_WORKERS),
                "path": (
                    "Workloads API -> actual API Pods -> actual server CPU workers -> one "
                    "Triton GPU Pod"
                ),
                "triton_gpu_pods": 1,
            },
        },
        "execution_boundary": {
            "credit_matrix_started": False,
            "integrated_v4_started": False,
            "next_gate": "fresh_artifact_q0_and_non_credit_capacity_calibration",
        },
        "fresh_artifact_contract": {
            "correctness_oracle_required": True,
            "dataset_manifest_and_sha_required": True,
            "preliminary_seeded_dlrm_reuse_forbidden": True,
            "prior_artifact_reuse_forbidden": True,
            "q0_actual_cuda_models": 4,
            "silent_cpu_fallback_allowed": False,
            "triton_artifact_and_config_sha_required": True,
        },
        "frozen_acceptance": {
            "balanced_jain_minimum": 0.9,
            "balanced_starvation": 0,
            "batching_repetitions": 24,
            "concurrent_balanced_repetitions": 18,
            "concurrent_hot_repetitions": 18,
            "credit_matrix_repetitions": 78,
            "hot_non_hot_terminal_progress": True,
            "invariants": {
                "duplicate_effect": 0,
                "illegal_owner_overlap": 0,
                "loss": 0,
                "outcome_unknown": 0,
                "silent_cpu_fallback": 0,
                "unexpected_oom": 0,
            },
            "serial_repetitions": 18,
        },
        "frozen_calibration": {
            "arrival_steps_rps": [25, 50, 100, 200, 400, 800],
            "batching_candidate_repetitions": 24,
            "capacity_fraction": 0.7,
            "cooldown_seconds": 5,
            "load_selection": (
                "minimum of 70 percent measured GPU-time, API, and CPU-worker capacity"
            ),
            "measurement_seconds": 30,
            "per_model_solo_repetitions": 12,
            "profiler_qualification_repetitions": 3,
            "repetitions_each": 3,
            "topology_repetitions": 18,
            "warmup_seconds": 10,
        },
        "model_decision": {
            "disposition": "replaced_by_user_follow_up",
            "original_plan_archetypes": [
                "tabular_tiny_mlp",
                "image_classifier",
                "compact_vlm",
                "compact_4bit_llm",
            ],
            "replacement_models": list(MODEL_IDS),
            "scope_effect": (
                "model set only; the full server-side topology and 78-repetition matrix are "
                "retained"
            ),
        },
        "preliminary_isolation": {
            "amendment": PRELIMINARY_AMENDMENT,
            "branch": PRELIMINARY_BRANCH,
            "cherry_pick_forbidden": True,
            "credit": "non_credit",
            "merge_forbidden": True,
            "reuse_forbidden": True,
            "suite_id": PRELIMINARY_SUITE_ID,
        },
        "profiler": {
            "claim_rule": (
                "distinct model/request CUDA kernel intervals must overlap by a nonzero "
                "duration in Nsight/CUPTI evidence"
            ),
            "fallback_verdict": KERNEL_OVERLAP_FALLBACK,
            "qualification_mode": "concurrent_balanced",
            "qualification_topology": "r1-w4",
        },
        "schema_version": "evm.s8_v4.x1_contract_amendment.v1",
        "source": {
            "base_revision": CONTRACT_BASE_REVISION,
            "config_path": "configs/s8_v4_x1_heterogeneous_v1.toml",
            "config_sha256": contract.sha256,
        },
        "status": "contract_frozen",
        "work_item": "X1",
    }
    if payload != expected:
        raise X1ContractValidationError("x1_contract_amendment_mismatch")


def run_contract_mutations(contract: X1Contract, amendment: Mapping[str, Any]) -> dict[str, Any]:
    cases: tuple[tuple[str, str, Callable[[dict[str, Any]], None]], ...] = (
        ("model_set", "x1_contract_models", lambda value: value["models"].pop()),
        (
            "preliminary_reuse",
            "x1_preliminary_isolation",
            lambda value: value["preliminary_isolation"].__setitem__("reuse_forbidden", False),
        ),
        (
            "api_replica_axis",
            "x1_api_replicas",
            lambda value: value["topology"].__setitem__("api_replicas", [1]),
        ),
        (
            "server_worker_axis",
            "x1_cpu_workers",
            lambda value: value["topology"].__setitem__("cpu_workers", [1, 2]),
        ),
        (
            "triton_digest",
            "x1_triton_identity",
            lambda value: value["triton"].__setitem__("image_digest", "sha256:" + "0" * 64),
        ),
        (
            "arrival_steps",
            "x1_calibration_contract",
            lambda value: value["calibration"].__setitem__("arrival_steps_rps", [25, 50, 100]),
        ),
        (
            "batch_calibration_count",
            "x1_batching_calibration_contract",
            lambda value: value["batching_calibration"].__setitem__("total_repetitions", 12),
        ),
        (
            "credit_matrix_count",
            "x1_credit_matrix_counts",
            lambda value: value["credit_matrix"].__setitem__("total_repetitions", 77),
        ),
        (
            "batch_candidate",
            "x1_enabled_batch_candidate_values",
            lambda value: value["batching"]["enabled_candidates"][0].__setitem__(
                "max_queue_delay_microseconds", 10000
            ),
        ),
        (
            "jain_threshold",
            "x1_jain_threshold",
            lambda value: value["mix"].__setitem__("minimum_balanced_jain", 0.89),
        ),
        (
            "profiler_fallback",
            "x1_profiler_fallback",
            lambda value: value["profiler"].__setitem__("fallback_verdict", "overlap_unknown"),
        ),
        (
            "guardrail",
            "x1_guardrail_contract",
            lambda value: value["guardrails"].__setitem__("maximum_p99_ms", 999.0),
        ),
        (
            "cleanup",
            "x1_cleanup_contract",
            lambda value: value["cleanup"].__setitem__("require_prometheus_up", 4),
        ),
        (
            "claim_boundary",
            "x1_claim_boundary",
            lambda value: value["claim"].__setitem__("boundary", "production"),
        ),
    )
    results: list[dict[str, str]] = []
    for case_id, expected_reason, mutate in cases:
        payload = copy.deepcopy(contract.payload)
        mutate(payload)
        mutated = replace(contract, payload=payload)
        try:
            mutated.validate()
        except X1ContractError as exc:
            reason = str(exc)
            if reason != expected_reason:
                raise X1ContractValidationError(
                    f"x1_mutation_reason:{case_id}:{reason}:{expected_reason}"
                ) from exc
            results.append({"case_id": case_id, "reason": reason, "result": "FAIL_CLOSED"})
        else:
            raise X1ContractValidationError(f"x1_mutation_fail_open:{case_id}")

    amendment_mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
        (
            "amendment_preliminary_reuse",
            lambda value: value["preliminary_isolation"].__setitem__("reuse_forbidden", False),
        ),
        (
            "amendment_matrix_reduction",
            lambda value: value["frozen_acceptance"].__setitem__("credit_matrix_repetitions", 77),
        ),
        (
            "amendment_credit_promotion",
            lambda value: value.__setitem__("acceptance_credit", True),
        ),
    )
    for case_id, mutate in amendment_mutations:
        payload = copy.deepcopy(dict(amendment))
        mutate(payload)
        try:
            validate_contract_amendment(payload, contract)
        except X1ContractValidationError as exc:
            if str(exc) != "x1_contract_amendment_mismatch":
                raise
            results.append(
                {
                    "case_id": case_id,
                    "reason": "x1_contract_amendment_mismatch",
                    "result": "FAIL_CLOSED",
                }
            )
        else:
            raise X1ContractValidationError(f"x1_mutation_fail_open:{case_id}")
    return {
        "schema_version": "evm.s8_v4.x1_contract_mutation.v1",
        "positive_controls": 1,
        "negative_rejected": len(results),
        "case_set_sha256": canonical_sha256(results),
        "cases": results,
    }


def validate_contract_files(
    *, config_path: Path, amendment_path: Path, source_root: Path, data_root: Path
) -> tuple[X1Contract, dict[str, Any], dict[str, Any]]:
    contract = X1Contract.from_path(config_path, source_root=source_root, data_root=data_root)
    amendment = load_canonical_json(amendment_path)
    validate_contract_amendment(amendment, contract)
    mutation = run_contract_mutations(contract, amendment)
    return contract, amendment, mutation


def amendment_sha256(path: Path) -> str:
    return sha256_file(path)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result

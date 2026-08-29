from __future__ import annotations

import hashlib
import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


MODEL_IDS = (
    "higgs_logistic_regression",
    "higgs_gaussian_nb",
    "higgs_tiny_mlp",
    "criteo_dlrm_lite",
)
API_REPLICAS = (1, 2)
CPU_WORKERS = (1, 2, 4)
REPETITIONS = (1, 2, 3)
PRELIMINARY_BRANCH = "codex/x1-resume-results-20260825-215716"
PRELIMINARY_AMENDMENT = "8ab8c64"
PRELIMINARY_SUITE_ID = "x1-resume-20260825T171224Z-a35200d8"
CONTRACT_BASE_REVISION = "7c93ba8db442a992ba4445bf02482b6e89d63048"
KERNEL_OVERLAP_FALLBACK = "kernel_overlap_not_evidenced"
TRITON_IMAGE = "nvcr.io/nvidia/tritonserver:25.08-py3"
TRITON_IMAGE_DIGEST = "sha256:f836551575df7c9fb71144073845c6b3911de57db91a8c95e0687a4d2ac9f7a5"
GPU_NAME = "NVIDIA GeForce RTX 4080 SUPER"
GPU_UUID = "GPU-4eea4bfc-f15e-bd25-c1b8-ed53ade9ad1d"
CLAIM_BOUNDARY = (
    "Controlled measurements on one Windows/WSL2 physical node, one RTX 4080, one "
    "Triton GPU runtime, and governed synthetic or public-source inputs. No production "
    "SLA, physical HA/DR, multi-node, multi-GPU, MIG, MPS, tenant isolation, model "
    "accuracy, or physical zero-downtime claim."
)


class X1ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class X1MatrixCell:
    mode: str
    api_replicas: int | None
    cpu_workers: int | None
    model_id: str | None
    batch_candidate: str | None
    repetition: int

    @property
    def cell_id(self) -> str:
        parts = [self.mode]
        if self.api_replicas is not None:
            parts.append(f"r{self.api_replicas}")
        if self.cpu_workers is not None:
            parts.append(f"w{self.cpu_workers}")
        if self.model_id is not None:
            parts.append(self.model_id)
        if self.batch_candidate is not None:
            parts.append(self.batch_candidate)
        parts.append(f"rep{self.repetition}")
        return "-".join(parts)


@dataclass(frozen=True)
class X1Contract:
    path: Path
    sha256: str
    source_root: Path
    data_root: Path
    payload: Mapping[str, Any]

    @classmethod
    def from_path(cls, path: Path, *, source_root: Path, data_root: Path) -> "X1Contract":
        resolved = path.resolve()
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
        contract = cls(
            path=resolved,
            sha256=sha256_file(resolved),
            source_root=source_root.resolve(),
            data_root=data_root.resolve(),
            payload=payload,
        )
        contract.validate()
        return contract

    def validate(self) -> None:
        payload = self.payload
        if payload.get("schema_version") != "evm.s8_v4.x1_contract.v1":
            raise X1ContractError("x1_contract_schema")
        if payload.get("work_item") != "X1" or payload.get("tracking") != "EVM-301/SCRUM-212":
            raise X1ContractError("x1_contract_tracking")
        if _exact_string_list(payload.get("models"), "models") != MODEL_IDS:
            raise X1ContractError("x1_contract_models")
        source = _mapping(payload, "source")
        if source.get("contract_base_revision") != CONTRACT_BASE_REVISION:
            raise X1ContractError("x1_contract_base_revision")
        preliminary = _mapping(payload, "preliminary_isolation")
        expected_preliminary = {
            "branch": PRELIMINARY_BRANCH,
            "amendment": PRELIMINARY_AMENDMENT,
            "suite_id": PRELIMINARY_SUITE_ID,
            "credit": "non_credit",
            "reuse_forbidden": True,
            "merge_forbidden": True,
            "cherry_pick_forbidden": True,
        }
        if preliminary != expected_preliminary:
            raise X1ContractError("x1_preliminary_isolation")
        topology = _mapping(payload, "topology")
        if (
            topology.get("path")
            != "workloads_api_to_api_pods_to_server_workers_to_single_triton_gpu_pod"
        ):
            raise X1ContractError("x1_topology_path")
        if _exact_int_list(topology.get("api_replicas"), "api_replicas") != API_REPLICAS:
            raise X1ContractError("x1_api_replicas")
        if _exact_int_list(topology.get("cpu_workers"), "cpu_workers") != CPU_WORKERS:
            raise X1ContractError("x1_cpu_workers")
        if topology.get("triton_gpu_pods") != 1 or topology.get("triton_instances_per_model") != 1:
            raise X1ContractError("x1_single_triton_topology")
        if topology.get("require_all_api_pods_observed") is not True:
            raise X1ContractError("x1_api_pod_attribution")
        if topology.get("require_all_server_workers_observed") is not True:
            raise X1ContractError("x1_worker_attribution")
        if (
            topology.get("triton_gpu_pods") != 1
            or topology.get("triton_instances_per_model") != 1
            or topology.get("client_lanes") != 4
            or topology.get("client_driver_workers") != 16
            or topology.get("api_node_port") != 31120
            or topology.get("triton_http_node_port") != 31121
            or topology.get("triton_metrics_node_port") != 31122
        ):
            raise X1ContractError("x1_topology_exact_values")
        triton = _mapping(payload, "triton")
        if triton != {
            "image": TRITON_IMAGE,
            "image_digest": TRITON_IMAGE_DIGEST,
            "model_version": 1,
            "explicit_model_control": True,
            "gpu_device_index": 0,
            "gpu_name": GPU_NAME,
            "gpu_uuid": GPU_UUID,
        }:
            raise X1ContractError("x1_triton_identity")
        calibration = _mapping(payload, "calibration")
        expected_calibration = {
            "repetitions": 3,
            "warmup_seconds": 10,
            "measurement_seconds": 30,
            "cooldown_seconds": 5,
            "arrival_steps_rps": [25, 50, 100, 200, 400, 800],
            "request_deadline_seconds": 5.0,
            "admission_wait_seconds": 0.05,
            "max_outstanding": 512,
            "solo_models": 4,
            "solo_repetitions_total": 12,
            "topology_points": 6,
            "topology_repetitions_total": 18,
            "capacity_fraction": 0.70,
        }
        if calibration != expected_calibration:
            raise X1ContractError("x1_calibration_contract")
        if _exact_int_list(calibration.get("arrival_steps_rps"), "arrival_steps_rps") != (
            25,
            50,
            100,
            200,
            400,
            800,
        ):
            raise X1ContractError("x1_arrival_steps")
        batching_calibration = _mapping(payload, "batching_calibration")
        if batching_calibration != {
            "repetitions": 3,
            "models": 4,
            "enabled_candidates_per_model": 2,
            "total_repetitions": 24,
            "selection": (
                "highest safe service rate; p99/error/queue/OOM guardrails remain mandatory"
            ),
        }:
            raise X1ContractError("x1_batching_calibration_contract")
        training = _mapping(payload, "artifact_training")
        expected_training = {
            "higgs_train_rows": 250000,
            "higgs_validation_rows": 50000,
            "higgs_epochs": 5,
            "higgs_batch_size": 8192,
            "higgs_learning_rate": 0.001,
            "higgs_weight_decay": 0.00001,
            "criteo_train_rows": 50000,
            "criteo_validation_rows": 10000,
            "criteo_epochs": 3,
            "criteo_batch_size": 4096,
            "criteo_learning_rate": 0.001,
            "criteo_weight_decay": 0.00001,
            "criteo_embedding_vocab_size": 4096,
            "criteo_embedding_dim": 4,
            "oracle_rows": 64,
        }
        if training != expected_training:
            raise X1ContractError("x1_artifact_training_contract")
        credit = _mapping(payload, "credit_matrix")
        expected_counts = {
            "repetitions": 3,
            "serial_points": 6,
            "serial_repetitions_total": 18,
            "balanced_points": 6,
            "balanced_repetitions_total": 18,
            "hot_points": 6,
            "hot_repetitions_total": 18,
            "batch_models": 4,
            "batch_candidates_per_model": 2,
            "batch_repetitions_total": 24,
            "total_repetitions": 78,
        }
        if credit != expected_counts:
            raise X1ContractError("x1_credit_matrix_counts")
        if len(self.credit_matrix()) != 78:
            raise X1ContractError("x1_credit_matrix_cardinality")
        mix = _mapping(payload, "mix")
        if _exact_float_list(mix.get("balanced_gpu_time_shares"), "balanced_shares") != (
            0.25,
            0.25,
            0.25,
            0.25,
        ):
            raise X1ContractError("x1_balanced_shares")
        if mix.get("hot_model") != "criteo_dlrm_lite":
            raise X1ContractError("x1_hot_model")
        if _exact_float_list(mix.get("hot_gpu_time_shares"), "hot_shares") != (
            0.10,
            0.10,
            0.10,
            0.70,
        ):
            raise X1ContractError("x1_hot_shares")
        if mix.get("minimum_balanced_jain") != 0.90:
            raise X1ContractError("x1_jain_threshold")
        profiler = _mapping(payload, "profiler")
        if profiler.get("required_for_kernel_overlap_claim") is not True:
            raise X1ContractError("x1_profiler_requirement")
        if profiler.get("fallback_verdict") != KERNEL_OVERLAP_FALLBACK:
            raise X1ContractError("x1_profiler_fallback")
        if profiler != {
            "required_for_kernel_overlap_claim": True,
            "required_identity_fields": [
                "model_id",
                "request_id",
                "kernel_name",
                "start_ns",
                "end_ns",
                "device_id",
                "stream_id",
            ],
            "fallback_verdict": KERNEL_OVERLAP_FALLBACK,
            "qualification_repetitions": 3,
            "qualification_mode": "concurrent_balanced",
            "qualification_topology": "r1-w4",
        }:
            raise X1ContractError("x1_profiler_contract")
        guardrails = _mapping(payload, "guardrails")
        if guardrails != {
            "maximum_error_rate": 0.01,
            "maximum_p99_ms": 250.0,
            "maximum_queue_wait_ms": 100.0,
            "maximum_gpu_temperature_celsius": 84.0,
            "maximum_gpu_power_watts": 340.0,
            "require_zero_unexpected_oom": True,
            "require_zero_silent_fallback": True,
            "require_zero_illegal_owner_overlap": True,
            "require_zero_loss": True,
            "require_zero_duplicate_effect": True,
            "require_zero_outcome_unknown": True,
        }:
            raise X1ContractError("x1_guardrail_contract")
        cleanup = _mapping(payload, "cleanup")
        if cleanup != {
            "timeout_seconds": 120,
            "require_b0_exact_cuda": True,
            "require_prometheus_up": 5,
            "require_queue_active": 0,
            "require_queue_leased": 0,
            "require_queue_outcome_unknown": 0,
            "require_gpu_lease_absent": True,
            "require_temporary_runtime_absent": True,
        }:
            raise X1ContractError("x1_cleanup_contract")
        if _mapping(payload, "claim") != {"boundary": CLAIM_BOUNDARY}:
            raise X1ContractError("x1_claim_boundary")
        self._validate_batching()
        self._validate_sources()

    def _validate_batching(self) -> None:
        batching = _mapping(self.payload, "batching")
        disabled = _mapping(batching, "disabled")
        if disabled != {
            "max_batch_size": 0,
            "preferred_batch_size": [],
            "max_queue_delay_microseconds": 0,
            "preserve_ordering": True,
            "priority_levels": 0,
            "instance_group_count": 1,
        }:
            raise X1ContractError("x1_disabled_batch_contract")
        candidates = batching.get("enabled_candidates")
        if not isinstance(candidates, list) or len(candidates) != 2:
            raise X1ContractError("x1_enabled_batch_candidates")
        expected = [
            {
                "candidate_id": "enabled-4-8-2ms",
                "max_batch_size": 16,
                "preferred_batch_size": [4, 8],
                "max_queue_delay_microseconds": 2000,
                "preserve_ordering": True,
                "priority_levels": 0,
                "instance_group_count": 1,
            },
            {
                "candidate_id": "enabled-8-16-10ms",
                "max_batch_size": 32,
                "preferred_batch_size": [8, 16],
                "max_queue_delay_microseconds": 10000,
                "preserve_ordering": True,
                "priority_levels": 0,
                "instance_group_count": 1,
            },
        ]
        if candidates != expected:
            raise X1ContractError("x1_enabled_batch_candidate_values")

    def _validate_sources(self) -> None:
        source = _mapping(self.payload, "source")
        source_paths = ("s3_config", "s4_config", "s5_config")
        data_paths = (
            "s3_registry",
            "s3_split_manifest",
            "s4_train_features",
            "s4_train_labels",
            "s4_validation_features",
            "s4_validation_labels",
            "s4_replay_features",
            "s5_manifest",
            "s5_training_shard",
        )
        for key in source_paths:
            _contained_file(self.source_root, source.get(key), key)
        for key in data_paths:
            _contained_file(self.data_root, source.get(key), key)

    def assert_unchanged(self) -> None:
        if sha256_file(self.path) != self.sha256:
            raise X1ContractError("x1_contract_changed")

    def credit_matrix(self) -> tuple[X1MatrixCell, ...]:
        cells: list[X1MatrixCell] = []
        for mode in ("serial", "concurrent_balanced", "concurrent_hot"):
            for replicas in API_REPLICAS:
                for workers in CPU_WORKERS:
                    for repetition in REPETITIONS:
                        cells.append(X1MatrixCell(mode, replicas, workers, None, None, repetition))
        for model_id in MODEL_IDS:
            for candidate in ("disabled", "selected_enabled"):
                for repetition in REPETITIONS:
                    cells.append(
                        X1MatrixCell(
                            "per_model_batching",
                            None,
                            None,
                            model_id,
                            candidate,
                            repetition,
                        )
                    )
        identifiers = [cell.cell_id for cell in cells]
        if len(identifiers) != len(set(identifiers)):
            raise X1ContractError("x1_credit_matrix_duplicate")
        return tuple(cells)

    def solo_calibration_cells(self) -> tuple[X1MatrixCell, ...]:
        return tuple(
            X1MatrixCell("solo_calibration", 1, 1, model_id, "disabled", repetition)
            for model_id in MODEL_IDS
            for repetition in REPETITIONS
        )

    def topology_calibration_cells(self) -> tuple[X1MatrixCell, ...]:
        return tuple(
            X1MatrixCell("topology_calibration", replicas, workers, None, None, repetition)
            for replicas in API_REPLICAS
            for workers in CPU_WORKERS
            for repetition in REPETITIONS
        )

    def batching_calibration_cells(self) -> tuple[X1MatrixCell, ...]:
        candidates = tuple(
            item["candidate_id"]
            for item in _mapping(self.payload, "batching")["enabled_candidates"]
        )
        return tuple(
            X1MatrixCell("batching_calibration", 1, 1, model_id, candidate, repetition)
            for model_id in MODEL_IDS
            for candidate in candidates
            for repetition in REPETITIONS
        )

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "evm.s8_v4.x1_contract_snapshot.v1",
            "config_sha256": self.sha256,
            "models": list(MODEL_IDS),
            "topology": {
                "api_replicas": list(API_REPLICAS),
                "cpu_workers": list(CPU_WORKERS),
                "triton_gpu_pods": 1,
                "client_lanes": _mapping(self.payload, "topology")["client_lanes"],
                "client_driver_workers": _mapping(self.payload, "topology")[
                    "client_driver_workers"
                ],
            },
            "calibration": {
                "solo_repetitions": len(self.solo_calibration_cells()),
                "topology_repetitions": len(self.topology_calibration_cells()),
                "arrival_steps_rps": list(
                    _mapping(self.payload, "calibration")["arrival_steps_rps"]
                ),
                "warmup_seconds": _mapping(self.payload, "calibration")["warmup_seconds"],
                "measurement_seconds": _mapping(self.payload, "calibration")["measurement_seconds"],
                "cooldown_seconds": _mapping(self.payload, "calibration")["cooldown_seconds"],
                "batching_candidate_repetitions": len(self.batching_calibration_cells()),
            },
            "credit_matrix_repetitions": len(self.credit_matrix()),
            "preliminary_isolation": dict(_mapping(self.payload, "preliminary_isolation")),
            "claim_boundary": _mapping(self.payload, "claim")["boundary"],
        }


def compute_load_freeze(
    contract: X1Contract,
    *,
    model_calibrations: Sequence[Mapping[str, Any]],
    topology_calibrations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    contract.assert_unchanged()
    model_groups = _exact_repetition_groups(model_calibrations, key="model_id", expected=MODEL_IDS)
    topology_keys = tuple(
        f"r{replicas}-w{workers}" for replicas in API_REPLICAS for workers in CPU_WORKERS
    )
    topology_groups = _exact_repetition_groups(
        topology_calibrations, key="topology_id", expected=topology_keys
    )
    model_rates: dict[str, float] = {}
    gpu_demands: dict[str, float] = {}
    for model_id, records in model_groups.items():
        rates = [
            _positive_finite(item.get("safe_service_rps"), "safe_service_rps") for item in records
        ]
        demands = [
            _positive_finite(item.get("gpu_seconds_per_request"), "gpu_seconds_per_request")
            for item in records
        ]
        model_rates[model_id] = min(rates)
        gpu_demands[model_id] = max(demands)
    topology_rates: dict[str, float] = {}
    for topology_id, records in topology_groups.items():
        rates = [
            _positive_finite(item.get("safe_service_rps"), "safe_service_rps") for item in records
        ]
        topology_rates[topology_id] = min(rates)
    gpu_capacity = 1.0 / sum(0.25 * gpu_demands[model_id] for model_id in MODEL_IDS)
    api_capacity = min(
        topology_rates[topology_id]
        for topology_id in topology_keys
        if topology_id.startswith("r1-")
    )
    cpu_capacity = min(topology_rates.values())
    capacity_fraction = float(_mapping(contract.payload, "calibration")["capacity_fraction"])
    candidates = {
        "gpu_rps": gpu_capacity * capacity_fraction,
        "api_rps": api_capacity * capacity_fraction,
        "cpu_worker_rps": cpu_capacity * capacity_fraction,
    }
    selected_total_rps = min(candidates.values())
    balanced_targets = _target_rps(selected_total_rps, gpu_demands, (0.25,) * 4)
    hot_targets = _target_rps(selected_total_rps, gpu_demands, (0.10, 0.10, 0.10, 0.70))
    return {
        "schema_version": "evm.s8_v4.x1_load_freeze.v1",
        "config_sha256": contract.sha256,
        "models": list(MODEL_IDS),
        "model_safe_service_rps": model_rates,
        "gpu_seconds_per_request": gpu_demands,
        "topology_safe_service_rps": topology_rates,
        "capacity_candidates_rps": candidates,
        "selected_total_rps": selected_total_rps,
        "selection": "minimum_of_70_percent_gpu_api_cpu_capacity",
        "balanced_target_rps": balanced_targets,
        "hot_target_rps": hot_targets,
        "jain_threshold": 0.90,
        "kernel_overlap_verdict": KERNEL_OVERLAP_FALLBACK,
    }


def jain_service_attainment(
    service_rps: Mapping[str, float], target_rps: Mapping[str, float]
) -> float:
    if set(service_rps) != set(MODEL_IDS) or set(target_rps) != set(MODEL_IDS):
        raise X1ContractError("x1_jain_model_set")
    attainment = [
        _nonnegative_finite(service_rps[model_id], "service_rps")
        / _positive_finite(target_rps[model_id], "target_rps")
        for model_id in MODEL_IDS
    ]
    denominator = 4.0 * sum(value * value for value in attainment)
    if denominator == 0:
        raise X1ContractError("x1_jain_zero_service")
    return sum(attainment) ** 2 / denominator


def validate_kernel_overlap(records: Sequence[Mapping[str, Any]]) -> str:
    valid: list[tuple[str, str, int, int]] = []
    for record in records:
        if set(record) != {
            "model_id",
            "request_id",
            "kernel_name",
            "start_ns",
            "end_ns",
            "device_id",
            "stream_id",
        }:
            raise X1ContractError("x1_profiler_record_schema")
        model_id = str(record["model_id"])
        request_id = str(record["request_id"])
        start = _strict_int(record["start_ns"], "start_ns")
        end = _strict_int(record["end_ns"], "end_ns")
        if model_id not in MODEL_IDS or not request_id or start < 0 or end <= start:
            raise X1ContractError("x1_profiler_record_value")
        valid.append((model_id, request_id, start, end))
    for index, left in enumerate(valid):
        for right in valid[index + 1 :]:
            if left[:2] == right[:2]:
                continue
            if max(left[2], right[2]) < min(left[3], right[3]):
                return "kernel_overlap_evidenced"
    return KERNEL_OVERLAP_FALLBACK


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise X1ContractError(f"x1_contract_section:{key}")
    return value


def _exact_string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(type(item) is not str or not item for item in value):
        raise X1ContractError(f"x1_string_list:{field}")
    return tuple(value)


def _exact_int_list(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise X1ContractError(f"x1_int_list:{field}")
    return tuple(value)


def _exact_float_list(value: Any, field: str) -> tuple[float, ...]:
    if not isinstance(value, list) or any(type(item) is not float for item in value):
        raise X1ContractError(f"x1_float_list:{field}")
    values = tuple(value)
    if any(not math.isfinite(item) for item in values):
        raise X1ContractError(f"x1_float_finite:{field}")
    return values


def _strict_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise X1ContractError(f"x1_int:{field}")
    return value


def _positive_finite(value: Any, field: str) -> float:
    number = _nonnegative_finite(value, field)
    if number <= 0:
        raise X1ContractError(f"x1_positive:{field}")
    return number


def _nonnegative_finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise X1ContractError(f"x1_numeric:{field}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise X1ContractError(f"x1_finite:{field}")
    return number


def _contained_file(root: Path, relative: Any, field: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise X1ContractError(f"x1_source_path:{field}")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise X1ContractError(f"x1_source_containment:{field}") from exc
    if not resolved.is_file():
        raise X1ContractError(f"x1_source_missing:{field}")
    return resolved


def _exact_repetition_groups(
    records: Sequence[Mapping[str, Any]], *, key: str, expected: Sequence[str]
) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = {item: [] for item in expected}
    seen: set[tuple[str, int]] = set()
    for record in records:
        identity = record.get(key)
        repetition = record.get("repetition")
        if identity not in groups or type(repetition) is not int or repetition not in REPETITIONS:
            raise X1ContractError(f"x1_calibration_identity:{key}")
        pair = (str(identity), repetition)
        if pair in seen:
            raise X1ContractError(f"x1_calibration_duplicate:{key}")
        seen.add(pair)
        groups[str(identity)].append(record)
    expected_pairs = {(identity, repetition) for identity in expected for repetition in REPETITIONS}
    if seen != expected_pairs:
        raise X1ContractError(f"x1_calibration_repetition_set:{key}")
    return groups


def _target_rps(
    total_rps: float, gpu_demands: Mapping[str, float], shares: Sequence[float]
) -> dict[str, float]:
    weights = [shares[index] / gpu_demands[model_id] for index, model_id in enumerate(MODEL_IDS)]
    denominator = sum(weights)
    return {
        model_id: total_rps * weights[index] / denominator
        for index, model_id in enumerate(MODEL_IDS)
    }

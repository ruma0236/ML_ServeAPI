from __future__ import annotations

import hashlib
import json
import math
import statistics
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


Family = Literal["image", "vlm", "llm"]

QUALITY_SCHEMAS: dict[Family, tuple[str, ...]] = {
    "image": ("binary_accuracy", "mean_confidence"),
    "vlm": ("accuracy", "parse_rate"),
    "llm": ("mean_token_f1", "nonempty_rate"),
}
GENERATION_SCHEMAS: dict[Family, tuple[str, ...]] = {
    "image": (),
    "vlm": (
        "generated_tokens_total",
        "tokens_per_second_mean",
        "ttft_p95_seconds",
        "tpot_p95_seconds",
        "termination_reasons",
    ),
    "llm": (
        "generated_tokens_total",
        "tokens_per_second_mean",
        "ttft_p95_seconds",
        "tpot_p95_seconds",
        "termination_reasons",
    ),
}
OPERATIONAL_SCHEMAS: dict[Family, tuple[str, ...]] = {
    "image": (
        "request_bytes",
        "image_bytes",
        "image_pixels",
        "queue_wait_seconds",
        "decode_seconds",
        "preprocess_seconds",
        "inference_seconds",
        "peak_vram_bytes",
    ),
    "vlm": (
        "request_bytes",
        "image_bytes",
        "image_pixels",
        "queue_wait_seconds",
        "decode_seconds",
        "preprocess_seconds",
        "input_tokens",
        "generated_tokens",
        "generation_seconds",
        "inference_seconds",
        "tokens_per_second",
        "ttft_seconds",
        "tpot_seconds",
        "peak_vram_bytes",
    ),
    "llm": (
        "request_bytes",
        "queue_wait_seconds",
        "preprocess_seconds",
        "input_tokens",
        "generated_tokens",
        "generation_seconds",
        "inference_seconds",
        "tokens_per_second",
        "ttft_seconds",
        "tpot_seconds",
        "peak_vram_bytes",
    ),
}


class S7RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class S7RuntimeConfig:
    path: Path
    sha256: str
    seed: int
    repetitions: int
    profile_ids: tuple[str, ...]
    requests_per_profile: int
    fairness_short_requests: int
    fairness_long_requests: int
    closed_concurrency: int
    warmup_requests: int
    cooldown_seconds: float
    request_timeout_seconds: float
    resource_sample_interval_seconds: float
    maximum_p99_seconds: float
    maximum_queue_wait_seconds: float
    max_short_bypass: int
    starvation_seconds: float
    long_request_cost_units: dict[str, float]
    require_zero_oom: bool
    require_zero_starvation: bool
    image_accuracy_minimum: float
    vlm_accuracy_minimum: float
    vlm_parse_rate_minimum: float
    llm_token_f1_minimum: float
    llm_nonempty_rate_minimum: float
    claim_boundary: str

    @classmethod
    def from_path(cls, path: Path) -> "S7RuntimeConfig":
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        scheduler = _section(payload, "scheduler")
        experiment = _section(payload, "experiment")
        quality = _section(payload, "quality")
        claim = _section(payload, "claim_boundary")
        config = cls(
            path=path,
            sha256=file_sha256(path),
            seed=int(payload["seed"]),
            repetitions=int(payload["repetitions"]),
            profile_ids=tuple(str(value) for value in experiment["profile_ids"]),
            requests_per_profile=int(experiment["requests_per_profile"]),
            fairness_short_requests=int(experiment["fairness_short_requests"]),
            fairness_long_requests=int(experiment["fairness_long_requests"]),
            closed_concurrency=int(experiment["closed_concurrency"]),
            warmup_requests=int(experiment["warmup_requests"]),
            cooldown_seconds=float(experiment["cooldown_seconds"]),
            request_timeout_seconds=float(experiment["request_timeout_seconds"]),
            resource_sample_interval_seconds=float(experiment["resource_sample_interval_seconds"]),
            maximum_p99_seconds=float(experiment["maximum_p99_seconds"]),
            maximum_queue_wait_seconds=float(experiment["maximum_queue_wait_seconds"]),
            max_short_bypass=int(scheduler["max_short_bypass"]),
            starvation_seconds=float(scheduler["starvation_seconds"]),
            long_request_cost_units={
                family: float(_section(payload, family)["long_request_cost_units"])
                for family in QUALITY_SCHEMAS
            },
            require_zero_oom=bool(experiment["require_zero_oom"]),
            require_zero_starvation=bool(experiment["require_zero_starvation"]),
            image_accuracy_minimum=float(quality["image_accuracy_minimum"]),
            vlm_accuracy_minimum=float(quality["vlm_accuracy_minimum"]),
            vlm_parse_rate_minimum=float(quality["vlm_parse_rate_minimum"]),
            llm_token_f1_minimum=float(quality["llm_token_f1_minimum"]),
            llm_nonempty_rate_minimum=float(quality["llm_nonempty_rate_minimum"]),
            claim_boundary=str(claim["text"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        expected = {
            f"{family}-{suffix}"
            for family, suffixes in {
                "image": ("small", "large", "fairness", "over-limit"),
                "vlm": ("small-short", "large-long", "fairness", "over-limit"),
                "llm": ("short", "long", "fairness", "over-limit"),
            }.items()
            for suffix in suffixes
        }
        if set(self.profile_ids) != expected or len(self.profile_ids) != len(expected):
            raise S7RuntimeError("s7_profile_matrix_invalid")
        if self.repetitions != 3 or self.requests_per_profile < 1:
            raise S7RuntimeError("s7_repetition_contract_invalid")
        if self.fairness_short_requests < 1 or self.fairness_long_requests < 1:
            raise S7RuntimeError("s7_fairness_contract_invalid")
        if self.closed_concurrency < 2 or self.max_short_bypass < 1:
            raise S7RuntimeError("s7_scheduler_contract_invalid")
        finite = (
            self.cooldown_seconds,
            self.request_timeout_seconds,
            self.resource_sample_interval_seconds,
            self.maximum_p99_seconds,
            self.maximum_queue_wait_seconds,
            self.starvation_seconds,
            *self.long_request_cost_units.values(),
            self.image_accuracy_minimum,
            self.vlm_accuracy_minimum,
            self.vlm_parse_rate_minimum,
            self.llm_token_f1_minimum,
            self.llm_nonempty_rate_minimum,
        )
        if any(not math.isfinite(value) or value < 0 for value in finite):
            raise S7RuntimeError("s7_numeric_contract_invalid")
        if any(
            value > 1
            for value in (
                self.image_accuracy_minimum,
                self.vlm_accuracy_minimum,
                self.vlm_parse_rate_minimum,
                self.llm_token_f1_minimum,
                self.llm_nonempty_rate_minimum,
            )
        ):
            raise S7RuntimeError("s7_quality_threshold_invalid")

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evm.s7_family_admission.v1",
            "config_sha256": self.sha256,
            "seed": self.seed,
            "repetitions": self.repetitions,
            "profile_ids": list(self.profile_ids),
            "requests_per_profile": self.requests_per_profile,
            "fairness_short_requests": self.fairness_short_requests,
            "fairness_long_requests": self.fairness_long_requests,
            "closed_concurrency": self.closed_concurrency,
            "warmup_requests": self.warmup_requests,
            "cooldown_seconds": self.cooldown_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "resource_sample_interval_seconds": self.resource_sample_interval_seconds,
            "maximum_p99_seconds": self.maximum_p99_seconds,
            "maximum_queue_wait_seconds": self.maximum_queue_wait_seconds,
            "max_short_bypass": self.max_short_bypass,
            "starvation_seconds": self.starvation_seconds,
            "long_request_cost_units": self.long_request_cost_units,
            "quality_thresholds": {
                "image": {"binary_accuracy": self.image_accuracy_minimum},
                "vlm": {
                    "accuracy": self.vlm_accuracy_minimum,
                    "parse_rate": self.vlm_parse_rate_minimum,
                },
                "llm": {
                    "mean_token_f1": self.llm_token_f1_minimum,
                    "nonempty_rate": self.llm_nonempty_rate_minimum,
                },
            },
            "claim_boundary": self.claim_boundary,
        }


def profile_family(profile_id: str) -> Family:
    family = profile_id.split("-", 1)[0]
    if family not in QUALITY_SCHEMAS:
        raise S7RuntimeError(f"s7_profile_family_invalid:{profile_id}")
    return family  # type: ignore[return-value]


def analyze_s7_profiles(
    profiles: list[Mapping[str, Any]], config: S7RuntimeConfig
) -> dict[str, Any]:
    expected_keys = {
        (profile_id, repetition)
        for profile_id in config.profile_ids
        for repetition in range(1, config.repetitions + 1)
    }
    observed_keys = {
        (str(item.get("profile_id")), int(item.get("repetition", 0))) for item in profiles
    }
    matrix_complete = len(profiles) == len(expected_keys) and observed_keys == expected_keys
    schema_valid = True
    quality_valid = True
    selected_safe = True
    traces_complete = True
    prometheus_complete = True
    fairness_valid = True
    rejections_valid = True
    family_rollups: dict[str, dict[str, Any]] = {}

    for family in QUALITY_SCHEMAS:
        family_profiles = [item for item in profiles if item.get("family") == family]
        accepted_profiles = [
            item
            for item in family_profiles
            if not str(item.get("profile_id")).endswith("over-limit")
        ]
        quality_profiles = [
            item
            for item in accepted_profiles
            if not str(item.get("profile_id")).endswith("fairness")
        ]
        expected_metric_schema = {
            "quality": list(QUALITY_SCHEMAS[family]),
            "generation": list(GENERATION_SCHEMAS[family]),
            "operational": list(OPERATIONAL_SCHEMAS[family]),
        }
        schema_valid = schema_valid and all(
            item.get("metric_schema") == expected_metric_schema for item in family_profiles
        )
        quality_valid = quality_valid and all(
            _quality_passes(family, dict(item.get("quality", {})), config)
            for item in quality_profiles
        )
        selected_safe = selected_safe and all(
            int(item.get("oom_count", 0)) == 0
            and int(item.get("starvation_count", 0)) == 0
            and item.get("drained") is True
            and item.get("lease_identity_exact") is True
            and item.get("cleanup_passed") is True
            and _finite(item.get("p99_seconds"), "p99_seconds") <= config.maximum_p99_seconds
            and _finite(item.get("maximum_queue_wait_seconds"), "maximum_queue_wait_seconds")
            <= config.maximum_queue_wait_seconds
            for item in accepted_profiles
        )
        traces_complete = traces_complete and all(
            item.get("trace_complete") is True for item in accepted_profiles
        )
        prometheus_complete = prometheus_complete and all(
            item.get("prometheus_up") is True for item in accepted_profiles
        )
        over_limit = [
            item for item in family_profiles if str(item.get("profile_id")).endswith("over-limit")
        ]
        rejections_valid = (
            rejections_valid
            and len(over_limit) == config.repetitions
            and all(
                int(item.get("accepted", 0)) == 0
                and int(item.get("rejected", 0)) == config.requests_per_profile
                and set(item.get("rejection_statuses", [])) <= {413, 422}
                and bool(item.get("rejection_statuses"))
                for item in over_limit
            )
        )
        fairness = [
            item for item in family_profiles if str(item.get("profile_id")).endswith("fairness")
        ]
        fairness_valid = (
            fairness_valid
            and len(fairness) == config.repetitions
            and all(
                int(item.get("short_completed", 0)) == config.fairness_short_requests
                and int(item.get("long_completed", 0)) == config.fairness_long_requests
                and int(item.get("starvation_count", 0)) == 0
                and int(item.get("maximum_short_bypass", 0)) <= config.max_short_bypass
                and _finite(item.get("long_request_max_wait_seconds"), "long_wait")
                <= config.starvation_seconds
                for item in fairness
            )
        )
        family_rollups[family] = {
            "profile_repetitions": len(family_profiles),
            "completed": sum(int(item.get("completed", 0)) for item in family_profiles),
            "rejected": sum(int(item.get("rejected", 0)) for item in family_profiles),
            "p95_seconds_max": max(
                (_finite(item.get("p95_seconds"), "p95") for item in accepted_profiles),
                default=0.0,
            ),
            "p99_seconds_max": max(
                (_finite(item.get("p99_seconds"), "p99") for item in accepted_profiles),
                default=0.0,
            ),
            "throughput_mean_requests_per_second": _mean(
                [
                    _finite(item.get("throughput_requests_per_second"), "throughput")
                    for item in accepted_profiles
                ]
            ),
            "quality_schema": list(QUALITY_SCHEMAS[family]),
            "generation_schema": list(GENERATION_SCHEMAS[family]),
        }

    acceptance = {
        "S7-AC-01": bool(matrix_complete and schema_valid and quality_valid),
        "S7-AC-02": bool(
            matrix_complete
            and selected_safe
            and traces_complete
            and prometheus_complete
            and rejections_valid
        ),
        "S7-AC-03": bool(matrix_complete and fairness_valid),
        "S7-AC-04": bool(matrix_complete and schema_valid),
    }
    return {
        "acceptance": acceptance,
        "runtime_verdict": "passed" if all(acceptance.values()) else "failed",
        "matrix_complete": matrix_complete,
        "profile_repetition_count": len(profiles),
        "family_rollups": family_rollups,
        "checks": {
            "distinct_metric_schemas": schema_valid,
            "quality_thresholds": quality_valid,
            "zero_oom_and_starvation": selected_safe,
            "trace_complete": traces_complete,
            "prometheus_complete": prometheus_complete,
            "bounded_over_limit_rejection": rejections_valid,
            "fairness_and_hol": fairness_valid,
        },
    }


def _quality_passes(family: Family, quality: dict[str, Any], config: S7RuntimeConfig) -> bool:
    if set(quality) != set(QUALITY_SCHEMAS[family]):
        return False
    values = {key: _finite(value, key) for key, value in quality.items()}
    if family == "image":
        return values["binary_accuracy"] >= config.image_accuracy_minimum
    if family == "vlm":
        return (
            values["accuracy"] >= config.vlm_accuracy_minimum
            and values["parse_rate"] >= config.vlm_parse_rate_minimum
        )
    return (
        values["mean_token_f1"] >= config.llm_token_f1_minimum
        and values["nonempty_rate"] >= config.llm_nonempty_rate_minimum
    )


def source_identity(root: Path) -> tuple[str, str]:
    revision = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    if len(revision) != 40 or branch != "codex/distributed-scale-validation-plan":
        raise S7RuntimeError("s7_source_identity_invalid")
    if (
        subprocess.run(["git", "diff", "--quiet"], cwd=root, check=False).returncode
        or subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root, check=False).returncode
    ):
        raise S7RuntimeError("s7_tracked_worktree_must_be_clean")
    return revision, branch


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def host_image_data_environment(data_root: Path) -> dict[str, str]:
    resolved = str(data_root.resolve())
    return {
        "EVM_HOST_DATA_ROOT": resolved,
        "EVM_DATA_MOUNT_ROOT": resolved,
    }


def restore_file_sd_target(path: Path, prior: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if prior is None:
        path.write_text("[]\n", encoding="utf-8", newline="\n")
    else:
        path.write_bytes(prior)


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(_finite(value, "percentile") for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise S7RuntimeError(f"s7_metric_invalid:{label}") from exc
    if not math.isfinite(result) or result < 0:
        raise S7RuntimeError(f"s7_metric_invalid:{label}")
    return result


def _mean(values: list[float]) -> float:
    return 0.0 if not values else float(statistics.fmean(values))


def _section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise S7RuntimeError(f"s7_config_section_missing:{name}")
    return value


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()

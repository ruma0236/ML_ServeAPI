"""Bounded, fail-closed S3 capacity-probe smoke client."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

# isort: off
# Locust must initialize before either HTTP client imports its networking stack.
from locust import HttpUser, constant_pacing, task
from locust.env import Environment
from locust.exception import StopUser
import gevent
from gevent.event import Event
import httpx
from jsonschema import Draft202012Validator
from requests.exceptions import Timeout as RequestsTimeout
# isort: on


PROFILE_SCHEMA = "evm.container_test.profile.v1"
CORPUS_SCHEMA = "evm.container_test.request_corpus.v1"
RESULT_SCHEMA = "evm.container_test.smoke_result.v1"
RUN_ID_PATTERN = re.compile(r"ct-[a-z0-9][a-z0-9-]{1,55}")
HEX40 = re.compile(r"[a-f0-9]{40}")
HEX64 = re.compile(r"[a-f0-9]{64}")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}")
LOGICAL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
TRACEPARENT = re.compile(r"00-([a-f0-9]{32})-([a-f0-9]{16})-(0[01])")

HEALTH_PATH = "/health"
CATALOG_PATH = "/control-panel/v1/scenario-workloads/capacity-probes"
PREDICT_PATH = "/control-panel/v1/scenario-workloads/capacity-probes/predict"
MAX_REQUESTS = 3
MAX_DURATION_SECONDS = 60.0
DRAIN_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 5.0
PACING_SECONDS = 20.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
KNOWN_REJECTION_STATUS_CODES = {400, 401, 403, 404, 409, 413, 422, 429}

FIXED_THRESHOLDS = {
    "s3_p99_response_ms": 250.0,
    "maximum_error_rate": 0.01,
    "maximum_generator_lag_ms": 100.0,
    "maximum_host_cpu_percent": 90.0,
    "host_cpu_observer": "parent",
}


class SmokeError(RuntimeError):
    """A bounded smoke contract failure."""

    def __init__(self, message: str, observation: dict[str, Any] | None = None):
        super().__init__(message)
        self.observation = observation


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_capacity_request(body: dict[str, Any]) -> bytes:
    """Match CapacityProbeRequest's JSON-mode normalization without importing EVM."""
    normalized = {
        "schema_version": body["schema_version"],
        "probe_family": body["probe_family"],
        "dataset_identity_sha256": body["dataset_identity_sha256"],
        "features": [float(value) for value in body["features"]],
    }
    return canonical_json(normalized)


def parse_json_bytes(value: bytes, label: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise SmokeError(f"{label}: duplicate JSON key {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise SmokeError(f"{label}: non-finite JSON number {value}")

    try:
        return json.loads(
            value.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"{label}: invalid UTF-8 JSON: {exc}") from exc


def read_bounded(path: Path, maximum: int, label: str) -> bytes:
    try:
        size = path.stat(follow_symlinks=False).st_size
    except OSError as exc:
        raise SmokeError(f"{label}: cannot stat input: {exc}") from exc
    if path.is_symlink() or not path.is_file():
        raise SmokeError(f"{label}: input must be a regular non-symlink file")
    if size < 1 or size > maximum:
        raise SmokeError(f"{label}: input size {size} outside 1..{maximum}")
    data = path.read_bytes()
    if len(data) != size:
        raise SmokeError(f"{label}: input changed while reading")
    return data


def exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise SmokeError(f"{label}: expected object")
    actual = set(value)
    if actual != keys:
        raise SmokeError(
            f"{label}: keys mismatch missing={sorted(keys - actual)} extra={sorted(actual - keys)}"
        )
    return value


def exact_string(value: Any, expected: str, label: str) -> str:
    if type(value) is not str or value != expected:
        raise SmokeError(f"{label}: expected {expected!r}")
    return value


def bounded_string(value: Any, label: str, maximum: int = 256) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise SmokeError(f"{label}: expected non-empty string <= {maximum} characters")
    return value


def exact_number(value: Any, expected: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SmokeError(f"{label}: expected number")
    if not math.isfinite(float(value)) or float(value) != expected:
        raise SmokeError(f"{label}: expected fixed value {expected}")
    return float(value)


def exact_integer(value: Any, expected: int, label: str) -> int:
    if type(value) is not int or value != expected:
        raise SmokeError(f"{label}: expected fixed integer {expected}")
    return value


def lowercase_hash(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise SmokeError(f"{label}: invalid immutable identity")
    return value


def input_path(root: Path, relative: Any, label: str) -> Path:
    name = bounded_string(relative, label, maximum=240)
    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SmokeError(f"{label}: path must be relative and contained")
    try:
        resolved = (root / candidate).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SmokeError(f"{label}: path escapes or is unavailable") from exc
    return resolved


def validate_profile(raw: Any, profile_path: Path, self_sha256: str) -> dict[str, Any]:
    root_keys = {
        "schema_version",
        "profile_id",
        "status",
        "acceptance_credit",
        "blockers",
        "target",
        "request_contract",
        "frozen_inputs",
        "expected_runtime",
        "load",
        "runtime_constraints",
        "acceptance",
        "provenance",
        "deferred_windows_gates",
    }
    profile = exact_keys(raw, root_keys, "profile")
    exact_string(profile["schema_version"], PROFILE_SCHEMA, "profile.schema_version")
    bounded_string(profile["profile_id"], "profile.profile_id", 96)
    exact_string(profile["status"], "READY", "profile.status")
    if type(profile["acceptance_credit"]) is not bool:
        raise SmokeError("profile.acceptance_credit: expected boolean")
    if profile["blockers"] != []:
        raise SmokeError("profile.blockers: READY profile must have no blockers")

    target = exact_keys(
        profile["target"],
        {
            "network",
            "base_url",
            "health_path",
            "catalog_path",
            "predict_path",
            "authentication",
        },
        "profile.target",
    )
    network = target["network"]
    if network not in {"none", "evm-local"}:
        raise SmokeError("profile.target.network: expected none or evm-local")
    base_url = bounded_string(target["base_url"], "profile.target.base_url", 256)
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
        or parsed_url.path not in {"", "/"}
    ):
        raise SmokeError("profile.target.base_url: expected an HTTP(S) origin")
    if network == "none" and parsed_url.hostname not in {"127.0.0.1", "localhost"}:
        raise SmokeError("profile.target.base_url: network-none fixture must be loopback")
    if network == "evm-local" and base_url != "http://evm-api:8000":
        raise SmokeError("profile.target.base_url: evm-local target must be canonical")
    exact_string(target["health_path"], HEALTH_PATH, "profile.target.health_path")
    exact_string(target["catalog_path"], CATALOG_PATH, "profile.target.catalog_path")
    exact_string(target["predict_path"], PREDICT_PATH, "profile.target.predict_path")
    auth = exact_keys(
        target["authentication"],
        {"mode", "credentials_embedded"},
        "profile.target.authentication",
    )
    exact_string(auth["mode"], "none", "profile.target.authentication.mode")
    if auth["credentials_embedded"] is not False:
        raise SmokeError("profile.target.authentication: credentials must not be embedded")
    if network == "evm-local" and profile["acceptance_credit"] is not True:
        raise SmokeError("profile.acceptance_credit: evm-local READY profile requires true")
    if network == "none" and profile["acceptance_credit"] is not False:
        raise SmokeError("profile.acceptance_credit: fixture profile must be zero-credit")

    contract = exact_keys(
        profile["request_contract"],
        {
            "schema_version",
            "probe_family",
            "feature_count",
            "terminal_semantics",
            "job_status_polling",
            "idempotency",
            "effects",
            "asynchronous_workflow",
        },
        "profile.request_contract",
    )
    exact_string(
        contract["schema_version"],
        "evm.s3_capacity_probe_request.v1",
        "profile.request_contract.schema_version",
    )
    if contract["probe_family"] not in {
        "logistic",
        "probabilistic",
        "online-linear",
        "branch-heavy",
        "incremental",
    }:
        raise SmokeError("profile.request_contract.probe_family: invalid family")
    exact_integer(contract["feature_count"], 28, "profile.request_contract.feature_count")
    exact_string(
        contract["terminal_semantics"],
        "synchronous_http_response",
        "profile.request_contract.terminal_semantics",
    )
    for key in ("job_status_polling", "idempotency", "effects"):
        exact_string(contract[key], "not_applicable", f"profile.request_contract.{key}")
    exact_string(
        contract["asynchronous_workflow"],
        "NOT_RUN",
        "profile.request_contract.asynchronous_workflow",
    )

    frozen = exact_keys(
        profile["frozen_inputs"],
        {
            "openapi_path",
            "openapi_sha256",
            "catalog_path",
            "catalog_sha256",
            "catalog_canonical_sha256",
            "request_corpus_path",
            "request_corpus_sha256",
            "dataset_identity_sha256",
            "model_identity_sha256",
            "artifact_sha256",
        },
        "profile.frozen_inputs",
    )
    for key in (
        "openapi_sha256",
        "catalog_sha256",
        "catalog_canonical_sha256",
        "request_corpus_sha256",
        "dataset_identity_sha256",
        "model_identity_sha256",
        "artifact_sha256",
    ):
        lowercase_hash(frozen[key], HEX64, f"profile.frozen_inputs.{key}")
    for key in ("openapi_path", "catalog_path", "request_corpus_path"):
        bounded_string(frozen[key], f"profile.frozen_inputs.{key}", 240)

    expected_runtime = exact_keys(
        profile["expected_runtime"],
        {"api_replica_ids", "cpu_worker_count", "worker_slots"},
        "profile.expected_runtime",
    )
    replicas = expected_runtime["api_replica_ids"]
    if (
        type(replicas) is not list
        or not replicas
        or len(replicas) > 16
        or any(type(item) is not str or not 1 <= len(item) <= 64 for item in replicas)
        or len(set(replicas)) != len(replicas)
    ):
        raise SmokeError("profile.expected_runtime.api_replica_ids: invalid exact set")
    workers = expected_runtime["cpu_worker_count"]
    if type(workers) is not int or not 1 <= workers <= 64:
        raise SmokeError("profile.expected_runtime.cpu_worker_count: invalid")
    slots = expected_runtime["worker_slots"]
    if (
        type(slots) is not list
        or not slots
        or any(type(item) is not int or not 0 <= item < workers for item in slots)
        or len(set(slots)) != len(slots)
    ):
        raise SmokeError("profile.expected_runtime.worker_slots: invalid exact set")

    load = exact_keys(
        profile["load"],
        {
            "arrival_model",
            "concurrency",
            "maximum_request_count",
            "maximum_duration_seconds",
            "drain_timeout_seconds",
            "request_timeout_seconds",
            "pacing_seconds",
            "fixed_arrival_rate_claim",
            "claim_boundary",
        },
        "profile.load",
    )
    exact_string(load["arrival_model"], "closed_concurrency", "profile.load.arrival_model")
    exact_integer(load["concurrency"], 1, "profile.load.concurrency")
    request_count = load["maximum_request_count"]
    if type(request_count) is not int or not 1 <= request_count <= MAX_REQUESTS:
        raise SmokeError(f"profile.load.maximum_request_count: expected integer 1..{MAX_REQUESTS}")
    exact_number(
        load["maximum_duration_seconds"],
        MAX_DURATION_SECONDS,
        "profile.load.maximum_duration_seconds",
    )
    exact_number(
        load["drain_timeout_seconds"], DRAIN_TIMEOUT_SECONDS, "profile.load.drain_timeout_seconds"
    )
    exact_number(
        load["request_timeout_seconds"],
        REQUEST_TIMEOUT_SECONDS,
        "profile.load.request_timeout_seconds",
    )
    exact_number(load["pacing_seconds"], PACING_SECONDS, "profile.load.pacing_seconds")
    if load["fixed_arrival_rate_claim"] is not False:
        raise SmokeError("profile.load.fixed_arrival_rate_claim: must be false")
    bounded_string(load["claim_boundary"], "profile.load.claim_boundary", 256)

    constraints = exact_keys(
        profile["runtime_constraints"],
        {
            "cpus",
            "memory_mib",
            "pids_limit",
            "read_only_rootfs",
            "cap_drop",
            "no_new_privileges",
            "runtime_user",
            "code_mount",
            "input_mount",
            "output_mount",
        },
        "profile.runtime_constraints",
    )
    exact_integer(constraints["cpus"], 1, "profile.runtime_constraints.cpus")
    exact_integer(constraints["memory_mib"], 512, "profile.runtime_constraints.memory_mib")
    exact_integer(constraints["pids_limit"], 128, "profile.runtime_constraints.pids_limit")
    if constraints["read_only_rootfs"] is not True or constraints["no_new_privileges"] is not True:
        raise SmokeError("profile.runtime_constraints: isolation booleans must be true")
    for key, expected in {
        "cap_drop": "ALL",
        "runtime_user": "1000:1000",
        "code_mount": "read_only",
        "input_mount": "read_only",
        "output_mount": "write_only_by_contract",
    }.items():
        exact_string(constraints[key], expected, f"profile.runtime_constraints.{key}")

    acceptance = exact_keys(profile["acceptance"], set(FIXED_THRESHOLDS), "profile.acceptance")
    for key, expected in FIXED_THRESHOLDS.items():
        if isinstance(expected, str):
            exact_string(acceptance[key], expected, f"profile.acceptance.{key}")
        else:
            exact_number(acceptance[key], expected, f"profile.acceptance.{key}")

    provenance = exact_keys(
        profile["provenance"],
        {"source_commit", "source_tree", "smoke_sha256", "target_image_id"},
        "profile.provenance",
    )
    lowercase_hash(provenance["source_commit"], HEX40, "profile.provenance.source_commit")
    lowercase_hash(provenance["source_tree"], HEX40, "profile.provenance.source_tree")
    expected_smoke = lowercase_hash(
        provenance["smoke_sha256"], HEX64, "profile.provenance.smoke_sha256"
    )
    if expected_smoke != self_sha256:
        raise SmokeError("profile.provenance.smoke_sha256: running code hash mismatch")
    lowercase_hash(provenance["target_image_id"], IMAGE_ID, "profile.provenance.target_image_id")

    gates = profile["deferred_windows_gates"]
    expected_gate_ids = {
        "windows_etw_collector",
        "windows_wsl_dual_collector",
        "windows_high_integrity_token",
    }
    if type(gates) is not list or len(gates) != len(expected_gate_ids):
        raise SmokeError("profile.deferred_windows_gates: exact three gates required")
    observed_gate_ids: set[str] = set()
    for index, gate in enumerate(gates):
        item = exact_keys(gate, {"gate_id", "status"}, f"profile.deferred_windows_gates[{index}]")
        if item["gate_id"] not in expected_gate_ids or item["gate_id"] in observed_gate_ids:
            raise SmokeError("profile.deferred_windows_gates: invalid or duplicate gate")
        exact_string(item["status"], "NOT_RUN", f"profile.deferred_windows_gates[{index}].status")
        observed_gate_ids.add(item["gate_id"])

    profile["_profile_root"] = profile_path.parent.resolve(strict=True)
    return profile


def openapi_validator(document: Any, name: str) -> Draft202012Validator:
    doc = exact_keys(document, set(document) if type(document) is dict else set(), "openapi")
    components = doc.get("components")
    if type(components) is not dict or type(components.get("schemas")) is not dict:
        raise SmokeError("openapi: components.schemas missing")
    if name not in components["schemas"]:
        raise SmokeError(f"openapi: required schema {name} missing")
    root = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{name}",
        "components": components,
    }
    try:
        Draft202012Validator.check_schema(root)
    except Exception as exc:
        raise SmokeError(f"openapi: schema {name} is invalid: {type(exc).__name__}") from exc
    return Draft202012Validator(root)


def schema_errors(validator: Draft202012Validator, value: Any) -> list[str]:
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in errors[:16]
    ]


def load_inputs(profile: dict[str, Any]) -> dict[str, Any]:
    root: Path = profile["_profile_root"]
    frozen = profile["frozen_inputs"]
    specs = {
        "openapi": (frozen["openapi_path"], frozen["openapi_sha256"], 16 * 1024 * 1024),
        "catalog": (frozen["catalog_path"], frozen["catalog_sha256"], 4 * 1024 * 1024),
        "corpus": (
            frozen["request_corpus_path"],
            frozen["request_corpus_sha256"],
            4 * 1024 * 1024,
        ),
    }
    result: dict[str, Any] = {"files": {}}
    for label, (relative, expected_hash, maximum) in specs.items():
        path = input_path(root, relative, f"frozen {label}")
        raw = read_bounded(path, maximum, f"frozen {label}")
        actual_hash = sha256_bytes(raw)
        if actual_hash != expected_hash:
            raise SmokeError(f"frozen {label}: SHA-256 mismatch")
        result[label] = parse_json_bytes(raw, f"frozen {label}")
        result["files"][label] = {
            "path": path.relative_to(root).as_posix(),
            "bytes": len(raw),
            "sha256": actual_hash,
        }

    validators = {
        name: openapi_validator(result["openapi"], name)
        for name in ("CapacityProbeRequest", "CapacityProbeResponse", "CapacityProbeCatalog")
    }
    catalog_errors = schema_errors(validators["CapacityProbeCatalog"], result["catalog"])
    if catalog_errors:
        raise SmokeError(f"frozen catalog: OpenAPI validation failed: {catalog_errors[0]}")
    catalog_canonical = sha256_bytes(canonical_json(result["catalog"]))
    if catalog_canonical != frozen["catalog_canonical_sha256"]:
        raise SmokeError("frozen catalog: canonical SHA-256 mismatch")

    corpus = exact_keys(result["corpus"], {"schema_version", "requests"}, "request corpus")
    exact_string(corpus["schema_version"], CORPUS_SCHEMA, "request corpus.schema_version")
    requests = corpus["requests"]
    planned_requests = profile["load"]["maximum_request_count"]
    if type(requests) is not list or len(requests) != planned_requests:
        raise SmokeError("request corpus.requests: count must equal profile maximum_request_count")
    logical_ids: set[str] = set()
    for index, entry in enumerate(requests):
        item = exact_keys(
            entry, {"logical_request_id", "body"}, f"request corpus.requests[{index}]"
        )
        logical_id = item["logical_request_id"]
        if type(logical_id) is not str or LOGICAL_ID.fullmatch(logical_id) is None:
            raise SmokeError(f"request corpus.requests[{index}]: invalid logical_request_id")
        if logical_id in logical_ids:
            raise SmokeError("request corpus: duplicate logical_request_id")
        logical_ids.add(logical_id)
        body = exact_keys(
            item["body"],
            {"schema_version", "probe_family", "dataset_identity_sha256", "features"},
            f"request corpus.requests[{index}].body",
        )
        errors = schema_errors(validators["CapacityProbeRequest"], body)
        if errors:
            raise SmokeError(f"request corpus.requests[{index}]: {errors[0]}")
        if body["schema_version"] != profile["request_contract"]["schema_version"]:
            raise SmokeError("request corpus: request schema version mismatch")
        if body["probe_family"] != profile["request_contract"]["probe_family"]:
            raise SmokeError("request corpus: probe family mismatch")
        if body["dataset_identity_sha256"] != frozen["dataset_identity_sha256"]:
            raise SmokeError("request corpus: dataset identity mismatch")
        features = body["features"]
        if (
            type(features) is not list
            or len(features) != 28
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in features
            )
        ):
            raise SmokeError("request corpus: features must be 28 finite numbers")

    catalog = result["catalog"]
    if catalog.get("schema_version") != "evm.s3_capacity_probe_catalog.v1":
        raise SmokeError("frozen catalog: schema version mismatch")
    if catalog["dataset_identity_sha256"] != frozen["dataset_identity_sha256"]:
        raise SmokeError("frozen catalog: dataset identity mismatch")
    matches = [
        item
        for item in catalog["probes"]
        if item.get("probe_family") == profile["request_contract"]["probe_family"]
    ]
    if len(matches) != 1:
        raise SmokeError("frozen catalog: expected exactly one selected family")
    descriptor = matches[0]
    if descriptor["model_identity_sha256"] != frozen["model_identity_sha256"]:
        raise SmokeError("frozen catalog: model identity mismatch")
    if descriptor["artifact_sha256"] != frozen["artifact_sha256"]:
        raise SmokeError("frozen catalog: artifact identity mismatch")
    result["validators"] = validators
    result["catalog_canonical_sha256"] = catalog_canonical
    return result


def trace_context(run_id: str, logical_id: str) -> tuple[str, str]:
    trace_id = hashlib.sha256(f"{run_id}:{logical_id}:trace".encode()).hexdigest()[:32]
    span_id = hashlib.sha256(f"{run_id}:{logical_id}:span".encode()).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01", trace_id


def decode_response_json(content: bytes, label: str) -> Any:
    if len(content) > MAX_RESPONSE_BYTES:
        raise SmokeError(f"{label}: response exceeds {MAX_RESPONSE_BYTES} bytes")
    return parse_json_bytes(content, label)


def timed_get(
    client: httpx.Client,
    path: str,
    run_id: str,
    logical_id: str,
    deadline: float,
) -> tuple[dict[str, Any], Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SmokeError(f"{logical_id}: hard duration exhausted before request")
    timeout = min(REQUEST_TIMEOUT_SECONDS, remaining)
    traceparent, trace_id = trace_context(run_id, logical_id)
    started_ns = time.monotonic_ns()
    started_utc = utc_now()
    try:
        response = client.get(
            path,
            headers={"traceparent": traceparent, "x-evm-s3-run-id": run_id},
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise SmokeError(f"{logical_id}: timeout is UNKNOWN: {type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        raise SmokeError(f"{logical_id}: transport failure: {type(exc).__name__}") from exc
    completed_ns = time.monotonic_ns()
    original = {
        "logical_request_id": logical_id,
        "path": path,
        "status_code": response.status_code,
        "body_sha256": sha256_bytes(response.content),
        "body_bytes": len(response.content),
        "body_base64": base64.b64encode(response.content[:MAX_RESPONSE_BYTES]).decode("ascii"),
        "body_truncated": len(response.content) > MAX_RESPONSE_BYTES,
    }
    if response.status_code != 200:
        raise SmokeError(f"{logical_id}: HTTP {response.status_code}", original)
    try:
        payload = decode_response_json(response.content, logical_id)
    except SmokeError as exc:
        raise SmokeError(str(exc), original) from exc
    response_trace = response.headers.get("x-evm-trace-id")
    response_traceparent = response.headers.get("traceparent", "")
    trace_match = TRACEPARENT.fullmatch(response_traceparent)
    if response_trace != trace_id or trace_match is None or trace_match.group(1) != trace_id:
        raise SmokeError(f"{logical_id}: trace identity was not echoed", original)
    return (
        {
            **original,
            "logical_request_id": logical_id,
            "started_utc": started_utc,
            "started_monotonic_ns": started_ns,
            "completed_monotonic_ns": completed_ns,
            "duration_ms": (completed_ns - started_ns) / 1_000_000,
            "status_code": response.status_code,
            "body_bytes": len(response.content),
            "body_sha256": sha256_bytes(response.content),
            "trace_id": trace_id,
            "response_trace_id": response_trace,
            "trace_identity_matches": True,
        },
        payload,
    )


def cgroup_snapshot() -> dict[str, Any]:
    def integer(path: str) -> int | None:
        try:
            value = Path(path).read_text(encoding="ascii").strip()
            return None if value == "max" else int(value)
        except (OSError, ValueError):
            return None

    usage_ns = None
    source = None
    try:
        cpu_stat = Path("/sys/fs/cgroup/cpu.stat").read_text(encoding="ascii")
        values = dict(line.split() for line in cpu_stat.splitlines() if len(line.split()) == 2)
        if "usage_usec" in values:
            usage_ns = int(values["usage_usec"]) * 1_000
            source = "cgroup_v2"
    except (OSError, ValueError):
        pass
    if usage_ns is None:
        usage_ns = integer("/sys/fs/cgroup/cpuacct/cpuacct.usage")
        if usage_ns is not None:
            source = "cgroup_v1"
    memory_current = integer("/sys/fs/cgroup/memory.current")
    memory_peak = integer("/sys/fs/cgroup/memory.peak")
    memory_limit = integer("/sys/fs/cgroup/memory.max")
    if memory_current is None:
        memory_current = integer("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        memory_peak = integer("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")
        memory_limit = integer("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    return {
        "observed_utc": utc_now(),
        "observed_monotonic_ns": time.monotonic_ns(),
        "source": source,
        "cpu_usage_ns": usage_ns,
        "memory_current_bytes": memory_current,
        "memory_kernel_peak_bytes": memory_peak,
        "memory_limit_bytes": memory_limit,
    }


class ResourceSampler:
    def __init__(self) -> None:
        self.before = cgroup_snapshot()
        self.after: dict[str, Any] | None = None
        self.sample_count = 1
        self.sampled_memory_peak = self.before["memory_current_bytes"]
        self._stop = Event()
        self._greenlet = gevent.spawn(self._run)

    def _run(self) -> None:
        while not self._stop.wait(timeout=0.1):
            sample = cgroup_snapshot()
            self.sample_count += 1
            current = sample["memory_current_bytes"]
            if current is not None and (
                self.sampled_memory_peak is None or current > self.sampled_memory_peak
            ):
                self.sampled_memory_peak = current

    def close(self) -> dict[str, Any]:
        self._stop.set()
        self._greenlet.join(timeout=1)
        self.after = cgroup_snapshot()
        self.sample_count += 1
        before_cpu = self.before["cpu_usage_ns"]
        after_cpu = self.after["cpu_usage_ns"]
        return {
            "available": before_cpu is not None or self.before["memory_current_bytes"] is not None,
            "before": self.before,
            "after": self.after,
            "sample_count": self.sample_count,
            "sampled_memory_peak_bytes": self.sampled_memory_peak,
            "cpu_usage_delta_ns": (
                after_cpu - before_cpu
                if before_cpu is not None and after_cpu is not None and after_cpu >= before_cpu
                else None
            ),
        }


class RunState:
    def __init__(self, profile: dict[str, Any], inputs: dict[str, Any], run_id: str) -> None:
        self.profile = profile
        self.inputs = inputs
        self.run_id = run_id
        self.planned_requests = profile["load"]["maximum_request_count"]
        self.records: list[dict[str, Any]] = []
        self.next_request = 0
        self.in_flight = 0
        self.load_started = time.monotonic()
        self.load_started_ns = time.monotonic_ns()
        self.completed = Event()
        self.stop_requested = Event()
        self.stop_reason: str | None = None

    def request_stop(self, reason: str) -> None:
        if not self.stop_requested.is_set():
            self.stop_reason = reason
            self.stop_requested.set()

    def perform(self, user: HttpUser) -> None:
        if self.stop_requested.is_set() or self.next_request >= self.planned_requests:
            self.completed.set()
            raise StopUser()
        index = self.next_request
        self.next_request += 1
        entry = self.inputs["corpus"]["requests"][index]
        logical_id = entry["logical_request_id"]
        body = entry["body"]
        scheduled = self.load_started + index * PACING_SECONDS
        started = time.monotonic()
        started_ns = time.monotonic_ns()
        response_received_ns: int | None = None
        traceparent, trace_id = trace_context(self.run_id, logical_id)
        record: dict[str, Any] = {
            "request_index": index,
            "logical_request_id": logical_id,
            "attempted": True,
            "accepted": False,
            "rejected": False,
            "succeeded": False,
            "failed": False,
            "unknown": False,
            "terminal_status": "unknown",
            "scheduled_offset_ms": index * PACING_SECONDS * 1000,
            "actual_issuance_offset_ms": (started - self.load_started) * 1000,
            "generator_lag_ms": max(0.0, (started - scheduled) * 1000),
            "started_utc": utc_now(),
            "started_monotonic_ns": started_ns,
            "request_body_sha256": sha256_bytes(canonical_capacity_request(body)),
            "logical_request_headers": {
                "traceparent": traceparent,
                "x-evm-s3-run-id": self.run_id,
            },
            "status_code": None,
            "transport_error": None,
            "response_body_bytes": None,
            "response_body_sha256": None,
            "response_body_capture": None,
            "response_headers": {},
            "response_trace_id": None,
            "trace_identity_matches": False,
            "validation_errors": [],
            "server_timings_ms": {},
            "runtime": {},
        }
        self.in_flight += 1
        try:
            with user.client.post(
                self.profile["target"]["predict_path"],
                json=body,
                headers={"traceparent": traceparent, "x-evm-s3-run-id": self.run_id},
                timeout=REQUEST_TIMEOUT_SECONDS,
                catch_response=True,
                name=f"POST {PREDICT_PATH}",
            ) as response:
                response_received_ns = time.monotonic_ns()
                content = bytes(getattr(response, "content", b""))
                record["status_code"] = getattr(response, "status_code", None)
                record["response_body_bytes"] = len(content)
                record["response_body_sha256"] = sha256_bytes(content)
                captured = content[:MAX_RESPONSE_BYTES]
                record["response_body_capture"] = {
                    "encoding": "base64",
                    "captured_bytes": len(captured),
                    "truncated": len(captured) != len(content),
                    "value": base64.b64encode(captured).decode("ascii"),
                }
                record["response_headers"] = {
                    name: response.headers[name]
                    for name in (
                        "content-type",
                        "traceparent",
                        "x-evm-trace-id",
                        "retry-after",
                    )
                    if name in response.headers
                }
                transport_error = getattr(response, "error", None)
                if transport_error is not None:
                    record["transport_error"] = type(transport_error).__name__
                    record["unknown"] = True
                    record["terminal_status"] = "unknown"
                    if (
                        isinstance(transport_error, RequestsTimeout)
                        or "timeout" in type(transport_error).__name__.lower()
                    ):
                        record["validation_errors"].append("request_timeout_unknown")
                    else:
                        record["validation_errors"].append("transport_outcome_unknown")
                    response.failure(record["validation_errors"][0])
                elif response.status_code in KNOWN_REJECTION_STATUS_CODES:
                    record["rejected"] = True
                    record["failed"] = True
                    record["terminal_status"] = "rejected"
                    record["validation_errors"].append(f"http_status_{response.status_code}")
                    response.failure(record["validation_errors"][0])
                elif response.status_code != 200:
                    record["accepted"] = response.status_code == 202
                    record["unknown"] = True
                    record["terminal_status"] = "unknown"
                    record["validation_errors"].append(
                        f"unexpected_http_status_{response.status_code}_unknown"
                    )
                    response.failure(record["validation_errors"][0])
                else:
                    record["accepted"] = True
                    try:
                        payload = decode_response_json(content, f"response {logical_id}")
                        errors = schema_errors(
                            self.inputs["validators"]["CapacityProbeResponse"], payload
                        )
                        content_type = response.headers.get("content-type", "")
                        if content_type.split(";", 1)[0].strip().lower() != "application/json":
                            errors.append("response_content_type_mismatch")
                        frozen = self.profile["frozen_inputs"]
                        runtime_expected = self.profile["expected_runtime"]
                        if (
                            not errors
                            and payload.get("schema_version") != "evm.s3_capacity_probe_response.v1"
                        ):
                            errors.append("response_schema_version_mismatch")
                        if not errors and payload["probe_family"] != body["probe_family"]:
                            errors.append("probe_family_mismatch")
                        if (
                            not errors
                            and payload["dataset_identity_sha256"]
                            != frozen["dataset_identity_sha256"]
                        ):
                            errors.append("dataset_identity_mismatch")
                        if (
                            not errors
                            and payload["model_identity_sha256"] != frozen["model_identity_sha256"]
                        ):
                            errors.append("model_identity_mismatch")
                        runtime = payload.get("runtime") if type(payload) is dict else None
                        if type(runtime) is not dict:
                            errors.append("runtime_missing")
                        else:
                            record["runtime"] = runtime
                            if (
                                runtime.get("api_replica_id")
                                not in runtime_expected["api_replica_ids"]
                            ):
                                errors.append("runtime_api_replica_id_mismatch")
                            if (
                                runtime.get("cpu_worker_count")
                                != runtime_expected["cpu_worker_count"]
                            ):
                                errors.append("runtime_cpu_worker_count_mismatch")
                            if runtime.get("worker_slot") not in runtime_expected["worker_slots"]:
                                errors.append("runtime_worker_slot_mismatch")
                            if runtime.get("canonical_request_bytes") != len(
                                canonical_capacity_request(body)
                            ):
                                errors.append("runtime_canonical_request_bytes_mismatch")
                        timings = payload.get("timings") if type(payload) is dict else None
                        if type(timings) is dict:
                            record["server_timings_ms"] = timings
                            if timings.get("total_ms", -1) < timings.get("prediction_ms", 0):
                                errors.append("server_total_before_prediction")
                            if timings.get("compute_ms", -1) < timings.get("prediction_ms", 0):
                                errors.append("server_compute_before_prediction")
                        response_trace = response.headers.get("x-evm-trace-id")
                        response_traceparent = response.headers.get("traceparent", "")
                        trace_match = TRACEPARENT.fullmatch(response_traceparent)
                        record["response_trace_id"] = response_trace
                        record["trace_identity_matches"] = bool(
                            response_trace == trace_id
                            and trace_match is not None
                            and trace_match.group(1) == trace_id
                        )
                        if not record["trace_identity_matches"]:
                            errors.append("trace_identity_mismatch")
                        record["validation_errors"] = errors
                    except Exception as exc:
                        record["validation_errors"].append(
                            f"response_validation_exception:{type(exc).__name__}"
                        )
                    if record["validation_errors"]:
                        record["failed"] = True
                        record["terminal_status"] = "failed"
                        response.failure(record["validation_errors"][0])
                    else:
                        record["succeeded"] = True
                        record["terminal_status"] = "succeeded"
                        response.success()
        except RequestsTimeout as exc:
            response_received_ns = time.monotonic_ns()
            record["transport_error"] = type(exc).__name__
            record["unknown"] = True
            record["terminal_status"] = "unknown"
            record["validation_errors"].append("request_timeout_unknown")
        except Exception as exc:
            response_received_ns = time.monotonic_ns()
            record["transport_error"] = type(exc).__name__
            record["unknown"] = True
            record["terminal_status"] = "unknown"
            record["validation_errors"].append(
                f"request_exception_outcome_unknown:{type(exc).__name__}"
            )
        finally:
            completed_ns = time.monotonic_ns()
            if response_received_ns is None:
                response_received_ns = completed_ns
            response_duration_ms = (response_received_ns - started_ns) / 1_000_000
            end_to_end_duration_ms = (completed_ns - started_ns) / 1_000_000
            record["completed_utc"] = utc_now()
            record["response_received_monotonic_ns"] = response_received_ns
            record["completed_monotonic_ns"] = completed_ns
            record["response_duration_ms"] = response_duration_ms
            record["end_to_end_duration_ms"] = end_to_end_duration_ms
            record["local_validation_duration_ms"] = max(
                0.0, end_to_end_duration_ms - response_duration_ms
            )
            record["sync_latency_relation"] = (
                "response is synchronous; end-to-end additionally includes local contract validation"
            )
            self.records.append(record)
            self.in_flight -= 1
            if len(self.records) >= self.planned_requests:
                self.completed.set()
        if self.stop_requested.is_set() or self.completed.is_set():
            raise StopUser()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "maximum": max(values) if values else None,
    }


def build_metrics(records: list[dict[str, Any]], load_window: float) -> dict[str, Any]:
    response = [float(item["response_duration_ms"]) for item in records]
    end_to_end = [float(item["end_to_end_duration_ms"]) for item in records]
    lags = [float(item["generator_lag_ms"]) for item in records]
    timing_names = (
        "admission_wait_ms",
        "queue_wait_ms",
        "validation_ms",
        "transform_ms",
        "prediction_ms",
        "compute_ms",
        "total_ms",
    )
    server = {
        name: distribution(
            [
                float(item["server_timings_ms"][name])
                for item in records
                if name in item["server_timings_ms"]
            ]
        )
        for name in timing_names
    }
    return {
        "response_latency_ms": distribution(response),
        "end_to_end_latency_ms": distribution(end_to_end),
        "synchronous_latency_definition": (
            "response ends at HTTP receipt; end-to-end ends after local contract validation"
        ),
        "server_timings_ms": server,
        "throughput": {
            "measurement_window_seconds": load_window,
            "attempts_per_second": len(records) / load_window if load_window > 0 else None,
            "succeeded_per_second": (
                sum(bool(item["succeeded"]) for item in records) / load_window
                if load_window > 0
                else None
            ),
        },
        "generator": {
            "arrival_model": "closed_concurrency",
            "pacing_seconds": PACING_SECONDS,
            "fixed_arrival_rate_claim": False,
            "actual_issuance_offset_ms": [item["actual_issuance_offset_ms"] for item in records],
            "pacing_lag_ms": distribution(lags),
        },
    }


def execute(
    profile: dict[str, Any], inputs: dict[str, Any], run_id: str, signal_state: dict[str, Any]
) -> dict[str, Any]:
    started = time.monotonic()
    hard_deadline = started + MAX_DURATION_SECONDS
    resources = ResourceSampler()
    state = RunState(profile, inputs, run_id)
    preflight: dict[str, Any] = {}
    runner = None
    drain_started = None
    locust_stats: dict[str, Any] = {}
    execution_errors: list[str] = []
    try:
        with httpx.Client(
            base_url=profile["target"]["base_url"],
            follow_redirects=False,
            trust_env=False,
        ) as client:
            health_record, health = timed_get(
                client, HEALTH_PATH, run_id, "preflight-health", hard_deadline
            )
            if type(health) is not dict or set(health) != {"status", "service"}:
                raise SmokeError("preflight health: exact status/service object required")
            if (
                health["status"] != "ok"
                or type(health["service"]) is not str
                or not health["service"]
            ):
                raise SmokeError("preflight health: service is not healthy")
            health_record["contract_pass"] = True
            preflight["health"] = health_record
            if signal_state["name"] is not None:
                raise SmokeError(f"signal received during health preflight: {signal_state['name']}")

            catalog_record, live_catalog = timed_get(
                client, CATALOG_PATH, run_id, "preflight-catalog", hard_deadline
            )
            catalog_errors = schema_errors(
                inputs["validators"]["CapacityProbeCatalog"], live_catalog
            )
            live_catalog_sha = sha256_bytes(canonical_json(live_catalog))
            if catalog_errors:
                raise SmokeError(f"preflight catalog schema: {catalog_errors[0]}")
            if live_catalog != inputs["catalog"]:
                raise SmokeError("preflight catalog: frozen object mismatch")
            if live_catalog_sha != profile["frozen_inputs"]["catalog_canonical_sha256"]:
                raise SmokeError("preflight catalog: frozen canonical SHA-256 mismatch")
            catalog_record.update(
                {"contract_pass": True, "canonical_sha256": live_catalog_sha, "frozen_equal": True}
            )
            preflight["catalog_before"] = catalog_record
            if signal_state["name"] is not None or time.monotonic() >= hard_deadline:
                raise SmokeError("stop requested before Locust load start")

            class CapacityProbeUser(HttpUser):
                host = profile["target"]["base_url"]
                wait_time = constant_pacing(PACING_SECONDS)

                def on_start(self) -> None:
                    self.client.trust_env = False

                @task
                def predict(self) -> None:
                    state.perform(self)

            environment = Environment(
                user_classes=[CapacityProbeUser],
                catch_exceptions=False,
                stop_timeout=DRAIN_TIMEOUT_SECONDS,
            )
            runner = environment.create_local_runner()
            state.load_started = time.monotonic()
            state.load_started_ns = time.monotonic_ns()
            runner.start(user_count=1, spawn_rate=1)
            spawn_observed = False
            spawn_deadline = min(hard_deadline, time.monotonic() + REQUEST_TIMEOUT_SECONDS)
            while not state.completed.is_set() and not state.stop_requested.is_set():
                if runner.user_count > 0:
                    spawn_observed = True
                if signal_state["name"] is not None:
                    state.request_stop(f"signal:{signal_state['name']}")
                    break
                if time.monotonic() >= hard_deadline:
                    state.request_stop("hard_duration_exhausted")
                    break
                if spawn_observed and runner.user_count == 0:
                    state.request_stop("locust_user_exited_early")
                    break
                if not spawn_observed and time.monotonic() >= spawn_deadline:
                    state.request_stop("locust_user_spawn_timeout")
                    break
                gevent.sleep(0.02)

            drain_started = time.monotonic()
            while state.in_flight and time.monotonic() - drain_started < DRAIN_TIMEOUT_SECONDS:
                gevent.sleep(0.02)
            if state.in_flight:
                execution_errors.append("drain_timeout_with_request_in_flight")
            runner.stop()
            runner.quit()
            runner.greenlet.join(timeout=1)
            total = environment.stats.total
            locust_stats = {
                "request_count": total.num_requests,
                "failure_count": total.num_failures,
                "current_user_count": runner.user_count,
            }

            if signal_state["name"] is not None:
                state.request_stop(f"signal:{signal_state['name']}")
            if time.monotonic() >= hard_deadline:
                state.request_stop("hard_duration_exhausted")
            if not state.stop_requested.is_set():
                final_record, final_catalog = timed_get(
                    client, CATALOG_PATH, run_id, "postflight-catalog", hard_deadline
                )
                final_errors = schema_errors(
                    inputs["validators"]["CapacityProbeCatalog"], final_catalog
                )
                final_sha = sha256_bytes(canonical_json(final_catalog))
                stable = (
                    final_catalog == inputs["catalog"]
                    and final_sha == inputs["catalog_canonical_sha256"]
                )
                final_record.update(
                    {
                        "contract_pass": not final_errors,
                        "canonical_sha256": final_sha,
                        "frozen_equal": final_catalog == inputs["catalog"],
                        "stable": stable,
                    }
                )
                preflight["catalog_after"] = final_record
                if final_errors or not stable:
                    execution_errors.append("postflight_catalog_changed_or_invalid")
    except Exception as exc:
        execution_errors.append(f"execution_exception:{type(exc).__name__}:{str(exc)[:300]}")
        if getattr(exc, "observation", None) is not None:
            preflight["failed_observation"] = exc.observation
        state.request_stop("execution_exception")
    finally:
        if runner is not None:
            try:
                runner.stop()
                runner.quit()
            except Exception as exc:
                execution_errors.append(f"runner_cleanup:{type(exc).__name__}")
        resource_result = resources.close()

    finished = time.monotonic()
    drain_seconds = (finished - drain_started) if drain_started is not None else 0.0
    records = sorted(state.records, key=lambda item: item["request_index"])
    planned_requests = profile["load"]["maximum_request_count"]
    counts = {
        "planned": planned_requests,
        "attempts": len(records),
        "accepted": sum(bool(item["accepted"]) for item in records),
        "rejected": sum(bool(item["rejected"]) for item in records),
        "succeeded": sum(bool(item["succeeded"]) for item in records),
        "failed": sum(bool(item["failed"]) for item in records),
        "unknown": sum(bool(item["unknown"]) for item in records),
        "missing": planned_requests - len(records),
        "skipped": 0,
        "deselected": 0,
    }
    load_window = (
        (records[-1]["completed_monotonic_ns"] - state.load_started_ns) / 1_000_000_000
        if records
        else 0.0
    )
    metrics = build_metrics(records, load_window)
    p99 = metrics["response_latency_ms"]["p99"]
    max_lag = metrics["generator"]["pacing_lag_ms"]["maximum"]
    error_rate = (
        (counts["failed"] + counts["unknown"]) / counts["attempts"] if counts["attempts"] else 1.0
    )
    checks = {
        "preflight_health": preflight.get("health", {}).get("contract_pass") is True,
        "catalog_before_frozen": preflight.get("catalog_before", {}).get("frozen_equal") is True,
        "catalog_after_stable": preflight.get("catalog_after", {}).get("stable") is True,
        "exact_request_accounting": (
            counts["attempts"] == planned_requests and counts["missing"] == 0
        ),
        "zero_failure_unknown_skip_deselect": (
            counts["failed"] == counts["unknown"] == counts["skipped"] == counts["deselected"] == 0
        ),
        "all_responses_schema_identity_trace": counts["succeeded"] == planned_requests,
        "locust_request_accounting": (
            locust_stats.get("request_count") == counts["attempts"]
            and locust_stats.get("failure_count") == 0
        ),
        "s3_p99_response_ms": p99 is not None and p99 <= FIXED_THRESHOLDS["s3_p99_response_ms"],
        "maximum_error_rate": error_rate <= FIXED_THRESHOLDS["maximum_error_rate"],
        "maximum_generator_lag_ms": (
            max_lag is not None and max_lag <= FIXED_THRESHOLDS["maximum_generator_lag_ms"]
        ),
        "hard_duration_seconds": finished - started <= MAX_DURATION_SECONDS,
        "drain_timeout_seconds": drain_seconds <= DRAIN_TIMEOUT_SECONDS,
        "no_execution_errors": not execution_errors,
        "no_signal": signal_state["name"] is None,
    }
    local_pass = all(checks.values())
    return {
        "preflight": preflight,
        "requests": records,
        "accounting": counts,
        "metrics": metrics,
        "resources": {
            "container_cgroup": resource_result,
            "host_cpu_percent": {
                "status": "PENDING_PARENT_OBSERVATION",
                "maximum_allowed": FIXED_THRESHOLDS["maximum_host_cpu_percent"],
                "observer": "parent",
            },
        },
        "locust": {
            "library_runner": "Environment.create_local_runner",
            "user_class": "HttpUser",
            "wait_time": "constant_pacing",
            "concurrency": 1,
            "stats": locust_stats,
        },
        "duration": {
            "total_seconds": finished - started,
            "load_window_seconds": load_window,
            "drain_seconds": drain_seconds,
            "hard_maximum_seconds": MAX_DURATION_SECONDS,
            "drain_maximum_seconds": DRAIN_TIMEOUT_SECONDS,
        },
        "stop": {
            "reason": state.stop_reason or "completed_exact_request_count",
            "signal": signal_state["name"],
            "partial": counts["missing"] != 0,
        },
        "errors": execution_errors,
        "acceptance": {
            "fixed_thresholds": FIXED_THRESHOLDS,
            "observed_error_rate": error_rate,
            "checks": checks,
            "local_decision": "PASS" if local_pass else "FAIL",
            "global_decision": "PENDING_PARENT_HOST_CPU" if local_pass else "NO_GO",
            "host_cpu_gate_credit": "NOT_RUN_IN_CONTAINER",
        },
    }


def write_exclusive_json(path: Path, value: Any) -> None:
    path = path.resolve(strict=False)
    parent = path.parent.resolve(strict=True)
    if path.parent.resolve(strict=True) != parent or path.exists() or path.is_symlink():
        raise SmokeError("output path must be a new regular file")
    payload = canonical_json(value) + b"\n"
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise SmokeError("output write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path, follow_symlinks=False)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    signal_state: dict[str, Any] = {"name": None}

    def receive_signal(number: int, _frame: Any) -> None:
        signal_state["name"] = signal.Signals(number).name

    signal.signal(signal.SIGTERM, receive_signal)
    signal.signal(signal.SIGINT, receive_signal)
    started_utc = utc_now()
    started_ns = time.monotonic_ns()
    self_path = Path(__file__).resolve(strict=True)
    self_raw = read_bounded(self_path, 4 * 1024 * 1024, "smoke source")
    self_sha = sha256_bytes(self_raw)
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "started_utc": started_utc,
        "started_monotonic_ns": started_ns,
        "finished_utc": None,
        "finished_monotonic_ns": None,
        "run_id": os.environ.get("CT_RUN_ID"),
        "profile": None,
        "provenance": {
            "smoke_path": self_path.name,
            "smoke_bytes": len(self_raw),
            "smoke_sha256": self_sha,
            "python_executable": sys.executable,
            "python_version": sys.version,
            "source_commit": None,
            "source_tree": None,
            "target_image_id_expected": None,
            "target_image_id_observation": "PARENT_REQUIRED",
        },
        "semantics": {
            "request_completion": "synchronous_http_response",
            "job_status_polling": "NOT_APPLICABLE",
            "idempotency": "NOT_APPLICABLE",
            "effects": "NOT_APPLICABLE",
            "asynchronous_workflow": "NOT_RUN",
            "fixed_arrival_rate_claim": False,
        },
        "deferred_windows_gates": {
            "windows_etw_collector": "NOT_RUN",
            "windows_wsl_dual_collector": "NOT_RUN",
            "windows_high_integrity_token": "NOT_RUN",
        },
        "errors": [],
        "acceptance": {
            "local_decision": "FAIL",
            "global_decision": "NO_GO",
        },
    }
    exit_code = 1
    try:
        run_id = os.environ.get("CT_RUN_ID", "")
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise SmokeError("CT_RUN_ID missing or invalid")
        profile_path = args.profile.resolve(strict=True)
        profile_raw = read_bounded(profile_path, 1024 * 1024, "profile")
        profile_sha = sha256_bytes(profile_raw)
        profile = validate_profile(parse_json_bytes(profile_raw, "profile"), profile_path, self_sha)
        result["profile"] = {
            "path": profile_path.name,
            "bytes": len(profile_raw),
            "sha256": profile_sha,
            "profile_id": profile["profile_id"],
            "input_readiness_status": profile["status"],
            "acceptance_credit_requested": profile["acceptance_credit"],
            "scope": "TOOL_FIXTURE"
            if profile["target"]["network"] == "none"
            else "BOUNDED_API_SMOKE",
        }
        result["provenance"].update(
            {
                "source_commit": profile["provenance"]["source_commit"],
                "source_tree": profile["provenance"]["source_tree"],
                "target_image_id_expected": profile["provenance"]["target_image_id"],
            }
        )
        inputs = load_inputs(profile)
        result["frozen_inputs"] = inputs["files"]
        result["frozen_inputs"]["catalog_canonical_sha256"] = inputs["catalog_canonical_sha256"]
        execution = execute(profile, inputs, run_id, signal_state)
        result.update(execution)
        if result["acceptance"]["local_decision"] == "PASS":
            exit_code = 0
    except Exception as exc:
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)[:500]})
        result["acceptance"] = {
            "fixed_thresholds": FIXED_THRESHOLDS,
            "local_decision": "FAIL",
            "global_decision": "NO_GO",
            "host_cpu_gate_credit": "NOT_RUN_IN_CONTAINER",
        }
    result["finished_utc"] = utc_now()
    result["finished_monotonic_ns"] = time.monotonic_ns()
    result["process_exit_code"] = exit_code
    try:
        write_exclusive_json(args.output, result)
    except Exception as exc:
        print(f"smoke result publication failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema_version": RESULT_SCHEMA,
                "run_id": result["run_id"],
                "local_decision": result["acceptance"]["local_decision"],
                "global_decision": result["acceptance"]["global_decision"],
                "output_sha256": sha256_bytes(args.output.read_bytes()),
                "exit_code": exit_code,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

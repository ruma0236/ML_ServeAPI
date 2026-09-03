"""Fail-closed, non-credit CI lane contract for the pre-r8 r7s5 review.

This module validates repository-local structure and externally supplied receipt
bindings.  It does not authenticate a hosted runner, mint receipts, run tests,
or make an execution eligible for production credit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping, NoReturn, Sequence


MANIFEST_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-ci-contract.v1"
RUNNER_RECEIPT_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-runner-receipt.v1"
PRIVATE_RECEIPT_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-private-receipt.v1"
LANE_RESULT_RECEIPT_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-lane-result-receipt.v1"
COLLECTION_INVENTORY_RECEIPT_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-collection-inventory-receipt.v1"
)
COLLECTION_BINDING_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7-current-collection-binding.v1"
LANES = ("portable", "windows", "private")
EXPECTED_BASELINE_COMMIT = "c70d11abf2e6e34d0fca8e51db38ebfea7fc0f5b"
EXPECTED_BASELINE_TREE = "f8d691948ea908d9c12b11f8e35b87ba40dd5e62"
EXPECTED_BLOCKERS = (
    "current_commit_portable_lane_execution_unproven",
    "external_attested_windows_runner_absent",
    "external_attested_private_artifact_runner_absent",
    "hosted_linux_runner_external_provenance_unproven",
    "active_required_lane_workflow_not_connected",
    "external_oob_receipt_authority_unprovisioned",
    "external_worm_replay_authority_unprovisioned",
)
EXPECTED_LANE_FILES = {
    "portable": (
        "tests/test_phase_b2_r7s5_ci.py",
        "tests/test_scenario_workload_production.py",
        "tests/test_task_queue_process_safety.py",
    ),
    "windows": (
        "tests/test_phase_b2_r5_bundle_builder.py",
        "tests/test_phase_b2_r5_process.py",
        "tests/test_phase_b2_r7_bundle_builder.py",
        "tests/test_phase_b2_r7_process.py",
        "tests/test_phase_b2_r7_validator.py",
        "tests/test_phase_b2_r7s1.py",
        "tests/test_phase_b2_r7s1_bundle_builder.py",
        "tests/test_phase_b2_r7s1_runner.py",
        "tests/test_phase_b2_r7s1_validator.py",
        "tests/test_pre_r8_r7s2_outer_launcher.py",
    ),
    "private": (
        "tests/test_s3_capacity_evidence.py",
        "tests/test_s4_gpu_batching_evidence.py",
        "tests/test_s5_evidence.py",
        "tests/test_scenario_model_serving.py",
        "tests/test_x1_artifacts.py",
        "tests/test_x1_calibration.py",
        "tests/test_x1_contract.py",
        "tests/test_x1_contract_validation.py",
        "tests/test_x1_runtime.py",
        "tests/test_x1_topology.py",
    ),
}
EXPECTED_FAILED_FILE_COUNTS = {
    "tests/test_phase_b2_r5_bundle_builder.py": 2,
    "tests/test_phase_b2_r5_process.py": 2,
    "tests/test_phase_b2_r7_bundle_builder.py": 1,
    "tests/test_phase_b2_r7_process.py": 2,
    "tests/test_phase_b2_r7_validator.py": 1,
    "tests/test_phase_b2_r7s1.py": 61,
    "tests/test_phase_b2_r7s1_bundle_builder.py": 8,
    "tests/test_phase_b2_r7s1_runner.py": 1,
    "tests/test_phase_b2_r7s1_validator.py": 1,
    "tests/test_pre_r8_r7s2_outer_launcher.py": 20,
    "tests/test_s3_capacity_evidence.py": 2,
    "tests/test_s4_gpu_batching_evidence.py": 17,
    "tests/test_s5_evidence.py": 2,
    "tests/test_scenario_model_serving.py": 3,
    "tests/test_scenario_workload_production.py": 1,
    "tests/test_task_queue_process_safety.py": 1,
    "tests/test_x1_artifacts.py": 13,
    "tests/test_x1_calibration.py": 11,
    "tests/test_x1_contract.py": 6,
    "tests/test_x1_contract_validation.py": 2,
    "tests/test_x1_runtime.py": 6,
    "tests/test_x1_topology.py": 9,
}
EXPECTED_LANE_FAILED_NODE_COUNTS = {"portable": 2, "windows": 99, "private": 71}
EXPECTED_NODEID_CANONICALIZATION = (
    "utf-8_lf_terminal_newline_sorted_unique_reconstructed_pytest_nodeid_v1"
)
EXPECTED_NODEID_COUNTS = {
    "collected": 2170,
    "error": 0,
    "failed": 172,
    "passed": 1841,
    "skipped": 157,
}
EXPECTED_NODEID_HASHES = {
    "collected": "0dde9131ea86b7ceb00132a94fa4d4bb772a474231a01c058ffb79b04ff280ae",
    "error": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "failed": "e1bc6accb087153f15019696f59e5dae51757fc21d97f551ce2359d95f7b9590",
    "passed": "ec8748d22d1aea21fe2409e23ec956cce3a59f0e3330a5556424d5bbb2720cd1",
    "skipped": "b3fec069bc3c009a4a2087345fc52ecd1fc2bf06d76aab0dbb9eef1689bdc4d7",
}
EXPECTED_NODEID_BYTES = {
    "collected": 227637,
    "error": 1,
    "failed": 19934,
    "passed": 189598,
    "skipped": 18105,
}
EXPECTED_ACTION_REFS = Counter(
    {
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262": 4,
        "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020": 1,
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065": 1,
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02": 4,
        "azure/setup-kubectl@776406bce94f63e41d621b960d78ee25c8b76ede": 1,
    }
)
EXPECTED_JOB_IDS = (
    "portable-linux",
    "windows-platform-required",
    "private-artifact-required",
    "required-lane-closure",
)
EXPECTED_LANE_JOBS = {
    "portable": "portable-linux",
    "windows": "windows-platform-required",
    "private": "private-artifact-required",
}
EXPECTED_WINDOWS_LABELS = ("self-hosted", "Windows", "X64", "s8-v4-r7s5-private")
EXPECTED_WINDOWS_RUNNER_GROUP = "s8-v4-r7s5-private"
EXPECTED_TOOL_ROLES = ("docker", "git", "nvidia_smi", "powershell", "python", "wsl")
EXPECTED_LANE_CONTRACT = {
    "portable": {
        "actual_execution_required": True,
        "domain": "portable_common_linux",
        "external_result_receipt_required": True,
        "runner_class": "github_hosted_ubuntu_24_04",
    },
    "windows": {
        "actual_execution_required": True,
        "domain": "real_windows_semantics",
        "external_result_receipt_required": True,
        "runner_class": "attested_self_hosted_windows",
    },
    "private": {
        "actual_execution_required": True,
        "domain": "private_x1_s4_artifact_runtime",
        "external_result_receipt_required": True,
        "runner_class": "attested_self_hosted_windows_x1_s4",
    },
}
# No genuine externally approved lane collection inventory is available yet.
# A future reviewed commit may populate this with exact, independently derived
# receipt SHA, node count, and nodeid SHA pins.  Until then the public closure
# is intentionally incapable of returning closure PASS.
PINNED_EXTERNAL_COLLECTION_CONTRACT: Mapping[str, Any] | None = None
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
FULL_ACTION_RE = re.compile(r"[^/@\s]+/[^/@\s]+@[0-9a-f]{40}")
JOB_RE = re.compile(r"^  ([a-z0-9][a-z0-9-]*):\s*$")
STEP_KEY_RE = re.compile(
    r"^(?P<indent> *)(?P<list_item>-\s+)?(?P<key>uses|run)\s*:\s*(?P<value>.*?)\s*$"
)
USES_KEY_TOKEN_RE = re.compile(r'(?:^|[\s{,\-])(?:uses|"uses"|\'uses\')\s*:')
YAML_ANCHOR_OR_ALIAS_RE = re.compile(r"(?:^|[\s:\[,\-])[&*][A-Za-z_][A-Za-z0-9_.-]*(?=$|[\s,\]}#])")
INLINE_SEQUENCE_ITEM_RE = re.compile(r"^ *-\s*[\[{]")
BLOCK_SCALAR_RE = re.compile(r":\s*[|>][+-]?(?:\s+#.*)?$")
FORBIDDEN_WORKFLOW_PATTERNS = (
    ("continue_on_error", re.compile(r"(?i)\bcontinue-on-error\s*:")),
    ("shell_true_fallback", re.compile(r"\|\|\s*true\b")),
    ("pytest_ignore", re.compile(r"(?:^|\s)--ignore(?:=|\s)", re.MULTILINE)),
    ("pytest_k_selection", re.compile(r"(?:^|\s)-k(?:=|\s)", re.MULTILINE)),
    (
        "test_skip_or_xfail",
        re.compile(r"(?i)\b(?:pytest\.)?(?:skip|skipif|xfail|importorskip)\b"),
    ),
    ("mutable_runner", re.compile(r"(?i)\b(?:ubuntu|windows|macos)-latest\b")),
    ("dynamic_runner", re.compile(r"(?m)^\s*runs-on:\s*\$\{\{")),
    ("false_condition", re.compile(r"(?im)^\s*if:\s*false\s*$")),
    ("secret_condition", re.compile(r"(?im)^\s*if:.*(?:secrets|vars)\.")),
)


class R7S5CIContractError(RuntimeError):
    """Raised when the r7s5 CI contract fails closed."""


SignatureVerifier = Callable[[bytes, str, str], bool]


@dataclass(frozen=True)
class ReceiptBinding:
    repository: str
    workflow: str
    commit: str
    tree: str
    run_id: str
    run_uuid: str
    run_attempt: int
    job: str
    domain: str
    toolchain_sha256: str


@dataclass(frozen=True)
class VerifiedReceipt:
    receipt_id: str
    nonce: str
    issuer: str
    domain: str
    commit: str
    tree: str
    run_id: str
    run_uuid: str
    run_attempt: int
    job: str
    kind: str
    toolchain_sha256: str


@dataclass(frozen=True)
class AttestedLaneResult:
    """A result decoded only after its complete receipt signature is accepted."""

    receipt_id: str
    receipt_sha256: str
    issuer: str
    issued_at: datetime
    lane: str
    run_uuid: str
    toolchain_sha256: str
    result_sha256: str
    nodeids_sha256: str
    result: Mapping[str, Any]


@dataclass(frozen=True)
class AttestedCollectionInventory:
    """An externally signed expected collection fixed before lane execution."""

    receipt_id: str
    receipt_sha256: str
    issuer: str
    issued_at: datetime
    lane: str
    run_uuid: str
    toolchain_sha256: str
    nodeids_sha256: str
    nodeids: tuple[str, ...]


class ReceiptReplayGuard:
    """In-memory one-shot guard; production requires an independent durable store."""

    def __init__(self) -> None:
        self._consumed: set[tuple[str, ...]] = set()

    def consume(self, key: tuple[str, ...]) -> None:
        if key in self._consumed:
            raise R7S5CIContractError("receipt_replay_detected")
        self._consumed.add(key)


class DurableReceiptReplayGuard(ReceiptReplayGuard):
    """Local concurrency layer; not an administrative/WORM replay authority."""

    administrative_tamper_resistant = False

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root.resolve(strict=True)
        if not self._root.is_dir() or self._root.is_symlink():
            raise R7S5CIContractError("durable_replay_backend_directory_required")
        stat = self._root.stat()
        reparse_flag = getattr(stat, "st_file_attributes", 0) & 0x400
        if reparse_flag:
            raise R7S5CIContractError("durable_replay_backend_reparse_forbidden")
        self._identity = (stat.st_dev, stat.st_ino)
        self.parent_directory_fsync_completed: bool | None = None

    def _assert_backend_identity(self) -> None:
        try:
            stat = self._root.stat()
        except OSError as exc:
            raise R7S5CIContractError("durable_replay_backend_identity_unavailable") from exc
        if (stat.st_dev, stat.st_ino) != self._identity:
            raise R7S5CIContractError("durable_replay_backend_identity_changed")

    def _fsync_parent_directory(self) -> None:
        self._assert_backend_identity()
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(self._root, flags)
        except OSError as exc:
            if os.name == "nt":
                self.parent_directory_fsync_completed = False
                return
            raise R7S5CIContractError("durable_replay_parent_fsync_failed") from exc
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if os.name == "nt":
                self.parent_directory_fsync_completed = False
                return
            raise R7S5CIContractError("durable_replay_parent_fsync_failed") from exc
        finally:
            os.close(descriptor)
        self.parent_directory_fsync_completed = True

    def consume(self, key: tuple[str, ...]) -> None:
        if key in self._consumed:
            raise R7S5CIContractError("receipt_replay_detected")
        self._assert_backend_identity()
        serialized = _canonical_json({"receipt_key": list(key)}) + b"\n"
        marker = self._root / f"{hashlib.sha256(serialized).hexdigest()}.used"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(marker, flags, 0o600)
        except FileExistsError as exc:
            raise R7S5CIContractError("receipt_replay_detected") from exc
        except OSError as exc:
            raise R7S5CIContractError("durable_replay_backend_write_failed") from exc
        try:
            offset = 0
            while offset < len(serialized):
                written = os.write(descriptor, serialized[offset:])
                if written <= 0:
                    raise OSError("zero-byte durable replay write")
                offset += written
            os.fsync(descriptor)
        except OSError as exc:
            raise R7S5CIContractError("durable_replay_backend_write_failed") from exc
        finally:
            os.close(descriptor)
        self._fsync_parent_directory()
        self._consumed.add(key)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7S5CIContractError(f"{label}_keys_not_exact")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise R7S5CIContractError(f"{label}_mapping_required")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise R7S5CIContractError(f"{label}_nonempty_string_required")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise R7S5CIContractError(f"{label}_integer_out_of_range")
    return value


def _hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    text = _string(value, label)
    if pattern.fullmatch(text) is None:
        raise R7S5CIContractError(f"{label}_hex_not_exact")
    return text


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _uuid4(value: Any, label: str) -> str:
    text = _string(value, label)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise R7S5CIContractError(f"{label}_uuid4_canonical_required") from exc
    if parsed.version != 4 or str(parsed) != text:
        raise R7S5CIContractError(f"{label}_uuid4_canonical_required")
    return text


def _parse_json(raw: bytes, label: str) -> Mapping[str, Any]:
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise R7S5CIContractError(f"{label}_lf_terminal_newline_required")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise R7S5CIContractError(f"{label}_duplicate_key:{key}")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7S5CIContractError(f"{label}_json_invalid") from exc
    return _mapping(parsed, label)


def _validate_relative_test_path(value: Any, label: str) -> str:
    path = _string(value, label)
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or "\\" in path
        or ".." in pure.parts
        or len(pure.parts) != 2
        or pure.parts[0] != "tests"
        or not pure.name.startswith("test_")
        or pure.suffix != ".py"
    ):
        raise R7S5CIContractError(f"{label}_test_path_not_exact")
    return path


def validate_manifest(
    payload: Mapping[str, Any], *, project_root: Path | None = None
) -> dict[str, Any]:
    _exact_keys(
        payload,
        {
            "baseline",
            "current_collection_contract",
            "current_external_state",
            "decision",
            "file_inventory",
            "hosted_failure_observation",
            "lane_contract",
            "remaining_blockers",
            "runner_contract",
            "schema",
            "workflow_contract",
        },
        "manifest",
    )
    if payload["schema"] != MANIFEST_SCHEMA:
        raise R7S5CIContractError("manifest_schema_mismatch")

    current_collection = _mapping(
        payload["current_collection_contract"], "current_collection_contract"
    )
    _exact_keys(
        current_collection,
        {
            "binding_source",
            "historical_node_inventory_reuse_allowed",
            "required_binding_fields",
            "required_lane_binding_fields",
            "tracked_current_head_pin_allowed",
        },
        "current_collection_contract",
    )
    expected_current_collection = {
        "binding_source": "externally_attested_oob_per_run",
        "historical_node_inventory_reuse_allowed": False,
        "required_binding_fields": [
            "commit",
            "lanes",
            "repository",
            "run_attempt",
            "run_id",
            "run_uuid",
            "schema",
            "tree",
            "workflow",
        ],
        "required_lane_binding_fields": [
            "collection_receipt_sha256",
            "job",
            "node_count",
            "nodeids_sha256",
            "toolchain_sha256",
        ],
        "tracked_current_head_pin_allowed": False,
    }
    if current_collection != expected_current_collection:
        raise R7S5CIContractError("current_collection_contract_mismatch")

    lane_contract = _mapping(payload["lane_contract"], "lane_contract")
    if lane_contract != EXPECTED_LANE_CONTRACT:
        raise R7S5CIContractError("lane_semantics_contract_mismatch")

    baseline = _mapping(payload["baseline"], "baseline")
    _exact_keys(baseline, {"commit", "tree"}, "baseline")
    if baseline != {"commit": EXPECTED_BASELINE_COMMIT, "tree": EXPECTED_BASELINE_TREE}:
        raise R7S5CIContractError("baseline_identity_mismatch")

    inventory = _mapping(payload["file_inventory"], "file_inventory")
    _exact_keys(inventory, {"lanes", "scope", "scope_files"}, "file_inventory")
    if inventory["scope"] != "bounded_r7s5_known_failure_partition_and_contract_tests":
        raise R7S5CIContractError("file_inventory_scope_mismatch")
    lanes = _mapping(inventory["lanes"], "file_inventory_lanes")
    if set(lanes) != set(LANES):
        raise R7S5CIContractError("lane_names_or_order_not_exact")
    assigned: list[str] = []
    normalized_lanes: dict[str, tuple[str, ...]] = {}
    for lane in LANES:
        values = lanes[lane]
        if not isinstance(values, list) or not values:
            raise R7S5CIContractError(f"lane_files_missing:{lane}")
        normalized = tuple(
            _validate_relative_test_path(item, f"lane_file:{lane}") for item in values
        )
        if normalized != tuple(sorted(set(normalized))):
            raise R7S5CIContractError(f"lane_files_not_sorted_unique:{lane}")
        normalized_lanes[lane] = normalized
        assigned.extend(normalized)
    duplicates = sorted(path for path, count in Counter(assigned).items() if count != 1)
    if duplicates:
        raise R7S5CIContractError(f"lane_inventory_overlap:{','.join(duplicates)}")
    scope = inventory["scope_files"]
    if not isinstance(scope, list):
        raise R7S5CIContractError("scope_files_list_required")
    scope_files = tuple(_validate_relative_test_path(item, "scope_file") for item in scope)
    if scope_files != tuple(sorted(set(scope_files))):
        raise R7S5CIContractError("scope_files_not_sorted_unique")
    if set(scope_files) != set(assigned):
        raise R7S5CIContractError("lane_inventory_gap")
    for lane in LANES:
        if normalized_lanes[lane] != EXPECTED_LANE_FILES[lane]:
            raise R7S5CIContractError(f"lane_inventory_mutation:{lane}")
    if project_root is not None:
        root = project_root.resolve(strict=True)
        for relative in scope_files:
            candidate = (root / relative).resolve(strict=True)
            if root not in candidate.parents or not candidate.is_file():
                raise R7S5CIContractError(f"scope_file_not_regular_or_outside:{relative}")

    observed = _mapping(payload["hosted_failure_observation"], "hosted_failure")
    _exact_keys(
        observed,
        {
            "artifact_archive_bytes",
            "artifact_archive_sha256",
            "artifact_id",
            "artifact_name",
            "errors",
            "failed",
            "full_nodeid_inventory_available",
            "head_sha",
            "historical_only",
            "eligible_for_current_closure",
            "junit_xml_bytes",
            "junit_xml_sha256",
            "known_failed_file_counts",
            "lane_failed_node_counts",
            "nodeid_canonicalization",
            "nodeid_inventory_bytes",
            "nodeid_inventory_counts",
            "nodeid_inventory_readback_sha256",
            "nodeid_sorted_hashes",
            "other_files_failed_node_count",
            "passed",
            "run_id",
            "skipped",
            "tests",
        },
        "hosted_failure",
    )
    counts = _mapping(observed["known_failed_file_counts"], "known_failed_file_counts")
    if counts != EXPECTED_FAILED_FILE_COUNTS:
        raise R7S5CIContractError("known_failed_file_counts_mismatch")
    if set(counts) != set(scope_files) - {"tests/test_phase_b2_r7s5_ci.py"}:
        raise R7S5CIContractError("failed_file_inventory_not_exact_lane_scope")
    for path, expected in EXPECTED_FAILED_FILE_COUNTS.items():
        if _integer(counts[path], f"failed_file_count:{path}", minimum=1) != expected:
            raise R7S5CIContractError(f"failed_file_count_mismatch:{path}")

    lane_failure_counts = _mapping(observed["lane_failed_node_counts"], "lane_failed_node_counts")
    _exact_keys(lane_failure_counts, set(LANES), "lane_failed_node_counts")
    for lane, expected in EXPECTED_LANE_FAILED_NODE_COUNTS.items():
        actual = sum(counts.get(path, 0) for path in normalized_lanes[lane])
        if (
            _integer(lane_failure_counts[lane], f"lane_failed_node_count:{lane}") != expected
            or actual != expected
        ):
            raise R7S5CIContractError(f"lane_failed_node_count_mismatch:{lane}")

    nodeid_counts = _mapping(observed["nodeid_inventory_counts"], "nodeid_counts")
    _exact_keys(nodeid_counts, set(EXPECTED_NODEID_COUNTS), "nodeid_counts")
    for status, expected in EXPECTED_NODEID_COUNTS.items():
        if _integer(nodeid_counts[status], f"nodeid_count:{status}") != expected:
            raise R7S5CIContractError(f"nodeid_count_mismatch:{status}")
    nodeid_hashes = _mapping(observed["nodeid_sorted_hashes"], "nodeid_hashes")
    _exact_keys(nodeid_hashes, set(EXPECTED_NODEID_HASHES), "nodeid_hashes")
    for status, expected in EXPECTED_NODEID_HASHES.items():
        if _hex(nodeid_hashes[status], HEX64, f"nodeid_hash:{status}") != expected:
            raise R7S5CIContractError(f"nodeid_hash_mismatch:{status}")
    nodeid_bytes = _mapping(observed["nodeid_inventory_bytes"], "nodeid_bytes")
    _exact_keys(nodeid_bytes, set(EXPECTED_NODEID_BYTES), "nodeid_bytes")
    for status, expected in EXPECTED_NODEID_BYTES.items():
        if _integer(nodeid_bytes[status], f"nodeid_bytes:{status}") != expected:
            raise R7S5CIContractError(f"nodeid_bytes_mismatch:{status}")

    expected_observation = {
        "artifact_archive_bytes": 96190,
        "artifact_archive_sha256": (
            "0dbec4bea33d8890af17602423c024e62ea36689966b9df4ada8de4e257b87ee"
        ),
        "artifact_id": 9830224962,
        "artifact_name": "evm-python-test-diagnostics",
        "errors": 0,
        "failed": 172,
        "full_nodeid_inventory_available": True,
        "head_sha": "0f9a3b9ebb9d2248f9908852ec124eb6b9a5ba80",
        "historical_only": True,
        "eligible_for_current_closure": False,
        "junit_xml_bytes": 1158862,
        "junit_xml_sha256": "16aa6fa2bf4b9ddb8e6f2cbeef2739c8c0fd5ebbb7145a607fe832d04fabd209",
        "nodeid_canonicalization": EXPECTED_NODEID_CANONICALIZATION,
        "nodeid_inventory_readback_sha256": (
            "328a7a888c145baef695a66a7ed4995e9d950b277bc36c6fe8e7d4fef6dbf32e"
        ),
        "other_files_failed_node_count": 0,
        "passed": 1841,
        "run_id": "33586339216",
        "skipped": 157,
        "tests": 2170,
    }
    for key in (
        "artifact_archive_bytes",
        "artifact_id",
        "errors",
        "failed",
        "junit_xml_bytes",
        "other_files_failed_node_count",
        "passed",
        "skipped",
        "tests",
    ):
        _integer(observed[key], f"hosted_failure:{key}")
    if observed["full_nodeid_inventory_available"] is not True:
        raise R7S5CIContractError("hosted_full_nodeid_inventory_not_available")
    for key in (
        "artifact_archive_sha256",
        "head_sha",
        "junit_xml_sha256",
        "nodeid_inventory_readback_sha256",
    ):
        _hex(observed[key], HEX40 if key == "head_sha" else HEX64, f"hosted_failure:{key}")
    for key, expected in expected_observation.items():
        if observed[key] != expected:
            raise R7S5CIContractError(f"hosted_failure_observation_mismatch:{key}")
    known = sum(EXPECTED_FAILED_FILE_COUNTS.values())
    if known != 172 or known + observed["other_files_failed_node_count"] != observed["failed"]:
        raise R7S5CIContractError("hosted_failure_partition_arithmetic_mismatch")
    if (
        observed["passed"] + observed["failed"] + observed["skipped"] + observed["errors"]
        != observed["tests"]
    ):
        raise R7S5CIContractError("hosted_test_count_arithmetic_mismatch")
    if nodeid_counts != {
        "collected": observed["tests"],
        "error": observed["errors"],
        "failed": observed["failed"],
        "passed": observed["passed"],
        "skipped": observed["skipped"],
    }:
        raise R7S5CIContractError("hosted_nodeid_count_binding_mismatch")

    external = _mapping(payload["current_external_state"], "current_external_state")
    _exact_keys(
        external,
        {
            "external_attested_private_artifact_runner_available",
            "external_attested_windows_runner_available",
            "hosted_linux_runner_provenance_verified",
        },
        "current_external_state",
    )
    if any(value is not False for value in external.values()):
        raise R7S5CIContractError("external_state_cannot_be_promoted_in_frozen_no_go_contract")

    decision = _mapping(payload["decision"], "decision")
    _exact_keys(
        decision,
        {"credit", "go_evidence_eligible", "reason", "reviewer_sign_off", "status"},
        "decision",
    )
    if decision != {
        "credit": "zero_credit",
        "go_evidence_eligible": False,
        "reason": "current_commit_required_lane_execution_and_external_authority_unproven",
        "reviewer_sign_off": "pending",
        "status": "manual_intervention_required",
    }:
        raise R7S5CIContractError("decision_must_remain_no_go")
    if tuple(payload["remaining_blockers"]) != EXPECTED_BLOCKERS:
        raise R7S5CIContractError("remaining_blockers_mismatch")

    runner = _mapping(payload["runner_contract"], "runner_contract")
    _exact_keys(
        runner,
        {
            "closure_job",
            "closure_needs",
            "lane_jobs",
            "portable_runs_on",
            "trusted_windows_labels",
        },
        "runner_contract",
    )
    if runner["closure_job"] != "required-lane-closure":
        raise R7S5CIContractError("closure_job_mismatch")
    if tuple(runner["closure_needs"]) != (
        "portable-linux",
        "windows-platform-required",
        "private-artifact-required",
    ):
        raise R7S5CIContractError("closure_needs_mismatch")
    if runner["lane_jobs"] != EXPECTED_LANE_JOBS:
        raise R7S5CIContractError("lane_job_mapping_mismatch")
    if runner["portable_runs_on"] != "ubuntu-24.04":
        raise R7S5CIContractError("portable_runner_mismatch")
    if tuple(runner["trusted_windows_labels"]) != EXPECTED_WINDOWS_LABELS:
        raise R7S5CIContractError("trusted_windows_labels_mismatch")

    workflow = _mapping(payload["workflow_contract"], "workflow_contract")
    _exact_keys(
        workflow,
        {
            "active_workflow_lane_contract_connected",
            "actual_workflow_execution",
            "allowed_action_refs",
            "closure_command",
            "lane_command_tokens",
            "manifest_only_is_configuration_validation",
            "missing_required_lane_decision",
            "required_job_ids",
        },
        "workflow_contract",
    )
    action_contract = _mapping(workflow["allowed_action_refs"], "allowed_action_refs")
    if set(action_contract) != set(EXPECTED_ACTION_REFS):
        raise R7S5CIContractError("allowed_action_ref_set_mismatch")
    if Counter(action_contract) != EXPECTED_ACTION_REFS:
        raise R7S5CIContractError("allowed_action_ref_count_mismatch")
    if workflow["lane_command_tokens"] != {
        "portable": "--lane portable",
        "private": "--lane private",
        "windows": "--lane windows",
    }:
        raise R7S5CIContractError("lane_command_tokens_mismatch")
    if tuple(workflow["required_job_ids"]) != EXPECTED_JOB_IDS:
        raise R7S5CIContractError("required_job_ids_mismatch")
    if workflow["closure_command"] != (
        "python scripts/dev/validate_pre_r8_r7s5_ci.py manifest "
        "--manifest ci/pre-r8-r7s5-test-lanes.json --project-root ."
    ):
        raise R7S5CIContractError("closure_command_mismatch")
    if workflow["active_workflow_lane_contract_connected"] is not False:
        raise R7S5CIContractError("active_workflow_connection_unproven")
    if workflow["actual_workflow_execution"] != "unproven":
        raise R7S5CIContractError("actual_workflow_execution_must_remain_unproven")
    if workflow["manifest_only_is_configuration_validation"] is not True:
        raise R7S5CIContractError("manifest_only_scope_mismatch")
    if workflow["missing_required_lane_decision"] != "incomplete_no_go":
        raise R7S5CIContractError("missing_lane_decision_mismatch")

    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-ci-validation.v1",
        "status": "manual_intervention_required",
        "credit": "zero_credit",
        "go_evidence_eligible": False,
        "configuration_contract_valid": True,
        "historical_node_inventory_eligible_for_current_closure": False,
        "active_workflow_lane_contract_connected": False,
        "actual_workflow_execution": "unproven",
        "lane_file_counts": {lane: len(EXPECTED_LANE_FILES[lane]) for lane in LANES},
        "known_failed_nodes": known,
        "unclassified_failed_nodes": observed["other_files_failed_node_count"],
        "remaining_blockers": list(EXPECTED_BLOCKERS),
    }


def load_and_validate_manifest(path: Path, *, project_root: Path | None = None) -> dict[str, Any]:
    payload = _parse_json(path.read_bytes(), "manifest")
    return validate_manifest(payload, project_root=project_root)


def load_manifest(path: Path, *, project_root: Path | None = None) -> Mapping[str, Any]:
    payload = _parse_json(path.read_bytes(), "manifest")
    validate_manifest(payload, project_root=project_root)
    return payload


def load_receipt(path: Path) -> Mapping[str, Any]:
    """Load a canonical receipt without treating it as authenticated."""

    return _parse_json(path.read_bytes(), "receipt")


def _extract_job_blocks(workflow: str) -> dict[str, str]:
    lines = workflow.splitlines(keepends=True)
    jobs_positions = [index for index, line in enumerate(lines) if line == "jobs:\n"]
    if len(jobs_positions) != 1:
        raise R7S5CIContractError("workflow_jobs_section_not_unique")
    start = jobs_positions[0] + 1
    positions: list[tuple[str, int]] = []
    for index in range(start, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "\n", "#")):
            break
        match = JOB_RE.fullmatch(line.rstrip("\n"))
        if match:
            positions.append((match.group(1), index))
    duplicate_jobs = sorted(
        job for job, count in Counter(job for job, _ in positions).items() if count > 1
    )
    if duplicate_jobs:
        raise R7S5CIContractError(f"workflow_job_id_not_unique:{duplicate_jobs[0]}")
    blocks: dict[str, str] = {}
    for ordinal, (job, index) in enumerate(positions):
        end = positions[ordinal + 1][1] if ordinal + 1 < len(positions) else len(lines)
        blocks[job] = "".join(lines[index:end])
    for job, block in blocks.items():
        if len(re.findall(r"(?m)^    steps:\s*$", block)) != 1:
            raise R7S5CIContractError(f"workflow_job_steps_not_unique:{job}")
    return blocks


def _single_job_value(block: str, key: str, label: str) -> str:
    values = re.findall(rf"(?m)^    {re.escape(key)}:\s*(.+?)\s*$", block)
    if len(values) != 1:
        raise R7S5CIContractError(f"{label}_{key}_not_unique")
    return values[0]


def _parse_inline_list(value: str, label: str) -> tuple[str, ...]:
    if not value.startswith("[") or not value.endswith("]"):
        raise R7S5CIContractError(f"{label}_inline_list_required")
    items = tuple(item.strip() for item in value[1:-1].split(","))
    if not items or any(not item for item in items):
        raise R7S5CIContractError(f"{label}_inline_list_invalid")
    return items


def _count_active_lines(block: str, token: str) -> int:
    return sum(
        1 for line in block.splitlines() if token in line and not line.lstrip().startswith("#")
    )


def _active_workflow_yaml_lines(workflow: str) -> list[tuple[int, str]]:
    """Return YAML lines while excluding comments and block-scalar payloads."""

    active: list[tuple[int, str]] = []
    block_scalar_indent: int | None = None
    for line_number, line in enumerate(workflow.splitlines(), start=1):
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if block_scalar_indent is not None:
            if indent > block_scalar_indent:
                continue
            block_scalar_indent = None
        active.append((line_number, line))
        if BLOCK_SCALAR_RE.search(line):
            block_scalar_indent = indent
    return active


def _extract_action_refs(workflow: str) -> list[str]:
    """Extract every action ref from an unambiguous, job-scoped workflow step."""

    action_refs: list[str] = []
    jobs_scope = False
    current_job = False
    in_steps = False
    current_step = False
    step_uses = 0
    step_runs = 0

    def register_step_key(key: str, value: str) -> None:
        nonlocal step_uses, step_runs
        if key == "uses":
            step_uses += 1
            if step_uses > 1:
                raise R7S5CIContractError("workflow_step_uses_not_unique")
            if step_runs:
                raise R7S5CIContractError("workflow_step_uses_and_run_conflict")
            match = re.fullmatch(r"([^\s#]+)\s*(?:#.*)?", value)
            if match is None:
                raise R7S5CIContractError("workflow_action_ref_scalar_ambiguous")
            action_refs.append(match.group(1))
            return
        step_runs += 1
        if step_runs > 1:
            raise R7S5CIContractError("workflow_step_run_not_unique")
        if step_uses:
            raise R7S5CIContractError("workflow_step_uses_and_run_conflict")

    for line_number, line in _active_workflow_yaml_lines(workflow):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        syntax = re.split(r"\s+#", line, maxsplit=1)[0]

        if YAML_ANCHOR_OR_ALIAS_RE.search(syntax):
            raise R7S5CIContractError(f"workflow_yaml_anchor_or_alias_forbidden:line={line_number}")
        if USES_KEY_TOKEN_RE.search(stripped) and STEP_KEY_RE.fullmatch(line) is None:
            raise R7S5CIContractError(f"workflow_action_ref_inline_or_ambiguous:line={line_number}")
        if INLINE_SEQUENCE_ITEM_RE.match(syntax):
            raise R7S5CIContractError(f"workflow_yaml_inline_step_forbidden:line={line_number}")

        if indent == 0:
            jobs_scope = stripped == "jobs:"
            current_job = False
            in_steps = False
            current_step = False
            step_uses = 0
            step_runs = 0
        elif jobs_scope and indent == 2 and JOB_RE.fullmatch(line):
            current_job = True
            in_steps = False
            current_step = False
            step_uses = 0
            step_runs = 0
        elif current_job and indent == 4:
            in_steps = stripped == "steps:"
            current_step = False
            step_uses = 0
            step_runs = 0
        elif in_steps and indent == 6 and stripped.startswith("- "):
            current_step = True
            step_uses = 0
            step_runs = 0
        elif in_steps and indent <= 6:
            current_step = False
            step_uses = 0
            step_runs = 0

        key_match = STEP_KEY_RE.fullmatch(line)
        if key_match is None:
            continue
        key = key_match.group("key")
        is_direct_step_key = (
            in_steps and current_step and indent == 6 and key_match.group("list_item") is not None
        )
        is_step_child_key = (
            in_steps and current_step and indent == 8 and key_match.group("list_item") is None
        )
        if key == "uses" and not (is_direct_step_key or is_step_child_key):
            raise R7S5CIContractError(f"workflow_action_ref_unscoped:line={line_number}")
        if is_direct_step_key or is_step_child_key:
            register_step_key(key, key_match.group("value"))

    return action_refs


def validate_workflow_contract(raw: bytes, manifest: Mapping[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise R7S5CIContractError("workflow_lf_terminal_newline_required")
    try:
        workflow = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R7S5CIContractError("workflow_utf8_required") from exc
    for label, pattern in FORBIDDEN_WORKFLOW_PATTERNS:
        if pattern.search(workflow):
            raise R7S5CIContractError(f"workflow_forbidden_bypass:{label}")

    action_refs = _extract_action_refs(workflow)
    if any(FULL_ACTION_RE.fullmatch(ref) is None for ref in action_refs):
        raise R7S5CIContractError("workflow_action_ref_not_full_sha")
    if Counter(action_refs) != EXPECTED_ACTION_REFS:
        raise R7S5CIContractError("workflow_action_ref_inventory_mismatch")

    blocks = _extract_job_blocks(workflow)
    if tuple(blocks) != EXPECTED_JOB_IDS:
        raise R7S5CIContractError("workflow_job_order_or_set_mismatch")
    portable_runner = _single_job_value(blocks["portable-linux"], "runs-on", "portable")
    if portable_runner != "ubuntu-24.04":
        raise R7S5CIContractError("portable_runner_not_exact")
    for job in ("windows-platform-required", "private-artifact-required"):
        value = _single_job_value(blocks[job], "runs-on", job)
        if _parse_inline_list(value, job) != EXPECTED_WINDOWS_LABELS:
            raise R7S5CIContractError(f"trusted_runner_labels_not_exact:{job}")

    for lane, job in EXPECTED_LANE_JOBS.items():
        block = blocks[job]
        token = f"--lane {lane}"
        if _count_active_lines(block, token) != 1:
            raise R7S5CIContractError(f"lane_validator_call_not_exact:{lane}")
        if _count_active_lines(block, "python -m pytest") != 1:
            raise R7S5CIContractError(f"pytest_call_not_exact:{lane}")
        for required in ("--strict-config", "--strict-markers"):
            if _count_active_lines(block, required) != 1:
                raise R7S5CIContractError(f"pytest_strict_flag_not_exact:{lane}:{required}")
        # The lane files are already an exact, disjoint inventory.  A second
        # marker selector can silently reduce a required lane to zero tests
        # when a marker is missing or renamed, so selectors are forbidden.
        if re.search(
            r"(?s)\bpython\s+-m\s+pytest\b.*?(?:^|\s)-m(?:=|\s)",
            block,
        ):
            raise R7S5CIContractError(f"pytest_marker_selection_forbidden:{lane}")
        for relative in EXPECTED_LANE_FILES[lane]:
            if _count_active_lines(block, relative) != 1:
                raise R7S5CIContractError(f"lane_file_invocation_not_exact:{lane}:{relative}")
        if re.search(r"(?m)^    if:", block):
            raise R7S5CIContractError(f"lane_job_condition_forbidden:{lane}")

    closure = blocks["required-lane-closure"]
    if _single_job_value(closure, "runs-on", "closure") != "ubuntu-24.04":
        raise R7S5CIContractError("workflow_closure_runner_not_exact")
    needs = _parse_inline_list(_single_job_value(closure, "needs", "closure"), "closure_needs")
    if needs != (
        "portable-linux",
        "windows-platform-required",
        "private-artifact-required",
    ):
        raise R7S5CIContractError("workflow_closure_needs_not_exact")
    if _single_job_value(closure, "if", "closure") != "always()":
        raise R7S5CIContractError("workflow_closure_if_not_always")
    closure_command = manifest["workflow_contract"]["closure_command"]
    if _count_active_lines(closure, closure_command) != 1:
        raise R7S5CIContractError("workflow_closure_command_not_exact")
    if "validate_pre_r8_r7s5_ci.py manifest" in closure_command:
        raise R7S5CIContractError("workflow_required_closure_not_authenticated")

    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-workflow-validation.v1",
        "status": "local_structure_pass_external_provenance_unproven",
        "go_evidence_eligible": False,
        "jobs": list(blocks),
        "action_refs": action_refs,
        "remaining_blockers": list(EXPECTED_BLOCKERS),
    }


def _parse_timestamp(value: Any, label: str) -> datetime:
    text = _string(value, label)
    if not text.endswith("Z"):
        raise R7S5CIContractError(f"{label}_utc_z_required")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise R7S5CIContractError(f"{label}_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise R7S5CIContractError(f"{label}_timezone_required")
    return parsed.astimezone(UTC)


def _validate_receipt_envelope(
    receipt: Mapping[str, Any],
    *,
    expected_schema: str,
    expected: ReceiptBinding,
    now: datetime,
    verifier: SignatureVerifier | None,
) -> tuple[str, str, str]:
    if receipt["schema"] != expected_schema:
        raise R7S5CIContractError("receipt_schema_mismatch")
    receipt_id = _string(receipt["receipt_id"], "receipt_id")
    try:
        parsed_id = uuid.UUID(receipt_id)
    except ValueError as exc:
        raise R7S5CIContractError("receipt_id_uuid_required") from exc
    if parsed_id.version != 4 or str(parsed_id) != receipt_id:
        raise R7S5CIContractError("receipt_id_uuid4_canonical_required")
    nonce = _hex(receipt["nonce"], HEX64, "receipt_nonce")
    for field in ("repository", "workflow", "run_id", "run_uuid", "domain", "job"):
        if receipt[field] != getattr(expected, field):
            raise R7S5CIContractError(f"receipt_binding_mismatch:{field}")
    _uuid4(receipt["run_uuid"], "receipt_run_uuid")
    if _integer(receipt["run_attempt"], "receipt_run_attempt", minimum=1) != expected.run_attempt:
        raise R7S5CIContractError("receipt_binding_mismatch:run_attempt")
    if receipt["commit"] != expected.commit or HEX40.fullmatch(str(receipt["commit"])) is None:
        raise R7S5CIContractError("receipt_binding_mismatch:commit")
    if receipt["tree"] != expected.tree or HEX40.fullmatch(str(receipt["tree"])) is None:
        raise R7S5CIContractError("receipt_binding_mismatch:tree")
    if (
        _hex(receipt["toolchain_sha256"], HEX64, "receipt_toolchain_sha256")
        != expected.toolchain_sha256
    ):
        raise R7S5CIContractError("receipt_binding_mismatch:toolchain_sha256")
    issued = _parse_timestamp(receipt["issued_at"], "receipt_issued_at")
    expires = _parse_timestamp(receipt["expires_at"], "receipt_expires_at")
    if now.tzinfo is None or now.utcoffset() is None:
        raise R7S5CIContractError("receipt_validation_time_timezone_required")
    current = now.astimezone(UTC)
    if issued > current or expires <= current:
        raise R7S5CIContractError("receipt_stale_or_not_yet_valid")
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > 900:
        raise R7S5CIContractError("receipt_lifetime_not_bounded")
    authority = _mapping(receipt["authority"], "receipt_authority")
    _exact_keys(authority, {"issuer", "key_fingerprint"}, "receipt_authority")
    _string(authority["issuer"], "receipt_authority_issuer")
    fingerprint = _hex(authority["key_fingerprint"], HEX64, "receipt_key_fingerprint")
    signature = _string(receipt["signature"], "receipt_signature")
    if verifier is None:
        raise R7S5CIContractError("independent_signature_verifier_required")
    signed = dict(receipt)
    signed.pop("signature", None)
    if verifier(_canonical_json(signed), signature, fingerprint) is not True:
        raise R7S5CIContractError("receipt_signature_rejected")
    return receipt_id, nonce, authority["issuer"]


def _validate_runner_receipt_for_test(
    receipt: Mapping[str, Any],
    *,
    expected: ReceiptBinding,
    now: datetime,
    replay_guard: ReceiptReplayGuard,
    verifier: SignatureVerifier | None,
) -> VerifiedReceipt:
    _exact_keys(
        receipt,
        {
            "authority",
            "commit",
            "domain",
            "expires_at",
            "issued_at",
            "job",
            "nonce",
            "receipt_id",
            "repository",
            "run_attempt",
            "run_id",
            "run_uuid",
            "runner",
            "schema",
            "signature",
            "token",
            "toolchain",
            "toolchain_sha256",
            "tree",
            "workflow",
        },
        "runner_receipt",
    )
    if expected.domain not in ("windows", "private"):
        raise R7S5CIContractError("runner_receipt_domain_not_external")
    receipt_id, nonce, issuer = _validate_receipt_envelope(
        receipt,
        expected_schema=RUNNER_RECEIPT_SCHEMA,
        expected=expected,
        now=now,
        verifier=verifier,
    )
    runner = _mapping(receipt["runner"], "runner")
    _exact_keys(
        runner,
        {
            "group",
            "labels",
            "machine_identity_sha256",
            "machine_sid_sha256",
            "name",
            "os_build",
            "version",
        },
        "runner",
    )
    if runner["group"] != EXPECTED_WINDOWS_RUNNER_GROUP:
        raise R7S5CIContractError("runner_group_mismatch")
    if tuple(runner["labels"]) != EXPECTED_WINDOWS_LABELS:
        raise R7S5CIContractError("runner_labels_mismatch")
    for field in ("name", "os_build", "version"):
        _string(runner[field], f"runner_{field}")
    for field in ("machine_identity_sha256", "machine_sid_sha256"):
        _hex(runner[field], HEX64, f"runner_{field}")
    token = _mapping(receipt["token"], "runner_token")
    _exact_keys(token, {"administrator", "elevation_type", "integrity"}, "runner_token")
    if token["administrator"] is not True:
        raise R7S5CIContractError("runner_token_administrator_required")
    if token["integrity"] not in ("High", "System") or token["elevation_type"] != "Full":
        raise R7S5CIContractError("runner_token_high_full_required")
    toolchain = _mapping(receipt["toolchain"], "runner_toolchain")
    if tuple(sorted(toolchain)) != EXPECTED_TOOL_ROLES:
        raise R7S5CIContractError("runner_toolchain_roles_mismatch")
    for role in EXPECTED_TOOL_ROLES:
        pin = _mapping(toolchain[role], f"runner_toolchain:{role}")
        _exact_keys(pin, {"path", "sha256", "version"}, f"runner_toolchain:{role}")
        path = _string(pin["path"], f"runner_toolchain_path:{role}")
        if not PureWindowsPath(path).is_absolute():
            raise R7S5CIContractError(f"runner_toolchain_path_not_absolute:{role}")
        _hex(pin["sha256"], HEX64, f"runner_toolchain_sha256:{role}")
        _string(pin["version"], f"runner_toolchain_version:{role}")
    if hashlib.sha256(_canonical_json(toolchain)).hexdigest() != expected.toolchain_sha256:
        raise R7S5CIContractError("runner_toolchain_digest_mismatch")
    replay_guard.consume(("receipt", issuer, receipt_id, nonce))
    return VerifiedReceipt(
        receipt_id=receipt_id,
        nonce=nonce,
        issuer=issuer,
        domain=expected.domain,
        commit=expected.commit,
        tree=expected.tree,
        run_id=expected.run_id,
        run_uuid=expected.run_uuid,
        run_attempt=expected.run_attempt,
        job=expected.job,
        kind="runner",
        toolchain_sha256=expected.toolchain_sha256,
    )


def _validate_private_artifact_receipt_for_test(
    receipt: Mapping[str, Any],
    *,
    expected: ReceiptBinding,
    now: datetime,
    replay_guard: ReceiptReplayGuard,
    verifier: SignatureVerifier | None,
) -> VerifiedReceipt:
    _exact_keys(
        receipt,
        {
            "artifact",
            "authority",
            "commit",
            "domain",
            "expires_at",
            "issued_at",
            "job",
            "nonce",
            "receipt_id",
            "repository",
            "run_attempt",
            "run_id",
            "run_uuid",
            "schema",
            "signature",
            "tree",
            "toolchain_sha256",
            "workflow",
        },
        "private_receipt",
    )
    if expected.domain != "private":
        raise R7S5CIContractError("private_receipt_domain_required")
    receipt_id, nonce, issuer = _validate_receipt_envelope(
        receipt,
        expected_schema=PRIVATE_RECEIPT_SCHEMA,
        expected=expected,
        now=now,
        verifier=verifier,
    )
    artifact = _mapping(receipt["artifact"], "private_artifact")
    _exact_keys(
        artifact,
        {
            "acl_write_denied",
            "aggregate_sha256",
            "artifact_count",
            "directory_file_id",
            "filesystem",
            "manifest_sha256",
            "mount_read_only",
            "path_set_sha256",
            "reparse_component_count",
            "root",
            "total_bytes",
            "volume_serial",
        },
        "private_artifact",
    )
    root = _string(artifact["root"], "private_artifact_root")
    if not PureWindowsPath(root).is_absolute():
        raise R7S5CIContractError("private_artifact_root_not_absolute")
    for field in ("aggregate_sha256", "manifest_sha256", "path_set_sha256"):
        _hex(artifact[field], HEX64, f"private_artifact_{field}")
    for field in ("artifact_count", "total_bytes", "volume_serial"):
        _integer(artifact[field], f"private_artifact_{field}", minimum=1)
    if (
        _integer(
            artifact["reparse_component_count"],
            "private_artifact_reparse_component_count",
        )
        != 0
    ):
        raise R7S5CIContractError("private_artifact_reparse_component_present")
    if artifact["filesystem"] != "NTFS":
        raise R7S5CIContractError("private_artifact_filesystem_not_ntfs")
    if artifact["mount_read_only"] is not True or artifact["acl_write_denied"] is not True:
        raise R7S5CIContractError("private_artifact_not_read_only")
    _string(artifact["directory_file_id"], "private_artifact_directory_file_id")
    replay_guard.consume(("receipt", issuer, receipt_id, nonce))
    return VerifiedReceipt(
        receipt_id=receipt_id,
        nonce=nonce,
        issuer=issuer,
        domain=expected.domain,
        commit=expected.commit,
        tree=expected.tree,
        run_id=expected.run_id,
        run_uuid=expected.run_uuid,
        run_attempt=expected.run_attempt,
        job=expected.job,
        kind="private_artifact",
        toolchain_sha256=expected.toolchain_sha256,
    )


def _nodeid_sha256(nodeids: Sequence[str]) -> str:
    return hashlib.sha256(("\n".join(nodeids) + "\n").encode("utf-8")).hexdigest()


def _lane_result_sha256(result: Mapping[str, Any]) -> str:
    digest_payload = dict(result)
    digest_payload.pop("result_sha256", None)
    return hashlib.sha256(_canonical_json(digest_payload)).hexdigest()


def _validated_lane_nodes(value: Any, lane: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or value != sorted(set(value)):
        raise R7S5CIContractError(f"closure_nodeids_not_sorted_unique:{lane}")
    allowed_files = EXPECTED_LANE_FILES[lane]
    observed_files: set[str] = set()
    for node in value:
        if (
            not isinstance(node, str)
            or not node
            or "\r" in node
            or "\n" in node
            or not any(node.startswith(relative + "::") for relative in allowed_files)
        ):
            raise R7S5CIContractError(f"closure_node_domain_mismatch:{lane}")
        observed_files.add(node.split("::", 1)[0])
    if observed_files != set(allowed_files):
        raise R7S5CIContractError(f"closure_lane_file_inventory_gap:{lane}")
    return tuple(value)


def _validate_collection_inventory_receipt_for_test(
    receipt: Mapping[str, Any],
    *,
    expected: ReceiptBinding,
    now: datetime,
    replay_guard: ReceiptReplayGuard,
    verifier: SignatureVerifier | None,
) -> AttestedCollectionInventory:
    """Validate the out-of-band expected node inventory fixed before execution."""

    _exact_keys(
        receipt,
        {
            "authority",
            "commit",
            "domain",
            "expires_at",
            "inventory",
            "issued_at",
            "job",
            "nonce",
            "receipt_id",
            "repository",
            "run_attempt",
            "run_id",
            "run_uuid",
            "schema",
            "signature",
            "tree",
            "toolchain_sha256",
            "workflow",
        },
        "collection_inventory_receipt",
    )
    receipt_id, nonce, issuer = _validate_receipt_envelope(
        receipt,
        expected_schema=COLLECTION_INVENTORY_RECEIPT_SCHEMA,
        expected=expected,
        now=now,
        verifier=verifier,
    )
    inventory = _mapping(receipt["inventory"], "collection_inventory")
    _exact_keys(
        inventory,
        {
            "lane",
            "node_count",
            "nodeids",
            "nodeids_sha256",
            "scope_files",
            "scope_files_sha256",
        },
        "collection_inventory",
    )
    if inventory["lane"] != expected.domain:
        raise R7S5CIContractError("collection_inventory_lane_mismatch")
    scope_files = inventory["scope_files"]
    if (
        not isinstance(scope_files, list)
        or tuple(scope_files) != EXPECTED_LANE_FILES[expected.domain]
    ):
        raise R7S5CIContractError(f"collection_scope_files_mismatch:{expected.domain}")
    scope_sha = _hex(
        inventory["scope_files_sha256"],
        HEX64,
        f"collection_scope_files_sha:{expected.domain}",
    )
    if scope_sha != _nodeid_sha256(scope_files):
        raise R7S5CIContractError(f"collection_scope_files_sha_mismatch:{expected.domain}")
    nodes = _validated_lane_nodes(inventory["nodeids"], expected.domain)
    nodeids_sha256 = _hex(
        inventory["nodeids_sha256"],
        HEX64,
        f"collection_nodeids_sha:{expected.domain}",
    )
    if nodeids_sha256 != _nodeid_sha256(nodes):
        raise R7S5CIContractError(f"collection_nodeids_sha_mismatch:{expected.domain}")
    if _integer(
        inventory["node_count"],
        f"collection_node_count:{expected.domain}",
        minimum=1,
    ) != len(nodes):
        raise R7S5CIContractError(f"collection_node_count_mismatch:{expected.domain}")
    replay_guard.consume(("receipt", issuer, receipt_id, nonce))
    return AttestedCollectionInventory(
        receipt_id=receipt_id,
        receipt_sha256=hashlib.sha256(_canonical_json(receipt)).hexdigest(),
        issuer=issuer,
        issued_at=_parse_timestamp(receipt["issued_at"], "collection_issued_at"),
        lane=expected.domain,
        run_uuid=expected.run_uuid,
        toolchain_sha256=expected.toolchain_sha256,
        nodeids_sha256=nodeids_sha256,
        nodeids=nodes,
    )


def _validate_lane_result_receipt_for_test(
    receipt: Mapping[str, Any],
    *,
    expected: ReceiptBinding,
    now: datetime,
    replay_guard: ReceiptReplayGuard,
    verifier: SignatureVerifier | None,
) -> AttestedLaneResult:
    """Authenticate and validate a complete, exact lane execution result.

    The node inventory is intentionally inside the signed envelope.  A caller
    cannot supply a second, unauthenticated list of nodeids to the closure.
    """

    _exact_keys(
        receipt,
        {
            "authority",
            "commit",
            "domain",
            "expires_at",
            "issued_at",
            "job",
            "nonce",
            "receipt_id",
            "repository",
            "result",
            "run_attempt",
            "run_id",
            "run_uuid",
            "schema",
            "signature",
            "tree",
            "toolchain_sha256",
            "workflow",
        },
        "lane_result_receipt",
    )
    if expected.domain not in LANES:
        raise R7S5CIContractError("lane_result_receipt_domain_unknown")
    receipt_id, nonce, issuer = _validate_receipt_envelope(
        receipt,
        expected_schema=LANE_RESULT_RECEIPT_SCHEMA,
        expected=expected,
        now=now,
        verifier=verifier,
    )
    result = _mapping(receipt["result"], "lane_result")
    _exact_keys(
        result,
        {
            "artifact_receipt_id",
            "collected",
            "collection_inventory_receipt_id",
            "commit",
            "deselected",
            "errors",
            "executed",
            "failed",
            "job",
            "job_result",
            "lane",
            "nodeids",
            "nodeids_sha256",
            "passed",
            "result_sha256",
            "run_attempt",
            "run_id",
            "run_uuid",
            "runner_receipt_id",
            "selected",
            "skipped",
            "status",
            "tree",
            "toolchain_sha256",
            "xfailed",
            "xpassed",
        },
        "lane_result",
    )
    if result["lane"] != expected.domain or result["job"] != expected.job:
        raise R7S5CIContractError(f"closure_lane_identity_mismatch:{expected.domain}")
    if result["job_result"] != "success" or result["status"] != "passed":
        raise R7S5CIContractError(f"closure_lane_not_success:{expected.domain}")
    _integer(result["run_attempt"], "closure_lane_run_attempt", minimum=1)
    for field in (
        "commit",
        "tree",
        "run_id",
        "run_uuid",
        "run_attempt",
        "toolchain_sha256",
    ):
        if result[field] != getattr(expected, field):
            raise R7S5CIContractError(f"closure_lane_binding_mismatch:{expected.domain}:{field}")

    nodes = _validated_lane_nodes(result["nodeids"], expected.domain)
    nodeids_sha256 = _hex(result["nodeids_sha256"], HEX64, f"closure_nodeids_sha:{expected.domain}")
    if nodeids_sha256 != _nodeid_sha256(nodes):
        raise R7S5CIContractError(f"closure_nodeids_sha_mismatch:{expected.domain}")
    count = len(nodes)
    for field in ("collected", "selected", "executed", "passed"):
        if _integer(result[field], f"closure_count:{expected.domain}:{field}") != count:
            raise R7S5CIContractError(f"closure_count_mismatch:{expected.domain}:{field}")
    for field in ("failed", "errors", "skipped", "deselected", "xfailed", "xpassed"):
        if _integer(result[field], f"closure_nonpass:{expected.domain}:{field}") != 0:
            raise R7S5CIContractError(f"closure_nonpass_outcome:{expected.domain}:{field}")
    for field in (
        "collection_inventory_receipt_id",
        "runner_receipt_id",
        "artifact_receipt_id",
    ):
        if result[field] is not None:
            _string(result[field], f"closure_{field}:{expected.domain}")
    result_sha256 = _hex(result["result_sha256"], HEX64, f"closure_result_sha:{expected.domain}")
    if result_sha256 != _lane_result_sha256(result):
        raise R7S5CIContractError(f"closure_result_sha_mismatch:{expected.domain}")

    replay_guard.consume(("receipt", issuer, receipt_id, nonce))
    receipt_sha256 = hashlib.sha256(_canonical_json(receipt)).hexdigest()
    immutable_copy = json.loads(_canonical_json(result).decode("utf-8"))
    return AttestedLaneResult(
        receipt_id=receipt_id,
        receipt_sha256=receipt_sha256,
        issuer=issuer,
        issued_at=_parse_timestamp(receipt["issued_at"], "lane_result_issued_at"),
        lane=expected.domain,
        run_uuid=expected.run_uuid,
        toolchain_sha256=expected.toolchain_sha256,
        result_sha256=result_sha256,
        nodeids_sha256=nodeids_sha256,
        result=immutable_copy,
    )


def _validate_required_closure(
    manifest: Mapping[str, Any],
    collection_inventory_receipts: Mapping[str, Mapping[str, Any]],
    lane_result_receipts: Mapping[str, Mapping[str, Any]],
    *,
    repository: str,
    workflow: str,
    commit: str,
    tree: str,
    run_id: str,
    run_attempt: int,
    runner_receipts: Mapping[str, Mapping[str, Any]],
    private_artifact_receipt: Mapping[str, Any],
    now: datetime,
    replay_guard: ReceiptReplayGuard,
    verifier: SignatureVerifier | None,
    frozen_collection_contract: Mapping[str, Any] | None,
    frozen_collection_contract_sha256: str | None,
) -> dict[str, Any]:
    """Validate closure exclusively from signed raw receipts.

    Public ``VerifiedReceipt`` instances and caller-provided nodeid lists are
    deliberately not accepted.  Every authoritative result is decoded from a
    signed lane-result receipt in this function and replay-consumed once.
    """

    validate_manifest(manifest)
    if tuple(collection_inventory_receipts) != LANES:
        raise R7S5CIContractError("closure_collection_receipt_set_or_order_mismatch")
    if tuple(lane_result_receipts) != LANES:
        raise R7S5CIContractError("closure_lane_receipt_set_or_order_mismatch")
    if set(runner_receipts) != {"windows", "private"}:
        raise R7S5CIContractError("closure_runner_receipt_set_mismatch")
    repository = _string(repository, "closure_repository")
    workflow = _string(workflow, "closure_workflow")
    _hex(commit, HEX40, "closure_commit")
    _hex(tree, HEX40, "closure_tree")
    _string(run_id, "closure_run_id")
    _integer(run_attempt, "closure_run_attempt", minimum=1)
    if frozen_collection_contract is None:
        raise R7S5CIContractError("external_collection_authority_unprovisioned")
    expected_contract_sha256 = _hex(
        frozen_collection_contract_sha256,
        HEX64,
        "frozen_collection_contract_sha256",
    )
    observed_contract_sha256 = hashlib.sha256(
        _canonical_json(frozen_collection_contract)
    ).hexdigest()
    if observed_contract_sha256 != expected_contract_sha256:
        raise R7S5CIContractError("frozen_collection_contract_digest_mismatch")
    contract = _mapping(frozen_collection_contract, "frozen_collection_contract")
    _exact_keys(
        contract,
        {
            "commit",
            "lanes",
            "repository",
            "run_attempt",
            "run_id",
            "run_uuid",
            "schema",
            "tree",
            "workflow",
        },
        "frozen_collection_contract",
    )
    if contract["schema"] != COLLECTION_BINDING_SCHEMA:
        raise R7S5CIContractError("frozen_collection_contract_schema_mismatch")
    run_uuid = _uuid4(contract["run_uuid"], "frozen_collection_run_uuid")
    for field, expected_value in {
        "repository": repository,
        "workflow": workflow,
        "commit": commit,
        "tree": tree,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }.items():
        if contract[field] != expected_value:
            raise R7S5CIContractError(f"frozen_collection_binding_mismatch:{field}")
    contract_lanes = _mapping(contract["lanes"], "frozen_collection_lanes")
    if tuple(contract_lanes) != LANES:
        raise R7S5CIContractError("frozen_collection_contract_lane_set_or_order_mismatch")
    normalized_collection_contract: dict[str, dict[str, Any]] = {}
    for lane in LANES:
        pin = _mapping(contract_lanes[lane], f"frozen_collection:{lane}")
        _exact_keys(
            pin,
            {
                "collection_receipt_sha256",
                "job",
                "node_count",
                "nodeids_sha256",
                "toolchain_sha256",
            },
            f"frozen_collection:{lane}",
        )
        if pin["job"] != EXPECTED_LANE_JOBS[lane]:
            raise R7S5CIContractError(f"frozen_collection_job_mismatch:{lane}")
        normalized_collection_contract[lane] = {
            "node_count": _integer(
                pin["node_count"], f"frozen_collection_node_count:{lane}", minimum=1
            ),
            "nodeids_sha256": _hex(
                pin["nodeids_sha256"], HEX64, f"frozen_collection_nodeids_sha:{lane}"
            ),
            "receipt_sha256": _hex(
                pin["collection_receipt_sha256"],
                HEX64,
                f"frozen_collection_receipt_sha:{lane}",
            ),
            "toolchain_sha256": _hex(
                pin["toolchain_sha256"], HEX64, f"frozen_collection_toolchain_sha:{lane}"
            ),
        }

    bindings = {
        lane: ReceiptBinding(
            repository=repository,
            workflow=workflow,
            commit=commit,
            tree=tree,
            run_id=run_id,
            run_uuid=run_uuid,
            run_attempt=run_attempt,
            job=EXPECTED_LANE_JOBS[lane],
            domain=lane,
            toolchain_sha256=normalized_collection_contract[lane]["toolchain_sha256"],
        )
        for lane in LANES
    }
    verified_runners = {
        lane: _validate_runner_receipt_for_test(
            _mapping(runner_receipts[lane], f"runner_receipt:{lane}"),
            expected=bindings[lane],
            now=now,
            replay_guard=replay_guard,
            verifier=verifier,
        )
        for lane in ("windows", "private")
    }
    verified_artifact = _validate_private_artifact_receipt_for_test(
        _mapping(private_artifact_receipt, "private_artifact_receipt"),
        expected=bindings["private"],
        now=now,
        replay_guard=replay_guard,
        verifier=verifier,
    )
    attested_collections = {
        lane: _validate_collection_inventory_receipt_for_test(
            _mapping(
                collection_inventory_receipts[lane],
                f"collection_inventory_receipt:{lane}",
            ),
            expected=bindings[lane],
            now=now,
            replay_guard=replay_guard,
            verifier=verifier,
        )
        for lane in LANES
    }
    attested_results = {
        lane: _validate_lane_result_receipt_for_test(
            _mapping(lane_result_receipts[lane], f"lane_result_receipt:{lane}"),
            expected=bindings[lane],
            now=now,
            replay_guard=replay_guard,
            verifier=verifier,
        )
        for lane in LANES
    }

    all_nodes: list[str] = []
    seen: set[str] = set()
    receipt_ids: dict[str, str | None] = {}
    collection_receipt_ids: dict[str, str] = {}
    collection_receipt_digests: dict[str, str] = {}
    lane_result_receipt_ids: dict[str, str] = {}
    lane_result_receipt_digests: dict[str, str] = {}
    lane_result_digests: dict[str, str] = {}
    lane_nodeid_digests: dict[str, str] = {}
    for lane in LANES:
        attested = attested_results[lane]
        collection = attested_collections[lane]
        result = attested.result
        nodes = result["nodeids"]
        frozen = normalized_collection_contract[lane]
        if (
            frozen["node_count"] != len(collection.nodeids)
            or frozen["nodeids_sha256"] != collection.nodeids_sha256
            or frozen["receipt_sha256"] != collection.receipt_sha256
            or frozen["toolchain_sha256"] != collection.toolchain_sha256
        ):
            raise R7S5CIContractError(f"closure_frozen_collection_mismatch:{lane}")
        if (
            collection.issuer != attested.issuer
            or collection.issued_at >= attested.issued_at
            or result["collection_inventory_receipt_id"] != collection.receipt_id
            or tuple(nodes) != collection.nodeids
            or attested.nodeids_sha256 != collection.nodeids_sha256
            or attested.run_uuid != collection.run_uuid
            or attested.toolchain_sha256 != collection.toolchain_sha256
        ):
            raise R7S5CIContractError(f"closure_collection_inventory_mismatch:{lane}")
        for node in nodes:
            if node in seen:
                raise R7S5CIContractError("closure_node_overlap")
            seen.add(node)
        all_nodes.extend(nodes)
        collection_receipt_ids[lane] = collection.receipt_id
        collection_receipt_digests[lane] = collection.receipt_sha256
        lane_result_receipt_ids[lane] = attested.receipt_id
        lane_result_receipt_digests[lane] = attested.receipt_sha256
        lane_result_digests[lane] = attested.result_sha256
        lane_nodeid_digests[lane] = attested.nodeids_sha256

        if lane == "portable":
            if result["runner_receipt_id"] is not None or result["artifact_receipt_id"] is not None:
                raise R7S5CIContractError("portable_external_receipt_forbidden")
            receipt_ids[lane] = None
            continue
        verified = verified_runners[lane]
        if (
            verified.kind != "runner"
            or verified.domain != lane
            or verified.issuer != attested.issuer
            or result["runner_receipt_id"] != verified.receipt_id
        ):
            raise R7S5CIContractError(f"closure_runner_receipt_mismatch:{lane}")
        receipt_ids[lane] = verified.receipt_id
        if lane == "windows" and result["artifact_receipt_id"] is not None:
            raise R7S5CIContractError("windows_artifact_receipt_forbidden")
        if lane == "private":
            artifact = verified_artifact
            if (
                artifact.kind != "private_artifact"
                or artifact.domain != "private"
                or artifact.issuer != attested.issuer
                or result["artifact_receipt_id"] != artifact.receipt_id
            ):
                raise R7S5CIContractError("closure_private_artifact_receipt_mismatch")

    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-closure-engine-neutral.v1",
        "status": "internal_non_authoritative",
        "credit": "zero_credit",
        "contract_checks_satisfied": True,
        "production_closure_eligible": False,
        "go_evidence_eligible": False,
        "node_count": len(all_nodes),
        "commit": commit,
        "tree": tree,
        "run_id": run_id,
        "run_uuid": run_uuid,
        "run_attempt": run_attempt,
        "frozen_collection_contract_sha256": expected_contract_sha256,
        "nodeids_sha256": _nodeid_sha256(sorted(all_nodes)),
        "collection_inventory_receipt_ids": collection_receipt_ids,
        "collection_inventory_receipt_sha256": collection_receipt_digests,
        "lane_result_receipt_ids": lane_result_receipt_ids,
        "lane_result_receipt_sha256": lane_result_receipt_digests,
        "lane_result_sha256": lane_result_digests,
        "lane_nodeids_sha256": lane_nodeid_digests,
        "lane_node_counts": {
            lane: normalized_collection_contract[lane]["node_count"] for lane in LANES
        },
        "lane_toolchain_sha256": {
            lane: normalized_collection_contract[lane]["toolchain_sha256"] for lane in LANES
        },
        "runner_receipt_ids": receipt_ids,
        "private_artifact_receipt_id": verified_artifact.receipt_id,
        "remaining_blockers": list(EXPECTED_BLOCKERS),
    }


def _public_external_receipt_validation_unavailable() -> NoReturn:
    raise R7S5CIContractError("external_receipt_authority_adapter_unprovisioned")


def validate_runner_receipt(*_args: Any, **_kwargs: Any) -> NoReturn:
    _public_external_receipt_validation_unavailable()


def validate_private_artifact_receipt(*_args: Any, **_kwargs: Any) -> NoReturn:
    _public_external_receipt_validation_unavailable()


def validate_collection_inventory_receipt(*_args: Any, **_kwargs: Any) -> NoReturn:
    _public_external_receipt_validation_unavailable()


def validate_lane_result_receipt(*_args: Any, **_kwargs: Any) -> NoReturn:
    _public_external_receipt_validation_unavailable()


def validate_required_closure(
    manifest: Mapping[str, Any],
    collection_inventory_receipts: Mapping[str, Mapping[str, Any]],
    lane_result_receipts: Mapping[str, Mapping[str, Any]],
    *,
    repository: str,
    workflow: str,
    commit: str,
    tree: str,
    run_id: str,
    run_attempt: int,
    runner_receipts: Mapping[str, Mapping[str, Any]],
    private_artifact_receipt: Mapping[str, Any],
    now: datetime,
    replay_guard: ReceiptReplayGuard,
    verifier: SignatureVerifier | None,
) -> dict[str, Any]:
    """Production closure, intentionally unavailable pending both authorities.

    Populating collection pins alone must not silently promote a local replay
    folder into a WORM authority.  Enabling this entry point requires a later
    reviewed implementation that binds an external replay adapter and its
    attestation as well as the frozen collection contract.
    """

    if PINNED_EXTERNAL_COLLECTION_CONTRACT is None:
        raise R7S5CIContractError("external_collection_authority_unprovisioned")
    raise R7S5CIContractError("external_worm_replay_authority_adapter_not_implemented")


def _validate_required_closure_for_test(
    manifest: Mapping[str, Any],
    collection_inventory_receipts: Mapping[str, Mapping[str, Any]],
    lane_result_receipts: Mapping[str, Mapping[str, Any]],
    *,
    frozen_collection_contract: Mapping[str, Any],
    frozen_collection_contract_sha256: str,
    repository: str,
    workflow: str,
    commit: str,
    tree: str,
    run_id: str,
    run_attempt: int,
    runner_receipts: Mapping[str, Mapping[str, Any]],
    private_artifact_receipt: Mapping[str, Any],
    now: datetime,
    replay_guard: ReceiptReplayGuard,
    verifier: SignatureVerifier | None,
) -> dict[str, Any]:
    """Private seam that can never emit production-shaped closure evidence."""

    result = _validate_required_closure(
        manifest,
        collection_inventory_receipts,
        lane_result_receipts,
        repository=repository,
        workflow=workflow,
        commit=commit,
        tree=tree,
        run_id=run_id,
        run_attempt=run_attempt,
        runner_receipts=runner_receipts,
        private_artifact_receipt=private_artifact_receipt,
        now=now,
        replay_guard=replay_guard,
        verifier=verifier,
        frozen_collection_contract=frozen_collection_contract,
        frozen_collection_contract_sha256=frozen_collection_contract_sha256,
    )
    result.pop("required_lane_test_closure_passed", None)
    result.update(
        {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-required-closure-test-only.v1",
            "status": "test_only",
            "test_contract_logic_exercised": True,
            "production_closure_eligible": False,
            "go_evidence_eligible": False,
        }
    )
    return result


__all__ = [
    "COLLECTION_BINDING_SCHEMA",
    "COLLECTION_INVENTORY_RECEIPT_SCHEMA",
    "DurableReceiptReplayGuard",
    "LANES",
    "LANE_RESULT_RECEIPT_SCHEMA",
    "MANIFEST_SCHEMA",
    "PRIVATE_RECEIPT_SCHEMA",
    "RUNNER_RECEIPT_SCHEMA",
    "R7S5CIContractError",
    "ReceiptBinding",
    "ReceiptReplayGuard",
    "VerifiedReceipt",
    "load_and_validate_manifest",
    "load_manifest",
    "load_receipt",
    "validate_manifest",
    "validate_collection_inventory_receipt",
    "validate_lane_result_receipt",
    "validate_private_artifact_receipt",
    "validate_required_closure",
    "validate_runner_receipt",
    "validate_workflow_contract",
]

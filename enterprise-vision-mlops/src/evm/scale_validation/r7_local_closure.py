"""R7s5 local-code closure assessment and durable publication.

This module implements only the source-local R7 decision described by
``docs/agenda/2026-09-09-r7s5-local-code-closure-contract.md``.  Assessment
only establishes readiness for an independent review.  Even durable local
publication leaves ledger/canonical read-back pending and never asserts
formal CI, production admission, OOB/WORM authority, an R8 operational
result, or V4 acceptance credit.

The input manifest is canonical JSON with exact gate roles.  Each gate binds
its existing receipt and raw artifacts by path, byte count and SHA-256.  The
old aggregate remains NO-GO and is consumed only for its node inventory.
Review is a process-separated, procedural source-local review: it is not a
cryptographic identity claim.  The review hash is supplied separately from
the frozen manifest to avoid a circular/self-approved input.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-closure-manifest.v1"
ASSESSMENT_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-closure-assessment.v1"
REVIEW_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-closure-review.v1"
REVIEW_OBSERVATION_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-review-observation.v1"
PRODUCER_OBSERVATION_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-producer-observation.v1"
REVIEW_VALIDATION_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-review-validation.v1"
REPORT_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-closure-report.v1"
SEAL_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-closure-seal.v1"
INDEX_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-closure-index.v1"
PUBLICATION_SCHEMA = "evm.s8-v4.pre-r8.r7s5.local-code-closure-publication.v1"

REQUIRED_GATES = (
    "static",
    "focused",
    "general",
    "real_pg",
    "host",
    "linux_portable",
    "inverse",
    "private",
    "fresh",
    "elevated",
    "cleanup",
)
EXECUTION_GATES = (
    "focused",
    "general",
    "real_pg",
    "host",
    "linux_portable",
    "inverse",
    "elevated",
)
STATIC_ROLES = (
    "source-before-static",
    "ci-check",
    "ci-format",
    "m04-check",
    "m04-format",
    "container-tools-check",
    "container-tools-format",
    "in-memory-compile",
    "powershell-ast",
    "diff-working-check",
    "diff-candidate-check",
)
STATIC_LOG_KINDS = {
    *(f"stdout_{index:02d}" for index in range(len(STATIC_ROLES))),
    *(f"stderr_{index:02d}" for index in range(len(STATIC_ROLES))),
}
HOST_FILES = (
    "tests/test_pre_r8_r7s2_contract_stager.py",
    "tests/test_pre_r8_r7s2_outer_launcher.py",
    "tests/test_pre_r8_r7s2_wsl_qualification.py",
)
CONTAINER_TOOL_FILES = (
    "tools/container-tests/smoke.py",
    "tools/container-tests/run_container.py",
    "tools/container-tests/test_smoke.py",
    "tools/container-tests/freeze_corpus.py",
)
REQUIRED_KINDS = {
    "static": {"scope_contract", *STATIC_LOG_KINDS},
    "focused": {
        "result",
        "node_manifest",
        "stdout",
        "stderr",
        "collection_stdout",
        "collection_stderr",
        "junit",
        "postcondition",
        "runtime_provenance",
        "module_origin",
    },
    "general": {
        "result",
        "node_manifest",
        "stdout",
        "stderr",
        "collection_stdout",
        "collection_stderr",
        "junit",
        "run_intent",
        "exit",
        "postcondition",
        "runtime_provenance",
        "module_origin",
    },
    "real_pg": {
        "stdout",
        "stderr",
        "junit",
        "baseline",
        "postcondition",
        "runtime_provenance",
        "module_origin",
    },
    "host": {
        "stdout",
        "stderr",
        "junit",
        "runtime_provenance",
        "module_origin",
        "token_observation",
        "execution_manifest",
    },
    "linux_portable": {
        "stdout",
        "stderr",
        "junit",
        "intent",
        "collection_runtime",
        "execution_runtime",
        "execution_postcondition",
    },
    "inverse": {
        "stdout",
        "stderr",
        "junit",
        "intent",
        "collection_runtime",
        "execution_runtime",
        "collection_postcondition",
        "execution_postcondition",
    },
    "private": {"result", "junit", "input_before", "input_after"},
    "fresh": {"tool_before", "tool_after"},
    "elevated": {
        "execution_manifest",
        "stdout",
        "stderr",
        "junit",
        "runtime_provenance",
        "token_observation",
        "module_origin",
        "tool_snapshot",
        "runtime_script",
        "execution_script",
        "original_m01_manifest",
    },
    "cleanup": {"inventory"},
}
BOUNDARIES = {
    "scope": "r7s5_local_code_closure_only",
    "formal_ci": "not_required_not_claimed",
    "production_oob": "not_run",
    "production_worm": "not_run",
    "production_admission": "not_run",
    "r8_operational": "not_run",
    "acceptance_credit": False,
}
PROCESS_KEYS = {
    "role",
    "run_uuid",
    "pid",
    "create_time_utc",
    "executable_sha256",
    "script_sha256",
}
REF_KEYS = {"kind", "path", "bytes", "sha256"}


class R7LocalClosureError(RuntimeError):
    """The local R7 closure contract was not satisfied."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one accepted UTF-8 JSON representation."""

    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise R7LocalClosureError(f"{label}_mapping_required")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7LocalClosureError(f"{label}_keys_not_exact")


def _hex(value: Any, length: int, label: str) -> str:
    if not isinstance(value, str) or len(value) != length:
        raise R7LocalClosureError(f"{label}_invalid")
    try:
        int(value, 16)
    except ValueError as exc:
        raise R7LocalClosureError(f"{label}_invalid") from exc
    if value != value.lower():
        raise R7LocalClosureError(f"{label}_invalid")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise R7LocalClosureError(f"{label}_utc_required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise R7LocalClosureError(f"{label}_utc_required") from exc
    if parsed.tzinfo != UTC:
        raise R7LocalClosureError(f"{label}_utc_required")
    return parsed


def _now(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise R7LocalClosureError("now_timezone_required")
    return value.astimezone(UTC)


def _artifact_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise R7LocalClosureError(f"{label}_utc_required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise R7LocalClosureError(f"{label}_utc_required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise R7LocalClosureError(f"{label}_utc_required")
    return parsed.astimezone(UTC)


def _uuid(value: Any, label: str) -> str:
    try:
        parsed = str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise R7LocalClosureError(f"{label}_uuid_required") from exc
    if value != parsed:
        raise R7LocalClosureError(f"{label}_uuid_required")
    return parsed


def _process(value: Any, label: str) -> dict[str, Any]:
    item = _mapping(value, label)
    _exact_keys(item, PROCESS_KEYS, label)
    if not isinstance(item["role"], str) or not item["role"]:
        raise R7LocalClosureError(f"{label}_role_invalid")
    _uuid(item["run_uuid"], f"{label}_run")
    if isinstance(item["pid"], bool) or not isinstance(item["pid"], int) or item["pid"] <= 0:
        raise R7LocalClosureError(f"{label}_pid_invalid")
    _utc(item["create_time_utc"], f"{label}_create_time")
    _hex(item["executable_sha256"], 64, f"{label}_executable")
    _hex(item["script_sha256"], 64, f"{label}_script")
    return item


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise R7LocalClosureError("json_duplicate_key")
        value[key] = item
    return value


def _json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_json_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7LocalClosureError(f"{label}_json_invalid") from exc
    return _mapping(value, label)


def _under(path: Path, roots: Sequence[Path]) -> bool:
    target = os.path.normcase(str(path))
    for root in roots:
        try:
            if os.path.commonpath((target, os.path.normcase(str(root)))) == os.path.normcase(
                str(root)
            ):
                return True
        except ValueError:
            continue
    return False


def _read_ref(
    value: Any, roots: Sequence[Path], *, kind: str | None = None
) -> tuple[dict[str, Any], bytes]:
    ref = _mapping(value, "artifact_ref")
    _exact_keys(ref, REF_KEYS, "artifact_ref")
    if kind is not None and ref["kind"] != kind:
        raise R7LocalClosureError("artifact_ref_kind_mismatch")
    if not isinstance(ref["kind"], str) or not ref["kind"]:
        raise R7LocalClosureError("artifact_ref_kind_invalid")
    path = Path(str(ref["path"]))
    if not path.is_absolute():
        raise R7LocalClosureError("artifact_ref_path_not_absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise R7LocalClosureError("artifact_ref_missing") from exc
    if resolved != path or not resolved.is_file() or not _under(resolved, roots):
        raise R7LocalClosureError("artifact_ref_path_unsafe")
    raw = resolved.read_bytes()
    if (
        isinstance(ref["bytes"], bool)
        or not isinstance(ref["bytes"], int)
        or ref["bytes"] != len(raw)
        or _hex(ref["sha256"], 64, "artifact_ref_sha256") != hashlib.sha256(raw).hexdigest()
    ):
        raise R7LocalClosureError("artifact_ref_identity_mismatch")
    return ref, raw


def _tree_bound(value: Mapping[str, Any], expected_commit: str, expected_tree: str) -> None:
    trees = [value[key] for key in ("source_tree", "tested_tree", "TESTED_TREE") if key in value]
    commits = [
        value[key]
        for key in ("code_commit", "candidate_commit", "CANDIDATE_COMMIT", "HEAD")
        if key in value
    ]
    if not trees or any(item != expected_tree for item in trees):
        raise R7LocalClosureError("gate_tree_mismatch")
    if commits and any(item != expected_commit for item in commits):
        raise R7LocalClosureError("gate_commit_mismatch")


def _zero(value: Any) -> bool:
    return (type(value) is int and value == 0) or (type(value) is list and not value)


def _int_zero(value: Any) -> bool:
    return type(value) is int and value == 0


def _int_equal(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _sha_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _nodes(value: Any, label: str) -> list[str]:
    if type(value) is not list or any(not isinstance(item, str) or not item for item in value):
        raise R7LocalClosureError(f"{label}_invalid")
    if len(value) != len(set(value)):
        raise R7LocalClosureError(f"{label}_duplicate")
    return value


def _governed_skips(value: Any, label: str) -> dict[str, str]:
    if type(value) is not list:
        raise R7LocalClosureError(f"{label}_invalid")
    result: dict[str, str] = {}
    for entry in value:
        item = _mapping(entry, label)
        _exact_keys(item, {"nodeid", "reason"}, label)
        nodeid = item["nodeid"]
        reason = item["reason"]
        if (
            not isinstance(nodeid, str)
            or not nodeid
            or not isinstance(reason, str)
            or not reason
            or nodeid in result
        ):
            raise R7LocalClosureError(f"{label}_invalid")
        result[nodeid] = reason
    return result


def _junit_nodeid(case: ET.Element, expected: set[str]) -> str:
    classname = case.attrib.get("classname")
    name = case.attrib.get("name")
    if not classname or not name:
        raise R7LocalClosureError("junit_case_identity_missing")
    matches: list[str] = []
    for nodeid in expected:
        parts = nodeid.split("::")
        module = parts[0]
        test_name = parts[-1]
        expected_classname = module.removesuffix(".py").replace("/", ".").replace("\\", ".")
        if len(parts) > 2:
            expected_classname += "." + ".".join(parts[1:-1])
        if classname == expected_classname and name == test_name:
            matches.append(nodeid)
    if len(matches) != 1:
        raise R7LocalClosureError("junit_case_identity_mismatch")
    return matches[0]


def _junit(raw: bytes, expected_nodes: Sequence[str]) -> dict[str, str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise R7LocalClosureError("junit_invalid") from exc
    expected = set(expected_nodes)
    outcomes: dict[str, str] = {}
    for case in root.iter("testcase"):
        nodeid = _junit_nodeid(case, expected)
        if nodeid in outcomes:
            raise R7LocalClosureError("junit_case_duplicate")
        children = {child.tag.rsplit("}", 1)[-1]: child for child in case}
        terminal = {name for name in children if name in {"skipped", "failure", "error"}}
        if len(terminal) > 1:
            raise R7LocalClosureError("junit_case_outcome_ambiguous")
        if "failure" in terminal or "error" in terminal:
            outcomes[nodeid] = next(iter(terminal))
        elif "skipped" in terminal:
            outcomes[nodeid] = f"skipped:{children['skipped'].attrib.get('message', '')}"
        else:
            outcomes[nodeid] = "passed"
    if set(outcomes) != expected:
        raise R7LocalClosureError("junit_node_set_mismatch")
    return outcomes


def _hash_map(value: Any, label: str) -> dict[str, str]:
    items = _mapping(value, label)
    if not items:
        raise R7LocalClosureError(f"{label}_empty")
    for relative, digest in items.items():
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise R7LocalClosureError(f"{label}_path_invalid")
        _hex(digest, 64, f"{label}_sha256")
    return items


def _verify_source_snapshot(
    fresh: Mapping[str, Any],
    static: Mapping[str, Any],
    tool_snapshots: Sequence[Mapping[str, Any]],
) -> tuple[Path, dict[str, str]]:
    project = Path(str(fresh.get("project")))
    if not project.is_absolute():
        raise R7LocalClosureError("fresh_project_invalid")
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise R7LocalClosureError("fresh_project_invalid")
    source_hashes = _hash_map(fresh.get("source_hashes"), "fresh_source_hashes")
    for relative, expected in source_hashes.items():
        path = (project / relative).resolve(strict=True)
        if not path.is_file() or not _under(path, [project]):
            raise R7LocalClosureError("fresh_source_path_unsafe")
        if _sha_file(path)[1] != expected:
            raise R7LocalClosureError("fresh_source_changed")
    snapshots = [static, *tool_snapshots]
    for index, snapshot in enumerate(snapshots):
        subset = _hash_map(snapshot.get("source_hashes"), f"source_subset_{index}")
        if any(source_hashes.get(path) != digest for path, digest in subset.items()):
            raise R7LocalClosureError("source_snapshot_disagrees_with_fresh_checkout")
    return project, source_hashes


def _tool_projection(value: Mapping[str, Any], expected_tree: str) -> dict[str, Any]:
    required = {
        "source_hashes",
        "install_receipt_sha256",
        "executable",
        "executable_sha256",
        "actual_runtime_image",
        "actual_runtime_image_sha256",
        "python_version",
        "packages",
        "verified_hashed_files",
        "verified_hashed_bytes",
        "module_origins",
        "ruff_binary",
        "pth_files",
        "project_install",
    }
    if value.get("candidate_tree") != expected_tree or not required <= set(value):
        raise R7LocalClosureError("fresh_tool_provenance_incomplete")
    if not str(value.get("status", "")).startswith("PASS"):
        raise R7LocalClosureError("fresh_tool_provenance_rejected")
    _hex(value["executable_sha256"], 64, "tool_executable_sha256")
    _hex(value["actual_runtime_image_sha256"], 64, "tool_runtime_image_sha256")
    for path_key, sha_key in (
        ("executable", "executable_sha256"),
        ("actual_runtime_image", "actual_runtime_image_sha256"),
    ):
        path = Path(str(value[path_key])).resolve(strict=True)
        if not path.is_file() or _sha_file(path)[1] != value[sha_key]:
            raise R7LocalClosureError("fresh_tool_binary_changed")
    packages = _mapping(value["packages"], "tool_packages")
    if not packages:
        raise R7LocalClosureError("tool_packages_empty")
    for name, package in packages.items():
        item = _mapping(package, f"tool_package_{name}")
        if not isinstance(item.get("metadata_origin"), str):
            raise R7LocalClosureError("tool_package_origin_missing")
        _hex(item.get("record_sha256"), 64, "tool_package_record_sha256")
        if type(item.get("verified_hashed_files")) is not int or item["verified_hashed_files"] <= 0:
            raise R7LocalClosureError("tool_package_inventory_missing")
        record = (Path(item["metadata_origin"]) / "RECORD").resolve(strict=True)
        if not record.is_file() or _sha_file(record)[1] != item["record_sha256"]:
            raise R7LocalClosureError("tool_package_record_changed")
    ruff = _mapping(value["ruff_binary"], "ruff_binary")
    ruff_path = Path(str(ruff.get("path"))).resolve(strict=True)
    if not ruff_path.is_file() or _sha_file(ruff_path)[1] != ruff.get("sha256"):
        raise R7LocalClosureError("ruff_binary_changed")
    volatile = {"TASK", "GLOBAL_DECISION", "NEXT_TASK", "utc", "pid", "ppid", "create_time"}
    return {key: item for key, item in value.items() if key not in volatile}


def _verify_static_commands(
    receipt: Mapping[str, Any],
    artifacts: Mapping[str, bytes],
    tool: Mapping[str, Any],
    refs: Sequence[Mapping[str, Any]],
) -> None:
    contract = _json_bytes(artifacts["scope_contract"], "static_scope_contract")
    if (
        receipt.get("scope_origin")
        != next(ref["path"] for ref in refs if ref["kind"] == "scope_contract")
        or receipt.get("scope_sha256") != hashlib.sha256(artifacts["scope_contract"]).hexdigest()
    ):
        raise R7LocalClosureError("static_scope_contract_binding_rejected")
    ci_files = _nodes(receipt.get("ci_files"), "static_ci_files")
    m04_files = _nodes(receipt.get("m04_files"), "static_m04_files")
    tool_files = _nodes(receipt.get("additional_tool_files"), "static_tool_files")
    repository_ci = _mapping(contract.get("repository_ci"), "static_repository_ci")
    selected = _mapping(contract.get("r7_m04_selected"), "static_m04_selected")
    if (
        ci_files != repository_ci.get("files")
        or not set(selected.get("files", [])) <= set(m04_files)
        or tool_files != list(CONTAINER_TOOL_FILES)
        or not {
            "src/evm/scale_validation/r7_local_closure.py",
            "tests/test_r7_local_closure.py",
        }
        <= set(m04_files)
    ):
        raise R7LocalClosureError("static_scope_files_rejected")
    commands = receipt["commands"]
    if any(not commands[index]["name"].endswith(role) for index, role in enumerate(STATIC_ROLES)):
        raise R7LocalClosureError("static_command_roles_rejected")
    python = tool["executable"]
    ruff_specs = (
        (1, "check", ci_files),
        (2, "format", ci_files),
        (3, "check", m04_files),
        (4, "format", m04_files),
        (5, "check", tool_files),
        (6, "format", tool_files),
    )
    for index, action, files in ruff_specs:
        argv = commands[index]["argv"]
        marker = ["-m", "ruff", action]
        position = next(
            (
                offset
                for offset in range(len(argv) - len(marker) + 1)
                if argv[offset : offset + len(marker)] == marker
            ),
            -1,
        )
        required_option = "--no-cache" if action == "check" else "--check"
        if (
            argv[0] != python
            or position < 0
            or required_option not in argv[position + len(marker) :]
            or argv[-len(files) :] != files
        ):
            raise R7LocalClosureError("static_ruff_argv_rejected")
    compile_argv = commands[7]["argv"]
    compile_files = sorted(_hash_map(receipt.get("source_hashes"), "static_source_hashes"))
    if (
        compile_argv[0] != tool["executable"]
        or "-S" not in compile_argv
        or compile_argv[-len(compile_files) :] != compile_files
    ):
        raise R7LocalClosureError("static_compile_argv_rejected")
    ast_argv = commands[8]["argv"]
    if (
        Path(ast_argv[0]).name.lower() != "powershell.exe"
        or ast_argv[1:3] != ["-NoProfile", "-NonInteractive"]
        or "Parser]::ParseFile" not in ast_argv[-1]
    ):
        raise R7LocalClosureError("static_powershell_ast_argv_rejected")
    for index, tail in (
        (0, ["diff", "--quiet", "--no-ext-diff"]),
        (9, ["diff", "--check"]),
        (10, ["diff", "--cached", "--check", "HEAD", "--"]),
    ):
        if commands[index]["argv"][-len(tail) :] != tail:
            raise R7LocalClosureError("static_git_argv_rejected")


def _verify_private_inputs(
    before_raw: bytes,
    after_raw: bytes,
    expected_tree: str,
) -> int:
    before = _json_bytes(before_raw, "private_inputs_before")
    after = _json_bytes(after_raw, "private_inputs_after")
    for value in (before, after):
        if (
            value.get("status") != "PASS_READONLY_IDENTITY"
            or value.get("source_tree") != expected_tree
            or value.get("mutations") != []
            or value.get("execution_credit") is not False
            or value.get("authority_credit") is not False
        ):
            raise R7LocalClosureError("private_input_snapshot_rejected")
    if (
        before.get("stage") != "before-private"
        or after.get("stage") != "after-private"
        or not _artifact_utc(before.get("utc"), "private_before")
        < _artifact_utc(after.get("utc"), "private_after")
    ):
        raise R7LocalClosureError("private_input_snapshot_order_rejected")
    ignored = {"stage", "utc", "temporal_limit"}
    if {key: value for key, value in before.items() if key not in ignored} != {
        key: value for key, value in after.items() if key not in ignored
    }:
        raise R7LocalClosureError("private_inputs_changed")
    entries = before.get("entries")
    if type(entries) is not list or not entries:
        raise R7LocalClosureError("private_input_entries_missing")
    for entry in entries:
        item = _mapping(entry, "private_input_entry")
        try:
            path = Path(str(item.get("path"))).resolve(strict=True)
        except OSError as exc:
            raise R7LocalClosureError("private_input_missing") from exc
        if not path.is_file():
            raise R7LocalClosureError("private_input_missing")
        size, digest = _sha_file(path)
        if item.get("bytes") != size or item.get("sha256") != digest:
            raise R7LocalClosureError("private_input_identity_changed")
    return len(entries)


def _verify_module_origin(
    raw: bytes,
    project: Path,
    source_hashes: Mapping[str, str],
) -> int:
    origin = _json_bytes(raw, "module_origin")
    if (
        Path(str(origin.get("project"))).resolve(strict=True) != project
        or origin.get("all_project_sources_within_fresh_checkout") is not True
    ):
        raise R7LocalClosureError("fresh_module_origin_rejected")
    roles: dict[str, Any] = {}
    for key in ("loaded_project_modules", "additional_source_roles"):
        roles.update(_mapping(origin.get(key), f"module_origin_{key}"))
    if not roles:
        raise R7LocalClosureError("fresh_module_origin_empty")
    for value in roles.values():
        item = _mapping(value, "module_origin_item")
        path = Path(str(item.get("path"))).resolve(strict=True)
        if not path.is_file() or not _under(path, [project]):
            raise R7LocalClosureError("fresh_module_origin_path_rejected")
        relative = path.relative_to(project).as_posix()
        digest = _sha_file(path)[1]
        if item.get("sha256") != digest or source_hashes.get(relative) != digest:
            raise R7LocalClosureError("fresh_module_origin_hash_mismatch")
    return len(roles)


def _verify_runtime_provenance(raw: bytes, project: Path) -> dict[str, Any]:
    runtime = _json_bytes(raw, "runtime_provenance")
    image = Path(str(runtime.get("image"))).resolve(strict=True)
    if (
        not image.is_file()
        or _sha_file(image)[1] != runtime.get("image_sha256")
        or Path(str(runtime.get("cwd"))).resolve(strict=True) != project
        or runtime.get("isolated") not in (1, True)
        or runtime.get("no_site") not in (1, True)
        or runtime.get("dont_write_bytecode") is not True
    ):
        raise R7LocalClosureError("runtime_provenance_rejected")
    return runtime


def _verify_postcondition(role: str, raw: bytes) -> None:
    value = _json_bytes(raw, f"{role}_postcondition")
    if role in {"focused", "general"}:
        active_key = (
            "recorded_execution_processes_active"
            if role == "focused"
            else "active_recorded_run_processes"
        )
        if (
            not _int_zero(value.get("exit_code"))
            or value.get("candidate_index_worktree_unchanged") is not True
            or not _int_zero(value.get(active_key))
        ):
            raise R7LocalClosureError(f"{role}_postcondition_rejected")
        if role == "general" and (
            value.get("package_and_tool_bytes_before_after_equal") is not True
            or type(value.get("installed_hashed_files_reverified")) is not int
            or value["installed_hashed_files_reverified"] <= 0
            or value.get("private_inputs_pre_run_and_post_equal") is not True
        ):
            raise R7LocalClosureError("general_postcondition_rejected")


def _verify_linux_raw(
    role: str, receipt: Mapping[str, Any], artifacts: Mapping[str, bytes]
) -> None:
    intent = _json_bytes(artifacts["intent"], f"{role}_intent")
    collection = _json_bytes(artifacts["collection_runtime"], f"{role}_collection")
    execution = _json_bytes(artifacts["execution_runtime"], f"{role}_execution")
    expected = receipt["node_ids"]
    if (
        intent.get("code_commit") != receipt.get("code_commit")
        or intent.get("source_tree") != receipt.get("source_tree")
        or type(intent.get("files")) is not list
        or not intent["files"]
        or intent.get("expected_skips") != []
        or collection.get("node_ids") != expected
        or execution.get("node_ids") != expected
        or collection.get("started") != []
        or collection.get("deselected") != []
        or set(execution.get("started", [])) != set(expected)
        or set(execution.get("finished", [])) != set(expected)
        or execution.get("deselected") != []
        or not _int_zero(execution.get("exit_code"))
        or receipt.get("existing_container_identities_unchanged") is not True
    ):
        raise R7LocalClosureError(f"{role}_runtime_artifacts_rejected")
    postconditions = ["execution_postcondition"]
    if role == "inverse":
        postconditions.insert(0, "collection_postcondition")
    for kind in postconditions:
        state = _json_bytes(artifacts[kind], f"{role}_{kind}")
        if state.get("running") is not False or not _int_zero(state.get("exit_code")):
            raise R7LocalClosureError(f"{role}_{kind}_rejected")


def _verify_host_scope(
    raw: bytes,
    receipt: Mapping[str, Any],
    expected_tree: str,
) -> None:
    manifest = _json_bytes(raw, "host_execution_manifest")
    nodes = receipt["actual_nodes"]
    if (
        manifest.get("source_tree") != expected_tree
        or manifest.get("files") != list(HOST_FILES)
        or manifest.get("node_ids") != nodes
        or not _int_equal(manifest.get("node_count"), len(nodes))
        or manifest.get("expected_skips") != []
        or not _int_zero(manifest.get("collection_exit_code"))
        or manifest.get("command", [])[-len(HOST_FILES) :] != list(HOST_FILES)
    ):
        raise R7LocalClosureError("host_execution_scope_rejected")


def _private_nodes_from_source(project: Path, general_nodes: Sequence[str]) -> list[str]:
    lane_path = project / "ci" / "pre-r8-r7s5-test-lanes.json"
    lanes = _json_bytes(lane_path.read_bytes(), "current_test_lanes")
    file_inventory = _mapping(lanes.get("file_inventory"), "test_lane_file_inventory")
    lane_map = _mapping(file_inventory.get("lanes"), "test_lanes")
    private_files = _nodes(lane_map.get("private"), "private_lane_files")
    selected = [
        nodeid
        for nodeid in general_nodes
        if any(nodeid.startswith(f"{path}::") for path in private_files)
    ]
    if not selected or any(
        not any(nodeid.startswith(f"{path}::") for nodeid in general_nodes)
        for path in private_files
    ):
        raise R7LocalClosureError("private_source_scope_empty")
    return selected


def _verify_full_tool_snapshot(raw: bytes, expected_receipt_sha256: str) -> int:
    snapshot = _json_bytes(raw, "elevated_tool_snapshot")
    _exact_keys(
        snapshot,
        {"files", "package_record_hashes", "prior_pinned_receipt_sha256", "purpose"},
        "elevated_tool_snapshot",
    )
    files = _mapping(snapshot["files"], "elevated_tool_files")
    records = _mapping(snapshot["package_record_hashes"], "elevated_package_records")
    if (
        not files
        or not records
        or snapshot["prior_pinned_receipt_sha256"] != expected_receipt_sha256
    ):
        raise R7LocalClosureError("elevated_tool_snapshot_binding_rejected")
    for raw_path, expected_sha in files.items():
        _hex(expected_sha, 64, "elevated_tool_file_sha256")
        try:
            path = Path(str(raw_path)).resolve(strict=True)
        except OSError as exc:
            raise R7LocalClosureError("elevated_tool_file_missing") from exc
        if not path.is_file() or _sha_file(path)[1] != expected_sha:
            raise R7LocalClosureError("elevated_tool_file_changed")
    for expected_sha in records.values():
        _hex(expected_sha, 64, "elevated_package_record_sha256")
    return len(files)


def _verify_elevated_raw(
    receipt: Mapping[str, Any],
    artifacts: Mapping[str, bytes],
    refs: Sequence[Mapping[str, Any]],
    project: Path,
    source_hashes: Mapping[str, str],
    expected_commit: str,
    expected_tree: str,
    expected_tool_receipt_sha256: str,
) -> dict[str, Any]:
    execution = _json_bytes(artifacts["execution_manifest"], "elevated_execution_manifest")
    _tree_bound(execution, expected_commit, expected_tree)
    nodes = _nodes(receipt.get("actual_node_ids"), "elevated_nodes")
    command = execution.get("command")
    command_nodes = [item for item in command or [] if isinstance(item, str) and "::" in item]
    if (
        len(nodes) != 2
        or execution.get("node_ids") != nodes
        or execution.get("node_count") != 2
        or execution.get("expected_passed") != 2
        or execution.get("expected_skips") != []
        or not _int_zero(execution.get("expected_failed"))
        or not _int_zero(execution.get("expected_errors"))
        or not _int_zero(execution.get("expected_deselected"))
        or command_nodes != nodes
        or not _int_zero(execution.get("collection_exit_code"))
    ):
        raise R7LocalClosureError("elevated_manifest_rejected")
    raw_manifest_sha = hashlib.sha256(artifacts["execution_manifest"]).hexdigest()
    original_sha = hashlib.sha256(artifacts["original_m01_manifest"]).hexdigest()
    original_ref = next(ref for ref in refs if ref["kind"] == "original_m01_manifest")
    if (
        receipt.get("manifest_sha256") != raw_manifest_sha
        or receipt.get("original_M01_manifest_sha256") != original_sha
        or execution.get("original_M01_manifest_sha256") != original_sha
        or receipt.get("original_M01_manifest_path") != original_ref["path"]
        or execution.get("original_M01_manifest_path") != original_ref["path"]
        or any(node.encode("utf-8") not in artifacts["original_m01_manifest"] for node in nodes)
        or receipt.get("run_uuid") != execution.get("run_uuid")
        or receipt.get("attempt_uuid") != execution.get("attempt_uuid")
    ):
        raise R7LocalClosureError("elevated_capsule_binding_rejected")
    expected_raw = {
        "tool_snapshot": execution.get("tool_snapshot_sha256"),
        "runtime_script": execution.get("bootstrap_sha256"),
        "execution_script": execution.get("executor_observer_script_sha256"),
    }
    tool_file_count = _verify_full_tool_snapshot(
        artifacts["tool_snapshot"], expected_tool_receipt_sha256
    )
    for kind, expected_sha in expected_raw.items():
        if hashlib.sha256(artifacts[kind]).hexdigest() != expected_sha:
            raise R7LocalClosureError("elevated_tool_or_script_mismatch")
    artifact_receipts = _mapping(receipt.get("artifact_receipts"), "elevated_artifacts")
    for ref in refs[1:]:
        name = Path(str(ref["path"])).name
        if ref["kind"] != "original_m01_manifest" and artifact_receipts.get(name) != ref["sha256"]:
            raise R7LocalClosureError("elevated_artifact_receipt_mismatch")
    runtime = _verify_runtime_provenance(artifacts["runtime_provenance"], project)
    _verify_module_origin(artifacts["module_origin"], project, source_hashes)
    observation = _json_bytes(artifacts["token_observation"], "elevated_token_observation")
    token = _mapping(receipt.get("observed_token"), "elevated_observed_token")
    executor_token = _mapping(
        receipt.get("observed_executor_token"), "elevated_observed_executor_token"
    )
    independent = _mapping(receipt.get("independent_review"), "elevated_independent_review")
    if (
        observation.get("pid") != runtime.get("pid")
        or observation.get("image_sha256") != runtime.get("image_sha256")
        or token.get("pid") != runtime.get("pid")
        or executor_token.get("pid") != observation.get("observer_pid")
        or independent.get("runtime_pid") != runtime.get("pid")
        or independent.get("observer_pid") != observation.get("observer_pid")
        or any(
            value.get("administrator") is not True
            or value.get("integrity") != "High"
            or value.get("token_elevation_type") != "Full"
            for value in (token, executor_token)
        )
        or independent.get("mock_PIDs_credited") is not False
        or not str(independent.get("decision", "")).startswith("PASS_EXACT_TWO_NODE")
        or type(receipt.get("tool_files_verified_before_after")) is not int
        or receipt["tool_files_verified_before_after"] != tool_file_count
    ):
        raise R7LocalClosureError("elevated_independent_observation_rejected")
    junit = _junit(artifacts["junit"], nodes)
    if junit != dict.fromkeys(nodes, "passed"):
        raise R7LocalClosureError("elevated_junit_rejected")
    return {"nodes": nodes, "passed": nodes, "skipped": []}


def _result_gate(
    role: str,
    receipt: Mapping[str, Any],
    artifacts: Mapping[str, bytes],
    expected_commit: str,
    expected_tree: str,
) -> dict[str, Any]:
    result = _json_bytes(artifacts["result"], f"{role}_result")
    manifest = _json_bytes(artifacts["node_manifest"], f"{role}_node_manifest")
    _tree_bound(receipt, expected_commit, expected_tree)
    _tree_bound(result, expected_commit, expected_tree)
    _tree_bound(manifest, expected_commit, expected_tree)
    actual = _nodes(result.get("actual_node_ids"), f"{role}_actual_nodes")
    expected = _nodes(manifest.get("node_ids"), f"{role}_manifest_nodes")
    try:
        collection_lines = artifacts["collection_stdout"].decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise R7LocalClosureError(f"{role}_collection_stdout_invalid") from exc
    collection_nodes = [
        line.strip() for line in collection_lines if line.startswith("tests/") and "::" in line
    ]
    if (
        collection_nodes != expected
        or manifest.get("collection_stdout_sha256")
        != hashlib.sha256(artifacts["collection_stdout"]).hexdigest()
    ):
        raise R7LocalClosureError(f"{role}_collection_output_mismatch")
    skipped_items = result.get("skipped")
    if type(skipped_items) is not list or any(type(item) is not dict for item in skipped_items):
        raise R7LocalClosureError(f"{role}_skips_invalid")
    skipped_nodes = _nodes([item.get("nodeid") for item in skipped_items], f"{role}_skipped_nodes")
    skipped = {
        item["nodeid"]: item.get("reason")
        for item in skipped_items
        if isinstance(item.get("reason"), str) and item.get("reason")
    }
    governed = _governed_skips(manifest.get("expected_skips"), f"{role}_governed_skips")
    if role == "general" and manifest.get("files") != [
        "tests",
        *(f"--ignore={path}" for path in HOST_FILES),
    ]:
        raise R7LocalClosureError("general_collection_scope_rejected")
    if (
        not str(receipt.get("status", "")).startswith("PASS")
        or not str(result.get("status", "")).startswith("PASS")
        or not _int_zero(result.get("exit_code"))
        or set(actual) != set(expected)
        or len(actual) != len(expected)
        or skipped != governed
        or result.get("exact_expected_skips") is not True
        or any(not _zero(result.get(key)) for key in ("failed", "errors", "missing", "extra"))
        or not _int_zero(result.get("deselected"))
        or not _int_zero(result.get("duplicate_count"))
        or result.get("source_hashes_unchanged") is not True
        or not _int_equal(result.get("collected"), len(actual))
        or not _int_equal(result.get("junit_cases"), len(actual))
        or not _int_equal(result.get("passed"), len(actual) - len(skipped_nodes))
    ):
        raise R7LocalClosureError(f"{role}_result_rejected")
    junit = _junit(artifacts["junit"], expected)
    expected_junit = {
        nodeid: f"skipped:{governed[nodeid]}" if nodeid in governed else "passed"
        for nodeid in expected
    }
    if junit != expected_junit:
        raise R7LocalClosureError(f"{role}_junit_mismatch")
    return {
        "nodes": expected,
        "passed": sorted(set(expected) - set(skipped_nodes)),
        "skipped": sorted(skipped_nodes),
    }


def _all_pass_gate(
    role: str,
    receipt: Mapping[str, Any],
    artifacts: Mapping[str, bytes],
    expected_commit: str,
    expected_tree: str,
) -> dict[str, Any]:
    _tree_bound(receipt, expected_commit, expected_tree)
    node_key = "actual_nodes" if "actual_nodes" in receipt else "node_ids"
    nodes = _nodes(receipt.get(node_key), f"{role}_nodes")
    if (
        not str(receipt.get("status", "")).startswith("PASS")
        or not _int_zero(receipt.get("exit_code"))
        or not _int_equal(receipt.get("collected"), len(nodes))
        or (role in {"real_pg", "host"} and not _int_equal(receipt.get("junit_cases"), len(nodes)))
        or not _int_equal(receipt.get("passed"), len(nodes))
        or not _int_zero(receipt.get("deselected"))
        or not _zero(receipt.get("nonpass", []))
        or receipt.get("source_unchanged") is not True
        or (role != "host" and not _int_zero(receipt.get("owned_container_residue")))
    ):
        raise R7LocalClosureError(f"{role}_receipt_rejected")
    junit = _junit(artifacts["junit"], nodes)
    if junit != dict.fromkeys(nodes, "passed"):
        raise R7LocalClosureError(f"{role}_junit_mismatch")
    return {"nodes": nodes, "passed": nodes, "skipped": []}


def _identity_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()


def assess(
    manifest_path: Path,
    *,
    expected_commit: str,
    expected_tree: str,
    now: datetime,
) -> dict[str, Any]:
    """Validate all R7 inputs and return a deterministic, non-production assessment."""

    commit = _hex(expected_commit, 40, "expected_commit")
    tree = _hex(expected_tree, 40, "expected_tree")
    try:
        resolved_manifest = Path(manifest_path).resolve(strict=True)
    except OSError as exc:
        raise R7LocalClosureError("manifest_missing") from exc
    raw = resolved_manifest.read_bytes()
    manifest = _json_bytes(raw, "manifest")
    if raw != canonical_json_bytes(manifest):
        raise R7LocalClosureError("manifest_not_canonical")
    _exact_keys(
        manifest,
        {
            "schema",
            "created_at_utc",
            "expires_at_utc",
            "candidate",
            "producer",
            "producer_observation",
            "evidence_roots",
            "gates",
            "boundaries",
        },
        "manifest",
    )
    if manifest["schema"] != MANIFEST_SCHEMA or manifest["boundaries"] != BOUNDARIES:
        raise R7LocalClosureError("manifest_contract_mismatch")
    candidate = _mapping(manifest["candidate"], "candidate")
    _exact_keys(candidate, {"commit", "tree"}, "candidate")
    if candidate != {"commit": commit, "tree": tree}:
        raise R7LocalClosureError("manifest_candidate_mismatch")
    created = _utc(manifest["created_at_utc"], "manifest_created")
    expires = _utc(manifest["expires_at_utc"], "manifest_expires")
    current = _now(now)
    if not created <= current < expires:
        raise R7LocalClosureError("manifest_stale")
    producer = _process(manifest["producer"], "producer")
    if producer["role"] != "r7_local_manifest_producer":
        raise R7LocalClosureError("producer_role_invalid")
    if type(manifest["evidence_roots"]) is not list or not manifest["evidence_roots"]:
        raise R7LocalClosureError("evidence_roots_invalid")
    roots = [Path(str(item)).resolve(strict=True) for item in manifest["evidence_roots"]]
    if any(not root.is_dir() for root in roots) or len(roots) != len(set(roots)):
        raise R7LocalClosureError("evidence_roots_invalid")
    producer_ref, producer_raw = _read_ref(
        manifest["producer_observation"], roots, kind="producer_observation"
    )
    producer_observation = _json_bytes(producer_raw, "producer_observation")
    if producer_raw != canonical_json_bytes(producer_observation):
        raise R7LocalClosureError("producer_observation_not_canonical")
    _exact_keys(
        producer_observation,
        {
            "schema",
            "candidate",
            "run_uuid",
            "pid",
            "create_time_utc",
            "executable_path",
            "executable_sha256",
            "script_path",
            "script_sha256",
            "observed_at_utc",
            "method",
        },
        "producer_observation",
    )
    try:
        producer_executable = Path(str(producer_observation["executable_path"])).resolve(
            strict=True
        )
        producer_script = Path(str(producer_observation["script_path"])).resolve(strict=True)
    except OSError as exc:
        raise R7LocalClosureError("producer_observed_files_changed") from exc
    if (
        producer_observation["schema"] != PRODUCER_OBSERVATION_SCHEMA
        or producer_observation["candidate"] != candidate
        or producer_observation["run_uuid"] != producer["run_uuid"]
        or producer_observation["pid"] != producer["pid"]
        or producer_observation["create_time_utc"] != producer["create_time_utc"]
        or producer_observation["executable_sha256"] != producer["executable_sha256"]
        or producer_observation["script_sha256"] != producer["script_sha256"]
        or producer_observation["method"] != "os_process_observation"
        or not producer_executable.is_file()
        or not producer_script.is_file()
        or _sha_file(producer_executable)[1] != producer["executable_sha256"]
        or _sha_file(producer_script)[1] != producer["script_sha256"]
        or not _utc(producer["create_time_utc"], "producer_create_time")
        <= _utc(producer_observation["observed_at_utc"], "producer_observed_at")
        <= created
    ):
        raise R7LocalClosureError("producer_observation_rejected")
    gates = _mapping(manifest["gates"], "gates")
    _exact_keys(gates, set(REQUIRED_GATES), "gates")
    values: dict[str, dict[str, Any]] = {}
    material: dict[str, dict[str, bytes]] = {}
    refs: dict[str, list[dict[str, Any]]] = {}
    for role in REQUIRED_GATES:
        gate = _mapping(gates[role], f"gate_{role}")
        _exact_keys(gate, {"receipt", "artifacts"}, f"gate_{role}")
        receipt_ref, receipt_raw = _read_ref(gate["receipt"], roots, kind="receipt")
        if type(gate["artifacts"]) is not list or not gate["artifacts"]:
            raise R7LocalClosureError(f"gate_{role}_artifacts_required")
        gate_refs = [receipt_ref]
        by_kind: dict[str, bytes] = {}
        seen_paths = {receipt_ref["path"]: (receipt_ref["bytes"], receipt_ref["sha256"])}
        for artifact in gate["artifacts"]:
            ref, artifact_raw = _read_ref(artifact, roots)
            prior_identity = seen_paths.get(ref["path"])
            if ref["kind"] in by_kind or (
                prior_identity is not None and prior_identity != (ref["bytes"], ref["sha256"])
            ):
                raise R7LocalClosureError(f"gate_{role}_artifact_duplicate")
            seen_paths[ref["path"]] = (ref["bytes"], ref["sha256"])
            by_kind[ref["kind"]] = artifact_raw
            gate_refs.append(ref)
        if not REQUIRED_KINDS[role] <= set(by_kind):
            raise R7LocalClosureError(f"gate_{role}_raw_artifacts_missing")
        receipt = _json_bytes(receipt_raw, f"{role}_receipt")
        receipt_artifacts = receipt.get("artifacts", {})
        if type(receipt_artifacts) is dict:
            for ref in gate_refs[1:]:
                if (
                    Path(ref["path"]).name in receipt_artifacts
                    and receipt_artifacts[Path(ref["path"]).name] != ref["sha256"]
                ):
                    raise R7LocalClosureError(f"gate_{role}_embedded_artifact_mismatch")
        values[role], material[role], refs[role] = receipt, by_kind, gate_refs

    static = values["static"]
    _tree_bound(static, commit, tree)
    commands = static.get("commands")
    if (
        static.get("status") != "PASS"
        or type(commands) is not list
        or len(commands) != 11
        or any(
            type(command) is not dict
            or not isinstance(command.get("name"), str)
            or type(command.get("argv")) is not list
            or not command["argv"]
            or not _int_zero(command.get("exit_code"))
            for command in commands
        )
        or len({command["name"] for command in commands}) != len(commands)
    ):
        raise R7LocalClosureError("static_gate_rejected")
    outcomes = {
        role: _result_gate(role, values[role], material[role], commit, tree)
        for role in ("focused", "general")
    }
    outcomes.update(
        {
            role: _all_pass_gate(role, values[role], material[role], commit, tree)
            for role in ("real_pg", "host", "linux_portable", "inverse")
        }
    )
    fresh = values["fresh"]
    _tree_bound(fresh, commit, tree)
    if (
        not str(fresh.get("status", "")).startswith("PASS_CLEAN_EXACT")
        or fresh.get("detached") is not True
        or fresh.get("clean_status") is not True
        or fresh.get("object_alternates") is not False
        or fresh.get("hardlinked_objects") is not False
        or fresh.get("existing_checkout_runtime_reference") is not False
    ):
        raise R7LocalClosureError("fresh_gate_rejected")
    tool_before = _json_bytes(material["fresh"]["tool_before"], "tool_before")
    tool_after = _json_bytes(material["fresh"]["tool_after"], "tool_after")
    if _tool_projection(tool_before, tree) != _tool_projection(tool_after, tree):
        raise R7LocalClosureError("fresh_tool_provenance_changed")
    _verify_static_commands(static, material["static"], tool_before, refs["static"])
    general_postcondition = _json_bytes(
        material["general"]["postcondition"], "general_postcondition"
    )
    general_intent = _json_bytes(material["general"]["run_intent"], "general_run_intent")
    general_exit = _json_bytes(material["general"]["exit"], "general_exit")
    general_manifest = _json_bytes(
        material["general"]["node_manifest"], "general_node_manifest_for_timeline"
    )
    private_before = _json_bytes(material["private"]["input_before"], "private_before_time")
    private_after = _json_bytes(material["private"]["input_after"], "private_after_time")
    if (
        general_intent.get("source_tree") != tree
        or general_intent.get("manifest_sha256")
        != hashlib.sha256(material["general"]["node_manifest"]).hexdigest()
        or general_intent.get("expected_nodes") != len(outcomes["general"]["nodes"])
        or general_intent.get("expected_skips") != general_manifest.get("expected_skips")
        or general_intent.get("status") != "RUNNING_INTENT"
        or general_exit.get("command") != general_intent.get("command")
        or not _int_zero(general_exit.get("exit_code"))
    ):
        raise R7LocalClosureError("general_execution_window_rejected")
    if not (
        _artifact_utc(tool_before.get("utc"), "tool_before")
        < _artifact_utc(static.get("utc"), "static")
        < _artifact_utc(private_before.get("utc"), "private_before")
        <= _artifact_utc(general_intent.get("utc"), "general_started")
        <= _artifact_utc(general_exit.get("finished_utc"), "general_finished")
        <= _artifact_utc(private_after.get("utc"), "private_after")
        <= _artifact_utc(tool_after.get("utc"), "tool_after")
        <= _artifact_utc(general_postcondition.get("utc"), "general_postcondition")
    ):
        raise R7LocalClosureError("tool_snapshot_order_rejected")
    project, source_hashes = _verify_source_snapshot(
        fresh,
        static,
        (tool_before, tool_after),
    )
    for role in ("focused", "general", "real_pg", "host"):
        _verify_runtime_provenance(material[role]["runtime_provenance"], project)
        _verify_module_origin(material[role]["module_origin"], project, source_hashes)
    for role in ("focused", "general"):
        _verify_postcondition(role, material[role]["postcondition"])
    if (
        _json_bytes(material["real_pg"]["baseline"], "real_pg_baseline")
        != _json_bytes(material["real_pg"]["postcondition"], "real_pg_postcondition")
        or values["real_pg"].get("db_catalog_and_sessions_restored") is not True
        or values["real_pg"].get("existing_container_identities_unchanged") is not True
    ):
        raise R7LocalClosureError("real_pg_postcondition_rejected")
    host_token = _json_bytes(material["host"]["token_observation"], "host_token")
    if (
        host_token != values["host"].get("independent_runtime_token_observed")
        or host_token.get("elevated") is not True
        or host_token.get("elevation_type_value") != 2
        or host_token.get("integrity_rid") != 12288
    ):
        raise R7LocalClosureError("host_token_observation_rejected")
    _verify_host_scope(material["host"]["execution_manifest"], values["host"], tree)
    for role in ("linux_portable", "inverse"):
        _verify_linux_raw(role, values[role], material[role])

    elevated = values["elevated"]
    _tree_bound(elevated, commit, tree)
    residue = _mapping(elevated.get("runtime_residue"), "elevated_runtime_residue")
    if (
        elevated.get("LOCAL_DECISION") != "PASS_EXACT_ORIGINAL_TWO_NODE_EVIDENCE"
        or not _int_equal(elevated.get("passed"), 2)
        or any(
            not _zero(elevated.get(key)) for key in ("failed", "skipped", "deselected", "errors")
        )
        or not _int_zero(elevated.get("exit_code"))
        or elevated.get("source_unchanged") is not True
        or not _int_zero(residue.get("processes"))
        or not _int_zero(residue.get("test_containers"))
    ):
        raise R7LocalClosureError("elevated_gate_rejected")
    outcomes["elevated"] = _verify_elevated_raw(
        elevated,
        material["elevated"],
        refs["elevated"],
        project,
        source_hashes,
        commit,
        tree,
        hashlib.sha256(material["fresh"]["tool_before"]).hexdigest(),
    )

    inventory = values["private"]
    _tree_bound(inventory, commit, tree)
    actual_results = _mapping(inventory.get("actual_results"), "aggregate_actual_results")
    private_summary = _mapping(actual_results.get("private_file_subset"), "private_summary")
    union_summary = _mapping(actual_results.get("unique_repository_node_union"), "union_summary")
    expected_nodes = _nodes(inventory.get("all_expected_repository_node_ids"), "expected_nodes")
    private_nodes = _nodes(inventory.get("private_node_ids"), "private_nodes")
    source_private_nodes = _private_nodes_from_source(project, outcomes["general"]["nodes"])
    if (
        inventory.get("GLOBAL_DECISION") != "NO-GO"
        or inventory.get("LOCAL_DECISION") != "PASS_LOCAL_CODE_REGRESSION_ONLY"
        or inventory.get("private_inputs_before_after_equal") is not True
        or set(private_nodes) != set(source_private_nodes)
        or private_summary.get("included_in_general") is not True
        or not _int_equal(private_summary.get("passed"), len(private_nodes))
        or not _int_zero(private_summary.get("skipped"))
        or not _int_equal(union_summary.get("expected"), len(expected_nodes))
        or not _int_equal(
            union_summary.get("passed_in_correct_local_environment"), len(expected_nodes)
        )
        or not _int_zero(union_summary.get("missing"))
        or not _int_zero(union_summary.get("duplicate_node_ids_in_expected_union"))
    ):
        raise R7LocalClosureError("private_or_union_inventory_rejected")
    if (
        material["private"]["result"] != material["general"]["result"]
        or material["private"]["junit"] != material["general"]["junit"]
    ):
        raise R7LocalClosureError("private_general_raw_binding_mismatch")
    private_input_count = _verify_private_inputs(
        material["private"]["input_before"],
        material["private"]["input_after"],
        tree,
    )
    passed = set().union(*(set(item["passed"]) for item in outcomes.values()))
    observed = set().union(*(set(item["nodes"]) for item in outcomes.values()))
    expected_set = set(expected_nodes)
    general_set = set(outcomes["general"]["nodes"])
    host_set = set(outcomes["host"]["nodes"])
    if (
        general_set & host_set
        or expected_set != general_set | host_set
        or observed - expected_set
        or passed != expected_set
        or not set(private_nodes) <= set(outcomes["general"]["passed"])
    ):
        raise R7LocalClosureError("node_union_not_fully_passed")

    cleanup = values["cleanup"]
    _tree_bound(cleanup, commit, tree)
    cleanup_state = _mapping(cleanup.get("cleanup"), "cleanup_state")
    runtime = _mapping(cleanup.get("runtime"), "cleanup_runtime")
    if (
        cleanup_state.get("filesystem_residue_zero") is not True
        or not _int_zero(cleanup_state.get("root_count"))
        or not _int_zero(cleanup_state.get("files"))
        or not _int_zero(cleanup_state.get("bytes"))
        or not _int_zero(runtime.get("active_python_pytest"))
        or not _int_zero(runtime.get("new_experiment_containers"))
        or runtime.get("service_differences") != []
        or cleanup.get("user_untracked_preserved") is not True
        or not _int_zero(cleanup.get("tracked_changes"))
    ):
        raise R7LocalClosureError("cleanup_gate_rejected")

    evidence_refs = [producer_ref, *(ref for role in REQUIRED_GATES for ref in refs[role])]
    evidence_identities = {(ref["path"], ref["sha256"]) for ref in evidence_refs}
    return {
        "schema": ASSESSMENT_SCHEMA,
        "status": "PASS_READY_FOR_INDEPENDENT_REVIEW",
        "decision": "PENDING_INDEPENDENT_REVIEW",
        "decision_scope": "r7s5_local_code_closure_only",
        "candidate": {"commit": commit, "tree": tree},
        "manifest": {
            "path": str(resolved_manifest),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "producer": producer,
        "producer_identity_sha256": _identity_digest(producer),
        "producer_observation": producer_ref,
        "freshness": {
            "created_at_utc": manifest["created_at_utc"],
            "expires_at_utc": manifest["expires_at_utc"],
        },
        "evidence_roots": [str(root) for root in roots],
        "gate_roles": list(REQUIRED_GATES),
        "gate_receipts": {role: refs[role][0] for role in REQUIRED_GATES},
        "evidence_file_count": len(evidence_identities),
        "node_accounting": {
            "expected": len(expected_nodes),
            "passed": len(passed),
            "missing": 0,
            "unexpected": 0,
            "expected_sha256": hashlib.sha256(
                canonical_json_bytes(sorted(expected_set))
            ).hexdigest(),
            "passed_sha256": hashlib.sha256(canonical_json_bytes(sorted(passed))).hexdigest(),
            "governed_skips": sum(len(item["skipped"]) for item in outcomes.values()),
            "deselected": 0,
            "private_input_files": private_input_count,
        },
        "boundaries": dict(BOUNDARIES),
        "r7s5_decision": "NO-GO_PENDING_INDEPENDENT_REVIEW",
        "x1_transition": {"to_status": "remediation_required", "credit": "non_credit"},
        "r8_m00_enabled": False,
        "production_go_enabled": False,
        "blockers": [
            "independent_review_not_yet_validated",
            "immutable_local_publication_not_yet_completed",
        ],
    }


def validate_review(
    assessment: dict[str, Any],
    review_path: Path,
    expected_review_sha256: str,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Validate a separately pinned procedural source-local review receipt."""

    if (
        assessment.get("schema") != ASSESSMENT_SCHEMA
        or assessment.get("status") != "PASS_READY_FOR_INDEPENDENT_REVIEW"
        or assessment.get("decision") != "PENDING_INDEPENDENT_REVIEW"
        or assessment.get("r7s5_decision") != "NO-GO_PENDING_INDEPENDENT_REVIEW"
        or assessment.get("r8_m00_enabled") is not False
    ):
        raise R7LocalClosureError("assessment_not_passed")
    expected_hash = _hex(expected_review_sha256, 64, "expected_review")
    try:
        resolved_review = Path(review_path).resolve(strict=True)
    except OSError as exc:
        raise R7LocalClosureError("review_missing") from exc
    raw = resolved_review.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise R7LocalClosureError("review_sha256_mismatch")
    review = _json_bytes(raw, "review")
    if raw != canonical_json_bytes(review):
        raise R7LocalClosureError("review_not_canonical")
    _exact_keys(
        review,
        {
            "schema",
            "status",
            "result",
            "scope",
            "candidate",
            "manifest_sha256",
            "assessment_sha256",
            "producer_identity_sha256",
            "reviewer",
            "reviewer_observation",
            "reviewed_at_utc",
            "reviewed_inputs_mutated",
            "fixture_or_mock",
            "external_signer_authenticated",
            "production_authority",
            "formal_ci_claimed",
            "r8_operational_claimed",
        },
        "review",
    )
    reviewer = _process(review["reviewer"], "reviewer")
    producer = _mapping(assessment.get("producer"), "assessment_producer")
    manifest_sha = assessment["manifest"]["sha256"]
    assessment_sha = hashlib.sha256(canonical_json_bytes(assessment)).hexdigest()
    reviewed_at = _utc(review["reviewed_at_utc"], "reviewed_at")
    current = _now(now)
    freshness = _mapping(assessment.get("freshness"), "assessment_freshness")
    created = _utc(freshness.get("created_at_utc"), "assessment_created")
    expires = _utc(freshness.get("expires_at_utc"), "assessment_expires")
    roots = [Path(str(item)).resolve(strict=True) for item in assessment.get("evidence_roots", [])]
    observation_ref, observation_raw = _read_ref(
        review["reviewer_observation"],
        roots,
        kind="reviewer_observation",
    )
    observation = _json_bytes(observation_raw, "reviewer_observation")
    if observation_raw != canonical_json_bytes(observation):
        raise R7LocalClosureError("reviewer_observation_not_canonical")
    _exact_keys(
        observation,
        {
            "schema",
            "candidate",
            "run_uuid",
            "pid",
            "create_time_utc",
            "executable_path",
            "executable_sha256",
            "script_path",
            "script_sha256",
            "observed_at_utc",
            "method",
        },
        "reviewer_observation",
    )
    observed_at = _utc(observation["observed_at_utc"], "reviewer_observed_at")
    try:
        executable = Path(str(observation["executable_path"])).resolve(strict=True)
        script = Path(str(observation["script_path"])).resolve(strict=True)
    except OSError as exc:
        raise R7LocalClosureError("reviewer_observed_files_changed") from exc
    if (
        not executable.is_file()
        or not script.is_file()
        or _sha_file(executable)[1] != observation["executable_sha256"]
        or _sha_file(script)[1] != observation["script_sha256"]
    ):
        raise R7LocalClosureError("reviewer_observed_files_changed")
    if (
        review["schema"] != REVIEW_SCHEMA
        or review["status"] != "PASS"
        or review["result"] != "approved"
        or review["scope"] != "procedural_source_local_independent_review"
        or review["candidate"] != assessment["candidate"]
        or review["manifest_sha256"] != manifest_sha
        or review["assessment_sha256"] != assessment_sha
        or review["producer_identity_sha256"] != assessment["producer_identity_sha256"]
        or review["reviewed_inputs_mutated"] is not False
        or review["fixture_or_mock"] is not False
        or review["external_signer_authenticated"] is not False
        or review["production_authority"] is not False
        or review["formal_ci_claimed"] is not False
        or review["r8_operational_claimed"] is not False
        or reviewer["role"] != "r7_local_independent_reviewer"
        or observation["schema"] != REVIEW_OBSERVATION_SCHEMA
        or observation["candidate"] != assessment["candidate"]
        or observation["run_uuid"] != reviewer["run_uuid"]
        or observation["pid"] != reviewer["pid"]
        or observation["create_time_utc"] != reviewer["create_time_utc"]
        or observation["executable_sha256"] != reviewer["executable_sha256"]
        or observation["script_sha256"] != reviewer["script_sha256"]
        or observation["method"] != "independent_os_process_observation"
        or reviewer["run_uuid"] == producer["run_uuid"]
        or (reviewer["pid"], reviewer["create_time_utc"])
        == (producer["pid"], producer["create_time_utc"])
        or not _utc(producer["create_time_utc"], "producer_create_time") <= created
        or not _utc(reviewer["create_time_utc"], "reviewer_create_time") <= observed_at
        or not created <= observed_at <= reviewed_at <= current < expires
    ):
        raise R7LocalClosureError("review_boundary_rejected")
    return {
        "schema": REVIEW_VALIDATION_SCHEMA,
        "status": "PASS_APPROVED_FOR_LOCAL_PUBLICATION",
        "scope": review["scope"],
        "candidate": assessment["candidate"],
        "manifest_sha256": manifest_sha,
        "assessment_sha256": assessment_sha,
        "review": {"path": str(resolved_review), "bytes": len(raw), "sha256": expected_hash},
        "producer_identity_sha256": assessment["producer_identity_sha256"],
        "reviewer": reviewer,
        "reviewer_observation": observation_ref,
        "reviewed_at_utc": review["reviewed_at_utc"],
        "r7s5_decision": "NO-GO_PENDING_IMMUTABLE_LOCAL_PUBLICATION",
        "r8_m00_enabled": False,
        "identity_strength": "procedural_source_local_process_separation",
        "external_identity_authenticated": False,
        "production_authority": False,
    }


def _document_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = canonical_json_bytes(dict(value))
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def publish_local_closure(
    parent: Path,
    manifest_path: Path,
    review_path: Path,
    expected_review_sha256: str,
    *,
    expected_commit: str,
    expected_tree: str,
    now: datetime,
) -> dict[str, Any]:
    """Publish one candidate's local-code evidence, without ledger/Git side effects.

    Reservation is deterministic within the configured R7 output parent, not a
    new global/WORM authority. Any partial attempt remains consumed. Original
    lower-level handle I/O retains its non-GO/hostile-admin limitation.
    """
    from evm.scale_validation import phase_b2_r7s3_handle_io as bound_io
    from evm.scale_validation import phase_b2_r7s4_handle_io as durable_io

    assessment = assess(
        manifest_path, expected_commit=expected_commit, expected_tree=expected_tree, now=now
    )
    review = validate_review(assessment, review_path, expected_review_sha256, now=now)
    output_parent = Path(parent)
    if not output_parent.is_absolute():
        raise R7LocalClosureError("output_parent_not_absolute")
    resolved_parent = output_parent.resolve(strict=True)
    if resolved_parent != output_parent or not resolved_parent.is_dir():
        raise R7LocalClosureError("output_parent_unsafe")
    output_parent = resolved_parent
    run_uuid = assessment["producer"]["run_uuid"]
    leaf = f"r7s5-local-code-closure-{expected_commit}"
    reservation_leaf = f"r7s5-local-code-{expected_commit}.reservation.json"
    output = output_parent / leaf
    if output.exists() or (output_parent / reservation_leaf).exists():
        raise R7LocalClosureError("closure_candidate_already_reserved")

    # All known document bytes are prepared before consuming this candidate.
    frozen_input = Path(manifest_path).read_bytes()
    frozen_review = Path(review_path).read_bytes()
    if (
        hashlib.sha256(frozen_input).hexdigest() != assessment["manifest"]["sha256"]
        or hashlib.sha256(frozen_review).hexdigest() != expected_review_sha256
    ):
        raise R7LocalClosureError("closure_inputs_changed_before_publication")
    report = {
        "schema": REPORT_SCHEMA,
        "status": "local_code_evidence_published",
        "r7s5_decision": "GO",
        "decision_scope": "r7s5_local_code_closure_only",
        "candidate": assessment["candidate"],
        "manifest": assessment["manifest"],
        "assessment_sha256": _document_ref(assessment)["sha256"],
        "review": review["review"],
        "node_accounting": assessment["node_accounting"],
        "boundaries": assessment["boundaries"],
        "production_go_enabled": False,
        "acceptance_credit": False,
        "r8_operational": "not_run",
        "R7_M15_complete": False,
        "r8_m00_enabled": False,
        "next_task": "R7-M15-LEDGER-CANONICAL-READBACK",
    }
    seal = {
        "schema": SEAL_SCHEMA,
        "status": "sealed_local_code_evidence",
        "r7s5_decision": "GO",
        "decision_scope": report["decision_scope"],
        "candidate": assessment["candidate"],
        "report": _document_ref(report),
        "assessment": _document_ref(assessment),
        "review_validation": _document_ref(review),
        "manifest_sha256": assessment["manifest"]["sha256"],
        "review_sha256": expected_review_sha256,
        "writer_invocations": 1,
        "writer_retries": 0,
        "replace_existing": False,
        "production_go_enabled": False,
        "acceptance_credit": False,
    }
    documents = {
        "r7-local-code-input-manifest.json": frozen_input,
        "r7-local-code-independent-review.json": frozen_review,
        "r7-local-code-closure-assessment.json": canonical_json_bytes(assessment),
        "r7-local-code-closure-review-validation.json": canonical_json_bytes(review),
        "r7-local-code-closure-report.json": canonical_json_bytes(report),
        "r7-local-code-closure-seal.json": canonical_json_bytes(seal),
    }
    for name in [
        leaf,
        reservation_leaf,
        *documents,
        "r7-local-code-identity-manifest.json",
        "r7-local-code-closure-index.json",
    ]:
        durable_io.validate_strict_windows_leaf(name)
    reservation_raw = canonical_json_bytes(
        {
            "schema": "evm.s8-v4.pre-r8.r7s5.local-code-reservation.v1",
            "candidate": assessment["candidate"],
            "run_uuid": run_uuid,
            "manifest_sha256": assessment["manifest"]["sha256"],
            "review_sha256": expected_review_sha256,
            "retry_allowed": False,
            "scope": "configured_local_r7_output_parent_only",
            "production_authority": False,
        }
    )

    api = durable_io.WindowsHandleApi()
    parent_handle: int | None = None
    output_handle: int | None = None
    publications: dict[str, Any] = {}
    readback: dict[str, Any] = {}
    try:
        # Existing handles exclude delete sharing: keep parent/output identity
        # stable across this local batch, without inventing a new I/O framework.
        parent_handle = api.open_directory(str(output_parent))
        parent_identity = api.identity(parent_handle)
        bound_io._reject_unsafe_directory_identity(
            parent_identity, expected_path=str(output_parent)
        )
        # This fixed latch consumes the candidate even if the reservation's
        # later temporary-file write/rename fails before creating its final leaf.
        os.mkdir(output)  # No exist_ok, retry, deletion or alternate output.
        output_handle = api.open_directory(str(output))
        output_identity = api.identity(output_handle)
        bound_io._reject_unsafe_directory_identity(output_identity, expected_path=str(output))
        api.flush_directory(parent_handle)
        reservation = durable_io.publish_bound_no_replace_durable(
            output_parent, reservation_leaf, reservation_raw, run_uuid=run_uuid
        )
        if type(reservation) is not durable_io.DurableBoundPublication:
            raise R7LocalClosureError("reservation_publication_type_invalid")

        def publish(name: str, raw: bytes) -> dict[str, Any]:
            pub = durable_io.publish_bound_no_replace_durable(output, name, raw, run_uuid=run_uuid)
            if type(pub) is not durable_io.DurableBoundPublication:
                raise R7LocalClosureError("closure_publication_type_invalid")
            if (
                pub.sha256 != hashlib.sha256(raw).hexdigest()
                or pub.bytes != len(raw)
                or pub.directory_flush_succeeded is not True
                or pub.directory_flush_count != 1
                or pub.file_flush_count != 2
                or pub.replace_if_exists is not False
                or pub.same_handle_readback is not True
                or pub.file_identity_stable_across_rename is not True
            ):
                raise R7LocalClosureError("closure_publication_receipt_invalid")
            if durable_io._directory_identity_changed(output_identity, pub.directory_identity):
                raise R7LocalClosureError("closure_output_directory_identity_changed")
            publications[name] = pub
            return pub.to_dict()

        catalog = {name: publish(name, raw) for name, raw in documents.items()}
        identity_manifest = {
            "schema": "evm.s8-v4.pre-r8.r7s5.local-code-identity-manifest.v1",
            "candidate": assessment["candidate"],
            "scope": report["decision_scope"],
            "reservation": reservation.to_dict(),
            "publications": catalog,
            "r7s5_decision": "GO",
            "production_authority": False,
            "acceptance_credit": False,
        }
        manifest_leaf = "r7-local-code-identity-manifest.json"
        documents[manifest_leaf] = canonical_json_bytes(identity_manifest)
        identity_publication = publish(manifest_leaf, documents[manifest_leaf])
        index_leaf = "r7-local-code-closure-index.json"
        documents[index_leaf] = canonical_json_bytes(
            {
                "schema": INDEX_SCHEMA,
                "candidate": assessment["candidate"],
                "status": "local_code_evidence_published",
                "r7s5_decision": "GO",
                "manifest_publication": identity_publication,
                "terminal_identity_requires_postpublication_readback": True,
                "writer_invocations": 1,
                "writer_retries": 0,
                "R7_M15_complete": False,
                "r8_m00_enabled": False,
                "production_authority": False,
                "acceptance_credit": False,
            }
        )
        publish(index_leaf, documents[index_leaf])  # Terminal index is last.
        all_publications = {reservation_leaf: reservation, **publications}
        for name, pub in all_publications.items():
            raw = reservation_raw if name == reservation_leaf else documents[name]
            pin = {
                "path": pub.identity.final_path,
                "sha256": pub.sha256,
                "bytes": pub.bytes,
                "volume_serial_number": pub.identity.volume_serial_number,
                "file_id_hex": pub.identity.file_id_hex,
                "security_descriptor_sha256": pub.identity.security_descriptor_sha256,
            }
            observed = bound_io.read_bound_file(pub.final_path, expected_pin=pin)
            if observed.raw != raw:
                raise R7LocalClosureError("closure_postpublication_readback_mismatch")
            readback[name] = observed.pin
        if durable_io._directory_identity_changed(
            parent_identity, api.identity(parent_handle)
        ) or durable_io._directory_identity_changed(output_identity, api.identity(output_handle)):
            raise R7LocalClosureError("closure_directory_identity_changed")
    finally:
        api.close(output_handle)
        api.close(parent_handle)

    return {
        "schema": PUBLICATION_SCHEMA,
        "status": "PASS_LOCAL_PUBLICATION_ONLY",
        "local_decision": "GO_LOCAL_CODE_EVIDENCE",
        "global_decision": "NO-GO_CLOSURE_CANONICAL_READBACK_PENDING",
        "candidate": assessment["candidate"],
        "output_directory": str(output),
        "run_uuid": run_uuid,
        "reservation": reservation.to_dict(),
        "postpublication_readback": readback,
        "terminal_index_pin": readback[index_leaf],
        "ledger_event_template": {
            "schema_version": "evm.s8_v4.progress_event.v1",
            "event_type": "x1_phase_b2_pre_r8_r7s5_local_code_closure_go",
            "work_item": "X1",
            "from_status": "remediation_required",
            "to_status": "ready",
            "credit": "non_credit",
            "acceptance_credit": False,
            "r7s5_decision": "GO",
            "source_git_revision": expected_commit,
            "source_tree_sha": expected_tree,
            "closure_evidence": {"output_directory": str(output), "documents": readback},
            "claim_boundary": "R7 local code only; R8/production/V4 not credited",
        },
        "writer_invocations": 1,
        "writer_retries": 0,
        "durable_primitive_calls": len(readback),
        "production_go_enabled": False,
        "formal_ci_claimed": False,
        "r8_operational": "not_run",
        "acceptance_credit": False,
        "R7_M15_complete": False,
        "r8_m00_enabled": False,
        "NEXT_TASK": "R7-M15-LEDGER-CANONICAL-READBACK",
    }


__all__ = [
    "ASSESSMENT_SCHEMA",
    "BOUNDARIES",
    "MANIFEST_SCHEMA",
    "PUBLICATION_SCHEMA",
    "R7LocalClosureError",
    "REQUIRED_GATES",
    "REVIEW_SCHEMA",
    "assess",
    "canonical_json_bytes",
    "publish_local_closure",
    "validate_review",
]

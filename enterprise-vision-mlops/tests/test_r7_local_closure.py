from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evm.scale_validation import r7_local_closure as closure


TREE = "2" * 40
COMMIT = "1" * 40


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, object]) -> Path:
    path.write_bytes(closure.canonical_json_bytes(value))
    return path.resolve(strict=True)


def _ref(path: Path, kind: str) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    raw = resolved.read_bytes()
    return {
        "kind": kind,
        "path": str(resolved),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _process(
    role: str,
    run_uuid: str,
    pid: int,
    create_time_utc: str,
    executable_sha256: str = "a" * 64,
    script_sha256: str = "b" * 64,
) -> dict[str, object]:
    return {
        "role": role,
        "run_uuid": run_uuid,
        "pid": pid,
        "create_time_utc": create_time_utc,
        "executable_sha256": executable_sha256,
        "script_sha256": script_sha256,
    }


def _minimal_manifest() -> dict[str, object]:
    return {
        "schema": closure.MANIFEST_SCHEMA,
        "created_at_utc": "2026-09-09T00:00:00Z",
        "expires_at_utc": "2026-09-09T01:00:00Z",
        "candidate": {"commit": COMMIT, "tree": TREE},
        "producer": _process(
            "r7_local_manifest_producer",
            "00000000-0000-4000-8000-000000000001",
            101,
            "2026-09-08T23:50:00Z",
        ),
        "producer_observation": {},
        "evidence_roots": [],
        "gates": {},
        "boundaries": dict(closure.BOUNDARIES),
    }


def _review_fixture(tmp_path: Path) -> tuple[dict[str, object], Path, str]:
    executable = tmp_path / "reviewer.exe"
    script = tmp_path / "review.py"
    executable.write_bytes(b"reviewer executable")
    script.write_bytes(b"review script")
    producer = _process(
        "r7_local_manifest_producer",
        "00000000-0000-4000-8000-000000000001",
        101,
        "2026-09-08T23:50:00Z",
    )
    reviewer = _process(
        "r7_local_independent_reviewer",
        "00000000-0000-4000-8000-000000000002",
        202,
        "2026-09-09T00:05:00Z",
        _sha256(executable),
        _sha256(script),
    )
    assessment: dict[str, object] = {
        "schema": closure.ASSESSMENT_SCHEMA,
        "status": "PASS_READY_FOR_INDEPENDENT_REVIEW",
        "decision": "PENDING_INDEPENDENT_REVIEW",
        "candidate": {"commit": COMMIT, "tree": TREE},
        "manifest": {"sha256": "c" * 64},
        "producer": producer,
        "producer_identity_sha256": closure._identity_digest(producer),
        "freshness": {
            "created_at_utc": "2026-09-09T00:00:00Z",
            "expires_at_utc": "2026-09-09T01:00:00Z",
        },
        "evidence_roots": [str(tmp_path.resolve())],
        "r7s5_decision": "NO-GO_PENDING_INDEPENDENT_REVIEW",
        "r8_m00_enabled": False,
    }
    observation = {
        "schema": closure.REVIEW_OBSERVATION_SCHEMA,
        "candidate": assessment["candidate"],
        "run_uuid": reviewer["run_uuid"],
        "pid": reviewer["pid"],
        "create_time_utc": reviewer["create_time_utc"],
        "executable_path": str(executable.resolve()),
        "executable_sha256": reviewer["executable_sha256"],
        "script_path": str(script.resolve()),
        "script_sha256": reviewer["script_sha256"],
        "observed_at_utc": "2026-09-09T00:10:00Z",
        "method": "independent_os_process_observation",
    }
    observation_path = _write_json(tmp_path / "review-observation.json", observation)
    review = {
        "schema": closure.REVIEW_SCHEMA,
        "status": "PASS",
        "result": "approved",
        "scope": "procedural_source_local_independent_review",
        "candidate": assessment["candidate"],
        "manifest_sha256": assessment["manifest"]["sha256"],
        "assessment_sha256": hashlib.sha256(closure.canonical_json_bytes(assessment)).hexdigest(),
        "producer_identity_sha256": assessment["producer_identity_sha256"],
        "reviewer": reviewer,
        "reviewer_observation": _ref(observation_path, "reviewer_observation"),
        "reviewed_at_utc": "2026-09-09T00:20:00Z",
        "reviewed_inputs_mutated": False,
        "fixture_or_mock": False,
        "external_signer_authenticated": False,
        "production_authority": False,
        "formal_ci_claimed": False,
        "r8_operational_claimed": False,
    }
    review_path = _write_json(tmp_path / "review.json", review)
    return assessment, review_path, _sha256(review_path)


def _review_for_assessment(directory: Path, assessment: dict[str, Any]) -> tuple[Path, str]:
    directory.mkdir()
    executable = directory / "reviewer.exe"
    script = directory / "review.py"
    executable.write_bytes(b"independent reviewer executable")
    script.write_bytes(b"independent reviewer script")
    reviewer = _process(
        "r7_local_independent_reviewer",
        "00000000-0000-4000-8000-000000000002",
        202,
        "2026-09-09T00:05:00Z",
        _sha256(executable),
        _sha256(script),
    )
    observation = {
        "schema": closure.REVIEW_OBSERVATION_SCHEMA,
        "candidate": assessment["candidate"],
        "run_uuid": reviewer["run_uuid"],
        "pid": reviewer["pid"],
        "create_time_utc": reviewer["create_time_utc"],
        "executable_path": str(executable.resolve()),
        "executable_sha256": reviewer["executable_sha256"],
        "script_path": str(script.resolve()),
        "script_sha256": reviewer["script_sha256"],
        "observed_at_utc": "2026-09-09T00:10:00Z",
        "method": "independent_os_process_observation",
    }
    observation_path = _write_json(directory / "review-observation.json", observation)
    review = {
        "schema": closure.REVIEW_SCHEMA,
        "status": "PASS",
        "result": "approved",
        "scope": "procedural_source_local_independent_review",
        "candidate": assessment["candidate"],
        "manifest_sha256": assessment["manifest"]["sha256"],
        "assessment_sha256": hashlib.sha256(closure.canonical_json_bytes(assessment)).hexdigest(),
        "producer_identity_sha256": assessment["producer_identity_sha256"],
        "reviewer": reviewer,
        "reviewer_observation": _ref(observation_path, "reviewer_observation"),
        "reviewed_at_utc": "2026-09-09T00:20:00Z",
        "reviewed_inputs_mutated": False,
        "fixture_or_mock": False,
        "external_signer_authenticated": False,
        "production_authority": False,
        "formal_ci_claimed": False,
        "r8_operational_claimed": False,
    }
    review_path = _write_json(directory / "review.json", review)
    return review_path, _sha256(review_path)


def _raw(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    return closure.canonical_json_bytes(value)


def _artifact_path(root: Path, role: str, kind: str) -> Path:
    suffix = ".xml" if kind == "junit" else ".json"
    if kind in {"stdout", "stderr", "runtime_script", "execution_script"}:
        suffix = ".txt"
    return root / f"{role}-{kind}{suffix}"


def _add_gate(
    root: Path,
    role: str,
    receipt: dict[str, Any],
    artifacts: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Path]]:
    paths: dict[str, Path] = {}
    refs: list[dict[str, object]] = []
    for kind, value in artifacts.items():
        path = _artifact_path(root, role, kind)
        path.write_bytes(_raw(value))
        paths[kind] = path.resolve(strict=True)
        refs.append(_ref(path, kind))
    receipt_path = _write_json(root / f"{role}-receipt.json", receipt)
    paths["receipt"] = receipt_path
    return {"receipt": _ref(receipt_path, "receipt"), "artifacts": refs}, paths


def _node_case(nodeid: str) -> tuple[str, str, str, None]:
    module, name = nodeid.split("::")
    classname = module.removesuffix(".py").replace("/", ".")
    return classname, name, "passed", None


def _passing_junit(nodes: list[str]) -> bytes:
    return _junit(*(_node_case(node) for node in nodes))


def _result_junit(nodes: list[str], skipped: dict[str, str]) -> bytes:
    cases = []
    for node in nodes:
        classname, name, _, _ = _node_case(node)
        outcome = "skipped" if node in skipped else "passed"
        cases.append((classname, name, outcome, skipped.get(node)))
    return _junit(*cases)


def _result(nodes: list[str], skipped: dict[str, str] | None = None) -> dict[str, Any]:
    skipped = skipped or {}
    return {
        "status": "PASS",
        "source_tree": TREE,
        "actual_node_ids": nodes,
        "skipped": [{"nodeid": node, "reason": skipped[node]} for node in nodes if node in skipped],
        "failed": [],
        "errors": [],
        "missing": [],
        "extra": [],
        "exit_code": 0,
        "exact_expected_skips": True,
        "deselected": 0,
        "duplicate_count": 0,
        "source_hashes_unchanged": True,
        "collected": len(nodes),
        "junit_cases": len(nodes),
        "passed": len(nodes) - len(skipped),
    }


def _all_pass_receipt(role: str, nodes: list[str]) -> dict[str, Any]:
    node_key = "actual_nodes" if role in {"real_pg", "host"} else "node_ids"
    return {
        "status": "PASS",
        "code_commit": COMMIT,
        "source_tree": TREE,
        node_key: nodes,
        "exit_code": 0,
        "collected": len(nodes),
        "junit_cases": len(nodes),
        "passed": len(nodes),
        "deselected": 0,
        "nonpass": [],
        "source_unchanged": True,
        "owned_container_residue": 0,
    }


def _happy_evidence(tmp_path: Path) -> dict[str, Any]:
    root = (tmp_path / "evidence").resolve()
    root.mkdir()
    project = root / "fresh-project"
    source = project / "src" / "module.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    lanes = project / "ci" / "pre-r8-r7s5-test-lanes.json"
    lanes.parent.mkdir()
    lanes.write_bytes(
        closure.canonical_json_bytes(
            {"file_inventory": {"lanes": {"private": ["tests/test_private.py"]}}}
        )
    )
    source_hashes = {
        "src/module.py": _sha256(source),
        "ci/pre-r8-r7s5-test-lanes.json": _sha256(lanes),
    }

    runtime_image = root / "python.exe"
    ruff_binary = root / "ruff.exe"
    runtime_image.write_bytes(b"pinned python")
    ruff_binary.write_bytes(b"pinned ruff")
    package_metadata = root / "fixture-1.0.dist-info"
    package_metadata.mkdir()
    record = package_metadata / "RECORD"
    record.write_text("fixture record\n", encoding="utf-8")

    tool_snapshot = {
        "status": "PASS_PINNED_TOOLCHAIN",
        "candidate_tree": TREE,
        "source_hashes": source_hashes,
        "install_receipt_sha256": "c" * 64,
        "executable": str(runtime_image.resolve()),
        "executable_sha256": _sha256(runtime_image),
        "actual_runtime_image": str(runtime_image.resolve()),
        "actual_runtime_image_sha256": _sha256(runtime_image),
        "python_version": "3.13.7",
        "packages": {
            "fixture": {
                "metadata_origin": str(package_metadata.resolve()),
                "record_sha256": _sha256(record),
                "verified_hashed_files": 1,
            }
        },
        "verified_hashed_files": 1,
        "verified_hashed_bytes": record.stat().st_size,
        "module_origins": {"fixture": str(source.resolve())},
        "ruff_binary": {"path": str(ruff_binary.resolve()), "sha256": _sha256(ruff_binary)},
        "pth_files": [],
        "project_install": str(project.resolve()),
    }
    tool_before = {**tool_snapshot, "utc": "2026-09-08T23:56:00+00:00"}
    tool_after = {**tool_snapshot, "utc": "2026-09-09T00:02:00+00:00"}
    runtime = {
        "image": str(runtime_image.resolve()),
        "image_sha256": _sha256(runtime_image),
        "cwd": str(project.resolve()),
        "isolated": True,
        "no_site": True,
        "dont_write_bytecode": True,
    }

    def module_origin(role: str) -> dict[str, Any]:
        return {
            "project": str(project.resolve()),
            "all_project_sources_within_fresh_checkout": True,
            "loaded_project_modules": {
                role: {"path": str(source.resolve()), "sha256": _sha256(source)}
            },
            "additional_source_roles": {},
        }

    focused_node = "tests/test_focus.py::test_focus"
    pg_node = "tests/test_pg.py::test_pg"
    linux_node = "tests/test_linux.py::test_linux"
    inverse_node = "tests/test_inverse.py::test_inverse"
    elevated_nodes = [
        "tests/test_elevated.py::test_admin_one",
        "tests/test_elevated.py::test_admin_two",
    ]
    nodes = {
        "focused": [focused_node],
        "general": [
            "tests/test_general.py::test_general",
            "tests/test_private.py::test_private",
            focused_node,
            pg_node,
            linux_node,
            inverse_node,
            *elevated_nodes,
        ],
        "real_pg": [pg_node],
        "host": ["tests/test_pre_r8_r7s2_contract_stager.py::test_host"],
        "linux_portable": [linux_node],
        "inverse": [inverse_node],
        "elevated": elevated_nodes,
    }
    general_skips = {
        node: "governed environment lane"
        for node in [focused_node, pg_node, linux_node, inverse_node, *elevated_nodes]
    }
    gates: dict[str, dict[str, Any]] = {}
    paths: dict[str, dict[str, Path]] = {}

    ci_files = ["src/evm/scale_validation/r7_local_closure.py"]
    m04_files = [
        "src/evm/scale_validation/r7_local_closure.py",
        "tests/test_r7_local_closure.py",
    ]
    tool_files = list(closure.CONTAINER_TOOL_FILES)
    compile_files = sorted(source_hashes)
    scope_contract = {
        "repository_ci": {"files": ci_files},
        "r7_m04_selected": {"files": m04_files},
    }
    scope_path = _artifact_path(root, "static", "scope_contract").resolve()
    static_commands = [
        ["git", "diff", "--quiet", "--no-ext-diff"],
        [str(runtime_image.resolve()), "-m", "ruff", "check", "--no-cache", *ci_files],
        [str(runtime_image.resolve()), "-m", "ruff", "format", "--check", *ci_files],
        [str(runtime_image.resolve()), "-m", "ruff", "check", "--no-cache", *m04_files],
        [str(runtime_image.resolve()), "-m", "ruff", "format", "--check", *m04_files],
        [str(runtime_image.resolve()), "-m", "ruff", "check", "--no-cache", *tool_files],
        [str(runtime_image.resolve()), "-m", "ruff", "format", "--check", *tool_files],
        [str(runtime_image.resolve()), "-I", "-S", "-c", "compile", *compile_files],
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[System.Management.Automation.Language.Parser]::ParseFile('fixture')",
        ],
        ["git", "diff", "--check"],
        ["git", "diff", "--cached", "--check", "HEAD", "--"],
    ]
    static_artifacts: dict[str, Any] = {"scope_contract": scope_contract}
    for index in range(len(closure.STATIC_ROLES)):
        static_artifacts[f"stdout_{index:02d}"] = b"command pass\n"
        static_artifacts[f"stderr_{index:02d}"] = b""
    gates["static"], paths["static"] = _add_gate(
        root,
        "static",
        {
            "status": "PASS",
            "code_commit": COMMIT,
            "source_tree": TREE,
            "source_hashes": source_hashes,
            "utc": "2026-09-08T23:57:00+00:00",
            "scope_origin": str(scope_path),
            "scope_sha256": hashlib.sha256(_raw(scope_contract)).hexdigest(),
            "ci_files": ci_files,
            "m04_files": m04_files,
            "additional_tool_files": tool_files,
            "commands": [
                {"name": f"fixture-{role}", "argv": argv, "exit_code": 0}
                for role, argv in zip(closure.STATIC_ROLES, static_commands, strict=True)
            ],
        },
        static_artifacts,
    )
    for role in ("focused", "general"):
        skipped = general_skips if role == "general" else {}
        result = _result(nodes[role], skipped)
        collection_stdout = ("\n".join(nodes[role]) + "\n").encode()
        postcondition: dict[str, Any] = {
            "exit_code": 0,
            "candidate_index_worktree_unchanged": True,
            "utc": (
                "2026-09-09T00:03:00+00:00" if role == "general" else "2026-09-08T23:57:30+00:00"
            ),
            (
                "recorded_execution_processes_active"
                if role == "focused"
                else "active_recorded_run_processes"
            ): 0,
        }
        if role == "general":
            postcondition.update(
                {
                    "package_and_tool_bytes_before_after_equal": True,
                    "installed_hashed_files_reverified": 1,
                    "private_inputs_pre_run_and_post_equal": True,
                }
            )
        node_manifest = {
            "code_commit": COMMIT,
            "source_tree": TREE,
            "node_ids": nodes[role],
            "expected_skips": [
                {"nodeid": node, "reason": skipped[node]} for node in nodes[role] if node in skipped
            ],
            "collection_stdout_sha256": hashlib.sha256(collection_stdout).hexdigest(),
            "files": (
                ["tests", *(f"--ignore={path}" for path in closure.HOST_FILES)]
                if role == "general"
                else ["tests/test_focus.py"]
            ),
        }
        execution_window: dict[str, Any] = {}
        if role == "general":
            command = ["python", "-m", "pytest", *node_manifest["files"]]
            execution_window = {
                "run_intent": {
                    "source_tree": TREE,
                    "manifest_sha256": hashlib.sha256(_raw(node_manifest)).hexdigest(),
                    "expected_nodes": len(nodes[role]),
                    "expected_skips": node_manifest["expected_skips"],
                    "status": "RUNNING_INTENT",
                    "command": command,
                    "utc": "2026-09-09T00:00:10+00:00",
                },
                "exit": {
                    "command": command,
                    "exit_code": 0,
                    "finished_utc": "2026-09-09T00:00:20+00:00",
                },
            }
        gates[role], paths[role] = _add_gate(
            root,
            role,
            {"status": "PASS", "code_commit": COMMIT, "source_tree": TREE},
            {
                "result": result,
                "node_manifest": node_manifest,
                "stdout": b"pytest pass\n",
                "stderr": b"",
                "collection_stdout": collection_stdout,
                "collection_stderr": b"",
                "junit": _result_junit(nodes[role], skipped),
                "postcondition": postcondition,
                "runtime_provenance": runtime,
                "module_origin": module_origin(role),
                **execution_window,
            },
        )

    pg_receipt = _all_pass_receipt("real_pg", nodes["real_pg"])
    pg_receipt.update(
        {
            "db_catalog_and_sessions_restored": True,
            "existing_container_identities_unchanged": True,
        }
    )
    pg_state = {"state": "original"}
    gates["real_pg"], paths["real_pg"] = _add_gate(
        root,
        "real_pg",
        pg_receipt,
        {
            "stdout": b"postgres pass\n",
            "stderr": b"",
            "junit": _passing_junit(nodes["real_pg"]),
            "baseline": pg_state,
            "postcondition": pg_state,
            "runtime_provenance": runtime,
            "module_origin": module_origin("real_pg"),
        },
    )
    host_token = {"elevated": True, "elevation_type_value": 2, "integrity_rid": 12288}
    host_receipt = _all_pass_receipt("host", nodes["host"])
    host_receipt["independent_runtime_token_observed"] = host_token
    gates["host"], paths["host"] = _add_gate(
        root,
        "host",
        host_receipt,
        {
            "stdout": b"host pass\n",
            "stderr": b"",
            "junit": _passing_junit(nodes["host"]),
            "runtime_provenance": runtime,
            "module_origin": module_origin("host"),
            "token_observation": host_token,
            "execution_manifest": {
                "source_tree": TREE,
                "files": list(closure.HOST_FILES),
                "node_ids": nodes["host"],
                "node_count": len(nodes["host"]),
                "expected_skips": [],
                "collection_exit_code": 0,
                "command": ["python", "-m", "pytest", *closure.HOST_FILES],
            },
        },
    )

    for role in ("linux_portable", "inverse"):
        receipt = _all_pass_receipt(role, nodes[role])
        receipt["existing_container_identities_unchanged"] = True
        artifacts: dict[str, Any] = {
            "stdout": b"container pass\n",
            "stderr": b"",
            "junit": _passing_junit(nodes[role]),
            "intent": {
                "code_commit": COMMIT,
                "source_tree": TREE,
                "files": [f"tests/test_{role}.py"],
                "expected_skips": [],
            },
            "collection_runtime": {
                "node_ids": nodes[role],
                "started": [],
                "deselected": [],
            },
            "execution_runtime": {
                "node_ids": nodes[role],
                "started": nodes[role],
                "finished": nodes[role],
                "deselected": [],
                "exit_code": 0,
            },
            "execution_postcondition": {"running": False, "exit_code": 0},
        }
        if role == "inverse":
            artifacts["collection_postcondition"] = {"running": False, "exit_code": 0}
        gates[role], paths[role] = _add_gate(root, role, receipt, artifacts)

    private_input = root / "private-input.bin"
    private_input.write_bytes(b"immutable private input")
    before = _private_snapshot(private_input, stage="before-private")
    after = deepcopy(before)
    after["stage"] = "after-private"
    after["utc"] = "2026-09-09T00:01:00+00:00"
    expected_nodes = [*nodes["general"], *nodes["host"]]
    private_receipt = {
        "code_commit": COMMIT,
        "source_tree": TREE,
        "GLOBAL_DECISION": "NO-GO",
        "LOCAL_DECISION": "PASS_LOCAL_CODE_REGRESSION_ONLY",
        "private_inputs_before_after_equal": True,
        "all_expected_repository_node_ids": expected_nodes,
        "private_node_ids": [nodes["general"][1]],
        "actual_results": {
            "private_file_subset": {"included_in_general": True, "passed": 1, "skipped": 0},
            "unique_repository_node_union": {
                "expected": len(expected_nodes),
                "passed_in_correct_local_environment": len(expected_nodes),
                "missing": 0,
                "duplicate_node_ids_in_expected_union": 0,
            },
        },
    }
    gates["private"], paths["private"] = _add_gate(
        root,
        "private",
        private_receipt,
        {
            "result": _result(nodes["general"], general_skips),
            "junit": _result_junit(nodes["general"], general_skips),
            "input_before": before,
            "input_after": after,
        },
    )
    gates["fresh"], paths["fresh"] = _add_gate(
        root,
        "fresh",
        {
            "status": "PASS_CLEAN_EXACT_CHECKOUT",
            "code_commit": COMMIT,
            "source_tree": TREE,
            "detached": True,
            "clean_status": True,
            "object_alternates": False,
            "hardlinked_objects": False,
            "existing_checkout_runtime_reference": False,
            "project": str(project.resolve()),
            "source_hashes": source_hashes,
        },
        {"tool_before": tool_before, "tool_after": tool_after},
    )

    elevated_nodes = nodes["elevated"]
    elevated_original = ("\n".join(elevated_nodes) + "\n").encode()
    elevated_runtime = {**runtime, "pid": 303}
    elevated_observation = {
        "pid": 303,
        "observer_pid": 404,
        "image_sha256": _sha256(runtime_image),
    }
    full_tool_files = {
        str(path.resolve()): _sha256(path)
        for path in (source, lanes, runtime_image, ruff_binary, record)
    }
    full_tool_snapshot = {
        "files": full_tool_files,
        "package_record_hashes": {"fixture": _sha256(record)},
        "prior_pinned_receipt_sha256": hashlib.sha256(_raw(tool_before)).hexdigest(),
        "purpose": "before-after pinned tool file identity",
    }
    elevated_raws: dict[str, Any] = {
        "stdout": b"elevated pass\n",
        "stderr": b"",
        "junit": _passing_junit(elevated_nodes),
        "runtime_provenance": elevated_runtime,
        "token_observation": elevated_observation,
        "module_origin": module_origin("elevated"),
        "tool_snapshot": full_tool_snapshot,
        "runtime_script": b"runtime script\n",
        "execution_script": b"execution script\n",
        "original_m01_manifest": elevated_original,
    }
    original_path = _artifact_path(root, "elevated", "original_m01_manifest").resolve()
    execution = {
        "code_commit": COMMIT,
        "source_tree": TREE,
        "node_ids": elevated_nodes,
        "node_count": 2,
        "expected_passed": 2,
        "expected_skips": [],
        "expected_failed": 0,
        "expected_errors": 0,
        "expected_deselected": 0,
        "command": ["python", "-m", "pytest", *elevated_nodes],
        "collection_exit_code": 0,
        "original_M01_manifest_sha256": hashlib.sha256(elevated_original).hexdigest(),
        "original_M01_manifest_path": str(original_path),
        "run_uuid": "00000000-0000-4000-8000-000000000003",
        "attempt_uuid": "00000000-0000-4000-8000-000000000004",
        "tool_snapshot_sha256": hashlib.sha256(_raw(elevated_raws["tool_snapshot"])).hexdigest(),
        "bootstrap_sha256": hashlib.sha256(_raw(elevated_raws["runtime_script"])).hexdigest(),
        "executor_observer_script_sha256": hashlib.sha256(
            _raw(elevated_raws["execution_script"])
        ).hexdigest(),
    }
    elevated_raws["execution_manifest"] = execution
    artifact_receipts = {
        _artifact_path(root, "elevated", kind).name: hashlib.sha256(_raw(value)).hexdigest()
        for kind, value in elevated_raws.items()
        if kind != "original_m01_manifest"
    }
    elevated_receipt = {
        "code_commit": COMMIT,
        "source_tree": TREE,
        "actual_node_ids": elevated_nodes,
        "LOCAL_DECISION": "PASS_EXACT_ORIGINAL_TWO_NODE_EVIDENCE",
        "passed": 2,
        "failed": 0,
        "skipped": 0,
        "deselected": 0,
        "errors": 0,
        "exit_code": 0,
        "source_unchanged": True,
        "runtime_residue": {"processes": 0, "test_containers": 0},
        "manifest_sha256": hashlib.sha256(_raw(execution)).hexdigest(),
        "original_M01_manifest_sha256": hashlib.sha256(elevated_original).hexdigest(),
        "original_M01_manifest_path": str(original_path),
        "run_uuid": execution["run_uuid"],
        "attempt_uuid": execution["attempt_uuid"],
        "artifact_receipts": artifact_receipts,
        "observed_token": {
            "pid": 303,
            "administrator": True,
            "integrity": "High",
            "token_elevation_type": "Full",
        },
        "observed_executor_token": {
            "pid": 404,
            "administrator": True,
            "integrity": "High",
            "token_elevation_type": "Full",
        },
        "independent_review": {
            "runtime_pid": 303,
            "observer_pid": 404,
            "mock_PIDs_credited": False,
            "decision": "PASS_EXACT_TWO_NODE_INDEPENDENT",
        },
        "tool_files_verified_before_after": len(full_tool_files),
    }
    gates["elevated"], paths["elevated"] = _add_gate(
        root, "elevated", elevated_receipt, elevated_raws
    )
    gates["cleanup"], paths["cleanup"] = _add_gate(
        root,
        "cleanup",
        {
            "code_commit": COMMIT,
            "source_tree": TREE,
            "cleanup": {
                "filesystem_residue_zero": True,
                "root_count": 0,
                "files": 0,
                "bytes": 0,
            },
            "runtime": {
                "active_python_pytest": 0,
                "new_experiment_containers": 0,
                "service_differences": [],
            },
            "user_untracked_preserved": True,
            "tracked_changes": 0,
        },
        {"inventory": {"status": "zero_residue"}},
    )
    producer_executable = root / "producer.exe"
    producer_script = root / "produce-manifest.py"
    producer_executable.write_bytes(b"manifest producer executable")
    producer_script.write_bytes(b"manifest producer script")
    producer = _process(
        "r7_local_manifest_producer",
        "00000000-0000-4000-8000-000000000001",
        101,
        "2026-09-08T23:50:00Z",
        _sha256(producer_executable),
        _sha256(producer_script),
    )
    candidate = {"commit": COMMIT, "tree": TREE}
    producer_observation = {
        "schema": closure.PRODUCER_OBSERVATION_SCHEMA,
        "candidate": candidate,
        "run_uuid": producer["run_uuid"],
        "pid": producer["pid"],
        "create_time_utc": producer["create_time_utc"],
        "executable_path": str(producer_executable.resolve()),
        "executable_sha256": producer["executable_sha256"],
        "script_path": str(producer_script.resolve()),
        "script_sha256": producer["script_sha256"],
        "observed_at_utc": "2026-09-08T23:55:00Z",
        "method": "os_process_observation",
    }
    producer_observation_path = _write_json(
        root / "producer-observation.json", producer_observation
    )
    manifest = {
        "schema": closure.MANIFEST_SCHEMA,
        "created_at_utc": "2026-09-09T00:00:00Z",
        "expires_at_utc": "2026-09-09T01:00:00Z",
        "candidate": candidate,
        "producer": producer,
        "producer_observation": _ref(producer_observation_path, "producer_observation"),
        "evidence_roots": [str(root)],
        "gates": gates,
        "boundaries": dict(closure.BOUNDARIES),
    }
    manifest_path = _write_json(root / "manifest.json", manifest)
    return {
        "root": root,
        "project": project,
        "manifest": manifest_path,
        "producer_observation": producer_observation_path,
        "producer_executable": producer_executable,
        "producer_script": producer_script,
        "paths": paths,
        "nodes": nodes,
    }


def _junit(*cases: tuple[str, str, str, str | None]) -> bytes:
    body: list[str] = []
    for classname, name, outcome, message in cases:
        terminal = ""
        if outcome != "passed":
            terminal = f'<{outcome} message="{message or ""}" />'
        body.append(f'<testcase classname="{classname}" name="{name}">{terminal}</testcase>')
    return ("<testsuite>" + "".join(body) + "</testsuite>").encode()


def _private_snapshot(path: Path, *, stage: str) -> dict[str, object]:
    observed_at = (
        "2026-09-09T00:00:00+00:00" if stage == "before-private" else "2026-09-09T00:01:00+00:00"
    )
    return {
        "TASK": "PRIVATE-INPUT-READBACK",
        "stage": stage,
        "utc": observed_at,
        "status": "PASS_READONLY_IDENTITY",
        "config_path": str(path),
        "config_sha256": _sha256(path) if path.is_file() else "0" * 64,
        "source_tree": TREE,
        "entries": [
            {
                "identity": "fixture_input",
                "path": str(path),
                "relative_path": "private/input.bin",
                "bytes": path.stat().st_size if path.is_file() else 1,
                "sha256": _sha256(path) if path.is_file() else "0" * 64,
                "mtime_ns": path.stat().st_mtime_ns if path.is_file() else 0,
            }
        ],
        "mutations": [],
        "execution_credit": False,
        "authority_credit": False,
        "temporal_limit": "identity observation only",
    }


def test_governed_skips_accepts_actual_object_schema() -> None:
    expected = [
        {
            "nodeid": "tests/test_lane.py::test_platform_only",
            "reason": "requires Windows host",
        }
    ]

    assert closure._governed_skips(expected, "expected_skips") == {
        "tests/test_lane.py::test_platform_only": "requires Windows host"
    }


@pytest.mark.parametrize(
    "expected_skips",
    [
        [
            {"nodeid": "tests/test_lane.py::test_one", "reason": "governed"},
            {"nodeid": "tests/test_lane.py::test_one", "reason": "governed"},
        ],
        [{"nodeid": "tests/test_lane.py::test_one", "reason": ""}],
        [{"nodeid": "tests/test_lane.py::test_one"}],
    ],
)
def test_governed_skips_rejects_duplicate_or_missing_reason(
    expected_skips: list[dict[str, str]],
) -> None:
    with pytest.raises(closure.R7LocalClosureError, match="expected_skips"):
        closure._governed_skips(expected_skips, "expected_skips")


def test_junit_rejects_different_node_with_same_count() -> None:
    raw = _junit(("tests.test_lane", "test_other", "passed", None))

    with pytest.raises(closure.R7LocalClosureError, match="junit_case_identity_mismatch"):
        closure._junit(raw, ["tests/test_lane.py::test_expected"])


def test_junit_rejects_duplicate_exact_node() -> None:
    case = ("tests.test_lane", "test_expected", "passed", None)

    with pytest.raises(closure.R7LocalClosureError, match="junit_case_duplicate"):
        closure._junit(_junit(case, case), ["tests/test_lane.py::test_expected"])


def test_junit_preserves_failure_outcome() -> None:
    node = "tests/test_lane.py::test_expected"
    raw = _junit(("tests.test_lane", "test_expected", "failure", "assertion failed"))

    assert closure._junit(raw, [node]) == {node: "failure"}


def test_junit_rejects_ambiguous_terminal_outcome() -> None:
    raw = (
        b'<testsuite><testcase classname="tests.test_lane" name="test_expected">'
        b'<failure message="failed"/><skipped message="skip"/>'
        b"</testcase></testsuite>"
    )

    with pytest.raises(closure.R7LocalClosureError, match="junit_case_outcome_ambiguous"):
        closure._junit(raw, ["tests/test_lane.py::test_expected"])


def test_source_snapshot_rejects_changed_file(tmp_path: Path) -> None:
    project = tmp_path / "fresh"
    source = project / "src" / "module.py"
    source.parent.mkdir(parents=True)
    source.write_text("before\n", encoding="utf-8")
    original = _sha256(source)
    fresh = {"project": str(project), "source_hashes": {"src/module.py": original}}
    static = {"source_hashes": {"src/module.py": original}}
    source.write_text("after\n", encoding="utf-8")

    with pytest.raises(closure.R7LocalClosureError, match="fresh_source_changed"):
        closure._verify_source_snapshot(fresh, static, [])


def test_source_snapshot_rejects_path_escape(tmp_path: Path) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    escaped = tmp_path / "escaped.py"
    escaped.write_text("outside\n", encoding="utf-8")
    digest = _sha256(escaped)
    fresh = {"project": str(project), "source_hashes": {"../escaped.py": digest}}
    static = {"source_hashes": {"../escaped.py": digest}}

    with pytest.raises(closure.R7LocalClosureError, match="fresh_source_path_unsafe"):
        closure._verify_source_snapshot(fresh, static, [])


def test_private_input_snapshots_reject_changed_identity(tmp_path: Path) -> None:
    private_input = tmp_path / "input.bin"
    private_input.write_bytes(b"original")
    before = _private_snapshot(private_input, stage="before-private")
    after = deepcopy(before)
    after["stage"] = "after-private"
    after["utc"] = "2026-09-09T00:01:00+00:00"
    entries = after["entries"]
    assert isinstance(entries, list)
    entries[0]["sha256"] = "f" * 64

    with pytest.raises(closure.R7LocalClosureError, match="private_inputs_changed"):
        closure._verify_private_inputs(
            closure.canonical_json_bytes(before),
            closure.canonical_json_bytes(after),
            TREE,
        )


def test_private_input_snapshots_reject_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bin"
    before = _private_snapshot(missing, stage="before-private")
    after = deepcopy(before)
    after["stage"] = "after-private"
    after["utc"] = "2026-09-09T00:01:00+00:00"

    with pytest.raises(closure.R7LocalClosureError, match="private_input_missing"):
        closure._verify_private_inputs(
            closure.canonical_json_bytes(before),
            closure.canonical_json_bytes(after),
            TREE,
        )


def test_json_reader_rejects_duplicate_keys() -> None:
    with pytest.raises(closure.R7LocalClosureError, match="json_duplicate_key"):
        closure._json_bytes(b'{"candidate":1,"candidate":2}', "manifest")


def test_assess_rejects_wrong_candidate_before_reading_evidence(tmp_path: Path) -> None:
    manifest = _minimal_manifest()
    manifest["candidate"] = {"commit": "3" * 40, "tree": TREE}
    manifest_path = _write_json(tmp_path / "manifest.json", manifest)

    with pytest.raises(closure.R7LocalClosureError, match="manifest_candidate_mismatch"):
        closure.assess(
            manifest_path,
            expected_commit=COMMIT,
            expected_tree=TREE,
            now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
        )


def test_assess_rejects_stale_manifest_before_reading_evidence(tmp_path: Path) -> None:
    manifest_path = _write_json(tmp_path / "manifest.json", _minimal_manifest())

    with pytest.raises(closure.R7LocalClosureError, match="manifest_stale"):
        closure.assess(
            manifest_path,
            expected_commit=COMMIT,
            expected_tree=TREE,
            now=datetime(2026, 9, 9, 1, 0, tzinfo=UTC),
        )


def test_review_requires_external_sha_pin_and_keeps_r7_pending(tmp_path: Path) -> None:
    assessment, review_path, review_sha256 = _review_fixture(tmp_path)

    with pytest.raises(closure.R7LocalClosureError, match="review_sha256_mismatch"):
        closure.validate_review(
            assessment,
            review_path,
            "d" * 64,
            now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
        )

    validated = closure.validate_review(
        assessment,
        review_path,
        review_sha256,
        now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
    )
    assert validated["status"] == "PASS_APPROVED_FOR_LOCAL_PUBLICATION"
    assert validated["r7s5_decision"] == "NO-GO_PENDING_IMMUTABLE_LOCAL_PUBLICATION"
    assert validated["r8_m00_enabled"] is False


def test_review_rejects_candidate_swap(tmp_path: Path) -> None:
    assessment, review_path, _ = _review_fixture(tmp_path)
    review = closure._json_bytes(review_path.read_bytes(), "review")
    review["candidate"] = {"commit": "3" * 40, "tree": TREE}
    review_path = _write_json(review_path, review)

    with pytest.raises(closure.R7LocalClosureError, match="review_boundary_rejected"):
        closure.validate_review(
            assessment,
            review_path,
            _sha256(review_path),
            now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
        )


def test_review_rejects_after_assessment_expiry(tmp_path: Path) -> None:
    assessment, review_path, review_sha256 = _review_fixture(tmp_path)

    with pytest.raises(closure.R7LocalClosureError, match="review_boundary_rejected"):
        closure.validate_review(
            assessment,
            review_path,
            review_sha256,
            now=datetime(2026, 9, 9, 1, 0, tzinfo=UTC),
        )


def test_review_rejects_assessment_not_pending(tmp_path: Path) -> None:
    assessment, review_path, review_sha256 = _review_fixture(tmp_path)
    assessment["decision"] = "GO"

    with pytest.raises(closure.R7LocalClosureError, match="assessment_not_passed"):
        closure.validate_review(
            assessment,
            review_path,
            review_sha256,
            now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
        )


def test_review_missing_is_fail_closed(tmp_path: Path) -> None:
    assessment, _, _ = _review_fixture(tmp_path)
    missing = tmp_path / "missing-review.json"

    with pytest.raises(closure.R7LocalClosureError, match="review_missing"):
        closure.validate_review(
            assessment,
            missing,
            "d" * 64,
            now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
        )


def _assess(case: dict[str, Any]) -> dict[str, Any]:
    return closure.assess(
        case["manifest"],
        expected_commit=COMMIT,
        expected_tree=TREE,
        now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
    )


def _manifest_artifact(manifest: dict[str, Any], role: str, kind: str) -> dict[str, Any]:
    return next(ref for ref in manifest["gates"][role]["artifacts"] if ref["kind"] == kind)


def test_happy_assessment_stops_at_independent_review_boundary(tmp_path: Path) -> None:
    assessment = _assess(_happy_evidence(tmp_path))

    assert assessment["status"] == "PASS_READY_FOR_INDEPENDENT_REVIEW"
    assert assessment["decision"] == "PENDING_INDEPENDENT_REVIEW"
    assert assessment["r7s5_decision"] == "NO-GO_PENDING_INDEPENDENT_REVIEW"
    assert assessment["r8_m00_enabled"] is False
    assert assessment["production_go_enabled"] is False
    assert assessment["node_accounting"]["missing"] == 0
    assert assessment["node_accounting"]["unexpected"] == 0


def test_assessment_rejects_deleted_artifact(tmp_path: Path) -> None:
    case = _happy_evidence(tmp_path)
    case["paths"]["focused"]["stderr"].unlink()

    with pytest.raises(closure.R7LocalClosureError, match="artifact_ref_missing"):
        _assess(case)


def test_assessment_rejects_corrupt_artifact_bytes(tmp_path: Path) -> None:
    case = _happy_evidence(tmp_path)
    case["paths"]["focused"]["stdout"].write_bytes(b"changed after freeze")

    with pytest.raises(closure.R7LocalClosureError, match="artifact_ref_identity_mismatch"):
        _assess(case)


def test_assessment_rejects_semantic_result_swap(tmp_path: Path) -> None:
    case = _happy_evidence(tmp_path)
    manifest = closure._json_bytes(case["manifest"].read_bytes(), "manifest")
    focused = _manifest_artifact(manifest, "focused", "result")
    general = _manifest_artifact(manifest, "general", "result")
    focused_copy = dict(focused)
    focused.clear()
    focused.update(general)
    general.clear()
    general.update(focused_copy)
    _write_json(case["manifest"], manifest)

    with pytest.raises(closure.R7LocalClosureError, match="focused_result_rejected"):
        _assess(case)


def test_assessment_rejects_same_count_wrong_junit_node(tmp_path: Path) -> None:
    case = _happy_evidence(tmp_path)
    manifest = closure._json_bytes(case["manifest"].read_bytes(), "manifest")
    junit_path = case["paths"]["focused"]["junit"]
    junit_path.write_bytes(_junit(("tests.test_focus", "test_other", "passed", None)))
    frozen_ref = _manifest_artifact(manifest, "focused", "junit")
    frozen_ref.update(_ref(junit_path, "junit"))
    _write_json(case["manifest"], manifest)

    with pytest.raises(closure.R7LocalClosureError, match="junit_case_identity_mismatch"):
        _assess(case)


def test_assessment_rejects_private_input_changed_after_snapshot(tmp_path: Path) -> None:
    case = _happy_evidence(tmp_path)
    (case["root"] / "private-input.bin").write_bytes(b"mutated")

    with pytest.raises(closure.R7LocalClosureError, match="private_input_identity_changed"):
        _assess(case)


def test_publication_fault_consumes_fixed_candidate_latch_across_new_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evm.scale_validation import phase_b2_r7s3_handle_io as bound_io
    from evm.scale_validation import phase_b2_r7s4_handle_io as durable_io

    case = _happy_evidence(tmp_path)
    first_assessment = _assess(case)
    first_review, first_review_sha = _review_for_assessment(
        case["root"] / "review-one", first_assessment
    )
    output_parent = (tmp_path / "published").resolve()
    output_parent.mkdir()
    primitive_calls: list[Path] = []

    class FakeWindowsHandleApi:
        def open_directory(self, path: str) -> int:
            return 1

        def identity(self, handle: int) -> object:
            return object()

        def flush_directory(self, handle: int) -> None:
            return None

        def close(self, handle: int | None) -> None:
            return None

    def fail_first_primitive(directory: Path, leaf: str, raw: bytes, *, run_uuid: str) -> None:
        primitive_calls.append(Path(directory) / leaf)
        raise OSError("forced reservation publication failure")

    monkeypatch.setattr(bound_io, "_reject_unsafe_directory_identity", lambda *a, **k: None)
    monkeypatch.setattr(durable_io, "WindowsHandleApi", FakeWindowsHandleApi)
    monkeypatch.setattr(durable_io, "publish_bound_no_replace_durable", fail_first_primitive)

    with pytest.raises(OSError, match="forced reservation publication failure"):
        closure.publish_local_closure(
            output_parent,
            case["manifest"],
            first_review,
            first_review_sha,
            expected_commit=COMMIT,
            expected_tree=TREE,
            now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
        )

    latch = output_parent / f"r7s5-local-code-closure-{COMMIT}"
    reservation = output_parent / f"r7s5-local-code-{COMMIT}.reservation.json"
    assert latch.is_dir()
    assert not reservation.exists()
    assert len(primitive_calls) == 1

    manifest = closure._json_bytes(case["manifest"].read_bytes(), "manifest")
    new_producer = _process(
        "r7_local_manifest_producer",
        "00000000-0000-4000-8000-000000000005",
        105,
        "2026-09-08T23:52:00Z",
        _sha256(case["producer_executable"]),
        _sha256(case["producer_script"]),
    )
    manifest["producer"] = new_producer
    observation = closure._json_bytes(
        case["producer_observation"].read_bytes(), "producer_observation"
    )
    observation.update(
        {
            "run_uuid": new_producer["run_uuid"],
            "pid": new_producer["pid"],
            "create_time_utc": new_producer["create_time_utc"],
        }
    )
    _write_json(case["producer_observation"], observation)
    manifest["producer_observation"] = _ref(case["producer_observation"], "producer_observation")
    _write_json(case["manifest"], manifest)
    second_assessment = _assess(case)
    second_review, second_review_sha = _review_for_assessment(
        case["root"] / "review-two", second_assessment
    )

    with pytest.raises(closure.R7LocalClosureError, match="closure_candidate_already_reserved"):
        closure.publish_local_closure(
            output_parent,
            case["manifest"],
            second_review,
            second_review_sha,
            expected_commit=COMMIT,
            expected_tree=TREE,
            now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
        )

    assert latch.is_dir()
    assert not reservation.exists()
    assert len(primitive_calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handle APIs")
def test_native_windows_publication_succeeds_with_local_only_boundary(
    tmp_path: Path,
) -> None:
    case = _happy_evidence(tmp_path)
    assessment = _assess(case)
    review_path, review_sha256 = _review_for_assessment(case["root"] / "native-review", assessment)
    output_parent = (tmp_path / "native-publication").resolve()
    output_parent.mkdir()

    result = closure.publish_local_closure(
        output_parent,
        case["manifest"],
        review_path,
        review_sha256,
        expected_commit=COMMIT,
        expected_tree=TREE,
        now=datetime(2026, 9, 9, 0, 30, tzinfo=UTC),
    )

    assert result["status"] == "PASS_LOCAL_PUBLICATION_ONLY"
    assert result["local_decision"] == "GO_LOCAL_CODE_EVIDENCE"
    assert result["global_decision"] == "NO-GO_CLOSURE_CANONICAL_READBACK_PENDING"
    assert result["r8_m00_enabled"] is False
    assert result["R7_M15_complete"] is False
    assert result["production_go_enabled"] is False
    assert result["acceptance_credit"] is False
    assert Path(result["output_directory"]).is_dir()
    assert Path(result["terminal_index_pin"]["path"]).is_file()


def _load_closure_cli() -> Any:
    script = Path(__file__).parents[1] / "scripts" / "dev" / "close_pre_r8_r7s5.py"
    spec = importlib.util.spec_from_file_location("test_close_pre_r8_r7s5", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_candidate_accepts_only_tracked_closure_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_closure_cli()
    project = (tmp_path / "project").resolve()
    module_source = project / "src" / "evm" / "scale_validation" / "r7_local_closure.py"
    cli_source = project / "scripts" / "dev" / "close_pre_r8_r7s5.py"
    module_source.parent.mkdir(parents=True)
    cli_source.parent.mkdir(parents=True)
    module_source.write_text("# closure\n", encoding="utf-8")
    cli_source.write_text("# cli\n", encoding="utf-8")
    git = (tmp_path / "git.exe").resolve()
    git.write_bytes(b"git")
    seen_ls_files: list[str] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        operation = command[3]
        if operation == "rev-parse":
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{COMMIT}\n{TREE}\n".encode(), stderr=b""
            )
        if operation == "ls-files":
            seen_ls_files.append(command[-1])
            return subprocess.CompletedProcess(
                command, 0, stdout=(command[-1] + "\n").encode(), stderr=b""
            )
        if operation == "status":
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        raise AssertionError(command)

    monkeypatch.setattr(cli.r7_local_closure, "__file__", str(module_source))
    monkeypatch.setattr(cli, "__file__", str(cli_source))
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._candidate(project, git) == (COMMIT, TREE)
    assert seen_ls_files == [
        "src/evm/scale_validation/r7_local_closure.py",
        "scripts/dev/close_pre_r8_r7s5.py",
    ]


def test_cli_candidate_rejects_untracked_closure_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_closure_cli()
    project = (tmp_path / "project").resolve()
    module_source = project / "src" / "evm" / "scale_validation" / "r7_local_closure.py"
    cli_source = project / "scripts" / "dev" / "close_pre_r8_r7s5.py"
    module_source.parent.mkdir(parents=True)
    cli_source.parent.mkdir(parents=True)
    module_source.write_text("# injected closure\n", encoding="utf-8")
    cli_source.write_text("# tracked cli\n", encoding="utf-8")
    git = (tmp_path / "git.exe").resolve()
    git.write_bytes(b"git")

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        operation = command[3]
        if operation == "rev-parse":
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{COMMIT}\n{TREE}\n".encode(), stderr=b""
            )
        if operation == "ls-files":
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"untracked")
        raise AssertionError(command)

    monkeypatch.setattr(cli.r7_local_closure, "__file__", str(module_source))
    monkeypatch.setattr(cli, "__file__", str(cli_source))
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="git_readback_failed:ls-files:1"):
        cli._candidate(project, git)

from __future__ import annotations

import ast
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.dev import render_pre_r8_r7s3_oob_candidate as oob


NOW = datetime(2026, 9, 1, 23, 0, 0, tzinfo=UTC)
CREATED = "2026-09-01T22:59:00Z"
EXPIRES = "2026-09-01T23:29:00Z"
GLOBAL_RUN_ID = "pre-r8-r7s3-20260901T225900Z-cafebabe"
RUN_UUID = "11223344-5566-4788-899a-bbccddeeff00"
ATTEMPT_UUID = "aabbccdd-eeff-4111-8222-334455667788"
COMMIT = "1" * 40
TREE = "2" * 40
UNTRACKED_SHA = "3" * 64


def _pin(path: Path, character: str = "a", size: int = 101) -> dict[str, Any]:
    return {"path": str(path), "sha256": character * 64, "bytes": size}


def _source_pin(tmp_path: Path, role: str, ordinal: int) -> dict[str, Any]:
    character = f"{ordinal:x}"
    return {
        "path": str(tmp_path / "repository" / f"{role}.py"),
        "sha256": character * 64,
        "bytes": 1000 + ordinal,
        "relative_path": f"enterprise-vision-mlops/{role}.py",
        "lf_normalized_sha256": character * 64,
        "git_head_blob_oid": character * 40,
        "git_mode": "100644",
    }


def _work_order(tmp_path: Path, *, output: Path | None = None) -> dict[str, Any]:
    candidate_root = output or (tmp_path / "candidate")
    source_pins = {
        role: _source_pin(tmp_path, role, ordinal)
        for ordinal, role in enumerate(sorted(oob.SOURCE_ROLES), start=4)
    }
    return {
        "schema": oob.WORK_ORDER_SCHEMA,
        "state": oob.WORK_ORDER_STATE,
        "approval_request_id": "approval-request-r7s3-001",
        "work_order_id": "pre-r8-r7s3-oob-work-order-001",
        "created_at_utc": CREATED,
        "expires_at_utc": EXPIRES,
        "run_identity": {
            "qualification_id": f"{GLOBAL_RUN_ID}-qualification",
            "global_run_id": GLOBAL_RUN_ID,
            "domain_run_id": f"{GLOBAL_RUN_ID}-wsl",
            "domain": "wsl",
            "run_uuid": RUN_UUID,
            "attempt_uuid": ATTEMPT_UUID,
            "execution_mode": oob.EXECUTION_MODE,
        },
        "canonical_repository": {
            "path": str(tmp_path / "repository"),
            "branch": "codex/pre-r8-r7s3-hardening",
            "commit": COMMIT,
            "tree": TREE,
            "tracked_changes": 0,
            "untracked": {
                "count": 4244,
                "encoding": "utf-8-nul-sorted",
                "path_set_sha256": UNTRACKED_SHA,
            },
        },
        "evidence_layout": {
            "candidate_root": str(candidate_root),
            "runtime_evidence_root": str(tmp_path / "runtime-evidence"),
            "root_leaf": "root-001",
            "root_emergency_leaf": "root-001-emergency",
            "staging_leaf": "staging-001",
            "outer_leaf": "outer-001",
            "qualification_leaf": "qualification-001",
        },
        "parent_map": _pin(tmp_path / "parent-map.json", "a", 201),
        "source_pins": source_pins,
        "bootstrap_pins": {
            "root_orchestrator": _pin(tmp_path / "root-orchestrator.py", "b", 301),
            "stager_bootstrap_sha256": "c" * 64,
            "outer_bootstrap_sha256": "d" * 64,
            "inner_bootstrap_sha256": "e" * 64,
        },
        "runtime_tcb": {
            "python": {
                "path": str(tmp_path / "python.exe"),
                "sha256": "f" * 64,
                "bytes": 104264,
                "version": "3.13.11",
            },
            "closure_inventory": _pin(tmp_path / "python-tcb.json", "9", 401),
            "closure_status": "review_pending",
        },
        "timeout_contracts": copy.deepcopy(oob.TIMEOUT_CONTRACTS),
        "call_contract": copy.deepcopy(oob.CALL_CONTRACT),
        "containment_contract": copy.deepcopy(oob.CONTAINMENT_CONTRACT),
        "safety_contract": copy.deepcopy(oob.SAFETY_CONTRACT),
        "artifact_dag": copy.deepcopy(oob.ARTIFACT_DAG_CONTRACT),
    }


def _artifacts(tmp_path: Path, *, output: Path | None = None) -> oob.CandidateArtifacts:
    candidate_root = output or (tmp_path / "candidate")
    return oob.build_candidate_artifacts(
        candidate_root,
        _work_order(tmp_path, output=candidate_root),
        validation_time=NOW,
    )


def _approval_request(artifacts: oob.CandidateArtifacts) -> dict[str, Any]:
    return oob.strict_canonical_json_bytes(artifacts.approval_request, "approval_request")


def _receipt(
    request: dict[str, Any],
    *,
    mechanism: str = "independent_out_of_band_sha256",
    issued_at: str = "2026-09-01T23:01:00Z",
    expires_at: str = "2026-09-01T23:10:00Z",
) -> bytes:
    request_raw = oob.canonical_json_bytes(request)
    return oob.canonical_json_bytes(
        {
            "schema": oob.APPROVAL_RECEIPT_SCHEMA,
            "status": "approved",
            "decision": "approve_exact_candidate_once",
            "approval_request_id": request["approval_request_id"],
            "issued_at_utc": issued_at,
            "expires_at_utc": expires_at,
            "authority": {
                "mechanism": mechanism,
                "reviewer_identity": "independent-reviewer:test-fixture",
                "approval_id": "external-approval:test-fixture",
            },
            "approval_request": {
                "sha256": hashlib.sha256(request_raw).hexdigest(),
                "bytes": len(request_raw),
            },
            "subject": copy.deepcopy(request["subject"]),
        }
    )


def test_work_order_round_trip_is_strict_and_canonical(tmp_path: Path) -> None:
    value = _work_order(tmp_path)
    raw = oob.canonical_json_bytes(value)
    assert raw.endswith(b"\n")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert oob.load_work_order_bytes(raw, validation_time=NOW) == value


@pytest.mark.parametrize(
    "raw",
    [
        b'{"same":1,"same":1}\n',
        b'{"value":NaN}\n',
        b'{"value":Infinity}\n',
        b'\xef\xbb\xbf{"value":1}\n',
        b'{"value":1}\r\n',
        b'{"value":1}\n\n',
        b'{"value":1}\ntrailing',
        b'{"value":"\xff"}\n',
    ],
)
def test_strict_json_rejects_duplicate_nonfinite_encoding_and_layout(raw: bytes) -> None:
    with pytest.raises(oob.R7S3OOBError):
        oob.strict_canonical_json_bytes(raw, "mutation")


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_work_order_rejects_missing_or_unknown_top_level_field(
    tmp_path: Path, mutation: str
) -> None:
    value = _work_order(tmp_path)
    if mutation == "missing":
        value.pop("parent_map")
    else:
        value["unexpected"] = True
    with pytest.raises(oob.R7S3OOBError, match="work_order_fields_mismatch"):
        oob.validate_work_order(value, validation_time=NOW)


def test_work_order_rejects_expired_and_future_windows(tmp_path: Path) -> None:
    expired = _work_order(tmp_path)
    expired["created_at_utc"] = "2026-09-01T21:00:00Z"
    expired["expires_at_utc"] = "2026-09-01T21:30:00Z"
    with pytest.raises(oob.R7S3OOBError, match="work_order_expired"):
        oob.validate_work_order(expired, validation_time=NOW)

    future = _work_order(tmp_path)
    future["created_at_utc"] = "2026-09-01T23:01:00Z"
    future["expires_at_utc"] = "2026-09-01T23:31:00Z"
    with pytest.raises(oob.R7S3OOBError, match="work_order_from_future"):
        oob.validate_work_order(future, validation_time=NOW)


def test_work_order_rejects_ttl_contract_mutation(tmp_path: Path) -> None:
    value = _work_order(tmp_path)
    value["expires_at_utc"] = "2026-09-01T23:30:00Z"
    with pytest.raises(oob.R7S3OOBError, match="work_order_ttl_exact_mismatch"):
        oob.validate_work_order(value, validation_time=NOW)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("domain", "windows"),
        ("domain_run_id", f"{GLOBAL_RUN_ID}-windows"),
        ("qualification_id", f"{GLOBAL_RUN_ID}-wsl"),
        ("run_uuid", ATTEMPT_UUID),
    ],
)
def test_work_order_rejects_identity_domain_swap(
    tmp_path: Path, field: str, replacement: str
) -> None:
    value = _work_order(tmp_path)
    value["run_identity"][field] = replacement
    with pytest.raises(oob.R7S3OOBError):
        oob.validate_work_order(value, validation_time=NOW)


@pytest.mark.parametrize(
    ("section", "path", "replacement"),
    [
        ("call_contract", ("candidate_observed_calls", "live_wsl_qualification"), 1),
        ("call_contract", ("candidate_observed_calls", "r8"), 1),
        ("call_contract", ("candidate_observed_calls", "service_mutation"), 1),
        ("safety_contract", ("overwrite_allowed",), True),
        ("safety_contract", ("local_self_signature_is_authority",), True),
        ("containment_contract", ("private_inherited_capability_required",), False),
    ],
)
def test_work_order_rejects_call_safety_or_containment_relaxation(
    tmp_path: Path,
    section: str,
    path: tuple[str, ...],
    replacement: Any,
) -> None:
    value = _work_order(tmp_path)
    target = value[section]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(oob.R7S3OOBError):
        oob.validate_work_order(value, validation_time=NOW)


def test_bootstrap_rendering_is_byte_deterministic_and_inert(tmp_path: Path) -> None:
    first = _artifacts(tmp_path)
    second = _artifacts(tmp_path)
    assert first.bootstrap_source == second.bootstrap_source
    assert first.bootstrap_argv == second.bootstrap_argv
    source = first.bootstrap_source
    assert source.endswith(b"\n") and not source.endswith(b"\n\n")
    assert not source.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in source
    assert b"R7S3_PRODUCTION_ENTRY_ENABLED=False" in source
    assert b"r7s3_external_approval_receipt_required" in source
    assert b"exec(" not in source
    ast.parse(source.decode("ascii"))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: b"\xef\xbb\xbf" + raw,
        lambda raw: raw.replace(b"\n", b"\r\n"),
        lambda raw: raw[:-1],
        lambda raw: raw + b"\n",
    ],
)
def test_bootstrap_source_byte_mutations_are_rejected(tmp_path: Path, mutation: Any) -> None:
    artifacts = _artifacts(tmp_path)
    request = _work_order(tmp_path)["runtime_tcb"]["python"]
    with pytest.raises(oob.R7S3OOBError):
        oob.canonical_bootstrap_argv(request["path"], mutation(artifacts.bootstrap_source))


@pytest.mark.parametrize("mutation", ["extra", "order", "source"])
def test_canonical_argv_mutation_is_rejected(tmp_path: Path, mutation: str) -> None:
    artifacts = _artifacts(tmp_path)
    python_path = _work_order(tmp_path)["runtime_tcb"]["python"]["path"]
    argv = json.loads(artifacts.bootstrap_argv)
    if mutation == "extra":
        argv.append("--work-order=attacker-selected.json")
    elif mutation == "order":
        argv[1], argv[2] = argv[2], argv[1]
    else:
        argv[5] += "\n# repinned"
    mutated = oob.canonical_json_bytes(argv)
    assert (
        hashlib.sha256(mutated).hexdigest() != hashlib.sha256(artifacts.bootstrap_argv).hexdigest()
    )
    with pytest.raises(oob.R7S3OOBError, match="bootstrap_argv_exact_binding_mismatch"):
        oob.validate_bootstrap_argv(
            mutated,
            python_path=python_path,
            bootstrap_source=artifacts.bootstrap_source,
        )


def test_candidate_approval_request_binds_b_w_r_revision_and_zero_calls(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    request = _approval_request(artifacts)
    subject = request["subject"]
    assert request["status"] == "review_pending"
    assert request["decision"] == "not_approved"
    assert request["production_entry_enabled"] is False
    assert request["call_counts"] == oob.CANDIDATE_OBSERVED_CALLS
    assert subject["bootstrap"]["sha256"] == hashlib.sha256(artifacts.bootstrap_source).hexdigest()
    assert subject["work_order"]["sha256"] == hashlib.sha256(artifacts.work_order).hexdigest()
    assert (
        subject["root_orchestrator"] == _work_order(tmp_path)["bootstrap_pins"]["root_orchestrator"]
    )
    assert subject["canonical_revision"] == {"commit": COMMIT, "tree": TREE}


def test_hash_cycle_is_forbidden_and_b_only_points_to_w_r_python(tmp_path: Path) -> None:
    value = _work_order(tmp_path)
    value["artifact_dag"]["work_order_contains_bootstrap_pin"] = True
    with pytest.raises(oob.R7S3OOBError, match="artifact_dag_or_hash_cycle_mismatch"):
        oob.validate_work_order(value, validation_time=NOW)

    direct_cycle = _work_order(tmp_path)
    direct_cycle["bootstrap_pins"]["root_bootstrap_sha256"] = "1" * 64
    with pytest.raises(oob.R7S3OOBError, match="bootstrap_hash_cycle_forbidden"):
        oob.validate_work_order(direct_cycle, validation_time=NOW)

    artifacts = _artifacts(tmp_path)
    work_order = json.loads(artifacts.work_order)
    bootstrap = artifacts.bootstrap_source.decode("ascii")
    assert "root_bootstrap_sha256" not in work_order
    assert "bootstrap_source_sha256" not in work_order
    bootstrap_tree = ast.parse(bootstrap)
    binding_assignment = next(
        node
        for node in bootstrap_tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "FROZEN_BINDINGS_JSON"
    )
    bindings = json.loads(ast.literal_eval(binding_assignment.value))
    assert set(bindings) >= {"work_order", "root_orchestrator", "python"}


def test_self_consistent_w_and_b_repin_cannot_approve_without_external_receipt(
    tmp_path: Path,
) -> None:
    value = _work_order(tmp_path)
    value["canonical_repository"]["commit"] = "8" * 40
    value["canonical_repository"]["tree"] = "7" * 40
    value["bootstrap_pins"]["root_orchestrator"]["sha256"] = "6" * 64
    artifacts = oob.build_candidate_artifacts(tmp_path / "candidate", value, validation_time=NOW)
    request = _approval_request(artifacts)
    locally_created_receipt = _receipt(request)
    with pytest.raises(
        oob.R7S3OOBError,
        match="caller_supplied_expected_external_receipt_sha256_required",
    ):
        oob.validate_external_approval_receipt(
            locally_created_receipt,
            expected_receipt_sha256=None,
            approval_request=request,
            validation_time=datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
        )


def test_exact_external_receipt_sha_and_subject_can_be_validated(tmp_path: Path) -> None:
    request = _approval_request(_artifacts(tmp_path))
    raw = _receipt(request)
    result = oob.validate_external_approval_receipt(
        raw,
        expected_receipt_sha256=hashlib.sha256(raw).hexdigest(),
        approval_request=request,
        validation_time=datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
    )
    assert result["receipt"]["decision"] == "approve_exact_candidate_once"
    assert result["independent_anchor_verified"] is False
    assert result["one_shot_consumed"] is False
    assert result["production_approval_eligible"] is False


def test_same_receipt_one_shot_consumption_is_atomic_and_still_not_authority(
    tmp_path: Path,
) -> None:
    request = _approval_request(_artifacts(tmp_path))
    raw = _receipt(request)
    validation = oob.validate_external_approval_receipt(
        raw,
        expected_receipt_sha256=hashlib.sha256(raw).hexdigest(),
        approval_request=request,
        validation_time=datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
    )
    consumption = tmp_path / "consumption"

    def consume() -> str:
        try:
            result = oob.consume_structurally_valid_receipt_once(validation, consumption)
            assert result["independent_anchor_verified"] is False
            assert result["production_approval_eligible"] is False
            return "consumed"
        except oob.R7S3OOBError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: consume(), range(2)))
    assert sorted(outcomes) == ["consumed", "external_receipt_already_consumed"]
    assert len(list(consumption.glob("receipt-*.consumed.json"))) == 1


@pytest.mark.parametrize(
    "mechanism",
    ["reviewer_text", "jira", "notion", "local_self_sign"],
)
def test_text_ledger_or_local_self_sign_is_never_approval(tmp_path: Path, mechanism: str) -> None:
    request = _approval_request(_artifacts(tmp_path))
    raw = _receipt(request, mechanism=mechanism)
    with pytest.raises(oob.R7S3OOBError, match="authority_mechanism_mismatch"):
        oob.validate_external_approval_receipt(
            raw,
            expected_receipt_sha256=hashlib.sha256(raw).hexdigest(),
            approval_request=request,
            validation_time=datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
        )


def test_external_receipt_wrong_sha_replay_and_domain_swap_are_rejected(
    tmp_path: Path,
) -> None:
    first = _artifacts(tmp_path, output=tmp_path / "candidate-a")
    first_request = _approval_request(first)
    raw = _receipt(first_request)
    with pytest.raises(oob.R7S3OOBError, match="external_receipt_sha256_mismatch"):
        oob.validate_external_approval_receipt(
            raw,
            expected_receipt_sha256="0" * 64,
            approval_request=first_request,
            validation_time=datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
        )

    second_value = _work_order(tmp_path, output=tmp_path / "candidate-b")
    second_value["approval_request_id"] = "approval-request-r7s3-002"
    second_value["run_identity"]["global_run_id"] = "pre-r8-r7s3-20260901T225901Z-deadbeef"
    second_global = second_value["run_identity"]["global_run_id"]
    second_value["run_identity"]["qualification_id"] = f"{second_global}-qualification"
    second_value["run_identity"]["domain_run_id"] = f"{second_global}-wsl"
    second = oob.build_candidate_artifacts(
        tmp_path / "candidate-b", second_value, validation_time=NOW
    )
    with pytest.raises(oob.R7S3OOBError, match="subject_or_decision_mismatch"):
        oob.validate_external_approval_receipt(
            raw,
            expected_receipt_sha256=hashlib.sha256(raw).hexdigest(),
            approval_request=_approval_request(second),
            validation_time=datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("issued", "expires", "now", "error"),
    [
        (
            "2026-09-01T23:03:00Z",
            "2026-09-01T23:10:00Z",
            datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
            "issuance_time_invalid",
        ),
        (
            "2026-09-01T23:01:00Z",
            "2026-09-01T23:02:00Z",
            datetime(2026, 9, 1, 23, 2, tzinfo=UTC),
            "expired_or_window_invalid",
        ),
    ],
)
def test_external_receipt_future_or_expired_is_rejected(
    tmp_path: Path,
    issued: str,
    expires: str,
    now: datetime,
    error: str,
) -> None:
    request = _approval_request(_artifacts(tmp_path))
    raw = _receipt(request, issued_at=issued, expires_at=expires)
    with pytest.raises(oob.R7S3OOBError, match=error):
        oob.validate_external_approval_receipt(
            raw,
            expected_receipt_sha256=hashlib.sha256(raw).hexdigest(),
            approval_request=request,
            validation_time=now,
        )


def test_publish_is_exclusive_no_overwrite_and_read_back_exact(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    pins = oob.publish_candidate(artifacts)
    before = {name: (artifacts.output_directory / name).read_bytes() for name in pins}
    with pytest.raises(FileExistsError):
        oob.publish_candidate(artifacts)
    after = {name: (artifacts.output_directory / name).read_bytes() for name in pins}
    assert before == after
    for name, raw in after.items():
        assert pins[name]["sha256"] == hashlib.sha256(raw).hexdigest()


def test_concurrent_candidate_publication_has_exactly_one_winner(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)

    def publish() -> str:
        try:
            oob.publish_candidate(artifacts)
            return "published"
        except FileExistsError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _value: publish(), range(2)))
    assert sorted(results) == ["published", "rejected"]
    assert {path.name for path in artifacts.output_directory.iterdir()} == {
        "work-order.json",
        "bootstrap-source.py",
        "bootstrap-argv.json",
        "approval-request.json",
        "candidate-index.json",
    }


def test_renderer_has_no_process_launch_or_production_entry(tmp_path: Path) -> None:
    source_path = Path(oob.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    assert "subprocess" not in imported
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"exec", "eval", "compile"}
        for node in ast.walk(tree)
    )
    artifacts = _artifacts(tmp_path)
    index = oob.strict_canonical_json_bytes(artifacts.candidate_index, "candidate_index")
    assert index["production_entry_enabled"] is False
    assert index["call_counts"] == {
        "subprocess": 0,
        "live_wsl_qualification": 0,
        "docker_lifecycle": 0,
        "service_mutation": 0,
        "r8": 0,
        "automatic_retry": 0,
        "force_kill": 0,
    }

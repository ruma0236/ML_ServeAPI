from __future__ import annotations

import hashlib
import importlib.util
import ntpath
import sys
import uuid
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s7_qualification_work_order as gate


ROOT = Path(__file__).resolve().parents[1]
QUALIFIER_PATH = ROOT / "scripts" / "dev" / "qualify_pre_r8_r7s7_windows.py"
GLOBAL_RUN_ID = "0c4fbab2-380c-46aa-809d-6a9ad3570bad"
RUN_UUID = "4a43c7aa-3df8-46e8-8b66-a4ba29519793"
ATTEMPT_UUID = "b01d338a-e69a-4a6e-a0dc-e4a59c8a6cb0"
COMMIT = "1" * 40
TREE = "2" * 40
PROJECT_ROOT = r"C:\approved\pre-r8-r7s7"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _directory(role: str, path: str, seed: int) -> dict[str, Any]:
    del seed
    return {
        "role": role,
        "final_path": path,
        "volume_serial_number": 71,
        "file_id_hex": hashlib.sha256(path.lower().encode()).hexdigest()[:32],
        "owner_sid": "S-1-5-21-1-2-3-1001",
        "security_descriptor_sha256": _sha(f"sd:{path.lower()}"),
        "dacl_present": True,
        "dacl_protected": True,
        "link_count": 1,
        "reparse_tag": 0,
        "file_type": 1,
        "is_directory": True,
    }


def _handle(role: str, path: str, seed: int) -> dict[str, Any]:
    qualified_role = f"qualification:{role}"
    return {
        "role": qualified_role,
        "final_path": path,
        "volume_serial_number": 71,
        "file_id_hex": f"{seed:032x}",
        "sha256": _sha(f"content:{role}"),
        "bytes": 100 + seed,
        "owner_sid": "S-1-5-21-1-2-3-1001",
        "security_descriptor_sha256": _sha(f"sd:{role}:{path}"),
        "dacl_present": True,
        "dacl_protected": True,
        "link_count": 1,
        "reparse_tag": 0,
        "file_type": 1,
        "creation_time_ns": 1_000_000 + seed,
        "parent_directory_identity": _directory(
            f"{qualified_role}:parent", ntpath.dirname(path), 10_000 + seed
        ),
    }


def _paths() -> dict[str, str]:
    return {
        "interpreter": r"C:\Python311\python.exe",
        "fixture": ntpath.join(PROJECT_ROOT, r"scripts\dev\pre_r8_r7s7_windows_fixture.py"),
        "qualifier": ntpath.join(PROJECT_ROOT, r"scripts\dev\qualify_pre_r8_r7s7_windows.py"),
        "runner_source": ntpath.join(
            PROJECT_ROOT,
            r"src\evm\scale_validation\phase_b2_r7s3_process.py",
        ),
        "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "codex": r"C:\approved\bin\codex.exe",
        "command_processor": r"C:\Windows\System32\cmd.exe",
        "trusted_outer": ntpath.join(
            PROJECT_ROOT,
            r"scripts\dev\invoke_pre_r8_r7s7_windows_qualification.ps1",
        ),
        "work_order_gate": ntpath.join(
            PROJECT_ROOT,
            r"src\evm\scale_validation\phase_b2_r7s7_qualification_work_order.py",
        ),
        "admission_source": ntpath.join(
            PROJECT_ROOT,
            r"src\evm\scale_validation\phase_b2_r7s7_admission.py",
        ),
        "r7s3_handle_io_source": ntpath.join(
            PROJECT_ROOT,
            r"src\evm\scale_validation\phase_b2_r7s3_handle_io.py",
        ),
        "r7s4_handle_io_source": ntpath.join(
            PROJECT_ROOT,
            r"src\evm\scale_validation\phase_b2_r7s4_handle_io.py",
        ),
        "evm_package_init_source": ntpath.join(PROJECT_ROOT, r"src\evm\__init__.py"),
        "scale_validation_package_init_source": ntpath.join(
            PROJECT_ROOT, r"src\evm\scale_validation\__init__.py"
        ),
        "preparer": ntpath.join(
            PROJECT_ROOT,
            r"scripts\dev\prepare_pre_r8_r7s7_windows_qualification.py",
        ),
    }


def _source_entry(role: str, binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "final_path": binding["final_path"],
        "sha256": binding["sha256"],
        "bytes": binding["bytes"],
        "volume_serial_number": binding["volume_serial_number"],
        "file_id_hex": binding["file_id_hex"],
        "security_descriptor_sha256": binding["security_descriptor_sha256"],
        "creation_time_ns": binding["creation_time_ns"],
    }


def _work_order() -> tuple[bytes, gate.QualificationWorkOrderExpectation, dict[str, Any]]:
    bindings = {
        role: _handle(role, _paths()[role], index + 1)
        for index, role in enumerate(gate.FILE_BINDING_ROLES)
    }
    prefix = ntpath.join(gate.CANONICAL_PYCACHE_ROOT, f"{RUN_UUID}-{ATTEMPT_UUID}")
    argv = [
        bindings["interpreter"]["final_path"],
        "-I",
        "-B",
        "-S",
        "-X",
        f"pycache_prefix={prefix}",
        bindings["fixture"]["final_path"],
        "--mode",
        "root",
        "--run-uuid",
        RUN_UUID,
        "--pycache-prefix",
        prefix,
        "--interpreter-sha256",
        bindings["interpreter"]["sha256"],
        "--fixture-sha256",
        bindings["fixture"]["sha256"],
        "--command-processor",
        bindings["command_processor"]["final_path"],
        "--command-processor-sha256",
        bindings["command_processor"]["sha256"],
    ]
    invocation = {
        "schema": gate.INVOCATION_SCHEMA,
        "working_directory_identity": _directory(
            "qualification:working_directory", PROJECT_ROOT, 30_001
        ),
        "argv": argv,
        "absolute_path_argument_indexes": [0, 6, 12, 18],
        "pycache_prefix_argument_index": 5,
    }
    invocation["canonical_sha256"] = _sha_bytes(gate.canonical_json_bytes(invocation))
    closure = {
        "schema": gate.SOURCE_CLOSURE_SCHEMA,
        "roles": list(gate.SOURCE_CLOSURE_ROLES),
        "files": [_source_entry(role, bindings[role]) for role in gate.SOURCE_CLOSURE_ROLES],
        "count": len(gate.SOURCE_CLOSURE_ROLES),
        "total_bytes": sum(bindings[role]["bytes"] for role in gate.SOURCE_CLOSURE_ROLES),
    }
    closure["inventory_sha256"] = _sha_bytes(gate.canonical_json_bytes(closure))
    preserved_untracked = {
        "schema": gate.PRESERVED_UNTRACKED_SCHEMA,
        "scope": gate.PRESERVED_UNTRACKED_SCOPE,
        "files": [],
        "count": 0,
        "total_bytes": 0,
        "import_active_count": 0,
    }
    preserved_untracked["inventory_sha256"] = _sha_bytes(
        gate.canonical_json_bytes(preserved_untracked)
    )
    value = {
        "schema": gate.WORK_ORDER_SCHEMA,
        "status": "internal_non_authoritative",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "authority_scope": gate.AUTHORITY_SCOPE,
        "authority_verified": False,
        "external_authority_verified": False,
        "production_go": False,
        "go_evidence_eligible": False,
        "global_run_id": GLOBAL_RUN_ID,
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "commit": COMMIT,
        "tree": TREE,
        "qualification_mode": gate.QUALIFICATION_MODE,
        "file_bindings": bindings,
        "source_closure": closure,
        "preserved_untracked_inventory": preserved_untracked,
        "normalized_invocation": invocation,
        "pycache_prefix": prefix,
        "pycache_parent_identity": _directory(
            "qualification:pycache_parent", gate.CANONICAL_PYCACHE_ROOT, 30_002
        ),
        "pycache_prefix_initially_absent": True,
        "pycache_prefix_postcondition_absent": True,
        "output_root": gate.CANONICAL_OUTPUT_ROOT,
        "output_parent_identity": _directory(
            "qualification:output_parent", gate.CANONICAL_OUTPUT_ROOT, 30_003
        ),
        "work_order_path": ntpath.join(
            gate.CANONICAL_WORK_ORDER_ROOT,
            f"windows-qualification-work-order-{RUN_UUID}-{ATTEMPT_UUID}.json",
        ),
        "work_order_parent_identity": _directory(
            "qualification:work_order_parent",
            gate.CANONICAL_WORK_ORDER_ROOT,
            30_004,
        ),
        "same_token_hostile_admin_protected": False,
        "toolchain_runtime_closure_state": "unproven",
        "reviewer_blockers": [
            "external_oob_work_order_authority_required",
            "preparer_prelaunch_trusted_pin_unproven",
            "python_runtime_transitive_closure_unproven",
            "same_token_hostile_admin_tamper_resistance_unproven",
        ],
    }
    raw = gate.canonical_json_bytes(value)
    expectation = gate.QualificationWorkOrderExpectation(
        work_order_sha256=_sha_bytes(raw),
        global_run_id=GLOBAL_RUN_ID,
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        commit=COMMIT,
        tree=TREE,
    )
    return raw, expectation, value


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verified() -> gate.VerifiedInternalQualificationWorkOrder:
    raw, expectation, _ = _work_order()
    return gate.verify_internal_qualification_work_order(raw, expected=expectation)


def _load_qualifier() -> Any:
    name = f"r7s7_qualifier_work_order_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, QUALIFIER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == QUALIFIER_PATH.resolve()
    return module


def test_valid_work_order_binds_internal_non_authoritative_config_and_closure() -> None:
    token = _verified()
    projection = gate.qualification_config_projection(token)
    assert projection["run_uuid"] == RUN_UUID
    assert projection["attempt_uuid"] == ATTEMPT_UUID
    assert projection["pycache_prefix"].endswith(f"{RUN_UUID}-{ATTEMPT_UUID}")
    assert projection["output_root"] == gate.CANONICAL_OUTPUT_ROOT
    assert tuple(role for role, _ in token.order.file_bindings) == gate.FILE_BINDING_ROLES
    assert token.order.source_closure.roles == gate.SOURCE_CLOSURE_ROLES
    assert token.order.source_closure.count == len(gate.SOURCE_CLOSURE_ROLES)
    assert token.order.preserved_untracked_inventory.count == 0
    evidence = token.to_dict()
    assert evidence["decision"] == "NO-GO"
    assert evidence["credit"] == "zero_credit"
    assert evidence["external_authority_verified"] is False
    assert evidence["production_go"] is False
    assert evidence["go_evidence_eligible"] is False
    assert evidence["same_token_hostile_admin_protected"] is False
    assert evidence["reviewer_blockers"] == [
        "external_oob_work_order_authority_required",
        "preparer_prelaunch_trusted_pin_unproven",
        "python_runtime_transitive_closure_unproven",
        "same_token_hostile_admin_tamper_resistance_unproven",
    ]


@pytest.mark.parametrize("mutation", ["missing", "reordered", "renamed"])
def test_reviewer_blocker_contract_is_exact_and_cannot_authorize_qualification(
    mutation: str,
) -> None:
    _, _, value = _work_order()
    if mutation == "missing":
        value["reviewer_blockers"].pop(0)
    elif mutation == "reordered":
        value["reviewer_blockers"].reverse()
    else:
        value["reviewer_blockers"][1] = "preparer_prelaunch_trusted_pin_verified"
    raw = gate.canonical_json_bytes(value)
    expectation = replace(_work_order()[1], work_order_sha256=_sha_bytes(raw))
    with pytest.raises(gate.QualificationWorkOrderError, match="reviewer_blockers_not_exact"):
        gate.verify_internal_qualification_work_order(raw, expected=expectation)


def test_internal_unprotected_dacl_is_exactly_bound_but_not_promoted() -> None:
    _, _, value = _work_order()
    value["file_bindings"]["interpreter"]["dacl_protected"] = False
    value["file_bindings"]["interpreter"]["parent_directory_identity"]["dacl_protected"] = False
    value["normalized_invocation"]["working_directory_identity"]["dacl_protected"] = False
    invocation = value["normalized_invocation"]
    projection = dict(invocation)
    del projection["canonical_sha256"]
    invocation["canonical_sha256"] = _sha_bytes(gate.canonical_json_bytes(projection))
    raw = gate.canonical_json_bytes(value)
    expectation = replace(_work_order()[1], work_order_sha256=_sha_bytes(raw))
    token = gate.verify_internal_qualification_work_order(raw, expected=expectation)
    assert dict(token.order.file_bindings)["interpreter"].dacl_protected is False
    assert token.order.normalized_invocation.working_directory_identity.dacl_protected is False
    assert token.order.same_token_hostile_admin_protected is False


@pytest.mark.parametrize("mutation", ["missing", "non_boolean", "false_dacl_present"])
def test_internal_dacl_fields_are_present_typed_and_exact(mutation: str) -> None:
    _, _, value = _work_order()
    identity = value["file_bindings"]["interpreter"]
    if mutation == "missing":
        del identity["dacl_protected"]
    elif mutation == "non_boolean":
        identity["dacl_protected"] = 0
    else:
        identity["dacl_present"] = False
    raw = gate.canonical_json_bytes(value)
    expectation = replace(_work_order()[1], work_order_sha256=_sha_bytes(raw))
    with pytest.raises(gate.QualificationWorkOrderError):
        gate.verify_internal_qualification_work_order(raw, expected=expectation)


def test_verified_token_graph_is_immutable_and_fabricated_token_is_rejected() -> None:
    token = _verified()
    assert type(token.order.file_bindings) is tuple
    assert type(token.order.source_closure.files) is tuple
    with pytest.raises(FrozenInstanceError):
        token.order.source_closure.count = 0  # type: ignore[misc]
    fabricated = replace(token, _capability=None)
    with pytest.raises(gate.QualificationWorkOrderError, match="verified_internal_work_order"):
        gate.qualification_config_projection(fabricated)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("global_run_id", "72194db4-9ed6-4b8a-bb67-84272fd4e977"),
        ("run_uuid", "886bc0dc-0f14-4121-b9ea-bb83ab15780f"),
        ("attempt_uuid", "2ba09f1c-7aad-4126-bfe6-25831282332f"),
        ("commit", "3" * 40),
        ("tree", "4" * 40),
    ],
)
def test_oob_identity_expectation_mismatch_is_rejected(field: str, replacement: str) -> None:
    raw, expectation, _ = _work_order()
    changed = replace(expectation, **{field: replacement})
    with pytest.raises(gate.QualificationWorkOrderError, match="mismatch"):
        gate.verify_internal_qualification_work_order(raw, expected=changed)


def test_self_consistent_work_order_repin_cannot_defeat_independent_digest() -> None:
    raw, expectation, value = _work_order()
    value["tree"] = "5" * 40
    changed_raw = gate.canonical_json_bytes(value)
    assert _sha_bytes(changed_raw) != _sha_bytes(raw)
    with pytest.raises(gate.QualificationWorkOrderError, match="oob_digest_mismatch"):
        gate.verify_internal_qualification_work_order(changed_raw, expected=expectation)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_binding",
        "source_sha",
        "closure_missing",
        "closure_order",
        "closure_count",
        "closure_inventory_sha",
        "project_path",
    ],
)
def test_transitive_source_closure_mutations_are_rejected(mutation: str) -> None:
    _, _, value = _work_order()
    if mutation == "missing_binding":
        del value["file_bindings"]["admission_source"]
    elif mutation == "source_sha":
        value["file_bindings"]["r7s3_handle_io_source"]["sha256"] = "f" * 64
    elif mutation == "closure_missing":
        value["source_closure"]["files"].pop()
    elif mutation == "closure_order":
        value["source_closure"]["roles"][0:2] = reversed(value["source_closure"]["roles"][0:2])
    elif mutation == "closure_count":
        value["source_closure"]["count"] -= 1
    elif mutation == "closure_inventory_sha":
        value["source_closure"]["inventory_sha256"] = "e" * 64
    elif mutation == "project_path":
        binding = value["file_bindings"]["evm_package_init_source"]
        binding["final_path"] = ntpath.join(PROJECT_ROOT, r"other\__init__.py")
        binding["parent_directory_identity"]["final_path"] = ntpath.dirname(binding["final_path"])
    raw = gate.canonical_json_bytes(value)
    expectation = replace(_work_order()[1], work_order_sha256=_sha_bytes(raw))
    with pytest.raises(gate.QualificationWorkOrderError):
        gate.verify_internal_qualification_work_order(raw, expected=expectation)


@pytest.mark.parametrize(
    "mutation",
    ["path", "content", "count", "bytes", "import_active", "digest", "order"],
)
def test_preserved_untracked_inventory_mutations_are_rejected(mutation: str) -> None:
    _, _, value = _work_order()
    files = [
        {"relative_path": ".r7s5-validation/a.json", "sha256": "a" * 64, "bytes": 7},
        {
            "relative_path": "enterprise-vision-mlops/.r7s5-ci-readback/b.xml",
            "sha256": "b" * 64,
            "bytes": 9,
        },
    ]
    inventory = {
        "schema": gate.PRESERVED_UNTRACKED_SCHEMA,
        "scope": gate.PRESERVED_UNTRACKED_SCOPE,
        "files": files,
        "count": 2,
        "total_bytes": 16,
        "import_active_count": 0,
    }
    inventory["inventory_sha256"] = _sha_bytes(gate.canonical_json_bytes(inventory))
    value["preserved_untracked_inventory"] = inventory
    if mutation == "path":
        files[0]["relative_path"] = "../escape"
    elif mutation == "content":
        files[0]["sha256"] = "c" * 64
    elif mutation == "count":
        inventory["count"] = 1
    elif mutation == "bytes":
        inventory["total_bytes"] = 15
    elif mutation == "import_active":
        inventory["import_active_count"] = 1
    elif mutation == "digest":
        inventory["inventory_sha256"] = "d" * 64
    elif mutation == "order":
        files.reverse()
    raw = gate.canonical_json_bytes(value)
    expectation = replace(_work_order()[1], work_order_sha256=_sha_bytes(raw))
    with pytest.raises(gate.QualificationWorkOrderError):
        gate.verify_internal_qualification_work_order(raw, expected=expectation)


@pytest.mark.parametrize(
    "mutation",
    [
        "argv",
        "indexes",
        "boolean_index",
        "prefix_index",
        "invocation_sha",
        "prefix",
        "prefix_presence",
    ],
)
def test_invocation_and_pycache_contract_mutations_are_rejected(mutation: str) -> None:
    _, _, value = _work_order()
    if mutation == "argv":
        value["normalized_invocation"]["argv"][2:4] = ["-S", "-B"]
    elif mutation == "indexes":
        value["normalized_invocation"]["absolute_path_argument_indexes"] = [0, 6, 18]
    elif mutation == "boolean_index":
        value["normalized_invocation"]["absolute_path_argument_indexes"] = [
            False,
            6,
            12,
            18,
        ]
        invocation = value["normalized_invocation"]
        projection = dict(invocation)
        del projection["canonical_sha256"]
        invocation["canonical_sha256"] = _sha_bytes(gate.canonical_json_bytes(projection))
    elif mutation == "prefix_index":
        value["normalized_invocation"]["pycache_prefix_argument_index"] = 6
    elif mutation == "invocation_sha":
        value["normalized_invocation"]["canonical_sha256"] = "d" * 64
    elif mutation == "prefix":
        value["pycache_prefix"] = ntpath.join(gate.CANONICAL_PYCACHE_ROOT, "stale")
    elif mutation == "prefix_presence":
        value["pycache_prefix_initially_absent"] = False
    raw = gate.canonical_json_bytes(value)
    expectation = replace(_work_order()[1], work_order_sha256=_sha_bytes(raw))
    with pytest.raises(gate.QualificationWorkOrderError):
        gate.verify_internal_qualification_work_order(raw, expected=expectation)


@pytest.mark.parametrize("target", ["output", "pycache", "identity_collision"])
def test_output_and_pycache_parent_identity_are_fixed_and_distinct(target: str) -> None:
    _, _, value = _work_order()
    if target == "output":
        value["output_root"] = r"F:\historical\r3"
    elif target == "pycache":
        value["pycache_parent_identity"]["final_path"] = r"F:\attacker-cache"
    else:
        value["pycache_parent_identity"]["file_id_hex"] = value["output_parent_identity"][
            "file_id_hex"
        ]
    raw = gate.canonical_json_bytes(value)
    expectation = replace(_work_order()[1], work_order_sha256=_sha_bytes(raw))
    with pytest.raises(gate.QualificationWorkOrderError):
        gate.verify_internal_qualification_work_order(raw, expected=expectation)


def test_config_drift_is_rejected_after_token_verification() -> None:
    token = _verified()
    projection = gate.qualification_config_projection(token)
    projection["output_root"] = r"F:\historical\r4"
    with pytest.raises(gate.QualificationWorkOrderError, match="config_work_order_mismatch"):
        gate.require_verified_qualification_work_order(token, config=projection)


def test_actual_internal_entry_without_token_stops_before_lineage_or_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qualifier = _load_qualifier()
    token = _verified()
    config = qualifier.QualificationConfig(**gate.qualification_config_projection(token))
    calls: list[str] = []
    monkeypatch.setattr(qualifier, "_measure_live_lineage", lambda: calls.append("lineage"))
    with pytest.raises(
        qualifier.WindowsQualificationError, match="verified_internal_work_order_required"
    ) as caught:
        qualifier.run_internal_non_authoritative_once(config)
    assert caught.value.stage == "internal_work_order"
    assert asdict(caught.value.counts) == asdict(qualifier.QualificationCallCounts())
    assert calls == []


def test_actual_internal_entry_rejects_untyped_config_without_getter_or_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qualifier = _load_qualifier()
    token = _verified()
    calls: list[str] = []

    class HostileConfig:
        def __getattr__(self, _name: str) -> str:
            calls.append("getter")
            raise AssertionError("untyped config getter must not be evaluated")

    monkeypatch.setattr(qualifier, "_measure_live_lineage", lambda: calls.append("lineage"))
    with pytest.raises(qualifier.WindowsQualificationError, match="typed_configuration_required"):
        qualifier.run_internal_non_authoritative_once(
            HostileConfig(),  # type: ignore[arg-type]
            work_order=token,
        )
    assert calls == []


def test_valid_token_still_stops_before_prefix_lineage_or_process_while_runtime_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qualifier = _load_qualifier()
    token = _verified()
    config = qualifier.QualificationConfig(**gate.qualification_config_projection(token))
    calls: list[str] = []

    def prefix_check(_config: Any) -> None:
        calls.append("pycache")

    monkeypatch.setattr(qualifier, "_validate_runtime_pycache_prefix", prefix_check)
    monkeypatch.setattr(qualifier, "_measure_live_lineage", lambda: calls.append("lineage"))
    with pytest.raises(
        qualifier.WindowsQualificationError, match="toolchain_runtime_closure_unproven"
    ) as caught:
        qualifier.run_internal_non_authoritative_once(config, work_order=token)
    assert caught.value.counts.process_creation == 0
    assert caught.value.counts.runner_invocation == 0
    assert calls == []


def test_contract_never_claims_external_or_production_authority() -> None:
    contract = gate.work_order_contract()
    assert contract["external_authority_replaced"] is False
    assert contract["production_go_enabled"] is False
    assert contract["go_evidence_eligible"] is False
    assert contract["process_calls_implemented"] is False
    assert contract["success_or_completion_marker_allowed"] is False
    assert contract["external_canonical_parent_provisioning_required"] is True
    assert contract["candidate_may_self_provision_canonical_parents"] is False
    assert contract["exact_file_binding_roles"] == list(gate.FILE_BINDING_ROLES)
    assert contract["exact_source_closure_roles"] == list(gate.SOURCE_CLOSURE_ROLES)

from __future__ import annotations

import copy
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evm.scale_validation import phase_b2_r7s5_ci as ci
from scripts.dev import validate_pre_r8_r7s5_ci as cli


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ci/pre-r8-r7s5-test-lanes.json"
ACTIVE_WORKFLOW = ROOT.parent / ".github/workflows/enterprise-vision-mlops-ci.yml"
NOW = datetime(2026, 9, 2, 5, 0, tzinfo=UTC)
CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
SETUP_NODE = "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020"
SETUP_KUBECTL = "azure/setup-kubectl@776406bce94f63e41d621b960d78ee25c8b76ede"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
COLLECTION_RECEIPT_IDS = {
    "portable": "88888888-8888-4888-8888-888888888888",
    "windows": "99999999-9999-4999-8999-999999999999",
    "private": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
}


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _lane_step(lane: str, marker_expression: str) -> str:
    files = ci.EXPECTED_LANE_FILES[lane]
    file_lines = "\n".join(f"            {relative} \\" for relative in files)
    return (
        "      - name: Validate and run lane\n"
        "        run: |\n"
        "          python scripts/dev/validate_pre_r8_r7s5_ci.py manifest "
        "--manifest ci/pre-r8-r7s5-test-lanes.json --project-root . "
        f"--lane {lane}\n"
        "          python -m pytest -q -ra --strict-config --strict-markers "
        f'-m "{marker_expression}" \\\n'
        f"{file_lines}\n"
        "            --junitxml lane.xml\n"
    )


def _workflow() -> bytes:
    return (
        "name: r7s5 CI contract fixture\n"
        "on: push\n"
        "jobs:\n"
        "  portable-linux:\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        f"      - uses: {CHECKOUT}\n"
        f"      - uses: {SETUP_PYTHON}\n"
        f"      - uses: {SETUP_NODE}\n"
        f"      - uses: {SETUP_KUBECTL}\n"
        + _lane_step("portable", "not ci_windows_platform and not ci_private_artifact")
        + f"      - uses: {UPLOAD}\n"
        "  windows-platform-required:\n"
        "    runs-on: [self-hosted, Windows, X64, s8-v4-r7s5-private]\n"
        "    steps:\n"
        f"      - uses: {CHECKOUT}\n"
        + _lane_step("windows", "ci_windows_platform and not ci_private_artifact")
        + f"      - uses: {UPLOAD}\n"
        "  private-artifact-required:\n"
        "    runs-on: [self-hosted, Windows, X64, s8-v4-r7s5-private]\n"
        "    steps:\n"
        f"      - uses: {CHECKOUT}\n"
        + _lane_step("private", "ci_private_artifact")
        + f"      - uses: {UPLOAD}\n"
        "  required-lane-closure:\n"
        "    needs: [portable-linux, windows-platform-required, private-artifact-required]\n"
        "    if: always()\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        f"      - uses: {CHECKOUT}\n"
        "      - name: Validate closure\n"
        "        run: |\n"
        "          python scripts/dev/validate_pre_r8_r7s5_ci.py manifest "
        "--manifest ci/pre-r8-r7s5-test-lanes.json --project-root .\n"
        f"      - uses: {UPLOAD}\n"
    ).encode("utf-8")


def _binding(domain: str) -> ci.ReceiptBinding:
    return ci.ReceiptBinding(
        repository="ruma0236/ML_ServeAPI",
        workflow="enterprise-vision-mlops-ci.yml",
        commit=ci.EXPECTED_BASELINE_COMMIT,
        tree=ci.EXPECTED_BASELINE_TREE,
        run_id="33590000000",
        run_attempt=1,
        job=ci.EXPECTED_LANE_JOBS[domain],
        domain=domain,
    )


def _runner_receipt(domain: str) -> dict[str, Any]:
    binding = _binding(domain)
    identifier = {
        "windows": "11111111-1111-4111-8111-111111111111",
        "private": "22222222-2222-4222-8222-222222222222",
    }[domain]
    nonce = {"windows": "a" * 64, "private": "b" * 64}[domain]
    paths = {
        "docker": r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
        "git": r"C:\Program Files\Git\cmd\git.exe",
        "nvidia_smi": r"C:\Windows\System32\nvidia-smi.exe",
        "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "python": r"C:\Python311\python.exe",
        "wsl": r"C:\Windows\System32\wsl.exe",
    }
    return {
        "schema": ci.RUNNER_RECEIPT_SCHEMA,
        "receipt_id": identifier,
        "nonce": nonce,
        "domain": binding.domain,
        "repository": binding.repository,
        "workflow": binding.workflow,
        "commit": binding.commit,
        "tree": binding.tree,
        "run_id": binding.run_id,
        "run_attempt": binding.run_attempt,
        "job": binding.job,
        "issued_at": "2026-09-02T04:58:00Z",
        "expires_at": "2026-09-02T05:08:00Z",
        "authority": {"issuer": "external-r7s5-authority", "key_fingerprint": "f" * 64},
        "runner": {
            "name": "r7s5-ephemeral-01",
            "group": "s8-v4-r7s5-private",
            "labels": list(ci.EXPECTED_WINDOWS_LABELS),
            "version": "2.328.0-pinned",
            "os_build": "Windows-pinned-build",
            "machine_sid_sha256": "c" * 64,
            "machine_identity_sha256": "d" * 64,
        },
        "token": {"administrator": True, "integrity": "High", "elevation_type": "Full"},
        "toolchain": {
            role: {"path": paths[role], "sha256": "e" * 64, "version": "pinned"}
            for role in ci.EXPECTED_TOOL_ROLES
        },
        "signature": "external-signature",
    }


def _private_receipt() -> dict[str, Any]:
    binding = _binding("private")
    return {
        "schema": ci.PRIVATE_RECEIPT_SCHEMA,
        "receipt_id": "33333333-3333-4333-8333-333333333333",
        "nonce": "3" * 64,
        "domain": binding.domain,
        "repository": binding.repository,
        "workflow": binding.workflow,
        "commit": binding.commit,
        "tree": binding.tree,
        "run_id": binding.run_id,
        "run_attempt": binding.run_attempt,
        "job": binding.job,
        "issued_at": "2026-09-02T04:58:00Z",
        "expires_at": "2026-09-02T05:08:00Z",
        "authority": {"issuer": "external-r7s5-authority", "key_fingerprint": "f" * 64},
        "artifact": {
            "root": r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops",
            "manifest_sha256": "1" * 64,
            "path_set_sha256": "2" * 64,
            "aggregate_sha256": "3" * 64,
            "artifact_count": 4244,
            "total_bytes": 758070438,
            "filesystem": "NTFS",
            "volume_serial": 123456,
            "directory_file_id": "0000000000000001:0000000000000002",
            "mount_read_only": True,
            "reparse_component_count": 0,
            "acl_write_denied": True,
        },
        "signature": "external-signature",
    }


def _accept_signature(_payload: bytes, _signature: str, _fingerprint: str) -> bool:
    return True


def _node_sha(nodeids: list[str]) -> str:
    return hashlib.sha256(("\n".join(nodeids) + "\n").encode()).hexdigest()


def _result_sha(result: dict[str, Any]) -> str:
    payload = dict(result)
    payload.pop("result_sha256", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _receipt_sha(receipt: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _verified_receipts() -> tuple[dict[str, ci.VerifiedReceipt], ci.VerifiedReceipt]:
    guard = ci.ReceiptReplayGuard()
    windows = ci._validate_runner_receipt_for_test(
        _runner_receipt("windows"),
        expected=_binding("windows"),
        now=NOW,
        replay_guard=guard,
        verifier=_accept_signature,
    )
    private = ci._validate_runner_receipt_for_test(
        _runner_receipt("private"),
        expected=_binding("private"),
        now=NOW,
        replay_guard=guard,
        verifier=_accept_signature,
    )
    artifact = ci._validate_private_artifact_receipt_for_test(
        _private_receipt(),
        expected=_binding("private"),
        now=NOW,
        replay_guard=guard,
        verifier=_accept_signature,
    )
    return {"windows": windows, "private": private}, artifact


def _lane_results() -> dict[str, dict[str, Any]]:
    sizes = {"portable": 1000, "windows": 600, "private": 570}
    receipts, artifact = _verified_receipts()
    results: dict[str, dict[str, Any]] = {}
    for lane in ci.LANES:
        files = ci.EXPECTED_LANE_FILES[lane]
        nodes = sorted(
            f"{files[index % len(files)]}::test_{lane}[{index:04d}]" for index in range(sizes[lane])
        )
        count = len(nodes)
        result = {
            "lane": lane,
            "job": ci.EXPECTED_LANE_JOBS[lane],
            "job_result": "success",
            "status": "passed",
            "commit": ci.EXPECTED_BASELINE_COMMIT,
            "tree": ci.EXPECTED_BASELINE_TREE,
            "run_id": "33590000000",
            "run_attempt": 1,
            "nodeids": nodes,
            "nodeids_sha256": _node_sha(nodes),
            "collected": count,
            "collection_inventory_receipt_id": COLLECTION_RECEIPT_IDS[lane],
            "selected": count,
            "executed": count,
            "passed": count,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "deselected": 0,
            "xfailed": 0,
            "xpassed": 0,
            "runner_receipt_id": receipts[lane].receipt_id if lane != "portable" else None,
            "artifact_receipt_id": artifact.receipt_id if lane == "private" else None,
        }
        result["result_sha256"] = _result_sha(result)
        results[lane] = result
    return results


def _lane_result_receipts() -> dict[str, dict[str, Any]]:
    identifiers = {
        "portable": "44444444-4444-4444-8444-444444444444",
        "windows": "55555555-5555-4555-8555-555555555555",
        "private": "66666666-6666-4666-8666-666666666666",
    }
    nonces = {"portable": "4" * 64, "windows": "5" * 64, "private": "6" * 64}
    results = _lane_results()
    receipts: dict[str, dict[str, Any]] = {}
    for lane in ci.LANES:
        binding = _binding(lane)
        receipts[lane] = {
            "schema": ci.LANE_RESULT_RECEIPT_SCHEMA,
            "receipt_id": identifiers[lane],
            "nonce": nonces[lane],
            "domain": lane,
            "repository": binding.repository,
            "workflow": binding.workflow,
            "commit": binding.commit,
            "tree": binding.tree,
            "run_id": binding.run_id,
            "run_attempt": binding.run_attempt,
            "job": binding.job,
            "issued_at": "2026-09-02T04:58:00Z",
            "expires_at": "2026-09-02T05:08:00Z",
            "authority": {
                "issuer": "external-r7s5-authority",
                "key_fingerprint": "f" * 64,
            },
            "result": results[lane],
            "signature": "external-signature",
        }
    return receipts


def _collection_inventory_receipts() -> dict[str, dict[str, Any]]:
    results = _lane_results()
    receipts: dict[str, dict[str, Any]] = {}
    nonces = {"portable": "8" * 64, "windows": "9" * 64, "private": "a" * 64}
    for lane in ci.LANES:
        binding = _binding(lane)
        nodes = results[lane]["nodeids"]
        scope_files = list(ci.EXPECTED_LANE_FILES[lane])
        receipts[lane] = {
            "schema": ci.COLLECTION_INVENTORY_RECEIPT_SCHEMA,
            "receipt_id": COLLECTION_RECEIPT_IDS[lane],
            "nonce": nonces[lane],
            "domain": lane,
            "repository": binding.repository,
            "workflow": binding.workflow,
            "commit": binding.commit,
            "tree": binding.tree,
            "run_id": binding.run_id,
            "run_attempt": binding.run_attempt,
            "job": binding.job,
            "issued_at": "2026-09-02T04:56:00Z",
            "expires_at": "2026-09-02T05:06:00Z",
            "authority": {
                "issuer": "external-r7s5-authority",
                "key_fingerprint": "f" * 64,
            },
            "inventory": {
                "lane": lane,
                "node_count": len(nodes),
                "nodeids": nodes,
                "nodeids_sha256": _node_sha(nodes),
                "scope_files": scope_files,
                "scope_files_sha256": _node_sha(scope_files),
            },
            "signature": "external-signature",
        }
    return receipts


def _closure(*, engine: bool = False, **overrides: Any) -> dict[str, Any]:
    collection_inventory_receipts = overrides.pop(
        "collection_inventory_receipts", _collection_inventory_receipts()
    )
    lane_result_receipts = overrides.pop("lane_result_receipts", _lane_result_receipts())
    frozen_collection_contract = overrides.pop(
        "frozen_collection_contract",
        {
            lane: {
                "node_count": receipt["inventory"]["node_count"],
                "nodeids_sha256": receipt["inventory"]["nodeids_sha256"],
                "receipt_sha256": _receipt_sha(receipt),
            }
            for lane, receipt in collection_inventory_receipts.items()
        },
    )
    arguments: dict[str, Any] = {
        "repository": "ruma0236/ML_ServeAPI",
        "workflow": "enterprise-vision-mlops-ci.yml",
        "commit": ci.EXPECTED_BASELINE_COMMIT,
        "tree": ci.EXPECTED_BASELINE_TREE,
        "run_id": "33590000000",
        "run_attempt": 1,
        "runner_receipts": {
            "windows": _runner_receipt("windows"),
            "private": _runner_receipt("private"),
        },
        "private_artifact_receipt": _private_receipt(),
        "now": NOW,
        "replay_guard": ci.ReceiptReplayGuard(),
        "verifier": _accept_signature,
    }
    arguments.update(overrides)
    validator = ci._validate_required_closure if engine else ci._validate_required_closure_for_test
    return validator(
        _manifest(),
        collection_inventory_receipts,
        lane_result_receipts,
        frozen_collection_contract=frozen_collection_contract,
        **arguments,
    )


def _key_fingerprint(key: Ed25519PrivateKey) -> str:
    public_key = key.public_key()
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _sign_receipt(receipt: dict[str, Any], key: Ed25519PrivateKey) -> None:
    receipt["authority"]["key_fingerprint"] = _key_fingerprint(key)
    payload = copy.deepcopy(receipt)
    payload.pop("signature")
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    receipt["signature"] = base64.b64encode(key.sign(serialized)).decode("ascii")


def _signed_receipt_bundle() -> tuple[
    Ed25519PrivateKey,
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    key = Ed25519PrivateKey.generate()
    collection_receipts = _collection_inventory_receipts()
    lane_receipts = _lane_result_receipts()
    runner_receipts = {
        "windows": _runner_receipt("windows"),
        "private": _runner_receipt("private"),
    }
    artifact_receipt = _private_receipt()
    for receipt in [
        *collection_receipts.values(),
        *lane_receipts.values(),
        *runner_receipts.values(),
        artifact_receipt,
    ]:
        _sign_receipt(receipt, key)
    return key, collection_receipts, lane_receipts, runner_receipts, artifact_receipt


def _write_canonical_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def test_frozen_manifest_is_valid_but_explicitly_zero_credit_no_go() -> None:
    result = ci.load_and_validate_manifest(MANIFEST, project_root=ROOT)
    assert result["status"] == "manual_intervention_required"
    assert result["credit"] == "zero_credit"
    assert result["go_evidence_eligible"] is False
    assert result["known_failed_nodes"] == 172
    assert result["unclassified_failed_nodes"] == 0
    assert result["remaining_blockers"] == list(ci.EXPECTED_BLOCKERS)


def test_downloaded_junit_inventory_is_exactly_bound_but_does_not_promote_go() -> None:
    observed = _manifest()["hosted_failure_observation"]
    assert observed["artifact_archive_sha256"] == (
        "0dbec4bea33d8890af17602423c024e62ea36689966b9df4ada8de4e257b87ee"
    )
    assert observed["junit_xml_sha256"] == (
        "16aa6fa2bf4b9ddb8e6f2cbeef2739c8c0fd5ebbb7145a607fe832d04fabd209"
    )
    assert observed["nodeid_inventory_readback_sha256"] == (
        "328a7a888c145baef695a66a7ed4995e9d950b277bc36c6fe8e7d4fef6dbf32e"
    )
    assert observed["nodeid_inventory_counts"] == ci.EXPECTED_NODEID_COUNTS
    assert observed["nodeid_sorted_hashes"] == ci.EXPECTED_NODEID_HASHES
    assert observed["nodeid_inventory_bytes"] == ci.EXPECTED_NODEID_BYTES
    assert observed["known_failed_file_counts"] == ci.EXPECTED_FAILED_FILE_COUNTS
    assert observed["lane_failed_node_counts"] == ci.EXPECTED_LANE_FAILED_NODE_COUNTS
    assert observed["full_nodeid_inventory_available"] is True
    assert observed["other_files_failed_node_count"] == 0


def test_manifest_lane_overlap_is_rejected() -> None:
    payload = _manifest()
    duplicate = payload["file_inventory"]["lanes"]["windows"][0]
    payload["file_inventory"]["lanes"]["private"] = sorted(
        [*payload["file_inventory"]["lanes"]["private"], duplicate]
    )
    with pytest.raises(ci.R7S5CIContractError, match="lane_inventory_overlap"):
        ci.validate_manifest(payload)


def test_manifest_lane_gap_is_rejected() -> None:
    payload = _manifest()
    payload["file_inventory"]["lanes"]["windows"].pop(0)
    with pytest.raises(ci.R7S5CIContractError, match="lane_inventory_gap"):
        ci.validate_manifest(payload)


def test_manifest_self_consistent_lane_mutation_is_rejected() -> None:
    payload = _manifest()
    old = payload["file_inventory"]["lanes"]["private"][0]
    new = "tests/test_api_container_contract.py"
    payload["file_inventory"]["lanes"]["private"][0] = new
    payload["file_inventory"]["lanes"]["private"].sort()
    payload["file_inventory"]["scope_files"].remove(old)
    payload["file_inventory"]["scope_files"].append(new)
    payload["file_inventory"]["scope_files"].sort()
    with pytest.raises(ci.R7S5CIContractError, match="lane_inventory_mutation"):
        ci.validate_manifest(payload)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda value: value["hosted_failure_observation"].update(
                full_nodeid_inventory_available=False
            ),
            "hosted_full_nodeid_inventory_not_available",
        ),
        (
            lambda value: value["hosted_failure_observation"].update(
                other_files_failed_node_count=1
            ),
            "hosted_failure_observation_mismatch",
        ),
        (
            lambda value: value["hosted_failure_observation"]["nodeid_sorted_hashes"].update(
                collected="0" * 64
            ),
            "nodeid_hash_mismatch:collected",
        ),
        (
            lambda value: value["hosted_failure_observation"].update(
                nodeid_inventory_readback_sha256="0" * 64
            ),
            "hosted_failure_observation_mismatch",
        ),
        (
            lambda value: value["hosted_failure_observation"]["known_failed_file_counts"].update(
                {"tests/test_scenario_workload_production.py": 2}
            ),
            "known_failed_file_counts_mismatch",
        ),
        (
            lambda value: value["hosted_failure_observation"].update(errors=False),
            "hosted_failure:errors_integer_out_of_range",
        ),
        (
            lambda value: value["current_external_state"].update(
                external_attested_windows_runner_available=True
            ),
            "external_state_cannot_be_promoted",
        ),
        (
            lambda value: value["decision"].update(status="passed", go_evidence_eligible=True),
            "decision_must_remain_no_go",
        ),
    ],
)
def test_manifest_no_go_facts_cannot_be_reclassified(mutation: Any, error: str) -> None:
    payload = _manifest()
    mutation(payload)
    with pytest.raises(ci.R7S5CIContractError, match=error):
        ci.validate_manifest(payload)


def test_manifest_only_workflow_closure_is_rejected() -> None:
    with pytest.raises(ci.R7S5CIContractError, match="workflow_required_closure_not_authenticated"):
        ci.validate_workflow_contract(_workflow(), _manifest())


def test_named_step_child_uses_are_all_accounted_for() -> None:
    raw = _workflow().replace(
        f"      - uses: {CHECKOUT}\n".encode(),
        f"      - name: Checkout pinned source\n        uses: {CHECKOUT}\n".encode(),
    )
    with pytest.raises(ci.R7S5CIContractError, match="workflow_required_closure_not_authenticated"):
        ci.validate_workflow_contract(raw, _manifest())


def test_unscoped_uses_is_rejected_instead_of_escaping_inventory() -> None:
    raw = _workflow().replace(b"jobs:\n", f"uses: {CHECKOUT}\njobs:\n".encode(), 1)
    with pytest.raises(ci.R7S5CIContractError, match="workflow_action_ref_unscoped"):
        ci.validate_workflow_contract(raw, _manifest())


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        (
            f"      - uses: {CHECKOUT}\n",
            f"      - uses: {CHECKOUT}\n        uses: {CHECKOUT}\n",
            "workflow_step_uses_not_unique",
        ),
        (
            f"      - uses: {CHECKOUT}\n",
            f"      - uses: {CHECKOUT}\n        run: echo forbidden\n",
            "workflow_step_uses_and_run_conflict",
        ),
        (
            "        run: |\n",
            "        run: echo first\n        run: echo duplicate\n",
            "workflow_step_run_not_unique",
        ),
    ],
)
def test_workflow_step_cannot_mix_or_duplicate_uses_and_run(old: str, new: str, error: str) -> None:
    raw = _workflow().replace(old.encode(), new.encode(), 1)
    with pytest.raises(ci.R7S5CIContractError, match=error):
        ci.validate_workflow_contract(raw, _manifest())


@pytest.mark.parametrize(
    ("replacement", "error"),
    [
        (f"      - {{ uses: {CHECKOUT} }}\n", "workflow_action_ref_inline_or_ambiguous"),
        ("      - { run: echo forbidden }\n", "workflow_yaml_inline_step_forbidden"),
        (
            f"      - &checkout-step\n        uses: {CHECKOUT}\n",
            "workflow_yaml_anchor_or_alias_forbidden",
        ),
        ("      - *checkout-step\n", "workflow_yaml_anchor_or_alias_forbidden"),
    ],
)
def test_workflow_inline_anchor_and_alias_steps_fail_closed(replacement: str, error: str) -> None:
    raw = _workflow().replace(f"      - uses: {CHECKOUT}\n".encode(), replacement.encode(), 1)
    with pytest.raises(ci.R7S5CIContractError, match=error):
        ci.validate_workflow_contract(raw, _manifest())


def test_active_workflow_remains_truthful_inventory_mismatch() -> None:
    with pytest.raises(ci.R7S5CIContractError, match="workflow_action_ref_inventory_mismatch"):
        ci.validate_workflow_contract(ACTIVE_WORKFLOW.read_bytes(), _manifest())


def test_duplicate_job_id_cannot_shadow_an_action_inventory() -> None:
    raw = _workflow().replace(
        b"jobs:\n",
        b"jobs:\n  portable-linux:\n    steps:\n",
        1,
    )
    with pytest.raises(ci.R7S5CIContractError, match="workflow_job_id_not_unique:portable-linux"):
        ci.validate_workflow_contract(raw, _manifest())


def test_duplicate_steps_mapping_cannot_split_an_action_inventory() -> None:
    raw = _workflow().replace(
        b"    steps:\n",
        b"    steps:\n    steps:\n",
        1,
    )
    with pytest.raises(
        ci.R7S5CIContractError, match="workflow_job_steps_not_unique:portable-linux"
    ):
        ci.validate_workflow_contract(raw, _manifest())


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        (CHECKOUT, "actions/checkout@v4", "workflow_action_ref_not_full_sha"),
        ("    runs-on: ubuntu-24.04", "    runs-on: ubuntu-latest", "mutable_runner"),
        (
            "      - name: Validate and run lane",
            "      - name: Validate and run lane\n        continue-on-error: true",
            "continue_on_error",
        ),
        (
            "--strict-markers",
            "--strict-markers --ignore tests/test_required.py",
            "pytest_ignore",
        ),
        ("--strict-config", "--strict-config -k safe_subset", "pytest_k_selection"),
        (
            "python -m pytest",
            "python -c \"import pytest; pytest.skip('bypass')\"\n          python -m pytest",
            "test_skip_or_xfail",
        ),
    ],
)
def test_workflow_bypass_mutations_are_rejected(old: str, new: str, error: str) -> None:
    raw = _workflow()
    assert raw.count(old.encode()) >= 1
    mutated = raw.replace(old.encode(), new.encode(), 1)
    with pytest.raises(ci.R7S5CIContractError, match=error):
        ci.validate_workflow_contract(mutated, _manifest())


def test_workflow_closure_must_need_every_lane_and_run_always() -> None:
    raw = _workflow()
    missing = raw.replace(
        b"[portable-linux, windows-platform-required, private-artifact-required]",
        b"[portable-linux, windows-platform-required]",
        1,
    )
    with pytest.raises(ci.R7S5CIContractError, match="closure_needs"):
        ci.validate_workflow_contract(missing, _manifest())
    conditional = raw.replace(b"    if: always()", b"    if: false", 1)
    with pytest.raises(ci.R7S5CIContractError, match="false_condition"):
        ci.validate_workflow_contract(conditional, _manifest())


def test_runner_receipt_requires_independent_verifier() -> None:
    with pytest.raises(ci.R7S5CIContractError, match="independent_signature_verifier_required"):
        ci._validate_runner_receipt_for_test(
            _runner_receipt("windows"),
            expected=_binding("windows"),
            now=NOW,
            replay_guard=ci.ReceiptReplayGuard(),
            verifier=None,
        )


@pytest.mark.parametrize(
    "validator",
    (
        ci.validate_runner_receipt,
        ci.validate_private_artifact_receipt,
        ci.validate_collection_inventory_receipt,
        ci.validate_lane_result_receipt,
    ),
)
def test_public_receipt_validators_fail_closed_without_external_adapter(validator: object) -> None:
    with pytest.raises(
        ci.R7S5CIContractError,
        match="external_receipt_authority_adapter_unprovisioned",
    ):
        validator()


def test_runner_receipt_is_one_shot_and_replay_is_rejected() -> None:
    receipt = _runner_receipt("windows")
    guard = ci.ReceiptReplayGuard()
    first = ci._validate_runner_receipt_for_test(
        receipt,
        expected=_binding("windows"),
        now=NOW,
        replay_guard=guard,
        verifier=_accept_signature,
    )
    assert first.domain == "windows"
    with pytest.raises(ci.R7S5CIContractError, match="receipt_replay_detected"):
        ci._validate_runner_receipt_for_test(
            receipt,
            expected=_binding("windows"),
            now=NOW,
            replay_guard=guard,
            verifier=_accept_signature,
        )


@pytest.mark.parametrize("field", ["runner", "toolchain", "token", "signature"])
def test_runner_receipt_missing_required_field_is_rejected(field: str) -> None:
    receipt = _runner_receipt("windows")
    del receipt[field]
    with pytest.raises(ci.R7S5CIContractError, match="runner_receipt_keys_not_exact"):
        ci._validate_runner_receipt_for_test(
            receipt,
            expected=_binding("windows"),
            now=NOW,
            replay_guard=ci.ReceiptReplayGuard(),
            verifier=_accept_signature,
        )


@pytest.mark.parametrize("field", ["commit", "tree"])
def test_runner_receipt_commit_tree_binding_mutation_is_rejected(field: str) -> None:
    receipt = _runner_receipt("windows")
    receipt[field] = "0" * 40
    with pytest.raises(ci.R7S5CIContractError, match=f"receipt_binding_mismatch:{field}"):
        ci._validate_runner_receipt_for_test(
            receipt,
            expected=_binding("windows"),
            now=NOW,
            replay_guard=ci.ReceiptReplayGuard(),
            verifier=_accept_signature,
        )


def test_runner_receipt_domain_swap_and_stale_receipt_are_rejected() -> None:
    with pytest.raises(ci.R7S5CIContractError, match="receipt_binding_mismatch:domain"):
        ci._validate_runner_receipt_for_test(
            _runner_receipt("windows"),
            expected=_binding("private"),
            now=NOW,
            replay_guard=ci.ReceiptReplayGuard(),
            verifier=_accept_signature,
        )
    with pytest.raises(ci.R7S5CIContractError, match="receipt_stale_or_not_yet_valid"):
        ci._validate_runner_receipt_for_test(
            _runner_receipt("windows"),
            expected=_binding("windows"),
            now=NOW + timedelta(minutes=20),
            replay_guard=ci.ReceiptReplayGuard(),
            verifier=_accept_signature,
        )


def test_private_receipt_rejects_domain_swap_replay_and_writable_mount() -> None:
    receipt = _private_receipt()
    with pytest.raises(ci.R7S5CIContractError, match="private_receipt_domain_required"):
        ci._validate_private_artifact_receipt_for_test(
            receipt,
            expected=_binding("windows"),
            now=NOW,
            replay_guard=ci.ReceiptReplayGuard(),
            verifier=_accept_signature,
        )
    writable = copy.deepcopy(receipt)
    writable["artifact"]["mount_read_only"] = False
    with pytest.raises(ci.R7S5CIContractError, match="private_artifact_not_read_only"):
        ci._validate_private_artifact_receipt_for_test(
            writable,
            expected=_binding("private"),
            now=NOW,
            replay_guard=ci.ReceiptReplayGuard(),
            verifier=_accept_signature,
        )
    guard = ci.ReceiptReplayGuard()
    ci._validate_private_artifact_receipt_for_test(
        receipt,
        expected=_binding("private"),
        now=NOW,
        replay_guard=guard,
        verifier=_accept_signature,
    )
    with pytest.raises(ci.R7S5CIContractError, match="receipt_replay_detected"):
        ci._validate_private_artifact_receipt_for_test(
            receipt,
            expected=_binding("private"),
            now=NOW,
            replay_guard=guard,
            verifier=_accept_signature,
        )


def test_required_lane_closure_can_close_tests_but_never_promotes_go() -> None:
    result = _closure()
    assert result["schema"].endswith("required-closure-test-only.v1")
    assert result["status"] == "test_only"
    assert result["test_contract_logic_exercised"] is True
    assert result["production_closure_eligible"] is False
    assert "required_lane_test_closure_passed" not in result
    assert result["node_count"] == 2170
    assert result["lane_result_sha256"] == {
        lane: _lane_result_receipts()[lane]["result"]["result_sha256"] for lane in ci.LANES
    }
    assert result["credit"] == "zero_credit"
    assert result["go_evidence_eligible"] is False


def test_internal_closure_engine_never_emits_production_shaped_evidence() -> None:
    result = _closure(engine=True)
    assert result["schema"].endswith("closure-engine-neutral.v1")
    assert result["status"] == "internal_non_authoritative"
    assert result["contract_checks_satisfied"] is True
    assert result["production_closure_eligible"] is False
    assert "required_lane_test_closure_passed" not in result


def test_public_required_closure_fails_without_external_authorities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = {
        "repository": "ruma0236/ML_ServeAPI",
        "workflow": "enterprise-vision-mlops-ci.yml",
        "commit": ci.EXPECTED_BASELINE_COMMIT,
        "tree": ci.EXPECTED_BASELINE_TREE,
        "run_id": "33590000000",
        "run_attempt": 1,
        "runner_receipts": {
            "windows": _runner_receipt("windows"),
            "private": _runner_receipt("private"),
        },
        "private_artifact_receipt": _private_receipt(),
        "now": NOW,
        "replay_guard": ci.ReceiptReplayGuard(),
        "verifier": _accept_signature,
    }
    with pytest.raises(ci.R7S5CIContractError, match="external_collection_authority_unprovisioned"):
        ci.validate_required_closure(
            _manifest(),
            _collection_inventory_receipts(),
            _lane_result_receipts(),
            **arguments,
        )
    monkeypatch.setattr(ci, "PINNED_EXTERNAL_COLLECTION_CONTRACT", {})
    with pytest.raises(
        ci.R7S5CIContractError,
        match="external_worm_replay_authority_adapter_not_implemented",
    ):
        ci.validate_required_closure(
            _manifest(),
            _collection_inventory_receipts(),
            _lane_result_receipts(),
            **arguments,
        )


def test_required_closure_rejects_nonbaseline_commit_tree() -> None:
    with pytest.raises(ci.R7S5CIContractError, match="frozen_baseline_identity_mismatch"):
        _closure(commit="0" * 40)


@pytest.mark.parametrize(
    ("field", "value"),
    [("job_result", "failure"), ("status", "failed"), ("skipped", 1), ("deselected", 1)],
)
def test_required_closure_rejects_failure_skip_or_deselection(field: str, value: Any) -> None:
    lane_receipts = _lane_result_receipts()
    lane_receipts["windows"]["result"][field] = value
    lane_receipts["windows"]["result"]["result_sha256"] = _result_sha(
        lane_receipts["windows"]["result"]
    )
    with pytest.raises(ci.R7S5CIContractError, match="closure_lane_not_success|closure_nonpass"):
        _closure(
            lane_result_receipts=lane_receipts,
        )


def test_required_closure_rejects_node_gap_and_receipt_domain_swap() -> None:
    lane_receipts = _lane_result_receipts()
    lane_results = {lane: lane_receipts[lane]["result"] for lane in ci.LANES}
    omitted_file = ci.EXPECTED_LANE_FILES["portable"][-1]
    lane_results["portable"]["nodeids"] = [
        node
        for node in lane_results["portable"]["nodeids"]
        if not node.startswith(omitted_file + "::")
    ]
    count = len(lane_results["portable"]["nodeids"])
    lane_results["portable"]["nodeids_sha256"] = _node_sha(lane_results["portable"]["nodeids"])
    for field in ("collected", "selected", "executed", "passed"):
        lane_results["portable"][field] = count
    lane_results["portable"]["result_sha256"] = _result_sha(lane_results["portable"])
    with pytest.raises(ci.R7S5CIContractError, match="closure_lane_file_inventory_gap"):
        _closure(
            lane_result_receipts=lane_receipts,
        )

    runner_receipts = {
        "windows": _runner_receipt("private"),
        "private": _runner_receipt("private"),
    }
    with pytest.raises(ci.R7S5CIContractError, match="receipt_binding_mismatch:domain"):
        _closure(
            runner_receipts=runner_receipts,
        )


def test_required_closure_rejects_signed_twenty_three_node_reduction() -> None:
    collection_receipts = _collection_inventory_receipts()
    frozen_contract = {
        lane: {
            "node_count": receipt["inventory"]["node_count"],
            "nodeids_sha256": receipt["inventory"]["nodeids_sha256"],
            "receipt_sha256": _receipt_sha(receipt),
        }
        for lane, receipt in collection_receipts.items()
    }
    lane_receipts = _lane_result_receipts()
    for lane in ci.LANES:
        result = lane_receipts[lane]["result"]
        nodes = sorted(
            f"{relative}::test_invented_only" for relative in ci.EXPECTED_LANE_FILES[lane]
        )
        result["nodeids"] = nodes
        result["nodeids_sha256"] = _node_sha(nodes)
        for field in ("collected", "selected", "executed", "passed"):
            result[field] = len(nodes)
        result["result_sha256"] = _result_sha(result)
        inventory = collection_receipts[lane]["inventory"]
        inventory["nodeids"] = nodes
        inventory["nodeids_sha256"] = _node_sha(nodes)
        inventory["node_count"] = len(nodes)
    assert sum(len(value["result"]["nodeids"]) for value in lane_receipts.values()) == 23
    with pytest.raises(ci.R7S5CIContractError, match="closure_frozen_collection_mismatch"):
        _closure(
            collection_inventory_receipts=collection_receipts,
            lane_result_receipts=lane_receipts,
            frozen_collection_contract=frozen_contract,
        )


def test_required_closure_rejects_fabricated_public_verified_receipt() -> None:
    fabricated = ci.VerifiedReceipt(
        receipt_id="77777777-7777-4777-8777-777777777777",
        nonce="7" * 64,
        issuer="external-r7s5-authority",
        domain="windows",
        commit=ci.EXPECTED_BASELINE_COMMIT,
        tree=ci.EXPECTED_BASELINE_TREE,
        run_id="33590000000",
        run_attempt=1,
        job=ci.EXPECTED_LANE_JOBS["windows"],
        kind="runner",
    )
    with pytest.raises(ci.R7S5CIContractError, match="runner_receipt:windows_mapping_required"):
        _closure(
            runner_receipts={
                "windows": fabricated,
                "private": _runner_receipt("private"),
            }
        )


def test_signed_lane_result_rejects_caller_nodeids_even_with_recomputed_digests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key, collection_receipts, lane_receipts, runner_receipts, artifact = _signed_receipt_bundle()
    public_key = tmp_path / "authority.pem"
    public_key.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setattr(cli, "PINNED_EXTERNAL_AUTHORITY_KEY_FINGERPRINT", _key_fingerprint(key))
    original = lane_receipts["portable"]["result"]
    original["nodeids"][0] = original["nodeids"][0].replace("[0000]", "[forged]")
    original["nodeids"].sort()
    original["nodeids_sha256"] = _node_sha(original["nodeids"])
    original["result_sha256"] = _result_sha(original)
    with pytest.raises(ci.R7S5CIContractError, match="receipt_signature_rejected"):
        _closure(
            collection_inventory_receipts=collection_receipts,
            lane_result_receipts=lane_receipts,
            runner_receipts=runner_receipts,
            private_artifact_receipt=artifact,
            verifier=cli._ed25519_verifier(public_key),
        )


def test_lane_result_self_consistent_digest_is_required() -> None:
    lane_receipts = _lane_result_receipts()
    lane_receipts["windows"]["result"]["result_sha256"] = "0" * 64
    with pytest.raises(ci.R7S5CIContractError, match="closure_result_sha_mismatch:windows"):
        _closure(lane_result_receipts=lane_receipts)


def test_lane_result_boolean_run_attempt_is_rejected() -> None:
    lane_receipts = _lane_result_receipts()
    lane_receipts["portable"]["run_attempt"] = True
    with pytest.raises(ci.R7S5CIContractError, match="receipt_run_attempt_integer"):
        _closure(lane_result_receipts=lane_receipts)


def test_cli_does_not_trust_a_caller_supplied_authority_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    public_key = tmp_path / "caller-key.pem"
    public_key.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    assert cli.PINNED_EXTERNAL_AUTHORITY_KEY_FINGERPRINT is None
    with pytest.raises(ci.R7S5CIContractError, match="key_pin_not_provisioned"):
        cli._ed25519_verifier(public_key)
    monkeypatch.setattr(cli, "PINNED_EXTERNAL_AUTHORITY_KEY_FINGERPRINT", "0" * 64)
    with pytest.raises(ci.R7S5CIContractError, match="key_pin_mismatch"):
        cli._ed25519_verifier(public_key)


def test_durable_replay_guard_rejects_reuse_across_instances(tmp_path: Path) -> None:
    ledger = tmp_path / "durable-replay"
    ledger.mkdir()
    key = ("receipt", "external-authority", "receipt-id", "nonce")
    first = ci.DurableReceiptReplayGuard(ledger)
    first.consume(key)
    assert first.parent_directory_fsync_completed in (True, False)
    with pytest.raises(ci.R7S5CIContractError, match="receipt_replay_detected"):
        ci.DurableReceiptReplayGuard(ledger).consume(key)
    markers = list(ledger.glob("*.used"))
    assert len(markers) == 1
    assert json.loads(markers[0].read_text(encoding="utf-8")) == {"receipt_key": list(key)}
    assert ci.DurableReceiptReplayGuard.administrative_tamper_resistant is False


def test_local_replay_layer_rejects_deleted_marker_in_same_process(tmp_path: Path) -> None:
    ledger = tmp_path / "deleted-marker"
    ledger.mkdir()
    guard = ci.DurableReceiptReplayGuard(ledger)
    key = ("receipt", "external-authority", "receipt-id", "nonce")
    guard.consume(key)
    next(ledger.glob("*.used")).unlink()
    with pytest.raises(ci.R7S5CIContractError, match="receipt_replay_detected"):
        guard.consume(key)
    # A new process could accept after administrative deletion; this is why
    # the local layer is explicitly ineligible as the production authority.
    ci.DurableReceiptReplayGuard(ledger).consume(key)
    assert ci.DurableReceiptReplayGuard.administrative_tamper_resistant is False
    with pytest.raises(ci.R7S5CIContractError, match="worm_replay_authority_not_provisioned"):
        cli._production_replay_guard()


def test_local_replay_layer_rejects_directory_replacement(tmp_path: Path) -> None:
    ledger = tmp_path / "replace-ledger"
    ledger.mkdir()
    guard = ci.DurableReceiptReplayGuard(ledger)
    ledger.rename(tmp_path / "original-ledger")
    ledger.mkdir()
    with pytest.raises(ci.R7S5CIContractError, match="backend_identity_changed"):
        guard.consume(("receipt", "external-authority", "new-receipt", "new-nonce"))


def test_cli_requires_external_worm_replay_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.PINNED_EXTERNAL_WORM_REPLAY_AUTHORITY_IDENTITY is None
    assert cli.PINNED_EXTERNAL_WORM_REPLAY_BACKEND_ATTESTATION_SHA256 is None
    with pytest.raises(ci.R7S5CIContractError, match="worm_replay_authority_not_provisioned"):
        cli._production_replay_guard()
    monkeypatch.setattr(cli, "PINNED_EXTERNAL_WORM_REPLAY_AUTHORITY_IDENTITY", "approved-authority")
    monkeypatch.setattr(
        cli,
        "PINNED_EXTERNAL_WORM_REPLAY_BACKEND_ATTESTATION_SHA256",
        "a" * 64,
    )
    with pytest.raises(ci.R7S5CIContractError, match="authority_adapter_not_implemented"):
        cli._production_replay_guard()


def test_cli_closure_remains_no_go_without_external_collection_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key, collection_receipts, lane_receipts, runner_receipts, artifact = _signed_receipt_bundle()
    public_key = tmp_path / "authority.pem"
    public_key.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    lane_paths: dict[str, Path] = {}
    for lane, receipt in lane_receipts.items():
        lane_paths[lane] = tmp_path / f"{lane}-result.json"
        _write_canonical_json(lane_paths[lane], receipt)
    collection_paths: dict[str, Path] = {}
    for lane, receipt in collection_receipts.items():
        collection_paths[lane] = tmp_path / f"{lane}-collection.json"
        _write_canonical_json(collection_paths[lane], receipt)
    runner_paths: dict[str, Path] = {}
    for lane, receipt in runner_receipts.items():
        runner_paths[lane] = tmp_path / f"{lane}-runner.json"
        _write_canonical_json(runner_paths[lane], receipt)
    artifact_path = tmp_path / "private-artifact.json"
    _write_canonical_json(artifact_path, artifact)

    monkeypatch.setattr(
        cli,
        "_git_identity",
        lambda _root: (ci.EXPECTED_BASELINE_COMMIT, ci.EXPECTED_BASELINE_TREE, True),
    )
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    monkeypatch.setattr(cli, "PINNED_EXTERNAL_AUTHORITY_KEY_FINGERPRINT", _key_fingerprint(key))
    monkeypatch.setattr(cli, "_production_replay_guard", ci.ReceiptReplayGuard)
    arguments = [
        "closure",
        "--manifest",
        str(MANIFEST),
        "--project-root",
        str(ROOT),
        "--repository",
        "ruma0236/ML_ServeAPI",
        "--workflow-name",
        "enterprise-vision-mlops-ci.yml",
        "--commit",
        ci.EXPECTED_BASELINE_COMMIT,
        "--tree",
        ci.EXPECTED_BASELINE_TREE,
        "--run-id",
        "33590000000",
        "--run-attempt",
        "1",
        "--authority-public-key",
        str(public_key),
        "--private-artifact-receipt",
        str(artifact_path),
    ]
    for lane in ci.LANES:
        arguments.extend(
            [
                "--collection-inventory-receipt",
                f"{lane}={collection_paths[lane]}",
            ]
        )
        arguments.extend(["--lane-result-receipt", f"{lane}={lane_paths[lane]}"])
    for lane in ("windows", "private"):
        arguments.extend(["--runner-receipt", f"{lane}={runner_paths[lane]}"])

    assert cli.main(arguments) == 2
    rejection = json.loads(capsys.readouterr().err)
    assert "external_collection_authority_unprovisioned" in rejection["error"]

    monkeypatch.setattr(
        cli,
        "_git_identity",
        lambda _root: ("0" * 40, ci.EXPECTED_BASELINE_TREE, True),
    )
    assert cli.main(arguments) == 2
    rejection = json.loads(capsys.readouterr().err)
    assert "closure_checkout_identity_mismatch" in rejection["error"]

    monkeypatch.setattr(
        cli,
        "_git_identity",
        lambda _root: (ci.EXPECTED_BASELINE_COMMIT, ci.EXPECTED_BASELINE_TREE, True),
    )
    monkeypatch.setattr(cli, "PINNED_EXTERNAL_AUTHORITY_KEY_FINGERPRINT", None)
    assert cli.main(arguments) == 2
    rejection = json.loads(capsys.readouterr().err)
    assert "external_authority_key_pin_not_provisioned" in rejection["error"]


def test_cli_readback_reports_no_go_without_live_calls(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "manifest",
            "--manifest",
            str(MANIFEST),
            "--project-root",
            str(ROOT),
            "--lane",
            "portable",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["selected_lane"] == "portable"
    assert result["go_evidence_eligible"] is False
    assert result["status"] == "manual_intervention_required"


def test_cli_manifest_only_cannot_serve_as_required_closure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "manifest",
            "--manifest",
            str(MANIFEST),
            "--project-root",
            str(ROOT),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 3
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["required_lane_closure_eligible"] is False
    assert result["credit"] == "zero_credit"
    assert result["go_evidence_eligible"] is False

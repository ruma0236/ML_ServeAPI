from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from evm.scale_validation.phase_b2_r7 import (
    LONG_LIVED_SERVICES,
    ONE_SHOT_SERVICES,
    RESTORE_LIFECYCLE_COUNTS,
    RestoreCheckpoint,
    RestoreDeadline,
    RestoreReport,
    TimeoutContract,
    sha256_file,
)
from scripts.dev import run_x1_phase_b2_r7 as runner


def _early_runtime_fixture(
    tmp_path: Path,
) -> tuple[list[str], dict[str, Path], dict[str, bytes]]:
    runtime_paths = {role: tmp_path / f"{role}.py" for role in ("runner", "process", "core")}
    payloads = {
        "runner": b"RUNTIME_SENTINEL = 'runner-pinned'\n",
        "process": b"RUNTIME_SENTINEL = 'process-pinned'\n",
        "core": b"RUNTIME_SENTINEL = 'core-pinned'\n",
    }
    runtime = {}
    for role, path in runtime_paths.items():
        path.write_bytes(payloads[role])
        runtime[role] = {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(payloads[role]).hexdigest(),
            "blob_oid": "a" * 40,
            "bytes": len(payloads[role]),
        }
    manifest_path = tmp_path / runner.MANIFEST_LEAF
    manifest = {
        "execution_mode": "restore-only",
        "bundle_id": "x1-phase-b2-r7-early-runtime",
        "runtime": runtime,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    evidence = {
        "mode": "restore-only",
        "run_id": manifest["bundle_id"],
        "sha_chain": {
            "manifest": sha256_file(manifest_path),
            **{role: item["sha256"] for role, item in runtime.items()},
        },
    }
    encoded = base64.b64encode(json.dumps(evidence).encode()).decode()
    return (
        [
            "--manifest",
            str(manifest_path),
            "--launcher-evidence-base64",
            encoded,
        ],
        runtime_paths,
        payloads,
    )


def test_pretrust_runtime_snapshot_rejects_swap_and_loads_verified_bytes(tmp_path: Path) -> None:
    argv, runtime_paths, payloads = _early_runtime_fixture(tmp_path)
    _, _, snapshots = runner._early_runtime_snapshots(
        argv,
        runner_path=runtime_paths["runner"],
        expected_runtime_paths=runtime_paths,
    )
    runtime_paths["core"].write_text("RUNTIME_SENTINEL = 'swapped'\n", encoding="utf-8")
    module_name = "r7_test_verified_core_snapshot"
    try:
        module = runner._load_module_snapshot(
            module_name, snapshots["core"][0], snapshots["core"][1]
        )
        assert module.RUNTIME_SENTINEL == "core-pinned"
        assert snapshots["core"][1] == payloads["core"]
    finally:
        runner.sys.modules.pop(module_name, None)
    with pytest.raises(RuntimeError, match="pretrust_runtime_snapshot_mismatch:core"):
        runner._early_runtime_snapshots(
            argv,
            runner_path=runtime_paths["runner"],
            expected_runtime_paths=runtime_paths,
        )


def test_pretrust_launcher_run_id_is_bound_to_manifest(tmp_path: Path) -> None:
    argv, runtime_paths, _ = _early_runtime_fixture(tmp_path)
    encoded_index = argv.index("--launcher-evidence-base64") + 1
    evidence = json.loads(base64.b64decode(argv[encoded_index]).decode())
    evidence["run_id"] = "x1-phase-b2-r7-different-run"
    argv[encoded_index] = base64.b64encode(json.dumps(evidence).encode()).decode()
    with pytest.raises(RuntimeError, match="pretrust_launcher_run_id_mismatch"):
        runner._early_runtime_snapshots(
            argv,
            runner_path=runtime_paths["runner"],
            expected_runtime_paths=runtime_paths,
        )


def test_etw_amendment_is_read_back_immediately_before_full_prepare(tmp_path: Path) -> None:
    amendment = tmp_path / "etw-amendment.json"
    amendment.write_text('{"decision":"pinned"}\n', encoding="utf-8")
    manifest = {
        "etw_contract": {
            "amendment_path": str(amendment),
            "amendment_sha256": sha256_file(amendment),
        }
    }
    runner._verify_etw_amendment(manifest)
    amendment.write_text('{"decision":"swapped"}\n', encoding="utf-8")
    with pytest.raises(runner.R7RunnerError, match="etw_amendment_sha256_mismatch"):
        runner._verify_etw_amendment(manifest)


def test_json_hash_and_payload_come_from_one_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "attestation.json"
    original = b'{"decision":"pinned"}\n'
    swapped = b'{"decision":"swapped"}\n'
    target.write_bytes(original)
    original_read_bytes = Path.read_bytes
    reads = [0]

    def read_and_swap(path: Path) -> bytes:
        payload = original_read_bytes(path)
        if path.resolve() == target.resolve():
            reads[0] += 1
            path.write_bytes(swapped)
        return payload

    monkeypatch.setattr(Path, "read_bytes", read_and_swap)
    payload, measured_sha = runner._read_json_snapshot(target, "attestation")

    assert payload == {"decision": "pinned"}
    assert measured_sha == hashlib.sha256(original).hexdigest()
    assert target.read_text(encoding="utf-8") == '{"decision":"swapped"}\n'
    assert reads == [1]


def _process_evidence(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "active_process_zero": True,
        "streams_drained": True,
        "identity_coverage_complete": True,
        "forced_termination_attempts": 0,
    }


def _command_result(name: str, stdout: str = "") -> dict[str, Any]:
    return {
        "passed": True,
        "last_error": None,
        "manual_intervention_required": False,
        "residual_pids": [],
        "stdout": stdout,
        "stderr": "",
        "process_evidence": _process_evidence(name),
    }


def _probe(monkeypatch: pytest.MonkeyPatch, expected: dict[str, Any]) -> runner.R7ProbeSet:
    monkeypatch.setattr(
        runner.R7ProbeSet,
        "_find_executable",
        staticmethod(lambda name, _fallback: f"{name}.exe"),
    )
    return runner.R7ProbeSet(
        manifest={"expected_state": expected, "canonical_tree": "b" * 40},
        contract=TimeoutContract(),
        expected_revision="a" * 40,
        repository_root=Path.cwd(),
        process_runner=object(),
    )


def _compose_expected() -> dict[str, Any]:
    pins = {
        service: {
            "container_name": f"container-{index}",
            "container_id": f"{index + 1:064x}",
            "image_id": f"sha256:{index + 101:064x}",
            "healthcheck_expected": index % 2 == 0,
        }
        for index, service in enumerate(LONG_LIVED_SERVICES)
    }
    return {
        "compose": {
            "project_name": "enterprise-vision-mlops",
            "config_path": str(Path.cwd() / "docker-compose.yml"),
            "config_sha256": sha256_file(Path.cwd() / "docker-compose.yml"),
            "long_lived_services": list(LONG_LIVED_SERVICES),
            "one_shot_services": list(ONE_SHOT_SERVICES),
            "service_pins": pins,
            "stability": {
                "duration_seconds": 300,
                "interval_seconds": 5,
                "samples": 61,
                "restart_delta": 0,
            },
        },
        "database": {
            "instances": {
                "control_plane": {
                    "container_name": "cp",
                    "user": "cp",
                    "database": "cp",
                },
                "mlflow": {
                    "container_name": "mlflow-db",
                    "user": "mlflow",
                    "database": "mlflow",
                },
                "airflow": {
                    "container_name": "airflow-db",
                    "user": "airflow",
                    "database": "airflow",
                },
            },
            "control_plane_schema_versions": ["001_a"],
            "mlflow_migration_head": "0584bdc529eb",
            "airflow_migration_head": "5f2621c13b39",
        },
    }


def _compose_rows(expected: dict[str, Any]) -> list[dict[str, Any]]:
    pins = expected["compose"]["service_pins"]
    rows = [
        {
            "Service": service,
            "State": "running",
            "Health": "healthy" if pins[service]["healthcheck_expected"] else "",
        }
        for service in LONG_LIVED_SERVICES
    ]
    rows.extend(
        {"Service": service, "State": "exited", "ExitCode": 0} for service in ONE_SHOT_SERVICES
    )
    return rows


def _snapshot(expected: dict[str, Any], restart_count: int = 0) -> dict[str, dict[str, Any]]:
    return {
        pin["container_name"]: {
            "container_id": pin["container_id"],
            "image_id": pin["image_id"],
            "container_name": pin["container_name"],
            "status": "running",
            "running": True,
            "restarting": False,
            "restart_count": restart_count,
            "health": "healthy" if pin["healthcheck_expected"] else "none",
            "oom_killed": False,
        }
        for pin in expected["compose"]["service_pins"].values()
    }


def _database_pass() -> dict[str, Any]:
    invariants = {
        "postgres_3_of_3_connected": True,
        "postgres_3_of_3_not_in_recovery": True,
        "control_plane_migrations_exact": True,
        "mlflow_migration_head_exact": True,
        "airflow_migration_head_exact": True,
    }
    return {
        "passed": True,
        "manual_intervention_required": False,
        "last_error": None,
        "invariants": invariants,
        "process_evidence": [],
        "residual_pids": [],
        "databases": {},
    }


def test_compose_exact_identity_and_restart_stability_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _compose_expected()
    probe = _probe(monkeypatch, expected)
    clock = [0.0]
    probe.clock = lambda: clock[0]
    probe.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    monkeypatch.setattr(
        probe,
        "_compose_ps",
        lambda _deadline, name: (_command_result(name), _compose_rows(expected)),
    )
    monkeypatch.setattr(
        probe,
        "_container_snapshot",
        lambda _deadline, _ids, name: (_command_result(name), _snapshot(expected)),
    )
    monkeypatch.setattr(probe, "_database_readback", lambda _deadline: _database_pass())

    result = probe.compose(RestoreDeadline(600, clock=probe.clock, started_monotonic=0))

    assert result["passed"] is True
    assert result["stability"]["observed_samples"] == 61
    assert result["stability"]["observed_duration_seconds"] == 300
    assert result["invariants"]["compose_exact_13_running"] is True


@pytest.mark.parametrize("mutation", ["missing_service", "restart_delta", "database"])
def test_compose_mutations_fail_closed(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    expected = _compose_expected()
    probe = _probe(monkeypatch, expected)
    clock = [0.0]
    calls = [0]
    probe.clock = lambda: clock[0]
    probe.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    rows = _compose_rows(expected)
    if mutation == "missing_service":
        rows.pop()
    monkeypatch.setattr(
        probe,
        "_compose_ps",
        lambda _deadline, name: (_command_result(name), rows),
    )

    def snapshot(_deadline: Any, _ids: Any, name: str) -> tuple[dict[str, Any], Any]:
        calls[0] += 1
        restarts = 1 if mutation == "restart_delta" and calls[0] > 1 else 0
        return _command_result(name), _snapshot(expected, restarts)

    monkeypatch.setattr(probe, "_container_snapshot", snapshot)
    database = _database_pass()
    if mutation == "database":
        database["passed"] = False
        database["invariants"]["control_plane_migrations_exact"] = False
    monkeypatch.setattr(probe, "_database_readback", lambda _deadline: database)

    result = probe.compose(RestoreDeadline(600, clock=probe.clock, started_monotonic=0))

    assert result["passed"] is False


def test_compose_containment_latch_stops_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _compose_expected()
    probe = _probe(monkeypatch, expected)
    launches = [0]
    failure = {
        **_command_result("compose"),
        "passed": False,
        "manual_intervention_required": True,
        "residual_pids": [4242],
    }
    monkeypatch.setattr(probe, "_compose_ps", lambda _deadline, name: (failure, []))

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        launches[0] += 1
        raise AssertionError("followup probe forbidden")

    monkeypatch.setattr(probe, "_container_snapshot", forbidden)
    monkeypatch.setattr(probe, "_database_readback", forbidden)

    result = probe.compose(RestoreDeadline(600))

    assert result["manual_intervention_required"] is True
    assert result["residual_pids"] == [4242]
    assert launches == [0]


def test_kubernetes_two_confirmations_are_not_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe(monkeypatch, {"kubernetes": {"health_confirmation_samples": 2}})
    calls: list[str] = []

    def run(_deadline: Any, _command: Any, *, name: str) -> dict[str, Any]:
        calls.append(name)
        return _command_result(name, "ok\n")

    monkeypatch.setattr(probe, "_run", run)
    result = probe.kubernetes_api(RestoreDeadline(600))

    assert result["passed"] is True
    assert result["automatic_retries"] == 0
    assert calls == [
        "r7-kubernetes-livez-confirmation-1",
        "r7-kubernetes-readyz-confirmation-1",
        "r7-kubernetes-livez-confirmation-2",
        "r7-kubernetes-readyz-confirmation-2",
    ]


def test_kubernetes_eof_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe(monkeypatch, {"kubernetes": {"health_confirmation_samples": 2}})
    calls = [0]

    def run(_deadline: Any, _command: Any, *, name: str) -> dict[str, Any]:
        calls[0] += 1
        return {
            **_command_result(name),
            "passed": False,
            "last_error": "unexpected EOF",
        }

    monkeypatch.setattr(probe, "_run", run)
    result = probe.kubernetes_api(RestoreDeadline(600))

    assert result["passed"] is False
    assert result["retryable"] is False
    assert calls[0] == 1


@pytest.mark.parametrize(
    "mutation", [None, "worker_image", "ready_revision", "image_revision", "attestation"]
)
def test_api_release_attestation_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    revision = "a" * 40
    tree = "b" * 40
    image = f"sha256:{'c' * 64}"
    attestation_path = tmp_path / "image-attestation.json"
    attestation = {"image_id": image, "source_revision": revision, "source_tree": tree}
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    expected = {
        "api": {
            "base_url": "http://127.0.0.1:8000",
            "api_container_name": "evm-api",
            "worker_container_name": "evm-task-queue-worker",
            "image_id": image,
            "source_revision": revision,
            "source_tree": tree,
            "image_attestation": {
                "path": str(attestation_path),
                "sha256": sha256_file(attestation_path),
            },
        }
    }
    if mutation == "attestation":
        expected["api"]["image_attestation"]["sha256"] = "d" * 64
    probe = _probe(monkeypatch, expected)

    def http(_deadline: Any, _method: str, url: str, **_kwargs: Any) -> dict[str, Any]:
        body: dict[str, Any] = {"status": "ok"}
        if url.endswith("/ready"):
            body = {
                "runtime_source_commit": "e" * 40 if mutation == "ready_revision" else revision,
                "runtime_revision_matches": True,
            }
        return {"status": 200, "body": body, "body_text": "", "error": None}

    monkeypatch.setattr(probe, "_http_json", http)

    def run(_deadline: Any, command: Any, *, name: str) -> dict[str, Any]:
        if "image" in command:
            observed_revision = "e" * 40 if mutation == "image_revision" else revision
            return _command_result(name, f"{image}\t{observed_revision}\n")
        worker_image = f"sha256:{'d' * 64}" if mutation == "worker_image" else image
        stdout = (
            f"{'1' * 64}\t{image}\t/evm-api\n{'2' * 64}\t{worker_image}\t/evm-task-queue-worker\n"
        )
        return _command_result(name, stdout)

    monkeypatch.setattr(probe, "_run", run)
    result = probe.api_release_identity(RestoreDeadline(600))

    assert result["passed"] is (mutation is None)
    assert result["invariants"]["api_image_attestation_exact"] is (mutation != "attestation")


def test_api_containment_latch_stops_image_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = f"sha256:{'c' * 64}"
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text("{}", encoding="utf-8")
    expected = {
        "api": {
            "base_url": "http://127.0.0.1:8000",
            "api_container_name": "evm-api",
            "worker_container_name": "evm-task-queue-worker",
            "image_id": image,
            "source_revision": "a" * 40,
            "source_tree": "b" * 40,
            "image_attestation": {
                "path": str(attestation_path),
                "sha256": sha256_file(attestation_path),
            },
        }
    }
    probe = _probe(monkeypatch, expected)
    monkeypatch.setattr(
        probe,
        "_http_json",
        lambda *_args, **_kwargs: {"status": 200, "body": {}, "error": None},
    )
    launches = [0]

    def run(_deadline: Any, _command: Any, *, name: str) -> dict[str, Any]:
        launches[0] += 1
        return {
            **_command_result(name),
            "passed": False,
            "manual_intervention_required": True,
            "residual_pids": [55],
        }

    monkeypatch.setattr(probe, "_run", run)
    result = probe.api_release_identity(RestoreDeadline(600))

    assert result["manual_intervention_required"] is True
    assert launches == [1]


def test_database_migration_mismatch_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _compose_expected()
    probe = _probe(monkeypatch, expected)

    def run(_deadline: Any, _command: Any, *, name: str) -> dict[str, Any]:
        if "migration" not in name:
            role = name.split("-")[-2]
            database = expected["database"]["instances"][role]["database"]
            return _command_result(name, f"{database}|f\n")
        if "control_plane" in name:
            return _command_result(name, "001_a\n002_missing\n")
        head = "0584bdc529eb" if "mlflow" in name else "5f2621c13b39"
        return _command_result(name, f"{head}\n")

    monkeypatch.setattr(probe, "_run", run)
    result = probe._database_readback(RestoreDeadline(600))

    assert result["passed"] is False
    assert result["invariants"]["control_plane_migrations_exact"] is False
    assert result["invariants"]["mlflow_migration_head_exact"] is True
    assert result["invariants"]["airflow_migration_head_exact"] is True


def test_historical_unproven_count_blocks_restore() -> None:
    base = {
        "observed_count": 1,
        "executing_count": 0,
        "historical_count": 1,
        "unproven_count": 0,
        "classification": "historical_nonexecuting",
    }
    assert runner.R7ProbeSet._historical_classification_exact(
        base, observed_count=1, attestation_exact=True
    )
    mutated = {
        **base,
        "historical_count": 0,
        "unproven_count": 1,
        "classification": "unproven",
    }
    assert not runner.R7ProbeSet._historical_classification_exact(
        mutated, observed_count=1, attestation_exact=True
    )


def _semantic_attestation(
    *, tmp_path: Path, source: str, query: str, observed_state: str = "queued"
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    classification = {
        "source": source,
        "observed_count": 1,
        "executing_count": 0,
        "historical_count": 1,
        "unproven_count": 0,
        "classification": "historical_nonexecuting",
        "attestation": {"path": "unused", "sha256": "a" * 64},
    }
    identity = {
        "entity_id": "task-1",
        "created_at": "2026-08-31T00:00:00.000000Z",
        "updated_at": "2026-08-31T01:00:00.000000Z",
    }
    observed = [
        {
            "identity": identity,
            "observed_state": observed_state,
            "execution_counts": {
                "active_job_count": 0,
                "active_claim_count": 0,
                "active_lease_count": 0,
                "outcome_unknown_count": 0,
            },
        }
    ]
    proof_path = tmp_path / "record-proof.json"
    proof_payload = {
        "source": source,
        "identity": identity,
        "observed_state": observed_state,
        "captured_at": "2026-09-01T00:30:00Z",
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "active_job_count": 0,
        "active_claim_count": 0,
        "active_lease_count": 0,
        "outcome_unknown_count": 0,
        "inactivity_decision": "proven_inactive",
        "decision_authority": runner.HISTORICAL_DECISION_AUTHORITY,
    }
    proof_path.write_text(json.dumps(proof_payload), encoding="utf-8")
    payload = {
        "source": source,
        "captured_at": "2026-09-01T01:00:00Z",
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "counts": {
            "observed_count": 1,
            "executing_count": 0,
            "historical_count": 1,
            "unproven_count": 0,
        },
        "classification": "historical_nonexecuting",
        "records": [
            {
                "identity": identity,
                "observed_state": observed_state,
                "classification": "historical_nonexecuting",
                "execution_proof": {
                    "inactivity_proven": True,
                    "active_job_count": 0,
                    "active_claim_count": 0,
                    "active_lease_count": 0,
                    "outcome_unknown_count": 0,
                    "evidence": {
                        "path": str(proof_path),
                        "sha256": sha256_file(proof_path),
                    },
                },
            }
        ],
    }
    return classification, payload, observed


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "empty",
        "source",
        "count",
        "query",
        "old",
        "proof",
        "proof_sha",
        "authority",
        "contradictory_active",
        "live_link",
    ],
)
def test_historical_attestation_semantics_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    probe = _probe(monkeypatch, {})
    probe.parent_payloads = {"post_manual_on_readback": {"captured_at": "2026-09-01T00:00:00Z"}}
    source = "control_plane_task_entity_statuses"
    query = runner.CONTROL_PLANE_HISTORY_QUERY
    classification, payload, observed = _semantic_attestation(
        tmp_path=tmp_path, source=source, query=query
    )
    if mutation == "empty":
        payload = {}
    elif mutation == "source":
        payload["source"] = "mlflow_running_rows"
    elif mutation == "count":
        payload["counts"]["observed_count"] = 2
    elif mutation == "query":
        payload["query_sha256"] = "c" * 64
    elif mutation == "old":
        payload["captured_at"] = "2026-08-31T23:59:59Z"
    elif mutation == "proof":
        payload["records"][0]["execution_proof"]["inactivity_proven"] = False
    elif mutation == "proof_sha":
        payload["records"][0]["execution_proof"]["evidence"]["sha256"] = "c" * 64
    elif mutation == "authority":
        proof_path = Path(payload["records"][0]["execution_proof"]["evidence"]["path"])
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        proof["decision_authority"] = "arbitrary-reviewer"
        proof_path.write_text(json.dumps(proof), encoding="utf-8")
        payload["records"][0]["execution_proof"]["evidence"]["sha256"] = sha256_file(proof_path)
    elif mutation == "contradictory_active":
        execution_proof = payload["records"][0]["execution_proof"]
        execution_proof["active_job_count"] = 1
        proof_path = Path(execution_proof["evidence"]["path"])
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        proof["active_job_count"] = 1
        proof_path.write_text(json.dumps(proof), encoding="utf-8")
        execution_proof["evidence"]["sha256"] = sha256_file(proof_path)
    elif mutation == "live_link":
        observed[0]["execution_counts"]["active_claim_count"] = 1

    result = probe._historical_attestation_exact(
        source=source,
        classification=classification,
        payload=payload,
        query=query,
        observed_records=observed,
        file_sha_exact=True,
        attestation_path=tmp_path / "attestation.json",
    )

    assert result is (mutation is None)


def test_queue_unknown_state_and_per_record_execution_links_are_fail_closed() -> None:
    assert runner.R7ProbeSet._parse_queue_readback("0|0|0|0|0\n") == (0, 0, 0, 0, 0)
    assert runner.R7ProbeSet._parse_queue_readback("0|0|0|0|1\n")[4] == 1
    with pytest.raises(ValueError, match="queue_field_count"):
        runner.R7ProbeSet._parse_queue_readback("0|0|0|0\n")
    assert runner.R7ProbeSet._parse_control_execution_links("task-1|0|0|0|0\n") == {
        "task-1": {
            "active_job_count": 0,
            "active_claim_count": 0,
            "active_lease_count": 0,
            "outcome_unknown_count": 0,
        }
    }
    with pytest.raises(ValueError, match="control_execution_link_fields_invalid"):
        runner.R7ProbeSet._parse_control_execution_links("task-1|0|0|0|0\ntask-1|0|0|0|0\n")


@pytest.mark.parametrize(
    "status",
    [
        {},
        {"active": 0},
        {"active": 0, "conditions": [{"type": "Suspended", "status": "True"}]},
    ],
)
def test_kubernetes_pending_or_unknown_job_is_unproven(status: dict[str, Any]) -> None:
    snapshot = runner.R7ProbeSet._kubernetes_job_snapshot(
        [
            {
                "metadata": {"uid": "uid-1", "namespace": "default", "name": "pending"},
                "status": status,
            }
        ]
    )
    assert snapshot["active_count"] == 0
    assert len(snapshot["unproven"]) == 1


def test_kubernetes_job_requires_terminal_condition_or_reports_active() -> None:
    snapshot = runner.R7ProbeSet._kubernetes_job_snapshot(
        [
            {
                "metadata": {"uid": "uid-a", "namespace": "default", "name": "active"},
                "status": {"active": 1},
            },
            {
                "metadata": {"uid": "uid-t", "namespace": "default", "name": "terminal"},
                "status": {
                    "conditions": [{"type": "Complete", "status": "True"}],
                },
            },
        ]
    )
    assert snapshot["active_count"] == 1
    assert len(snapshot["terminal"]) == 1
    assert not snapshot["unproven"]
    with pytest.raises(ValueError, match="active_nonnegative_integer"):
        runner.R7ProbeSet._kubernetes_job_snapshot(
            [
                {
                    "metadata": {"uid": "uid", "namespace": "default", "name": "bad"},
                    "status": {"active": -1},
                }
            ]
        )


def test_final_temporal_reread_rejects_late_activation() -> None:
    zeros = (0, 0, 0, 0, 0)
    assert runner.R7ProbeSet._temporal_queue_zero(zeros, zeros)
    assert not runner.R7ProbeSet._temporal_queue_zero(zeros, (1, 0, 0, 0, 0))
    terminal = {
        "active_count": 0,
        "terminal": [{"uid": "u", "namespace": "n", "name": "j"}],
        "unproven": [],
        "total": 1,
    }
    pending = {
        "active_count": 0,
        "terminal": [],
        "unproven": [{"uid": "u", "namespace": "n", "name": "j"}],
        "total": 1,
    }
    assert runner.R7ProbeSet._temporal_jobs_zero(terminal, terminal)
    assert not runner.R7ProbeSet._temporal_jobs_zero(terminal, pending)
    initial_links = {
        "task": {
            "active_job_count": 0,
            "active_claim_count": 0,
            "active_lease_count": 0,
            "outcome_unknown_count": 0,
        }
    }
    final_links = json.loads(json.dumps(initial_links))
    final_links["task"]["active_lease_count"] = 1
    assert runner.R7ProbeSet._temporal_execution_links_zero(initial_links, initial_links)
    assert not runner.R7ProbeSet._temporal_execution_links_zero(initial_links, final_links)


def test_global_residual_presence_is_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe(monkeypatch, {})
    monkeypatch.setattr(
        probe,
        "_run",
        lambda _deadline, _command, *, name: _command_result(
            name,
            json.dumps(
                [
                    {
                        "pid": 123,
                        "ppid": 1,
                        "creation_time": "20260901000000.000000+000",
                        "name": "python.exe",
                        "command_line_sha256": "f" * 64,
                    }
                ]
            ),
        ),
    )
    process, residuals = probe._global_windows_residuals(RestoreDeadline(600))

    assert process["passed"] is True
    assert [item["pid"] for item in residuals] == [123]


def _prepared(tmp_path: Path) -> runner.PreparedExecution:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest_path = bundle / runner.MANIFEST_LEAF
    manifest_path.write_text("{}", encoding="utf-8")
    args = argparse.Namespace(
        manifest=manifest_path,
        output_directory=tmp_path / "output",
        expected_revision="a" * 40,
        launcher_evidence_base64="ignored",
        repository_root=tmp_path,
        mode="restore-only",
    )
    return runner.PreparedExecution(
        args=args,
        manifest={"canonical_tree": "b" * 40, "parent_checkpoints": []},
        manifest_sha256=sha256_file(manifest_path),
        launcher_evidence={},
        parent_payloads={},
        restore_checkpoint=RestoreCheckpoint(
            source="test",
            historical_call_counts=dict(RESTORE_LIFECYCLE_COUNTS),
        ),
        timeout_contract=TimeoutContract(),
        output_directory=args.output_directory,
        run_id="x1-phase-b2-r7-test",
        bundle_directory=bundle,
    )


def test_metadata_uses_prepared_manifest_snapshot_after_disk_swap(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    pinned_sha = prepared.manifest_sha256
    prepared.args.manifest.write_text('{"swapped":true}\n', encoding="utf-8")

    metadata = runner._metadata(prepared)

    assert metadata["manifest_sha256"] == pinned_sha
    assert metadata["manifest_sha256"] != sha256_file(prepared.args.manifest)


def _preflight_args(tmp_path: Path) -> tuple[list[str], Path, Path]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    output = tmp_path / "output"
    run_id = "x1-phase-b2-r7-preflight-failure"
    revision = "a" * 40
    manifest_path = bundle / runner.MANIFEST_LEAF
    manifest_path.write_text(
        json.dumps(
            {
                "execution_mode": "restore-only",
                "canonical_revision": revision,
                "bundle_id": run_id,
                "bundle": {"path": str(bundle.resolve())},
                "output": {"path": str(output.resolve())},
            }
        ),
        encoding="utf-8",
    )
    for leaf, schema in (
        (
            runner.OUTER_RESERVATION,
            "s8-v4-x1-phase-b2-r7-outer-reservation/v1",
        ),
        (
            runner.BRIDGE_RESERVATION,
            "s8-v4-x1-phase-b2-r7-bridge-reservation/v1",
        ),
    ):
        (bundle / leaf).write_text(
            json.dumps(
                {
                    "schema": schema,
                    "pid": 1234,
                    "mode": "restore-only",
                    "run_id": run_id,
                    "output_directory": str(output.resolve()),
                }
            ),
            encoding="utf-8",
        )
    argv = [
        "--manifest",
        str(manifest_path),
        "--output-directory",
        str(output),
        "--expected-revision",
        revision,
        "--launcher-evidence-base64",
        "unused-after-reservation",
        "--repository-root",
        str(tmp_path),
        "--mode",
        "restore-only",
    ]
    return argv, bundle, output


def _report(passed: bool) -> RestoreReport:
    stage_names = tuple(stage.value for stage in runner.RestoreStage)
    invariants = {name: passed for name in (*runner.R7_REQUIRED_INVARIANTS, *stage_names)}
    stages = [
        {
            "stage": stage,
            "started_at": "2026-09-01T00:00:00Z",
            "ended_at": "2026-09-01T00:00:01Z",
            "duration_seconds": 1.0,
            "attempts": 1,
            "max_attempts": 1,
            "passed": passed,
            "retryable_ignored": False,
            "last_error": None if passed else "gate_failed",
            "manual_intervention_required": not passed,
            "residual_pids": [],
            "invariants": {},
            "details": {},
            "deadline_remaining_seconds": 500.0,
        }
        for stage in stage_names
    ]
    return RestoreReport(
        mode="restore-only",
        started_at="2026-09-01T00:00:00Z",
        ended_at="2026-09-01T00:00:01Z",
        duration_seconds=1.0,
        expected_revision="a" * 40,
        passed=passed,
        manual_intervention_required=not passed,
        deadline_exceeded=False,
        last_error=None if passed else "gate_failed",
        stages=stages,
        call_counts=dict(RESTORE_LIFECYCLE_COUNTS),
        residual_pids=(),
        checkpoint={},
        success_invariants=invariants,
        required_invariants=runner.R7_REQUIRED_INVARIANTS,
        decision="restore_only_pass" if passed else "manual_intervention_required",
    )


def test_duplicate_runner_reservation_prevents_probe(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    runner.reserve_runner(prepared)
    calls = [0]

    def execute(_prepared: Any, _checkpoint: Any) -> RestoreReport:
        calls[0] += 1
        return _report(True)

    with pytest.raises(runner.DuplicateInvocationError):
        runner.execute_restore_only(prepared, restore_executor=execute)
    assert calls == [0]
    assert not prepared.output_directory.exists()


@pytest.mark.parametrize(
    "failure",
    [
        "preflight_contract_mismatch",
        "sha_chain_mismatch",
        "historical_attestation_mismatch",
    ],
)
def test_owned_preflight_failure_seals_once_and_duplicate_adds_no_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    argv, bundle, output = _preflight_args(tmp_path)

    def fail_full_preflight(_args: argparse.Namespace) -> runner.PreparedExecution:
        raise runner.R7RunnerError(failure)

    monkeypatch.setattr(runner, "prepare_execution", fail_full_preflight)
    assert runner.main(argv) == 2
    reservation = bundle / runner.RUNNER_RESERVATION
    failure_index = output / "failure-evidence-index.json"
    failure_seal = output / "failure-seal.json"
    assert reservation.is_file()
    expected_output_leaves = [
        ".failure-evidence-index.json.publish-source",
        ".failure-seal.json.publish-source",
        "failure-evidence-index.json",
        "failure-seal.json",
    ]
    assert sorted(path.name for path in output.iterdir()) == expected_output_leaves
    index = json.loads(failure_index.read_text(encoding="utf-8"))
    assert index["commit_record"]["sha256"] == sha256_file(failure_seal)
    assert index["completion_marker_created"] is False
    assert not (output / "completion-marker.json").exists()
    identities = {reservation.name: sha256_file(reservation)}
    identities.update({path.name: sha256_file(path) for path in output.iterdir()})

    assert runner.main(argv) == 2
    measured = {reservation.name: sha256_file(reservation)}
    measured.update({path.name: sha256_file(path) for path in output.iterdir()})
    assert identities == measured
    assert sorted(path.name for path in output.iterdir()) == expected_output_leaves


@pytest.mark.parametrize("passed", [True, False])
def test_restore_only_seals_without_phase_b2_marker(tmp_path: Path, passed: bool) -> None:
    prepared = _prepared(tmp_path)
    code, result = runner.execute_restore_only(
        prepared, restore_executor=lambda _prepared, _checkpoint: _report(passed)
    )

    assert code == (0 if passed else 2)
    assert result["report"]["phase_b2_executed"] is False
    assert not (prepared.output_directory / "completion-marker.json").exists()
    assert not (prepared.output_directory / "private-evidence-index.json").exists()
    expected_leaf = "restore-only-index.json" if passed else "failure-evidence-index.json"
    assert (prepared.output_directory / expected_leaf).is_file()


def test_success_schema_failure_after_output_creation_commits_failure_only(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    malformed = _report(True)
    assert isinstance(malformed.success_invariants, dict)
    malformed.success_invariants.pop("docker_engine")

    code, result = runner.execute_restore_only(
        prepared,
        restore_executor=lambda _prepared, _checkpoint: malformed,
    )

    assert code == 2
    assert result["decision"] == "manual_intervention_required"
    assert "restore_only_success_publication_failed" in result["report"]["error"]
    assert (prepared.output_directory / "failure-seal.json").is_file()
    assert (prepared.output_directory / "failure-evidence-index.json").is_file()
    assert not (prepared.output_directory / "restore-only-index.json").exists()
    assert not (prepared.output_directory / "completion-marker.json").exists()


def test_harness_exactly_one_attempt_even_when_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path)
    launches = [0]

    class FakeProbes:
        def probes(self) -> dict[str, Any]:
            def failed(_deadline: Any) -> dict[str, Any]:
                launches[0] += 1
                return {
                    "passed": False,
                    "retryable": True,
                    "last_error": "EOF",
                    "manual_intervention_required": True,
                    "residual_pids": [],
                    "invariants": {},
                }

            return {value: failed for value in runner.RESTORE_STAGE_KEYS.values()}

    monkeypatch.setattr(runner, "_new_probe_set", lambda *_args, **_kwargs: FakeProbes())
    report = runner._run_restore_harness(prepared, prepared.restore_checkpoint)

    assert report.passed is False
    assert launches == [1]
    assert len(report.stages) == 1


def test_runner_has_no_old_executable_import_or_forbidden_command() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    forbidden = (
        "run_x1_phase_b2_r3",
        "run_x1_phase_b2_r4",
        "run_x1_phase_b2_r5",
        "docker compose down",
        "--force-recreate",
        "docker prune",
        "TerminateJobObject",
        "taskkill",
        "wsl --shutdown",
        "execute_fresh",
    )
    assert all(value not in source for value in forbidden)


def test_write_exclusive_handles_partial_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = runner.os.write
    calls = [0]

    def partial(fd: int, value: bytes | memoryview) -> int:
        calls[0] += 1
        data = bytes(value)
        return real_write(fd, data[: max(1, len(data) // 2)])

    monkeypatch.setattr(runner.os, "write", partial)
    path = tmp_path / "exclusive.json"
    runner._write_exclusive_json(path, {"value": "x" * 100})

    assert json.loads(path.read_text(encoding="ascii"))["value"] == "x" * 100
    assert calls[0] > 1

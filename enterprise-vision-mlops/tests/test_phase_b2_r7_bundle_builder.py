from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.dev import prepare_x1_phase_b2_r7_bundle as builder


PROJECT = Path(__file__).parents[1]
REVISION = "a" * 40
TREE = "b" * 40
IMAGE_ID = "sha256:" + "c" * 64
EMPTY_UNTRACKED_DIGEST = hashlib.sha256(b"").hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _source_identity() -> dict[str, object]:
    return {
        "revision": REVISION,
        "tree": TREE,
        "branch": "codex/distributed-scale-validation-plan",
        "origin_revision": REVISION,
        "remote_revision": REVISION,
        "tracked": 0,
        "untracked": 4_244,
        "untracked_path_digest_sha256": EMPTY_UNTRACKED_DIGEST,
    }


def _service_pins() -> dict[str, dict[str, object]]:
    pins = {
        name: {
            "container_name": builder.CONTAINER_NAMES[name],
            "container_id": f"{index:x}" * 64,
            "image_id": "sha256:" + f"{(index + 1) % 16:x}" * 64,
            "healthcheck_expected": builder.HEALTHCHECK_EXPECTED[name],
        }
        for index, name in enumerate(builder.LONG_LIVED_SERVICES, start=1)
    }
    pins["api"]["image_id"] = IMAGE_ID
    pins["task-queue-worker"]["image_id"] = IMAGE_ID
    return pins


def _failed_pods() -> list[dict[str, str]]:
    return [
        {
            "uid": f"{index:08x}-1111-1111-1111-111111111111",
            "name": f"evm-b0-production-failed-{index:02d}",
            "namespace": "evm-production",
            "reason": "UnexpectedAdmissionError",
            "owner_uid": f"{index:08x}-2222-2222-2222-222222222222",
        }
        for index in range(1, 12)
    ]


def _attestation(
    tmp_path: Path,
    *,
    source: str,
    records: list[dict[str, object]],
) -> dict[str, str]:
    path = tmp_path / f"{source}-attestation.json"
    for index, record in enumerate(records):
        proof = record["execution_proof"]
        assert isinstance(proof, dict)
        proof_path = tmp_path / f"{source}-proof-{index:03d}.json"
        _write_json(
            proof_path,
            {
                "source": source,
                "identity": record["identity"],
                "observed_state": record["observed_state"],
                "captured_at": "2026-09-01T00:00:00Z",
                "query_sha256": builder.HISTORICAL_QUERY_SHA256[source],
                "active_job_count": proof["active_job_count"],
                "active_claim_count": proof["active_claim_count"],
                "active_lease_count": proof["active_lease_count"],
                "outcome_unknown_count": proof["outcome_unknown_count"],
                "inactivity_decision": "proven_inactive",
                "decision_authority": builder.HISTORICAL_DECISION_AUTHORITY,
            },
        )
        proof["evidence"] = {
            "path": str(proof_path.resolve()),
            "sha256": builder.sha256_file(proof_path),
        }
    counts = {
        "observed_count": len(records),
        "executing_count": 0,
        "historical_count": len(records),
        "unproven_count": 0,
    }
    payload = {
        "source": source,
        "captured_at": "2026-09-01T00:00:00Z",
        "query_sha256": builder.HISTORICAL_QUERY_SHA256[source],
        "counts": counts,
        "classification": "historical_nonexecuting",
        "records": records,
    }
    _write_json(path, payload)
    return {"path": str(path.resolve()), "sha256": builder.sha256_file(path)}


def _job_scope(tmp_path: Path) -> dict[str, object]:
    proof = {
        "inactivity_proven": True,
        "active_job_count": 0,
        "active_claim_count": 0,
        "active_lease_count": 0,
        "outcome_unknown_count": 0,
    }
    cp_records = [
        {
            "identity": {
                "entity_id": f"entity-{index:03d}",
                "created_at": "2026-08-31T00:00:00Z",
                "updated_at": "2026-08-31T01:00:00Z",
            },
            "observed_state": "pending_confirmation",
            "classification": "historical_nonexecuting",
            "execution_proof": copy.deepcopy(proof),
        }
        for index in range(36)
    ]
    mlflow_records = [
        {
            "identity": {
                "run_id": "9bd54156084842ca93bce35a44a0cea7",
                "lifecycle_stage": "active",
                "start_time": "2026-08-31T00:00:00Z",
                "end_time": "2026-08-31T01:00:00Z",
            },
            "observed_state": "RUNNING",
            "classification": "historical_nonexecuting",
            "execution_proof": copy.deepcopy(proof),
        }
    ]
    kubernetes_records = [
        {
            "identity": {
                "uid": pod["uid"],
                "namespace": pod["namespace"],
                "name": pod["name"],
                "owner_uid": pod["owner_uid"],
                "reason": pod["reason"],
            },
            "observed_state": "Failed",
            "classification": "historical_nonexecuting",
            "execution_proof": copy.deepcopy(proof),
        }
        for pod in _failed_pods()
    ]
    classifications = []
    for source, records in (
        ("control_plane_task_entity_statuses", cp_records),
        ("mlflow_running_rows", mlflow_records),
        ("kubernetes_terminal_failed_objects", kubernetes_records),
    ):
        classifications.append(
            {
                "source": source,
                "observed_count": len(records),
                "executing_count": 0,
                "historical_count": len(records),
                "unproven_count": 0,
                "classification": "historical_nonexecuting",
                "attestation": _attestation(tmp_path, source=source, records=records),
            }
        )
    return {
        "canonical_active_jobs": {
            "sources": [
                "kubernetes_job_status_active",
                "manifest_active_job_file_markers",
            ],
            "required_count": 0,
        },
        "historical_observations": {
            "sources": [
                "control_plane_task_entity_statuses",
                "mlflow_running_rows",
                "kubernetes_terminal_failed_objects",
            ],
            "separate_from_canonical_active_jobs": True,
            "unknown_or_unproven_blocks_restore": True,
            "deletion_required": False,
        },
        "historical_classifications": classifications,
    }


def _runtime_state(tmp_path: Path) -> tuple[dict[str, object], Path]:
    attestation = tmp_path / "api-image-attestation.json"
    _write_json(
        attestation,
        {"image_id": IMAGE_ID, "source_revision": REVISION, "source_tree": TREE},
    )
    state: dict[str, object] = {
        "compose": {
            "project_name": "enterprise-vision-mlops",
            "config_path": str((PROJECT / "docker-compose.yml").resolve()),
            "config_sha256": builder.sha256_file(PROJECT / "docker-compose.yml"),
            "long_lived_services": list(builder.LONG_LIVED_SERVICES),
            "one_shot_services": list(builder.ONE_SHOT_SERVICES),
            "service_pins": _service_pins(),
            "stability": {
                "duration_seconds": 300,
                "interval_seconds": 5,
                "samples": 61,
                "restart_delta": 0,
            },
        },
        "api": {
            "base_url": "http://127.0.0.1:8000",
            "api_container_name": "evm-api",
            "worker_container_name": "evm-task-queue-worker",
            "image_id": IMAGE_ID,
            "image_attestation": {
                "path": str(attestation.resolve()),
                "sha256": builder.sha256_file(attestation),
            },
            "source_revision": REVISION,
            "source_tree": TREE,
        },
        "database": {
            "control_plane_schema_versions": builder.source_schema_versions(PROJECT),
            "airflow_migration_head": builder.AIRFLOW_MIGRATION_HEAD,
            "mlflow_migration_head": builder.MLFLOW_MIGRATION_HEAD,
            "instances": {
                "control_plane": {
                    "container_name": "evm-control-plane-postgres",
                    "user": "evm_control_plane",
                    "database": "evm_control_plane",
                },
                "mlflow": {
                    "container_name": "evm-postgres",
                    "user": "mlflow",
                    "database": "mlflow",
                },
                "airflow": {
                    "container_name": "evm-airflow-postgres",
                    "user": "airflow",
                    "database": "airflow",
                },
            },
        },
        "kubernetes": {
            "allowed_historical_failed_pods": _failed_pods(),
            "health_confirmation_samples": 2,
            "residual_selectors": ["evm.openai.local/scenario=s8-v4-x1"],
        },
        "job_scope_contract": _job_scope(tmp_path),
    }
    return state, attestation


def _parent_fixture(
    tmp_path: Path, runtime_state: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], dict[str, Path]]:
    paths: dict[str, Path] = {}
    for role in builder.REQUIRED_PARENT_ROLES:
        paths[role] = tmp_path / f"{role}.json"
        if role not in {"post_manual_on_readback", "post_manual_on_index"}:
            _write_json(paths[role], {"role": role, "failure_only": True})
    _write_json(paths["post_manual_on_readback"], {"runtime_state": runtime_state})
    readback_sha = builder.sha256_file(paths["post_manual_on_readback"])
    _write_json(
        paths["post_manual_on_index"],
        {
            "files": [
                {
                    "path": str(paths["post_manual_on_readback"].resolve()),
                    "sha256": readback_sha,
                }
            ]
        },
    )
    parent_paths = {role: path.resolve() for role, path in paths.items()}
    entries, payloads = builder.build_parent_checkpoints(parent_paths)
    return entries, payloads, parent_paths


def _pins_document(
    runtime_state: dict[str, object],
    parent_entries: list[dict[str, object]],
) -> dict[str, object]:
    parents = {str(item["role"]): item for item in parent_entries}
    return {
        "schema_version": builder.RUNTIME_STATE_SCHEMA,
        "source_evidence": {
            role: {"path": parents[role]["path"], "sha256": parents[role]["sha256"]}
            for role in ("post_manual_on_readback", "post_manual_on_index")
        },
        **copy.deepcopy(runtime_state),
    }


def _runtime_pins(tmp_path: Path) -> dict[str, dict[str, object]]:
    runtime = {
        name: {
            "path": str(tmp_path / f"{name}.txt"),
            "sha256": f"{index:x}" * 64,
            "blob_oid": f"{index:x}" * 40,
            "bytes": 1,
        }
        for index, name in enumerate(builder.RUNTIME_PATHS, start=1)
    }
    core_path = (PROJECT / builder.RUNTIME_PATHS["core"]).resolve()
    runtime["core"].update(
        {
            "path": str(core_path),
            "sha256": builder.sha256_file(core_path),
            "bytes": core_path.stat().st_size,
        }
    )
    return runtime


def test_runtime_component_set_is_r7_only() -> None:
    assert list(builder.RUNTIME_PATHS) == [
        "builder",
        "core",
        "process",
        "runner",
        "validator",
        "docker_compose",
    ]
    assert all("r5" not in str(path).lower() for path in builder.RUNTIME_PATHS.values())
    assert "fresh" not in builder.RUNTIME_PATHS
    assert "process_base" not in builder.RUNTIME_PATHS


def test_schema_versions_are_ast_literal_from_canonical_source() -> None:
    assert builder.source_schema_versions(PROJECT) == [
        "001_transactional_control_plane",
        "002_bounded_admission_queue",
        "003_task_queue_safety",
        "004_task_entity_storage",
        "005_task_queue_operational_safety",
        "006_s6bm_causal_receipts",
        "007_s6bm_transition_fence_identity",
        "008_s6bm_route_revision_history",
    ]


def test_historical_contract_is_exactly_aligned_with_core() -> None:
    from evm.scale_validation.phase_b2_r7 import (
        HISTORICAL_DECISION_AUTHORITY,
        HISTORICAL_QUERY_SHA256,
    )

    assert builder.HISTORICAL_QUERY_SHA256 == HISTORICAL_QUERY_SHA256
    assert builder.HISTORICAL_DECISION_AUTHORITY == HISTORICAL_DECISION_AUTHORITY


def test_runtime_state_pins_validate_exact_contract(tmp_path: Path) -> None:
    state, _ = _runtime_state(tmp_path)
    entries, payloads, _ = _parent_fixture(tmp_path, state)
    document = _pins_document(state, entries)
    pins = tmp_path / "runtime-state-pins.json"
    _write_json(pins, document)
    observed, raw = builder.validate_runtime_state_pins(
        pins,
        project_root=PROJECT,
        source_identity=_source_identity(),
        parent_entries=entries,
        parent_payloads=payloads,
    )
    assert observed == state
    assert raw["schema_version"] == builder.RUNTIME_STATE_SCHEMA
    assert observed["compose"]["stability"]["samples"] == 61  # type: ignore[index]
    assert observed["kubernetes"]["health_confirmation_samples"] == 2  # type: ignore[index]


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda value: value["compose"]["service_pins"]["api"].update({"container_id": "short"}),
            "compose_container_id_invalid",
        ),
        (
            lambda value: value["compose"]["stability"].update({"samples": 60}),
            "compose_stability_contract_mismatch",
        ),
        (
            lambda value: value["database"].update(
                {"control_plane_schema_versions": ["001_transactional_control_plane"]}
            ),
            "control_plane_schema_versions_source_mismatch",
        ),
        (
            lambda value: value["kubernetes"].update({"health_confirmation_samples": 1}),
            "kubernetes_health_confirmation_samples_mismatch",
        ),
        (
            lambda value: value["job_scope_contract"]["historical_observations"].update(
                {"unknown_or_unproven_blocks_restore": False}
            ),
            "job_scope_historical_observations_mismatch",
        ),
    ],
)
def test_runtime_state_mutations_fail_closed(tmp_path: Path, mutation, error: str) -> None:
    state, _ = _runtime_state(tmp_path)
    entries, payloads, _ = _parent_fixture(tmp_path, state)
    document = _pins_document(state, entries)
    mutation(document)
    pins = tmp_path / "runtime-state-pins.json"
    _write_json(pins, document)
    with pytest.raises(builder.BundleBuildError, match=error):
        builder.validate_runtime_state_pins(
            pins,
            project_root=PROJECT,
            source_identity=_source_identity(),
            parent_entries=entries,
            parent_payloads=payloads,
        )


def test_parent_role_set_is_exact_and_paths_are_distinct(tmp_path: Path) -> None:
    specs = [f"{role}={tmp_path / f'{role}.json'}" for role in builder.REQUIRED_PARENT_ROLES]
    parsed = builder.parse_parent_specs(specs)
    assert tuple(parsed) == builder.REQUIRED_PARENT_ROLES
    with pytest.raises(builder.BundleBuildError, match="duplicate"):
        builder.parse_parent_specs([*specs, specs[0]])


def test_manifest_is_restore_only_and_all_mutating_calls_are_zero(
    tmp_path: Path,
) -> None:
    state, _ = _runtime_state(tmp_path)
    entries, _, _ = _parent_fixture(tmp_path, state)
    runtime = _runtime_pins(tmp_path)
    manifest = builder.build_manifest(
        run_id="x1-phase-b2-r7-restore-test",
        source_identity=_source_identity(),
        project_root=PROJECT,
        staging_directory=tmp_path / "stage",
        output_directory=tmp_path / "output",
        python_path=Path(sys.executable),
        runtime=runtime,
        parent_checkpoints=entries,
        expected_state=state,
    )
    assert manifest["execution_mode"] == "restore-only"
    assert manifest["schema_version"] == "evm.s8_v4.x1_phase_b2_r7_restore_work_order.v1"
    assert manifest["bundle"]["path"] == str((tmp_path / "stage").resolve())  # type: ignore[index]
    assert manifest["probe_max_attempts"] == 1
    assert set(manifest["runtime"]) == set(builder.RUNTIME_PATHS)  # type: ignore[arg-type]
    assert set(manifest["repository"]) == {
        "preserved_untracked_count",
        "untracked_path_set_sha256",
        "untracked_path_set_encoding",
        "tracked_changes",
    }
    calls = manifest["call_contract"]  # type: ignore[assignment]
    assert all(value == 0 for value in calls["restore-only"].values())
    assert all(value == 0 for value in calls["collectors"].values())
    assert all(value == 0 for value in calls["downstream"].values())


def test_builder_manifest_passes_core_validator(tmp_path: Path) -> None:
    from evm.scale_validation.phase_b2_r7 import validate_r7_manifest

    state, _ = _runtime_state(tmp_path)
    entries, _, _ = _parent_fixture(tmp_path, state)
    runtime = _runtime_pins(tmp_path)
    manifest = builder.build_manifest(
        run_id="x1-phase-b2-r7-core-integration",
        source_identity=_source_identity(),
        project_root=PROJECT,
        staging_directory=tmp_path / "stage",
        output_directory=tmp_path / "output",
        python_path=Path(sys.executable),
        runtime=runtime,
        parent_checkpoints=entries,
        expected_state=state,
    )
    validated = validate_r7_manifest(
        manifest,
        expected_revision=REVISION,
        expected_untracked_path_set_sha256=EMPTY_UNTRACKED_DIGEST,
    )
    assert validated["mode"] == "restore-only"
    assert validated["revision"] == REVISION


def test_rendered_launchers_are_restore_only_exact_once_and_ast_valid(
    tmp_path: Path,
) -> None:
    state, _ = _runtime_state(tmp_path)
    entries, _, _ = _parent_fixture(tmp_path, state)
    runtime = _runtime_pins(tmp_path)
    manifest = builder.build_manifest(
        run_id="x1-phase-b2-r7-restore-render",
        source_identity=_source_identity(),
        project_root=PROJECT,
        staging_directory=tmp_path / "stage",
        output_directory=tmp_path / "output",
        python_path=Path(sys.executable),
        runtime=runtime,
        parent_checkpoints=entries,
        expected_state=state,
    )
    outer = builder.render_outer(bridge_sha256="e" * 64, run_id=manifest["bundle_id"])
    bridge = builder.render_bridge(
        manifest_sha256="f" * 64,
        manifest=manifest,
        runtime=runtime,
        project_root=PROJECT,
        source_identity=_source_identity(),
        python_path=Path(sys.executable),
    )
    assert outer.count("R7_BRIDGE_INVOKE_EXACTLY_ONCE") == 1
    assert bridge.count("R7_RUNNER_INVOKE_EXACTLY_ONCE") == 1
    assert outer.count("$stream.Write($bytes,0,$bytes.Length)") == 1
    assert outer.count("$stream.Flush($true)") == 1
    assert outer.count("$stream.Dispose()") == 1
    assert bridge.count("untracked_path_set_sha256=$untrackedDigest") == 1
    assert "untracked_path_digest_sha256=$untrackedDigest" not in bridge
    assert bridge.count("run_id=$PinnedRunId") == 2
    assert "launcherEvidence = [ordered]@{" in bridge
    assert outer.index("outer_sha256_mismatch_immediate") < outer.index(
        "R7_BRIDGE_INVOKE_EXACTLY_ONCE"
    )
    assert outer.index("bridge_sha256_mismatch_immediate") < outer.index(
        "R7_BRIDGE_INVOKE_EXACTLY_ONCE"
    )
    assert bridge.index("validator_sha256_mismatch_immediate") < bridge.index("& $ValidatorPath")
    runner_boundary = bridge.index("R7_RUNNER_INVOKE_EXACTLY_ONCE")
    launcher_encoded = bridge.index("$launcherBase64 = ")
    for guard in (
        "outer_sha256_mismatch_immediate_before_runner",
        "bridge_sha256_mismatch_immediate_before_runner",
        "runner_sha256_mismatch_immediate",
        "core_sha256_mismatch_immediate",
        "process_sha256_mismatch_immediate",
    ):
        assert launcher_encoded < bridge.index(guard) < runner_boundary
    assert "--mode restore-only" in bridge
    assert "--checkpoint" not in bridge
    assert "process_base" not in bridge
    assert "'fresh'" not in outer + bridge
    for old in ("phase_b2_r3.py", "phase_b2_r4.py", "phase_b2_r5.py"):
        assert old not in outer + bridge
    for name, text in (("outer", outer), ("bridge", bridge)):
        path = tmp_path / f"{name}.ps1"
        path.write_text(text, encoding="utf-8")
        command = (
            "$t=$null;$e=$null;"
            f"[void][Management.Automation.Language.Parser]::ParseFile('{path}',"
            "[ref]$t,[ref]$e);if($e.Count){$e|% ToString;exit 1}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_passed_project_root_core_is_used_and_wrong_core_rejected(tmp_path: Path) -> None:
    state, _ = _runtime_state(tmp_path)
    entries, _, _ = _parent_fixture(tmp_path, state)
    wrong_project = tmp_path / "alternate" / "enterprise-vision-mlops"
    wrong_core = wrong_project / builder.RUNTIME_PATHS["core"]
    wrong_core.parent.mkdir(parents=True)
    (wrong_core.parent / "__init__.py").write_text("", encoding="utf-8")
    (wrong_core.parents[1] / "__init__.py").write_text("", encoding="utf-8")
    wrong_core.write_text(
        f"HISTORICAL_QUERY_SHA256 = {builder.HISTORICAL_QUERY_SHA256!r}\n"
        f"HISTORICAL_DECISION_AUTHORITY = {builder.HISTORICAL_DECISION_AUTHORITY!r}\n"
        "def validate_r7_manifest(*args, **kwargs):\n"
        "    raise RuntimeError('WRONG_CORE_SENTINEL')\n",
        encoding="utf-8",
    )
    runtime = _runtime_pins(tmp_path)
    runtime["core"].update(
        {
            "path": str(wrong_core.resolve()),
            "sha256": builder.sha256_file(wrong_core),
            "bytes": wrong_core.stat().st_size,
        }
    )
    with pytest.raises(builder.BundleBuildError, match="WRONG_CORE_SENTINEL"):
        builder.build_manifest(
            run_id="x1-phase-b2-r7-wrong-core",
            source_identity=_source_identity(),
            project_root=wrong_project,
            staging_directory=tmp_path / "wrong-stage",
            output_directory=tmp_path / "wrong-output",
            python_path=Path(sys.executable),
            runtime=runtime,
            parent_checkpoints=entries,
            expected_state=state,
        )


def test_unrelated_valid_sha_proof_is_rejected(tmp_path: Path) -> None:
    state, _ = _runtime_state(tmp_path)
    job_scope = state["job_scope_contract"]
    classification = job_scope["historical_classifications"][0]  # type: ignore[index]
    attestation_path = Path(classification["attestation"]["path"])  # type: ignore[index]
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    record = attestation["records"][0]
    proof_path = tmp_path / "unrelated-valid-sha-proof.json"
    _write_json(
        proof_path,
        {
            "source": "control_plane_task_entity_statuses",
            "identity": {
                "entity_id": "different-entity",
                "created_at": "2026-08-31T00:00:00Z",
                "updated_at": "2026-08-31T01:00:00Z",
            },
            "observed_state": record["observed_state"],
            "captured_at": "2026-09-01T00:00:00Z",
            "query_sha256": builder.HISTORICAL_QUERY_SHA256["control_plane_task_entity_statuses"],
            "active_job_count": 0,
            "active_claim_count": 0,
            "active_lease_count": 0,
            "outcome_unknown_count": 0,
            "inactivity_decision": "proven_inactive",
            "decision_authority": builder.HISTORICAL_DECISION_AUTHORITY,
        },
    )
    record["execution_proof"]["evidence"] = {
        "path": str(proof_path.resolve()),
        "sha256": builder.sha256_file(proof_path),
    }
    _write_json(attestation_path, attestation)
    classification["attestation"]["sha256"] = builder.sha256_file(attestation_path)  # type: ignore[index]
    parent_root = tmp_path / "parents"
    parent_root.mkdir()
    entries, payloads, _ = _parent_fixture(parent_root, state)
    pins = tmp_path / "runtime-state-pins-unrelated.json"
    _write_json(pins, _pins_document(state, entries))
    with pytest.raises(builder.BundleBuildError, match="proof_identity_mismatch"):
        builder.validate_runtime_state_pins(
            pins,
            project_root=PROJECT,
            source_identity=_source_identity(),
            parent_entries=entries,
            parent_payloads=payloads,
        )


def test_write_exclusive_rejects_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "create-new.json"
    builder.write_exclusive(path, b"one")
    with pytest.raises(builder.BundleBuildError, match="exists"):
        builder.write_exclusive(path, b"two")
    assert path.read_bytes() == b"one"

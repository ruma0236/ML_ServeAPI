from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts.dev import prepare_x1_phase_b2_r7_bundle as builder


PROJECT = Path(__file__).parents[1]
VALIDATOR_SOURCE = PROJECT / "scripts" / "dev" / "validate_phase_b2_r7_bundle.ps1"
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")


def _run(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )


def _git(repo: Path, *args: str) -> str:
    result = _run("git", "-C", repo, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, sort_keys=True) + "\n")


@dataclass
class ValidatorFixture:
    git_root: Path
    project: Path
    branch: str
    revision: str
    tree: str
    untracked_digest: str
    runtime: dict[str, dict[str, Any]]
    parents: list[dict[str, Any]]
    expected_state: dict[str, Any]


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


def _historical_scope(evidence: Path) -> dict[str, Any]:
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
    classifications: list[dict[str, Any]] = []
    for source, records in (
        ("control_plane_task_entity_statuses", cp_records),
        ("mlflow_running_rows", mlflow_records),
        ("kubernetes_terminal_failed_objects", kubernetes_records),
    ):
        for index, record in enumerate(records):
            proof = record["execution_proof"]
            proof_path = evidence / f"{source}-proof-{index:03d}.json"
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
            proof["evidence"] = {"path": str(proof_path.resolve()), "sha256": _sha(proof_path)}
        path = evidence / f"{source}-attestation.json"
        _write_json(
            path,
            {
                "source": source,
                "captured_at": "2026-09-01T00:00:00Z",
                "query_sha256": builder.HISTORICAL_QUERY_SHA256[source],
                "counts": {
                    "observed_count": len(records),
                    "executing_count": 0,
                    "historical_count": len(records),
                    "unproven_count": 0,
                },
                "classification": "historical_nonexecuting",
                "records": records,
            },
        )
        classifications.append(
            {
                "source": source,
                "observed_count": len(records),
                "executing_count": 0,
                "historical_count": len(records),
                "unproven_count": 0,
                "classification": "historical_nonexecuting",
                "attestation": {"path": str(path.resolve()), "sha256": _sha(path)},
            }
        )
    return {
        "canonical_active_jobs": {
            "sources": ["kubernetes_job_status_active", "manifest_active_job_file_markers"],
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


@pytest.fixture(scope="module")
def validator_fixture(tmp_path_factory: pytest.TempPathFactory) -> ValidatorFixture:
    if not POWERSHELL.is_file() or shutil.which("git") is None:
        pytest.skip("PowerShell and git required")
    base = tmp_path_factory.mktemp("r7-validator")
    git_root = base / "repo"
    project = git_root / "enterprise-vision-mlops"
    remote = base / "remote.git"
    branch = "codex/r7-validator-fixture"
    project.mkdir(parents=True)
    assert _run("git", "init", "--bare", remote).returncode == 0
    assert _run("git", "init", "-b", branch, git_root).returncode == 0
    _git(git_root, "config", "user.email", "r7-validator@example.invalid")
    _git(git_root, "config", "user.name", "R7 Validator")
    _git(git_root, "config", "core.autocrlf", "false")

    paths = {
        "builder": project / "scripts" / "dev" / "prepare_x1_phase_b2_r7_bundle.py",
        "core": project / "src" / "evm" / "scale_validation" / "phase_b2_r7.py",
        "process": project / "src" / "evm" / "scale_validation" / "phase_b2_r7_process.py",
        "runner": project / "scripts" / "dev" / "run_x1_phase_b2_r7.py",
        "validator": project / "scripts" / "dev" / "validate_phase_b2_r7_bundle.ps1",
        "docker_compose": project / "docker-compose.yml",
    }
    _write(paths["builder"], (PROJECT / builder.RUNTIME_PATHS["builder"]).read_bytes())
    _write(paths["validator"], VALIDATOR_SOURCE.read_bytes())
    _write(paths["core"], (PROJECT / builder.RUNTIME_PATHS["core"]).read_bytes())
    _write(paths["process"], (PROJECT / builder.RUNTIME_PATHS["process"]).read_bytes())
    _write(paths["runner"], (PROJECT / builder.RUNTIME_PATHS["runner"]).read_bytes())
    _write(paths["docker_compose"], (PROJECT / "docker-compose.yml").read_bytes())
    _write(project / "src" / "evm" / "__init__.py", "")
    _write(project / "src" / "evm" / "scale_validation" / "__init__.py", "")
    versions = builder.source_schema_versions(PROJECT)
    _write(
        project / "src" / "evm" / "control_panel" / "transactional_store.py",
        "SCHEMA_VERSIONS = (\n" + "".join(f'    "{version}",\n' for version in versions) + ")\n",
    )
    _write(project / "src" / "evm" / "control_panel" / "__init__.py", "")

    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "fixture r7 sources")
    _git(git_root, "remote", "add", "origin", str(remote))
    _git(git_root, "push", "-u", "origin", branch)
    revision = _git(git_root, "rev-parse", "HEAD")
    tree = _git(git_root, "rev-parse", "HEAD^{tree}")
    for index in range(4_244):
        _write(git_root / "user-untracked" / f"preserved-{index:04d}.txt", "")
    untracked_count, untracked_digest = builder.untracked_identity(git_root)
    assert untracked_count == 4_244

    runtime: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        relative = path.relative_to(git_root).as_posix()
        runtime[name] = {
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "blob_oid": _git(git_root, "rev-parse", f"HEAD:{relative}"),
            "bytes": path.stat().st_size,
        }

    evidence = base / "parents"
    evidence.mkdir()
    image_id = "sha256:" + "a" * 64
    attestation = evidence / "api-image-attestation.json"
    _write_json(
        attestation,
        {"image_id": image_id, "source_revision": revision, "source_tree": tree},
    )
    services = builder.LONG_LIVED_SERVICES
    service_pins = {
        service: {
            "container_name": builder.CONTAINER_NAMES[service],
            "container_id": f"{index:x}" * 64,
            "image_id": "sha256:" + f"{(index + 1) % 16:x}" * 64,
            "healthcheck_expected": builder.HEALTHCHECK_EXPECTED[service],
        }
        for index, service in enumerate(services, start=1)
    }
    service_pins["api"]["image_id"] = image_id
    service_pins["task-queue-worker"]["image_id"] = image_id
    expected_state: dict[str, Any] = {
        "compose": {
            "project_name": "enterprise-vision-mlops",
            "config_path": str(paths["docker_compose"].resolve()),
            "config_sha256": _sha(paths["docker_compose"]),
            "long_lived_services": list(builder.LONG_LIVED_SERVICES),
            "one_shot_services": list(builder.ONE_SHOT_SERVICES),
            "service_pins": service_pins,
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
            "image_id": image_id,
            "image_attestation": {
                "path": str(attestation.resolve()),
                "sha256": _sha(attestation),
            },
            "source_revision": revision,
            "source_tree": tree,
        },
        "database": {
            "control_plane_schema_versions": versions,
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
        "job_scope_contract": _historical_scope(evidence),
    }
    parent_paths: dict[str, Path] = {}
    for role in builder.REQUIRED_PARENT_ROLES:
        parent_paths[role] = evidence / f"{role}.json"
        if role not in {"post_manual_on_readback", "post_manual_on_index"}:
            _write_json(parent_paths[role], {"role": role, "failure_only": True})
    _write_json(
        parent_paths["post_manual_on_readback"],
        {"runtime_state": expected_state},
    )
    readback_sha = _sha(parent_paths["post_manual_on_readback"])
    _write_json(
        parent_paths["post_manual_on_index"],
        {
            "files": [
                {
                    "path": str(parent_paths["post_manual_on_readback"].resolve()),
                    "sha256": readback_sha,
                }
            ]
        },
    )
    parents, _ = builder.build_parent_checkpoints(
        {role: path.resolve() for role, path in parent_paths.items()}
    )
    return ValidatorFixture(
        git_root=git_root,
        project=project,
        branch=branch,
        revision=revision,
        tree=tree,
        untracked_digest=untracked_digest,
        runtime=runtime,
        parents=parents,
        expected_state=expected_state,
    )


ManifestMutation = Callable[[dict[str, Any]], None]
TextMutation = Callable[[str], str]


def _source_identity(fixture: ValidatorFixture) -> dict[str, Any]:
    return {
        "revision": fixture.revision,
        "tree": fixture.tree,
        "branch": fixture.branch,
        "origin_revision": fixture.revision,
        "remote_revision": fixture.revision,
        "tracked": 0,
        "untracked": 4_244,
        "untracked_path_digest_sha256": fixture.untracked_digest,
    }


def _make_bundle(
    fixture: ValidatorFixture,
    tmp_path: Path,
    *,
    manifest_mutation: ManifestMutation | None = None,
    outer_mutation: TextMutation | None = None,
    bridge_mutation: TextMutation | None = None,
) -> tuple[Path, Path, Path, str]:
    stage = tmp_path / "r7-stage"
    output = tmp_path / "r7-output-never-created"
    manifest = builder.build_manifest(
        run_id="x1-phase-b2-r7-validator-fixture",
        source_identity=_source_identity(fixture),
        project_root=fixture.project,
        staging_directory=stage,
        output_directory=output,
        python_path=Path(sys.executable),
        runtime=fixture.runtime,
        parent_checkpoints=copy.deepcopy(fixture.parents),
        expected_state=copy.deepcopy(fixture.expected_state),
    )
    if manifest_mutation:
        manifest_mutation(manifest)
    stage.mkdir()
    manifest_path = stage / "phase-b2-r7-work-order.json"
    manifest_path.write_bytes(builder.canonical_json_bytes(manifest))
    bridge_path = stage / "invoke-x1-phase-b2-r7-bridge.ps1"
    bridge = builder.render_bridge(
        manifest_sha256=_sha(manifest_path),
        manifest=manifest,
        runtime=fixture.runtime,
        project_root=fixture.project,
        source_identity=_source_identity(fixture),
        python_path=Path(sys.executable),
    )
    if bridge_mutation:
        bridge = bridge_mutation(bridge)
    _write(bridge_path, bridge)
    outer_path = stage / "invoke-verified-x1-phase-b2-r7.ps1"
    outer = builder.render_outer(bridge_sha256=_sha(bridge_path), run_id=str(manifest["bundle_id"]))
    if outer_mutation:
        outer = outer_mutation(outer)
    _write(outer_path, outer)
    return manifest_path, outer_path, bridge_path, _sha(outer_path)


def _validate(
    fixture: ValidatorFixture,
    paths: tuple[Path, Path, Path, str],
) -> subprocess.CompletedProcess[str]:
    manifest, outer, bridge, outer_sha = paths
    return _run(
        POWERSHELL,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        fixture.runtime["validator"]["path"],
        "-ManifestPath",
        manifest,
        "-OuterPath",
        outer,
        "-BridgePath",
        bridge,
        "-ExpectedOuterSha256",
        outer_sha,
    )


def test_validator_accepts_exact_r7_bundle(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    result = _validate(validator_fixture, _make_bundle(validator_fixture, tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert payload["execution_mode"] == "restore-only"
    assert set(payload["observed_sha256"]) == {
        "outer",
        "bridge",
        "manifest",
        "builder",
        "core",
        "process",
        "runner",
        "validator",
        "docker_compose",
    }


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (
            lambda manifest: manifest["expected_state"]["compose"]["stability"].update(
                {"samples": 60}
            ),
            "compose_stability_exact",
        ),
        (
            lambda manifest: manifest["expected_state"]["kubernetes"].update(
                {"health_confirmation_samples": 1}
            ),
            "kubernetes_health_confirmation_samples_exact",
        ),
        (
            lambda manifest: manifest["expected_state"]["database"].update(
                {"control_plane_schema_versions": ["001_transactional_control_plane"]}
            ),
            "database_schema_versions_match_source",
        ),
        (
            lambda manifest: manifest["parent_checkpoints"].append(
                copy.deepcopy(manifest["parent_checkpoints"][0])
            ),
            "parent_checkpoint_count_exact",
        ),
        (
            lambda manifest: manifest["call_contract"]["collectors"].update(
                {"windows_fresh_collector": 1}
            ),
            "collector_call_contract_count_mismatch",
        ),
        (
            lambda manifest: manifest["repository"].update({"untracked_path_set_sha256": "f" * 64}),
            "bridge_untracked_digest_pin",
        ),
    ],
)
def test_manifest_mutations_fail_closed(
    validator_fixture: ValidatorFixture,
    tmp_path: Path,
    mutation: ManifestMutation,
    needle: str,
) -> None:
    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, manifest_mutation=mutation),
    )
    assert result.returncode != 0
    assert needle in result.stdout + result.stderr


def test_duplicate_outer_bridge_call_is_rejected(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    def duplicate(text: str) -> str:
        marker = "# R7_BRIDGE_INVOKE_EXACTLY_ONCE"
        return text.replace(marker, marker + "\n" + marker, 1)

    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, outer_mutation=duplicate),
    )
    assert result.returncode != 0
    assert "outer_exact_one_bridge_marker" in result.stdout + result.stderr


def test_outer_alias_bridge_invocation_is_rejected(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    marker = "# R7_BRIDGE_INVOKE_EXACTLY_ONCE"

    def add_alias_call(text: str) -> str:
        extra = "$bridgeAlias = $bridgePath\n& $bridgeAlias -OutputDirectory $OutputDirectory\n"
        return text.replace(marker, extra + marker, 1)

    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, outer_mutation=add_alias_call),
    )
    assert result.returncode != 0
    assert "outer_ast_exact_invocation_set" in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("replacement", "needle"),
    [
        (
            "$pythonAlias = $PythonPath\n& $pythonAlias $RunnerPath",
            "bridge_ast_exact_ampersand_target_multiset",
        ),
        (
            "& ($PythonPath) $RunnerPath",
            "bridge_ast_exact_ampersand_target_multiset",
        ),
        (
            "& $PythonPath ($RunnerPath)",
            "bridge_ast_exact_one_runner_invocation",
        ),
    ],
)
def test_runner_invocation_alias_parentheses_and_argument_indirection_are_rejected(
    validator_fixture: ValidatorFixture,
    tmp_path: Path,
    replacement: str,
    needle: str,
) -> None:
    def evade_exact_shape(text: str) -> str:
        original = "& $PythonPath $RunnerPath"
        assert text.count(original) == 1
        return text.replace(original, replacement, 1)

    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, bridge_mutation=evade_exact_shape),
    )
    assert result.returncode != 0
    assert needle in result.stdout + result.stderr


def test_launcher_evidence_run_id_must_use_manifest_pin(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    def change_launcher_run_id(text: str) -> str:
        original = "  run_id=$PinnedRunId\n  mode='restore-only'"
        assert text.count(original) == 1
        return text.replace(
            original,
            "  run_id='x1-phase-b2-r7-wrong-identity'\n  mode='restore-only'",
            1,
        )

    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, bridge_mutation=change_launcher_run_id),
    )
    assert result.returncode != 0
    assert "launcher_evidence_run_id_exact_manifest_pin" in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("target", "needle"),
    [
        (
            "outer_sha256_mismatch_immediate",
            "outer_immediate_rehash_after_reservation_before_bridge",
        ),
        (
            "validator_sha256_mismatch_immediate",
            "bridge_validator_immediate_rehash_before_validator",
        ),
        (
            "runner_sha256_mismatch_immediate",
            "bridge_runner_sha256_mismatch_immediate_at_invocation_boundary",
        ),
    ],
)
def test_invocation_boundary_sha_guard_removal_is_rejected(
    validator_fixture: ValidatorFixture,
    tmp_path: Path,
    target: str,
    needle: str,
) -> None:
    def remove_guard(text: str) -> str:
        lines = text.splitlines(keepends=True)
        matches = [index for index, line in enumerate(lines) if target in line]
        assert len(matches) == 1
        del lines[matches[0]]
        return "".join(lines)

    kwargs = (
        {"outer_mutation": remove_guard}
        if target.startswith("outer_sha256_mismatch_immediate")
        else {"bridge_mutation": remove_guard}
    )
    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, **kwargs),
    )
    assert result.returncode != 0
    assert needle in result.stdout + result.stderr


def test_old_r5_executable_reference_is_rejected(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    result = _validate(
        validator_fixture,
        _make_bundle(
            validator_fixture,
            tmp_path,
            bridge_mutation=lambda text: text
            + "\n# forbidden executable reference run_x1_phase_b2_r5.py\n",
        ),
    )
    assert result.returncode != 0
    assert "old_executable_leaf_absent_run_x1_phase_b2_r5.py" in (result.stdout + result.stderr)


def test_validator_powershell_ast_is_valid() -> None:
    command = (
        "$t=$null;$e=$null;"
        f"[void][Management.Automation.Language.Parser]::ParseFile('{VALIDATOR_SOURCE}',"
        "[ref]$t,[ref]$e);if($e.Count){$e|% ToString;exit 1}"
    )
    result = _run(POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command)
    assert result.returncode == 0, result.stdout + result.stderr

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.dev import run_x1_phase_b2_r7s1 as runner


def _canonical_git_config_bytes() -> bytes:
    return b"""[core]
\trepositoryformatversion = 0
\tfilemode = false
\tbare = false
\tlogallrefupdates = true
\tsymlinks = false
\tignorecase = true
[remote \"origin\"]
\turl = https://github.com/ruma0236/ML_ServeAPI.git
\tfetch = +refs/heads/*:refs/remotes/origin/*
[branch \"codex/local-infra-mvp\"]
\tremote = origin
\tmerge = refs/heads/codex/local-infra-mvp
[user]
\tname = redacted-test-user
\temail = redacted@example.invalid
[extensions]
\tworktreeConfig = true
[branch \"codex/mac-mini-worker\"]
\tremote = origin
\tmerge = refs/heads/codex/mac-mini-worker
[branch \"codex/distributed-scale-validation-plan\"]
\tremote = origin
\tmerge = refs/heads/codex/distributed-scale-validation-plan
[branch \"codex/x1-resume-results-20260825-215716\"]
\tremote = origin
\tmerge = refs/heads/codex/x1-resume-results-20260825-215716
    """


def _canonical_git_attributes_bytes() -> bytes:
    patterns = (
        "*.sh",
        "Makefile",
        "docs/status/evidence/*.json",
        "docs/status/*scenario-progress.json",
        "docs/status/*scenario-progress.md",
        "docs/status/*progress-ledger.jsonl",
        "docs/status/evidence/*evidence-manifest.json",
        "contracts/distributed-scale/*.json",
        "configs/s2_*.toml",
        "configs/s3_*.toml",
        "configs/s5_*.toml",
        "configs/s6_*.toml",
        "configs/s8_v4_*.toml",
        "monitoring/prometheus/prometheus.yml",
        "infra/postgres/control-plane/*.sql",
    )
    return ("\n".join(f"{pattern} text eol=lf" for pattern in patterns) + "\n").encode()


def _git_blob_oid(payload: bytes) -> str:
    return runner.hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def _docker_client_config_bytes(*, credential_store: str = "test-secret-store") -> bytes:
    return json.dumps(
        {
            "auths": {},
            "credsStore": credential_store,
            "currentContext": "desktop-linux",
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _docker_context_metadata_bytes(
    *, host: str = "npipe:////./pipe/dockerDesktopLinuxEngine"
) -> bytes:
    return json.dumps(
        {
            "Name": "desktop-linux",
            "Metadata": {"Description": "test", "GODEBUG": {}, "otel": {}},
            "Endpoints": {"docker": {"Host": host, "SkipTLSVerify": False}},
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _kubernetes_client_config_bytes(*, extra_user_line: str | None = None) -> bytes:
    lines = [
        "apiVersion: v1",
        "clusters:",
        "- cluster:",
        "    certificate-authority-data: Y2E=",
        "    server: https://kubernetes.docker.internal:6443",
        "  name: docker-desktop",
        "contexts:",
        "- context:",
        "    cluster: docker-desktop",
        "    user: docker-desktop",
        "  name: docker-desktop",
        "current-context: docker-desktop",
        "kind: Config",
        "users:",
        "- name: docker-desktop",
        "  user:",
        "    client-certificate-data: Y2VydA==",
        "    client-key-data: a2V5",
    ]
    if extra_user_line is not None:
        lines.append(extra_user_line)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _job(
    *,
    uid: str = "job-uid",
    name: str = "train-job",
    reason: str = "BackoffLimitExceeded",
) -> dict[str, Any]:
    return {
        "metadata": {"uid": uid, "namespace": "default", "name": name},
        "status": {
            "conditions": [
                {"type": "Failed", "status": "True", "reason": reason},
            ]
        },
    }


def _job_owned_pod(*, owner_uid: str = "job-uid", owner_name: str = "train-job") -> dict[str, Any]:
    return {
        "metadata": {
            "uid": "pod-uid",
            "namespace": "default",
            "name": "evm-lifecycle-train-test-abcde",
            "ownerReferences": [
                {
                    "uid": owner_uid,
                    "name": owner_name,
                    "kind": "Job",
                    "controller": True,
                }
            ],
        },
        "status": {"phase": "Failed", "reason": ""},
    }


def _open_mlflow_record() -> dict[str, Any]:
    return {
        "identity": {
            "run_id": "9bd54156084842ca93bce35a44a0cea7",
            "status": "RUNNING",
            "lifecycle_stage": "active",
            "start_time": "1783653474422",
            "end_time": "",
        },
        "observed_state": "RUNNING",
    }


def _bare_probe(*, historical_go: bool = True) -> runner.R7S1ProbeSet:
    probe = object.__new__(runner.R7S1ProbeSet)
    probe.validated_manifest = {"historical_go": historical_go}
    probe.parent_payloads = {"post_manual_on_readback": {"captured_at": "2026-09-01T00:00:00Z"}}
    return probe


def test_verified_snapshot_bootstrap_does_not_execute_package_initializers(
    tmp_path: Path,
) -> None:
    root_name = "r7s1_bootstrap_test_package"
    child_name = f"{root_name}.verified"
    package_path = tmp_path / root_name
    package_path.mkdir()
    (package_path / "__init__.py").write_text(
        "raise RuntimeError('unpinned initializer executed')\n",
        encoding="utf-8",
    )
    try:
        package = runner._install_package_shell(root_name, package_path)
        module = runner._load_module_snapshot(
            child_name,
            package_path / "verified.py",
            b"VALUE = 42\n",
        )
        assert module.VALUE == 42
        assert package.verified is module
        assert package.__file__ is None
    finally:
        runner.sys.modules.pop(child_name, None)
        runner.sys.modules.pop(root_name, None)


def _command_guard_probe(tmp_path: Path) -> runner.R7S1ProbeSet:
    probe = object.__new__(runner.R7S1ProbeSet)
    probe.repository_root = tmp_path.resolve()
    project_root = tmp_path / "enterprise-vision-mlops"
    project_root.mkdir(parents=True, exist_ok=True)
    compose_config = project_root / "docker-compose.yml"
    probe.contract = runner.TimeoutContract().validate()
    probe.expected = {
        "compose": {
            "project_name": "enterprise-vision-mlops",
            "config_path": str(compose_config),
            "long_lived_services": ["api", "worker"],
            "service_pins": {
                "api": {"container_id": "a" * 64},
                "worker": {"container_id": "b" * 64},
            },
            "stability": {"samples": 61},
        },
        "database": {
            "instances": {
                role: {
                    "container_name": f"evm-{role}-postgres",
                    "user": f"{role}_user",
                    "database": f"{role}_db",
                }
                for role in ("control_plane", "mlflow", "airflow")
            }
        },
        "api": {
            "api_container_name": "evm-api",
            "worker_container_name": "evm-worker",
            "image_id": "sha256:" + "c" * 64,
        },
        "kubernetes": {"health_confirmation_samples": 2},
        "x1_kubernetes_selectors": ["evm.run=x1-a", "evm.run=x1-b"],
        "x1_docker_name_filter": "name=evm-x1",
    }
    git_config = tmp_path / ".git" / "config"
    git_config.parent.mkdir(parents=True, exist_ok=True)
    git_config.write_bytes(_canonical_git_config_bytes())
    git_attributes = project_root / ".gitattributes"
    git_attributes.write_bytes(_canonical_git_attributes_bytes())
    docker_root = tmp_path / ".docker"
    docker_config = docker_root / "config.json"
    docker_config.parent.mkdir(parents=True, exist_ok=True)
    docker_config.write_bytes(_docker_client_config_bytes())
    context_id = runner.hashlib.sha256(b"desktop-linux").hexdigest()
    docker_context = docker_root / "contexts" / "meta" / context_id / "meta.json"
    docker_context.parent.mkdir(parents=True, exist_ok=True)
    docker_context.write_bytes(_docker_context_metadata_bytes())
    kube_config = tmp_path / ".kube" / "config"
    kube_config.parent.mkdir(parents=True, exist_ok=True)
    kube_config.write_bytes(_kubernetes_client_config_bytes())
    safe_system32 = tmp_path.parent / f"{tmp_path.name}-safe-windows" / "System32"
    safe_system32.mkdir(parents=True, exist_ok=True)
    (safe_system32 / "WindowsPowerShell" / "v1.0" / "Modules").mkdir(parents=True)
    probe.toolchain = {
        "git_repository_config": {
            "path": str(git_config.resolve()),
            "sha256": runner.hashlib.sha256(git_config.read_bytes()).hexdigest(),
            "bytes": git_config.stat().st_size,
            "policy": dict(runner.GIT_REPOSITORY_CONFIG_POLICY),
            "readback": {
                "path": str(tmp_path / "git-config-readback.json"),
                "sha256": "a" * 64,
                "schema": "s8-v4-x1-phase-b2-r7s1-git-repository-config-readback/v1",
            },
        },
        "git_repository_attributes": {
            "path": str(git_attributes.resolve()),
            "sha256": runner.hashlib.sha256(git_attributes.read_bytes()).hexdigest(),
            "bytes": git_attributes.stat().st_size,
            "policy": dict(runner.GIT_REPOSITORY_ATTRIBUTES_POLICY),
            "readback": {
                "path": str(tmp_path / "git-attributes-readback.json"),
                "sha256": "e" * 64,
                "schema": runner.GIT_ATTRIBUTES_READBACK_SCHEMA,
            },
        },
        "docker_client_config": {
            "path": str(docker_config.resolve()),
            "sha256": runner.hashlib.sha256(docker_config.read_bytes()).hexdigest(),
            "bytes": docker_config.stat().st_size,
            "context_metadata": {
                "path": str(docker_context.resolve()),
                "sha256": runner.hashlib.sha256(docker_context.read_bytes()).hexdigest(),
                "bytes": docker_context.stat().st_size,
            },
            "policy": runner.R7S1ProbeSet._dynamic_docker_policy(docker_config),
            "readback": {
                "path": str(tmp_path / "docker-config-readback.json"),
                "sha256": "b" * 64,
                "schema": "s8-v4-x1-phase-b2-r7s1-docker-client-config-readback/v1",
            },
        },
        "kubernetes_client_config": {
            "path": str(kube_config.resolve()),
            "sha256": runner.hashlib.sha256(kube_config.read_bytes()).hexdigest(),
            "bytes": kube_config.stat().st_size,
            "policy": runner.R7S1ProbeSet._dynamic_kubernetes_policy(kube_config),
            "readback": {
                "path": str(tmp_path / "kubernetes-config-readback.json"),
                "sha256": "c" * 64,
                "schema": "s8-v4-x1-phase-b2-r7s1-kubernetes-client-config-readback/v1",
            },
        },
        "windows_tcb": {"system32_path": str(safe_system32.resolve())},
        "wsl_runtime": {
            "distro": "Ubuntu",
            "python3": {"realpath": "/usr/bin/python3"},
        },
        "container_psql": {
            "realpath": "/usr/bin/psql",
            "execution_scope": dict(runner.DOCKER_CONTAINER_EXECUTION_SCOPE),
        },
    }
    runtime_paths = {
        "builder": project_root / "scripts" / "dev" / "prepare_x1_phase_b2_r7s1_bundle.py",
        "core": project_root / "src" / "evm" / "scale_validation" / "phase_b2_r7s1.py",
        "process": project_root / "src" / "evm" / "scale_validation" / "windows_job.py",
        "runner": project_root / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py",
        "validator": project_root / "scripts" / "dev" / "validate_phase_b2_r7s1_bundle.ps1",
        "docker_compose": compose_config,
    }
    runtime_pins: dict[str, dict[str, Any]] = {}
    for role, path in runtime_paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"fixture-{role}\r\n".encode()
        path.write_bytes(payload)
        normalized = payload.replace(b"\r\n", b"\n")
        runtime_pins[role] = {
            "path": str(path.resolve()),
            "sha256": runner.hashlib.sha256(payload).hexdigest(),
            "worktree_blob_oid": _git_blob_oid(payload),
            "head_blob_oid": _git_blob_oid(normalized),
            "bytes": len(payload),
        }
    probe.expected["compose"]["config_sha256"] = runtime_pins["docker_compose"]["sha256"]
    probe.manifest = {
        "runtime": runtime_pins,
        "external_terminal_fencing": {
            "successor_binding": {
                "attempt_id": "00000000-0000-4000-8000-000000000001",
            }
        },
    }
    probe._host_tool_pins = {}
    for role in (
        "python",
        "docker",
        "docker_compose",
        "kubectl",
        "wsl",
        "powershell",
        "git",
    ):
        path = tmp_path / f"{role}.exe"
        path.write_bytes(f"trusted-{role}".encode())
        pin = {
            "path": str(path.resolve()),
            "sha256": runner.hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "version": "test-version",
            "signature": {
                "status": "valid",
                "subject": "test",
                "thumbprint": "d" * 40,
            },
        }
        probe._host_tool_pins[role] = pin
        setattr(probe, role, str(path.resolve()))
    return probe


def _mlflow_attestation(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    classification = {
        "observed_count": 1,
        "executing_count": 0,
        "historical_count": 1,
        "unproven_count": 0,
        "classification": "historical_nonexecuting",
    }
    payload = {
        "source": "mlflow_running_rows",
        "captured_at": "2026-09-01T00:01:00Z",
        "query_sha256": runner.HISTORICAL_QUERY_SHA256["mlflow_running_rows"],
        "counts": {
            key: classification[key]
            for key in (
                "observed_count",
                "executing_count",
                "historical_count",
                "unproven_count",
            )
        },
        "classification": "historical_nonexecuting",
        "records": [
            {
                "identity": dict(record["identity"]),
                "observed_state": record["observed_state"],
                "classification": "historical_nonexecuting",
            }
        ],
    }
    return classification, payload


def test_job_owned_failed_pod_binds_exact_job_identity_and_condition_reason() -> None:
    job = _job()
    jobs = runner.R7S1ProbeSet._job_owner_index([job])

    identity = runner.R7S1ProbeSet._failed_pod_identity(_job_owned_pod(), jobs)

    assert identity == {
        "uid": "pod-uid",
        "namespace": "default",
        "name": "evm-lifecycle-train-test-abcde",
        "reason": "BackoffLimitExceeded",
        "reason_source": "owner_job.status.conditions[type=Failed].reason",
        "owner_uid": "job-uid",
        "owner_kind": "Job",
        "owner_name": "train-job",
        "owner_controller": True,
    }


def test_failed_pod_owner_uid_or_name_drift_fails_closed() -> None:
    jobs = runner.R7S1ProbeSet._job_owner_index([_job()])

    with pytest.raises(ValueError, match="owner_job_identity_mismatch"):
        runner.R7S1ProbeSet._failed_pod_identity(
            _job_owned_pod(owner_uid="different-job-uid"), jobs
        )
    with pytest.raises(ValueError, match="owner_job_identity_mismatch"):
        runner.R7S1ProbeSet._failed_pod_identity(
            _job_owned_pod(owner_name="different-job-name"), jobs
        )


@pytest.mark.parametrize(
    "conditions",
    [
        [{"type": "Failed", "status": "True", "reason": ""}],
        [
            {"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"},
            {"type": "Failed", "status": "True", "reason": "DeadlineExceeded"},
        ],
    ],
)
def test_failed_owner_job_empty_or_ambiguous_reason_fails_closed(
    conditions: list[dict[str, str]],
) -> None:
    job = _job()
    job["status"]["conditions"] = conditions
    jobs = runner.R7S1ProbeSet._job_owner_index([job])

    with pytest.raises(ValueError, match="failed_owner_job"):
        runner.R7S1ProbeSet._failed_pod_identity(_job_owned_pod(), jobs)


def test_b0_failed_pod_uses_nonempty_pod_status_reason() -> None:
    pod = {
        "metadata": {
            "uid": "b0-pod-uid",
            "namespace": "evm-production",
            "name": "evm-b0-production-deadbeef-abcde",
            "ownerReferences": [
                {
                    "uid": "replicaset-uid",
                    "name": "evm-b0-production-deadbeef",
                    "kind": "ReplicaSet",
                    "controller": True,
                }
            ],
        },
        "status": {"phase": "Failed", "reason": "UnexpectedAdmissionError"},
    }

    identity = runner.R7S1ProbeSet._failed_pod_identity(pod, {})

    assert identity["reason"] == "UnexpectedAdmissionError"
    assert identity["reason_source"] == "pod.status.reason"


def test_b0_failed_pod_rejects_legacy_or_wrong_namespace_identity() -> None:
    pod = {
        "metadata": {
            "uid": "b0-pod-uid",
            "namespace": "default",
            "name": "evm-efficientnet-b0-deadbeef-abcde",
            "ownerReferences": [
                {
                    "uid": "replicaset-uid",
                    "name": "evm-efficientnet-b0-deadbeef",
                    "kind": "ReplicaSet",
                    "controller": True,
                }
            ],
        },
        "status": {"phase": "Failed", "reason": "UnexpectedAdmissionError"},
    }

    with pytest.raises(ValueError, match="owner_kind_or_b0_identity_invalid"):
        runner.R7S1ProbeSet._failed_pod_identity(pod, {})


def test_open_mlflow_row_without_external_decision_is_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _bare_probe()
    monkeypatch.setattr(runner, "find_verified_decision", lambda *_args: None)

    exact, decisions = probe._mlflow_terminal_fencing_exact([_open_mlflow_record()])

    assert exact is False
    assert decisions[0]["terminal_fencing_required"] is True
    assert decisions[0]["verified"] is False


def test_running_mlflow_row_with_nonempty_end_time_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _bare_probe()
    record = _open_mlflow_record()
    record["identity"]["end_time"] = "1783653475809"
    monkeypatch.setattr(
        runner,
        "find_verified_decision",
        lambda *_args: {
            "source": "mlflow_running_rows",
            "identity": dict(record["identity"]),
            "decision": "proven_terminal_fenced",
            "verified": True,
        },
    )

    exact, decisions = probe._mlflow_terminal_fencing_exact([record])

    assert exact is False
    assert decisions[0]["terminal_fencing_required"] is False
    assert decisions[0]["verified"] is False


def test_open_mlflow_row_rejects_decision_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _bare_probe()
    record = _open_mlflow_record()
    drifted = dict(record["identity"])
    drifted["run_id"] = "different-run"
    monkeypatch.setattr(
        runner,
        "find_verified_decision",
        lambda *_args: {
            "source": "mlflow_running_rows",
            "identity": drifted,
            "decision": "proven_terminal_fenced",
            "verified": True,
        },
    )

    exact, _ = probe._mlflow_terminal_fencing_exact([record])

    assert exact is False


def test_open_mlflow_row_accepts_only_core_verified_terminal_fencing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _bare_probe()
    record = _open_mlflow_record()
    monkeypatch.setattr(
        runner,
        "find_verified_decision",
        lambda *_args: {
            "source": "mlflow_running_rows",
            "identity": dict(record["identity"]),
            "decision": "proven_terminal_fenced",
            "authority": "phase-b2-r7s1-independent-terminal-review",
            "verified": True,
        },
    )

    exact, decisions = probe._mlflow_terminal_fencing_exact([record])

    assert exact is True
    assert decisions[0]["end_time_empty"] is True
    assert decisions[0]["verified"] is True


def test_staleness_or_zero_links_cannot_override_core_historical_no_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _bare_probe(historical_go=False)
    record = _open_mlflow_record()
    monkeypatch.setattr(
        runner,
        "find_verified_decision",
        lambda *_args: {
            "source": "mlflow_running_rows",
            "identity": dict(record["identity"]),
            "decision": "proven_terminal_fenced",
            "verified": True,
        },
    )

    exact, _ = probe._mlflow_terminal_fencing_exact([record])

    assert exact is False


def test_mlflow_attestation_is_exactly_bound_to_live_row_without_legacy_proof() -> None:
    probe = _bare_probe()
    record = _open_mlflow_record()
    classification, payload = _mlflow_attestation(record)

    assert probe._mlflow_attestation_live_exact(
        classification=classification,
        payload=payload,
        observed_records=[record],
        file_sha_exact=True,
        attestation_path=Path("attestation.json"),
    )


@pytest.mark.parametrize("mutation", ["legacy_execution_proof", "status_drift"])
def test_mlflow_attestation_shape_or_identity_drift_fails_closed(mutation: str) -> None:
    probe = _bare_probe()
    record = _open_mlflow_record()
    classification, payload = _mlflow_attestation(record)
    if mutation == "legacy_execution_proof":
        payload["records"][0]["execution_proof"] = {"inactivity_proven": True}
    else:
        payload["records"][0]["identity"]["status"] = "FINISHED"

    assert not probe._mlflow_attestation_live_exact(
        classification=classification,
        payload=payload,
        observed_records=[record],
        file_sha_exact=True,
        attestation_path=Path("attestation.json"),
    )


def test_r7s1_identity_is_distinct_and_process_containment_stays_r7() -> None:
    assert runner.MANIFEST_LEAF == "phase-b2-r7s1-work-order.json"
    assert runner.OUTER_RESERVATION == "r7s1-outer-invocation-reservation.json"
    assert runner.BRIDGE_RESERVATION == "r7s1-bridge-invocation-reservation.json"
    assert runner.RUNNER_RESERVATION == "r7s1-runner-invocation-reservation.json"
    assert runner.RUNNER_INVOKE_MARKER == "R7S1_RUNNER_INVOKE_EXACTLY_ONCE"
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "from evm.scale_validation.phase_b2_r7s1 import" in source
    assert "from evm.scale_validation.phase_b2_r7_process import" in source
    assert "TerminateJobObject" not in source
    assert "taskkill" not in source


def test_read_only_command_allowlist_accepts_exact_role_and_argv(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    allowed = {
        "r7s1-git-tracked-readback": probe._git_command(
            "status", "--porcelain=v1", "--untracked-files=no"
        ),
        "r7s1-docker-engine-readback": probe._docker_command(
            "version", "--format", "{{json .Server}}"
        ),
        "r7s1-compose-ps-initial": probe._compose_command("ps", "-a", "--format", "json"),
        "r7s1-kubernetes-node-readback": probe._kubectl_command("get", "nodes", "-o", "json"),
        "r7s1-windows-global-residual-readback": [
            probe.powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            probe._windows_residual_script(),
        ],
        "r7s1-wsl-global-residual-readback": probe._wsl_protocol_launch_command(
            probe._wsl_protocol()
        ),
    }

    assert {
        probe._validate_read_only_command(command, name=name) for name, command in allowed.items()
    } == {"git", "docker", "docker_compose", "kubectl", "powershell", "wsl"}


def test_compose_config_uses_pinned_project_subdir_below_git_top_level(tmp_path: Path) -> None:
    git_root = tmp_path / "git-top-level"
    project_root = git_root / "enterprise-vision-mlops"
    project_root.mkdir(parents=True, exist_ok=True)
    probe = _command_guard_probe(git_root)
    config_path = project_root / "docker-compose.yml"
    config_path.write_text("services: {}\n", encoding="utf-8")
    config_sha256 = runner.sha256_file(config_path)
    compose = probe.expected["compose"]
    compose["config_path"] = str(config_path)
    compose["config_sha256"] = config_sha256
    compose_pin = probe.manifest["runtime"]["docker_compose"]
    payload = config_path.read_bytes()
    compose_pin.update(
        {
            "path": str(config_path),
            "sha256": config_sha256,
            "worktree_blob_oid": _git_blob_oid(payload),
            "head_blob_oid": _git_blob_oid(payload.replace(b"\r\n", b"\n")),
            "bytes": len(payload),
        }
    )

    assert probe.repository_root == git_root.resolve()
    assert not (probe.repository_root / "docker-compose.yml").exists()
    assert probe._compose_config_identity_exact(compose) is True

    probe.manifest["runtime"]["docker_compose"]["path"] = str(
        probe.repository_root / "docker-compose.yml"
    )
    assert probe._compose_config_identity_exact(compose) is False


def _successful_process_result(stdout: str) -> dict[str, Any]:
    return {
        "passed": True,
        "last_error": None,
        "manual_intervention_required": False,
        "residual_pids": [],
        "process_evidence": {"forced_termination_attempts": 0},
        "stdout": stdout,
        "stderr": "",
    }


def test_wsl_global_scan_uses_uuid_setsid_and_process_group_post_scan(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    protocol = probe._wsl_protocol()
    root = {
        "pid": 410,
        "ppid": 409,
        "pgrp": 410,
        "session": 410,
        "start_time_ticks": 9001,
        "boot_id": "11111111-1111-4111-8111-111111111111",
    }
    launch_payload = {
        "schema": "s8-v4-x1-phase-b2-r7s1-wsl-global-residual-readback/v2",
        "run_uuid": protocol.run_uuid,
        "root": root,
        "residuals": [],
    }
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(_deadline: Any, command: Any, *, name: str) -> dict[str, Any]:
        calls.append((name, tuple(command)))
        if name == "r7s1-wsl-global-residual-readback":
            return _successful_process_result(json.dumps(launch_payload))
        assert name == "r7s1-wsl-run-uuid-residual-scan"
        return _successful_process_result("[]")

    probe._run = fake_run
    result, residuals = probe._global_wsl_residuals(runner.RestoreDeadline(600))

    assert result["passed"] is True
    assert residuals == []
    assert [name for name, _command in calls] == [
        "r7s1-wsl-global-residual-readback",
        "r7s1-wsl-run-uuid-residual-scan",
    ]
    launch_command = calls[0][1]
    assert launch_command[:5] == (
        probe.wsl,
        "--distribution",
        "Ubuntu",
        "--exec",
        "env",
    )
    assert f"EVM_PHASE_B2_RUN_UUID={protocol.run_uuid}" in launch_command
    assert launch_command[6:9] == ("setsid", "--fork", "--wait")
    scan_command = calls[1][1]
    assert protocol.run_uuid in scan_command
    assert str(root["pgrp"]) in scan_command
    assert str(root["start_time_ticks"]) in scan_command
    assert root["boot_id"] in scan_command
    probe._pending_wsl_post_scan_command = scan_command
    try:
        assert (
            probe._validate_read_only_command(scan_command, name="r7s1-wsl-run-uuid-residual-scan")
            == "wsl"
        )
        with pytest.raises(runner.ReadOnlyCommandPolicyError, match="not_allowlisted"):
            probe._validate_read_only_command(
                [*scan_command, "unexpected"],
                name="r7s1-wsl-run-uuid-residual-scan",
            )
    finally:
        probe._pending_wsl_post_scan_command = None
    scope = result["process_evidence"]
    assert scope["scope"] == "wsl_uuid_process_group"
    assert scope["run_uuid"] == protocol.run_uuid
    assert scope["root"] == root
    assert scope["linux_residual_zero"] is True
    assert scope["subsequent_probe_after_residual"] == 0


def test_wsl_launcher_residual_forbids_post_scan_and_followup(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    calls: list[str] = []

    def fake_run(_deadline: Any, _command: Any, *, name: str) -> dict[str, Any]:
        calls.append(name)
        return {
            "passed": False,
            "last_error": "timeout_residual",
            "manual_intervention_required": True,
            "residual_pids": [4242],
            "process_evidence": {
                "forced_termination_attempts": 0,
                "residual_pids": [4242],
            },
            "stdout": "",
            "stderr": "",
        }

    probe._run = fake_run
    result, residuals = probe._global_wsl_residuals(runner.RestoreDeadline(600))

    assert result["passed"] is False
    assert result["manual_intervention_required"] is True
    assert result["residual_pids"] == [4242]
    assert residuals == []
    assert calls == ["r7s1-wsl-global-residual-readback"]
    assert result["process_evidence"]["post_scan"] is None
    assert result["process_evidence"]["subsequent_probe_after_residual"] == 0


def test_wsl_protocol_residual_is_manual_and_no_probe_follows_scan(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    protocol = probe._wsl_protocol()
    root = {
        "pid": 510,
        "ppid": 509,
        "pgrp": 510,
        "session": 510,
        "start_time_ticks": 10001,
        "boot_id": "22222222-2222-4222-8222-222222222222",
    }
    launch_payload = {
        "schema": "s8-v4-x1-phase-b2-r7s1-wsl-global-residual-readback/v2",
        "run_uuid": protocol.run_uuid,
        "root": root,
        "residuals": [],
    }
    escaped = {
        "pid": 777,
        "ppid": 1,
        "pgrp": 510,
        "session": 510,
        "start_time_ticks": 10002,
        "boot_id": root["boot_id"],
        "run_uuid_match": True,
        "process_group_match": True,
        "cmdline_sha256": "e" * 64,
    }
    calls: list[str] = []

    def fake_run(_deadline: Any, _command: Any, *, name: str) -> dict[str, Any]:
        calls.append(name)
        if len(calls) == 1:
            return _successful_process_result(json.dumps(launch_payload))
        return _successful_process_result(json.dumps([escaped]))

    probe._run = fake_run
    result, residuals = probe._global_wsl_residuals(runner.RestoreDeadline(600))

    assert result["passed"] is False
    assert result["manual_intervention_required"] is True
    assert result["process_evidence"]["linux_residual_zero"] is False
    assert result["process_evidence"]["post_scan_records"] == [escaped]
    assert residuals == [{"protocol_residual": escaped}]
    assert calls == [
        "r7s1-wsl-global-residual-readback",
        "r7s1-wsl-run-uuid-residual-scan",
    ]


def test_docker_psql_result_marks_daemon_container_exec_tcb(tmp_path: Path) -> None:
    class Outcome:
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0
        stdout = "0|0|0|0|0"
        stderr = ""

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {"forced_termination_attempts": 0, "residual_pids": []}

    class ProcessRunner:
        @staticmethod
        def run(*_args: Any, **_kwargs: Any) -> Outcome:
            return Outcome()

    probe = _command_guard_probe(tmp_path)
    probe.runner = ProcessRunner()
    command = probe._psql_command(
        role="control_plane",
        query=runner.QUEUE_READBACK_QUERY,
        field_separator=True,
    )
    assert "-X" in command
    with pytest.raises(runner.ReadOnlyCommandPolicyError, match="not_allowlisted"):
        probe._validate_read_only_command(
            [part for part in command if part != "-X"],
            name="r7s1-queue-claims-readback",
        )
    result = probe._run(runner.RestoreDeadline(600), command, name="r7s1-queue-claims-readback")

    assert result["passed"] is True
    assert result["execution_scope"] == runner.DOCKER_CONTAINER_EXECUTION_SCOPE
    assert result["process_evidence"]["execution_scope"] == runner.DOCKER_CONTAINER_EXECUTION_SCOPE
    assert result["execution_scope"]["linux_container_descendants_job_accounted"] is False


def test_client_configs_commands_and_minimal_environments_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Outcome:
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0
        stdout = "{}"
        stderr = ""

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {"forced_termination_attempts": 0, "residual_pids": []}

    class RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run(self, command: Any, **kwargs: Any) -> Outcome:
            self.calls.append({"command": list(command), **kwargs})
            return Outcome()

    for name in (
        "DOCKER_HOST",
        "docker_context",
        "DOCKER_CONFIG",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "HTTP_PROXY",
        "https_proxy",
        "SSL_CERT_FILE",
        "KUBECONFIG",
        "KUBE_EDITOR",
        "SSH_ASKPASS",
        "GIT_ASKPASS",
    ):
        monkeypatch.setenv(name, f"attacker-{name}")
    probe = _command_guard_probe(tmp_path)
    recording = RecordingRunner()
    probe.runner = recording

    docker_command = probe._docker_command("version", "--format", "{{json .Server}}")
    compose_command = probe._compose_command("ps", "-a", "--format", "json")
    kubectl_command = probe._kubectl_command("get", "nodes", "-o", "json")
    docker_result = probe._run(
        runner.RestoreDeadline(600), docker_command, name="r7s1-docker-engine-readback"
    )
    compose_result = probe._run(
        runner.RestoreDeadline(600), compose_command, name="r7s1-compose-ps-initial"
    )
    kubectl_result = probe._run(
        runner.RestoreDeadline(600), kubectl_command, name="r7s1-kubernetes-node-readback"
    )

    docker_config = Path(probe.toolchain["docker_client_config"]["path"])
    assert docker_command[1:5] == [
        "--config",
        str(docker_config.parent.resolve()),
        "--context",
        "desktop-linux",
    ]
    assert compose_command[1:7] == [
        "-p",
        "enterprise-vision-mlops",
        "-f",
        str((tmp_path / "enterprise-vision-mlops" / "docker-compose.yml").resolve()),
        "--project-directory",
        str((tmp_path / "enterprise-vision-mlops").resolve()),
    ]
    kube_config = Path(probe.toolchain["kubernetes_client_config"]["path"])
    assert kubectl_command[1:7] == [
        "--kubeconfig",
        str(kube_config.resolve()),
        "--context",
        "docker-desktop",
        "--request-timeout=8s",
        "get",
    ]
    docker_env = recording.calls[0]["env"]
    compose_env = recording.calls[1]["env"]
    kube_env = recording.calls[2]["env"]
    assert docker_env == compose_env
    assert docker_env["DOCKER_CONFIG"] == str(docker_config.parent.resolve())
    assert docker_env["DOCKER_CONTEXT"] == "desktop-linux"
    assert docker_env["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert kube_env["KUBECONFIG"] == str(kube_config.resolve())
    for environment in (docker_env, compose_env, kube_env):
        assert set(environment).issuperset({"SystemRoot", "WINDIR"})
        assert not any(value.startswith("attacker-") for value in environment.values())
    assert docker_result["client_command_policy"]["credential_store_invocation_count"] == 0
    assert docker_result["client_command_policy"]["registry_operations_allowed"] is False
    assert compose_result["client_command_policy"]["credential_store_invocation_count"] == 0
    assert kubectl_result["client_command_policy"]["external_auth_helper_allowed"] is False
    serialized = json.dumps(
        [
            docker_result["client_configuration"],
            compose_result["client_configuration"],
            kubectl_result["client_configuration"],
        ],
        sort_keys=True,
    )
    assert "test-secret-store" not in serialized
    assert "Y2E=" not in serialized
    assert "Y2VydA==" not in serialized
    assert "a2V5" not in serialized


def test_powershell_and_wsl_residual_children_reject_ambient_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Outcome:
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0
        stdout = "[]"
        stderr = ""

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {"forced_termination_attempts": 0, "residual_pids": []}

    class RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run(self, command: Any, **kwargs: Any) -> Outcome:
            self.calls.append({"command": list(command), **kwargs})
            return Outcome()

    for name in (
        "PSModulePath",
        "POWERSHELL_DISTRIBUTION_CHANNEL",
        "WSLENV",
        "WSL_DISTRO_NAME",
        "WSL_INTEROP",
        "PATH",
        "HOME",
        "USERPROFILE",
    ):
        monkeypatch.setenv(name, f"attacker-{name}")
    probe = _command_guard_probe(tmp_path)
    recording = RecordingRunner()
    probe.runner = recording
    powershell_command = [
        probe.powershell,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        probe._windows_residual_script(),
    ]
    wsl_command = probe._wsl_protocol_launch_command(probe._wsl_protocol())

    powershell_result = probe._run(
        runner.RestoreDeadline(600),
        powershell_command,
        name="r7s1-windows-global-residual-readback",
    )
    wsl_result = probe._run(
        runner.RestoreDeadline(600),
        wsl_command,
        name="r7s1-wsl-global-residual-readback",
    )

    powershell_env = recording.calls[0]["env"]
    wsl_env = recording.calls[1]["env"]
    assert set(powershell_env) == {
        "SystemRoot",
        "WINDIR",
        "PSModulePath",
        "POWERSHELL_TELEMETRY_OPTOUT",
    }
    assert powershell_env["PSModulePath"].endswith("System32\\WindowsPowerShell\\v1.0\\Modules")
    assert set(wsl_env) == {"SystemRoot", "WINDIR", "WSL_UTF8"}
    assert "WSLENV" not in wsl_env
    assert "CimCmdlets\\Get-CimInstance" in powershell_command[-1]
    assert "Microsoft.PowerShell.Utility\\ConvertTo-Json" in powershell_command[-1]
    assert powershell_result["ambient_environment_policy"] == {
        "scope": "windows_powershell_read_only_residual_scan",
        "inherited_environment": False,
        "profile_loading_allowed": False,
        "system_module_path_only": True,
        "module_qualified_commands": True,
        "os_module_distribution_tcb": True,
    }
    assert wsl_result["ambient_environment_policy"]["wslenv_present"] is False
    assert wsl_result["ambient_environment_policy"]["wsl_registration_kernel_rootfs_tcb"] is True


@pytest.mark.parametrize("mutation", ["config", "context", "tls"])
def test_docker_client_state_is_rechecked_before_every_child_and_stops_chain(
    tmp_path: Path, mutation: str
) -> None:
    class Outcome:
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0
        stdout = "{}"
        stderr = ""

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {"forced_termination_attempts": 0, "residual_pids": []}

    probe = _command_guard_probe(tmp_path)
    config = Path(probe.toolchain["docker_client_config"]["path"])
    context = Path(probe.toolchain["docker_client_config"]["context_metadata"]["path"])
    context_id = runner.hashlib.sha256(b"desktop-linux").hexdigest()
    tls_path = config.parent / "contexts" / "tls" / context_id
    calls: list[str] = []

    class MutatingRunner:
        @staticmethod
        def run(_command: Any, *, name: str, **_kwargs: Any) -> Outcome:
            calls.append(name)
            if mutation == "config":
                config.write_bytes(config.read_bytes() + b" ")
            elif mutation == "context":
                context.write_bytes(context.read_bytes() + b" ")
            else:
                tls_path.mkdir(parents=True)
            return Outcome()

    probe.runner = MutatingRunner()
    first = probe._run(
        runner.RestoreDeadline(600),
        probe._docker_command("version", "--format", "{{json .Server}}"),
        name="r7s1-docker-engine-readback",
    )
    second = probe._run(
        runner.RestoreDeadline(600),
        probe._docker_command(
            "ps",
            "-a",
            "--filter",
            str(probe.expected["x1_docker_name_filter"]),
            "--format",
            "{{json .}}",
        ),
        name="r7s1-x1-docker-residue-readback",
    )

    assert first["passed"] is True
    assert second["passed"] is False
    assert second["manual_intervention_required"] is True
    assert second["process_evidence"]["child_created"] is False
    assert calls == ["r7s1-docker-engine-readback"]


@pytest.mark.parametrize("mutation", ["auth", "endpoint"])
def test_self_consistent_docker_config_repin_rejects_unsafe_projection(
    tmp_path: Path, mutation: str
) -> None:
    probe = _command_guard_probe(tmp_path)
    if mutation == "auth":
        path = Path(probe.toolchain["docker_client_config"]["path"])
        path.write_bytes(
            json.dumps(
                {
                    "auths": {"attacker.invalid": {"auth": "secret"}},
                    "credsStore": "test-secret-store",
                    "currentContext": "desktop-linux",
                },
                separators=(",", ":"),
            ).encode()
        )
        pin = probe.toolchain["docker_client_config"]
    else:
        path = Path(probe.toolchain["docker_client_config"]["context_metadata"]["path"])
        path.write_bytes(_docker_context_metadata_bytes(host="npipe:////./pipe/attacker"))
        pin = probe.toolchain["docker_client_config"]["context_metadata"]
    pin["sha256"] = runner.hashlib.sha256(path.read_bytes()).hexdigest()
    pin["bytes"] = path.stat().st_size

    with pytest.raises(
        runner.R7S1RunnerError,
        match="auths_must_be_empty|endpoint_identity_mismatch",
    ):
        probe._verify_docker_client_config()


def test_kubernetes_config_is_rechecked_and_forbidden_helper_repin_is_rejected(
    tmp_path: Path,
) -> None:
    class Outcome:
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0
        stdout = "{}"
        stderr = ""

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {"forced_termination_attempts": 0, "residual_pids": []}

    probe = _command_guard_probe(tmp_path)
    config = Path(probe.toolchain["kubernetes_client_config"]["path"])
    calls: list[str] = []

    class MutatingRunner:
        @staticmethod
        def run(_command: Any, *, name: str, **_kwargs: Any) -> Outcome:
            calls.append(name)
            config.write_bytes(config.read_bytes() + b"# changed\n")
            return Outcome()

    probe.runner = MutatingRunner()
    first = probe._run(
        runner.RestoreDeadline(600),
        probe._kubectl_command("get", "nodes", "-o", "json"),
        name="r7s1-kubernetes-node-readback",
    )
    second = probe._run(
        runner.RestoreDeadline(600),
        probe._kubectl_command("get", "jobs", "-A", "-o", "json"),
        name="r7s1-active-jobs-readback",
    )
    assert first["passed"] is True
    assert second["passed"] is False
    assert second["process_evidence"]["child_created"] is False
    assert calls == ["r7s1-kubernetes-node-readback"]

    config.write_bytes(_kubernetes_client_config_bytes(extra_user_line="    exec: attacker"))
    pin = probe.toolchain["kubernetes_client_config"]
    pin["sha256"] = runner.hashlib.sha256(config.read_bytes()).hexdigest()
    pin["bytes"] = config.stat().st_size
    with pytest.raises(
        runner.R7S1RunnerError,
        match="structure_mismatch|forbidden_indirection",
    ):
        probe._verify_kubernetes_client_config()


@pytest.mark.parametrize(
    ("name", "role", "arguments"),
    [
        ("r7s1-docker-engine-readback", "docker", ["restart", "evm-api"]),
        (
            "r7s1-compose-ps-initial",
            "docker_compose",
            ["-p", "enterprise-vision-mlops", "stop"],
        ),
        ("r7s1-x1-docker-residue-readback", "docker", ["rm", "-f", "evm-api"]),
        (
            "r7s1-queue-claims-readback",
            "docker",
            ["exec", "evm-control_plane-postgres", "sh", "-c", "echo unsafe"],
        ),
        ("r7s1-kubernetes-node-readback", "kubectl", ["delete", "pod", "evm-api"]),
        ("r7s1-kubernetes-node-readback", "kubectl", ["apply", "-f", "payload.yml"]),
        ("r7s1-wsl-global-residual-readback", "wsl", ["--exec", "sh", "-c", "reboot"]),
        (
            "r7s1-windows-global-residual-readback",
            "powershell",
            ["-Command", "Stop-Process -Force -Id 1234"],
        ),
    ],
)
def test_read_only_command_allowlist_rejects_lifecycle_delete_and_shell_wrappers(
    tmp_path: Path,
    name: str,
    role: str,
    arguments: list[str],
) -> None:
    probe = _command_guard_probe(tmp_path)

    with pytest.raises(runner.ReadOnlyCommandPolicyError, match="not_allowlisted"):
        probe._validate_read_only_command([getattr(probe, role), *arguments], name=name)


def test_read_only_command_allowlist_rejects_untrusted_executable_and_name_swap(
    tmp_path: Path,
) -> None:
    probe = _command_guard_probe(tmp_path)
    exact_docker = [probe.docker, "version", "--format", "{{json .Server}}"]

    with pytest.raises(runner.ReadOnlyCommandPolicyError, match="untrusted_executable"):
        probe._validate_read_only_command(
            [str(tmp_path / "cmd.exe"), "/c", *exact_docker],
            name="r7s1-docker-engine-readback",
        )
    with pytest.raises(runner.ReadOnlyCommandPolicyError, match="not_allowlisted"):
        probe._validate_read_only_command(exact_docker, name="r7s1-compose-ps-initial")

    legacy_plugin_discovery = [
        probe.docker,
        "compose",
        "-p",
        "enterprise-vision-mlops",
        "-f",
        str(tmp_path / "docker-compose.yml"),
        "ps",
        "-a",
        "--format",
        "json",
    ]
    with pytest.raises(runner.ReadOnlyCommandPolicyError, match="not_allowlisted"):
        probe._validate_read_only_command(
            legacy_plugin_discovery,
            name="r7s1-compose-ps-initial",
        )

    direct = probe._compose_command("ps", "-a", "--format", "json")
    assert direct[0] == probe.docker_compose
    assert "compose" not in direct[1:]


def test_runtime_hash_object_commands_require_exact_normalization_path_and_role(
    tmp_path: Path,
) -> None:
    probe = _command_guard_probe(tmp_path)
    for role in runner.RUNTIME_COMPONENTS:
        name = probe._runtime_hash_name(role)
        command = probe._runtime_hash_object_command(role)
        component = probe.manifest["runtime"][role]
        relative = Path(component["path"]).resolve().relative_to(tmp_path.resolve()).as_posix()
        assert command[1:7] == [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.autocrlf=true",
            "-C",
            str(tmp_path.resolve()),
        ]
        assert command[-3:] == ["hash-object", f"--path={relative}", component["path"]]
        assert probe._validate_read_only_command(command, name=name) == "git"

        for mutation in (
            [part for part in command if not part.startswith("--path=")],
            [*command[:-1], relative],
            [*command[:4], "core.autocrlf=false", *command[5:]],
        ):
            with pytest.raises(runner.ReadOnlyCommandPolicyError, match="not_allowlisted"):
                probe._validate_read_only_command(mutation, name=name)

    with pytest.raises(runner.ReadOnlyCommandPolicyError, match="not_allowlisted"):
        probe._validate_read_only_command(
            probe._runtime_hash_object_command("builder"),
            name=probe._runtime_hash_name("runner"),
        )


def test_rejected_command_never_reaches_job_runner(tmp_path: Path) -> None:
    class NeverCalledRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def run(self, *args: Any, **kwargs: Any) -> Any:
            self.calls.append((*args, kwargs))
            raise AssertionError("process runner must not be called")

    probe = _command_guard_probe(tmp_path)
    process_runner = NeverCalledRunner()
    probe.runner = process_runner
    result = probe._run(
        runner.RestoreDeadline(600),
        [probe.docker_compose, "down", "-v"],
        name="r7s1-compose-ps-initial",
    )

    assert result["passed"] is False
    assert result["manual_intervention_required"] is True
    assert result["residual_status"] == "not_created"
    assert result["process_evidence"]["forced_termination_attempts"] == 0
    assert result["process_evidence"]["command"] == "redacted_by_read_only_command_policy"
    assert process_runner.calls == []


def test_contained_git_readback_precedes_operational_docker_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SuccessfulOutcome:
        def __init__(self, name: str, stdout: str) -> None:
            self.name = name
            self.stdout = stdout
            self.stderr = ""
            self.return_code = 0
            self.residual_pids: tuple[int, ...] = ()
            self.timed_out = False
            self.cancelled = False
            self.manual_intervention_required = False
            self.active_process_zero = True
            self.streams_drained = True
            self.identity_coverage_complete = True
            self.forced_termination_attempts = 0

        def to_dict(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "child_created": True,
                "residual_pids": [],
                "active_process_zero": True,
                "streams_drained": True,
                "identity_coverage_complete": True,
                "forced_termination_attempts": 0,
            }

    revision = "1" * 40
    tree = "2" * 40
    untracked_paths = ["z-last.txt", "a-first.txt"]
    digest = runner.hashlib.sha256()
    for path in sorted(untracked_paths):
        digest.update(path.encode())
        digest.update(b"\0")
    outputs = {
        "r7s1-git-branch-readback": runner.CANONICAL_BRANCH + "\n",
        "r7s1-git-local-revision-readback": revision + "\n",
        "r7s1-git-origin-revision-readback": revision + "\n",
        "r7s1-git-remote-revision-readback": (
            f"{revision}\trefs/heads/{runner.CANONICAL_BRANCH}\n"
        ),
        "r7s1-git-tree-readback": tree + "\n",
        "r7s1-git-tracked-readback": "",
        "r7s1-git-untracked-readback": "\0".join(f"?? {path}" for path in untracked_paths) + "\0",
        "r7s1-docker-engine-readback": '{"Version":"test"}',
    }

    class RecordingRunner:
        def __init__(self) -> None:
            self.names: list[str] = []

        def run(
            self,
            command: Any,
            *,
            name: str,
            cwd: Path,
            env: dict[str, str] | None,
        ) -> SuccessfulOutcome:
            if name.startswith("r7s1-git-"):
                assert command[1:3] == ["-c", "core.fsmonitor=false"]
                assert env is not None
                expected_cwd = (
                    Path(probe.toolchain["windows_tcb"]["system32_path"]).parent.resolve()
                    if name == "r7s1-git-remote-revision-readback"
                    else tmp_path.resolve()
                )
                assert cwd == expected_cwd
                assert env["GIT_TERMINAL_PROMPT"] == "0"
                assert env["GIT_OPTIONAL_LOCKS"] == "0"
                assert env["GIT_CONFIG_NOSYSTEM"] == "1"
                assert env["GIT_CONFIG_GLOBAL"] == "NUL"
                assert env["GIT_ATTR_NOSYSTEM"] == "1"
                assert env["GCM_INTERACTIVE"] == "never"
                assert "GIT_DIR" not in env
                assert "GIT_EXEC_PATH" not in env
                assert "GIT_SSH_COMMAND" not in env
                assert "SSH_ASKPASS" not in env
                assert "HTTPS_PROXY" not in env
                assert "EDITOR" not in env
                if name == "r7s1-git-remote-revision-readback":
                    assert "-C" not in command
                    assert command[3:5] == ["-c", "credential.helper="]
                    assert runner.CANONICAL_GIT_REMOTE_URL in command
                    assert "origin" not in command
                else:
                    assert command[3:5] == ["-c", "core.autocrlf=true"]
                    assert command[5:7] == ["-C", str(tmp_path.resolve())]
            else:
                assert cwd == tmp_path.resolve()
            self.names.append(name)
            return SuccessfulOutcome(name, outputs[name])

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker-git-dir"))
    monkeypatch.setenv("GIT_EXEC_PATH", str(tmp_path / "attacker-exec-path"))
    monkeypatch.setenv("GIT_SSH_COMMAND", "attacker-ssh")
    monkeypatch.setenv("SSH_ASKPASS", "attacker-askpass")
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid")
    monkeypatch.setenv("EDITOR", "attacker-editor")
    probe = _command_guard_probe(tmp_path)
    probe.manifest.update(
        {
            "canonical_revision": revision,
            "canonical_tree": tree,
            "repository": {
                "preserved_untracked_count": len(untracked_paths),
                "untracked_path_set_sha256": digest.hexdigest(),
            },
        }
    )
    outputs.update(
        {
            probe._runtime_hash_name(role): pin["head_blob_oid"] + "\n"
            for role, pin in probe.manifest["runtime"].items()
        }
    )
    process_runner = RecordingRunner()
    probe.runner = process_runner

    result = probe.docker_engine(runner.RestoreDeadline(600))

    assert result["passed"] is True
    assert result["invariants"]["canonical_repository_identity_exact"] is True
    config_checks = result["repository_identity"]["git_repository_config_checks"]
    assert len(config_checks) == 13
    attributes_checks = result["repository_identity"]["git_repository_attributes_checks"]
    assert len(attributes_checks) == 12
    bindings = result["repository_identity"]["runtime_head_worktree_bindings"]
    assert len(bindings) == 6
    assert all(binding["exact"] is True for binding in bindings)
    assert all(
        binding["worktree_readback"]["worktree_blob_oid"] != binding["measured_head_blob_oid"]
        for binding in bindings
    )
    assert all(
        check["origin_identity"] == runner.GIT_CONFIG_ORIGIN_IDENTITY for check in config_checks
    )
    assert runner.CANONICAL_GIT_REMOTE_URL not in json.dumps(result["repository_process_evidence"])
    assert process_runner.names == [
        "r7s1-git-branch-readback",
        "r7s1-git-local-revision-readback",
        "r7s1-git-origin-revision-readback",
        "r7s1-git-remote-revision-readback",
        "r7s1-git-tree-readback",
        "r7s1-git-tracked-readback",
        "r7s1-git-untracked-readback",
        *(probe._runtime_hash_name(role) for role in runner.RUNTIME_COMPONENTS),
        "r7s1-docker-engine-readback",
    ]


@pytest.mark.parametrize("mutation", ["content", "worktree"])
def test_git_config_is_rechecked_before_every_child_and_mutation_stops_chain(
    tmp_path: Path, mutation: str
) -> None:
    class SuccessfulOutcome:
        stdout = runner.CANONICAL_BRANCH + "\n"
        stderr = ""
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {
                "child_created": True,
                "residual_pids": [],
                "active_process_zero": True,
                "streams_drained": True,
                "identity_coverage_complete": True,
                "forced_termination_attempts": 0,
            }

    probe = _command_guard_probe(tmp_path)
    config_path = Path(probe.toolchain["git_repository_config"]["path"])
    calls: list[str] = []

    class MutatingRunner:
        @staticmethod
        def run(_command: Any, *, name: str, cwd: Path, env: dict[str, str] | None) -> Any:
            del cwd, env
            calls.append(name)
            if mutation == "content":
                config_path.write_bytes(
                    config_path.read_bytes() + b"\n# changed after first child\n"
                )
            else:
                config_path.with_name("config.worktree").write_text(
                    "[core]\n\tfsmonitor = attacker.exe\n", encoding="utf-8"
                )
            return SuccessfulOutcome()

    probe.runner = MutatingRunner()
    probe.manifest.update(
        {
            "canonical_revision": "1" * 40,
            "canonical_tree": "2" * 40,
            "repository": {
                "preserved_untracked_count": 0,
                "untracked_path_set_sha256": runner.hashlib.sha256(b"").hexdigest(),
            },
        }
    )

    result = probe._repository_identity(runner.RestoreDeadline(600))

    assert result["passed"] is False
    assert result["automatic_retries"] == 0
    assert calls == ["r7s1-git-branch-readback"]
    assert result["process_evidence"][-1]["child_created"] is False
    assert len(result["git_repository_config_checks"]) == 1


def test_self_consistent_git_config_repin_cannot_add_helper_key(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    config_path = Path(probe.toolchain["git_repository_config"]["path"])
    config_path.write_bytes(
        config_path.read_bytes() + b"\n[credential]\n\thelper = C:/attacker/credential-helper.exe\n"
    )
    pin = probe.toolchain["git_repository_config"]
    pin["sha256"] = runner.hashlib.sha256(config_path.read_bytes()).hexdigest()
    pin["bytes"] = config_path.stat().st_size

    with pytest.raises(runner.R7S1RunnerError, match="key_policy_mismatch"):
        probe._verify_git_repository_config()


@pytest.mark.parametrize("mutation", ["content", "nested", "info"])
def test_git_attributes_are_rechecked_before_every_local_child_and_stop_chain(
    tmp_path: Path, mutation: str
) -> None:
    class SuccessfulOutcome:
        stdout = runner.CANONICAL_BRANCH + "\n"
        stderr = ""
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {"child_created": True, "residual_pids": [], "forced_termination_attempts": 0}

    probe = _command_guard_probe(tmp_path)
    attributes_path = Path(probe.toolchain["git_repository_attributes"]["path"])
    runtime_path = Path(probe.manifest["runtime"]["builder"]["path"])
    calls: list[str] = []

    class MutatingRunner:
        @staticmethod
        def run(_command: Any, *, name: str, cwd: Path, env: dict[str, str] | None) -> Any:
            del cwd, env
            calls.append(name)
            if mutation == "content":
                attributes_path.write_bytes(attributes_path.read_bytes() + b"# mutation\n")
            elif mutation == "nested":
                runtime_path.parent.joinpath(".gitattributes").write_text(
                    "* filter=attacker\n", encoding="utf-8"
                )
            else:
                info = tmp_path / ".git" / "info" / "attributes"
                info.parent.mkdir(parents=True, exist_ok=True)
                info.write_text("* filter=attacker\n", encoding="utf-8")
            return SuccessfulOutcome()

    probe.runner = MutatingRunner()
    probe.manifest.update(
        {
            "canonical_revision": "1" * 40,
            "canonical_tree": "2" * 40,
            "repository": {
                "preserved_untracked_count": 0,
                "untracked_path_set_sha256": runner.hashlib.sha256(b"").hexdigest(),
            },
        }
    )
    result = probe._repository_identity(runner.RestoreDeadline(600))

    assert result["passed"] is False
    assert result["automatic_retries"] == 0
    assert calls == ["r7s1-git-branch-readback"]
    assert result["process_evidence"][-1]["child_created"] is False
    assert len(result["git_repository_attributes_checks"]) == 1


def test_self_consistent_git_attributes_repin_cannot_enable_filter(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    pin = probe.toolchain["git_repository_attributes"]
    attributes_path = Path(pin["path"])
    payload = attributes_path.read_bytes().replace(
        b"*.sh text eol=lf", b"*.sh filter=attacker text", 1
    )
    attributes_path.write_bytes(payload)
    pin["sha256"] = runner.hashlib.sha256(payload).hexdigest()
    pin["bytes"] = len(payload)

    with pytest.raises(runner.R7S1RunnerError, match="rule_not_allowlisted"):
        probe._verify_git_repository_attributes()


@pytest.mark.parametrize("mutation", ["head_mismatch", "swapped_worktree"])
def test_runtime_crlf_head_worktree_mutation_fails_before_docker(
    tmp_path: Path, mutation: str
) -> None:
    revision = "1" * 40
    tree = "2" * 40
    probe = _command_guard_probe(tmp_path)
    original_heads = {role: pin["head_blob_oid"] for role, pin in probe.manifest["runtime"].items()}
    target = probe.manifest["runtime"]["builder"]
    assert target["worktree_blob_oid"] != target["head_blob_oid"]
    if mutation == "head_mismatch":
        target["head_blob_oid"] = "f" * 40
    else:
        target["worktree_blob_oid"] = target["head_blob_oid"]
    probe.manifest.update(
        {
            "canonical_revision": revision,
            "canonical_tree": tree,
            "repository": {
                "preserved_untracked_count": 0,
                "untracked_path_set_sha256": runner.hashlib.sha256(b"").hexdigest(),
            },
        }
    )
    outputs = {
        "r7s1-git-branch-readback": runner.CANONICAL_BRANCH + "\n",
        "r7s1-git-local-revision-readback": revision + "\n",
        "r7s1-git-origin-revision-readback": revision + "\n",
        "r7s1-git-remote-revision-readback": (
            f"{revision}\trefs/heads/{runner.CANONICAL_BRANCH}\n"
        ),
        "r7s1-git-tree-readback": tree + "\n",
        "r7s1-git-tracked-readback": "",
        "r7s1-git-untracked-readback": "",
        **{probe._runtime_hash_name(role): head + "\n" for role, head in original_heads.items()},
        "r7s1-docker-engine-readback": '{"Version":"must-not-run"}',
    }
    calls: list[str] = []

    class Outcome:
        stderr = ""
        return_code = 0
        residual_pids: tuple[int, ...] = ()
        timed_out = False
        cancelled = False
        manual_intervention_required = False
        active_process_zero = True
        streams_drained = True
        identity_coverage_complete = True
        forced_termination_attempts = 0

        def __init__(self, name: str) -> None:
            self.stdout = outputs[name]

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return {"child_created": True, "residual_pids": [], "forced_termination_attempts": 0}

    class RecordingRunner:
        @staticmethod
        def run(_command: Any, *, name: str, cwd: Path, env: dict[str, str] | None) -> Outcome:
            del cwd, env
            calls.append(name)
            return Outcome(name)

    probe.runner = RecordingRunner()
    result = probe.docker_engine(runner.RestoreDeadline(600))

    assert result["passed"] is False
    assert result["automatic_retries"] == 0
    assert "r7s1-docker-engine-readback" not in calls
    if mutation == "head_mismatch":
        assert calls[-1] == probe._runtime_hash_name("docker_compose")
        bindings = result["identity"]["runtime_head_worktree_bindings"]
        assert next(item for item in bindings if item["role"] == "builder")["exact"] is False
    else:
        assert calls[-1] == "r7s1-git-untracked-readback"


def test_git_config_readback_redacts_user_values_and_raw_origin(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)

    readback = probe._verify_git_repository_config()

    serialized = json.dumps(readback, sort_keys=True)
    assert "redacted-test-user" not in serialized
    assert "redacted@example.invalid" not in serialized
    assert runner.CANONICAL_GIT_REMOTE_URL not in serialized
    assert readback["origin_identity"] == runner.GIT_CONFIG_ORIGIN_IDENTITY


def test_host_tool_sha_drift_is_rejected_before_process_creation(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    Path(probe.docker).write_bytes(b"tampered-docker")

    with pytest.raises(runner.R7S1RunnerError, match="binary_identity_mismatch:docker"):
        probe._validate_read_only_command(
            probe._docker_command("version", "--format", "{{json .Server}}"),
            name="r7s1-docker-engine-readback",
        )


def test_docker_compose_sha_drift_is_rejected_before_process_creation(tmp_path: Path) -> None:
    probe = _command_guard_probe(tmp_path)
    command = probe._compose_command("ps", "-a", "--format", "json")
    Path(probe.docker_compose).write_bytes(b"tampered-docker-compose")

    with pytest.raises(
        runner.R7S1RunnerError,
        match="binary_identity_mismatch:docker_compose",
    ):
        probe._validate_read_only_command(command, name="r7s1-compose-ps-initial")


def test_reservations_bind_live_parent_nonce_and_outer_sha(tmp_path: Path) -> None:
    process_path = tmp_path / "powershell.exe"
    process_path.write_bytes(b"pinned-powershell")
    parent = {
        "pid": 1200,
        "ppid": 1100,
        "session_id": 3,
        "creation_filetime": 133_000_000_000_000_000,
        "path": str(process_path.resolve()),
        "path_sha256": runner.sha256_file(process_path),
        "name": "powershell.exe",
    }
    nonce = "a" * 64
    output = tmp_path / "new-output"
    base = {
        "created_at": "2026-09-01T00:00:00Z",
        "invocation_nonce": nonce,
        **runner._process_reservation_identity(parent),
        "run_id": "phase-b2-r7s1-test",
        "mode": "restore-only",
        "output_directory": str(output),
    }
    outer_path = tmp_path / runner.OUTER_RESERVATION
    outer_path.write_text(
        runner.json.dumps(
            {
                "schema": "s8-v4-x1-phase-b2-r7s1-outer-reservation/v1",
                **base,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    bridge_path = tmp_path / runner.BRIDGE_RESERVATION
    bridge_path.write_text(
        runner.json.dumps(
            {
                "schema": "s8-v4-x1-phase-b2-r7s1-bridge-reservation/v1",
                **base,
                "outer_reservation_sha256": runner.sha256_file(outer_path),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    _outer, _bridge, measured_nonce = runner._verify_launcher_reservations(
        bundle=tmp_path,
        output_directory=output,
        run_id="phase-b2-r7s1-test",
        parent_identity=parent,
    )

    assert measured_nonce == nonce
    mutated = runner.json.loads(bridge_path.read_text(encoding="utf-8"))
    mutated["invocation_nonce"] = "b" * 64
    bridge_path.write_text(runner.json.dumps(mutated), encoding="utf-8")
    with pytest.raises(runner.R7S1RunnerError, match="reservation_nonce_mismatch"):
        runner._verify_launcher_reservations(
            bundle=tmp_path,
            output_directory=output,
            run_id="phase-b2-r7s1-test",
            parent_identity=parent,
        )


def test_native_authority_must_match_launcher_parent_codex_and_nonce(tmp_path: Path) -> None:
    python_path = tmp_path / "python.exe"
    powershell_path = tmp_path / "powershell.exe"
    codex_path = tmp_path / "codex.exe"
    for path in (python_path, powershell_path, codex_path):
        path.write_bytes(path.name.encode())
    runner_identity = {
        "pid": 300,
        "ppid": 200,
        "session_id": 7,
        "creation_filetime": 30,
        "path": str(python_path.resolve()),
        "path_sha256": runner.sha256_file(python_path),
        "name": "python.exe",
    }
    parent = {
        "pid": 200,
        "ppid": 100,
        "session_id": 7,
        "creation_filetime": 20,
        "path": str(powershell_path.resolve()),
        "path_sha256": runner.sha256_file(powershell_path),
        "name": "powershell.exe",
    }
    codex = {
        "pid": 100,
        "ppid": 50,
        "session_id": 7,
        "creation_filetime": 10,
        "path": str(codex_path.resolve()),
        "path_sha256": runner.sha256_file(codex_path),
        "name": "codex.exe",
    }
    token = {
        "captured_at": "2026-09-01T00:00:00Z",
        "administrator": True,
        "integrity": "High",
        "integrity_rid": 0x3000,
        "token_elevation_type": "Full",
        "token_elevation_type_value": 2,
    }
    nonce = "c" * 64
    claimed_token = {
        "captured_at": "2026-09-01T00:00:00Z",
        "administrator": True,
        "integrity": "High",
        "token_elevation_type": "Full",
        "token_elevation_type_value": 2,
        "invocation_nonce": nonce,
        "execution_powershell": {
            key: parent[key]
            for key in (
                "pid",
                "ppid",
                "session_id",
                "creation_filetime",
                "path",
                "path_sha256",
            )
        },
        "codex": {
            **{
                key: codex[key]
                for key in (
                    "pid",
                    "ppid",
                    "session_id",
                    "creation_filetime",
                    "path",
                    "path_sha256",
                )
            },
            "command_line_sha256": "d" * 64,
        },
    }
    manifest = {
        "toolchain": {
            "python": {
                "path": str(python_path.resolve()),
                "sha256": runner.sha256_file(python_path),
            },
            "powershell": {
                "path": str(powershell_path.resolve()),
                "sha256": runner.sha256_file(powershell_path),
            },
        }
    }
    environment = {
        "token": token,
        "runner": runner_identity,
        "parent": parent,
        "codex": codex,
    }

    runner._verify_launcher_authority_and_parent(
        manifest=manifest,
        launcher={"token_evidence": claimed_token},
        environment=environment,
        invocation_nonce=nonce,
    )

    environment["token"] = {**token, "token_elevation_type": "Limited"}
    with pytest.raises(runner.R7S1RunnerError, match="administrator_token_required"):
        runner._verify_launcher_authority_and_parent(
            manifest=manifest,
            launcher={"token_evidence": claimed_token},
            environment=environment,
            invocation_nonce=nonce,
        )


def test_token_failure_precedes_runner_reservation_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / runner.MANIFEST_LEAF
    output = tmp_path / "output"
    manifest = {
        "execution_mode": "restore-only",
        "canonical_revision": "1" * 40,
        "bundle_id": "phase-b2-r7s1-token-gate",
        "bundle": {"path": str(tmp_path.resolve())},
        "output": {"path": str(output.resolve())},
    }
    args = runner.argparse.Namespace(
        manifest=manifest_path,
        output_directory=output,
        expected_trusted_checkpoint_sha256="a" * 64,
        mode="restore-only",
        expected_revision="1" * 40,
        launcher_evidence_base64="unused",
    )
    monkeypatch.setattr(runner, "_read_manifest_snapshot", lambda _path: manifest)
    monkeypatch.setattr(runner, "_assert_bound_run_locations", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "decode_launcher_evidence", lambda *_args: {})
    monkeypatch.setattr(
        runner,
        "_native_runner_environment",
        lambda: {"parent": {"pid": 1}, "runner": {"pid": 2}},
    )
    monkeypatch.setattr(
        runner,
        "_verify_launcher_reservations",
        lambda **_kwargs: ({}, {}, "b" * 64),
    )
    monkeypatch.setattr(
        runner,
        "_verify_launcher_authority_and_parent",
        lambda **_kwargs: (_ for _ in ()).throw(
            runner.R7S1RunnerError("administrator_token_required")
        ),
    )
    writes: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runner,
        "_write_runner_reservation",
        lambda **kwargs: writes.append(kwargs),
    )

    with pytest.raises(runner.R7S1RunnerError, match="administrator_token_required"):
        runner.reserve_runner_preflight(args)

    assert writes == []
    assert not (tmp_path / runner.RUNNER_RESERVATION).exists()


def test_reparse_ancestor_added_after_initial_check_is_rejected(tmp_path: Path) -> None:
    protected_root = tmp_path / "protected"
    protected_root.mkdir()
    future_output = protected_root / "attempt" / "evidence.json"
    runner._assert_no_reparse_ancestors(future_output, label="test_output")

    redirected_root = tmp_path / "redirected"
    redirected_root.mkdir()
    try:
        (protected_root / "attempt").symlink_to(redirected_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"reparse-point creation unavailable: {exc}")

    with pytest.raises(RuntimeError, match="test_output_reparse_ancestor"):
        runner._assert_no_reparse_ancestors(future_output, label="test_output")


def test_run_locations_bind_staging_output_and_emergency_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_root = tmp_path / "staging"
    output_root = tmp_path / "output"
    staging_root.mkdir()
    output_root.mkdir()
    monkeypatch.setattr(runner, "_EARLY_CANONICAL_STAGING_ROOT", staging_root)
    monkeypatch.setattr(runner, "_EARLY_CANONICAL_OUTPUT_ROOT", output_root)
    run_id = "phase-b2-r7s1-location-binding"
    staging = staging_root / run_id
    staging.mkdir()
    output = output_root / run_id
    emergency = output_root / f"{run_id}-emergency-seal"
    manifest_path = staging / runner.MANIFEST_LEAF
    manifest = {
        "bundle_id": run_id,
        "bundle": {"path": str(staging)},
        "output": {"path": str(output)},
        "external_terminal_fencing": {
            "successor_binding": {
                "staging_path": str(staging),
                "output_path": str(output),
                "emergency_seal_path": str(emergency),
            }
        },
    }
    runner._assert_bound_run_locations(
        manifest_argument=manifest_path,
        output_argument=output,
        manifest=manifest,
        label="test_binding",
    )

    manifest["external_terminal_fencing"]["successor_binding"]["emergency_seal_path"] = str(
        tmp_path / "alternate-emergency"
    )
    with pytest.raises(RuntimeError, match="binding_emergency_not_canonical"):
        runner._assert_bound_run_locations(
            manifest_argument=manifest_path,
            output_argument=output,
            manifest=manifest,
            label="test_binding",
        )


def _publication_prepared(tmp_path: Path) -> SimpleNamespace:
    run_id = "phase-b2-r7s1-publication-test"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    output = tmp_path / "output"
    manifest_path = bundle / runner.MANIFEST_LEAF
    binding = {
        "run_id": run_id,
        "attempt_id": "11111111-1111-4111-8111-111111111111",
        "commit": "1" * 40,
        "tree": "2" * 40,
        "nonce": "3" * 64,
        "parent_map_sha256": "4" * 64,
        "staging_path": str(bundle),
        "output_path": str(output),
        "emergency_seal_path": str(tmp_path / "output-emergency-seal"),
    }
    manifest = {
        "bundle_id": run_id,
        "canonical_revision": "1" * 40,
        "canonical_tree": "2" * 40,
        "bundle": {"path": str(bundle)},
        "output": {"path": str(output)},
        "external_terminal_fencing": {"successor_binding": binding},
        "parent_checkpoints": [],
    }
    return SimpleNamespace(
        args=SimpleNamespace(
            manifest=manifest_path,
            expected_revision="1" * 40,
            expected_trusted_checkpoint_sha256="5" * 64,
        ),
        manifest=manifest,
        manifest_sha256="6" * 64,
        validated_manifest={},
        launcher_evidence={},
        parent_payloads={},
        restore_checkpoint=None,
        timeout_contract=runner.TimeoutContract().validate(),
        output_directory=output,
        run_id=run_id,
        bundle_directory=bundle,
    )


def _converted_publication_report(*, passed: bool) -> dict[str, Any]:
    return {
        "passed": passed,
        "overall_pass": passed,
        "restore_only_pass": passed,
        "manual_intervention_required": not passed,
        "decision": "restore_only_pass" if passed else "manual_intervention_required",
        "call_counts": dict(runner.RESTORE_LIFECYCLE_COUNTS),
        "phase_b2_executed": False,
        "residual_pids": [],
        "residual_status": "clear",
    }


def _prepare_publication_test(
    monkeypatch: pytest.MonkeyPatch, *, converted: dict[str, Any]
) -> None:
    monkeypatch.setattr(runner, "_assert_bound_run_locations", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_assert_no_reparse_ancestors", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_verify_owned_runner_reservation", lambda _prepared: None)
    monkeypatch.setattr(runner, "r7s1_restore_report", lambda *_args: dict(converted))


def test_ordinary_failure_seal_failure_uses_emergency_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converted = _converted_publication_report(passed=False)
    _prepare_publication_test(monkeypatch, converted=converted)
    calls: list[str] = []

    class Writer:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            calls.append("writer")

        def seal_failure(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("failure")
            raise OSError("failure seal write failed")

        @classmethod
        def seal_emergency(cls, **kwargs: Any) -> dict[str, Any]:
            calls.append(f"emergency:{kwargs['failed_stage']}")
            return {"emergency_directory": "emergency", "emergency_seal": {"sha256": "a" * 64}}

    monkeypatch.setattr(runner, "EvidenceWriter", Writer)
    prepared = _publication_prepared(tmp_path)
    code, result = runner.execute_restore_only(
        prepared,
        restore_executor=lambda *_args: SimpleNamespace(passed=False),
        runner_reserved=True,
    )
    assert code == 2
    assert result["emergency_seal_created"] is True
    assert calls == ["writer", "failure", "emergency:restore_only_failure_seal"]


def test_success_then_two_publication_failures_use_emergency_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converted = _converted_publication_report(passed=True)
    _prepare_publication_test(monkeypatch, converted=converted)
    calls: list[str] = []

    class Writer:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            calls.append("writer")

        def seal_restore_only(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("success")
            raise PermissionError("success publication denied")

        def seal_failure(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("failure")
            raise OSError("failure publication denied")

        @classmethod
        def seal_emergency(cls, **kwargs: Any) -> dict[str, Any]:
            calls.append(f"emergency:{kwargs['failed_stage']}")
            return {"emergency_directory": "emergency", "emergency_seal": {"sha256": "b" * 64}}

    monkeypatch.setattr(runner, "EvidenceWriter", Writer)
    code, result = runner.execute_restore_only(
        _publication_prepared(tmp_path),
        restore_executor=lambda *_args: SimpleNamespace(passed=True),
        runner_reserved=True,
    )
    assert code == 2
    assert result["report"]["passed"] is False
    assert result["emergency_seal_created"] is True
    assert calls == [
        "writer",
        "success",
        "failure",
        "emergency:restore_only_failure_seal_after_success_publication",
    ]


def test_writer_initialization_failure_uses_emergency_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converted = _converted_publication_report(passed=False)
    _prepare_publication_test(monkeypatch, converted=converted)
    calls: list[str] = []

    class Writer:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            calls.append("writer")
            raise PermissionError("output directory denied")

        @classmethod
        def seal_emergency(cls, **kwargs: Any) -> dict[str, Any]:
            calls.append(f"emergency:{kwargs['failed_stage']}")
            return {"emergency_directory": "emergency", "emergency_seal": {"sha256": "c" * 64}}

    monkeypatch.setattr(runner, "EvidenceWriter", Writer)
    code, result = runner.execute_restore_only(
        _publication_prepared(tmp_path),
        restore_executor=lambda *_args: SimpleNamespace(passed=False),
        runner_reserved=True,
    )
    assert code == 2
    assert result["report"]["passed"] is False
    assert result["emergency_seal_created"] is True
    assert calls == ["writer", "emergency:restore_only_writer_initialization"]


def test_emergency_failure_is_irrecoverable_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converted = _converted_publication_report(passed=False)
    _prepare_publication_test(monkeypatch, converted=converted)
    calls: list[str] = []

    class Writer:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            calls.append("writer")

        def seal_failure(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("failure")
            raise OSError("ordinary seal failed")

        @classmethod
        def seal_emergency(cls, **_kwargs: Any) -> dict[str, Any]:
            calls.append("emergency")
            raise PermissionError("emergency seal failed")

    monkeypatch.setattr(runner, "EvidenceWriter", Writer)
    code, result = runner.execute_restore_only(
        _publication_prepared(tmp_path),
        restore_executor=lambda *_args: SimpleNamespace(passed=False),
        runner_reserved=True,
    )
    assert code == 2
    assert result["emergency_seal_created"] is False
    assert result["irrecoverable_evidence_failure"] is True
    assert "emergency_seal_failed" in result["emergency_seal_error"]
    assert calls == ["writer", "failure", "emergency"]


def test_bootstrap_failure_before_trusted_binding_is_distinctly_irrecoverable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = SimpleNamespace(
        manifest=tmp_path / "untrusted-manifest.json",
        output_directory=tmp_path / "output",
        expected_trusted_checkpoint_sha256="a" * 64,
    )
    monkeypatch.setattr(
        runner,
        "_assert_no_reparse_ancestors",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("path blocked")),
    )

    evidence = runner._seal_bootstrap_failure(args, RuntimeError("bootstrap failed"))

    assert evidence["emergency_seal_created"] is False
    assert evidence["irrecoverable_evidence_failure"] is True
    assert evidence["failure_seal_error"] == "PermissionError:path blocked"
    assert evidence["emergency_seal_error"] == (
        "emergency_seal_unavailable_without_trusted_binding:PermissionError:path blocked"
    )


class _HTTPTestClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _HTTPTestSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class _HTTPTestHeaders:
    def __init__(self, content_lengths: list[str] | None = None) -> None:
        self.content_lengths = list(content_lengths or [])

    def get_all(self, name: str) -> list[str] | None:
        if name.lower() == "content-length":
            return list(self.content_lengths) or None
        return None


class _HTTPTestResponse:
    def __init__(
        self,
        *,
        url: str,
        chunks: list[tuple[bytes, float]],
        content_lengths: list[str] | None = None,
        clock: _HTTPTestClock,
        status: int = 200,
    ) -> None:
        self.url = url
        self.chunks = list(chunks)
        self.clock = clock
        self.status = status
        self.code = status
        self.headers = _HTTPTestHeaders(content_lengths)
        self.socket = _HTTPTestSocket()
        raw = type("Raw", (), {})()
        raw._sock = self.socket
        self.fp = type("FP", (), {})()
        self.fp.raw = raw
        self.closed = False
        self.read_calls = 0

    def geturl(self) -> str:
        return self.url

    def read1(self, size: int) -> bytes:
        self.read_calls += 1
        if not self.chunks:
            return b""
        chunk, elapsed = self.chunks.pop(0)
        self.clock.now += elapsed
        if len(chunk) > size:
            self.chunks.insert(0, (chunk[size:], 0.0))
            return chunk[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


def _http_test_probe(clock: _HTTPTestClock) -> runner.R7S1ProbeSet:
    probe = object.__new__(runner.R7S1ProbeSet)
    probe.contract = runner.TimeoutContract().validate()
    probe.clock = clock
    return probe


def _install_http_response(
    monkeypatch: pytest.MonkeyPatch,
    response: _HTTPTestResponse,
) -> list[Any]:
    handlers: list[Any] = []

    class Opener:
        def open(self, _request: Any, *, timeout: float) -> _HTTPTestResponse:
            assert 0 < timeout <= 8.0
            return response

    def build_opener(*values: Any) -> Opener:
        handlers.extend(values)
        return Opener()

    monkeypatch.setattr(runner.urllib.request, "build_opener", build_opener)
    return handlers


def test_http_json_disables_redirects_and_requires_exact_final_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://127.0.0.1:30800/ready"
    clock = _HTTPTestClock()
    probe = _http_test_probe(clock)
    deadline = runner.RestoreDeadline(total_seconds=100.0, clock=clock)
    redirected = _HTTPTestResponse(
        url=f"{url}/redirected",
        chunks=[(b"{}", 0.0), (b"", 0.0)],
        clock=clock,
    )
    handlers = _install_http_response(monkeypatch, redirected)

    result = probe._http_json(deadline, "GET", url)

    assert len(handlers) == 2
    assert isinstance(handlers[0], runner.urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}
    assert isinstance(handlers[1], runner._NoRedirectHandler)
    assert result["status"] is None
    assert result["error"].endswith("http_final_url_mismatch")
    assert redirected.read_calls == 0
    assert redirected.closed is True


def test_http_json_rejects_redirect_without_following(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://127.0.0.1:30800/ready"
    clock = _HTTPTestClock()
    probe = _http_test_probe(clock)
    deadline = runner.RestoreDeadline(total_seconds=100.0, clock=clock)
    handlers: list[Any] = []

    class RedirectOpener:
        def open(self, _request: Any, *, timeout: float) -> Any:
            assert timeout == 8.0
            raise runner.urllib.error.HTTPError(
                url,
                302,
                "Found",
                {"Location": f"{url}/redirected"},
                None,
            )

    def build_opener(*values: Any) -> RedirectOpener:
        handlers.extend(values)
        return RedirectOpener()

    monkeypatch.setattr(runner.urllib.request, "build_opener", build_opener)

    result = probe._http_json(deadline, "GET", url)

    assert len(handlers) == 2
    assert isinstance(handlers[0], runner.urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}
    assert isinstance(handlers[1], runner._NoRedirectHandler)
    assert result["status"] == 302
    assert result["error"] == "http_redirect_forbidden"


@pytest.mark.parametrize(
    ("chunks", "content_lengths", "expected_error", "expected_reads"),
    [
        ([(b"123456789", 0.0)], None, "http_response_too_large", 1),
        ([(b"{}", 0.0), (b"", 0.0)], ["3"], "http_response_truncated", 2),
    ],
)
def test_http_json_rejects_oversize_and_truncated_responses(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[tuple[bytes, float]],
    content_lengths: list[str] | None,
    expected_error: str,
    expected_reads: int,
) -> None:
    monkeypatch.setattr(runner, "HTTP_RESPONSE_MAX_BYTES", 8)
    url = "http://127.0.0.1:30800/ready"
    clock = _HTTPTestClock()
    probe = _http_test_probe(clock)
    deadline = runner.RestoreDeadline(total_seconds=100.0, clock=clock)
    response = _HTTPTestResponse(
        url=url,
        chunks=chunks,
        content_lengths=content_lengths,
        clock=clock,
    )
    _install_http_response(monkeypatch, response)

    result = probe._http_json(deadline, "GET", url)

    assert result["status"] is None
    assert result["error"].endswith(expected_error)
    assert response.read_calls == expected_reads
    assert response.closed is True


def test_http_json_drip_read_is_bounded_by_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://127.0.0.1:30800/ready"
    clock = _HTTPTestClock()
    probe = _http_test_probe(clock)
    deadline = runner.RestoreDeadline(total_seconds=100.0, clock=clock)
    response = _HTTPTestResponse(
        url=url,
        chunks=[(b"{", 4.1), (b'"ok":true}', 4.1), (b"", 0.0)],
        clock=clock,
    )
    _install_http_response(monkeypatch, response)

    result = probe._http_json(deadline, "GET", url)

    assert result["status"] is None
    assert result["error"].endswith("http_total_deadline_exceeded")
    assert response.read_calls == 2
    assert response.socket.timeouts == pytest.approx([8.0, 3.9])
    assert response.closed is True

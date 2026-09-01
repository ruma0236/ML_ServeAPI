from __future__ import annotations

import base64
import copy
import hashlib
import json
import subprocess
import sys
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evm.scale_validation.phase_b2_r7s1 import (
    EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES,
    FAILED_POD_IDENTITY_FIELDS,
    OBSERVATION_SOURCE_REVISION,
)
from scripts.dev import prepare_x1_phase_b2_r7s1_bundle as builder


PROJECT = Path(__file__).parents[1]
REVISION = "a" * 40
TREE = "b" * 40
IMAGE_ID = "sha256:" + "c" * 64
EMPTY_UNTRACKED_DIGEST = hashlib.sha256(b"").hexdigest()
RUN_ID = "x1-clock-phase-b2-r7s1-test-0001"
SUCCESSOR_NONCE = "d" * 64
ATTEMPT_ID = "11111111-2222-4333-8444-555555555555"
DEFAULT_PARENT_MAP_SHA256 = "e" * 64

# Exact argv captured by the two immutable pre-r8 source artifact classes.
# Keeping the vectors compressed makes synthetic evidence self-contained while
# still exercising the production normalized-argv SHA contract.
_OBSERVATION_ARGV = json.loads(
    zlib.decompress(
        base64.b64decode(
            "".join(
                (
                    "eNrlWm1T20gS/itTLvYk3Vo6XrPELufCgtn4jpgUNpfbRZRKlsZYQRqJGQlwEf/36x6NbNkWwSxOapPjg5jXnu6e7p6eZ/xQ",
                    "CwN2XWs81NyAD8P4zuEZc7BN1BoXtcOGbX/g8RV3I3IchFTY9lHsXVM++8+piDPuYdcgYLbty3ar/d92rV6j99TDf7eRqeib",
                    "SSzSK5gEzYm4CeGfeQuf067TPjs7PXN6/dMPrS1sPoePmoVVf756kOL3GD6fsYDL9Non7cM+0Xz3CsXQ6jYj+Z8XZyzV/26Q",
                    "485Jv31G9I/v2mdtgrIGfkt7PfD3drf2Xm3u7+7vbnvu652BR3f23N1dd9Oj7i+a8SQpECtIg5jpXsxuKU+dNNafplvXzvvH",
                    "+5pBAgaE2dAgb8jm04vpKzNOTs/Wyho56B4RkbopJZ0u0bWbjGbUh8HAEQvYlWYYNjs+O31P1CbY7LzbOe2Sg5MTmxUblLri",
                    "2gkY0GEe/c62CU06S2PuoFKCq7/ojlVyubh5whtRPwvl/i1tJJQojOVpXskSZwjkOE35uFwtaECbT4eUcyAytYG5fa60hHsv",
                    "jr4zA7h1w4wubvumkhjlqRSUU+ZTUI4zpxNnGNDQF99UA1NO8rUbjZTep+Sk8+820X56iv5P0j6v94WTxL4zdqPwufMrlPZF",
                    "3djs9OwIuP/1d7LVrF3Wa7BxKY9DJwldRr/acaVWMeUqzzy0YL4zx2VxfFV2fOEgoywFY6Ur2IccOV6bk7jjMHb9Z2+t2s8l",
                    "Ma1CkErXCHwaJXFKmTd2run4m0sL+5rETFBn7WIvSlYpfhgMqTf2Qup4oRtE3zYYrF3kRWkqRRagGIcOYWTqxFk6iO+fFnpG",
                    "+K8s/rJkjydArh8FQsBZ5MjD92kV5OFxPZJLWmsXv0quxxXgByJxU2+kFCbWqQE8plQKurY4Aa1BRL+S1haVUe04rwYw2c2E",
                    "Gzr0FsLgdx4tluV5XGwO/gTOT28DtK0fQfAFiZYynTw3WU5xPna6R6cfe7bdG4uURjvb0BQwP74TH+I7ynsjGoa2fbtlbdp2",
                    "gi0CW1SOY3ZjSJGGkB3lFdZhKeWulwa3suUwjiKXYbay0eY85gcepssfOCb3cJDRltZL40RrbgBbnVW029zI5WhpL0rPgE7g",
                    "i9Zb/W9EESSJIKZ7YzQ34sEndJlWMNRxkHWIu2g8vNVLgyGtTGAQeQsDPh/myf8xjyPzXyJmhjGhoaAww5g0NyL0Q4prFZQ/",
                    "fxyB9OaprD3oG05BoR/L+cQ8okk6IlubmwQ1mIAcwgA+WOrCwnquLGPy+TjmbdcbFZQuYi4z38u3D2CpFyLlcNu63HCsjt9k",
                    "bkRbeqmtCw2G1edB1MN7ma79QzOaeJ3LRHlqDy948puJycRoltaIB4LyW0i0pZm3CulyfTWl2ECk6FVqKPeCUormyZIKCrkL",
                    "ZeyhDV9nA8oZTal4caqOpLx0ascmp3C+iNTEoAye1NrH9PyKYjoNlxNR/wTi1n2ahPE4wtBSl1ffYQYbDRXfpRFmfVDkMCbw",
                    "XCzLhBw/MXw+gVgow13uXN+/I9J7L8x86qNlf+gc1WH06+369i97O6/Ai8TIbV30qJdxSK6tQz5OUtyfZDS2eu8OtvdeXTYa",
                    "h5yCDnWj7CS/0dQ8DKKOurcRUMHOtgOywdYteA6Yp2rv+MRkcQr36SlX4MzMJxssC0PoowQGKx2cBFDNe+fanuVgCXpYwNLL",
                    "MhPNZK7ZBc2ms04PpcVEBk2s7GOHquMIlJE76oKfNr2cS7QX6oBmQX0t3fwUg8CgfGhAORKI/+9cMdIv+nCuWG3mxT4SaTQQ",
                    "brBAsb+OwXPKQaAkvWEsSgv9/bgnx+ra/bZmTCCwTfKttY4gwYgF7l1JKV/F5+9E+AVfEVNfgXGFL6CZnw9gzUy6trqIJ+N0",
                    "FLOd4locwGWKp2QEGguDQR3dsx6LOqRNWLcZo9QP6QqeYLOpJzzEwoKYAVagG3VVlpWJzQrxLy5tNow5Xjv5GCEgtSLYSzqC",
                    "MJyAwWiGFYD7+gHXjUaeTgRDAhaez7LQSKxA+MFVkE5H5BkHZJUM02SsoTWCMeqzScaUGPTl0F7O+qM0YGapj7t3rZzcPzQv",
                    "8tEegVewX98ZSOMyynSkcbVgjoVB0fWoPtBse1OrDzQC03wKFkp1LUuH5r6ECOUg6EELTcq0UHq5H1IJEkSWxEusLbOOfxil",
                    "pxxjpWAXcy+wXsKDq1Hawh6LDyHc6poBByHJ4SLZfiGH/LzVuCz4sgSE+LTMX2HpbpJQoPGggXq1BnzqWiKLuA05zYutSwNa",
                    "r3gy17qNrYLKq85cx47swDNaBg74eNdinuBrHFIRI7SGsm4rr+tqjEWZ1LxhWCN6D1YEBx/YqJIHTAK8j+h4kHbj9Bic15dn",
                    "Rl3FspM4vs4S1US5up/l9f8gnpkXO8yn97JYbaEJRxnQ7yw/ixIBWpsPIFojpExXqgUJVUlrCHBc6hc99Ws6boVuNPBdAl4T",
                    "NXT8XsgtuKzn5SX9XUIsqyMdCaW0+jyjdUETl7tpzEVLB3Osaw3NQPS5djmp1wRzEzGKU3zdAjVi8HPAVTFSymwcDg44kdeB",
                    "GyYyb3Bl6IIDHeIAFEN3QMMWLGypsYoHS/HQonjeg0oFNfMbgBmFcU7KhHADuoLiwwOqm1iTyTL2mYP8eD79qAioQg4R6qvr",
                    "qm12RVsdgyA3JL/I3VgFhFAiLd9FbqzSy4h76wawf/JZQz55OHdukEIlpJAmqmeSHAyA4JG/kEAKCjtMnYxds/iOgSH+/CyW",
                    "FwEA4iue/Ud59ss8w+bI5B5YEdkgClL1cqMqsmOZx5XVugjsEU+x50F0ztXiuCnp9Ej3/OREsudZ9D4JgC/oeOOFYGfSn4Hn",
                    "CEKyGpLf0qUI5d0+PD04afcO29CoLt/mmzdaPhgEgTt79YgZ5vOlUYtgohz7DGX8SRvTlZG1CjtClAoaZc2BDYF7KirwtD9T",
                    "YtH7JVV+A9annC+Z0Ley8oqlyRPPDYQq4lOS15AvtBT8Cgq4Yngz1OQy1Kp611b+nb+jYkzGw7703E2mmE2J7Yp3qlEg4Jga",
                    "/4gxeuqzUn/1NHa8kct1eXnKg8JBn/Q779vkj9Num2jn/UPQ4O/wZ75/bx4d2bW+XXv3bnu38b7T6PWs855d+8OugfMWpLLE",
                    "fyGpJy1l7mHpC1byQhuZs5ASNjKE8wYERNzi26AjU6DDlCmpKSCGe2ChrRxQshLIQ2nrWLK1iIeU+EaM5eszLFepQmaiUP5m",
                    "SYImoNg1e9fz/CnnpXCiWe1xz+Hy8MsycB1u5XqHwuxsgqYrOjvB5BiVE+dI9PwJx8Ga/cXe6uAcQU4TeIJEyu6jKSetGVMl",
                    "0gWVyL3Xp+eOoeDw51B8nCVM5CGrSNT8pHp+9VzQkyCpmplWzswHQlUQrgYWOm9pZ+fdbqf7mzZz1NlM6anS1VXi8wOG8GWt",
                    "zr1+rD0xfk5uUrXny9xNM6qvQnvlxGPVRVZMsYvfJ1Sn17OMkLwhyznhyzLC8s5jPrq23ZewRxLS/EKSn3v447HwBr65SFj3",
                    "EEoOQ/lrsmYZhL8K44Eb4i/PAj9zw78YFl9C2H95vbdtNDcQw0OUXMvBTLgSofbyyp2qIRCalwauGKmiKgAJSJiBG9bS9H8G",
                    "hi5P5gvHvBxsf77fMuXGm7LRhBaxb97umvdbhvZcQJ+Dqp5C88lnUsbzyUMuHjEVniKIwr+naP064X5iSvyIFAqZADvzCDj5",
                    "wQH/CoFXxPyfBO6J2WHAniILLKM9GAWc/6TXfS1Uv84Rd1QOwKkE0cBNda6t7AhAwuoYK2L+Q8RO/38Qf8UxTHkSl1/34wAs",
                    "q/bVEtTl3qjAuo2XPhGUVFH1VDDrfuaTgbSNFz4YzIs2+3vJQ8KjNL//Bwb1biA1/2dfDar0s/o7wuR/gvvFjA==",
                )
            )
        )
    ).decode("utf-8")
)


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


def _failed_pods() -> list[dict[str, object]]:
    return [
        dict(zip(FAILED_POD_IDENTITY_FIELDS, identity, strict=True))
        for identity in EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES
    ]


def _attestation(
    tmp_path: Path,
    *,
    source: str,
    records: list[dict[str, object]],
) -> dict[str, str]:
    path = tmp_path / f"{source}-attestation.json"
    for index, record in enumerate(records):
        if "execution_proof" not in record:
            continue
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
                "status": "RUNNING",
                "lifecycle_stage": "active",
                "start_time": "1783653474422",
                "end_time": "",
            },
            "observed_state": "RUNNING",
            "classification": "historical_nonexecuting",
        }
    ]
    kubernetes_records = [
        {
            "identity": copy.deepcopy(pod),
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


def _observation_commands(names: tuple[str, ...], captured_at: str) -> list[dict[str, object]]:
    empty_sha = hashlib.sha256(b"").hexdigest()
    command_class = "snapshot" if names == builder.SNAPSHOT_COMMAND_NAMES else "link"
    commands: list[dict[str, object]] = []
    for index, name in enumerate(names, start=1):
        run_uuid = f"00000000-0000-4000-8000-{index:012d}"
        commands.append(
            {
                "accounting": [
                    {
                        "active_pids": [],
                        "active_processes": 0,
                        "monotonic_ns": index * 10 + 2,
                        "sequence": 2,
                        "timestamp_utc": captured_at,
                        "total_processes": 1,
                        "total_terminated_processes": 1,
                    }
                ],
                "active_process_zero": True,
                "cancelled": False,
                "command": copy.deepcopy(_OBSERVATION_ARGV[command_class][name]),
                "duration_seconds": 0.01,
                "ended_at_utc": captured_at,
                "errors": [],
                "events": [
                    {
                        "details": {},
                        "event": "job_empty",
                        "monotonic_ns": index * 10 + 1,
                        "pid": None,
                        "sequence": 1,
                        "timestamp_utc": captured_at,
                    }
                ],
                "final_active_process_count": 0,
                "forced_termination_attempts": 0,
                "identities": [
                    {
                        "creation_time_ns": index,
                        "creation_time_utc": captured_at,
                        "image": "synthetic-read-only.exe",
                        "observed_sequence": 1,
                        "pid": 1000 + index,
                        "ppid": 999,
                        "run_uuid": run_uuid,
                    }
                ],
                "identity_coverage_complete": True,
                "job_limit_flags": 0,
                "manual_intervention_required": False,
                "name": name,
                "residual_pids": [],
                "return_code": 0,
                "run_uuid": run_uuid,
                "safe_for_followup": True,
                "safe_for_followup_gate": True,
                "started_at_utc": captured_at,
                "stderr": {"bytes": 0, "redacted": True, "sha256": empty_sha},
                "stderr_drained": True,
                "stdout": {"bytes": 0, "redacted": True, "sha256": empty_sha},
                "stdout_drained": True,
                "streams_drained": True,
                "timed_out": False,
            }
        )
    return commands


def _external_fencing(
    tmp_path: Path,
    *,
    run_id: str = RUN_ID,
    parent_map_sha256: str = DEFAULT_PARENT_MAP_SHA256,
    commit: str = REVISION,
    tree: str = TREE,
    attempt_id: str = ATTEMPT_ID,
    staging_directory: Path | None = None,
    output_directory: Path | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC).replace(microsecond=0)
    identity = {
        "run_id": "9bd54156084842ca93bce35a44a0cea7",
        "status": "RUNNING",
        "lifecycle_stage": "active",
        "start_time": "1783653474422",
        "end_time": "",
    }
    successor_binding = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "commit": commit,
        "tree": tree,
        "nonce": SUCCESSOR_NONCE,
        "parent_map_sha256": parent_map_sha256,
        "staging_path": str(
            (staging_directory or (builder.CANONICAL_STAGING_BASE / run_id)).resolve()
        ),
        "output_path": str(
            (output_directory or (builder.CANONICAL_OUTPUT_BASE / run_id)).resolve()
        ),
        "emergency_seal_path": str(
            (
                (output_directory or (builder.CANONICAL_OUTPUT_BASE / run_id)).parent
                / f"{run_id}-emergency-seal"
            ).resolve()
        ),
    }
    activity = {
        **identity,
        "metric_count": 7,
        "last_metric_timestamp": "1783653475809",
        "parameter_count": 13,
        "tag_count": 5,
    }
    identity_sha = builder._canonical_object_sha256(identity)
    activity_sha = builder._canonical_object_sha256(activity)
    pins: list[dict[str, object]] = []
    source_revision = OBSERVATION_SOURCE_REVISION
    snapshot_times = (now - timedelta(seconds=300), now - timedelta(seconds=240))
    for ordinal, captured in enumerate(snapshot_times, start=1):
        path = tmp_path / f"historical-snapshot-{ordinal}.json"
        captured_at = captured.isoformat().replace("+00:00", "Z")
        _write_json(
            path,
            {
                "schema": builder.HISTORICAL_SNAPSHOT_SCHEMA,
                "ordinal": ordinal,
                "captured_at": captured_at,
                "all_commands_safe": True,
                "automatic_retry_count": 0,
                "command_count": len(builder.SNAPSHOT_COMMAND_NAMES),
                "commands": _observation_commands(builder.SNAPSHOT_COMMAND_NAMES, captured_at),
                "expected_command_count": len(builder.SNAPSHOT_COMMAND_NAMES),
                "process_containment": {
                    "type": "windows_job_object",
                    "create_suspended_before_assignment": True,
                    "kill_on_job_close": False,
                    "terminate_job_object_calls": 0,
                    "forced_termination_attempts": 0,
                },
                "query_sha256": dict(builder.SNAPSHOT_QUERY_SHA256),
                "read_only": True,
                "repository": builder.SNAPSHOT_REPOSITORY,
                "service_mutation_count": 0,
                "source_revision": source_revision,
                "stopped_after": None,
                "observed": {
                    "mlflow_activity": [activity],
                    "control_plane_history": [],
                    "control_plane_execution_links": [],
                    "queue_claims": {
                        "active": 0,
                        "active_claims": 0,
                        "leased": 0,
                        "outcome_unknown": 0,
                        "unknown_state": 0,
                    },
                    "kubernetes_failed_pods": [],
                    "kubernetes_jobs": [],
                    "compose_project_containers": [],
                    "windows_global_residuals": [],
                    "wsl_global_residuals": [],
                },
            },
        )
        pins.append(
            {
                "kind": "historical_snapshot",
                "ordinal": ordinal,
                "path": str(path.resolve()),
                "sha256": builder.sha256_file(path),
                "captured_at": captured_at,
                "schema": builder.HISTORICAL_SNAPSHOT_SCHEMA,
                "source_revision": source_revision,
                "target_identity_sha256": identity_sha,
                "decision_authority": builder.EXTERNAL_DECISION_AUTHORITY,
            }
        )
    scan_times = (now - timedelta(seconds=180), now - timedelta(seconds=120))
    for ordinal, captured in enumerate(scan_times, start=1):
        path = tmp_path / f"exact-link-scan-{ordinal}.json"
        captured_at = captured.isoformat().replace("+00:00", "Z")
        _write_json(
            path,
            {
                "schema": builder.TARGET_LINK_SCAN_SCHEMA,
                "ordinal": ordinal,
                "captured_at": captured_at,
                "target_run_id": identity["run_id"],
                "all_commands_safe": True,
                "all_exact_links_zero": True,
                "automatic_retry_count": 0,
                "command_count": len(builder.LINK_SCAN_COMMAND_NAMES),
                "commands": _observation_commands(builder.LINK_SCAN_COMMAND_NAMES, captured_at),
                "expected_command_count": len(builder.LINK_SCAN_COMMAND_NAMES),
                "forced_termination_attempts": 0,
                "query_sha256": dict(builder.LINK_SCAN_QUERY_SHA256),
                "read_only": True,
                "service_mutation_count": 0,
                "source_revision": source_revision,
                "stopped_after": None,
                "observed": {
                    "control_plane_run_links": [
                        {"table": table, "identity_matches": 0, "payload_matches": 0}
                        for table in sorted(builder.CONTROL_PLANE_LINK_TABLES)
                    ],
                    "airflow_run_links": [
                        {
                            "table": table,
                            "identity_matches": 0,
                            "payload_matches": 0,
                            "active_matches": 0,
                        }
                        for table in sorted(builder.AIRFLOW_LINK_TABLES)
                    ],
                    "docker_run_links": {
                        "observed_count": 13,
                        "matching_count": 0,
                        "matches": [],
                    },
                    "kubernetes_run_links": {
                        "observed_count": 14,
                        "matching_count": 0,
                        "matches": [],
                    },
                    "windows_run_links": {"matching_count": 0, "matches": []},
                    "wsl_run_links": {"matching_count": 0, "matches": []},
                },
            },
        )
        pins.append(
            {
                "kind": "exact_link_scan",
                "ordinal": ordinal,
                "path": str(path.resolve()),
                "sha256": builder.sha256_file(path),
                "captured_at": captured_at,
                "schema": builder.TARGET_LINK_SCAN_SCHEMA,
                "source_revision": source_revision,
                "target_identity_sha256": identity_sha,
                "decision_authority": builder.EXTERNAL_DECISION_AUTHORITY,
            }
        )
    issued_at = (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    decision = tmp_path / "terminal-fencing-decision.json"
    _write_json(
        decision,
        {
            "schema": builder.EXTERNAL_FENCING_DECISION_SCHEMA,
            "target_source": "mlflow_running_rows",
            "target_identity": identity,
            "successor_binding": successor_binding,
            "decision": "proven_terminal_fenced",
            "decision_authority": builder.EXTERNAL_DECISION_AUTHORITY,
            "issued_at": issued_at,
            "future_dispatch_fenced": True,
            "supporting_sha256": {
                "historical_snapshot_1": pins[0]["sha256"],
                "historical_snapshot_2": pins[1]["sha256"],
                "exact_link_scan_1": pins[2]["sha256"],
                "exact_link_scan_2": pins[3]["sha256"],
                "historical_snapshot_1_target_activity_sha256": activity_sha,
                "historical_snapshot_2_target_activity_sha256": activity_sha,
                "successor_binding_sha256": builder._canonical_object_sha256(successor_binding),
            },
        },
    )
    decision_sha = builder.sha256_file(decision)
    checkpoint = tmp_path / "trusted-checkpoint.json"
    checkpoint_issued_at = (now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
    fence_read_at = (now - timedelta(seconds=40)).isoformat().replace("+00:00", "Z")
    expires_at = (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    supporting = {
        "historical_snapshot_1": pins[0]["sha256"],
        "historical_snapshot_2": pins[1]["sha256"],
        "exact_link_scan_1": pins[2]["sha256"],
        "exact_link_scan_2": pins[3]["sha256"],
        "historical_snapshot_1_target_activity_sha256": activity_sha,
        "historical_snapshot_2_target_activity_sha256": activity_sha,
        "successor_binding_sha256": builder._canonical_object_sha256(successor_binding),
    }
    _write_json(
        checkpoint,
        {
            "schema": builder.TRUSTED_CHECKPOINT_SCHEMA,
            "checkpointed_at": checkpoint_issued_at,
            "expires_at": expires_at,
            "decision_authority": builder.EXTERNAL_DECISION_AUTHORITY,
            "independent_approval": {
                "source": "synthetic-independent-review",
                "reviewer_identity": "reviewer:test",
                "approval_id": "approval:test",
            },
            "successor_binding": successor_binding,
            "target_source": "mlflow_running_rows",
            "target_identity_sha256": identity_sha,
            "decision_sha256": decision_sha,
            "supporting_sha256": supporting,
            "fence_readback": {
                "target_run_id": identity["run_id"],
                "future_dispatch_fenced": True,
                "fence_state": "fenced",
                "read_back_at": fence_read_at,
            },
        },
    )
    checkpoint_sha = builder.sha256_file(checkpoint)
    document = tmp_path / "external-terminal-fencing-pins.json"
    _write_json(
        document,
        {
            "schema": builder.EXTERNAL_FENCING_PINS_SCHEMA,
            "target_source": "mlflow_running_rows",
            "decision_authority": builder.EXTERNAL_DECISION_AUTHORITY,
            "target_identity": identity,
            "target_identity_sha256": identity_sha,
            "successor_binding": successor_binding,
            "terminal_decision": {
                "path": str(decision.resolve()),
                "sha256": decision_sha,
                "schema": builder.EXTERNAL_FENCING_DECISION_SCHEMA,
            },
            "trusted_checkpoint": {
                "path": str(checkpoint.resolve()),
                "sha256": checkpoint_sha,
                "schema": builder.TRUSTED_CHECKPOINT_SCHEMA,
            },
            "snapshots": pins[:2],
            "exact_link_scans": pins[2:],
        },
    )
    return builder.validate_external_terminal_fencing(
        document,
        expected_successor_binding=successor_binding,
        expected_trusted_checkpoint_sha256=checkpoint_sha,
        now=now,
    )


def _revalidate_external(path: Path, *, now: datetime) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return builder.validate_external_terminal_fencing(
        path,
        expected_successor_binding=document["successor_binding"],
        expected_trusted_checkpoint_sha256=document["trusted_checkpoint"]["sha256"],
        now=now,
    )


def _external_for_manifest(
    tmp_path: Path,
    parents: list[dict[str, object]],
    run_id: str,
    *,
    staging_directory: Path | None = None,
    output_directory: Path | None = None,
) -> dict[str, object]:
    parent_map_sha256 = builder.parent_map_sha256(parents)
    return _external_fencing(
        tmp_path,
        run_id=run_id,
        parent_map_sha256=parent_map_sha256,
        staging_directory=staging_directory,
        output_directory=output_directory,
    )


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
    paths = {role: tmp_path / f"{role}.json" for role in builder.REQUIRED_PARENT_ROLES}
    _write_json(
        paths["r5_failure_seal"],
        {
            "schema": builder.PARENT_SCHEMAS["r5_failure_seal"],
            "metadata": {"run_id": "r5-chain-run"},
        },
    )
    _write_json(
        paths["r5_failure_index"],
        {
            "schema": builder.PARENT_SCHEMAS["r5_failure_index"],
            "files": [{"sha256": builder.sha256_file(paths["r5_failure_seal"])}],
        },
    )
    _write_json(
        paths["r6_compose_rca"],
        {"schema": builder.PARENT_SCHEMAS["r6_compose_rca"], "run_identity": "r6-chain-run"},
    )
    _write_json(
        paths["r6_failure_seal_amendment"],
        {
            "schema": builder.PARENT_SCHEMAS["r6_failure_seal_amendment"],
            "base_rca_sha256": builder.sha256_file(paths["r6_compose_rca"]),
        },
    )
    _write_json(
        paths["r6_final_index"],
        {
            "schema": builder.PARENT_SCHEMAS["r6_final_index"],
            "seal_amendment_sha256": builder.sha256_file(paths["r6_failure_seal_amendment"]),
        },
    )
    _write_json(
        paths["post_manual_on_readback"],
        {
            "schema": builder.PARENT_SCHEMAS["post_manual_on_readback"],
            "runtime_state": runtime_state,
        },
    )
    readback_sha = builder.sha256_file(paths["post_manual_on_readback"])
    _write_json(
        paths["post_manual_on_index"],
        {
            "schema": builder.PARENT_SCHEMAS["post_manual_on_index"],
            "files": [
                {"sha256": builder.sha256_file(paths["r6_final_index"])},
                {
                    "path": str(paths["post_manual_on_readback"].resolve()),
                    "sha256": readback_sha,
                },
            ],
        },
    )
    _write_json(
        paths["r7_failure_index"],
        {"schema": builder.PARENT_SCHEMAS["r7_failure_index"], "run_identity": "r7-chain-run"},
    )
    _write_json(
        paths["r7_failure_seal"],
        {
            "schema": builder.PARENT_SCHEMAS["r7_failure_seal"],
            "run_identity": "r7-chain-run",
            "pinned_evidence": {
                "failure_index_sha256": builder.sha256_file(paths["r7_failure_index"])
            },
        },
    )
    _write_json(
        paths["r7_post_seal_residual_amendment"],
        {
            "schema": builder.PARENT_SCHEMAS["r7_post_seal_residual_amendment"],
            "parent_failure_seal_sha256": builder.sha256_file(paths["r7_failure_seal"]),
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
            "worktree_blob_oid": f"{index:x}" * 40,
            "head_blob_oid": f"{index:x}" * 40,
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
    compose_path = (PROJECT / builder.RUNTIME_PATHS["docker_compose"]).resolve()
    runtime["docker_compose"].update(
        {
            "path": str(compose_path),
            "sha256": builder.sha256_file(compose_path),
            "bytes": compose_path.stat().st_size,
        }
    )
    return runtime


def _toolchain(tmp_path: Path) -> dict[str, object]:
    executable = Path(sys.executable).resolve()
    host_pin = {
        "path": str(executable),
        "sha256": builder.sha256_file(executable),
        "bytes": executable.stat().st_size,
        "version": sys.version.split()[0],
        "signature": {
            "status": "valid",
            "subject": "Synthetic test signer",
            "thumbprint": "1" * 40,
        },
    }

    def artifact(name: str, schema: str, extra: dict[str, object] | None = None) -> dict[str, str]:
        path = tmp_path / f"{name}.json"
        _write_json(path, {"schema": schema, "status": "verified", **(extra or {})})
        return {"path": str(path.resolve()), "sha256": builder.sha256_file(path), "schema": schema}

    host_pins = {role: copy.deepcopy(host_pin) for role in builder.HOST_TOOLCHAIN_ROLES}
    compose_executable = builder.EXPECTED_DOCKER_COMPOSE_PATH.resolve()
    host_pins["docker_compose"] = {
        "path": str(compose_executable),
        "sha256": builder.sha256_file(compose_executable),
        "bytes": compose_executable.stat().st_size,
        "version": "5.0.2",
        "signature": copy.deepcopy(host_pin["signature"]),
    }
    git_config_path = builder.EXPECTED_GIT_CONFIG_PATH.resolve()
    git_config_readback = artifact(
        "git-repository-config",
        builder.GIT_REPOSITORY_CONFIG_READBACK_SCHEMA,
        {
            "captured_at": "2026-09-01T00:00:00Z",
            "path": str(git_config_path),
            "sha256": builder.EXPECTED_GIT_CONFIG_SHA256,
            "bytes": builder.EXPECTED_GIT_CONFIG_BYTES,
            "key_names": list(builder.GIT_CONFIG_ALLOWED_KEY_NAMES),
            "origin_identity": dict(builder.GIT_CONFIG_ORIGIN_IDENTITY),
            "config_worktree_absent": True,
            "policy_sha256": hashlib.sha256(
                builder.canonical_json_bytes(builder.GIT_REPOSITORY_CONFIG_POLICY)
            ).hexdigest(),
        },
    )
    git_attributes_path = builder.EXPECTED_GIT_ATTRIBUTES_PATH.resolve()
    git_attributes_readback = artifact(
        "git-repository-attributes",
        builder.GIT_REPOSITORY_ATTRIBUTES_READBACK_SCHEMA,
        {
            "captured_at": "2026-09-01T00:00:00Z",
            "path": str(git_attributes_path),
            "sha256": builder.EXPECTED_GIT_ATTRIBUTES_SHA256,
            "bytes": builder.EXPECTED_GIT_ATTRIBUTES_BYTES,
            "rule_count": 15,
            "pattern_sha256": list(builder.GIT_ATTRIBUTES_PATTERN_SHA256),
            "attribute_tokens": ["text", "eol=lf"],
            "forbidden_attributes_absent": True,
            "git_top_level_attributes_absent": True,
            "git_info_attributes_absent": True,
            "system_attributes_disabled": True,
            "policy_sha256": hashlib.sha256(
                builder.canonical_json_bytes(builder.GIT_REPOSITORY_ATTRIBUTES_POLICY)
            ).hexdigest(),
        },
    )
    docker_context_metadata = {
        "path": str(builder.EXPECTED_DOCKER_CONTEXT_METADATA_PATH.resolve()),
        "sha256": builder.EXPECTED_DOCKER_CONTEXT_METADATA_SHA256,
        "bytes": builder.EXPECTED_DOCKER_CONTEXT_METADATA_BYTES,
    }
    docker_client_readback = artifact(
        "docker-client-config",
        builder.DOCKER_CLIENT_CONFIG_READBACK_SCHEMA,
        {
            "captured_at": "2026-09-01T00:00:00Z",
            "path": str(builder.EXPECTED_DOCKER_CLIENT_CONFIG_PATH.resolve()),
            "sha256": builder.EXPECTED_DOCKER_CLIENT_CONFIG_SHA256,
            "bytes": builder.EXPECTED_DOCKER_CLIENT_CONFIG_BYTES,
            "top_level_keys": list(builder.DOCKER_CLIENT_CONFIG_POLICY["top_level_keys"]),
            "auth_entries": 0,
            "credential_store_present": True,
            "credential_store_value_exposed": False,
            "current_context": "desktop-linux",
            "context_metadata": copy.deepcopy(docker_context_metadata),
            "endpoint_identity": copy.deepcopy(builder.DOCKER_CONTEXT_ENDPOINT_IDENTITY),
            "tls_material_directory_absent": True,
            "policy_sha256": hashlib.sha256(
                builder.canonical_json_bytes(builder.DOCKER_CLIENT_CONFIG_POLICY)
            ).hexdigest(),
        },
    )
    kubernetes_client_readback = artifact(
        "kubernetes-client-config",
        builder.KUBERNETES_CLIENT_CONFIG_READBACK_SCHEMA,
        {
            "captured_at": "2026-09-01T00:00:00Z",
            "path": str(builder.EXPECTED_KUBERNETES_CLIENT_CONFIG_PATH.resolve()),
            "sha256": builder.EXPECTED_KUBERNETES_CLIENT_CONFIG_SHA256,
            "bytes": builder.EXPECTED_KUBERNETES_CLIENT_CONFIG_BYTES,
            "current_context": "docker-desktop",
            "object_counts": copy.deepcopy(
                builder.KUBERNETES_CLIENT_CONFIG_POLICY["object_counts"]
            ),
            "context_identity": copy.deepcopy(
                builder.KUBERNETES_CLIENT_CONFIG_POLICY["context_identity"]
            ),
            "cluster_identity": copy.deepcopy(
                builder.KUBERNETES_CLIENT_CONFIG_POLICY["cluster_identity"]
            ),
            "user_identity": copy.deepcopy(
                builder.KUBERNETES_CLIENT_CONFIG_POLICY["user_identity"]
            ),
            "forbidden_fields_absent": list(
                builder.KUBERNETES_CLIENT_CONFIG_POLICY["forbidden_fields_absent"]
            ),
            "multiple_config_merge_forbidden": True,
            "embedded_material_presence": copy.deepcopy(
                builder.KUBERNETES_CLIENT_CONFIG_POLICY["embedded_material_presence"]
            ),
            "policy_sha256": hashlib.sha256(
                builder.canonical_json_bytes(builder.KUBERNETES_CLIENT_CONFIG_POLICY)
            ).hexdigest(),
        },
    )
    return {
        **host_pins,
        "git_repository_config": {
            "path": str(git_config_path),
            "sha256": builder.EXPECTED_GIT_CONFIG_SHA256,
            "bytes": builder.EXPECTED_GIT_CONFIG_BYTES,
            "policy": copy.deepcopy(builder.GIT_REPOSITORY_CONFIG_POLICY),
            "readback": git_config_readback,
        },
        "git_repository_attributes": {
            "path": str(git_attributes_path),
            "sha256": builder.EXPECTED_GIT_ATTRIBUTES_SHA256,
            "bytes": builder.EXPECTED_GIT_ATTRIBUTES_BYTES,
            "policy": copy.deepcopy(builder.GIT_REPOSITORY_ATTRIBUTES_POLICY),
            "readback": git_attributes_readback,
        },
        "docker_client_config": {
            "path": str(builder.EXPECTED_DOCKER_CLIENT_CONFIG_PATH.resolve()),
            "sha256": builder.EXPECTED_DOCKER_CLIENT_CONFIG_SHA256,
            "bytes": builder.EXPECTED_DOCKER_CLIENT_CONFIG_BYTES,
            "context_metadata": docker_context_metadata,
            "policy": copy.deepcopy(builder.DOCKER_CLIENT_CONFIG_POLICY),
            "readback": docker_client_readback,
        },
        "kubernetes_client_config": {
            "path": str(builder.EXPECTED_KUBERNETES_CLIENT_CONFIG_PATH.resolve()),
            "sha256": builder.EXPECTED_KUBERNETES_CLIENT_CONFIG_SHA256,
            "bytes": builder.EXPECTED_KUBERNETES_CLIENT_CONFIG_BYTES,
            "policy": copy.deepcopy(builder.KUBERNETES_CLIENT_CONFIG_POLICY),
            "readback": kubernetes_client_readback,
        },
        "python_distribution": {
            "implementation": "cpython",
            "name": "synthetic-test-python",
            "version": sys.version.split()[0],
            "base_prefix": str(tmp_path.resolve()),
            "distribution_tree_sha256": "2" * 64,
            "file_count": 1,
            "tree_encoding": builder.PYTHON_TREE_ENCODING,
            "included_roots": list(builder.PYTHON_INCLUDED_ROOTS),
            "excluded_roots": list(builder.PYTHON_EXCLUDED_ROOTS),
            "evidence": artifact(
                "python-distribution", builder.PYTHON_DISTRIBUTION_READBACK_SCHEMA
            ),
        },
        "git_distribution": {
            "root": str(builder.EXPECTED_GIT_ROOT.resolve()),
            "distribution_tree_sha256": "6" * 64,
            "file_count": 1,
            "tree_encoding": builder.GIT_TREE_ENCODING,
            "evidence": artifact("git-distribution", builder.GIT_DISTRIBUTION_READBACK_SCHEMA),
        },
        "windows_tcb": {
            "build": "test-build",
            "system32_path": str(tmp_path.resolve()),
            "kernel": copy.deepcopy(host_pin),
            "evidence": artifact("windows-tcb", builder.WINDOWS_TCB_READBACK_SCHEMA),
        },
        "wsl_runtime": {
            "distro": "test-distro",
            "kernel_release": "test-kernel",
            "rootfs_identity": "test-rootfs",
            "python3": {
                "realpath": "/usr/bin/python3",
                "sha256": "3" * 64,
                "bytes": 1,
                "version": "3.11.0",
            },
            "readback": artifact("wsl-runtime", builder.WSL_RUNTIME_READBACK_SCHEMA),
        },
        "container_psql": {
            "container_name": "evm-control-plane-postgres",
            "image_digest": "sha256:" + "4" * 64,
            "realpath": "/usr/bin/psql",
            "sha256": "5" * 64,
            "bytes": 1,
            "version": "psql 15",
            "execution_scope": copy.deepcopy(builder.DOCKER_CONTAINER_EXECUTION_SCOPE),
            "readback": artifact(
                "container-psql",
                builder.CONTAINER_PSQL_READBACK_SCHEMA,
                {
                    "captured_at": "2026-09-01T00:00:00Z",
                    "container_name": "evm-control-plane-postgres",
                    "image_digest": "sha256:" + "4" * 64,
                    "realpath": "/usr/bin/psql",
                    "sha256": "5" * 64,
                    "bytes": 1,
                    "version": "psql 15",
                    "execution_scope": copy.deepcopy(builder.DOCKER_CONTAINER_EXECUTION_SCOPE),
                },
            ),
        },
    }


def test_runtime_component_set_is_r7s1_only() -> None:
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
    from evm.scale_validation.phase_b2_r7s1 import (
        HISTORICAL_DECISION_AUTHORITY,
        HISTORICAL_QUERY_SHA256,
    )

    assert builder.HISTORICAL_QUERY_SHA256 == HISTORICAL_QUERY_SHA256
    assert builder.HISTORICAL_DECISION_AUTHORITY == HISTORICAL_DECISION_AUTHORITY


@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "renamed"])
def test_exact_link_table_scope_mutations_fail_closed(tmp_path: Path, mutation: str) -> None:
    external = _external_fencing(tmp_path)
    scan_path = Path(external["exact_link_scans"][0]["path"])  # type: ignore[index]
    payload = json.loads(scan_path.read_text(encoding="utf-8"))
    tables = payload["observed"]["control_plane_run_links"]
    if mutation == "empty":
        tables.clear()
    elif mutation == "missing":
        tables.pop()
    elif mutation == "duplicate":
        tables[-1] = copy.deepcopy(tables[0])
    else:
        tables[0]["table"] = "renamed_table"
    with pytest.raises(builder.BundleBuildError, match="table_set_mismatch"):
        builder._validate_zero_link_payload(
            payload,
            ordinal=1,
            captured_at=payload["captured_at"],
            target_run_id=external["target_identity"]["run_id"],  # type: ignore[index]
        )


def test_external_terminal_fencing_stale_or_absent_decision_is_rejected(
    tmp_path: Path,
) -> None:
    _external_fencing(tmp_path)
    document_path = tmp_path / "external-terminal-fencing-pins.json"
    with pytest.raises(builder.BundleBuildError, match="decision_stale|expired"):
        _revalidate_external(document_path, now=datetime.now(UTC) + timedelta(hours=2))
    document = json.loads(document_path.read_text(encoding="utf-8"))
    document["terminal_decision"] = None
    absent = tmp_path / "external-terminal-fencing-absent.json"
    _write_json(absent, document)
    with pytest.raises(builder.BundleBuildError, match="pin_object_required"):
        _revalidate_external(absent, now=datetime.now(UTC))


def test_self_consistent_snapshot_and_decision_rewrite_still_rejects_identity_change(
    tmp_path: Path,
) -> None:
    _external_fencing(tmp_path)
    document_path = tmp_path / "external-terminal-fencing-pins.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    snapshot_path = Path(document["snapshots"][0]["path"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["observed"]["mlflow_activity"][0]["status"] = "FINISHED"
    _write_json(snapshot_path, snapshot)
    document["snapshots"][0]["sha256"] = builder.sha256_file(snapshot_path)
    decision_path = Path(document["terminal_decision"]["path"])
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["supporting_sha256"]["historical_snapshot_1"] = document["snapshots"][0]["sha256"]
    _write_json(decision_path, decision)
    document["terminal_decision"]["sha256"] = builder.sha256_file(decision_path)
    rewritten = tmp_path / "external-terminal-fencing-rewritten.json"
    _write_json(rewritten, document)
    with pytest.raises(builder.BundleBuildError, match="target_state_mismatch"):
        _revalidate_external(rewritten, now=datetime.now(UTC))


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
    assert PROJECT.parent != PROJECT
    assert Path(observed["compose"]["config_path"]) == (PROJECT / "docker-compose.yml").resolve()  # type: ignore[index]
    assert (
        Path(observed["compose"]["config_path"])
        != (PROJECT.parent / "docker-compose.yml").resolve()
    )  # type: ignore[index]
    assert observed["compose"]["stability"]["samples"] == 61  # type: ignore[index]
    assert observed["kubernetes"]["health_confirmation_samples"] == 2  # type: ignore[index]


def test_runtime_state_rejects_self_consistent_repository_top_compose_repin(
    tmp_path: Path,
) -> None:
    state, _ = _runtime_state(tmp_path)
    git_top = tmp_path / "git-top"
    project_root = git_top / "enterprise-vision-mlops"
    project_root.mkdir(parents=True)
    repository_compose = git_top / "docker-compose.yml"
    repository_compose.write_bytes((PROJECT / "docker-compose.yml").read_bytes())
    compose = state["compose"]
    assert isinstance(compose, dict)
    compose["config_path"] = str(repository_compose.resolve())
    compose["config_sha256"] = builder.sha256_file(repository_compose)
    parent_root = tmp_path / "parents"
    parent_root.mkdir()
    entries, payloads, _ = _parent_fixture(parent_root, state)
    pins = tmp_path / "runtime-state-repository-top-compose.json"
    _write_json(pins, _pins_document(state, entries))

    with pytest.raises(builder.BundleBuildError, match="compose_config_path_mismatch"):
        builder.validate_runtime_state_pins(
            pins,
            project_root=project_root,
            source_identity=_source_identity(),
            parent_entries=entries,
            parent_payloads=payloads,
        )


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


def test_parent_role_swap_and_cross_link_rewrite_fail_closed(tmp_path: Path) -> None:
    state, _ = _runtime_state(tmp_path)
    _entries, _payloads, paths = _parent_fixture(tmp_path, state)
    swapped = {role: path.resolve() for role, path in paths.items()}
    swapped["r7_failure_seal"], swapped["r7_failure_index"] = (
        swapped["r7_failure_index"],
        swapped["r7_failure_seal"],
    )
    with pytest.raises(builder.BundleBuildError, match="parent_(?:schema|cross_link)"):
        builder.build_parent_checkpoints(swapped)

    r7_seal = json.loads(paths["r7_failure_seal"].read_text(encoding="utf-8"))
    r7_seal["pinned_evidence"]["failure_index_sha256"] = "f" * 64
    _write_json(paths["r7_failure_seal"], r7_seal)
    with pytest.raises(builder.BundleBuildError, match="parent_cross_link_missing"):
        builder.build_parent_checkpoints({role: path.resolve() for role, path in paths.items()})


def test_run_locations_are_exact_fixed_base_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_base = tmp_path / "fixed-staging"
    output_base = tmp_path / "fixed-output"
    monkeypatch.setattr(builder, "CANONICAL_STAGING_BASE", staging_base)
    monkeypatch.setattr(builder, "CANONICAL_OUTPUT_BASE", output_base)
    staging, output, emergency = builder.validate_canonical_run_locations(
        run_id=RUN_ID,
        staging_directory=staging_base / RUN_ID,
        output_directory=output_base / RUN_ID,
    )
    assert staging == (staging_base / RUN_ID).resolve(strict=False)
    assert output == (output_base / RUN_ID).resolve(strict=False)
    assert emergency == (output_base / f"{RUN_ID}-emergency-seal").resolve(strict=False)

    with pytest.raises(builder.BundleBuildError, match="not_canonical_run_location"):
        builder.validate_canonical_run_locations(
            run_id=RUN_ID,
            staging_directory=(tmp_path / "alternate-staging" / RUN_ID),
            output_directory=output_base / RUN_ID,
        )
    with pytest.raises(builder.BundleBuildError, match="not_canonical_run_location"):
        builder.validate_canonical_run_locations(
            run_id=RUN_ID,
            staging_directory=staging_base / RUN_ID / "nested-old-root",
            output_directory=output_base / RUN_ID,
        )


def test_run_location_reparse_redirect_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_staging = tmp_path / "real-staging"
    real_staging.mkdir()
    staging_link = tmp_path / "staging-junction"
    try:
        staging_link.symlink_to(real_staging, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows reparse-point creation unavailable: {exc}")
    output_base = tmp_path / "fixed-output"
    monkeypatch.setattr(builder, "CANONICAL_STAGING_BASE", staging_link)
    monkeypatch.setattr(builder, "CANONICAL_OUTPUT_BASE", output_base)
    with pytest.raises(builder.BundleBuildError, match="reparse_ancestor"):
        builder.validate_canonical_run_locations(
            run_id=RUN_ID,
            staging_directory=staging_link / RUN_ID,
            output_directory=output_base / RUN_ID,
        )


def test_post_staging_reparse_mutation_is_rejected_by_launcher_fence(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-staging"
    (real_root / RUN_ID).mkdir(parents=True)
    redirected_root = tmp_path / "mutated-staging-root"
    try:
        redirected_root.symlink_to(real_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows reparse-point creation unavailable: {exc}")
    target = redirected_root / RUN_ID
    reservation = target / "must-not-be-written.json"
    identity = builder.path_filesystem_identity(target)
    script = tmp_path / "post-staging-reparse-check.ps1"
    script.write_text(
        builder._render_location_fence_functions()
        + "\nAssert-BoundRunLocation "
        + " ".join(
            (
                builder._ps_literal(str(target)),
                builder._ps_literal(str(target)),
                builder._ps_literal(str(identity["volume_root"])),
                builder._ps_literal(str(identity["volume_serial"])),
                builder._ps_literal(str(identity["filesystem"])),
                "$true",
                "'staging'",
            )
        )
        + "\n[IO.File]::WriteAllText("
        + builder._ps_literal(str(reservation))
        + ", '{}')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "bound_run_location_reparse_ancestor:staging" in result.stdout + result.stderr
    assert not reservation.exists()


def test_manifest_is_restore_only_and_all_mutating_calls_are_zero(
    tmp_path: Path,
) -> None:
    state, _ = _runtime_state(tmp_path)
    entries, _, _ = _parent_fixture(tmp_path, state)
    runtime = _runtime_pins(tmp_path)
    run_id = "x1-phase-b2-r7s1-restore-test"
    external = _external_for_manifest(tmp_path, entries, run_id)
    manifest = builder.build_manifest(
        run_id=run_id,
        attempt_id=ATTEMPT_ID,
        successor_nonce=SUCCESSOR_NONCE,
        source_identity=_source_identity(),
        project_root=PROJECT,
        staging_directory=builder.CANONICAL_STAGING_BASE / run_id,
        output_directory=builder.CANONICAL_OUTPUT_BASE / run_id,
        emergency_seal_directory=(builder.CANONICAL_OUTPUT_BASE / f"{run_id}-emergency-seal"),
        python_path=Path(sys.executable),
        runtime=runtime,
        parent_checkpoints=entries,
        expected_state=state,
        external_terminal_fencing=external,
        expected_trusted_checkpoint_sha256=external["trusted_checkpoint"]["sha256"],  # type: ignore[index]
        toolchain=_toolchain(tmp_path),
    )
    assert manifest["execution_mode"] == "restore-only"
    assert manifest["schema_version"] == "evm.s8_v4.x1_phase_b2_r7s1_restore_work_order.v1"
    assert manifest["bundle"]["path"] == str((builder.CANONICAL_STAGING_BASE / run_id).resolve())  # type: ignore[index]
    assert manifest["probe_max_attempts"] == 1
    assert set(manifest["runtime"]) == set(builder.RUNTIME_PATHS)  # type: ignore[arg-type]
    assert manifest["process_containment"]["scope_boundaries"] == builder.PROCESS_SCOPE_BOUNDARIES
    assert (
        manifest["toolchain"]["container_psql"]["execution_scope"]
        == builder.DOCKER_CONTAINER_EXECUTION_SCOPE
    )
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
    from evm.scale_validation.phase_b2_r7s1 import validate_r7s1_manifest

    state, _ = _runtime_state(tmp_path)
    entries, _, _ = _parent_fixture(tmp_path, state)
    runtime = _runtime_pins(tmp_path)
    run_id = "x1-phase-b2-r7s1-core-integration"
    external = _external_for_manifest(tmp_path, entries, run_id)
    manifest = builder.build_manifest(
        run_id=run_id,
        attempt_id=ATTEMPT_ID,
        successor_nonce=SUCCESSOR_NONCE,
        source_identity=_source_identity(),
        project_root=PROJECT,
        staging_directory=builder.CANONICAL_STAGING_BASE / run_id,
        output_directory=builder.CANONICAL_OUTPUT_BASE / run_id,
        emergency_seal_directory=(builder.CANONICAL_OUTPUT_BASE / f"{run_id}-emergency-seal"),
        python_path=Path(sys.executable),
        runtime=runtime,
        parent_checkpoints=entries,
        expected_state=state,
        external_terminal_fencing=external,
        expected_trusted_checkpoint_sha256=external["trusted_checkpoint"]["sha256"],  # type: ignore[index]
        toolchain=_toolchain(tmp_path),
    )
    validated = validate_r7s1_manifest(
        manifest,
        expected_revision=REVISION,
        expected_untracked_path_set_sha256=EMPTY_UNTRACKED_DIGEST,
        expected_trusted_checkpoint_sha256=external["trusted_checkpoint"]["sha256"],  # type: ignore[index]
        verify_attestations=True,
    )
    assert validated["mode"] == "restore-only"
    assert validated["revision"] == REVISION


def test_rendered_launchers_are_restore_only_exact_once_and_ast_valid(
    tmp_path: Path,
) -> None:
    state, _ = _runtime_state(tmp_path)
    entries, _, _ = _parent_fixture(tmp_path, state)
    runtime = _runtime_pins(tmp_path)
    run_id = "x1-phase-b2-r7s1-restore-render"
    external = _external_for_manifest(tmp_path, entries, run_id)
    manifest = builder.build_manifest(
        run_id=run_id,
        attempt_id=ATTEMPT_ID,
        successor_nonce=SUCCESSOR_NONCE,
        source_identity=_source_identity(),
        project_root=PROJECT,
        staging_directory=builder.CANONICAL_STAGING_BASE / run_id,
        output_directory=builder.CANONICAL_OUTPUT_BASE / run_id,
        emergency_seal_directory=(builder.CANONICAL_OUTPUT_BASE / f"{run_id}-emergency-seal"),
        python_path=Path(sys.executable),
        runtime=runtime,
        parent_checkpoints=entries,
        expected_state=state,
        external_terminal_fencing=external,
        expected_trusted_checkpoint_sha256=external["trusted_checkpoint"]["sha256"],  # type: ignore[index]
        toolchain=_toolchain(tmp_path),
    )
    outer = builder.render_outer(
        bridge_sha256="e" * 64,
        run_id=manifest["bundle_id"],
        trusted_checkpoint_sha256=external["trusted_checkpoint"]["sha256"],  # type: ignore[index]
        toolchain=manifest["toolchain"],
        successor_binding=manifest["external_terminal_fencing"]["successor_binding"],
    )
    bridge = builder.render_bridge(
        manifest_sha256="f" * 64,
        manifest=manifest,
        runtime=runtime,
        project_root=PROJECT,
        source_identity=_source_identity(),
        python_path=Path(sys.executable),
    )
    assert outer.count("R7S1_BRIDGE_INVOKE_EXACTLY_ONCE") == 1
    assert bridge.count("R7S1_RUNNER_INVOKE_EXACTLY_ONCE") == 1
    assert outer.count("R7S1_CANONICAL_POWERSHELL_ENTRY_OUTER") == 1
    assert bridge.count("R7S1_CANONICAL_POWERSHELL_ENTRY_BRIDGE") == 1
    assert outer.count("R7S1_GIT_CONFIG_FENCE_OUTER_PREWRITE") == 1
    assert outer.count("R7S1_GIT_CONFIG_FENCE_OUTER_FINAL") == 1
    assert bridge.count("R7S1_GIT_CONFIG_FENCE_BRIDGE_PREWRITE") == 1
    assert bridge.count("R7S1_GIT_CONFIG_FENCE_BRIDGE_FINAL") == 1
    assert outer.count("Assert-GitRepositoryConfigPin $PinnedGitRepositoryConfigPath") == 2
    assert bridge.count("Assert-GitRepositoryConfigPin $PinnedGitRepositoryConfigPath") == 2
    assert outer.count("Assert-GitRepositoryAttributesPin $PinnedGitRepositoryAttributesPath") == 2
    assert bridge.count("Assert-GitRepositoryAttributesPin $PinnedGitRepositoryAttributesPath") == 2
    assert outer.count("R7S1_CLIENT_CONFIG_FENCE_OUTER_PREWRITE") == 1
    assert outer.count("R7S1_CLIENT_CONFIG_FENCE_OUTER_FINAL") == 1
    assert bridge.count("R7S1_CLIENT_CONFIG_FENCE_BRIDGE_PREWRITE") == 1
    assert bridge.count("R7S1_CLIENT_CONFIG_FENCE_BRIDGE_FINAL") == 1
    assert outer.count("[Environment]::GetCommandLineArgs()") == 1
    assert bridge.count("[Environment]::GetCommandLineArgs()") == 1
    assert "'-NoProfile','-NonInteractive','-File'" in outer
    assert "'-NoProfile','-NonInteractive','-File'" in bridge
    assert "canonical_powershell_entry_command_or_encoded_command_rejected" in outer
    assert "canonical_powershell_entry_command_or_encoded_command_rejected" in bridge
    assert outer.count("$stream.Write($bytes,0,$bytes.Length)") == 1
    assert outer.count("$stream.Flush($true)") == 1
    assert outer.count("$stream.Dispose()") == 1
    assert bridge.count("untracked_path_set_sha256=$ExpectedUntrackedDigestSha256") == 1
    assert "subprocess.run" not in bridge
    assert "ls-files" not in bridge
    assert "whoami.exe" not in bridge
    assert "& $ValidatorPath" not in bridge
    assert bridge.count("run_id=$PinnedRunId") == 2
    assert "launcherEvidence = [ordered]@{" in bridge
    assert outer.index("outer_sha256_mismatch_immediate") < outer.index(
        "R7S1_BRIDGE_INVOKE_EXACTLY_ONCE"
    )
    assert outer.index("bridge_sha256_mismatch_immediate") < outer.index(
        "R7S1_BRIDGE_INVOKE_EXACTLY_ONCE"
    )
    runner_boundary = bridge.index("R7S1_RUNNER_INVOKE_EXACTLY_ONCE")
    assert outer.index("R7S1_CANONICAL_POWERSHELL_ENTRY_OUTER") < outer.index(
        "$reservation = Join-Path $PSScriptRoot"
    )
    assert bridge.index("R7S1_CANONICAL_POWERSHELL_ENTRY_BRIDGE") < bridge.index(
        "$bridgeReservation = Join-Path $PSScriptRoot"
    )
    assert outer.index("R7S1_GIT_CONFIG_FENCE_OUTER_PREWRITE") < outer.index(
        "$reservation = Join-Path $PSScriptRoot"
    )
    assert bridge.index("R7S1_GIT_CONFIG_FENCE_BRIDGE_PREWRITE") < bridge.index(
        "$bridgeReservation = Join-Path $PSScriptRoot"
    )
    assert outer.index("R7S1_CLIENT_CONFIG_FENCE_OUTER_PREWRITE") < outer.index(
        "$reservation = Join-Path $PSScriptRoot"
    )
    assert bridge.index("R7S1_CLIENT_CONFIG_FENCE_BRIDGE_PREWRITE") < bridge.index(
        "$bridgeReservation = Join-Path $PSScriptRoot"
    )
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
    assert "-I -S -B $RunnerPath" in bridge
    assert "--expected-trusted-checkpoint-sha256 $trustedCheckpointExpected" in bridge
    assert "--checkpoint" not in bridge
    assert "process_base" not in bridge
    assert "'fresh'" not in outer + bridge
    for old in ("phase_b2_r3.py", "phase_b2_r4.py", "phase_b2_r5.py"):
        assert old not in outer + bridge
    builder_source = Path(builder.__file__).read_text(encoding="utf-8")
    offline_validator_block = builder_source[
        builder_source.index('name="r7s1-builder-contained-offline-validator"')
        - 900 : builder_source.index('name="r7s1-builder-contained-offline-validator"')
    ]
    assert '"-NoProfile"' in offline_validator_block
    assert '"-NonInteractive"' in offline_validator_block
    assert '"-File"' in offline_validator_block
    assert '"-ExecutionPolicy"' not in offline_validator_block
    assert '"-NoLogo"' not in offline_validator_block
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
    wrong_project = tmp_path / "alternate" / "enterprise-vision-mlops"
    wrong_core = wrong_project / builder.RUNTIME_PATHS["core"]
    wrong_core.parent.mkdir(parents=True)
    (wrong_core.parent / "__init__.py").write_text("", encoding="utf-8")
    (wrong_core.parents[1] / "__init__.py").write_text("", encoding="utf-8")
    wrong_core.write_text(
        f"HISTORICAL_QUERY_SHA256 = {builder.HISTORICAL_QUERY_SHA256!r}\n"
        f"HISTORICAL_DECISION_AUTHORITY = {builder.HISTORICAL_DECISION_AUTHORITY!r}\n"
        "def validate_r7s1_manifest(*args, **kwargs):\n"
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
    wrong_compose = wrong_project / "docker-compose.yml"
    wrong_compose.write_bytes((PROJECT / "docker-compose.yml").read_bytes())
    runtime["docker_compose"].update(
        {
            "path": str(wrong_compose.resolve()),
            "sha256": builder.sha256_file(wrong_compose),
            "bytes": wrong_compose.stat().st_size,
        }
    )
    compose_state = state["compose"]
    assert isinstance(compose_state, dict)
    compose_state["config_path"] = str(wrong_compose.resolve())
    compose_state["config_sha256"] = builder.sha256_file(wrong_compose)
    entries, _, _ = _parent_fixture(tmp_path, state)
    run_id = "x1-phase-b2-r7s1-wrong-core"
    external = _external_for_manifest(
        tmp_path,
        entries,
        run_id,
        staging_directory=builder.CANONICAL_STAGING_BASE / run_id,
        output_directory=builder.CANONICAL_OUTPUT_BASE / run_id,
    )
    with pytest.raises(builder.BundleBuildError, match="WRONG_CORE_SENTINEL"):
        builder.build_manifest(
            run_id=run_id,
            attempt_id=ATTEMPT_ID,
            successor_nonce=SUCCESSOR_NONCE,
            source_identity=_source_identity(),
            project_root=wrong_project,
            staging_directory=builder.CANONICAL_STAGING_BASE / run_id,
            output_directory=builder.CANONICAL_OUTPUT_BASE / run_id,
            emergency_seal_directory=(builder.CANONICAL_OUTPUT_BASE / f"{run_id}-emergency-seal"),
            python_path=Path(sys.executable),
            runtime=runtime,
            parent_checkpoints=entries,
            expected_state=state,
            external_terminal_fencing=external,
            expected_trusted_checkpoint_sha256=external["trusted_checkpoint"]["sha256"],  # type: ignore[index]
            toolchain=_toolchain(tmp_path),
        )


def test_bootstrap_process_loader_ignores_shadowed_evm_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = tmp_path / "shadow"
    shadow_init = shadow / "evm" / "__init__.py"
    shadow_init.parent.mkdir(parents=True)
    shadow_init.write_text(
        "raise RuntimeError('SHADOW_EVM_INITIALIZER_EXECUTED')\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(shadow))
    monkeypatch.delitem(sys.modules, "_evm_r7s1_builder_verified_process", raising=False)
    monkeypatch.setitem(sys.modules, "evm.scale_validation.phase_b2_r7_process", object())

    outcome = builder._run_contained(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import time; print('contained-ok', flush=True); time.sleep(0.35)",
        ],
        name="test-bootstrap-shadow-rejection",
        cwd=PROJECT,
    )
    assert outcome.return_code == 0
    assert outcome.stdout.strip() == "contained-ok"
    loaded = sys.modules["_evm_r7s1_builder_verified_process"]
    assert Path(loaded.__file__).resolve() == (PROJECT / builder.RUNTIME_PATHS["process"]).resolve()
    assert isinstance(loaded.__loader__, builder.importlib.machinery.SourceFileLoader)


def test_bootstrap_process_pin_mismatch_blocks_before_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "BOOTSTRAP_PROCESS_SHA256", "0" * 64)
    monkeypatch.delitem(sys.modules, "_evm_r7s1_builder_verified_process", raising=False)
    with pytest.raises(builder.BundleBuildError, match="contained_process_bootstrap_pin_mismatch"):
        builder._run_contained(
            [sys.executable, "-I", "-S", "-B", "-c", "raise SystemExit(99)"],
            name="test-bootstrap-pin-mismatch",
            cwd=PROJECT,
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


def test_source_pin_distinguishes_crlf_worktree_and_normalized_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "enterprise-vision-mlops"
    relative = Path("scripts/dev/crlf-source.py")
    source = project / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b"line_one = 1\r\nline_two = 2\r\n")
    raw_oid = builder.worktree_blob_oid(source)
    normalized = source.read_bytes().replace(b"\r\n", b"\n")
    normalized_oid = hashlib.sha1(
        f"blob {len(normalized)}\0".encode("ascii") + normalized,
        usedforsecurity=False,
    ).hexdigest()
    assert raw_oid != normalized_oid
    monkeypatch.setattr(builder, "git_head_blob_oid", lambda _repo, _path: normalized_oid)
    monkeypatch.setattr(
        builder,
        "git_normalized_worktree_blob_oid",
        lambda _repo, _path: normalized_oid,
    )

    pin = builder.source_pin(project, relative)

    assert pin == {
        "path": str(source.resolve()),
        "sha256": builder.sha256_file(source),
        "worktree_blob_oid": raw_oid,
        "head_blob_oid": normalized_oid,
        "bytes": source.stat().st_size,
    }


def test_source_pin_rejects_normalized_head_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "enterprise-vision-mlops"
    relative = Path("scripts/dev/crlf-source.py")
    source = project / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b"line_one = 1\r\n")
    monkeypatch.setattr(builder, "git_head_blob_oid", lambda _repo, _path: "1" * 40)
    monkeypatch.setattr(
        builder,
        "git_normalized_worktree_blob_oid",
        lambda _repo, _path: "2" * 40,
    )

    with pytest.raises(builder.BundleBuildError, match="runtime_source_normalized_blob_mismatch"):
        builder.source_pin(project, relative)

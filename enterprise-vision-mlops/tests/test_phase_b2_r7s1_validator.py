from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pytest

from evm.scale_validation.phase_b2_r7s1 import (
    EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES,
    FAILED_POD_IDENTITY_FIELDS,
    OBSERVATION_SOURCE_REVISION,
)
from evm.scale_validation.phase_b2_r7_process import (
    TimeoutContract,
    WindowsJobProcessRunner,
)
from scripts.dev import prepare_x1_phase_b2_r7s1_bundle as builder


PROJECT = Path(__file__).parents[1]
VALIDATOR_SOURCE = PROJECT / "scripts" / "dev" / "validate_phase_b2_r7s1_bundle.ps1"
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
VALIDATOR_RUN_ID = "x1-phase-b2-r7s1-validator-fixture"
VALIDATOR_ATTEMPT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
VALIDATOR_SUCCESSOR_NONCE = "d" * 64

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
    # Exercise the production Windows normalization boundary explicitly: HEAD
    # may store LF while the checked-out executable source remains CRLF.
    result = _run("git", "-c", "core.autocrlf=true", "-C", repo, *args)
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
    external_terminal_fencing: dict[str, Any]
    toolchain: dict[str, Any]
    staging_root: Path
    output_root: Path


def _toolchain(
    evidence: Path,
    git_config_path: Path,
    git_attributes_path: Path,
) -> dict[str, Any]:
    executable = Path(sys.executable).resolve()

    def host(path: Path = executable) -> dict[str, Any]:
        return {
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "bytes": path.stat().st_size,
            "version": "test-version",
            "signature": {
                "status": "valid",
                "subject": "Synthetic test signer",
                "thumbprint": "1" * 40,
            },
        }

    def artifact(name: str, schema: str, extra: dict[str, Any] | None = None) -> dict[str, str]:
        path = evidence / f"{name}.json"
        _write_json(path, {"schema": schema, "status": "verified", **(extra or {})})
        return {"path": str(path.resolve()), "sha256": _sha(path), "schema": schema}

    tools = {role: host() for role in builder.HOST_TOOLCHAIN_ROLES}
    tools["git"] = host(builder.EXPECTED_GIT_PATH)
    tools["docker_compose"] = host(builder.EXPECTED_DOCKER_COMPOSE_PATH)
    tools["powershell"] = host(POWERSHELL)
    git_config_sha = _sha(git_config_path)
    git_config_bytes = git_config_path.stat().st_size
    git_config_readback = artifact(
        "git-repository-config",
        builder.GIT_REPOSITORY_CONFIG_READBACK_SCHEMA,
        {
            "captured_at": "2026-09-01T00:00:00Z",
            "path": str(git_config_path.resolve()),
            "sha256": git_config_sha,
            "bytes": git_config_bytes,
            "key_names": list(builder.GIT_CONFIG_ALLOWED_KEY_NAMES),
            "origin_identity": copy.deepcopy(builder.GIT_CONFIG_ORIGIN_IDENTITY),
            "config_worktree_absent": True,
            "policy_sha256": hashlib.sha256(
                builder.canonical_json_bytes(builder.GIT_REPOSITORY_CONFIG_POLICY)
            ).hexdigest(),
        },
    )
    git_attributes_sha = _sha(git_attributes_path)
    git_attributes_bytes = git_attributes_path.stat().st_size
    git_attributes_readback = artifact(
        "git-repository-attributes",
        builder.GIT_REPOSITORY_ATTRIBUTES_READBACK_SCHEMA,
        {
            "captured_at": "2026-09-01T00:00:00Z",
            "path": str(git_attributes_path.resolve()),
            "sha256": git_attributes_sha,
            "bytes": git_attributes_bytes,
            "rule_count": 20,
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
    tools.update(
        {
            "git_repository_config": {
                "path": str(git_config_path.resolve()),
                "sha256": git_config_sha,
                "bytes": git_config_bytes,
                "policy": copy.deepcopy(builder.GIT_REPOSITORY_CONFIG_POLICY),
                "readback": git_config_readback,
            },
            "git_repository_attributes": {
                "path": str(git_attributes_path.resolve()),
                "sha256": git_attributes_sha,
                "bytes": git_attributes_bytes,
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
                "version": "test-version",
                "base_prefix": str(evidence.resolve()),
                "distribution_tree_sha256": "2" * 64,
                "file_count": 1,
                "tree_encoding": builder.PYTHON_TREE_ENCODING,
                "included_roots": list(builder.PYTHON_INCLUDED_ROOTS),
                "excluded_roots": list(builder.PYTHON_EXCLUDED_ROOTS),
                "evidence": artifact(
                    "python-distribution",
                    builder.PYTHON_DISTRIBUTION_READBACK_SCHEMA,
                    {
                        "captured_at": "2026-09-01T00:00:00Z",
                        "implementation": "cpython",
                        "name": "synthetic-test-python",
                        "version": "test-version",
                        "base_prefix": str(evidence.resolve()),
                        "distribution_tree_sha256": "2" * 64,
                        "file_count": 1,
                        "tree_encoding": builder.PYTHON_TREE_ENCODING,
                        "included_roots": list(builder.PYTHON_INCLUDED_ROOTS),
                        "excluded_roots": list(builder.PYTHON_EXCLUDED_ROOTS),
                    },
                ),
            },
            "git_distribution": {
                "root": str(builder.EXPECTED_GIT_ROOT.resolve()),
                "distribution_tree_sha256": "6" * 64,
                "file_count": 1,
                "tree_encoding": builder.GIT_TREE_ENCODING,
                "evidence": artifact(
                    "git-distribution",
                    builder.GIT_DISTRIBUTION_READBACK_SCHEMA,
                    {
                        "captured_at": "2026-09-01T00:00:00Z",
                        "root": str(builder.EXPECTED_GIT_ROOT.resolve()),
                        "distribution_tree_sha256": "6" * 64,
                        "file_count": 1,
                        "tree_encoding": builder.GIT_TREE_ENCODING,
                        "volume_identity": "synthetic-volume",
                        "filesystem_identity": "synthetic-ntfs",
                        "reparse_entries": 0,
                    },
                ),
            },
            "windows_tcb": {
                "build": "test-build",
                "system32_path": str(evidence.resolve()),
                "kernel": host(),
                "evidence": artifact(
                    "windows-tcb",
                    builder.WINDOWS_TCB_READBACK_SCHEMA,
                    {
                        "captured_at": "2026-09-01T00:00:00Z",
                        "build": "test-build",
                        "system32_path": str(evidence.resolve()),
                        "kernel": host(),
                    },
                ),
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
                "readback": artifact(
                    "wsl-runtime",
                    builder.WSL_RUNTIME_READBACK_SCHEMA,
                    {
                        "captured_at": "2026-09-01T00:00:00Z",
                        "distro": "test-distro",
                        "kernel_release": "test-kernel",
                        "rootfs_identity": "test-rootfs",
                        "python3": {
                            "realpath": "/usr/bin/python3",
                            "sha256": "3" * 64,
                            "bytes": 1,
                            "version": "3.11.0",
                        },
                    },
                ),
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
    )
    return tools


def _failed_pods() -> list[dict[str, object]]:
    return [
        dict(zip(FAILED_POD_IDENTITY_FIELDS, identity, strict=True))
        for identity in EXPECTED_HISTORICAL_FAILED_POD_IDENTITIES
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
    classifications: list[dict[str, Any]] = []
    for source, records in (
        ("control_plane_task_entity_statuses", cp_records),
        ("mlflow_running_rows", mlflow_records),
        ("kubernetes_terminal_failed_objects", kubernetes_records),
    ):
        for index, record in enumerate(records):
            if "execution_proof" not in record:
                continue
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


def _observation_commands(names: tuple[str, ...], captured_at: str) -> list[dict[str, Any]]:
    empty_sha = hashlib.sha256(b"").hexdigest()
    command_class = "snapshot" if names == builder.SNAPSHOT_COMMAND_NAMES else "link"
    commands: list[dict[str, Any]] = []
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


def _external_fencing(evidence: Path, *, successor_binding: dict[str, str]) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0)
    identity = {
        "run_id": "9bd54156084842ca93bce35a44a0cea7",
        "status": "RUNNING",
        "lifecycle_stage": "active",
        "start_time": "1783653474422",
        "end_time": "",
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
    pins: list[dict[str, Any]] = []
    source_revision = OBSERVATION_SOURCE_REVISION
    for ordinal, seconds in enumerate((300, 240), start=1):
        captured_at = (now - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
        path = evidence / f"historical-snapshot-{ordinal}.json"
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
                "sha256": _sha(path),
                "captured_at": captured_at,
                "schema": builder.HISTORICAL_SNAPSHOT_SCHEMA,
                "source_revision": source_revision,
                "target_identity_sha256": identity_sha,
                "decision_authority": builder.EXTERNAL_DECISION_AUTHORITY,
            }
        )
    for ordinal, seconds in enumerate((180, 120), start=1):
        captured_at = (now - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
        path = evidence / f"exact-link-scan-{ordinal}.json"
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
                "sha256": _sha(path),
                "captured_at": captured_at,
                "schema": builder.TARGET_LINK_SCAN_SCHEMA,
                "source_revision": source_revision,
                "target_identity_sha256": identity_sha,
                "decision_authority": builder.EXTERNAL_DECISION_AUTHORITY,
            }
        )
    issued_at = (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    decision = evidence / "terminal-fencing-decision.json"
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
    decision_sha = _sha(decision)
    supporting = {
        "historical_snapshot_1": pins[0]["sha256"],
        "historical_snapshot_2": pins[1]["sha256"],
        "exact_link_scan_1": pins[2]["sha256"],
        "exact_link_scan_2": pins[3]["sha256"],
        "historical_snapshot_1_target_activity_sha256": activity_sha,
        "historical_snapshot_2_target_activity_sha256": activity_sha,
        "successor_binding_sha256": builder._canonical_object_sha256(successor_binding),
    }
    checkpoint = evidence / "trusted-checkpoint.json"
    _write_json(
        checkpoint,
        {
            "schema": builder.TRUSTED_CHECKPOINT_SCHEMA,
            "checkpointed_at": (now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
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
                "read_back_at": (now - timedelta(seconds=40)).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    checkpoint_sha = _sha(checkpoint)
    document = evidence / "external-terminal-fencing-pins.json"
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


@pytest.fixture(scope="module")
def validator_fixture(tmp_path_factory: pytest.TempPathFactory) -> ValidatorFixture:
    if not POWERSHELL.is_file() or shutil.which("git") is None:
        pytest.skip("PowerShell and git required")
    base = tmp_path_factory.mktemp("r7s1-validator")
    git_root = base / "repo"
    project = git_root / "enterprise-vision-mlops"
    remote = base / "remote.git"
    branch = "codex/distributed-scale-validation-plan"
    project.mkdir(parents=True)
    assert _run("git", "init", "--bare", remote).returncode == 0
    assert _run("git", "init", "-b", branch, git_root).returncode == 0
    _git(git_root, "config", "user.email", "r7s1-validator@example.invalid")
    _git(git_root, "config", "user.name", "R7S1 Validator")
    _git(git_root, "config", "core.symlinks", "false")
    _git(git_root, "config", "core.ignorecase", "true")
    _git(git_root, "config", "extensions.worktreeConfig", "true")
    for extra_branch in (
        "codex/local-infra-mvp",
        "codex/mac-mini-worker",
        "codex/x1-resume-results-20260825-215716",
    ):
        _git(git_root, "config", f"branch.{extra_branch}.remote", "origin")
        _git(git_root, "config", f"branch.{extra_branch}.merge", f"refs/heads/{extra_branch}")
    _write(git_root / "fixture-seed.txt", "seed\n")
    _git(git_root, "add", "fixture-seed.txt")
    _git(git_root, "commit", "-m", "fixture seed")
    _git(git_root, "remote", "add", "origin", str(remote))
    _git(git_root, "push", "-u", "origin", branch)
    _git(git_root, "remote", "set-url", "origin", builder.CANONICAL_GIT_REMOTE_URL)
    git_config_path = git_root / ".git" / "config"
    git_config_sha = _sha(git_config_path)
    git_config_bytes = git_config_path.stat().st_size
    git_attributes_path = project / ".gitattributes"
    git_top_attributes_path = git_root / ".gitattributes"
    git_info_attributes_path = git_root / ".git" / "info" / "attributes"
    _write(git_attributes_path, (PROJECT / ".gitattributes").read_bytes())
    git_attributes_sha = _sha(git_attributes_path)
    git_attributes_bytes = git_attributes_path.stat().st_size

    paths = {
        "builder": project / "scripts" / "dev" / "prepare_x1_phase_b2_r7s1_bundle.py",
        "core": project / "src" / "evm" / "scale_validation" / "phase_b2_r7s1.py",
        "process": project / "src" / "evm" / "scale_validation" / "phase_b2_r7_process.py",
        "runner": project / "scripts" / "dev" / "run_x1_phase_b2_r7s1.py",
        "validator": project / "scripts" / "dev" / "validate_phase_b2_r7s1_bundle.ps1",
        "docker_compose": project / "docker-compose.yml",
    }
    _write(paths["builder"], (PROJECT / builder.RUNTIME_PATHS["builder"]).read_bytes())
    validator_text = VALIDATOR_SOURCE.read_text(encoding="utf-8")
    validator_text = validator_text.replace(
        "C:\\Users\\mlops\\EnterpriseMLOps_Project\\.git\\config",
        str(git_config_path.resolve()),
    )
    validator_text = validator_text.replace(
        "C:\\Users\\mlops\\EnterpriseMLOps_Project\\enterprise-vision-mlops\\.gitattributes",
        str(git_attributes_path.resolve()),
    )
    validator_text = validator_text.replace(
        "C:\\Users\\mlops\\EnterpriseMLOps_Project\\.gitattributes",
        str(git_top_attributes_path.resolve()),
    )
    validator_text = validator_text.replace(
        "C:\\Users\\mlops\\EnterpriseMLOps_Project\\.git\\info\\attributes",
        str(git_info_attributes_path.resolve()),
    )
    validator_text = validator_text.replace(builder.CANONICAL_GIT_REMOTE_URL, str(remote.resolve()))
    validator_text = validator_text.replace(builder.EXPECTED_GIT_CONFIG_SHA256, git_config_sha)
    validator_text = validator_text.replace(
        builder.EXPECTED_GIT_ATTRIBUTES_SHA256, git_attributes_sha
    )
    validator_text = validator_text.replace(
        "[int64]$gitRepositoryConfigPin.bytes -eq 787",
        f"[int64]$gitRepositoryConfigPin.bytes -eq {git_config_bytes}",
    )
    validator_text = validator_text.replace(
        (f"[int64]$gitRepositoryAttributesPin.bytes -eq {builder.EXPECTED_GIT_ATTRIBUTES_BYTES}"),
        f"[int64]$gitRepositoryAttributesPin.bytes -eq {git_attributes_bytes}",
    )
    _write(paths["validator"], validator_text)
    _write(paths["core"], (PROJECT / builder.RUNTIME_PATHS["core"]).read_bytes())
    staging_root = (base / "allowed-staging-root").resolve()
    output_root = (base / "allowed-output-root").resolve()
    core_text = paths["core"].read_text(encoding="utf-8")
    core_text, staging_replacements = re.subn(
        r"CANONICAL_STAGING_ROOT = Path\(\n.*?\n\)\.resolve\(\)",
        lambda _match: (f"CANONICAL_STAGING_ROOT = Path({str(staging_root)!r}).resolve()"),
        core_text,
        count=1,
        flags=re.DOTALL,
    )
    core_text, output_replacements = re.subn(
        r"CANONICAL_OUTPUT_ROOT = Path\(\n.*?\n\)\.resolve\(\)",
        lambda _match: f"CANONICAL_OUTPUT_ROOT = Path({str(output_root)!r}).resolve()",
        core_text,
        count=1,
        flags=re.DOTALL,
    )
    core_text, config_path_replacements = re.subn(
        r"CANONICAL_GIT_CONFIG_PATH = Path\([^\n]+\)\.resolve\(\)",
        lambda _match: f"CANONICAL_GIT_CONFIG_PATH = Path({str(git_config_path.resolve())!r}).resolve()",
        core_text,
        count=1,
    )
    core_text, config_sha_replacements = re.subn(
        r'CANONICAL_GIT_CONFIG_SHA256 = "[0-9a-f]{64}"',
        f'CANONICAL_GIT_CONFIG_SHA256 = "{git_config_sha}"',
        core_text,
        count=1,
    )
    core_text, config_bytes_replacements = re.subn(
        r"CANONICAL_GIT_CONFIG_BYTES = \d+",
        f"CANONICAL_GIT_CONFIG_BYTES = {git_config_bytes}",
        core_text,
        count=1,
    )
    core_text, attributes_path_replacements = re.subn(
        r"CANONICAL_GIT_ATTRIBUTES_PATH = Path\(\n.*?\n\)\.resolve\(\)",
        lambda _match: (
            f"CANONICAL_GIT_ATTRIBUTES_PATH = Path({str(git_attributes_path.resolve())!r}).resolve()"
        ),
        core_text,
        count=1,
        flags=re.DOTALL,
    )
    core_text, attributes_sha_replacements = re.subn(
        r'CANONICAL_GIT_ATTRIBUTES_SHA256 = "[0-9a-f]{64}"',
        f'CANONICAL_GIT_ATTRIBUTES_SHA256 = "{git_attributes_sha}"',
        core_text,
        count=1,
    )
    core_text, attributes_bytes_replacements = re.subn(
        r"CANONICAL_GIT_ATTRIBUTES_BYTES = \d+",
        f"CANONICAL_GIT_ATTRIBUTES_BYTES = {git_attributes_bytes}",
        core_text,
        count=1,
    )
    core_text, top_attributes_path_replacements = re.subn(
        r"CANONICAL_GIT_TOP_ATTRIBUTES_PATH = Path\(\n.*?\n\)\.resolve\(\)",
        lambda _match: (
            f"CANONICAL_GIT_TOP_ATTRIBUTES_PATH = Path({str(git_top_attributes_path.resolve())!r}).resolve()"
        ),
        core_text,
        count=1,
        flags=re.DOTALL,
    )
    core_text, info_attributes_path_replacements = re.subn(
        r"CANONICAL_GIT_INFO_ATTRIBUTES_PATH = Path\(\n.*?\n\)\.resolve\(\)",
        lambda _match: (
            f"CANONICAL_GIT_INFO_ATTRIBUTES_PATH = Path({str(git_info_attributes_path.resolve())!r}).resolve()"
        ),
        core_text,
        count=1,
        flags=re.DOTALL,
    )
    assert (
        staging_replacements
        == output_replacements
        == config_path_replacements
        == config_sha_replacements
        == config_bytes_replacements
        == attributes_path_replacements
        == attributes_sha_replacements
        == attributes_bytes_replacements
        == top_attributes_path_replacements
        == info_attributes_path_replacements
        == 1
    )
    _write(paths["core"], core_text)
    _write(paths["process"], (PROJECT / builder.RUNTIME_PATHS["process"]).read_bytes())
    _write(paths["runner"], (PROJECT / builder.RUNTIME_PATHS["runner"]).read_bytes())
    _write(paths["docker_compose"], (PROJECT / "docker-compose.yml").read_bytes())
    _write(git_root / "docker-compose.yml", (PROJECT / "docker-compose.yml").read_bytes())
    _write(project / "src" / "evm" / "__init__.py", "")
    _write(project / "src" / "evm" / "scale_validation" / "__init__.py", "")
    versions = builder.source_schema_versions(PROJECT)
    _write(
        project / "src" / "evm" / "control_panel" / "transactional_store.py",
        "SCHEMA_VERSIONS = (\n" + "".join(f'    "{version}",\n' for version in versions) + ")\n",
    )
    _write(project / "src" / "evm" / "control_panel" / "__init__.py", "")

    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "fixture r7s1 sources")
    _git(git_root, "push", str(remote), branch)
    revision = _git(git_root, "rev-parse", "HEAD")
    _git(git_root, "update-ref", f"refs/remotes/origin/{branch}", revision)
    tree = _git(git_root, "rev-parse", "HEAD^{tree}")
    for index in range(4_244):
        _write(git_root / "user-untracked" / f"preserved-{index:04d}.txt", "")
    raw_untracked = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            # Match the fixture's explicit Git-for-Windows checkout policy even
            # when the validation harness suppresses system Git configuration.
            "-c",
            "core.autocrlf=true",
            "-C",
            str(git_root),
            "-c",
            "core.quotepath=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        check=False,
        capture_output=True,
    )
    assert raw_untracked.returncode == 0
    records = [record for record in raw_untracked.stdout.split(b"\0") if record]
    assert all(record.startswith(b"?? ") for record in records)
    untracked_paths = sorted(record[3:].decode("utf-8") for record in records)
    untracked_hash = hashlib.sha256()
    for untracked_path in untracked_paths:
        untracked_hash.update(untracked_path.encode("utf-8") + b"\0")
    untracked_count, untracked_digest = len(untracked_paths), untracked_hash.hexdigest()
    assert untracked_count == 4_244

    runtime: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        relative = path.relative_to(git_root).as_posix()
        runtime[name] = {
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "worktree_blob_oid": builder.worktree_blob_oid(path),
            "head_blob_oid": _git(git_root, "rev-parse", f"HEAD:{relative}"),
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
    parent_paths = {role: evidence / f"{role}.json" for role in builder.REQUIRED_PARENT_ROLES}
    _write_json(
        parent_paths["r5_failure_seal"],
        {
            "schema": builder.PARENT_SCHEMAS["r5_failure_seal"],
            "metadata": {"run_id": "r5-chain-run"},
        },
    )
    _write_json(
        parent_paths["r5_failure_index"],
        {
            "schema": builder.PARENT_SCHEMAS["r5_failure_index"],
            "files": [{"sha256": _sha(parent_paths["r5_failure_seal"])}],
        },
    )
    _write_json(
        parent_paths["r6_compose_rca"],
        {"schema": builder.PARENT_SCHEMAS["r6_compose_rca"], "run_identity": "r6-chain-run"},
    )
    _write_json(
        parent_paths["r6_failure_seal_amendment"],
        {
            "schema": builder.PARENT_SCHEMAS["r6_failure_seal_amendment"],
            "base_rca_sha256": _sha(parent_paths["r6_compose_rca"]),
        },
    )
    _write_json(
        parent_paths["r6_final_index"],
        {
            "schema": builder.PARENT_SCHEMAS["r6_final_index"],
            "seal_amendment_sha256": _sha(parent_paths["r6_failure_seal_amendment"]),
        },
    )
    _write_json(
        parent_paths["post_manual_on_readback"],
        {
            "schema": builder.PARENT_SCHEMAS["post_manual_on_readback"],
            "runtime_state": expected_state,
        },
    )
    readback_sha = _sha(parent_paths["post_manual_on_readback"])
    _write_json(
        parent_paths["post_manual_on_index"],
        {
            "schema": builder.PARENT_SCHEMAS["post_manual_on_index"],
            "files": [
                {"sha256": _sha(parent_paths["r6_final_index"])},
                {
                    "path": str(parent_paths["post_manual_on_readback"].resolve()),
                    "sha256": readback_sha,
                },
            ],
        },
    )
    _write_json(
        parent_paths["r7_failure_index"],
        {"schema": builder.PARENT_SCHEMAS["r7_failure_index"], "run_identity": "r7-chain-run"},
    )
    _write_json(
        parent_paths["r7_failure_seal"],
        {
            "schema": builder.PARENT_SCHEMAS["r7_failure_seal"],
            "run_identity": "r7-chain-run",
            "pinned_evidence": {"failure_index_sha256": _sha(parent_paths["r7_failure_index"])},
        },
    )
    _write_json(
        parent_paths["r7_post_seal_residual_amendment"],
        {
            "schema": builder.PARENT_SCHEMAS["r7_post_seal_residual_amendment"],
            "parent_failure_seal_sha256": _sha(parent_paths["r7_failure_seal"]),
        },
    )
    parents, _ = builder.build_parent_checkpoints(
        {role: path.resolve() for role, path in parent_paths.items()}
    )
    successor_binding = {
        "run_id": VALIDATOR_RUN_ID,
        "attempt_id": VALIDATOR_ATTEMPT_ID,
        "commit": revision,
        "tree": tree,
        "nonce": VALIDATOR_SUCCESSOR_NONCE,
        "parent_map_sha256": builder.parent_map_sha256(parents),
        "staging_path": str((staging_root / VALIDATOR_RUN_ID).resolve()),
        "output_path": str((output_root / VALIDATOR_RUN_ID).resolve()),
        "emergency_seal_path": str((output_root / f"{VALIDATOR_RUN_ID}-emergency-seal").resolve()),
    }
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
        external_terminal_fencing=_external_fencing(evidence, successor_binding=successor_binding),
        toolchain=_toolchain(evidence, git_config_path, git_attributes_path),
        staging_root=staging_root,
        output_root=output_root,
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
    run_id = f"x1-phase-b2-r7s1-{tmp_path.name}"
    stage = fixture.staging_root / run_id
    output = fixture.output_root / run_id
    successor_binding = {
        "run_id": run_id,
        "attempt_id": VALIDATOR_ATTEMPT_ID,
        "commit": fixture.revision,
        "tree": fixture.tree,
        "nonce": VALIDATOR_SUCCESSOR_NONCE,
        "parent_map_sha256": builder.parent_map_sha256(fixture.parents),
        "staging_path": str(stage.resolve()),
        "output_path": str(output.resolve()),
        "emergency_seal_path": str((fixture.output_root / f"{run_id}-emergency-seal").resolve()),
    }
    external_terminal_fencing = _external_fencing(
        tmp_path / "external-fencing", successor_binding=successor_binding
    )
    runtime = copy.deepcopy(fixture.runtime)
    manifest = builder.build_manifest(
        run_id=run_id,
        attempt_id=VALIDATOR_ATTEMPT_ID,
        successor_nonce=VALIDATOR_SUCCESSOR_NONCE,
        source_identity=_source_identity(fixture),
        project_root=fixture.project,
        staging_directory=stage,
        output_directory=output,
        emergency_seal_directory=(fixture.output_root / f"{run_id}-emergency-seal"),
        python_path=Path(sys.executable),
        runtime=runtime,
        parent_checkpoints=copy.deepcopy(fixture.parents),
        expected_state=copy.deepcopy(fixture.expected_state),
        external_terminal_fencing=external_terminal_fencing,
        expected_trusted_checkpoint_sha256=external_terminal_fencing["trusted_checkpoint"][
            "sha256"
        ],
        toolchain=copy.deepcopy(fixture.toolchain),
    )
    if manifest_mutation:
        manifest_mutation(manifest)
    stage.mkdir(parents=True)
    manifest_path = stage / "phase-b2-r7s1-work-order.json"
    manifest_path.write_bytes(builder.canonical_json_bytes(manifest))
    bridge_path = stage / "invoke-x1-phase-b2-r7s1-bridge.ps1"
    bridge = builder.render_bridge(
        manifest_sha256=_sha(manifest_path),
        manifest=manifest,
        runtime=runtime,
        project_root=fixture.project,
        source_identity=_source_identity(fixture),
        python_path=Path(sys.executable),
    )
    if bridge_mutation:
        bridge = bridge_mutation(bridge)
    _write(bridge_path, bridge)
    outer_path = stage / "invoke-verified-x1-phase-b2-r7s1.ps1"
    outer = builder.render_outer(
        bridge_sha256=_sha(bridge_path),
        run_id=str(manifest["bundle_id"]),
        trusted_checkpoint_sha256=external_terminal_fencing["trusted_checkpoint"]["sha256"],
        toolchain=manifest["toolchain"],
        successor_binding=manifest["external_terminal_fencing"]["successor_binding"],
    )
    if outer_mutation:
        outer = outer_mutation(outer)
    _write(outer_path, outer)
    return manifest_path, outer_path, bridge_path, _sha(outer_path)


def _validate(
    fixture: ValidatorFixture,
    paths: tuple[Path, Path, Path, str],
) -> subprocess.CompletedProcess[str]:
    manifest, outer, bridge, outer_sha = paths
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    trusted_checkpoint_sha256 = manifest_payload["external_terminal_fencing"]["trusted_checkpoint"][
        "sha256"
    ]
    command = [
        str(POWERSHELL),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(fixture.runtime["validator"]["path"]),
        "-ManifestPath",
        str(manifest),
        "-OuterPath",
        str(outer),
        "-BridgePath",
        str(bridge),
        "-ExpectedOuterSha256",
        outer_sha,
        "-ExpectedTrustedCheckpointSha256",
        str(trusted_checkpoint_sha256),
        "-OfflineContained",
    ]
    outcome = WindowsJobProcessRunner(TimeoutContract()).run(
        command,
        name="test-r7s1-offline-validator",
        cwd=fixture.project,
    )
    return subprocess.CompletedProcess(
        args=command,
        returncode=int(outcome.return_code if outcome.return_code is not None else 2),
        stdout=outcome.stdout,
        stderr=outcome.stderr,
    )


def _direct_validator_command(
    fixture: ValidatorFixture,
    paths: tuple[Path, Path, Path, str],
    *,
    validator: Path | None = None,
    pre_execution: bool = False,
    offline_contained: bool = False,
) -> list[str]:
    manifest, outer, bridge, outer_sha = paths
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    trusted_checkpoint_sha256 = manifest_payload["external_terminal_fencing"]["trusted_checkpoint"][
        "sha256"
    ]
    command = [
        str(POWERSHELL),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(validator or fixture.runtime["validator"]["path"]),
        "-ManifestPath",
        str(manifest),
        "-OuterPath",
        str(outer),
        "-BridgePath",
        str(bridge),
        "-ExpectedOuterSha256",
        outer_sha,
        "-ExpectedTrustedCheckpointSha256",
        str(trusted_checkpoint_sha256),
    ]
    if offline_contained:
        command.append("-OfflineContained")
    if pre_execution:
        command.append("-PreExecution")
    return command


def _write_outer_reservation(manifest_path: Path, outer_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reservation = {
        "schema": "s8-v4-x1-phase-b2-r7s1-outer-reservation/v1",
        "created_at": "2030-01-01T00:00:00Z",
        "invocation_nonce": "1" * 64,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "session_id": 1,
        "creation_filetime": 1,
        "process_path": str(POWERSHELL),
        "process_path_sha256": _sha(POWERSHELL),
        "run_id": manifest["bundle_id"],
        "mode": "restore-only",
        "output_directory": manifest["output"]["path"],
    }
    (outer_path.parent / "r7s1-outer-invocation-reservation.json").write_bytes(
        builder.canonical_json_bytes(reservation)
    )


def test_validator_accepts_exact_r7s1_bundle(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    process_pin = validator_fixture.runtime["process"]
    process_bytes = Path(str(process_pin["path"])).read_bytes()
    assert b"\r\n" in process_bytes
    assert process_pin["worktree_blob_oid"] != process_pin["head_blob_oid"]
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


def test_validator_rejects_mixed_head_and_worktree_blob_pins(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    def mix_blob_identities(manifest: dict[str, Any]) -> None:
        process_pin = manifest["runtime"]["process"]
        process_pin["worktree_blob_oid"] = process_pin["head_blob_oid"]

    result = _validate(
        validator_fixture,
        _make_bundle(
            validator_fixture,
            tmp_path,
            manifest_mutation=mix_blob_identities,
        ),
    )
    assert result.returncode != 0
    assert "manifest_process_worktree_blob" in result.stdout + result.stderr


def test_validator_rejects_self_consistent_repository_top_compose_repin(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    project_compose = (validator_fixture.project / "docker-compose.yml").resolve()
    repository_compose = (validator_fixture.project.parent / "docker-compose.yml").resolve()
    assert project_compose.is_file() and repository_compose.is_file()
    assert project_compose != repository_compose
    assert _sha(project_compose) == _sha(repository_compose)

    def repin_manifest(manifest: dict[str, Any]) -> None:
        runtime_pin = manifest["runtime"]["docker_compose"]
        runtime_pin["path"] = str(repository_compose)
        runtime_pin["sha256"] = _sha(repository_compose)
        runtime_pin["bytes"] = repository_compose.stat().st_size
        runtime_pin["worktree_blob_oid"] = builder.worktree_blob_oid(repository_compose)
        runtime_pin["head_blob_oid"] = _git(
            validator_fixture.project.parent,
            "rev-parse",
            "HEAD:docker-compose.yml",
        )
        compose = manifest["expected_state"]["compose"]
        compose["config_path"] = str(repository_compose)
        compose["config_sha256"] = _sha(repository_compose)

    result = _validate(
        validator_fixture,
        _make_bundle(
            validator_fixture,
            tmp_path,
            manifest_mutation=repin_manifest,
        ),
    )
    assert result.returncode != 0
    assert "compose_path_project_subdir_exact" in result.stdout + result.stderr


def test_full_validator_rejects_uncontained_direct_entry_before_children(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    paths = _make_bundle(validator_fixture, tmp_path)
    result = _run(*_direct_validator_command(validator_fixture, paths))
    assert result.returncode != 0
    assert "offline_validator_containment_acknowledged" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "extra_host_arguments",
    [
        ["-NoLogo"],
        ["-ExecutionPolicy", "Bypass"],
    ],
)
def test_validator_rejects_noncanonical_host_entry_flags_before_validation(
    validator_fixture: ValidatorFixture,
    tmp_path: Path,
    extra_host_arguments: list[str],
) -> None:
    paths = _make_bundle(validator_fixture, tmp_path)
    command = _direct_validator_command(validator_fixture, paths)
    command[3:3] = extra_host_arguments
    result = _run(*command)
    assert result.returncode != 0
    assert "canonical_validator_entry_argv_count_mismatch" in result.stdout + result.stderr


def test_preexecution_path_invokes_no_child_backed_validator_function(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    paths = _make_bundle(validator_fixture, tmp_path)
    manifest_path, outer_path, _bridge_path, _outer_sha = paths
    _write_outer_reservation(manifest_path, outer_path)

    validator_copy = tmp_path / "validator-zero-child.ps1"
    validator_text = Path(validator_fixture.runtime["validator"]["path"]).read_text(
        encoding="utf-8"
    )
    sentinel = "throw 'CHILD_BACKED_VALIDATOR_PATH_REACHED'"
    start = validator_text.index("function Invoke-GitRead")
    body_start = validator_text.index("{", start)
    next_function = validator_text.index("\nfunction Get-GitBlobOid", body_start)
    validator_text = (
        validator_text[: body_start + 1]
        + "\n  "
        + sentinel
        + "\n}"
        + validator_text[next_function:]
    )
    validator_copy.write_text(validator_text, encoding="utf-8")

    result = _run(
        *_direct_validator_command(
            validator_fixture,
            paths,
            validator=validator_copy,
            pre_execution=True,
        )
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert payload["validation_scope"] == "pre_execution_zero_child"
    assert "CHILD_BACKED_VALIDATOR_PATH_REACHED" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("launcher", "needle"),
    [
        ("outer", "outer_canonical_powershell_entry_guard_exact"),
        ("bridge", "bridge_canonical_powershell_entry_guard_exact"),
    ],
)
def test_launcher_profile_shadow_entry_guard_mutation_is_rejected(
    validator_fixture: ValidatorFixture,
    tmp_path: Path,
    launcher: str,
    needle: str,
) -> None:
    target = "'-NoProfile','-NonInteractive','-File'"
    replacement = "'-NoLogo','-NonInteractive','-File'"

    def mutate(text: str) -> str:
        assert text.count(target) == 1
        return text.replace(target, replacement, 1)

    kwargs = {"outer_mutation": mutate} if launcher == "outer" else {"bridge_mutation": mutate}
    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, **kwargs),
    )
    assert result.returncode != 0
    assert needle in result.stdout + result.stderr


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
        (
            lambda manifest: manifest["external_terminal_fencing"].update(
                {"terminal_decision": None}
            ),
            "external_terminal_fencing_decision_required",
        ),
        (
            lambda manifest: manifest["external_terminal_fencing"].update(
                {"decision_authority": "self-appointed"}
            ),
            "external_terminal_fencing_authority_exact",
        ),
        (
            lambda manifest: manifest["expected_state"]["kubernetes"][
                "allowed_historical_failed_pods"
            ].pop(),
            "kubernetes_failed_pod_count_exact",
        ),
        (
            lambda manifest: manifest["expected_state"]["kubernetes"][
                "allowed_historical_failed_pods"
            ][0].update({"reason_source": "unproven"}),
            "kubernetes_failed_pod_identity_exact",
        ),
        (
            lambda manifest: manifest["expected_state"]["b0"].update(
                {"ready_url": "http://127.0.0.1:30801/ready"}
            ),
            "b0_identity_endpoints_and_sample_exact",
        ),
        (
            lambda manifest: (
                manifest["expected_state"]["api"].update({"base_url": "http://127.0.0.1:8001"}),
                manifest["expected_state"].update({"api_base_url": "http://127.0.0.1:8001"}),
            ),
            "runtime_endpoint_and_residue_pins_exact",
        ),
        (
            lambda manifest: manifest["expected_state"].update(
                {"prometheus_targets_url": "http://127.0.0.1:9091/api/v1/targets"}
            ),
            "runtime_endpoint_and_residue_pins_exact",
        ),
        (
            lambda manifest: manifest["toolchain"]["container_psql"]["execution_scope"].update(
                {"linux_container_descendants_job_accounted": True}
            ),
            "container_psql_execution_scope_exact",
        ),
        (
            lambda manifest: manifest["process_containment"]["scope_boundaries"]["wsl"].update(
                {"post_scan_required": False}
            ),
            "process_containment_wsl_scope_exact",
        ),
        (
            lambda manifest: manifest["toolchain"]["git_repository_attributes"]["policy"][
                "hash_object_policy"
            ].update({"core_autocrlf": "false"}),
            "git_repository_attributes_policy_exact",
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
        marker = "# R7S1_BRIDGE_INVOKE_EXACTLY_ONCE"
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
    marker = "# R7S1_BRIDGE_INVOKE_EXACTLY_ONCE"

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
            "$pythonAlias = $PythonPath\n& $pythonAlias -I -S -B $RunnerPath",
            "bridge_ast_exact_ampersand_target_multiset",
        ),
        (
            "& ($PythonPath) -I -S -B $RunnerPath",
            "bridge_ast_exact_ampersand_target_multiset",
        ),
        (
            "& $PythonPath -I -S -B ($RunnerPath)",
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
        original = "& $PythonPath -I -S -B $RunnerPath"
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
        assert text.count(original) == 2
        index = text.rfind(original)
        replacement = "  run_id='x1-phase-b2-r7s1-wrong-identity'\n  mode='restore-only'"
        return text[:index] + replacement + text[index + len(original) :]

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
            "$pythonDistributionFinal = Get-DistributionTreeIdentity",
            "bridge_python_distribution_final_remeasurement_at_invocation_boundary",
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


def test_standalone_docker_compose_destructive_command_is_rejected(
    validator_fixture: ValidatorFixture, tmp_path: Path
) -> None:
    result = _validate(
        validator_fixture,
        _make_bundle(
            validator_fixture,
            tmp_path,
            bridge_mutation=lambda text: text + "\n# docker-compose.exe down\n",
        ),
    )
    assert result.returncode != 0
    assert "forbidden_absent_docker_compose_standalone_destructive" in (
        result.stdout + result.stderr
    )


@pytest.mark.parametrize(
    ("target", "needle"),
    [
        (
            "# R7S1_PATH_FENCE_OUTER_PREWRITE",
            "outer_bound_path_fence_before_any_reservation_write",
        ),
        (
            "# R7S1_PATH_FENCE_OUTER_FINAL",
            "outer_bound_path_fence_after_reservation_before_bridge",
        ),
        (
            "# R7S1_PATH_FENCE_BRIDGE_PREWRITE",
            "bridge_bound_path_fence_before_any_reservation_write",
        ),
        (
            "# R7S1_PATH_FENCE_BRIDGE_FINAL",
            "bridge_bound_path_fence_at_invocation_boundary",
        ),
    ],
)
def test_invocation_boundary_path_fence_removal_is_rejected(
    validator_fixture: ValidatorFixture,
    tmp_path: Path,
    target: str,
    needle: str,
) -> None:
    def remove_marker(text: str) -> str:
        assert text.count(target) == 1
        return text.replace(target, "# removed path fence", 1)

    kwargs = (
        {"outer_mutation": remove_marker}
        if "OUTER" in target
        else {"bridge_mutation": remove_marker}
    )
    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, **kwargs),
    )
    assert result.returncode != 0
    assert needle in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("launcher", "target", "needle"),
    [
        (
            "outer",
            "# R7S1_GIT_ATTRIBUTES_FENCE_OUTER_PREWRITE",
            "outer_git_repository_attributes_fence_order",
        ),
        (
            "outer",
            "# R7S1_GIT_ATTRIBUTES_FENCE_OUTER_FINAL",
            "outer_git_repository_attributes_fence_order",
        ),
        (
            "bridge",
            "# R7S1_GIT_ATTRIBUTES_FENCE_BRIDGE_PREWRITE",
            "bridge_git_repository_attributes_fence_order",
        ),
        (
            "bridge",
            "# R7S1_GIT_ATTRIBUTES_FENCE_BRIDGE_FINAL",
            "bridge_git_repository_attributes_fence_order",
        ),
    ],
)
def test_git_attributes_fence_removal_is_rejected(
    validator_fixture: ValidatorFixture,
    tmp_path: Path,
    launcher: str,
    target: str,
    needle: str,
) -> None:
    def remove_marker(text: str) -> str:
        assert text.count(target) == 1
        return text.replace(target, "# removed git attributes fence", 1)

    kwargs = (
        {"outer_mutation": remove_marker}
        if launcher == "outer"
        else {"bridge_mutation": remove_marker}
    )
    result = _validate(
        validator_fixture,
        _make_bundle(validator_fixture, tmp_path, **kwargs),
    )
    assert result.returncode != 0
    assert needle in result.stdout + result.stderr


def test_validator_powershell_ast_is_valid() -> None:
    command = (
        "$t=$null;$e=$null;"
        f"[void][Management.Automation.Language.Parser]::ParseFile('{VALIDATOR_SOURCE}',"
        "[ref]$t,[ref]$e);if($e.Count){$e|% ToString;exit 1}"
    )
    result = _run(POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command)
    assert result.returncode == 0, result.stdout + result.stderr

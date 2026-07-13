from __future__ import annotations

import io
import json
import os
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Literal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from evm.control_panel.cdct import (
    DEFAULT_CI_REPOSITORY,
    DEFAULT_CI_WORKFLOW,
    atomic_write_json,
    load_ci_evidence,
    validate_ci_evidence,
)
from evm.control_panel.schemas import CIEvidenceBundle, CIEvidenceValidation


OpenUrl = Callable[..., object]
CredentialRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CIEvidenceSyncResult:
    status: Literal["ready", "pending", "blocked"]
    message: str
    workflow_run_id: str | None = None
    workflow_url: str | None = None
    evidence_uri: str | None = None
    synchronized: bool = False
    blockers: list[str] = field(default_factory=list)
    validation: CIEvidenceValidation | None = None


def synchronize_ci_evidence(
    expected_commit: str,
    branch: str,
    *,
    opener: OpenUrl = urlopen,
    token: str | None = None,
    credential_runner: CredentialRunner = subprocess.run,
) -> CIEvidenceSyncResult:
    evidence_path = Path(
        os.getenv(
            "EVM_CI_EVIDENCE_PATH",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/ci/"
            "latest_ci_evidence.json",
        )
    )
    validation_path = Path(
        os.getenv(
            "EVM_CI_VALIDATION_REPORT_PATH",
            str(evidence_path.with_name("latest_ci_validation.json")),
        )
    )
    repository = os.getenv("EVM_GITHUB_REPOSITORY", DEFAULT_CI_REPOSITORY).strip()
    workflow = os.getenv("EVM_GITHUB_CI_WORKFLOW_NAME", DEFAULT_CI_WORKFLOW).strip()
    artifact_name = os.getenv("EVM_GITHUB_CI_ARTIFACT_NAME", "evm-ci-evidence").strip()
    current = load_ci_evidence(
        evidence_path,
        expected_commit=expected_commit,
        expected_repository=repository,
        expected_workflow=workflow,
        report_uri=validation_path,
    )
    if current.valid:
        return CIEvidenceSyncResult(
            status="ready",
            message="Exact immutable CI evidence is already available locally",
            workflow_run_id=current.workflow_run_id,
            workflow_url=current.source_uri,
            evidence_uri=str(evidence_path),
            validation=current,
        )

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return blocked("github_repository_invalid")
    resolved_token = token or github_token(credential_runner)
    headers = github_headers(resolved_token)
    query = urlencode({"branch": branch, "per_page": 30})
    runs_url = f"https://api.github.com/repos/{repository}/actions/runs?{query}"
    try:
        run_payload = request_json(runs_url, headers, opener)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return blocked("github_ci_runs_query_failed", str(exc))
    workflows = run_payload.get("workflow_runs", [])
    matches = [
        item
        for item in workflows
        if isinstance(item, dict)
        and str(item.get("head_sha") or "").lower() == expected_commit.lower()
        and str(item.get("name") or "") == workflow
    ]
    if not matches:
        return CIEvidenceSyncResult(
            status="pending",
            message="Waiting for the exact commit CI workflow run",
        )
    selected = max(matches, key=lambda item: int(item.get("id") or 0))
    run_id = str(selected.get("id") or "")
    workflow_url = str(selected.get("html_url") or "") or None
    if str(selected.get("status") or "") != "completed":
        return CIEvidenceSyncResult(
            status="pending",
            message=f"GitHub CI run {run_id} is still executing",
            workflow_run_id=run_id,
            workflow_url=workflow_url,
        )
    conclusion = str(selected.get("conclusion") or "unknown")
    if conclusion != "success":
        return blocked(
            f"github_ci_conclusion_{conclusion}",
            workflow_run_id=run_id,
            workflow_url=workflow_url,
        )

    artifacts_url = (
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts"
    )
    try:
        artifacts_payload = request_json(artifacts_url, headers, opener)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return blocked(
            "github_ci_artifacts_query_failed",
            str(exc),
            workflow_run_id=run_id,
            workflow_url=workflow_url,
        )
    artifacts = artifacts_payload.get("artifacts", [])
    artifact = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict)
            and str(item.get("name") or "") == artifact_name
            and not bool(item.get("expired"))
        ),
        None,
    )
    if artifact is None:
        return CIEvidenceSyncResult(
            status="pending",
            message=f"Waiting for GitHub artifact {artifact_name}",
            workflow_run_id=run_id,
            workflow_url=workflow_url,
        )
    if not resolved_token:
        return blocked(
            "github_artifact_authentication_missing",
            workflow_run_id=run_id,
            workflow_url=workflow_url,
        )
    archive_url = str(artifact.get("archive_download_url") or "")
    if not archive_url:
        return blocked(
            "github_artifact_download_url_missing",
            workflow_run_id=run_id,
            workflow_url=workflow_url,
        )
    try:
        archive = request_bytes(archive_url, headers, opener)
        payload = evidence_payload_from_zip(archive)
        bundle = CIEvidenceBundle.model_validate(payload)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        return blocked(
            "github_ci_artifact_invalid",
            str(exc),
            workflow_run_id=run_id,
            workflow_url=workflow_url,
        )
    validation = validate_ci_evidence(
        bundle,
        expected_commit=expected_commit,
        expected_repository=repository,
        expected_workflow=workflow,
        report_uri=str(validation_path),
    )
    if not validation.valid:
        return CIEvidenceSyncResult(
            status="blocked",
            message="Downloaded CI evidence failed immutable validation",
            workflow_run_id=run_id,
            workflow_url=workflow_url,
            blockers=validation.blockers,
            validation=validation,
        )
    immutable_path = evidence_path.parent / "runs" / run_id / evidence_path.name
    atomic_write_json(immutable_path, payload)
    atomic_write_json(evidence_path, payload)
    persisted = load_ci_evidence(
        evidence_path,
        expected_commit=expected_commit,
        expected_repository=repository,
        expected_workflow=workflow,
        report_uri=validation_path,
    )
    if not persisted.valid:
        return CIEvidenceSyncResult(
            status="blocked",
            message="Persisted CI evidence failed post-write validation",
            workflow_run_id=run_id,
            workflow_url=workflow_url,
            evidence_uri=str(evidence_path),
            blockers=persisted.blockers,
            validation=persisted,
        )
    return CIEvidenceSyncResult(
        status="ready",
        message="GitHub CI artifact synchronized and validated",
        workflow_run_id=run_id,
        workflow_url=workflow_url,
        evidence_uri=str(evidence_path),
        synchronized=True,
        validation=persisted,
    )


def github_token(runner: CredentialRunner = subprocess.run) -> str | None:
    for variable in ("EVM_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.getenv(variable, "").strip()
        if value:
            return value
    credential_input = "protocol=https\nhost=github.com\n\n"
    for command in (["git", "credential-manager", "get"], ["git", "credential", "fill"]):
        try:
            result = runner(
                command,
                input=credential_input,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        values = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        password = values.get("password", "").strip()
        if password:
            return password
    return None


def github_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "enterprise-vision-mlops-lifecycle-worker",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(url: str, headers: dict[str, str], opener: OpenUrl) -> dict[str, object]:
    payload = request_bytes(url, headers, opener)
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("github_api_response_not_object")
    return value


def request_bytes(url: str, headers: dict[str, str], opener: OpenUrl) -> bytes:
    request = Request(url, headers=headers)
    with opener(request, timeout=30) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("github_response_size_limit_exceeded")
    return payload


def evidence_payload_from_zip(archive: bytes) -> dict[str, object]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        matches = [
            name
            for name in bundle.namelist()
            if PurePosixPath(name).name == "latest_ci_evidence.json"
        ]
        if len(matches) != 1:
            raise ValueError("ci_evidence_file_count_invalid")
        raw = bundle.read(matches[0])
    if len(raw) > 1_000_000:
        raise ValueError("ci_evidence_size_limit_exceeded")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ci_evidence_root_not_object")
    return payload


def blocked(
    code: str,
    detail: str | None = None,
    *,
    workflow_run_id: str | None = None,
    workflow_url: str | None = None,
) -> CIEvidenceSyncResult:
    message = code if not detail else f"{code}: {detail}"
    return CIEvidenceSyncResult(
        status="blocked",
        message=message,
        workflow_run_id=workflow_run_id,
        workflow_url=workflow_url,
        blockers=[code],
    )

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

from evm.control_panel.cdct import atomic_write_json, with_ci_bundle_digest
from evm.control_panel.ci_evidence_sync import (
    CrossHostAuthorizationStrippingRedirectHandler,
    synchronize_ci_evidence,
)


COMMIT = "a" * 40
RUN_ID = "29243478857"


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.payload


class GitHubOpener:
    def __init__(self, *, status: str = "completed"):
        self.status = status
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, request: Request, **_kwargs) -> FakeResponse:
        url = request.full_url
        self.calls.append((url, request.headers.get("Authorization")))
        if "/actions/runs?" in url:
            return response(
                {
                    "workflow_runs": [
                        {
                            "id": int(RUN_ID),
                            "name": "Enterprise Vision MLOps CI",
                            "head_sha": COMMIT,
                            "status": self.status,
                            "conclusion": "success" if self.status == "completed" else None,
                            "html_url": f"https://github.com/ruma0236/ML_ServeAPI/actions/runs/{RUN_ID}",
                        }
                    ]
                }
            )
        if url.endswith(f"/actions/runs/{RUN_ID}/artifacts"):
            return response(
                {
                    "artifacts": [
                        {
                            "name": "evm-ci-evidence",
                            "expired": False,
                            "archive_download_url": "https://api.github.test/artifact.zip",
                        }
                    ]
                }
            )
        if url == "https://api.github.test/artifact.zip":
            return FakeResponse(evidence_zip())
        raise AssertionError(url)


def evidence_payload() -> dict[str, object]:
    return with_ci_bundle_digest(
        {
            "repository": "ruma0236/ML_ServeAPI",
            "workflow_name": "Enterprise Vision MLOps CI",
            "workflow_run_id": RUN_ID,
            "workflow_run_attempt": 1,
            "commit_sha": COMMIT,
            "ref": "refs/heads/codex/mac-mini-worker",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "python_test_result": "pass",
            "frontend_test_result": "pass",
            "evidence_validator_result": "pass",
            "compose_config_result": "pass",
            "kustomize_render_result": "pass",
            "image_digest": "example/serving@sha256:" + "b" * 64,
            "config_render_digest": "c" * 64,
            "contract_digest": "d" * 64,
            "source_uri": f"https://github.com/ruma0236/ML_ServeAPI/actions/runs/{RUN_ID}",
            "generated_at": "2026-07-13T10:39:50Z",
        }
    ).model_dump(mode="json")


def evidence_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("latest_ci_evidence.json", json.dumps(evidence_payload()))
    return buffer.getvalue()


def response(payload: dict[str, object]) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def configure_paths(tmp_path: Path, monkeypatch) -> Path:
    evidence = tmp_path / "ci" / "latest_ci_evidence.json"
    monkeypatch.setenv("EVM_CI_EVIDENCE_PATH", str(evidence))
    monkeypatch.setenv(
        "EVM_CI_VALIDATION_REPORT_PATH",
        str(evidence.with_name("latest_ci_validation.json")),
    )
    return evidence


def test_cross_host_redirect_strips_github_authorization() -> None:
    handler = CrossHostAuthorizationStrippingRedirectHandler()
    request = Request(
        "https://api.github.com/repos/example/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer secret", "Accept": "application/zip"},
    )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://results.blob.core.windows.net/artifact.zip?sig=example",
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Accept") == "application/zip"


def test_same_host_redirect_retains_authorization() -> None:
    handler = CrossHostAuthorizationStrippingRedirectHandler()
    request = Request(
        "https://api.github.com/repos/example/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer secret"},
    )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://api.github.com/repos/example/actions/artifacts/1/archive",
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") == "Bearer secret"


def test_sync_downloads_exact_successful_ci_artifact(tmp_path, monkeypatch) -> None:
    evidence = configure_paths(tmp_path, monkeypatch)
    opener = GitHubOpener()

    result = synchronize_ci_evidence(
        COMMIT,
        "codex/mac-mini-worker",
        opener=opener,
        token="secret-token",
    )

    assert result.status == "ready"
    assert result.synchronized is True
    assert result.workflow_run_id == RUN_ID
    assert json.loads(evidence.read_text(encoding="utf-8"))["commit_sha"] == COMMIT
    assert (evidence.parent / "runs" / RUN_ID / evidence.name).is_file()
    assert all(authorization == "Bearer secret-token" for _, authorization in opener.calls)


def test_sync_reuses_valid_local_evidence_without_network(tmp_path, monkeypatch) -> None:
    evidence = configure_paths(tmp_path, monkeypatch)
    atomic_write_json(evidence, evidence_payload())

    def no_network(*_args, **_kwargs):
        raise AssertionError("network must not be called")

    result = synchronize_ci_evidence(
        COMMIT,
        "codex/mac-mini-worker",
        opener=no_network,
        token="secret-token",
    )

    assert result.status == "ready"
    assert result.synchronized is False


def test_sync_reports_running_workflow_as_pending(tmp_path, monkeypatch) -> None:
    configure_paths(tmp_path, monkeypatch)

    result = synchronize_ci_evidence(
        COMMIT,
        "codex/mac-mini-worker",
        opener=GitHubOpener(status="in_progress"),
        token="secret-token",
    )

    assert result.status == "pending"
    assert result.workflow_run_id == RUN_ID
    assert result.blockers == []


def test_sync_retries_transient_runs_query_then_succeeds(tmp_path, monkeypatch) -> None:
    configure_paths(tmp_path, monkeypatch)
    upstream = GitHubOpener()
    attempts = 0
    delays: list[float] = []

    def flaky_opener(request: Request, **kwargs) -> FakeResponse:
        nonlocal attempts
        if "/actions/runs?" in request.full_url:
            attempts += 1
            if attempts < 3:
                raise URLError(OSError(11001, "getaddrinfo failed"))
        return upstream(request, **kwargs)

    result = synchronize_ci_evidence(
        COMMIT,
        "codex/mac-mini-worker",
        opener=flaky_opener,
        token="secret-token",
        sleeper=delays.append,
    )

    assert result.status == "ready"
    assert attempts == 3
    assert delays == [1.0, 2.0]


def test_sync_blocks_after_transient_retry_budget_is_exhausted(
    tmp_path, monkeypatch
) -> None:
    configure_paths(tmp_path, monkeypatch)
    attempts = 0
    delays: list[float] = []

    def unavailable(_request: Request, **_kwargs) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        raise URLError(OSError(11001, "getaddrinfo failed"))

    result = synchronize_ci_evidence(
        COMMIT,
        "codex/mac-mini-worker",
        opener=unavailable,
        token="secret-token",
        request_attempts=3,
        retry_delay_seconds=0.25,
        sleeper=delays.append,
    )

    assert result.status == "blocked"
    assert result.blockers == ["github_ci_runs_query_failed"]
    assert "transient_request_exhausted attempts=3" in result.message
    assert attempts == 3
    assert delays == [0.25, 0.5]


def test_sync_does_not_retry_http_policy_failure(tmp_path, monkeypatch) -> None:
    configure_paths(tmp_path, monkeypatch)
    attempts = 0
    delays: list[float] = []

    def forbidden(request: Request, **_kwargs) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

    result = synchronize_ci_evidence(
        COMMIT,
        "codex/mac-mini-worker",
        opener=forbidden,
        token="secret-token",
        sleeper=delays.append,
    )

    assert result.status == "blocked"
    assert result.blockers == ["github_ci_runs_query_failed"]
    assert attempts == 1
    assert delays == []

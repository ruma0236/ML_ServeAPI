from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.request import Request

from evm.control_panel.cdct import atomic_write_json, with_ci_bundle_digest
from evm.control_panel.ci_evidence_sync import synchronize_ci_evidence


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

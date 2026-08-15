from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evm.scale_validation.contracts import EvidenceArtifact
from evm.scale_validation.evidence import (
    PublicEvidenceIntegrityError,
    git_blob_loader,
    public_file_sha256,
    verify_public_artifacts,
    write_public_json,
)


NOW = datetime(2026, 8, 15, tzinfo=UTC)


def artifact(path: str, digest: str) -> EvidenceArtifact:
    return EvidenceArtifact(
        path=path,
        sha256=digest,
        generated_at=NOW,
        claim="Canonical public evidence bytes are hash-linked.",
    )


def test_public_json_writer_and_rehash_use_canonical_lf(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    write_public_json(path, {"value": "line\nfeed"})
    payload = path.read_bytes()

    assert b"\r" not in payload
    assert public_file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_public_artifact_verifier_rehashes_actual_bytes(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    write_public_json(path, {"status": "pass"})
    expected = public_file_sha256(path)

    verified = verify_public_artifacts(
        [artifact("evidence.json", expected)],
        load_bytes=lambda _: path.read_bytes(),
    )

    assert verified == {"evidence.json": expected}


def test_public_artifact_verifier_rejects_crlf_and_wrong_hash() -> None:
    with pytest.raises(PublicEvidenceIntegrityError, match="not canonical LF"):
        verify_public_artifacts(
            [artifact("evidence.json", "a" * 64)],
            load_bytes=lambda _: b'{\r\n  "status": "pass"\r\n}\r\n',
        )

    with pytest.raises(PublicEvidenceIntegrityError, match="hash mismatch"):
        verify_public_artifacts(
            [artifact("evidence.json", "a" * 64)],
            load_bytes=lambda _: b'{"status":"pass"}\n',
        )


def test_git_blob_loader_rehashes_committed_canonical_bytes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitattributes").write_bytes(b"*.json text eol=lf\n")
    worktree_payload = b'{\r\n  "status": "pass"\r\n}\r\n'
    canonical_blob = worktree_payload.replace(b"\r\n", b"\n")
    (tmp_path / "evidence.json").write_bytes(worktree_payload)
    subprocess.run(
        ["git", "add", ".gitattributes", "evidence.json"],
        cwd=tmp_path,
        check=True,
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "evidence-test",
        "GIT_AUTHOR_EMAIL": "evidence-test@example.invalid",
        "GIT_COMMITTER_NAME": "evidence-test",
        "GIT_COMMITTER_EMAIL": "evidence-test@example.invalid",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", "canonical evidence fixture"],
        cwd=tmp_path,
        check=True,
        env=env,
    )

    expected = hashlib.sha256(canonical_blob).hexdigest()
    verified = verify_public_artifacts(
        [artifact("evidence.json", expected)],
        load_bytes=git_blob_loader(tmp_path, "HEAD"),
    )

    assert (tmp_path / "evidence.json").read_bytes() == worktree_payload
    assert verified == {"evidence.json": expected}

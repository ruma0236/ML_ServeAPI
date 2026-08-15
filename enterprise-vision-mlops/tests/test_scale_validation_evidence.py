from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evm.scale_validation.contracts import EvidenceArtifact
from evm.scale_validation.evidence import (
    PublicEvidenceIntegrityError,
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

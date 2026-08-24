from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from evm.scale_validation.contracts import EvidenceArtifact


class PublicEvidenceIntegrityError(ValueError):
    pass


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def canonical_public_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def write_public_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(canonical_public_json_bytes(payload))
    temporary.replace(path)


def require_canonical_lf(path: str, payload: bytes) -> bytes:
    if b"\r" in payload:
        raise PublicEvidenceIntegrityError(f"public evidence is not canonical LF: {path}")
    if path.endswith(".json"):
        try:
            json.loads(
                payload.decode("utf-8"),
                parse_constant=_reject_non_finite_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PublicEvidenceIntegrityError(
                f"public evidence is not valid UTF-8 JSON: {path}"
            ) from exc
    return payload


def public_bytes_sha256(path: str, payload: bytes) -> str:
    return hashlib.sha256(require_canonical_lf(path, payload)).hexdigest()


def public_file_sha256(path: Path) -> str:
    return public_bytes_sha256(path.as_posix(), path.read_bytes())


def verify_public_artifacts(
    artifacts: Iterable[EvidenceArtifact],
    *,
    load_bytes: Callable[[str], bytes],
) -> dict[str, str]:
    expected_by_path: dict[str, str] = {}
    for artifact in artifacts:
        previous = expected_by_path.setdefault(artifact.path, artifact.sha256)
        if previous != artifact.sha256:
            raise PublicEvidenceIntegrityError(
                f"conflicting public hashes for {artifact.path}: {previous} != {artifact.sha256}"
            )

    verified: dict[str, str] = {}
    for path, expected in sorted(expected_by_path.items()):
        observed = public_bytes_sha256(path, load_bytes(path))
        if observed != expected:
            raise PublicEvidenceIntegrityError(
                f"public evidence hash mismatch: {path}: expected={expected}: observed={observed}"
            )
        verified[path] = observed
    return verified


def git_blob_loader(project_root: Path, revision: str) -> Callable[[str], bytes]:
    git_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    project_prefix = project_root.resolve().relative_to(git_root.resolve())

    def load(path: str) -> bytes:
        git_path = (project_prefix / Path(path)).as_posix()
        completed = subprocess.run(
            ["git", "show", f"{revision}:{git_path}"],
            cwd=git_root,
            check=True,
            capture_output=True,
        )
        return completed.stdout

    return load

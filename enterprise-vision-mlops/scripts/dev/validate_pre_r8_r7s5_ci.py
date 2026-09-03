"""Validate the frozen, zero-credit pre-r8 r7s5 CI contract."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evm.scale_validation.phase_b2_r7s5_ci import (  # noqa: E402
    LANES,
    R7S5CIContractError,
    ReceiptReplayGuard,
    load_and_validate_manifest,
    load_manifest,
    load_receipt,
    validate_required_closure,
    validate_workflow_contract,
)


# This must be populated only by a separately reviewed commit after the
# external receipt authority provisions its Ed25519 public key.  Accepting a
# caller-supplied key without this repository-history pin would let the caller
# mint both the key and all receipts.  The current unprovisioned state is an
# intentional fail-closed NO-GO.
PINNED_EXTERNAL_AUTHORITY_KEY_FINGERPRINT: str | None = None
# A local folder cannot resist an administrator deleting/replacing its marker
# files.  These values remain unprovisioned until a separately operated WORM
# replay authority and its backend attestation are reviewed and integrated.
PINNED_EXTERNAL_WORM_REPLAY_AUTHORITY_IDENTITY: str | None = None
PINNED_EXTERNAL_WORM_REPLAY_BACKEND_ATTESTATION_SHA256: str | None = None


def _emit(value: object, *, stream: object | None = None) -> None:
    destination = sys.stdout if stream is None else stream
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), file=destination)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _git_identity(project_root: Path) -> tuple[str, str, bool]:
    root = project_root.resolve(strict=True)

    def invoke(*arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise R7S5CIContractError(f"closure_git_readback_timeout:{arguments[0]}") from exc
        if completed.returncode != 0:
            raise R7S5CIContractError(
                f"closure_git_readback_failed:{arguments[0]}:{completed.returncode}"
            )
        return completed.stdout.strip()

    head = invoke("rev-parse", "HEAD")
    tree = invoke("rev-parse", "HEAD^{tree}")
    tracked_status = invoke("status", "--porcelain", "--untracked-files=no")
    return head, tree, not tracked_status


def _ed25519_verifier(
    public_key_path: Path,
) -> Callable[[bytes, str, str], bool]:
    key_bytes = public_key_path.read_bytes()
    try:
        key = serialization.load_pem_public_key(key_bytes)
    except (TypeError, ValueError) as exc:
        raise R7S5CIContractError("authority_public_key_invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise R7S5CIContractError("authority_public_key_ed25519_required")
    der = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    trusted_fingerprint = hashlib.sha256(der).hexdigest()
    expected_fingerprint = PINNED_EXTERNAL_AUTHORITY_KEY_FINGERPRINT
    if expected_fingerprint is None:
        raise R7S5CIContractError("external_authority_key_pin_not_provisioned")
    if len(expected_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in expected_fingerprint
    ):
        raise R7S5CIContractError("external_authority_key_pin_invalid")
    if not hmac.compare_digest(trusted_fingerprint, expected_fingerprint):
        raise R7S5CIContractError("external_authority_key_pin_mismatch")

    def verify(payload: bytes, signature: str, fingerprint: str) -> bool:
        if not hmac.compare_digest(fingerprint, trusted_fingerprint):
            return False
        try:
            decoded = base64.b64decode(signature, validate=True)
            key.verify(decoded, payload)
        except (binascii.Error, InvalidSignature, ValueError):
            return False
        return True

    return verify


def _production_replay_guard() -> ReceiptReplayGuard:
    if (
        PINNED_EXTERNAL_WORM_REPLAY_AUTHORITY_IDENTITY is None
        or PINNED_EXTERNAL_WORM_REPLAY_BACKEND_ATTESTATION_SHA256 is None
    ):
        raise R7S5CIContractError("external_worm_replay_authority_not_provisioned")
    raise R7S5CIContractError("external_worm_replay_authority_adapter_not_implemented")


def _path_map(values: Sequence[str], expected: set[str], label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, separator, raw_path = value.partition("=")
        if not separator or key not in expected or not raw_path or key in result:
            raise R7S5CIContractError(f"{label}_mapping_not_exact")
        result[key] = Path(raw_path)
    if set(result) != expected:
        raise R7S5CIContractError(f"{label}_set_not_exact")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--manifest", type=Path, required=True)
    manifest.add_argument("--project-root", type=Path, required=True)
    manifest.add_argument("--lane", choices=LANES)

    workflow = subparsers.add_parser("workflow")
    workflow.add_argument("--manifest", type=Path, required=True)
    workflow.add_argument("--project-root", type=Path, required=True)
    workflow.add_argument("--workflow", type=Path, required=True)

    closure = subparsers.add_parser("closure")
    closure.add_argument("--manifest", type=Path, required=True)
    closure.add_argument("--project-root", type=Path, required=True)
    closure.add_argument("--repository", required=True)
    closure.add_argument("--workflow-name", required=True)
    closure.add_argument("--commit", required=True)
    closure.add_argument("--tree", required=True)
    closure.add_argument("--run-id", required=True)
    closure.add_argument("--run-attempt", type=int, required=True)
    closure.add_argument(
        "--collection-inventory-receipt",
        action="append",
        default=[],
        metavar="LANE=PATH",
    )
    closure.add_argument(
        "--lane-result-receipt",
        action="append",
        default=[],
        metavar="LANE=PATH",
    )
    closure.add_argument(
        "--runner-receipt",
        action="append",
        default=[],
        metavar="LANE=PATH",
    )
    closure.add_argument("--private-artifact-receipt", type=Path, required=True)
    closure.add_argument("--authority-public-key", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "manifest":
            result = load_and_validate_manifest(args.manifest, project_root=args.project_root)
            if args.lane is not None:
                payload = load_manifest(args.manifest, project_root=args.project_root)
                result = {
                    **result,
                    "configuration_validation_only": True,
                    "required_lane_closure_eligible": False,
                    "selected_lane": args.lane,
                    "selected_lane_files": payload["file_inventory"]["lanes"][args.lane],
                }
            else:
                result = {
                    **result,
                    "required_lane_closure_eligible": False,
                    "reason": "manifest_validation_is_descriptive_not_authenticated_closure",
                }
        elif args.command == "workflow":
            payload = load_manifest(args.manifest, project_root=args.project_root)
            result = validate_workflow_contract(args.workflow.read_bytes(), payload)
        else:
            payload = load_manifest(args.manifest, project_root=args.project_root)
            head, tree, tracked_clean = _git_identity(args.project_root)
            if args.commit != head or args.tree != tree:
                raise R7S5CIContractError("closure_checkout_identity_mismatch")
            if not tracked_clean:
                raise R7S5CIContractError("closure_checkout_tracked_dirty")
            lane_paths = _path_map(
                args.lane_result_receipt,
                set(LANES),
                "lane_result_receipt",
            )
            collection_paths = _path_map(
                args.collection_inventory_receipt,
                set(LANES),
                "collection_inventory_receipt",
            )
            runner_paths = _path_map(
                args.runner_receipt,
                {"windows", "private"},
                "runner_receipt",
            )
            result = validate_required_closure(
                payload,
                {lane: load_receipt(collection_paths[lane]) for lane in LANES},
                {lane: load_receipt(lane_paths[lane]) for lane in LANES},
                repository=args.repository,
                workflow=args.workflow_name,
                commit=args.commit,
                tree=args.tree,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                runner_receipts={
                    lane: load_receipt(runner_paths[lane]) for lane in ("windows", "private")
                },
                private_artifact_receipt=load_receipt(args.private_artifact_receipt),
                now=_utc_now(),
                replay_guard=_production_replay_guard(),
                verifier=_ed25519_verifier(args.authority_public_key),
            )
            if result.get("required_lane_test_closure_passed") is not True:
                raise R7S5CIContractError("closure_not_exact_pass")
    except (OSError, R7S5CIContractError) as exc:
        _emit(
            {
                "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5-ci-rejection.v1",
                "status": "rejected",
                "error": f"{type(exc).__name__}:{exc}",
                "go_evidence_eligible": False,
            },
            stream=sys.stderr,
        )
        return 2
    _emit(result)
    if args.command == "manifest" and args.lane is None:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

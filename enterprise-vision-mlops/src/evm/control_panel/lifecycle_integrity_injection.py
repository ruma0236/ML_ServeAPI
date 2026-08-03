from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from evm.control_panel.lifecycle_guards import canonical_digest, file_digest
from evm.control_panel.readiness_evaluator import runtime_path


SCHEMA = "evm.lifecycle_integrity_injection.v1"
DATA_ACTION = "corrupt_run_local_shard_identity"
RELEASE_ACTION = "corrupt_run_local_release_model_identity"
InjectionAction = Literal[
    "corrupt_run_local_shard_identity",
    "corrupt_run_local_release_model_identity",
]
TARGETS: dict[str, str] = {
    DATA_ACTION: "data/shards/shard_index.json",
    RELEASE_ACTION: "validation/release-submission.json",
}
FILE_KEYS: dict[str, str] = {
    DATA_ACTION: "data",
    RELEASE_ACTION: "release",
}


class LifecycleIdentity(Protocol):
    run_id: str
    lifecycle_series_id: str | None
    attempt_id: str | None
    correlation_id: str | None
    profile_digest: str
    effective_config_digest: str
    source_commit: str | None
    identity_envelope_uri: str | None
    artifact_root: str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LifecycleIntegrityInjection(StrictModel):
    schema_version: Literal["evm.lifecycle_integrity_injection.v1"] = SCHEMA
    injection_id: str = Field(min_length=12)
    action: InjectionAction
    target_relative_path: str
    run_id: str
    lifecycle_series_id: str
    attempt_id: str
    correlation_id: str
    profile_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_config_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    actor: str
    reason: str
    issued_at: datetime
    expires_at: datetime
    single_use: Literal[True] = True
    canonical_mutation_allowed: Literal[False] = False
    production_mutation_allowed: Literal[False] = False
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class LifecycleIntegrityInjectionBlocked(RuntimeError):
    def __init__(self, blockers: list[str]):
        self.blockers = sorted(set(blockers))
        super().__init__(", ".join(self.blockers))


def utc_now() -> datetime:
    return datetime.now(UTC)


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path, blocker: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleIntegrityInjectionBlocked([blocker]) from exc
    if not isinstance(payload, dict):
        raise LifecycleIntegrityInjectionBlocked([blocker])
    return payload


def guard_root(run: LifecycleIdentity) -> Path:
    if not run.identity_envelope_uri:
        raise LifecycleIntegrityInjectionBlocked(["integrity_injection_identity_missing"])
    return runtime_path(run.identity_envelope_uri).parent.resolve()


def validate_run_scope(run: LifecycleIdentity) -> None:
    ledger_root = guard_root(run)
    artifact_root = runtime_path(run.artifact_root).resolve()
    if ledger_root.name != run.run_id or artifact_root.name != run.run_id:
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_artifact_root_mismatch"]
        )


def injection_root(run: LifecycleIdentity) -> Path:
    return guard_root(run) / "e-inject"


def injection_contract_path(run: LifecycleIdentity, action: InjectionAction) -> Path:
    return injection_root(run) / f"{FILE_KEYS[action]}.contract.json"


def injection_receipt_path(run: LifecycleIdentity, action: InjectionAction) -> Path:
    return injection_root(run) / f"{FILE_KEYS[action]}.used.json"


def injection_material(payload: dict[str, Any]) -> dict[str, Any]:
    material = dict(payload)
    material.pop("action_digest", None)
    return material


def issue_lifecycle_integrity_injection(
    run: LifecycleIdentity,
    *,
    action: InjectionAction,
    actor: str,
    reason: str,
    ttl_seconds: int = 7200,
    issued_at: datetime | None = None,
) -> Path:
    if ttl_seconds <= 0 or ttl_seconds > 86400:
        raise ValueError("integrity_injection_ttl_invalid")
    identity = {
        "lifecycle_series_id": run.lifecycle_series_id,
        "attempt_id": run.attempt_id,
        "correlation_id": run.correlation_id,
        "source_commit": run.source_commit,
    }
    if any(not value for value in identity.values()):
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_identity_incomplete"]
        )
    validate_run_scope(run)
    path = injection_contract_path(run, action)
    receipt = injection_receipt_path(run, action)
    if path.exists() or receipt.exists():
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_contract_already_exists"]
        )
    started = (issued_at or utc_now()).astimezone(UTC).replace(microsecond=0)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "injection_id": f"scenario-e-{uuid4().hex}",
        "action": action,
        "target_relative_path": TARGETS[action],
        "run_id": run.run_id,
        "lifecycle_series_id": str(run.lifecycle_series_id),
        "attempt_id": str(run.attempt_id),
        "correlation_id": str(run.correlation_id),
        "profile_digest": run.profile_digest,
        "effective_config_digest": run.effective_config_digest,
        "source_commit": str(run.source_commit),
        "actor": actor,
        "reason": reason,
        "issued_at": started.isoformat(),
        "expires_at": (started + timedelta(seconds=ttl_seconds)).isoformat(),
        "single_use": True,
        "canonical_mutation_allowed": False,
        "production_mutation_allowed": False,
    }
    payload["action_digest"] = canonical_digest(payload)
    LifecycleIntegrityInjection.model_validate(payload)
    atomic_write_json(path, payload)
    return path


def validate_injection_contract(
    run: LifecycleIdentity,
    action: InjectionAction,
    *,
    observed_at: datetime | None = None,
) -> LifecycleIntegrityInjection | None:
    path = injection_contract_path(run, action)
    if not path.is_file():
        return None
    validate_run_scope(run)
    raw_contract = read_json(path, "integrity_injection_contract_invalid")
    try:
        contract = LifecycleIntegrityInjection.model_validate(
            raw_contract
        )
    except ValueError as exc:
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_contract_invalid"]
        ) from exc
    blockers: list[str] = []
    expected = {
        "action": action,
        "target_relative_path": TARGETS[action],
        "run_id": run.run_id,
        "lifecycle_series_id": run.lifecycle_series_id,
        "attempt_id": run.attempt_id,
        "correlation_id": run.correlation_id,
        "profile_digest": run.profile_digest,
        "effective_config_digest": run.effective_config_digest,
        "source_commit": run.source_commit,
    }
    observed = contract.model_dump(mode="json")
    for key, value in expected.items():
        if observed.get(key) != value:
            blockers.append(f"integrity_injection_{key}_mismatch")
    if contract.action_digest != canonical_digest(injection_material(raw_contract)):
        blockers.append("integrity_injection_action_digest_mismatch")
    now = (observed_at or utc_now()).astimezone(UTC)
    if contract.issued_at.tzinfo is None or contract.expires_at.tzinfo is None:
        blockers.append("integrity_injection_timezone_invalid")
    elif now < contract.issued_at or now >= contract.expires_at:
        blockers.append("integrity_injection_expired_or_not_yet_valid")
    if injection_receipt_path(run, action).exists():
        blockers.append("integrity_injection_already_consumed")
    claim = injection_root(run) / f"{FILE_KEYS[action]}.claim.json"
    if claim.exists():
        blockers.append("integrity_injection_claim_exists")
    if blockers:
        raise LifecycleIntegrityInjectionBlocked(blockers)
    return contract


def claim_injection(run: LifecycleIdentity, contract: LifecycleIntegrityInjection) -> Path:
    claim = injection_root(run) / f"{FILE_KEYS[contract.action]}.claim.json"
    claim.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "evm.lifecycle_integrity_injection_claim.v1",
        "injection_id": contract.injection_id,
        "action": contract.action,
        "run_id": contract.run_id,
        "action_digest": contract.action_digest,
        "claimed_at": utc_now().isoformat(),
    }
    try:
        with claim.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError as exc:
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_claim_exists"]
        ) from exc
    return claim


def exact_run_local_target(run: LifecycleIdentity, relative: str) -> Path:
    root = runtime_path(run.artifact_root).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_target_outside_run_root"]
        )
    return target


def consume_data_integrity_injection(
    run: LifecycleIdentity,
    *,
    observed_at: datetime | None = None,
) -> Path | None:
    contract = validate_injection_contract(run, DATA_ACTION, observed_at=observed_at)
    if contract is None:
        return None
    target = exact_run_local_target(run, contract.target_relative_path)
    if not target.is_file():
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_data_target_missing"]
        )
    payload = read_json(target, "integrity_injection_data_target_invalid")
    before_sha = file_digest(target)
    before_identity = str(payload.get("identity_sha256") or "")
    if len(before_identity) != 64:
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_data_identity_invalid"]
        )
    claim = claim_injection(run, contract)
    payload["identity_sha256"] = "0" * 64
    atomic_write_json(target, payload)
    receipt = injection_receipt_path(run, DATA_ACTION)
    atomic_write_json(
        receipt,
        {
            "schema_version": "evm.lifecycle_integrity_injection_receipt.v1",
            "injection_id": contract.injection_id,
            "action": contract.action,
            "run_id": contract.run_id,
            "action_digest": contract.action_digest,
            "target_uri": str(target),
            "target_relative_path": contract.target_relative_path,
            "before_sha256": before_sha,
            "after_sha256": file_digest(target),
            "before_identity_sha256": before_identity,
            "injected_identity_sha256": "0" * 64,
            "claim_uri": str(claim),
            "consumed_at": utc_now().isoformat(),
            "single_use": True,
            "canonical_mutation_allowed": False,
            "production_mutation_allowed": False,
        },
    )
    return receipt


def release_submission_for_admission(
    run: LifecycleIdentity,
    canonical_submission: Path,
    *,
    observed_at: datetime | None = None,
) -> Path:
    contract = validate_injection_contract(run, RELEASE_ACTION, observed_at=observed_at)
    if contract is None:
        return canonical_submission
    expected = exact_run_local_target(run, contract.target_relative_path)
    canonical = canonical_submission.resolve()
    if canonical != expected or not canonical.is_file():
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_release_target_mismatch"]
        )
    payload = read_json(canonical, "integrity_injection_release_target_invalid")
    original_model_digest = str(payload.get("model_digest") or "")
    if len(original_model_digest) != 64:
        raise LifecycleIntegrityInjectionBlocked(
            ["integrity_injection_release_identity_invalid"]
        )
    claim = claim_injection(run, contract)
    injected_model_digest = (
        "e" * 64 if original_model_digest == "f" * 64 else "f" * 64
    )
    payload["model_digest"] = injected_model_digest
    payload.pop("submission_digest", None)
    payload["submission_digest"] = canonical_digest(payload)
    derived = injection_root(run) / f"release-{contract.injection_id[-12:]}.json"
    atomic_write_json(derived, payload)
    receipt = injection_receipt_path(run, RELEASE_ACTION)
    atomic_write_json(
        receipt,
        {
            "schema_version": "evm.lifecycle_integrity_injection_receipt.v1",
            "injection_id": contract.injection_id,
            "action": contract.action,
            "run_id": contract.run_id,
            "action_digest": contract.action_digest,
            "target_uri": str(canonical),
            "derived_submission_uri": str(derived),
            "canonical_submission_sha256": file_digest(canonical),
            "derived_submission_sha256": file_digest(derived),
            "canonical_model_digest": original_model_digest,
            "injected_model_digest": injected_model_digest,
            "claim_uri": str(claim),
            "consumed_at": utc_now().isoformat(),
            "single_use": True,
            "canonical_mutation_allowed": False,
            "production_mutation_allowed": False,
        },
    )
    return derived

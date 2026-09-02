"""Fail-closed root gate for the pre-r8 r7s4 local framework.

No production verifier or execution wiring exists in this revision. The gate
therefore cannot read a repository, spawn a process, touch a service, or start
r8. Tests may provide observation callbacks solely to prove they stay unused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, NoReturn

from evm.scale_validation.phase_b2_r7s4_authority import (
    PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED,
    R7S4AuthorityError,
    require_verified_external_receipt,
)


PRODUCTION_WIRING_IMPLEMENTED = False
PRODUCTION_ENTRY_ENABLED = False
LIVE_WSL_ENABLED = False
SERVICE_CALLS_ENABLED = False
R8_ENABLED = False
FUTURE_PRODUCTION_ADMISSION_SEQUENCE = (
    "consumption_time_revalidate_raw_receipt_a0_and_expectation",
    "fixed_canonical_receipt_store_one_shot",
    "process_admission_only_after_one_shot_readback",
)
PRODUCTION_BLOCKERS = (
    "independent_external_authority_unavailable",
    "verifier_identity_allowlist_not_implemented",
    "authority_key_allowlist_not_implemented",
    "external_trust_root_not_implemented",
    "receipt_store_to_process_admission_wiring_not_implemented",
)
ZERO_CALL_COUNTS = {
    "repo_reads": 0,
    "process_spawn": 0,
    "service_calls": 0,
    "live_wsl": 0,
    "r8": 0,
    "automatic_retry": 0,
    "force_kill": 0,
}


class R7S4RootGateError(RuntimeError):
    """Root-gate rejection with immutable zero-call semantics."""

    call_counts = ZERO_CALL_COUNTS
    automatic_retry_allowed = False
    downstream_calls_allowed = False
    production_entry_enabled = False


@dataclass(frozen=True, slots=True)
class RootObservationHooks:
    """Adversarial observation seam; callbacks must never run in this revision."""

    repo_read: Callable[[], object]
    process_spawn: Callable[[], object]
    service_call: Callable[[], object]
    live_wsl: Callable[[], object]
    r8: Callable[[], object]


def enter_production_once(
    external_capability: object | None,
    *,
    observation_hooks: RootObservationHooks | None = None,
) -> NoReturn:
    """Reject before all downstream operations until independent wiring exists."""

    del observation_hooks
    if external_capability is None:
        raise R7S4RootGateError("r7s4_external_authority_capability_required")
    try:
        require_verified_external_receipt(external_capability)
    except R7S4AuthorityError as exc:
        raise R7S4RootGateError("r7s4_external_authority_capability_unanchored") from exc
    if PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED is not True:
        raise R7S4RootGateError("r7s4_production_external_authority_unavailable")
    if PRODUCTION_WIRING_IMPLEMENTED is not True:
        raise R7S4RootGateError("r7s4_production_wiring_not_implemented")
    if PRODUCTION_ENTRY_ENABLED is not True:
        raise R7S4RootGateError("r7s4_production_entry_disabled")
    if LIVE_WSL_ENABLED or SERVICE_CALLS_ENABLED or R8_ENABLED:
        raise R7S4RootGateError("r7s4_forbidden_live_or_r8_flag_enabled")
    raise R7S4RootGateError("r7s4_production_execution_intentionally_absent")


def root_gate_contract() -> dict[str, object]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.root-gate-contract.v1",
        "production_external_authority_configured": PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED,
        "production_wiring_implemented": PRODUCTION_WIRING_IMPLEMENTED,
        "production_entry_enabled": PRODUCTION_ENTRY_ENABLED,
        "live_wsl_enabled": LIVE_WSL_ENABLED,
        "service_calls_enabled": SERVICE_CALLS_ENABLED,
        "r8_enabled": R8_ENABLED,
        "required_future_production_admission_sequence": list(FUTURE_PRODUCTION_ADMISSION_SEQUENCE),
        "consumption_time_raw_receipt_a0_expectation_revalidation_required": True,
        "fixed_receipt_store_one_shot_required_before_process_admission": True,
        "process_admission_after_successful_one_shot_only": True,
        "required_production_sequence_enforced": False,
        "local_flag_flip_can_enable_execution": False,
        "production_blockers": list(PRODUCTION_BLOCKERS),
        "missing_or_unanchored_capability_blocks_before_repo_read": True,
        "missing_or_unanchored_capability_blocks_before_spawn": True,
        "missing_or_unanchored_capability_blocks_before_service_call": True,
        "call_counts": dict(ZERO_CALL_COUNTS),
        "automatic_retry_allowed": False,
    }


def main() -> int:
    try:
        enter_production_once(None)
    except R7S4RootGateError:
        return 73
    return 74


if __name__ == "__main__":
    raise SystemExit(main())

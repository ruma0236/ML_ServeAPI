from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.dev.update_s8_v4_e0_state import remediation_transition
from evm.scale_validation.v4_ledger import (
    V4LedgerError,
    append_event,
    calculate_event_hash,
    read_events,
    verify_events,
)


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/status/2026-08-24-s8-v4-progress-ledger.jsonl"


def test_committed_v4_ledger_hash_chain() -> None:
    events = read_events(LEDGER)
    assert len(events) >= 9
    assert events[-1]["event_hash"] == calculate_event_hash(events[-1])


def test_v4_ledger_rejects_mutation() -> None:
    events = read_events(LEDGER)
    mutated = copy.deepcopy(events)
    mutated[0]["summary"] = "mutated"
    with pytest.raises(V4LedgerError, match="event_hash"):
        verify_events(mutated)


def test_v4_ledger_append_preserves_existing_bytes(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    original = LEDGER.read_bytes()
    ledger.write_bytes(original)
    events = read_events(ledger)
    event = {
        "schema_version": "evm.s8_v4.progress_event.v1",
        "event_id": "s8-v4-9999",
        "event_type": "test",
        "work_item": "E0",
        "occurred_at": "2026-08-25T00:00:00Z",
        "from_status": "contract_frozen",
        "to_status": "ready",
        "source_git_revision": "a" * 40,
        "source_tree_sha": "b" * 40,
        "acceptance_results": [],
        "evidence_manifest": {"path": "fixture", "sha256": "c" * 64},
        "credit": "non_credit",
        "cleanup": "not_applicable",
        "rca": None,
        "summary": "fixture",
        "claim_boundary": "fixture",
    }
    appended = append_event(ledger, event)
    assert ledger.read_bytes().startswith(original)
    updated = read_events(ledger)
    assert len(updated) == len(events) + 1
    assert updated[-1]["event_hash"] == appended["event_hash"]


def test_e0_gpu_lease_remediation_is_recorded_as_append_only_amendment() -> None:
    transition = remediation_transition(
        {
            "failure": "ScenarioWorkloadError:s8-v4-e0-1-fixture",
            "credit": "zero_credit",
        },
        amendment=True,
    )
    assert transition["event_type"] == "e0_gpu_lease_remediation_amendment"
    assert "GPU lease" in transition["summary"]
    assert "E0" in transition["rca"]
    assert transition["credit"] == "non_credit"


def test_e0_triton_backend_failure_records_zero_credit_rca() -> None:
    transition = remediation_transition(
        {"failure": "HTTPError:500 Server Error for url: fixture", "credit": "zero_credit"},
        amendment=False,
    )
    assert transition["event_type"] == "e0_triton_backend_remediation_required"
    assert transition["credit"] == "zero_credit"
    assert "libnvrtc.so.12" in transition["rca"]


def test_e0_profiler_wrapper_failure_records_zero_credit_scope_rca() -> None:
    transition = remediation_transition(
        {"failure": "E0RuntimeError:e0_profiler_not_parseable:", "credit": "zero_credit"},
        amendment=False,
    )
    assert transition["event_type"] == "e0_profiler_scope_remediation_required"
    assert transition["credit"] == "zero_credit"
    assert "cuda-sw" in transition["rca"]


def test_e0_unsupported_nsys_method_records_cupti_remediation() -> None:
    transition = remediation_transition(
        {"failure": "CalledProcessError:nsys profile --trace=cuda-sw", "credit": "zero_credit"},
        amendment=False,
    )
    assert transition["event_type"] == "e0_profiler_method_remediation_required"
    assert transition["credit"] == "zero_credit"
    assert "CUPTI" in transition["rca"]


def test_e0_cupti_compile_failure_records_zero_credit_rca() -> None:
    transition = remediation_transition(
        {"failure": "E0RuntimeError:e0_cupti_probe_exit:1", "credit": "zero_credit"},
        amendment=False,
    )
    assert transition["event_type"] == "e0_cupti_compile_remediation_required"
    assert transition["credit"] == "zero_credit"
    assert "LD_LIBRARY_PATH" in transition["rca"]

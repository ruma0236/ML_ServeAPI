from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.dev.update_s8_v4_e0_state import (
    remediation_transition,
    validate_private_review_evidence,
    validate_review_handoff,
)
from evm.scale_validation.e0_runtime import canonical_sha256, sha256_file
from evm.scale_validation.s1_runtime import canonical_write
from evm.scale_validation.v4_ledger import (
    V4LedgerError,
    append_event,
    calculate_event_hash,
    read_events,
    verify_events,
)


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/status/2026-08-24-s8-v4-progress-ledger.jsonl"
E0_EXPERIMENT = ROOT / "docs/status/evidence/s8-v4-e0-environment-experiment.json"
E0_HANDOFF = ROOT / "docs/status/evidence/s8-v4-e0-review-handoff.json"
E0_VERIFIED_CLOSURE = ROOT / "docs/status/evidence/s8-v4-e0-verified-closure.json"
E0_PRE_SIGN_OFF_LEDGER_SHA256 = "0b825ab53b063ba181350b2ce3236b4b3b81cdf41736a09b03d99f102b89c58d"


def test_committed_v4_ledger_hash_chain() -> None:
    events = read_events(LEDGER)
    assert len(events) >= 9
    assert events[-1]["event_hash"] == calculate_event_hash(events[-1])


def test_e0_verified_sign_off_is_append_only_and_evidence_bound() -> None:
    ledger_lines = LEDGER.read_bytes().splitlines(keepends=True)
    assert hashlib.sha256(b"".join(ledger_lines[:41])).hexdigest() == (
        E0_PRE_SIGN_OFF_LEDGER_SHA256
    )

    events = read_events(LEDGER)
    event = next(item for item in events if item["event_id"] == "s8-v4-0042")
    closure_bytes = E0_VERIFIED_CLOSURE.read_bytes()
    closure = json.loads(closure_bytes)
    assert event["event_id"] == "s8-v4-0042"
    assert event["from_status"] == "review_pending"
    assert event["to_status"] == "verified"
    assert event["acceptance_credit"] is True
    assert event["reviewer_sign_off"]["result"] == "passed"
    assert event["verified_closure"]["sha256"] == hashlib.sha256(closure_bytes).hexdigest()
    assert closure["status"] == "verified"
    assert closure["acceptance_credit"] is True
    assert all(closure["acceptance"].values())
    assert all(closure["alignment"].values())


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


def test_e0_review_handoff_is_pending_and_fail_closed() -> None:
    evidence_bytes = E0_EXPERIMENT.read_bytes()
    evidence = json.loads(evidence_bytes)
    handoff = json.loads(E0_HANDOFF.read_bytes())
    validate_review_handoff(
        evidence,
        handoff,
        evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
    )
    mutated = copy.deepcopy(handoff)
    mutated["acceptance"]["E0-AC-03"]["result"] = "failed"
    with pytest.raises(RuntimeError, match="handoff_acceptance"):
        validate_review_handoff(
            evidence,
            mutated,
            evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
        )


def test_e0_private_review_bundle_is_rehashed_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "focused_e0": "focused-e0.log",
        "full_python": "full-python.log",
        "real_postgresql": "real-postgresql.log",
        "lifecycle_host": "lifecycle-host.log",
        "control_panel": "control-panel.log",
        "frontend_production_build": "frontend-build.log",
        "git_blob_evidence_validator": "git-evidence-validator.log",
    }
    for relative in files.values():
        (tmp_path / relative).write_text(relative, encoding="utf-8")
    revision = "a" * 40
    canonical_write(
        tmp_path / "runtime-cleanup.json",
        {"valid": True, "git": {"head": revision, "origin": revision}},
    )
    entries = [
        {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(tmp_path.iterdir())
    ]
    canonical_write(
        tmp_path / "private-review-index.json",
        {
            "schema_version": "evm.s8_v4.e0_review_private_index.v1",
            "artifact_count": len(entries),
            "total_bytes": sum(item["bytes"] for item in entries),
            "aggregate_sha256": canonical_sha256(entries),
            "entries": entries,
        },
    )
    handoff = {
        "source_identity": {"review_validation_commit": revision},
        "regressions": [
            {"name": name, "log_sha256": sha256_file(tmp_path / relative)}
            for name, relative in files.items()
        ],
        "cleanup": {"runtime_cleanup_sha256": sha256_file(tmp_path / "runtime-cleanup.json")},
        "private_review_evidence": {
            "artifact_count": len(entries),
            "total_bytes": sum(item["bytes"] for item in entries),
            "aggregate_sha256": canonical_sha256(entries),
            "index_sha256": sha256_file(tmp_path / "private-review-index.json"),
        },
    }

    class Completed:
        returncode = 0

    monkeypatch.setattr(
        "scripts.dev.update_s8_v4_e0_state.subprocess.run", lambda *a, **k: Completed()
    )
    validate_private_review_evidence(handoff, tmp_path)
    (tmp_path / "focused-e0.log").write_text("mutated", encoding="utf-8")
    with pytest.raises(RuntimeError, match="index"):
        validate_private_review_evidence(handoff, tmp_path)

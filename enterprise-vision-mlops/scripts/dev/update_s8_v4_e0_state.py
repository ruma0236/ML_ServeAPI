from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.v4_ledger import append_event, read_events  # noqa: E402
from evm.scale_validation.e0_runtime import canonical_sha256, sha256_file  # noqa: E402


MANIFEST = ROOT / "docs/status/evidence/s8-v4-evidence-manifest.json"
LEDGER = ROOT / "docs/status/2026-08-24-s8-v4-progress-ledger.jsonl"
PROGRESS = ROOT / "docs/status/2026-08-24-s8-v4-progress.md"
EVIDENCE = ROOT / "docs/status/evidence/s8-v4-e0-environment-experiment.json"
REVIEW_HANDOFF = ROOT / "docs/status/evidence/s8-v4-e0-review-handoff.json"


TRANSITIONS = {
    "ready": {
        "event_type": "e0_ready_checkpoint",
        "summary": "E0 implementation, frozen identities, focused tests, and non-credit preflight are ready for controlled runtime qualification",
        "credit": "non_credit",
        "acceptance": "E0-AC-01..04 pending",
        "next_gate": "Append running checkpoint, then execute three controlled repetitions",
    },
    "running": {
        "event_type": "e0_runtime_started",
        "summary": "E0 controlled runtime qualification started from a clean committed revision",
        "credit": "non_credit",
        "acceptance": "E0-AC-01..04 pending",
        "next_gate": "Three repetitions, strict evidence projection, and complete cleanup",
    },
    "remediation_required": {
        "event_type": "e0_preflight_remediation_required",
        "summary": "E0 non-credit preflight stopped on a CUPTI location-detection defect; B0 and control-plane baselines remained unchanged",
        "credit": "non_credit",
        "acceptance": "E0-AC-01..04 pending; no acceptance repetition credited",
        "next_gate": "Commit the path-independent CUPTI probe, then append a fresh ready event",
    },
    "review_pending": {
        "event_type": "e0_evidence_ready",
        "summary": "E0 evidence package passed self-validation and awaits independent source-local review",
        "credit": "non_credit",
        "acceptance": "E0-AC-01..04 evidence-ready; reviewer sign-off pending",
        "next_gate": "Independent review; S6B-M remains blocked until a verified event",
    },
}


def remediation_transition(evidence: dict[str, Any], *, amendment: bool) -> dict[str, str]:
    failure = str(evidence.get("failure") or "")
    if failure.startswith("E0RuntimeError:e0_cupti_probe_exit:1"):
        return {
            "event_type": "e0_cupti_compile_remediation_required",
            "summary": (
                "E0 stopped before CUPTI execution because NVCC rejected an unnecessary "
                "GCC-style rpath option; cleanup restored B0 and no repetition received credit"
            ),
            "credit": "zero_credit",
            "acceptance": "E0-AC-01..04 pending; no acceptance repetition credited",
            "next_gate": "Remove the redundant rpath option and rerun all three repetitions",
            "rca": (
                "The probe linked against the correct CUPTI directory but passed -Wl directly "
                "to NVCC. Runtime library resolution is already frozen through LD_LIBRARY_PATH, "
                "so removing the unsupported and redundant rpath flag is the minimal fix."
            ),
        }
    if failure.startswith("CalledProcessError:") and "--trace=cuda-sw" in failure:
        return {
            "event_type": "e0_profiler_method_remediation_required",
            "summary": (
                "E0 stopped before profiler credit because the pinned Nsight 2025.4 CLI "
                "does not implement the cuda-sw option; cleanup restored B0 and no repetition "
                "received acceptance credit"
            ),
            "credit": "zero_credit",
            "acceptance": "E0-AC-01..04 pending; no acceptance repetition credited",
            "next_gate": (
                "Use the frozen image's supported CUPTI Activity API path and rerun all three "
                "independent repetitions"
            ),
            "rca": (
                "Current Nsight documentation names cuda-sw, but the exact 2025.4.1 CLI in "
                "the frozen Triton image exposes only cuda and cuda-hw. The V4 contract permits "
                "Nsight or CUPTI, so remediation selects direct CUPTI kernel activity records "
                "without changing the Triton image digest."
            ),
        }
    if failure.startswith("E0RuntimeError:e0_profiler_not_parseable"):
        return {
            "event_type": "e0_profiler_scope_remediation_required",
            "summary": (
                "E0 Triton CUDA inference and the standalone CUDA probe passed, but Nsight's "
                "default trace recorded CUDA API calls without GPU workload rows; cleanup "
                "restored B0 and the attempt received zero acceptance credit"
            ),
            "credit": "zero_credit",
            "acceptance": "E0-AC-01..04 pending; no acceptance repetition credited",
            "next_gate": (
                "Force the vendor-documented cuda-sw trace method for WSL2, then rerun all "
                "three independent repetitions"
            ),
            "rca": (
                "Both the Triton wrapper and a same-container deterministic CUDA probe "
                "produced reports with CUDA API launch calls but no GPU workload rows under "
                "the default cuda method. WSL2 is a virtualized environment, so the frozen "
                "profiler contract now forces Nsight's legacy software trace, cuda-sw."
            ),
        }
    if failure.startswith("HTTPError:500 Server Error"):
        return {
            "event_type": "e0_triton_backend_remediation_required",
            "summary": (
                "E0 reached Triton readiness but the first inference failed because the "
                "Python backend CuPy runtime expected CUDA 12 libraries while the frozen "
                "official image provides CUDA 13; cleanup restored B0 and no repetition "
                "received acceptance credit"
            ),
            "credit": "zero_credit",
            "acceptance": "E0-AC-01..04 pending; no acceptance repetition credited",
            "next_gate": (
                "Use the frozen image's PyTorch GPU backend with a deterministic TorchScript "
                "model, then append a fresh ready event"
            ),
            "rca": (
                "The Triton image bundled CuPy built for CUDA 12 while its CUDA runtime is 13. "
                "The Python backend therefore failed to load libnvrtc.so.12. The remediation "
                "keeps the image digest fixed and moves only the deterministic test model to "
                "the image-supported PyTorch GPU backend."
            ),
        }
    if failure.startswith("ScenarioWorkloadError:s8-v4-e0-"):
        return {
            "event_type": (
                "e0_gpu_lease_remediation_amendment"
                if amendment
                else "e0_gpu_lease_remediation_required"
            ),
            "summary": (
                "E0 stopped fail-closed before Triton start because the shared GPU lease "
                "contract did not admit the exact E0 identity; B0 was restored and no "
                "acceptance repetition was credited"
            ),
            "credit": "non_credit",
            "acceptance": "E0-AC-01..04 pending; no acceptance repetition credited",
            "next_gate": (
                "Commit the exact E0 lease identity extension, then append a fresh ready event"
            ),
            "rca": (
                "The shared scale-validation GPU lease admitted only S4 and S7 identities. "
                "E0 was rejected before Triton start; remediation is restricted to E0, the "
                "tabular family, and the s8-v4-e0- run prefix."
            ),
        }
    return {
        **TRANSITIONS["remediation_required"],
        "rca": str(evidence.get("rca") or "CUPTI path detection was not portable."),
    }


def validate_review_handoff(
    evidence: dict[str, Any], handoff: dict[str, Any], *, evidence_sha256: str
) -> None:
    if (
        handoff.get("status") != "review_pending"
        or handoff.get("evidence_ready") is not True
        or handoff.get("acceptance_credit") is not False
        or handoff.get("reviewer_sign_off") != "pending"
    ):
        raise RuntimeError("e0_review_handoff_state_invalid")
    source_identity = dict(handoff.get("source_identity", {}))
    if source_identity.get("runtime_revision") != evidence["source_identity"]["runtime_revision"]:
        raise RuntimeError("e0_review_handoff_runtime_revision")
    if source_identity.get("experiment_sha256") != evidence_sha256:
        raise RuntimeError("e0_review_handoff_experiment_sha256")
    acceptance = dict(evidence.get("acceptance", {}))
    handoff_acceptance = dict(handoff.get("acceptance", {}))
    observed = {
        criterion: dict(result).get("result") == "passed"
        for criterion, result in handoff_acceptance.items()
    }
    if observed != acceptance:
        raise RuntimeError("e0_review_handoff_acceptance")


def validate_private_review_evidence(handoff: dict[str, Any], root: Path) -> None:
    if not root.is_dir():
        raise RuntimeError("e0_review_private_root_missing")
    index_path = root / "private-review-index.json"
    index = json.loads(index_path.read_bytes())
    expected_files = {
        "focused_e0": "focused-e0.log",
        "full_python": "full-python.log",
        "real_postgresql": "real-postgresql.log",
        "lifecycle_host": "lifecycle-host.log",
        "control_panel": "control-panel.log",
        "frontend_production_build": "frontend-build.log",
        "git_blob_evidence_validator": "git-evidence-validator.log",
    }
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == index_path:
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("../") or Path(relative).is_absolute():
            raise RuntimeError("e0_review_private_path_invalid")
        entries.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    expected_index = {
        "artifact_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "aggregate_sha256": canonical_sha256(entries),
    }
    for key, expected in expected_index.items():
        if index.get(key) != expected:
            raise RuntimeError(f"e0_review_private_index_{key}")
    if index.get("entries") != entries:
        raise RuntimeError("e0_review_private_index_entries")
    review = dict(handoff.get("private_review_evidence", {}))
    for key, expected in expected_index.items():
        if review.get(key) != expected:
            raise RuntimeError(f"e0_review_handoff_private_{key}")
    if review.get("index_sha256") != sha256_file(index_path):
        raise RuntimeError("e0_review_handoff_private_index_sha256")
    regressions = {item["name"]: item for item in handoff.get("regressions", [])}
    if set(regressions) != set(expected_files):
        raise RuntimeError("e0_review_handoff_regression_set")
    for name, relative in expected_files.items():
        if regressions[name].get("log_sha256") != sha256_file(root / relative):
            raise RuntimeError(f"e0_review_handoff_regression_sha256:{name}")
    cleanup_path = root / "runtime-cleanup.json"
    cleanup = json.loads(cleanup_path.read_bytes())
    if cleanup.get("valid") is not True:
        raise RuntimeError("e0_review_cleanup_invalid")
    if handoff.get("cleanup", {}).get("runtime_cleanup_sha256") != sha256_file(cleanup_path):
        raise RuntimeError("e0_review_cleanup_sha256")
    review_revision = handoff.get("source_identity", {}).get("review_validation_commit")
    if not isinstance(review_revision, str) or len(review_revision) != 40:
        raise RuntimeError("e0_review_validation_revision")
    subprocess.run(
        ["git", "cat-file", "-e", f"{review_revision}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", review_revision, "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    ).returncode:
        raise RuntimeError("e0_review_validation_revision_ancestry")
    if cleanup.get("git") != {"head": review_revision, "origin": review_revision}:
        raise RuntimeError("e0_review_cleanup_revision")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append an E0 V4 state transition.")
    parser.add_argument("--to", choices=tuple(TRANSITIONS), required=True)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--review-handoff", type=Path, default=REVIEW_HANDOFF)
    parser.add_argument("--private-review-root", type=Path)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def git_value(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, allow_nan=False, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    args = parse_args()
    revision = git_value("rev-parse", "HEAD")
    tree = git_value("rev-parse", "HEAD^{tree}")
    generated_at = utc_now()
    manifest = json.loads(MANIFEST.read_bytes())
    e0 = next(item for item in manifest["work_items"] if item["work_item"] == "E0")
    prior = str(e0["status"])
    expected_prior = {
        "ready": {"contract_frozen", "remediation_required"},
        "running": "ready",
        "remediation_required": {"running", "remediation_required"},
        "review_pending": "running",
    }[args.to]
    if isinstance(expected_prior, set):
        prior_valid = prior in expected_prior
    else:
        prior_valid = prior == expected_prior
    if not prior_valid:
        raise RuntimeError(f"e0_transition_invalid:{prior}->{args.to}")
    acceptance_results: list[dict[str, Any]] = []
    cleanup: Any = "not_applicable_pre_execution"
    review_handoff_ref: dict[str, Any] | None = None
    if args.to == "review_pending":
        evidence = json.loads(args.evidence.read_bytes())
        if evidence.get("status") != "review_pending":
            raise RuntimeError("e0_evidence_not_review_pending")
        acceptance = dict(evidence.get("acceptance", {}))
        if not acceptance or not all(acceptance.values()):
            raise RuntimeError("e0_evidence_acceptance_not_ready")
        handoff = json.loads(args.review_handoff.read_bytes())
        validate_review_handoff(
            evidence,
            handoff,
            evidence_sha256=hashlib.sha256(args.evidence.read_bytes()).hexdigest(),
        )
        if args.private_review_root is None:
            raise RuntimeError("e0_review_private_root_required")
        validate_private_review_evidence(handoff, args.private_review_root)
        review_handoff_ref = {
            "path": args.review_handoff.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(args.review_handoff.read_bytes()).hexdigest(),
        }
        acceptance_results = [
            {"criterion": criterion, "result": "passed" if passed else "failed"}
            for criterion, passed in sorted(acceptance.items())
        ]
        cleanup = evidence.get("cleanup")
        successful_attempts = [
            {
                "attempt_id": item["summary"]["attempt_id"],
                "repetition": item["summary"]["repetition"],
                "credit": item["summary"]["credit"],
                "passed": item["summary"]["passed"],
                "private_evidence_sha256": item["private_evidence"]["sha256"],
                "public_evidence": args.evidence.relative_to(ROOT).as_posix(),
            }
            for item in evidence["attempts"]
        ]
        failed_attempts = [
            {
                "attempt_id": item["attempt_id"],
                "credit": item["credit"],
                "passed": False,
                "failure": item["failure"],
                "private_evidence_sha256": item["private_evidence"]["sha256"],
                "public_evidence": args.evidence.relative_to(ROOT).as_posix(),
            }
            for item in evidence.get("failed_attempts_and_rca", [])
        ]
        e0["attempts"] = [*failed_attempts, *successful_attempts]
        e0["evidence"] = {
            "path": args.evidence.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest(),
            "private_aggregate_sha256": evidence["private_evidence"]["aggregate_sha256"],
            "runtime_revision": evidence["source_identity"]["runtime_revision"],
            "review_handoff": review_handoff_ref,
        }
    e0["status"] = args.to
    e0["reviewer_sign_off"] = "pending"
    manifest["generated_at"] = generated_at
    manifest["runtime_execution_started"] = args.to in {
        "running",
        "remediation_required",
        "review_pending",
    }
    manifest["acceptance_credit"] = False
    write_json(MANIFEST, manifest)
    manifest_sha = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()

    events = read_events(LEDGER)
    next_number = max(int(str(event["event_id"]).rsplit("-", 1)[1]) for event in events) + 1
    transition: dict[str, Any] = TRANSITIONS[args.to]
    transition_evidence: dict[str, Any] | None = None
    if args.to == "remediation_required":
        transition_evidence = json.loads(args.evidence.read_bytes())
        transition = remediation_transition(
            transition_evidence,
            amendment=prior == "remediation_required",
        )
    event = {
        "schema_version": "evm.s8_v4.progress_event.v1",
        "event_id": f"s8-v4-{next_number:04d}",
        "event_type": transition["event_type"],
        "work_item": "E0",
        "occurred_at": generated_at,
        "from_status": prior,
        "to_status": args.to,
        "source_git_revision": revision,
        "source_tree_sha": tree,
        "acceptance_results": acceptance_results,
        "evidence_manifest": {
            "path": MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": manifest_sha,
        },
        "credit": transition["credit"],
        "cleanup": cleanup,
        "rca": transition.get("rca"),
        "summary": transition["summary"],
        "claim_boundary": (
            "one Windows/WSL2 physical node, one RTX 4080, controlled traffic; "
            "no production SLA, HA/DR, multi-GPU/MIG/MPS, autoscaling, "
            "security/privacy/compliance, FinOps, end-to-end retraining, or long-term drift claim"
        ),
    }
    if transition_evidence is not None:
        event["failure_evidence"] = {
            "attempt_id": transition_evidence.get("attempt_id"),
            "credit": transition_evidence.get("credit"),
            "failure": transition_evidence.get("failure"),
            "sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest(),
        }
        if prior == "remediation_required":
            event["amends_event_id"] = events[-1]["event_id"]
    if review_handoff_ref is not None:
        event["review_handoff"] = review_handoff_ref
    appended = append_event(LEDGER, event)

    progress = PROGRESS.read_text(encoding="utf-8")
    row = (
        f"| E0 | EVM-299 / SCRUM-210 | {args.to} | {transition['credit']} | "
        f"{transition['acceptance']} | {transition['next_gate']} |"
    )
    progress, replacements = re.subn(
        r"^\| E0 \| EVM-299 / SCRUM-210 \|.*$",
        row,
        progress,
        count=1,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        raise RuntimeError("e0_progress_row_missing")
    progress = re.sub(
        r"^Generated: .+$", f"Generated: {generated_at}", progress, count=1, flags=re.MULTILINE
    )
    chronology_action = (
        f"appended an amendment while remaining `{args.to}`"
        if prior == args.to
        else f"transitioned `{prior}` -> `{args.to}`"
    )
    chronology = (
        f"\n- `{generated_at}`: E0 {chronology_action} at source `{revision}`. "
        f"{transition['summary']}. This checkpoint is `{transition['credit']}` and reviewer "
        "sign-off remains pending.\n"
    )
    marker = "\n## External Canonical References\n"
    if marker not in progress:
        raise RuntimeError("e0_progress_chronology_marker_missing")
    progress = progress.replace(marker, chronology + marker, 1)
    PROGRESS.write_text(progress, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "event_id": appended["event_id"],
                "event_hash": appended["event_hash"],
                "from": prior,
                "to": args.to,
                "source_revision": revision,
                "manifest_sha256": manifest_sha,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

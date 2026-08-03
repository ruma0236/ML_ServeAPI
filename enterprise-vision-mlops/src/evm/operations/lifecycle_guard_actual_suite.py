from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.control_panel.readiness_evaluator import runtime_path


SCHEMA = "evm.lifecycle_guard_actual_injection_suite.v1"
ORDER = ("E", "C", "B", "D", "A")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def evidence_path(value: str, *, index_root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = index_root / path
    if path.is_file():
        return path.resolve()
    mapped = runtime_path(value)
    return mapped.resolve()


def verify_evidence_index(index_path: Path) -> dict[str, Any]:
    index = read_json(index_path)
    artifacts = index.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError(f"evidence_index_artifacts_missing:{index_path}")
    if int(index.get("artifact_count") or -1) != len(artifacts):
        raise RuntimeError(f"evidence_index_count_mismatch:{index_path}")
    checked: list[dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            raise RuntimeError(f"evidence_index_entry_invalid:{index_path}")
        location = str(item.get("uri") or item.get("path") or "")
        path = evidence_path(location, index_root=index_path.parent)
        expected = str(item.get("sha256") or "")
        expected_size = int(item.get("size_bytes") or -1)
        if not path.is_file():
            raise RuntimeError(f"evidence_artifact_missing:{path}")
        observed = file_digest(path)
        size = path.stat().st_size
        if observed != expected or size != expected_size:
            raise RuntimeError(f"evidence_artifact_mismatch:{path}")
        checked.append(
            {
                "uri": str(path),
                "sha256": observed,
                "size_bytes": size,
            }
        )
    return {
        "index_uri": str(index_path.resolve()),
        "index_sha256": file_digest(index_path),
        "artifact_count": len(checked),
        "matched": len(checked),
    }


def result_index_path(result_path: Path, result: dict[str, Any]) -> Path:
    configured = str(result.get("evidence_index_uri") or "")
    if configured:
        path = evidence_path(configured, index_root=result_path.parent)
    else:
        path = result_path.parent / "evidence-index.json"
    if not path.is_file():
        raise RuntimeError(f"result_evidence_index_missing:{path}")
    return path.resolve()


def scenario_identity(
    scenario: str,
    result_path: Path,
    result: dict[str, Any],
    companion_path: Path | None,
) -> dict[str, Any]:
    if scenario == "E":
        if (
            result.get("status") != "pass"
            or result.get("mode") != "lifecycle_stage_injection"
        ):
            raise RuntimeError("scenario_e_integrated_result_required")
        run_ids = [
            str(result.get("data_blocked_run_id") or ""),
            str(result.get("release_blocked_run_id") or ""),
        ]
        injection = "L2 run-local integrity block and L6 release identity block"
    elif scenario == "C":
        if result.get("status") != "pass" or not all(
            (result.get("checks") or {}).values()
        ):
            raise RuntimeError("scenario_c_pass_required")
        run_ids = [
            str(result.get("run_id") or ""),
            str(result.get("rejection_run_id") or ""),
        ]
        injection = "pre-training quality/drift review hold"
    elif scenario == "B":
        branches = result.get("branches")
        if (
            result.get("status") != "pass"
            or not isinstance(branches, list)
            or len(branches) != 2
            or any(item.get("status") != "pass" for item in branches)
        ):
            raise RuntimeError("scenario_b_two_pass_branches_required")
        run_ids = [str(item.get("lifecycle_run_id") or "") for item in branches]
        injection = "quality admission breach and runtime replay breach"
    elif scenario == "D":
        if (
            result.get("status") != "pass"
            or (result.get("injection") or {}).get("target") != "lifecycle_worker"
        ):
            raise RuntimeError("scenario_d_exact_worker_pass_required")
        run_ids = [str(result.get("run_id") or "")]
        injection = "exact lifecycle worker stop during reserved/running training"
    else:
        if result.get("status") != "passed" or companion_path is None:
            raise RuntimeError("scenario_a_pass_and_candidate_required")
        companion = read_json(companion_path)
        if (
            companion.get("schema_version")
            != "evm.lifecycle_guard_scenario_a_candidate.v1"
            or companion.get("status") != "pass"
        ):
            raise RuntimeError("scenario_a_fresh_candidate_pass_required")
        m1_package = read_json(result_path.parent / "m1-package.json")
        candidate_run_id = str(companion.get("lifecycle_run_id") or "")
        if m1_package.get("lifecycle_run_id") != candidate_run_id:
            raise RuntimeError("scenario_a_candidate_run_identity_mismatch")
        run_ids = [candidate_run_id]
        injection = "exact committed-M1 B0 Pod restart after fresh candidate lifecycle"
    if any(not value.startswith("lifecycle-") for value in run_ids):
        raise RuntimeError(f"scenario_lifecycle_run_identity_missing:{scenario}")
    if len(run_ids) != len(set(run_ids)):
        raise RuntimeError(f"scenario_lifecycle_run_duplicate:{scenario}")
    return {
        "run_ids": run_ids,
        "source_revision": str(
            result.get("source_commit") or result.get("source_revision") or ""
        ),
        "injection": injection,
        "companion_result_uri": (
            str(companion_path.resolve()) if companion_path else None
        ),
        "companion_result_sha256": (
            file_digest(companion_path) if companion_path else None
        ),
    }


def record(
    *,
    suite_root: Path,
    suite_id: str,
    source_checkpoint: str,
    scenario: str,
    result_path: Path,
    companion_path: Path | None = None,
) -> Path:
    scenario = scenario.upper()
    if scenario not in ORDER:
        raise RuntimeError(f"scenario_invalid:{scenario}")
    manifest_path = suite_root / "suite-manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if manifest.get("suite_id") != suite_id:
            raise RuntimeError("suite_id_mismatch")
    else:
        manifest = {
            "schema_version": SCHEMA,
            "suite_id": suite_id,
            "execution_order": list(ORDER),
            "source_checkpoint": source_checkpoint,
            "created_at": utc_now(),
            "canonical_records": {
                "git": "docs/status/2026-08-02-full-lifecycle-guard-validation-execution.md",
                "jira": "SCRUM-193",
                "notion": "3b010ad2-dcad-817f-8258-f1736f7116aa",
                "obsidian": (
                    "08_Codex_Memory/01_Work_Logs/"
                    "2026-08-02 Full Lifecycle Guard Validation Execution.md"
                ),
            },
            "scenarios": {},
            "status": "in_progress",
        }
    scenarios = manifest["scenarios"]
    if scenario in scenarios:
        raise RuntimeError(f"scenario_already_recorded:{scenario}")
    expected = ORDER[len(scenarios)]
    if scenario != expected:
        raise RuntimeError(f"scenario_order_violation:expected={expected}:actual={scenario}")

    result_path = result_path.resolve()
    result = read_json(result_path)
    index = verify_evidence_index(result_index_path(result_path, result))
    identity = scenario_identity(scenario, result_path, result, companion_path)
    existing_run_ids = {
        run_id
        for entry in scenarios.values()
        for run_id in entry.get("lifecycle_run_ids", [])
    }
    overlap = existing_run_ids.intersection(identity["run_ids"])
    if overlap:
        raise RuntimeError(f"cross_scenario_run_reuse:{sorted(overlap)}")

    scenarios[scenario] = {
        "status": "pass",
        "recorded_at": utc_now(),
        "result_uri": str(result_path),
        "result_sha256": file_digest(result_path),
        "evidence_index_uri": index["index_uri"],
        "evidence_index_sha256": index["index_sha256"],
        "evidence_artifacts_matched": index["matched"],
        "lifecycle_run_ids": identity["run_ids"],
        "source_revision": identity["source_revision"],
        "injection": identity["injection"],
        "companion_result_uri": identity["companion_result_uri"],
        "companion_result_sha256": identity["companion_result_sha256"],
        "claim_boundary": result.get("claim_boundary"),
    }
    manifest["updated_at"] = utc_now()
    if len(scenarios) == len(ORDER):
        manifest["status"] = "pass"
        manifest["completed_at"] = utc_now()
    write_json(manifest_path, manifest)

    references: list[dict[str, Any]] = []
    for name, entry in scenarios.items():
        for kind in ("result", "evidence_index", "companion_result"):
            uri = entry.get(f"{kind}_uri")
            digest = entry.get(f"{kind}_sha256")
            if uri and digest:
                references.append(
                    {"scenario": name, "kind": kind, "uri": uri, "sha256": digest}
                )
    write_json(
        suite_root / "suite-evidence-index.json",
        {
            "schema_version": "evm.lifecycle_guard_actual_suite_index.v1",
            "suite_id": suite_id,
            "manifest_uri": str(manifest_path.resolve()),
            "manifest_sha256": file_digest(manifest_path),
            "reference_count": len(references),
            "references": references,
            "generated_at": utc_now(),
        },
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record one ordered A-E actual lifecycle injection result."
    )
    parser.add_argument("--suite-root", required=True, type=Path)
    parser.add_argument("--suite-id", required=True)
    parser.add_argument("--source-checkpoint", required=True)
    parser.add_argument("--scenario", required=True, choices=ORDER)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--companion-result", type=Path)
    args = parser.parse_args()
    manifest = record(
        suite_root=args.suite_root.resolve(),
        suite_id=args.suite_id,
        source_checkpoint=args.source_checkpoint,
        scenario=args.scenario,
        result_path=args.result.resolve(),
        companion_path=(
            args.companion_result.resolve() if args.companion_result else None
        ),
    )
    print(json.dumps({"manifest_uri": str(manifest.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

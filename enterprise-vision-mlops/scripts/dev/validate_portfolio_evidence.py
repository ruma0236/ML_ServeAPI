from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_LIFECYCLE_STAGES = {
    "profile_snapshot",
    "data_pipeline",
    "model_training",
    "model_evaluation",
    "artifact_readiness",
    "ci_ct_gate",
    "approval",
    "deployment",
    "serving_validation",
    "monitoring",
}


def validate(
    scenario_root: Path,
    lifecycle_root: Path,
    *,
    data_root: Path,
    ct_root: Path,
) -> dict[str, object]:
    blockers: list[str] = []
    scenarios = [
        validate_scenario(path, blockers)
        for path in sorted(scenario_root.glob("*.json"))
    ]
    lifecycle = validate_latest_lifecycle(
        lifecycle_root,
        blockers,
        data_root=data_root,
        ct_root=ct_root,
    )
    verified_data_scenarios = sum(
        item["evidence_status"] in {"pass", "review_required", "verified_external"}
        for item in scenarios
    )
    material_gaps = sorted(
        {
            blocker
            for item in scenarios
            for blocker in item["platform_blockers"]
        }
        | {
            "multi_tenant_sso_rbac_not_verified",
            "ha_disaster_recovery_not_verified",
            "production_load_chaos_slo_not_verified",
        }
    )
    return {
        "schema_version": "evm.portfolio_evidence_validation.v1",
        "status": "pass" if not blockers else "failed",
        "portfolio_verdict": (
            "strong_individual_portfolio_with_material_enterprise_gaps"
            if not blockers
            else "evidence_integrity_failed"
        ),
        "verified_data_scenarios": verified_data_scenarios,
        "scenario_count": len(scenarios),
        "full_lifecycle_verified": bool(lifecycle.get("verified")),
        "latest_lifecycle": lifecycle,
        "scenarios": scenarios,
        "material_gaps": material_gaps,
        "evidence_blockers": blockers,
    }


def validate_scenario(path: Path, blockers: list[str]) -> dict[str, object]:
    payload = read_object(path)
    scenario = object_value(payload, "scenario")
    dataset = object_value(payload, "dataset")
    scenario_id = text_value(scenario, "scenario_id")
    manifest_path = Path(text_value(dataset, "manifest_uri"))
    split_path = Path(text_value(dataset, "split_manifest_uri"))
    state_path = Path(text_value(dataset, "output_root")) / "evidence" / "intake_state.json"
    intake_supported = bool(scenario.get("intake_supported"))
    state = read_object(state_path) if state_path.is_file() else {}
    record_count = int(state.get("records_output") or 0)
    manifest_sha256 = sha256_file(manifest_path) if manifest_path.is_file() else None
    if not record_count and manifest_path.is_file() and manifest_path.suffix == ".jsonl":
        with manifest_path.open("r", encoding="utf-8-sig") as handle:
            record_count = sum(bool(line.strip()) for line in handle)
    if not manifest_path.is_file():
        blockers.append(f"scenario_manifest_missing:{scenario_id}")
    if not split_path.is_file():
        blockers.append(f"scenario_split_manifest_missing:{scenario_id}")
    if intake_supported and not state:
        blockers.append(f"scenario_intake_state_missing:{scenario_id}")
    if state.get("manifest_sha256") and manifest_sha256 != state["manifest_sha256"]:
        blockers.append(f"scenario_manifest_digest_mismatch:{scenario_id}")
    if state.get("status") == "failed":
        blockers.append(f"scenario_intake_failed:{scenario_id}")
    license_id = text_value(dataset, "license_id")
    source_revision = text_value(dataset, "source_revision")
    if license_id.lower() in {"unknown", "none", "n/a"}:
        blockers.append(f"scenario_license_unknown:{scenario_id}")
    if len(source_revision) < 12:
        blockers.append(f"scenario_source_revision_not_immutable:{scenario_id}")
    evidence_status = (
        str(state.get("quality_status") or state.get("status") or "not_started")
        if intake_supported
        else "verified_external"
    )
    return {
        "scenario_id": scenario_id,
        "department": text_value(scenario, "department"),
        "modality": text_value(scenario, "modality"),
        "dataset_id": text_value(dataset, "dataset_id"),
        "dataset_version": text_value(dataset, "dataset_version"),
        "source_revision": source_revision,
        "license_id": license_id,
        "usage_policy": text_value(dataset, "usage_policy"),
        "record_count": record_count,
        "manifest_sha256": manifest_sha256,
        "evidence_status": evidence_status,
        "model_readiness": str(scenario.get("model_readiness") or "not_implemented"),
        "deployment_readiness": str(
            scenario.get("deployment_readiness") or "not_implemented"
        ),
        "platform_blockers": sorted(
            str(item) for item in scenario.get("platform_blockers", []) if item
        ),
    }


def validate_latest_lifecycle(
    lifecycle_root: Path,
    blockers: list[str],
    *,
    data_root: Path,
    ct_root: Path,
) -> dict[str, object]:
    candidates: list[dict[str, Any]] = []
    for path in lifecycle_root.glob("lifecycle-*/lifecycle_run.json"):
        try:
            payload = read_object(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("state") == "completed" and not payload.get("dry_run"):
            candidates.append(payload)
    if not candidates:
        blockers.append("completed_real_lifecycle_missing")
        return {"verified": False}
    run = max(candidates, key=lambda item: str(item.get("finished_at") or ""))
    run_id = str(run.get("run_id") or "")
    stages = {
        str(item.get("stage_id") or ""): item
        for item in run.get("stages", [])
        if isinstance(item, dict)
    }
    missing = sorted(EXPECTED_LIFECYCLE_STAGES - set(stages))
    incomplete = sorted(
        stage_id
        for stage_id in EXPECTED_LIFECYCLE_STAGES & set(stages)
        if stages[stage_id].get("state") != "completed"
    )
    if missing:
        blockers.extend(f"lifecycle_stage_missing:{stage_id}" for stage_id in missing)
    if incomplete:
        blockers.extend(f"lifecycle_stage_incomplete:{stage_id}" for stage_id in incomplete)
    required_evidence = {
        "cycle_snapshot_uri": run.get("cycle_snapshot_uri"),
        "model_matrix_uri": run.get("model_matrix_uri"),
        "readiness_uri": run.get("readiness_uri"),
        "real_test_validation_uri": run.get("real_test_validation_uri"),
        "ct_snapshot_uri": run.get("ct_snapshot_uri"),
        "ct_evaluation_uri": run.get("ct_evaluation_uri"),
    }
    evidence_files: dict[str, str] = {}
    for key, value in required_evidence.items():
        resolved = runtime_path(str(value or ""), data_root=data_root, ct_root=ct_root)
        evidence_files[key] = str(resolved)
        if not resolved.is_file():
            blockers.append(f"lifecycle_evidence_missing:{key}")
    runtime_states = {
        stage_id: str(stages.get(stage_id, {}).get("runtime_state") or "")
        for stage_id in EXPECTED_LIFECYCLE_STAGES
    }
    if runtime_states.get("model_training") != "complete":
        blockers.append("real_gpu_training_runtime_not_complete")
    if runtime_states.get("ci_ct_gate") != "success":
        blockers.append("ci_ct_runtime_not_success")
    if runtime_states.get("deployment") != "applied":
        blockers.append("deployment_runtime_not_applied")
    if runtime_states.get("serving_validation") != "ready":
        blockers.append("serving_runtime_not_ready")
    if runtime_states.get("monitoring") != "up":
        blockers.append("monitoring_runtime_not_up")
    lifecycle_blockers = [item for item in blockers if item.startswith("lifecycle_")]
    lifecycle_blockers.extend(
        item
        for item in blockers
        if item.startswith(("real_gpu_", "ci_ct_", "deployment_", "serving_", "monitoring_"))
    )
    return {
        "run_id": run_id,
        "source_commit": run.get("source_commit"),
        "source_branch": run.get("source_branch"),
        "finished_at": run.get("finished_at"),
        "cycle_id": run.get("cycle_id"),
        "stage_count": len(stages),
        "runtime_states": runtime_states,
        "evidence_files": evidence_files,
        "verified": not lifecycle_blockers,
    }


def runtime_path(value: str, *, data_root: Path, ct_root: Path) -> Path:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/app/artifacts/"):
        return data_root / "artifacts" / normalized.removeprefix("/app/artifacts/")
    if normalized.startswith("/mnt/evm-data/"):
        return data_root / normalized.removeprefix("/mnt/evm-data/")
    if normalized.startswith("/mnt/evm-ct/"):
        return ct_root / normalized.removeprefix("/mnt/evm-ct/")
    return Path(value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def object_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"object_required:{key}")
    return value


def text_value(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"text_required:{key}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-root", default="configs/scenarios")
    parser.add_argument(
        "--lifecycle-root",
        default="F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs",
    )
    parser.add_argument(
        "--data-root",
        default="F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    )
    parser.add_argument(
        "--ct-root",
        default="F:/EnterpriseMLOps_CT/enterprise-vision-mlops",
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    report = validate(
        Path(args.scenario_root),
        Path(args.lifecycle_root),
        data_root=Path(args.data_root),
        ct_root=Path(args.ct_root),
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

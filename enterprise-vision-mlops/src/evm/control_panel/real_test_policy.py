from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.schemas import CycleRun
from evm.core.config import project_root_from


FORBIDDEN_EVIDENCE_TERMS = (
    "mock adapter",
    "mock-only",
    "smoke-only",
    "placeholder prediction",
    "placeholder predictions",
    "synthetic-only",
)

NEGATION_OR_GUARD_TERMS = (
    "block",
    "blocked",
    "blocks",
    "cannot",
    "deny",
    "denies",
    "forbid",
    "forbids",
    "guard",
    "no ",
    "not ",
    "prevent",
    "prevents",
    "reject",
    "rejects",
    "without",
)


@dataclass(frozen=True)
class ClosureRecord:
    source_id: str
    title: str
    status: str
    sprint: str
    evidence: str

    @property
    def text(self) -> str:
        return " ".join([self.source_id, self.title, self.status, self.sprint, self.evidence])


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON: {path}")
    return payload


def read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fp:
        payload = tomllib.load(fp)
    payload["_config_path"] = str(path.resolve())
    payload["_project_root"] = str(project_root_from(path))
    return payload


def parse_issue_register(path: Path, sprint: str = "2026-07-W7") -> list[ClosureRecord]:
    records: list[ClosureRecord] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `EVM-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        source_id = cells[0].strip("`")
        records.append(
            ClosureRecord(
                source_id=source_id,
                title=cells[1],
                status=cells[2],
                sprint=cells[3],
                evidence=cells[4],
            )
        )
    return [record for record in records if record.sprint == sprint]


def _is_guarded_reference(text: str, start_index: int) -> bool:
    before = text[max(0, start_index - 160) : start_index]
    after = text[start_index : start_index + 80]
    window = f"{before} {after}"
    return any(term in window for term in NEGATION_OR_GUARD_TERMS)


def forbidden_evidence_claims(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower())
    claims: list[str] = []
    for term in FORBIDDEN_EVIDENCE_TERMS:
        start = 0
        while True:
            idx = normalized.find(term, start)
            if idx < 0:
                break
            if not _is_guarded_reference(normalized, idx):
                claims.append(term)
            start = idx + len(term)
    return sorted(set(claims))


def validate_real_test_policy(
    cycle: CycleRun,
    closure_records: list[ClosureRecord] | None = None,
) -> dict[str, Any]:
    closure_records = closure_records or []
    violations: list[dict[str, Any]] = []
    policy = cycle.model_matrix.real_test_policy if cycle.model_matrix else None

    if policy is None:
        violations.append({"code": "missing_real_test_policy", "message": "CycleRun.model_matrix.real_test_policy is required."})
    else:
        if policy.mock_allowed:
            violations.append({"code": "mock_allowed_true", "message": "W7 model evidence cannot allow mock adapters."})
        if policy.smoke_allowed:
            violations.append({"code": "smoke_allowed_true", "message": "W7 model evidence cannot allow smoke-only completion."})
        if not policy.requires_real_dataset:
            violations.append({"code": "real_dataset_not_required", "message": "W7 model evidence must require a real dataset."})
        if not policy.requires_real_training:
            violations.append({"code": "real_training_not_required", "message": "W7 model evidence must require real training."})

    if cycle.serving.placeholder is True:
        violations.append(
            {
                "code": "placeholder_serving_active",
                "message": "Placeholder serving blocks W7 model readiness and promotion claims.",
            }
        )

    done_records = [record for record in closure_records if record.status.lower() in {"done", "완료"}]
    for record in done_records:
        claims = forbidden_evidence_claims(record.text)
        if claims:
            violations.append(
                {
                    "code": "forbidden_done_evidence",
                    "source_id": record.source_id,
                    "terms": claims,
                    "message": "Done W7 closure record appears to rely on forbidden mock, placeholder, synthetic-only, or smoke-only evidence.",
                }
            )

    return {
        "valid": not violations,
        "cycle_id": cycle.cycle_id,
        "checked_records": len(closure_records),
        "checked_done_records": len(done_records),
        "policy": policy.model_dump(mode="json") if policy is not None else None,
        "violations": violations,
    }


def _metric_value(metrics: list[Any], name: str) -> float | None:
    for metric in metrics:
        if getattr(metric, "name", "") == name:
            return float(getattr(metric, "value"))
    return None


def _candidate_min_epochs(candidate_cfg: dict[str, Any], acceptance: dict[str, Any]) -> int:
    if "epochs" in candidate_cfg:
        return int(candidate_cfg["epochs"])
    if str(candidate_cfg.get("architecture")) == "efficientnet-b7":
        return int(acceptance.get("min_epochs_b7", 3))
    return int(acceptance.get("min_epochs_b0", 5))


def _required_candidate_files(candidate_dir: Path) -> dict[str, Path]:
    return {
        "candidate_summary": candidate_dir / "candidate_summary.json",
        "model_artifact": candidate_dir / "model.pt",
        "training_history": candidate_dir / "training_history.json",
        "confusion_matrix_json": candidate_dir / "confusion_matrix.json",
        "confusion_matrix_png": candidate_dir / "confusion_matrix.png",
        "gpu_profile": candidate_dir / "gpu_profile.json",
        "environment_report": candidate_dir / "environment_report.json",
        "model_card": candidate_dir / "model_card.md",
        "split_manifest": candidate_dir / "split_manifest.json",
    }


def validate_real_test_evidence(
    cycle: CycleRun,
    efficientnet_config_path: Path = Path("configs/w7_efficientnet_real_test.toml"),
) -> dict[str, Any]:
    config = read_toml(efficientnet_config_path)
    matrix_cfg = config.get("model_matrix", {})
    resources = config.get("resources", {})
    acceptance = config.get("acceptance", {})
    candidates_cfg = config.get("candidates", [])
    matrix_id = str(matrix_cfg.get("matrix_id", "w7-efficientnet-real-test-matrix"))
    artifact_root = Path(str(resources.get("artifact_root", "artifacts/w7/efficientnet")))
    if not artifact_root.is_absolute():
        artifact_root = Path(str(config["_project_root"])) / artifact_root
    matrix_path = artifact_root / "latest_model_matrix.json"
    matrix_payload = read_json(matrix_path)

    violations: list[dict[str, Any]] = []
    checked_candidates: list[dict[str, Any]] = []
    required_metrics = [str(item) for item in acceptance.get("required_metrics", [])]
    candidate_cfg_by_id = {
        str(item.get("candidate_id")): item
        for item in candidates_cfg
        if isinstance(item, dict) and item.get("candidate_id")
    }
    cycle_candidates = {
        candidate.candidate_id: candidate
        for candidate in (cycle.model_matrix.candidates if cycle.model_matrix else [])
    }
    matrix_candidates = {
        str(item.get("candidate_id")): item
        for item in matrix_payload.get("candidates", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }

    if cycle.model_matrix is None:
        violations.append({"code": "missing_cycle_model_matrix", "message": "CycleRun.model_matrix is required."})
    elif cycle.model_matrix.status != "pass":
        violations.append(
            {
                "code": "model_matrix_not_pass",
                "status": cycle.model_matrix.status,
                "message": "CycleRun.model_matrix must be pass after all candidates run.",
            }
        )

    configured_count = len(candidate_cfg_by_id)
    if int(matrix_payload.get("configured_candidate_count") or 0) != configured_count:
        violations.append(
            {
                "code": "configured_candidate_count_mismatch",
                "expected": configured_count,
                "actual": matrix_payload.get("configured_candidate_count"),
            }
        )
    if int(matrix_payload.get("candidate_count") or 0) != configured_count:
        violations.append(
            {
                "code": "candidate_count_mismatch",
                "expected": configured_count,
                "actual": matrix_payload.get("candidate_count"),
            }
        )

    split_manifest_value = str(matrix_payload.get("split_manifest") or "")
    split_manifest_path = Path(split_manifest_value) if split_manifest_value else None
    if split_manifest_path is None:
        violations.append({"code": "split_manifest_missing", "message": "latest_model_matrix.json must include split_manifest."})
        split_manifest = {}
    elif not split_manifest_path.is_file():
        violations.append(
            {
                "code": "split_manifest_not_found",
                "path": str(split_manifest_path),
                "message": "Configured split_manifest path must point to a JSON file.",
            }
        )
        split_manifest = {}
    else:
        split_manifest = read_json(split_manifest_path)
    split_counts = split_manifest.get("split_counts", {}) if isinstance(split_manifest.get("split_counts"), dict) else {}
    split_requirements = {
        "record_count": int(acceptance.get("min_total_records", 0)),
        "train": int(acceptance.get("min_train_images", 0)),
        "validation": int(acceptance.get("min_validation_images", 0)),
        "test": int(acceptance.get("min_test_images", 0)),
    }
    if int(split_manifest.get("record_count") or 0) < split_requirements["record_count"]:
        violations.append({"code": "split_record_count_too_small", "required": split_requirements["record_count"]})
    for split_name in ("train", "validation", "test"):
        if int(split_counts.get(split_name) or 0) < split_requirements[split_name]:
            violations.append(
                {
                    "code": "split_count_too_small",
                    "split": split_name,
                    "required": split_requirements[split_name],
                    "actual": split_counts.get(split_name),
                }
            )

    for candidate_id, candidate_cfg in candidate_cfg_by_id.items():
        cycle_candidate = cycle_candidates.get(candidate_id)
        matrix_candidate = matrix_candidates.get(candidate_id)
        if cycle_candidate is None:
            violations.append({"code": "candidate_missing_from_cycle", "candidate_id": candidate_id})
            continue
        if matrix_candidate is None:
            violations.append({"code": "candidate_missing_from_matrix_payload", "candidate_id": candidate_id})
            continue

        candidate_dir_value = str(matrix_candidate.get("artifact_uri") or cycle_candidate.artifact_uri or "")
        candidate_dir = Path(candidate_dir_value) if candidate_dir_value else Path("__missing_candidate_artifact_uri__")
        if not candidate_dir_value:
            violations.append({"code": "candidate_artifact_uri_missing", "candidate_id": candidate_id})
        elif not candidate_dir.is_dir():
            violations.append(
                {
                    "code": "candidate_artifact_dir_not_found",
                    "candidate_id": candidate_id,
                    "path": str(candidate_dir),
                }
            )
        files = _required_candidate_files(candidate_dir)
        missing_files = [name for name, path in files.items() if not path.exists()]
        if missing_files:
            violations.append(
                {"code": "candidate_missing_artifacts", "candidate_id": candidate_id, "missing": missing_files}
            )
        summary = read_json(files["candidate_summary"]) if files["candidate_summary"].exists() else {}
        if files["training_history"].exists():
            history_payload = json.loads(files["training_history"].read_text(encoding="utf-8"))
        else:
            history_payload = []

        epochs_required = _candidate_min_epochs(candidate_cfg, acceptance)
        epochs_actual = len(history_payload) if isinstance(history_payload, list) else 0
        if epochs_actual < epochs_required:
            violations.append(
                {
                    "code": "candidate_epoch_count_too_small",
                    "candidate_id": candidate_id,
                    "required": epochs_required,
                    "actual": epochs_actual,
                }
            )
        optimizer_steps = int(summary.get("optimizer_step_count") or 0)
        if optimizer_steps <= 0:
            violations.append({"code": "candidate_optimizer_steps_missing", "candidate_id": candidate_id})
        if cycle_candidate.status != "pass":
            violations.append(
                {"code": "candidate_not_pass", "candidate_id": candidate_id, "status": cycle_candidate.status}
            )
        if not (summary.get("mlflow_run_id") and cycle_candidate.run_uri):
            violations.append({"code": "candidate_mlflow_run_missing", "candidate_id": candidate_id})
        if not (summary.get("model_artifact") and Path(str(summary.get("model_artifact"))).exists()):
            violations.append({"code": "candidate_model_artifact_missing", "candidate_id": candidate_id})

        metric_names = {metric.name for metric in cycle_candidate.metrics}
        missing_metrics = [name for name in required_metrics if name not in metric_names]
        if missing_metrics:
            violations.append(
                {"code": "candidate_missing_metrics", "candidate_id": candidate_id, "missing": missing_metrics}
            )
        env = read_json(files["environment_report"]) if files["environment_report"].exists() else {}
        gpu = read_json(files["gpu_profile"]) if files["gpu_profile"].exists() else {}
        if acceptance.get("require_cuda_available") and env.get("cuda_available") is not True:
            violations.append({"code": "candidate_cuda_not_available", "candidate_id": candidate_id})
        if acceptance.get("require_cuda_device_name") and not env.get("cuda_device_name"):
            violations.append({"code": "candidate_cuda_device_missing", "candidate_id": candidate_id})
        if acceptance.get("require_gpu_profile") and not gpu.get("cuda_memory_peak_mb"):
            violations.append({"code": "candidate_gpu_peak_missing", "candidate_id": candidate_id})
        not_promotable = any(
            _metric_value(cycle_candidate.metrics, metric_name) is not None
            and threshold is not None
            and _metric_value(cycle_candidate.metrics, metric_name) < float(threshold)
            for metric_name, threshold in {
                "accuracy": acceptance.get("promotion_min_accuracy"),
                "f1": acceptance.get("promotion_min_f1"),
                "auroc": acceptance.get("promotion_min_auroc"),
            }.items()
        )
        if not_promotable and not cycle_candidate.promotion_blockers:
            violations.append({"code": "candidate_blocker_reason_missing", "candidate_id": candidate_id})

        checked_candidates.append(
            {
                "candidate_id": candidate_id,
                "status": cycle_candidate.status,
                "mlflow_run_id": summary.get("mlflow_run_id", ""),
                "artifact_uri": str(candidate_dir),
                "epochs": epochs_actual,
                "optimizer_steps": optimizer_steps,
                "metrics": {metric.name: metric.value for metric in cycle_candidate.metrics},
                "promotion_blockers": cycle_candidate.promotion_blockers,
            }
        )

    return {
        "valid": not violations,
        "schema_version": "evm.w7.real_test_evidence_validation.v1",
        "cycle_id": cycle.cycle_id,
        "matrix_id": matrix_id,
        "matrix_path": str(matrix_path),
        "configured_candidate_count": configured_count,
        "checked_candidate_count": len(checked_candidates),
        "split_manifest": split_manifest,
        "checked_candidates": checked_candidates,
        "violations": violations,
    }


def default_report_path() -> Path:
    return Path("artifacts/w7/real_test_policy/real_test_policy_report.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate W7 no-mock/no-smoke real-test policy.")
    parser.add_argument("--cycle-json", type=Path, help="Optional captured CycleRun payload. Defaults to live aggregation.")
    parser.add_argument("--issue-register", type=Path, default=Path("docs/issues/issue-register.md"))
    parser.add_argument("--sprint", default="2026-07-W7")
    parser.add_argument("--report", type=Path, default=default_report_path())
    parser.add_argument("--validate-evidence", action="store_true")
    parser.add_argument("--efficientnet-config", type=Path, default=Path("configs/w7_efficientnet_real_test.toml"))
    args = parser.parse_args()

    if args.cycle_json:
        cycle = CycleRun.model_validate(read_json(args.cycle_json))
    else:
        cycle = build_latest_cycle()
    records = parse_issue_register(args.issue_register, sprint=args.sprint)
    report = (
        validate_real_test_evidence(cycle, args.efficientnet_config)
        if args.validate_evidence
        else validate_real_test_policy(cycle, records)
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

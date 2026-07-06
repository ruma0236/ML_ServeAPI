from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from evm.core.config import resolve_path


REQUIRED_TOP_LEVEL_SECTIONS = (
    "domain_pack",
    "datasets",
    "manifest",
    "validation_rules",
    "model_adapters",
    "request_schema",
    "response_schema",
    "promotion_gates",
    "failure_scenarios",
)

REQUIRED_MANIFEST_FIELDS = {
    "dataset_id",
    "dataset_version",
    "sample_id",
    "image_uri",
    "split",
    "label",
    "label_type",
    "width",
    "height",
    "content_sha256",
    "source_uri",
    "license_id",
}

REQUIRED_REQUEST_FIELDS = {
    "request_id",
    "trace_id",
    "dataset_id",
    "sample_id",
    "image_uri",
    "question",
    "request_type",
    "prompt_version",
    "model_version",
}

REQUIRED_RESPONSE_FIELDS = {
    "request_id",
    "trace_id",
    "defect_detected",
    "defect_type",
    "severity",
    "evidence",
    "confidence_proxy",
    "recommended_action",
    "raw_model_output",
    "schema_valid",
    "latency_ms",
    "error_type",
}


def load_domain_pack(config: dict[str, Any], pack_path: str | Path) -> tuple[Path, dict[str, Any]]:
    path = resolve_path(config, pack_path)
    with path.open("rb") as fp:
        return path, tomllib.load(fp)


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("id", "")) for item in items if item.get("id")}


def validate_domain_pack(pack: dict[str, Any]) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []

    for section in REQUIRED_TOP_LEVEL_SECTIONS:
        if section not in pack:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "missing_section",
                    "message": f"Missing required section: {section}",
                }
            )

    domain = pack.get("domain_pack", {})
    for field in ("id", "name", "version", "reference_workload"):
        if not domain.get(field):
            diagnostics.append(
                {
                    "level": "error",
                    "code": "missing_domain_field",
                    "message": f"domain_pack.{field} is required",
                }
            )

    datasets = pack.get("datasets", [])
    if not isinstance(datasets, list) or not datasets:
        diagnostics.append(
            {
                "level": "error",
                "code": "missing_dataset",
                "message": "At least one dataset candidate is required",
            }
        )
    elif not any(str(item.get("role", "")).lower() == "primary" for item in datasets):
        diagnostics.append(
            {
                "level": "error",
                "code": "missing_primary_dataset",
                "message": "One dataset candidate must be marked as primary",
            }
        )

    manifest = pack.get("manifest", {})
    manifest_fields = set(manifest.get("required_fields", []))
    missing_manifest_fields = sorted(REQUIRED_MANIFEST_FIELDS - manifest_fields)
    for field in missing_manifest_fields:
        diagnostics.append(
            {
                "level": "error",
                "code": "missing_manifest_field",
                "message": f"manifest.required_fields must include {field}",
            }
        )

    request_schema = pack.get("request_schema", {})
    request_fields = set(request_schema.get("required_fields", []))
    for field in sorted(REQUIRED_REQUEST_FIELDS - request_fields):
        diagnostics.append(
            {
                "level": "error",
                "code": "missing_request_field",
                "message": f"request_schema.required_fields must include {field}",
            }
        )

    response_schema = pack.get("response_schema", {})
    response_fields = set(response_schema.get("required_fields", []))
    for field in sorted(REQUIRED_RESPONSE_FIELDS - response_fields):
        diagnostics.append(
            {
                "level": "error",
                "code": "missing_response_field",
                "message": f"response_schema.required_fields must include {field}",
            }
        )

    adapter_ids = set(pack.get("model_adapters", {}).keys())
    for adapter in ("mock", "real_candidate"):
        if adapter not in adapter_ids:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "missing_model_adapter",
                    "message": f"model_adapters.{adapter} is required",
                }
            )

    rule_ids = _ids(pack.get("validation_rules", []))
    for rule in ("required_manifest_fields", "image_readability", "split_label_integrity"):
        if rule not in rule_ids:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "missing_validation_rule",
                    "message": f"validation rule {rule} is required",
                }
            )

    gate_ids = _ids(pack.get("promotion_gates", []))
    for gate in ("schema_validity", "bad_prompt_regression"):
        if gate not in gate_ids:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "missing_promotion_gate",
                    "message": f"promotion gate {gate} is required",
                }
            )

    scenario_ids = _ids(pack.get("failure_scenarios", []))
    for scenario in ("bad_prompt_candidate", "corrupt_or_drifted_image", "schema_invalid_output"):
        if scenario not in scenario_ids:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "missing_failure_scenario",
                    "message": f"failure scenario {scenario} is required",
                }
            )

    if not diagnostics:
        diagnostics.append(
            {
                "level": "info",
                "code": "domain_pack_valid",
                "message": "Domain pack contract is valid",
            }
        )
    return diagnostics


def summarize_domain_pack(pack_path: Path, pack: dict[str, Any], diagnostics: list[dict[str, str]]) -> dict[str, Any]:
    error_count = sum(1 for item in diagnostics if item["level"] == "error")
    warning_count = sum(1 for item in diagnostics if item["level"] == "warn")
    datasets = pack.get("datasets", [])
    return {
        "status": "pass" if error_count == 0 else "fail",
        "domain_pack_path": str(pack_path),
        "domain_pack_id": str(pack.get("domain_pack", {}).get("id", "")),
        "domain_pack_version": str(pack.get("domain_pack", {}).get("version", "")),
        "reference_workload": str(pack.get("domain_pack", {}).get("reference_workload", "")),
        "dataset_candidates": [str(item.get("id", "")) for item in datasets],
        "primary_dataset": next(
            (str(item.get("id", "")) for item in datasets if str(item.get("role", "")).lower() == "primary"),
            "",
        ),
        "manifest_required_fields": len(pack.get("manifest", {}).get("required_fields", [])),
        "validation_rules": len(pack.get("validation_rules", [])),
        "promotion_gates": len(pack.get("promotion_gates", [])),
        "failure_scenarios": len(pack.get("failure_scenarios", [])),
        "error_count": error_count,
        "warning_count": warning_count,
        "diagnostics": diagnostics,
    }

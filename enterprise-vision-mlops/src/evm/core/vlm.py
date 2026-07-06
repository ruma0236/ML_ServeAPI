from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from evm.core.domain_pack import REQUIRED_RESPONSE_FIELDS


REQUEST_TYPES = {
    "visual_inspection",
    "caption",
    "visual_question_answering",
    "unsupported",
}


def classify_request(question: str, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    explicit = str(metadata.get("request_type", "") or "").strip()
    if explicit in REQUEST_TYPES:
        return explicit
    text = question.lower()
    if any(token in text for token in ("defect", "inspect", "anomaly", "scratch", "quality")):
        return "visual_inspection"
    if any(token in text for token in ("caption", "describe", "description")):
        return "caption"
    if "?" in question or any(token in text for token in ("what", "where", "is there")):
        return "visual_question_answering"
    return "unsupported"


def confidence_for_sample(sample_id: str, label: str) -> float:
    seed = f"{sample_id}:{label}".encode("utf-8")
    value = int(hashlib.sha256(seed).hexdigest()[:8], 16) / 0xFFFFFFFF
    return round(0.55 + value * 0.4, 6)


@dataclass(frozen=True)
class MockVlmAdapter:
    model_version: str

    def infer(self, request: dict[str, Any], candidate: str = "baseline") -> dict[str, Any]:
        started = time.perf_counter()
        if candidate == "bad_prompt":
            return {
                "request_id": request.get("request_id", ""),
                "trace_id": request.get("trace_id", ""),
                "raw_model_output": "bad candidate omitted required structured fields",
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }

        label = str(request.get("label", "") or "unknown").lower()
        defect_detected = label not in {"normal", "ok", "good", "pass"}
        defect_type = "none" if not defect_detected else str(request.get("defect_type") or label)
        severity = "none"
        if defect_detected:
            severity = str(request.get("severity") or "medium")
        confidence_proxy = confidence_for_sample(str(request.get("sample_id", "")), label)
        action = "accept" if not defect_detected else "route_to_human_review"
        evidence = (
            "Mock adapter found no defect indicators."
            if not defect_detected
            else f"Mock adapter flagged label-derived defect type: {defect_type}."
        )
        latency_ms = round((time.perf_counter() - started) * 1000 + 8.0 + confidence_proxy, 3)
        return {
            "request_id": request.get("request_id", ""),
            "trace_id": request.get("trace_id", ""),
            "defect_detected": defect_detected,
            "defect_type": defect_type,
            "severity": severity,
            "evidence": evidence,
            "confidence_proxy": confidence_proxy,
            "recommended_action": action,
            "raw_model_output": {
                "adapter": "mock_vlm_adapter",
                "model_version": self.model_version,
                "request_type": request.get("request_type", ""),
            },
            "schema_valid": True,
            "latency_ms": latency_ms,
            "error_type": "",
        }


def validate_vlm_response(response: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(field for field in REQUIRED_RESPONSE_FIELDS if field not in response)
    type_errors: list[str] = []
    if "defect_detected" in response and not isinstance(response["defect_detected"], bool):
        type_errors.append("defect_detected_not_bool")
    if "latency_ms" in response and not isinstance(response["latency_ms"], int | float):
        type_errors.append("latency_ms_not_number")
    if "confidence_proxy" in response and not isinstance(response["confidence_proxy"], int | float):
        type_errors.append("confidence_proxy_not_number")
    schema_valid = not missing and not type_errors
    return {
        "schema_valid": schema_valid,
        "missing_fields": missing,
        "type_errors": type_errors,
    }


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return round(float(ordered[index]), 6)

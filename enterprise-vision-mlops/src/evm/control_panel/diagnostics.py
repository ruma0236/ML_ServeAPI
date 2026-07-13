from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from evm.control_panel.readiness_evaluator import canonical_evidence_uri, runtime_path
from evm.control_panel.schemas import (
    ControlPanelDiagnostics,
    CycleRun,
    RuntimeResourceList,
    SourceFreshness,
    State,
    StatusDiagnostic,
)


DEFAULT_DIAGNOSTIC_ROOT = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
    "artifacts/w7/control_panel_diagnostics"
)
DEFAULT_TERMINAL_RESOURCE_DIAGNOSTIC_TTL_SECONDS = 3600
_DIAGNOSTIC_LOCK = RLock()


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def diagnostic_root() -> Path:
    configured = os.getenv("EVM_CONTROL_PANEL_DIAGNOSTIC_ROOT", DEFAULT_DIAGNOSTIC_ROOT)
    normalized = configured.replace("\\", "/")
    if normalized.lower().startswith("/app/artifacts") and Path("/app/artifacts").exists():
        return Path(configured)
    return runtime_path(configured)


def build_control_panel_diagnostics(
    cycle: CycleRun,
    resources: RuntimeResourceList,
    *,
    persist: bool = True,
) -> ControlPanelDiagnostics:
    generated_at = utc_now()
    items: list[StatusDiagnostic] = []

    def add(
        *,
        status: State,
        scope: str,
        component: str,
        code: str,
        source: str,
        evidence_uri: str | None = None,
        observed_at: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"warn", "blocked", "fail"}:
            return
        normalized_code = normalize_code(code)
        material = f"{scope}:{component}:{normalized_code}"
        items.append(
            StatusDiagnostic(
                diagnostic_id=f"diag-{hashlib.sha256(material.encode()).hexdigest()[:16]}",
                status=status,
                scope=scope,  # type: ignore[arg-type]
                component=component,
                code=normalized_code,
                summary=diagnostic_summary(normalized_code, details or {}),
                remediation=diagnostic_remediation(normalized_code),
                source=source,
                evidence_uri=evidence_uri,
                observed_at=observed_at,
                details=details or {},
            )
        )

    if cycle.status in {"warn", "blocked", "fail"}:
        add(
            status=cycle.status,
            scope="cycle",
            component=cycle.cycle_id,
            code=f"cycle_{cycle.status}",
            source="CycleRun.status",
            evidence_uri=cycle.artifacts[0].uri if cycle.artifacts else None,
            observed_at=generated_at,
        )

    for stage in cycle.stages:
        evidence_uri = stage.artifacts[0].uri if stage.artifacts else None
        if stage.status in {"warn", "blocked", "fail"}:
            add(
                status=stage.status,
                scope="stage",
                component=stage.stage_id,
                code=stage.failure_reason or f"stage_{stage.status}",
                source=f"CycleRun.stages.{stage.stage_id}",
                evidence_uri=evidence_uri,
                observed_at=stage.finished_at or stage.started_at,
                details={"current_step": stage.current_step, "progress": stage.progress},
            )
        for metric in stage.metrics:
            if metric.status in {"warn", "blocked", "fail"}:
                add(
                    status=metric.status,
                    scope="metric",
                    component=f"{stage.stage_id}.{metric.name}",
                    code=f"{metric.name}_threshold_not_met",
                    source=f"CycleRun.stages.{stage.stage_id}.metrics",
                    evidence_uri=evidence_uri,
                    observed_at=stage.finished_at or stage.started_at,
                    details={
                        "value": metric.value,
                        "threshold": metric.threshold,
                        "unit": metric.unit,
                    },
                )

    if cycle.readiness_evaluation:
        for check in cycle.readiness_evaluation.checks:
            for blocker in check.blockers:
                add(
                    status="blocked",
                    scope="readiness",
                    component=check.check_id,
                    code=blocker,
                    source="CycleRun.readiness_evaluation",
                    evidence_uri=check.evidence_uri,
                    observed_at=cycle.readiness_evaluation.evaluated_at,
                    details={"category": check.category},
                )

    if cycle.promotion_gate:
        promotion_evidence_uri = (
            cycle.cdct_gate.gate_report_uri
            if cycle.cdct_gate and cycle.cdct_gate.gate_report_uri
            else cycle.readiness_evaluation.report_uri
            if cycle.readiness_evaluation
            else None
        )
        for blocker in cycle.promotion_gate.blockers:
            add(
                status="blocked",
                scope="promotion",
                component="model-promotion-gate",
                code=blocker,
                source="CycleRun.promotion_gate",
                evidence_uri=promotion_evidence_uri,
                details={"decision": cycle.promotion_gate.decision},
            )

    if cycle.environment:
        for blocker in cycle.environment.promotion_blockers:
            add(
                status="blocked",
                scope="promotion",
                component=cycle.environment.name,
                code=blocker,
                source="CycleRun.environment",
                details={"namespace": cycle.environment.namespace},
            )

    if cycle.drift:
        metric_values = {
            "input_category_js": cycle.drift.input_category_js,
            "predicted_class_js": cycle.drift.predicted_class_js,
            "confidence_psi": cycle.drift.confidence_psi,
            "mean_confidence_drop": cycle.drift.mean_confidence_drop,
            "low_confidence_rate_increase": cycle.drift.low_confidence_rate_increase,
        }
        for rule in cycle.drift.triggered_rules:
            add(
                status="warn",
                scope="drift",
                component=cycle.drift.review_event_id or "measured-drift",
                code=f"drift_{rule}_threshold_exceeded",
                source="CycleRun.drift",
                evidence_uri=cycle.drift.report_uri,
                details={
                    "value": metric_values.get(rule),
                    "threshold": cycle.drift.thresholds.get(rule),
                    "review_status": cycle.drift.review_event_status,
                },
            )
        if cycle.drift.review_event_status in {"open", "acknowledged", "approved"}:
            add(
                status="warn",
                scope="drift",
                component=cycle.drift.review_event_id or "drift-review",
                code=f"drift_review_{cycle.drift.review_event_status}",
                source="CycleRun.drift.review_event_status",
                evidence_uri=cycle.drift.label_review_queue_uri,
                details={"queue_count": cycle.drift.review_queue_count},
            )

    if cycle.cdct_gate:
        for check in cycle.cdct_gate.failed_checks:
            add(
                status="blocked",
                scope="cdct",
                component="cdct-gate",
                code=f"cdct_{check}_not_passing",
                source="CycleRun.cdct_gate",
                evidence_uri=cycle.cdct_gate.gate_report_uri,
                details={"block_reason": cycle.cdct_gate.block_reason},
            )

    for resource in resources.resources:
        if resource.observation_source != "kubernetes_snapshot":
            continue
        if not resource_diagnostic_is_current(resource, generated_at):
            continue
        resource_status = resource.status
        if resource_status not in {"warn", "blocked", "fail"} and resource.pressure == "warn":
            resource_status = "warn"
        if resource_status in {"warn", "blocked", "fail"}:
            add(
                status=resource_status,
                scope="resource",
                component=resource.resource_id,
                code=resource.reason or resource.observation_message or f"resource_{resource_status}",
                source=f"RuntimeResource.{resource.observation_source}",
                observed_at=resource.observed_at or resource.last_transition_time,
                details={
                    "namespace": resource.namespace,
                    "kind": resource.kind,
                    "readiness": resource.readiness,
                    "pressure": resource.pressure,
                    "restarts": resource.restarts,
                },
            )

    items = deduplicate(items)
    sources = build_source_freshness(resources, generated_at)
    state_material = {
        "cycle_id": cycle.cycle_id,
        "sources": [
            {
                "source_id": source.source_id,
                "status": source.status,
            }
            for source in sources
        ],
        "diagnostics": [
            {
                key: value
                for key, value in item.model_dump(mode="json").items()
                if key != "observed_at"
            }
            for item in items
        ],
    }
    state_digest = hashlib.sha256(
        json.dumps(state_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    blocked_count = sum(item.status == "blocked" for item in items)
    warn_count = sum(item.status == "warn" for item in items)
    fail_count = sum(item.status == "fail" for item in items)
    status = "fail" if fail_count else "blocked" if blocked_count else "warn" if warn_count else "pass"
    root = diagnostic_root()
    report = ControlPanelDiagnostics(
        schema_version="evm.control_panel.diagnostics.v1",
        generated_at=generated_at,
        cycle_id=cycle.cycle_id,
        status=status,
        blocked_count=blocked_count,
        warn_count=warn_count,
        fail_count=fail_count,
        sources=sources,
        diagnostics=items,
        state_digest=state_digest,
        snapshot_uri=canonical_evidence_uri(root / "latest.json") if persist else None,
        audit_uri=canonical_evidence_uri(root / "diagnostic_events.jsonl") if persist else None,
    )
    if persist:
        try:
            persist_diagnostics(report)
        except OSError as exc:
            report = persistence_failure_report(report, exc)
    return report


def resource_diagnostic_is_current(resource: Any, generated_at: str) -> bool:
    if str(resource.kind).lower() not in {"job", "pod"}:
        return True
    if resource.status not in {"blocked", "fail"}:
        return True
    transition = resource.last_transition_time
    if not transition:
        return True
    try:
        observed = datetime.fromisoformat(str(transition).replace("Z", "+00:00"))
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        ttl = max(
            0,
            int(
                os.getenv(
                    "EVM_CONTROL_PANEL_TERMINAL_RESOURCE_DIAGNOSTIC_TTL_SECONDS",
                    str(DEFAULT_TERMINAL_RESOURCE_DIAGNOSTIC_TTL_SECONDS),
                )
            ),
        )
    except (TypeError, ValueError):
        return True
    return max(0.0, (generated - observed).total_seconds()) <= ttl


def persistence_failure_report(
    report: ControlPanelDiagnostics,
    error: OSError,
) -> ControlPanelDiagnostics:
    diagnostic = StatusDiagnostic(
        diagnostic_id="diag-diagnostics-persistence-failed",
        status="blocked",
        scope="sync",
        component="diagnostics-ledger",
        code="diagnostics_persistence_failed",
        summary="Runtime diagnostics were evaluated but their audit snapshot could not be persisted.",
        remediation="Restore write access to the configured diagnostics evidence root and refresh the Control Panel.",
        source="control-panel-diagnostics",
        evidence_uri=report.snapshot_uri,
        observed_at=report.generated_at,
        details={"error_type": type(error).__name__, "error": str(error)},
    )
    diagnostics = deduplicate([*report.diagnostics, diagnostic])
    digest = hashlib.sha256(
        json.dumps(
            {
                "previous_state_digest": report.state_digest,
                "diagnostics": [
                    {
                        key: value
                        for key, value in item.model_dump(mode="json").items()
                        if key != "observed_at"
                    }
                    for item in diagnostics
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return report.model_copy(
        update={
            "status": "blocked",
            "blocked_count": sum(item.status == "blocked" for item in diagnostics),
            "warn_count": sum(item.status == "warn" for item in diagnostics),
            "fail_count": sum(item.status == "fail" for item in diagnostics),
            "diagnostics": diagnostics,
            "state_digest": digest,
        }
    )


def build_source_freshness(
    resources: RuntimeResourceList,
    generated_at: str,
) -> list[SourceFreshness]:
    kubernetes_status = {
        "live": "live",
        "stale": "stale",
        "projected": "stale",
        "unavailable": "unavailable",
    }[resources.observation_status]
    return [
        SourceFreshness(
            source_id="cycle-aggregation",
            status="live",
            observed_at=generated_at,
            age_seconds=0,
            poll_interval_seconds=5,
            message="CycleRun rebuilt from current artifact and control-plane state",
        ),
        SourceFreshness(
            source_id="kubernetes-observer",
            status=kubernetes_status,  # type: ignore[arg-type]
            observed_at=resources.observed_at,
            age_seconds=resources.snapshot_age_seconds,
            poll_interval_seconds=5,
            message=resources.observation_message,
        ),
    ]


def persist_diagnostics(report: ControlPanelDiagnostics) -> None:
    with _DIAGNOSTIC_LOCK:
        root = diagnostic_root()
        root.mkdir(parents=True, exist_ok=True)
        latest = root / "latest.json"
        previous_digest = ""
        if latest.exists():
            try:
                previous_digest = str(json.loads(latest.read_text(encoding="utf-8")).get("state_digest") or "")
            except (OSError, json.JSONDecodeError):
                previous_digest = ""
        payload = report.model_dump(mode="json")
        atomic_write_json(latest, payload)
        if previous_digest == report.state_digest:
            return
        event = {
            "event_id": f"diagnostic-{report.state_digest[:16]}",
            "observed_at": report.generated_at,
            "cycle_id": report.cycle_id,
            "status": report.status,
            "state_digest": report.state_digest,
            "blocked_count": report.blocked_count,
            "warn_count": report.warn_count,
            "fail_count": report.fail_count,
            "diagnostics": [item.model_dump(mode="json") for item in report.diagnostics],
        }
        with (root / "diagnostic_events.jsonl").open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, sort_keys=True) + "\n")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def deduplicate(items: list[StatusDiagnostic]) -> list[StatusDiagnostic]:
    unique: dict[tuple[str, str, str], StatusDiagnostic] = {}
    for item in items:
        unique[(item.scope, item.component, item.code)] = item
    order = {"fail": 3, "blocked": 2, "warn": 1}
    return sorted(
        unique.values(),
        key=lambda item: (-order[item.status], item.scope, item.component, item.code),
    )


def normalize_code(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.<>:-]+", "_", value.strip()).strip("_")
    return normalized.lower() or "reason_not_recorded"


def diagnostic_summary(code: str, details: dict[str, Any]) -> str:
    threshold_match = re.fullmatch(r"([a-z0-9_]+)<([0-9.]+)", code)
    if threshold_match:
        return f"{threshold_match.group(1)} is below required threshold {threshold_match.group(2)}"
    if code.startswith("drift_") and code.endswith("_threshold_exceeded"):
        metric = code.removeprefix("drift_").removesuffix("_threshold_exceeded")
        return f"Measured drift rule {metric} exceeded its threshold"
    if code.startswith("cdct_"):
        return f"CD/CT check {code.removeprefix('cdct_').removesuffix('_not_passing')} is not passing"
    if code.startswith("drift_review_"):
        return f"Drift review is {code.removeprefix('drift_review_')}"
    if details.get("threshold") is not None:
        return f"Observed value {details.get('value')} does not meet threshold {details['threshold']}"
    return code.replace("_", " ")


def diagnostic_remediation(code: str) -> str:
    if re.fullmatch(r"[a-z0-9_]+<[0-9.]+", code):
        return "Review model evaluation evidence and train or select a candidate that passes the gate."
    if code.startswith("drift_review_"):
        return "Complete label review, record an independent approval, then close the review event."
    if code.startswith("drift_"):
        return "Inspect the measured windows and label queue; do not trigger automatic retraining."
    if code.startswith("cdct_"):
        return "Resolve the named gate and rerun CT evaluation before promotion."
    if "missing" in code:
        return "Restore the referenced evidence artifact and rerun the owning pipeline stage."
    if "stale" in code or "unavailable" in code:
        return "Refresh the source observer and verify its timestamp before acting on this state."
    return "Open the linked evidence and resolve the recorded component condition."

from __future__ import annotations

import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


VALID_LEVELS = {"info", "warn", "error"}


@dataclass(frozen=True)
class QualityIssue:
    level: str
    code: str
    message: str
    sample_id: str = ""
    check_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }
        if self.sample_id:
            payload["sample_id"] = self.sample_id
        if self.check_id:
            payload["check_id"] = self.check_id
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(frozen=True)
class QualityGateDecision:
    status: str
    decision: str
    blocking_count: int
    warning_count: int
    info_count: int
    fail_levels: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decision": self.decision,
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "fail_levels": list(self.fail_levels),
        }


@dataclass(frozen=True)
class QualityPolicy:
    policy_id: str = "default_quality_policy"
    version: str = "unversioned"
    dataset_types: tuple[str, ...] = ()
    fail_levels: tuple[str, ...] = ("error",)
    severity_overrides: dict[str, str] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)

    def severity_for(self, code: str, default_level: str) -> str:
        level = self.severity_overrides.get(code, default_level)
        if level not in VALID_LEVELS:
            raise ValueError(f"Invalid quality severity level for {code}: {level}")
        return level

    def issue(
        self,
        default_level: str,
        code: str,
        message: str,
        *,
        sample_id: str = "",
        check_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> QualityIssue:
        return QualityIssue(
            level=self.severity_for(code, default_level),
            code=code,
            message=message,
            sample_id=sample_id,
            check_id=check_id,
            metadata=metadata or {},
        )

    def evaluate(self, diagnostics: Iterable[dict[str, Any] | QualityIssue]) -> QualityGateDecision:
        levels: Counter[str] = Counter()
        for item in diagnostics:
            level = item.level if isinstance(item, QualityIssue) else str(item.get("level", "info"))
            levels[level] += 1
        blocking_count = sum(levels[level] for level in self.fail_levels)
        warning_count = levels["warn"]
        info_count = levels["info"]
        if blocking_count:
            decision = "fail"
        elif warning_count:
            decision = "pass_with_warnings"
        else:
            decision = "pass"
        return QualityGateDecision(
            status="fail" if blocking_count else "pass",
            decision=decision,
            blocking_count=blocking_count,
            warning_count=warning_count,
            info_count=info_count,
            fail_levels=self.fail_levels,
        )

    def to_report(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "dataset_types": list(self.dataset_types),
            "fail_levels": list(self.fail_levels),
            "severity_overrides": dict(sorted(self.severity_overrides.items())),
            "thresholds": self.thresholds,
        }


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return tuple(str(item) for item in value)


def _validate_severity_map(values: dict[str, str]) -> dict[str, str]:
    invalid = {key: level for key, level in values.items() if level not in VALID_LEVELS}
    if invalid:
        raise ValueError(f"Invalid quality severity levels: {invalid}")
    return values


def load_quality_policy(
    policy_path: Path | None,
    *,
    severity_defaults: dict[str, str] | None = None,
    fail_levels: tuple[str, ...] = ("error",),
) -> QualityPolicy:
    severity_overrides = dict(severity_defaults or {})
    if policy_path is None:
        return QualityPolicy(
            severity_overrides=_validate_severity_map(severity_overrides),
            fail_levels=fail_levels,
        )

    with policy_path.open("rb") as fp:
        payload = tomllib.load(fp)
    policy = payload.get("policy", {})
    severity_overrides.update({str(key): str(value) for key, value in payload.get("severity", {}).items()})
    configured_fail_levels = _as_str_tuple(policy.get("fail_levels")) or fail_levels
    return QualityPolicy(
        policy_id=str(policy.get("id", policy_path.stem)),
        version=str(policy.get("version", "unversioned")),
        dataset_types=_as_str_tuple(policy.get("dataset_types")),
        fail_levels=configured_fail_levels,
        severity_overrides=_validate_severity_map(severity_overrides),
        thresholds=payload.get("thresholds", {}),
    )

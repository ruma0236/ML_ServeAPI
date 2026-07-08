from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from evm.data_quality.policy import QualityIssue, QualityPolicy


@dataclass(frozen=True)
class QualityCheckContext:
    dataset_id: str
    dataset_version: str
    raw_root: Path
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityCheckResult:
    check_id: str
    issues: list[QualityIssue]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_report(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "issues": [item.to_dict() for item in self.issues],
            "metrics": self.metrics,
        }


class DataQualityCheck(Protocol):
    check_id: str

    def run(
        self,
        records: list[dict[str, Any]],
        context: QualityCheckContext,
        policy: QualityPolicy,
    ) -> QualityCheckResult:
        """Run a data quality check over a manifest-like record collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class TransformContext:
    dataset_id: str
    dataset_version: str
    input_root: Path
    output_root: Path
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransformResult:
    transform_id: str
    status: str
    records_in: int
    records_out: int
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_report(self) -> dict[str, Any]:
        return {
            "transform_id": self.transform_id,
            "status": self.status,
            "records_in": self.records_in,
            "records_out": self.records_out,
            "metrics": self.metrics,
        }


class ETLTransform(Protocol):
    transform_id: str

    def run(self, records: list[dict[str, Any]], context: TransformContext) -> TransformResult:
        """Run a deterministic ETL transform over a manifest-like record collection."""

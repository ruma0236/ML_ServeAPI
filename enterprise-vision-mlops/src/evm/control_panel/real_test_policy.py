from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.schemas import CycleRun


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


def default_report_path() -> Path:
    return Path("artifacts/w7/real_test_policy/real_test_policy_report.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate W7 no-mock/no-smoke real-test policy.")
    parser.add_argument("--cycle-json", type=Path, help="Optional captured CycleRun payload. Defaults to live aggregation.")
    parser.add_argument("--issue-register", type=Path, default=Path("docs/issues/issue-register.md"))
    parser.add_argument("--sprint", default="2026-07-W7")
    parser.add_argument("--report", type=Path, default=default_report_path())
    args = parser.parse_args()

    if args.cycle_json:
        cycle = CycleRun.model_validate(read_json(args.cycle_json))
    else:
        cycle = build_latest_cycle()
    records = parse_issue_register(args.issue_register, sprint=args.sprint)
    report = validate_real_test_policy(cycle, records)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from typing import Any

from evm.core.image_quality import summarize_counts
from evm.core.pipeline import (
    build_context,
    display_path,
    read_jsonl,
    utc_now,
    write_json,
    write_jsonl,
    write_markdown_report,
)


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("sample_id") or record.get("id") or "")


def _is_unknown_label(label: str) -> bool:
    return label.strip().lower() in {"", "unknown", "unlabeled", "none", "null"}


def _diagnostics(record: dict[str, Any]) -> list[dict[str, Any]]:
    quality = record.get("image_quality", {})
    if not isinstance(quality, dict):
        return []
    diagnostics = quality.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        return []
    return [item for item in diagnostics if isinstance(item, dict)]


def _diagnostic_levels(record: dict[str, Any]) -> Counter[str]:
    return Counter(str(item.get("level", "unknown")).lower() for item in _diagnostics(record))


def _review_reasons(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    label = str(record.get("label", ""))
    levels = _diagnostic_levels(record)
    if _is_unknown_label(label):
        reasons.append("missing_or_unknown_label")
    if levels.get("error", 0) > 0:
        reasons.append("quality_error")
    if levels.get("warn", 0) > 0:
        reasons.append("quality_warning")
    if str(record.get("license_id", "")).lower() in {"", "manual-review-required"}:
        reasons.append("license_review_required")
    return reasons


def _stable_rank(record: dict[str, Any], seed: int) -> str:
    return hashlib.sha256(f"{seed}:{_record_key(record)}".encode("utf-8")).hexdigest()


def _review_sample_ids(records: list[dict[str, Any]], *, max_samples: int, seed: int) -> set[str]:
    if max_samples <= 0:
        return set()
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (
            str(record.get("split", "unassigned")),
            str(record.get("label", "unknown")),
            str(record.get("class_name", "unknown")),
        )
        buckets.setdefault(key, []).append(record)

    selected: list[dict[str, Any]] = []
    for bucket in buckets.values():
        selected.extend(sorted(bucket, key=lambda item: _stable_rank(item, seed))[:1])

    if len(selected) < max_samples:
        selected_keys = {_record_key(item) for item in selected}
        remaining = [item for item in records if _record_key(item) not in selected_keys]
        selected.extend(sorted(remaining, key=lambda item: _stable_rank(item, seed))[: max_samples - len(selected)])
    return {_record_key(item) for item in selected[:max_samples]}


def _curation_state(
    record: dict[str, Any],
    *,
    review_sample_ids: set[str],
    eval_splits: set[str],
) -> dict[str, Any]:
    sample_id = _record_key(record)
    reasons = _review_reasons(record)
    levels = _diagnostic_levels(record)
    label = str(record.get("label", ""))
    split = str(record.get("split", "unassigned"))
    has_error = levels.get("error", 0) > 0
    missing_label = _is_unknown_label(label)
    sample_review = sample_id in review_sample_ids

    if has_error or missing_label:
        label_state = "needs_human_review"
        review_state = "hitl_required"
    elif sample_review or reasons:
        label_state = "sample_review"
        review_state = "review_requested"
    else:
        label_state = "auto_accepted"
        review_state = "not_required"

    eval_candidate = split in eval_splits and not has_error and not missing_label
    if eval_candidate and review_state in {"not_required", "review_requested"}:
        eval_state = "candidate"
    elif has_error or missing_label:
        eval_state = "blocked"
    else:
        eval_state = "not_candidate"

    return {
        "label_state": label_state,
        "review_state": review_state,
        "eval_promotion_state": eval_state,
        "review_reasons": reasons,
        "review_sample": sample_review,
        "curated_at": utc_now(),
    }


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("curation_workflow", config_path)
    cfg = ctx.pipeline_config()
    input_manifest = ctx.path(str(cfg.get("input_manifest", "data/validated/mvi_quality_manifest.jsonl")))
    output_dir = ctx.path(str(cfg.get("output_dir", "data/validated/curation")))
    state_path = ctx.path(str(cfg.get("state_path", output_dir / "curation_state.json")))
    curation_manifest = ctx.path(str(cfg.get("curation_manifest", output_dir / "curation_manifest.jsonl")))
    hitl_queue = ctx.path(str(cfg.get("hitl_queue", output_dir / "hitl_queue.jsonl")))
    sample_review_manifest = ctx.path(str(cfg.get("sample_review_manifest", output_dir / "sample_review.jsonl")))
    curated_eval_manifest = ctx.path(
        str(cfg.get("curated_eval_manifest", output_dir / "curated_eval_manifest.jsonl"))
    )
    sample_seed = int(cfg.get("sample_seed", 20260709))
    max_review_samples = int(cfg.get("max_review_samples", 64))
    max_eval_records = int(cfg.get("max_eval_records", 0) or 0)
    eval_splits = {str(item) for item in cfg.get("eval_splits", ["validation", "test"])}

    records = read_jsonl(input_manifest)
    review_sample_ids = _review_sample_ids(records, max_samples=max_review_samples, seed=sample_seed)
    curated_records: list[dict[str, Any]] = []
    hitl_records: list[dict[str, Any]] = []
    sample_records: list[dict[str, Any]] = []
    eval_records: list[dict[str, Any]] = []

    for record in records:
        item = dict(record)
        curation = _curation_state(item, review_sample_ids=review_sample_ids, eval_splits=eval_splits)
        item["curation"] = curation
        curated_records.append(item)
        if curation["review_state"] in {"hitl_required", "review_requested"}:
            hitl_records.append(item)
        if curation["review_sample"]:
            sample_records.append(item)
        if curation["eval_promotion_state"] == "candidate":
            eval_records.append(item)

    eval_records = sorted(eval_records, key=lambda item: _stable_rank(item, sample_seed))
    if max_eval_records > 0:
        eval_records = eval_records[:max_eval_records]

    state = {
        "schema_version": "evm.curation_workflow.v1",
        "created_at": utc_now(),
        "input_manifest": display_path(input_manifest, ctx.project_root),
        "record_count": len(records),
        "hitl_queue_count": len(hitl_records),
        "sample_review_count": len(sample_records),
        "curated_eval_count": len(eval_records),
        "label_counts": summarize_counts(curated_records, "label"),
        "split_counts": summarize_counts(curated_records, "split"),
        "label_state_counts": summarize_counts(
            [{"label_state": item["curation"]["label_state"]} for item in curated_records],
            "label_state",
        ),
        "review_state_counts": summarize_counts(
            [{"review_state": item["curation"]["review_state"]} for item in curated_records],
            "review_state",
        ),
        "eval_promotion_state_counts": summarize_counts(
            [{"eval_promotion_state": item["curation"]["eval_promotion_state"]} for item in curated_records],
            "eval_promotion_state",
        ),
        "review_reason_counts": dict(
            Counter(reason for item in curated_records for reason in item["curation"]["review_reasons"])
        ),
        "outputs": {
            "curation_manifest": display_path(curation_manifest, ctx.project_root),
            "hitl_queue": display_path(hitl_queue, ctx.project_root),
            "sample_review_manifest": display_path(sample_review_manifest, ctx.project_root),
            "curated_eval_manifest": display_path(curated_eval_manifest, ctx.project_root),
        },
        "trace": ctx.trace.to_dict(),
    }

    write_jsonl(curation_manifest, curated_records)
    write_jsonl(hitl_queue, hitl_records)
    write_jsonl(sample_review_manifest, sample_records)
    write_jsonl(curated_eval_manifest, eval_records)
    write_json(state_path, state)
    write_json(ctx.run_dir / "summary.json", state)
    write_markdown_report(
        ctx,
        "Curation Workflow Pipeline",
        {
            "record_count": len(records),
            "hitl_queue_count": len(hitl_records),
            "sample_review_count": len(sample_records),
            "curated_eval_count": len(eval_records),
            "state_path": display_path(state_path, ctx.project_root),
        },
        [
            "",
            "## Contract",
            "",
            "- Input: image-quality manifest.",
            "- Output: curation manifest, HITL queue, sample review manifest, curated eval manifest, and state summary.",
            "- The Control Panel can use `label_state`, `review_state`, and `eval_promotion_state` as curation workflow columns.",
        ],
    )
    return state


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

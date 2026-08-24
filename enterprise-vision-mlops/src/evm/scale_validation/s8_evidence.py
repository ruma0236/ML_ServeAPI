from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evm.control_panel.transactional_store import canonical_digest
from evm.scale_validation.s1_runtime import sha256_file
from evm.scale_validation.s3_runtime import S3LoadPoint, public_point_projection
from evm.scale_validation.s8_runtime import (
    FAULT_PROFILE_IDS,
    SCHEMA_VERSION,
    S8RuntimeConfig,
    analyze_fault_results,
    analyze_soak_private,
    analyze_soak_results,
    git_blob_identity,
)


REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class S8EvidenceValidationError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_s8_experiment(
    payload: Mapping[str, Any],
    *,
    config: S8RuntimeConfig,
    private_root: Path,
    project_root: Path,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    _validate_finite(payload, "evidence", errors)
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version")
    source = dict(payload.get("source_identity", {}))
    revision = str(source.get("implementation_revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("source_revision")
    elif not _is_ancestor(project_root, revision, validation_revision):
        errors.append("source_revision_ancestry")
    expected_blobs = {
        "runtime": Path("src/evm/scale_validation/s8_runtime.py"),
        "runner": Path("scripts/dev/run_s8_dependency_soak_experiment.py"),
        "s2_runtime": Path("src/evm/scale_validation/s2_runtime.py"),
        "s3_runtime": Path("src/evm/scale_validation/s3_runtime.py"),
        "admission": Path("src/evm/control_panel/admission_queue.py"),
        "worker": Path("src/evm/control_panel/task_queue_worker.py"),
        "store": Path("src/evm/control_panel/transactional_store.py"),
        "scenario_config": Path("configs/s8_dependency_soak_v5.toml"),
        "soak_config": Path("configs/s8_soak_capacity_runtime.toml"),
    }
    recorded_blobs = dict(source.get("git_blobs", {}))
    if REVISION_PATTERN.fullmatch(revision):
        for label, relative_path in expected_blobs.items():
            try:
                expected = git_blob_identity(project_root, revision, relative_path)
            except (OSError, subprocess.SubprocessError):
                errors.append(f"source_blob_unresolvable:{label}")
                continue
            if canonical(recorded_blobs.get(label)) != canonical(expected):
                errors.append(f"source_blob_identity:{label}")
    if source.get("runtime_module_sha256") != dict(
        recorded_blobs.get("runtime", {})
    ).get("sha256"):
        errors.append("runtime_module_sha256")
    public_config = dict(payload.get("config", {}))
    if public_config.get("scenario_sha256") != config.sha256:
        errors.append("scenario_config_sha256")
    if public_config.get("seed") != config.seed:
        errors.append("seed")
    if public_config.get("repetitions") != config.repetitions:
        errors.append("repetitions")

    fault_results = _mappings(payload.get("fault_results"))
    fault_private_errors, private_faults = validate_fault_private_evidence(
        fault_results, private_root=private_root
    )
    errors.extend(fault_private_errors)
    recomputed_fault = analyze_fault_results(fault_results, config)
    if canonical(payload.get("fault_analysis")) != canonical(recomputed_fault):
        errors.append("fault_analysis_projection")

    soak_results = _mappings(payload.get("soak_results"))
    soak_errors, soak_analyses = validate_soak_private_evidence(
        soak_results, private_root=private_root, config=config
    )
    errors.extend(soak_errors)
    recomputed_soak = analyze_soak_results(soak_results, soak_analyses, config)
    if canonical(payload.get("soak_analysis")) != canonical(recomputed_soak):
        errors.append("soak_analysis_projection")

    index_projection, index_errors = validate_private_index(
        private_root,
        public_summary=dict(payload.get("private_evidence", {})),
    )
    errors.extend(index_errors)
    expected_acceptance = {
        "S8-AC-01": bool(recomputed_fault.get("passed")),
        "S8-AC-02": bool(recomputed_soak.get("passed")),
        "S8-AC-03": bool(recomputed_fault.get("passed"))
        and bool(recomputed_soak.get("passed"))
        and bool(dict(payload.get("resource_efficiency", {})).get("gpu_reference")),
        "S8-AC-04": False,
    }
    if canonical(payload.get("acceptance")) != canonical(expected_acceptance):
        errors.append("acceptance_projection")
    if payload.get("runtime_verdict") != (
        "exercised_pending_hash_closure"
        if all(expected_acceptance[key] for key in ("S8-AC-01", "S8-AC-02", "S8-AC-03"))
        else "not_passed"
    ):
        errors.append("runtime_verdict_projection")
    if not all(expected_acceptance[key] for key in ("S8-AC-01", "S8-AC-02", "S8-AC-03")):
        errors.append("runtime_acceptance_not_passed")
    if errors:
        raise S8EvidenceValidationError(
            "s8_experiment_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "valid": True,
        "source_revision": revision,
        "fault_result_count": len(fault_results),
        "soak_result_count": len(soak_results),
        "private_fault_count": len(private_faults),
        "private_index": index_projection,
        "recomputed_acceptance": expected_acceptance,
    }


def validate_fault_private_evidence(
    results: Sequence[Mapping[str, Any]], *, private_root: Path
) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    observed: list[dict[str, Any]] = []
    expected_pairs = {
        (profile, repetition)
        for profile in FAULT_PROFILE_IDS
        for repetition in range(1, 4)
    }
    actual_pairs = {
        (str(item.get("profile_id")), int(item.get("repetition", 0)))
        for item in results
    }
    if actual_pairs != expected_pairs or len(results) != len(expected_pairs):
        errors.append("fault_matrix_identity")
    for public in results:
        profile = str(public.get("profile_id"))
        repetition = int(public.get("repetition", 0))
        path = private_root / "faults" / profile / f"repetition-{repetition}" / "profile-result-private.json"
        if not path.is_file():
            errors.append(f"fault_private_missing:{profile}:{repetition}")
            continue
        if sha256_file(path) != public.get("private_evidence_sha256"):
            errors.append(f"fault_private_sha256:{profile}:{repetition}")
        private = _read_mapping(path)
        observed.append(private)
        _validate_finite(private, f"fault:{profile}:{repetition}", errors)
        terminal = dict(private.get("terminal", {}))
        fixture = dict(private.get("fixture", {}))
        expected_terminal = {
            "accepted_count": terminal.get("accepted_count"),
            "terminal_count": terminal.get("terminal_seen_count"),
            "missing_count": terminal.get("missing_terminal_count"),
            "elapsed_seconds": terminal.get("elapsed_seconds"),
            "final_state_counts": dict(terminal.get("final", {})).get("state_counts", {}),
        }
        if canonical(public.get("terminal")) != canonical(expected_terminal):
            errors.append(f"fault_terminal_projection:{profile}:{repetition}")
        expected_effects = {
            "attempts": fixture.get("attempts"),
            "unique": fixture.get("unique_external_effects"),
            "duplicates": fixture.get("duplicate_external_effects"),
            "multiple_logical": fixture.get("tasks_with_multiple_logical_effects"),
            "max_runtime_concurrency": fixture.get("max_runtime_concurrency", {}),
            "max_external_in_flight": fixture.get("max_external_in_flight", {}),
            "cuda_probe_count": fixture.get("cuda_probe_count", 0),
            "cuda_failure_count": fixture.get("cuda_failure_count", 0),
            "cuda_nonzero_activity_count": fixture.get("cuda_nonzero_activity_count", 0),
            "cuda_peak_allocated_bytes": fixture.get("cuda_peak_allocated_bytes", 0),
        }
        if canonical(public.get("external_effects")) != canonical(expected_effects):
            errors.append(f"fault_effect_projection:{profile}:{repetition}")
        if canonical(public.get("metrics")) != canonical(dict(private.get("metrics", {})).get("summary", {})):
            errors.append(f"fault_metric_projection:{profile}:{repetition}")
        if not bool(private.get("passed")) or not bool(public.get("passed")):
            errors.append(f"fault_not_passed:{profile}:{repetition}")
        if not all(bool(item.get("passed")) for item in public.get("assertions", [])):
            errors.append(f"fault_assertion:{profile}:{repetition}")
    return errors, observed


def validate_soak_private_evidence(
    results: Sequence[Mapping[str, Any]],
    *,
    private_root: Path,
    config: S8RuntimeConfig,
) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    analyses: list[dict[str, Any]] = []
    if len(results) != config.repetitions:
        errors.append("soak_repetition_count")
    for public in results:
        point = S3LoadPoint(**dict(public.get("point", {})))
        repetition = int(public.get("repetition", 0))
        path = private_root / "soak" / point.point_id / f"repetition-{repetition}" / "point-evidence-private.json"
        if not path.is_file():
            errors.append(f"soak_private_missing:{repetition}")
            continue
        if sha256_file(path) != public.get("private_evidence_sha256"):
            errors.append(f"soak_private_sha256:{repetition}")
        private = _read_mapping(path)
        _validate_finite(private, f"soak:{repetition}", errors)
        expected_public = public_point_projection(private)
        expected_public["private_evidence_sha256"] = sha256_file(path)
        expected_public["private_evidence_bytes"] = path.stat().st_size
        if canonical(public) != canonical(expected_public):
            errors.append(f"soak_public_projection:{repetition}")
        analyses.append(analyze_soak_private(path, config))
    return errors, analyses


def validate_private_index(
    root: Path, *, public_summary: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    path = root / "private-evidence-index.json"
    if not path.is_file():
        return {}, ["private_index_missing"]
    payload = _read_mapping(path)
    entries = _mappings(payload.get("artifacts"))
    for entry in entries:
        target = root / str(entry.get("path"))
        if not target.is_file():
            errors.append(f"private_artifact_missing:{entry.get('path')}")
            continue
        if target.stat().st_size != int(entry.get("bytes", -1)):
            errors.append(f"private_artifact_bytes:{entry.get('path')}")
        if sha256_file(target) != entry.get("sha256"):
            errors.append(f"private_artifact_sha256:{entry.get('path')}")
    projected = {
        "artifact_count": len(entries),
        "aggregate_sha256": canonical_digest(entries),
        "index_sha256": sha256_file(path),
    }
    if int(payload.get("artifact_count", -1)) != len(entries):
        errors.append("private_index_count")
    if payload.get("aggregate_sha256") != projected["aggregate_sha256"]:
        errors.append("private_index_aggregate")
    if public_summary.get("artifact_count") != projected["artifact_count"]:
        errors.append("public_private_count")
    if public_summary.get("aggregate_sha256") != projected["aggregate_sha256"]:
        errors.append("public_private_aggregate")
    return projected, errors


def _validate_finite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"non_finite:{path}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite(item, f"{path}.{key}", errors)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_finite(item, f"{path}[{index}]", errors)


def _mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _read_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise S8EvidenceValidationError(f"s8_mapping_required:{path.name}")
    return payload


def _is_ancestor(root: Path, revision: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, descendant],
        cwd=root,
        capture_output=True,
        timeout=15,
        check=False,
    )
    return result.returncode == 0

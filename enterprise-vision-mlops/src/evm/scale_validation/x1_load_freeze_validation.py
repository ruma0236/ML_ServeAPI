from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from evm.scale_validation.x1_artifacts import validate_x1_artifacts
from evm.scale_validation.x1_calibration import project_calibration_attempt
from evm.scale_validation.x1_contract import (
    API_REPLICAS,
    CPU_WORKERS,
    MODEL_IDS,
    REPETITIONS,
    X1Contract,
    canonical_sha256,
    compute_load_freeze,
    sha256_file,
)
from evm.scale_validation.x1_runtime import select_batching_profiles, validate_q0_bundle


REQUIRED_SOURCE_PATHS = (
    "apps/api/control_panel_workloads.py",
    "apps/api/main.py",
    "apps/api/requirements.txt",
    "configs/s8_v4_x1_heterogeneous_v1.toml",
    "monitoring/prometheus/prometheus.yml",
    "scripts/dev/prepare_s8_v4_x1_artifacts.py",
    "scripts/dev/run_s8_v4_x1_calibration.py",
    "scripts/dev/validate_s8_v4_x1_contract.py",
    "scripts/dev/validate_s8_v4_x1_load_freeze.py",
    "src/evm/control_panel/scenario_workloads.py",
    "src/evm/model_runtime/x1_serving.py",
    "src/evm/scale_validation/x1_artifacts.py",
    "src/evm/scale_validation/x1_calibration.py",
    "src/evm/scale_validation/x1_contract.py",
    "src/evm/scale_validation/x1_contract_validation.py",
    "src/evm/scale_validation/x1_load_freeze_validation.py",
    "src/evm/scale_validation/x1_runtime.py",
    "src/evm/scale_validation/x1_topology.py",
)


class X1LoadFreezeValidationError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def load_canonical_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise X1LoadFreezeValidationError(f"x1_load_freeze_canonical_lf:{path.name}")

    def unique_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise X1LoadFreezeValidationError(f"x1_load_freeze_duplicate_key:{path.name}:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise X1LoadFreezeValidationError(f"x1_load_freeze_json:{path.name}") from exc
    if not isinstance(value, dict) or raw != canonical_bytes(value):
        raise X1LoadFreezeValidationError(f"x1_load_freeze_canonical_bytes:{path.name}")
    return value


def source_blob_inventory(
    source_root: Path, revision: str, *, require_worktree_match: bool
) -> dict[str, Any]:
    root = source_root.resolve()
    commit = _git(root, "rev-parse", f"{revision}^{{commit}}")
    tree = _git(root, "rev-parse", f"{revision}^{{tree}}")
    entries: list[dict[str, Any]] = []
    for relative in REQUIRED_SOURCE_PATHS:
        oid = _git(root, "rev-parse", f"{revision}:{relative}")
        payload = subprocess.run(
            ["git", "show", f"{revision}:{relative}"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        worktree_path = root / relative
        if require_worktree_match and worktree_path.read_bytes() != payload:
            raise X1LoadFreezeValidationError(f"x1_source_worktree_drift:{relative}")
        entries.append(
            {
                "path": relative,
                "blob_oid": oid,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return {
        "revision": commit,
        "tree": tree,
        "entries": entries,
        "aggregate_sha256": canonical_sha256(entries),
    }


def validate_x1_load_freeze(
    *,
    public_path: Path,
    private_root: Path,
    contract: X1Contract,
    source_root: Path,
) -> dict[str, Any]:
    public = load_canonical_json(public_path)
    if (
        public.get("schema_version") != "evm.s8_v4.x1_load_freeze_evidence.v1"
        or public.get("status") != "load_freeze_ready"
        or public.get("credit") != "non_credit"
        or public.get("acceptance_credit") is not False
    ):
        raise X1LoadFreezeValidationError("x1_load_freeze_public_status")
    root = private_root.resolve()
    if public.get("suite_id") != root.name:
        raise X1LoadFreezeValidationError("x1_load_freeze_suite_identity")
    source = public.get("source")
    if not isinstance(source, Mapping):
        raise X1LoadFreezeValidationError("x1_load_freeze_source")
    revision = str(source.get("revision", ""))
    source_inventory = source_blob_inventory(
        source_root,
        revision,
        require_worktree_match=True,
    )
    if source.get("tree_sha") != source_inventory["tree"]:
        raise X1LoadFreezeValidationError("x1_load_freeze_source_tree")
    if public.get("source_blobs") != source_inventory:
        raise X1LoadFreezeValidationError("x1_load_freeze_source_blobs")
    if public.get("contract") != {
        "path": contract.path.relative_to(source_root.resolve()).as_posix(),
        "sha256": contract.sha256,
    }:
        raise X1LoadFreezeValidationError("x1_load_freeze_contract")

    index_path = root / "private-evidence-index.json"
    index = load_canonical_json(index_path)
    entries = _private_entries(root)
    expected_index = {
        "schema_version": "evm.s8_v4.x1_private_index.v1",
        "artifact_count": len(entries),
        "total_bytes": sum(entry["bytes"] for entry in entries),
        "aggregate_sha256": canonical_sha256(entries),
        "entries": entries,
    }
    if index != expected_index:
        raise X1LoadFreezeValidationError("x1_load_freeze_private_index")
    expected_private = {
        "artifact_count": index["artifact_count"],
        "total_bytes": index["total_bytes"],
        "aggregate_sha256": index["aggregate_sha256"],
        "index_sha256": sha256_file(index_path),
    }
    if public.get("private_evidence") != expected_private:
        raise X1LoadFreezeValidationError("x1_load_freeze_private_binding")

    artifact_manifest_path = root / "artifacts/x1-artifact-manifest.json"
    release_path = root / "training-lease-release.json"
    release = load_canonical_json(release_path)
    if release.get("state") != "released" or release.get("scenario_id") != "X1":
        raise X1LoadFreezeValidationError("x1_load_freeze_training_lease")
    artifact_manifest = validate_x1_artifacts(
        contract,
        manifest_path=artifact_manifest_path,
        source_revision=revision,
        source_tree=source_inventory["tree"],
        lease_run_id=str(release["run_id"]),
        lease_id=str(release["lease_id"]),
        fencing_token=str(release["fencing_token"]),
    )
    if public.get("artifact_manifest") != {
        "path": str(artifact_manifest_path),
        "sha256": sha256_file(artifact_manifest_path),
    }:
        raise X1LoadFreezeValidationError("x1_load_freeze_artifact_binding")
    q0_path = root / "q0.json"
    q0 = load_canonical_json(q0_path)
    q0_projection = validate_q0_bundle(q0, contract, artifact_manifest)
    if q0.get("projection") != q0_projection or public.get("q0") != {
        "models_passed": 4,
        "requests_passed": 256,
        "sha256": sha256_file(q0_path),
    }:
        raise X1LoadFreezeValidationError("x1_load_freeze_q0")

    solo, topology, batching = _validate_calibration_files(root, contract)
    selected_batching = select_batching_profiles(batching)
    load_freeze = compute_load_freeze(
        contract,
        model_calibrations=solo,
        topology_calibrations=topology,
    )
    load_freeze["selected_batching"] = selected_batching
    load_freeze["profiler_qualification"] = {
        "mode": "concurrent_balanced",
        "topology": "r1-w4",
        "repetitions": 3,
        "verdict": "kernel_overlap_not_evidenced",
        "reason": (
            "No model/request-attributed Nsight or CUPTI kernel intervals were accepted "
            "during calibration."
        ),
    }
    if load_canonical_json(root / "load-freeze.json") != load_freeze:
        raise X1LoadFreezeValidationError("x1_load_freeze_projection")
    calibration = public.get("calibration")
    if not isinstance(calibration, Mapping) or calibration != {
        "solo_repetitions": 12,
        "topology_repetitions": 18,
        "batching_repetitions": 24,
        "selected_batching": selected_batching,
        "load_freeze": load_freeze,
    }:
        raise X1LoadFreezeValidationError("x1_load_freeze_public_calibration")
    cleanup = load_canonical_json(root / "final-cleanup.json")
    if public.get("cleanup") != cleanup or not _cleanup_passed(cleanup):
        raise X1LoadFreezeValidationError("x1_load_freeze_cleanup")
    boundary = public.get("execution_boundary")
    if boundary != {
        "credit_matrix_started": False,
        "integrated_v4_started": False,
        "next_gate": "independent_review_of_contract_and_load_freeze",
    }:
        raise X1LoadFreezeValidationError("x1_load_freeze_execution_boundary")
    if public.get("claim_boundary") != contract.payload["claim"]["boundary"]:
        raise X1LoadFreezeValidationError("x1_load_freeze_claim_boundary")
    return {
        "valid": True,
        "suite_id": root.name,
        "source_revision": revision,
        "q0_models": 4,
        "solo_repetitions": len(solo),
        "topology_repetitions": len(topology),
        "batching_repetitions": len(batching),
        "private_artifacts": index["artifact_count"],
        "private_aggregate_sha256": index["aggregate_sha256"],
        "public_sha256": sha256_file(public_path),
    }


def _validate_calibration_files(
    root: Path, contract: X1Contract
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    expected: dict[str, set[str]] = {
        "solo_calibration": {f"x1-{cell.cell_id}" for cell in contract.solo_calibration_cells()},
        "topology_calibration": {
            f"x1-topology_calibration-r{replicas}-w{workers}-rep{repetition}"
            for replicas in API_REPLICAS
            for workers in CPU_WORKERS
            for repetition in REPETITIONS
        },
        "batching_calibration": {
            f"x1-batching_calibration-r1-w1-{model_id}-{candidate}-rep{repetition}"
            for model_id in MODEL_IDS
            for candidate in ("enabled-4-8-2ms", "enabled-8-16-10ms")
            for repetition in REPETITIONS
        },
    }
    projected: dict[str, list[dict[str, Any]]] = {mode: [] for mode in expected}
    for mode, expected_ids in expected.items():
        raw_paths = sorted((root / "calibration" / mode).glob("*.json"))
        projection_paths = sorted((root / "projections" / mode).glob("*.json"))
        if len(raw_paths) != len(expected_ids) or len(projection_paths) != len(expected_ids):
            raise X1LoadFreezeValidationError(f"x1_load_freeze_attempt_count:{mode}")
        by_id: dict[str, dict[str, Any]] = {}
        for raw_path in raw_paths:
            raw = load_canonical_json(raw_path)
            if raw.get("schema_version") != "evm.s8_v4.x1_calibration_attempt.v1":
                raise X1LoadFreezeValidationError(f"x1_load_freeze_attempt_schema:{mode}")
            projection = project_calibration_attempt(raw, contract)
            projection_path = root / "projections" / mode / raw_path.name
            stored = load_canonical_json(projection_path)
            if mode == "batching_calibration":
                attempt_id = str(raw["attempt_id"])
                candidates = [
                    candidate
                    for candidate in ("enabled-4-8-2ms", "enabled-8-16-10ms")
                    if candidate in attempt_id
                ]
                if len(candidates) != 1:
                    raise X1LoadFreezeValidationError("x1_load_freeze_batch_candidate")
                projection["batch_candidate"] = candidates[0]
                projection["guardrails_passed"] = _guardrails_pass(projection, contract)
            if stored != projection:
                raise X1LoadFreezeValidationError(
                    f"x1_load_freeze_attempt_projection:{raw_path.name}"
                )
            attempt_id = str(raw["attempt_id"])
            if attempt_id in by_id:
                raise X1LoadFreezeValidationError(f"x1_load_freeze_attempt_duplicate:{mode}")
            by_id[attempt_id] = projection
        if set(by_id) != expected_ids:
            raise X1LoadFreezeValidationError(f"x1_load_freeze_attempt_set:{mode}")
        projected[mode] = [by_id[attempt_id] for attempt_id in sorted(by_id)]
    return (
        projected["solo_calibration"],
        projected["topology_calibration"],
        projected["batching_calibration"],
    )


def _guardrails_pass(projection: Mapping[str, Any], contract: X1Contract) -> bool:
    return all(
        step["error_rate"] <= float(contract.payload["guardrails"]["maximum_error_rate"])
        and step["p99_ms"] <= float(contract.payload["guardrails"]["maximum_p99_ms"])
        and step["queue_wait_p99_ms"]
        <= float(contract.payload["guardrails"]["maximum_queue_wait_ms"])
        and step["lost"] == 0
        and step["duplicate_effects"] == 0
        and step["outcome_unknown"] == 0
        and step["silent_fallback"] == 0
        and step["unexpected_oom"] == 0
        for step in projection["steps"]
        if step["offered_rps"] <= projection["selected_offered_rps"]
    )


def _cleanup_passed(cleanup: Mapping[str, Any]) -> bool:
    queues = cleanup.get("queues")
    return bool(
        cleanup.get("b0_uid_exact") is True
        and cleanup.get("b0_image_exact") is True
        and cleanup.get("b0_ready_1_of_1") is True
        and cleanup.get("b0_actual_cuda") is True
        and cleanup.get("prometheus_5_of_5") is True
        and isinstance(queues, Mapping)
        and queues == {"active": 0, "leased": 0, "outcome_unknown": 0}
        and cleanup.get("gpu_lease_absent") is True
        and cleanup.get("runtime_absent") is True
        and cleanup.get("database_schema_absent") is True
        and cleanup.get("vram_restored") is True
    )


def _private_entries(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "private-evidence-index.json"
    ]


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise X1LoadFreezeValidationError(
            f"x1_load_freeze_git:{' '.join(arguments)}:{result.stderr[-500:]}"
        )
    return result.stdout.strip()

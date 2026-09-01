from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

import httpx
import psutil
import requests
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.control_panel.scenario_workloads import (  # noqa: E402
    GpuLease,
    acquire_scale_validation_gpu_lease,
    assert_scale_validation_gpu_lease_owner,
    read_active_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.scale_validation.evidence import canonical_public_json_bytes  # noqa: E402
from evm.scale_validation.s7_evidence import (  # noqa: E402
    SMOKE_CLAIM_BOUNDARY,
    project_profile,
    source_git_identity,
)
from evm.scale_validation.s7_manifest_contract import (  # noqa: E402
    build_trusted_manifest_envelope,
    create_run_scoped_manifest_snapshots,
    manifest_snapshot_binding_sha256,
    publish_exclusive_atomic_bytes,
)
from evm.scale_validation.s7_runtime import (  # noqa: E402
    S7RuntimeConfig,
    S7RuntimeError,
    canonical_sha256,
    file_sha256,
    host_image_data_environment,
    profile_family,
    source_identity,
)


PROMETHEUS_URL = "http://127.0.0.1:9090"
TARGET_JOB = "evm-s7-family"
EXPECTED_BASELINE_TARGET_COUNT = 5
S7_V3_KERNEL_CONTAINMENT_IMPLEMENTED = False
SCENARIO_CONTRACT_PATHS = {
    "image": "configs/scenarios/manufacturing-visual-inspection.json",
    "vlm": "configs/scenarios/scienceqa-vlm-evaluation.json",
    "llm": "configs/scenarios/dolly-instruction-tuning.json",
}


@dataclass(frozen=True)
class HolderSnapshot:
    namespace: str
    name: str
    uid: str
    replicas: int
    selector: str
    pod_uid: str
    pod_name: str
    image: str
    model_sha256: str
    candidate_id: str


@dataclass(frozen=True)
class AssetSpec:
    family: str
    port: int
    manifest: Path
    manifest_sha256: str
    model_artifact: Path | None = None
    model_artifact_sha256: str | None = None
    candidate_id: str | None = None
    dataset_version: str | None = None
    base_model: Path | None = None
    adapter: Path | None = None
    adapter_sha256: str | None = None
    model_repository: str | None = None
    model_revision: str | None = None
    data_identity_sha256: str | None = None
    model_source_commit: str | None = None
    quantization: str = "none"


@dataclass
class ServiceProcess:
    family: str
    process: subprocess.Popen[str]
    log_handle: Any
    log_path: Path
    base_url: str
    run_uuid: str
    root_created_at: float


class S7ManualInterventionRequired(S7RuntimeError):
    def __init__(self, message: str, *, process_evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.process_evidence = process_evidence


@dataclass(frozen=True)
class RequestInput:
    request_id: str
    request_class: str
    payload: dict[str, Any]
    expected: str | int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run S7 family admission through existing CUDA serving routes."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument("--config", type=Path, default=ROOT / "configs/s7_family_admission.toml")
    parser.add_argument("--cuda-python", type=Path, default=Path("F:/evm_w7_torch/python.exe"))
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/private/s7"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New absent append-only public evidence path; existing paths are rejected.",
    )
    parser.add_argument("--maintenance-approved", action="store_true")
    parser.add_argument(
        "--families",
        default="image,vlm,llm",
        help="Comma-separated subset for a non-acceptance diagnostic run.",
    )
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument(
        "--acknowledge-diagnostic-manifest-drift",
        action="store_true",
        help=(
            "Permit a read-only image-manifest SHA override only for a non-acceptance "
            "diagnostic; the accepted matrix remains bound to the frozen config."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.maintenance_approved:
        raise S7RuntimeError("s7_exact_gpu_handoff_requires_maintenance_approval")
    config = S7RuntimeConfig.from_path(args.config)
    revision, branch = source_identity(args.root)
    families = tuple(value.strip() for value in args.families.split(",") if value.strip())
    if any(family not in {"image", "vlm", "llm"} for family in families):
        raise S7RuntimeError("s7_family_selection_invalid")
    if not args.diagnostic:
        raise S7RuntimeError("s7_acceptance_mode_disabled_pending_independent_review")
    if families != ("image", "vlm", "llm"):
        raise S7RuntimeError("s7_v3_diagnostic_requires_all_families")
    if not args.acknowledge_diagnostic_manifest_drift:
        raise S7RuntimeError("s7_v3_diagnostic_requires_explicit_manifest_drift_acknowledgement")
    if not S7_V3_KERNEL_CONTAINMENT_IMPLEMENTED:
        raise S7RuntimeError("s7_v3_execution_blocked_kernel_containment_required")
    if not args.cuda_python.is_file():
        raise S7RuntimeError("s7_cuda_python_missing")
    suite_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{revision[:8]}"
    suite_root = args.private_root / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    (suite_root / "profiles").mkdir()
    trusted_envelope_output = args.output.with_name(f"{args.output.stem}.trusted-envelope.json")
    if args.output.exists() or trusted_envelope_output.exists():
        publish_failure_seal(
            suite_root,
            failure_seal_payload(
                suite_id=suite_id,
                stage="success_output_preflight",
                error=S7RuntimeError("s7_success_output_exists"),
                manual_intervention_required=False,
                process_evidence=None,
                pre_mutation_checkpoint=None,
                restore_checkpoint=None,
            ),
        )
        raise S7RuntimeError("s7_success_output_exists")
    assets = load_assets(args.config, args.data_root)
    runtime_asset_overrides: dict[str, dict[str, Any]] = {}
    assets, runtime_asset_overrides = resolve_diagnostic_manifest_drift(assets)
    validate_assets(assets)
    manifest_snapshot_contract = create_run_scoped_manifest_snapshots(
        suite_root=suite_root,
        suite_id=suite_id,
        sources={family: assets[family].manifest for family in ("image", "vlm", "llm")},
        expected_raw_sha256={
            family: assets[family].manifest_sha256 for family in ("image", "vlm", "llm")
        },
    )
    manifest_snapshot_binding = manifest_snapshot_binding_sha256(manifest_snapshot_contract)
    image_manifest_snapshot = suite_root / manifest_snapshot_contract["families"]["image"]["path"]
    asset_provenance = capture_asset_provenance(
        root=args.root,
        suite_root=suite_root,
        assets=assets,
    )
    holder = capture_holder()
    source_before = source_serving_probe(holder, manifest=image_manifest_snapshot)
    active_lease = read_active_gpu_lease()
    if active_lease is not None and active_lease.state == "active":
        raise S7RuntimeError(f"s7_gpu_lease_already_active:{active_lease.run_id}")
    gpu_before = gpu_snapshot()
    target_path = args.data_root / "artifacts/w7/prometheus-targets/s7-family.json"
    prior_target = target_path.read_bytes() if target_path.is_file() else None
    if prior_target is not None and json.loads(prior_target) != []:
        raise S7RuntimeError("s7_stale_prometheus_target_present")
    prometheus_before = prometheus_health()
    if not prometheus_baseline_matches(
        prometheus_before,
        expected_target_count=EXPECTED_BASELINE_TARGET_COUNT,
    ):
        raise S7RuntimeError("s7_prometheus_baseline_not_ready")
    records = {
        family: read_jsonl(suite_root / manifest_snapshot_contract["families"][family]["path"])
        for family in families
    }
    input_catalog = prepare_inputs(
        suite_root=suite_root,
        data_root=args.data_root,
        assets=assets,
        records=records,
        seed=config.seed,
    )
    canonical_write(
        suite_root / "preflight.json",
        {
            "schema_version": "evm.s7_preflight.v1",
            "suite_id": suite_id,
            "source_revision": revision,
            "source_branch": branch,
            "holder": holder.__dict__,
            "source_serving": source_before,
            "gpu": gpu_before,
            "prometheus_baseline": prometheus_before,
            "expected_prometheus_baseline_target_count": EXPECTED_BASELINE_TARGET_COUNT,
            "assets": public_asset_identity(assets),
            "runtime_asset_overrides": runtime_asset_overrides,
            "manifest_snapshot_contract": manifest_snapshot_contract,
            "manifest_snapshot_binding_sha256": manifest_snapshot_binding,
            "asset_provenance": public_asset_provenance(asset_provenance),
            "input_catalog_sha256": canonical_sha256(public_input_catalog(input_catalog)),
            "started_at": utc_now(),
        },
    )
    pre_mutation_checkpoint = {
        "schema_version": "evm.s7_lifecycle_checkpoint.v1",
        "stage": "pre_mutation",
        "suite_id": suite_id,
        "holder": holder.__dict__,
        "active_gpu_lease": None,
        "file_sd": file_state(target_path),
        "file_sd_restore_bytes_base64": (
            base64.b64encode(prior_target).decode("ascii") if prior_target is not None else None
        ),
        "prometheus": prometheus_before,
        "mutations_started": False,
        "recorded_at": utc_now(),
    }
    canonical_write(suite_root / "lifecycle-pre-mutation.json", pre_mutation_checkpoint)
    profile_results: list[dict[str, Any]] = []
    ready_identities: dict[str, dict[str, Any]] = {}
    failed: dict[str, Any] | None = None
    failure_exc: Exception | None = None
    family_cleanup: list[dict[str, Any]] = []
    final_target_cleanup: dict[str, Any] | None = None
    restore_checkpoint: dict[str, Any] | None = None
    manual_latch_evidence: dict[str, Any] | None = None
    owned_lease: GpuLease | None = None
    holder_scaled_down = False
    try:
        holder_scaled_down = True
        scale_holder(holder, replicas=0, require_ready=False)
        for family in families:
            lease = acquire_scale_validation_gpu_lease(
                f"s7-{family}-{suite_id}",
                source_commit=revision,
                purpose="scale_validation_inference",
                scenario_id="S7",
                model_family=family,  # type: ignore[arg-type]
                owner_pid=os.getpid(),
                ttl_seconds=7200,
            )
            owned_lease = lease
            canonical_write(
                suite_root / f"{family}-lease-acquired.json",
                lease_checkpoint(lease, stage="acquired"),
            )
            service: ServiceProcess | None = None
            family_started = time.monotonic()
            try:
                service = start_service(
                    family=family,
                    asset=assets[family],
                    args=args,
                    config=config,
                    revision=revision,
                    suite_id=suite_id,
                    suite_root=suite_root,
                    lease=lease,
                )
                ready = wait_ready(service.base_url, timeout=240)
                ready_identities[family] = assert_ready_identity(
                    family, ready, assets[family], revision
                )
                canonical_write(suite_root / f"{family}-ready.json", ready)
                write_target(target_path, assets[family].port, family, suite_id)
                prometheus_recovery = refresh_prometheus_target(family, timeout=45)
                warmup(
                    family,
                    service.base_url,
                    input_catalog[family]["warmup"],
                    config,
                )
                family_profiles = [
                    value for value in config.profile_ids if profile_family(value) == family
                ]
                if args.diagnostic:
                    family_profiles = family_profiles[:1]
                for profile_id in family_profiles:
                    repetitions = 1 if args.diagnostic else config.repetitions
                    for repetition in range(1, repetitions + 1):
                        result = run_profile(
                            family=family,
                            profile_id=profile_id,
                            repetition=repetition,
                            service=service,
                            lease=lease,
                            inputs=input_catalog[family][profile_id],
                            config=config,
                            suite_root=suite_root,
                            prometheus_recovery=prometheus_recovery,
                        )
                        profile_results.append(result)
                        canonical_write(
                            suite_root / "profiles" / f"{profile_id}-r{repetition:02d}.json",
                            result,
                        )
                        time.sleep(config.cooldown_seconds)
            finally:
                stop_evidence: dict[str, Any] | None = None
                if service is not None:
                    try:
                        stop_evidence = stop_service(service)
                    except S7ManualInterventionRequired as exc:
                        manual_latch_evidence = exc.process_evidence
                        manual_latch_evidence["owned_gpu_lease"] = lease_checkpoint(
                            lease, stage="preserved_for_manual_intervention"
                        )
                        family_cleanup.append(
                            {
                                "family": family,
                                "lease_state": "preserved_for_manual_intervention",
                                "elapsed_seconds": time.monotonic() - family_started,
                                "service_process_stopped": False,
                                "process_evidence": manual_latch_evidence,
                                "active_lease_zero": False,
                                "followup_probe_count": 0,
                            }
                        )
                        canonical_write(suite_root / f"{family}-cleanup.json", family_cleanup[-1])
                        raise
                released = release_scale_validation_gpu_lease(
                    run_id=lease.run_id,
                    lease_id=lease.lease_id,
                    fencing_token=lease.fencing_token,
                    reason=f"S7 {family} family profiles completed",
                )
                canonical_write(
                    suite_root / f"{family}-lease-released.json",
                    lease_checkpoint(released, stage="released"),
                )
                owned_lease = None
                family_cleanup.append(
                    {
                        "family": family,
                        "lease_state": released.state,
                        "elapsed_seconds": time.monotonic() - family_started,
                        "service_process_stopped": service is None
                        or service.process.poll() is not None,
                        "process_evidence": stop_evidence,
                        "active_lease_zero": read_active_gpu_lease() is None,
                        "gpu_after": gpu_snapshot(),
                    }
                )
                canonical_write(suite_root / f"{family}-cleanup.json", family_cleanup[-1])
    except Exception as exc:
        failed = {
            "schema_version": "evm.s7_failed_attempt.v1",
            "suite_id": suite_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "completed_profile_repetitions": len(profile_results),
            "acceptance_credit": False,
            "action": "Stopped new family load and entered bounded fail-closed handling.",
            "recorded_at": utc_now(),
        }
        if isinstance(exc, S7ManualInterventionRequired):
            manual_latch_evidence = exc.process_evidence
        failure_exc = exc
    finally:
        followup_policy = lifecycle_followup_policy(manual_latch_evidence)
        if followup_policy["automatic_restore_allowed"]:
            try:
                final_target_cleanup = restore_runtime_state(
                    holder=holder,
                    holder_scaled_down=holder_scaled_down,
                    target_path=target_path,
                    prior_target=prior_target,
                    owned_lease=owned_lease,
                )
            except Exception as exc:
                final_target_cleanup = {
                    "restored": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                if failure_exc is None:
                    failure_exc = exc
                    failed = {
                        "schema_version": "evm.s7_failed_attempt.v1",
                        "suite_id": suite_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "completed_profile_repetitions": len(profile_results),
                        "acceptance_credit": False,
                        "action": "Exact lifecycle restore did not complete.",
                        "recorded_at": utc_now(),
                    }
        else:
            final_target_cleanup = {
                "restored": False,
                "automatic_restore_skipped": True,
                "reason": "residual_process_manual_intervention_required",
                "followup_probe_count": 0,
                "followup_policy": followup_policy,
            }
    if manual_latch_evidence is not None:
        manual_error = failure_exc or S7ManualInterventionRequired(
            "s7_residual_manual_intervention_required",
            process_evidence=manual_latch_evidence,
        )
        publish_failure_seal(
            suite_root,
            failure_seal_payload(
                suite_id=suite_id,
                stage="service_residual_latch",
                error=manual_error,
                manual_intervention_required=True,
                process_evidence=manual_latch_evidence,
                pre_mutation_checkpoint=pre_mutation_checkpoint,
                restore_checkpoint=None,
            ),
        )
        raise manual_error
    if not final_target_cleanup or final_target_cleanup.get("restored") is not True:
        restore_error = failure_exc or S7RuntimeError("s7_exact_restore_incomplete")
        restore_checkpoint = {
            "schema_version": "evm.s7_lifecycle_checkpoint.v1",
            "stage": "restore_failed",
            "suite_id": suite_id,
            "restore": final_target_cleanup,
            "success_probe_count_after_failure": 0,
            "recorded_at": utc_now(),
        }
        publish_failure_seal(
            suite_root,
            failure_seal_payload(
                suite_id=suite_id,
                stage="exact_restore_failure",
                error=restore_error,
                manual_intervention_required=True,
                process_evidence=None,
                pre_mutation_checkpoint=pre_mutation_checkpoint,
                restore_checkpoint=restore_checkpoint,
            ),
        )
        raise restore_error
    source_after = source_serving_probe(holder, manifest=image_manifest_snapshot)
    holder_after = capture_holder()
    cleanup = {
        "schema_version": "evm.s7_cleanup.v1",
        "holder_uid_exact": holder_after.uid == holder.uid,
        "holder_image_exact": holder_after.image == holder.image,
        "holder_replicas_exact": holder_after.replicas == holder.replicas,
        "source_model_sha256_exact": source_after.get("model_sha256") == holder.model_sha256,
        "source_candidate_exact": source_after.get("candidate_id") == holder.candidate_id,
        "source_cuda_inference": source_after.get("device") == "cuda",
        "gpu_lease_zero": read_active_gpu_lease() is None,
        "family_cleanup": family_cleanup,
        "prometheus_baseline": prometheus_health(),
        "s7_target_cleanup": final_target_cleanup,
        "gpu_after": gpu_snapshot(),
        "finished_at": utc_now(),
    }
    cleanup["cleanup_passed"] = cleanup_contract_passed(
        cleanup,
        expected_baseline_target_count=EXPECTED_BASELINE_TARGET_COUNT,
    )
    restore_checkpoint = {
        "schema_version": "evm.s7_lifecycle_checkpoint.v1",
        "stage": "post_restore",
        "suite_id": suite_id,
        "holder": holder_after.__dict__,
        "holder_uid_exact": cleanup["holder_uid_exact"],
        "holder_image_exact": cleanup["holder_image_exact"],
        "holder_replicas_exact": cleanup["holder_replicas_exact"],
        "active_gpu_lease": None if cleanup["gpu_lease_zero"] else "nonzero",
        "file_sd": file_state(target_path),
        "file_sd_matches_pre_mutation": file_state(target_path)
        == pre_mutation_checkpoint["file_sd"],
        "prometheus": cleanup["prometheus_baseline"],
        "restore_complete": cleanup["cleanup_passed"],
        "recorded_at": utc_now(),
    }
    canonical_write(suite_root / "lifecycle-post-restore.json", restore_checkpoint)
    canonical_write(suite_root / "cleanup.json", cleanup)
    if failure_exc is None and cleanup["cleanup_passed"] is not True:
        failure_exc = S7RuntimeError("s7_cleanup_contract_failed")
        failed = {
            "schema_version": "evm.s7_failed_attempt.v1",
            "suite_id": suite_id,
            "error_type": type(failure_exc).__name__,
            "error": str(failure_exc),
            "completed_profile_repetitions": len(profile_results),
            "acceptance_credit": False,
            "action": "Cleanup evidence failed the frozen source/lease/Prometheus contract.",
            "recorded_at": utc_now(),
        }
    if failure_exc is not None:
        assert failed is not None
        publish_failure_seal(
            suite_root,
            failure_seal_payload(
                suite_id=suite_id,
                stage="runtime_or_restore_failure",
                error=failure_exc,
                manual_intervention_required=False,
                process_evidence=None,
                pre_mutation_checkpoint=pre_mutation_checkpoint,
                restore_checkpoint=restore_checkpoint,
            ),
        )
        raise failure_exc
    if args.diagnostic:
        index = private_evidence_index(
            suite_root, manifest_snapshot_contract=manifest_snapshot_contract
        )
        errors: list[str] = []
        projected = [
            project_profile(item, config=config, errors=errors) for item in profile_results
        ]
        if errors or set(ready_identities) != set(families):
            projection_error = S7RuntimeError("s7_diagnostic_projection_failed:" + ",".join(errors))
            publish_failure_seal(
                suite_root,
                failure_seal_payload(
                    suite_id=suite_id,
                    stage="diagnostic_projection",
                    error=projection_error,
                    manual_intervention_required=False,
                    process_evidence=None,
                    pre_mutation_checkpoint=pre_mutation_checkpoint,
                    restore_checkpoint=restore_checkpoint,
                ),
            )
            raise projection_error
        canonical_write(suite_root / "private-evidence-index.json", index)
        index_raw = (suite_root / "private-evidence-index.json").read_bytes()
        public = {
            "schema_version": "evm.s7_current_revision_cuda_smoke.v3",
            "status": "verified",
            "verdict": "passed",
            "acceptance_credit": False,
            "suite_id": suite_id,
            "source_identity": {
                "revision": revision,
                "branch": branch,
                "config_sha256": config.sha256,
                "git_blobs": source_git_identity(
                    args.root.parent, revision, include_manifest_contract=True
                ),
                "runtime_asset_overrides": runtime_asset_overrides,
                "manifest_snapshot_contract": manifest_snapshot_contract,
                "manifest_snapshot_binding_sha256": manifest_snapshot_binding,
            },
            "families": list(families),
            "profiles": projected,
            "family_ready_identity": ready_identities,
            "asset_provenance": public_asset_provenance(asset_provenance),
            "runtime_evidence": {
                "transport": "external_http",
                "submitted_requests": sum(item["request_count"] for item in projected),
                "completed_requests": sum(item["completed"] for item in projected),
                "rejected_requests": sum(item["rejected"] for item in projected),
                "transport_failures": sum(item["transport_failed"] for item in projected),
                "actual_cuda": all(
                    dict(item.get("runtime", {})).get("cuda_available") is True
                    if family != "image"
                    else item.get("device") == "cuda"
                    for family, item in ready_identities.items()
                ),
                "trace_identity_complete": all(item["trace_complete"] for item in projected),
                "oom_count": sum(item["oom_count"] for item in projected),
                "admitted_starvation_count": sum(
                    item["admitted_starvation_count"] for item in projected
                ),
            },
            "cleanup": {
                "source_serving_ready": cleanup["source_model_sha256_exact"],
                "source_cuda_inference": cleanup["source_cuda_inference"],
                "source_model_identity_exact": cleanup["source_model_sha256_exact"],
                "source_candidate_identity_exact": cleanup["source_candidate_exact"],
                "source_holder_identity_exact": cleanup["holder_uid_exact"],
                "service_processes_stopped": all(
                    item["service_process_stopped"] for item in family_cleanup
                ),
                "gpu_lease_zero": cleanup["gpu_lease_zero"],
                "family_queues_drained": all(item["drained"] for item in projected),
                "s7_prometheus_target_zero": bool(
                    dict(cleanup["s7_target_cleanup"]).get("restored")
                ),
                "prometheus_baseline_target_count": cleanup["prometheus_baseline"]["target_count"],
                "prometheus_baseline_up_count": cleanup["prometheus_baseline"]["up_count"],
            },
            "private_evidence": {
                "artifact_count": len(index["artifacts"]),
                "total_bytes": sum(int(item["bytes"]) for item in index["artifacts"]),
                "aggregate_sha256": index["aggregate_sha256"],
                "index_sha256": hashlib.sha256(index_raw).hexdigest(),
                "location": "outside_git_private_evidence_root",
            },
            "claim_boundary": SMOKE_CLAIM_BOUNDARY,
            "generated_at": utc_now(),
        }
        public_raw = canonical_public_json_bytes(public)
        trusted_envelope = build_trusted_manifest_envelope(
            suite_id=suite_id,
            source_revision=revision,
            manifest_snapshot_binding_sha256=manifest_snapshot_binding,
            private_evidence_index_sha256=hashlib.sha256(index_raw).hexdigest(),
            public_evidence_sha256=hashlib.sha256(public_raw).hexdigest(),
        )
        try:
            publish_exclusive_atomic_bytes(args.output, public_raw)
            # The independently pinned envelope is the final and sole success
            # commit.  Nothing fallible may be published after it: a public-only
            # orphan remains untrusted, while a committed envelope must never be
            # followed by a contradictory failure artifact.
            write_public_json_exclusive(trusted_envelope_output, trusted_envelope)
        except Exception as exc:
            publish_failure_seal(
                suite_root,
                failure_seal_payload(
                    suite_id=suite_id,
                    stage="diagnostic_success_publication",
                    error=exc,
                    manual_intervention_required=False,
                    process_evidence=None,
                    pre_mutation_checkpoint=pre_mutation_checkpoint,
                    restore_checkpoint=restore_checkpoint,
                ),
            )
            raise
        print(json.dumps({"status": "diagnostic_passed", "suite_root": str(suite_root)}))
        return 0
    raise S7RuntimeError("s7_acceptance_mode_unreachable")


def load_assets(config_path: Path, data_root: Path) -> dict[str, AssetSpec]:
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, dict):
        raise S7RuntimeError("s7_asset_config_missing")
    result: dict[str, AssetSpec] = {}
    for family in ("image", "vlm", "llm"):
        raw = raw_assets.get(family)
        if not isinstance(raw, dict):
            raise S7RuntimeError(f"s7_asset_config_missing:{family}")
        result[family] = AssetSpec(
            family=family,
            port=int(raw["port"]),
            manifest=data_root / str(raw["manifest"]),
            manifest_sha256=str(raw["manifest_sha256"]),
            model_artifact=(data_root / str(raw["model_artifact"]))
            if raw.get("model_artifact")
            else None,
            model_artifact_sha256=raw.get("model_artifact_sha256"),
            candidate_id=raw.get("candidate_id"),
            dataset_version=raw.get("dataset_version"),
            base_model=(data_root / str(raw["base_model"])) if raw.get("base_model") else None,
            adapter=(data_root / str(raw["adapter"])) if raw.get("adapter") else None,
            adapter_sha256=raw.get("adapter_sha256"),
            model_repository=raw.get("model_repository"),
            model_revision=raw.get("model_revision"),
            data_identity_sha256=raw.get("data_identity_sha256"),
            model_source_commit=raw.get("model_source_commit"),
            quantization=str(raw.get("quantization", "none")),
        )
    return result


def validate_assets(assets: dict[str, AssetSpec]) -> None:
    for asset in assets.values():
        if not asset.manifest.is_file() or file_sha256(asset.manifest) != asset.manifest_sha256:
            raise S7RuntimeError(f"s7_manifest_identity_mismatch:{asset.family}")
        if asset.family == "image":
            if (
                asset.model_artifact is None
                or not asset.model_artifact.is_file()
                or file_sha256(asset.model_artifact) != asset.model_artifact_sha256
            ):
                raise S7RuntimeError("s7_image_model_identity_mismatch")
        else:
            if (
                asset.base_model is None
                or not asset.base_model.is_dir()
                or asset.adapter is None
                or not asset.adapter.is_dir()
                or file_sha256(asset.adapter / "adapter_model.safetensors") != asset.adapter_sha256
            ):
                raise S7RuntimeError(f"s7_adapter_identity_mismatch:{asset.family}")


def resolve_diagnostic_manifest_drift(
    assets: dict[str, AssetSpec],
) -> tuple[dict[str, AssetSpec], dict[str, dict[str, Any]]]:
    resolved = dict(assets)
    overrides: dict[str, dict[str, Any]] = {}
    for family, asset in assets.items():
        if not asset.manifest.is_file():
            raise S7RuntimeError(f"s7_manifest_missing:{family}")
        observed = file_sha256(asset.manifest)
        if observed == asset.manifest_sha256:
            continue
        if family != "image":
            raise S7RuntimeError(f"s7_manifest_identity_mismatch:{family}")
        records = read_jsonl(asset.manifest)
        if len(records) < 6 or any(
            not str(item.get("content_sha256") or "") for item in records[:24]
        ):
            raise S7RuntimeError("s7_diagnostic_image_manifest_not_governed")
        resolved[family] = replace(asset, manifest_sha256=observed)
        overrides[family] = {
            "scope": "non_acceptance_current_revision_diagnostic_only",
            "reason": "curated_manifest_regenerated_after_accepted_matrix",
            "frozen_manifest_sha256": asset.manifest_sha256,
            "observed_manifest_sha256": observed,
            "dataset_version": asset.dataset_version,
            "record_count": len(records),
            "acceptance_credit": False,
        }
    return resolved, overrides


def prepare_inputs(
    *,
    suite_root: Path,
    data_root: Path,
    assets: dict[str, AssetSpec],
    records: dict[str, list[dict[str, Any]]],
    seed: int,
) -> dict[str, dict[str, list[RequestInput] | RequestInput]]:
    rng = random.Random(seed)
    catalog: dict[str, dict[str, Any]] = {}
    if "image" in records:
        image_records = records["image"][:24]
        rng.shuffle(image_records)
        image_records.sort(key=lambda item: int(item["width"]) * int(item["height"]))
        selected = image_records[:6]
        large = image_records[-6:]
        small_inputs: list[RequestInput] = []
        large_inputs: list[RequestInput] = []
        derived_root = suite_root / "inputs" / "image"
        derived_root.mkdir(parents=True, exist_ok=True)
        for index, record in enumerate(selected):
            source = visa_image_path(data_root, record)
            derived = derived_root / f"small-{index:02d}.jpg"
            with Image.open(source) as image:
                image.convert("RGB").resize((224, 224)).save(derived, quality=92)
            small_inputs.append(
                request_input(
                    "image-small", index, "short", {"image_uri": str(derived)}, image_label(record)
                )
            )
        for index, record in enumerate(large):
            source = visa_image_path(data_root, record)
            large_inputs.append(
                request_input(
                    "image-large", index, "long", {"image_uri": str(source)}, image_label(record)
                )
            )
        over = derived_root / "over-limit.png"
        Image.new("RGB", (2048, 2048), color=(127, 127, 127)).save(over)
        catalog["image"] = {
            "warmup": small_inputs[0],
            "image-small": small_inputs,
            "image-large": large_inputs,
            "image-fairness": fairness_inputs(small_inputs, large_inputs),
            "image-over-limit": [
                request_input("image-over-limit", i, "long", {"image_uri": str(over)}, "normal")
                for i in range(6)
            ],
        }
    if "vlm" in records:
        test = [item for item in records["vlm"] if item.get("split") == "test"]
        if len(test) < 6:
            test = records["vlm"][-8:]
        test.sort(key=lambda item: int(item["width"]) * int(item["height"]))
        short = [
            vlm_input(item, "vlm-small-short", index, 8, "short")
            for index, item in enumerate(test[:6])
        ]
        long = [
            vlm_input(item, "vlm-large-long", index, 32, "long")
            for index, item in enumerate(test[-6:])
        ]
        over = [
            vlm_input(item, "vlm-over-limit", index, 64, "long")
            for index, item in enumerate(test[:6])
        ]
        catalog["vlm"] = {
            "warmup": short[0],
            "vlm-small-short": short,
            "vlm-large-long": long,
            "vlm-fairness": fairness_inputs(short, long),
            "vlm-over-limit": over,
        }
    if "llm" in records:
        test = [item for item in records["llm"] if item.get("split") == "test"]
        if len(test) < 12:
            test = records["llm"][-32:]
        test.sort(
            key=lambda item: len(str(item.get("context", "")))
            + len(str(item.get("instruction", "")))
        )
        short_records = test[:6]
        long_candidates = test[:6]
        short = [
            llm_input(item, "llm-short", index, 8, "short")
            for index, item in enumerate(short_records)
        ]
        long = [
            llm_input(
                item,
                "llm-long",
                index,
                128,
                "long",
                synthetic_padding="Bounded load context. " * 240,
            )
            for index, item in enumerate(long_candidates)
        ]
        oversized_context = "bounded admission token " * 3000
        over = [
            request_input(
                "llm-over-limit",
                index,
                "long",
                {
                    "model_family": "llm",
                    "instruction": "Summarize the supplied context.",
                    "context": oversized_context,
                    "max_new_tokens": 8,
                    "deadline_seconds": 120,
                },
                "",
            )
            for index in range(6)
        ]
        catalog["llm"] = {
            "warmup": short[0],
            "llm-short": short,
            "llm-long": long,
            "llm-fairness": fairness_inputs(short, long),
            "llm-over-limit": over,
        }
    return catalog


def fairness_inputs(short: list[RequestInput], long: list[RequestInput]) -> list[RequestInput]:
    return [short[0], long[0], short[1], short[2], short[3], long[1]]


def request_input(
    profile: str,
    index: int,
    request_class: str,
    payload: dict[str, Any],
    expected: str | int,
) -> RequestInput:
    identity = hashlib.sha256(
        json.dumps([profile, index, payload], sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return RequestInput(f"s7-{profile}-{identity}", request_class, payload, expected)


def vlm_input(
    record: dict[str, Any], profile: str, index: int, tokens: int, request_class: str
) -> RequestInput:
    return request_input(
        profile,
        index,
        request_class,
        {
            "model_family": "vlm",
            "image_uri": record["image_uri"],
            "image_sha256": record["image_sha256"],
            "question": record["question"],
            "choices": record["choices"],
            "max_new_tokens": tokens,
            "deadline_seconds": 120,
        },
        int(record["answer_index"]),
    )


def llm_input(
    record: dict[str, Any],
    profile: str,
    index: int,
    tokens: int,
    request_class: str,
    synthetic_padding: str = "",
) -> RequestInput:
    context = record.get("context") or ""
    if synthetic_padding:
        context = f"{context}\n{synthetic_padding}".strip()
    return request_input(
        profile,
        index,
        request_class,
        {
            "model_family": "llm",
            "instruction": record["instruction"],
            "context": context or None,
            "max_new_tokens": tokens,
            "deadline_seconds": 120,
        },
        str(record["response"]),
    )


def run_profile(
    *,
    family: str,
    profile_id: str,
    repetition: int,
    service: ServiceProcess,
    lease: GpuLease,
    inputs: list[RequestInput],
    config: S7RuntimeConfig,
    suite_root: Path,
    prometheus_recovery: dict[str, Any],
) -> dict[str, Any]:
    assert_scale_validation_gpu_lease_owner(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        purpose="scale_validation_inference",
        scenario_id="S7",
        model_family=family,  # type: ignore[arg-type]
    )
    samples: list[dict[str, Any]] = []
    stop = threading.Event()
    started = time.monotonic()
    sampler = threading.Thread(
        target=sample_resources,
        args=(service.process.pid, started, stop, samples, config.resource_sample_interval_seconds),
        daemon=True,
    )
    sampler.start()
    requests_result: list[dict[str, Any]] = []
    try:
        ordered = list(inputs)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=config.closed_concurrency
        ) as executor:
            futures: list[concurrent.futures.Future[dict[str, Any]]] = []
            for index, item in enumerate(ordered):
                futures.append(
                    executor.submit(
                        send_request,
                        family,
                        service.base_url,
                        item,
                        started,
                        config.request_timeout_seconds,
                    )
                )
                if profile_id.endswith("fairness") and index == 0:
                    time.sleep(0.02)
            for future in futures:
                requests_result.append(future.result(timeout=config.request_timeout_seconds + 10))
    finally:
        stop.set()
        sampler.join(timeout=5)
        samples.append(resource_sample(service.process.pid, time.monotonic() - started))
    finished = time.monotonic()
    ready = requests.get(f"{service.base_url}/ready", timeout=10)
    ready.raise_for_status()
    final_admission = dict(ready.json().get("admission", {}))
    prometheus_up = prometheus_target_up(family)
    service.log_handle.flush()
    return {
        "schema_version": "evm.s7_profile_private.v1",
        "profile_id": profile_id,
        "family": family,
        "repetition": repetition,
        "seed": config.seed,
        "seed_applied": True,
        "started_at": utc_now(),
        "measurement_seconds": finished - started,
        "requests": sorted(requests_result, key=lambda item: item["arrived_offset_seconds"]),
        "resource_samples": samples,
        "final_admission": final_admission,
        "prometheus_up": prometheus_up,
        "prometheus_recovery_seconds": float(prometheus_recovery["elapsed_seconds"]),
        "prometheus_refresh_restart_used": bool(prometheus_recovery["container_restart_count"]),
        "lease_identity_exact": True,
        "lease": {
            "run_id": lease.run_id,
            "lease_id": lease.lease_id,
            "fencing_token_sha256": hashlib.sha256(lease.fencing_token.encode()).hexdigest(),
            "model_family": lease.model_family,
            "scenario_id": lease.scenario_id,
        },
        "service_pid": service.process.pid,
        "service_log_sha256": file_sha256(service.log_path),
        "metrics_sha256": hashlib.sha256(
            requests.get(f"{service.base_url}/metrics", timeout=10).content
        ).hexdigest(),
        "cleanup_passed": (
            service.process.poll() is None
            and int(final_admission.get("active_requests", -1)) == 0
            and int(final_admission.get("queue_depth", -1)) == 0
        ),
        "finished_at": utc_now(),
    }


def send_request(
    family: str,
    base_url: str,
    item: RequestInput,
    profile_started: float,
    timeout: float,
) -> dict[str, Any]:
    arrived = time.monotonic()
    trace_id = hashlib.sha256(f"{item.request_id}-{uuid4().hex}".encode()).hexdigest()[:32]
    span_id = uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"
    endpoint = "/predict" if family == "image" else "/infer"
    result: dict[str, Any] = {
        "request_id": item.request_id,
        "request_class": item.request_class,
        "expected": item.expected,
        "arrived_offset_seconds": arrived - profile_started,
        "trace_id_sent": trace_id,
        "requested_output_tokens": int(item.payload.get("max_new_tokens", 0)),
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{base_url}{endpoint}",
                json=item.payload,
                headers={"traceparent": traceparent},
            )
        finished = time.monotonic()
        result.update(
            {
                "finished_offset_seconds": finished - profile_started,
                "latency_seconds": finished - arrived,
                "status_code": response.status_code,
                "trace_id_observed": trace_id_from_header(response.headers.get("traceparent")),
                "retry_after": response.headers.get("Retry-After"),
                "response": response.json(),
                "oom": "out of memory" in response.text.lower(),
            }
        )
        if response.status_code == 200:
            result["outcome"] = "completed"
        elif response.status_code == 408:
            result["outcome"] = "expired"
        else:
            result["outcome"] = "rejected"
    except Exception as exc:
        finished = time.monotonic()
        result.update(
            {
                "finished_offset_seconds": finished - profile_started,
                "latency_seconds": finished - arrived,
                "status_code": 0,
                "trace_id_observed": None,
                "response": {"error_type": type(exc).__name__, "error": str(exc)},
                "oom": "out of memory" in str(exc).lower(),
                "outcome": "transport_failed",
            }
        )
    return result


def warmup(
    family: str,
    base_url: str,
    item: RequestInput,
    config: S7RuntimeConfig,
) -> None:
    result = send_request(family, base_url, item, time.monotonic(), config.request_timeout_seconds)
    if result.get("outcome") != "completed" or result.get("trace_id_sent") != result.get(
        "trace_id_observed"
    ):
        raise S7RuntimeError(f"s7_warmup_failed:{family}:{result.get('status_code')}")


def start_service(
    *,
    family: str,
    asset: AssetSpec,
    args: argparse.Namespace,
    config: S7RuntimeConfig,
    revision: str,
    suite_id: str,
    suite_root: Path,
    lease: GpuLease,
) -> ServiceProcess:
    env = os.environ.copy()
    service_run_uuid = str(uuid4())
    env.update(
        {
            "PYTHONPATH": os.pathsep.join([str(args.root), str(args.root / "src")]),
            "EVM_GIT_COMMIT": revision,
            "EVM_S7_ADMISSION_CONFIG": str(args.config),
            "EVM_OTEL_ENABLED": "true",
            "EVM_OTEL_REQUIRED": "true",
            "EVM_OTEL_PROCESSOR": "simple",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
            "OTEL_SERVICE_NAMESPACE": "enterprise-mlops-scale-validation",
            "OTEL_SERVICE_INSTANCE_ID": f"s7-{family}-{suite_id}",
            "EVM_S7_SERVICE_RUN_UUID": service_run_uuid,
        }
    )
    if family == "image":
        env.update(
            {
                "APP_NAME": "evm-s7-image-serving",
                "EVM_MODEL_PATH": str(asset.model_artifact),
                "EVM_MODEL_SHA256": str(asset.model_artifact_sha256),
                "EVM_MODEL_CANDIDATE_ID": str(asset.candidate_id),
                "EVM_DATASET_VERSION": str(asset.dataset_version),
                "EVM_REQUIRE_CUDA": "true",
                **host_image_data_environment(args.data_root),
            }
        )
        command = [
            str(args.cuda_python),
            "-m",
            "uvicorn",
            "apps.api.efficientnet_serving:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(asset.port),
        ]
    else:
        command = [
            str(args.cuda_python),
            "-m",
            "evm.model_runtime.serving",
            "--model-family",
            family,
            "--base-model-dir",
            str(asset.base_model),
            "--adapter-dir",
            str(asset.adapter),
            "--model-repository",
            str(asset.model_repository),
            "--model-revision",
            str(asset.model_revision),
            "--model-artifact-sha256",
            str(asset.adapter_sha256),
            "--data-identity-sha256",
            str(asset.data_identity_sha256),
            "--source-commit",
            str(asset.model_source_commit),
            "--runtime-source-commit",
            revision,
            "--lifecycle-run-id",
            f"s7-{family}-{suite_id}",
            "--quantization",
            asset.quantization,
            "--admission-config",
            str(args.config),
            "--environment",
            "local-staging",
            "--host",
            "127.0.0.1",
            "--port",
            str(asset.port),
        ]
    assert_scale_validation_gpu_lease_owner(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        purpose="scale_validation_inference",
        scenario_id="S7",
        model_family=family,  # type: ignore[arg-type]
    )
    log_path = suite_root / f"{family}-service.log"
    handle = log_path.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        command,
        cwd=args.root,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        start_new_session=os.name != "nt",
    )
    root_created_at = psutil.Process(process.pid).create_time()
    return ServiceProcess(
        family,
        process,
        handle,
        log_path,
        f"http://127.0.0.1:{asset.port}",
        service_run_uuid,
        root_created_at,
    )


def _process_record(process: psutil.Process) -> dict[str, Any]:
    try:
        command = "\0".join(process.cmdline()).encode("utf-8", errors="replace")
        return {
            "pid": process.pid,
            "ppid": process.ppid(),
            "created_at": process.create_time(),
            "status": process.status(),
            "cmdline_sha256": hashlib.sha256(command).hexdigest(),
        }
    except psutil.Error as exc:
        raise S7RuntimeError(f"s7_process_identity_unavailable:{process.pid}") from exc


def _live_service_processes(
    service: ServiceProcess,
    known: dict[tuple[int, float], dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        root = psutil.Process(service.process.pid)
        if root.create_time() == service.root_created_at:
            for process in [root, *root.children(recursive=True)]:
                record = _process_record(process)
                known[(record["pid"], record["created_at"])] = record
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        pass
    except psutil.Error as exc:
        raise S7RuntimeError("s7_process_scope_scan_uncertain:root") from exc
    live: list[dict[str, Any]] = []
    for (pid, created_at), recorded in sorted(known.items()):
        try:
            process = psutil.Process(pid)
            if process.create_time() != created_at or process.status() == psutil.STATUS_ZOMBIE:
                continue
            current = _process_record(process)
            known[(pid, created_at)] = current
            live.append(current)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.Error as exc:
            raise S7RuntimeError(f"s7_process_scope_scan_uncertain:{pid}") from exc
    return live


def _send_graceful_signal(service: ServiceProcess) -> None:
    if os.name == "nt":
        service.process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(os.getpgid(service.process.pid), signal.SIGTERM)


def stop_service(
    service: ServiceProcess,
    *,
    residual_timeout: float = 30.0,
    poll_interval: float = 0.1,
) -> dict[str, Any]:
    known: dict[tuple[int, float], dict[str, Any]] = {}
    try:
        initial = _live_service_processes(service, known)
    except S7RuntimeError as exc:
        service.log_handle.flush()
        service.log_handle.close()
        raise S7ManualInterventionRequired(
            f"s7_service_process_scope_uncertain:{service.family}",
            process_evidence={
                "schema_version": "evm.s7_cooperative_service_stop.v1",
                "family": service.family,
                "run_uuid": service.run_uuid,
                "root_pid": service.process.pid,
                "scan_error": str(exc),
                "residual_process_count": -1,
                "forced_termination_attempts": 0,
                "automatic_retry_count": 0,
                "subsequent_probe_after_residual": 0,
            },
        ) from exc
    graceful_signal_count = 0
    signal_error: str | None = None
    if service.process.poll() is None:
        try:
            _send_graceful_signal(service)
            graceful_signal_count = 1
        except (OSError, ProcessLookupError) as exc:
            signal_error = f"{type(exc).__name__}:{exc}"
    deadline = time.monotonic() + residual_timeout
    residual = initial
    while residual and time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            residual = _live_service_processes(service, known)
        except S7RuntimeError as exc:
            signal_error = f"process_scope_uncertain:{exc}"
            break
    service.log_handle.flush()
    service.log_handle.close()
    evidence = {
        "schema_version": "evm.s7_cooperative_service_stop.v1",
        "family": service.family,
        "run_uuid": service.run_uuid,
        "root_pid": service.process.pid,
        "root_created_at": service.root_created_at,
        "graceful_signal_count": graceful_signal_count,
        "signal_error": signal_error,
        "bounded_residual_wait_seconds": residual_timeout,
        "known_processes": sorted(
            known.values(), key=lambda item: (item["pid"], item["created_at"])
        ),
        "residual_processes": residual,
        "residual_process_count": len(residual),
        "forced_termination_attempts": 0,
        "automatic_retry_count": 0,
        "subsequent_probe_after_residual": 0,
    }
    if signal_error is not None or residual:
        raise S7ManualInterventionRequired(
            f"s7_service_residual_manual_intervention_required:{service.family}",
            process_evidence=evidence,
        )
    return evidence


def wait_ready(base_url: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/ready", timeout=3)
            if response.status_code == 200:
                return response.json()
            last = f"status={response.status_code}:{response.text[:200]}"
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(1)
    raise S7RuntimeError(f"s7_service_ready_timeout:{last}")


def assert_ready_identity(
    family: str, ready: dict[str, Any], asset: AssetSpec, revision: str
) -> dict[str, Any]:
    if family == "image":
        if (
            ready.get("status") != "ok"
            or ready.get("model_sha256") != asset.model_artifact_sha256
            or ready.get("candidate_id") != asset.candidate_id
            or ready.get("dataset_version") != asset.dataset_version
            or ready.get("device") != "cuda"
            or ready.get("cuda_available") is not True
        ):
            raise S7RuntimeError("s7_image_ready_identity_mismatch")
        return {
            "status": ready["status"],
            "model_family": "image",
            "candidate_id": ready["candidate_id"],
            "model_artifact_sha256": ready["model_sha256"],
            "data_identity_sha256": ready["dataset_version"],
            "device": ready["device"],
            "cuda_available": ready["cuda_available"],
            "quantization_requested": "none",
            "quantization_observed": "none",
        }
    quantization = dict(ready.get("quantization_runtime", {}))
    expected_observed = "int4_nf4" if family == "llm" else "none"
    if (
        ready.get("status") != "ready"
        or ready.get("model_family") != family
        or ready.get("model_repository") != asset.model_repository
        or ready.get("model_artifact_sha256") != asset.adapter_sha256
        or ready.get("model_revision") != asset.model_revision
        or ready.get("data_identity_sha256") != asset.data_identity_sha256
        or ready.get("model_source_commit") != asset.model_source_commit
        or ready.get("runtime_source_commit") != revision
        or ready.get("quantization") != asset.quantization
        or quantization.get("requested") != asset.quantization
        or quantization.get("observed") != expected_observed
        or not dict(ready.get("runtime", {})).get("cuda_available")
        or (
            family == "llm"
            and (
                quantization.get("loaded_in_4bit") is not True
                or int(quantization.get("linear_4bit_module_count", 0)) < 1
            )
        )
    ):
        raise S7RuntimeError(f"s7_{family}_ready_identity_mismatch")
    return {
        "status": ready["status"],
        "model_family": family,
        "model_repository": ready["model_repository"],
        "model_revision": ready["model_revision"],
        "model_artifact_sha256": ready["model_artifact_sha256"],
        "data_identity_sha256": ready["data_identity_sha256"],
        "model_source_commit": ready["model_source_commit"],
        "runtime_source_commit": ready["runtime_source_commit"],
        "quantization_requested": asset.quantization,
        "quantization_observed": quantization["observed"],
        "loaded_in_4bit": quantization["loaded_in_4bit"],
        "linear_4bit_module_count": int(quantization["linear_4bit_module_count"]),
        "runtime": {
            "cuda_available": dict(ready["runtime"])["cuda_available"],
            "torch": dict(ready["runtime"]).get("torch"),
            "cuda": dict(ready["runtime"]).get("cuda"),
        },
    }


def sample_resources(
    pid: int,
    started: float,
    stop: threading.Event,
    output: list[dict[str, Any]],
    interval: float,
) -> None:
    while not stop.is_set():
        output.append(resource_sample(pid, time.monotonic() - started))
        stop.wait(interval)


def resource_sample(pid: int, elapsed: float) -> dict[str, Any]:
    gpu = gpu_snapshot()
    rss = 0
    child_count = 0
    try:
        process = psutil.Process(pid)
        processes = [process, *process.children(recursive=True)]
        child_count = max(0, len(processes) - 1)
        rss = sum(item.memory_info().rss for item in processes if item.is_running())
    except psutil.Error:
        pass
    return {
        "elapsed_seconds": elapsed,
        "process_tree_rss_bytes": rss,
        "process_child_count": child_count,
        "gpu_used_memory_bytes": int(float(gpu["memory_used_mib"]) * 1024 * 1024),
        "gpu_free_memory_bytes": int(float(gpu["memory_free_mib"]) * 1024 * 1024),
        "gpu_utilization_percent": float(gpu["utilization_percent"]),
        "gpu_temperature_celsius": float(gpu["temperature_celsius"]),
    }


def gpu_snapshot() -> dict[str, Any]:
    output = (
        run_checked(
            [
                "nvidia-smi",
                "--query-gpu=uuid,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=15,
        )
        .stdout.strip()
        .splitlines()
    )
    if len(output) != 1:
        raise S7RuntimeError("s7_exact_single_gpu_inventory_required")
    fields = [value.strip() for value in output[0].split(",")]
    if len(fields) != 8:
        raise S7RuntimeError("s7_gpu_inventory_invalid")
    return {
        "uuid": fields[0],
        "name": fields[1],
        "driver": fields[2],
        "memory_total_mib": float(fields[3]),
        "memory_used_mib": float(fields[4]),
        "memory_free_mib": float(fields[5]),
        "utilization_percent": float(fields[6]),
        "temperature_celsius": float(fields[7]),
    }


def capture_holder() -> HolderSnapshot:
    namespace = "evm-production"
    name = "evm-b0-production"
    deployment = kubectl_json(
        ["kubectl", "-n", namespace, "get", f"deployment/{name}", "-o", "json"]
    )
    replicas = int(deployment.get("spec", {}).get("replicas") or 0)
    available = int(deployment.get("status", {}).get("availableReplicas") or 0)
    uid = str(deployment.get("metadata", {}).get("uid") or "")
    selector_map = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
    selector = ",".join(f"{key}={value}" for key, value in sorted(selector_map.items()))
    pods = kubectl_json(["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"])
    active = [
        item
        for item in pods.get("items", [])
        if not item.get("metadata", {}).get("deletionTimestamp")
        and item.get("status", {}).get("phase") == "Running"
    ]
    env = {
        item["name"]: item.get("value", "")
        for item in deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])
    }
    if not uid or replicas != 1 or available != 1 or len(active) != 1:
        raise S7RuntimeError("s7_holder_identity_not_exact_ready")
    return HolderSnapshot(
        namespace=namespace,
        name=name,
        uid=uid,
        replicas=replicas,
        selector=selector,
        pod_uid=str(active[0].get("metadata", {}).get("uid") or ""),
        pod_name=str(active[0].get("metadata", {}).get("name") or ""),
        image=str(deployment["spec"]["template"]["spec"]["containers"][0]["image"]),
        model_sha256=env["EVM_MODEL_SHA256"],
        candidate_id=env["EVM_MODEL_CANDIDATE_ID"],
    )


def scale_holder(holder: HolderSnapshot, *, replicas: int, require_ready: bool) -> None:
    current = kubectl_json(
        ["kubectl", "-n", holder.namespace, "get", f"deployment/{holder.name}", "-o", "json"]
    )
    if str(current.get("metadata", {}).get("uid") or "") != holder.uid:
        raise S7RuntimeError("s7_holder_uid_changed")
    run_checked(
        [
            "kubectl",
            "-n",
            holder.namespace,
            "scale",
            f"deployment/{holder.name}",
            f"--replicas={replicas}",
        ],
        timeout=60,
    )
    wait_holder(holder, expected=replicas, require_ready=require_ready)


def wait_holder(
    holder: HolderSnapshot, *, expected: int, require_ready: bool, timeout: float = 300
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = kubectl_json(
            ["kubectl", "-n", holder.namespace, "get", "pods", "-l", holder.selector, "-o", "json"]
        )
        active = [
            item
            for item in pods.get("items", [])
            if not item.get("metadata", {}).get("deletionTimestamp")
            and item.get("status", {}).get("phase") in {"Pending", "Running"}
        ]
        ready = [
            item
            for item in active
            if any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in item.get("status", {}).get("conditions", [])
            )
        ]
        if len(active) == expected and (not require_ready or len(ready) == expected):
            return
        time.sleep(2)
    raise S7RuntimeError(f"s7_holder_scale_timeout:{expected}")


def source_serving_probe(holder: HolderSnapshot, *, manifest: Path) -> dict[str, Any]:
    ready = requests.get("http://127.0.0.1:30800/ready", timeout=10)
    ready.raise_for_status()
    payload = ready.json()
    if payload.get("model_sha256") != holder.model_sha256 or payload.get("device") != "cuda":
        raise S7RuntimeError("s7_source_serving_probe_failed")
    records = read_jsonl(manifest)
    response = requests.post(
        "http://127.0.0.1:30800/predict",
        json={"image_uri": records[0]["image_uri"]},
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    return {
        "status": payload.get("status"),
        "candidate_id": payload.get("candidate_id"),
        "model_sha256": payload.get("model_sha256"),
        "device": result.get("device"),
        "prediction": result.get("prediction"),
        "latency_ms": result.get("latency_ms"),
    }


def write_target(path: Path, port: int, family: str, suite_id: str) -> None:
    replace_mutable_json(
        path,
        [
            {
                "targets": [f"host.docker.internal:{port}"],
                "labels": {"family": family, "suite": suite_id},
            }
        ],
    )


def reload_prometheus() -> None:
    response = requests.post(f"{PROMETHEUS_URL}/-/reload", timeout=10)
    if response.status_code not in {200, 204}:
        raise S7RuntimeError(f"s7_prometheus_reload_failed:{response.status_code}")


def refresh_prometheus_target(family: str, *, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    reload_prometheus()
    if wait_until(lambda: prometheus_target_up(family), timeout=timeout):
        return {
            "elapsed_seconds": time.monotonic() - started,
            "reload_count": 1,
            "container_restart_count": 0,
        }
    raise S7RuntimeError(f"s7_prometheus_target_timeout:{family}")


def prometheus_target_up(family: str) -> bool:
    try:
        payload = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=5).json()
    except (requests.RequestException, ValueError):
        return False
    targets = payload.get("data", {}).get("activeTargets", [])
    matches = [
        item
        for item in targets
        if item.get("labels", {}).get("job") == TARGET_JOB
        and item.get("labels", {}).get("family") == family
    ]
    return len(matches) == 1 and matches[0].get("health") == "up"


def refresh_prometheus_target_absent(
    *,
    timeout: float,
    expected_baseline_target_count: int,
) -> dict[str, Any]:
    started = time.monotonic()
    reload_prometheus()
    if wait_until(
        lambda: prometheus_cleanup_restored(expected_baseline_target_count),
        timeout=timeout,
    ):
        return {
            "restored": True,
            "elapsed_seconds": time.monotonic() - started,
            "reload_count": 1,
            "container_restart_count": 0,
            "prometheus_baseline": prometheus_health(),
        }
    raise S7RuntimeError("s7_prometheus_target_cleanup_timeout")


def prometheus_cleanup_restored(expected_baseline_target_count: int) -> bool:
    try:
        return prometheus_target_count() == 0 and prometheus_baseline_matches(
            prometheus_health(),
            expected_target_count=expected_baseline_target_count,
        )
    except (requests.RequestException, ValueError):
        return False


def prometheus_baseline_matches(
    health: dict[str, Any],
    *,
    expected_target_count: int,
) -> bool:
    return (
        int(health.get("target_count", -1)) == expected_target_count
        and int(health.get("up_count", -1)) == expected_target_count
        and health.get("all_up") is True
    )


def cleanup_contract_passed(
    cleanup: dict[str, Any],
    *,
    expected_baseline_target_count: int,
) -> bool:
    target_cleanup = cleanup.get("s7_target_cleanup") or {}
    return (
        cleanup.get("holder_uid_exact") is True
        and cleanup.get("holder_image_exact") is True
        and cleanup.get("holder_replicas_exact") is True
        and cleanup.get("source_model_sha256_exact") is True
        and cleanup.get("source_candidate_exact") is True
        and cleanup.get("source_cuda_inference") is True
        and cleanup.get("gpu_lease_zero") is True
        and target_cleanup.get("restored") is True
        and prometheus_baseline_matches(
            cleanup.get("prometheus_baseline") or {},
            expected_target_count=expected_baseline_target_count,
        )
    )


def prometheus_target_count() -> int:
    try:
        payload = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=5).json()
    except (requests.RequestException, ValueError):
        return -1
    targets = payload.get("data", {}).get("activeTargets", [])
    return sum(item.get("labels", {}).get("job") == TARGET_JOB for item in targets)


def wait_until(predicate: Callable[[], bool], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(1)
    return False


def prometheus_health() -> dict[str, Any]:
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return {"target_count": 0, "up_count": 0, "all_up": False}
    targets = payload.get("data", {}).get("activeTargets", [])
    baseline = [item for item in targets if item.get("labels", {}).get("job") != TARGET_JOB]
    return {
        "target_count": len(baseline),
        "up_count": sum(item.get("health") == "up" for item in baseline),
        "all_up": bool(baseline) and all(item.get("health") == "up" for item in baseline),
    }


def public_asset_identity(assets: dict[str, AssetSpec]) -> dict[str, Any]:
    return {
        family: {
            "manifest_sha256": asset.manifest_sha256,
            "model_artifact_sha256": asset.model_artifact_sha256 or asset.adapter_sha256,
            "model_revision": asset.model_revision,
            "data_identity_sha256": asset.data_identity_sha256 or asset.dataset_version,
            "quantization": asset.quantization,
        }
        for family, asset in assets.items()
    }


def capture_asset_provenance(
    *, root: Path, suite_root: Path, assets: dict[str, AssetSpec]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    output_root = suite_root / "asset-provenance"
    output_root.mkdir(parents=True, exist_ok=True)
    for family, relative in SCENARIO_CONTRACT_PATHS.items():
        contract_path = root / relative
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        dataset = dict(contract.get("dataset", {}))
        asset = assets[family]
        cache_roots = (
            [asset.model_artifact] if family == "image" else [asset.base_model, asset.adapter]
        )
        cache_entries: list[dict[str, Any]] = []
        for cache_root in cache_roots:
            if cache_root is None:
                continue
            paths = [cache_root] if cache_root.is_file() else sorted(cache_root.rglob("*"))
            for path in paths:
                if not path.is_file():
                    continue
                cache_entries.append(
                    {
                        "scope": "model_artifact" if cache_root.is_file() else cache_root.name,
                        "path": path.name
                        if cache_root.is_file()
                        else path.relative_to(cache_root).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": file_sha256(path),
                    }
                )
        cache_entries.sort(key=lambda item: (item["scope"], item["path"]))
        payload = {
            "schema_version": "evm.s7_asset_provenance.v1",
            "family": family,
            "scenario_contract_path": relative,
            "scenario_contract_sha256": file_sha256(contract_path),
            "dataset": {
                key: dataset[key]
                for key in (
                    "dataset_id",
                    "dataset_version",
                    "source_url",
                    "source_revision",
                    "license_id",
                    "license_url",
                    "usage_policy",
                )
            },
            "runtime_manifest_sha256": asset.manifest_sha256,
            "model": {
                "repository": asset.model_repository or asset.candidate_id,
                "revision": asset.model_revision or asset.model_artifact_sha256,
                "artifact_sha256": asset.model_artifact_sha256 or asset.adapter_sha256,
                "source_commit": asset.model_source_commit,
                "quantization": asset.quantization,
            },
            "cache_manifest": {
                "file_count": len(cache_entries),
                "total_bytes": sum(int(item["bytes"]) for item in cache_entries),
                "aggregate_sha256": canonical_sha256(cache_entries),
                "entries": cache_entries,
            },
        }
        canonical_write(output_root / f"{family}.json", payload)
        result[family] = payload
    return result


def public_asset_provenance(provenance: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        family: {
            "scenario_contract_path": payload["scenario_contract_path"],
            "scenario_contract_sha256": payload["scenario_contract_sha256"],
            "dataset": payload["dataset"],
            "runtime_manifest_sha256": payload["runtime_manifest_sha256"],
            "model": payload["model"],
            "cache_manifest": {
                key: payload["cache_manifest"][key]
                for key in ("file_count", "total_bytes", "aggregate_sha256")
            },
        }
        for family, payload in provenance.items()
    }


def public_input_catalog(catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        family: {
            key: (len(value) if isinstance(value, list) else {"request_class": value.request_class})
            for key, value in values.items()
        }
        for family, values in catalog.items()
    }


def private_evidence_index(
    root: Path, *, manifest_snapshot_contract: dict[str, Any] | None = None
) -> dict[str, Any]:
    artifacts = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "private-evidence-index.json":
            continue
        artifacts.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    if manifest_snapshot_contract is None:
        return {
            "schema_version": "evm.s7_private_evidence_index.v1",
            "artifacts": artifacts,
            "aggregate_sha256": canonical_sha256(artifacts),
            "generated_at": utc_now(),
        }
    return {
        "schema_version": "evm.s7_private_evidence_index.v2",
        "suite_id": root.name,
        "manifest_snapshot_contract": manifest_snapshot_contract,
        "manifest_snapshot_binding_sha256": manifest_snapshot_binding_sha256(
            manifest_snapshot_contract
        ),
        "artifacts": artifacts,
        "aggregate_sha256": canonical_sha256(artifacts),
        "generated_at": utc_now(),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not rows:
        raise S7RuntimeError(f"s7_manifest_empty:{path.name}")
    return rows


def visa_image_path(data_root: Path, record: dict[str, Any]) -> Path:
    relative = str(dict(record.get("metadata", {})).get("relative_path") or "")
    path = data_root / "data/raw/industrial/visa" / relative
    if not path.is_file() or file_sha256(path) != record.get("content_sha256"):
        raise S7RuntimeError(f"s7_image_identity_mismatch:{record.get('sample_id')}")
    return path


def image_label(record: dict[str, Any]) -> str:
    return "anomaly" if str(record.get("label_type")) == "anomaly" else "normal"


def trace_id_from_header(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split("-")
    return parts[1] if len(parts) == 4 else None


def canonical_write(path: Path, payload: Any) -> dict[str, Any]:
    return publish_exclusive_atomic_bytes(
        path,
        (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"),
    )


def restore_runtime_state(
    *,
    holder: HolderSnapshot,
    holder_scaled_down: bool,
    target_path: Path,
    prior_target: bytes | None,
    owned_lease: GpuLease | None,
) -> dict[str, Any]:
    active = read_active_gpu_lease()
    if active is not None and active.state == "active":
        if owned_lease is None or (
            active.run_id,
            active.lease_id,
            active.fencing_token,
        ) != (
            owned_lease.run_id,
            owned_lease.lease_id,
            owned_lease.fencing_token,
        ):
            raise S7ManualInterventionRequired(
                "s7_restore_refuses_unowned_gpu_lease",
                process_evidence={
                    "residual_process_count": 0,
                    "unowned_active_lease": True,
                    "automatic_retry_count": 0,
                    "subsequent_probe_after_residual": 0,
                },
            )
        release_scale_validation_gpu_lease(
            run_id=owned_lease.run_id,
            lease_id=owned_lease.lease_id,
            fencing_token=owned_lease.fencing_token,
            reason="S7 fail-closed final cleanup",
        )
    if holder_scaled_down:
        scale_holder(holder, replicas=holder.replicas, require_ready=True)
    restored_file_sd = restore_file_sd_target_exact(target_path, prior_target)
    target_cleanup = refresh_prometheus_target_absent(
        timeout=45,
        expected_baseline_target_count=EXPECTED_BASELINE_TARGET_COUNT,
    )
    target_cleanup["file_sd_exact"] = restored_file_sd
    return target_cleanup


def lifecycle_followup_policy(
    manual_latch_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    latched = manual_latch_evidence is not None
    return {
        "manual_latch": latched,
        "automatic_restore_allowed": not latched,
        "subsequent_service_probe_allowed": not latched,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
    }


def lease_checkpoint(lease: GpuLease, *, stage: str) -> dict[str, Any]:
    return {
        "schema_version": "evm.s7_gpu_lease_checkpoint.v1",
        "stage": stage,
        "run_id": lease.run_id,
        "lease_id": lease.lease_id,
        "fencing_token_sha256": hashlib.sha256(lease.fencing_token.encode("utf-8")).hexdigest(),
        "scenario_id": lease.scenario_id,
        "model_family": lease.model_family,
        "lease_purpose": lease.lease_purpose,
        "source_commit": lease.source_commit,
        "owner_pid": lease.owner_pid,
        "acquired_at": lease.acquired_at,
        "expires_at": lease.expires_at,
        "state": lease.state,
        "released_at": lease.released_at,
        "release_reason": lease.release_reason,
    }


def failure_seal_payload(
    *,
    suite_id: str,
    stage: str,
    error: BaseException,
    manual_intervention_required: bool,
    process_evidence: dict[str, Any] | None,
    pre_mutation_checkpoint: dict[str, Any] | None,
    restore_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": "evm.s7_atomic_failure_seal.v1",
        "suite_id": suite_id,
        "status": "failed",
        "verdict": "zero_credit",
        "acceptance_credit": False,
        "failure_stage": stage,
        "exception": {"type": type(error).__name__, "message": str(error)},
        "manual_intervention_required": manual_intervention_required,
        "automatic_retry_count": 0,
        "success_publication_count": 0,
        "completion_marker_created": False,
        "process_evidence": process_evidence,
        "pre_mutation_checkpoint": pre_mutation_checkpoint,
        "restore_checkpoint": restore_checkpoint,
        "recorded_at": utc_now(),
    }


def publish_failure_seal(suite_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    primary = suite_root / "failure-seal.json"
    try:
        return canonical_write(primary, payload)
    except FileExistsError:
        existing = file_state(primary)
        amendment = {
            "schema_version": "evm.s7_atomic_failure_amendment.v1",
            "suite_id": payload.get("suite_id"),
            "status": "failed",
            "verdict": "zero_credit",
            "acceptance_credit": False,
            "primary_failure_seal": existing,
            "followup_failure": payload,
            "automatic_retry_count": 0,
            "completion_marker_created": False,
            "recorded_at": utc_now(),
        }
        amendment_path = suite_root / f"failure-amendment-{uuid4().hex}.json"
        return canonical_write(amendment_path, amendment)


def write_public_json_exclusive(path: Path, payload: Any) -> dict[str, Any]:
    return publish_exclusive_atomic_bytes(path, canonical_public_json_bytes(payload))


def replace_mutable_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.s7-target")
    raw = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "bytes": 0, "sha256": None}
    if path.is_symlink() or not path.is_file():
        raise S7RuntimeError(f"s7_file_state_unsafe:{path.name}")
    raw = path.read_bytes()
    return {
        "exists": True,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def restore_file_sd_target_exact(path: Path, prior: bytes | None) -> dict[str, Any]:
    expected = {
        "exists": prior is not None,
        "bytes": len(prior or b""),
        "sha256": hashlib.sha256(prior).hexdigest() if prior is not None else None,
    }
    if prior is None:
        if path.exists():
            path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.s7-restore")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(prior)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    observed = file_state(path)
    if observed != expected:
        raise S7RuntimeError("s7_file_sd_exact_restore_failed")
    return observed


def kubectl_json(command: list[str]) -> dict[str, Any]:
    payload = json.loads(run_checked(command, timeout=30).stdout)
    if not isinstance(payload, dict):
        raise S7RuntimeError("s7_kubectl_payload_invalid")
    return payload


def run_checked(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())

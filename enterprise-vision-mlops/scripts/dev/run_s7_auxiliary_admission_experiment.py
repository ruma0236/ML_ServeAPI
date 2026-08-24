from __future__ import annotations

import argparse
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
from evm.scale_validation.evidence import write_public_json  # noqa: E402
from evm.scale_validation.s7_evidence import (  # noqa: E402
    SMOKE_CLAIM_BOUNDARY,
    project_profile,
    source_git_identity,
)
from evm.scale_validation.s7_runtime import (  # noqa: E402
    S7RuntimeConfig,
    S7RuntimeError,
    analyze_s7_profiles,
    canonical_sha256,
    file_sha256,
    host_image_data_environment,
    profile_family,
    restore_file_sd_target,
    source_identity,
)


PROMETHEUS_URL = "http://127.0.0.1:9090"
TARGET_JOB = "evm-s7-family"
EXPECTED_BASELINE_TARGET_COUNT = 5
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
        default=ROOT / "docs/status/evidence/s7-auxiliary-admission-experiment.json",
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
    if not args.diagnostic and families != ("image", "vlm", "llm"):
        raise S7RuntimeError("s7_acceptance_requires_all_families")
    if not args.cuda_python.is_file():
        raise S7RuntimeError("s7_cuda_python_missing")
    suite_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{revision[:8]}"
    suite_root = args.private_root / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    (suite_root / "profiles").mkdir()
    assets = load_assets(args.config, args.data_root)
    runtime_asset_overrides: dict[str, dict[str, Any]] = {}
    if args.diagnostic and args.acknowledge_diagnostic_manifest_drift:
        assets, runtime_asset_overrides = resolve_diagnostic_manifest_drift(assets)
    validate_assets(assets)
    asset_provenance = capture_asset_provenance(
        root=args.root,
        suite_root=suite_root,
        assets=assets,
    )
    holder = capture_holder()
    source_before = source_serving_probe(holder, data_root=args.data_root)
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
    records = {family: read_jsonl(assets[family].manifest) for family in families}
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
            "asset_provenance": public_asset_provenance(asset_provenance),
            "input_catalog_sha256": canonical_sha256(public_input_catalog(input_catalog)),
            "started_at": utc_now(),
        },
    )
    profile_results: list[dict[str, Any]] = []
    ready_identities: dict[str, dict[str, Any]] = {}
    failed: dict[str, Any] | None = None
    failure_exc: Exception | None = None
    family_cleanup: list[dict[str, Any]] = []
    final_target_cleanup: dict[str, Any] | None = None
    holder_scaled_down = False
    try:
        scale_holder(holder, replicas=0, require_ready=False)
        holder_scaled_down = True
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
                if service is not None:
                    stop_service(service)
                released = release_scale_validation_gpu_lease(
                    run_id=lease.run_id,
                    lease_id=lease.lease_id,
                    fencing_token=lease.fencing_token,
                    reason=f"S7 {family} family profiles completed",
                )
                family_cleanup.append(
                    {
                        "family": family,
                        "lease_state": released.state,
                        "elapsed_seconds": time.monotonic() - family_started,
                        "service_process_stopped": service is None
                        or service.process.poll() is not None,
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
            "action": "Stopped new family load and entered exact holder/lease cleanup.",
            "recorded_at": utc_now(),
        }
        canonical_write(suite_root / "failed-attempt.json", failed)
        failure_exc = exc
    finally:
        active = read_active_gpu_lease()
        if active is not None and active.state == "active" and active.run_id.startswith("s7-"):
            release_scale_validation_gpu_lease(
                run_id=active.run_id,
                lease_id=active.lease_id,
                fencing_token=active.fencing_token,
                reason="S7 fail-closed final cleanup",
            )
        if holder_scaled_down:
            scale_holder(holder, replicas=holder.replicas, require_ready=True)
        restore_file_sd_target(target_path, prior_target)
        try:
            final_target_cleanup = refresh_prometheus_target_absent(
                timeout=45,
                expected_baseline_target_count=EXPECTED_BASELINE_TARGET_COUNT,
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
                    "action": "Prometheus cleanup did not restore the frozen baseline.",
                    "recorded_at": utc_now(),
                }
    source_after = source_serving_probe(holder, data_root=args.data_root)
    cleanup = {
        "schema_version": "evm.s7_cleanup.v1",
        "holder_uid_exact": capture_holder().uid == holder.uid,
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
        failed["action"] = "Fail-closed cleanup completed; no acceptance credit was assigned."
        failed["cleanup"] = {
            "source_model_sha256_exact": cleanup["source_model_sha256_exact"],
            "source_candidate_exact": cleanup["source_candidate_exact"],
            "source_cuda_inference": cleanup["source_cuda_inference"],
            "gpu_lease_zero": cleanup["gpu_lease_zero"],
            "prometheus_baseline": cleanup["prometheus_baseline"],
        }
        canonical_write(suite_root / "failed-attempt.json", failed)
        raise failure_exc
    if args.diagnostic:
        index = private_evidence_index(suite_root)
        canonical_write(suite_root / "private-evidence-index.json", index)
        errors: list[str] = []
        projected = [
            project_profile(item, config=config, errors=errors)
            for item in profile_results
        ]
        if errors or set(ready_identities) != set(families):
            raise S7RuntimeError("s7_diagnostic_projection_failed:" + ",".join(errors))
        index_raw = (suite_root / "private-evidence-index.json").read_bytes()
        public = {
            "schema_version": "evm.s7_current_revision_cuda_smoke.v2",
            "status": "verified",
            "verdict": "passed",
            "acceptance_credit": False,
            "suite_id": suite_id,
            "source_identity": {
                "revision": revision,
                "branch": branch,
                "config_sha256": config.sha256,
                "git_blobs": source_git_identity(args.root.parent, revision),
                "runtime_asset_overrides": runtime_asset_overrides,
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
                "trace_identity_complete": all(
                    item["trace_complete"] for item in projected
                ),
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
                "prometheus_baseline_target_count": cleanup["prometheus_baseline"][
                    "target_count"
                ],
                "prometheus_baseline_up_count": cleanup["prometheus_baseline"][
                    "up_count"
                ],
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
        write_public_json(args.output, public)
        print(json.dumps({"status": "diagnostic_passed", "suite_root": str(suite_root)}))
        return 0
    index = private_evidence_index(suite_root)
    canonical_write(suite_root / "private-evidence-index.json", index)
    errors: list[str] = []
    projected = [project_profile(item, config=config, errors=errors) for item in profile_results]
    analysis = analyze_s7_profiles(projected, config)
    if errors or analysis["runtime_verdict"] != "passed":
        canonical_write(
            suite_root / "acceptance-failure.json",
            {"errors": errors, "analysis": analysis, "acceptance_credit": False},
        )
        raise S7RuntimeError("s7_acceptance_failed:" + ",".join(errors))
    index_raw = (suite_root / "private-evidence-index.json").read_bytes()
    public = {
        "schema_version": "evm.s7_auxiliary_admission_experiment.v1",
        "status": "verified",
        "verdict": "passed",
        "suite_id": suite_id,
        "source_identity": {
            "revision": revision,
            "branch": branch,
            "config_sha256": config.sha256,
            "git_blobs": source_git_identity(args.root.parent, revision),
        },
        "runtime_contract": config.public_dict(),
        "profiles": projected,
        "analysis": analysis,
        "private_evidence": {
            "artifact_count": len(index["artifacts"]),
            "total_bytes": sum(int(item["bytes"]) for item in index["artifacts"]),
            "aggregate_sha256": index["aggregate_sha256"],
            "index_sha256": hashlib.sha256(index_raw).hexdigest(),
            "location": "outside_git_private_evidence_root",
        },
        "cleanup_summary": {
            "source_serving_ready": cleanup["source_model_sha256_exact"]
            and cleanup["source_cuda_inference"],
            "gpu_lease_zero": cleanup["gpu_lease_zero"],
            "family_count": len(family_cleanup),
            "prometheus_up": cleanup["prometheus_baseline"]["all_up"],
        },
        "failed_attempts": [] if failed is None else [failed],
        "claim_boundary": config.claim_boundary,
        "generated_at": utc_now(),
    }
    write_public_json(args.output, public)
    print(
        json.dumps({"status": "passed", "output": str(args.output), "suite_root": str(suite_root)})
    )
    return 0


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
        "prometheus_refresh_restart_used": bool(prometheus_recovery["restart_used"]),
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
    )
    return ServiceProcess(family, process, handle, log_path, f"http://127.0.0.1:{asset.port}")


def stop_service(service: ServiceProcess) -> None:
    try:
        process = psutil.Process(service.process.pid)
        children = process.children(recursive=True)
    except psutil.Error:
        children = []
    if service.process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(service.process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        else:
            os.killpg(os.getpgid(service.process.pid), signal.SIGTERM)
        try:
            service.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            service.process.kill()
            service.process.wait(timeout=10)
    for child in children:
        try:
            child.wait(timeout=2)
        except psutil.Error:
            pass
    service.log_handle.flush()
    service.log_handle.close()


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
        "linear_4bit_module_count": int(
            quantization["linear_4bit_module_count"]
        ),
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


def source_serving_probe(holder: HolderSnapshot, *, data_root: Path) -> dict[str, Any]:
    ready = requests.get("http://127.0.0.1:30800/ready", timeout=10)
    ready.raise_for_status()
    payload = ready.json()
    if payload.get("model_sha256") != holder.model_sha256 or payload.get("device") != "cuda":
        raise S7RuntimeError("s7_source_serving_probe_failed")
    manifest = read_jsonl(
        data_root / "data/validated/visa/curation/curated_eval_manifest.jsonl"
    )
    response = requests.post(
        "http://127.0.0.1:30800/predict",
        json={"image_uri": manifest[0]["image_uri"]},
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
    canonical_write(
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
    if wait_until(lambda: prometheus_target_up(family), timeout=min(5.0, timeout)):
        return {"elapsed_seconds": time.monotonic() - started, "restart_used": False}
    restart_prometheus()
    remaining = max(1.0, timeout - (time.monotonic() - started))
    if wait_until(lambda: prometheus_target_up(family), timeout=remaining):
        return {"elapsed_seconds": time.monotonic() - started, "restart_used": True}
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
        timeout=min(5.0, timeout),
    ):
        return {
            "restored": True,
            "elapsed_seconds": time.monotonic() - started,
            "restart_used": False,
            "prometheus_baseline": prometheus_health(),
        }
    restart_prometheus()
    remaining = max(1.0, timeout - (time.monotonic() - started))
    if wait_until(
        lambda: prometheus_cleanup_restored(expected_baseline_target_count),
        timeout=remaining,
    ):
        return {
            "restored": True,
            "elapsed_seconds": time.monotonic() - started,
            "restart_used": True,
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


def restart_prometheus() -> None:
    run_checked(["docker", "restart", "evm-prometheus"], timeout=60)


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
            [asset.model_artifact]
            if family == "image"
            else [asset.base_model, asset.adapter]
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
                        "scope": "model_artifact"
                        if cache_root.is_file()
                        else cache_root.name,
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


def public_asset_provenance(
    provenance: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
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


def private_evidence_index(root: Path) -> dict[str, Any]:
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
    return {
        "schema_version": "evm.s7_private_evidence_index.v1",
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


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    )


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

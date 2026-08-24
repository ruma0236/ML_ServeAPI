from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.control_panel.transactional_store import canonical_digest  # noqa: E402
from evm.scale_validation.evidence import write_public_json  # noqa: E402
from evm.scale_validation.s1_runtime import (  # noqa: E402
    canonical_write,
    sha256_file,
    utc_now,
)
from evm.scale_validation.s8_runtime import (  # noqa: E402
    S8RuntimeConfig,
    analyze_fault_results,
    git_blob_identity,
)


PUBLIC_EVIDENCE = ROOT / "docs/status/evidence/s8-dependency-soak-experiment.json"
CONFIG = ROOT / "configs/s8_dependency_soak_v6.toml"
PROJECTION_PATH = Path("scripts/dev/reproject_s8_dependency_soak_evidence.py")
AMENDMENT_ID = "s8-non-finite-histogram-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproject accepted S8 raw evidence into strict finite JSON."
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--projection-revision", required=True)
    parser.add_argument("--evidence", type=Path, default=PUBLIC_EVIDENCE)
    parser.add_argument("--config", type=Path, default=CONFIG)
    return parser.parse_args()


def read_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"mapping_required:{path.name}")
    return payload


def normalize_histogram_overflow(value: Any, path: str = "root") -> list[str]:
    amended: list[str] = []
    if isinstance(value, dict):
        for key, item in list(value.items()):
            child = f"{path}.{key}"
            if isinstance(item, float) and not math.isfinite(item):
                if key != "observed_upper_bound" or not math.isinf(item):
                    raise RuntimeError(f"unsupported_non_finite:{child}")
                value[key] = None
                value["observed_upper_bound_status"] = "overflowed_finite_buckets"
                amended.append(child)
                continue
            amended.extend(normalize_histogram_overflow(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            amended.extend(normalize_histogram_overflow(item, f"{path}[{index}]"))
    elif isinstance(value, float) and not math.isfinite(value):
        raise RuntimeError(f"unsupported_non_finite:{path}")
    return amended


def copy_original(source: Path, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {
        "source": source.name,
        "preserved_path": target.as_posix(),
        "sha256": sha256_file(target),
        "bytes": target.stat().st_size,
    }


def private_index(root: Path) -> dict[str, Any]:
    excluded = {"private-evidence-index.json", "suite-summary-private.json"}
    artifacts = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in excluded
    ]
    return {
        "schema_version": "evm.s2_private_evidence_index.v1",
        "generated_at": utc_now(),
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "aggregate_sha256": canonical_digest(artifacts),
    }


def require_revision(revision: str) -> None:
    observed = subprocess.run(
        ["git", "cat-file", "-t", revision],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if observed != "commit":
        raise RuntimeError(f"projection_revision_not_commit:{revision}")


def main() -> int:
    args = parse_args()
    private_root = args.private_root.resolve()
    evidence_path = args.evidence.resolve()
    require_revision(args.projection_revision)
    amendment_root = private_root / "amendments" / AMENDMENT_ID
    if amendment_root.exists():
        raise RuntimeError(f"amendment_already_exists:{AMENDMENT_ID}")

    original_index = private_root / "private-evidence-index.json"
    original_summary = private_root / "suite-summary-private.json"
    for required in (evidence_path, original_index, original_summary):
        if not required.is_file():
            raise RuntimeError(f"required_evidence_missing:{required.name}")

    preserved = [
        copy_original(
            evidence_path,
            amendment_root / "original-public-experiment.json",
        ),
        copy_original(
            original_index,
            amendment_root / "original-private-evidence-index.json",
        ),
        copy_original(
            original_summary,
            amendment_root / "original-suite-summary-private.json",
        ),
    ]
    payload = read_mapping(evidence_path)
    normalized_private: list[dict[str, Any]] = []
    for repetition in range(1, 4):
        relative = (
            Path("faults")
            / "retry-budget"
            / f"repetition-{repetition}"
            / "profile-result-private.json"
        )
        path = private_root / relative
        backup = amendment_root / "original-private" / relative
        original = copy_original(path, backup)
        preserved.append(original)
        private = read_mapping(path)
        amended_paths = normalize_histogram_overflow(private, "private")
        expected = ["private.metrics.summary.queue_wait_seconds.observed_upper_bound"]
        if amended_paths != expected:
            raise RuntimeError(f"unexpected_private_amendment_paths:r{repetition}:{amended_paths}")
        canonical_write(path, private)
        normalized_private.append(
            {
                "repetition": repetition,
                "path": relative.as_posix(),
                "original_sha256": original["sha256"],
                "normalized_sha256": sha256_file(path),
                "normalized_bytes": path.stat().st_size,
                "amended_paths": amended_paths,
            }
        )
        for result in payload.get("fault_results", []):
            if (
                result.get("profile_id") == "retry-budget"
                and int(result.get("repetition", 0)) == repetition
            ):
                result["metrics"] = dict(private.get("metrics", {})).get("summary", {})
                result["private_evidence_sha256"] = sha256_file(path)
                break
        else:
            raise RuntimeError(f"public_fault_result_missing:r{repetition}")

    config = S8RuntimeConfig.from_path(args.config)
    payload["fault_analysis"] = analyze_fault_results(
        [dict(item) for item in payload.get("fault_results", [])], config
    )
    public_amendments = normalize_histogram_overflow(payload, "public")
    if public_amendments:
        raise RuntimeError(f"unbound_public_non_finite:{public_amendments}")
    projection_blob = git_blob_identity(ROOT, args.projection_revision, PROJECTION_PATH)
    payload["source_identity"]["evidence_projection"] = {
        "revision": args.projection_revision,
        "script": projection_blob,
        "runtime_semantics_changed": False,
    }
    payload["evidence_amendments"] = [
        {
            "amendment_id": AMENDMENT_ID,
            "reason": (
                "Prometheus histogram observations exceeded every finite bucket; "
                "the prior projection serialized the +Inf bucket as non-standard JSON."
            ),
            "normalization": ("observed_upper_bound=null with overflowed_finite_buckets status"),
            "runtime_semantics_changed": False,
            "runtime_results_reused": True,
            "normalized_private_file_count": len(normalized_private),
        }
    ]
    amendment_manifest = {
        "schema_version": "evm.s8_evidence_amendment.v1",
        "amendment_id": AMENDMENT_ID,
        "generated_at": utc_now(),
        "projection_identity": payload["source_identity"]["evidence_projection"],
        "preserved_originals": preserved,
        "normalized_private": normalized_private,
        "runtime_semantics_changed": False,
        "claim": (
            "Only non-finite histogram overflow representation and evidence hashes "
            "changed; the 21 fault and three soak executions were not rerun."
        ),
    }
    canonical_write(amendment_root / "amendment-manifest.json", amendment_manifest)
    index = private_index(private_root)
    canonical_write(original_index, index)
    payload["private_evidence"] = {
        "artifact_count": index["artifact_count"],
        "aggregate_sha256": index["aggregate_sha256"],
        "location": "outside_git_private_evidence_root",
    }
    payload["generated_at"] = utc_now()
    write_public_json(evidence_path, payload)
    canonical_write(original_summary, payload)
    print(
        json.dumps(
            {
                "amendment_id": AMENDMENT_ID,
                "private_artifact_count": index["artifact_count"],
                "private_aggregate_sha256": index["aggregate_sha256"],
                "normalized_private_file_count": len(normalized_private),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

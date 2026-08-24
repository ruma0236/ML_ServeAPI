from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.evidence import write_public_json  # noqa: E402
from evm.scale_validation.s7_evidence import (  # noqa: E402
    HISTORICAL_EXPERIMENT_COMMIT,
    HISTORICAL_EXPERIMENT_PATH,
    asset_contract_projection,
    git_blob_identity,
    project_profile,
    source_git_identity,
    validate_private_evidence,
)
from evm.scale_validation.s7_runtime import (  # noqa: E402
    S7RuntimeConfig,
    analyze_s7_profiles,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproject immutable S7 raw evidence under the corrected scope contract."
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/"
            "scale_validation/private/s7/20260823T192119Z-6704349a"
        ),
    )
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/s7_family_admission.toml"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "docs/status/evidence/s7-auxiliary-admission-reprojection.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    git_root = Path(
        run_git("rev-parse", "--show-toplevel", cwd=ROOT)
    ).resolve()
    projection_revision = run_git("rev-parse", "HEAD", cwd=ROOT)
    branch = run_git("branch", "--show-current", cwd=ROOT)
    if branch != "codex/distributed-scale-validation-plan":
        raise RuntimeError("s7_reprojection_branch_invalid")
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode or subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT
    ).returncode:
        raise RuntimeError("s7_reprojection_requires_clean_tracked_worktree")

    historical_raw = subprocess.run(
        ["git", "show", f"{HISTORICAL_EXPERIMENT_COMMIT}:{HISTORICAL_EXPERIMENT_PATH}"],
        cwd=git_root,
        check=True,
        capture_output=True,
    ).stdout
    historical = json.loads(historical_raw)
    execution_revision = str(
        dict(historical.get("source_identity", {})).get("revision") or ""
    )
    config = S7RuntimeConfig.from_path(args.config)
    errors: list[str] = []
    private = validate_private_evidence(args.private_root, errors)
    profiles = [
        project_profile(item, config=config, errors=errors)
        for item in private.get("profiles", [])
    ]
    analysis = analyze_s7_profiles(profiles, config)
    if errors or analysis.get("runtime_verdict") != "passed":
        raise RuntimeError(
            "s7_reprojection_failed:" + ",".join(errors or ["acceptance"])
        )
    payload = {
        "schema_version": "evm.s7_auxiliary_admission_reprojection.v2",
        "status": "verified",
        "verdict": "passed",
        "suite_id": historical["suite_id"],
        "source_identity": {
            "execution_revision": execution_revision,
            "projection_revision": projection_revision,
            "branch": branch,
            "config_sha256": config.sha256,
            "execution_git_blobs": source_git_identity(
                git_root, execution_revision
            ),
            "projection_git_blobs": source_git_identity(
                git_root, projection_revision
            ),
            "historical_experiment": git_blob_identity(
                git_root,
                HISTORICAL_EXPERIMENT_COMMIT,
                HISTORICAL_EXPERIMENT_PATH,
            ),
        },
        "runtime_contract": historical["runtime_contract"],
        "profiles": profiles,
        "analysis": analysis,
        "asset_contracts": asset_contract_projection(
            config=config,
            git_root=git_root,
            revision=projection_revision,
        ),
        "private_evidence": private["summary"],
        "cleanup_summary": historical["cleanup_summary"],
        "failed_attempts": historical["failed_attempts"],
        "historical_projection_note": (
            "The immutable 36-run private matrix is reprojected without rerun. "
            "Selected/admitted starvation is separated from intentional over-limit "
            "pre-admission rejection; the original v1 experiment and closure remain "
            "addressable in Git history."
        ),
        "claim_boundary": config.claim_boundary,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_public_json(args.output, payload)
    print(
        json.dumps(
            {
                "status": "passed",
                "output": str(args.output),
                "profiles": len(profiles),
                "completed": analysis["outcome_accounting"]["completed_requests"],
                "intentional_pre_admission_rejections": analysis[
                    "outcome_accounting"
                ]["intentional_pre_admission_rejections"],
            },
            sort_keys=True,
        )
    )
    return 0


def run_git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())

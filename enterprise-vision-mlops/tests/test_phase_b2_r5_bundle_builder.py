from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.dev import prepare_x1_phase_b2_r5_bundle as builder


def source_identity() -> dict[str, object]:
    return {
        "revision": "a" * 40,
        "tree": "b" * 40,
        "branch": "codex/distributed-scale-validation-plan",
        "origin_revision": "a" * 40,
        "remote_revision": "a" * 40,
        "tracked": 0,
        "untracked": 4244,
    }


def runtime(tmp_path: Path) -> dict[str, dict[str, object]]:
    return {
        name: {
            "path": str(tmp_path / f"{name}.txt"),
            "sha256": str(index) * 64,
            "blob_oid": str(index) * 40,
            "bytes": index,
        }
        for index, name in enumerate(builder.RUNTIME_PATHS, start=1)
    }


def build(tmp_path: Path, mode: str = "restore-only") -> dict[str, object]:
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    checkpoint_index = tmp_path / "checkpoint-index.json"
    checkpoint_index.write_text("{}", encoding="utf-8")
    return builder.build_manifest(
        mode=mode,
        run_id=f"r5-{mode}",
        source_identity=source_identity(),
        project_root=tmp_path,
        checkpoint=checkpoint,
        checkpoint_index=checkpoint_index,
        output_directory=tmp_path / "output",
        python_path=tmp_path / "python.exe",
        runtime=runtime(tmp_path),
    )


def test_manifest_uses_corrected_historical_b0_uid_and_exact_modes(tmp_path: Path) -> None:
    restore = build(tmp_path)
    fresh = build(tmp_path, "fresh")
    assert restore["expected_state"]["b0"]["uid"] == (  # type: ignore[index]
        "cfdab424-dcc5-4d5f-a46f-ae7530441ef4"
    )
    assert restore["call_contract"]["restore-only"]["compose_stop"] == 0  # type: ignore[index]
    assert fresh["call_contract"]["fresh"]["compose_stop"] == 1  # type: ignore[index]
    assert fresh["call_contract"]["fresh"]["wsl_shutdown"] == 0  # type: ignore[index]
    assert fresh["phase_b2_contract"]["windows_samples"] == 1800  # type: ignore[index]
    assert fresh["phase_b2_contract"]["wsl_samples"] == 1800  # type: ignore[index]


def test_checkpoint_validation_distinguishes_failure_and_restore_pass(tmp_path: Path) -> None:
    failure = tmp_path / "failure.json"
    failure.write_text(
        json.dumps(
            {
                "failure_only": True,
                "acceptance_credit": False,
                "success_marker_created": False,
            }
        ),
        encoding="utf-8",
    )
    assert builder.read_checkpoint(failure, "restore-only")["failure_only"]
    assert builder.read_checkpoint_index(failure, "restore-only")["failure_only"]
    with pytest.raises(builder.BundleBuildError, match="fresh_requires"):
        builder.read_checkpoint(failure, "fresh")

    restored = tmp_path / "restored.json"
    restored.write_text(
        json.dumps(
            {
                "restore_only_pass": True,
                "acceptance_credit": False,
                "completion_marker_created": False,
            }
        ),
        encoding="utf-8",
    )
    assert builder.read_checkpoint(restored, "fresh")["restore_only_pass"]
    assert builder.read_checkpoint_index(restored, "fresh")["restore_only_pass"]


def test_outer_and_bridge_have_one_call_marker_and_no_forbidden_command(tmp_path: Path) -> None:
    manifest = build(tmp_path)
    pins = runtime(tmp_path)
    bridge = builder.render_bridge(
        manifest_sha256="f" * 64,
        manifest=manifest,
        runtime=pins,
    )
    outer = builder.render_outer(bridge_sha256="e" * 64)
    assert outer.count("R5_BRIDGE_INVOKE_EXACTLY_ONCE") == 1
    assert bridge.count("R5_RUNNER_INVOKE_EXACTLY_ONCE") == 1
    assert "Get-FileHash -LiteralPath $outerPath" in outer
    assert "Get-Sha256 $OuterLauncherPath" in bridge
    combined = (outer + bridge).lower()
    for forbidden in (
        "taskkill",
        "terminatejobobject",
        "docker system prune",
        "reset-cluster",
        "wsl.exe --shutdown",
        "compose down",
        "compose up",
        "remove-item",
    ):
        assert forbidden not in combined


@pytest.mark.parametrize("kind", ["outer", "bridge"])
def test_rendered_powershell_ast_is_valid(tmp_path: Path, kind: str) -> None:
    manifest = build(tmp_path)
    text = (
        builder.render_outer(bridge_sha256="e" * 64)
        if kind == "outer"
        else builder.render_bridge(
            manifest_sha256="f" * 64,
            manifest=manifest,
            runtime=runtime(tmp_path),
        )
    )
    script = tmp_path / f"{kind}.ps1"
    script.write_text(text, encoding="utf-8")
    command = (
        "$tokens=$null;$errors=$null;"
        f"[void][Management.Automation.Language.Parser]::ParseFile('{script}',"
        "[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors|ForEach-Object ToString;exit 1}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_write_exclusive_rejects_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    builder.write_exclusive(path, b"one")
    with pytest.raises(builder.BundleBuildError, match="exists"):
        builder.write_exclusive(path, b"two")
    assert path.read_bytes() == b"one"

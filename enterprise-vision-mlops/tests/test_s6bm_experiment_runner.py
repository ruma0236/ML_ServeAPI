from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GIT_ROOT = ROOT.parent
RUNNER = ROOT / "scripts/dev/run_s8_v4_s6bm_experiment.py"
CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml"


def load_runner():
    spec = importlib.util.spec_from_file_location("s6bm_experiment_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_git_blob_hash_uses_parent_repository_root() -> None:
    runner = load_runner()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    repository_path = CONFIG.relative_to(GIT_ROOT).as_posix()
    blob = subprocess.run(
        ["git", "show", f"{revision}:{repository_path}"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert runner.git_blob_sha256(revision, CONFIG) == hashlib.sha256(blob).hexdigest()

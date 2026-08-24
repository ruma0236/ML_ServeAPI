from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import threading
import time
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


def test_send_batch_reuses_bounded_per_worker_sessions(monkeypatch) -> None:
    runner = load_runner()
    created = []
    seen: list[int] = []
    seen_lock = threading.Lock()

    class FakeSession:
        def __init__(self) -> None:
            self.closed = False
            created.append(self)

        def mount(self, _prefix: str, _adapter: object) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    def fake_send(_config, body, *, session=None):
        time.sleep(0.002)
        with seen_lock:
            seen.append(id(session))
        return {"request_id": body["request_id"]}

    monkeypatch.setattr(runner.requests, "Session", FakeSession)
    monkeypatch.setattr(runner, "send_request", fake_send)

    records, bodies = runner.send_batch(object(), "run-id", "batch", 24, 3)

    assert len(records) == len(bodies) == 24
    assert 1 <= len(created) <= 3
    assert set(seen) == {id(session) for session in created}
    assert all(session.closed for session in created)

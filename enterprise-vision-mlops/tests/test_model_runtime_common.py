from __future__ import annotations

import json
from pathlib import Path

from evm.model_runtime import common


def test_atomic_write_json_retries_transient_windows_reader_lock(tmp_path, monkeypatch) -> None:
    target = tmp_path / "worker.json"
    real_replace = Path.replace
    attempts = 0

    def transient_replace(source: Path, destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("simulated Windows reader lock")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", transient_replace)
    monkeypatch.setattr(common.time, "sleep", lambda _: None)

    common.atomic_write_json(target, {"status": "online"})

    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "online"}
    assert list(tmp_path.glob(".*.tmp")) == []

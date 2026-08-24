from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/dev/reproject_s8_dependency_soak_evidence.py"


def load_module():
    spec = importlib.util.spec_from_file_location("s8_reprojection", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_histogram_overflow_normalization_is_explicit_and_idempotent() -> None:
    module = load_module()
    payload = {"wait": {"observed_upper_bound": float("inf"), "max": 22.0}}

    amended = module.normalize_histogram_overflow(payload)
    repeated = module.normalize_histogram_overflow(payload)

    assert amended == ["root.wait.observed_upper_bound"]
    assert repeated == []
    assert payload["wait"] == {
        "observed_upper_bound": None,
        "observed_upper_bound_status": "overflowed_finite_buckets",
        "max": 22.0,
    }


def test_non_histogram_non_finite_value_fails_closed() -> None:
    module = load_module()

    with pytest.raises(RuntimeError, match="unsupported_non_finite"):
        module.normalize_histogram_overflow({"latency": float("nan")})


def test_existing_original_backup_is_not_overwritten(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "source.json"
    target = tmp_path / "preserved.json"
    source.write_bytes(b"new bytes")
    target.write_bytes(b"original bytes")

    observed = module.copy_original(source, target)

    assert target.read_bytes() == b"original bytes"
    assert observed["bytes"] == len(b"original bytes")

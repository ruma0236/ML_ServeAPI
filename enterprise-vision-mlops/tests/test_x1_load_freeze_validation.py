from __future__ import annotations

from pathlib import Path

import pytest

from evm.scale_validation.x1_load_freeze_validation import (
    X1LoadFreezeValidationError,
    _cleanup_passed,
    load_canonical_json,
)


def test_x1_load_freeze_loader_requires_canonical_lf_and_unique_keys(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(b'{"a":1,"b":2}\n')
    assert load_canonical_json(path) == {"a": 1, "b": 2}
    path.write_bytes(b'{"a":1,"a":1}\n')
    with pytest.raises(X1LoadFreezeValidationError, match="x1_load_freeze_duplicate_key"):
        load_canonical_json(path)
    path.write_bytes(b'{"a":1}\r\n')
    with pytest.raises(X1LoadFreezeValidationError, match="x1_load_freeze_canonical_lf"):
        load_canonical_json(path)


def test_x1_load_freeze_cleanup_is_recomputed_from_raw_fields() -> None:
    cleanup = {
        "b0_uid_exact": True,
        "b0_image_exact": True,
        "b0_ready_1_of_1": True,
        "b0_actual_cuda": True,
        "prometheus_5_of_5": True,
        "queues": {"active": 0, "leased": 0, "outcome_unknown": 0},
        "gpu_lease_absent": True,
        "runtime_absent": True,
        "database_schema_absent": True,
        "vram_restored": True,
    }
    assert _cleanup_passed(cleanup)
    cleanup["queues"] = {"active": 1, "leased": 0, "outcome_unknown": 0}
    assert not _cleanup_passed(cleanup)

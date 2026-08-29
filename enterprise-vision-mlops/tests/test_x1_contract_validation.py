from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evm.scale_validation.x1_contract_validation import (
    X1ContractValidationError,
    load_canonical_json,
    run_contract_mutations,
    validate_contract_files,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
CONFIG = ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml"
AMENDMENT = ROOT / "docs/status/evidence/s8-v4-x1-contract-amendment-v1.json"


def test_x1_contract_amendment_and_mutations_are_fail_closed() -> None:
    contract, amendment, mutation = validate_contract_files(
        config_path=CONFIG,
        amendment_path=AMENDMENT,
        source_root=ROOT,
        data_root=DATA_ROOT,
    )
    assert contract.public_snapshot()["credit_matrix_repetitions"] == 78
    assert mutation["positive_controls"] == 1
    assert mutation["negative_rejected"] == 17
    assert len(mutation["cases"]) == 17
    assert run_contract_mutations(contract, amendment) == mutation


def test_x1_contract_json_rejects_duplicate_or_noncanonical_bytes(tmp_path: Path) -> None:
    canonical = load_canonical_json(AMENDMENT)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":1}\n', encoding="ascii", newline="")
    with pytest.raises(X1ContractValidationError, match="x1_json_parse"):
        load_canonical_json(duplicate)

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(json.dumps(canonical, indent=2) + "\n", encoding="ascii", newline="")
    with pytest.raises(X1ContractValidationError, match="x1_json_not_canonical"):
        load_canonical_json(noncanonical)


def test_x1_amendment_mutation_is_not_accepted(tmp_path: Path) -> None:
    payload = copy.deepcopy(load_canonical_json(AMENDMENT))
    payload["preliminary_isolation"]["reuse_forbidden"] = False
    path = tmp_path / "amendment.json"
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
        newline="",
    )
    with pytest.raises(X1ContractValidationError, match="x1_contract_amendment_mismatch"):
        validate_contract_files(
            config_path=CONFIG,
            amendment_path=path,
            source_root=ROOT,
            data_root=DATA_ROOT,
        )

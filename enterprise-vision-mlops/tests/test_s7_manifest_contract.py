from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from evm.scale_validation.s7_evidence import validate_private_evidence
from evm.scale_validation.s7_manifest_contract import (
    MANIFEST_FAMILIES,
    S7ManifestContractError,
    build_trusted_manifest_envelope,
    canonical_sha256,
    classify_live_manifest_drift,
    create_run_scoped_manifest_snapshots,
    manifest_semantic_identity,
    manifest_snapshot_binding_sha256,
    publish_exclusive_atomic_bytes,
    validate_manifest_snapshot_contract,
    validate_trusted_manifest_envelope,
)


def _private_index(suite_root: Path, contract: dict[str, object]) -> dict[str, object]:
    artifacts = [
        {
            "path": path.relative_to(suite_root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(suite_root.rglob("*"))
        if path.is_file() and path.name != "private-evidence-index.json"
    ]
    return {
        "schema_version": "evm.s7_private_evidence_index.v2",
        "suite_id": suite_root.name,
        "manifest_snapshot_contract": contract,
        "manifest_snapshot_binding_sha256": manifest_snapshot_binding_sha256(contract),
        "artifacts": artifacts,
        "aggregate_sha256": canonical_sha256(artifacts),
        "generated_at": "2026-09-01T00:00:00Z",
    }


def _manifest(family: str, curated_at: str, *, label: str = "normal") -> bytes:
    records = [
        {
            "record_id": f"{family}-{index}",
            "content_sha256": hashlib.sha256(f"{family}-{index}".encode("utf-8")).hexdigest(),
            "label": label,
            "curation": {
                "curated_at": curated_at,
                "eval_promotion_state": "candidate",
            },
        }
        for index in range(2)
    ]
    return b"".join(
        (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8") for item in records
    )


def _create_contract(tmp_path: Path):
    suite_id = "20260901T000000Z-abcdef12"
    suite_root = tmp_path / suite_id
    suite_root.mkdir()
    source_root = tmp_path / "sources"
    source_root.mkdir()
    sources = {}
    expected = {}
    for family in MANIFEST_FAMILIES:
        path = source_root / f"{family}.jsonl"
        raw = _manifest(family, "2026-09-01T00:00:00Z")
        path.write_bytes(raw)
        sources[family] = path
        expected[family] = hashlib.sha256(raw).hexdigest()
    contract = create_run_scoped_manifest_snapshots(
        suite_root=suite_root,
        suite_id=suite_id,
        sources=sources,
        expected_raw_sha256=expected,
    )
    artifacts = [
        {
            "path": identity["path"],
            "bytes": identity["bytes"],
            "sha256": identity["raw_sha256"],
        }
        for identity in contract["families"].values()
    ]
    return suite_id, suite_root, sources, expected, contract, artifacts


def test_semantic_identity_excludes_only_volatile_curated_at() -> None:
    before = _manifest("image", "2026-08-29T09:26:59Z")
    after = _manifest("image", "2026-09-01T03:08:30Z")

    assert hashlib.sha256(before).hexdigest() != hashlib.sha256(after).hexdigest()
    assert manifest_semantic_identity(before) == manifest_semantic_identity(after)

    changed = _manifest("image", "2026-09-01T03:08:30Z", label="anomaly")
    assert manifest_semantic_identity(changed) != manifest_semantic_identity(after)


def test_run_scoped_snapshot_binds_path_raw_semantic_bytes_and_count(
    tmp_path: Path,
) -> None:
    suite_id, suite_root, _, _, contract, artifacts = _create_contract(tmp_path)
    binding = manifest_snapshot_binding_sha256(contract)

    result = validate_manifest_snapshot_contract(
        suite_root=suite_root,
        suite_id=suite_id,
        contract=contract,
        indexed_artifacts=artifacts,
        trusted_binding_sha256=binding,
    )

    assert result["status"] == "valid"
    assert result["binding_sha256"] == binding
    assert set(result["families"]) == set(MANIFEST_FAMILIES)
    assert all(item["record_count"] == 2 for item in result["families"].values())


def test_snapshot_creation_is_exclusive_and_never_overwrites(tmp_path: Path) -> None:
    suite_id, suite_root, sources, expected, contract, _ = _create_contract(tmp_path)
    before = {
        family: (suite_root / identity["path"]).read_bytes()
        for family, identity in contract["families"].items()
    }

    with pytest.raises(FileExistsError):
        create_run_scoped_manifest_snapshots(
            suite_root=suite_root,
            suite_id=suite_id,
            sources=sources,
            expected_raw_sha256=expected,
        )

    assert before == {
        family: (suite_root / identity["path"]).read_bytes()
        for family, identity in contract["families"].items()
    }


def test_snapshot_replay_at_alternate_private_root_is_rejected(tmp_path: Path) -> None:
    suite_id, suite_root, _, _, contract, artifacts = _create_contract(tmp_path)
    replay_root = tmp_path / "alternate-parent" / suite_id
    replay_root.parent.mkdir()
    shutil.copytree(suite_root, replay_root)

    with pytest.raises(S7ManifestContractError, match="manifest_snapshot_private_root_replay"):
        validate_manifest_snapshot_contract(
            suite_root=replay_root,
            suite_id=suite_id,
            contract=contract,
            indexed_artifacts=artifacts,
        )


def test_snapshot_run_identity_replay_is_rejected(tmp_path: Path) -> None:
    _, suite_root, _, _, contract, artifacts = _create_contract(tmp_path)

    with pytest.raises(S7ManifestContractError, match="manifest_snapshot_suite_identity"):
        validate_manifest_snapshot_contract(
            suite_root=suite_root,
            suite_id="different-run-id",
            contract=contract,
            indexed_artifacts=artifacts,
        )


def test_raw_snapshot_mutation_is_rejected_even_when_semantics_match(
    tmp_path: Path,
) -> None:
    suite_id, suite_root, _, _, contract, artifacts = _create_contract(tmp_path)
    image_path = suite_root / contract["families"]["image"]["path"]
    image_path.write_bytes(_manifest("image", "2026-09-02T00:00:00Z"))

    with pytest.raises(S7ManifestContractError, match="manifest_snapshot_identity:image"):
        validate_manifest_snapshot_contract(
            suite_root=suite_root,
            suite_id=suite_id,
            contract=contract,
            indexed_artifacts=artifacts,
        )


def test_self_consistent_snapshot_and_index_mutation_rejected_by_trusted_binding(
    tmp_path: Path,
) -> None:
    suite_id, suite_root, _, _, contract, artifacts = _create_contract(tmp_path)
    trusted_binding = manifest_snapshot_binding_sha256(contract)
    mutated = copy.deepcopy(contract)
    image_path = suite_root / mutated["families"]["image"]["path"]
    raw = _manifest("image", "2026-09-02T00:00:00Z")
    image_path.write_bytes(raw)
    semantic_sha256, record_count = manifest_semantic_identity(raw)
    mutated["families"]["image"] = {
        "path": "manifest-snapshots/image.jsonl",
        "bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "semantic_sha256": semantic_sha256,
        "record_count": record_count,
    }
    mutated_artifacts = copy.deepcopy(artifacts)
    image_artifact = next(
        item for item in mutated_artifacts if item["path"].endswith("image.jsonl")
    )
    image_artifact.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())

    with pytest.raises(S7ManifestContractError, match="trusted_binding"):
        validate_manifest_snapshot_contract(
            suite_root=suite_root,
            suite_id=suite_id,
            contract=mutated,
            indexed_artifacts=mutated_artifacts,
            trusted_binding_sha256=trusted_binding,
        )


def test_snapshot_domain_swap_is_rejected(tmp_path: Path) -> None:
    suite_id, suite_root, _, _, contract, artifacts = _create_contract(tmp_path)
    swapped = copy.deepcopy(contract)
    swapped["families"]["image"], swapped["families"]["vlm"] = (
        swapped["families"]["vlm"],
        swapped["families"]["image"],
    )

    with pytest.raises(S7ManifestContractError, match="manifest_snapshot_path:image"):
        validate_manifest_snapshot_contract(
            suite_root=suite_root,
            suite_id=suite_id,
            contract=swapped,
            indexed_artifacts=artifacts,
        )


def test_live_raw_drift_is_classified_without_weakening_snapshot_raw_identity(
    tmp_path: Path,
) -> None:
    _, _, _, _, contract, _ = _create_contract(tmp_path)
    live = tmp_path / "live-image.jsonl"
    live.write_bytes(_manifest("image", "2026-09-03T00:00:00Z"))

    result = classify_live_manifest_drift(
        snapshot_identity=contract["families"]["image"], live_manifest=live
    )

    assert result["status"] == "drift_classified"
    assert result["classification"] == "volatile_curated_at_only"
    assert result["observed"]["raw_sha256"] != contract["families"]["image"]["raw_sha256"]
    assert result["observed"]["semantic_sha256"] == contract["families"]["image"]["semantic_sha256"]


def test_live_semantic_drift_is_fail_closed(tmp_path: Path) -> None:
    _, _, _, _, contract, _ = _create_contract(tmp_path)
    live = tmp_path / "live-image.jsonl"
    live.write_bytes(_manifest("image", "2026-09-03T00:00:00Z", label="anomaly"))

    result = classify_live_manifest_drift(
        snapshot_identity=contract["families"]["image"], live_manifest=live
    )

    assert result["status"] == "remediation_required"
    assert result["classification"] == "semantic_manifest_drift"


def test_invalid_live_manifest_is_classified_fail_closed(tmp_path: Path) -> None:
    _, _, _, _, contract, _ = _create_contract(tmp_path)
    live = tmp_path / "live-image.jsonl"
    live.write_bytes(b"not-json\n")

    result = classify_live_manifest_drift(
        snapshot_identity=contract["families"]["image"], live_manifest=live
    )

    assert result == {
        "status": "remediation_required",
        "classification": "live_manifest_unreadable_or_invalid",
        "error_type": "S7ManifestContractError",
    }


def test_private_index_v2_requires_and_rehashes_manifest_snapshots(
    tmp_path: Path,
) -> None:
    _, suite_root, _, _, contract, _ = _create_contract(tmp_path)
    index = _private_index(suite_root, contract)
    (suite_root / "private-evidence-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    errors: list[str] = []

    result = validate_private_evidence(suite_root, errors)

    assert errors == []
    assert result["legacy_snapshot_absent"] is False
    assert result["manifest_snapshot_contract"] == contract
    assert result["manifest_snapshot_binding_sha256"] == (
        manifest_snapshot_binding_sha256(contract)
    )


def test_private_index_v2_rejects_snapshot_removal(tmp_path: Path) -> None:
    _, suite_root, _, _, contract, _ = _create_contract(tmp_path)
    index = _private_index(suite_root, contract)
    (suite_root / "private-evidence-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (suite_root / contract["families"]["image"]["path"]).unlink()
    errors: list[str] = []

    validate_private_evidence(suite_root, errors)

    assert any(item.startswith("private_artifact_missing:") for item in errors)
    assert any(item.startswith("private_manifest_snapshot:") for item in errors)


def test_private_index_v2_rejects_unindexed_failure_artifact(tmp_path: Path) -> None:
    _, suite_root, _, _, contract, _ = _create_contract(tmp_path)
    index = _private_index(suite_root, contract)
    (suite_root / "private-evidence-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (suite_root / "failure-seal.json").write_text(
        '{"status":"failed","verdict":"zero_credit"}\n',
        encoding="utf-8",
        newline="\n",
    )
    errors: list[str] = []

    validate_private_evidence(suite_root, errors)

    assert "private_unindexed_artifact:failure-seal.json" in errors


def test_private_index_v2_rejects_self_consistent_repin_against_trusted_public_binding(
    tmp_path: Path,
) -> None:
    _, suite_root, _, _, contract, _ = _create_contract(tmp_path)
    trusted_binding = manifest_snapshot_binding_sha256(contract)
    mutated = copy.deepcopy(contract)
    image_path = suite_root / mutated["families"]["image"]["path"]
    raw = _manifest("image", "2026-09-05T00:00:00Z")
    image_path.write_bytes(raw)
    semantic_sha256, record_count = manifest_semantic_identity(raw)
    mutated["families"]["image"].update(
        bytes=len(raw),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        semantic_sha256=semantic_sha256,
        record_count=record_count,
    )
    index = _private_index(suite_root, mutated)
    (suite_root / "private-evidence-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    errors: list[str] = []

    validate_private_evidence(
        suite_root,
        errors,
        trusted_manifest_snapshot_binding_sha256=trusted_binding,
    )

    assert any("manifest_snapshot_trusted_binding" in item for item in errors)


def test_runner_uses_immutable_image_snapshot_for_both_source_probes() -> None:
    runner = (
        Path(__file__).resolve().parents[1] / "scripts/dev/run_s7_auxiliary_admission_experiment.py"
    ).read_text(encoding="utf-8")

    assert runner.count("source_serving_probe(holder, manifest=image_manifest_snapshot)") == 2
    assert "source_serving_probe(holder, data_root=" not in runner


def test_atomic_publication_is_exclusive_and_preserves_original(tmp_path: Path) -> None:
    target = tmp_path / "result.json"
    first = b'{"status":"first"}\n'

    identity = publish_exclusive_atomic_bytes(target, first)
    with pytest.raises(FileExistsError):
        publish_exclusive_atomic_bytes(target, b'{"status":"replay"}\n')

    assert target.read_bytes() == first
    assert identity["sha256"] == hashlib.sha256(first).hexdigest()
    assert not list(tmp_path.glob("*.publish"))


def test_atomic_publication_has_no_fallible_final_readback_after_commit(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "result.json"
    raw = b'{"status":"committed"}\n'
    original_read_bytes = Path.read_bytes

    def reject_final_readback(path: Path) -> bytes:
        if path == target:
            raise PermissionError("simulated post-commit final readback failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_final_readback)

    identity = publish_exclusive_atomic_bytes(target, raw)

    assert identity["bytes"] == len(raw)
    assert identity["sha256"] == hashlib.sha256(raw).hexdigest()
    assert original_read_bytes(target) == raw


def test_atomic_publication_cleanup_error_does_not_downgrade_committed_result(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "result.json"
    raw = b'{"status":"committed"}\n'
    original_unlink = Path.unlink

    def fail_temporary_cleanup(path: Path, *args, **kwargs) -> None:
        if path.name.endswith(".publish"):
            raise PermissionError("simulated temporary cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temporary_cleanup)

    identity = publish_exclusive_atomic_bytes(target, raw)

    assert identity["temporary_cleanup_error"] == "PermissionError"
    assert target.read_bytes() == raw


def test_trusted_envelope_is_an_independent_binding_anchor() -> None:
    envelope = build_trusted_manifest_envelope(
        suite_id="20260901T000000Z-abcdef12",
        source_revision="a" * 40,
        manifest_snapshot_binding_sha256="b" * 64,
        private_evidence_index_sha256="c" * 64,
        public_evidence_sha256="d" * 64,
    )

    assert (
        validate_trusted_manifest_envelope(
            envelope,
            suite_id="20260901T000000Z-abcdef12",
            source_revision="a" * 40,
        )
        == envelope
    )
    with pytest.raises(S7ManifestContractError, match="envelope_identity"):
        validate_trusted_manifest_envelope(
            {**envelope, "acceptance_credit": True},
            suite_id="20260901T000000Z-abcdef12",
            source_revision="a" * 40,
        )


def test_self_consistent_public_repin_cannot_replace_out_of_band_envelope() -> None:
    trusted = build_trusted_manifest_envelope(
        suite_id="20260901T000000Z-abcdef12",
        source_revision="a" * 40,
        manifest_snapshot_binding_sha256="b" * 64,
        private_evidence_index_sha256="c" * 64,
        public_evidence_sha256="d" * 64,
    )
    self_consistent_public_repin = {
        **trusted,
        "manifest_snapshot_binding_sha256": "e" * 64,
        "private_evidence_index_sha256": "f" * 64,
        "public_evidence_sha256": "0" * 64,
    }

    assert (
        trusted["manifest_snapshot_binding_sha256"]
        != self_consistent_public_repin["manifest_snapshot_binding_sha256"]
    )
    assert canonical_sha256(trusted) != canonical_sha256(self_consistent_public_repin)

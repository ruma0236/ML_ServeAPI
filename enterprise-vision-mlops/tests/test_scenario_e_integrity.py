from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evm.core.dataset import shard_index_identity_digest
from evm.operations.failure_evidence import sha256_file
from evm.operations.scenario_e_integrity import (
    IntegrityCounts,
    IntegrityException,
    IntegrityIdentity,
    MlflowObservation,
    SignedTrustManifest,
    TrustedFile,
    TrustManifest,
    build_integrity_admission,
    identity_fingerprint,
    manifest_id,
    sign_manifest,
    validate_integrity,
    validate_integrity_admission,
)


NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
IMAGE_DIGEST = "sha256:" + "7" * 64


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def record(split: str, index: int) -> dict:
    sample_id = f"{split}-{index}"
    return {
        "dataset_id": "manufacturing_visual_inspection",
        "dataset_version": "visa-test-v1",
        "sample_id": sample_id,
        "id": sample_id,
        "content_sha256": f"{index + {'train': 10, 'validation': 20, 'test': 30}[split]:064x}",
        "label": "normal" if index % 2 else "anomaly",
        "split": split,
        "width": 64,
        "height": 64,
        "source_uri": "test://visa",
        "metadata": {"relative_path": f"{split}/{index}.jpg"},
    }


def finalize_manifest(payload: dict) -> TrustManifest:
    draft = TrustManifest.model_validate({**payload, "manifest_id": "0" * 64})
    return draft.model_copy(update={"manifest_id": manifest_id(draft)})


def signed_bundle(tmp_path: Path) -> dict:
    data_root = tmp_path / "data-root"
    ct_root = tmp_path / "ct-root"
    artifact_root = data_root / "artifacts" / "candidate"
    shard_root = data_root / "data" / "shards"
    shards = []
    shard_digests = {}
    all_rows: dict[str, list[dict]] = {}
    for number, split in enumerate(("train", "validation", "test")):
        rows = [record(split, index) for index in range(3)]
        all_rows[split] = rows
        shard_id = f"{split}-{number:04d}"
        shard_path = shard_root / f"{split}.jsonl"
        write_jsonl(shard_path, rows)
        shard_digests[shard_id] = sha256_file(shard_path)
        shards.append(
            {
                "shard_id": shard_id,
                "split": split,
                "path": str(shard_path),
                "record_count": len(rows),
                "first_sample_id": rows[0]["sample_id"],
                "last_sample_id": rows[-1]["sample_id"],
            }
        )
    shard_index = {
        "schema_version": "evm.dataset_shards.v1",
        "records_per_shard": 3,
        "record_count": 9,
        "shard_count": 3,
        "split_counts": {"train": 3, "validation": 3, "test": 3},
        "label_counts": {"normal": 3, "anomaly": 6},
        "label_type_counts": {"normal": 3, "anomaly": 6},
        "shards": shards,
    }
    shard_index["identity_sha256"] = shard_index_identity_digest(shard_index)
    shard_path = shard_root / "shard_index.json"
    write_json(shard_path, shard_index)
    split_path = artifact_root / "split_manifest.json"
    write_json(
        split_path,
        {
            "schema_version": "evm.w7.efficientnet_split_manifest.v1",
            "dataset_version": "visa-test-v1",
            "record_count": 9,
            "split_counts": shard_index["split_counts"],
            "source_shard_identity_sha256": shard_index["identity_sha256"],
        },
    )
    ct_path = ct_root / "holdout.jsonl"
    write_jsonl(ct_path, all_rows["test"])
    model_path = artifact_root / "model.pt"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"immutable-model")
    candidate_path = artifact_root / "candidate_summary.json"
    lineage_path = artifact_root / "lineage.json"
    card_path = artifact_root / "model_card.md"
    mlflow_run_id = "mlflow-test-run"
    artifact_uri = "test://candidate"
    candidate = {
        "candidate_id": "candidate-test",
        "dataset_version": "visa-test-v1",
        "model_sha256": sha256_file(model_path),
        "split_manifest_sha256": sha256_file(split_path),
        "source_shard_index_sha256": shard_index["identity_sha256"],
        "mlflow_run_id": mlflow_run_id,
        "artifact_uri": artifact_uri,
    }
    lineage = {
        **candidate,
        "split_manifest_uri": str(split_path),
        "model_artifact": str(model_path),
    }
    write_json(candidate_path, candidate)
    write_json(lineage_path, lineage)
    card_path.write_text(
        f"candidate-test\nMLflow run id: {mlflow_run_id}\n",
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text("policy_id = 'scenario-e-test'\n", encoding="utf-8")
    identity = IntegrityIdentity(
        dataset_version="visa-test-v1",
        shard_identity_sha256=shard_index["identity_sha256"],
        shard_manifest_sha256=sha256_file(shard_path),
        split_manifest_sha256=sha256_file(split_path),
        ct_manifest_sha256=sha256_file(ct_path),
        candidate_id="candidate-test",
        model_digest=sha256_file(model_path),
        container_image_digest=IMAGE_DIGEST,
        mlflow_run_id=mlflow_run_id,
        candidate_summary_sha256=sha256_file(candidate_path),
        lineage_sha256=sha256_file(lineage_path),
        model_card_sha256=sha256_file(card_path),
        policy_sha256=sha256_file(policy_path),
    )
    exception = IntegrityException(
        exception_id="legacy-source-test",
        code="training_source_revision_missing",
        requester="ml-platform",
        approver="ai-infra",
        reason="Legacy training source revision is unavailable for this fixture.",
        subject_fingerprint=identity_fingerprint(identity),
        issued_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    files = [
        TrustedFile(role="shard_manifest", path=str(shard_path), sha256=sha256_file(shard_path)),
        TrustedFile(role="split_manifest", path=str(split_path), sha256=sha256_file(split_path)),
        TrustedFile(role="ct_manifest", path=str(ct_path), sha256=sha256_file(ct_path)),
        TrustedFile(role="candidate_summary", path=str(candidate_path), sha256=sha256_file(candidate_path)),
        TrustedFile(role="lineage", path=str(lineage_path), sha256=sha256_file(lineage_path)),
        TrustedFile(role="model_card", path=str(card_path), sha256=sha256_file(card_path)),
        TrustedFile(role="model_artifact", path=str(model_path), sha256=sha256_file(model_path)),
        TrustedFile(role="policy", path=str(policy_path), sha256=sha256_file(policy_path)),
    ]
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(public_pem)
    manifest = finalize_manifest(
        {
            "schema_version": "evm.scenario_e_trust_manifest.v1",
            "issue": "SCRUM-176",
            "issuer": "test",
            "key_id": "test-key-12345678",
            "validator_source_revision": "a" * 40,
            "issued_at": NOW,
            "expires_at": NOW + timedelta(days=1),
            "admission_ttl_seconds": 3600,
            "identity": identity.model_dump(mode="json"),
            "expected_counts": IntegrityCounts(
                record_count=9,
                shard_count=3,
                split_counts={"train": 3, "validation": 3, "test": 3},
                ct_record_count=3,
            ).model_dump(mode="json"),
            "files": [item.model_dump(mode="json") for item in files],
            "shard_digests": shard_digests,
            "lineage_parents": [
                "split_manifest_uri",
                "model_artifact",
                "artifact_uri",
                "mlflow_run_id",
            ],
            "exceptions": [exception.model_dump(mode="json")],
        }
    )
    return {
        "data_root": data_root,
        "ct_root": ct_root,
        "manifest": manifest,
        "envelope": sign_manifest(manifest, private_pem),
        "private_pem": private_pem,
        "public_pem": public_pem,
        "public_path": public_path,
        "mlflow": MlflowObservation(
            run_id=mlflow_run_id,
            status="FINISHED",
            candidate_id="candidate-test",
            dataset_version="visa-test-v1",
            artifact_uri=artifact_uri,
        ),
        "shard_path": shard_path,
        "train_path": shard_root / "train.jsonl",
    }


def validate(bundle: dict, envelope: SignedTrustManifest | None = None, now: datetime = NOW):
    return validate_integrity(
        envelope or bundle["envelope"],
        public_key_pem=bundle["public_pem"],
        allowed_roots=[bundle["data_root"], bundle["ct_root"], bundle["public_path"].parent],
        host_data_root=bundle["data_root"],
        host_ct_root=bundle["ct_root"],
        observed_image_digest=IMAGE_DIGEST,
        mlflow=bundle["mlflow"],
        now=now,
    )


def test_signed_known_good_bundle_is_admitted_deterministically(tmp_path: Path) -> None:
    bundle = signed_bundle(tmp_path)
    first = validate(bundle)
    second = validate(bundle, now=NOW + timedelta(seconds=1))

    assert first.decision == "admitted"
    assert first.blockers == []
    assert first.decision_fingerprint == second.decision_fingerprint
    first_freshness = next(item for item in first.checks if item.check_id == "trust_freshness")
    second_freshness = next(item for item in second.checks if item.check_id == "trust_freshness")
    assert first_freshness.observed["evaluated_at"] != second_freshness.observed["evaluated_at"]
    assert first.counts["record_count"] == 9
    assert first.counts["cross_split_record_count"] == 0
    assert first.exceptions_applied == ["legacy-source-test"]


def test_invalid_signature_and_stale_manifest_fail_before_admission(tmp_path: Path) -> None:
    bundle = signed_bundle(tmp_path)
    invalid = bundle["envelope"].model_copy(
        update={"signature": base64.b64encode(b"invalid-signature").decode("ascii")}
    )
    invalid_result = validate(bundle, invalid)
    assert invalid_result.primary_blocker == "trust_signature_invalid"
    assert len(invalid_result.checks) == 1

    stale_manifest = bundle["manifest"].model_copy(
        update={
            "issued_at": NOW - timedelta(days=2),
            "expires_at": NOW - timedelta(days=1),
            "exceptions": [
                bundle["manifest"].exceptions[0].model_copy(
                    update={
                        "issued_at": NOW - timedelta(days=2),
                        "expires_at": NOW - timedelta(days=1),
                    }
                )
            ],
        }
    )
    stale_manifest = finalize_manifest(stale_manifest.model_dump(mode="json"))
    stale = sign_manifest(stale_manifest, bundle["private_pem"])
    stale_result = validate(bundle, stale)
    assert stale_result.primary_blocker == "trust_manifest_stale"
    assert stale_result.deployment_intent_allowed is False


def test_signed_duplicate_content_is_rejected_semantically(tmp_path: Path) -> None:
    bundle = signed_bundle(tmp_path)
    rows = [json.loads(line) for line in bundle["train_path"].read_text().splitlines()]
    rows[1] = dict(rows[0])
    write_jsonl(bundle["train_path"], rows)
    manifest = bundle["manifest"].model_copy(
        update={
            "shard_digests": {
                **bundle["manifest"].shard_digests,
                "train-0000": sha256_file(bundle["train_path"]),
            }
        }
    )
    manifest = finalize_manifest(manifest.model_dump(mode="json"))
    envelope = sign_manifest(manifest, bundle["private_pem"])

    result = validate(bundle, envelope)

    assert result.primary_blocker == "duplicate_record_identity"
    assert "duplicate_content_identity" in result.blockers
    assert result.deployment_intent_allowed is False


def test_admission_pointer_revalidates_signature_evidence_and_identity(tmp_path: Path) -> None:
    bundle = signed_bundle(tmp_path)
    validation = validate(bundle)
    signed_path = tmp_path / "signed.json"
    validation_path = tmp_path / "validation.json"
    signed_path.write_text(bundle["envelope"].model_dump_json(indent=2), encoding="utf-8")
    validation_path.write_text(validation.model_dump_json(indent=2), encoding="utf-8")
    admission = build_integrity_admission(
        envelope=bundle["envelope"],
        validation=validation,
        signed_manifest_path=signed_path,
        validation_path=validation_path,
        source_revision="a" * 40,
    )
    admission_path = tmp_path / "admission.json"
    admission_path.write_text(admission.model_dump_json(indent=2), encoding="utf-8")

    blockers = validate_integrity_admission(
        admission_path,
        public_key_path=bundle["public_path"],
        expected_candidate_id="candidate-test",
        expected_dataset_version="visa-test-v1",
        expected_model_digest=bundle["manifest"].identity.model_digest,
        expected_image_digest=IMAGE_DIGEST,
        now=NOW + timedelta(minutes=1),
    )
    mismatch = validate_integrity_admission(
        admission_path,
        public_key_path=bundle["public_path"],
        expected_candidate_id="other-candidate",
        expected_dataset_version="visa-test-v1",
        expected_model_digest=bundle["manifest"].identity.model_digest,
        expected_image_digest=IMAGE_DIGEST,
        now=NOW + timedelta(minutes=1),
    )
    stale = validate_integrity_admission(
        admission_path,
        public_key_path=bundle["public_path"],
        expected_candidate_id="candidate-test",
        expected_dataset_version="visa-test-v1",
        expected_model_digest=bundle["manifest"].identity.model_digest,
        expected_image_digest=IMAGE_DIGEST,
        now=NOW + timedelta(hours=1),
    )
    extended_path = tmp_path / "extended-admission.json"
    extended_path.write_text(
        admission.model_copy(
            update={"expires_at": admission.expires_at + timedelta(minutes=1)}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    extended = validate_integrity_admission(
        extended_path,
        public_key_path=bundle["public_path"],
        expected_candidate_id="candidate-test",
        expected_dataset_version="visa-test-v1",
        expected_model_digest=bundle["manifest"].identity.model_digest,
        expected_image_digest=IMAGE_DIGEST,
        now=NOW + timedelta(minutes=1),
    )

    assert blockers == []
    assert mismatch == ["integrity_admission_identity_mismatch"]
    assert admission.expires_at == NOW + timedelta(hours=1)
    assert stale == ["integrity_admission_stale"]
    assert extended == ["integrity_admission_evidence_mismatch"]


def test_manifest_byte_change_fails_even_when_semantic_identity_is_unchanged(tmp_path: Path) -> None:
    bundle = signed_bundle(tmp_path)
    bundle["shard_path"].write_bytes(bundle["shard_path"].read_bytes() + b"\n")

    result = validate(bundle)

    assert result.primary_blocker == "manifest_digest_mismatch"
    dataset_check = next(item for item in result.checks if item.check_id == "dataset_contract")
    assert dataset_check.observed["shard_identity_sha256"] == bundle["manifest"].identity.shard_identity_sha256

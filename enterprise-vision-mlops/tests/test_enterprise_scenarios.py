from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel.scenarios import (
    ScenarioIntakeLaunchRequest,
    launch_scenario_intake,
    read_scenario_catalog,
)
from evm.pipelines.scenario_intake import run as scenario_intake


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_enterprise_scenario_catalog_exposes_verified_and_fail_closed_use_cases(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EVM_SCENARIO_CONFIG_ROOT", str(PROJECT_ROOT / "configs" / "scenarios"))

    catalog = read_scenario_catalog()
    scenarios = {item.scenario_id: item for item in catalog.scenarios}

    assert len(catalog.catalog_digest) == 64
    assert set(scenarios) == {
        "manufacturing-visual-inspection",
        "banking77-intent-classification",
        "dolly-instruction-tuning",
        "scienceqa-vlm-evaluation",
    }
    assert scenarios["manufacturing-visual-inspection"].profile_template is not None
    assert scenarios["manufacturing-visual-inspection"].model_readiness == "verified"
    assert scenarios["banking77-intent-classification"].model_readiness == "not_implemented"
    assert "text_training_adapter_not_implemented" in scenarios[
        "banking77-intent-classification"
    ].blockers
    assert scenarios["scienceqa-vlm-evaluation"].dataset.usage_policy == (
        "non-commercial-portfolio-and-research-only"
    )


def test_scenario_intake_dry_run_creates_audited_airflow_assignment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EVM_SCENARIO_CONFIG_ROOT", str(PROJECT_ROOT / "configs" / "scenarios"))
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "ledger"))

    task = launch_scenario_intake(
        "banking77-intent-classification",
        ScenarioIntakeLaunchRequest(actor="portfolio-reviewer", dry_run=True),
    )

    assert task.status == "dry_run"
    assert task.task_type == "airflow_dag_run"
    assert task.config_payload["dag_id"] == "enterprise_mlops_scenario_intake"
    assert task.config_payload["scenario_id"] == "banking77-intent-classification"
    assert str(task.config_payload["pipeline_config_uri"]).endswith(
        "configs/scenarios/banking77-intent-classification.json"
    )


def test_banking77_intake_builds_deterministic_manifest_and_quality_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    categories = [
        "cash_withdrawal",
        "card_payment_wrong_exchange_rate",
        *[f"unused_category_{index}" for index in range(75)],
    ]
    (source_root / "categories.json").write_text(json.dumps(categories), encoding="utf-8")
    (source_root / "train.csv").write_text(
        "text,category\nWhere is my cash?,cash_withdrawal\nWrong exchange rate,card_payment_wrong_exchange_rate\n",
        encoding="utf-8",
    )
    (source_root / "test.csv").write_text(
        (
            "text,category\n"
            "Cash was not received,cash_withdrawal\n"
            "Where is my cash?,cash_withdrawal\n"
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"
    config = banking_config(output_root)
    config_path = tmp_path / "banking.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(PROJECT_ROOT))
    monkeypatch.delenv("EVM_DATA_MOUNT_ROOT", raising=False)

    def fake_acquire(*_args, **_kwargs):
        return [
            {
                "path": source_root / name,
                "definition": {"role": role},
                "evidence": {"role": role, "relative_path": name},
            }
            for name, role in (
                ("categories.json", "label_schema"),
                ("train.csv", "train"),
                ("test.csv", "test"),
            )
        ]

    monkeypatch.setattr(scenario_intake, "acquire_sources", fake_acquire)

    first = scenario_intake.run(str(config_path))
    first_manifest = (output_root / "processed" / "normalized_manifest.jsonl").read_bytes()
    second = scenario_intake.run(str(config_path))
    second_manifest = (output_root / "processed" / "normalized_manifest.jsonl").read_bytes()
    quality = json.loads((output_root / "evidence" / "quality_report.json").read_text())
    split = json.loads((output_root / "evidence" / "split_manifest.json").read_text())

    assert first["status"] == "pass"
    assert second["status"] == "pass"
    assert first_manifest == second_manifest
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert quality["records_out"] == 3
    assert quality["label_count"] == 2
    assert quality["duplicates_observed"] == 1
    assert quality["duplicates_removed"] == 1
    assert quality["cross_split_leakage_removed"] == 1
    assert split["immutable"] is True
    assert split["split_counts"]["test"] == 2


def test_scenario_preprocessing_rejects_unregistered_transform() -> None:
    with pytest.raises(scenario_intake.ScenarioIntakeError, match="not_approved"):
        scenario_intake.validated_steps(
            {
                "steps": [
                    {"transform_id": "run_arbitrary_shell", "parameters": {"command": "rm -rf"}}
                ]
            }
        )


def test_pii_review_policy_blocks_data_readiness(tmp_path: Path) -> None:
    config = banking_config(tmp_path)
    config["preprocessing"]["steps"][-1]["parameters"]["mode"] = "review_required"
    quality = scenario_intake.build_quality_report(
        config,
        [
            {
                "content_sha256": "a" * 64,
                "split": "train",
                "label": "cash_withdrawal",
            }
        ],
        {"records_in": 1, "dropped": 0, "pii_flagged": 1},
        manifest_path=tmp_path / "manifest.jsonl",
        manifest_sha256="b" * 64,
        split_manifest_path=tmp_path / "split.json",
        source_registry_path=tmp_path / "sources.json",
    )

    assert quality["status"] == "review_required"
    assert quality["review_reasons"] == ["pii_review_required:1"]
    assert quality["warnings"] == ["pii_patterns_detected:1"]


def banking_config(output_root: Path) -> dict[str, object]:
    return {
        "schema_version": "evm.scenario_intake.v1",
        "scenario": {
            "scenario_id": "banking-test",
            "display_name": "Banking Test",
            "department": "Customer Operations",
            "business_outcome": "Test deterministic intent preprocessing.",
            "modality": "text",
            "intake_supported": True,
        },
        "dataset": {
            "dataset_id": "banking77",
            "dataset_name": "BANKING77",
            "dataset_version": "test-v1",
            "source_url": "https://example.invalid",
            "source_revision": "revision",
            "license_id": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "usage_policy": "test-only",
            "output_root": str(output_root),
            "manifest_uri": str(output_root / "processed" / "normalized_manifest.jsonl"),
            "split_manifest_uri": str(output_root / "evidence" / "split_manifest.json"),
        },
        "preprocessing": {
            "recipe_id": "banking-test-v1",
            "version": "1.0.0",
            "steps": [
                {"transform_id": "normalize_unicode_nfc", "parameters": {}},
                {"transform_id": "collapse_whitespace", "parameters": {}},
                {
                    "transform_id": "enforce_text_length",
                    "parameters": {"min_chars": 2, "max_chars": 200},
                },
                {
                    "transform_id": "deduplicate_content_prefer_holdout",
                    "parameters": {"holdout_order": ["test", "validation", "train"]},
                },
                {"transform_id": "scan_pii_patterns", "parameters": {"mode": "report_only"}},
            ],
        },
        "acquisition": {
            "parser": "banking77_csv",
            "source_files": [{"file_id": "placeholder"}],
        },
        "split_policy": {"seed": 20260714, "train": 0.72, "validation": 0.08, "test": 0.2},
    }

# 2026-07-10 W7 Real-Test Evidence Validation

## Scope

This checkpoint closes the W7 real model evidence path for `EVM-237` and
`EVM-238-B`. All configured Torch/TorchVision EfficientNet candidates ran on
the real VisA split, produced F-drive artifacts, logged MLflow runs, and passed
the real-test evidence validator.

This does not close `EVM-226`; Kubernetes real `kubectl apply` proof remains
blocked by the local Kubernetes runtime state.

## Evidence Roots

- Matrix summary:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/latest_model_matrix.json`
- Matrix artifact root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/w7-efficientnet-real-test-matrix`
- EVM-238-B validation report:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/real_test_evidence/evm-238-b-real-test-evidence-report.json`

## Split Evidence

- Dataset version: `visa-open-data-f1f1c9ee9922`
- Total records: `10821`
- Train: `6504`
- Validation: `2136`
- Test: `2181`
- Shards: `23`

## Candidate Results

| Candidate | Epochs | Steps | MLflow run | Accuracy | F1 | AUROC | GPU peak MB | Blockers |
|---|---:|---:|---|---:|---:|---:|---:|---|
| `effnet-b0-img224-freeze-adamw` | 5 | 1020 | `eeac494a65b447e4bb4a65ce7a101ca9` | 0.797341 | 0.404313 | 0.839289 | 989.201 | `accuracy<0.8`, `f1<0.75` |
| `effnet-b0-img224-finetune-sgd` | 5 | 1020 | `da6fb45efb224d9f9de3344697d9dddc` | 0.835855 | 0.526455 | 0.948841 | 1471.127 | `f1<0.75` |
| `effnet-b7-img600-freeze-adamw` | 3 | 2439 | `0a3b6141e81749fc9bde1e0f341bf5f9` | 0.892710 | 0.323699 | 0.787404 | 14014.601 | `f1<0.75`, `auroc<0.8` |
| `effnet-b7-img600-finetune-adamw` | 3 | 4878 | `a4e2763b28ae494ea67944084edd4b3f` | 0.961027 | 0.812362 | 0.970650 | 13386.656 | none |

Best candidate by promotion blockers and F1:

```text
effnet-b7-img600-finetune-adamw
```

## Control Panel Evidence

`CycleRun.model_matrix` now reads all four candidates from
`latest_model_matrix.json`.

Observed state:

```text
matrix_status=pass
candidate_count=4
configured_candidate_count=4
```

Each candidate has:

- MLflow run URI,
- F-drive artifact URI,
- metrics,
- model artifact,
- training history,
- confusion matrix JSON and PNG,
- GPU profile,
- environment report,
- model card,
- promotion blocker reason when not promotable.

## EVM-238-B Validation

Command:

```powershell
F:\evm_w7_torch\python.exe -m evm.control_panel.real_test_policy --validate-evidence --efficientnet-config configs\w7_efficientnet_real_test.toml --report F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\real_test_evidence\evm-238-b-real-test-evidence-report.json
```

Result:

```text
valid=true
checked_candidate_count=4
violations=[]
```

## Code Verification

Targeted validation tests:

```powershell
F:\evm_w7_torch\python.exe -m pytest tests\test_w7_real_test_evidence_validation.py tests\test_efficientnet_real_test_matrix.py tests\test_w7_real_test_policy.py tests\test_control_panel_aggregation.py -q
```

Result:

```text
12 passed in 0.27s
```

## Remaining W7 Blocker

`EVM-226` remains open because Docker Desktop Kubernetes is disabled or no
current Kubernetes context exists. The model real-test path is complete, but
the Kubernetes real execution proof is still required for W7 closeout.

## Synchronization

- Git evidence commit: `0211cd8`
- Jira:
  - `SCRUM-115` / `EVM-237`: transitioned to Done, comment `10211`
  - `SCRUM-118` / `EVM-238-B`: transitioned to Done, comment `10212`
  - `SCRUM-116` / `EVM-238`: transitioned to Done, comment `10213`
  - `SCRUM-106` / `EVM-228`: updated, comment `10214`
  - `SCRUM-98` / W7 epic: updated, comment `10215`
- Notion:
  - Evidence page:
    `https://app.notion.com/p/39910ad2dcad81a3a25be86ae957b290`
  - Knowledge Base comment:
    `39910ad2-dcad-8123-9e20-001d4aafbae6`
  - W7 Acceptance Matrix comment:
    `39910ad2-dcad-8176-af19-001dc8fc4b37`
  - Evidence Ledger comment:
    `39910ad2-dcad-8184-83f5-001d3c3ca825`
- Obsidian:
  - `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-10 W7 EVM-237 238B Full EfficientNet Matrix Validation.md`
  - Current Context Pack, Retrieval Index, and Work Log Graph updated.

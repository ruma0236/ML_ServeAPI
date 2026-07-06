# Domain Pack Pipeline

## Purpose

`domain-pack-check` validates the domain-specific policy layer before data,
model, and serving work runs.

The core MLOps platform should stay reusable across domains. Domain packs carry
the parts that change by workload:

- dataset candidates and access policy,
- manifest schema,
- validation rules,
- model adapter contract,
- request and response schemas,
- promotion gates,
- failure scenarios,
- benchmark and RCA keys.

The first reference domain pack is manufacturing visual inspection with a
VLM-first batch inference and evaluation path.

## Code

- Domain pack file:
  `domain_packs/manufacturing_visual_inspection/domain_pack.toml`
- Validator:
  `src/evm/core/domain_pack.py`
- Pipeline:
  `src/evm/pipelines/domain_pack_check/run.py`

## Command

```powershell
python scripts/run_pipeline.py domain-pack-check --config configs/local.toml
```

## Contract

Input:

- TOML domain pack path from `pipelines.domain_pack_check.domain_pack`.

Output:

- `artifacts/runs/domain_pack_check/<run_id>/summary.json`
- `artifacts/reports/domain_pack_check.md`

The summary reports:

- domain pack id and version,
- reference workload,
- dataset candidates,
- primary dataset,
- manifest required field count,
- validation rule count,
- promotion gate count,
- failure scenario count,
- diagnostics.

## Current Manufacturing VLM Policy

Current P0 domain policy:

| Policy Surface | Current Decision |
|---|---|
| Reference workload | manufacturing visual inspection |
| Primary dataset | VisA |
| Fallback/secondary dataset | MVTec AD |
| Mock backend | `mock_vlm_adapter` |
| Real candidate backend | Qwen2.5-VL 3B/7B quantized on Windows RTX |
| Required manifest schema | `mvi_manifest_v1` |
| Key output schema | defect detection, severity, evidence, action, raw output, latency, error type |
| Required gates | schema validity and bad prompt regression |
| Required scenarios | bad prompt, corrupt/drifted image, schema-invalid output, endpoint failure |

## Portability Rule

To adapt the platform to another domain, keep the core pipeline stages and
replace the domain pack:

```text
core platform:
  orchestration, storage, tracking, registry, serving, monitoring, audit

domain pack:
  dataset policy, manifest schema, validation rules, model adapter, eval gates
```

This makes manufacturing VLM the first reference workload, not a hard-coded
platform boundary.

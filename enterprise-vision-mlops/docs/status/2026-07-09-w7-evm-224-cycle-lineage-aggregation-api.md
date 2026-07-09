# 2026-07-09 W7 EVM-224 Cycle Lineage Aggregation API

## Summary

`EVM-224` is implemented as the first W7 execution task. The API now exposes a
read-only Control Panel `CycleRun` aggregation endpoint backed by current local
MLOps evidence instead of a static example payload.

Implemented endpoints:

- `GET /control-panel/v1/cycles/latest`
- `GET /control-panel/v1/cycles/{cycle_id}`

## Implementation

- `src/evm/control_panel/schemas.py`
  - Pydantic contract models for `CycleRun`, dataset/model/readiness state,
    drift, CD/CT, model matrix, stages, artifacts, and resource refs.
- `src/evm/control_panel/aggregation.py`
  - Aggregates local config, registry, dataset metadata, quality report,
    curation state, lakehouse probe, lifecycle dashboard, drift queue, and W7
    EfficientNet matrix config.
  - Missing evidence is represented as `unknown` or `blocked` instead of being
    hidden.
- `src/evm/control_panel/validate_cycle_run.py`
  - Validates a `CycleRun` JSON payload against the OpenAPI component required
    fields and the Pydantic `CycleRun` model.
- `apps/api/control_panel.py`
  - FastAPI router for the read-only cycle endpoints.
- `apps/api/main.py`
  - Includes the Control Panel router.
- `tests/test_control_panel_aggregation.py`
  - Verifies aggregation over real-shaped local evidence and missing-evidence
    behavior.
- `tests/test_control_panel_contract.py`
  - Verifies example and route payloads conform to the `CycleRun` contract.

## Evidence

F-drive source-of-truth evidence root:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel/evm-224-20260709T110004Z/cycle_run.json`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel/evm-224-20260709T110004Z/cycle_run_schema_validation.json`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel/evm-224-20260709T110004Z/cycle_run_http.json`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel/evm-224-20260709T110004Z/cycle_run_http_schema_validation.json`

Latest validated cycle:

- `cycle_id`: `cycle-w7-visa-open-data-f1f1c9ee9922-vision-baseline-v10`
- `status`: `running`
- `dataset_version`: `visa-open-data-f1f1c9ee9922`
- `model_name`: `vision-baseline`
- `model_version`: `10`
- `stage_count`: `7`
- `artifact_count`: `7`

## Verification

```powershell
C:\Users\opop0\miniconda3\python.exe -m py_compile apps\api\main.py apps\api\control_panel.py src\evm\control_panel\schemas.py src\evm\control_panel\aggregation.py src\evm\control_panel\validate_cycle_run.py tests\test_control_panel_aggregation.py tests\test_control_panel_contract.py
C:\Users\opop0\miniconda3\python.exe -m pytest tests\test_control_panel_aggregation.py tests\test_control_panel_contract.py tests\test_api_metrics.py tests\test_model_promotion.py -q
C:\Users\opop0\miniconda3\python.exe -m json.tool contracts\control-panel\examples\cycle-run.json
C:\Users\opop0\miniconda3\python.exe -m evm.control_panel.validate_cycle_run --openapi contracts\control-panel\control-panel.openapi.json --input F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\control_panel\evm-224-20260709T110004Z\cycle_run.json --report F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\control_panel\evm-224-20260709T110004Z\cycle_run_schema_validation.json
C:\Users\opop0\miniconda3\python.exe -m evm.control_panel.validate_cycle_run --openapi contracts\control-panel\control-panel.openapi.json --input F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\control_panel\evm-224-20260709T110004Z\cycle_run_http.json --report F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\control_panel\evm-224-20260709T110004Z\cycle_run_http_schema_validation.json
kubectl kustomize infra\kubernetes\local
docker compose config
```

Result:

- Python compile passed.
- `8 passed, 2 warnings`
- OpenAPI example JSON parsed.
- Kustomize rendered.
- Docker Compose config rendered.
- Direct aggregator payload and temporary HTTP route payload both passed
  `evm.control_panel.validate_cycle_run`.

## Closure

`EVM-224` is complete for W7 implementation entry. The next W7 implementation
step can proceed to `EVM-238-A` policy guard or `EVM-225` Control Panel UI
scaffold, with `EVM-224` as the live read-only API dependency.

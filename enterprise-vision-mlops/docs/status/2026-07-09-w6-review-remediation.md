# 2026-07-09 W6 Review Remediation

## Scope

This closes the explicit W6 review findings before W7 Control Panel work starts.
The goal is to remove ambiguous runtime state, make data workflow failure modes
operationally safe, and expose orchestration contracts clearly enough for an
enterprise-grade UI/control layer.

## Remediated Findings

| Priority | Finding | Remediation | Evidence |
|---|---|---|---|
| P1 | API metrics retained stale model stage/version label series | `refresh_model_state()` now clears previously emitted model metric children before publishing the current loaded model or current failed target | `tests/test_api_metrics.py` |
| P2 | K8s scaffold did not define how W7 should control Airflow | Added `evm-airflow-control-contract` and OpenAPI `OrchestratorConnection` so W7 can treat Compose Airflow as an explicit external orchestrator until in-cluster Airflow is added | `infra/kubernetes/local/airflow-external.yaml`, `contracts/control-panel/control-panel.openapi.json` |
| P2 | K8s pipeline Job only ran `domain-pack-check` against `configs/local.toml` | Local overlay now uses `configs/local_visa.toml` and includes `evm-curation-workflow` plus `evm-lakehouse-probe` Jobs for the W6 VisA cycle | `infra/kubernetes/local/pipeline-job.yaml` |
| P2 | Curation workflow allowed empty input to pass | Added fail-closed `fail_on_empty` behavior with default `true` and regression coverage | `src/evm/pipelines/curation_workflow/run.py`, `tests/test_curation_workflow.py` |
| P2 | Airflow runtime used a different PyArrow version than local/pipeline runtime | Added custom Airflow image pinned to `pyarrow==18.1.0` and updated Compose Airflow services to build it | `infra/docker/airflow/Dockerfile`, `docker-compose.yml` |
| P2 | API metric regression coverage needed API dependencies available in standard project installs | Added API runtime dependencies to the central project dependency list while keeping the API Docker requirements file | `pyproject.toml`, `apps/api/requirements.txt` |
| P3 | W6 Notion/Obsidian sync needed closure | Current remediation should be synced as the W6 review-close entry after commit | Notion/Obsidian sync step |

## W7 Enterprise Fit Assessment

W7 is now a better fit for the enterprise Control Panel target because:

- Serving metrics expose one authoritative model state instead of multiple
  active-looking model versions.
- Airflow is no longer an implicit dependency; the UI can read a control
  contract and decide whether actions are external REST calls or future
  in-cluster mutations.
- K8s pipeline jobs represent actual VisA data curation and lakehouse checks,
  not only a policy smoke example.
- Dataset curation fails closed when upstream manifests are missing or empty,
  which is the safer default for scheduled enterprise pipelines.
- Airflow and pipeline Parquet runtimes are pinned to the same `pyarrow` version
  for reproducible lakehouse behavior.

Remaining W7 design constraint:

- The local overlay still does not deploy in-cluster Airflow webserver,
  scheduler, workers, or Airflow metadata DB. W7 should either keep using the
  external Airflow contract for initial UI control or add an Airflow Kubernetes
  deployment/operator layer before enabling in-cluster Airflow actions.

## Verification

Commands:

```powershell
C:\Users\opop0\miniconda3\python.exe -m pytest tests\test_api_metrics.py tests\test_curation_workflow.py
C:\Users\opop0\miniconda3\python.exe -m pytest tests\test_lakehouse_probe.py tests\test_data_quality_policy.py tests\test_image_quality_pipeline.py tests\test_model_promotion.py
C:\Users\opop0\miniconda3\python.exe -m py_compile apps\api\main.py scripts\run_pipeline.py src\evm\pipelines\curation_workflow\run.py
python -m json.tool contracts\control-panel\control-panel.openapi.json
docker compose config --quiet
kubectl kustomize infra\kubernetes\local
C:\Users\opop0\miniconda3\python.exe scripts\run_pipeline.py curation-workflow --config configs\local_visa.toml
C:\Users\opop0\miniconda3\python.exe scripts\run_pipeline.py lakehouse-probe --config configs\local_visa.toml
```

Observed results:

- API/curation regression tests: `3 passed`
- Lakehouse/data-quality/image-quality/model-promotion tests: `6 passed`
- Kustomize render: `34` resources
- Docker Compose config: pass
- VisA curation: `10821` records, `128` HITL/sample-review records, `4317`
  curated eval candidates
- VisA lakehouse probe: `pass`, `10821` Parquet rows,
  `1517264` bytes

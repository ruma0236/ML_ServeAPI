# 2026-07-09 W6 Kubernetes Runtime Resource Map

Issue: `EVM-221` / Jira `SCRUM-99`

## Result

`EVM-221` is complete. The current Docker Compose runtime is mapped to
Kubernetes resource types, storage claims, node placement, readiness checks, and
Control Panel resource fields.

## Files Added

- `infra/kubernetes/runtime-resource-map.json`
- `docs/architecture/kubernetes-runtime-resource-map.md`

## Coverage

The resource map covers 11 current Compose services:

- `postgres`
- `airflow-postgres`
- `airflow-init`
- `airflow-webserver`
- `airflow-scheduler`
- `minio`
- `minio-create-buckets`
- `mlflow`
- `api`
- `prometheus`
- `grafana`

It also defines initial pipeline workload classes:

- `evm-pipeline-job`
- `evm-gpu-training-job`
- `evm-vlm-serving`

## Placement Decisions

- `local-cpu` is the first control-plane node pool for API, Airflow, MLflow,
  MinIO, Prometheus, Grafana, Postgres, and CPU pipeline jobs.
- `windows-rtx` is reserved for future deep image model training, VLM serving,
  and GPU batch inference.
- `mac-mini-remote` remains an external ARM64 evaluator and is not yet treated
  as a Kubernetes worker.

## Storage Decisions

Large data and artifacts stay on the F drive:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/minio`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts`

The Kubernetes scaffold should bind local-path PVCs back to the F-drive root for
TB-scale data and artifacts.

## Verification

Commands:

```powershell
python -m json.tool infra\kubernetes\runtime-resource-map.json
```

```powershell
python - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("infra/kubernetes/runtime-resource-map.json").read_text())
expected = {
    "postgres",
    "airflow-postgres",
    "airflow-init",
    "airflow-webserver",
    "airflow-scheduler",
    "minio",
    "minio-create-buckets",
    "mlflow",
    "api",
    "prometheus",
    "grafana",
}
actual = {item["compose_service"] for item in data["services"]}
assert expected <= actual
print(len(actual), len(data["persistent_volumes"]), len(data["node_pools"]))
PY
```

Observed:

- services: `11`
- missing services: `[]`
- persistent volume entries: `7`
- node pools: `3`

## Handoff

`EVM-222` can now create the first local Kubernetes scaffold from this map.
`EVM-223` can reuse the `control_panel_resource_fields` and `control_actions`
sections as the first metadata/control contract boundary.

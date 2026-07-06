# Registry-Driven Serving Runbook

Date: 2026-07-05
Scope: W3 registry-driven serving contract.

## Purpose

The serving API should load the promoted local registry metadata instead of
returning placeholder prediction behavior. The current local source of truth is:

```text
artifacts/registry/vision-baseline/latest.json
```

In Docker Compose, the API container reads the mounted path:

```text
/app/artifacts/registry/vision-baseline/latest.json
```

## Runtime Contract

`/ready` must report:

- `status=ok`
- `mlflow_ready=true`
- `model_loaded=true`
- `model_name`
- `model_version`
- `model_stage`
- `dataset_version`
- `validated_parquet_uri`
- `registry_path`

`/predict` must report:

- `placeholder=false`
- `model_name`
- `model_version`
- `model_stage`
- `dataset_version`
- `validated_parquet_uri`
- prediction derived from the loaded model artifact

`/metrics` must expose:

```text
evm_serving_model_loaded
evm_serving_model_version
evm_serving_model_info
```

## Rollback

Rollback is performed by changing the promoted registry pointer:

1. Inspect available registry versions:

   ```powershell
   Get-ChildItem artifacts\registry\vision-baseline\v*.json
   ```

2. Replace `latest.json` with the selected known-good version:

   ```powershell
   Copy-Item artifacts\registry\vision-baseline\v<N>.json artifacts\registry\vision-baseline\latest.json -Force
   ```

3. Recreate or restart the API service:

   ```powershell
   docker compose up -d api
   ```

4. Verify the serving contract:

   ```powershell
   Invoke-RestMethod http://localhost:8000/ready
   Invoke-RestMethod -Method Post http://localhost:8000/predict `
     -ContentType "application/json" `
     -Body '{"image_uri":"s3://raw/sample_0001.jpg","features":{"width":640,"height":480}}'
   ```

5. Run pipeline smoke checks:

   ```powershell
   python scripts\run_pipeline.py deploy-check --config configs\local.toml
   python scripts\run_pipeline.py monitor-check --config configs\local.toml
   ```

## Failure Behavior

If the registry file is missing or invalid:

- `/ready` returns `status=degraded` and `model_loaded=false`.
- `/predict` returns HTTP `503`.
- `deploy-check` fails because the W3 serving contract is not satisfied.

This is intentional. The API should not silently fall back to placeholder
prediction behavior after W3.

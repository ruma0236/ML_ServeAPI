# W3 Preflight Runbook

## Purpose

Run this check before starting W3 implementation. It verifies that W2 handoff
artifacts and W3 backlog contracts are present without changing Jira status or
marking any W3 task as in progress.

This is intentionally not a W3 implementation step. It does not load the model
in the API, submit a remote job, or collect remote artifacts.

## Command

```powershell
python scripts\dev\w3_preflight.py
```

JSON output:

```powershell
python scripts\dev\w3_preflight.py --json
```

## Checks

The preflight validates:

- local config exists,
- Airflow DAG exists,
- W2 dataset metadata contains `dataset_version`, `validated_parquet_uri`,
  `record_count`, and trace metadata,
- local registry `latest.json` points to the same dataset version and validated
  Parquet URI,
- `ruma_macmini` worker config has SSH execution fields,
- serving API still exposes the current placeholder gap for `EVM-053`,
- W3 issue ids are still present in the issue register.

## Expected Pre-W3 Result

The expected pre-W3 result is:

```text
ready_for_w3_start=True
status_counts={'pass': 6, 'warn': 1, 'fail': 0}
```

The single warning is expected before W3:

```text
serving gap: predict endpoint is still placeholder, which is expected before EVM-053
```

Treat any `fail` result as a blocker before starting W3.

## W3 Start Guardrails

- Do not transition W3 Jira tasks until actual implementation starts.
- Preserve `dataset_version` and `validated_parquet_uri` across remote jobs,
  registry metadata, readiness responses, and serving metrics.
- Keep `trace_id`, `git_commit`, and `git_branch` in every new W3 artifact.
- Make model serving registry-driven before removing the placeholder flag.

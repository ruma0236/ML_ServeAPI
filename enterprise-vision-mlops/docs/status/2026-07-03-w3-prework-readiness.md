# W3 Prework Readiness

Date: 2026-07-03

Branch: `codex/mac-mini-worker`

Scope: W3 preflight only. No W3 Jira task was moved to `In Progress`, and no
W3 implementation task was marked `Done`.

## Summary

W3 can be started from the current W2 handoff state. The preflight check found
no blocking failure.

```text
ready_for_w3_start=True
status_counts={'pass': 6, 'warn': 1, 'fail': 0}
```

The only warning is expected before W3:

```text
serving gap: predict endpoint is still placeholder, which is expected before EVM-053
```

## Handoff Values

| Field | Value |
|---|---|
| Dataset version | `public-vision-local-3cafd20ac032` |
| Validated Parquet URI | `s3://validated/public-vision-local/public-vision-local-3cafd20ac032/validated/validated_dataset.parquet` |
| Registry model | `vision-baseline` |
| Registry version | `13` |
| Registry stage | `Production` |

## Preflight Checks

| Check | Status | Detail |
|---|---|---|
| local config | pass | `configs/local.toml` |
| Airflow DAG | pass | `orchestration/airflow/dags/enterprise_vision_mlops_daily.py` |
| dataset metadata | pass | `public-vision-local-3cafd20ac032`, `8` records |
| registry latest | pass | `vision-baseline` version `13`, stage `Production` |
| worker config | pass | `ruma_macmini` has SSH execution fields and `4` roles |
| serving gap | warn | `/predict` still returns placeholder output before `EVM-053` |
| W3 backlog state | pass | W3 issue ids exist in `docs/issues/issue-register.md` |

## W3 Implementation Order

Start with the serving track because it closes the biggest portfolio gap:

1. `EVM-051`: load the promoted local registry artifact in the API.
2. `EVM-052`: expose model version, stage, dataset version, and load status in
   `/ready`.
3. `EVM-053`: remove placeholder prediction and use the promoted artifact.
4. `EVM-054`: expose model version metrics to Prometheus.
5. `EVM-055`: document rollback-ready registry selection.

Then move to the remote execution track:

1. `EVM-041`: define the structured remote job spec.
2. `EVM-042`: run a mac-mini ARM64 evaluation job.
3. `EVM-044`: emit worker resource reports.
4. `EVM-045`: collect remote artifacts back into the control-plane.

## Guardrails

- Keep W3 tasks in `Planned` until implementation actually starts.
- Preserve `dataset_version` and `validated_parquet_uri` as immutable inputs.
- Carry `trace_id`, `git_commit`, and `git_branch` into new W3 artifacts.
- Do not describe the mac-mini as a GPU cluster substitute; keep it positioned
  as ARM64, edge, and heterogeneous worker validation.

## 2026-07-05 Expanded Target Guardrail

The enterprise target has expanded toward VLM and multimodal workloads, but W3
should not jump directly into VLM implementation. W3 is the serving contract
foundation for that future work.

Required W3 outcome before VLM/multimodal execution starts:

- API loads the promoted registry artifact instead of returning placeholder
  behavior.
- `/ready` exposes model load state, model name, version, stage, and dataset
  version.
- `/predict` uses the promoted artifact and returns model/dataset metadata.
- Prometheus exposes model version and request metrics.
- Rollback-ready registry selection is documented.

## Command

```powershell
python scripts\dev\w3_preflight.py
```

Runbook: `docs/runbooks/w3-preflight.md`

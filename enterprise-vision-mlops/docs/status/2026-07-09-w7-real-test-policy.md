# 2026-07-09 W7 Real-Test Policy Guard

## Summary

`EVM-238-A` implements the W7 no-mock/no-smoke policy guard. It prevents W7
model or pipeline closure from relying on mock adapters, placeholder
predictions, synthetic-only fixtures, or smoke-only checks as completion proof.

This is the policy half of the `EVM-238` umbrella. `EVM-238-B` remains open
until `EVM-237` produces actual EfficientNet MLflow runs, F-drive artifacts,
metrics, split manifest, GPU profile, environment report, confusion matrices,
and `CycleRun.model_matrix` evidence.

## Implementation

- `src/evm/control_panel/real_test_policy.py`
  - validates `CycleRun.model_matrix.real_test_policy`;
  - blocks `mock_allowed=true`;
  - blocks `smoke_allowed=true`;
  - requires real dataset and real training policy flags;
  - blocks placeholder serving state;
  - scans W7 Done closure records for forbidden mock, placeholder,
    synthetic-only, or smoke-only evidence claims;
  - allows guard language such as "blocks mock adapter" so policy documents are
    not incorrectly flagged as violations.
- `tests/test_w7_real_test_policy.py`
  - covers strict policy pass, weak policy failure, placeholder failure,
    forbidden Done evidence failure, and guarded-language allowance.

## Evidence

F-drive source-of-truth evidence root:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/real_test_policy/evm-238-a-20260709T111244Z/real_test_policy_report.json`

Latest validated policy report:

- `cycle_id`: `cycle-w7-visa-open-data-f1f1c9ee9922-vision-baseline-v10`
- `valid`: `true`
- `checked_records`: `21`
- `checked_done_records`: `2`
- `mock_allowed`: `false`
- `smoke_allowed`: `false`
- `requires_real_dataset`: `true`
- `requires_real_training`: `true`

## Verification

```powershell
C:\Users\opop0\miniconda3\python.exe -m py_compile src\evm\control_panel\real_test_policy.py tests\test_w7_real_test_policy.py
C:\Users\opop0\miniconda3\python.exe -m pytest tests\test_w7_real_test_policy.py tests\test_control_panel_aggregation.py tests\test_control_panel_contract.py -q
C:\Users\opop0\miniconda3\python.exe -m evm.control_panel.real_test_policy --cycle-json F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\control_panel\evm-224-20260709T110004Z\cycle_run.json --issue-register docs\issues\issue-register.md --report F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\real_test_policy\evm-238-a-20260709T111244Z\real_test_policy_report.json
```

Result:

- Python compile passed.
- `10 passed`
- Real-test policy report returned `valid=true`.

## Closure

`EVM-238-A` is complete as a policy guard. `EVM-238` umbrella stays open because
`EVM-238-B` depends on actual `EVM-237` EfficientNet execution evidence.

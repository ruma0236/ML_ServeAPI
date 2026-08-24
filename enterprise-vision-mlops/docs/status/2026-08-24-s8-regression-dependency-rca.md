# S8 Full Regression Dependency RCA

- Date: 2026-08-24
- Scenario: S8 Dependency Soak & Resource-efficiency Closure
- Affected gate: current-revision full Python regression
- Runtime result impact: none; closure remains pending until rerun

## Failure

The first full Python regression attempt stopped during collection because
`tests/test_s7_runtime.py` imports the S7 runner, which imports `PIL.Image`, but the
project and API container dependency manifests did not declare Pillow. The failed
private log is retained as `full-python-attempt-01.log` with zero closure credit.

## Root Cause

Image/VLM execution paths and EfficientNet serving import Pillow at runtime, while
the package manifest and API image requirements relied on incidental environment or
workload-image installations. The checked-in `uv.lock` also did not contain the
resolved project dependency graph.

## Remediation

- Pin `pillow==12.2.0` in `pyproject.toml` and `apps/api/requirements.txt`.
- Regenerate `uv.lock` from the authoritative project manifest.
- Add a container-contract regression that requires the same Pillow pin in both
  manifests.
- Install the exact pin in the isolated closure test runtime and rerun the complete
  current-revision suite with real PostgreSQL.

## Acceptance Boundary

This remediation closes dependency reproducibility for the implemented local
runtime and container build. It is not evidence of production supply-chain policy,
vulnerability management, or multi-platform image certification.

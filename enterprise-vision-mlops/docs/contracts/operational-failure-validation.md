# Operational Failure Validation Contract

Date: 2026-08-01
Contract version: `evm.operational_failure_evidence.v1`
Owner issue: `EVM-265 / SCRUM-171`

## Purpose

This contract defines the evidence required to claim that a local operational
failure was detected, contained, mitigated, recovered, and verified. A test is
not complete merely because a command returned zero or a Pod became Ready.

The contract applies to the five scenario runbooks under `docs/runbooks/` and
to the final VisA end-to-end operations drill.

## Environment Boundary

- Runtime: one Windows workstation, Docker Desktop Kubernetes, WSL2 GPU-PV,
  one NVIDIA GPU, local Docker Compose services, and host worker processes.
- Traffic: controlled replay or operator-generated requests only.
- Not proven: multi-node availability, real production traffic, tenant
  isolation, business KPI experiments, or a contractual production SLA.
- A result must use `local_operational_validation` as its claim class. It must
  not use `production_proof` or `business_ab_test`.

## Evidence Root

Original evidence belongs outside Git:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/
  failure_scenarios/<scenario_id>/<run_id>/
```

Git stores code, tests, versioned configuration, runbooks, and a small summary
index. Model binaries, datasets, raw logs, and repeated telemetry samples stay
on the F drive.

## Required Envelope

Every `validation-report.json` must contain the following logical fields. The
runtime implementation may add fields but may not omit these.

| Field | Requirement |
|---|---|
| `schema_version` | exactly `evm.operational_failure_evidence.v1` |
| `scenario_id` | one of `A`, `B`, `C`, `D`, `E`, or `CROSS` |
| `run_id` | immutable unique execution identifier |
| `claim_class` | exactly `local_operational_validation` |
| `status` | `passed`, `failed`, `blocked`, or `rolled_back` |
| `started_at`, `finished_at` | UTC timestamps used to calculate duration |
| `actor`, `approval` | operator identity and approval decision, if required |
| `source` | commit, branch, dirty state, API revision, worker revision |
| `environment` | cluster context, node, namespaces, hardware and runtime versions |
| `identities` | dataset, split, model, artifact, image and rollback digests |
| `preconditions` | named checks with observed values and pass/fail state |
| `injection` | method, parameters, target, expected effect and blast radius |
| `signals` | telemetry/log/lineage sources and first detection timestamps |
| `decision` | expected and observed policy result with blocker codes |
| `mitigation` | containment action and result |
| `recovery` | recovery/rollback action, target identity and measured time |
| `postconditions` | readiness, inference, metrics and identity checks |
| `artifacts` | URI, SHA-256 and media type for every evidence object |
| `limitations` | residual risk and claims explicitly not supported |
| `portfolio` | competency, interview questions, trade-offs and factual claims |

## State Semantics

- `passed`: all required preconditions, detection, containment, recovery,
  identity, and postcondition checks passed.
- `failed`: the scenario ran but one or more required checks failed. Failure
  evidence is retained and the next dependent scenario remains blocked.
- `blocked`: execution was not started or was stopped before injection because
  a safety, approval, baseline, dependency, or evidence precondition failed.
- `rolled_back`: the injected condition was contained and an exact known-good
  identity was restored, but the scenario acceptance still decides whether the
  overall result is `passed` or `failed` in its summary.

No empty artifact directory, dry-run, fixture-only check, or successful shell
exit is sufficient for `passed`.

## Safety Gate

The executor must fail closed before fault injection when any of these apply:

1. Git source, API, worker, and observer revisions cannot be reconciled.
2. The baseline is already unhealthy.
3. The target is outside the allowlisted local namespaces.
4. An immutable known-good model/artifact identity or rollback reference is
   missing.
5. Another lifecycle or failure scenario owns the target resource.
6. The requested action is cluster-wide or production-impacting and approval
   is absent.
7. The evidence root is unavailable or cannot be written atomically.

Canonical datasets and approved artifacts are read-only. Corruption tests use
isolated copies under the scenario evidence root.

## Timing

The report must distinguish:

- time to detect: injection timestamp to first valid alert/state signal;
- time to contain: injection timestamp to traffic/work mutation stop;
- time to recover: injection timestamp to all postconditions passing;
- observation window: first to last sample used for a policy decision.

The initial local targets are detection within 30 seconds and serving recovery
within 300 seconds. They are engineering acceptance targets for this machine,
not production SLO claims.

## Validation Rule

The implementation must provide one deterministic validator command:

```powershell
python scripts/dev/validate_operational_failure_evidence.py `
  --report <absolute-path-to-validation-report.json>
```

The command does not exist at the contract-only checkpoint. Scenario A cannot
start until the schema/model, validator, fixtures, and focused tests are
implemented and committed.

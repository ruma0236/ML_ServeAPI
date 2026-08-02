# Scenario D: Lifecycle Supervision Recovery

Issue: `EVM-269 / SCRUM-175`
State: Implemented and validated for the admitted local scope.

## Purpose

Validate worker/observer process termination, stale heartbeat and source
revision mismatch handling without duplicate work or stale-code execution.

## Existing Evidence

P0 commit `1c6e908` proved lifecycle-worker termination and supervisor restart.
It is a baseline reference only. Scenario D must independently prove observer,
stale heartbeat, revision mismatch, lease/fencing, restart budgeting, duplicate
ownership, and in-flight idempotency cases.

## Preconditions

- Canonical integrated start path owns supervisor and both children.
- Heartbeats contain PID, process start identity, timestamp, source revision,
  supervisor lease ID, and fencing token.
- Test owns the selected lifecycle run or no mutation is active.
- Current source/API/worker/observer revisions are captured.

## Safe Injection

1. Terminate one supervisor-owned child PID.
2. Use an isolated heartbeat fixture to exceed the 20-second stale threshold.
3. Start an isolated test child with a mismatched revision.

Do not stop Docker Desktop, databases, the API, or unrelated user processes.

## Signals

Supervisor/child PIDs, Windows command line, heartbeat age, revision match,
restart count/reason, lease/fencing token, restart budget, process census,
lifecycle claim, queue state, observer status, API-exposed worker/observer
health and stage audit.

## Success

- Five-second checks detect termination or heartbeat age over 20 seconds.
- Only the expected owned child restarts.
- Mismatched revision is never reported live.
- All components converge to the current commit.
- Restart reason/count survives process replacement.
- Restart budget/backoff prevents a restart storm.
- Duplicate or unknown ownership blocks without broad process termination.
- In-flight work resumes idempotently or becomes explicitly blocked; it is not
  executed twice.

## Failure and Recovery

Fail on PID reuse affecting another process, duplicate workers, stale state
reported live, lost audit, revision mismatch, or duplicate stage mutation.
Stop only supervisor-owned children and restart through the integrated stack
entrypoint.

## Interview Evidence

- Demonstrates: supervision, process identity, heartbeat semantics and idempotency.
- Expected questions: PID reuse; lease/heartbeat differences; split brain;
  exactly-once versus at-least-once recovery.
- Claim allowed: local dev-stack host-process recovery.
- Claim prohibited: distributed control-plane HA.

Detailed contract:
`docs/status/2026-08-02-scenario-d-lifecycle-supervision-contract.md`.

## Executable Proof

The current exact-child proof command is:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$head = (git rev-parse HEAD).Trim()
$branch = (git branch --show-current).Trim()
C:\Users\opop0\miniconda3\python.exe -m evm.operations.scenario_d_live `
  --policy configs\operations\scenario_d_supervision.toml `
  --project-root . `
  --output-root F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\operations\scenario-d `
  --source-commit $head `
  --source-branch $branch `
  --sequence lifecycle_worker,kubernetes_observer,lifecycle_worker `
  --cooldown-seconds 10
```

Run only with no active lifecycle work, a clean pushed revision, available
restart budget, exact single-process identities, healthy production CUDA
inference, GPU/device-plugin `1 / 1`, Prometheus `up`, and an explicit bounded
maintenance approval. The runner rejects any other sequence or cooldown.

Authoritative result and RCAs:

- `docs/status/2026-08-02-scenario-d-lifecycle-supervision-closure.md`;
- `docs/status/2026-08-02-scenario-d-heartbeat-closure-rca.md`.

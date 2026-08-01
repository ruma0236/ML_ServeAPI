# Scenario D: Lifecycle Supervision Recovery

Issue: `EVM-269 / SCRUM-175`
State: contract defined; implementation extension not started.

## Purpose

Validate worker/observer process termination, stale heartbeat and source
revision mismatch handling without duplicate work or stale-code execution.

## Existing Evidence

P0 commit `1c6e908` proved lifecycle-worker termination and supervisor restart.
The new scenario must preserve that evidence and add observer, stale heartbeat,
revision mismatch and in-flight idempotency cases.

## Preconditions

- Canonical integrated start path owns supervisor and both children.
- Heartbeats contain PID, command identity, timestamp and source revision.
- Test owns the selected lifecycle run or no mutation is active.
- Current source/API/worker/observer revisions are captured.

## Safe Injection

1. Terminate one supervisor-owned child PID.
2. Use an isolated heartbeat fixture to exceed the 20-second stale threshold.
3. Start an isolated test child with a mismatched revision.

Do not stop Docker Desktop, databases, the API, or unrelated user processes.

## Signals

Supervisor/child PIDs, Windows command line, heartbeat age, revision match,
restart count/reason, ownership lock, lifecycle queue state, observer status,
API-exposed worker/observer health and stage audit.

## Success

- Five-second checks detect termination or heartbeat age over 20 seconds.
- Only the expected owned child restarts.
- Mismatched revision is never reported live.
- All components converge to the current commit.
- Restart reason/count survives process replacement.
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

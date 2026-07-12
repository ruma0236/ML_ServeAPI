# AgentOps Reliability Contract

## Decision

EVM-212 is a validated post-MVP design, not a claim that LangGraph or an LLM
agent is running in production. The implementation boundary delivered here is
an executable AgentRun contract, a reliability policy, a validation command,
and explicit failure scenarios.

The contract uses durable checkpoints, thread-scoped state, human approval for
side-effecting tools, argument/result digests, idempotency keys, and a recovery
ledger. It keeps automatic deployment and automatic model promotion disabled.

## Runtime Contract

Every run must record:

- immutable agent, graph, and model versions;
- tenant, environment, thread, input digest, and checkpoint URI;
- each graph step with attempt number and checkpoint;
- every tool proposal and result without raw secret-bearing arguments;
- HITL decisions `approve`, `edit`, or `reject` for write/execute/deploy tools;
- checkpoint-based recovery with an idempotency key;
- evidence and audit URIs on the F drive.

LangGraph persistence saves graph checkpoints and enables pause/resume,
time-travel debugging, and recovery from the last successful step. Its HITL
flow pauses before a protected tool executes and resumes from persisted state
after a human decision. These are the basis for the local contract:

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langchain/human-in-the-loop

## Failure Policy

| Failure | Required behavior | Automatic action |
|---|---|---|
| Tool timeout after an external side effect | reconcile external state before retry | blocked |
| Worker restart during approval | restore checkpoint and pending interrupt | allowed |
| Duplicate resume request | deduplicate with idempotency key | allowed |
| Requester attempts self-approval | reject by separation of duties | blocked |
| Irreversible tool proposal | require named approver | blocked until approved |

## Verification

```powershell
$env:PYTHONPATH='src;.'
python scripts/dev/validate_agentops_reliability.py `
  --output F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/agentops/design-validation.json
python -m pytest tests/test_agentops_reliability_contract.py -q
```

Success requires `status=pass`, `design_only=true`,
`runtime_execution_claimed=false`, a pending HITL interrupt, no raw tool
arguments, and both automatic release flags disabled.

## Runtime Handoff

The next implementation must add Postgres checkpoint storage, a real LangGraph
run, one approved and one rejected side-effecting tool call, worker restart
recovery, duplicate-resume reconciliation, and Control Panel rendering of the
same AgentRun contract. Until that evidence exists, EVM-212 must be described
as reliability design completion only.

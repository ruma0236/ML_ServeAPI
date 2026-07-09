# 2026-07-09 W6 Control Panel API Contract

Issue: `EVM-223` / Jira `SCRUM-101`

## Result

`EVM-223` is complete. The Control Panel metadata and control API contract is
now defined in machine-readable OpenAPI form, with example payloads and a
developer-facing explanation.

## Files Added

- `contracts/control-panel/control-panel.openapi.json`
- `contracts/control-panel/examples/cycle-run.json`
- `contracts/control-panel/examples/command-intent.json`
- `docs/contracts/control-panel-api.md`

## Contract Coverage

The contract defines:

- cycle run detail
- latest cycle lookup
- runtime resource list
- orchestrator connection contracts
- Airflow/MLflow/Kubernetes task assignment
- command intent creation
- command confirmation
- command cancellation
- dataset version
- model version
- metrics
- promotion gate
- serving state
- pipeline stage timeline
- artifact references
- audit events

## Verification

Commands:

```powershell
python -m json.tool contracts\control-panel\control-panel.openapi.json
python -m json.tool contracts\control-panel\examples\cycle-run.json
python -m json.tool contracts\control-panel\examples\command-intent.json
```

Structured checks confirmed:

- OpenAPI version: `3.1.0`
- API paths: `8`
- schema count: `20`
- example payloads parse as JSON

## W7 Enterprise Supplement

The contract was expanded during the W7 enterprise-readiness audit in
`docs/status/2026-07-09-w7-enterprise-mlops-readiness-audit.md`.

Current W7 contract stats:

- contract version: `2026-07-09.w7.enterprise.v1`
- API paths: `8`
- schema count: `26`
- new W7 concepts: tenant/service scope, environment/promotion state, data
  pipeline readiness, experiment pipeline readiness, drift state, and CD/CT
  gate state

## Handoff

`EVM-224` should implement the first read-only cycle aggregation API using this
expanded W7 contract. `EVM-225`, `EVM-229`, `EVM-230`, `EVM-231`, and
`EVM-232` should use these schemas as the W7 UI/control baseline. Airflow task
assignment should read the orchestrator contract before enabling mutation,
because W6 local Kubernetes still controls external Compose Airflow rather than
in-cluster Airflow resources.

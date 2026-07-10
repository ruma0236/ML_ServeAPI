# 2026-07-10 W7 EVM-235 CI-Gated Deployment Intent

> Historical implementation checkpoint. Live apply and exact rollback are now
> complete in `docs/status/2026-07-11-w7-kubernetes-b7-closeout.md`.

## Decision

The EVM-235 implementation is complete, but operational closure remains `In
Progress`. Real GitHub CI and admission evidence now gate deployment intent
creation. The local system correctly refuses to create or execute an intent
while EVM-226 Kubernetes GPU proof and live EVM-236 readiness are blocked.

No real Kubernetes apply or rollback is claimed in this checkpoint.

## Implemented Boundary

- Repository-root GitHub CI executes the full Python suite, frontend contract
  tests and build, Compose render, Kustomize render, CycleRun validation, and
  real-test policy validation.
- CI emits an immutable bundle containing workflow identity, run id, commit,
  test results, evidence-validator result, image digest, config-render digest,
  contract digest, source URL, timestamp, and bundle digest.
- A second workflow downloads and validates that exact bundle before local
  admission is possible.
- The API admits only dry-run deployment intents after CI, EVM-236 readiness,
  and EVM-233 environment policy pass.
- The ledger enforces optimistic versions and records actor, timestamp,
  environment, namespace, artifact digest, reason, and result for every state
  transition.
- Production requires an allowed separate approver and an allowlisted
  namespace. Queue and approval-request actors are constrained to the intent
  participants.
- Only the disabled-by-default executor can call `kubectl`, only from `queued`.
  Rollback rejects invalid states before invoking any command.
- The Control Panel polls live cycle, resource, task, command, and deployment
  ledgers, renders CI/readiness/policy/executor signals, and exposes dry-run,
  approval, and queue controls without an API apply endpoint.
- A two-second server snapshot cache prevents repeated MLflow and evidence
  aggregation under concurrent dashboard reads.

## Real CI Evidence

- Implementation commit: `895533376760309a34b8a926f6eacd801284f05b`
- EVM-235 final implementation and evidence commit:
  `7db547cb9df05899769fda04d23da21dc4f53950`
- CI run: `29087199282`, conclusion `success`
- CI artifact: `8225236686`, archive SHA-256
  `47d80b812b8cd26b25ed95ddefd02f9b26cdc3be464a8537f459781a1c35a36e`
- Deployment admission run: `29087230617`, conclusion `success`
- Admission artifact: `8225244356`, archive SHA-256
  `415859efbda66c3cf38365962d36075712401f8af80645013821014585c6c684`
- Both artifacts were downloaded through the existing Git credential, checked
  against GitHub's archive digest, and copied to
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/ci/`.

## Live Result

After loading the CI artifact and restarting the API with the expected commit:

- API health: `ok`
- CI: `pass`
- CD: `pass`
- CT: `blocked`
- EVM-236 readiness: `blocked`
- EVM-233 promotion policy: `blocked`
- Deployment admission: HTTP `409`
- Admission blockers:
  - `artifact_readiness_not_ready`
  - `environment_policy_blocked`
  - `rollback_reference_not_ready`

The initial integration exposed a read-only mount error while persisting the CI
validation report. The report path now uses the nested writable W7 mount, and
report persistence failures return a blocked validation instead of taking down
the CycleRun API.

## Verification

- Python: `85 passed`, with two existing FastAPI `on_event` deprecation warnings
- Frontend contracts: `7 files / 19 tests passed`
- Type check and production build: passed
- Full Playwright: `14 passed` across desktop Chromium and MobileChrome
- Post-CI Gates and all-tab Playwright: `4 passed`
- Concurrent read proof: `24/24` HTTP `200` responses in `1.777s`
- Compose configuration and model-runtime Kustomize render: passed
- Static and live CycleRun schema validation: passed

Evidence root:

`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/deployment_intents/evm-235-verification-20260710T193530/`

Post-CI UI root:

`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/playwright/evm-235-post-ci-20260710T1940/`

## Cross-System Synchronization

- Jira `SCRUM-113` remains `In Progress`; implementation and claim-boundary
  comment: `10233`.
- Jira parent Epic `SCRUM-98` remains `In Progress`; checkpoint comment:
  `10234`.
- Notion detail page:
  `https://app.notion.com/p/39910ad2dcad81788a2aeee87e55d719`.
- Notion W7 Acceptance Matrix:
  `https://app.notion.com/p/39810ad2dcad810e97bfd525beee90b5`.
- Obsidian work log:
  `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-10 W7 EVM-235 CI-Gated Deployment Intent.md`.
- Obsidian Current Context Pack, Retrieval Index, Memory Hub, and Work Log Graph
  link to the new work log and preserve the operational blocker boundary.

## Closure Blockers

- Docker Desktop Kubernetes does not yet advertise `nvidia.com/gpu` for the B7
  workload, so EVM-226 is incomplete.
- Live EVM-236 readiness still rejects the current evidence set.
- Consequently no real intent can enter `queued`, and no real Kubernetes
  `applying`, `applied`, `failed`, or `rolled_back` artifact exists yet.
- The current actor fields are policy-audited local identities, not an external
  OIDC/RBAC identity provider. That is an enterprise hardening item beyond this
  local admission proof.

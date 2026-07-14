# EVM-261 Fleet Operations And Multi-Model Control

Date: 2026-07-14
Sprint: 2026-07-W8
Epic: EVM-EPIC-21 / SCRUM-156

## Outcome

The Control Panel now starts from fleet-wide live operations instead of one selected cycle. The primary navigation is grouped by operator intent:

- Overview: Operations, Runs, Resources
- Build: Pipeline Studio, Handoffs, Runtime Tasks
- Deploy: Models, Readiness, Quality & Drift
- Govern: Decisions

## Runtime Proof

- Kubernetes observation covers `evm-training`, `evm-staging`, and `evm-production`.
- `evm-production/evm-b0-production` is observed live at `1/1` ready replicas.
- `evm-staging/evm-b0-staging` and `evm-staging/evm-b7-serving` are visible as `0/0` scaled-down or rolled-back targets.
- Overview reconciles one active serving model and `1/1` allocated GPU from live Pod requests without counting completed Jobs or controller duplicates.
- Models shows three deployment targets and two promotion-ready model candidates on one page; release evidence and mutation controls remain collapsed until requested.

## Governed Authoring

- Data authoring explicitly separates the approved scenario catalog from a custom manifest and immutable split identity.
- Model authoring accepts a custom component only when the source commit and training/serving image digests are pinned and the runtime adapter is wired.
- `portfolio-custom-efficientnet-b0@0.1.0` was registered through the browser and persisted to `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w8/model_components/70f1da0872eae90b0d41.json`.
- Component versions are immutable and duplicate registration fails closed.
- The current executable custom-model boundary remains the proven EfficientNet adapter. New architecture families require a separately tested runtime adapter and artifact contract.

## Decision UX

- Readiness defaults to a release decision with the verdict, blocker count, and next action; technical evidence is a separate view.
- Governance defaults to a decision queue with outcome, impact, and next action; evidence and the new-decision form use progressive disclosure.

## Verification

- `npm --prefix apps/control-panel run lint`: passed.
- `npm --prefix apps/control-panel run test`: 47 passed.
- `npm --prefix apps/control-panel run build`: passed.
- Related Python integration suite: 78 passed.
- Desktop Chrome and Mobile Chrome core UX scenarios: 14 passed after selector updates and rerun.
- All-tab visual checks enforce no horizontal overflow and write evidence under `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest`.

## Explicit Follow-Ups

- EVM-244 remains planned for real A/B traffic routing and statistical rollback decisions.
- EVM-255 and EVM-256 remain planned for executable text and VLM runtime adapters.
- CPU and memory utilization still require an exporter-backed metrics contract.
- Multi-tenant SSO/RBAC and HA/DR evidence remain EVM-257 and EVM-258.

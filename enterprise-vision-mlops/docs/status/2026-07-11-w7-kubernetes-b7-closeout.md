# W7 Kubernetes B7 And Control-Plane Closeout

## Decision

W7 executable closeout is complete. The live evaluator returned
`closeout_allowed=true`, with 13 passing claims and zero required blockers.
This closes real VisA EfficientNet-B7 training, Kubernetes serving, artifact
readiness, staging policy, CI admission, deployment apply, exact rollback, and
desktop/mobile Control Panel proof.

This decision does not relabel the legacy `vision-baseline v11` lifecycle as
promotable. The top-level CycleRun remains `blocked` because its historical
baseline metrics and the open measured-drift review are still visible. The W7
B7 lifecycle and that legacy baseline state are separate claims.

## Real Training And Serving

- Kubernetes context: `docker-desktop`, Kubernetes `v1.34.1`.
- GPU: RTX 4080 SUPER, `nvidia.com/gpu=1`, device plugin `v0.18.0`.
- Dataset: VisA `visa-open-data-f1f1c9ee9922`, 10,821 records.
- Immutable split: train 6,504, validation 2,136, test 2,181.
- Training execution: `w7-k8s-b7-20260711T010003`.
- Candidate: `effnet-b7-img600-finetune-adamw`, seed `20260710`.
- MLflow run: `445be011d88a40ada5e70ab86de4f750`.
- Model SHA-256: `1d1df27fc20089688cc4efef8496169dcac700f65b84951190dc61fcc438337d`.
- Accuracy `0.972948`, F1 `0.864989`, AUROC `0.987418`.
- Calibrated decision threshold: `0.5115311741828918`.
- Serving image: `enterprise-vision-mlops-efficientnet-serving@sha256:073b63bb18a983ff36264068ec9addd353040e92d16c0209737a8ce0567d2d76`.

The Kubernetes proof includes a successful GPU Job, 1/1 Ready serving
Deployment, real VisA CUDA inference, controlled invalid-digest failure, and
recovery. Empty scheduled data now fails closed instead of replacing canonical
VisA evidence, and host/container artifact paths are validated bidirectionally.

## Readiness And Promotion

- EVM-236: `ready`, 13/13 content checks pass, zero blockers.
- EVM-233: staging / `evm-staging` decision `allow`.
- Real-test validator: four candidates checked, `valid=true`, violations `[]`.
- Closeout control-plane CI: run `29108780028`, commit
  `28f40d8f30a8bcd5703087e15640b06f80dff636`.
- Deployment execution CI: run `29108295585`, commit
  `4b668bf8586d7af179f2b173732f110b37e9ea80`.

## Audited Deployment Intent

Intent `deploy-3bfdb1f5a81ba507` completed:

`dry_run -> pending_approval -> approved -> queued -> applying -> applied -> rolled_back`

Apply bound the selected model URI, candidate ID, model SHA, and serving image
digest in one Kubernetes Pod-template patch. The applied model returned real
VisA CUDA inference with confidence `0.998601`.

Rollback did not use `kubectl rollout undo`. The executor parsed the approved
rollback reference, recomputed the artifact SHA, rejected same-model rollback,
and patched the exact approved baseline:

- rollback MLflow run: `b8ea73666eb54cd5a0ac20df021ac9f5`;
- rollback model SHA: `cb20160e287c3bab9ac9625056d8320715dbe218b4ba2d14cd3dcbf575ece7b4`;
- rollback F1 `0.840085`, AUROC `0.978900`;
- post-rollback Deployment: 1/1 Ready;
- post-rollback CUDA inference: `normal`, confidence `0.9972`.

The intent owns an immutable CI bundle copy and audit snapshot. Updating the
global latest CI evidence does not change the bundle used by this execution.

## Closeout And UI Evidence

- Closeout: 13 passed, 0 blocked, `closeout_allowed=true`.
- Python: 120 passed; only the existing FastAPI `on_event` warning remains.
- Frontend contracts: 19 passed; TypeScript and production build pass.
- Playwright: 14/14 scenarios pass in Desktop Chrome and Pixel 5 profiles.
- All five tabs were captured for both viewports.
- Mobile evidence URIs retain full values through `title` tooltips when truncated.
- The topology test reads the live Job state rather than assuming the historical
  `DeadlineExceeded` failure.

Primary evidence roots:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_b7/w7-k8s-b7-20260711T010003/`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/deployment_intents/deploy-3bfdb1f5a81ba507/`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/verification/w7-gates-20260711T012355/final-closeout-28f40d8/`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/verification/w7-gates-20260711T012355/ui-live-28f40d8/playwright-all-tabs-final/`

## Remaining Operational Boundary

- The cluster currently serves the approved rollback baseline by design. A new
  promotion requires a new CI-bound deployment intent.
- Measured input-category drift remains `review_required`; 128 records remain
  in label review and automatic retraining stays disabled.
- Airflow remains an external Docker Compose orchestrator controlled through
  its REST contract; W7 does not claim an in-cluster Airflow migration.
- The Mac mini is not part of this RTX 4080 Kubernetes execution proof.

## Post-closeout Stabilization

The 2026-07-12 EVM-214 replay found and corrected two post-run reproducibility
defects: runtime mount paths influenced dataset identity, and the selected B7
readiness evaluator followed mutable latest data evidence. The full VisA host
and Airflow-container replay now produces the same canonical dataset and shard
identity, while model-run-scoped readiness snapshots keep the selected B7
candidate `ready` after latest data advances. See
`docs/status/2026-07-12-post-w7-portfolio-stabilization.md` for the incident,
verification commands, and evidence index.

# W7 EVM-229/EVM-231 Topology And Timeline Drilldown

Date: 2026-07-09
Branch: codex/mac-mini-worker
Evidence root: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/topology_timeline/evm-229-231-20260709T215123Z0900`

## Scope

This closes the W7 Control Panel read-only visualization layer for:

- `EVM-229`: Kubernetes resource topology and animation UI.
- `EVM-231`: Live pipeline timeline and intermediate result drilldown.

The implementation remains read-only. It exposes resource action intent labels such
as `restart_dry_run`, `scale_dry_run`, `rerun_dry_run`, and `cancel_dry_run`, but it
does not mutate Kubernetes, Airflow, or MLflow resources. Actual mutation belongs
to `EVM-230` and `EVM-232`.

## Implementation Files

- `apps/api/control_panel.py`
  - added `GET /control-panel/v1/resources`;
  - derives normalized runtime resources from `CycleRun.resources` and stage-level
    resource refs;
  - adds Deployment, Job, Pod-template, Service, PersistentVolumeClaim, F-drive
    storage, node-pool, GPU, pressure, readiness, restart, and dry-run action
    fields.
- `src/evm/control_panel/schemas.py`
  - added `RuntimeResource` and `RuntimeResourceList`.
- `apps/control-panel/src/api/types.ts`
  - added frontend resource API types.
- `apps/control-panel/src/api/controlPanelClient.ts`
  - added `fetchRuntimeResources`, stage summaries, compact artifact labels, and
    resource pressure mapping.
- `apps/control-panel/src/views/PipelineTimeline.tsx`
  - added selected-stage drilldown, stage summary strip, and topology embedding.
- `apps/control-panel/src/views/KubernetesTopology.tsx`
  - added namespace/resource topology view.
- `apps/control-panel/src/components/StageDetail.tsx`
  - added metrics, artifacts, sample outputs, resources, and result drilldown.
- `apps/control-panel/src/components/ResourceNode.tsx`
  - added readable resource cards with status and pressure state.
- `apps/control-panel/src/components/ResourceDetailDrawer.tsx`
  - added resource allocation/readiness/action detail.
- `tests/control-panel/kubernetes-topology.*`
  - added API-backed topology contract and desktop/mobile e2e coverage.
- `tests/control-panel/pipeline-timeline.*`
  - added stage summary contract and desktop/mobile e2e coverage.

## Input Data

- `GET /control-panel/v1/cycles/latest`
- `GET /control-panel/v1/resources`
- Live local W7 cycle aggregation from VisA dataset metadata, quality report,
  curation state, lakehouse probe, MLflow registry metadata, lifecycle dashboard,
  drift queue, and EfficientNet real-test config.

## Output Artifacts

- `chromium-kubernetes-topology.png`
- `MobileChrome-kubernetes-topology.png`
- `chromium-pipeline-timeline.png`
- `MobileChrome-pipeline-timeline.png`
- `control-panel-api-8011.log`
- `control-panel-api-8011-rerun.log`
- `control-panel-ui-5174.log`
- `playwright/`
- `playwright-rerun/`

## Verification

Commands executed:

```powershell
C:\Users\opop0\miniconda3\python.exe -m py_compile apps\api\control_panel.py src\evm\control_panel\schemas.py
C:\Users\opop0\miniconda3\python.exe -m pytest tests\test_control_panel_contract.py tests\test_control_panel_aggregation.py tests\test_w7_real_test_policy.py -q
npm --prefix apps/control-panel run lint
npm --prefix apps/control-panel run test
npm --prefix apps/control-panel run build
npm --prefix apps/control-panel run test:e2e -- --grep '@w7-'
```

Results:

- Python contract and policy tests: `11 passed`.
- Vitest frontend contract tests: `3 passed`, `7 passed`.
- Control Panel production build: passed.
- Playwright desktop/mobile e2e: `4 passed`.
- Resource API returned `Job=5`, `Deployment=3`, `Pod=3`, `Service=3`,
  `PersistentVolumeClaim=2`.

## Success Criteria

- Namespace/resource grouping is visible for `evm-platform` and `evm-pipelines`.
- Job, Deployment, Pod-template, Service, PVC, GPU request, F-drive storage,
  node-pool, readiness, restart count, and pressure states are visible.
- Stage timeline shows pass/blocked/queued states from live `CycleRun.stages`.
- Stage detail shows metrics, artifacts, sample outputs, resources, and result
  text including `missing_or_blocked_evidence`.
- Screenshots are generated from desktop and mobile Playwright runs, not manual
  static mockups.

## Remaining Boundaries

- The topology is read-only and normalized from local CycleRun plus local runtime
  contracts. `EVM-226` still owns live `kubectl apply` execution proof.
- Task authoring and mutation lifecycle remain open for `EVM-230`.
- Command-intent audit and confirm/apply/cancel guardrails remain open for
  `EVM-232`.

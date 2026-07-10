# 2026-07-10 W7 Live Kubernetes Resource Observer

## Decision

The EVM-229 live resource topology follow-up is complete in implementation
commit `a4d318a`. EVM-226 remains `In Progress` and blocked.

The prior `/control-panel/v1/resources` response projected resource state from
CycleRun stages. It did not include the real `evm-b7-training` Job after that
Job changed from unschedulable `Pending` to `Failed`. The Control Panel could
therefore display a plausible topology without showing the authoritative
cluster condition.

## Implemented Boundary

- A host-side observer uses the existing local `kubectl` identity and writes a
  sanitized snapshot to the F-drive every five seconds.
- The API receives no kubeconfig, client key, token, Secret, environment value,
  or raw Pod specification. It reads only the normalized snapshot.
- Snapshot writes are atomic and retry transient Windows/Docker bind-mount file
  locks. A write failure cannot terminate the observer loop.
- State history excludes heartbeat timestamps, so a history artifact is added
  only when resource state changes.
- A snapshot older than 15 seconds is reported as `stale`; missing or malformed
  input is `unavailable`. Projected resources remain visible but are labeled
  `cycle_projection` instead of being represented as live Kubernetes state.
- The local stack startup script starts one hidden observer process and reuses
  its PID on later stack starts.

## API And UI

- `RuntimeResource` now exposes observation source/status/time, reason,
  message, replicas, and GPU capacity.
- `RuntimeResourceList` exposes snapshot status, age, cluster context, URI, and
  collection message.
- The API overlays live Kubernetes resources by namespace/kind/name while
  retaining external Compose and host-worker projections with explicit source
  labels.
- The Timeline topology defaults to a compact `Live` view and provides an
  `All` segmented control for projected resources.
- The UI shows live/stale age, failed Job reason/message, Node GPU capacity,
  desired/ready replicas, and data source in the detail drawer.
- Mobile navigation uses five stable tab columns so all tab labels remain
  visible without clipping.

## Live State

- Snapshot collection: `pass`
- Resource aggregate: `fail`
- Live resources: `6`
- Projected resources: `18`
- Live namespaces/scopes: `_cluster`, `evm-staging`, `evm-training`
- Node: `docker-desktop`, `warn`, `GpuNotAdvertised`
- Training Job: `evm-b7-training`, `fail`, `DeadlineExceeded`
- Training Job message: `Job was active longer than specified deadline`
- Serving Deployment: `evm-b7-serving`, `queued`, `ScaledToZero`
- Training and staging PVCs: `Bound`

The Job originally failed scheduling with `Insufficient nvidia.com/gpu`. After
its 7,200-second active deadline elapsed, Kubernetes recorded `Failed` with
`DeadlineExceeded` and removed the Pod. This changes the current phase, not the
root blocker: the node still has no `nvidia.com/gpu` capacity or allocatable
resource, and the NVIDIA device plugin reports `No devices found`.

## GitHub Evidence

- Commit: `a4d318a56941df9c1452f84823b338bb621ee605`
- CI run: `29089242017`, conclusion `success`
- CI artifact: `8226058173`, archive SHA-256
  `86183ae8b0548a1968f723a874691a9f7af12dc2e14bbcd37c2d532fe78cfa80`
- Deployment admission run: `29089282780`, conclusion `success`
- Admission artifact: `8226067057`, archive SHA-256
  `e98b4d5357586530e9f5c3f1e5f957ab3bd9f3c15dbed66266abbde63a772ac4`
- Both archives were digest-checked and imported to the F-drive. The live API
  validates CI commit `a4d318a56941df9c1452f84823b338bb621ee605`.

## Verification

- Python: `90 passed`, with two existing FastAPI `on_event` deprecation
  warnings
- Frontend contracts: `7 files / 19 tests passed`
- TypeScript lint and production build: passed
- Playwright: `14 passed` across desktop Chromium and MobileChrome
- Observer durability: `16/16` live samples over `80.8s`, `15` distinct
  timestamps, maximum snapshot age `5.85s`
- Compose config: passed
- model-runtime Kustomize client validation: passed
- CycleRun OpenAPI validation: `valid=true`

Evidence root:

`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_observer/evm-229-live-observer-20260710T2020-final/`

The root contains the sanitized snapshot, full resource API response, current
Kubernetes Job condition summary, CI proof, durability result, evidence index,
and desktop/mobile screenshots for every Control Panel tab.

## Cross-System Synchronization

- Jira EVM-229 / `SCRUM-107` remains Done; observer follow-up comment: `10237`.
- Jira EVM-226 / `SCRUM-104` remains In Progress; live-state correction
  comment: `10238`.
- Jira parent Epic `SCRUM-98` remains In Progress; checkpoint comment: `10239`.
- Notion observer detail:
  `https://app.notion.com/p/39910ad2dcad81b78036e58d55cfdb5e`.
- Notion W7 Acceptance Matrix and EVM-226 detail link the observer and preserve
  the GPU blocker boundary.
- Obsidian work log:
  `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-10 W7 Live Kubernetes Resource Observer.md`.
- Obsidian Current Context Pack, Retrieval Index, Memory Hub, EVM-226 work log,
  and Work Log Graph all link to the new record.

## Claim Boundary

This closes the EVM-229 observability gap: the UI now distinguishes live
Kubernetes state from projected topology and shows the real failed Job. It does
not close EVM-226. Kubernetes training, serving, controlled readiness failure,
and rollback still require a schedulable GPU resource.

# Live SmolVLM Local-Production Lifecycle Recording

Date: 2026-08-12 KST
Status: implementation and non-disruptive contract tests complete; live capture requires the recorded preflight to pass

## Purpose And Claim Boundary

The primary recording follows one fresh SmolVLM run created from the real Control
Panel. It shows ScienceQA intake, immutable data and source identity, bounded LoRA
adaptation on the RTX 4080, MLflow tracking, held-out VLM evaluation, artifact
sealing, independent staging approval, real CUDA staging inference, Prometheus
observation, independent local-production approval, and a persistent CUDA serving
endpoint.

This is evidence of a reproducible, guarded lifecycle on one Windows host, one
Docker Desktop Kubernetes node, and one GPU. The transformer production runtime
is a supervised Windows host-CUDA process because the current single GPU is
initially held by the Kubernetes B0 deployment. The recording does not claim
Kubernetes transformer serving, high availability, customer traffic, production
load, business A/B testing, or multi-cluster delivery.

## Reviewer Validation

| Reviewer | Question answered | Evidence visible in the recording |
|---|---|---|
| document screener | Is this a working MLOps system rather than a static dashboard? | one fresh run ID advances while real stages and training steps update |
| ML engineer | Are data, model, and evaluation reproducible? | exact ScienceQA view counts, model revision, source SHA, MLflow run, VLM metrics, artifact digest |
| platform engineer | Are scarce GPU transitions controlled? | exact B0 holder, independent single-use handoff, exclusive lease, host-CUDA target |
| SRE or AI infra interviewer | Does release fail closed and remain observable? | local CI admission, identity-bound approvals, applied intent, CUDA inference, Prometheus up, B0 rollback control |

Verdict: **PASS as a portfolio flow** when every acceptance check below passes in
the same fresh run. It is not valid if a past run is replayed as live state, if a
mock metric is displayed, or if the production endpoint is not the exact artifact
shown by the run.

## Main Recording Flow

### 1. Preflight

- Verify the API, Control Panel, Airflow, MLflow, Prometheus, and transformer
  workload worker are reachable.
- Verify the worker, API, and local CI evidence use the exact committed source
  revision.
- Verify Kubernetes reports one allocatable NVIDIA GPU, the device plugin is
  ready, and `evm-production/evm-b0-production` is exactly `1/1` before handoff.
- Verify no workload, GPU lease, or transformer production intent is already active.
- Record the B0 Deployment UID, Pod UID, source revision, preset digest, and
  local CI evidence digest.

### 2. Create One Fresh VLM Run

- Open `AI Workloads` in the real Control Panel.
- Select `SmolVLM / ScienceQA` from the governed preset catalog.
- Show that the bounded data view contains 32 train, 8 validation, and 8 test
  records. These are counts, not percentages or benchmark-scale training claims.
- Enter the requester and execution reason in the UI and click
  `Launch real workload`.
- Keep the resulting run ID visible for the remainder of the flow.

### 3. Intake, Identity, And GPU Handoff

- Observe Airflow execute the real scenario intake task.
- Observe the control plane re-hash the manifest and split identities.
- Enter a distinct platform approver in the UI and click
  `Authorize GPU handoff`.
- The handoff may scale down only the exact B0 Deployment UID and its one active
  Pod. Device-plugin, cluster-wide, source data, and unrelated workloads are out
  of scope.
- The workload worker acquires an exclusive fenced GPU lease after the B0 holder
  has reached zero Pods.

### 4. Real Adaptation, MLflow, And CT

- Observe all eight real LoRA steps and their measured loss in the live progress
  panel.
- Record the actual CUDA runtime, peak allocated VRAM, training duration, and
  adapter digest.
- Verify the exact MLflow run is `FINISHED` and includes the adapter and evaluation
  artifacts.
- Show the VLM-specific metric schema: held-out choice accuracy, parse rate, P95
  generation latency, evaluated record count, and GPU profile.
- Treat the held-out evaluation as isolated local CT. It is not a remote CI runner
  or an online production quality monitor.

### 5. Staging Validation

- Pause at `waiting_approval` after the artifact is sealed.
- Click `Approve staging` with an approver different from the requester.
- Start the exact adapter on the bounded staging port, issue a real ScienceQA image
  inference on CUDA, and expose model identity metrics.
- Verify Prometheus reports the exact run target `up`.
- Retire the bounded staging process, release the GPU lease, restore the B0 holder
  to `1/1`, and seal the ten-stage evidence index.

### 6. Local-Production Release

- Click `Create production intent` for the completed run.
- The intent must re-hash the adapter, evaluation, evidence index, and exact-source
  local CI evidence before entering `pending_approval`.
- Click `Approve local production` with the independent approver.
- The worker revalidates the B0 Deployment UID, scales only that holder to zero,
  starts the exact SmolVLM adapter as a persistent Windows host-CUDA service, and
  verifies `/ready`, a real `/infer`, and `/metrics`.
- Show the intent transition `pending approval -> queued -> applying -> applied`.
- Verify Prometheus reports the local-production target `up` and the Control Panel
  banner shows the same model, run, endpoint, and artifact identity.

### 7. Independent Evidence Views

- Open the exact MLflow run rather than relying only on the Control Panel summary.
- Open the transformer `/ready` endpoint and show CUDA, run ID, model revision,
  artifact digest, data identity, source commit, and local-production environment.
- Open the exact Prometheus target or query and show `up == 1`.
- Return to AI Workloads and capture the final applied state and release metrics.

## Acceptance Criteria

1. One fresh run ID is used from UI launch through local-production serving.
2. Intake, adaptation, evaluation, staging inference, deployment, production
   inference, and monitoring are actual executions; mock-only evidence is rejected.
3. API, worker, run, local CI evidence, and production intent share one exact
   40-character source revision.
4. The manifest, split, model revision, adapter, evaluation, evidence index, and CI
   evidence digests all revalidate before production approval.
5. Requester, GPU handoff approver, staging approver, and production approver obey
   the configured separation-of-duties checks.
6. Training progress advances through 8/8 measured steps; the UI does not infer
   step completion from the final run state.
7. VLM metrics are read from the fresh evaluation artifact and no LLM or
   classification metric is invented.
8. Staging reports real CUDA inference and is retired after bounded validation.
9. Local production reaches `applied`, `/ready` and `/infer` report the exact
   adapter on CUDA, and Prometheus reports the exact target up.
10. The production B0 holder is `1/1` before handoff and `0/0` only while the
    local-production transformer service owns the single GPU. The UI exposes an
    exact rollback action back to B0.
11. Browser console errors are zero and all retained screenshots are nonblank and
    free of incoherent overlap at 1440x900.
12. The source recording is real-time, constant 60 fps, H.264/AAC, seekable, and
    faststart enabled. No waiting period is shortened before user review.

## Failure And Recovery Rules

- A missing or stale worker, source mismatch, dirty scoped worktree, CI failure,
  artifact mismatch, ambiguous B0 target, self-approval, occupied serving port,
  empty inference, or missing Prometheus target blocks the next transition.
- A GPU handoff that fails to converge attempts exact B0 restoration and records
  whether restoration was confirmed.
- An interrupted production apply is reconciled on worker startup: any exact child
  process is stopped, Prometheus target state is restored, and B0 is restored only
  when its Deployment UID still matches.
- The source recording and failure evidence are retained. A failed run is not cut
  into a successful release story.

## Guard Companion Recording

The operational guard recording is a separate artifact so deliberate failures are
not confused with the successful release path.

| Guard | Lifecycle boundary | Visible result | Claim boundary |
|---|---|---|---|
| A GPU and serving | production serving and monitoring | exact target failure, bounded detection and B0 recovery | controlled local recovery, not HA |
| B bad model release | evaluation and release admission | deterministic quality or identity breach blocks intent | controlled replay, not user A/B |
| C drift and quality | intake and candidate evaluation | review hold and retraining candidate without automatic promotion | batch drift workflow, not business KPI drift |
| D lifecycle execution | worker and observer ownership | stale or stopped exact process recovery without duplicate effects | supervised single-host recovery |
| E integrity | data, artifact, and evidence identity | altered or missing digest blocks release with zero deployment intent | fail-closed local integrity admission |

## Recording Rules

- Record at 1440x900 and 60 fps. Mobile capture is outside this recording.
- Keep the source recording at real elapsed time. Editing and time compression occur
  only after user review.
- Captions explain why a control exists and use only facts observed in the fresh run.
- The pointer remains fixed on the control or evidence discussed in each scene.
- Titles, captions, filenames, and overlays use release-facing language and do not
  include draft-status labels.

# 2026-07-10 W7 Kubernetes B7 Execution Proof

> Historical blocked checkpoint. Superseded by the completed real execution
> and closeout in `docs/status/2026-07-11-w7-kubernetes-b7-closeout.md`.

## Decision

`EVM-226` remains `In Progress` and blocked. Docker Desktop Kubernetes is now
running, the workload and evidence paths are implemented, and Docker GPU
execution is proven. The Kubernetes node does not advertise `nvidia.com/gpu`,
so Kubernetes training, serving, failure rollout, and rollback cannot be
accepted as complete.

## Implemented Runtime

- Cluster: Docker Desktop Kubernetes, `kubeadm`, Kubernetes `v1.34.1`.
- Namespaces: `evm-training` and `evm-staging`.
- Storage: namespace-specific static PV/PVC pairs backed by
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops`; training is RWO and
  staging is ROX.
- Training: selected `effnet-b7-img600-finetune-adamw` only, full VisA split,
  CUDA required, no CPU fallback, `nvidia.com/gpu: 1` request and limit.
- Serving: B7 checkpoint loader with model SHA-256, candidate id, dataset
  version, CUDA, startup, readiness, and liveness checks.
- Deployment sequencing: serving starts at zero replicas so the single GPU is
  available to training first; successful proof scales serving after training.
- Runtime identity: the proof runner generates an evidence-local Kustomize
  overlay with immutable training and serving RepoDigests.
- Failure path: the successful path injects an invalid model digest, requires a
  failed readiness rollout, captures logs, and performs `kubectl rollout undo`.

## Final Kubernetes Evidence

- Run:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_b7/w7-k8s-b7-20260710T164828`
- Evidence index: `evidence_index.json`
- Git commit: `f2a59885185b6a3bfbc8e10980f21085fe291caa`
- Model SHA-256:
  `f5aafeb1060e10048359cbb78393ea2c7519cfa47f501bf8c6b065373c87fe47`
- Training image:
  `enterprise-vision-mlops-efficientnet-training@sha256:afb2abe6277c408e33243adc7169b64be82d6c015a6c8147394466dff0d5a06b`
- Serving image:
  `enterprise-vision-mlops-efficientnet-serving@sha256:840e4f757a8738e12764f475f54b29cdd6366e5212e0e11f9bf1c45b78f1cc88`
- Split-manifest SHA-256:
  `9e6698a6085d45dd57d58ddd673ede307150fa13dd3839dd370943193ef6acf6`
- Source shard-index SHA-256 enforced by config:
  `49584d29e7ebf7dd8d8f7e13fb54cd0ba81bdb60a892b92e1d43f80368cc4f7d`

Observed state:

```text
Docker Desktop Kubernetes: running
Node: docker-desktop Ready
Training PVC: Bound, 1Ti, RWO
Staging PVC: Bound, 1Ti, ROX
Training Job image: immutable RepoDigest
Initial Training Pod: Pending
Initial scheduler reason: 1 Insufficient nvidia.com/gpu
Current Training Job: Failed
Current Job reason: DeadlineExceeded after activeDeadlineSeconds=7200
Current serving replicas: 0 by sequencing policy
```

The NVIDIA device-plugin `v0.17.1` runs but reports `No devices found` and
points to the NVIDIA Container Toolkit/runtime prerequisite. Docker Engine has
an NVIDIA runtime and direct `docker run --gpus all` succeeds, but Docker
Desktop's embedded Kubernetes uses `cri-dockerd` and exposes no supported
`nvidia` RuntimeClass handler or allocatable GPU resource.

## Supplemental Docker GPU Proof

- Run:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_b7/docker_runtime/w7-docker-b7-20260710T164634`
- Status: `pass`, with `kubernetes_completion_claim_allowed=false`.
- Runtime: Torch `2.13.0+cu126`, TorchVision `0.28.0+cu126`, RTX 4080 SUPER.
- Real data: 10,821 records, 23 shards; train 6,504, validation 2,136, test
  2,181; one 600x600 tensor read from each split.
- Real inference: VisA `pcb3/Normal/0682.JPG` returned `normal`, confidence
  `0.921806`, CUDA latency `273.648 ms`.
- Controlled identity failure: an all-zero expected model digest returned
  readiness HTTP `503` with the actual model SHA-256.

This proves container packaging, data mapping, model identity, CUDA loading,
and real inference. It is supporting evidence only and does not replace the
required Kubernetes proof.

## Verification

```powershell
C:\Users\opop0\miniconda3\python.exe -m pytest -q
kubectl kustomize infra\kubernetes\model-runtime
scripts\dev\w7_docker_b7_runtime_proof.ps1
scripts\dev\w7_kubernetes_b7_execution_proof.ps1 -AllowBlocked
```

Results:

- Python: `59 passed`, with two existing FastAPI `on_event` deprecation
  warnings from `apps/api/main.py`.
- Kustomize client dry-run: namespaces, service, PVs, PVCs, Deployment, and Job
  all render and validate.
- Docker proof: pass.
- Kubernetes proof: blocked, and completion claims are disabled in the
  evidence index.

## Remaining Blocker

EVM-226 can close only when a supported Kubernetes runtime advertises at least
one schedulable `nvidia.com/gpu`. The same proof runner must then complete the
real B7 Job, create a new MLflow run and artifact checksum, deploy the resulting
model, execute real inference, capture the controlled failure, and complete the
rollback.

Resolving this requires either a supported NVIDIA runtime configuration for
Docker Desktop's Kubernetes node or an approved change to a GPU-capable local
Kubernetes target. Direct Docker GPU success must not be used to mark the issue
Done.

## Live Observation Follow-Up

Commit `a4d318a` adds a sanitized five-second Kubernetes observer and overlays
its state into `/control-panel/v1/resources`. The Control Panel now shows the
current Job phase `Failed`, reason `DeadlineExceeded`, the retained root GPU
capacity blocker, and the zero-replica serving state. The observer follow-up is
verified in
`docs/status/2026-07-10-w7-live-kubernetes-resource-observer.md`. This improves
operational visibility but does not change the EVM-226 completion boundary.

## Non-Blocking Follow-Up

The CUDA training and serving images are each about 3.74 GB. A shared pinned
CUDA/Torch base image and registry-backed BuildKit cache should be added after
the scheduling blocker is resolved to reduce duplicate downloads, local disk
use, and source-only rebuild time.

Local stack recovery exposed a Compose race in which three Airflow services
attempted to build the same image tag concurrently. Commit `ac60132` makes
`start_local_stack.ps1` build each unique image through one canonical service,
run `compose up --no-build`, and fail on non-zero Docker exit codes. The full
stack was then restored; Airflow, API, MLflow, MinIO, Prometheus, and Grafana
health endpoints all returned HTTP 200.

## Cross-System Sync

- Git implementation/status/runtime commits through `ac60132` are pushed to
  `origin/codex/mac-mini-worker`.
- Jira `SCRUM-104` remains `In Progress` under `SCRUM-98`, with labels
  `w7-p0`, `kubernetes`, and `gpu-blocked`; execution evidence comment:
  `10226`.
- Notion evidence page:
  `https://app.notion.com/p/39910ad2dcad817687e2d3102ce443b4`.
- Notion W7 Implementation Acceptance Matrix was updated with the EVM-226
  completion boundary and handoff.
- Obsidian work log:
  `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-10 W7 EVM-226 Kubernetes B7 Execution Proof.md`.
- Obsidian Retrieval Index, Current Context Pack, and Work Log Graph now link
  this checkpoint and supersede the old disabled-cluster state.

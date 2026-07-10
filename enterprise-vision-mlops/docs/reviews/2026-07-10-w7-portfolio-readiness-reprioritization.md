# 2026-07-10 W7 Portfolio Readiness Reprioritization

## Executive Judgment

The project is already a credible real-data MLOps engineering portfolio, but it
is not yet defensible as an enterprise production-ready platform. The strongest
evidence is the real VisA dataset cycle, four Torch EfficientNet candidate
runs, MLflow lineage, F-drive artifacts, and the live Control Panel. The main
gap is that several W7 issues were closed from schema and UI evidence while the
runtime policy or mutation they describe was not executed.

For a large-enterprise application, breadth should no longer be the priority.
The portfolio claim should be narrowed to one reproducible path:

`real VisA data -> B7 training -> evidence evaluation -> staging policy ->
Kubernetes serving -> monitored review event -> audited promotion decision`

Until that path is executed, use the claim "enterprise-oriented MLOps control
plane and real model lifecycle" rather than "enterprise production-ready
MLOps platform."

## Current Evidence Assessment

| Area | Current evidence | Objective assessment |
|---|---|---|
| Real data and model work | Real VisA split, four B0/B7 runs, MLflow runs, metrics, confusion matrices, model cards | Strong and portfolio-ready |
| Control Panel | Live CycleRun API, responsive UI, resource/timeline/gate views | Strong read-only operations surface |
| Kubernetes runtime | Manifests and render evidence; no active cluster, apply log, GPU Job, serving rollout, or pod artifact | Not complete |
| Tenancy and environment | Labels, owners, namespace, and blockers are visible | Presentation complete; policy enforcement incomplete |
| CD/CT | Gate fields and UI exist | CI evidence ingestion and deployment state machine incomplete |
| Readiness | Data/model checklist fields exist | Artifact-content evaluator incomplete |
| Drift | Existing queue count is converted into a heuristic score | Not a measured B7 baseline/current distribution comparison |

## Fixed Runtime Decision

W7 standardizes on **Docker Desktop Kubernetes**. Kind is not an active option
for this tranche.

Reasons:

- Docker Desktop is already running on the Windows RTX workstation.
- The repository already has Docker Desktop-oriented images, host storage, and
  Kubernetes manifests.
- `kind` is not installed and would add a second local-cluster support path.
- The workstation has an RTX 4080 SUPER and an NVIDIA container runtime, so
  the next proof should focus on making one cluster advertise and schedule the
  GPU resource.

The cluster remains blocked until its node advertises `nvidia.com/gpu`. CPU
fallback is not acceptable evidence for the selected B7 training Job or
serving Deployment.

## Priority And Dependency Order

| Order | Scope | Required outcome |
|---:|---|---|
| 1 | `EVM-226`, absorbing the execution scope of `EVM-227` | Run selected B7 training Job and serving Deployment on Docker Desktop Kubernetes with GPU request/limit, probes, logs, model artifact, and rollout evidence |
| 2 | `EVM-236` | Evaluate real data/model artifacts and return reproducible `ready` or `blocked` with evidence-level reasons |
| 3 | `EVM-233` | Make target environment and namespace change promotion eligibility; production requires complete gates and a distinct approver |
| 4 | `EVM-235` | Permit deployment intent creation only from passing CI and evidence-evaluator output; execute an audited deployment state machine |
| 5 | `EVM-234` | Compare B7 baseline and current input/confidence distributions and emit `review_required` without automatic retraining |
| 6 | `EVM-228` | Replay the complete path and publish a claim/evidence matrix with no UI-only completion claims |

## Issue Redefinitions

### EVM-226 - B7 Kubernetes Training And Serving Proof

`EVM-227` remains a historical design record, but its active serving acceptance
is transferred to `EVM-226`.

Required implementation:

- Docker Desktop Kubernetes context and GPU resource preflight.
- `evm-training` namespace for the selected B7 training Job.
- `evm-staging` namespace for the B7 serving Deployment.
- Immutable dataset version, split-manifest digest, Git SHA, image digest,
  model artifact digest, and MLflow run id.
- `nvidia.com/gpu: 1` request and limit on training and serving workloads.
- Readiness and liveness probes that verify the selected B7 model is loaded.
- Captured successful logs plus at least one controlled failed run and its
  diagnostic evidence.
- Serving request evidence and an explicit rollback target.

The first serving implementation should use the existing FastAPI inference
contract with the selected B7 artifact. Triton remains a later optimization;
adding another serving framework before the end-to-end path works would reduce
the depth of the evidence.

### EVM-236 - Evidence Readiness Evaluator

Replace file-presence checklist logic with an evaluator that parses and
cross-validates:

- data contract and schema version;
- split manifest, record counts, and dataset version;
- lineage graph and source dataset identity;
- MLflow run status, parameters, metrics, and artifact URI;
- model card identity and model artifact checksum;
- quality-gate decision and threshold details.

The evaluator must return `ready` or `blocked`, blocker codes, evaluated
evidence URIs, digests, and evaluation time. Dataset, run, model-card, and
artifact identities must agree. File existence alone is insufficient.

### EVM-233 - Environment Promotion Policy

Promotion eligibility must be computed, not displayed.

- Staging may queue a deployment when CI, data readiness, model readiness, and
  target namespace policy pass.
- Production requires all gates, an allow-listed production namespace, a
  rollback reference, and an approver who is different from the requester.
- Unknown owner, namespace mismatch, missing approval, mutable image tag, or
  missing artifact digest returns `blocked` with explicit policy reasons.

### EVM-235 - CI-Gated Deployment State Machine

CI must emit an immutable evidence bundle containing commit SHA, workflow run,
test result, evidence-validator result, image digest, and config render digest.
A deployment intent cannot be created unless that bundle and `EVM-236` both
pass.

The audited state path is:

`dry_run -> pending_approval -> queued -> applying -> applied`

Failure branches are `failed` and `rolled_back`. Every transition records
actor, timestamp, target environment, namespace, artifact digest, reason, and
result. Only the executor may mutate Kubernetes, and only from `queued`.

### EVM-234 - Review-First B7 Drift Event

Do not add a separate dashboard. Integrate the result into the existing Gates
and Timeline views.

- Build a baseline from the selected B7 validation split and prediction
  confidence distribution.
- Compare a current real-input window using a documented distribution metric
  and confidence quantiles/low-confidence rate.
- Emit `review_required` with dataset window, thresholds, measured deltas, and
  evidence URI when the policy is exceeded.
- Route the event to label review and approval. It must not directly trigger
  retraining or promotion.

## Portfolio Claim Gate

W7 can be presented as enterprise production-ready evidence only when all of
the following exist for one traceable cycle:

- successful GPU-backed Kubernetes B7 training Job;
- successful B7 serving Deployment and probe/request evidence;
- artifact-content readiness evaluation;
- environment policy decision for staging and a blocked/approved production
  example;
- CI-gated deployment state transitions and rollback evidence;
- measured drift `review_required` event from real inputs;
- Git, Jira, Notion, and Obsidian links to the same run and commit.

Until then, the honest portfolio positioning is "real model lifecycle plus an
enterprise-oriented control and governance layer under active hardening."

## Synchronization Evidence

- Git implementation/decision commit: `5f846d8`
- Jira:
  - `SCRUM-104` comment `10216`, label `w7-p0`
  - `SCRUM-105` comment `10217`, label `scope-absorbed`
  - `SCRUM-114` comment `10218`, label `w7-p1-1`
  - `SCRUM-111` comment `10219`, label `w7-p1-2`
  - `SCRUM-113` comment `10220`, label `w7-p1-3`
  - `SCRUM-112` comment `10221`, label `w7-p2`
  - `SCRUM-106` comment `10222`, label `w7-closeout`
  - `SCRUM-98` epic comment `10223`
- Notion:
  - W7 portfolio reprioritization page:
    `https://app.notion.com/p/39910ad2dcad813a8979cb46afd5e011`
  - Knowledge Base comment:
    `39910ad2-dcad-8131-8802-001db3e3c59f`
  - W7 Acceptance Matrix comment:
    `39910ad2-dcad-8147-bec4-001d7a4cef25`
- Obsidian:
  - `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-10 W7 Portfolio Readiness Reprioritization.md`
  - Current Context Pack, Retrieval Index, and Work Log Graph updated.

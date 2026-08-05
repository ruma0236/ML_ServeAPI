# Real VLM And LLM Scenario Lifecycle Expansion

Date: 2026-08-05
Epic: `EVM-EPIC-24 / SCRUM-194`
Status: In Progress; execution contract accepted, implementation not yet credited

## Objective

Extend the proven classification lifecycle with two real, independently run
GPU workloads:

1. ScienceQA image-text adaptation and evaluation with
   `HuggingFaceTB/SmolVLM-500M-Instruct` at immutable revision
   `a7da5b986cb59b408707209984f360a5f4ad7e47`;
2. governed Dolly instruction adaptation with
   `Qwen/Qwen2.5-0.5B-Instruct` at immutable revision
   `7ae557604adf67be50417f59c2c2f167def9a775`.

This is a controlled local single-node portfolio proof on one RTX 4080-class
GPU with 16 GB VRAM. It is not customer production, HA, a ScienceQA benchmark,
or proof of production-scale LLM throughput.

## Accepted Baseline And Gaps

| Area | Evidence-backed baseline | Gap closed by this workstream |
|---|---|---|
| ScienceQA intake | 512 real image-text records, immutable manifest and quality PASS under `F:/EnterpriseMLOps_Data` | current VLM execution is mock-only; no real train, MLflow, serving, inference or Prometheus closure |
| Dolly intake | 14,942 real instruction records and immutable split; quality is `review_required` | no audited PII-review disposition, train/eval adapter, model artifact or serving path |
| GPU | local CUDA PyTorch sees RTX 4080 and classification serving is operational | new workloads need an exclusive lease, bounded memory policy, teardown and identity evidence |
| Lifecycle | real Airflow, CUDA training, MLflow, CT, approval, serving and monitoring exist for EfficientNet | the run profile and serving contract are image-classifier-specific |
| Control plane | scenario catalog can launch real intake through Airflow | no model-family-neutral scenario run launch/status/progress contract |

Existing mock VLM reliability evidence is retained as historical contract-test
evidence only. It cannot satisfy any real VLM acceptance item below.

## Architecture Contract

The new `scenario workload lifecycle` is separate from the existing
EfficientNet profile but reuses its governance principles:

`Airflow intake -> identity/quality gate -> GPU lease -> bounded adaptation ->`
`MLflow -> isolated evaluation -> artifact seal -> approval/hold ->`
`staging serving -> real inference -> Prometheus/evidence closure`.

Every transition carries this identity tuple:

- scenario and lifecycle run IDs;
- source and split manifest SHA-256;
- model repository and immutable revision;
- tokenizer/processor revision;
- source Git revision and dirty-worktree flag;
- Python, PyTorch, CUDA, Transformers, PEFT and quantization runtime versions;
- compute backend, GPU UUID/name and peak allocated/reserved VRAM;
- training configuration digest, MLflow run ID and artifact digest;
- serving process/deployment identity and evidence-index SHA-256.

Unknown, stale, missing or mismatched identity fails closed. Intake quality in
`review_required` cannot be bypassed: a versioned filtering/review disposition
must create a new approved manifest before GPU work starts.

## Storage And Resource Policy

Canonical large data and evidence stay outside Git:

- datasets: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/scenarios`;
- Hugging Face cache: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/cache/huggingface`;
- adapters/models: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/models/scenarios`;
- run evidence: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scenario_workloads`;
- GPU lease/audit: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease`.

Only one VLM/LLM training or serving mutation may own the GPU lease. Workloads
run in the fixed order VLM then LLM, with CUDA cache release, process exit and a
fresh `nvidia-smi` snapshot between them. The existing B0 production target is
not mutated. New model serving is staging-only and must release its GPU before
the next workload.

The execution backend is recorded as `windows-host-cuda`. Kubernetes is not
claimed for these workloads unless a later proof actually schedules them there.

## Data And Adaptation Policy

### VLM

The existing ScienceQA image-bearing source is transformed into a new,
deterministic local adaptation split. Because the source originates from the
official test partition, resulting accuracy is only a pipeline/evaluation
signal and must not be reported as a ScienceQA benchmark. Training is bounded
LoRA/PEFT; full fine-tuning is not claimed.

### LLM

Dolly rows that trigger PII patterns are held out from train/evaluation. The
remaining rows require an explicit versioned review disposition before use.
Training is bounded LoRA, with QLoRA/4-bit used only if the actual Windows CUDA
runtime proves compatible. Quantized inference and full-precision adapter
training are reported separately. Full model fine-tuning is not claimed.

## Work Breakdown And Acceptance

### EVM-286 / SCRUM-195

- implementation: generic workload contracts, state store, GPU lease,
  lifecycle runner, MLflow/evidence helpers, tests;
- input: scenario config plus immutable intake evidence;
- output: run state, transition audit, identity envelope and evidence index;
- verification: focused Pytest contract suite plus stale identity, duplicate
  lease, resume and idempotency tests;
- success: allowed transitions close, concurrent GPU ownership is impossible,
  partial runs never render as completed and automatic production promotion is
  absent;
- blocker: missing exact identity, dirty source, unresolved quality review or
  runtime dependency mismatch.

### EVM-287 / SCRUM-196

- implementation: real SmolVLM processor/model adapter, ScienceQA collation,
  bounded LoRA train/eval, staging inference and metrics exporter;
- input: new immutable adaptation split derived from the 512 governed records;
- output: MLflow run, adapter artifact, model card, metric/confusion records,
  GPU profile, real image-text inference and Prometheus evidence;
- verification: one fresh lifecycle from intake identity through serving,
  independent artifact re-hash and post-run GPU cleanup;
- success: real weights and images are used, no mock adapter is imported, every
  stage reaches a truthful terminal state and inference is reproducible;
- blocker: license/policy breach, CUDA OOM after bounded fallback, model revision
  mismatch, missing artifact digest or non-exclusive GPU lease.

### EVM-288 / SCRUM-197

- implementation: Dolly review/filter transform, Qwen causal-LM adapter,
  bounded LoRA or proved QLoRA, isolated generation evaluation and staging
  inference;
- input: approved PII-filtered immutable Dolly split;
- output: review disposition, MLflow run, adapter/quantization artifacts, model
  card, evaluation, GPU profile, real generation and Prometheus evidence;
- verification: one fresh lifecycle after VLM teardown, approval/hold negative
  tests, independent artifact re-hash and runtime cleanup;
- success: quality gate is not bypassed, real CUDA adaptation and inference pass,
  and quantization claims match measured runtime behavior;
- blocker: unresolved review, unsafe/mismatched identity, unsupported Windows
  quantization runtime, CUDA OOM after bounded fallback or missing evaluation.

### EVM-289 / SCRUM-198

- implementation: scenario lifecycle API/status, Control Panel progress and
  blocker causes, cross-family inventory, evidence closure and regression tests;
- input: accepted VLM and LLM runs;
- output: comparable run summaries and one cross-family evidence manifest;
- verification: API schema tests, UI tests, classification regressions,
  Prometheus target checks and four-system cross-check;
- success: operators can distinguish queued/running/held/failed/completed,
  inspect exact model/data identity and see classification/VLM/LLM inventory;
- blocker: either real workload is absent, UI status contradicts run state, or
  Git/Jira/Notion/Obsidian point to different evidence.

## Promotion And Claim Boundary

- No workload automatically promotes to production.
- `completed` means every required stage and evidence hash passed; partial
  counters cannot imply completion.
- LoRA, QLoRA, 4-bit loading and full fine-tuning are different claims.
- ScienceQA licensing restricts this evidence to non-commercial research and
  portfolio use.
- Local controlled replay does not establish production throughput, HA, SLO,
  business A/B performance or large-model fleet operations.

## Four-System Checkpoints

The workstream synchronizes Git, Jira, Notion and Obsidian after: contract
acceptance; generic runtime implementation; each fresh VLM/LLM lifecycle;
cross-family closure. Planned work remains To Do and is never marked complete
from configuration or mock evidence alone.

## Generic Runtime Implementation Checkpoint

`src/evm/control_panel/scenario_workloads.py` now implements the first
`EVM-286` boundary:

- exact scenario/data/model/source identity resolution and digesting;
- fail-closed manifest/split/quality-disposition validation;
- dependency-aware workload stage transitions whose final stage cannot by
  itself mark a run completed;
- an exclusive cross-process GPU lease with run binding, fencing token,
  expiry, idempotent same-run acquisition and exact-identity release;
- immutable stage evidence re-hash and model artifact verification before the
  final evidence index and `completed` state are written;
- explicit `windows-host-cuda`, LoRA/QLoRA and quantization fields without
  claiming an unproved Kubernetes or quantized runtime.

Verification at this checkpoint:

- `tests/test_scenario_workloads.py`: `6 passed`;
- workload plus existing scenario-intake regression: `11 passed`;
- Control Panel contract regression: `8 passed`;
- changed-file Ruff: PASS;
- `git diff --check`: PASS.

This checkpoint does not close `EVM-286`: API/worker execution, real GPU
adapter invocation and fresh workload evidence remain required. It creates no
GPU lease or runtime mutation during tests.

## Transformer Runtime And Data-View Checkpoint

The host runtime now has a versioned pin set at
`infra/runtime/scenario-transformers/requirements.txt`. The existing
`F:/evm_w7_torch` environment proves the following versions in one process:

- PyTorch `2.13.0+cu126`, CUDA runtime `12.6`;
- Transformers `4.57.6`, PEFT `0.18.0`, Accelerate `1.12.0`;
- bitsandbytes `0.49.2` on Windows;
- RTX 4080 SUPER compute capability `8.9` and a real CUDA matrix kernel PASS.

This proves runtime import and CUDA execution, not 4-bit model loading; a
quantization claim remains blocked until the Qwen workload performs that exact
operation.

`scenario_preparation` adds immutable, idempotent derived views without
changing canonical intake data:

- ScienceQA local-adaptation view: source 512 -> 48 records (`32/8/8`), output
  manifest `b4d5b881...224cf`, identity `8bce3a7a...ed7a1`;
- every selected image is re-hashed before view creation;
- `source_split=test` remains explicit and the view states that it is not a
  ScienceQA benchmark;
- Dolly preparation requires a named approver and reason, removes all rows with
  PII flags, writes an identity-bound quality disposition and refuses overwrite
  conflicts.

The ScienceQA command was executed twice against the same F-drive root and
returned the original `created_at` and digests on the second call. Preparation
and workload tests pass `8/8`; Ruff passes. A one-record real SmolVLM CUDA
inference also returned `Answer: 1` for expected option `1` with approximately
`3,374 MiB` peak allocated, `3,928 MiB` peak reserved and `0.99 s` generation.
This is a runtime preflight, not the accepted VLM lifecycle run.

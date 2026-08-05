# Real VLM And LLM Lifecycle Validation Results

Date: 2026-08-05
Scope: `EVM-EPIC-24 / SCRUM-194`, `EVM-286..289 / SCRUM-195..198`
Status: Accepted for controlled local single-node validation
Implementation range: `6858631..d96e529`

## Verdict

PASS for the bounded portfolio objective. The platform executed one real VLM
and one real LLM lifecycle sequentially on the RTX 4080 SUPER through:

`Airflow intake -> identity/quality gate -> exclusive GPU lease -> real CUDA`
`adaptation -> MLflow -> isolated evaluation -> artifact seal -> approval ->`
`local staging serving -> real CUDA inference -> Prometheus -> teardown`.

Both accepted runs reached all 10 stages, released the GPU lease and retired
their bounded staging server. Two failed attempts remain immutable RCA evidence
and are shown as failed at 30% in the Control Panel.

## Governed Data Views

| Family | Source and view | Records and split | Identity evidence | Boundary |
|---|---|---:|---|---|
| VLM | ScienceQA image-bearing official test-derived source | 48 (`32/8/8`) | manifest `b4d5b881...224cf`; view identity `8bce3a7a...bed7a1`; split `6c5c161e...d1290` | deterministic lifecycle signal only; not a ScienceQA benchmark |
| LLM | Databricks Dolly 15k source -> approved filtered view | 320 (`256/32/32`) from 14,801 clean rows | source manifest `69941cf3...fb4f`; view manifest `98e339fb...90fa`; view identity `15b54c61...a8557`; split `87f1e551...74e3c` | 141 pattern-flagged source rows excluded; automated pattern filtering is not a privacy audit |

Canonical datasets, caches, models and evidence remain under
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops`; large artifacts are not
stored in Git.

## Accepted VLM Run

- Run: `scenario-workload-20260805T121300-966b6bc1`.
- Source revision: `8f3b4d6dced6e08125021829239ea0c798dead8a`.
- Airflow task/run: `task-20260805T121251-b6ad28e4` /
  `cp__20260805T121251-b6ad28e4`, success.
- Model: `HuggingFaceTB/SmolVLM-500M-Instruct` revision
  `a7da5b986cb59b408707209984f360a5f4ad7e47`.
- Adaptation: real BF16 CUDA LoRA, 8 steps, 409,600 trainable parameters,
  `32/8/8` records; 6.816490 s training.
- Evaluation: baseline/adapted option accuracy `0.75/0.75`, parse rate `1.0`,
  p95 evaluation latency `0.492075 s`, final loss `7.476597`.
- GPU: peak allocated `7,268.164 MiB`, peak reserved `8,374 MiB`.
- MLflow: `e5f5e081a3f742d78ff671b13e580e79`.
- Adapter digest: `9135f9045b92f2bb5914818e47d168187c327307b2ff2826a4d319b78cb6534b`.
- Staging proof: real CUDA response `2`, latency `1.200827 s`; Prometheus
  `up{job="evm-lifecycle-serving",evm_run_id="scenario-workload-20260805T121300-966b6bc1"}=1`.
- Evidence index: `624fbbadab02950a895eeb2ba40639e96bbb2b1f05b97c5b88af2363532d0393`.

## Accepted LLM Run

- Run: `scenario-workload-20260805T121811-dcee8c89`.
- Source revision: `d9374ce824157c1a63b5a56559a059adac4ccfab`.
- Airflow task/run: `task-20260805T121805-49bd46f5` /
  `cp__20260805T121805-49bd46f5`, success.
- Model: `Qwen/Qwen2.5-0.5B-Instruct` revision
  `7ae557604adf67be50417f59c2c2f167def9a775`.
- Adaptation: real CUDA QLoRA with actual NF4 int4 base model, 24 steps,
  540,672 trainable parameters; 8.432306 s training.
- Evaluation: baseline/adapted loss `2.148472/1.918441`, token F1
  `0.300604/0.343650`, non-empty rate `1.0`, p95 latency `5.113666 s`, final
  training loss `2.388472`.
- GPU: peak allocated `1,951.45 MiB`, peak reserved `2,042 MiB`.
- MLflow: `c91082f943234895b6d0f6352e5901a5`.
- Adapter digest: `326f95c3cba5cf29a02fb7798be0c282083da303359d350d27ddbd9861a85d58`.
- Staging proof: real CUDA response
  `The name of the third daughter is Lily.`, latency `1.269497 s`; Prometheus
  `up{job="evm-lifecycle-serving",evm_run_id="scenario-workload-20260805T121811-dcee8c89"}=1`.
- Evidence index: `488e958ca5a55e7ba12ff6fc8a4b3c19ab5fc832c10b14e72ac6e4df4c0f0653`.

## Failures And Remediation

| Run/attempt | Failure | Containment | Remediation |
|---|---|---|---|
| `scenario-workload-20260805T121052-d612e5ed` | MLflow artifact upload resolved the global default tracking URI | run failed at adaptation; GPU lease released; Prometheus target removed | set the global MLflow tracking URI before artifact logging (`8f3b4d6`) |
| Dolly preparation attempt | approved quality disposition replaced the source manifest, then failed exact-input validation | stopped before run/GPU mutation | accept only the exact source or current approved intermediate manifest (`4279906`) |
| `scenario-workload-20260805T121628-cf3b5313` | long prompt consumed the 512-token budget and left no supervised assistant token | run failed at adaptation; GPU lease released | bound prompt fields and require the exact requested number of usable held-out records (`d9374ce`) |

The failures are not counted as accepted model runs. They prove fail-closed
state handling and truthful partial progress rather than model quality.

## Control Plane And Verification

- API routes expose the run ledger, exact run detail and current GPU lease at
  `/control-panel/v1/scenario-workloads`.
- Control Panel `Build -> AI Workloads` shows four real runs: two completed and
  two failed. Failed runs stop at 30%, show the failing stage and RCA, and do
  not mark downstream stages complete.
- Desktop and `390x844` mobile browser checks passed. A mobile secondary-tab
  shrink/overlap defect was fixed at `d96e529`; the tabs now retain distinct
  touch targets with bounded horizontal scrolling.
- Browser console warnings/errors: `0` during the accepted and failed run
  inspections.
- Python focused suites: workload/API path `17/17` and API contract `13/13`.
- Control Panel: `17` test files / `54` tests, TypeScript lint and production
  build PASS.

## Resource And Promotion Guarantees

- One fenced GPU lease serializes VLM/LLM training and serving work.
- Every accepted and failed run released the lease.
- The existing EfficientNet B0 production target, device plugin, canonical
  source data and cluster-wide resources were not mutated.
- Approval is scoped to bounded local staging; staging is retired after
  inference and Prometheus proof. Automatic production promotion is absent.

## Claim Boundary

This evidence supports a claim that the local platform can extend a governed
classification lifecycle to two transformer families with immutable data/model
identity, parameter-efficient adaptation, MLflow lineage, staging inference,
observability and truthful failure evidence on one 16 GB GPU.

It does not support claims of customer production, HA, distributed scheduling,
production throughput/SLO, real-user A/B, complete privacy compliance, a
ScienceQA benchmark, full fine-tuning, or large-model fleet operation.

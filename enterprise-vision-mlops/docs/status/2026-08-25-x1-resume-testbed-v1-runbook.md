# X1 Resume Testbed v1 — Windows/RTX 4080 runbook

This is a deliberately separate `preliminary_controlled_testbed` with
`non_credit` evidence. It does not change the canonical X1 dependency gate or
claim completion of S8-V4.

## Purpose and fixed matrix

The testbed packages four governed HIGGS/Criteo-derived model paths into the
pinned Triton PyTorch GPU backend:

- HIGGS LogisticRegression from the S3 JSON artifact;
- HIGGS GaussianNB from the S3 JSON artifact;
- HIGGS TinyMLP from the S4 checkpoint and preprocessing registry;
- Criteo DLRM-lite with governed S5 rows and deterministic seeded test
  parameters (no training-quality or accuracy claim).

It runs four isolated Q0 CUDA diagnostics and 22 unique physical measurement
runs: four solo diagnostics plus serial, concurrent/batching-off,
concurrent/batching-on, L1W1 client-driver, L2W4 client-driver, and 70% DLRM-hot
cells. Each repeated cell runs three times. L1W1/L2W4 mean local client
admission lanes and load-driver workers; they are not deployed API replicas or
service worker processes. The actual API replica/worker topology is a follow-up
gap.

Every timed run has a fixed 10-second warmup and 30-second measurement window.
The direct driver offers 800 requests/second through a deterministic smooth
weighted schedule. The controlled batching pair uses the same L1W8 load and
topology with batching off/on; the on profile must prove
`nv_inference_count / nv_inference_exec_count > 1` in every repetition.
Every completed run must attain at least 90% of the 800 RPS offered-load
target and may not overshoot it by more than 5%. Serial, concurrent batch-off,
and concurrent batch-on achieved offered rates must remain within 5% across
repetitions and across their three-run medians; otherwise report generation is
blocked.
The minimum timed window is 14 minutes 40 seconds. Allow 25–50 minutes for
preparation, source hash checks, two Triton starts, Q0, queue drains, and exact
cleanup on the current host.

## Exact PowerShell execution

Run from Windows PowerShell on the RTX 4080 host, not from inside WSL. The
Triton container is the Ubuntu execution layer.

```powershell
Set-Location C:\Users\mlops\EnterpriseMLOps_Project\enterprise-vision-mlops

$DataRoot = 'F:\EnterpriseMLOps_Data\enterprise-vision-mlops'
$Python = 'F:\evm_w7_torch\python.exe'
$PrivateBase = Join-Path $DataRoot 'artifacts\scale_validation\private\s8-v4\x1-resume-testbed-v1'
$Revision = (git rev-parse --short=12 HEAD).Trim()
$Stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$ModelRepo = Join-Path $PrivateBase ("prepared-model-repository-$Revision-$Stamp")
$Evidence = "docs\status\evidence\s8-v4-x1-resume-testbed-experiment-$Revision-$Stamp.json"
$Report = "docs\status\evidence\s8-v4-x1-resume-testbed-report-$Revision-$Stamp.json"

& $Python -c "import torch, pyarrow, requests, pydantic; assert torch.cuda.is_available(); print(torch.__version__, torch.version.cuda)"

& $Python scripts\dev\prepare_s8_v4_x1_resume_testbed.py `
  --config configs\s8_v4_x1_resume_testbed_v1.toml `
  --data-root $DataRoot `
  --output $ModelRepo

& $Python scripts\dev\run_s8_v4_x1_resume_testbed.py `
  --config configs\s8_v4_x1_resume_testbed_v1.toml `
  --model-repository-root $ModelRepo `
  --private-base $PrivateBase `
  --output $Evidence `
  --report-output $Report `
  --maintenance-approved

$SuiteId = (Get-Content $Evidence -Raw | ConvertFrom-Json).suite_id
$SuiteRoot = Join-Path $PrivateBase $SuiteId

& $Python scripts\dev\validate_s8_v4_x1_resume_testbed.py `
  --config configs\s8_v4_x1_resume_testbed_v1.toml `
  --evidence $Evidence `
  --private-suite-root $SuiteRoot `
  --model-repository-root $ModelRepo
```

Both preparation and execution require a clean committed worktree. Preparation
fails closed on every governed source artifact/shard digest, and execution
fails closed on the committed source revision, profile repository digest,
exact GPU UUID/name, pinned Triton image digest, active GPU lease, queue state,
ports, B0 holder, and Prometheus 5/5 baseline.

## Evidence and claim rules

Q0 combines an isolated high-load window, exact `KIND_GPU` model-config
readback, a model-specific GPU-instance log line, per-model Triton success and
compute-duration deltas, nonzero device-busy samples, and an official Triton
`COMPUTE_START` timestamp. CPU fallback fails the run.
Tracing is sampled and enabled only in the isolated Q0 server; it is disabled
for the 22-run timed matrix to bound shutdown and evidence size.

The run separately records fixed-window completions/throughput and the terminal
counts for the admitted cohort after a bounded drain. It also records
offered/admitted/local-admission-rejected/5xx/loss/duplicate arithmetic,
latency and queue-wait percentiles, per-model progress, raw throughput fairness,
mix-normalized attainment fairness, GPU utilization/VRAM, topology identifiers,
and raw artifact hashes. Hot-mix evidence is invalid if any non-hot model makes
no progress.

The independent validator reopens every private attempt and recomputes summary
counts, fixed-window throughput, percentiles, per-model fairness, request
interval overlap, and formed-batch ratio from raw records, measurement bounds,
admission counters, and before/after Triton metrics. It also binds the immutable
`cleanup.json` contents and final-check digest to the public evidence. Cached
summary copies alone cannot authorize the resume report.

The built-in Triton timestamp trace is not a CUDA-kernel profiler. Therefore
the generated report always says `kernel_overlap_proved=false`; kernel overlap
may be claimed only after a separate direct profiler gate. Resume bullets are
generated only from validated measured fields and retain the preliminary,
single-node boundary.

Preparation, suite, evidence, report, and regenerated-report destinations are
immutable: every command fails if its output already exists. Use a new timestamp
for every attempt; never overwrite an earlier run.

Cleanup stops only the allowlisted X1 containers, releases only the exact X1
lease, restores the captured B0 UID/image to 1/1 and verifies CUDA inference,
checks the exact Prometheus jobs at 5/5, requires queue/lease/outcome-unknown
zero, rejects remaining Triton GPU processes or ports, and verifies VRAM
restoration. Independent cleanup actions continue even if an earlier action
fails.

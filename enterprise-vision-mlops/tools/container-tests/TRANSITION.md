# Container test-tool transition — 2026-09-09 KST

Historical CT-M07 report below. The later approved API recovery and successful actual smoke
are recorded in [the CT-R follow-up](RESUME-2026-09-09.md). R7 remains NO-GO; R8 is not started.

**Container migration is not complete. GLOBAL NO-GO.** CT-M00–M05 completed; CT-M06 is
BLOCKED by existing API runtime configuration. CT-M07 records this partial transition and
the exact approval boundary. No model prediction, full focused suite, training, rollout,
fault injection, 54/78-run experiment, soak, or Integrated V4 was started in this transition.

## Results and identities

| Task | Actual result |
| --- | --- |
| CT-M00 | Existing checkpoint, five dirty tracked files, 4,245 user untracked paths and original failures preserved; no existing Python test running |
| CT-M01 | A/B/C/D mapping saved; [full mapping](PLAN-MAP.json); no old FAIL changed to PASS |
| CT-M02–03 | One official Locust-based Linux/amd64 image; hash-locked export from existing `uv.lock`; build/pip check PASS; local image saved |
| CT-M04 | Four tool Python sources lint/format/compile PASS; actual OpenAPI captured read-only; no model execution |
| CT-M05 | Two distinct fresh containers, each 10/10 TOOL_ONLY checks PASS; correct expected failure/timeout/SIGTERM exits; no skipped checks |
| CT-M06 | Actual container-to-API GET `/health`=200, catalog=503 `capacity_registry_unavailable`; prediction requests=0; model smoke NOT_RUN |
| CT-M07 | Documentation and retained-gate handoff; overall transition still BLOCKED, not COMPLETE |

Image version: `0.1.0`; platform: `linux/amd64`.

```text
Tool local image ID: sha256:2c7a0ee83cc1f33740a480f6ee8d123a125de2c14bdeb985394d9e5b272605be
Official base platform digest: sha256:d1dd30fdb0940e4623606331bfdc5a37953c1820b49b3e2965badfec1de18338
Saved tool image SHA256: 4b14ab4811113d8690aecfbde655fe6ed3c04714df343679ca2012bffcee7325
Tested tool/source snapshot: d5cd2e916400f578bef6faf3aba591042cf50f89
Checkout HEAD: ae9c014fba1ffe4891c1e5835d7ca94595846683
Actual API image: sha256:4570d7208b55459eface30dea69a4c6ef05d6770e6b6a5bf3e8de1acd836c35a
Actual API revision: b9140adce0c9928a20c6c35ac29d42df7ac76d8c
```

No tool image was published to a registry. Docker's local RepoDigests field is not publication
evidence. See `image.lock.json`, `image.local.json`, and the saved archive. Runtime imports
observed Python 3.13.15, Locust 2.46.5, httpx 0.28.1, jsonschema 4.26.0, NumPy 2.5.2 and
Pydantic 2.10.3. The export correctly selects NumPy 2.5.2 for Python >=3.12; the Windows
Python <3.12 lock branch remains 2.4.6. No new Windows venv/Miniconda was created.

CT-M05 run IDs: `ct-toolcheck-01`, `ct-toolcheck-02`. CT-M06: `ct-readiness-01`.
All three owned containers were removed only after outside-container result/log capture.
Residual owned containers=0; the existing 18 container IDs/names/states stayed unchanged.
Temporary in-container files disappeared with these containers; persisted evidence is intentional.
Do not reuse these run IDs or overwrite their output directories.

## Files, profiles and results

Evidence root (private local files, not production authority):

```text
F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private\s8-v4\container-tool-transition-20260908T163633Z-01
```

Each `ct-m00-receipt.json` through `ct-m07-receipt.json` links its predecessor and task results.
Inspect `ct-toolcheck-01/result.json`, `ct-toolcheck-02/result.json`, their `host-run.json`,
and `ct-readiness-01/{result.json,host-run.json,catalog.body}`. Original HTTP failure bytes,
stdout/stderr, image/config inspection, exact argv/exit codes and pre-removal hashes are retained.

`profiles/smoke.ready.example.json` is intentionally non-executable until actual inputs and
identities are filled and hash-bound. It defines a closed-concurrency logistic smoke:
one user, three governed replay requests, 20-second pacing, 60-second ceiling, five-second
request timeout and 30-second drain ceiling. The tool also accepts one or two requests,
used for short fixtures; it never claims these are fixed open-arrival rates. Existing S3
guardrails remain fixed: p99 <=250ms, error rate <=0.01, generator lag <=100ms, host CPU <=90%.
This small sample cannot establish production performance or capacity.

Image/base/dependency changes require a new versioned image. Mounted executable changes get a
new code hash and tool checks; input/settings changes get a new profile/hash. Every run gets
a fresh container, unique ID, target identity and separate output directory. No runtime installs.
`requirements.txt` is generated from `uv.lock`, never a second manually maintained lock.

## Copyable management commands (PowerShell, project directory)

```powershell
$ctProject = 'C:\Users\mlops\EnterpriseMLOps_Project\enterprise-vision-mlops'
$ctEvidence = 'F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private\s8-v4\container-tool-transition-20260908T163633Z-01'
$ctDocker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$ctPython = 'F:\r7-m03-pythonbase-ace23615-01\python.exe'
$ctImage = (Get-Content -Raw "$ctProject\tools\container-tests\image.local.json" | ConvertFrom-Json).image_id
Set-Location -LiteralPath $ctProject
& $ctDocker image inspect $ctImage
Get-Content -Raw "$ctEvidence\ct-toolcheck-01\result.json" | ConvertFrom-Json
Get-Content -Raw "$ctEvidence\ct-readiness-01\result.json" | ConvertFrom-Json
```

Only if the base/dependency definition changes, export/build a NEW version; do not rebuild for
each run or overwrite the validated `0.1.0` tag. Example build for a deliberately new revision:

```powershell
& 'C:\Users\opop0\.codex-runtime\enterprise-vision-mlops-py311\Scripts\uv.exe' export --frozen --only-group container-test --no-emit-project --format requirements-txt --output-file tools/container-tests/requirements.txt
& $ctDocker build --platform linux/amd64 --tag evm-container-tests:0.1.1 tools/container-tests
& $ctDocker image inspect evm-container-tests:0.1.1 --format '{{.Id}}'
```

Verify/update the dependency/base lock bindings when deliberately changing them, record the
new image ID, save it, hash the archive and run tool checks before using it. Existing image reuse:

```powershell
# Optional new tool check; unique ID, not a retry of a failed run:
& $ctPython -B tools/container-tests/run_container.py --run-id ct-toolcheck-03 --input-directory "$ctEvidence\ct-m05-inputs" --output-root $ctEvidence --tool-script test_smoke.py --network none --timeout 180 --docker $ctDocker
# Actual smoke ONLY after readiness, telemetry baseline and READY profile freeze:
& $ctPython -B tools/container-tests/run_container.py --run-id ct-model-smoke-01 --input-directory "$ctEvidence\ct-m06-ready-inputs" --output-root $ctEvidence --network evm-local --timeout 90 --docker $ctDocker
```

The second input directory does not yet exist: this is the gated future command, not permission
to manufacture a READY profile. `run_container.py` captures/removes only its exact owned fresh
container. If interrupted, inspect the exact name and `evm.ct.run` label, capture logs and inspect
state, then stop if necessary and remove that exact container ID. Never prune or bulk-remove.

```powershell
# Recovery example only if this particular future run actually exists:
& $ctDocker inspect evm-ct-model-smoke-01 --format '{{.Id}} {{.Name}} {{json .Config.Labels}} {{json .State}}'
# After matching the exact run label, capture stdout/stderr/state before cleanup:
& $ctDocker logs --timestamps evm-ct-model-smoke-01 1> "$ctEvidence\ct-model-smoke-01-recovered.stdout.log" 2> "$ctEvidence\ct-model-smoke-01-recovered.stderr.log"
& $ctDocker inspect evm-ct-model-smoke-01 > "$ctEvidence\ct-model-smoke-01-recovered-state.json"
# Stop only if the inspected owned container is still running, then:
& $ctDocker stop --time 10 evm-ct-model-smoke-01
& $ctDocker rm evm-ct-model-smoke-01
```

Use new, nonexisting recovery filenames; do not overwrite prior captures. Reverting the test
tool means selecting the prior validated immutable image ID, not reverting services or data:

```powershell
Get-FileHash -Algorithm SHA256 "$ctEvidence\evm-container-tests-0.1.0-linux-amd64.tar"
# Only if the hash matches 4b14...7325 above and the local image is unavailable:
& $ctDocker image load --input "$ctEvidence\evm-container-tests-0.1.0-linux-amd64.tar"
& $ctDocker image inspect $ctImage
```

## Blocker and exact next micro-tasks

The running API has no `EVM_S3_CAPACITY_REGISTRY_PATH`. Its 503 body names the Windows `F:/...`
default, while its existing read-only data mount is `/mnt/evm-data`. Required setting:

```text
EVM_S3_CAPACITY_REGISTRY_PATH=/mnt/evm-data/artifacts/scale_validation/s3/capacity-registry.json
```

No setting was changed. First obtain explicit approval for the API-only configuration/recreation
and its brief service interruption, then read the actual Compose deployment metadata and choose
the matching existing image/config workflow. Do not issue an invented rebuild/deploy command.

1. CT-M06-CONFIG-APPROVAL: approve and scope that one API runtime change; preserve all other services.
2. CT-M06-READINESS-02: after the approved change, new ID/output; GET health/catalog once; save raw
   responses, exact target image and registry/model/dataset binding. Do not retry the unchanged 503.
3. CT-M06-CORPUS: use `freeze_corpus.py` inside the same image with readonly governed S3 files and
   readonly source config; it executes no model. CLI: `--registry <S3>/capacity-registry.json
   --features <S3>/higgs-uci-2014-seed-20260817-v1/splits/replay/features.npy
   --split-manifest <S3>/higgs-uci-2014-seed-20260817-v1/split-manifest.json
   --source-config <readonly>/s3_capacity_runtime.toml --output-dir <new-output>/corpus`.
   It hashes the exact seeded 32,768-row selection and emits the first three request bodies.
4. CT-M06-PROFILE: bind corpus/OpenAPI/catalog/model/artifact hashes and independently observed
   replica/worker identity in a fresh READY profile. Preserve the image/code/profile separation.
5. CT-M06-SMOKE: run the gated command above once, three actual requests. Baseline/preserve the
   shared OTEL file offset and Prometheus counters; collect only this run's trace IDs and post-offset
   spans. Require API/admission/worker/validation/transform/prediction spans, request/admission/worker
   counter deltas, zero six terminal queue/in-flight/outstanding gauges, and bounded drain/resources.
   Allow the existing 15-second Prometheus scrape lag; never substitute missing observations with zero.
6. CT-M07-CLOSE: only after actual model/identity/telemetry/output/cleanup PASS, report tool transition
   COMPLETE. No r7/r8/V4 or production performance completion is implied.

## Retained work and subsequent experiments

Original focused EXECUTION05 remains **1,298 passed / 5 failed / 1 governed skip** (1,304 unique),
not rerun. Prior scoped fixes/checks are preserved but do not constitute current full regression.
Unfinished Windows harness PATCH-11 draft was stopped by this directive: 2/19 native tests passed,
17 failed; NOT APPROVED/MUST_NOT_EXECUTE. Its original logs and source remain untouched.
The native seal sharing-conflict and launcher/runtime PID failures, Windows CUDA/Torch/private
lanes, independent elevated receipts and all original production admission/publisher guards remain.
Old capsule/run identities are not reused. External production WORM remains UNPROVISIONED/ZERO-CREDIT.

After tool transition closure, define one common baseline comparison between old/new measurement
paths before any full experiment. Keep old/new raw performance series separate. Then resume original
R7 unresolved lane → M04 aggregate → M05 → M06–M09 → M10–M15; only GO permits R8-M00–M11 and then
V4-M00–M11. Container-compatible and host-only lanes follow PLAN-MAP, not blanket replacement.
No final criterion is removed or relaxed.

The current X1 config defines 54 calibration repetitions =12 solo+18 topology+24 batch, and
78 matrix repetitions =18 serial+18 balanced+18 hot+24 batch. These were not run here.
`full_stack_3180` is a legacy named gate; a fresh exact-node manifest is still required, not numeric
padding. Windows ETW and 1,800-sample-per-lane/100ms/180s Windows–WSL dual collection remain host gates.

Source additions are present in the working tree, not committed or pushed. The real Git index was
not changed. CT-M07 documentation additions do not alter the executable hashes tested in CT-M05;
no full-current-tree regression claim is made. Receipts retain the exact tested snapshot above.

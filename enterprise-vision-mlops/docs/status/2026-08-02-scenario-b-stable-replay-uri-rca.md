# Scenario B Stable Replay URI RCA

Date: 2026-08-02  
Scenario: `EVM-267 / SCRUM-173`  
Router dependency: `EVM-244 / SCRUM-144`

## Detection

Run `scenario-b-quality-closure-20260802T031803Z-3058c67e` failed closed at
the newly required post-replay inference. A direct request returned HTTP 422
with `image is not readable` for:

`/mnt/evm-data/enterprise-vision-mlops/data/raw/industrial/visa/...`

The correct production serving path is:

`/mnt/evm-data/data/raw/industrial/visa/...`

The corrected URI produced a real CUDA EfficientNet-B0 response with the exact
stable model digest. Production readiness and Prometheus remained healthy.

## Root Cause

The production Deployment mounts the host project data root
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops` directly at
`/mnt/evm-data`. The replay command manually supplied a root that appended
`enterprise-vision-mlops` a second time.

The collector correctly converted the resulting HTTP 422 responses into
failed stable observations, but the initial evaluator contract only enforced
shadow sample count and model identity. It did not require every stable
authoritative observation to succeed. All four earlier post-RCA candidate
runs therefore contain `1,000 / 1,000` failed stable observations and cannot
close Scenario B, even where their challenger decisions and artifact hashes
are otherwise correct.

## Corrective Action

- Move the stable serving data root into the versioned Scenario B TOML config.
- Remove the manually supplied CLI data-root argument.
- Fail before challenger evaluation if any stable HTTP observation fails.
- Make stable authoritative success a typed `ControlledReplayResult`
  invariant.
- Add `stable_authoritative_observations_clean` as a required common-report
  postcondition.
- Retain exact-digest post-replay inference as a separate required check.
- Add regression coverage and rerun the complete operational suite.

## Evidence And Safety

- Correct direct B0 inference: `normal`, confidence `0.9782`, CUDA, model SHA
  `abcb8504...9a27f`.
- Production Deployment stayed at the same UID and `1 / 1 Ready`.
- Exact Prometheus target stayed `up`.
- No Kubernetes, data, model, or serving route mutation occurred.
- The failed closure run created no run evidence root and is identified by its
  immutable run ID in this RCA.

## Prevention

An authoritative baseline must be both identity-correct and successful. Model
digest equality on a failed HTTP observation is not sufficient evidence. Any
future replay path must use a versioned serving-root contract and require zero
stable-authoritative errors before quality or runtime decisions are accepted.

## Claim Boundary

Earlier candidate runs remain useful RCA evidence only. Fresh runs using the
versioned serving root, zero stable errors, post-replay inference and common
live-proof validation are required for Scenario B closure.

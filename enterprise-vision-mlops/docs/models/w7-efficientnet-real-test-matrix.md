# W7 Torch EfficientNet Real-Test Matrix

## Purpose

W7 should move the model evidence boundary from lifecycle proof to real
PyTorch-based model experimentation. The first practical model family is:

- `efficientnet-b0` for fast iteration and parallel condition search.
- `efficientnet-b7` for higher-capacity comparison under stricter GPU resource
  control.

This is a W7 specification supplement, not an implementation completion record.

## Real-Test Policy

W7 model completion evidence must not use mock adapters or smoke-only runs.

Accepted evidence:

- real VisA dataset records from the F-drive data root;
- real Torch/TorchVision training or fine-tuning run;
- MLflow run id;
- model artifact on the F-drive artifact root;
- evaluation metrics on held-out validation/test splits;
- model card or lifecycle dashboard;
- Control Panel `model_matrix` output;
- CD/CT gate result.

Rejected as W7 completion evidence:

- mock VLM adapter output;
- placeholder predictions;
- synthetic-only fixtures;
- compile-only or smoke-only checks;
- CPU fallback runs used as the sole proof for an EfficientNet candidate.

Historical W0-W6 smoke/mock evidence remains valid as historical scaffolding
evidence, but it should not be used as W7 model acceptance proof.

## Parallel Candidate Matrix

Source config:

- `configs/w7_efficientnet_real_test.toml`

| Candidate | Backbone | Input | Train Mode | Optimizer | Batch | Parallel Policy |
|---|---|---:|---|---|---:|---|
| `effnet-b0-img224-freeze-adamw` | `torchvision.models.efficientnet_b0` | 224 | frozen backbone | AdamW | 32 | up to 2 B0 jobs |
| `effnet-b0-img224-finetune-sgd` | `torchvision.models.efficientnet_b0` | 224 | fine-tune | SGD | 32 | up to 2 B0 jobs |
| `effnet-b7-img600-freeze-adamw` | `torchvision.models.efficientnet_b7` | 600 | frozen backbone | AdamW | 8 | exclusive B7 job |
| `effnet-b7-img600-finetune-adamw` | `torchvision.models.efficientnet_b7` | 600 | fine-tune | AdamW | 4 | exclusive B7 job |

## Resource Interpretation

- Windows RTX 4080 SUPER should be treated as the primary GPU trainer.
- B0 candidates can be parallelized first because they are lighter and useful
  for pipeline validation, hyperparameter comparison, and fast regression.
- B7 candidates should be scheduled conservatively because image resolution,
  memory pressure, and training time are much higher.
- Mac mini M4 Pro remains useful as a remote evaluator or artifact verifier, not
  the primary CUDA trainer.
- CPU fallback is disabled for acceptance, because W7 is meant to prove real
  model lifecycle behavior under the intended local compute profile.

## Control Panel Requirements

The W7 Control Panel should expose:

- matrix id and execution mode;
- real-test policy flags;
- each candidate backbone, status, conditions, and resource profile;
- MLflow run/artifact URI after execution;
- metrics per candidate;
- promotion blockers per candidate;
- final selected candidate and reason.

`CycleRun.model_matrix` is the contract field for this view.

## Next Implementation Handoff

When W7 implementation starts, add a Torch training pipeline that can read
`configs/w7_efficientnet_real_test.toml`, launch the configured candidates,
record each run in MLflow, and feed the resulting candidate matrix into the
Control Panel aggregation API.

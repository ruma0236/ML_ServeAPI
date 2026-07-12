# Scale Serving Decision

## Decision

EVM-213 selects a `KServe + Triton` staging pilot for a future EfficientNet-B7
serving migration. The current Kubernetes FastAPI/CUDA service remains the
verified rollback path until protocol conformance, load, failure, canary, and
rollback evidence all pass. This is a validated research decision and does not
claim that KServe or Triton is currently installed.

## Role Matrix

| Technology | Assigned role | Current B7 fit | Decision |
|---|---|---|---|
| KServe | Kubernetes online-serving control plane | yes | selected pilot |
| Triton | optimized vision model runtime | after ONNX/TensorRT export | selected pilot |
| vLLM | future generative VLM/LLM runtime | no | separate VLM track |
| Ray Serve | Python-heavy multi-stage composition | conditional | exception path |
| Kueue | quota/admission for finite GPU jobs | not an online runtime | training/batch path |

Triton provides per-model schedulers, batching, model management, health
endpoints, and metrics. KServe adds declarative inference resources and
independently scalable inference graphs. vLLM is reserved for compatible
generative models, while Ray Serve is used only where independently scalable
Python components are the primary requirement. Kueue manages finite workloads,
GPU flavors, quotas, priority, and admission checks rather than online request
routing.

Official references:

- https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/architecture.html
- https://kserve.github.io/website/docs/concepts/resources/inferencegraph
- https://docs.vllm.ai/en/stable/cli/serve/
- https://docs.ray.io/en/latest/serve/model_composition.html
- https://kueue.sigs.k8s.io/docs/concepts/

## Pilot Sequence

1. Export the immutable B7 checkpoint to ONNX or TensorRT and verify output
   parity against the current CUDA service.
2. Validate the serving protocol, model SHA, dataset version, threshold, and
   class order.
3. Benchmark throughput, p50/p95/p99 latency, GPU memory, queue delay, and
   error rate against the current service.
4. Deploy a staging-only KServe canary with Triton and no production route.
5. Exercise pod loss, model-load failure, queue saturation, and node drain.
6. Roll back to the current FastAPI service within 300 seconds.

Production cutover remains blocked if success rate is below `99.9%`, identity
match is below `100%`, p95 regresses by more than `10%`, or rollback exceeds
five minutes.

## Verification

```powershell
python scripts/dev/validate_scale_serving_decision.py `
  --output F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/serving-research/decision-validation.json
python -m pytest tests/test_scale_serving_decision.py -q
```

Success requires all five technologies to have distinct roles, all six pilot
phases to be present, and the design/runtime boundary to remain explicit.

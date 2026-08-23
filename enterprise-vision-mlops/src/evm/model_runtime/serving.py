from __future__ import annotations

import argparse
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field

from evm.model_runtime.common import (
    ModelRuntimeError,
    file_sha256,
    file_uri_path,
    nvidia_smi_snapshot,
    parse_choice_index,
    runtime_inventory,
    utc_now,
)
from evm.model_runtime.family_admission import (
    AdmissionCost,
    FamilyAdmissionController,
    FamilyAdmissionError,
    FamilyAdmissionLimits,
    request_json_bytes,
)
from evm.model_runtime.vlm import scienceqa_messages
from evm.observability.otel import configure_tracing, shutdown_tracing, trace_span
from evm.observability.trace_context import TraceContextError, W3CTraceContext


ModelFamily = Literal["vlm", "llm"]


class ScenarioInferenceRequest(BaseModel):
    model_family: ModelFamily
    image_uri: str | None = Field(default=None, max_length=4096)
    image_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    question: str | None = Field(default=None, max_length=16384)
    choices: list[str] = Field(default_factory=list, max_length=8)
    instruction: str | None = Field(default=None, max_length=32768)
    context: str | None = Field(default=None, max_length=131072)
    max_new_tokens: int = Field(default=32, ge=1, le=256)
    deadline_seconds: float | None = Field(default=None, gt=0, le=180)


@dataclass(frozen=True)
class ScenarioServingConfig:
    model_family: ModelFamily
    base_model_dir: Path
    adapter_dir: Path
    model_repository: str
    model_revision: str
    model_artifact_sha256: str
    data_identity_sha256: str
    source_commit: str
    lifecycle_run_id: str
    runtime_source_commit: str | None = None
    quantization: str = "none"
    environment: Literal["local-staging", "local-production"] = "local-staging"
    admission_config_path: Path | None = None


class ScenarioModelService:
    def __init__(self, config: ScenarioServingConfig) -> None:
        self.config = config
        self.started_at = utc_now()
        self.runtime = runtime_inventory()
        self.lock = threading.Lock()
        self.processor: Any = None
        self.model: Any = None
        self.registry = CollectorRegistry()
        self.admission = FamilyAdmissionController(
            FamilyAdmissionLimits.from_path(config.model_family, config.admission_config_path),
            registry=self.registry,
        )
        labels = ["model_family", "quantization", "environment"]
        self.info = Gauge(
            "evm_scenario_model_info",
            "Exact identity of the locally staged scenario model.",
            labels,
            registry=self.registry,
        )
        self.requests = Counter(
            "evm_scenario_inference_requests_total",
            "Scenario model inference requests.",
            ["model_family", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "evm_scenario_inference_latency_seconds",
            "Scenario model inference latency.",
            ["model_family"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
            registry=self.registry,
        )
        self.info.labels(
            config.model_family,
            config.quantization,
            config.environment,
        ).set(1)
        self._load()

    def _load(self) -> None:
        import torch
        from peft import PeftModel

        if file_sha256(self.config.adapter_dir / "adapter_model.safetensors") != (
            self.config.model_artifact_sha256
        ):
            raise ModelRuntimeError("serving_adapter_identity_mismatch")
        if self.config.model_family == "vlm":
            from transformers import AutoModelForImageTextToText, AutoProcessor

            self.processor = AutoProcessor.from_pretrained(
                self.config.base_model_dir, local_files_only=True
            )
            base = AutoModelForImageTextToText.from_pretrained(
                self.config.base_model_dir,
                local_files_only=True,
                dtype=torch.bfloat16,
                _attn_implementation="eager",
            ).to("cuda")
        else:
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

            self.processor = AutoTokenizer.from_pretrained(
                self.config.base_model_dir, local_files_only=True
            )
            if self.config.quantization == "int4_nf4":
                quantization_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                )
                base = AutoModelForCausalLM.from_pretrained(
                    self.config.base_model_dir,
                    local_files_only=True,
                    quantization_config=quantization_config,
                    device_map={"": 0},
                )
            else:
                base = AutoModelForCausalLM.from_pretrained(
                    self.config.base_model_dir,
                    local_files_only=True,
                    dtype=torch.bfloat16,
                ).to("cuda")
        self.model = PeftModel.from_pretrained(base, self.config.adapter_dir)
        self.model.eval()

    def ready_payload(self) -> dict[str, Any]:
        runtime_source_commit = self.config.runtime_source_commit or self.config.source_commit
        return {
            "status": "ready",
            "model_family": self.config.model_family,
            "model_repository": self.config.model_repository,
            "model_revision": self.config.model_revision,
            "model_artifact_sha256": self.config.model_artifact_sha256,
            "data_identity_sha256": self.config.data_identity_sha256,
            "source_commit": self.config.source_commit,
            "model_source_commit": self.config.source_commit,
            "runtime_source_commit": runtime_source_commit,
            "lifecycle_run_id": self.config.lifecycle_run_id,
            "quantization": self.config.quantization,
            "environment": self.config.environment,
            "runtime": self.runtime,
            "gpu": nvidia_smi_snapshot(),
            "admission": self.admission.snapshot(),
            "started_at": self.started_at,
        }

    def infer(self, request: ScenarioInferenceRequest) -> dict[str, Any]:
        if request.model_family != self.config.model_family:
            raise ModelRuntimeError("serving_model_family_mismatch")
        cost = self._admission_cost(request)
        started = time.perf_counter()
        try:
            with trace_span(
                "model.infer",
                attributes={
                    "evm.stage": "serving",
                    "evm.model.family": self.config.model_family,
                    "evm.model.revision": self.config.model_revision,
                    "evm.model.artifact.sha256": self.config.model_artifact_sha256,
                    "evm.lifecycle.run_id": self.config.lifecycle_run_id,
                },
            ) as active:
                with self.admission.acquire(
                    cost,
                    deadline_seconds=request.deadline_seconds,
                ) as lease:
                    active.set_attribute(
                        "evm.admission.queue_wait_seconds", lease.queue_wait_seconds
                    )
                    active.set_attribute("evm.admission.request_bytes", cost.request_bytes)
                    active.set_attribute("evm.admission.image_pixels", cost.image_pixels)
                    active.set_attribute("evm.admission.reserved_tokens", cost.total_tokens)
                    with self.lock:
                        result = (
                            self._infer_vlm(request)
                            if request.model_family == "vlm"
                            else self._infer_llm(request)
                        )
                runtime_metrics = result.pop("_runtime_metrics")
                runtime_metrics["queue_wait_seconds"] = lease.queue_wait_seconds
                runtime_metrics["request_bytes"] = cost.request_bytes
                if cost.image_bytes:
                    runtime_metrics["image_bytes"] = cost.image_bytes
                    runtime_metrics["image_pixels"] = cost.image_pixels
                self.admission.record_runtime_metrics(runtime_metrics)
            status = "success"
            return {
                **result,
                "model_family": self.config.model_family,
                "lifecycle_run_id": self.config.lifecycle_run_id,
                "model_artifact_sha256": self.config.model_artifact_sha256,
                "latency_seconds": round(time.perf_counter() - started, 6),
                "operational_metrics": {
                    key: round(float(value), 6)
                    for key, value in runtime_metrics.items()
                },
                "observed_at": utc_now(),
            }
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - started
            self.requests.labels(self.config.model_family, status).inc()
            self.latency.labels(self.config.model_family).observe(elapsed)

    def _admission_cost(self, request: ScenarioInferenceRequest) -> AdmissionCost:
        if request.model_family == "vlm":
            if not request.image_uri or not request.image_sha256:
                raise ModelRuntimeError("vlm_image_identity_missing")
            if not request.question or not request.choices:
                raise ModelRuntimeError("vlm_question_contract_invalid")
            image_path = file_uri_path(request.image_uri)
            if not image_path.is_file() or file_sha256(image_path) != request.image_sha256:
                raise ModelRuntimeError("vlm_image_identity_mismatch")
            from PIL import Image

            with Image.open(image_path) as source_image:
                width, height = source_image.size
            return AdmissionCost(
                request_bytes=request_json_bytes(request),
                image_bytes=image_path.stat().st_size,
                image_pixels=width * height,
                # Reserve the frozen worst-case input budget before image preprocessing.
                input_tokens=self.admission.limits.max_input_tokens,
                output_tokens=request.max_new_tokens,
            )
        prompt = self._llm_prompt(request)
        input_tokens = len(self.processor(prompt, add_special_tokens=False)["input_ids"])
        return AdmissionCost(
            request_bytes=request_json_bytes(request),
            input_tokens=input_tokens,
            output_tokens=request.max_new_tokens,
        )

    def _infer_vlm(self, request: ScenarioInferenceRequest) -> dict[str, Any]:
        from PIL import Image
        import torch

        if not request.image_uri or not request.image_sha256:
            raise ModelRuntimeError("vlm_image_identity_missing")
        if not request.question or not request.choices:
            raise ModelRuntimeError("vlm_question_contract_invalid")
        image_path = file_uri_path(request.image_uri)
        if not image_path.is_file() or file_sha256(image_path) != request.image_sha256:
            raise ModelRuntimeError("vlm_image_identity_mismatch")
        record = {"question": request.question, "choices": request.choices}
        decode_started = time.perf_counter()
        with Image.open(image_path) as source_image:
            image = source_image.convert("RGB")
        decode_seconds = time.perf_counter() - decode_started
        prompt = self.processor.apply_chat_template(
            scienceqa_messages(record, include_answer=False),
            add_generation_prompt=True,
        )
        preprocess_started = time.perf_counter()
        inputs = self.processor(text=prompt, images=[image], return_tensors="pt")
        input_tokens = int(inputs["input_ids"].shape[1])
        if input_tokens > self.admission.limits.max_input_tokens:
            raise FamilyAdmissionError("input_tokens_exceeded", status_code=422)
        inputs = {
            key: value.to("cuda") if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        preprocess_seconds = time.perf_counter() - preprocess_started
        timer, stopping_criteria = _generation_timer()
        torch.cuda.reset_peak_memory_stats()
        generation_started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=request.max_new_tokens,
                do_sample=False,
                stopping_criteria=stopping_criteria,
            )
        torch.cuda.synchronize()
        generation_finished = time.perf_counter()
        new_tokens = generated[:, inputs["input_ids"].shape[1] :]
        generated_tokens = int(new_tokens.shape[1])
        output = self.processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        runtime_metrics = _generation_metrics(
            timer,
            generation_started=generation_started,
            generation_finished=generation_finished,
            generated_tokens=generated_tokens,
            input_tokens=input_tokens,
            peak_vram_bytes=int(torch.cuda.max_memory_allocated()),
        )
        runtime_metrics.update(
            {
                "decode_seconds": decode_seconds,
                "preprocess_seconds": preprocess_seconds,
            }
        )
        return {
            "output": output,
            "predicted_index": parse_choice_index(output, len(request.choices)),
            "termination_reason": _termination_reason(
                generated_tokens, request.max_new_tokens
            ),
            "_runtime_metrics": runtime_metrics,
        }

    def _infer_llm(self, request: ScenarioInferenceRequest) -> dict[str, Any]:
        import torch

        prompt = self._llm_prompt(request)
        preprocess_started = time.perf_counter()
        inputs = self.processor(prompt, return_tensors="pt")
        input_tokens = int(inputs["input_ids"].shape[1])
        if input_tokens > self.admission.limits.max_input_tokens:
            raise FamilyAdmissionError("input_tokens_exceeded", status_code=422)
        inputs = {
            key: value.to("cuda") if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        preprocess_seconds = time.perf_counter() - preprocess_started
        timer, stopping_criteria = _generation_timer()
        torch.cuda.reset_peak_memory_stats()
        generation_started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=request.max_new_tokens,
                do_sample=False,
                pad_token_id=self.processor.eos_token_id,
                stopping_criteria=stopping_criteria,
            )
        torch.cuda.synchronize()
        generation_finished = time.perf_counter()
        new_tokens = generated[:, inputs["input_ids"].shape[1] :]
        generated_tokens = int(new_tokens.shape[1])
        output = self.processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        runtime_metrics = _generation_metrics(
            timer,
            generation_started=generation_started,
            generation_finished=generation_finished,
            generated_tokens=generated_tokens,
            input_tokens=input_tokens,
            peak_vram_bytes=int(torch.cuda.max_memory_allocated()),
        )
        runtime_metrics["preprocess_seconds"] = preprocess_seconds
        return {
            "output": output,
            "termination_reason": _termination_reason(
                generated_tokens, request.max_new_tokens
            ),
            "_runtime_metrics": runtime_metrics,
        }

    def _llm_prompt(self, request: ScenarioInferenceRequest) -> str:
        if not request.instruction:
            raise ModelRuntimeError("llm_instruction_missing")
        content = request.instruction.strip()
        if request.context:
            content = f"Context:\n{request.context.strip()}\n\nInstruction:\n{content}"
        return self.processor.apply_chat_template(
            [{"role": "user", "content": content}],
            tokenize=False,
            add_generation_prompt=True,
        )


def _generation_timer() -> tuple[Any, Any]:
    from transformers import StoppingCriteria, StoppingCriteriaList

    class StepTimer(StoppingCriteria):
        def __init__(self) -> None:
            self.first_step_at: float | None = None

        def __call__(self, _input_ids, _scores, **_kwargs) -> bool:
            if self.first_step_at is None:
                self.first_step_at = time.perf_counter()
            return False

    timer = StepTimer()
    return timer, StoppingCriteriaList([timer])


def _generation_metrics(
    timer: Any,
    *,
    generation_started: float,
    generation_finished: float,
    generated_tokens: int,
    input_tokens: int,
    peak_vram_bytes: int,
) -> dict[str, float | int]:
    generation_seconds = generation_finished - generation_started
    metrics: dict[str, float | int] = {
        "input_tokens": input_tokens,
        "generated_tokens": generated_tokens,
        "generation_seconds": generation_seconds,
        "inference_seconds": generation_seconds,
        "peak_vram_bytes": peak_vram_bytes,
    }
    if generated_tokens > 0 and generation_seconds > 0:
        metrics["tokens_per_second"] = generated_tokens / generation_seconds
    if timer.first_step_at is not None:
        metrics["ttft_seconds"] = timer.first_step_at - generation_started
        if generated_tokens > 1:
            metrics["tpot_seconds"] = (
                generation_finished - timer.first_step_at
            ) / (generated_tokens - 1)
    return metrics


def _termination_reason(generated_tokens: int, requested_tokens: int) -> str:
    return "max_new_tokens" if generated_tokens >= requested_tokens else "eos_or_model_stop"


def create_app(service: ScenarioModelService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        configure_tracing(
            "evm-scenario-model-serving",
            service_version=(
                service.config.runtime_source_commit or service.config.source_commit
            ),
        )
        try:
            yield
        finally:
            shutdown_tracing()

    app = FastAPI(title="EVM Scenario Model Serving", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def propagate_trace_context(request: Request, call_next):
        try:
            parent = (
                W3CTraceContext.parse(
                    request.headers["traceparent"],
                    tracestate=request.headers.get("tracestate"),
                )
                if "traceparent" in request.headers
                else None
            )
        except TraceContextError:
            parent = None
        with trace_span(
            f"{request.method} {request.url.path}",
            parent=parent,
            kind="server",
            attributes={
                "http.request.method": request.method,
                "url.path": request.url.path,
                "evm.stage": "serving",
                "evm.model.family": service.config.model_family,
                "evm.runtime.environment": service.config.environment,
            },
        ) as active:
            response = await call_next(request)
            active.set_attribute("http.response.status_code", response.status_code)
            response.headers["traceparent"] = active.context.traceparent
            response.headers["x-evm-trace-id"] = active.context.trace_id
            return response

    @app.get("/ready")
    def ready() -> dict[str, Any]:
        return service.ready_payload()

    @app.post("/infer")
    def infer(request: ScenarioInferenceRequest) -> dict[str, Any]:
        try:
            return service.infer(request)
        except FamilyAdmissionError as exc:
            headers = (
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds is not None
                else None
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.code,
                headers=headers,
            ) from exc
        except ModelRuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(service.registry), media_type=CONTENT_TYPE_LATEST)

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve one exact scenario adapter on local CUDA.")
    parser.add_argument("--model-family", choices=("vlm", "llm"), required=True)
    parser.add_argument("--base-model-dir", type=Path, required=True)
    parser.add_argument("--adapter-dir", type=Path, required=True)
    parser.add_argument("--model-repository", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-artifact-sha256", required=True)
    parser.add_argument("--data-identity-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--runtime-source-commit",
        default=os.getenv("EVM_GIT_COMMIT") or os.getenv("GIT_COMMIT"),
    )
    parser.add_argument("--lifecycle-run-id", required=True)
    parser.add_argument("--quantization", default="none")
    parser.add_argument(
        "--admission-config",
        type=Path,
        default=(Path(os.environ["EVM_S7_ADMISSION_CONFIG"]) if os.getenv("EVM_S7_ADMISSION_CONFIG") else None),
    )
    parser.add_argument(
        "--environment",
        choices=("local-staging", "local-production"),
        default="local-staging",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    return parser


def main() -> int:
    import uvicorn

    args = build_parser().parse_args()
    service = ScenarioModelService(
        ScenarioServingConfig(
            model_family=args.model_family,
            base_model_dir=args.base_model_dir,
            adapter_dir=args.adapter_dir,
            model_repository=args.model_repository,
            model_revision=args.model_revision,
            model_artifact_sha256=args.model_artifact_sha256,
            data_identity_sha256=args.data_identity_sha256,
            source_commit=args.source_commit,
            lifecycle_run_id=args.lifecycle_run_id,
            runtime_source_commit=args.runtime_source_commit,
            quantization=args.quantization,
            environment=args.environment,
            admission_config_path=args.admission_config,
        )
    )
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

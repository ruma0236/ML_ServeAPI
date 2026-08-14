from __future__ import annotations

import argparse
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
from evm.model_runtime.vlm import scienceqa_messages
from evm.observability.otel import configure_tracing, shutdown_tracing, trace_span
from evm.observability.trace_context import TraceContextError, W3CTraceContext


ModelFamily = Literal["vlm", "llm"]


class ScenarioInferenceRequest(BaseModel):
    model_family: ModelFamily
    image_uri: str | None = None
    image_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    question: str | None = None
    choices: list[str] = Field(default_factory=list)
    instruction: str | None = None
    context: str | None = None
    max_new_tokens: int = Field(default=32, ge=1, le=256)


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
    quantization: str = "none"
    environment: Literal["local-staging", "local-production"] = "local-staging"


class ScenarioModelService:
    def __init__(self, config: ScenarioServingConfig) -> None:
        self.config = config
        self.started_at = utc_now()
        self.runtime = runtime_inventory()
        self.lock = threading.Lock()
        self.processor: Any = None
        self.model: Any = None
        self.registry = CollectorRegistry()
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
        return {
            "status": "ready",
            "model_family": self.config.model_family,
            "model_repository": self.config.model_repository,
            "model_revision": self.config.model_revision,
            "model_artifact_sha256": self.config.model_artifact_sha256,
            "data_identity_sha256": self.config.data_identity_sha256,
            "source_commit": self.config.source_commit,
            "lifecycle_run_id": self.config.lifecycle_run_id,
            "quantization": self.config.quantization,
            "environment": self.config.environment,
            "runtime": self.runtime,
            "gpu": nvidia_smi_snapshot(),
            "started_at": self.started_at,
        }

    def infer(self, request: ScenarioInferenceRequest) -> dict[str, Any]:
        if request.model_family != self.config.model_family:
            raise ModelRuntimeError("serving_model_family_mismatch")
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
            ):
                with self.lock:
                    result = (
                        self._infer_vlm(request)
                        if request.model_family == "vlm"
                        else self._infer_llm(request)
                    )
            status = "success"
            return {
                **result,
                "model_family": self.config.model_family,
                "lifecycle_run_id": self.config.lifecycle_run_id,
                "model_artifact_sha256": self.config.model_artifact_sha256,
                "latency_seconds": round(time.perf_counter() - started, 6),
                "observed_at": utc_now(),
            }
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - started
            self.requests.labels(self.config.model_family, status).inc()
            self.latency.labels(self.config.model_family).observe(elapsed)

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
        with Image.open(image_path) as source_image:
            image = source_image.convert("RGB")
        prompt = self.processor.apply_chat_template(
            scienceqa_messages(record, include_answer=False),
            add_generation_prompt=True,
        )
        inputs = self.processor(text=prompt, images=[image], return_tensors="pt")
        inputs = {
            key: value.to("cuda") if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=request.max_new_tokens,
                do_sample=False,
            )
        new_tokens = generated[:, inputs["input_ids"].shape[1] :]
        output = self.processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        return {
            "output": output,
            "predicted_index": parse_choice_index(output, len(request.choices)),
        }

    def _infer_llm(self, request: ScenarioInferenceRequest) -> dict[str, Any]:
        import torch

        if not request.instruction:
            raise ModelRuntimeError("llm_instruction_missing")
        content = request.instruction.strip()
        if request.context:
            content = f"Context:\n{request.context.strip()}\n\nInstruction:\n{content}"
        messages = [{"role": "user", "content": content}]
        prompt = self.processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self.processor(prompt, return_tensors="pt")
        inputs = {
            key: value.to("cuda") if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=request.max_new_tokens,
                do_sample=False,
                pad_token_id=self.processor.eos_token_id,
            )
        new_tokens = generated[:, inputs["input_ids"].shape[1] :]
        output = self.processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        return {"output": output}


def create_app(service: ScenarioModelService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        configure_tracing(
            "evm-scenario-model-serving",
            service_version=service.config.source_commit,
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
    parser.add_argument("--lifecycle-run-id", required=True)
    parser.add_argument("--quantization", default="none")
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
            quantization=args.quantization,
            environment=args.environment,
        )
    )
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

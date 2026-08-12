from __future__ import annotations

import gc
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm.model_runtime.common import (
    ModelRuntimeError,
    atomic_write_json,
    file_sha256,
    log_mlflow_evidence,
    nvidia_smi_snapshot,
    p95,
    read_jsonl,
    runtime_inventory,
    set_reproducible_seed,
    split_records,
    utc_now,
)


@dataclass(frozen=True)
class QwenTrainingConfig:
    model_dir: Path
    manifest_path: Path
    output_dir: Path
    model_repository: str
    model_revision: str
    data_identity_sha256: str
    source_commit: str
    lifecycle_run_id: str
    seed: int = 20260805
    max_steps: int = 24
    learning_rate: float = 1e-4
    lora_rank: int = 8
    lora_alpha: int = 16
    max_length: int = 512
    max_new_tokens: int = 64
    evaluation_records: int = 8
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"
    mlflow_experiment_name: str = "enterprise-mlops-real-llm"
    progress_path: Path | None = None


def instruction_content(record: dict[str, Any]) -> str:
    instruction = str(record.get("instruction") or "").strip()
    if not instruction:
        raise ModelRuntimeError("dolly_instruction_missing")
    context = str(record.get("context") or "").strip()
    if context:
        return f"Context:\n{context[:768]}\n\nInstruction:\n{instruction[:768]}"
    return instruction[:1536]


def instruction_messages(
    record: dict[str, Any], *, include_response: bool
) -> list[dict[str, str]]:
    messages = [{"role": "user", "content": instruction_content(record)}]
    if include_response:
        response = str(record.get("response") or "").strip()
        if not response:
            raise ModelRuntimeError("dolly_response_missing")
        messages.append({"role": "assistant", "content": response})
    return messages


def normalize_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def token_f1(prediction: str, expected: str) -> float:
    predicted_tokens = normalize_tokens(prediction)
    expected_tokens = normalize_tokens(expected)
    if not predicted_tokens or not expected_tokens:
        return 0.0
    overlap = sum((Counter(predicted_tokens) & Counter(expected_tokens)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted_tokens)
    recall = overlap / len(expected_tokens)
    return round(2 * precision * recall / (precision + recall), 6)


def train_qwen_qlora(config: QwenTrainingConfig) -> dict[str, Any]:
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise ModelRuntimeError("cuda_unavailable")
    if config.max_steps < 4:
        raise ModelRuntimeError("llm_training_steps_too_small")
    records = split_records(read_jsonl(config.manifest_path))
    if not records["train"] or not records["validation"] or not records["test"]:
        raise ModelRuntimeError("llm_required_split_empty")
    set_reproducible_seed(config.seed)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    runtime = runtime_inventory()
    gpu_before = nvidia_smi_snapshot()
    tokenizer = AutoTokenizer.from_pretrained(config.model_dir, local_files_only=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        config.model_dir,
        local_files_only=True,
        quantization_config=quantization_config,
        device_map={"": 0},
    )
    if not bool(getattr(model, "is_loaded_in_4bit", False)):
        raise ModelRuntimeError("llm_int4_runtime_not_observed")
    baseline = evaluate_qwen(
        model,
        tokenizer,
        records,
        max_length=config.max_length,
        max_new_tokens=config.max_new_tokens,
        evaluation_records=config.evaluation_records,
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(
        model,
        LoraConfig(
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=0.05,
            bias="none",
            target_modules=["q_proj", "v_proj"],
            task_type="CAUSAL_LM",
        ),
    )
    model.train()
    model.config.use_cache = False
    optimizer = torch.optim.AdamW(
        [parameter for parameter in model.parameters() if parameter.requires_grad],
        lr=config.learning_rate,
    )
    trainable_parameters = sum(
        parameter.numel() for parameter in model.parameters() if parameter.requires_grad
    )
    total_parameters = sum(parameter.numel() for parameter in model.parameters())
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    history: list[dict[str, Any]] = []
    started = time.perf_counter()
    cursor = 0
    while len(history) < config.max_steps:
        record = records["train"][cursor % len(records["train"])]
        cursor += 1
        try:
            inputs = training_inputs(tokenizer, record, max_length=config.max_length)
        except ModelRuntimeError as exc:
            if str(exc) != "llm_supervised_tokens_empty":
                raise
            if cursor > len(records["train"]) * 2:
                raise ModelRuntimeError("llm_trainable_records_insufficient") from exc
            continue
        inputs = {key: value.to("cuda") for key, value in inputs.items()}
        output = model(**inputs)
        if output.loss is None or not torch.isfinite(output.loss):
            raise ModelRuntimeError(f"llm_training_loss_invalid:{len(history) + 1}")
        output.loss.backward()
        torch.nn.utils.clip_grad_norm_(
            [parameter for parameter in model.parameters() if parameter.requires_grad], 1.0
        )
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        history.append(
            {
                "step": len(history) + 1,
                "loss": round(float(output.loss.detach().cpu()), 6),
                "sample_id": str(record["sample_id"]),
                "observed_at": utc_now(),
            }
        )
        if config.progress_path is not None:
            atomic_write_json(
                config.progress_path,
                {
                    "schema_version": "evm.scenario_training_progress.v1",
                    "model_family": "llm",
                    "lifecycle_run_id": config.lifecycle_run_id,
                    "current_step": len(history),
                    "max_steps": config.max_steps,
                    "progress": round(len(history) / config.max_steps, 6),
                    "latest_loss": history[-1]["loss"],
                    "observed_at": history[-1]["observed_at"],
                },
            )
    training_seconds = round(time.perf_counter() - started, 6)
    model.eval()
    adapted = evaluate_qwen(
        model,
        tokenizer,
        records,
        max_length=config.max_length,
        max_new_tokens=config.max_new_tokens,
        evaluation_records=config.evaluation_records,
    )
    adapter_dir = config.output_dir / "adapter"
    model.save_pretrained(adapter_dir, safe_serialization=True)
    tokenizer.save_pretrained(config.output_dir / "tokenizer")
    artifact_path = adapter_dir / "adapter_model.safetensors"
    if not artifact_path.is_file():
        raise ModelRuntimeError("llm_adapter_artifact_missing")
    artifact_sha256 = file_sha256(artifact_path)
    peak_allocated_mib = round(torch.cuda.max_memory_allocated() / 1048576, 3)
    peak_reserved_mib = round(torch.cuda.max_memory_reserved() / 1048576, 3)
    gpu_after_training = nvidia_smi_snapshot()
    promotion_blockers: list[str] = []
    adapted_loss = float(adapted["metrics"]["validation_loss"])
    baseline_loss = float(baseline["metrics"]["validation_loss"])
    if float(adapted["metrics"]["nonempty_rate"]) < 0.9:
        promotion_blockers.append("llm_nonempty_rate_below_0_9")
    if not math.isfinite(adapted_loss) or adapted_loss > baseline_loss * 1.25:
        promotion_blockers.append("llm_validation_loss_regression")
    metrics = {
        "baseline_validation_loss": baseline_loss,
        "adapted_validation_loss": adapted_loss,
        "baseline_token_f1": baseline["metrics"]["mean_token_f1"],
        "adapted_token_f1": adapted["metrics"]["mean_token_f1"],
        "adapted_nonempty_rate": adapted["metrics"]["nonempty_rate"],
        "adapted_p95_latency_seconds": adapted["metrics"]["p95_latency_seconds"],
        "final_training_loss": history[-1]["loss"],
        "peak_gpu_allocated_mib": peak_allocated_mib,
        "peak_gpu_reserved_mib": peak_reserved_mib,
        "training_seconds": training_seconds,
    }
    baseline_path = config.output_dir / "baseline-evaluation.json"
    adapted_path = config.output_dir / "adapted-evaluation.json"
    history_path = config.output_dir / "training-history.json"
    gpu_profile_path = config.output_dir / "gpu-profile.json"
    atomic_write_json(baseline_path, baseline)
    atomic_write_json(adapted_path, adapted)
    atomic_write_json(history_path, history)
    atomic_write_json(
        gpu_profile_path,
        {
            "runtime": runtime,
            "before": gpu_before,
            "after_training": gpu_after_training,
            "peak_allocated_mib": peak_allocated_mib,
            "peak_reserved_mib": peak_reserved_mib,
        },
    )
    mlflow_run_id = log_mlflow_evidence(
        tracking_uri=config.mlflow_tracking_uri,
        experiment_name=config.mlflow_experiment_name,
        run_name=f"qwen-{config.lifecycle_run_id}",
        params={
            "model_repository": config.model_repository,
            "model_revision": config.model_revision,
            "data_identity_sha256": config.data_identity_sha256,
            "source_commit": config.source_commit,
            "lifecycle_run_id": config.lifecycle_run_id,
            "adaptation_method": "qlora",
            "quantization": "int4_nf4",
            "lora_rank": config.lora_rank,
            "lora_alpha": config.lora_alpha,
            "learning_rate": config.learning_rate,
            "max_steps": config.max_steps,
            "seed": config.seed,
            "artifact_uri": str(config.output_dir),
            "model_artifact": str(artifact_path),
            "model_artifact_sha256": artifact_sha256,
        },
        metrics=metrics,
        tags={
            "evm.model_family": "llm",
            "evm.lifecycle_run_id": config.lifecycle_run_id,
            "evm.compute_backend": "windows-host-cuda",
            "evm.quantization": "int4_nf4",
        },
        artifact_paths=[
            artifact_path,
            adapter_dir / "adapter_config.json",
            baseline_path,
            adapted_path,
            history_path,
            gpu_profile_path,
        ],
    )
    result = {
        "schema_version": "evm.scenario_llm_training_result.v1",
        "status": "pass" if not promotion_blockers else "blocked",
        "model_family": "llm",
        "model_repository": config.model_repository,
        "model_revision": config.model_revision,
        "data_identity_sha256": config.data_identity_sha256,
        "source_commit": config.source_commit,
        "lifecycle_run_id": config.lifecycle_run_id,
        "adaptation_method": "qlora",
        "quantization": "int4_nf4",
        "seed": config.seed,
        "max_steps": config.max_steps,
        "train_records": len(records["train"]),
        "validation_records": len(records["validation"]),
        "test_records": len(records["test"]),
        "trainable_parameters": trainable_parameters,
        "total_parameters": total_parameters,
        "trainable_ratio": round(trainable_parameters / max(total_parameters, 1), 9),
        "training_seconds": training_seconds,
        "metrics": metrics,
        "baseline_evaluation_uri": str(baseline_path),
        "evaluation_uri": str(adapted_path),
        "training_history_uri": str(history_path),
        "model_artifact_uri": str(artifact_path),
        "model_artifact_sha256": artifact_sha256,
        "mlflow_run_id": mlflow_run_id,
        "mlflow_run_uri": f"{config.mlflow_tracking_uri}/#/experiments/0/runs/{mlflow_run_id}",
        "runtime_versions": {key: str(value) for key, value in runtime.items()},
        "gpu_before": gpu_before,
        "gpu_after_training": gpu_after_training,
        "peak_gpu_allocated_mib": peak_allocated_mib,
        "peak_gpu_reserved_mib": peak_reserved_mib,
        "promotion_blockers": promotion_blockers,
        "claim_boundary": (
            "Bounded QLoRA adaptation on a PII-pattern-filtered Dolly view; not a privacy "
            "certification, full-corpus tune, or production language-quality benchmark."
        ),
        "created_at": utc_now(),
    }
    atomic_write_json(config.output_dir / "training-result.json", result)
    write_model_card(config.output_dir / "model-card.md", result)
    del optimizer, model
    gc.collect()
    torch.cuda.empty_cache()
    return result


def training_inputs(tokenizer: Any, record: dict[str, Any], *, max_length: int) -> dict[str, Any]:
    prompt = tokenizer.apply_chat_template(
        instruction_messages(record, include_response=False),
        tokenize=False,
        add_generation_prompt=True,
    )
    full = tokenizer.apply_chat_template(
        instruction_messages(record, include_response=True),
        tokenize=False,
        add_generation_prompt=False,
    )
    prompt_inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
    inputs = tokenizer(
        full,
        return_tensors="pt",
        add_special_tokens=False,
        truncation=True,
        max_length=max_length,
    )
    labels = inputs["input_ids"].clone()
    prompt_length = min(prompt_inputs["input_ids"].shape[1], labels.shape[1])
    labels[:, :prompt_length] = -100
    labels[inputs["attention_mask"] == 0] = -100
    if int((labels != -100).sum().item()) == 0:
        raise ModelRuntimeError("llm_supervised_tokens_empty")
    inputs["labels"] = labels
    return inputs


def evaluate_qwen(
    model: Any,
    tokenizer: Any,
    records: dict[str, list[dict[str, Any]]],
    *,
    max_length: int,
    max_new_tokens: int,
    evaluation_records: int,
) -> dict[str, Any]:
    import torch

    model.eval()
    losses: list[float] = []
    validation_cursor = 0
    while len(losses) < evaluation_records:
        if validation_cursor >= len(records["validation"]):
            raise ModelRuntimeError("llm_validation_records_insufficient")
        record = records["validation"][validation_cursor]
        validation_cursor += 1
        try:
            inputs = training_inputs(tokenizer, record, max_length=max_length)
        except ModelRuntimeError as exc:
            if str(exc) == "llm_supervised_tokens_empty":
                continue
            raise
        inputs = {key: value.to("cuda") for key, value in inputs.items()}
        with torch.inference_mode():
            output = model(**inputs)
        if output.loss is None or not torch.isfinite(output.loss):
            raise ModelRuntimeError("llm_validation_loss_invalid")
        losses.append(float(output.loss.detach().cpu()))
    generated_results: list[dict[str, Any]] = []
    for record in records["test"][:evaluation_records]:
        prompt = tokenizer.apply_chat_template(
            instruction_messages(record, include_response=False),
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=max_length)
        inputs = {key: value.to("cuda") for key, value in inputs.items()}
        started = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        latency = time.perf_counter() - started
        new_tokens = generated[:, inputs["input_ids"].shape[1] :]
        prediction = tokenizer.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        expected = str(record.get("response") or "").strip()
        generated_results.append(
            {
                "sample_id": record["sample_id"],
                "prediction": prediction,
                "expected": expected,
                "nonempty": bool(prediction),
                "token_f1": token_f1(prediction, expected),
                "latency_seconds": round(latency, 6),
            }
        )
    token_scores = [float(item["token_f1"]) for item in generated_results]
    latencies = [float(item["latency_seconds"]) for item in generated_results]
    nonempty = sum(1 for item in generated_results if item["nonempty"])
    return {
        "schema_version": "evm.scenario_llm_evaluation.v1",
        "metrics": {
            "validation_record_count": len(losses),
            "validation_loss": round(sum(losses) / max(len(losses), 1), 6),
            "generated_record_count": len(generated_results),
            "nonempty_rate": round(nonempty / max(len(generated_results), 1), 6),
            "mean_token_f1": round(sum(token_scores) / max(len(token_scores), 1), 6),
            "p95_latency_seconds": p95(latencies),
        },
        "results": generated_results,
        "evaluated_at": utc_now(),
    }


def write_model_card(path: Path, result: dict[str, Any]) -> None:
    metrics = result["metrics"]
    lines = [
        "# Qwen Local Adaptation Model Card",
        "",
        f"- Status: `{result['status']}`",
        f"- Base model: `{result['model_repository']}@{result['model_revision']}`",
        f"- Adaptation: `QLoRA`, `{result['trainable_parameters']}` trainable parameters",
        f"- Quantization observed: `{result['quantization']}`",
        f"- Data identity: `{result['data_identity_sha256']}`",
        f"- Source commit: `{result['source_commit']}`",
        f"- MLflow run: `{result['mlflow_run_id']}`",
        f"- Adapter SHA-256: `{result['model_artifact_sha256']}`",
        f"- Baseline/adapted validation loss: `{metrics['baseline_validation_loss']}` / "
        f"`{metrics['adapted_validation_loss']}`",
        f"- Adapted non-empty rate: `{metrics['adapted_nonempty_rate']}`",
        f"- Peak allocated/reserved MiB: `{metrics['peak_gpu_allocated_mib']}` / "
        f"`{metrics['peak_gpu_reserved_mib']}`",
        "",
        "## Claim Boundary",
        "",
        result["claim_boundary"],
        "",
        "## Promotion Blockers",
        "",
    ]
    blockers = result.get("promotion_blockers") or []
    lines.extend(f"- `{blocker}`" for blocker in blockers)
    if not blockers:
        lines.append("- None for controlled local staging validation.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

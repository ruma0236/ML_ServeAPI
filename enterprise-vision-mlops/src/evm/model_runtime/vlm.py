from __future__ import annotations

import gc
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm.model_runtime.common import (
    ModelRuntimeError,
    atomic_write_json,
    file_sha256,
    file_uri_path,
    log_mlflow_evidence,
    metric_summary,
    nvidia_smi_snapshot,
    parse_choice_index,
    read_jsonl,
    runtime_inventory,
    set_reproducible_seed,
    split_records,
    utc_now,
)


@dataclass(frozen=True)
class SmolVlmTrainingConfig:
    model_dir: Path
    manifest_path: Path
    output_dir: Path
    model_repository: str
    model_revision: str
    data_identity_sha256: str
    source_commit: str
    lifecycle_run_id: str
    seed: int = 20260805
    max_steps: int = 8
    learning_rate: float = 2e-4
    lora_rank: int = 4
    lora_alpha: int = 8
    max_new_tokens: int = 8
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"
    mlflow_experiment_name: str = "enterprise-mlops-real-vlm"
    progress_path: Path | None = None


def scienceqa_prompt(record: dict[str, Any]) -> str:
    choices = record.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelRuntimeError("scienceqa_choices_missing")
    rendered = "\n".join(f"{index}. {choice}" for index, choice in enumerate(choices))
    return (
        "Answer the multiple-choice question using only the option number.\n"
        f"Question: {record.get('question', '')}\nChoices:\n{rendered}"
    )


def scienceqa_messages(record: dict[str, Any], *, include_answer: bool) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": scienceqa_prompt(record)},
            ],
        }
    ]
    if include_answer:
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": str(record["answer_index"])}],
            }
        )
    return messages


def train_smolvlm_lora(config: SmolVlmTrainingConfig) -> dict[str, Any]:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForImageTextToText, AutoProcessor

    if not torch.cuda.is_available():
        raise ModelRuntimeError("cuda_unavailable")
    if config.max_steps < 2:
        raise ModelRuntimeError("vlm_training_steps_too_small")
    records = split_records(read_jsonl(config.manifest_path))
    if not records["train"] or not records["validation"] or not records["test"]:
        raise ModelRuntimeError("vlm_required_split_empty")
    set_reproducible_seed(config.seed)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    runtime = runtime_inventory()
    gpu_before = nvidia_smi_snapshot()
    processor = AutoProcessor.from_pretrained(config.model_dir, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(
        config.model_dir,
        local_files_only=True,
        dtype=torch.bfloat16,
        _attn_implementation="eager",
    ).to("cuda")
    baseline = evaluate_smolvlm(
        model,
        processor,
        records["test"],
        max_new_tokens=config.max_new_tokens,
    )
    targets = [
        name
        for name, _ in model.named_modules()
        if name.startswith("model.text_model.") and name.endswith(("q_proj", "v_proj"))
    ]
    if not targets:
        raise ModelRuntimeError("vlm_lora_targets_missing")
    model = get_peft_model(
        model,
        LoraConfig(
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=0.05,
            bias="none",
            target_modules=targets,
        ),
    )
    model.train()
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
    for step in range(config.max_steps):
        record = records["train"][step % len(records["train"])]
        inputs = training_inputs(processor, record)
        inputs = {
            key: value.to("cuda") if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        output = model(**inputs)
        if output.loss is None or not torch.isfinite(output.loss):
            raise ModelRuntimeError(f"vlm_training_loss_invalid:{step}")
        output.loss.backward()
        torch.nn.utils.clip_grad_norm_(
            [parameter for parameter in model.parameters() if parameter.requires_grad], 1.0
        )
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        history.append(
            {
                "step": step + 1,
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
                    "model_family": "vlm",
                    "lifecycle_run_id": config.lifecycle_run_id,
                    "current_step": step + 1,
                    "max_steps": config.max_steps,
                    "progress": round((step + 1) / config.max_steps, 6),
                    "latest_loss": history[-1]["loss"],
                    "observed_at": history[-1]["observed_at"],
                },
            )
    training_seconds = round(time.perf_counter() - started, 6)
    model.eval()
    adapted = evaluate_smolvlm(
        model,
        processor,
        records["test"],
        max_new_tokens=config.max_new_tokens,
    )
    adapter_dir = config.output_dir / "adapter"
    model.save_pretrained(adapter_dir, safe_serialization=True)
    processor.save_pretrained(config.output_dir / "processor")
    artifact_path = adapter_dir / "adapter_model.safetensors"
    if not artifact_path.is_file():
        raise ModelRuntimeError("vlm_adapter_artifact_missing")
    artifact_sha256 = file_sha256(artifact_path)
    peak_allocated_mib = round(torch.cuda.max_memory_allocated() / 1048576, 3)
    peak_reserved_mib = round(torch.cuda.max_memory_reserved() / 1048576, 3)
    gpu_after_training = nvidia_smi_snapshot()
    minimum_accuracy = max(0.125, float(baseline["metrics"]["accuracy"]) - 0.25)
    promotion_blockers: list[str] = []
    if float(adapted["metrics"]["parse_rate"]) < 0.9:
        promotion_blockers.append("vlm_parse_rate_below_0_9")
    if float(adapted["metrics"]["accuracy"]) < minimum_accuracy:
        promotion_blockers.append("vlm_accuracy_below_local_guardrail")
    metrics = {
        "baseline_accuracy": baseline["metrics"]["accuracy"],
        "adapted_accuracy": adapted["metrics"]["accuracy"],
        "adapted_parse_rate": adapted["metrics"]["parse_rate"],
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
        run_name=f"smolvlm-{config.lifecycle_run_id}",
        params={
            "model_repository": config.model_repository,
            "model_revision": config.model_revision,
            "data_identity_sha256": config.data_identity_sha256,
            "source_commit": config.source_commit,
            "lifecycle_run_id": config.lifecycle_run_id,
            "adaptation_method": "lora",
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
            "evm.model_family": "vlm",
            "evm.lifecycle_run_id": config.lifecycle_run_id,
            "evm.compute_backend": "windows-host-cuda",
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
        "schema_version": "evm.scenario_vlm_training_result.v1",
        "status": "pass" if not promotion_blockers else "blocked",
        "model_family": "vlm",
        "model_repository": config.model_repository,
        "model_revision": config.model_revision,
        "data_identity_sha256": config.data_identity_sha256,
        "source_commit": config.source_commit,
        "lifecycle_run_id": config.lifecycle_run_id,
        "adaptation_method": "lora",
        "quantization": "none",
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
            "Bounded LoRA adaptation on an official-test-derived local ScienceQA view; "
            "not a ScienceQA benchmark or full-model fine-tune."
        ),
        "created_at": utc_now(),
    }
    atomic_write_json(config.output_dir / "training-result.json", result)
    write_model_card(config.output_dir / "model-card.md", result)
    del optimizer, model
    gc.collect()
    torch.cuda.empty_cache()
    return result


def training_inputs(processor: Any, record: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    image_path = file_uri_path(str(record.get("image_uri") or ""))
    if not image_path.is_file() or file_sha256(image_path) != record.get("image_sha256"):
        raise ModelRuntimeError(f"scienceqa_image_identity_mismatch:{record.get('sample_id', '')}")
    with Image.open(image_path) as source_image:
        image = source_image.convert("RGB")
    prompt_messages = scienceqa_messages(record, include_answer=False)
    full_messages = scienceqa_messages(record, include_answer=True)
    prompt = processor.apply_chat_template(prompt_messages, add_generation_prompt=True)
    full = processor.apply_chat_template(full_messages, add_generation_prompt=False)
    prompt_inputs = processor(text=prompt, images=[image], return_tensors="pt")
    inputs = processor(text=full, images=[image], return_tensors="pt")
    labels = inputs["input_ids"].clone()
    prompt_length = min(prompt_inputs["input_ids"].shape[1], labels.shape[1])
    labels[:, :prompt_length] = -100
    labels[inputs["attention_mask"] == 0] = -100
    if int((labels != -100).sum().item()) == 0:
        raise ModelRuntimeError("vlm_supervised_tokens_empty")
    inputs["labels"] = labels
    return inputs


def evaluate_smolvlm(
    model: Any,
    processor: Any,
    records: list[dict[str, Any]],
    *,
    max_new_tokens: int,
) -> dict[str, Any]:
    from PIL import Image
    import torch

    model.eval()
    results: list[dict[str, Any]] = []
    for record in records:
        image_path = file_uri_path(str(record.get("image_uri") or ""))
        if not image_path.is_file() or file_sha256(image_path) != record.get("image_sha256"):
            raise ModelRuntimeError(f"scienceqa_image_identity_mismatch:{record.get('sample_id', '')}")
        with Image.open(image_path) as source_image:
            image = source_image.convert("RGB")
        prompt = processor.apply_chat_template(
            scienceqa_messages(record, include_answer=False),
            add_generation_prompt=True,
        )
        inputs = processor(text=prompt, images=[image], return_tensors="pt")
        inputs = {
            key: value.to("cuda") if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        started = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        latency = time.perf_counter() - started
        new_tokens = generated[:, inputs["input_ids"].shape[1] :]
        output = processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        choices = record.get("choices") if isinstance(record.get("choices"), list) else []
        predicted = parse_choice_index(output, len(choices))
        expected = int(record["answer_index"])
        results.append(
            {
                "sample_id": record["sample_id"],
                "expected_index": expected,
                "predicted_index": predicted,
                "correct": predicted == expected,
                "output": output,
                "latency_seconds": round(latency, 6),
            }
        )
    return {
        "schema_version": "evm.scenario_vlm_evaluation.v1",
        "metrics": metric_summary(results),
        "results": results,
        "evaluated_at": utc_now(),
    }


def write_model_card(path: Path, result: dict[str, Any]) -> None:
    metrics = result["metrics"]
    lines = [
        "# SmolVLM Local Adaptation Model Card",
        "",
        f"- Status: `{result['status']}`",
        f"- Base model: `{result['model_repository']}@{result['model_revision']}`",
        f"- Adaptation: `LoRA`, `{result['trainable_parameters']}` trainable parameters",
        f"- Data identity: `{result['data_identity_sha256']}`",
        f"- Source commit: `{result['source_commit']}`",
        f"- MLflow run: `{result['mlflow_run_id']}`",
        f"- Adapter SHA-256: `{result['model_artifact_sha256']}`",
        f"- Baseline accuracy: `{metrics['baseline_accuracy']}`",
        f"- Adapted accuracy: `{metrics['adapted_accuracy']}`",
        f"- Adapted parse rate: `{metrics['adapted_parse_rate']}`",
        f"- Peak allocated/reserved MiB: `{metrics['peak_gpu_allocated_mib']}` / `{metrics['peak_gpu_reserved_mib']}`",
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

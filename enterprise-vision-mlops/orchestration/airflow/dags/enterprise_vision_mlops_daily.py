from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator


PROJECT_ROOT = Path(os.environ.get("EVM_PROJECT_ROOT", "/opt/airflow/evm_project"))
PIPELINE_CONFIG = os.environ.get(
    "EVM_PIPELINE_CONFIG",
    str(PROJECT_ROOT / "configs" / "airflow.toml"),
)
PYTHON_BIN = os.environ.get("EVM_PYTHON_BIN", "python")
DEFAULT_RETRIES = int(os.environ.get("EVM_AIRFLOW_TASK_RETRIES", "1"))
DEFAULT_RETRY_DELAY_MINUTES = int(os.environ.get("EVM_AIRFLOW_RETRY_DELAY_MINUTES", "2"))
DEFAULT_TASK_TIMEOUT_MINUTES = int(os.environ.get("EVM_AIRFLOW_TASK_TIMEOUT_MINUTES", "10"))
DAG_RUN_TIMEOUT_MINUTES = int(os.environ.get("EVM_AIRFLOW_DAG_TIMEOUT_MINUTES", "45"))


def pipeline_command(name: str) -> str:
    return (
        "set -euo pipefail; "
        f"cd {PROJECT_ROOT} && "
        f"PYTHONPATH={PROJECT_ROOT / 'src'} "
        f"{PYTHON_BIN} scripts/run_pipeline.py {name} --config {PIPELINE_CONFIG}"
    )


def airflow_trace_env() -> dict[str, str]:
    return {
        "EVM_TRACE_ID": "{{ dag.dag_id }}__{{ run_id }}",
        "EVM_AIRFLOW_DAG_ID": "{{ dag.dag_id }}",
        "EVM_AIRFLOW_DAG_RUN_ID": "{{ run_id }}",
        "EVM_AIRFLOW_TASK_ID": "{{ task.task_id }}",
        "EVM_AIRFLOW_TRY_NUMBER": "{{ ti.try_number }}",
    }


def pipeline_task(task_id: str, pipeline_name: str) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=pipeline_command(pipeline_name),
        env=airflow_trace_env(),
        append_env=True,
        retries=DEFAULT_RETRIES,
        retry_delay=timedelta(minutes=DEFAULT_RETRY_DELAY_MINUTES),
        execution_timeout=timedelta(minutes=DEFAULT_TASK_TIMEOUT_MINUTES),
    )


default_args = {
    "owner": "enterprise-vision-mlops",
    "depends_on_past": False,
    "retries": DEFAULT_RETRIES,
    "retry_delay": timedelta(minutes=DEFAULT_RETRY_DELAY_MINUTES),
    "execution_timeout": timedelta(minutes=DEFAULT_TASK_TIMEOUT_MINUTES),
}


with DAG(
    dag_id="enterprise_vision_mlops_daily",
    description="Enterprise vision MLOps local orchestration DAG.",
    default_args=default_args,
    start_date=datetime(2026, 6, 1),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=DAG_RUN_TIMEOUT_MINUTES),
    tags=["enterprise-mlops", "vision", "local-control-plane"],
) as dag:
    dataset_intake_audit = pipeline_task("dataset_intake_audit", "dataset-intake-audit")
    domain_pack_check = pipeline_task("domain_pack_check", "domain-pack-check")
    object_store_bootstrap = pipeline_task("object_store_bootstrap", "object-store-bootstrap")
    data_validate = pipeline_task("data_validate", "data-validate")
    lakehouse_probe = pipeline_task("lakehouse_probe", "lakehouse-probe")
    image_quality = pipeline_task("image_quality", "image-quality")
    curation_workflow = pipeline_task("curation_workflow", "curation-workflow")
    dataset_shards = pipeline_task("dataset_shards", "dataset-shards")
    vlm_contract = pipeline_task("vlm_contract", "vlm-contract")
    vlm_batch_eval = pipeline_task("vlm_batch_eval", "vlm-batch-eval")
    vlm_reliability = pipeline_task("vlm_reliability", "vlm-reliability")
    vlm_rca = pipeline_task("vlm_rca", "vlm-rca")
    vlm_observability = pipeline_task("vlm_observability", "vlm-observability")
    train = pipeline_task("train", "train")
    register_model = pipeline_task("register_model", "register-model")
    model_lifecycle = pipeline_task("model_lifecycle", "model-lifecycle")
    deploy_check = pipeline_task("deploy_check", "deploy-check")
    monitor_check = pipeline_task("monitor_check", "monitor-check")

    (
        dataset_intake_audit
        >> domain_pack_check
        >> object_store_bootstrap
        >> data_validate
        >> lakehouse_probe
        >> image_quality
        >> curation_workflow
        >> dataset_shards
        >> vlm_contract
        >> vlm_batch_eval
        >> vlm_reliability
        >> vlm_rca
        >> vlm_observability
        >> train
        >> register_model
        >> model_lifecycle
        >> deploy_check
        >> monitor_check
    )

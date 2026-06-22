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


def pipeline_command(name: str) -> str:
    return (
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


default_args = {
    "owner": "enterprise-vision-mlops",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=10),
}


with DAG(
    dag_id="enterprise_vision_mlops_daily",
    description="Enterprise vision MLOps local orchestration DAG.",
    default_args=default_args,
    start_date=datetime(2026, 6, 1),
    schedule="@daily",
    catchup=False,
    tags=["enterprise-mlops", "vision", "local-control-plane"],
) as dag:
    data_ingest = BashOperator(
        task_id="data_ingest",
        bash_command=pipeline_command("data-ingest"),
        env=airflow_trace_env(),
        append_env=True,
    )

    data_validate = BashOperator(
        task_id="data_validate",
        bash_command=pipeline_command("data-validate"),
        env=airflow_trace_env(),
        append_env=True,
    )

    data_ingest >> data_validate

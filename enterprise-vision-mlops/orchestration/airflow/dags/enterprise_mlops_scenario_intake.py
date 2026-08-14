from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator


PROJECT_ROOT = Path(os.environ.get("EVM_PROJECT_ROOT", "/opt/airflow/evm_project"))
PYTHON_BIN = os.environ.get("EVM_PYTHON_BIN", "python")


with DAG(
    dag_id="enterprise_mlops_scenario_intake",
    description="Governed dataset acquisition and preprocessing for approved enterprise scenarios.",
    # Keep manual runs schedulable across local time zones and UTC-based Airflow metadata.
    start_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
    schedule=None,
    catchup=False,
    max_active_runs=2,
    dagrun_timeout=timedelta(minutes=45),
    tags=["enterprise-mlops", "data-intake", "multi-domain"],
) as dag:
    scenario_intake = BashOperator(
        task_id="scenario_intake",
        bash_command=(
            "set -euo pipefail; "
            f"cd {PROJECT_ROOT} && PYTHONPATH={PROJECT_ROOT / 'src'} "
            f"{PYTHON_BIN} scripts/run_profile_pipeline.py scenario-intake"
        ),
        env={
            "EVM_RUN_CONFIG": (
                "{{ dag_run.conf.get('pipeline_config_uri', '') if dag_run else '' }}"
            ),
            "EVM_TRACE_ID": (
                "{{ dag_run.conf.get('trace_id', '') if dag_run else '' }}"
            ),
            "EVM_TRACEPARENT": (
                "{{ dag_run.conf.get('traceparent', '') if dag_run else '' }}"
            ),
            "EVM_TRACESTATE": (
                "{{ dag_run.conf.get('tracestate', '') if dag_run else '' }}"
            ),
            "EVM_AIRFLOW_DAG_ID": "{{ dag.dag_id }}",
            "EVM_AIRFLOW_DAG_RUN_ID": "{{ run_id }}",
            "EVM_AIRFLOW_TASK_ID": "{{ task.task_id }}",
            "EVM_GIT_COMMIT": (
                "{{ dag_run.conf.get('source_commit', '') if dag_run else '' }}"
            ),
        },
        append_env=True,
        retries=1,
        retry_delay=timedelta(minutes=1),
        execution_timeout=timedelta(minutes=40),
    )

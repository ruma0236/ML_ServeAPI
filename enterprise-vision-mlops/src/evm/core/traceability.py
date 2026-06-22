from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    pipeline_name: str
    pipeline_run_id: str
    airflow_dag_id: str
    airflow_dag_run_id: str
    airflow_task_id: str
    airflow_try_number: str
    git_commit: str
    git_branch: str

    @classmethod
    def from_environment(cls, pipeline_name: str, pipeline_run_id: str) -> "TraceContext":
        airflow_dag_id = os.getenv("EVM_AIRFLOW_DAG_ID") or os.getenv("AIRFLOW_CTX_DAG_ID", "")
        airflow_dag_run_id = os.getenv("EVM_AIRFLOW_DAG_RUN_ID") or os.getenv("AIRFLOW_CTX_DAG_RUN_ID", "")
        airflow_task_id = os.getenv("EVM_AIRFLOW_TASK_ID") or os.getenv("AIRFLOW_CTX_TASK_ID", "")
        airflow_try_number = os.getenv("EVM_AIRFLOW_TRY_NUMBER") or os.getenv("AIRFLOW_CTX_TRY_NUMBER", "")
        trace_id = os.getenv("EVM_TRACE_ID", "")
        if not trace_id and airflow_dag_id and airflow_dag_run_id:
            trace_id = f"{airflow_dag_id}__{airflow_dag_run_id}"
        if not trace_id:
            trace_id = f"local__{pipeline_run_id}"

        return cls(
            trace_id=trace_id,
            pipeline_name=pipeline_name,
            pipeline_run_id=pipeline_run_id,
            airflow_dag_id=airflow_dag_id,
            airflow_dag_run_id=airflow_dag_run_id,
            airflow_task_id=airflow_task_id,
            airflow_try_number=airflow_try_number,
            git_commit=os.getenv("EVM_GIT_COMMIT", ""),
            git_branch=os.getenv("EVM_GIT_BRANCH", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "pipeline_name": self.pipeline_name,
            "pipeline_run_id": self.pipeline_run_id,
            "airflow_dag_id": self.airflow_dag_id,
            "airflow_dag_run_id": self.airflow_dag_run_id,
            "airflow_task_id": self.airflow_task_id,
            "airflow_try_number": self.airflow_try_number,
            "git_commit": self.git_commit,
            "git_branch": self.git_branch,
        }

    def mlflow_params(self) -> dict[str, str]:
        return {key: str(value) for key, value in self.to_dict().items() if value}

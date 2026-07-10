from __future__ import annotations

from pathlib import Path


def test_api_container_includes_control_panel_runtime_files() -> None:
    dockerfile = Path("apps/api/Dockerfile").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "COPY apps/api /app/apps/api" in dockerfile
    assert "COPY configs /app/configs" in dockerfile
    assert "COPY domain_packs /app/domain_packs" in dockerfile
    assert "COPY pyproject.toml docker-compose.yml /app/" in dockerfile
    assert "EVM_CONTROL_PANEL_CONFIG: configs/airflow.toml" in compose
    assert "EVM_EXPECTED_CI_COMMIT: ${EVM_EXPECTED_CI_COMMIT:-}" in compose
    assert "GIT_COMMIT: ${EVM_GIT_COMMIT:-}" in compose
    assert "GIT_BRANCH: ${EVM_GIT_BRANCH:-}" in compose


def test_pipeline_container_has_project_root_markers_for_config_resolution() -> None:
    dockerfile = Path("infra/docker/pipeline/Dockerfile").read_text(encoding="utf-8")
    k8s_config = Path("infra/kubernetes/local/configmaps.yaml").read_text(encoding="utf-8")
    k8s_jobs = Path("infra/kubernetes/local/pipeline-job.yaml").read_text(encoding="utf-8")

    assert "COPY pyproject.toml docker-compose.yml /app/" in dockerfile
    assert "EVM_PIPELINE_CONFIG: /app/configs/airflow.toml" in k8s_config
    assert "configs/airflow.toml" in k8s_jobs
    assert "configs/local_visa.toml" not in k8s_jobs

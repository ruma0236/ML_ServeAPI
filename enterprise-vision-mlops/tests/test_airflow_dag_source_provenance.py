from __future__ import annotations

import ast
from pathlib import Path


def test_airflow_tasks_bind_source_revision_from_dag_run_conf() -> None:
    project = Path(__file__).resolve().parents[1]
    source = (
        project / "orchestration" / "airflow" / "dags" / "enterprise_vision_mlops_daily.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not isinstance(key, ast.Constant) or key.value not in {
                "EVM_GIT_COMMIT",
                "EVM_GIT_BRANCH",
            }:
                continue
            assert isinstance(value, ast.Call)
            assert isinstance(value.func, ast.Name)
            assert value.func.id == "dag_conf_template"
            assert isinstance(value.args[0], ast.Constant)
            bindings[str(key.value)] = str(value.args[0].value)

    assert bindings == {
        "EVM_GIT_COMMIT": "source_commit",
        "EVM_GIT_BRANCH": "source_branch",
    }
    assert "append_env=True" in source

from __future__ import annotations

from pathlib import Path


def test_start_local_stack_builds_shared_images_once_and_checks_failures() -> None:
    script = Path("scripts/dev/start_local_stack.ps1").read_text(encoding="utf-8")

    assert '"enterprise-vision-mlops-airflow:local" = "airflow-init"' in script
    assert 'Invoke-Docker -Arguments @("compose", "build", $target)' in script
    assert 'Invoke-Docker -Arguments @("compose", "up", "-d", "--no-build")' in script
    assert "$commit = (git rev-parse HEAD).Trim()" in script
    assert "$env:EVM_EXPECTED_CI_COMMIT = $commit" in script
    assert "if ($LASTEXITCODE -ne 0)" in script
    assert 'throw "docker $($Arguments -join \' \') exited with code $LASTEXITCODE"' in script

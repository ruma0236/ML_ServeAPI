from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTORCH_INDEX = "https://download.pytorch.org/whl/cu128"
WINDOWS_CP311_WHEEL = {
    "url": (
        "https://download-r2.pytorch.org/whl/cu128/torch-2.7.1%2Bcu128-cp311-cp311-win_amd64.whl"
    ),
    "hash": "sha256:138c66dcd0ed2f07aafba3ed8b7958e2bed893694990e0b4b55b6b2b4a336aa6",
    "upload-time": "2025-06-03T18:31:13Z",
}


def _toml(relative_path: str) -> dict[str, object]:
    with (ROOT / relative_path).open("rb") as handle:
        return tomllib.load(handle)


def test_windows_cuda_extra_uses_only_the_explicit_cu128_index() -> None:
    project = _toml("pyproject.toml")
    optional = project["project"]["optional-dependencies"]  # type: ignore[index]
    uv = project["tool"]["uv"]  # type: ignore[index]

    assert optional["windows-cuda"] == ["torch==2.7.1"]
    assert all(not dependency.startswith("torch") for dependency in optional["test"])
    assert uv["sources"]["torch"] == [  # type: ignore[index]
        {"index": "pytorch-cu128", "extra": "windows-cuda"}
    ]
    assert uv["index"] == [{"name": "pytorch-cu128", "url": PYTORCH_INDEX, "explicit": True}]


def test_windows_cuda_extra_is_bound_to_the_exact_locked_wheel() -> None:
    lock = _toml("uv.lock")
    packages = lock["package"]
    project = next(package for package in packages if package["name"] == "enterprise-vision-mlops")
    torch = next(package for package in packages if package["name"] == "torch")

    assert project["optional-dependencies"]["windows-cuda"] == [{"name": "torch"}]
    torch_requirement = next(
        requirement
        for requirement in project["metadata"]["requires-dist"]
        if requirement["name"] == "torch"
    )
    assert torch_requirement == {
        "name": "torch",
        "marker": "extra == 'windows-cuda'",
        "specifier": "==2.7.1",
        "index": PYTORCH_INDEX,
        "conflict": {"package": "enterprise-vision-mlops", "extra": "windows-cuda"},
    }
    assert torch["version"] == "2.7.1+cu128"
    assert torch["source"] == {"registry": PYTORCH_INDEX}
    assert [
        wheel for wheel in torch["wheels"] if wheel["url"].endswith("cp311-cp311-win_amd64.whl")
    ] == [WINDOWS_CP311_WHEEL]

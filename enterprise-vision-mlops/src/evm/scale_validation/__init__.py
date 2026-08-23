"""Contracts and utilities for distributed-scale validation evidence.

The public names remain backwards compatible, while lazy loading keeps lightweight
Spark executor modules from importing control-plane-only dependencies.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "BenchmarkEvidence",
    "PrivateEvidenceIndex",
    "ScenarioProgressLedger",
    "S0RuntimeConfig",
    "S0RuntimeError",
    "render_progress_markdown",
]


_EXPORTS = {
    "BenchmarkEvidence": ("evm.scale_validation.contracts", "BenchmarkEvidence"),
    "PrivateEvidenceIndex": ("evm.scale_validation.contracts", "PrivateEvidenceIndex"),
    "ScenarioProgressLedger": ("evm.scale_validation.contracts", "ScenarioProgressLedger"),
    "render_progress_markdown": ("evm.scale_validation.contracts", "render_progress_markdown"),
    "S0RuntimeConfig": ("evm.scale_validation.s0_runtime", "S0RuntimeConfig"),
    "S0RuntimeError": ("evm.scale_validation.s0_runtime", "S0RuntimeError"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

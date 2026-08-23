from __future__ import annotations

from typing import Any


TINY_MLP_ARCHITECTURE = "28-64-32-1-relu-fp32"


def build_tiny_mlp(torch: Any) -> Any:
    class TinyMlp(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layers = torch.nn.Sequential(
                torch.nn.Linear(28, 64),
                torch.nn.ReLU(),
                torch.nn.Linear(64, 32),
                torch.nn.ReLU(),
                torch.nn.Linear(32, 1),
            )

        def forward(self, value: Any) -> Any:
            return self.layers(value).squeeze(-1)

    return TinyMlp()

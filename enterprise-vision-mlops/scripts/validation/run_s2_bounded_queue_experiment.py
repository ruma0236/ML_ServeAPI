from __future__ import annotations

from importlib import import_module
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

main = import_module("evm.scale_validation.s2_runtime").main


if __name__ == "__main__":
    raise SystemExit(main())


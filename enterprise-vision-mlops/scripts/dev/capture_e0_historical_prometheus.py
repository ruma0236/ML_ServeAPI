from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


QUERIES = {
    "target_up": 'up{job="evm-s8-v4-e0"}',
    "request_success": 'nv_inference_request_success{job="evm-s8-v4-e0",model="e0_cuda_linear",version="1"}',
    "inference_count": 'nv_inference_count{job="evm-s8-v4-e0",model="e0_cuda_linear",version="1"}',
    "gpu_memory": 'nv_gpu_memory_used_bytes{job="evm-s8-v4-e0"}',
}


def canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def query_range(base_url: str, query: str, start: str, end: str, step: str) -> Any:
    parameters = urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": step}
    )
    with urllib.request.urlopen(
        f"{base_url.rstrip('/')}/api/v1/query_range?{parameters}", timeout=15
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"prometheus_http_status:{response.status}")
        return json.loads(response.read())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture immutable E0 Prometheus history.")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--start", default="2026-08-24T15:45:30Z")
    parser.add_argument("--end", default="2026-08-24T15:46:50Z")
    parser.add_argument("--step", default="1s")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "schema_version": "evm.s8_v4.e0_prometheus_history.v1",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "window": {"start": args.start, "end": args.end, "step": args.step},
        "queries": {
            name: {
                "query": query,
                "response": query_range(
                    args.prometheus_url, query, args.start, args.end, args.step
                ),
            }
            for name, query in QUERIES.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=False)
    args.output.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")
    print(
        canonical(
            {
                "path": str(args.output),
                "bytes": args.output.stat().st_size,
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

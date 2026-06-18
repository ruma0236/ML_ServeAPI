from __future__ import annotations

import json
import socket
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.pipeline import build_context, write_json, write_markdown_report


def _load_workers(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("rb") as fp:
        payload = tomllib.load(fp)
    return payload.get("workers", {})


def _tailscale_status() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return {}


def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _peer_index(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for peer in status.get("Peer", {}).values():
        keys = [
            peer.get("HostName", ""),
            peer.get("DNSName", "").rstrip("."),
            *(peer.get("TailscaleIPs", []) or []),
        ]
        for key in keys:
            if key:
                index[str(key).lower()] = peer
    return index


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("remote_workers", config_path)
    cfg = ctx.pipeline_config()
    workers_config = ctx.path(str(cfg.get("workers_config", "configs/workers.toml")))
    workers = _load_workers(workers_config)
    status = _tailscale_status()
    peers = _peer_index(status)

    inventory: list[dict[str, Any]] = []
    for worker_id, worker in workers.items():
        tailscale_ip = str(worker.get("tailscale_ip", ""))
        host = str(worker.get("host", worker_id))
        dns_name = str(worker.get("dns_name", "")).rstrip(".")
        port = int(worker.get("ssh_port", 22))

        peer = (
            peers.get(host.lower())
            or peers.get(dns_name.lower())
            or peers.get(tailscale_ip.lower())
            or {}
        )
        online = bool(peer.get("Online", False))
        ssh_port_open = _tcp_probe(tailscale_ip or dns_name or host, port)

        inventory.append(
            {
                "worker_id": worker_id,
                "display_name": worker.get("display_name", worker_id),
                "host": host,
                "dns_name": dns_name,
                "tailscale_ip": tailscale_ip,
                "os": worker.get("os", "unknown"),
                "online": online,
                "ssh_port": port,
                "ssh_port_open": ssh_port_open,
                "remote_exec_ready": False,
                "roles": worker.get("roles", []),
                "notes": worker.get("notes", ""),
                "last_seen": peer.get("LastSeen", ""),
                "relay": peer.get("Relay", ""),
            }
        )

    online_workers = sum(1 for item in inventory if item["online"])
    ssh_open_workers = sum(1 for item in inventory if item["ssh_port_open"])
    summary = {
        "workers_config": str(workers_config.relative_to(ctx.project_root)),
        "workers": len(inventory),
        "online_workers": online_workers,
        "ssh_open_workers": ssh_open_workers,
        "inventory": inventory,
    }

    write_json(ctx.run_dir / "summary.json", summary)

    lines = ["", "## Worker Inventory", ""]
    lines.append("| Worker | OS | Online | SSH Port | Remote Roles |")
    lines.append("|---|---|---:|---:|---|")
    for item in inventory:
        roles = ", ".join(item.get("roles", []))
        lines.append(
            f"| `{item['display_name']}` | `{item['os']}` | `{item['online']}` | "
            f"`{item['ssh_port_open']}` | {roles} |"
        )

    write_markdown_report(
        ctx,
        "Remote Workers Pipeline",
        {
            "workers": len(inventory),
            "online_workers": online_workers,
            "ssh_open_workers": ssh_open_workers,
            "workers_config": str(workers_config.relative_to(ctx.project_root)),
        },
        lines,
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()

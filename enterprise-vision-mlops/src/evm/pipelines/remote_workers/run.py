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


def _tailscale_status() -> tuple[dict[str, Any], str]:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or f"exit_code={result.returncode}"
            return {}, f"tailscale_status_failed: {error}"
        return json.loads(result.stdout), ""
    except FileNotFoundError:
        return {}, "tailscale_cli_not_found"
    except json.JSONDecodeError as exc:
        return {}, f"tailscale_status_json_decode_failed: {exc}"
    except subprocess.TimeoutExpired:
        return {}, "tailscale_status_timeout"
    except subprocess.SubprocessError as exc:
        return {}, f"tailscale_status_failed: {exc}"


def _tcp_probe(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_home_path(value: str) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser()


def _ssh_probe(
    user: str,
    host: str,
    key_path: Path | None,
    command: str,
    timeout: int = 10,
) -> tuple[bool, str]:
    if not user or key_path is None or not key_path.exists():
        return False, ""

    target = f"{user}@{host}"
    result = subprocess.run(
        [
            "ssh",
            "-i",
            str(key_path),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={timeout}",
            target,
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout + 5,
    )
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return result.returncode == 0, output


def _peer_index(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    nodes = []
    if status.get("Self"):
        nodes.append(status["Self"])
    nodes.extend(status.get("Peer", {}).values())

    for peer in nodes:
        keys = [
            peer.get("HostName", ""),
            peer.get("DNSName", "").rstrip("."),
            *(peer.get("TailscaleIPs", []) or []),
        ]
        for key in keys:
            if key:
                index[str(key).lower()] = peer
    return index


def _connectivity_status(
    tailnet_online: bool,
    ssh_port_open: bool,
    remote_exec_ready: bool,
) -> str:
    if remote_exec_ready:
        return "remote_exec_ready"
    if ssh_port_open:
        return "ssh_port_open"
    if tailnet_online:
        return "tailnet_online"
    return "unreachable"


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("remote_workers", config_path)
    cfg = ctx.pipeline_config()
    workers_config = ctx.path(str(cfg.get("workers_config", "configs/workers.toml")))
    workers = _load_workers(workers_config)
    status, tailnet_status_error = _tailscale_status()
    peers = _peer_index(status)

    inventory: list[dict[str, Any]] = []
    for worker_id, worker in workers.items():
        tailscale_ip = str(worker.get("tailscale_ip", ""))
        host = str(worker.get("host", worker_id))
        dns_name = str(worker.get("dns_name", "")).rstrip(".")
        port = int(worker.get("ssh_port", 22))
        ssh_user = str(worker.get("ssh_user", ""))
        ssh_key_path = _resolve_home_path(str(worker.get("ssh_key_path", "")))
        remote_exec_probe = str(worker.get("remote_exec_probe", "whoami; hostname; uname -m"))

        peer = (
            peers.get(host.lower())
            or peers.get(dns_name.lower())
            or peers.get(tailscale_ip.lower())
            or {}
        )
        tailnet_online = bool(peer.get("Online", False))
        ssh_port_open = _tcp_probe(tailscale_ip or dns_name or host, port)
        remote_exec_ready, remote_exec_output = _ssh_probe(
            ssh_user,
            dns_name or tailscale_ip or host,
            ssh_key_path,
            remote_exec_probe,
        )
        connectivity_status = _connectivity_status(
            tailnet_online,
            ssh_port_open,
            remote_exec_ready,
        )

        inventory.append(
            {
                "worker_id": worker_id,
                "display_name": worker.get("display_name", worker_id),
                "host": host,
                "dns_name": dns_name,
                "tailscale_ip": tailscale_ip,
                "os": worker.get("os", "unknown"),
                "tailnet_online": tailnet_online,
                "online": tailnet_online,
                "ssh_port": port,
                "ssh_port_open": ssh_port_open,
                "remote_exec_ready": remote_exec_ready,
                "remote_exec_output": remote_exec_output,
                "connectivity_status": connectivity_status,
                "roles": worker.get("roles", []),
                "notes": worker.get("notes", ""),
                "last_seen": peer.get("LastSeen", ""),
                "relay": peer.get("Relay", ""),
            }
        )

    online_workers = sum(1 for item in inventory if item["tailnet_online"])
    ssh_open_workers = sum(1 for item in inventory if item["ssh_port_open"])
    remote_exec_ready_workers = sum(1 for item in inventory if item["remote_exec_ready"])
    reachable_workers = sum(1 for item in inventory if item["connectivity_status"] != "unreachable")
    summary = {
        "workers_config": str(workers_config.relative_to(ctx.project_root)),
        "workers": len(inventory),
        "tailnet_status_available": not tailnet_status_error,
        "tailnet_status_error": tailnet_status_error,
        "online_workers": online_workers,
        "ssh_open_workers": ssh_open_workers,
        "remote_exec_ready_workers": remote_exec_ready_workers,
        "reachable_workers": reachable_workers,
        "inventory": inventory,
    }

    write_json(ctx.run_dir / "summary.json", summary)

    lines = ["", "## Worker Inventory", ""]
    lines.append("| Worker | OS | Tailnet Online | SSH Port | Remote Exec | Connectivity | Remote Roles |")
    lines.append("|---|---|---:|---:|---:|---|---|")
    for item in inventory:
        roles = ", ".join(item.get("roles", []))
        lines.append(
            f"| `{item['display_name']}` | `{item['os']}` | `{item['tailnet_online']}` | "
            f"`{item['ssh_port_open']}` | `{item['remote_exec_ready']}` | "
            f"`{item['connectivity_status']}` | {roles} |"
        )

    if tailnet_status_error:
        lines.extend(
            [
                "",
                "## Tailnet Status Note",
                "",
                f"- Tailscale status source unavailable: `{tailnet_status_error}`.",
                "- Use SSH/TCP probe fields for current control-plane reachability.",
            ]
        )

    write_markdown_report(
        ctx,
        "Remote Workers Pipeline",
        {
            "workers": len(inventory),
            "tailnet_status_available": not tailnet_status_error,
            "online_workers": online_workers,
            "ssh_open_workers": ssh_open_workers,
            "remote_exec_ready_workers": remote_exec_ready_workers,
            "reachable_workers": reachable_workers,
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

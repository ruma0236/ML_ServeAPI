"""One fresh, bounded Docker client run; never controls the service stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(directory):
    return {
        p.relative_to(directory).as_posix(): sha(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input-directory", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--tool-script", choices=("smoke.py", "test_smoke.py"), default="smoke.py")
    parser.add_argument("--network", choices=("none", "evm-local"), default="none")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--image-lock", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"ct-[a-z0-9][a-z0-9-]{1,55}", args.run_id):
        parser.error("run ID must be a unique ct- lowercase safe label")
    if not 10 <= args.timeout <= 180:
        parser.error("outer timeout must be 10..180 seconds")
    if args.tool_script == "test_smoke.py" and args.network != "none":
        parser.error("tool fixtures must use --network none")
    code = Path(__file__).resolve().parent
    inputs = args.input_directory.resolve(strict=True)
    profile = inputs / "profile.json"
    if not profile.is_file():
        parser.error("input-directory/profile.json required")
    image_lock = (args.image_lock or code / "image.local.json").resolve(strict=True)
    image = json.loads(image_lock.read_text(encoding="utf-8"))["image_id"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        parser.error("local image identity must be immutable sha256")
    for directory in (code, inputs, args.output_root.resolve()):
        if "," in str(directory):
            parser.error("mount paths containing commas are unsupported")
    output = args.output_root.resolve() / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    commands = []

    def command(label, argv, timeout=30, check=True):
        result = subprocess.run([args.docker, *argv], capture_output=True, timeout=timeout)
        (output / (label + ".stdout.log")).write_bytes(result.stdout)
        (output / (label + ".stderr.log")).write_bytes(result.stderr)
        commands.append(
            {
                "argv": [args.docker, *argv],
                "exit_code": result.returncode,
                "stdout": label + ".stdout.log",
                "stderr": label + ".stderr.log",
            }
        )
        if check and result.returncode:
            raise RuntimeError(f"{label} exit {result.returncode}; original logs retained")
        return result

    before_code, before_inputs = inventory(code), inventory(inputs)
    report = {
        "schema": "evm.container-test.host-run.v1",
        "run_id": args.run_id,
        "started_utc": now(),
        "image_id": image,
        "image_lock_sha256": sha(image_lock),
        "host_executor": {
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "executable": sys.executable,
            "python": sys.version,
        },
        "scope": "TOOL_FIXTURE" if args.network == "none" else "BOUNDED_API_SMOKE",
        "code_before": before_code,
        "inputs_before": before_inputs,
        "container_id": None,
        "runtime_residue": "UNKNOWN",
        "commands": commands,
    }
    container_id = None
    final_exit = 2
    try:
        baseline = command(
            "baseline", ["ps", "-a", "--no-trunc", "--format", "{{.ID}} {{.Names}} {{.State}}"]
        )
        report["baseline_sha256"] = hashlib.sha256(baseline.stdout).hexdigest()
        identity = json.loads(command("image", ["image", "inspect", image]).stdout)[0]
        if identity["Os"] != "linux" or identity["Architecture"] != "amd64":
            raise RuntimeError("image platform mismatch")
        create = [
            "create",
            "--name",
            "evm-" + args.run_id,
            "--label",
            "evm.ct.run=" + args.run_id,
            "--network",
            args.network,
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--pids-limit",
            "128",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "1000:1000",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "CT_RUN_ID=" + args.run_id,
            "--mount",
            f"type=bind,src={code},dst=/work/code,readonly",
            "--mount",
            f"type=bind,src={inputs},dst=/work/input,readonly",
            "--mount",
            f"type=bind,src={output},dst=/work/output",
            "--workdir",
            "/work/code",
            image,
            "-B",
            "/work/code/" + args.tool_script,
            "--profile",
            "/work/input/profile.json",
            "--output",
            "/work/output/result.json",
        ]
        created = command("create", create)
        container_id = created.stdout.decode("ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise RuntimeError("invalid container ID returned")
        report["container_id"] = container_id
        observed = json.loads(command("created-inspect", ["inspect", container_id]).stdout)[0]
        if (
            observed["Config"]["Labels"].get("evm.ct.run") != args.run_id
            or observed["Name"] != "/evm-" + args.run_id
        ):
            raise RuntimeError("run ownership mismatch")
        resolved = json.loads(
            command("resolved-image", ["image", "inspect", observed["Image"]]).stdout
        )[0]
        if observed["Config"]["Image"] != image or any(
            resolved[key] != identity[key] for key in ("RootFS", "Config", "Os", "Architecture")
        ):
            raise RuntimeError("created image identity mismatch")
        actual_host = observed["HostConfig"]
        expected_host = {
            "NanoCpus": 1_000_000_000,
            "Memory": 512 * 1024 * 1024,
            "PidsLimit": 128,
            "ReadonlyRootfs": True,
            "Privileged": False,
            "PidMode": "",
            "NetworkMode": args.network,
        }
        if any(actual_host.get(key) != value for key, value in expected_host.items()):
            raise RuntimeError("created resource/isolation configuration mismatch")
        if (
            actual_host.get("Devices")
            or actual_host.get("DeviceRequests")
            or actual_host.get("CapDrop") != ["ALL"]
            or actual_host.get("SecurityOpt") != ["no-new-privileges:true"]
            or observed["Config"].get("User") != "1000:1000"
        ):
            raise RuntimeError("created privileges mismatch")
        mounts = {
            item["Destination"]: item for item in observed["Mounts"] if item["Type"] == "bind"
        }
        expected_mounts = {
            "/work/code": (code, False),
            "/work/input": (inputs, False),
            "/work/output": (output, True),
        }
        if set(mounts) != set(expected_mounts):
            raise RuntimeError("created bind mount set mismatch")
        for destination, (source, writable) in expected_mounts.items():
            actual = mounts[destination]
            if (
                actual["RW"] != writable
                or actual["Source"].replace("\\", "/").lower() != source.as_posix().lower()
            ):
                raise RuntimeError("created input/output mount mismatch")
        command("start", ["start", container_id])
        deadline = time.monotonic() + args.timeout
        samples = []

        def probe_timeout():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report["outer_timeout"] = True
                raise TimeoutError("bounded container observation deadline")
            return min(15, remaining)

        while True:
            state = subprocess.run(
                [args.docker, "inspect", "--format", "{{json .State}}", container_id],
                capture_output=True,
                timeout=probe_timeout(),
                check=True,
            )
            report["last_state"] = json.loads(state.stdout)
            if not report["last_state"]["Running"]:
                break
            stat = subprocess.run(
                [args.docker, "stats", "--no-stream", "--format", "{{json .}}", container_id],
                capture_output=True,
                timeout=probe_timeout(),
            )
            host_cpu = None
            if os.name == "nt":
                cpu = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Command",
                        "(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter \"Name='_Total'\").PercentProcessorTime",
                    ],
                    capture_output=True,
                    timeout=probe_timeout(),
                )
                if cpu.returncode == 0 and cpu.stdout.strip().isdigit():
                    host_cpu = int(cpu.stdout.strip())
            samples.append(
                {
                    "utc": now(),
                    "exit_code": stat.returncode,
                    "physical_windows_host_cpu_percent": host_cpu,
                    "stdout": stat.stdout.decode("utf-8", errors="replace"),
                    "stderr": stat.stderr.decode("utf-8", errors="replace"),
                }
            )
            if time.monotonic() >= deadline:
                report["outer_timeout"] = True
                command("timeout-stop", ["stop", "--time", "10", container_id])
                break
            time.sleep(1)
        (output / "resource-samples.json").write_text(
            json.dumps(samples, indent=2), encoding="utf-8"
        )
        state = json.loads(command("final-inspect", ["inspect", container_id]).stdout)[0]
        report["container_state"] = state["State"]
        final_exit = int(state["State"]["ExitCode"])
        if report.get("outer_timeout") or state["State"].get("OOMKilled"):
            final_exit = final_exit or 2
        command("container-logs", ["logs", "--timestamps", container_id], check=False)
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        final_exit = 2
    finally:
        if container_id and re.fullmatch(r"[a-f0-9]{64}", container_id):
            try:
                state = json.loads(command("cleanup-inspect", ["inspect", container_id]).stdout)[0]
                if (
                    state["Config"]["Labels"].get("evm.ct.run") != args.run_id
                    or state["Name"] != "/evm-" + args.run_id
                ):
                    raise RuntimeError("refuse cleanup: label mismatch")
                if state["State"]["Running"]:
                    command("cleanup-stop", ["stop", "--time", "10", container_id])
                command("cleanup-logs", ["logs", "--timestamps", container_id], check=False)
                captured_state = json.loads(
                    command("cleanup-final-state", ["inspect", container_id]).stdout
                )[0]
                report["container_exit_code"] = captured_state["State"]["ExitCode"]
                (output / "pre-removal-artifacts.json").write_text(
                    json.dumps(inventory(output), indent=2), encoding="utf-8"
                )
                command("remove-owned-container", ["rm", container_id])
                residual = command(
                    "residue", ["ps", "-a", "-q", "--filter", "label=evm.ct.run=" + args.run_id]
                )
                report["runtime_residue"] = 0 if not residual.stdout.strip() else "NONZERO"
            except Exception as exc:
                report["cleanup_error"] = str(exc)
                final_exit = 2
        else:
            residual = command(
                "residue",
                ["ps", "-a", "-q", "--filter", "label=evm.ct.run=" + args.run_id],
                check=False,
            )
            report["runtime_residue"] = (
                0 if residual.returncode == 0 and not residual.stdout.strip() else "UNKNOWN"
            )
        report["code_after"], report["inputs_after"] = inventory(code), inventory(inputs)
        report["code_input_unchanged"] = (
            before_code == report["code_after"] and before_inputs == report["inputs_after"]
        )
        if not report["code_input_unchanged"] or report["runtime_residue"] != 0:
            final_exit = final_exit or 2
        try:
            tool_result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            if not isinstance(tool_result, dict):
                raise ValueError("tool result is not an object")
            report["tool_result_sha256"] = sha(output / "result.json")
        except (OSError, ValueError) as exc:
            report["tool_result_error"] = str(exc)
            final_exit = final_exit or 2
        postcondition = command(
            "postcondition",
            ["ps", "-a", "--no-trunc", "--format", "{{.ID}} {{.Names}} {{.State}}"],
            check=False,
        )
        report["existing_container_inventory_unchanged"] = (
            postcondition.returncode == 0
            and (output / "baseline.stdout.log").is_file()
            and postcondition.stdout == (output / "baseline.stdout.log").read_bytes()
        )
        if not report["existing_container_inventory_unchanged"]:
            final_exit = final_exit or 2
        report["finished_utc"], report["exit_code"] = now(), final_exit
        report["output_files"] = inventory(output)
        (output / "host-run.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "exit_code": final_exit,
                    "runtime_residue": report["runtime_residue"],
                    "result": str(output / "host-run.json"),
                }
            )
        )
    return final_exit


if __name__ == "__main__":
    raise SystemExit(main())

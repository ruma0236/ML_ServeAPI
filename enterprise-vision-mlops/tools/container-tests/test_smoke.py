"""Short loopback fixtures only: never reports actual MLOps experiment credit."""

from __future__ import annotations

import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import threading
import time


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.profile.read_bytes())
    assert config["scope"] == "TOOL_FIXTURE"
    code = Path(__file__).resolve().parent
    template = json.loads((code / "profiles/smoke.ready.example.json").read_bytes())
    openapi = (args.profile.parent / "openapi.json").read_bytes()
    assert digest(openapi) == config["openapi_sha256"]
    cases = []
    output_root = args.output.parent / "cases"
    output_root.mkdir(exist_ok=False)
    imported = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import locust,httpx,jsonschema,numpy,pydantic; "
            "import json; print(json.dumps({m.__name__:m.__file__ for m in "
            "(locust,httpx,jsonschema,numpy,pydantic)}))",
        ],
        capture_output=True,
        timeout=20,
    )
    (output_root / "imports.stdout.log").write_bytes(imported.stdout)
    (output_root / "imports.stderr.log").write_bytes(imported.stderr)
    identity_ok = os.getuid() == os.getgid() == pwd.getpwnam("locust").pw_uid == 1000
    cases.append(
        {
            "case": "imports_and_nonroot_identity",
            "passed": imported.returncode == 0 and identity_ok,
            "exit_code": imported.returncode,
            "uid": os.getuid(),
            "gid": os.getgid(),
        }
    )
    catalog = {
        "schema_version": "evm.s3_capacity_probe_catalog.v1",
        "dataset_id": "uci-higgs",
        "dataset_version": "tool-fixture-not-real-data",
        "dataset_identity_sha256": "a" * 64,
        "split_manifest_sha256": "b" * 64,
        "source_uri": "https://example.invalid/fixture",
        "source_doi": "10.24432/C5V312",
        "license": "CC BY 4.0",
        "feature_count": 28,
        "probes": [
            {
                "probe_family": family,
                "algorithm": "linear_logit",
                "model_identity_sha256": "c" * 64,
                "artifact_sha256": "d" * 64,
                "feature_count": 28,
            }
            for family in (
                "logistic",
                "probabilistic",
                "online-linear",
                "branch-heavy",
                "incremental",
            )
        ],
    }
    body = {
        "schema_version": "evm.s3_capacity_probe_request.v1",
        "probe_family": "logistic",
        "dataset_identity_sha256": "a" * 64,
        "features": [1.0] * 28,
    }
    for mode in (
        "success",
        "invalid-config",
        "wrong-model",
        "wrong-trace",
        "rejected",
        "accepted-incomplete",
        "server-error",
        "timeout",
        "sigterm",
    ):
        case_dir = output_root / mode
        case_dir.mkdir()
        posted, release = threading.Event(), threading.Event()
        observations = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, payload):
                raw = encoded(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                traceparent = self.headers.get("traceparent", "")
                trace_id = traceparent.split("-")[1] if traceparent else "0" * 32
                if mode == "wrong-trace" and self.command == "POST":
                    trace_id = "f" * 32
                self.send_header("x-evm-trace-id", trace_id)
                self.send_header("traceparent", "00-" + trace_id + "-123456789abcdef0-01")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    # Deliberately timed-out/cancelled fixture transport only.
                    observations.append({"expected_fixture_disconnect": mode})

            def do_GET(self):
                observations.append({"method": "GET", "path": self.path})
                if self.path == "/health":
                    self.reply(200, {"status": "ok", "service": "tool-fixture"})
                elif self.path.endswith("/capacity-probes"):
                    self.reply(200, catalog)
                else:
                    self.reply(404, {"error": "fixture_path_not_found"})

            def do_POST(self):
                observed_body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observations.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "body_sha256": digest(encoded(observed_body)),
                    }
                )
                posted.set()
                if mode in ("timeout", "sigterm"):
                    release.wait(8)
                payload = {
                    "schema_version": "evm.s3_capacity_probe_response.v1",
                    "probe_family": "logistic",
                    "dataset_identity_sha256": "a" * 64,
                    "model_identity_sha256": ("e" if mode == "wrong-model" else "c") * 64,
                    "prediction": 1,
                    "positive_probability": 0.75,
                    "timings": {
                        "admission_wait_ms": 0,
                        "queue_wait_ms": 0,
                        "validation_ms": 0.1,
                        "transform_ms": 0.1,
                        "prediction_ms": 0.1,
                        "compute_ms": 0.3,
                        "total_ms": 0.4,
                    },
                    "runtime": {
                        "api_replica_id": "fixture",
                        "cpu_worker_count": 1,
                        "worker_slot": 0,
                        "canonical_request_bytes": len(encoded(observed_body)),
                    },
                }
                statuses = {"rejected": 429, "accepted-incomplete": 202, "server-error": 500}
                self.reply(statuses.get(mode, 200), payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        profile = copy.deepcopy(template)
        profile.update(
            status="READY", acceptance_credit=False, blockers=[], profile_id="tool-fixture-" + mode
        )
        profile["target"].update(network="none", base_url=f"http://127.0.0.1:{server.server_port}")
        profile["load"]["maximum_request_count"] = 1
        profile["expected_runtime"] = {
            "api_replica_ids": ["fixture"],
            "cpu_worker_count": 1,
            "worker_slots": [0],
        }
        profile["provenance"] = {
            "source_commit": config["source_commit"],
            "source_tree": config["source_tree"],
            "smoke_sha256": digest((code / "smoke.py").read_bytes()),
            "target_image_id": config["tool_image_id"],
        }
        corpus = {
            "schema_version": "evm.container_test.request_corpus.v1",
            "requests": [{"logical_request_id": "fixture-1", "body": body}],
        }
        for name, raw in (
            ("openapi.json", openapi),
            ("catalog.json", encoded(catalog)),
            ("request-corpus.json", encoded(corpus)),
        ):
            (case_dir / name).write_bytes(raw)
        profile["frozen_inputs"].update(
            openapi_sha256=digest(openapi),
            catalog_sha256=digest(encoded(catalog)),
            catalog_canonical_sha256=digest(encoded(catalog)),
            request_corpus_sha256=digest(encoded(corpus)),
            dataset_identity_sha256="a" * 64,
            model_identity_sha256="c" * 64,
            artifact_sha256="d" * 64,
        )
        if mode == "invalid-config":
            profile["unexpected_configuration"] = True
        profile_path = case_dir / "profile.json"
        profile_path.write_bytes(encoded(profile))
        result_path = case_dir / "result.json"
        argv = [
            sys.executable,
            "-B",
            str(code / "smoke.py"),
            "--profile",
            str(profile_path),
            "--output",
            str(result_path),
        ]
        process = None
        started = time.monotonic()
        case = {
            "case": mode,
            "argv": argv,
            "passed": False,
            "expected_credit": "ZERO-CREDIT_TOOL_ONLY",
        }
        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if mode == "sigterm":
                if not posted.wait(10):
                    raise RuntimeError("fixture POST not reached before signal")
                process.send_signal(signal.SIGTERM)
                time.sleep(0.15)
                release.set()
            stdout, stderr = process.communicate(timeout=22)
            (case_dir / "stdout.log").write_bytes(stdout)
            (case_dir / "stderr.log").write_bytes(stderr)
            result = json.loads(result_path.read_bytes())
            decision = result["acceptance"]["local_decision"]
            expected_exit = 0 if mode == "success" else 1
            valid = process.returncode == result["process_exit_code"] == expected_exit
            valid = valid and decision == ("PASS" if mode == "success" else "FAIL")
            if mode == "invalid-config":
                valid = valid and not observations and bool(result["errors"])
            else:
                counts = result["accounting"]
                valid = (
                    valid
                    and counts["attempts"] == 1
                    and counts["skipped"] == counts["deselected"] == 0
                )
                if mode == "success":
                    valid = (
                        valid
                        and counts["succeeded"] == 1
                        and counts["unknown"] == counts["failed"] == 0
                    )
                elif mode in ("timeout", "accepted-incomplete", "server-error"):
                    valid = valid and counts["unknown"] == 1 and counts["succeeded"] == 0
                elif mode == "sigterm":
                    valid = valid and result["stop"]["signal"] == "SIGTERM"
                else:
                    valid = valid and counts["failed"] == 1 and counts["succeeded"] == 0
            case.update(
                passed=valid,
                exit_code=process.returncode,
                decision=decision,
                result_sha256=digest(result_path.read_bytes()),
                elapsed_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            case["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            release.set()
            if process and process.poll() is None:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()  # Only the deliberately created fixture child, never a service.
                    stdout, stderr = process.communicate(timeout=5)
                    case["forced_fixture_child_recovery"] = True
                (case_dir / "recovery.stdout.log").write_bytes(stdout)
                (case_dir / "recovery.stderr.log").write_bytes(stderr)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            case["child_exited"] = process is None or process.poll() is not None
            case["listener_thread_exited"] = not thread.is_alive()
            case["passed"] = (
                case["passed"] and case["child_exited"] and case["listener_thread_exited"]
            )
            (case_dir / "fixture-observations.json").write_bytes(encoded(observations))
            cases.append(case)
    result = {
        "schema_version": "evm.container_test.tool_suitability.v1",
        "scope": "TOOL_FIXTURE",
        "actual_mlops_requests": 0,
        "run_id": os.environ.get("CT_RUN_ID"),
        "python": sys.version,
        "executable": sys.executable,
        "packages": {
            name: metadata.version(name)
            for name in ("locust", "httpx", "jsonschema", "numpy", "pydantic")
        },
        "cases": cases,
        "passed": sum(case["passed"] for case in cases),
        "failed": sum(not case["passed"] for case in cases),
        "skipped": 0,
        "decision": "PASS" if all(case["passed"] for case in cases) else "FAIL",
        "production_credit": "ZERO-CREDIT",
    }
    with args.output.open("xb") as stream:
        stream.write(encoded(result) + b"\n")
    print(
        json.dumps(
            {"scope": result["scope"], "passed": result["passed"], "failed": result["failed"]}
        )
    )
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

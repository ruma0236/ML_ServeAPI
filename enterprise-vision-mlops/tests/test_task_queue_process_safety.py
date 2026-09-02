from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from evm.control_panel.task_queue_worker import (
    BoundedTaskQueueWorker,
    process_rss_bytes,
    process_tree_rss_bytes,
    replace_file_with_retry,
)


def test_process_tree_rss_includes_executor_child():
    parent = psutil.Process(os.getpid())
    baseline = parent.memory_info().rss
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; payload=bytearray(16*1024*1024); print(len(payload), flush=True); time.sleep(10)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == str(16 * 1024 * 1024)
        observed = process_tree_rss_bytes()
        assert observed > baseline + 8 * 1024 * 1024
    finally:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)


def test_process_rss_reports_parent_only_baseline():
    parent = process_rss_bytes()
    tree = process_tree_rss_bytes()

    assert parent > 0
    assert tree >= parent


def test_worker_retains_short_lived_executor_process_tree_rss_peak():
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; payload=bytearray(8*1024*1024); time.sleep(0.2)",
        ]
    )
    worker = object.__new__(BoundedTaskQueueWorker)
    worker._executor_processes = {child.pid: child}
    worker._executor_process_tree_rss_peak_bytes = 0
    try:
        deadline = time.monotonic() + 2
        while worker._executor_process_tree_rss_peak_bytes == 0:
            worker._observe_executor_process_tree_rss()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        retained = worker._executor_process_tree_rss_peak_bytes
        child.wait(timeout=2)
        assert retained > 0
        assert worker._observe_executor_process_tree_rss() == 0
        assert worker._executor_process_tree_rss_peak_bytes == retained
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)


def test_heartbeat_replace_retries_transient_windows_reader_lock(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "heartbeat.tmp"
    target = tmp_path / "heartbeat.json"
    source.write_text("new", encoding="ascii")
    target.write_text("old", encoding="ascii")
    original_replace = Path.replace
    calls = 0

    def transient_replace(path: Path, destination: Path):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError("transient reader lock")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", transient_replace)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    replace_file_with_retry(source, target)

    assert calls == 3
    assert target.read_text(encoding="ascii") == "new"


def test_executor_exits_when_exact_parent_process_is_killed(tmp_path: Path):
    ready_path = tmp_path / "executor-ready.json"
    source_root = Path(__file__).resolve().parents[1] / "src"
    child_code = (
        "import json,os,sys,time,psutil; "
        "from pathlib import Path; "
        f"sys.path.insert(0, {str(source_root)!r}); "
        "from evm.control_panel.task_queue_executor import bind_parent_lifetime; "
        "parent=os.getppid(); "
        "bind_parent_lifetime(parent, psutil.Process(parent).create_time()); "
        "process=psutil.Process(os.getpid()); "
        "payload={'status':'ready','pid':os.getpid(),'ppid':parent,"
        "'create_time':process.create_time()}; "
        f"path=Path({str(ready_path)!r}); temporary=path.with_suffix('.tmp'); "
        "temporary.write_text(json.dumps(payload, sort_keys=True), encoding='ascii'); "
        "temporary.replace(path); "
        "time.sleep(60)"
    )
    parent_code = (
        "import json,subprocess,sys,time; "
        "from pathlib import Path; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True); "
        f"path=Path({str(ready_path)!r}); "
        "deadline=time.monotonic()+20; payload=None; "
        "\nwhile time.monotonic()<deadline:\n"
        " if child.poll() is not None: break\n"
        " if path.exists():\n"
        "  try:\n"
        "   payload=json.loads(path.read_text(encoding='ascii'))\n"
        "  except (OSError,json.JSONDecodeError):\n"
        "   time.sleep(0.05); continue\n"
        "  break\n"
        " time.sleep(0.05)\n"
        "returncode=child.poll(); "
        "stderr=(child.stderr.read() if returncode is not None and child.stderr is not None else ''); "
        "\nif payload is None:\n"
        " payload={'status':('child_exited' if returncode is not None else 'readiness_timeout'),"
        "'pid':child.pid,'returncode':returncode,'stderr':stderr}\n"
        "elif payload.get('status') != 'ready' or payload.get('pid') != child.pid:\n"
        " payload={'status':'invalid_readiness','pid':child.pid,'returncode':returncode,"
        "'stderr':stderr,'received':payload}\n"
        "else:\n"
        " payload={**payload,'returncode':returncode,'stderr':stderr}\n"
        "print(json.dumps(payload, sort_keys=True), flush=True)\n"
        "if payload.get('status') != 'ready':\n"
        " raise SystemExit(91)\n"
        "time.sleep(60)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_process = None
    handshake = None
    try:
        assert parent.stdout is not None
        line = parent.stdout.readline().strip()
        if not line:
            parent.wait(timeout=3)
            parent_stderr = parent.stderr.read() if parent.stderr is not None else ""
            raise AssertionError(
                f"parent produced no handshake; returncode={parent.returncode}; "
                f"stderr={parent_stderr!r}"
            )
        try:
            handshake = json.loads(line)
        except json.JSONDecodeError as exc:
            parent_returncode = parent.poll()
            parent_stderr = (
                parent.stderr.read()
                if parent_returncode is not None and parent.stderr is not None
                else "<parent-still-running>"
            )
            raise AssertionError(
                f"invalid parent handshake={line!r}; returncode={parent_returncode}; "
                f"stderr={parent_stderr!r}"
            ) from exc
        if handshake.get("status") != "ready":
            parent.wait(timeout=3)
            parent_stderr = parent.stderr.read() if parent.stderr is not None else ""
            raise AssertionError(
                f"handshake={handshake!r}; returncode={parent.returncode}; "
                f"parent_stderr={parent_stderr!r}"
            )
        child_pid = int(handshake["pid"])
        assert int(handshake["ppid"]) == parent.pid, f"handshake={handshake!r}"
        child_process = psutil.Process(child_pid)
        assert abs(child_process.create_time() - float(handshake["create_time"])) < 0.01
        assert child_process.is_running(), f"handshake={handshake!r}"
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 15
        while child_process.is_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not child_process.is_running(), (
            f"exact child did not exit after exact parent death; handshake={handshake!r}; "
            f"parent_returncode={parent.returncode}; child_status={child_process.status()!r}"
        )
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_process is not None and child_process.is_running():
            try:
                child_process.kill()
                child_process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass

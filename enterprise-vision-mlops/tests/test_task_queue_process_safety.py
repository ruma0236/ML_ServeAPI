from __future__ import annotations

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
    ready_path = tmp_path / "executor-ready.txt"
    child_code = (
        "import os,time,psutil; "
        "from pathlib import Path; "
        "from evm.control_panel.task_queue_executor import bind_parent_lifetime; "
        "parent=os.getppid(); "
        "bind_parent_lifetime(parent, psutil.Process(parent).create_time()); "
        f"Path({str(ready_path)!r}).write_text(str(os.getpid()), encoding='ascii'); "
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"path={str(ready_path)!r}; "
        "deadline=time.time()+5; "
        "\nwhile time.time()<deadline:\n"
        " import os\n"
        " if os.path.exists(path): break\n"
        " time.sleep(0.05)\n"
        "print(child.pid, flush=True); time.sleep(30)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = None
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline().strip())
        assert ready_path.exists()
        assert psutil.pid_exists(child_pid)
        parent.kill()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 5
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                psutil.Process(child_pid).kill()
            except psutil.NoSuchProcess:
                pass

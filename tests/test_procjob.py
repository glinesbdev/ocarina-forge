"""Process lifetime: job limits, worker shutdown, and the leftover scan."""

import logging
import multiprocessing
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from ocarina_forge.procjob import (
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    POOL_TASKS_PER_CHILD,
    ProcessFact,
    assign_pid,
    child_pids,
    close_handle,
    collect_process_facts,
    create_job,
    ensure_builder_job,
    ensure_server_job,
    is_forge_worker,
    join_server_job,
    kill_on_close_limits,
    kill_tree,
    leftover_workers,
    log_leftover_workers,
    parent_death_signal,
    pid_alive,
    pool_executor_kwargs,
    prepare_pool_worker,
    spawn_parent_pid,
    terminate_executor,
)


def _wait_dead(pid: int, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} is still running")


def _write_proc(root: Path, pid: int, ppid: int, cmdline: str, environ: str = "", image: str = "") -> None:
    folder = root / str(pid)
    folder.mkdir()
    (folder / "cmdline").write_bytes(cmdline.encode() + b"\0")
    (folder / "environ").write_bytes(environ.replace("\n", "\0").encode() + b"\0")
    (folder / "stat").write_text(f"{pid} (python worker) S {ppid} 0 0")
    if image:
        os.symlink(image, folder / "exe")


def test_the_job_limit_is_kill_on_close():
    info = kill_on_close_limits()
    assert isinstance(info, JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
    assert info.BasicLimitInformation.LimitFlags == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if os.name != "nt":
        assert ensure_server_job() is False
        assert ensure_builder_job() is False
        assert create_job("Local\\OcarinaForgeTest") is None
        assert join_server_job() is False
    # 64-bit: 64 byte basic limit + 48 byte IO counters + 4 size_t fields.
    if __import__("ctypes").sizeof(__import__("ctypes").c_void_p) == 8:
        assert __import__("ctypes").sizeof(info) == 144


def test_pool_kwargs_recycle_workers_on_python_3_11():
    kwargs = pool_executor_kwargs(3, initializer=prepare_pool_worker)
    assert kwargs["max_workers"] == 3
    assert kwargs["initializer"] is prepare_pool_worker
    assert kwargs["mp_context"].get_start_method() == "spawn"
    if sys.version_info >= (3, 11):
        assert kwargs["max_tasks_per_child"] == POOL_TASKS_PER_CHILD
    else:
        assert "max_tasks_per_child" not in kwargs


def test_spawn_parent_pid_reads_the_multiprocessing_command():
    command = 'python -c "from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=4321, pipe_handle=7)"'
    assert spawn_parent_pid(command) == 4321
    assert spawn_parent_pid("spawn_main( parent_pid = 9 )") == 9
    assert spawn_parent_pid("python manage.py runserver") is None
    assert spawn_parent_pid("") is None


def test_a_stat_line_with_spaces_in_the_name_yields_the_parent():
    from ocarina_forge.procjob import _ppid_from_stat

    assert _ppid_from_stat("12 (python (worker)) S 34 0 0") == 34
    assert _ppid_from_stat("nope") is None


def test_leftover_scan_reports_forge_workers_and_ignores_other_python(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    spawn = "python -c from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=50, pipe_handle=3)"
    _write_proc(root, 10, 1, spawn, environ="OCARINA_FORGE_POOL_WORKER=1\nPATH=/usr/bin")
    _write_proc(root, 11, 1, spawn, image=str(tmp_path / "ocarina-forge" / ".venv" / "bin" / "python"))
    _write_proc(root, 12, 1, spawn)
    _write_proc(root, 13, 12, "node /src/frontend/tools/meshopt-worker.mjs")
    _write_proc(root, 14, 1, "python -c from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=50, pipe_handle=4)")
    live_parent = spawn.replace("parent_pid=50", "parent_pid=77")
    _write_proc(root, 15, 77, live_parent, environ="OCARINA_FORGE_POOL_WORKER=1")
    _write_proc(root, 77, 1, "python -m ocarina_forge")
    _write_proc(root, 16, 1, "python manage.py runserver", environ="OCARINA_FORGE_POOL_WORKER=1")

    facts, alive = collect_process_facts(root)
    rows = leftover_workers(facts, alive)
    found = sorted(fact.pid for fact in rows)
    # 10 marker, 11 checkout path, 12 has a meshopt child. 14 is the same shape
    # with no Forge marker. 15's parent is still alive. 16 is not a spawn worker.
    assert found == [10, 11, 12]
    assert all(is_forge_worker(fact) for fact in rows)
    plain = next(fact for fact in facts if fact.pid == 14)
    assert is_forge_worker(plain) is False


def test_the_startup_scan_logs_and_does_not_kill(monkeypatch, caplog):
    fact = ProcessFact(
        pid=10,
        ppid=1,
        cmdline="spawn_main(parent_pid=50, pipe_handle=1)",
        environ="OCARINA_FORGE_POOL_WORKER=1",
        image="",
        children=("node frontend/tools/meshopt-worker.mjs",),
    )
    monkeypatch.setattr("ocarina_forge.procjob.collect_process_facts", lambda proc_root=None: ([fact], set()))
    killed = []
    monkeypatch.setattr("ocarina_forge.procjob.kill_tree", lambda pid, seen=None: killed.append(pid))
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        rows = log_leftover_workers()
    assert [item.pid for item in rows] == [10]
    assert killed == []
    assert "was not stopped" in caplog.text
    assert "meshopt helper" in caplog.text


def test_terminate_executor_kills_recorded_workers(monkeypatch):
    killed = []
    monkeypatch.setattr("ocarina_forge.procjob.kill_tree", lambda pid, seen=None: killed.append(pid))

    class Pool:
        def __init__(self):
            self._processes = {1: type("Proc", (), {"pid": 111})(), 2: type("Proc", (), {"pid": 222})()}
            self.shut = False

        def shutdown(self, wait=True, cancel_futures=False):
            assert wait is False
            assert cancel_futures is True
            self.shut = True
            self._processes = None

    pool = Pool()
    terminate_executor(pool)
    assert pool.shut is True
    assert killed == [111, 222]
    terminate_executor(None)


def test_kill_tree_reaps_a_child():
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "time.sleep(120)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.time() + 10
        kids = []
        while time.time() < deadline:
            kids = child_pids(proc.pid)
            if kids:
                break
            time.sleep(0.05)
        assert kids, "child process did not appear"
        kill_tree(proc.pid)
        _wait_dead(proc.pid)
        for kid in kids:
            _wait_dead(kid)
    finally:
        if proc.poll() is None:
            proc.kill()


def _block_in_worker():
    import time
    time.sleep(60)


def test_terminate_executor_kills_a_live_worker():
    # Spawn starts the worker on the first submit, not at construction.
    executor = ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
    )
    future = executor.submit(_block_in_worker)
    try:
        deadline = time.time() + 30
        pids = []
        while time.time() < deadline:
            current = getattr(executor, "_processes", None) or {}
            pids = [proc.pid for proc in current.values() if getattr(proc, "pid", None)]
            if pids and all(pid_alive(pid) for pid in pids):
                break
            if future.done():
                break
            time.sleep(0.05)
        assert pids, future.exception() if future.done() else "worker did not start"
        terminate_executor(executor)
        executor = None
        for pid in pids:
            _wait_dead(pid)
    finally:
        if executor is not None:
            terminate_executor(executor)


def test_prepare_pool_worker_stays_up_while_the_parent_lives():
    code = (
        "import os, time\n"
        "from ocarina_forge.procjob import POOL_WORKER_ENV, prepare_pool_worker\n"
        "prepare_pool_worker()\n"
        "print(os.environ[POOL_WORKER_ENV])\n"
        "time.sleep(0.3)\n"
        "print('alive')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["1", "alive"]


def test_linux_arms_sigterm_on_parent_death():
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux prctl")
    code = (
        "import signal\n"
        "from ocarina_forge.procjob import arm_parent_death_signal, parent_death_signal\n"
        "assert arm_parent_death_signal()\n"
        "assert parent_death_signal() == signal.SIGTERM\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    # This process did not arm itself. The child above did.
    assert parent_death_signal() in (None, 0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_closing_the_job_kills_the_child():
    name = f"Local\\OcarinaForgeTest-{os.getpid()}-{time.time_ns()}"
    handle = create_job(name, assign_self=False)
    assert handle
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert assign_pid(handle, proc.pid)
        close_handle(handle)
        handle = 0
        proc.wait(timeout=10)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
        if handle:
            close_handle(handle)

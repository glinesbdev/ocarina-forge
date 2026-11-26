"""Converter cores: the env var wins, and the pool changes between jobs."""

import os
import sys

from ocarina_forge.config import load_config, save_config
from ocarina_forge.procjob import POOL_TASKS_PER_CHILD
from ocarina_forge.server import (
    _POOL_LOCK,
    _close_pool,
    _configured_workers,
    _job_finished,
    _pool,
    _worker_init,
)


def _clear_env(monkeypatch):
    monkeypatch.delenv("OCARINA_FORGE_WORKERS", raising=False)
    monkeypatch.delenv("BANCWRIGHT_WORKERS", raising=False)


def test_the_default_leaves_one_core_free(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    count, cpus, source = _configured_workers()
    assert source == "default"
    assert count == max(1, (os.cpu_count() or 1) - 1)
    assert 1 <= count <= cpus


def test_a_saved_count_is_used_until_the_env_overrides_it(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    save_config(workers=1)
    assert load_config()["workers"] == 1
    count, _cpus, source = _configured_workers()
    assert (count, source) == (1, "config")
    monkeypatch.setenv("OCARINA_FORGE_WORKERS", "2")
    count, cpus, source = _configured_workers()
    assert source == "env"
    assert count == min(2, cpus)


def test_the_pool_waits_until_the_current_job_finishes(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    cpus = os.cpu_count() or 1
    if cpus < 2:
        monkeypatch.setenv("OCARINA_FORGE_WORKERS", "1")
        assert _pool() is None
        _close_pool()
        return
    monkeypatch.setenv("OCARINA_FORGE_WORKERS", "2")
    _close_pool()
    try:
        assert _pool() is not None
        from ocarina_forge import server

        with _POOL_LOCK:
            server._POOL_INFLIGHT = 1
            first = server._POOL
        monkeypatch.setenv("OCARINA_FORGE_WORKERS", "1")
        assert _pool() is first
        assert server._POOL_WANT == 1
        _job_finished()
        assert _pool() is None
    finally:
        _close_pool()


def test_replacing_the_pool_terminates_the_previous_workers(monkeypatch):
    from ocarina_forge import server

    _close_pool()
    captured = {}
    ended = []

    class Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(server, "ProcessPoolExecutor", Fake)
    monkeypatch.setattr(server, "ensure_server_job", lambda: False)
    monkeypatch.setattr(server, "terminate_executor", lambda pool: ended.append(pool))
    try:
        with server._POOL_LOCK:
            server._POOL = None
            server._POOL_SIZE = 0
            server._POOL_INFLIGHT = 0
            server._install_pool(2)
        assert captured["max_workers"] == 2
        assert captured["initializer"] is _worker_init
        assert captured["mp_context"].get_start_method() == "spawn"
        if sys.version_info >= (3, 11):
            assert captured["max_tasks_per_child"] == POOL_TASKS_PER_CHILD
        old = server._POOL
        with server._POOL_LOCK:
            server._install_pool(1)
        assert ended == [old]
        assert server._POOL is None
    finally:
        with server._POOL_LOCK:
            server._POOL = None
            server._POOL_SIZE = 0
            server._POOL_WANT = None
            server._POOL_INFLIGHT = 0

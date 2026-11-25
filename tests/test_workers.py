"""Converter cores: the env var wins, and the pool changes between jobs."""

import os

from ocarina_forge.config import load_config, save_config
from ocarina_forge.server import (
    _POOL_LOCK,
    _close_pool,
    _configured_workers,
    _job_finished,
    _pool,
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

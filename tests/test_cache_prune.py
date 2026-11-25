"""Old cache versions are removed, and a size cap evicts the least recently used."""

import json
import logging
import os
import threading
import time

import pytest

from ocarina_forge import cache_prune
from ocarina_forge.cache_prune import (
    DEFAULT_CACHE_LIMIT,
    PruneStats,
    bntx_version,
    cache_limit_setting,
    cache_public,
    dump_model_meta,
    load_model_meta,
    maintain,
    model_version,
    note_access,
    note_written,
    start_maintenance,
    stop_maintenance,
)
from ocarina_forge.config import load_config, parse_cache_limit, save_config
from ocarina_forge.scene_preview import PREVIEW_VERSION, TERRAIN_FIELD_VERSION, TERRAIN_VERSION
from ocarina_forge.shared_models import TEXTURE_STORE, VERSION
from ocarina_forge.surface_pack import PACK_VERSION


@pytest.fixture(autouse=True)
def _stop_background_prune():
    stop_maintenance()
    cache_prune._prune_requested.clear()
    yield
    stop_maintenance()


def _old(path, *, atime_offset=0, mtime_offset=10_000):
    now = time.time()
    os.utime(path, (now - mtime_offset + atime_offset, now - mtime_offset))


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _old(path)
    return path


def test_stale_version_folders_and_sidecars_are_removed(tmp_path):
    root = tmp_path / "ocarina-forge"
    models = root / "models"
    _write(models / "scene-terrain-v3" / "old.glb", b"terrain-old")
    _write(models / TERRAIN_VERSION / "kept.glb", b"terrain")
    _write(models / TERRAIN_FIELD_VERSION / "field.glb", b"field")
    _write(models / "scene-preview-v1" / "old.glb", b"preview-old")
    _write(models / PREVIEW_VERSION / "kept.glb", b"preview")
    _write(models / "shared-v2" / "old.glb", b"shared-old")
    _write(models / VERSION / "models" / "kept.glb", b"shared")
    _write(models / TEXTURE_STORE / "textures" / "aa" / "kept.ktx2", b"tex")

    current = model_version()
    stale = "ab" * 32
    fresh = "cd" * 32
    plain = "ef" * 32
    _model(models, stale, "models-1:stale", b"stale-glb")
    _model(models, fresh, current, b"fresh-glb")
    _model(models, plain, None, b"plain-glb")

    pointer = models / "src" / "11" / ("1" * 64 + ".json")
    _write(pointer, json.dumps({"kind": "mesh", "cacheVersion": "models-1:stale"}).encode())
    live_pointer = models / "src" / "22" / ("2" * 64 + ".json")
    _write(live_pointer, json.dumps({"kind": "mesh", "cacheVersion": current}).encode())

    bwtx = models / "bntx" / "aa" / ("a" * 64 + ".bwtx")
    _write(bwtx, b"old-bwtx")
    _write(bwtx.with_suffix(".json"), json.dumps({"version": "bntx-1"}).encode())
    current_bwtx = models / "bntx" / "bb" / ("b" * 64 + ".bwtx")
    _write(current_bwtx, b"bwtx")
    _write(current_bwtx.with_suffix(".json"), json.dumps({"version": bntx_version()}).encode())
    bare_bwtx = models / "bntx" / "cc" / ("c" * 64 + ".bwtx")
    _write(bare_bwtx, b"bare")

    digest = "d" * 64
    surfaces = root / "surfaces"
    _write(surfaces / f"{digest}.ofgs.gz", b"surface")
    _write(surfaces / f"{digest}.json", json.dumps({"version": PACK_VERSION - 1}).encode())
    kept_digest = "e" * 64
    _write(surfaces / f"{kept_digest}.ofgs.gz", b"live")
    _write(surfaces / f"{kept_digest}.json", json.dumps({"version": PACK_VERSION}).encode())
    loose = "f" * 64
    _write(surfaces / f"{loose}.ofgs.gz", b"loose")
    _write(surfaces / f"{loose}.json", b"{}")

    stats = maintain(root, limit=0, now=time.time())

    assert not (models / "scene-terrain-v3").exists()
    assert not (models / "scene-preview-v1").exists()
    assert not (models / "shared-v2").exists()
    assert (models / TERRAIN_VERSION / "kept.glb").is_file()
    assert (models / TERRAIN_FIELD_VERSION / "field.glb").is_file()
    assert (models / PREVIEW_VERSION / "kept.glb").is_file()
    assert (models / VERSION / "models" / "kept.glb").is_file()
    assert (models / TEXTURE_STORE / "textures" / "aa" / "kept.ktx2").is_file()
    assert not (models / stale[:2] / f"{stale}.glb").exists()
    assert (models / fresh[:2] / f"{fresh}.glb").is_file()
    assert (models / plain[:2] / f"{plain}.glb").is_file()
    assert not pointer.exists()
    assert live_pointer.is_file()
    assert not bwtx.exists()
    assert current_bwtx.is_file()
    assert bare_bwtx.is_file()
    assert not (surfaces / f"{digest}.ofgs.gz").exists()
    assert (surfaces / f"{kept_digest}.ofgs.gz").is_file()
    assert (surfaces / f"{loose}.ofgs.gz").is_file()
    assert stats.version_bytes > 0
    assert stats.lru_bytes == 0


def test_flat_override_cache_prunes_version_folders(tmp_path):
    root = tmp_path / "models"
    _write(root / "scene-preview-v1" / "old.glb", b"old")
    _write(root / PREVIEW_VERSION / "kept.glb", b"kept")
    maintain(root, limit=0, now=time.time())
    assert not (root / "scene-preview-v1").exists()
    assert (root / PREVIEW_VERSION / "kept.glb").is_file()


def _model(models, digest, version, body):
    folder = models / digest[:2]
    _write(folder / f"{digest}.glb", body)
    meta = {"triangles": 1}
    if version is not None:
        meta["cacheVersion"] = version
    _write(folder / f"{digest}.json", json.dumps(meta).encode())


def test_lru_evicts_the_oldest_down_to_ninety_percent(tmp_path):
    root = tmp_path / "cache"
    now = time.time()
    mtime = now - 10_000
    for index in range(4):
        path = root / f"file-{index}.bin"
        _write(path, b"x" * 1000)
        os.utime(path, (mtime + index, mtime))
    digest = "a" * 64
    pair = root / "aa"
    glb = pair / f"{digest}.glb"
    meta = pair / f"{digest}.json"
    _write(glb, b"y" * 500)
    _write(meta, b"z" * 500)
    os.utime(glb, (mtime - 50, mtime - 50))
    os.utime(meta, (mtime - 40, mtime - 40))
    scratch = root / "profile-tmp" / "job" / "big.bin"
    _write(scratch, b"p" * 50_000)
    log = root / "meshopt.log"
    _write(log, b"log-line")
    os.utime(log, (mtime - 100, mtime - 100))

    limit = 2500
    stats = maintain(root, limit=limit, now=now)

    assert not glb.exists()
    assert not meta.exists()
    assert not (root / "file-0.bin").exists()
    assert not (root / "file-1.bin").exists()
    assert (root / "file-2.bin").is_file()
    assert (root / "file-3.bin").is_file()
    assert scratch.is_file()
    assert log.is_file()
    assert stats.remaining_bytes <= int(limit * 0.90)
    assert stats.lru_bytes > 0


def test_a_recent_member_keeps_its_cache_group(tmp_path):
    root = tmp_path / "cache"
    now = time.time()
    digest = "a" * 64
    glb = root / "aa" / f"{digest}.glb"
    meta = root / "aa" / f"{digest}.json"
    _write(glb, b"y" * 100)
    _write(meta, b"z" * 100)
    os.utime(glb, (now, now))
    os.utime(meta, (now - 10_000, now - 10_000))
    old = _write(root / "old.bin", b"x" * 500)

    maintain(root, limit=150, now=now)

    assert glb.is_file()
    assert meta.is_file()
    assert not old.exists()


def test_unlimited_cap_does_not_evict(tmp_path):
    root = tmp_path / "cache"
    path = _write(root / "models" / "aa" / "kept.bin", b"x" * 5000)
    stats = maintain(root, limit=0, now=time.time())
    assert path.is_file()
    assert stats.lru_bytes == 0
    assert stats.remaining_bytes == 5000


def test_recent_writes_and_temp_files_are_kept(tmp_path):
    root = tmp_path / "cache"
    stale = root / "scene-terrain-v1"
    old = _write(stale / "old.glb", b"old")
    fresh = stale / "fresh.glb"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"fresh")
    temp = stale / "writing.tmp"
    temp.write_bytes(b"temp")
    named = stale / "tmpabcdef"
    named.write_bytes(b"named")
    now = time.time()
    stats = maintain(root, limit=1, now=now)
    assert not old.exists()
    assert fresh.is_file()
    assert temp.is_file()
    assert named.is_file()
    assert stats.version_bytes == len(b"old")


def test_file_in_use_is_skipped_and_logged(tmp_path, monkeypatch, caplog):
    root = tmp_path / "cache"
    folder = root / "scene-preview-v1"
    stuck = _write(folder / "stuck.glb", b"stuck")
    gone = _write(folder / "gone.glb", b"gone")
    real = type(stuck).unlink

    def flaky(self, *args, **kwargs):
        if self.name == "stuck.glb":
            raise PermissionError(32, "The process cannot access the file because it is being used")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(stuck), "unlink", flaky)
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        stats = maintain(root, limit=0, now=time.time())
    assert stuck.is_file()
    assert not gone.exists()
    assert stats.skipped == 1
    assert "stuck.glb" in caplog.text
    assert "cache prune removed" in caplog.text


def test_new_writes_record_a_version_the_editor_does_not_see(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path))
    from ocarina_forge.models import _write_pointer

    pointer = tmp_path / "src" / "ab" / ("c" * 64 + ".json")
    _write_pointer(pointer, {"kind": "box", "bounds": None})
    stored = json.loads(pointer.read_text(encoding="utf-8"))
    assert stored["cacheVersion"] == model_version()
    assert stored["kind"] == "box"
    text = dump_model_meta({"triangles": 3})
    assert load_model_meta(text) == {"triangles": 3}
    assert json.loads(text)["cacheVersion"] == model_version()


def test_access_tracking_does_not_change_mtime(tmp_path):
    path = tmp_path / "preview.glb"
    path.write_bytes(b"glb")
    stamp = 1_700_000_000_000_000_000
    os.utime(path, ns=(1, stamp))
    note_access(path)
    after = path.stat()
    assert after.st_mtime_ns == stamp
    assert after.st_atime_ns != 1


def test_default_cap_is_20_gib_and_config_and_env_override_it(tmp_path, monkeypatch):
    monkeypatch.delenv("OCARINA_FORGE_CACHE_LIMIT", raising=False)
    monkeypatch.delenv("BANCWRIGHT_CACHE_LIMIT", raising=False)
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    assert parse_cache_limit("20G") == 20 * 1024**3
    assert parse_cache_limit("20GB") == 20 * 1024**3
    assert parse_cache_limit("20GiB") == 20 * 1024**3
    assert parse_cache_limit(0) == 0
    assert parse_cache_limit("nope") is None
    assert cache_limit_setting() == {"bytes": DEFAULT_CACHE_LIMIT, "source": "default"}
    assert DEFAULT_CACHE_LIMIT == 20 * 1024**3

    save_config(cache_limit="5M")
    assert load_config()["cacheLimit"] == 5 * 1024**2
    assert cache_limit_setting() == {"bytes": 5 * 1024**2, "source": "config"}
    save_config(cache_limit=0)
    assert load_config()["cacheLimit"] == 0
    assert "cacheLimit: 0" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert cache_limit_setting()["source"] == "config"

    monkeypatch.setenv("OCARINA_FORGE_CACHE_LIMIT", "nope")
    assert cache_limit_setting()["source"] == "config"
    monkeypatch.setenv("BANCWRIGHT_CACHE_LIMIT", "1G")
    monkeypatch.delenv("OCARINA_FORGE_CACHE_LIMIT")
    assert cache_limit_setting() == {"bytes": 1024**3, "source": "env"}
    monkeypatch.setenv("OCARINA_FORGE_CACHE_LIMIT", "0")
    assert cache_limit_setting() == {"bytes": 0, "source": "env"}


def test_status_reports_the_measured_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "a.bin").write_bytes(b"xyz")
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(cache))
    monkeypatch.setenv("OCARINA_FORGE_CACHE_LIMIT", "0")
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    stats = maintain()
    public = cache_public()
    assert public["bytes"] == 3
    assert public["bytes"] == stats.remaining_bytes
    assert public["unlimited"] is True
    assert public["limitBytes"] == 0
    assert public["source"] == "env"
    assert public["path"] == str(cache)

    from fastapi.testclient import TestClient

    from ocarina_forge.server import app

    with TestClient(app) as client:
        body = client.get("/api/status").json()
    assert body["cache"]["bytes"] == 3
    assert body["cache"]["path"] == str(cache)


def test_config_endpoint_saves_the_cap(tmp_path, monkeypatch):
    monkeypatch.delenv("OCARINA_FORGE_CACHE_LIMIT", raising=False)
    monkeypatch.delenv("BANCWRIGHT_CACHE_LIMIT", raising=False)
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    from fastapi.testclient import TestClient

    from ocarina_forge.server import app

    with TestClient(app) as client:
        saved = client.put("/api/config", json={"cacheLimit": "20G"}).json()
        rejected = client.put("/api/config", json={"cacheLimit": "lots"})
    assert saved["config"]["cacheLimit"] == 20 * 1024**3
    assert saved["cache"]["limitBytes"] == 20 * 1024**3
    assert saved["cache"]["source"] == "config"
    assert rejected.status_code == 400


def test_prune_on_startup_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    started = threading.Event()
    release = threading.Event()

    def hang(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return PruneStats()

    monkeypatch.setattr(cache_prune, "maintain", hang)
    try:
        began = time.monotonic()
        start_maintenance(interval=3600)
        assert time.monotonic() - began < 1
        assert started.wait(2)
    finally:
        release.set()
        stop_maintenance()


def test_a_write_over_the_cap_requests_another_prune(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(cache))
    monkeypatch.setenv("OCARINA_FORGE_CACHE_LIMIT", "100")
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    path = _write(cache / "keep.bin", b"x" * 80)
    maintain()
    assert cache_public()["bytes"] == 80
    extra = cache / "extra.bin"
    extra.write_bytes(b"y" * 40)
    _old(extra)
    note_written(extra)
    assert cache_prune._prune_requested.is_set()
    assert path.is_file()

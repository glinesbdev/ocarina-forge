"""Opt-in copy of a built mod into a Longshot folder."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from ocarina_forge.actors import format_hash
from ocarina_forge.dump import Dump
from ocarina_forge.errors import BancError
from ocarina_forge.fixtures import FADO, KOKIRI_STATIC, build_fixture
from ocarina_forge.formats.byml import loads
from ocarina_forge.formats.zsio import decompress
from ocarina_forge.hotreload import MANIFEST, Publisher, destination_for, publish
from ocarina_forge.project import Project
from ocarina_forge.scene import apply_command


def _dump(tmp_path):
    return Dump(build_fixture(tmp_path / "dump"))


def _project(tmp_path):
    project = Project.create(tmp_path / "mod", name="kokiri-notes", title="Kokiri notes", author="Ada", version="0.1.0")
    dump = Dump(build_fixture(tmp_path / "dump"))
    document, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    diff = project.load_scene_diff(KOKIRI_STATIC)
    diff, _focus = apply_command(
        document, diff, {"command": "transform", "id": format_hash(FADO), "translate": [9, 0, 3]}
    )
    project.save_scene_diff(diff)
    return project, dump


def test_publish_replaces_owned_files_and_leaves_the_rest(tmp_path):
    project, dump = _project(tmp_path)
    folder = tmp_path / "mods" / "OcarinaForge"
    keep = folder / "romfs" / "Keep" / "note.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("stay", encoding="utf-8")
    stale = folder / "romfs" / "Banc" / "Gone.bcett.byml.zs"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    (folder / MANIFEST).write_text(json.dumps({"files": ["Banc/Gone.bcett.byml.zs"]}), encoding="utf-8")

    result = publish(project, dump, str(folder))
    assert result["removed"] == 1
    assert not stale.exists()
    assert keep.read_text(encoding="utf-8") == "stay"
    written = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))["files"]
    assert KOKIRI_STATIC in written
    for name in written:
        path = folder / "romfs" / name
        assert path.is_file()
        assert not path.with_name(path.name + ".tmp").exists()
    packed = (folder / "romfs" / KOKIRI_STATIC).read_bytes()
    payload, _dict_id = decompress(packed, dump.dicts())
    document = loads(payload)
    fado = next(actor for actor in document["Actors"] if int(actor["Hash"]) == FADO)
    assert list(fado["Translate"]) == [9.0, 0.0, 3.0]

    project.save_scene_diff({"format": 1, "banc": KOKIRI_STATIC, "actors": {}})
    cleared = publish(project, dump, str(folder))
    assert cleared["files"] == []
    assert not (folder / "romfs" / KOKIRI_STATIC).exists()
    assert keep.read_text(encoding="utf-8") == "stay"


def test_refuses_a_disabled_mod_name_and_the_dump(tmp_path):
    dump_root = tmp_path / "dump"
    with pytest.raises(BancError, match="Longshot"):
        destination_for(str(tmp_path / "mods" / "_hidden"))
    with pytest.raises(BancError, match="dump"):
        destination_for(str(dump_root / "romfs" / "Banc"), dump_root=dump_root)


def test_a_burst_of_saves_publishes_once():
    calls = []
    publisher = Publisher(delay=0.05)

    def run():
        calls.append(1)

    for _ in range(5):
        publisher.schedule(run)
    time.sleep(0.25)
    assert calls == [1]
    assert publisher.state["status"] == "pending"


def test_hot_reload_setting_round_trips_and_is_kept_when_the_dump_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    from ocarina_forge.server import app

    client = TestClient(app)
    dump = Dump(build_fixture(tmp_path / "dump"))
    project = Project.create(tmp_path / "mod", name="kokiri-notes", title="Kokiri notes", author="Ada", version="0.1.0")
    client.put("/api/config", json={"dump": str(dump.root), "project": str(project.root)})
    folder = tmp_path / "mods" / "OcarinaForge"
    saved = client.put("/api/hot-reload", json={"enabled": True, "path": str(folder)})
    assert saved.status_code == 200
    body = saved.json()
    assert body["enabled"] is True
    assert body["status"] == "idle"
    assert "enter it again" in body["message"]
    kept = client.put("/api/config", json={"dump": str(dump.root)})
    assert kept.json()["config"]["hotReload"]["enabled"] is True
    assert kept.json()["config"]["hotReload"]["path"] == str(folder)
    rejected = client.put("/api/hot-reload", json={"enabled": True, "path": str(tmp_path / "mods" / "-skip")})
    assert rejected.status_code >= 400
    assert "Longshot" in rejected.json()["message"]
    assert client.get("/api/hot-reload").json()["enabled"] is True

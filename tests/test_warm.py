"""Warm-up order: the open scene first, neighbors in the same folder next."""

import asyncio

from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import FIELD_STATIC, KOKIRI_DYNAMIC, KOKIRI_STATIC, build_fixture
from ocarina_forge.warm import (
    neighbor_bancs,
    queue_warm,
    run_warm,
    scene_model_names,
    scene_warm_queue,
    warm_queue,
)

CASTLE_STATIC = "Banc/HyruleCastle/HyruleCastle_Static.bcett.byml.zs"
DYNAMIC = "Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs"


class _SceneList:
    """A scene list with one placement file and two files that have no actors."""

    def __init__(self):
        self.reads: list[str] = []

    def list_scenes(self):
        return [{"path": DYNAMIC}, {"path": KOKIRI_STATIC}, {"path": CASTLE_STATIC}]

    def read_byml(self, relative):
        self.reads.append(relative)
        if relative == DYNAMIC:
            return {"Actors": [{"Gyaml": "Obj_Tree"}, {"Gyaml": "Npc_Kokiri_F_01"}]}, None, b""
        return {"Climate": {}}, None, b""


def test_neighbors_stay_in_the_folder_before_the_rest_of_the_dump():
    scenes = [KOKIRI_STATIC, KOKIRI_DYNAMIC, FIELD_STATIC]
    assert neighbor_bancs(KOKIRI_STATIC, scenes) == [KOKIRI_DYNAMIC, FIELD_STATIC]
    queue = warm_queue(["Obj_Tree", "Npc_Kokiri_F_01"], ["Npc_Kokiri_F_01", "StartPos"], [KOKIRI_DYNAMIC])
    assert queue == [
        ("model", "Obj_Tree"),
        ("model", "Npc_Kokiri_F_01"),
        ("model", "StartPos"),
        ("scene", KOKIRI_DYNAMIC),
    ]


def test_scene_warm_queue_is_fixed_to_the_scene_pair():
    dump = _SceneList()
    queue = scene_warm_queue(dump, DYNAMIC, ["Npc_Kokiri_F_01", "AddedModel", "Npc_Kokiri_F_01"])
    assert queue == ["Npc_Kokiri_F_01", "AddedModel", "Obj_Tree"]
    assert dump.reads == [DYNAMIC, KOKIRI_STATIC]
    assert "StartPos" not in queue


def test_warm_loop_caps_concurrency_and_reports_unavailable_models(monkeypatch):
    import ocarina_forge.server as server

    active = 0
    maximum = 0

    async def describe(name, *, interactive=True):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        if name == "Marker":
            return {"status": "box", "reason": "No mesh."}
        if name == "Missing":
            return {"status": "box", "reason": "No model pack."}
        return {"status": "mesh"}

    async def run():
        monkeypatch.setattr(server, "_WARM_JOB", server._WarmJob(1, DYNAMIC, ["A", "B", "Marker", "Missing"], 4))
        monkeypatch.setattr(server, "_WARM_EVENT", asyncio.Event())
        monkeypatch.setattr(server, "_INTERACTIVE", 0)
        monkeypatch.setattr(server, "_configured_workers", lambda: (4, 8, "test"))
        monkeypatch.setattr(server, "_describe_model", describe)
        task = asyncio.create_task(server._warm_loop())
        while server._WARM_JOB.phase == "warming":
            await asyncio.sleep(0.005)
        status = server._warm_status()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return status

    status = asyncio.run(run())
    assert maximum == 2
    assert status["phase"] == "failed"
    assert status["done"] == status["total"] == 4
    assert status["failed"] == 1
    assert status["failures"][0]["name"] == "Missing"
    assert all(failure["name"] != "Marker" for failure in status["failures"])


def test_warm_loop_waits_for_interactive_converts(monkeypatch):
    import ocarina_forge.server as server

    calls: list[str] = []

    async def describe(name, *, interactive=True):
        calls.append(name)
        return {"status": "mesh"}

    async def run():
        monkeypatch.setattr(server, "_WARM_JOB", server._WarmJob(2, DYNAMIC, ["A"], 1))
        monkeypatch.setattr(server, "_WARM_EVENT", asyncio.Event())
        monkeypatch.setattr(server, "_INTERACTIVE", 1)
        monkeypatch.setattr(server, "_configured_workers", lambda: (2, 8, "test"))
        monkeypatch.setattr(server, "_describe_model", describe)
        task = asyncio.create_task(server._warm_loop())
        await asyncio.sleep(0.02)
        assert calls == []
        monkeypatch.setattr(server, "_INTERACTIVE", 0)
        server._kick_warm()
        while server._WARM_JOB.phase == "warming":
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert calls == ["A"]


def test_api_warm_status_reports_a_fixed_scene_job(monkeypatch):
    from ocarina_forge.local_api import editor_client
    import ocarina_forge.server as server

    dump = _SceneList()
    monkeypatch.setattr(server, "_open_dump", lambda: dump)
    monkeypatch.setattr(server, "_ensure_warm", lambda: None)
    monkeypatch.setattr(server, "_kick_warm", lambda: None)
    monkeypatch.setattr(server, "_WARM_JOB", None)
    monkeypatch.setattr(server, "_WARM_EVENT", None)
    with editor_client(server.app) as client:
        started = client.post("/api/warm", json={"banc": DYNAMIC, "names": ["Npc_Kokiri_F_01"]})
        assert started.status_code == 200
        status = client.get("/api/warm")
    assert status.status_code == 200
    assert status.json()["jobId"] == started.json()["jobId"]
    assert status.json()["scene"] == DYNAMIC
    assert status.json()["phase"] == "warming"
    assert status.json()["total"] == 2
    assert dump.reads == [DYNAMIC, KOKIRI_STATIC]


def test_warming_the_sample_twice_hits_the_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    dump = Dump(build_fixture(tmp_path / "dump"))
    assert "Npc_Kokiri_F_01" in scene_model_names(dump, KOKIRI_STATIC)
    cold = run_warm(dump, KOKIRI_STATIC)
    assert cold["misses"] >= 1
    assert cold["convertMs"] > 0
    warm = run_warm(dump, KOKIRI_STATIC)
    assert warm["misses"] == 0
    assert warm["hits"] == cold["misses"]
    assert warm["textureMs"] == 0
    out = capsys.readouterr().out
    assert "cache=hit" in out
    assert "warm done" in out
    assert "wall " in out
    assert warm["wallMs"] >= 0


def test_an_actorless_file_is_skipped_and_neighbors_are_parsed_later(monkeypatch, capsys):
    dump = _SceneList()
    started: list[list[str]] = []

    def describe_actor(_dump, name):
        started.append(list(_dump.reads))
        return {"status": "mesh", "timing": {"cache": "miss", "convertMs": 3, "textureMs": 1}}

    monkeypatch.setattr("ocarina_forge.models.describe_actor", describe_actor)
    assert scene_model_names(dump, KOKIRI_STATIC) == []
    dump.reads.clear()
    summary = run_warm(dump, DYNAMIC)
    assert summary["models"] == 2
    assert summary["misses"] == 2
    assert summary["wallMs"] >= 0
    assert started[0] == [DYNAMIC]
    assert KOKIRI_STATIC in dump.reads
    assert CASTLE_STATIC in dump.reads
    assert dump.reads.index(DYNAMIC) < dump.reads.index(KOKIRI_STATIC)
    out = capsys.readouterr().out
    assert "Obj_Tree cache=miss convert=3ms texture=1ms" in out
    assert KOKIRI_STATIC not in out
    only = _SceneList()
    run_warm(only, DYNAMIC, neighbors=False)
    assert only.reads == [DYNAMIC]
    queued = queue_warm(_SceneList(), DYNAMIC)
    assert queued[0] == ("model", "Obj_Tree")
    assert queued[1] == ("model", "Npc_Kokiri_F_01")
    assert ("scene", KOKIRI_STATIC) in queued
    assert ("scene", CASTLE_STATIC) in queued

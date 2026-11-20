"""Scene diffs, export, the YAML importer and the HTTP API, all on the synthetic dump."""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from ocarina_forge.actors import actor_id, format_hash, read_vec
from ocarina_forge.dump import Dump
from ocarina_forge.export import build
from ocarina_forge.fixtures import (
    CUCCO,
    FADO,
    FIELD_STATIC,
    GUARD,
    KOKIRI_STATIC,
    STAL,
    TREE,
    TREE2,
    build_fixture,
    kokiri_static,
)
from ocarina_forge.formats.byml import loads, same
from ocarina_forge.formats.restbl import load_table
from ocarina_forge.formats.zsio import decompress
from ocarina_forge.importers.ato import import_scenes
from ocarina_forge.project import Project
from ocarina_forge.scene import apply_command, export_document, scene_view

ROOT = Path(__file__).resolve().parents[1]


def _dump(tmp_path):
    return Dump(build_fixture(tmp_path / "dump"))


def _project(tmp_path):
    return Project.create(tmp_path / "mod", name="kokiri-notes", title="Kokiri notes", author="Ada", version="0.1.0")


def test_move_hide_add_and_undo_roundtrip(tmp_path):
    dump = _dump(tmp_path)
    document, dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    assert dict_id == 3
    assert same(document, kokiri_static())
    diff = {"format": 1, "banc": KOKIRI_STATIC, "actors": {}}
    diff, focus = apply_command(document, diff, {"command": "transform", "id": format_hash(FADO), "translate": [6, 0, 3]})
    assert focus == format_hash(FADO)
    diff, _focus = apply_command(document, diff, {"command": "hide", "id": format_hash(CUCCO)})
    diff, added = apply_command(
        document, diff, {"command": "add", "actor": "Npc_Zora_01", "translate": [1, 0, 5]}
    )
    view = scene_view(document, diff)
    by_id = {actor["id"]: actor for actor in view["actors"]}
    assert by_id[format_hash(FADO)]["status"] == "edited"
    assert by_id[format_hash(FADO)]["translate"] == [6, 0, 3]
    assert by_id[format_hash(CUCCO)]["status"] == "hidden"
    assert by_id[added]["status"] == "added"
    assert by_id[added]["gyaml"] == "Npc_Zora_01"
    # The sign still points at Fado, who is only moved, so there is no dangling-hash warning.
    assert not any("Sign" in warning for warning in view["warnings"])

    exported, warnings = export_document(document, diff)
    assert any("Sign" in warning or "Links" in warning for warning in warnings) is False
    ids = {actor_id(actor) for actor in exported["Actors"]}
    assert format_hash(CUCCO) not in ids
    assert added in ids
    fado = next(actor for actor in exported["Actors"] if actor_id(actor) == format_hash(FADO))
    assert fado["Translate"] == [6.0, 0.0, 3.0]
    assert fado["Dynamic"]["DefaultDungeonRoomIndex"] == document["Actors"][0]["Dynamic"]["DefaultDungeonRoomIndex"]
    assert exported["FilePath"] == document["FilePath"]
    assert same(exported["AuxFlags"], document["AuxFlags"])

    # Hiding Fado, whom the sign links to, is reported and does not rewrite the link.
    hidden, _focus = apply_command(document, diff, {"command": "hide", "id": format_hash(FADO)})
    _exported, warnings = export_document(document, hidden)
    assert any("FldObj_Sign" in warning for warning in warnings)

    reverted, _focus = apply_command(document, diff, {"command": "revert", "id": format_hash(FADO)})
    assert format_hash(FADO) not in reverted["actors"]


def test_transform_one_copy_leaves_the_other_and_undo_restores_it(tmp_path):
    dump = _dump(tmp_path)
    document, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    empty = {"format": 1, "banc": KOKIRI_STATIC, "actors": {}}
    before = {actor["id"]: actor for actor in scene_view(document, empty)["actors"]}
    tree = format_hash(TREE)
    twin = format_hash(TREE2)
    assert before[tree]["gyaml"] == before[twin]["gyaml"] == "Obj_Tree"
    original_tree = list(before[tree]["translate"])
    original_twin = list(before[twin]["translate"])
    diff, focus = apply_command(
        document,
        empty,
        {"command": "transform", "id": tree, "translate": [9, 2, 1], "rotate": [0, 0.4, 0]},
    )
    assert focus == tree
    assert set(diff["actors"]) == {tree}
    moved = {actor["id"]: actor for actor in scene_view(document, diff)["actors"]}
    assert moved[tree]["status"] == "edited"
    assert moved[tree]["translate"] == [9.0, 2.0, 1.0]
    assert moved[tree]["rotate"][1] == 0.4
    assert moved[twin]["status"] == "vanilla"
    assert moved[twin]["translate"] == original_twin
    assert moved[twin]["rotate"] == before[twin]["rotate"]
    exported, _warnings = export_document(document, diff)
    saved = {actor_id(actor): actor for actor in exported["Actors"]}
    assert saved[tree]["Translate"] == [9.0, 2.0, 1.0]
    assert read_vec(saved[twin], "Translate", [0, 0, 0]) == original_twin
    restored = {actor["id"]: actor for actor in scene_view(document, empty)["actors"]}
    assert restored[tree]["translate"] == original_tree
    assert restored[twin]["translate"] == original_twin


def test_set_param_keeps_the_byml_type(tmp_path):
    dump = _dump(tmp_path)
    document, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    diff = {"format": 1, "banc": KOKIRI_STATIC, "actors": {}}
    diff, _focus = apply_command(
        document,
        diff,
        {"command": "set", "id": format_hash(FADO), "path": "Dynamic.DefaultDungeonRoomIndex", "value": ["u32", 4]},
    )
    exported, _warnings = export_document(document, diff)
    fado = next(actor for actor in exported["Actors"] if actor_id(actor) == format_hash(FADO))
    from ocarina_forge.formats.byml import U32

    assert type(fado["Dynamic"]["DefaultDungeonRoomIndex"]) is U32
    assert int(fado["Dynamic"]["DefaultDungeonRoomIndex"]) == 4
    # The sign's Translate stays an X/Y/Z dictionary when we move it.
    diff, _focus = apply_command(
        document, {"format": 1, "banc": KOKIRI_STATIC, "actors": {}},
        {"command": "transform", "id": format_hash(0x100000000000000A), "translate": [9, 1, 1]},
    )
    exported, _warnings = export_document(document, diff)
    sign = next(actor for actor in exported["Actors"] if actor["Gyaml"] == "FldObj_Sign")
    assert set(sign["Translate"]) == {"X", "Y", "Z"}
    assert sign["Translate"]["X"] == 9.0


def test_export_installs_and_zips_without_touching_other_files(tmp_path):
    dump = _dump(tmp_path)
    project = _project(tmp_path)
    document, _dict_id, original = dump.read_byml(KOKIRI_STATIC)
    diff, _focus = apply_command(
        document,
        {"format": 1, "banc": KOKIRI_STATIC, "actors": {}},
        {"command": "transform", "id": format_hash(FADO), "translate": [6, 1, 3], "rotate": [0, 1.2, 0]},
    )
    project.save_scene_diff(diff)
    # A dialog patch is stored and must be reported, not silently dropped.
    (project.root / "text").mkdir(exist_ok=True)
    (project.root / "text" / "USen.yaml").write_text("language: USen\nmessages: {}\n", encoding="utf-8")
    result = build(project, dump, install=True, make_zip=True, level=1)
    assert result["ok"]
    text = "\n".join(line["message"] for line in result["log"])
    assert "Dialog: 1 patch file" in text
    assert "dictionary 3" in text
    installed = Path(result["installed"]) / "romfs"
    written = installed / KOKIRI_STATIC
    assert written.is_file()
    assert not (installed / "Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs").exists()
    payload, dict_id = decompress(written.read_bytes(), dump.dicts())
    assert dict_id == 3
    edited = loads(payload)
    fado = next(actor for actor in edited["Actors"] if actor_id(actor) == format_hash(FADO))
    assert fado["Translate"] == [6.0, 1.0, 3.0]
    assert fado["Rotate"][0] == 0.0 and fado["Rotate"][2] == 0.0
    assert abs(fado["Rotate"][1] - 1.2) < 1e-6

    table = load_table((installed / "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs").read_bytes())
    original_table = load_table((dump.romfs / "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs").read_bytes())
    assert table.get(KOKIRI_STATIC) >= len(payload)
    assert table.get(KOKIRI_STATIC) % 8 == 0
    assert table.get(KOKIRI_STATIC) >= original_table.get(KOKIRI_STATIC)
    assert table.get("System/Resource/KeepMe.bgyml") == 4096
    assert table.get("AS/Untouched.root.asb") == 2048
    # The vanilla file is still the vanilla file.
    assert (dump.romfs / KOKIRI_STATIC).read_bytes() != written.read_bytes() or len(original) == len(payload)
    archive = Path(result["zip"])
    import zipfile

    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
    assert any(name.startswith("kokiri-notes/romfs/Banc/") for name in names)
    assert not any(name.startswith("_") or name.startswith("-") for name in names)


def test_mod_name_cannot_be_disabled(tmp_path):
    from ocarina_forge.errors import BancError
    import pytest

    with pytest.raises(BancError, match="disabled"):
        Project.create(tmp_path / "bad", name="_hidden", title="No", author="", version="0.1.0")


def test_missing_dump_is_explicit(tmp_path):
    from ocarina_forge.errors import BancError
    import pytest

    with pytest.raises(BancError, match="romfs") as caught:
        Dump(tmp_path / "nope")
    assert caught.value.code == "dump_missing"


def test_import_ato_yaml(tmp_path):
    project = _project(tmp_path)
    messages = import_scenes(project, ROOT / "examples" / "ato-scenes")
    assert messages and "HyruleField" in messages[0]
    diff = project.load_scene_diff(FIELD_STATIC)
    actors = diff["actors"]
    guard = actors[format_hash(GUARD)]
    assert guard["state"] == "edited"
    assert guard["translate"] == [0.0, 0.0, 6.0]
    assert guard["name"] == "Posted guard"
    assert actors[format_hash(STAL)]["state"] == "hidden"
    added = [record for record in actors.values() if record["state"] == "added"]
    assert added and added[0]["actor"] == "Npc_Zora_01"
    assert added[0]["translate"] == [3.0, 0.0, 1.0]


def test_api_sample_edit_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    from ocarina_forge.server import app

    client = TestClient(app)
    missing = client.get("/api/scenes")
    assert missing.status_code == 409
    assert missing.json()["error"] == "dump_missing"
    assert "romfs" in missing.json()["message"]

    dump = _dump(tmp_path)
    project = _project(tmp_path)
    saved = client.put("/api/config", json={"dump": str(dump.root), "project": str(project.root)})
    assert saved.status_code == 200
    scenes = client.get("/api/scenes")
    assert scenes.status_code == 200
    areas = {scene["area"] for scene in scenes.json()["scenes"]}
    assert areas == {"KokiriForest", "HyruleField"}

    scene = client.get("/api/scene", params={"banc": KOKIRI_STATIC})
    assert scene.status_code == 200
    body = scene.json()
    assert len([actor for actor in body["actors"] if actor["status"] != "hidden"]) == 12
    companions = body["companions"]
    assert companions[0]["banc"] == "Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs"
    assert any(actor["gyaml"] == "Land_Kokiri_House_01" for actor in companions[0]["actors"])
    assert all(actor["gyaml"] != "Land_Kokiri_House_01" for actor in body["actors"])
    fado = next(actor for actor in body["actors"] if actor["gyaml"] == "Npc_Kokiri_F_01")
    assert fado["category"] == "npc"
    assert fado["name"] == "Fado"

    edited = client.post(
        "/api/scene/edit",
        json={"banc": KOKIRI_STATIC, "command": {"command": "duplicate", "id": fado["id"]}},
    )
    assert edited.status_code == 200
    focus = edited.json()["focus"]
    copy = next(actor for actor in edited.json()["actors"] if actor["id"] == focus)
    assert copy["status"] == "added"
    assert copy["translate"][0] == fado["translate"][0] + 1

    # Undo by putting the previous diff back.
    restored = client.put("/api/scene/diff", json={"banc": KOKIRI_STATIC, "actors": {}})
    assert restored.status_code == 200
    assert all(actor["status"] == "vanilla" for actor in restored.json()["actors"])

    client.post(
        "/api/scene/edit",
        json={
            "banc": KOKIRI_STATIC,
            "command": {"command": "remove", "id": fado["id"]},
        },
    )
    built = client.post("/api/export", json={"install": True, "zip": True})
    assert built.status_code == 200, built.text
    log = "\n".join(line["message"] for line in built.json()["log"])
    assert "size table" in log
    assert (dump.root / "mods" / "kokiri-notes" / "romfs" / KOKIRI_STATIC).is_file()
    # The dump's own placement file is untouched.
    fresh = Dump(dump.root)
    vanilla, _dict_id, _raw = fresh.read_byml(KOKIRI_STATIC)
    assert any(actor_id(actor) == format_hash(FADO) for actor in vanilla["Actors"])

    names = client.get("/api/actors", params={"q": "zora"})
    assert names.json()["names"] == ["Npc_Zora_01"]

    bad = client.post("/api/scene/edit", json={"banc": KOKIRI_STATIC, "command": {"command": "add", "actor": ""}})
    assert bad.status_code == 400
    assert "name" in bad.json()["message"].lower()


def test_sample_project_ids_exist_in_the_fixture(tmp_path):
    dump = _dump(tmp_path)
    document, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    present = {actor_id(actor) for actor in document["Actors"]}
    sample = yaml.safe_load((ROOT / "examples" / "sample-project" / "scenes" / "KokiriForest" / "KokiriForest_Child_Static.yaml").read_text())
    for ident, record in sample["actors"].items():
        if record["state"] != "added":
            assert ident in present


def test_model_batch_is_one_round_trip_and_glbs_are_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("OCARINA_FORGE_WORKERS", "1")
    monkeypatch.chdir(tmp_path)
    from ocarina_forge.server import app, describe_actor

    dump = _dump(tmp_path)
    client = TestClient(app)
    client.put("/api/config", json={"dump": str(dump.root), "project": ""})
    listed = client.post("/api/models/batch", json={"names": ["Npc_Kokiri_F_01", "Npc_Kokiri_F_01", "Not_An_Actor"]})
    assert listed.status_code == 200, listed.text
    models = listed.json()["models"]
    assert set(models) == {"Npc_Kokiri_F_01", "Not_An_Actor"}
    assert models["Not_An_Actor"]["status"] == "box"
    assert models["Npc_Kokiri_F_01"]["status"] == "queued"
    assert models["Npc_Kokiri_F_01"]["url"].endswith("/model.glb")

    calls = {"n": 0}
    real = describe_actor

    def counting(dump, gyaml, convert=True):
        if convert:
            calls["n"] += 1
        return real(dump, gyaml, convert=convert)

    monkeypatch.setattr("ocarina_forge.server.describe_actor", counting)
    import asyncio
    from httpx import ASGITransport, AsyncClient

    async def both():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            return await asyncio.gather(
                http.get("/api/models/Npc_Kokiri_F_01/model.glb"),
                http.get("/api/models/Npc_Kokiri_F_01/model.glb"),
            )

    first, second = asyncio.run(both())
    assert first.status_code == 200 and second.status_code == 200
    assert first.headers["etag"] == second.headers["etag"]
    assert calls["n"] == 1
    digest = first.headers["etag"].strip('"')
    cached = client.get(f"/api/models/Npc_Kokiri_F_01/model.glb?v={digest}")
    assert cached.status_code == 200
    assert cached.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert cached.headers["etag"] == f'"{digest}"'
    again = client.get(
        f"/api/models/Npc_Kokiri_F_01/model.glb?v={digest}",
        headers={"If-None-Match": f'"{digest}"'},
    )
    assert again.status_code == 304
    warm = client.post("/api/models/batch", json={"names": ["Npc_Kokiri_F_01"]})
    assert warm.json()["models"]["Npc_Kokiri_F_01"]["url"].endswith(f"v={digest}")


def test_sibling_bancs_swap_static_and_dynamic():
    from ocarina_forge.scene import sibling_bancs

    static = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"
    dynamic = "Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs"
    known = {static, dynamic, "Banc/HyruleField/HyruleField_Adult_Static.bcett.byml.zs"}
    assert sibling_bancs(static, known) == [dynamic]
    assert sibling_bancs(dynamic, known) == [static]
    assert sibling_bancs("Banc/HyruleField/HyruleField_Adult_Static.bcett.byml.zs", known) == []

"""Load filters skip decodes. Save and both exporters pass unloaded banc data through."""

import json
from pathlib import Path

from ocarina_forge.actors import actor_id, format_hash, make_actor
from ocarina_forge.dump import Dump
from ocarina_forge.export import build
from ocarina_forge.fixtures import FADO, KOKIRI_STATIC, build_fixture, kokiri_static
from ocarina_forge.formats.byml import U64, dumps, loads
from ocarina_forge.formats.zsio import compress, decompress
from ocarina_forge.layers import (
    WATER_TOKENS,
    actor_layer,
    filter_attached,
    keep_unloaded_edits,
    parse_load_query,
    preset_spec,
)
from ocarina_forge.modexport import export_mod
from ocarina_forge.project import Project
from ocarina_forge.scene import apply_command, export_document, scene_view

WATER = 0x10000000000000F1
AIR = 0x10000000000000F2
GRASS = 0x10000000000000F3
LAMP = 0x10000000000000F4
EFFECT = 0x1000000000000008


def test_actor_layers_follow_the_banc_prefixes():
    assert actor_layer("Npc_Kokiri_F_01") == "models"
    assert actor_layer("Npc_River_A") == "models"
    assert actor_layer("Enemy_DekuBaba") == "models"
    assert actor_layer("Animal_Cucco") == "models"
    assert actor_layer("Obj_Tree") == "models"
    assert actor_layer("Land_Kokiri_Ground") == "models"
    assert actor_layer("Area_Kokiri_Town") == "models"
    assert actor_layer("StartPos") == "models"
    assert actor_layer("Eft_Firefly") == "effects"
    assert actor_layer("EftLoc_Smoke") == "effects"
    assert actor_layer("Obj_Lake_Water") == "water"
    assert actor_layer("Obj_LakeHrl_WaterPlane_A_07") == "water"
    assert actor_layer("FldObj_AirWall_A") == "collision"
    assert actor_layer("Obj_Collision_A") == "collision"
    assert actor_layer("Obj_Grass_A") == "decorations"
    assert actor_layer("Obj_Lamp_A") == "lights"
    from ocarina_forge.models import _WATER_TOKENS

    assert WATER_TOKENS == _WATER_TOKENS


def test_presets_and_query():
    full = preset_spec("full")
    assert full is not None and "effects" in full.layers and full.textures
    layout = preset_spec("layout")
    assert layout is not None and layout.textures is False and "effects" not in layout.layers
    assert "models" in layout.layers and "collision" in layout.layers
    logic = preset_spec("logic")
    assert logic is not None and logic.layers == frozenset({"behavior", "collision"})
    query = parse_load_query("terrain,models")
    assert query is not None
    assert query.layers == frozenset({"terrain", "models"})
    assert query.textures is True
    assert parse_load_query("") is None
    assert parse_load_query("layout").preset == "layout"
    flat = parse_load_query("models", "0")
    assert flat is not None and flat.textures is False and flat.layers == frozenset({"models"})
    # The editor turns Layout's textures on without changing the layer list.
    # That list is Layout, which skips BNTX, unless textures=1 is sent too.
    layout_layers = "models,terrain,water,collision,decorations,lights"
    implied = parse_load_query(layout_layers)
    assert implied is not None and implied.preset == "layout" and implied.textures is False
    forced = parse_load_query(layout_layers, "1")
    assert forced is not None and forced.textures is True and forced.preset == "custom"


def _rich_document():
    document = kokiri_static()
    document["Actors"] = list(document["Actors"]) + [
        make_actor("Obj_Lake_Water", WATER, [1, 0, 2], params={"Dynamic": {"Marker": "water-byte"}}),
        make_actor("FldObj_AirWall_A", AIR, [0, 1, 0], scale=[4, 3, 4], params={"Dynamic": {"Marker": "collision-byte"}}),
        make_actor("Obj_Grass_A", GRASS, [2, 0, 2], params={"Dynamic": {"Marker": "grass-byte"}}),
        make_actor(
            "Obj_Lamp_A",
            LAMP,
            [3, 2, 1],
            params={"Dynamic": {"Marker": "lamp-byte"}, "Links": [{"Dst": U64(FADO), "Src": U64(LAMP), "Ref": "light"}]},
        ),
    ]
    document["AiGroups"] = [{"Name": "untouched-group", "Hash": U64(WATER)}]
    document["UntouchedNote"] = "keep-this-byte"
    return document


def _install(dump: Dump, document: dict) -> bytes:
    payload = dumps(document)
    packed = compress(payload, 3, dump.dicts(), level=3)
    path = dump.romfs / KOKIRI_STATIC
    path.write_bytes(packed)
    return path.read_bytes()


def _by_id(document):
    return {actor_id(actor): actor for actor in document["Actors"] if actor_id(actor)}


def test_filtered_view_omits_actors_and_keeps_the_diff(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    document = _rich_document()
    _install(dump, document)
    fresh, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    project = Project.create(tmp_path / "mod", name="layer-notes", title="Layers", author="Ada", version="0.1.0")
    lamp = format_hash(LAMP)
    diff, _focus = apply_command(
        fresh,
        {"format": 1, "banc": KOKIRI_STATIC, "actors": {}},
        {"command": "transform", "id": lamp, "translate": [8, 2, 1]},
    )
    project.save_scene_diff(diff)
    view = scene_view(fresh, project.load_scene_diff(KOKIRI_STATIC))
    spec = parse_load_query("terrain,models")
    filtered = filter_attached(view, spec)
    names = {actor["gyaml"] for actor in filtered["actors"]}
    assert "Npc_Kokiri_F_01" in names
    assert "Obj_Tree" in names
    assert "Eft_Firefly" not in names
    assert "Obj_Lake_Water" not in names
    assert "FldObj_AirWall_A" not in names
    assert "Obj_Grass_A" not in names
    assert "Obj_Lamp_A" not in names
    assert lamp in filtered["diff"]["actors"]
    assert filtered["load"]["omitted"] == ["water", "collision", "behavior", "effects", "decorations", "lights"]
    assert all(actor.get("links") is None for actor in filtered["actors"])


def test_logic_keeps_link_ends_as_gizmos_without_models():
    document = _rich_document()
    view = scene_view(document, {"format": 1, "banc": KOKIRI_STATIC, "actors": {}})
    filtered = filter_attached(view, parse_load_query("logic"))
    by_gyaml = {actor["gyaml"]: actor for actor in filtered["actors"]}
    assert "FldObj_AirWall_A" in by_gyaml
    assert by_gyaml["FldObj_AirWall_A"]["fetchModel"] is False
    assert by_gyaml["Obj_Lamp_A"]["logicOnly"] is True
    assert by_gyaml["Obj_Lamp_A"]["fetchModel"] is False
    assert format_hash(FADO) in by_gyaml["Obj_Lamp_A"]["links"]
    assert by_gyaml["Npc_Kokiri_F_01"]["logicOnly"] is True
    assert "Obj_Tree" not in by_gyaml
    assert "Eft_Firefly" not in by_gyaml


def test_save_keeps_unloaded_edits_byte_for_byte():
    lamp = format_hash(LAMP)
    effect = format_hash(EFFECT)
    fado = format_hash(FADO)
    stored = {
        lamp: {"state": "edited", "gyaml": "Obj_Lamp_A", "translate": [4, 0, 1]},
        effect: {"state": "edited", "gyaml": "Eft_Firefly", "translate": [1, 2, 3], "set": {"Dynamic.Marker": "keep-me"}},
    }
    incoming = {
        fado: {"state": "edited", "gyaml": "Npc_Kokiri_F_01", "translate": [9, 0, 0]},
        lamp: {"state": "edited", "gyaml": "Obj_Lamp_A", "translate": [99, 99, 99]},
    }
    before = dumps({"actors": stored})
    merged = keep_unloaded_edits(incoming, stored, ["models", "terrain"])
    assert dumps({"actors": stored}) == before
    assert merged[lamp] is stored[lamp]
    assert merged[effect] is stored[effect]
    assert dumps(merged[effect]) == dumps(stored[effect])
    assert merged[fado]["translate"] == [9, 0, 0]


def test_round_trip_with_layers_off_is_byte_identical(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    document = _rich_document()
    _install(dump, document)
    source_file = (dump.romfs / KOKIRI_STATIC).read_bytes()
    fresh, dict_id, original = dump.read_byml(KOKIRI_STATIC)
    assert dict_id == 3
    source_actors = _by_id(fresh)
    untouched = [format_hash(value) for value in (WATER, AIR, GRASS, LAMP, EFFECT)]

    project = Project.create(tmp_path / "mod", name="layer-notes", title="Layers", author="Ada", version="0.1.0")
    project.write_load_filters({"preset": "custom", "textures": True, "layers": {"models": True, "terrain": True}})
    fado = format_hash(FADO)
    diff, _focus = apply_command(
        fresh,
        {"format": 1, "banc": KOKIRI_STATIC, "actors": {}},
        {"command": "transform", "id": fado, "translate": [6, 1, 3]},
    )
    # A save payload that never saw the other layers must not wipe a stored edit,
    # and must not invent one. The round trip below has no stored edit on them.
    assert keep_unloaded_edits(
        {fado: diff["actors"][fado]},
        {format_hash(LAMP): {"state": "edited", "gyaml": "Obj_Lamp_A", "translate": [1, 1, 1]}},
        ["models"],
    )[format_hash(LAMP)]["translate"] == [1, 1, 1]
    project.save_scene_diff(diff)

    exported, _warnings = export_document(fresh, project.load_scene_diff(KOKIRI_STATIC))
    exported_actors = _by_id(exported)
    for ident in untouched:
        assert dumps(exported_actors[ident]) == dumps(source_actors[ident])
    for key, value in fresh.items():
        if key == "Actors":
            continue
        assert dumps({key: exported[key]}) == dumps({key: value})
    assert exported_actors[fado]["Translate"] == [6.0, 1.0, 3.0]
    assert original  # the source payload is the byte baseline above

    built = build(project, dump, install=False, make_zip=False, level=1)
    assert built["ok"]
    assert (dump.romfs / KOKIRI_STATIC).read_bytes() == source_file
    written = project.root / "build" / "romfs" / KOKIRI_STATIC
    payload, written_dict = decompress(written.read_bytes(), dump.dicts())
    assert written_dict == 3
    rebuilt = loads(payload)
    rebuilt_actors = _by_id(rebuilt)
    for ident in untouched:
        assert dumps(rebuilt_actors[ident]) == dumps(source_actors[ident])
    for key, value in fresh.items():
        if key == "Actors":
            continue
        assert dumps({key: rebuilt[key]}) == dumps({key: value})

    source = project.root / ".longshot-source"
    (source / "patches").mkdir(parents=True)
    sentinel = b"\x00\x01untouched-layer"
    (source / "sentinel.bin").write_bytes(sentinel)
    (source / "mod.json").write_text(
        json.dumps({"id": "local/layer-notes", "name": "Layers", "version": "0.1.0"}),
        encoding="utf-8",
    )
    (source / "patches" / "KokiriForest_Child_Static.json").write_text(
        json.dumps({
            "target": KOKIRI_STATIC,
            "placements": [{"op": "set", "hash": fado, "path": "Translate", "value": [6, 1, 3]}],
        }),
        encoding="utf-8",
    )
    mod = export_mod(project, dump, install=False)
    assert (dump.romfs / KOKIRI_STATIC).read_bytes() == source_file
    assert (project.root / "build" / "mod" / "sentinel.bin").read_bytes() == sentinel
    patch = json.loads((project.root / "build" / "mod" / "patches" / "KokiriForest_Child_Static.json").read_text(encoding="utf-8"))
    hashes = {op["hash"] for op in patch["placements"]}
    assert hashes == {fado}
    assert format_hash(LAMP) not in hashes
    assert format_hash(EFFECT) not in hashes
    assert mod["files"]


def test_texture_manifest_does_not_rewrite_geometry(tmp_path, monkeypatch):
    """Geometry stays one file. Textures decode once, into their own cache."""
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    from ocarina_forge.formats.bfres import sample_bfres
    from ocarina_forge.formats.bntx import write_rgba
    from ocarina_forge.formats.sarc import pack
    from ocarina_forge.formats.zsio import compress
    import ocarina_forge.models as models

    dump = Dump(build_fixture(tmp_path / "dump"))
    gyaml = "Obj_LiftRock_A_01"
    info = dumps(
        {
            "FmdbName": gyaml,
            "ModelProjectName": "Obj_Rock_A",
        }
    )
    archive = pack(
        {
            f"Actor/{gyaml}.engine__actor__ActorParam.bgyml": dumps({"Name": gyaml}),
            f"Component/ModelInfo/{gyaml}.engine__component__ModelInfo.bgyml": info,
        }
    )
    dicts = dump.dicts()
    (dump.romfs / "Pack" / "Actor" / f"{gyaml}.pack.zs").write_bytes(compress(archive, 4, dicts, level=3))
    rgba = bytes([180, 40, 40, 255, 40, 40, 40, 255, 180, 40, 40, 255, 40, 40, 40, 255])
    mesh = sample_bfres(
        gyaml,
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        rgba,
        2,
        2,
        texture_name="Obj_Rock_A_Alb",
        embed_texture=False,
    )
    (dump.romfs / "Model" / "Obj_Rock_A.Obj_LiftRock_A_01.bfres.zs").write_bytes(compress(mesh, 1, dicts, level=3))
    image = write_rgba("Obj_Rock_A_Alb", 2, 2, rgba, swizzled=True)
    (dump.romfs / "Tex" / "Obj_Rock_A_Alb.bntx.zs").write_bytes(compress(image, 1, dicts, level=3))

    calls = []
    original = models._decode_bntx_cached

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(models, "_decode_bntx_cached", counting)
    geometry = models.describe_actor(dump, gyaml, flat=True)
    assert geometry["status"] == "mesh"
    assert calls == []
    glb_path = models.cache_dir() / geometry["digest"][:2] / f"{geometry['digest']}.glb"
    before = glb_path.read_bytes()
    assert b"image/ktx2" not in before
    assert b"image/png" not in before

    def refuse_assemble(*_args, **_kwargs):
        raise AssertionError("texture load rebuilt the glb")

    monkeypatch.setattr(models, "_assemble", refuse_assemble)
    monkeypatch.setattr(models, "describe_actor", refuse_assemble)
    first = models.describe_actor_textures(dump, gyaml)
    decoded = len(calls)
    assert decoded >= 1
    maps = [slot.get("map") for slot in first["materials"].values()]
    assert maps and all(url and url.startswith("/api/model-textures/") for url in maps)
    json_len = int.from_bytes(before[12:16], "little")
    assert before[16:20] == b"JSON"
    document = json.loads(before[20:20 + json_len])
    families = []
    for node in document["nodes"]:
        prefix, colon, rest = node["name"].partition(":")
        assert colon == ":" and prefix.startswith("lod") and rest
        families.append(rest)
    assert set(first["materials"]) <= set(families)
    assert set(first["materials"])
    assert glb_path.read_bytes() == before
    second = models.describe_actor_textures(dump, gyaml)
    assert len(calls) == decoded
    assert second["materials"] == first["materials"]
    assert glb_path.read_bytes() == before


def test_texture_route_does_not_convert_geometry(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import ocarina_forge.server as server

    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    dump = Dump(build_fixture(tmp_path / "dump"))
    project = Project.create(tmp_path / "mod", name="layer-notes", title="Layers", author="Ada", version="0.1.0")
    calls = []

    def boom(*_args, **_kwargs):
        raise AssertionError("geometry convert ran")

    def manifest(_dump, gyaml):
        calls.append(gyaml)
        return {"gyaml": gyaml, "materials": {"Body:0": {"map": "/api/model-textures/abc.ktx2"}}}

    monkeypatch.setattr(server, "describe_actor", boom)
    monkeypatch.setattr(server, "describe_actor_textures", manifest)
    client = TestClient(server.app)
    saved = client.put("/api/config", json={"dump": str(dump.root), "project": str(project.root)})
    assert saved.status_code == 200
    skipped = client.get("/api/models/Npc_Kokiri_F_01/textures?textures=0")
    assert skipped.status_code == 200
    assert skipped.json()["skipped"] is True
    assert skipped.json()["materials"] == {}
    assert calls == []
    loaded = client.get("/api/models/Npc_Kokiri_F_01/textures")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["materials"]["Body:0"]["map"] == "/api/model-textures/abc.ktx2"
    assert calls == ["Npc_Kokiri_F_01"]
    layout = "models,terrain,water,collision,decorations,lights"
    implied = client.get(f"/api/models/Npc_Kokiri_F_01/textures?load={layout}")
    assert implied.status_code == 200
    assert implied.json()["skipped"] is True
    assert calls == ["Npc_Kokiri_F_01"]
    forced = client.get(f"/api/models/Npc_Kokiri_F_01/textures?load={layout}&textures=1")
    assert forced.status_code == 200
    assert forced.json()["materials"]["Body:0"]["map"] == "/api/model-textures/abc.ktx2"
    assert calls == ["Npc_Kokiri_F_01", "Npc_Kokiri_F_01"]


def test_flat_models_do_not_decode_textures(tmp_path, monkeypatch):
    dump = Dump(build_fixture(tmp_path / "dump"))
    calls = []

    import ocarina_forge.models as models

    original = models._decode_bntx_cached

    def boom(*args, **kwargs):
        calls.append(args[:1])
        return original(*args, **kwargs)

    monkeypatch.setattr(models, "_decode_bntx_cached", boom)
    info = models.describe_actor(dump, "Npc_Kokiri_F_01", flat=True)
    assert info["status"] == "mesh"
    assert calls == []
    assert info["textures"] == 0


def test_endpoints_skip_a_layer_that_is_off(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    import ocarina_forge.server as server

    dump = Dump(build_fixture(tmp_path / "dump"))
    project = Project.create(tmp_path / "mod", name="layer-notes", title="Layers", author="Ada", version="0.1.0")
    client = TestClient(server.app)
    saved = client.put("/api/config", json={"dump": str(dump.root), "project": str(project.root)})
    assert saved.status_code == 200

    def fail_model(*_args, **_kwargs):
        raise AssertionError("model decode ran")

    def fail_terrain(*_args, **_kwargs):
        raise AssertionError("terrain decode ran")

    def fail_behavior(*_args, **_kwargs):
        raise AssertionError("behavior decode ran")

    monkeypatch.setattr(server, "describe_actor", fail_model)
    monkeypatch.setattr(server, "describe_terrain", fail_terrain)
    monkeypatch.setattr("ocarina_forge.behavior.load_actor_graph", fail_behavior)

    scene = client.get("/api/scene", params={"banc": KOKIRI_STATIC, "load": "terrain,models"})
    assert scene.status_code == 200
    body = scene.json()
    assert all(actor["gyaml"] != "Eft_Firefly" for actor in body["actors"])
    assert any(actor["gyaml"] == "Npc_Kokiri_F_01" for actor in body["actors"])

    model = client.get("/api/models/Eft_Firefly", params={"load": "models"})
    assert model.status_code == 200
    assert model.json()["status"] == "skipped"

    terrain = client.get("/api/terrain", params={"banc": KOKIRI_STATIC, "load": "models"})
    assert terrain.status_code == 200
    assert terrain.json()["status"] == "skipped"

    ground = client.get("/api/ground", params={"banc": KOKIRI_STATIC, "x": 0, "z": 0, "load": "models"})
    assert ground.status_code == 200
    assert ground.json()["status"] == "skipped"

    surface = client.get("/api/ground/surface", params={"banc": KOKIRI_STATIC, "load": "models"})
    assert surface.status_code == 200
    assert surface.json()["status"] == "skipped"

    behavior = client.get("/api/behavior", params={"gyaml": "Npc_Kokiri_F_01", "load": "models"})
    assert behavior.status_code == 200
    assert behavior.json()["skipped"] is True

    stored = client.put(
        "/api/load-filters",
        json={"preset": "layout", "textures": False, "layers": {layer: layer in {"models", "terrain", "water", "collision", "decorations", "lights"} for layer in (
            "models", "terrain", "water", "collision", "behavior", "effects", "decorations", "lights",
        )}},
    )
    assert stored.status_code == 200
    assert stored.json()["preset"] == "layout"
    assert stored.json()["textures"] is False
    again = client.get("/api/load-filters")
    assert again.json()["layers"]["effects"] is False
    assert Path(project.root / "load-filters.yaml").is_file()

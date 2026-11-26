"""Automatic terrain fitting keeps the ordinary terrain available while it works."""

from types import SimpleNamespace

from ocarina_forge.local_api import editor_client

from ocarina_forge import scene_preview, server
from ocarina_forge.dump import Dump, lexical_dump_relative
from ocarina_forge.fixtures import FIELD_STATIC, KOKIRI_DYNAMIC, KOKIRI_STATIC, build_fixture


def test_terrain_uses_age_specific_material_then_falls_back(tmp_path, monkeypatch):
    from ocarina_forge import models
    from ocarina_forge.formats import terrain

    folder = tmp_path / "TerrainArc" / "Field"
    folder.mkdir(parents=True)
    for suffix in (".hght", ".mate", ".adult.mate", ".child.mate.zs"):
        (folder / f"001{suffix}").write_bytes(suffix.encode())
    dump = SimpleNamespace(
        romfs=tmp_path,
        read_file=lambda path: ((tmp_path / path).read_bytes(), 0),
        dicts=lambda: {},
        resolve_relative=lambda relative: tmp_path / lexical_dump_relative(relative),
    )
    monkeypatch.setattr(terrain, "read_height_file", lambda _data: ([0.1], None, "height"))

    def material(data, _dicts):
        if data == b".child.mate.zs":
            raise terrain.TerrainError("damaged child material")
        return [2] if data == b".adult.mate" else [1], None, "material"

    monkeypatch.setattr(terrain, "read_terrain_material", material)
    for variant, suffix, expected in (("adult", ".adult.mate", 2), ("child", ".mate", 1), ("", ".mate", 1)):
        loaded, skipped = models._read_height_tiles(dump, [(0, 0, 1, "001")], ["Field"], {}, "main", variant)
        assert not skipped
        assert loaded[0][4:6] == ([0.1], [expected])
        assert loaded[0][8].mate == f"TerrainArc/Field/001{suffix}"


def test_fit_cache_is_shared_between_scene_banks_but_not_maps(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    dump = SimpleNamespace(root=tmp_path / "dump")
    key = lambda name: scene_preview.corrected_terrain_path(dump, f"Banc/{name}.bcett.byml.zs", "base")
    assert key("Field/Field_Child_Dynamic") == key("Field/Field_Child_Static")
    assert key("Field/Field_Child_Dynamic") != key("Field/Field_Adult_Static")
    assert key("Field/Field_Child_Dynamic") != key("Town/Town_Child_Dynamic")


def test_scene_pair_shares_preview_url_and_retains_bank_ownership(tmp_path, monkeypatch):
    dump = Dump(build_fixture(tmp_path / "dump"))
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_open_dump", lambda: dump)
    monkeypatch.setattr(server, "_diff_for", lambda banc: {"banc": banc})
    monkeypatch.setattr(server, "_attach_recipes", lambda view, banc: view)
    path = scene_preview.preview_path(dump, KOKIRI_DYNAMIC)
    assert scene_preview.preview_path(dump, KOKIRI_STATIC) == path
    assert scene_preview.preview_path(dump, FIELD_STATIC) != path
    scene_preview._write_prepared(path, b"paired preview")

    client = editor_client(server.app)
    dynamic = client.get("/api/scene", params={"banc": KOKIRI_DYNAMIC}).json()
    static = client.get("/api/scene", params={"banc": KOKIRI_STATIC}).json()
    assert dynamic["banc"] == static["companions"][0]["banc"] == KOKIRI_DYNAMIC
    assert static["banc"] == dynamic["companions"][0]["banc"] == KOKIRI_STATIC
    assert dynamic["previewUrl"] == static["previewUrl"]
    assert client.get(static["previewUrl"]).content == b"paired preview"


def test_all_previews_use_one_bank_per_pair_and_reuse_model_work(tmp_path, monkeypatch):
    from ocarina_forge import warm

    dump = Dump(build_fixture(tmp_path / "dump"))
    model = tmp_path / "lamp.glb"
    model.write_bytes(b"model")
    converted = []
    packed = []
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(scene_preview, "_scene_actors", lambda *args: [{"gyaml": "Lamp"}])
    monkeypatch.setattr(warm, "scene_model_names", lambda *args: ["Lamp"])
    monkeypatch.setattr(scene_preview, "describe_terrain", lambda *args, **kwargs: {})
    monkeypatch.setattr(scene_preview, "_cached_glb", lambda digest: model if digest else None)

    def convert(_dump, name, *, convert):
        assert convert
        converted.append(name)
        return {"digest": model.stem}

    def pack(manifest):
        packed.append(manifest)
        return b"preview"

    monkeypatch.setattr(scene_preview, "describe_actor", convert)
    monkeypatch.setattr(scene_preview, "_run_worker", pack)
    for _ in range(2):
        assert scene_preview.build_all_scene_previews(dump, 0, prepare_missing=True, share_textures=False) == {
            "ready": 3, "skipped": 0, "failed": 0, "models": 1, "shared": 0,
        }
    assert converted == ["Lamp", "Lamp"]  # One cache lookup per model per build.
    assert len(packed) == 3  # The second build reused the packs. One pack per scene.


def test_terrain_api_serves_base_then_fit_with_separate_cache_keys(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path / "dump")
    banc = "Banc/Test/Test_Dynamic.bcett.byml.zs"
    info = {"status": "mesh", "terrain": {"heightScale": 100}, "digest": "base",
            "url": f"/api/terrain/model.glb?banc={banc}&v=base"}
    base = tmp_path / "base.glb"
    base.write_bytes(b"base terrain")
    prepared = []
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_open_dump", lambda: dump)
    monkeypatch.setattr(server, "_cached_glb", lambda _digest: base)

    async def describe(_banc):
        return info

    def prepare(_dump, _banc, _info):
        prepared.append(_banc)
        path = scene_preview.corrected_terrain_path(_dump, _banc, _info["digest"])
        scene_preview._write_prepared(path, b"fitted terrain")

    monkeypatch.setattr(server, "_describe_terrain_model", describe)
    monkeypatch.setattr(server, "prepare_scene_terrain", prepare)
    client = editor_client(server.app)
    original = client.get("/api/terrain", params={"banc": banc}).json()
    assert original["fitPending"] and not prepared
    assert client.get(original["url"]).content == b"base terrain"
    final = client.get("/api/terrain", params={"banc": banc, "refine": True}).json()
    assert final["url"] != original["url"] and "fitPending" not in final
    assert client.get(final["url"]).content == b"fitted terrain"
    assert client.get(original["url"]).content == b"base terrain"
    assert client.get("/api/terrain", params={"banc": banc, "refine": True}).json() == final
    assert prepared == [banc]


def test_map_without_qualifying_surfaces_stops_queuing_fit(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path / "dump")
    banc = "Banc/Other/Other_Dynamic.bcett.byml.zs"
    info = {"status": "mesh", "terrain": {"heightScale": 100}, "digest": "base"}
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(scene_preview, "_cached_glb", lambda _digest: tmp_path / "base.glb")
    assert scene_preview.terrain_fit_pending(dump, banc, info)
    assert scene_preview.prepare_scene_terrain(dump, banc, info, actors=[
        {"gyaml": "Obj_Ladder"}, {"gyaml": "Land_Town_HouseFloor"}, {"gyaml": "Land_Town_GroundDetail"},
    ]) is None
    assert not scene_preview.terrain_fit_pending(dump, banc, info)


def test_entrance_refines_at_the_matching_start_without_a_floor_model(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path)
    banc = "Banc/Test/Test_Child_Dynamic.bcett.byml.zs"
    terrain = {"status": "mesh", "terrain": {"heightScale": 100}, "digest": "base"}
    actors = [
        {"gyaml": "FieldAndDungeonEntranceEventRequestActor", "name": "Outside", "translate": [4, 9, 5]},
        {"gyaml": "StartPos", "name": "Outside", "translate": [5, 1, 4]},
        {"gyaml": "StartPos", "name": "Outside", "translate": [50, 1, 50]},
    ]
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(scene_preview, "_cached_glb", lambda _: tmp_path / "base.glb")
    monkeypatch.setattr(scene_preview, "_authored_height_tiles", lambda *_: ["fine samples"])
    requests = []
    monkeypatch.setattr(scene_preview, "_run_worker", lambda request: requests.append(request) or b"fitted")
    result = scene_preview.prepare_scene_terrain(dump, banc, terrain, actors=actors)
    assert result.read_bytes() == b"fitted"
    assert requests[0]["entrances"] == [[-10, -11, 20, 19]]
    assert requests[0]["heightTiles"] == ["fine samples"]
    assert requests[0]["cutout"] == {}

def test_entrance_uses_nearby_land_collision_with_no_model_name_override(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path)
    banc = "Banc/Test/Test_Dynamic.bcett.byml.zs"
    terrain = {"status": "mesh", "terrain": {"heightScale": 100}, "digest": "base"}
    actors = [
        {"gyaml": "StartPos", "name": "Outside", "translate": [0, 2, 0]},
        {"gyaml": "FieldAndDungeonEntranceEventRequestActor", "name": "Outside", "translate": [1, 5, 0]},
        {"gyaml": "Land_UnnamedApproach", "translate": [2, 2, 0]},
        {"gyaml": "Land_Unsupported", "translate": [2, 2, 0]},
        {"gyaml": "Land_FarAway", "translate": [40, 2, 0]},
    ]
    requests = []
    checked = []
    triangles = [[0, 0, 0, 0, 0, 1, 1, 0, 0]]

    def collision(_dump, name):
        checked.append(name)
        return triangles if name == "Land_UnnamedApproach" else []

    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(scene_preview, "_cached_glb", lambda digest: tmp_path / (digest + ".glb"))
    monkeypatch.setattr(scene_preview, "describe_actor", lambda *_args, **_kwargs: {"digest": "floor"})
    monkeypatch.setattr(scene_preview, "_entrance_collision", collision)
    monkeypatch.setattr(scene_preview, "_run_worker", lambda request: requests.append(request) or b"fitted")
    result = scene_preview.prepare_scene_terrain(dump, banc, terrain, actors=actors)
    assert result.read_bytes() == b"fitted"
    assert checked == ["Land_UnnamedApproach", "Land_Unsupported"]
    request, = requests
    assert request["entranceStarts"] == [[0, 2, 0]]
    assert request["collision"] == {"Land_UnnamedApproach": triangles}
    actor, = request["actors"]
    assert actor["gyaml"] == "Land_UnnamedApproach"
    assert actor["entranceIndices"] == [0]

def test_default_scene_preview_includes_all_cached_drawable_model_types(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path)
    banc = "Banc/Field/Field_Dynamic.bcett.byml.zs"
    model = tmp_path / "model.glb"
    model.write_bytes(b"model")
    actors = [{"gyaml": f"Land_Model_{i}"} for i in range(190)]
    actors.extend([{"gyaml": "Land_Model_0"}, {"gyaml": "RegionOnly", "region": True}])
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(scene_preview, "_scene_actors", lambda *_: actors)
    monkeypatch.setattr(scene_preview, "describe_terrain", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(scene_preview, "describe_actor", lambda *_args, **_kwargs: {"digest": "model"})
    monkeypatch.setattr(scene_preview, "_cached_glb", lambda digest: model if digest else None)
    manifests = []
    monkeypatch.setattr(scene_preview, "_run_worker", lambda sources: manifests.append(sources) or b"pack")

    _, count = scene_preview.build_scene_preview(dump, banc, known={banc})
    assert count == 190
    assert {source["name"] for source in manifests[-1]} == {f"Land_Model_{i}" for i in range(190)}
    _, count = scene_preview.build_scene_preview(dump, banc, limit=12, known={banc})
    assert count == len(manifests[-1]) == 12

def test_visible_cutouts_keep_roads_and_skip_islands():
    assert scene_preview._terrain_actor("Land_Kokiri_Ground_B_08")
    assert scene_preview._terrain_actor("Land_HyruleField_Road_A_01")
    assert not scene_preview._terrain_actor("Land_LakeHrl_Island_E_05")
    assert not scene_preview._terrain_actor("Land_LakeHrl_Cliff_B_01")
    assert not scene_preview._terrain_actor("Land_LakeHrl_Soil_A_01")
    assert not scene_preview._terrain_actor("Land_Town_HouseFloor")
    from ocarina_forge.world_surface import terrain_actor
    assert terrain_actor("Land_LakeHrl_Island_E_05")


def test_hyrule_field_fit_cache_uses_a_new_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    dump = SimpleNamespace(root=tmp_path / "dump")
    field = scene_preview.corrected_terrain_path(dump, "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs", "base")
    other = scene_preview.corrected_terrain_path(dump, "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs", "base")
    assert "scene-terrain-v11" in field.parts
    assert "scene-terrain-v10" in other.parts


def test_islands_are_not_queued_as_visible_terrain_cutouts(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path)
    banc = "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs"
    terrain = {"status": "mesh", "terrain": {"heightScale": 100}, "digest": "base"}
    monkeypatch.setattr(scene_preview, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(scene_preview, "_cached_glb", lambda _: tmp_path / "base.glb")
    requests = []
    monkeypatch.setattr(scene_preview, "_run_worker", lambda request: requests.append(request) or b"fitted")
    result = scene_preview.prepare_scene_terrain(dump, banc, terrain, actors=[
        {"gyaml": "Land_LakeHrl_Island_E_05", "translate": [-192.39, 131.35, 506.95], "rotate": [0, 0, 0], "scale": [1, 1, 1]},
    ])
    assert result is None
    assert requests == []
    none_marker = scene_preview.corrected_terrain_path(dump, banc, "base").with_suffix(".none")
    assert none_marker.is_file()


def test_ground_api_returns_the_composed_surface(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path / "dump")
    monkeypatch.setattr(server, "_open_dump", lambda: dump)

    def fake_query(_dump, banc, points, exclude_ids=None):
        x, z = points[0]
        return [{
            "x": x,
            "z": z,
            "worldY": 131.3531,
            "source": "mesh",
            "mesh": {"gyaml": "Land_LakeHrl_Island_E_05", "worldY": 131.3531, "source": "phive"},
        }]

    monkeypatch.setattr(server, "query_ground_cached", fake_query)
    client = editor_client(server.app)
    data = client.get("/api/ground", params={
        "banc": "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs",
        "x": -192.39,
        "z": 506.95,
    }).json()
    assert data["worldY"] == 131.3531
    assert data["source"] == "mesh"
    assert data["mesh"]["gyaml"] == "Land_LakeHrl_Island_E_05"

def test_ground_api_excludes_the_moved_land_actor(tmp_path, monkeypatch):
    dump = SimpleNamespace(root=tmp_path / "dump")
    monkeypatch.setattr(server, "_open_dump", lambda: dump)
    seen = {}

    def fake_query(_dump, banc, points, exclude_ids=None):
        seen["ids"] = list(exclude_ids or [])
        x, z = points[0]
        return [{
            "x": x,
            "z": z,
            "worldY": 102.6,
            "source": "heightmap",
            "mesh": None,
        }]

    monkeypatch.setattr(server, "query_ground_cached", fake_query)
    client = editor_client(server.app)
    data = client.get("/api/ground", params={
        "banc": "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs",
        "x": -195.2242,
        "z": 519.2956,
        "exclude": "0x2",
    }).json()
    assert data["worldY"] == 102.6
    assert seen["ids"] == ["0x2"]


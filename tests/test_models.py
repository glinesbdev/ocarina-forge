"""Synthetic BFRES, BNTX, and the actor-model cache. No game data."""

import os
from pathlib import Path

from bancwright.actors import is_volume, view_actor
from bancwright.dump import Dump
from bancwright.fixtures import KOKIRI_STATIC, build_fixture
from bancwright.formats.bfres import _rigid, base_color_name, parse, rank_albedo, sample_bfres
from bancwright.formats.bntx import _decode
from bancwright.formats.gltf import Primitive, png_rgba, to_glb
from bancwright.formats.swizzle import deswizzle, swizzle
from bancwright.models import cache_dir, describe_actor, describe_terrain


def test_swizzle_round_trip():
    rgba = bytes(index % 256 for index in range(16 * 16 * 4))
    packed = swizzle(16, 16, 1, 1, 4, 0, 2, rgba)
    assert deswizzle(16, 16, 1, 1, 4, 0, 2, packed) == rgba


def test_sample_bfres_positions_and_base_color():
    rgba = bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255])
    data = sample_bfres(
        "Pyramid",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        rgba,
        2,
        2,
        swizzled=True,
    )
    model = parse(data)[0]
    assert model.parts[0].positions[0] == (0.0, 0.0, 0.0)
    assert model.parts[0].indices == [0, 1, 2]
    assert model.images["alb"].rgba[:4] == b"\xff\x00\x00\xff"
    glb = to_glb([Primitive(model.parts[0].positions, model.parts[0].indices, model.parts[0].uvs, png=png_rgba(2, 2, rgba))])
    assert glb[:4] == b"glTF"


def test_rigid_bone_is_applied_once():
    bones = [
        (-1, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)),
        (0, (0.0, 2.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)),
    ]
    point = _rigid([(0.0, 0.0, 0.0)], bones, 1)[0]
    assert abs(point[0] - 1.0) < 1e-4
    assert abs(point[1] - 2.0) < 1e-4
    assert abs(point[2]) < 1e-4


def test_base_color_prefers_albedo():
    assert base_color_name(["Obj_Rock_A_Nrm", "Obj_Rock_A_Alb"]) == "Obj_Rock_A_Alb"
    assert base_color_name(["Bomb_Leaf_Trs"]) == "Bomb_Leaf_Trs"
    assert base_color_name(["Rock_Nrm"]) == ""
    assert rank_albedo([("_n0", "Tex_0"), ("_a0", "Tex_1")]) == [("_a0", "Tex_1")]
    assert rank_albedo([("_b0", "Blend"), ("_a1", "Dirt"), ("_a0", "Grass")])[0] == ("_a0", "Grass")


def test_bc1_block_decodes_red():
    try:
        import texture2ddecoder  # noqa: F401
    except ImportError:
        return
    block = bytes([0x00, 0xF8, 0x00, 0xE8, 0x00, 0x00, 0x00, 0x00])
    rgba, _note = _decode("bc1", 4, 4, block, 0x1A01)
    assert rgba[0] > 180
    assert rgba[2] < 40
    assert rgba[3] == 255


def test_bc1_punch_through_is_transparent():
    try:
        import texture2ddecoder  # noqa: F401
    except ImportError:
        return
    # color0 <= color1, pixel 0 is index 0 and the other 15 pixels are index 3.
    block = bytes([0x00, 0x00, 0xFF, 0xFF, 0xFC, 0xFF, 0xFF, 0xFF])
    rgba, _note = _decode("bc1", 4, 4, block, 0x1A01)
    assert rgba[3] == 255
    assert rgba[7] == 0
    assert rgba[-1] == 0


def test_astc_ids_and_partial_gob_row():
    from bancwright.formats.bntx import _FORMATS, _repair_astc_partial_gob

    assert _FORMATS[0x3701][1:3] == (10, 8)
    assert _FORMATS[0x3801][1:3] == (10, 10)
    assert _FORMATS[0x3901][1:3] == (12, 10)
    assert _FORMATS[0x3A01][1:3] == (12, 12)
    assert _FORMATS[0x3706][0] == "astc10x8"
    blocks_x, blocks_y, bpp = 2, 9, 16
    linear = bytearray(blocks_x * blocks_y * bpp)
    above = (7 * blocks_x + 1) * bpp
    linear[above : above + bpp] = b"\xab" * bpp
    filled = _repair_astc_partial_gob(bytes(linear), 8, 36, 4, 4)
    tail = (8 * blocks_x + 1) * bpp
    assert filled[tail : tail + bpp] == b"\xab" * bpp
    even = (8 * blocks_x) * bpp
    assert filled[even : even + bpp] == b"\0" * bpp


def test_block_height_uses_layout_low_bits_and_mip_size():
    from bancwright.formats.bntx import textures, write_rgba

    width, height = 20, 20
    rgba = bytearray([40, 40, 40, 255] * width * height)
    for y in range(height):
        rgba[(y * width) * 4 : (y * width) * 4 + 4] = bytes((220, 10, 10, 255))
        rgba[(y * width + width - 1) * 4 : (y * width + width - 1) * 4 + 4] = bytes((10, 10, 220, 255))
    stored = write_rgba("Edge", width, height, bytes(rgba), swizzled=True, log2=0, layout0=0, layout4=0xC9)
    image = textures(stored)[0]
    assert image.block_height == 1
    assert image.rgba[0] > 180 and image.rgba[2] < 40
    assert image.rgba[(width - 1) * 4 + 2] > 180
    # A stored log2 that disagrees with the mip size is replaced by the size.
    overridden = write_rgba("Edge", width, height, bytes(rgba), swizzled=True, log2=0, layout0=4, layout4=0xC9)
    fixed = textures(overridden)[0]
    assert fixed.block_height == 1
    assert fixed.rgba[0] > 180
    assert fixed.rgba[(width - 1) * 4 + 2] > 180


def test_fixture_models_cache_outside_the_repo(tmp_path, monkeypatch):
    cache = tmp_path / "model-cache"
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(cache))
    dump = Dump(build_fixture(tmp_path / "dump"))
    fado = describe_actor(dump, "Npc_Kokiri_F_01")
    assert fado["status"] == "mesh"
    assert fado["textures"] >= 1
    assert fado["triangles"] >= 1
    assert fado["source"].endswith("Model/Npc_Kokiri_F.Npc_Kokiri_F_01.bfres.zs")
    assert "untextured" not in (fado["reason"] or "")
    glb = cache_dir() / fado["digest"][:2] / f"{fado['digest']}.glb"
    assert glb.is_file()
    assert glb.read_bytes()[:4] == b"glTF"
    assert "repos" not in str(glb)
    repo = Path(__file__).resolve().parents[1]
    assert not str(glb).startswith(str(repo))

    tree = describe_actor(dump, "Obj_Tree")
    assert tree["status"] == "mesh"
    assert tree["source"].endswith("Model/Obj_Tree.Obj_Tree.bfres.zs")
    assert tree["textures"] >= 1
    again = describe_actor(dump, "Obj_Tree")
    assert again["digest"] == tree["digest"]

    baba = describe_actor(dump, "Enemy_DekuBaba")
    assert baba["status"] == "box"
    assert baba["reason"]
    assert "BFRES" in baba["reason"] or "bfres" in baba["reason"].lower() or "not a BFRES" in baba["reason"]

    missing = describe_actor(dump, "Npc_Kokiri_M_01")
    assert missing["status"] == "box"
    assert "No model" in missing["reason"]
    assert "Model/Npc_Kokiri_M_01.Npc_Kokiri_M_01.bfres.zs" in missing["reason"]

    assert describe_actor(dump, "../etc/passwd")["status"] == "box"

    terrain = describe_terrain(dump, KOKIRI_STATIC)
    assert terrain["status"] == "mesh"
    assert terrain["source"].endswith("Model/KokiriForest_01.KokiriForest_01.bfres.zs")
    assert terrain["textures"] >= 1
    assert describe_terrain(dump, "Banc/HyruleField/HyruleField_Adult_Static.bcett.byml.zs")["status"] == "none"
    assert os.environ["BANCWRIGHT_CACHE"] == str(cache)


def test_volume_flags():
    area = view_actor({"Gyaml": "Area_Kokiri_Town", "Hash": 1, "Translate": [0, 0, 0], "Scale": [30, 8, 30]}, "vanilla")
    assert area["category"] == "area"
    assert area["volume"] is True
    start = view_actor({"Gyaml": "StartPos", "Hash": 2, "Translate": [0, 0, 0]}, "vanilla")
    assert start["category"] == "system"
    assert start["volume"] is True
    ground = view_actor({"Gyaml": "Land_Kokiri_Ground", "Hash": 3, "Translate": [0, 0, 0], "Scale": [20, 1, 20]}, "vanilla")
    assert ground["volume"] is True
    pot = view_actor({"Gyaml": "Obj_Pot", "Hash": 4, "Translate": [0, 0, 0]}, "vanilla")
    assert pot["volume"] is False
    fog = view_actor({"Gyaml": "VolumeFogArea", "Hash": 5, "Translate": [0, 0, 0], "Scale": [100, 40, 100]}, "vanilla")
    assert fog["volume"] is True
    huge = view_actor({"Gyaml": "Obj_Pot", "Hash": 6, "Translate": [0, 0, 0], "Scale": [30, 1, 1]}, "vanilla")
    assert huge["volume"] is True
    assert is_volume("Obj_TriggerBox", [1, 1, 1]) is True
    assert is_volume("SensorTag", [1, 1, 1]) is True


def test_modelinfo_names_the_real_bfres_and_tex(tmp_path, monkeypatch):
    """The path a 1.1.1 dump actually uses: Model/<Project>.<Fmdb>.bfres.zs plus Tex/."""
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bntx import write_rgba
    from bancwright.formats.byml import dumps
    from bancwright.formats.sarc import pack
    from bancwright.formats.zsio import compress

    dump = Dump(build_fixture(tmp_path / "dump"))
    gyaml = "Obj_LiftRock_A_01"
    info = dumps(
        {
            "FmdbName": gyaml,
            "ModelProjectName": "Obj_Rock_A",
            "SubModels": [{"FmdbName": "Obj_Rock_A_Extra", "ModelProjectName": "Obj_Rock_A"}],
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
    # The old resolver stopped at Model/<Project>.bfres.zs. That file must not win.
    decoy = sample_bfres("decoy", [(0, 0, 0), (1, 0, 0), (0, 2, 0)], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], rgba, 2, 2)
    (dump.romfs / "Model" / "Obj_Rock_A.bfres.zs").write_bytes(compress(decoy, 1, dicts, level=3))
    (dump.romfs / "Model" / "Obj_Rock_A.Skip.anim.bfres.zs").write_bytes(b"not a model")
    image = write_rgba("Obj_Rock_A_Alb", 2, 2, rgba, swizzled=True)
    (dump.romfs / "Tex" / "Obj_Rock_A_Alb.bntx.zs").write_bytes(compress(image, 1, dicts, level=3))

    resolved = describe_actor(dump, gyaml)
    assert resolved["status"] == "mesh"
    assert resolved["source"].endswith("Model/Obj_Rock_A.Obj_LiftRock_A_01.bfres.zs")
    assert resolved["triangles"] == 1
    assert resolved["textures"] == 1
    assert "untextured" not in (resolved["reason"] or "")

    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    (dump.romfs / "Model" / "Obj_Rock_A.Obj_LiftRock_A_01.bfres.zs").unlink()
    other = sample_bfres(
        "other",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        rgba,
        2,
        2,
        texture_name="Obj_Rock_A_Alb",
        embed_texture=False,
    )
    (dump.romfs / "Model" / "Obj_Rock_A.Other.bfres.zs").write_bytes(compress(other, 1, dicts, level=3))
    fallback = describe_actor(dump, gyaml)
    assert fallback["status"] == "mesh"
    assert fallback["source"].endswith("Model/Obj_Rock_A.Other.bfres.zs")


def test_old_single_name_model_still_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.zsio import compress

    dump = Dump(build_fixture(tmp_path / "dump"))
    rgba = bytes([20, 80, 200, 255]) * 4
    mesh = sample_bfres("Mido", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], rgba, 2, 2)
    (dump.romfs / "Model" / "Npc_Kokiri_M_01.bfres.zs").write_bytes(compress(mesh, 1, dump.dicts(), level=3))
    info = describe_actor(dump, "Npc_Kokiri_M_01")
    assert info["status"] == "mesh"
    assert info["source"].endswith("Model/Npc_Kokiri_M_01.bfres.zs")
    assert info["textures"] >= 1


def test_spirit_stone_is_a_read_only_ghost():
    from bancwright.project import Project
    from bancwright.recipes import recipe_actors

    project = Project(Path(__file__).resolve().parents[1] / "examples" / "after-the-ocarina")
    stones = recipe_actors(project, KOKIRI_STATIC)
    assert [item["name"] for item in stones] == ["AtO_SpiritStone_Forest"]
    stone = stones[0]
    assert stone["gyaml"] == "Obj_TimeTemple_SpiritStone_A_01"
    assert stone["translate"] == [84.0, 120.3, -27.0]
    assert stone["status"] == "recipe"
    assert stone["recipe"]["file"] == "scenes/m6-stones.yaml"
    assert stone["recipe"]["note"] == "added by build, flag-gated"
    assert stone["recipe"]["flag"] == "HavePlayedEvent.TitleEventBase"
    assert all(item["gyaml"] != "Npc_Kokiri_Saria_Child" for item in stones)
    fire = recipe_actors(project, "Banc/GoronCity/GoronCity_Child_Static.bcett.byml.zs")
    assert fire[0]["name"] == "AtO_SpiritStone_Fire"
    assert abs(fire[0]["rotate"][1] + 1.8) < 1e-6


def test_version_10_reads_every_material_and_skips_runtime_names(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.bntx import write_rgba
    from bancwright.formats.byml import dumps
    from bancwright.formats.sarc import pack
    from bancwright.formats.zsio import compress

    bark = [
        ("_a0", "Tree_KokiriBark_A_Alb"),
        ("_a1", "Tree_KokiriHouse_A_Alb"),
        ("_n0", "Tree_KokiriBark_A_Nrm"),
        ("_n1", "Tree_KokiriHouse_A_Nrm"),
        ("_rgh0", "Tree_KokiriBark_A_Rgh"),
        ("_rgh1", "Tree_KokiriHouse_A_Rgh"),
        ("bake0", "CmnTex_BakeDefault"),
        ("bake1", "CmnTex_BakeDefault"),
    ]
    data = sample_v10_bfres(
        "OuterTree",
        [
            {"name": "Mt_Tree_KokiriBarkBlend_A", "samplers": bark, "mode": "opaque", "cull": "both"},
            {"name": "Mt_TerraMaterial_A", "samplers": [("tc", "MaterialAlb"), ("ta", "MaterialCmb")], "mode": "opaque", "cull": "front"},
        ],
        shapes=[{"name": "Bark", "material": 0}, {"name": "Water", "material": 1}],
        pool_name="Pool_Alb",
    )
    model = parse(data)[0]
    assert [item.name for item in model.materials] == ["Mt_Tree_KokiriBarkBlend_A", "Mt_TerraMaterial_A"]
    assert model.materials[0].albedo[0] == ("_a0", "Tree_KokiriBark_A_Alb")
    assert model.materials[0].albedo[1][0] == "_a1"
    assert model.materials[1].albedo == []
    assert [part.material_index for part in model.parts] == [0, 1]
    assert model.parts[0].texture == "Tree_KokiriBark_A_Alb"
    assert model.parts[1].texture == ""
    assert "Pool_Alb" not in {part.texture for part in model.parts}
    assert rank_albedo([("tc", "MaterialAlb"), ("ta", "MaterialCmb")]) == []
    assert rank_albedo([("_a3", "Extra"), ("_a0", "Main")]) == [("_a0", "Main")]

    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    gyaml = "Obj_Tree"
    info = dumps({"FmdbName": gyaml, "ModelProjectName": "Obj_Tree"})
    archive = pack(
        {
            f"Actor/{gyaml}.engine__actor__ActorParam.bgyml": dumps({"Name": gyaml}),
            f"Component/ModelInfo/{gyaml}.engine__component__ModelInfo.bgyml": info,
        }
    )
    (dump.romfs / "Pack" / "Actor" / f"{gyaml}.pack.zs").write_bytes(compress(archive, 4, dicts, level=3))
    (dump.romfs / "Model" / "Obj_Tree.Obj_Tree.bfres.zs").write_bytes(compress(data, 1, dicts, level=3))
    green = bytes([20, 90, 30, 255]) * 4
    magenta = bytes([255, 0, 255, 255]) * 4
    (dump.romfs / "Tex" / "Tree_KokiriBark_A_Alb.bntx.zs").write_bytes(compress(write_rgba("Tree_KokiriBark_A_Alb", 2, 2, green), 1, dicts, level=3))
    (dump.romfs / "Tex" / "MaterialAlb.bntx.zs").write_bytes(compress(write_rgba("MaterialAlb", 2, 2, magenta), 1, dicts, level=3))
    (dump.romfs / "Tex" / "Pool_Alb.bntx.zs").write_bytes(compress(write_rgba("Pool_Alb", 2, 2, magenta), 1, dicts, level=3))
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    resolved = describe_actor(dump, gyaml)
    assert resolved["textures"] == 1
    by_name = {row["material"]: row for row in resolved["textureLog"]}
    assert by_name["Mt_Tree_KokiriBarkBlend_A"]["wanted"] == "Tree_KokiriBark_A_Alb"
    assert by_name["Mt_Tree_KokiriBarkBlend_A"]["sampler"] == "_a0"
    assert by_name["Mt_TerraMaterial_A"]["wanted"] == ""
    assert "string pool" not in (by_name["Mt_TerraMaterial_A"].get("error") or "")
    assert "MaterialAlb" not in (by_name["Mt_TerraMaterial_A"].get("wanted") or "")
    assert by_name["Mt_Tree_KokiriBarkBlend_A"]["textured"] is True
    assert by_name["Mt_Tree_KokiriBarkBlend_A"]["shapes"] == ["Bark"]
    assert by_name["Mt_TerraMaterial_A"]["flat"] is True
    assert by_name["Mt_TerraMaterial_A"]["shapes"] == ["Water"]


def test_v10_render_state_drops_the_opaque_twin():
    from bancwright.formats.bfres import sample_v10_bfres

    data = sample_v10_bfres(
        "FarView",
        [
            {
                "name": "Mt_Tree_Kokiri_FarView_A",
                "samplers": [("_a0", "Tree_Kokiri_FarView_A_Alb")],
                "mode": "opaque",
                "cull": "front",
            },
            {
                "name": "Mt_Tree_Kokiri_FarView_A_Shaking",
                "samplers": [("_a0", "Tree_Kokiri_FarView_A_Alb")],
                "mode": "mask",
                "cull": "both",
            },
        ],
        shapes=[
            {"name": "FarView_A_03", "material": 0},
            {"name": "FarView_A_03_Shaking", "material": 1},
        ],
    )
    model = parse(data)[0]
    assert len(model.parts) == 1
    part = model.parts[0]
    assert part.name == "FarView_A_03_Shaking"
    assert part.alpha == "mask"
    assert part.double_sided is True
    assert part.texture == "Tree_Kokiri_FarView_A_Alb"
    shaking = model.materials[1]
    assert shaking.render_state == "mask"
    assert shaking.alpha == "mask"
    assert model.materials[0].render_state == "opaque"
    assert model.materials[0].double_sided is False


def test_dummy_terrain_is_an_earth_green_proxy(tmp_path, monkeypatch):
    import json
    import struct

    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.bntx import write_rgba
    from bancwright.formats.zsio import compress

    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    data = sample_v10_bfres(
        "KokiriForest_01",
        [{"name": "dummy_material", "samplers": []}],
        shapes=[{"name": "Ground", "material": 0}],
        with_uvs=False,
        pool_name="KokiriForest_01_Alb",
    )
    parsed = parse(data)[0]
    assert parsed.parts[0].uvs in (None, [])
    assert parsed.parts[0].texture == ""
    relative = dump.romfs / "Model" / "KokiriForest_01.KokiriForest_01.bfres.zs"
    relative.write_bytes(compress(data, 1, dicts, level=3))
    green = bytes([20, 90, 30, 255]) * 4
    (dump.romfs / "Tex" / "KokiriForest_01_Alb.bntx.zs").write_bytes(
        compress(write_rgba("KokiriForest_01_Alb", 2, 2, green), 1, dicts, level=3)
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    info = describe_terrain(dump, KOKIRI_STATIC)
    assert info["status"] == "mesh"
    assert info["textures"] == 0
    assert "terrain proxy" in (info.get("reason") or "")
    assert info["textureLog"][0]["error"] == "terrain proxy"
    assert info["textureLog"][0]["wanted"] == ""
    glb = (tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes()
    document = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    factor = document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert factor[1] > factor[0] and factor[1] > factor[2]
    assert abs(factor[1] - 0.45) < 1e-6
    assert "baseColorTexture" not in document["materials"][0]["pbrMetallicRoughness"]
    primitive = document["meshes"][0]["primitives"][0]
    assert "NORMAL" in primitive["attributes"]
    normal_view = document["bufferViews"][document["accessors"][primitive["attributes"]["NORMAL"]]["bufferView"]]
    start = normal_view["byteOffset"]
    nx, ny, nz = struct.unpack_from("<3f", glb, 20 + struct.unpack_from("<I", glb, 12)[0] + 8 + start)
    assert abs(nz) > 0.9


def test_heightmap_beats_the_proxy_and_uses_a_material_layer(tmp_path, monkeypatch):
    import json
    import struct

    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.bntx import write_rgba
    from bancwright.formats.byml import dumps
    from bancwright.formats.terrain import SIZE, level_stems
    from bancwright.formats.zsio import compress
    from bancwright.models import CacheMiss

    assert level_stems(KOKIRI_STATIC)[0] == "KokiriForest_Child"
    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    proxy = sample_v10_bfres(
        "KokiriForest_01",
        [{"name": "dummy_material", "samplers": []}],
        shapes=[{"name": "Ground", "material": 0}],
        with_uvs=False,
    )
    (dump.romfs / "Model" / "KokiriForest_01.KokiriForest_01.bfres.zs").write_bytes(compress(proxy, 1, dicts, level=3))
    parent = dumps(
        {
            "TerrainSceneName": "KokiriField",
            "TerrainScenePosition": {"X": 0.0, "Y": 2.0, "Z": 0.0},
        }
    )
    child = dumps(
        {"$parent": "Work/Banc/GameBancParam/KokiriForest_Base.game__banc__GameBancParam.gyml"}
    )
    param = dump.romfs / "Banc" / "GameBancParam"
    param.mkdir(parents=True)
    (param / "KokiriForest_Base.game__banc__GameBancParam.bgyml").write_bytes(parent)
    (param / "KokiriForest_Child.game__banc__GameBancParam.bgyml").write_bytes(child)
    heights = [0.4] * (SIZE * SIZE)
    layers = bytes([1]) * (SIZE * SIZE)
    folder = dump.romfs / "TerrainArc" / "KokiriField"
    folder.mkdir(parents=True)
    (dump.romfs / "TerrainArc" / "KokiriField.tscb").write_bytes(
        _tscb(1.0, 10.0, [(0.0, 0.0, 16.0, "Tile_00"), (8000.0, 8000.0, 20.0, "Far_00")])
    )
    (folder / "Tile_00.hght").write_bytes(_hght(heights, layers))
    green = bytes([20, 140, 40, 255]) * 4
    red = bytes([220, 20, 20, 255]) * 4
    (dump.romfs / "Tex" / "MaterialAlb.bntx.zs").write_bytes(
        compress(_array_bntx("MaterialAlb", [green, red], 2, 2), 1, dicts, level=3)
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    try:
        describe_terrain(dump, KOKIRI_STATIC, convert=False)
    except CacheMiss:
        pass
    else:
        raise AssertionError("a cold heightmap should miss the cache")
    info = describe_terrain(dump, KOKIRI_STATIC)
    assert info["status"] == "mesh"
    assert info["source"].endswith("TerrainArc/KokiriField.tscb")
    assert "terrain heightmap" in info["reason"]
    assert "terrain proxy" not in info["reason"]
    assert "MaterialAlb layers 1" in info["reason"]
    assert info["textures"] == 1
    assert info["terrain"]["origin"] == [0.0, 2.0, 0.0]
    drawn = [row for row in info["tiles"] if row["status"] == "drawn"]
    skipped = [row for row in info["tiles"] if row["status"] != "drawn"]
    assert [row["name"] for row in drawn] == ["Tile_00"]
    assert drawn[0]["layers"] == [1]
    assert "material index" in drawn[0]["reason"]
    assert any(row["name"] == "Far_00" and "outside" in row["reason"] for row in skipped)
    again = describe_terrain(dump, KOKIRI_STATIC)
    assert again["digest"] == info["digest"]
    glb = (tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes()
    document, blob = _glb_parts(glb)
    factor = document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert factor[0] == 1.0 and factor[1] == 1.0
    assert "baseColorTexture" in document["materials"][0]["pbrMetallicRoughness"]
    primitive = document["meshes"][0]["primitives"][0]
    positions = _accessor_floats(document, blob, primitive["attributes"]["POSITION"], 3)
    assert min(point[0] for point in positions) < -7
    assert max(point[0] for point in positions) > 7
    assert abs(positions[0][1] - 6.0) < 1e-3
    normals = _accessor_floats(document, blob, primitive["attributes"]["NORMAL"], 3)
    assert normals[0][1] > 0.9
    image = document["images"][0]
    view = document["bufferViews"][image["bufferView"]]
    png = blob[view["byteOffset"] : view["byteOffset"] + view["byteLength"]]
    from io import BytesIO

    from PIL import Image

    pixel = Image.open(BytesIO(png)).getpixel((0, 0))
    assert pixel[0] > 200 and pixel[1] < 40


def test_heightmap_without_an_index_is_earth_and_a_failed_tscb_is_not_the_proxy(tmp_path, monkeypatch):
    import struct

    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.terrain import SIZE
    from bancwright.formats.zsio import compress

    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    proxy = sample_v10_bfres(
        "KokiriForest_01",
        [{"name": "dummy_material", "samplers": []}],
        shapes=[{"name": "Ground", "material": 0}],
        with_uvs=False,
    )
    (dump.romfs / "Model" / "KokiriForest_01.KokiriForest_01.bfres.zs").write_bytes(compress(proxy, 1, dicts, level=3))
    folder = dump.romfs / "TerrainArc" / "KokiriForest"
    folder.mkdir(parents=True)
    (dump.romfs / "TerrainArc" / "KokiriForest.tscb").write_bytes(_tscb(1.0, 4.0, [(0.0, 0.0, 16.0, "Flat_00")]))
    (folder / "Flat_00.hght").write_bytes(_hght([0.5] * (SIZE * SIZE)))
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    info = describe_terrain(dump, KOKIRI_STATIC)
    assert info["status"] == "mesh"
    assert "no material index" in info["reason"]
    assert "terrain proxy" not in info["reason"]
    assert info["textures"] == 0
    glb = (tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes()
    document, _blob = _glb_parts(glb)
    factor = document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert abs(factor[0] - 0.40) < 1e-6
    assert abs(factor[1] - 0.36) < 1e-6
    flat = folder / "Flat_00.hght"
    flat.write_bytes(_hght([0.0] * (SIZE * SIZE)))
    stamp = flat.stat()
    os.utime(flat, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 2_000_000_000))
    model_mod._FILE_SIG.clear()
    empty = describe_terrain(dump, KOKIRI_STATIC)
    assert empty["status"] == "none"
    assert "terrain proxy" not in (empty.get("reason") or "")
    assert any("height is 0" in (row.get("reason") or "") for row in empty["tiles"])


def test_swizzled_heights_round_trip():
    import struct

    from bancwright.formats.swizzle import swizzle
    from bancwright.formats.terrain import SIZE, read_height_file

    heights = [0.5] * (SIZE * SIZE)
    heights[20 * SIZE + 10] = 0.25
    linear = _f32(heights)
    packed = swizzle(SIZE, SIZE, 1, 1, 4, 0, 4, linear)
    header = bytearray(0x40)
    struct.pack_into("<I", header, 0x2C, 0x40 - 0x2C)
    struct.pack_into("<I", header, 0x30, len(packed))
    decoded, layers, note = read_height_file(bytes(header) + packed)
    assert layers is None
    assert "no material index" in note
    assert abs(decoded[20 * SIZE + 10] - 0.25) < 1e-5
    assert abs(decoded[0] - 0.5) < 1e-5


def test_a_finer_tile_punches_the_coarser_one():
    from bancwright.formats.terrain import SIZE, build_scene

    coarse = [0.5] * (SIZE * SIZE)
    fine = [0.8] * (SIZE * SIZE)
    both, reports = build_scene(
        [
            (0.0, 0.0, 32.0, "Coarse", coarse, None),
            (0.0, 0.0, 8.0, "Fine", fine, None),
        ],
        1.0,
        10.0,
    )
    alone, _alone_reports = build_scene([(0.0, 0.0, 32.0, "Coarse", coarse, None)], 1.0, 10.0)
    assert all(row.status == "drawn" for row in reports)
    coarse_tris = next(row.triangles for row in reports if row.name == "Coarse")
    assert 0 < coarse_tris < alone[0].indices.__len__() // 3
    assert sum(len(part.indices) // 3 for part in both) > coarse_tris


def _tscb(scale: float, height_scale: float, tiles: list[tuple]) -> bytes:
    import struct

    blob = bytearray(0x80 + len(tiles) * 4)
    blob[:4] = b"TSCB"
    struct.pack_into("<2f", blob, 0x10, scale, height_scale)
    struct.pack_into("<I", blob, 0x38, len(tiles))
    struct.pack_into("<I", blob, 0x78, 8)
    for index, (center_x, center_z, edge, name) in enumerate(tiles):
        slot = 0x80 + index * 4
        body = len(blob)
        struct.pack_into("<I", blob, slot, body - slot)
        blob.extend(b"\0" * 0x64)
        struct.pack_into("<3f", blob, body, center_x, center_z, edge)
        name_at = len(blob)
        struct.pack_into("<I", blob, body + 0x60, name_at - (body + 0x60))
        blob.extend(str(name).encode("ascii") + b"\0")
    return bytes(blob)


def _f32(heights: list[float]) -> bytes:
    import struct

    return b"".join(
        struct.pack(f"<{len(piece)}f", *piece)
        for start in range(0, len(heights), 1024)
        for piece in (heights[start : start + 1024],)
    )


def _hght(heights: list[float], layers: bytes | None = None) -> bytes:
    import struct

    raw = _f32(heights)
    header = bytearray(0x40)
    blob = header + bytearray(raw)
    struct.pack_into("<I", blob, 0x2C, 0x40 - 0x2C)
    struct.pack_into("<I", blob, 0x30, len(raw))
    if layers is not None:
        start = len(blob)
        blob.extend(layers)
        struct.pack_into("<I", blob, 0x1C, start - 0x1C)
        struct.pack_into("<I", blob, 0x20, len(layers))
    from bancwright.formats.zsio import compress

    frame = compress(bytes(blob), 0, None, level=1)
    wrapped = bytearray(16)
    wrapped[:4] = b"TARB"
    struct.pack_into("<H", wrapped, 10, 16)
    return bytes(wrapped) + frame


def _array_bntx(name: str, layers: list[bytes], width: int, height: int) -> bytes:
    import struct

    from bancwright.formats.bntx import write_rgba

    blob = bytearray(write_rgba(name, width, height, layers[0]))
    layer_bytes = width * height * 4
    assert bytes(blob[-layer_bytes:]) == layers[0][:layer_bytes]
    for extra in layers[1:]:
        blob.extend(extra[:layer_bytes])
    at = blob.find(b"BRTI")
    struct.pack_into("<I", blob, at + 0x30, len(layers))
    struct.pack_into("<I", blob, at + 0x50, layer_bytes * len(layers))
    return bytes(blob)


def _glb_parts(glb: bytes):
    import json
    import struct

    json_len = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + json_len])
    return document, glb[20 + json_len + 8 :]


def _accessor_floats(document, blob: bytes, index: int, width: int):
    import struct

    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    start = view["byteOffset"]
    count = accessor["count"]
    return [struct.unpack_from(f"<{width}f", blob, start + item * width * 4) for item in range(count)]


def test_alb_suffix_and_bundle_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bntx import write_rgba
    from bancwright.formats.byml import dumps
    from bancwright.formats.sarc import pack
    from bancwright.formats.zsio import compress

    dump = Dump(build_fixture(tmp_path / "dump"))
    gyaml = "Obj_Tree"
    info = dumps({"FmdbName": gyaml, "ModelProjectName": "Obj_Tree"})
    archive = pack(
        {
            f"Actor/{gyaml}.engine__actor__ActorParam.bgyml": dumps({"Name": gyaml}),
            f"Component/ModelInfo/{gyaml}.engine__component__ModelInfo.bgyml": info,
        }
    )
    dicts = dump.dicts()
    (dump.romfs / "Pack" / "Actor" / f"{gyaml}.pack.zs").write_bytes(compress(archive, 4, dicts, level=3))
    rgba = bytes([10, 140, 40, 255]) * 4
    mesh = sample_bfres(
        gyaml,
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        rgba,
        2,
        2,
        texture_name="Bark",
        embed_texture=False,
    )
    (dump.romfs / "Model" / "Obj_Tree.Obj_Tree.bfres.zs").write_bytes(compress(mesh, 1, dicts, level=3))
    image = write_rgba("Bark_Alb", 2, 2, rgba, swizzled=True)
    (dump.romfs / "Tex" / "Bark_Alb.bntx.zs").write_bytes(compress(image, 1, dicts, level=3))
    resolved = describe_actor(dump, gyaml)
    assert resolved["textures"] == 1
    log = resolved["textureLog"]
    assert log[0]["wanted"] == "Bark"
    assert log[0]["decoded"] is True
    assert log[0]["found"].endswith("Bark_Alb.bntx.zs")
    assert "deswizzled" in (log[0]["swizzle"] or "")


def test_recipe_edits_round_trip_and_keep_comments(tmp_path):
    import shutil

    from bancwright.changes import scene_changes
    from bancwright.dump import Dump
    from bancwright.fixtures import build_fixture
    from bancwright.project import Project
    from bancwright.recipes import apply_recipe_command, recipe_actors

    source = Path(__file__).resolve().parents[1] / "examples" / "after-the-ocarina"
    root = tmp_path / "project"
    shutil.copytree(source, root)
    project = Project(root)
    dump = Dump(build_fixture(tmp_path / "dump"))
    ident = recipe_actors(project, KOKIRI_STATIC)[0]["id"]
    focus = apply_recipe_command(
        project,
        KOKIRI_STATIC,
        {"command": "transform", "id": ident, "translate": [90.0, 121.0, -27.0], "rotate": [0.0, 0.5, 0.0], "scale": [1.0, 1.0, 1.0]},
    )
    assert focus == ident
    text = (root / "scenes" / "m6-stones.yaml").read_text(encoding="utf-8")
    assert "In front of the Deku Tree" in text
    assert "Display Spiritual Stones" in text
    assert "90.0" in text or "90" in text
    moved = recipe_actors(project, KOKIRI_STATIC)[0]
    assert moved["translate"][0] == 90.0
    assert abs(moved["translate"][1] - 121.0) < 1e-6
    fire = recipe_actors(project, "Banc/GoronCity/GoronCity_Child_Static.bcett.byml.zs")[0]
    assert abs(fire["rotate"][1] + 1.8) < 1e-6
    report = scene_changes(dump, project, KOKIRI_STATIC)
    moved_rows = [entry for group in report["groups"] for entry in group["entries"] if entry["kind"] == "moved" and entry["source"] == "recipe"]
    assert moved_rows and moved_rows[0]["revertable"] is True
    apply_recipe_command(project, KOKIRI_STATIC, {"command": "revert_part", "id": ident, "part": "translate", "path": ""})
    restored = recipe_actors(project, KOKIRI_STATIC)[0]
    assert restored["translate"] == [84.0, 120.3, -27.0]
    dup = apply_recipe_command(project, KOKIRI_STATIC, {"command": "duplicate", "id": ident})
    names = [item["name"] for item in recipe_actors(project, KOKIRI_STATIC)]
    assert "AtO_SpiritStone_Forest_2" in names
    apply_recipe_command(project, KOKIRI_STATIC, {"command": "remove", "id": dup})
    assert [item["name"] for item in recipe_actors(project, KOKIRI_STATIC)] == ["AtO_SpiritStone_Forest"]
    assert "In front of the Deku Tree" in (root / "scenes" / "m6-stones.yaml").read_text(encoding="utf-8")


def test_alpha_and_sampler_role_come_from_the_material():
    rgba = bytes([20, 140, 40, 255]) * 4
    data = sample_bfres(
        "Leaf",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        rgba,
        2,
        2,
        samplers=[("_n0", "Leaf_Nrm"), ("_a0", "Leaf_Alb")],
        alpha="mask",
        cutoff=0.35,
        display_face="front",
        embed_texture=False,
    )
    part = parse(data)[0].parts[0]
    assert part.texture == "Leaf_Alb"
    assert part.albedo_sampler == "_a0"
    assert part.alpha == "mask"
    assert abs(part.cutoff - 0.35) < 1e-4
    assert part.double_sided is False
    assert ("_n0", "Leaf_Nrm") in part.binds


def test_untextured_mesh_is_flat_color_and_cutout_is_masked(tmp_path, monkeypatch):
    import json
    import struct

    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bntx import write_rgba
    from bancwright.formats.byml import dumps
    from bancwright.formats.gltf import Primitive, to_glb
    from bancwright.formats.sarc import pack
    from bancwright.formats.zsio import compress
    from bancwright.models import pick_image
    from bancwright.formats.bntx import Texture

    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    gyaml = "Obj_Tree"
    info = dumps({"FmdbName": gyaml, "ModelProjectName": "Obj_Tree"})
    archive = pack(
        {
            f"Actor/{gyaml}.engine__actor__ActorParam.bgyml": dumps({"Name": gyaml}),
            f"Component/ModelInfo/{gyaml}.engine__component__ModelInfo.bgyml": info,
        }
    )
    (dump.romfs / "Pack" / "Actor" / f"{gyaml}.pack.zs").write_bytes(compress(archive, 4, dicts, level=3))
    green = bytes([10, 140, 40, 255]) * 4
    blue = bytes([20, 40, 220, 255]) * 4
    mesh = sample_bfres(
        gyaml,
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        green,
        2,
        2,
        samplers=[("_n0", "FlatBlue"), ("_a0", "LeafGreen")],
        alpha="mask",
        cutoff=0.4,
        embed_texture=False,
    )
    (dump.romfs / "Model" / "Obj_Tree.Obj_Tree.bfres.zs").write_bytes(compress(mesh, 1, dicts, level=3))
    (dump.romfs / "Tex" / "FlatBlue.bntx.zs").write_bytes(compress(write_rgba("FlatBlue", 2, 2, blue), 1, dicts, level=3))
    (dump.romfs / "Tex" / "LeafGreen.bntx.zs").write_bytes(compress(write_rgba("LeafGreen", 2, 2, green), 1, dicts, level=3))
    resolved = describe_actor(dump, gyaml)
    assert resolved["textures"] == 1
    row = resolved["textureLog"][0]
    assert row["wanted"] == "LeafGreen"
    assert row["sampler"] == "_a0"
    assert row["decoded"] is True
    assert row["alpha"].startswith("mask")
    assert row["doubleSided"] is True
    glb = (tmp_path / "cache" / resolved["digest"][:2] / f"{resolved['digest']}.glb").read_bytes()
    document = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    assert document["materials"][0]["alphaMode"] == "MASK"
    assert document["materials"][0]["alphaCutoff"] == 0.4

    magenta = bytes([255, 0, 255, 255]) * 4
    missing = sample_bfres(
        "Missing",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        magenta,
        2,
        2,
        texture_name="MissingPaint",
        embed_texture=False,
    )
    (dump.romfs / "Model" / "Obj_Tree.Obj_Tree.bfres.zs").write_bytes(compress(missing, 1, dicts, level=3))
    (dump.romfs / "Tex" / "MissingPaint.bntx.zs").write_bytes(compress(write_rgba("MissingPaint", 2, 2, magenta), 1, dicts, level=3))
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    flat = describe_actor(dump, gyaml)
    assert flat["textures"] == 0
    assert "magenta" in (flat["textureLog"][0].get("error") or "")
    assert "flat colour" in (flat["reason"] or "")
    flat_glb = (tmp_path / "cache" / flat["digest"][:2] / f"{flat['digest']}.glb").read_bytes()
    flat_doc = json.loads(flat_glb[20 : 20 + struct.unpack_from("<I", flat_glb, 12)[0]])
    factor = flat_doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert "baseColorTexture" not in flat_doc["materials"][0]["pbrMetallicRoughness"]
    assert factor[0] < 0.8 and factor[2] < 0.8

    painted = sample_bfres(
        "Ground",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        green,
        2,
        2,
        texture_name="GroundPaint",
        embed_texture=False,
        link_textures=False,
    )
    (dump.romfs / "Model" / "Obj_Tree.Obj_Tree.bfres.zs").write_bytes(compress(painted, 1, dicts, level=3))
    (dump.romfs / "Tex" / "GroundPaint.bntx.zs").write_bytes(compress(write_rgba("GroundPaint", 2, 2, green), 1, dicts, level=3))
    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    pooled = describe_actor(dump, gyaml)
    assert pooled["textures"] == 1
    assert pooled["textureLog"][0]["wanted"] == "GroundPaint"
    assert "string pool" in (pooled["textureLog"][0]["error"] or "")

    noise = Texture("Noise", 1, 1, b"\xff\x00\xff\xff", 1, codec="rgba8")
    normal = Texture("Rock_Nrm", 1, 1, b"\x80\x80\xff\xff", 1, codec="bc5")
    assert pick_image([noise, normal], "Rock_Alb")[0] is None
    assert pick_image([normal, Texture("Rock_Alb", 1, 1, green, 1)], "Rock")[0].name == "Rock_Alb"
    failed = Texture("Rock_Alb", 0, 0, b"", 0x2D07, note="texture format 0x2D07 is not decoded")
    missed, why = pick_image([failed, normal], "Rock_Alb")
    assert missed is None
    assert "0x2D07" in why

    png = b"\x89PNG-fake"
    glb = to_glb(
        [
            Primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], png=png, alpha="mask", cutoff=0.4),
            Primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], None, base_color=(0.2, 0.3, 0.4, 1)),
        ]
    )
    shared = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    assert len(shared["materials"]) == 2
    assert "baseColorTexture" not in shared["materials"][1]["pbrMetallicRoughness"]
    assert shared["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"] == [0.2, 0.3, 0.4, 1]


def test_check_models_script(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    dump = build_fixture(tmp_path / "dump")
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_models.py"
    result = subprocess.run(
        [sys.executable, str(script), str(dump), KOKIRI_STATIC],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Model/KokiriForest_01.KokiriForest_01.bfres.zs" in result.stdout
    assert "Npc_Kokiri_F_01" in result.stdout
    assert "texture " in result.stdout
    assert "decoded" in result.stdout
    assert "material " in result.stdout
    assert "alpha " in result.stdout
    assert "Obj_Tree" in result.stdout
    assert "mesh" in result.stdout
    assert "shapes textured" in result.stdout
    assert "flat fallback" in result.stdout


def test_v10_material_dictionary_is_at_0x40_and_count_cross_checks():
    import struct

    from bancwright.formats.bfres import sample_v10_bfres

    materials = [
        {"name": "Bark", "samplers": [("_a0", "Tree_Alb")], "mode": "mask", "cull": "both"},
        {"name": "Leaf", "samplers": [("_a0", "Leaf_Alb")], "mode": "opaque", "cull": "front"},
    ]
    decoy = bytearray(
        sample_v10_bfres("Tree", materials, shapes=[{"name": "Bark", "material": 0}, {"name": "Leaf", "material": 1}], decoy_dict=True)
    )
    fmdl = decoy.find(b"FMDL")
    assert fmdl > 0
    # A one-name dictionary sits at +0x48. The material count is zero, so a
    # reader that uses that pointer cannot recover the second material.
    struct.pack_into("<H", decoy, fmdl + 0x6C, 0)
    model = parse(bytes(decoy))[0]
    assert [item.name for item in model.materials] == ["Bark", "Leaf"]
    assert model.materials[0].alpha == "mask"
    assert struct.unpack_from("<H", decoy, decoy.find(b"FMAT") + 0xA0)[0] == 1

    counted = bytearray(sample_v10_bfres("Tree", materials))
    fmdl = counted.find(b"FMDL")
    struct.pack_into("<Q", counted, fmdl + 0x40, 0)
    again = parse(bytes(counted))[0]
    assert [item.name for item in again.materials] == ["Bark", "Leaf"]


def test_flag_0x10_exact_file_is_a_bounds_box_not_a_sibling(tmp_path, monkeypatch):
    import struct

    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.byml import dumps
    from bancwright.formats.sarc import pack
    from bancwright.formats.zsio import compress

    outside = bytearray(
        sample_v10_bfres("Outside", [{"name": "Wall", "samplers": []}], shapes=[{"name": "House", "material": 0}])
    )
    shape = outside.find(b"FSHP")
    struct.pack_into("<I", outside, shape + 4, 0x12)
    struct.pack_into("<Q", outside, shape + 0x10, 0)
    outside[shape + 0x5B] = 0
    bounds_at = len(outside)
    outside.extend(struct.pack("<6f", 1.0, 2.0, 3.0, 4.0, 0.5, 2.0))
    struct.pack_into("<Q", outside, shape + 0x38, bounds_at)
    parsed = parse(bytes(outside))[0]
    assert parsed.parts == []
    assert parsed.notes == ["geometry format not decoded (FSHP flag 0x10)"]
    assert parsed.shape_bounds["min"] == [-3.0, 1.5, 1.0]
    assert parsed.shape_bounds["max"] == [5.0, 2.5, 5.0]

    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    gyaml = "Land_Kokiri_House_Outside_C_00"
    info = dumps({"FmdbName": gyaml, "ModelProjectName": "Land_Kokiri_House"})
    archive = pack(
        {
            f"Actor/{gyaml}.engine__actor__ActorParam.bgyml": dumps({"Name": gyaml}),
            f"Component/ModelInfo/{gyaml}.engine__component__ModelInfo.bgyml": info,
        }
    )
    (dump.romfs / "Pack" / "Actor" / f"{gyaml}.pack.zs").write_bytes(compress(archive, 4, dicts, level=3))
    (dump.romfs / "Model" / "Land_Kokiri_House.Land_Kokiri_House_Outside_C_00.bfres.zs").write_bytes(
        compress(bytes(outside), 1, dicts, level=3)
    )
    inside = sample_bfres(
        "Inside",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        bytes([40, 40, 40, 255]) * 4,
        2,
        2,
    )
    (dump.romfs / "Model" / "Land_Kokiri_House.Land_Kokiri_House_Inside_C_03.bfres.zs").write_bytes(
        compress(inside, 1, dicts, level=3)
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    resolved = describe_actor(dump, gyaml)
    assert resolved["status"] == "box"
    assert resolved["reason"] == "geometry format not decoded (FSHP flag 0x10)"
    assert resolved["bounds"]["min"] == [-3.0, 1.5, 1.0]
    assert resolved["bounds"]["max"] == [5.0, 2.5, 5.0]
    assert resolved["source"].endswith("Land_Kokiri_House.Land_Kokiri_House_Outside_C_00.bfres.zs")
    assert "Inside" not in resolved["source"]


def test_only_the_near_lod_is_the_full_mesh_and_images_shrink():
    import json
    import struct

    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.gltf import encode_albedo, limit_rgba, to_glb
    from bancwright.formats.gltf import Primitive

    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)]
    data = sample_v10_bfres(
        "Tree",
        [{"name": "Bark", "samplers": [("_a0", "Bark")]}],
        shapes=[{"name": "Canopy", "material": 0}],
        positions=positions,
        indices=[0, 1, 2, 0, 2, 3],
        far_lod=True,
    )
    model = parse(data)[0]
    near = [part for part in model.parts if part.lod == 0]
    far = [part for part in model.parts if part.lod]
    assert len(near) == 1 and len(far) == 1
    assert len(near[0].indices) == 6
    assert len(far[0].indices) == 3
    assert far[0].name == near[0].name

    wide = bytes([10, 140, 40, 255]) * (8 * 4)
    small_w, small_h, shrunk = limit_rgba(8, 4, wide, 2)
    assert small_w == 2 and small_h == 1 and len(shrunk) == 8
    jpeg, mime = encode_albedo(4, 4, bytes([20, 90, 30, 255]) * 16, "opaque")
    assert mime == "image/jpeg" and jpeg[:2] == b"\xff\xd8"
    cutout, cutout_mime = encode_albedo(2, 2, bytes([20, 90, 30, 10]) * 4, "mask")
    assert cutout_mime == "image/png" and cutout.startswith(b"\x89PNG")

    glb = to_glb(
        [
            Primitive(positions[:3], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], png=jpeg, name="Canopy:0"),
            Primitive(positions[:3], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], png=jpeg, name="Canopy:0", lod=1),
        ]
    )
    document = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    names = [node["name"] for node in document["nodes"]]
    assert names == ["lod0:Canopy:0", "lod1:Canopy:0"]
    assert document["images"][0]["mimeType"] == "image/jpeg"


def test_cluster_lod_shrinks_a_dense_mesh():
    from bancwright.formats.gltf import cluster_lod

    positions = [(float(x), float(y), 0.0) for y in range(40) for x in range(40)]
    indices = []
    for y in range(39):
        for x in range(39):
            corner = y * 40 + x
            indices.extend((corner, corner + 1, corner + 40, corner + 1, corner + 41, corner + 40))
    far = cluster_lod(positions, indices, None)
    assert far is not None
    assert len(far[1]) < len(indices) * 0.5


def _document(glb: bytes) -> dict:
    import json
    import struct

    return json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])


def _install(dump, gyaml: str, mesh: bytes, textures: dict[str, bytes] | None = None) -> None:
    from bancwright.formats.byml import dumps
    from bancwright.formats.sarc import pack
    from bancwright.formats.zsio import compress

    dicts = dump.dicts()
    info = dumps({"FmdbName": gyaml, "ModelProjectName": gyaml})
    archive = pack(
        {
            f"Actor/{gyaml}.engine__actor__ActorParam.bgyml": dumps({"Name": gyaml}),
            f"Component/ModelInfo/{gyaml}.engine__component__ModelInfo.bgyml": info,
        }
    )
    (dump.romfs / "Pack" / "Actor" / f"{gyaml}.pack.zs").write_bytes(compress(archive, 4, dicts, level=3))
    (dump.romfs / "Model" / f"{gyaml}.{gyaml}.bfres.zs").write_bytes(compress(mesh, 1, dicts, level=3))
    for name, blob in (textures or {}).items():
        (dump.romfs / "Tex" / f"{name}.bntx.zs").write_bytes(compress(blob, 1, dicts, level=3))


def test_a_busy_albedo_keeps_the_stored_block_height():
    from bancwright.formats.bntx import textures, write_rgba

    width = height = 64
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            rgba[(y * width + x) * 4 : (y * width + x) * 4 + 4] = bytes((90 + (x * 5 + y * 2) % 70, 50 + (y * 3) % 40, 30, 255))
    stored = write_rgba("Bark", width, height, bytes(rgba), swizzled=True, log2=0, layout0=0, layout4=0)
    image = textures(stored)[0]
    assert image.block_height == 1
    assert "looked like noise" not in (image.note or "")
    assert image.rgba[:3] == bytes((90, 50, 30))


def test_a_normal_named_texture_is_not_albedo_on_a0():
    assert rank_albedo([("_a0", "Field_Water_Nrm"), ("_n0", "Field_Water_N0")]) == []


def test_water_uses_a_real_albedo_or_a_teal_stand_in(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.bntx import write_rgba

    dump = Dump(build_fixture(tmp_path / "dump"))
    green = bytes([20, 140, 60, 255]) * (8 * 8)
    cream = bytes([240, 236, 220, 255]) * (8 * 8)
    normal = bytes([128, 128, 255, 255]) * (8 * 8)
    _install(
        dump,
        "Fld_Water",
        sample_v10_bfres(
            "Fld_Water",
            [{"name": "Mt_Field_Water", "samplers": [("_a0", "Field_Water_Alb"), ("_n0", "Field_Water_Nrm")], "mode": "custom"}],
            shapes=[{"name": "Water", "material": 0}],
        ),
        {"Field_Water_Alb": write_rgba("Field_Water_Alb", 8, 8, green)},
    )
    _install(
        dump,
        "Fld_WaterSheet",
        sample_v10_bfres(
            "Fld_WaterSheet",
            [{"name": "Mt_Lake_Water", "samplers": [("_a0", "Lake_Water_Alb")], "mode": "custom"}],
            shapes=[{"name": "Sheet", "material": 0}],
        ),
        {"Lake_Water_Alb": write_rgba("Lake_Water_Alb", 8, 8, cream)},
    )
    _install(
        dump,
        "Fld_WaterNorm",
        sample_v10_bfres(
            "Fld_WaterNorm",
            [{"name": "Mt_Pond_Water", "samplers": [("_n0", "Pond_Water_Nrm")], "mode": "opaque"}],
            shapes=[{"name": "Pond", "material": 0}],
        ),
    )
    _install(
        dump,
        "Fld_WaterBlue",
        sample_v10_bfres(
            "Fld_WaterBlue",
            [{"name": "Mt_Sea_Water", "samplers": [("_a0", "Sea_Water_Alb")], "mode": "translucent"}],
            shapes=[{"name": "Sea", "material": 0}],
        ),
        {"Sea_Water_Alb": write_rgba("Sea_Water_Alb", 8, 8, normal)},
    )
    _install(
        dump,
        "Npc_River_A",
        sample_v10_bfres(
            "Npc_River_A",
            [{"name": "Mt_Npc_River_A", "samplers": [], "mode": "opaque"}],
            shapes=[{"name": "Body", "material": 0}],
        ),
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()

    real = describe_actor(dump, "Fld_Water")
    assert real["status"] == "mesh"
    assert real["textures"] == 1
    assert "water stand-in" not in (real["reason"] or "")
    assert "unlit translucent" in (real["textureLog"][0].get("error") or "")
    real_doc = _document((tmp_path / "cache" / real["digest"][:2] / f"{real['digest']}.glb").read_bytes())
    real_mat = real_doc["materials"][0]
    assert real_mat["alphaMode"] == "BLEND"
    assert real_mat["extensions"]["KHR_materials_unlit"] == {}
    assert real_mat["extras"]["banc"] == "water"
    assert "baseColorTexture" in real_mat["pbrMetallicRoughness"]

    for gyaml in ("Fld_WaterSheet", "Fld_WaterNorm", "Fld_WaterBlue"):
        info = describe_actor(dump, gyaml)
        assert info["textures"] == 0, gyaml
        assert "water stand-in" in (info["reason"] or ""), gyaml
        assert info["textureLog"][0].get("standIn") == "water"
        doc = _document((tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes())
        mat = doc["materials"][0]
        factor = mat["pbrMetallicRoughness"]["baseColorFactor"]
        assert mat["alphaMode"] == "BLEND"
        assert "baseColorTexture" not in mat["pbrMetallicRoughness"]
        assert factor[1] > factor[0]
        assert factor[2] > factor[0]
        assert factor[3] < 0.8
        assert doc["extensionsUsed"] == ["KHR_materials_unlit"]

    npc = describe_actor(dump, "Npc_River_A")
    assert "water" not in (npc["reason"] or "")
    npc_mat = _document((tmp_path / "cache" / npc["digest"][:2] / f"{npc['digest']}.glb").read_bytes())["materials"][0]
    assert "extensions" not in npc_mat
    assert npc_mat["pbrMetallicRoughness"]["baseColorFactor"][3] == 1


def test_wrong_block_height_that_matches_the_mip_size_is_retried():
    from bancwright.formats.bntx import textures, write_rgba

    width = height = 64
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            pale = x < 40
            rgba[(y * width + x) * 4 : (y * width + x) * 4 + 4] = bytes((240, 230, 210, 255) if pale else (30, 40, 50, 255))
    stored = write_rgba("Sheet", width, height, bytes(rgba), swizzled=True, log2=0, layout0=1)
    image = textures(stored)[0]
    assert image.block_height == 1
    assert "stored 1 looked like noise" in (image.note or "")
    assert image.rgba[0] > 200
    assert image.rgba[(width - 1) * 4] < 50


def test_unlisted_channel_type_uses_the_known_block_family():
    import struct

    from bancwright.formats.bntx import textures, write_rgba

    rgba = bytes([10, 20, 30, 255]) * 16
    stored = bytearray(write_rgba("Flat", 4, 4, rgba))
    at = stored.find(struct.pack("<I", 0x0B01))
    assert at > 0
    stored[at : at + 4] = struct.pack("<I", 0x0B03)
    image = textures(bytes(stored))[0]
    assert image.codec == "rgba8"
    assert image.rgba[:4] == bytes((10, 20, 30, 255))
    assert "0x0B03" in (image.note or "")


def test_warm_cache_does_not_decode_textures_again(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.bntx import write_rgba

    dump = Dump(build_fixture(tmp_path / "dump"))
    green = bytes([12, 90, 40, 255]) * 4
    _install(
        dump,
        "Obj_CacheRock",
        sample_v10_bfres(
            "Obj_CacheRock",
            [{"name": "Rock", "samplers": [("_a0", "CacheRock_Alb")]}],
            shapes=[{"name": "Rock", "material": 0}],
        ),
        {"CacheRock_Alb": write_rgba("CacheRock_Alb", 2, 2, green)},
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    first = describe_actor(dump, "Obj_CacheRock")
    assert first["status"] == "mesh"
    assert first["textures"] == 1
    assert list((tmp_path / "cache").glob("src/*/*.json"))
    calls = {"n": 0}
    real = model_mod.read_bntx

    def counted(blob):
        calls["n"] += 1
        return real(blob)

    monkeypatch.setattr(model_mod, "read_bntx", counted)
    again = describe_actor(dump, "Obj_CacheRock")
    assert again["digest"] == first["digest"]
    assert calls["n"] == 0
    red = bytes([200, 20, 20, 255]) * 4
    from bancwright.formats.zsio import compress
    import os

    path = dump.romfs / "Tex" / "CacheRock_Alb.bntx.zs"
    path.write_bytes(compress(write_rgba("CacheRock_Alb", 2, 2, red), 1, dump.dicts(), level=3))
    stamped = path.stat()
    os.utime(path, ns=(stamped.st_atime_ns, stamped.st_mtime_ns + 2_000_000_000))
    folder = path.parent
    folder_st = folder.stat()
    os.utime(folder, ns=(folder_st.st_atime_ns, folder_st.st_mtime_ns + 2_000_000_000))
    changed = describe_actor(dump, "Obj_CacheRock")
    assert changed["digest"] != first["digest"]
    assert calls["n"] >= 1


def _array_rgba(name: str, colors: list[bytes]) -> bytes:
    """One BNTX whose first BRTI is an array of 2x2 images, one colour per layer."""
    import struct

    from bancwright.formats.bntx import write_rgba

    width = height = 2
    stride = width * height * 4
    blob = bytearray(write_rgba(name, width, height, colors[0] * (width * height), swizzled=False))
    for color in colors[1:]:
        blob.extend(color * (width * height))
    at = blob.find(b"BRTI")
    assert at > 0
    struct.pack_into("<I", blob, at + 0x30, len(colors))
    struct.pack_into("<I", blob, at + 0x50, stride * len(colors))
    return bytes(blob)


def _png_pixel(glb: bytes) -> tuple[int, int, int, int]:
    import struct
    from io import BytesIO

    from PIL import Image

    doc = _document(glb)
    json_len = struct.unpack_from("<I", glb, 12)[0]
    data = glb[20 + json_len + 8 :]
    view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    start = view.get("byteOffset", 0)
    raw = data[start : start + view["byteLength"]]
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    image = Image.open(BytesIO(raw))
    return image.getpixel((0, 0))


def test_texture_layer_reads_one_slice_and_forces_alpha():
    from bancwright.formats.bntx import BntxError, texture_layer, textures

    red = bytes((200, 20, 30, 10))
    green = bytes((20, 140, 40, 0))
    stored = _array_rgba("MaterialAlb", [red, green])
    layer0 = texture_layer(stored, 0, flatten_alpha=True)
    layer1 = texture_layer(stored, 1, flatten_alpha=True)
    assert layer0.rgba[:3] == red[:3]
    assert layer0.rgba[3] == 255
    assert layer1.rgba[:3] == green[:3]
    assert layer1.rgba[3] == 255
    assert "array layer 1 of 2" in (layer1.note or "")
    assert "alpha forced opaque" in (layer1.note or "")
    whole = textures(stored)[0]
    assert whole.rgba[:3] == red[:3]
    try:
        texture_layer(stored, 2)
    except BntxError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("layer 2 should be rejected")
    single = __import__("bancwright.formats.bntx", fromlist=["write_rgba"]).write_rgba("Flat", 2, 2, red * 4, swizzled=False)
    assert texture_layer(single, 0).rgba == textures(single)[0].rgba


def test_terra_array_sand_guess_and_helper_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres, texture_rank

    red = bytes((200, 20, 30, 10))
    green = bytes((20, 140, 40, 0))
    sand = bytes((186, 154, 96, 40))
    layers = [red] * 16 + [sand]
    array = _array_rgba("MaterialAlb", [red, green])
    sand_array = _array_rgba("MaterialAlb", layers)
    rock = bytes((90, 80, 70, 255))
    dump = Dump(build_fixture(tmp_path / "dump"))
    _install(
        dump,
        "Tm_Terra",
        sample_v10_bfres(
            "Tm_Terra",
            [{"name": "Mt_TerraMaterial_001", "samplers": [("tc", "MaterialCmb"), ("ta", "MaterialAlb")], "mode": "opaque"}],
            shapes=[{"name": "CoverGround", "material": 0}],
        ),
        {"MaterialAlb": array},
    )
    _install(
        dump,
        "Tm_Volume",
        sample_v10_bfres(
            "Tm_Volume",
            [{"name": "Mt_WaterVolume_A", "samplers": [], "mode": "opaque"}],
            shapes=[{"name": "WaterVolume_Box", "material": 0}],
            pool_name="Water__Common_A_2_Fx",
        ),
    )
    _install(
        dump,
        "Tm_Gate",
        sample_v10_bfres(
            "Tm_Gate",
            [
                {"name": "Mt_Rock", "samplers": [("_a0", "Gate_Rock_Alb")], "mode": "opaque"},
                {"name": "Mt_BakeShadowProxy", "samplers": [], "mode": "opaque"},
            ],
            shapes=[{"name": "Rock", "material": 0}, {"name": "BakeShadowProxy", "material": 1}],
        ),
        {"Gate_Rock_Alb": __import__("bancwright.formats.bntx", fromlist=["write_rgba"]).write_rgba("Gate_Rock_Alb", 2, 2, rock * 4)},
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    assert texture_rank("Water__Common_A_2_Fx") == 2
    assert texture_rank("Pond_Flow_Ind") == 2
    assert texture_rank("Leaf_Msk") == 2
    assert texture_rank("Leaf_Alb") == 0

    def fail_full(blob):
        raise AssertionError("the terra array must not be decoded as one image")

    real = model_mod.read_bntx
    model_mod.read_bntx = fail_full
    try:
        terra = describe_actor(dump, "Tm_Terra")
    finally:
        model_mod.read_bntx = real
    assert terra["status"] == "mesh"
    assert terra["textures"] == 1
    row = terra["textureLog"][0]
    assert row["wanted"] == "MaterialAlb#1"
    assert row["alpha"] == "opaque"
    assert "layer 1" in (row.get("error") or "")
    glb = (tmp_path / "cache" / terra["digest"][:2] / f"{terra['digest']}.glb").read_bytes()
    doc = _document(glb)
    assert doc["samplers"][0]["wrapS"] == 10497
    assert doc["samplers"][0]["wrapT"] == 10497
    assert doc["materials"][0].get("alphaMode", "OPAQUE") == "OPAQUE"
    pixel = _png_pixel(glb)
    assert pixel[0] < 80 and pixel[1] > 100 and pixel[3] == 255

    _install(
        dump,
        "Tm_Sand",
        sample_v10_bfres(
            "Tm_Sand",
            [{"name": "Mt_Sand_Kokiri_Cliff", "samplers": [("ta", "MaterialAlb")], "mode": "opaque"}],
            shapes=[{"name": "Cliff", "material": 0}],
        ),
        {"MaterialAlb": sand_array},
    )
    model_mod._TEX_INDEX.clear()
    sand_info = describe_actor(dump, "Tm_Sand")
    sand_row = sand_info["textureLog"][0]
    assert sand_row["wanted"] == "MaterialAlb#16"
    assert "layer 16 is a guess" in (sand_row.get("error") or "")
    assert "render-info" in (sand_row.get("error") or "") or "keys are empty" in (sand_row.get("error") or "")
    sand_pixel = _png_pixel((tmp_path / "cache" / sand_info["digest"][:2] / f"{sand_info['digest']}.glb").read_bytes())
    assert sand_pixel[0] > 150 and sand_pixel[2] < 140

    volume = describe_actor(dump, "Tm_Volume")
    assert volume["status"] == "box"
    assert "water volume" in (volume["reason"] or "")

    gate = describe_actor(dump, "Tm_Gate")
    assert gate["status"] == "mesh"
    assert gate["triangles"] == 1
    names = [mesh["name"] for mesh in _document((tmp_path / "cache" / gate["digest"][:2] / f"{gate['digest']}.glb").read_bytes())["meshes"]]
    assert any("Rock" in name for name in names)
    assert not any("Bake" in name or "Shadow" in name for name in names)


def test_water_keeps_only_the_front_sheet(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres

    dump = Dump(build_fixture(tmp_path / "dump"))
    _install(
        dump,
        "Tm_Pond",
        sample_v10_bfres(
            "Tm_Pond",
            [
                {"name": "Mt_Water_Back_Flowmap", "samplers": [], "mode": "custom"},
                {"name": "Mt_Water_Front_Flowmap", "samplers": [], "mode": "custom"},
            ],
            shapes=[{"name": "Back", "material": 0}, {"name": "Front", "material": 1}],
        ),
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    info = describe_actor(dump, "Tm_Pond")
    assert info["status"] == "mesh"
    assert info["triangles"] == 1
    assert "water stand-in" in (info["reason"] or "")
    doc = _document((tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes())
    names = [mesh["name"] for mesh in doc["meshes"]]
    assert any("Front" in name for name in names)
    assert not any("Back" in name for name in names)
    factor = doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert abs(factor[0] - 0.35) < 0.02
    assert abs(factor[1] - 0.55) < 0.02
    assert abs(factor[2] - 0.55) < 0.02
    assert abs(factor[3] - 0.2) < 0.02
    assert doc["materials"][0]["alphaMode"] == "BLEND"
    assert doc["materials"][0]["extras"]["banc"] == "water"


def test_missing_albedo_borrows_a_sibling_or_a_category_colour(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.bntx import write_rgba

    dump = Dump(build_fixture(tmp_path / "dump"))
    borrowed = bytes((40, 80, 120, 255))
    face = bytes((210, 170, 140, 255))
    _install(
        dump,
        "Tm_FieldProp",
        sample_v10_bfres(
            "Tm_FieldProp",
            [{"name": "Mt_Rock", "samplers": [("_a0", "Rock_Kokiri_FieldProp_J_Alb")], "mode": "opaque"}],
            shapes=[{"name": "Rock", "material": 0}],
        ),
        {"Rock_Kokiri_FieldProp_I_Alb": write_rgba("Rock_Kokiri_FieldProp_I_Alb", 2, 2, borrowed * 4)},
    )
    _install(
        dump,
        "Tm_Cloth",
        sample_v10_bfres(
            "Tm_Cloth",
            [{"name": "Mt_Cloth", "samplers": [("_a0", "Cloth_Kokiri_Interior_A_Alb")], "mode": "opaque"}],
            shapes=[{"name": "Cloth", "material": 0}],
        ),
    )
    _install(
        dump,
        "Tm_Vine",
        sample_v10_bfres(
            "Tm_Vine",
            [{"name": "Mt_Vine", "samplers": [("_a0", "Tree_Kokiri_GraphicVine_A_01_Alb")], "mode": "mask"}],
            shapes=[{"name": "Vine", "material": 0}],
        ),
    )
    _install(
        dump,
        "Tm_Saria",
        sample_v10_bfres(
            "Tm_Saria",
            [
                {"name": "Mt_Face", "samplers": [("_a0", "Face_Alb")], "mode": "opaque"},
                {"name": "Mt_Eyebrow", "samplers": [("_a0", "Face_Alb")], "mode": "opaque"},
                {"name": "Mt_Eye_Outer", "samplers": [("_a0", "Face_Alb")], "mode": "translucent"},
            ],
            shapes=[
                {"name": "Face", "material": 0},
                {"name": "Brow", "material": 1},
                {"name": "EyeOuter", "material": 2},
            ],
        ),
        {"Face_Alb": write_rgba("Face_Alb", 2, 2, face * 4)},
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()

    rock = describe_actor(dump, "Tm_FieldProp")
    assert rock["textures"] == 1
    assert "borrowed Rock_Kokiri_FieldProp_I_Alb" in (rock["textureLog"][0].get("error") or "")

    cloth = describe_actor(dump, "Tm_Cloth")
    assert cloth["textures"] == 0
    assert "flat cloth colour" in (cloth["textureLog"][0].get("error") or "")
    cloth_factor = _document((tmp_path / "cache" / cloth["digest"][:2] / f"{cloth['digest']}.glb").read_bytes())["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert abs(cloth_factor[0] - 0.55) < 0.02
    assert abs(cloth_factor[1] - 0.42) < 0.02
    assert abs(cloth_factor[2] - 0.28) < 0.02

    vine = describe_actor(dump, "Tm_Vine")
    assert "flat vine colour" in (vine["textureLog"][0].get("error") or "")
    vine_factor = _document((tmp_path / "cache" / vine["digest"][:2] / f"{vine['digest']}.glb").read_bytes())["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert abs(vine_factor[1] - 0.42) < 0.02
    assert vine_factor[1] > vine_factor[0]

    saria = describe_actor(dump, "Tm_Saria")
    assert saria["triangles"] == 2
    doc = _document((tmp_path / "cache" / saria["digest"][:2] / f"{saria['digest']}.glb").read_bytes())
    names = [mesh["name"] for mesh in doc["meshes"]]
    assert any("Face" in name for name in names)
    assert any("Brow" in name for name in names)
    assert not any("Eye" in name for name in names)
    brow = next(mesh for mesh in doc["meshes"] if "Brow" in mesh["name"])
    material = doc["materials"][brow["primitives"][0]["material"]]
    assert "baseColorTexture" not in material["pbrMetallicRoughness"]
    factor = material["pbrMetallicRoughness"]["baseColorFactor"]
    assert factor[0] < 0.4 and factor[1] < 0.3 and factor[2] < 0.2


def test_empty_mask_alpha_stays_a_cutout_and_bc4_is_not_colour():
    from bancwright.formats.bntx import Texture
    from bancwright.models import _accept

    rgba = bytearray()
    for x in range(4):
        light = x < 2
        rgba.extend((220, 210, 180, 0) if light else (0, 0, 0, 0))
    beard = Texture(name="Beard", width=4, height=1, rgba=bytes(rgba), fmt=0x1A01, codec="bc1")
    accepted, mode, cutoff, note = _accept(beard, "mask", 0.5)
    assert mode == "mask"
    assert cutoff < 0.5
    assert "cutout uses the colour" in note
    assert accepted.rgba[3] > 40
    assert accepted.rgba[8 + 3] == 0
    noise = Texture(name="Fx", width=1, height=1, rgba=bytes((80, 80, 80, 255)), fmt=0x1D01, codec="bc4")
    rejected, _mode, _cutoff, why = _accept(noise, "opaque", 0.5)
    assert rejected is None
    assert "BC4" in why

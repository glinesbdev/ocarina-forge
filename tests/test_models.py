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

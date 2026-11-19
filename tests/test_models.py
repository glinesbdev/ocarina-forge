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


def test_version_10_still_reads_the_albedo_name():
    import struct

    rgba = bytes([255, 0, 0, 255]) * 4
    blob = bytearray(
        sample_bfres(
            "Tree",
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            [0, 1, 2],
            [(0, 0), (1, 0), (0, 1)],
            rgba,
            2,
            2,
            texture_name="DekuTree_Alb",
            embed_texture=False,
        )
    )
    struct.pack_into("<I", blob, 0x08, 0x000A0000)
    model = parse(bytes(blob))[0]
    assert model.parts[0].texture == "DekuTree_Alb"


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

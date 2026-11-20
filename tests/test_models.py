"""Synthetic BFRES, BNTX, and the actor-model cache. No game data."""

import os
from pathlib import Path

from bancwright.actors import is_volume, view_actor
from bancwright.dump import Dump
from bancwright.fixtures import KOKIRI_STATIC, build_fixture
from bancwright.formats.bfres import _apply, _bind_pose, _euler_quat, _rigid, _trs, base_color_name, parse, rank_albedo, sample_bfres
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


def test_euler_bone_and_per_vertex_rigid_skin():
    """A door panel is often Euler XYZ, and each vertex names one rigid bone."""
    import math

    quat = _euler_quat(0.0, math.pi / 2, 0.0)
    bones = [
        (-1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), 0, 0),
        (0, (0.0, 0.0, 0.0), quat, (1.0, 1.0, 1.0), 0x1000, 0),
        (0, (4.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), 0, -1),
    ]
    turned = _bind_pose([(1.0, 0.0, 0.0)], bones, [], 1, None)[0]
    assert abs(turned[0]) < 1e-4
    assert abs(turned[1]) < 1e-4
    assert abs(turned[2] + 1.0) < 1e-4
    # Palette slot 0 is bone 1. Bone 2 is not rigid, so its vertex stays put.
    placed = _bind_pose(
        [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        bones,
        [1, 2],
        0,
        [0, 1],
    )
    assert abs(placed[0][2] + 1.0) < 1e-4
    assert abs(placed[1][0]) < 1e-4
    assert abs(placed[1][1] - 1.0) < 1e-4


def _shutter_bone(parent, euler, pos, flags, rigid, name):
    quat = _euler_quat(*euler)
    return (parent, pos, quat, (1.0, 1.0, 1.0), flags, rigid, name, euler, -1)


def test_switch_shutter_panels_stand_beside_the_arch():
    """Obj_HrlCastle_SwitchShutter_A_01 from the castle dump.

    Both door bones are Euler XYZ with rigid index -1. Skin count 0 still
    uses the shape bone. Rz · Ry · Rx stands each panel upright, hinged about
    2.65 m to either side of the centre.
    """
    flags = 0x1000 | 0x800000
    bones = [
        _shutter_bone(-1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0x1000, -1, "Root"),
        _shutter_bone(0, (1.5708, 1.5708, 0.0), (-2.65156, 0.0, -0.18121), flags, -1, "DoubleDoor_L"),
        _shutter_bone(0, (-1.5708, 1.5708, 0.0), (2.65156, 0.0, -0.18121), flags, -1, "DoubleDoor_R"),
    ]
    # FlameR01_1 is long in local -Z and tall in local Y. The long edge is the height.
    left_high = _bind_pose([(-0.283, -0.075, -7.230)], bones, [], 1, None)[0]
    left_inner = _bind_pose([(0.075, 2.648, 0.0)], bones, [], 1, None)[0]
    left_hinge = _bind_pose([(0.0, 0.0, 0.0)], bones, [], 1, None)[0]
    # FlameR01 is the mirror, long in local +Z.
    right_high = _bind_pose([(0.0, 0.0, 7.230)], bones, [], 2, None)[0]
    right_inner = _bind_pose([(0.074, 2.648, 0.0)], bones, [], 2, None)[0]
    right_hinge = _bind_pose([(0.0, 0.0, 0.0)], bones, [], 2, None)[0]
    assert abs(left_hinge[0] + 2.65156) < 0.01
    assert abs(right_hinge[0] - 2.65156) < 0.01
    assert abs(left_hinge[1]) < 0.01 and abs(right_hinge[1]) < 0.01
    assert abs(left_high[1] - 7.230) < 0.02
    assert abs(right_high[1] - 7.230) < 0.02
    assert abs(left_high[0] - left_hinge[0]) < 0.15
    assert abs(right_high[0] - right_hinge[0]) < 0.15
    # The local Y width reaches the centre instead of tipping the panel over.
    assert abs(left_inner[0]) < 0.05
    assert abs(right_inner[0]) < 0.05
    assert abs(left_inner[1]) < 0.05 and abs(right_inner[1]) < 0.05


def test_ivy_rotate_hangs_the_sheet_down_the_cliff():
    """Land_IvyClimbable_A_01 #745 and A_02 #743. The mesh runs along +Z.

    Actor Rotate is the same Rz · Ry · Rx as a bone. X = 90° then the yaw
    drops +Z straight down and leaves the sheet's normal horizontal, along
    the yaw direction. three.js order XYZ does not.
    """
    import math

    poses = (
        ((1.5708, -1.29154, 0.0), (29.5, 109.0, -21.75)),
        ((1.5708, -1.309, 0.0), (0.0, 0.0, 0.0)),
    )
    for euler, origin in poses:
        quat = _euler_quat(*euler)
        placed = _trs(origin, quat, (1.0, 1.0, 1.0))
        spin = _trs((0.0, 0.0, 0.0), quat, (1.0, 1.0, 1.0))
        tip = _apply(placed, (0.0, 0.0, 3.0))
        base = _apply(placed, (0.0, 0.0, 0.0))
        assert abs((tip[1] - base[1]) + 3.0) < 0.02
        assert abs(tip[0] - base[0]) < 0.02
        assert abs(tip[2] - base[2]) < 0.02
        assert abs(base[0] - origin[0]) < 1e-4
        assert abs(base[1] - origin[1]) < 1e-4
        side = _apply(spin, (1.7, 0.0, 0.0))
        assert abs(side[1]) < 0.02
        normal = _apply(spin, (0.0, 1.0, 0.0))
        assert abs(normal[1]) < 0.02
        assert abs(normal[0] - math.sin(euler[1])) < 0.02
        assert abs(normal[2] - math.cos(euler[1])) < 0.02


def test_moat_and_stream_count_as_water_and_a_bounds_box_is_omitted():
    from bancwright.models import _omit_reason, _untextured_bounds_box, _water_names
    from bancwright.formats.bfres import Part

    assert _water_names("Mt_HrlCastle_Moat")
    assert _water_names("Cliff", "HrlCastle_Stream_A")
    assert not _water_names("Npc_River_A")
    assert _omit_reason("Occluder_A", "Mt_White") == "occluder is not drawn"
    assert _omit_reason("MiniMap_Castle", "") == "minimap model is not drawn"
    box = Part("Bounds", [(0, 0, 0), (8, 0, 0), (8, 4, 6)], [0, 1, 2], None, "", "")
    tiny = Part("Courtyard", [(i * 0.01, 0, 0) for i in range(30)], [0, 1, 2], [(0, 0)] * 30, "alb", "")
    assert _untextured_bounds_box(box, [(box, ""), (tiny, "alb")])
    assert not _untextured_bounds_box(tiny, [(box, ""), (tiny, "alb")])


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


def test_sparse_heightmap_is_a_few_files_inside_a_large_index():
    from bancwright.models import _sparse_heightmap

    castle = [{"status": "drawn", "reason": ""}] * 14 + [{"status": "skipped", "reason": "no heightmap file"}] * 583
    assert _sparse_heightmap(castle) is True
    kokiri = [{"status": "drawn", "reason": ""}] * 12 + [{"status": "skipped", "reason": "outside the placed actors"}] * 40
    assert _sparse_heightmap(kokiri) is False
    assert _sparse_heightmap([{"status": "drawn", "reason": ""}]) is False


def test_converter_output_is_pinned_to_the_cache_version():
    """The converter sources are hashed into the cache key.

    Changing any of them fails this test until `_CACHE_VERSION` is bumped and
    the pin below is updated in the same edit. Reusing the previous version with
    a new stamp is a failure: the prefix has to move so a reader can see the rebuild.
    """
    from bancwright.models import _CACHE_VERSION, _cache_prefix, _converter_files, _converter_stamp

    assert [path.name for path in _converter_files()] == [
        "models.py",
        "bfres.py",
        "terrain.py",
        "gltf.py",
        "meshopt.py",
        "meshopt-worker.mjs",
    ]
    version = _CACHE_VERSION.split(b"\0", 1)[0].decode()
    stamp = _converter_stamp().hex()
    assert _cache_prefix() == _CACHE_VERSION + bytes.fromhex(stamp)
    pinned = ("models-31", "1f018c034470d9175560e720fd116f348ba16b3a2e6f51e05002b721f09d9093")
    assert (version, stamp) == pinned, (
        "Converter output changed without a cache bump. Bump _CACHE_VERSION and "
        "replace this pin with the new version and stamp. Do not keep the previous version."
    )


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
    assert area["region"] is True
    start = view_actor({"Gyaml": "StartPos", "Hash": 2, "Translate": [0, 0, 0]}, "vanilla")
    assert start["category"] == "system"
    assert start["volume"] is True
    ground = view_actor({"Gyaml": "Land_Kokiri_Ground", "Hash": 3, "Translate": [0, 0, 0], "Scale": [20, 1, 20]}, "vanilla")
    assert ground["volume"] is True
    assert ground["region"] is False
    air = view_actor({"Gyaml": "FldObj_AirWall_CharacterSight_GenerateNavMesh_Body", "Hash": 7, "Translate": [0, 0, 0]}, "vanilla")
    assert air["category"] == "system"
    assert air["region"] is True
    assert view_actor({"Gyaml": "BGM_Silence", "Hash": 8, "Translate": [0, 0, 0]}, "vanilla")["region"] is True
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
    document, blob = _glb_parts(glb)
    factor = document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert factor[1] > factor[0] and factor[1] > factor[2]
    assert abs(factor[1] - 0.45) < 1e-6
    assert "baseColorTexture" not in document["materials"][0]["pbrMetallicRoughness"]
    primitive = document["meshes"][0]["primitives"][0]
    assert "NORMAL" in primitive["attributes"]
    _nx, _ny, nz = _accessor_floats(document, blob, primitive["attributes"]["NORMAL"], 3)[0]
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
    pbr = document["materials"][0]["pbrMetallicRoughness"]
    factor = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
    assert factor[0] == 1.0 and factor[1] == 1.0
    assert "baseColorTexture" in pbr
    primitive = document["meshes"][0]["primitives"][0]
    assert document["materials"][0].get("doubleSided", False) is False
    assert "TEXCOORD_3" in primitive["attributes"]
    positions = _accessor_floats(document, blob, primitive["attributes"]["POSITION"], 3)
    assert min(point[0] for point in positions) < -7
    assert max(point[0] for point in positions) > 7
    assert abs(positions[0][1] - 6.0) < 1e-3
    normals = _accessor_floats(document, blob, primitive["attributes"]["NORMAL"], 3)
    assert normals[0][1] > 0.9
    image = document["images"][0]
    view = document["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    png = blob[start : start + view["byteLength"]]
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
    floor = describe_terrain(dump, KOKIRI_STATIC)
    assert floor["status"] == "mesh"
    assert "terrain proxy" not in (floor.get("reason") or "")
    floor_row = next(row for row in floor["tiles"] if row.get("name") == "Flat_00")
    assert floor_row["status"] == "drawn"
    assert floor_row["holes"] == 0
    floor_glb = (tmp_path / "cache" / floor["digest"][:2] / f"{floor['digest']}.glb").read_bytes()
    floor_doc, floor_blob = _glb_parts(floor_glb)
    floor_prim = floor_doc["meshes"][0]["primitives"][0]
    floor_y = _accessor_floats(floor_doc, floor_blob, floor_prim["attributes"]["POSITION"], 3)
    assert any(abs(point[1]) < 1e-3 for point in floor_y)
    flat.write_bytes(_hght([float("nan")] * (SIZE * SIZE)))
    stamp = flat.stat()
    os.utime(flat, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 2_000_000_000))
    model_mod._FILE_SIG.clear()
    empty = describe_terrain(dump, KOKIRI_STATIC)
    assert empty["status"] == "none"
    assert "terrain proxy" not in (empty.get("reason") or "")
    assert any("non-finite" in (row.get("reason") or "") for row in empty["tiles"])
    assert all(row.get("holes") == SIZE * SIZE for row in empty["tiles"] if row.get("name") == "Flat_00")


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


def test_zero_is_ground_and_a_hole_in_the_fine_tile_keeps_the_coarse_cell():
    from bancwright.formats.terrain import SIZE, build_scene

    coarse = [0.0] * (SIZE * SIZE)
    hole = [float("nan")] * (SIZE * SIZE)
    parts, reports = build_scene(
        [
            (0.0, 0.0, 32.0, "Coarse", coarse, None),
            (0.0, 0.0, 8.0, "Fine", hole, None),
        ],
        1.0,
        10.0,
        origin=(0.0, 2.0, 0.0),
    )
    by_name = {row.name: row for row in reports}
    assert by_name["Coarse"].status == "drawn"
    assert by_name["Coarse"].dropped == 0
    assert by_name["Coarse"].holes == 0
    assert by_name["Fine"].status != "drawn"
    assert by_name["Fine"].holes == SIZE * SIZE
    assert "non-finite" in by_name["Fine"].reason
    assert any(abs(y - 2.0) < 1e-4 for part in parts for _x, y, _z in part.positions)

    fine = [0.2] * (SIZE * SIZE)
    _punched, punched_reports = build_scene(
        [
            (0.0, 0.0, 32.0, "Coarse", coarse, None),
            (0.0, 0.0, 8.0, "Fine", fine, None),
        ],
        1.0,
        10.0,
    )
    punched_coarse = next(row for row in punched_reports if row.name == "Coarse")
    assert punched_coarse.dropped > 0
    assert punched_coarse.triangles < by_name["Coarse"].triangles


def test_the_partial_column_odd_rows_are_not_zeros():
    import struct

    from bancwright.formats.swizzle import _gob_address, surface_bytes
    from bancwright.formats.terrain import SIZE, _as_floats

    log2 = 4
    block_height = 1 << log2
    need = surface_bytes(SIZE, SIZE, 1, 1, 4, log2)
    buf = bytearray(need)
    for y in range(SIZE):
        for x in range(SIZE):
            if x < SIZE - 1:
                pos = _gob_address(x, y, SIZE, 4, block_height)
            else:
                pos = _gob_address(x, y - (y % 2), SIZE, 4, block_height) + (y % 2) * 4
            struct.pack_into("<f", buf, pos, 0.08 + (x % 5) * 0.01 + (y % 7) * 0.002)
    struct.pack_into("<f", buf, _gob_address(SIZE - 1, 0, SIZE, 4, block_height) + 4, 0.42)
    values = _as_floats(bytes(buf))
    assert values is not None
    assert abs(values[1 * SIZE + (SIZE - 1)] - 0.42) < 1e-5
    assert sum(1 for value in values if value == 0.0) == 0


def test_a_dropped_mid_quad_does_not_take_the_coarse_ground_with_it():
    from bancwright.formats.terrain import SIZE, build_scene

    count = SIZE * SIZE
    parts, reports = build_scene(
        [
            (0.0, 0.0, 32.0, "Coarse", [0.2] * count, None),
            (0.0, 0.0, 16.0, "Mid", [0.4] * count, None),
            (0.0, 0.0, 6.0, "Fine", [0.6] * count, None),
        ],
        1.0,
        10.0,
        steps={"Coarse": 8, "Mid": 64, "Fine": 8},
    )
    coarse = next(row for row in reports if row.name == "Coarse")
    band = [
        point
        for part in parts
        for point in part.positions
        if abs(point[1] - 2.0) < 0.05 and 3.5 < abs(point[0]) < 4.5 and abs(point[2]) < 1.0
    ]
    assert coarse.status == "drawn"
    assert band, "coarse ground just outside the fine tile should stay"


def test_a_uv_seam_is_closed_and_a_full_chart_is_left_alone():
    from bancwright.formats.bfres import _close_uv_seams

    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    seam = _close_uv_seams(positions, [(0.02, 0.2), (0.98, 0.2), (0.04, 0.8)], [0, 1, 2])
    assert seam[2][1] != 1
    assert seam[1][seam[2][1]][0] < 0
    assert abs(seam[1][1][0] - 0.98) < 1e-6
    # v=0.8 beside v=0.2 is a gradient, not a wrap. It stays.
    assert min(pair[1] for pair in seam[1]) >= 0.2 - 1e-6
    chart = _close_uv_seams(positions, [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], [0, 1, 2])
    assert chart[2] == [0, 1, 2]
    # A slate span of ~1.2 tiles is tiling. Folding it by 1 is what stretched the roof.
    roof = _close_uv_seams(
        [(0.0, 0.0, 0.0), (4.83, 0.0, 0.0), (0.0, 0.0, 1.2)],
        [(1.354, -1.45), (2.570, -1.40), (1.400, -1.20)],
        [0, 1, 2],
    )
    assert roof[2] == [0, 1, 2]
    assert roof[1][1][0] == 2.570


def test_tiled_roof_uvs_match_the_served_glb_on_both_lods():
    """Slate-style _u0 outside 0..1 survives the cached glb, lod0 and the far mesh.

    The served file is the meshopt glb. TEXCOORD_0 is float, not a normalized
    uint16, and the far mesh does not bridge a UV seam.
    """
    from bancwright.formats.bfres import parse, sample_bfres
    from bancwright.formats.meshopt import simplify_mesh
    from bancwright.models import _assemble

    cols, rows = 20, 12
    positions = []
    uvs = []
    for y in range(rows):
        for x in range(cols):
            positions.append((x * 4.83, 0.0, y * 4.0))
            uvs.append((1.354 + x * 1.216, -1.45 + y * 0.516))
    indices = []
    for y in range(rows - 1):
        for x in range(cols - 1):
            corner = y * cols + x
            indices.extend((corner, corner + 1, corner + cols, corner + 1, corner + cols + 1, corner + cols))
    model = parse(
        sample_bfres(
            "Slate",
            positions,
            indices,
            uvs,
            bytes([40, 70, 120, 255]) * 4,
            2,
            2,
            embed_texture=False,
        )
    )[0]
    part = model.parts[0]
    part.name = "pCube14947_1__Mt_Land_HrlCastle_Roof_Slate_A"
    assert abs(part.uvs[0][0] - 1.354) < 1e-4 and abs(part.uvs[0][1] + 1.45) < 1e-4
    assert abs(part.uvs[1][0] - (1.354 + 1.216)) < 1e-4
    assert max(u for u, _v in part.uvs) - min(u for u, _v in part.uvs) > 2.0

    glb, _info = _assemble([model], {})
    document, blob = _glb_parts(glb)
    by_lod = {}
    for node in document["nodes"]:
        mesh = document["meshes"][node["mesh"]]
        primitive = mesh["primitives"][0]
        by_lod[node["name"][:4]] = _accessor_floats(document, blob, primitive["attributes"]["TEXCOORD_0"], 2)
    assert "lod0" in by_lod and "lod1" in by_lod
    near = by_lod["lod0"]
    assert len(near) == len(uvs)
    for got, want in zip(near, uvs):
        assert abs(got[0] - want[0]) < 1e-4
        assert abs(got[1] - want[1]) < 1e-4
    accessor = document["accessors"][document["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]]
    assert accessor["componentType"] == 5126
    assert not accessor.get("normalized")
    far = by_lod["lod1"]
    assert min(u for u, _v in far) < 1.40
    assert max(u for u, _v in far) > 1.354 + 11 * 1.216
    # The corner texcoord is still the BFRES value, not a 0..1 squash.
    assert any(abs(u - 1.354) < 1e-3 and abs(v + 1.45) < 1e-3 for u, v in far)

    # A topological seam stays split on the simplified mesh: no far triangle
    # reaches across u=0 and u=1.
    seam_pos = []
    seam_uv = []
    seam_cols = 16 + 15
    seam_rows = 16
    for y in range(seam_rows):
        for x in range(16):
            seam_pos.append((float(x), 0.0, float(y)))
            seam_uv.append((x / 15.0, y / 15.0))
        for x in range(15):
            seam_pos.append((15.0 + x, 0.0, float(y)))
            seam_uv.append((x / 14.0, y / 15.0))
    seam_idx = []
    for y in range(seam_rows - 1):
        for i in range(seam_cols - 1):
            if i == 15:
                continue
            a = y * seam_cols + i
            b = a + 1
            c = a + seam_cols
            d = c + 1
            seam_idx.extend((a, b, c, b, d, c))
    simplified = simplify_mesh(seam_pos, seam_idx, seam_uv)
    assert simplified is not None
    _pos, far_idx, far_uv = simplified
    for start in range(0, len(far_idx), 3):
        span = [far_uv[far_idx[start + offset]][0] for offset in range(3)]
        assert max(span) - min(span) < 0.5


def test_coverage_png_marks_the_emitted_quad(tmp_path):
    from PIL import Image

    from bancwright.formats.terrain import SIZE, build_scene, write_coverage_png

    parts, _reports = build_scene([(0.0, 0.0, 1.0, "Tile", [0.5] * (SIZE * SIZE), None)], 1.0, 1.0)
    positions = [point for part in parts for point in part.positions]
    indices = []
    base = 0
    for part in parts:
        indices.extend(base + item for item in part.indices)
        base += len(part.positions)
    path = tmp_path / "coverage.png"
    stats = write_coverage_png(path, positions, indices, (0.0, 0.0, 0.0), 1.0, size=32)
    image = Image.open(path)
    assert image.getpixel((16, 16))[0] == 255
    assert image.getpixel((0, 0))[0] == 0
    assert stats["covered"] > 0
    assert stats["coveredSub"] == 0


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


def test_mate_blend_uses_the_second_index_and_rejects_noise():
    from bancwright.formats.terrain import SIZE, read_mate

    count = SIZE * SIZE
    raw = bytearray()
    for _index in range(count):
        raw.extend((3, 7, 200, 0))
    layers, note = read_mate(bytes(raw))
    assert layers is not None and layers[0] == 7
    assert "guess" in note
    wide = bytes([4, 9, 0, 0]) * (256 * 256)
    resampled, wide_note = read_mate(wide)
    assert resampled is not None and len(resampled) == count and resampled[0] == 4
    assert "256" in wide_note
    noise, rejected = read_mate(bytes([200, 201, 202, 203]) * count)
    assert noise is None
    assert "did not contain" in rejected


def test_swizzled_mate_keeps_material_a_and_lists_material_b():
    import struct

    from bancwright.formats.swizzle import swizzle
    from bancwright.formats.terrain import SIZE, read_mate, read_occlusion, split_histogram

    count = SIZE * SIZE
    raw = bytearray()
    for index in range(count):
        if index % 2 == 0:
            raw.extend((0x0F, 0x7F, 0x7F, 0x7F))
        else:
            raw.extend((0x0F, 0x0B, 0x7F, 0x7F))
    packed = swizzle(SIZE, SIZE, 1, 1, 4, 0, 4, bytes(raw))
    block = packed + bytes(len(packed))
    header = bytearray(0x34)
    header[4] = 0x06
    struct.pack_into("<I", header, 0x2C, 8)
    struct.pack_into("<I", header, 0x30, len(block))
    layers, note = read_mate(bytes(header) + block)
    histogram, rest = split_histogram(note)
    assert layers is not None
    assert layers[0] == 0x0F
    assert layers[1] == 0x0F
    assert "15×" in histogram and "11×" in histogram
    assert "0x7f" in rest and "0x06" in note
    shade = bytearray()
    for index in range(count):
        shade.extend((40 if index < count // 2 else 220, 0x7F, 0x7F, 0x7F))
    ao_packed = swizzle(SIZE, SIZE, 1, 1, 4, 0, 4, bytes(shade))
    ao_block = ao_packed + bytes(len(ao_packed))
    ao_header = bytearray(header)
    struct.pack_into("<I", ao_header, 0x30, len(ao_block))
    values, ao_note = read_occlusion(bytes(ao_header) + ao_block)
    assert values is not None
    assert values[0] < 0.2 and values[-1] > 0.8
    assert "4-byte" in ao_note


def test_tiles_inside_the_clip_are_not_capped():
    from bancwright.models import _choose_tiles

    tiles = [(float(index), 0.0, 4.0, f"T{index:02d}") for index in range(40)]
    chosen, skipped, notes = _choose_tiles(tiles, 1.0, (0.0, 0.0, 0.0), (-10.0, -10.0, 200.0, 10.0))
    assert len(chosen) == 40
    assert skipped == []
    assert not any("36" in note or "nearest" in note for note in notes)


def test_sub_scene_supplies_the_fine_height_and_the_mate(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.terrain import SIZE
    from bancwright.formats.zsio import compress

    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    main = dump.romfs / "TerrainArc" / "KokiriForest"
    sub = dump.romfs / "TerrainArc" / "KokiriForest_sub"
    main.mkdir(parents=True)
    sub.mkdir()
    (dump.romfs / "TerrainArc" / "KokiriForest.tscb").write_bytes(
        _tscb(
            1.0,
            4.0,
            [
                (0.0, 0.0, 32.0, "Coarse"),
                (0.0, 0.0, 8.0, "Fine"),
                (8000.0, 8000.0, 20.0, "Far"),
            ],
        )
    )
    (dump.romfs / "TerrainArc" / "KokiriForest_sub.tscb").write_bytes(
        _tscb(1.0, 4.0, [(0.0, 0.0, 8.0, "Fine"), (4.0, 0.0, 4.0, "Extra")])
    )
    (main / "Coarse.hght").write_bytes(_hght([0.5] * (SIZE * SIZE), bytes([0]) * (SIZE * SIZE)))
    (sub / "Fine.hght").write_bytes(_hght([0.5] * (SIZE * SIZE)))
    (sub / "Extra.hght").write_bytes(_hght([0.5] * (SIZE * SIZE)))
    (main / "Fine.mate").write_bytes(bytes([0, 0, 0, 0]) * (SIZE * SIZE))
    (sub / "Fine.mate").write_bytes(bytes([1, 0, 0, 0]) * (SIZE * SIZE))
    shade = bytes([40] * (SIZE * SIZE // 2) + [220] * (SIZE * SIZE - SIZE * SIZE // 2))
    (sub / "Fine.child.bake_ao.extm").write_bytes(shade)
    (sub / "Fine.custom.extm").write_bytes(b"not a grid")
    green = bytes([20, 140, 40, 255]) * 4
    red = bytes([220, 20, 20, 255]) * 4
    (dump.romfs / "Tex" / "MaterialAlb.bntx.zs").write_bytes(
        compress(_array_bntx("MaterialAlb", [green, red], 2, 2), 1, dicts, level=3)
    )
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    info = describe_terrain(dump, KOKIRI_STATIC)
    assert info["status"] == "mesh"
    assert "terrain heightmap" in info["reason"]
    assert any(path.endswith("KokiriForest_sub.tscb") for path in info["terrain"]["subs"])
    drawn = {row["name"]: row for row in info["tiles"] if row["status"] == "drawn"}
    assert "Fine" in drawn and "Extra" in drawn
    assert "KokiriForest_sub" in drawn["Fine"]["path"]
    assert drawn["Fine"]["directory"] == "KokiriForest_sub"
    assert drawn["Fine"]["layers"] == [1]
    assert "KokiriForest_sub/Fine.mate" in drawn["Fine"]["mate"]
    assert "guess" in drawn["Fine"]["mateNote"]
    assert "applied" in drawn["Fine"]["ao"], drawn["Fine"]["ao"]
    assert "custom.extm" in drawn["Fine"]["ao"]
    assert "not used" in drawn["Fine"]["ao"]
    assert drawn["Extra"]["directory"] == "KokiriForest_sub"
    assert drawn["Fine"]["layer"] == "sub"
    assert drawn["Coarse"]["layer"] == "main"
    assert any(row["name"] == "Far" and "outside" in row["reason"] for row in info["tiles"])
    glb = (tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes()
    document, blob = _glb_parts(glb)
    coloured = None
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            if "COLOR_0" in primitive["attributes"]:
                coloured = primitive
                break
    assert coloured is not None
    colors = _accessor_floats(document, blob, coloured["attributes"]["COLOR_0"], 3)
    assert min(item[0] for item in colors) < 0.3
    assert max(item[0] for item in colors) > 0.7


def test_sub_overlay_keeps_the_main_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.formats.terrain import SIZE

    dump = Dump(build_fixture(tmp_path / "dump"))
    main = dump.romfs / "TerrainArc" / "KokiriForest"
    sub = dump.romfs / "TerrainArc" / "KokiriForest_sub"
    main.mkdir(parents=True)
    sub.mkdir()
    count = SIZE * SIZE
    (dump.romfs / "TerrainArc" / "KokiriForest.tscb").write_bytes(
        _tscb(1.0, 10.0, [(0.0, 0.0, 32.0, "Coarse"), (0.0, 0.0, 8.0, "Floor")])
    )
    (dump.romfs / "TerrainArc" / "KokiriForest_sub.tscb").write_bytes(
        _tscb(1.0, 10.0, [(0.0, 0.0, 8.0, "Rim")])
    )
    (main / "Coarse.hght").write_bytes(_hght([0.2] * count))
    (main / "Floor.hght").write_bytes(_hght([0.2] * count))
    (sub / "Rim.hght").write_bytes(_hght([0.9] * count))
    (main / "Floor.mate").write_bytes(bytes([1, 0, 0, 0]) * count)
    (sub / "Floor.mate").write_bytes(bytes([9, 0, 0, 0]) * count)
    (sub / "Rim.mate").write_bytes(bytes([2, 0, 0, 0]) * count)
    (main / "Rim.mate").write_bytes(bytes([9, 0, 0, 0]) * count)
    from bancwright import models as model_mod

    model_mod._LISTING.clear()
    model_mod._TEX_INDEX.clear()
    info = describe_terrain(dump, KOKIRI_STATIC)
    drawn = {row["name"]: row for row in info["tiles"] if row["status"] == "drawn"}
    assert drawn["Floor"]["layer"] == "main"
    assert drawn["Floor"]["layers"] == [1]
    assert "KokiriForest/Floor.mate" in drawn["Floor"]["mate"]
    assert drawn["Rim"]["layer"] == "sub"
    assert drawn["Rim"]["layers"] == [2]
    assert "KokiriForest_sub/Rim.mate" in drawn["Rim"]["mate"]
    assert drawn["Coarse"]["dropped"] > 0
    glb = (tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes()
    document, blob = _glb_parts(glb)
    primitive = document["meshes"][0]["primitives"][0]
    positions = _accessor_floats(document, blob, primitive["attributes"]["POSITION"], 3)
    marks = _accessor_floats(document, blob, primitive["attributes"]["TEXCOORD_3"], 2)
    floor = [point for point, mark in zip(positions, marks) if mark[1] < 0.5 and abs(point[1] - 2.0) < 0.05 and abs(point[0]) < 1 and abs(point[2]) < 1]
    rim = [point for point, mark in zip(positions, marks) if mark[1] > 0.5 and abs(point[1] - 9.0) < 0.05 and abs(point[0]) < 1 and abs(point[2]) < 1]
    assert floor and rim


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


def _stored(glb: bytes) -> bytes:
    """The cache file is meshopt-compressed. Tests read the decoded floats and the original image."""
    from bancwright.formats.meshopt import plain_glb

    return plain_glb(glb)


def _glb_parts(glb: bytes):
    import json
    import struct

    glb = _stored(glb)
    json_len = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + json_len])
    return document, glb[20 + json_len + 8 :]


def _accessor_floats(document, blob: bytes, index: int, width: int):
    import struct

    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride") or width * 4
    count = accessor["count"]
    return [struct.unpack_from(f"<{width}f", blob, start + item * stride) for item in range(count)]


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
    assert resolved["textures"] == 2
    row = resolved["textureLog"][0]
    assert any(role[2] == "normal" and role[1] == "FlatBlue" for role in row["roles"])
    assert row["wanted"] == "LeafGreen"
    assert row["sampler"] == "_a0"
    assert row["decoded"] is True
    assert row["alpha"].startswith("mask")
    assert row["doubleSided"] is True
    glb = (tmp_path / "cache" / resolved["digest"][:2] / f"{resolved['digest']}.glb").read_bytes()
    document = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    assert document["materials"][0]["alphaMode"] == "MASK"
    assert document["materials"][0]["alphaCutoff"] == 0.4
    assert "normalTexture" in document["materials"][0]

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
    # Neighbouring texcoords that share a position cell stay distinct.
    # Welding them is what smears a seam on the fallback far mesh.
    uvs = [(float(x % 2), 0.0) for y in range(40) for x in range(40)]
    seamed = cluster_lod(positions, indices, uvs)
    assert seamed is not None
    assert any(abs(u - 1.0) < 1e-6 for u, _v in seamed[2])
    assert any(abs(u) < 1e-6 for u, _v in seamed[2])


def test_meshoptimizer_lod_shrinks_a_dense_mesh_and_keeps_the_border():
    from bancwright.formats.meshopt import simplify_mesh

    positions = [(float(x), float(y), 0.0) for y in range(40) for x in range(40)]
    indices = []
    for y in range(39):
        for x in range(39):
            corner = y * 40 + x
            indices.extend((corner, corner + 1, corner + 40, corner + 1, corner + 41, corner + 40))
    uvs = [(point[0] / 39, point[1] / 39) for point in positions]
    far = simplify_mesh(positions, indices, uvs)
    assert far is not None, "meshoptimizer did not return a far mesh (is node on PATH, and is frontend/node_modules/meshoptimizer installed?)"
    new_pos, new_idx, new_uv = far
    assert len(new_idx) < len(indices) * 0.5
    assert len(new_uv) == len(new_pos)
    xs = [point[0] for point in new_pos]
    ys = [point[1] for point in new_pos]
    assert min(xs) == 0 and max(xs) == 39
    assert min(ys) == 0 and max(ys) == 39


def test_ground_debug_height_and_extent(tmp_path, capsys):
    import json

    from bancwright.__main__ import print_ground_debug
    from bancwright.formats.byml import dumps
    from bancwright.formats.terrain import SIZE

    dump = Dump(build_fixture(tmp_path / "dump"))
    parent = dumps({"TerrainSceneName": "KokiriField", "TerrainScenePosition": {"X": 0.0, "Y": 2.0, "Z": 0.0}})
    child = dumps({"$parent": "Work/Banc/GameBancParam/KokiriForest_Base.game__banc__GameBancParam.gyml"})
    param = dump.romfs / "Banc" / "GameBancParam"
    param.mkdir(parents=True)
    (param / "KokiriForest_Base.game__banc__GameBancParam.bgyml").write_bytes(parent)
    (param / "KokiriForest_Child.game__banc__GameBancParam.bgyml").write_bytes(child)
    folder = dump.romfs / "TerrainArc" / "KokiriField"
    folder.mkdir(parents=True)
    (dump.romfs / "TerrainArc" / "KokiriField.tscb").write_bytes(
        _tscb(1.0, 10.0, [(0.0, 0.0, 16.0, "Tile_00"), (8000.0, 8000.0, 20.0, "Far_00")])
    )
    (folder / "Tile_00.hght").write_bytes(_hght([0.4] * (SIZE * SIZE), bytes([1]) * (SIZE * SIZE)))
    assert print_ground_debug(KOKIRI_STATIC, points=["0,0", "100,100"], as_json=True, dump=str(dump.romfs.parent)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "heightmap"
    assert payload["source"].endswith("TerrainArc/KokiriField.tscb")
    assert payload["origin"] == [0.0, 2.0, 0.0]
    assert payload["readableExtent"]["minX"] == -8
    assert payload["readableExtent"]["maxX"] == 8
    assert abs(payload["heights"]["minY"] - 6.0) < 1e-4
    drawn = payload["samples"][0]["drawn"]
    assert drawn["name"] == "Tile_00"
    assert abs(drawn["worldY"] - 6.0) < 1e-4
    assert payload["samples"][1]["drawn"] is None
    assert all("heights" not in tile or tile["heights"] is None for tile in payload["tiles"])
    assert print_ground_debug(KOKIRI_STATIC, points=["nope"], dump=str(dump.romfs.parent)) == 2


def test_terrain_atlas_is_cached_apart_from_the_glb(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "cache"))
    from bancwright.models import _CACHE_VERSION, _pack_atlas, _solid_cell

    cells = [_solid_cell((1, 0, 0)), _solid_cell((0, 1, 0))]
    png, cols = _pack_atlas(cells)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" or png[:2] == b"\xff\xd8"
    stored = next((tmp_path / "cache" / "atlas").glob("*.img"))
    assert _CACHE_VERSION.split(b"\0", 1)[0].decode() not in stored.name
    stored.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cached-atlas")
    again, cols_again = _pack_atlas(cells)
    assert again == stored.read_bytes()
    assert cols_again == cols
    other, _other_cols = _pack_atlas([_solid_cell((0, 0, 1))])
    assert other != again
    assert other[:8] == b"\x89PNG\r\n\x1a\n" or other[:2] == b"\xff\xd8"


def test_model_debug_prints_skin_and_the_actor_placement(tmp_path, capsys):
    import json

    from bancwright.__main__ import print_model_debug
    from bancwright.formats.bfres import _shape_report, _worlds, inspect

    rgba = bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255])
    blob = sample_bfres("Pyramid", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], rgba, 2, 2)
    report = inspect(blob)
    assert report["error"] == ""
    shape = report["models"][0]["shapes"][0]
    assert shape["decoded"] is True
    assert shape["name"] == "Body"
    assert shape["before"] == shape["after"]
    assert report["models"][0]["bones"] == []

    import math

    quat = _euler_quat(0.0, math.pi / 2, 0.0)
    raw = (0.0, math.pi / 2, 0.0, 0.0)
    bones = [
        (-1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), 0, 0, "root", (0.0, 0.0, 0.0, 1.0), -1),
        (0, (0.0, 0.0, 0.0), quat, (1.0, 1.0, 1.0), 0x1000, 0, "panel", raw, -1),
    ]
    worlds = _worlds(bones)
    baked = _bind_pose([(1.0, 0.0, 0.0)], bones, [], 1, None)
    row = _shape_report("Door", 0, 0, 1, None, bones, [], worlds, {"min": [1, 0, 0], "max": [1, 0, 0]}, {"min": list(baked[0]), "max": list(baked[0])}, 0, [], 0)
    chain = row["bones"][0]["chain"]
    assert [bone["name"] for bone in chain] == ["root", "panel"]
    assert "euler-xyz" in chain[1]["flagText"]
    assert chain[1]["rotation"][1] == math.pi / 2
    assert abs(row["after"]["min"][2] + 1.0) < 1e-4

    dump = build_fixture(tmp_path / "dump")
    assert print_model_debug("Npc_Kokiri_F_01", dump=str(dump), banc=KOKIRI_STATIC) == 0
    text = capsys.readouterr().out
    assert "skin" in text
    assert "translate 2.00000 0.00000 3.00000" in text
    assert "Fado" in text
    capsys.readouterr()
    assert print_model_debug("Npc_Kokiri_F_01", as_json=True, dump=str(dump), banc=KOKIRI_STATIC) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["placements"][0]["translate"] == [2.0, 0.0, 3.0]
    assert payload["files"][0]["models"][0]["shapes"]


def test_model_debug_near_lists_the_model_file(tmp_path, capsys):
    import json

    from bancwright.__main__ import print_model_debug

    dump = build_fixture(tmp_path / "dump")
    assert print_model_debug(None, dump=str(dump), banc=KOKIRI_STATIC, near="2,0,3", radius=4) == 0
    text = capsys.readouterr().out
    assert "Npc_Kokiri_F_01" in text
    assert "Model/Npc_Kokiri_F.Npc_Kokiri_F_01.bfres.zs" in text
    assert "Fado" in text
    assert "Presence=Default" in text
    assert "Npc_Kokiri_M_01" not in text
    assert "Obj_Tree" not in text
    capsys.readouterr()
    assert print_model_debug(None, as_json=True, dump=str(dump), banc=KOKIRI_STATIC, near="2, 0, 3", radius=4) == 0
    payload = json.loads(capsys.readouterr().out)
    gyamls = [row["gyaml"] for row in payload["near"]["actors"]]
    assert "Npc_Kokiri_F_01" in gyamls
    assert "Npc_Kokiri_M_01" not in gyamls
    fado = next(row for row in payload["near"]["actors"] if row["gyaml"] == "Npc_Kokiri_F_01")
    assert fado["model"].endswith("Npc_Kokiri_F.Npc_Kokiri_F_01.bfres.zs")
    assert fado["distance"] == 0
    assert print_model_debug(None, dump=str(dump), near="2,0,3") == 2


def _fork_pack(glb, conn):
    from bancwright.formats.meshopt import finish_glb

    packed = finish_glb(glb)
    conn.send(b"EXT_meshopt_compression" in packed)
    conn.close()


def test_a_dead_meshopt_helper_restarts_without_a_broken_pipe(monkeypatch, tmp_path):
    import contextlib
    import gc
    import io
    import os
    import sys

    from bancwright.formats import meshopt
    from bancwright.formats.gltf import Primitive, png_rgba, to_glb

    log = tmp_path / "meshopt.log"
    monkeypatch.setattr(meshopt, "_LOG", log)
    png = png_rgba(2, 2, bytes([1, 2, 3, 255]) * 4)
    glb = to_glb([Primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], png=png, name="Pot")])
    assert b"EXT_meshopt_compression" in meshopt.finish_glb(glb)
    proc = meshopt._PROCS[os.getpid()]
    proc.kill()
    proc.wait(timeout=5)
    caught = []
    previous = sys.unraisablehook

    def hook(unraisable):
        caught.append(unraisable)

    sys.unraisablehook = hook
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            packed = meshopt.finish_glb(glb)
            gc.collect()
    finally:
        sys.unraisablehook = previous
    noise = stderr.getvalue() + "".join(f"{item.exc_type} {item.exc_value}" for item in caught)
    assert "BrokenPipe" not in noise
    assert "Exception ignored" not in noise
    assert b"EXT_meshopt_compression" in packed
    assert "start" in log.read_text(encoding="utf-8")
    assert meshopt._PROCS[os.getpid()].poll() is None


def test_a_forked_worker_does_not_kill_the_parent_meshopt_helper():
    import multiprocessing
    import os

    from bancwright.formats.gltf import Primitive, png_rgba, to_glb
    from bancwright.formats.meshopt import _PROCS, finish_glb

    png = png_rgba(2, 2, bytes([4, 5, 6, 255]) * 4)
    glb = to_glb([Primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], [(0, 0), (1, 0), (0, 1)], png=png, name="Lamp")])
    assert b"EXT_meshopt_compression" in finish_glb(glb)
    parent = _PROCS[os.getpid()]
    node_pid = parent.pid
    ctx = multiprocessing.get_context("fork")
    reader, writer = ctx.Pipe(duplex=False)
    child = ctx.Process(target=_fork_pack, args=(glb, writer))
    child.start()
    writer.close()
    packed = reader.recv()
    child.join(30)
    assert child.exitcode == 0
    assert packed is True
    assert parent.pid == node_pid
    assert parent.poll() is None


def test_import_cache_stores_meshopt_geometry_and_keeps_the_texture():
    from bancwright.formats.gltf import Primitive, png_rgba, to_glb
    from bancwright.formats.meshopt import finish_glb

    png = png_rgba(8, 8, bytes([220, 20, 20, 255]) * 64)
    positions = [(0.0, 6.0, 0.0), (1.0, 6.0, 0.0), (0.0, 7.0, 0.0)]
    glb = finish_glb(to_glb([Primitive(positions, [0, 1, 2], [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], png=png, name="Body:0")]))
    packed = _packed_document(glb)
    assert "EXT_meshopt_compression" in packed.get("extensionsUsed", [])
    assert packed["images"][0]["mimeType"] == "image/png"
    doc, blob = _glb_parts(glb)
    got = _accessor_floats(doc, blob, doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"], 3)
    assert abs(got[0][1] - 6.0) < 1e-4
    pixel = _png_pixel(glb)
    assert pixel[0] > 180 and pixel[1] < 80


def _document(glb: bytes) -> dict:
    import json
    import struct

    glb = _stored(glb)
    return json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])


def _packed_document(glb: bytes) -> dict:
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


def test_material_debug_reads_the_shader_assign_and_leaves_uvs_alone(tmp_path, capsys):
    import json
    import struct

    from bancwright.__main__ import print_material_debug
    from bancwright.formats.bfres import parse, sample_bfres, sample_v10_bfres

    shader = {
        "archive": "Land.sharc",
        "model": "Land_Solid",
        "attributes": {"in_uv0": "_u1"},
        "samplers": {"_a0": "_u1"},
        "options": {"enable_alphatest": "1", "projection_type": "triplanar"},
        "params": [
            {"name": "albedo_color", "kind": 0x0F, "values": [0.2, 0.4, 0.6, 1.0]},
            {"name": "albedo_texsrt", "kind": 0x1E, "mode": 0, "scale": [2.0, 3.0], "rotate": 0.25, "translate": [0.1, -0.2]},
        ],
    }
    mesh = sample_bfres(
        "Leaf",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        bytes([20, 140, 40, 255]) * 4,
        2,
        2,
        samplers=[("_a0", "Leaf_Alb")],
        shader=shader,
        embed_texture=False,
    )
    path = tmp_path / "leaf.bfres"
    path.write_bytes(mesh)
    assert print_material_debug(str(path), as_json=True) == 0
    report = json.loads(capsys.readouterr().out)
    row = report["materials"][0]
    assert row["archive"] == "Land.sharc"
    assert row["shader"] == "Land_Solid"
    assert row["options"]["enable_alphatest"] == "1"
    assert row["attributes"]["in_uv0"] == "_u1"
    assert row["projection"]["projection_type"] == "triplanar"
    assert row["binds"][0]["role"] == "albedo"
    assert row["binds"][0]["uv"] == "_u1"
    assert row["binds"][0]["uvSource"] == "sampler assign"
    assert row["binds"][0]["srt"]["name"] == "albedo_texsrt"
    assert row["binds"][0]["srt"]["scale"] == [2.0, 3.0]
    shape = report["shapes"][0]
    assert shape["uvs"][0]["name"] == "_u0"
    assert "f32_2" in shape["uvs"][0]["format"]
    assert shape["uvs"][0]["min"] == [0.0, 0.0]
    assert shape["uvs"][0]["max"] == [1.0, 1.0]
    assert shape["uvs"][0]["samples"][0]["decoded"][:2] == [0.0, 0.0]
    assert shape["uvs"][0]["samples"][1]["decoded"][:2] == [1.0, 0.0]
    assert shape["uvs"][0]["samples"][0]["raw"]
    found_strings = [item["text"] for item in report["strings"]]
    assert "enable_alphatest" in found_strings
    enable = next(item for item in report["strings"] if item["text"] == "enable_alphatest")
    assert enable["refs"]
    assert row["viewerAlbedo"]["sampler"] == "_a0"
    assert row["viewerAlbedo"]["texture"] == "Leaf_Alb"
    by_name = {item["name"]: item for item in row["params"]}
    assert by_name["albedo_color"]["value"] == [0.2, 0.4, 0.6, 1.0]
    assert by_name["albedo_texsrt"]["mode"] == "maya"
    assert by_name["albedo_texsrt"]["scale"] == [2.0, 3.0]
    assert parse(mesh)[0].parts[0].uvs == [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]

    bare = sample_v10_bfres("Rock", [{"name": "Mt_Rock", "samplers": [("_a0", "Rock_Alb"), ("_n0", "Rock_Nrm")]}])
    assert parse(bare)[0].materials[0].archive == ""
    assert parse(bare)[0].materials[0].params == []
    v10 = sample_v10_bfres(
        "Gate",
        [{"name": "Mt_Gate", "samplers": [("_n0", "Gate_Nrm"), ("_a0", "Gate_Alb")], "shader": shader}],
    )
    material = parse(v10)[0].materials[0]
    assert material.archive == "Land.sharc"
    assert material.shader == "Land_Solid"
    assert material.attributes["in_uv0"] == "_u1"
    assert material.assign["_a0"] == "_u1"
    assert material.params[1]["kind"] == "texsrt"
    assert material.albedo[0] == ("_a0", "Gate_Alb")
    # Retail v10 keeps the dictionaries and leaves the count bytes at +0x44 clear.
    zeroed = bytearray(v10)
    at = zeroed.find(b"FMAT")
    assign = struct.unpack_from("<Q", zeroed, at + 0x10)[0]
    zeroed[assign + 0x44 : assign + 0x48] = b"\0\0\0\0"
    loose = parse(bytes(zeroed))[0].materials[0]
    assert loose.archive == "Land.sharc"
    assert loose.shader == "Land_Solid"
    assert loose.attributes["in_uv0"] == "_u1"
    assert loose.assign["_a0"] == "_u1"
    assert loose.options["projection_type"] == "triplanar"
    assert any(item["kind"] == "texsrt" for item in loose.params)
    assert loose.slots == []
    # A param row with no name of its own is not filled in from the dictionary.
    blank = bytearray(v10)
    param_array = struct.unpack_from("<Q", blank, at + 0x48)[0]
    struct.pack_into("<Q", blank, param_array + 8, 0)
    struct.pack_into("<Q", blank, param_array + 0x20 + 8, 0)
    assert parse(bytes(blank))[0].materials[0].params == []


def test_material_debug_opens_a_dump_relative_bfres_and_keeps_gyaml_lookup(tmp_path, capsys):
    from bancwright.__main__ import print_material_debug
    from bancwright.formats.bfres import sample_v10_bfres
    from bancwright.formats.zsio import compress

    dump = Dump(build_fixture(tmp_path / "dump"))
    mesh = sample_v10_bfres("Garden", [{"name": "Mt_Garden", "samplers": [("_a0", "Garden_Alb")]}])
    _install(dump, "GardenActor", mesh)
    assert print_material_debug("GardenActor", dump=str(dump.root)) == 0
    text = capsys.readouterr().out
    assert "Model/GardenActor.GardenActor.bfres.zs" in text
    assert "Model/Model/" not in text

    relative = "Model/Land_HrlCastle_HyruleCastle_A.Land_HrlCastle_Corridor_Garden_A_01.bfres.zs"
    dest = dump.romfs / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(compress(mesh, 1, dump.dicts(), level=3))
    assert print_material_debug(relative, dump=str(dump.root), strings=True) == 0
    text = capsys.readouterr().out
    assert relative in text
    assert "Model/Model/" not in text
    assert "string pool" in text
    assert print_material_debug("Model/Missing.bfres.zs", dump=str(dump.root)) == 2
    err = capsys.readouterr().err
    assert "Model/Model/" not in err
    assert "Missing.bfres.zs" in err


def _retail_assign(mesh: bytes) -> bytes:
    """Point FMAT +0x10 and +0xA8 at a string-list assign and a count block."""
    import struct

    blob = bytearray(mesh)

    def align() -> None:
        while len(blob) % 8:
            blob.append(0)

    def add_string(text: str) -> int:
        if len(blob) % 2:
            blob.append(0)
        at = len(blob)
        raw = text.encode()
        blob.extend(struct.pack("<H", len(raw)) + raw + b"\0")
        return at

    def string_list(names: list[str]) -> int:
        offsets = [add_string(name) for name in names]
        align()
        at = len(blob)
        for offset in offsets:
            blob.extend(struct.pack("<Q", offset))
        blob.extend(struct.pack("<Q", 0))
        return at

    labels = string_list(["Model", "model"])
    attributes = string_list(["_p0", "_n0", "_t0", "_u0", "_c0", "bake"])
    samplers = string_list(["_a0", "_n0", "_rah0", "_a1", "_n1", "_rah1"])
    align()
    byte_at = len(blob)
    blob.extend(bytes([0x00, 0x01, 0x02, 0x03, 0x06, 0x05, 0x00, 0x01, 0x02, 0x03, 0xFF, 0x05, 0x04, 0x00, 0x00, 0x00]))
    align()
    info = len(blob)
    blob.extend(struct.pack("<IIII", 0x00031B98, 0x000197F0, 9, 1))
    align()
    block = len(blob)
    blob.extend(struct.pack("<QQQQ", labels, attributes, byte_at, samplers))
    align()
    side = len(blob)
    blob.extend(struct.pack("<QQQQ", labels, info, 0, 0))
    at = blob.find(b"FMAT")
    struct.pack_into("<Q", blob, at + 0x10, block)
    struct.pack_into("<Q", blob, at + 0xA8, side)
    return bytes(blob)


def test_retail_v10_shader_block_is_printed_and_not_applied(tmp_path, capsys):
    import json

    from bancwright.__main__ import print_material_debug
    from bancwright.formats.bfres import parse, sample_v10_bfres

    mesh = _retail_assign(
        sample_v10_bfres("Roof", [{"name": "Mt_Roof", "samplers": [("_a0", "Slate_Alb")]}], shapes=[{"name": "pCube", "material": 0}])
    )
    path = tmp_path / "roof.bfres"
    path.write_bytes(mesh)
    model = parse(mesh)[0]
    assert model.parts[0].uvs == [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    names = [item["name"] for item in model.parts[0].vertex_attributes]
    assert names == ["_p0", "_u0"]
    assert model.parts[0].uv_sets[0]["name"] == "_u0"
    material = model.materials[0]
    primary = next(block for block in material.shader_blocks if block["slot"] == "+0x10")
    assert primary["attributeNames"] == ["_p0", "_n0", "_t0", "_u0", "_c0", "bake"]
    assert primary["samplerNames"] == ["_a0", "_n0", "_rah0", "_a1", "_n1", "_rah1"]
    unused = [item for item in primary["byteMap"] if item["unused"]]
    assert unused and unused[0]["value"] == 0xFF
    assert "not applied" in primary["byteNote"]
    side = next(block for block in material.shader_blocks if block["slot"] == "+0xA8")
    assert side["labels"] == ["Model", "model"]
    assert 9 in side["children"][0]["dwords"]
    assert 1 in side["children"][0]["dwords"]
    assert print_material_debug(str(path), as_json=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["shapes"][0]["attributes"][0]["name"] == "_p0"
    assert "_c0" in report["materials"][0]["shaderBlocks"][0]["attributeNames"]


def test_tex_debug_prints_the_header_and_writes_the_current_png(tmp_path, capsys):
    import struct

    from bancwright.__main__ import print_tex_debug
    from bancwright.formats.bntx import texture_headers, write_rgba
    from bancwright.formats.zsio import compress

    stored = write_rgba("Slate_Alb", 8, 8, bytes([11, 22, 33, 255]) * 64)
    patched = bytearray(stored)
    at = patched.find(b"BRTI")
    struct.pack_into("<ii", patched, at + 0x24, 455, 455)
    header = texture_headers(bytes(patched))[0]
    assert header["width"] == 455 and header["height"] == 455
    assert header["format"] == 0x0B01
    assert header["depth"] == 1

    dump = Dump(build_fixture(tmp_path / "dump"))
    dest = dump.romfs / "Tex" / "Slate_Alb.bntx.zs"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(compress(stored, 1, dump.dicts(), level=3))
    png = tmp_path / "slate.png"
    assert print_tex_debug("Slate_Alb", png=str(png), dump=str(dump.root)) == 0
    text = capsys.readouterr().out
    assert "8x8" in text
    assert "format 0x0B01" in text
    assert "tile mode" in text
    assert "block height" in text
    assert "alignment shift" in text
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


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
        assert info["textures"] == 1, gyaml
        assert "water stand-in" in (info["reason"] or ""), gyaml
        assert info["textureLog"][0].get("standIn") == "water"
        doc = _document((tmp_path / "cache" / info["digest"][:2] / f"{info['digest']}.glb").read_bytes())
        mat = doc["materials"][0]
        factor = mat["pbrMetallicRoughness"]["baseColorFactor"]
        assert mat["alphaMode"] == "BLEND"
        assert "baseColorTexture" in mat["pbrMetallicRoughness"]
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
    from io import BytesIO

    from PIL import Image

    doc, data = _glb_parts(glb)
    view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    start = view.get("byteOffset", 0)
    raw = data[start : start + view["byteLength"]]
    assert raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:2] == b"\xff\xd8"
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
    assert texture_rank("BarkFx") == 2
    assert texture_rank("Tree_Alb_Fx") == 2
    assert texture_rank("Wind") == 1

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
    assert abs(factor[0] - 0.18) < 0.02
    assert abs(factor[1] - 0.42) < 0.02
    assert abs(factor[2] - 0.48) < 0.02
    assert abs(factor[3] - 0.62) < 0.02
    assert "baseColorTexture" in doc["materials"][0]["pbrMetallicRoughness"]
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


def test_sparse_magenta_on_an_opaque_albedo_is_filled_not_punched():
    from bancwright.formats.bntx import Texture
    from bancwright.models import _accept

    rgba = bytearray([20, 90, 30, 255]) * 4
    rgba[4:8] = bytes([220, 10, 210, 255])
    image = Texture(name="Bark", width=2, height=2, rgba=bytes(rgba), fmt=0)
    accepted, mode, _cutoff, note = _accept(image, "opaque", 0.5)
    assert mode == "opaque"
    assert "neighbour" in note
    assert accepted.rgba[4] < 80 and accepted.rgba[6] < 80
    wide = bytearray()
    for x in range(14):
        wide.extend((220, 10, 210, 255) if 2 <= x <= 11 else (20, 90, 30, 255))
    gutter = Texture(name="BarkEdge", width=14, height=1, rgba=bytes(wide), fmt=0)
    filled, mode, _cutoff, _note = _accept(gutter, "opaque", 0.5)
    assert mode == "opaque"
    assert filled.rgba[6 * 4] < 80
    solid = Texture(name="Bad", width=2, height=2, rgba=bytes([255, 0, 255, 255]) * 4, fmt=0)
    rejected, _mode, _cutoff, why = _accept(solid, "opaque", 0.5)
    assert rejected is None
    assert "magenta" in why


def test_neutral_blend_keeps_both_materials_and_holes_copy_a_neighbour():
    from bancwright.formats.terrain import SIZE, _fill_pairs, read_terrain_material

    raw = bytes([15, 11, 0x7F, 0x7F]) * (SIZE * SIZE)
    layers, pairs, note = read_terrain_material(raw)
    assert layers is not None and pairs is not None
    assert layers[0] == 15
    assert pairs[0] == (15, 11, 0.5)
    assert "0x7f" in note
    hole = bytearray(raw)
    hole[0:4] = bytes([255, 127, 127, 127])
    _layers, opened, _note = read_terrain_material(bytes(hole))
    assert opened[0][0] < 0
    filled = _fill_pairs(opened)
    assert filled[0][0] == 15 and filled[0][1] == 11


def test_leaf_tiles_stay_under_a_triangle_budget():
    from bancwright.formats.terrain import SIZE, build_scene, sample_steps

    quads = []
    tiles = []
    for index in range(30):
        name = f"T{index:02d}"
        center = (float((index % 6) * 10), float((index // 6) * 10))
        quads.append((center[0], center[1], 8.0, name))
        tiles.append((center[0], center[1], 8.0, name, [0.5] * (SIZE * SIZE), None))
    coarse = (20.0, 10.0, 64.0, "Coarse", [0.5] * (SIZE * SIZE), None)
    steps, note = sample_steps(quads + [coarse[:4]], 1.0, (0.0, 0.0, 0.0), (20.0, 10.0))
    assert min(steps.values()) >= 8
    assert "m apart" in note
    parts, reports = build_scene(tiles + [coarse], 1.0, 10.0, steps=steps)
    total = sum(len(part.indices) // 3 for part in parts)
    assert total < 150000
    alone, _reports = build_scene([coarse], 1.0, 10.0, steps=steps)
    alone_tris = sum(len(part.indices) // 3 for part in alone)
    coarse_tris = next(row.triangles for row in reports if row.name == "Coarse")
    assert coarse_tris < alone_tris
    assert total > coarse_tris


def test_workers_ignore_sigint_and_the_pool_closes_quietly():
    import signal

    from bancwright.server import _close_pool, _ignore_sigint

    previous = signal.getsignal(signal.SIGINT)
    try:
        _ignore_sigint()
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, previous)
    _close_pool()

"""Synthetic BFRES, BNTX, and the actor-model cache. No game data."""

import os
from pathlib import Path

from bancwright.actors import is_volume, view_actor
from bancwright.dump import Dump
from bancwright.fixtures import KOKIRI_STATIC, build_fixture
from bancwright.formats.bfres import _rigid, parse, sample_bfres
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
    assert "Npc_Kokiri_F_01.bfres" in fado["source"]
    glb = cache_dir() / fado["digest"][:2] / f"{fado['digest']}.glb"
    assert glb.is_file()
    assert glb.read_bytes()[:4] == b"glTF"
    assert "repos" not in str(glb)
    repo = Path(__file__).resolve().parents[1]
    assert not str(glb).startswith(str(repo))

    tree = describe_actor(dump, "Obj_Tree")
    assert tree["status"] == "mesh"
    assert tree["source"].endswith("Model/Obj_Tree.bfres.zs")
    again = describe_actor(dump, "Obj_Tree")
    assert again["digest"] == tree["digest"]

    baba = describe_actor(dump, "Enemy_DekuBaba")
    assert baba["status"] == "box"
    assert baba["reason"]
    assert "BFRES" in baba["reason"] or "bfres" in baba["reason"].lower() or "not a BFRES" in baba["reason"]

    missing = describe_actor(dump, "Npc_Kokiri_M_01")
    assert missing["status"] == "box"
    assert "No model" in missing["reason"]

    assert describe_actor(dump, "../etc/passwd")["status"] == "box"

    terrain = describe_terrain(dump, KOKIRI_STATIC)
    assert terrain["status"] == "mesh"
    assert terrain["source"].endswith("KokiriForest_Child_Static.bfres.zs")
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
    assert is_volume("Obj_TriggerBox", [1, 1, 1]) is True

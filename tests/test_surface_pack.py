"""Packed walkable-surface format, exclusion, and dump snap points."""

from __future__ import annotations

from pathlib import Path

import pytest

from ocarina_forge.surface_pack import (
    HEIGHT_COUNT,
    MAGIC,
    STEP,
    pack_surface,
    sample_unpacked,
    unpack_surface,
)

ISLAND = (-192.39, 506.95, 131.353)
ISLAND_B = (-195.2242, 519.2956, 131.551)
FIELD = (0.0, 0.0, 147.441)
DYNAMIC = (-250.0, 400.0, 151.780)
HYRULE = "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs"
HYRULE_DYNAMIC = "Banc/HyruleField/HyruleField_Child_Dynamic.bcett.byml.zs"


def _tile(name, origin_height, *, edge=2.0, layer=0, center=(0.0, 0.0)):
    heights = [origin_height] * HEIGHT_COUNT
    return {"layer": layer, "name": name, "center": list(center), "edge": edge, "heights": heights}


def test_round_trip_and_exclusion():
    meshes = [
        {
            "hash": 2,
            "positions": [(-1.0, 10.0, -1.0), (1.0, 10.0, -1.0), (0.0, 10.0, 1.0)],
            "indices": [0, 1, 2],
        },
        {
            "hash": 3,
            "positions": [(-1.0, 20.0, -1.0), (1.0, 20.0, -1.0), (0.0, 20.0, 1.0)],
            "indices": [0, 1, 2],
        },
    ]
    raw = pack_surface(meshes, {"origin": [0.0, 0.0, 0.0], "scale": 1.0, "heightScale": 1.0, "tiles": []})
    assert raw[:4] == MAGIC
    pack = unpack_surface(raw)
    assert pack["triangleCount"] == 2
    assert pack["vertexCount"] == 6
    hit = sample_unpacked(pack, 0.0, 0.0)
    assert hit["worldY"] is not None
    assert abs(hit["worldY"] - 20.0) < 1e-6
    skipped = sample_unpacked(pack, 0.0, 0.0, exclude_hash=3)
    assert abs(skipped["worldY"] - 10.0) < 1e-6


def test_heightmap_and_mesh_compose():
    meshes = [
        {
            "hash": 1,
            "positions": [(-1.0, 131.353, -1.0), (1.0, 131.353, -1.0), (0.0, 131.353, 1.0)],
            "indices": [0, 1, 2],
        }
    ]
    raw = pack_surface(
        meshes,
        {
            "origin": [0.0, 0.0, 0.0],
            "scale": 1.0,
            "heightScale": 1.0,
            "tiles": [_tile("lake", 102.6, edge=8.0, center=(0.0, 0.0))],
        },
    )
    pack = unpack_surface(raw)
    hit = sample_unpacked(pack, 0.0, 0.0)
    assert abs(hit["worldY"] - 131.353) < 1e-4
    assert hit["source"] == "mesh"


def _real_dump():
    path = Path(r"E:\sw2\oot\stable\ray-test")
    if not path.is_dir():
        pytest.skip("The ray-test dump is not on this machine.")
    from ocarina_forge.dump import Dump

    return Dump(path)


def _close(actual, expected):
    assert actual is not None
    assert abs(actual - expected) < 1e-3


def test_pack_matches_query_ground_on_hyrule_field():
    dump = _real_dump()
    from ocarina_forge.grounddebug import query_ground
    from ocarina_forge.surface_pack import build_surface, unpack_surface
    import gzip

    compressed, meta, raw = build_surface(dump, HYRULE)
    assert meta["triangles"] > 0
    pack = unpack_surface(raw)
    points = [(ISLAND[0], ISLAND[1]), (ISLAND_B[0], ISLAND_B[1]), (FIELD[0], FIELD[1]), (DYNAMIC[0], DYNAMIC[1])]
    expected_y = [ISLAND[2], ISLAND_B[2], FIELD[2], DYNAMIC[2]]
    truth = query_ground(dump, HYRULE, points)
    for point, want, sample in zip(points, expected_y, truth["samples"]):
        packed = sample_unpacked(pack, point[0], point[1])
        assert packed["worldY"] is not None
        assert sample["worldY"] is not None
        assert abs(packed["worldY"] - sample["worldY"]) < 1e-3
        _close(packed["worldY"], want)
    # The Dynamic banc should agree at the field point named for it.
    dynamic = query_ground(dump, HYRULE_DYNAMIC, [(DYNAMIC[0], DYNAMIC[1])])
    packed = sample_unpacked(unpack_surface(build_surface(dump, HYRULE_DYNAMIC)[2]), DYNAMIC[0], DYNAMIC[1])
    assert abs(packed["worldY"] - dynamic["samples"][0]["worldY"]) < 1e-3
    _close(dynamic["samples"][0]["worldY"], DYNAMIC[2])


def test_gzip_round_trip_magic():
    import gzip

    raw = pack_surface([], {"origin": [0.0, 1.0, 0.0], "scale": 1.0, "heightScale": 1.0, "tiles": []})
    again = gzip.decompress(gzip.compress(raw))
    assert again[:4] == MAGIC
    pack = unpack_surface(again)
    assert pack["origin"][1] == 1.0

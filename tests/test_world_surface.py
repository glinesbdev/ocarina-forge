"""Classifier, triangle sampling, composed heights, and sentinel zeros."""

import math
import struct

from ocarina_forge.formats.terrain import SIZE, _as_floats, read_height_file
from ocarina_forge.world_surface import (
    MeshIndex,
    _keep_visual_part,
    actor_ident,
    actor_translate_overlaps,
    canonical_actor_ids,
    collect_terrain_surfaces,
    compose_height,
    local_to_world,
    terrain_actor,
    triangle_height,
)


def test_terrain_actor_includes_islands_and_excludes_scenery():
    assert terrain_actor("Land_LakeHrl_Island_E_05")
    assert terrain_actor("Land_LakeHrl_Island_C_01")
    assert terrain_actor("Land_LakeHrl_Cliff_B_01")
    assert terrain_actor("Land_LakeHrl_CliffEdge_A_01")
    assert terrain_actor("Land_LakeHrl_Soil_A_01")
    assert terrain_actor("Land_LakeHrl_MatsuCliff_A_01")
    assert terrain_actor("Land_Kokiri_Ground_B_08")
    assert not terrain_actor("Land_Kokiri_WaterPlane_A_02")
    assert not terrain_actor("Land_Town_HouseFloor")
    assert not terrain_actor("Land_Town_GroundDetail")
    assert not terrain_actor("Land_HyruleField_Bridge_A_01")
    assert not terrain_actor("Obj_Tree_A_01")
    assert not terrain_actor("Land_Kokiri_Tree_A_01")


def test_triangle_height_is_barycentric_in_xz():
    flat = (0.0, 10.0, 0.0, 4.0, 10.0, 0.0, 0.0, 14.0, 4.0)
    assert abs(triangle_height(1.0, 1.0, flat) - 11.0) < 1e-9
    assert triangle_height(-1.0, 0.0, flat) is None
    sloped = (0.0, 0.0, 0.0, 2.0, 4.0, 0.0, 0.0, 0.0, 2.0)
    assert abs(triangle_height(1.0, 0.0, sloped) - 2.0) < 1e-9


def test_compose_height_takes_the_highest_real_surface():
    y, source, mesh = compose_height(102.6, {"worldY": 131.35, "gyaml": "Land_LakeHrl_Island_E_05"})
    assert abs(y - 131.35) < 1e-9 and source == "mesh"
    y, source, mesh = compose_height(140.0, {"worldY": 131.35, "gyaml": "Land_LakeHrl_Island_E_05"})
    assert abs(y - 140.0) < 1e-9 and source == "heightmap"
    y, source, mesh = compose_height(0.0, {"worldY": 131.35, "gyaml": "Land_LakeHrl_Island_E_05"})
    assert abs(y - 131.35) < 1e-9 and source == "mesh"
    y, source, mesh = compose_height(0.0, None)
    assert y is None and source is None
    y, source, mesh = compose_height(float("nan"), None)
    assert y is None


def test_mesh_index_returns_the_highest_triangle():
    index = MeshIndex(step=8.0)
    index.add((0.0, 10.0, 0.0, 4.0, 10.0, 0.0, 0.0, 10.0, 4.0), {"gyaml": "low"})
    index.add((0.0, 13.0, 0.0, 2.0, 13.0, 0.0, 0.0, 13.0, 2.0), {"gyaml": "high"})
    hit = index.sample(0.5, 0.5)
    assert hit["gyaml"] == "high"
    assert abs(hit["worldY"] - 13.0) < 1e-9
    assert index.sample(3.5, 3.5) is None


def test_rotated_triangle_follows_extrinsic_xyz():
    local = (1.0, 2.0, 0.0, 1.0, 2.0, 2.0, 3.0, 2.0, 0.0)
    rotate = (0.0, math.pi / 2, 0.0)
    translate = (10.0, 5.0, -3.0)
    scale = (1.0, 1.0, 1.0)
    a = local_to_world(local[0:3], translate, rotate, scale)
    b = local_to_world(local[3:6], translate, rotate, scale)
    c = local_to_world(local[6:9], translate, rotate, scale)
    world = (*a, *b, *c)
    # Yaw 90 deg sends local +X toward -Z and local +Z toward +X.
    mid = local_to_world((1.5, 2.0, 0.5), translate, rotate, scale)
    y = triangle_height(mid[0], mid[2], world)
    assert y is not None
    assert abs(y - mid[1]) < 1e-9


def test_remaining_exact_zeros_are_uncovered_after_column_repair():
    values = [0.2] * (SIZE * SIZE)
    values[5 * SIZE + 10] = 0.0
    for x in range(20, 24):
        values[8 * SIZE + x] = 0.0
    packed = struct.pack(f"<{SIZE * SIZE}f", *values)
    out = _as_floats(packed)
    assert out is not None
    assert math.isfinite(out[5 * SIZE + 10])
    assert abs(out[5 * SIZE + 10] - 0.2) < 1e-6
    assert all(not math.isfinite(out[8 * SIZE + x]) for x in range(20, 24))
    assert sum(1 for value in out if value == 0.0) == 0


def test_read_height_file_treats_an_all_zero_tile_as_missing():
    header = bytearray(0x40)
    struct.pack_into("<I", header, 0x2C, 0x40 - 0x2C)
    packed = struct.pack(f"<{SIZE * SIZE}f", *([0.0] * (SIZE * SIZE)))
    struct.pack_into("<I", header, 0x30, len(packed))
    decoded, _layers, _note = read_height_file(bytes(header) + packed)
    assert all(not math.isfinite(value) for value in decoded)
    assert sum(1 for value in decoded if value == 0.0) == 0
def test_water_parts_are_front_or_surface_not_polysurface():
    assert _keep_visual_part("Surface__Mt_Water_LakeHrl_WaterPlane_A_Surface", water=True)
    assert _keep_visual_part("Surface_06__Mt_Water_LakeHrl_WaterPlane_A_Surface", water=True)
    assert _keep_visual_part("Front__Mt_Water_Front", water=True)
    assert _keep_visual_part("pPlane_front__Mt_Water_Front", water=True)
    assert _keep_visual_part("front__Mt_Water_Front", water=True)
    assert not _keep_visual_part("Back__Mt_Water_Back", water=True)
    assert not _keep_visual_part("Box__Mt_Water_LakeHrl_WaterPlane_A_Box", water=True)
    assert not _keep_visual_part("pPlane_back__Mt_Water_Back", water=True)
    assert not _keep_visual_part("polySurface12__Mt_Basin", water=True)
    assert _keep_visual_part("polySurface12__Mt_Basin", water=False)

def test_actor_translate_overlaps_rejects_far_pivots():
    bounds = (-10.0, -10.0, 10.0, 10.0)
    assert actor_translate_overlaps((-192.39, 131.35, 506.95), None)
    assert actor_translate_overlaps((0.0, 0.0, 0.0), bounds)
    assert actor_translate_overlaps((250.0, 0.0, 0.0), bounds)
    assert not actor_translate_overlaps((400.0, 0.0, 0.0), bounds)
    assert not actor_translate_overlaps((0.0, 0.0, -400.0), bounds)

def test_canonical_actor_ids_match_frontend_hashes():
    assert actor_ident({"Hash": 2}) == "0x0000000000000002"
    assert "0x0000000000000002" in canonical_actor_ids(["0x2", 2])
    assert actor_ident({"Gyaml": "Land_LakeHrl_CliffEdge_A_02"}) == ""


def test_collect_terrain_surfaces_skips_the_excluded_land_actor(monkeypatch):
    island = {
        "Gyaml": "Land_LakeHrl_Island_E_05",
        "Hash": 1,
        "Translate": [0.0, 131.0, 0.0],
        "Rotate": [0.0, 0.0, 0.0],
        "Scale": [1.0, 1.0, 1.0],
    }
    cliff = {
        "Gyaml": "Land_LakeHrl_CliffEdge_A_02",
        "Hash": 2,
        "Translate": [0.0, 200.0, 0.0],
        "Rotate": [0.0, 0.0, 0.0],
        "Scale": [1.0, 1.0, 1.0],
    }

    def floors(_dump, gyaml, _cache):
        return [[-1.0, 0.0, -1.0, 1.0, 0.0, -1.0, 0.0, 0.0, 1.0]], "phive"

    monkeypatch.setattr("ocarina_forge.world_surface._iter_actors", lambda *_: [("Banc/A", 0, island), ("Banc/A", 1, cliff)])
    monkeypatch.setattr("ocarina_forge.world_surface.local_floor_triangles", floors)
    both = collect_terrain_surfaces(None, "Banc/A.bcett.byml.zs")
    assert both.sample(0.0, 0.0)["worldY"] == 200.0
    without_self = collect_terrain_surfaces(None, "Banc/A.bcett.byml.zs", exclude_ids=["0x2"])
    assert without_self.sample(0.0, 0.0)["worldY"] == 131.0


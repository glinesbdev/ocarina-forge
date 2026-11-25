"""Synthetic checks for height-diff: results loading, water planes, and error patterns."""

import json
from pathlib import Path

from ocarina_forge.heightdiff import (
    banc_for_map,
    compare_area,
    detect_patterns,
    error_stats,
    inverse_euler_xyz,
    is_water_plane,
    load_results,
    local_to_world,
    render_text,
    sample_water,
    world_to_local,
    _euler_xyz,
)


def test_banc_for_map_uses_static_sibling():
    assert banc_for_map("KokiriForest_Child") == "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"
    assert banc_for_map("HyruleField_Child") == "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs"
    assert banc_for_map("Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs").endswith("_Static.bcett.byml.zs")


def test_is_water_plane_skips_effects_and_props():
    assert is_water_plane("Obj_LakeHrl_WaterPlane_A_05")
    assert is_water_plane("Land_Kokiri_WaterPlane_A_02")
    assert is_water_plane("Land_ShallowWaterPlane_A_01")
    assert not is_water_plane("EftLoc_HrlField_TerrainMock_WaterPlane_WaterFall_Basin_00")
    assert not is_water_plane("Land_Kokiri_WaterSideProp_A_04")
    assert not is_water_plane("Obj_LakeHrl_FishingPondFlag_A_01")


def test_load_results_reads_storage_chunks(tmp_path):
    payload = {
        "format": 1,
        "mod": "local/height-check",
        "data": {
            "heights": {
                "schema": 2,
                "status": "waiting",
                "hasWorldRay": True,
                "mapsSeen": ["KokiriForest_Child"],
                "areas": {
                    "kokiri": {
                        "id": "kokiri",
                        "label": "Kokiri Forest",
                        "map": "KokiriForest_Child",
                        "done": True,
                        "chunks": 2,
                    }
                },
            },
            "heights:kokiri:0": {
                "fields": ["x", "z", "water_y", "ground_y", "water_kind", "ground_kind",
                           "water_nx", "water_ny", "water_nz", "ground_nx", "ground_ny", "ground_nz",
                           "water_hit", "ground_hit", "skipped"],
                "rows": [[0, 0, None, 110.5, None, "ground", None, None, None, 0, 1, 0, False, True, None]],
            },
            "heights:kokiri:1": {
                "fields": ["x", "z", "water_y", "ground_y", "water_kind", "ground_kind",
                           "water_nx", "water_ny", "water_nz", "ground_nx", "ground_ny", "ground_nz",
                           "water_hit", "ground_hit", "skipped"],
                "rows": [[10, 0, 109.2277, 108.85, "water", "ground", 0, 1, 0, 0, 1, 0, True, True, None]],
            },
        },
    }
    path = tmp_path / "heights.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    results = load_results(path)
    assert results["hasWorldRay"] is True
    points = results["areas"]["kokiri"]["points"]
    assert len(points) == 2
    assert points[0]["ground_y"] == 110.5 and points[0]["water_hit"] is False
    assert points[1]["water_y"] == 109.2277 and points[1]["water_hit"] is True


def test_euler_roundtrip_and_yaw_only_water_box():
    rotate = (0.0, 0.7, 0.0)
    translate = (10.0, 5.0, -3.0)
    scale = (2.0, 1.0, 2.0)
    local = (1.5, 0.0, -0.25)
    world = local_to_world(local, translate, rotate, scale)
    back = world_to_local(world, translate, rotate, scale)
    assert abs(back[0] - local[0]) < 1e-9
    assert abs(back[1] - local[1]) < 1e-9
    assert abs(back[2] - local[2]) < 1e-9
    # Forward equals Rz Ry Rx
    vx, vy, vz = local[0] * scale[0], local[1] * scale[1], local[2] * scale[2]
    rotated = _euler_xyz(*rotate, vx, vy, vz)
    assert abs(world[0] - (rotated[0] + translate[0])) < 1e-9
    inv = inverse_euler_xyz(*rotate, *rotated)
    assert abs(inv[0] - vx) < 1e-9


def test_sample_water_uses_scaled_aabb_and_picks_higher_surface():
    low = {
        "gyaml": "LowPlane",
        "banc": "a",
        "index": 0,
        "translate": [0.0, 10.0, 0.0],
        "rotate": [0.0, 0.0, 0.0],
        "scale": [2.0, 1.0, 2.0],
        "local": {"min": [-5.0, 0.0, -5.0], "max": [5.0, 0.0, 5.0]},
    }
    high = {
        "gyaml": "HighPlane",
        "banc": "a",
        "index": 1,
        "translate": [0.0, 12.5, 0.0],
        "rotate": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
        "local": {"min": [-1.0, 0.0, -1.0], "max": [1.0, 0.0, 1.0]},
    }
    # Scale 2 makes the low plane cover 10 m; a point at x=8 is inside low, outside high.
    inside_low = sample_water([low, high], 8.0, 0.0)
    assert inside_low["gyaml"] == "LowPlane"
    assert abs(inside_low["worldY"] - 10.0) < 1e-9
    overlap = sample_water([low, high], 0.0, 0.0)
    assert overlap["gyaml"] == "HighPlane"
    assert abs(overlap["worldY"] - 12.5) < 1e-9
    assert sample_water([low, high], 20.0, 0.0) is None


def test_error_stats_and_constant_offset_pattern():
    pairs = [(10.0, 11.5), (20.0, 21.5), (0.0, 1.5), (4.0, 5.5),
             (8.0, 9.5), (12.0, 13.5), (16.0, 17.5), (18.0, 19.5)]
    stats = error_stats(pairs)
    assert stats["count"] == 8
    assert abs(stats["bias"] - 1.5) < 1e-9
    assert abs(stats["meanAbs"] - 1.5) < 1e-9
    assert stats["maxAbs"] == 1.5
    entries = [{"x": i, "z": 0.0, "gameGround": g, "groundDelta": 1.5, "nearSeam": False} for i, (g, _f) in enumerate(pairs)]
    notes = detect_patterns(entries, pairs, resample=None)
    kinds = {note["kind"] for note in notes}
    assert "constant-y-offset" in kinds


def test_detects_axis_flip_when_negating_x_matches():
    # Game heights follow x; a flipped sampler only matches at -x.
    entries = []
    pairs = []
    for x in range(8, 16):
        game = float(x)
        forge_at_x = float(-x)  # wrong if sampled at x
        entries.append({"x": float(x), "z": 1.0, "gameGround": game, "groundDelta": forge_at_x - game})
        pairs.append((game, forge_at_x))

    def resample(x, z):
        return float(-x)  # Forge stores the true height at -x

    notes = detect_patterns(entries, pairs, resample)
    flips = [note for note in notes if note["kind"] == "axis-transform"]
    assert any(note["transform"] == "neg-x" for note in flips)


def test_water_mismatch_and_render(monkeypatch):
    points = [
        {"x": 0.0, "z": 0.0, "water_y": 10.0, "ground_y": 9.0, "water_hit": True, "ground_hit": True},
        {"x": 3.0, "z": 0.0, "water_y": None, "ground_y": 11.0, "water_hit": False, "ground_hit": True},
    ]
    area = {"id": "lake-hylia", "label": "Lake Hylia", "map": "HyruleField_Child", "points": points}

    def fake_load(_dump, _banc):
        return {"kind": "heightmap", "source": "TerrainArc/Field/Field.tscb", "tiles": [], "origin": [0, 0, 0], "scale": 1, "heightScale": 1}

    def fake_ground(_report, x, z):
        return {"worldY": 9.0 if x == 0 else 11.25, "drawn": {"name": "t0", "edge": 10, "layer": "main"}, "nearSeam": False}

    def fake_planes(_dump, _banc, _cache):
        return [
            {
                "gyaml": "Obj_LakeHrl_WaterPlane_A_05",
                "banc": "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs",
                "index": 0,
                "translate": [0.0, 10.0, 0.0],
                "rotate": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "local": {"min": [-1.0, 0.0, -1.0], "max": [1.0, 0.0, 1.0]},
            }
        ]

    monkeypatch.setattr("ocarina_forge.heightdiff._load", fake_load)
    monkeypatch.setattr("ocarina_forge.heightdiff.sample_ground_at", fake_ground)
    monkeypatch.setattr("ocarina_forge.heightdiff.collect_water_planes", fake_planes)

    report = compare_area(None, area, "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs", {})
    assert report["ground"]["count"] == 2
    assert abs(report["ground"]["meanAbs"] - 0.125) < 1e-9  # only the second point differs by 0.25; mean abs (0+0.25)/2
    assert report["water"]["count"] == 1
    assert abs(report["water"]["bias"]) < 1e-9
    assert report["waterCoverage"]["game"] == 1
    assert report["waterCoverage"]["forge"] == 1
    assert report["waterCoverage"]["mismatches"] == 0
    text = render_text({"source": "synthetic.json", "mapsSeen": ["HyruleField_Child"], "areas": [report], "errors": []})
    assert "Lake Hylia" in text and "meanAbs" in text
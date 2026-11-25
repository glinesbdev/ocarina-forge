"""Lighting capture import, emitter listing, and the mod sources."""

import json
from pathlib import Path

from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import KOKIRI_STATIC, build_fixture
from ocarina_forge.lighting import (
    capture_root,
    collect_emitters,
    emitter_kind,
    import_capture,
    install_mod,
    lighting_report,
    load_reference,
    map_name_for_banc,
    normalize_sample,
    scenes_from_capture,
    windows_steps,
)


def test_map_name_strips_age_bank_and_suffix():
    assert map_name_for_banc(KOKIRI_STATIC) == "KokiriForest_Child"
    assert map_name_for_banc("Banc/HyruleField/HyruleField_Adult_Dynamic.bcett.byml.zs") == "HyruleField_Adult"


def test_emitter_kinds_follow_the_names_forge_already_uses():
    assert emitter_kind("Eft_Firefly") == "emitter"
    assert emitter_kind("EftLoc_HrlField_TerrainMock_WaterPlane_WaterFall_Basin_00") == "locator"
    assert emitter_kind("VolumeFogArea") == "fog-volume"
    assert emitter_kind("Npc_Kokiri_F_01") is None


def test_normalize_sample_drops_invented_or_invalid_light_values():
    sample = normalize_sample({
        "x": 1, "y": 2, "z": 3,
        "timeOfDay": 12.5,
        "sun": None,
        "fog": {"kind": "linear", "color": "#aabbcc", "near": 10, "far": 80},
        "exposure": 99,
        "tonemap": "filmic-guess",
        "effects": [{"type": "Eft_Firefly", "key": "a", "x": 1, "y": 2, "z": 3}],
        "observed": {"game": {"mystery": [1, 2, 3]}},
    })
    assert sample["sun"] is None
    assert sample["exposure"] is None
    assert sample["tonemap"] is None
    assert sample["fog"]["kind"] == "linear"
    assert sample["fog"]["color"] == "#aabbcc"
    assert sample["effects"][0]["type"] == "Eft_Firefly"
    assert sample["observed"]["game"]["mystery"] == [1, 2, 3]
    assert normalize_sample({"x": float("nan"), "y": 0, "z": 0}) is None


def test_import_splits_storage_chunks_and_does_not_keep_bad_colors(tmp_path, monkeypatch):
    monkeypatch.delenv("OCARINA_FORGE_LIGHTING", raising=False)
    payload = {
        "format": 1,
        "mod": "local/lighting-capture",
        "data": {
            "lighting": {
                "schema": 1,
                "unavailable": ["sun", "fog"],
                "probes": {"world.ray": False, "gfx.world": False},
                "notes": ["sun stays null"],
                "mapsSeen": ["KokiriForest_Child", "HyruleField_Child"],
            },
            "lighting:KokiriForest_Child": {
                "map": "KokiriForest_Child",
                "samples": [
                    {"x": 0, "y": 1, "z": 2, "timeOfDay": 8, "sun": None, "effects": []},
                    {"x": 12, "y": 1, "z": 4, "fog": {"color": [1.5, 0, 0], "near": 1, "far": 2}},
                ],
            },
            "lighting:HyruleField_Child": {
                "map": "HyruleField_Child",
                "samples": [{
                    "x": -20, "y": 130, "z": 500,
                    "sun": {"color": "#fff2d6", "intensity": 2.6, "position": [18, 36, 14]},
                    "exposure": 1.1,
                    "tonemap": "aces",
                }],
            },
            "lighting:../escape": {"samples": [{"x": 0, "y": 0, "z": 0}]},
        },
    }
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    written = import_capture(source, tmp_path / "out")
    names = sorted(path.name for path in written)
    assert names == ["HyruleField_Child.json", "KokiriForest_Child.json"]
    kokiri = json.loads((tmp_path / "out" / "KokiriForest_Child.json").read_text(encoding="utf-8"))
    assert len(kokiri["samples"]) == 2
    assert kokiri["samples"][1]["fog"] is None
    field = load_reference("HyruleField_Child", tmp_path / "out")
    assert field["samples"][0]["sun"]["color"] == "#fff2d6"
    assert field["samples"][0]["tonemap"] == "aces"
    assert scenes_from_capture(payload)["KokiriForest_Child"]["probes"]["world.ray"] is False


def test_fixture_report_lists_the_firefly_and_does_not_invent_a_sun(tmp_path, monkeypatch):
    monkeypatch.setattr("ocarina_forge.lighting.references_dir", lambda: tmp_path / "empty")
    dump = Dump(build_fixture(tmp_path / "dump"))
    report = lighting_report(dump, KOKIRI_STATIC)
    kinds = {(row["gyaml"], row["kind"]) for row in report["emitters"]}
    assert ("Eft_Firefly", "emitter") in kinds
    assert report["samples"] == []
    assert "sun" in report["unavailable"]
    assert report["param"]["keys"] == []
    assert "Env" in report["directories"]["absent"]


def test_install_copies_the_script_only(tmp_path):
    dest = install_mod(tmp_path / "game")
    assert (dest / "mod.json").is_file()
    assert (dest / "main.js").is_file()
    text = (dest / "main.js").read_text(encoding="utf-8")
    for banned in ("longshot/world", "longshot/gfx", "longshot/capture", "longshot/memory"):
        assert f'from "{banned}"' not in text
        assert f'import "{banned}"' not in text
    assert "sun: null" in text
    manifest = json.loads((dest / "mod.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "local/lighting-capture"
    assert manifest["entry"] == "main.js"
    assert (capture_root() / "main.js").read_text(encoding="utf-8") == text


def test_windows_steps_name_the_commands_bradyn_runs():
    text = windows_steps(Path(r"C:\Games\Longshot\mod-test"))
    assert "lighting-mod --install" in text
    assert "lighting-import" in text
    assert "lighting-survey" in text
    assert "FORGE_LIGHTING" in text
    assert "local/lighting-capture" in text


def test_collect_emitters_lists_dynamic_key_names_only():
    rows = collect_emitters({
        "Actors": [{
            "Gyaml": "VolumeFogArea",
            "Translate": [0, 1, 2],
            "Scale": [10, 4, 10],
            "Dynamic": {"SomeKey": {"nested": 1}},
        }],
    })
    assert rows[0]["kind"] == "fog-volume"
    assert rows[0]["paramKeys"] == ["SomeKey"]
    assert "nested" not in json.dumps(rows)

"""Lean Longshot mod export. Unchanged projects write no patch files."""

import json
from pathlib import Path

from ocarina_forge.actors import format_hash, read_vec
from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import FADO, KOKIRI_STATIC, build_fixture
from ocarina_forge.modexport import canonical_hash, content_placement_id, export_mod
from ocarina_forge.project import Project
from ocarina_forge.scene import apply_command


def _world(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    project = Project.create(tmp_path / "mod", name="kokiri-sign", title="Kokiri sign", author="Ada", version="0.1.0")
    document, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    return dump, project, document


def test_hash_spellings_are_one_id():
    decimal = 5337883246609832515
    assert canonical_hash("0x4a13f8880eb01a43") == "0x4a13f8880eb01a43"
    assert canonical_hash("4A13F8880EB01A43") == "0x4a13f8880eb01a43"
    assert canonical_hash(decimal) == "0x4a13f8880eb01a43"
    assert canonical_hash("0x4a13f8880eb01a43") == canonical_hash(content_placement_id(decimal))
    assert content_placement_id("0x4a13f8880eb01a43") == "4a13f8880eb01a43"


def test_unchanged_project_writes_no_patch_files(tmp_path):
    dump, project, _document = _world(tmp_path)
    result = export_mod(project, dump, install=False)
    assert result["files"] == []
    assert not (project.root / "build" / "mod").exists()


def test_move_and_move_back_writes_no_patch_files(tmp_path):
    dump, project, document = _world(tmp_path)
    ident = format_hash(FADO)
    original = read_vec(
        next(actor for actor in document["Actors"] if format_hash(int(actor["Hash"])) == ident),
        "Translate",
        [0, 0, 0],
    )
    empty = {"format": 1, "banc": KOKIRI_STATIC, "actors": {}}
    moved, _focus = apply_command(document, empty, {"command": "transform", "id": ident, "translate": [6, 1, 3]})
    assert ident in moved["actors"]
    restored, _focus = apply_command(document, moved, {"command": "transform", "id": ident, "translate": original})
    assert ident not in restored["actors"]
    project.save_scene_diff(restored)
    result = export_mod(project, dump)
    assert result["files"] == []

    stale = {
        "format": 1,
        "banc": KOKIRI_STATIC,
        "actors": {ident: {"state": "edited", "translate": original}},
    }
    project.save_scene_diff(stale)
    result = export_mod(project, dump)
    assert result["files"] == []
    assert not (project.root / "build" / "mod").exists()


def test_a_move_writes_one_patch_and_installs_under_mods_local(tmp_path):
    dump, project, document = _world(tmp_path)
    project.manifest["description"] = "Editor note that must not ship."
    project.manifest["license"] = "MIT"
    ident = format_hash(FADO)
    diff, _focus = apply_command(
        document,
        {"format": 1, "banc": KOKIRI_STATIC, "actors": {}},
        {"command": "transform", "id": ident, "translate": [6, 1, 3], "rotate": [0, 0.785398, 0]},
    )
    project.save_scene_diff(diff)
    game = tmp_path / "game"
    result = export_mod(project, dump, install=True, game=game)
    assert result["modId"] == "local/kokiri-sign"
    assert result["files"] == [
        "mod.json",
        "patches/KokiriForest_Child_Static.json",
    ]
    folder = project.root / "build" / "mod"
    assert (folder / "romfs").is_dir()
    assert list((folder / "romfs").iterdir()) == []
    assert not (folder / "src").exists()
    assert not (folder / "romfs" / ".keep").exists()
    manifest = json.loads((folder / "mod.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "local/kokiri-sign"
    assert manifest["name"] == "Kokiri sign"
    assert manifest["author"] == "Ada"
    assert manifest["license"] == "MIT"
    assert "description" not in manifest
    assert "runtime" not in manifest
    assert "entry" not in manifest
    patch = json.loads((folder / "patches" / "KokiriForest_Child_Static.json").read_text(encoding="utf-8"))
    assert patch["target"] == KOKIRI_STATIC
    assert patch["placements"] == [
        {"op": "set", "hash": ident, "path": "Translate", "value": [6.0, 1.0, 3.0]},
        {"op": "set", "hash": ident, "path": "Rotate", "value": [0.0, 0.785398, 0.0]},
    ]
    installed = Path(result["installed"])
    assert installed == game / "mods" / "local" / "kokiri-sign"
    assert (installed / "patches" / "KokiriForest_Child_Static.json").is_file()
    assert not (installed / "romfs" / ".keep").exists()

    project.save_scene_diff({"format": 1, "banc": KOKIRI_STATIC, "actors": {}})
    cleared = export_mod(project, dump, install=True, game=game)
    assert cleared["files"] == []
    assert not installed.exists()


def test_hide_is_warned_and_not_written(tmp_path):
    dump, project, document = _world(tmp_path)
    ident = format_hash(FADO)
    diff, _focus = apply_command(
        document,
        {"format": 1, "banc": KOKIRI_STATIC, "actors": {}},
        {"command": "hide", "id": ident},
    )
    project.save_scene_diff(diff)
    result = export_mod(project, dump)
    assert result["files"] == []
    assert any("hidden" in warning for warning in result["warnings"])

"""The tileset is an editor cache. These checks do not read a game dump."""

import json
from pathlib import Path

import pytest

from ocarina_forge.errors import BancError
from ocarina_forge.scene_tiles import (
    _bake_failure_text,
    _write_tileset,
    bake_scene_tiles,
    cell_id,
    tile_eligible,
    tile_file,
    tileset_box,
)


TREE = {
    "gyaml": "Obj_Tree",
    "banc": "Banc/HyruleField_Child_Static.bcett.byml.zs",
    "layer": "models",
    "category": "other",
}


def test_static_scenery_is_eligible_and_the_rest_stay_put():
    assert tile_eligible(TREE)
    assert not tile_eligible({**TREE, "banc": "Banc/HyruleField_Child_Dynamic.bcett.byml.zs"})
    assert not tile_eligible({**TREE, "layer": "water"})
    assert not tile_eligible({**TREE, "gyaml": "Obj_LakeHrl_WaterPlane"})
    assert not tile_eligible({**TREE, "layer": "lights"})
    assert not tile_eligible({**TREE, "category": "system"})
    assert not tile_eligible({**TREE, "volume": True})
    assert cell_id(10, -10) == "0_-1"
    assert cell_id(256, 256) == "1_1"


def test_a_baker_crash_keeps_the_stack_not_just_the_version_line():
    stderr = "\n".join([
        "Error: Could not load the \"sharp\" module using the win32-x64 runtime",
        "    at sharp.mjs:171:9",
        "    at ModuleJob.run (node:internal/modules/esm/module_job:569:25)",
        "Node.js v26.13.1",
    ])
    message = _bake_failure_text(stderr, "", 1)
    assert "Could not load the \"sharp\" module" in message
    assert "at sharp.mjs:171:9" in message
    assert "Node.js v26.13.1" in message
    long = ("x" * 5000) + "\nError: boom\n    at bake (file.js:1:1)\nNode.js v26.13.1\n"
    clipped = _bake_failure_text(long, "", 1)
    assert clipped.startswith("The tile bake failed")
    assert "Error: boom" in clipped
    assert len(clipped) < 6000


def test_a_field_like_scene_is_not_an_empty_bake(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path))
    glb = tmp_path / "tree.glb"
    glb.write_bytes(b"glb")
    monkeypatch.setattr(
        "ocarina_forge.scene_tiles._cached_model",
        lambda dump, gyaml: (glb, "d" * 64),
    )

    def run(job):
        assert len(job["cells"]) >= 2
        assert sum(len(cell["actors"]) for cell in job["cells"]) == 4
        for cell in job["cells"]:
            meta = {
                "id": cell["id"],
                "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]},
                "box": tileset_box([0, 0, 0], [1, 1, 1]),
                "actors": [actor["featureId"] for actor in cell["actors"]],
            }
            for name in ("meta", "leaf", "hlod"):
                Path(cell[name]).parent.mkdir(parents=True, exist_ok=True)
            Path(cell["meta"]).write_text(json.dumps(meta), encoding="utf-8")
            Path(cell["leaf"]).write_bytes(b"glb")
            Path(cell["hlod"]).write_bytes(b"glb")

    monkeypatch.setattr("ocarina_forge.scene_tiles._run_node", run)
    actors = []
    for index, (x, z, gyaml) in enumerate([
        (0, 0, "Obj_Tree"),
        (12, 4, "Obj_Tree"),
        (300, 0, "Obj_Rock"),
        (320, 40, "Obj_Rock"),
    ]):
        actors.append({
            "featureId": index + 1,
            "gyaml": gyaml,
            "banc": "Banc/HyruleField_Child_Static.bcett.byml.zs",
            "layer": "models",
            "category": "other",
            "translation": [x, 0, z],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
            "parts": [{"family": "Body:0", "image": gyaml, "alpha": "opaque"}],
        })
    actors.append({
        "featureId": 9,
        "gyaml": "Npc_Rider",
        "banc": "Banc/HyruleField_Child_Dynamic.bcett.byml.zs",
        "layer": "models",
        "category": "other",
        "translation": [0, 0, 0],
        "rotation": [0, 0, 0],
        "scale": [1, 1, 1],
    })
    result = bake_scene_tiles(None, actors)
    assert result["url"].endswith("/tileset.json")
    assert result["tiles"] == 2
    assert len(result["actors"]) == 4
    assert "9" not in result["actors"]


def test_tileset_uris_stay_under_the_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path))
    metas = [{
        "id": "0_0",
        "bounds": {"min": [0, 0, 0], "max": [4, 2, 4]},
        "box": tileset_box([0, 0, 0], [4, 2, 4]),
        "actors": [7, 11],
    }]
    directory = tmp_path / "tiles" / "sets" / ("ab" * 32)
    actors = _write_tileset(directory, metas)
    tileset = json.loads((directory / "tileset.json").read_text(encoding="utf-8"))
    assert tileset["asset"]["gltfUpAxis"] == "z"
    assert tileset["geometricError"] == 256
    child = tileset["root"]["children"][0]
    assert child["content"]["uri"] == "hlod/0_0.glb"
    assert child["children"][0]["content"]["uri"] == "cells/0_0.glb"
    assert actors["7"]["leaf"] == "cells/0_0.glb"
    leaf = directory / "cells" / "0_0.glb"
    leaf.parent.mkdir(parents=True, exist_ok=True)
    leaf.write_bytes(b"glTF")
    assert tile_file("ab" * 32, "cells/0_0.glb") == leaf.resolve()
    with pytest.raises(BancError):
        tile_file("ab" * 32, "../secrets.glb")
    with pytest.raises(BancError):
        tile_file("nope", "tileset.json")

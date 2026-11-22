"""An edit that does not pick one actor must fail, and must not write a scene file."""

import json
import subprocess
import sys
from pathlib import Path

from ocarina_forge.actors import format_hash
from ocarina_forge.fixtures import FADO, TREE, build_fixture
from ocarina_forge.project import Project

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, *extra):
    dump = build_fixture(tmp_path / "dump")
    project = Project.create(
        tmp_path / "mod",
        name="kokiri-sign",
        title="Kokiri sign",
        author="Ada",
        version="0.1.0",
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ocarina_forge",
            "edit",
            "KokiriForest_Child",
            "--dump",
            str(dump),
            "--project",
            str(project.root),
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    written = list((project.root / "scenes").rglob("*.yaml")) if (project.root / "scenes").is_dir() else []
    return proc, written


def test_an_ambiguous_class_exits_nonzero_and_writes_nothing(tmp_path):
    proc, written = _run(tmp_path, "--gyaml", "Obj_Tree", "--translate", "1,2,3")
    assert proc.returncode != 0
    assert "Nothing was written" in proc.stderr
    assert "--id" in proc.stderr
    assert "Obj_Tree" in proc.stderr
    assert written == []


def test_a_named_hash_writes_the_scene(tmp_path):
    proc, written = _run(
        tmp_path,
        "--gyaml",
        "Obj_Tree",
        "--id",
        format_hash(TREE),
        "--translate",
        "1,2,3",
    )
    assert proc.returncode == 0, proc.stderr
    assert written
    assert "Saved in the project" in proc.stdout


def test_rotate_then_scale_with_id_exports_set_and_add(tmp_path):
    dump = build_fixture(tmp_path / "dump")
    project = Project.create(
        tmp_path / "mod",
        name="kokiri-sign",
        title="Kokiri sign",
        author="Ada",
        version="0.1.0",
    )
    ident = format_hash(FADO)
    turned = subprocess.run(
        [
            sys.executable,
            "-m",
            "ocarina_forge",
            "edit",
            "KokiriForest_Child",
            "--gyaml",
            "Npc_Kokiri_F_01",
            "--id",
            ident,
            "--rotate",
            "0,0.785398,0",
            "--dump",
            str(dump),
            "--project",
            str(project.root),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert turned.returncode == 0, turned.stderr
    scaled = subprocess.run(
        [
            sys.executable,
            "-m",
            "ocarina_forge",
            "edit",
            "KokiriForest_Child",
            "--gyaml",
            "Npc_Kokiri_F_01",
            "--id",
            ident,
            "--scale",
            "2,2,2",
            "--dump",
            str(dump),
            "--project",
            str(project.root),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert scaled.returncode == 0, scaled.stderr
    exported = subprocess.run(
        [
            sys.executable,
            "-m",
            "ocarina_forge",
            "mod-export",
            "--dump",
            str(dump),
            "--project",
            str(project.root),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert exported.returncode == 0, exported.stderr
    assert "next game start" in exported.stdout
    folder = project.root / "build" / "mod"
    names = sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())
    assert names == ["mod.json", "patches/KokiriForest_Child_Static.json"]
    assert (folder / "romfs").is_dir()
    patch = json.loads((folder / "patches" / "KokiriForest_Child_Static.json").read_text(encoding="utf-8"))
    assert patch["placements"] == [
        {"op": "set", "hash": ident, "path": "Rotate", "value": [0.0, 0.785398, 0.0]},
        {"op": "add", "hash": ident, "path": "Scale", "value": [2.0, 2.0, 2.0]},
    ]


def test_a_missing_actor_exits_nonzero(tmp_path):
    proc, written = _run(tmp_path, "--gyaml", "No_Such_Actor", "--translate", "1,2,3")
    assert proc.returncode != 0
    assert "Nothing was written" in proc.stderr
    assert written == []

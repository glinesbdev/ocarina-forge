"""An edit that does not pick one actor must fail, and must not write a scene file."""

import subprocess
import sys
from pathlib import Path

from ocarina_forge.actors import format_hash
from ocarina_forge.fixtures import TREE, build_fixture
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


def test_a_missing_actor_exits_nonzero(tmp_path):
    proc, written = _run(tmp_path, "--gyaml", "No_Such_Actor", "--translate", "1,2,3")
    assert proc.returncode != 0
    assert "Nothing was written" in proc.stderr
    assert written == []

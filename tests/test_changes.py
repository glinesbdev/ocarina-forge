"""Per-map change lists: actor diffs and the advanced patches that touch a map."""

from pathlib import Path

from ocarina_forge.actors import format_hash
from ocarina_forge.changes import project_report, scene_changes
from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import CUCCO, FADO, KOKIRI_STATIC, build_fixture
from ocarina_forge.project import Project
from ocarina_forge.scene import apply_command

ROOT = Path(__file__).resolve().parents[1]


def _dump(tmp_path):
    return Dump(build_fixture(tmp_path / "dump"))


def _kinds(report):
    found = {}
    for group in report["groups"]:
        found[group["kind"]] = group["entries"]
    return found


def test_sample_scene_lists_before_and_after(tmp_path):
    dump = _dump(tmp_path)
    project = Project(ROOT / "examples" / "sample-project")
    report = scene_changes(dump, project, KOKIRI_STATIC)
    kinds = _kinds(report)
    moved = kinds["moved"]
    assert moved[0]["title"] == "Fado"
    assert moved[0]["before"] == "2, 0, 3"
    assert moved[0]["after"] == "6, 0, 3"
    assert moved[0]["source"] == "actor"
    assert moved[0]["file"].endswith("KokiriForest_Child_Static.yaml")
    assert moved[0]["revertable"] is True
    turned = kinds["turned"]
    assert turned[0]["title"] == "Rotation"
    assert "°" in turned[0]["before"] and "°" in turned[0]["after"]
    hidden = kinds["removed"]
    assert hidden[0]["actorId"] == format_hash(CUCCO)
    assert hidden[0]["after"] == "Hidden"
    added = kinds["added"]
    assert added[0]["gyaml"] == "Npc_Zora_01"
    assert "Not in this scene" == added[0]["before"]
    assert kinds["params"][0]["path"] == "Dynamic.IsEnable"
    assert report["counts"]["moved"] == 1
    assert report["counts"]["total"] == 5


def test_revert_part_keeps_the_other_edit(tmp_path):
    dump = _dump(tmp_path)
    document, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    project = Project.create(tmp_path / "mod", name="notes", title="Notes", author="Ada", version="0.1.0")
    diff = {"format": 1, "banc": KOKIRI_STATIC, "actors": {}}
    diff, _focus = apply_command(
        document, diff, {"command": "transform", "id": format_hash(FADO), "translate": [6, 0, 3], "rotate": [0, 1.2, 0]}
    )
    project.save_scene_diff(diff)
    report = scene_changes(dump, project, KOKIRI_STATIC)
    assert report["counts"]["moved"] == 1
    assert report["counts"]["turned"] == 1
    diff, focus = apply_command(
        document,
        project.load_scene_diff(KOKIRI_STATIC),
        {"command": "revert_part", "id": format_hash(FADO), "part": "translate"},
    )
    assert focus == format_hash(FADO)
    record = diff["actors"][format_hash(FADO)]
    assert "translate" not in record
    assert record["rotate"] == [0, 1.2, 0]
    project.save_scene_diff(diff)
    again = scene_changes(dump, project, KOKIRI_STATIC)
    assert again["counts"]["moved"] == 0
    assert again["counts"]["turned"] == 1


def test_advanced_patches_attach_to_the_map_they_name(tmp_path):
    dump = _dump(tmp_path)
    project = Project(ROOT / "examples" / "after-the-ocarina")
    report = scene_changes(dump, project, KOKIRI_STATIC)
    logic = [entry for entry in _kinds(report).get("logic", []) if entry["source"] == "patch"]
    files = {entry["file"] for entry in logic}
    assert "scenes/m6-stones.yaml" in files
    assert "scenes/m3-saria.yaml" in files
    stone = next(entry for entry in logic if entry["title"] == "AtO_SpiritStone_Forest")
    assert stone["before"] == "Not placed in the banc"
    assert "84" in stone["after"]
    assert stone["revertable"] is False
    schedules = _kinds(report)["schedule"]
    assert schedules[0]["file"] == "scenes/m3-saria.yaml"
    assert "AIScheduleParam" in schedules[0]["before"]
    whole = project_report(dump, project)
    kokiri = next(item for item in whole["maps"] if item["banc"] == KOKIRI_STATIC)
    assert kokiri["counts"]["logic"] >= 2
    assert kokiri["counts"]["total"] == report["counts"]["total"]

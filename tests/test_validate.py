"""Live diagnostics: class, model, hash, parameter kind, links, and flags."""

from fastapi.testclient import TestClient

from bancwright.actors import format_hash
from bancwright.dump import Dump
from bancwright.fixtures import FADO, KOKIRI_STATIC, SIGN, build_fixture
from bancwright.formats.byml import dumps
from bancwright.project import Project
from bancwright.scene import apply_command
from bancwright.validate import GAMEDATA_REL, byml_strings, diagnose, issues_for


def _dump(tmp_path):
    return Dump(build_fixture(tmp_path / "dump"))


def _project(tmp_path):
    return Project.create(tmp_path / "mod", name="kokiri-notes", title="Kokiri notes", author="Ada", version="0.1.0")


def _codes(report):
    return {row["code"] for row in report["diagnostics"]}


def test_byml_string_table_is_the_flag_catalog():
    raw = dumps({"flags": ["HavePlayedEvent.TitleEventBase", "OtherFlag"]})
    assert byml_strings(raw) >= {"HavePlayedEvent.TitleEventBase", "OtherFlag"}


def test_clean_scene_warns_about_stand_in_models_and_a_missing_list(tmp_path):
    report = diagnose(_dump(tmp_path), _project(tmp_path), KOKIRI_STATIC)
    codes = _codes(report)
    assert "gamedata_missing" in codes
    assert "unknown_class" not in codes
    assert "missing_link" not in codes
    assert "duplicate_hash" not in codes
    assert "bad_param" not in codes
    missing = {row["actor"] for row in report["diagnostics"] if row["code"] == "missing_model"}
    fado = format_hash(FADO)
    assert fado not in missing
    assert format_hash(0x1000000000000002) in missing  # Mido has a pack and no mesh
    assert format_hash(0x100000000000000D) not in missing  # the town volume
    assert format_hash(0x1000000000000005) not in missing  # StartPos
    assert format_hash(0x1000000000000007) not in missing  # the ground slab
    fado_row = next(row for row in report["diagnostics"] if row["code"] == "gamedata_missing")
    assert fado_row["severity"] == "warning"
    assert fado_row["file"].endswith("KokiriForest_Child_Static.yaml")


def test_edits_report_class_type_link_and_flag(tmp_path):
    dump = _dump(tmp_path)
    project = _project(tmp_path)
    (dump.romfs / GAMEDATA_REL).parent.mkdir(parents=True, exist_ok=True)
    (dump.romfs / GAMEDATA_REL).write_bytes(dumps({"flags": ["HavePlayedEvent.TitleEventBase"]}))
    document, _dict_id, _raw = dump.read_byml(KOKIRI_STATIC)
    diff = project.load_scene_diff(KOKIRI_STATIC)
    diff, added = apply_command(document, diff, {"command": "add", "actor": "NotARealActor", "translate": [0, 0, 0]})
    diff, _focus = apply_command(
        document,
        diff,
        {"command": "set", "id": format_hash(FADO), "path": "Dynamic.IsEnable", "value": ["str", "yes"]},
    )
    diff, _focus = apply_command(
        document,
        diff,
        {
            "command": "set",
            "id": format_hash(FADO),
            "path": "Dynamic.EventFlag",
            "value": ["str", "HavePlayedEvent.TitleEventBase"],
        },
    )
    diff, _focus = apply_command(
        document,
        diff,
        {
            "command": "set",
            "id": format_hash(SIGN),
            "path": "Links",
            "value": ["array", [["dict", {"Hash": ["u64", "153"], "Gyaml": ["str", "Npc_Kokiri_F_01"]}]]],
        },
    )
    # A class name stored beside the flag must not be treated as a flag.
    diff, _focus = apply_command(
        document,
        diff,
        {"command": "set", "id": format_hash(FADO), "path": "Dynamic.Drop__DropActor", "value": ["str", "Item_NotReal"]},
    )
    project.save_scene_diff(diff)
    stones = project.root / "scenes" / "stones.yaml"
    stones.write_text(
        "flag: MissingFlag.Nope\n"
        "stones:\n"
        "  - gyaml: Obj_Tree\n"
        "    name: Extra\n"
        f"    banc: {KOKIRI_STATIC}\n"
        "    translate: [1, 0, 1]\n",
        encoding="utf-8",
    )
    report = diagnose(dump, project, KOKIRI_STATIC)
    by_code = {}
    for row in report["diagnostics"]:
        by_code.setdefault(row["code"], []).append(row)
    assert "gamedata_missing" not in by_code
    assert any(row["actor"] == added and row["severity"] == "error" for row in by_code["unknown_class"])
    bad = by_code["bad_param"]
    assert any("IsEnable" in row["message"] and row["pointer"].endswith("/Dynamic/IsEnable") for row in bad)
    assert any(row["actor"] == format_hash(SIGN) for row in by_code["missing_link"])
    assert any("MissingFlag.Nope" in row["message"] for row in by_code["unknown_flag"])
    assert not any("HavePlayedEvent" in row["message"] for row in by_code.get("unknown_flag", []))
    assert not any("Drop__DropActor" in row["message"] or "Item_NotReal" in row["message"] for row in by_code.get("unknown_flag", []))
    # The known flag is a new field, so it is not a type error, and Drop stays a string.
    assert not any("EventFlag" in row["message"] or "Drop__DropActor" in row["message"] for row in bad)


def test_duplicate_hash_and_skipped_checks_when_catalogs_are_absent():
    shared = "0x1000000000000001"
    actors = [
        {"id": shared, "gyaml": "Nope", "name": "One", "scale": [1, 1, 1], "status": "vanilla", "params": ["dict", {}]},
        {"id": shared, "gyaml": "Nope", "name": "Two", "scale": [1, 1, 1], "status": "vanilla", "params": ["dict", {}]},
    ]
    rows = issues_for(
        file="scenes/Demo.yaml",
        document={"Actors": []},
        actors=actors,
        known_classes=None,
        flag_names=None,
        model_of=lambda _gyaml: "present",
        link_ids={shared},
    )
    codes = {row["code"] for row in rows}
    assert codes == {"duplicate_hash"}
    assert rows[0]["actor"] == shared
    assert rows[0]["pointer"] == f"/actors/{shared}"


def test_problems_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    from bancwright.server import app

    client = TestClient(app)
    dump = _dump(tmp_path)
    project = _project(tmp_path)
    saved = client.put("/api/config", json={"dump": str(dump.root), "project": str(project.root)})
    assert saved.status_code == 200
    found = client.get("/api/scene/problems", params={"banc": KOKIRI_STATIC})
    assert found.status_code == 200
    body = found.json()
    assert body["banc"] == KOKIRI_STATIC
    assert any(row["code"] == "gamedata_missing" for row in body["diagnostics"])

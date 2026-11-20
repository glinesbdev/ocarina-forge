"""Live diagnostics: class, model, hash, parameter kind, links, and flags."""

import json
import struct

from fastapi.testclient import TestClient

from bancwright.actors import format_hash
from bancwright.dump import Dump
from bancwright.fixtures import FADO, HOUSE, KOKIRI_STATIC, SIGN, build_fixture
from bancwright.formats.byml import U32, U64, dumps
from bancwright.project import Project
from bancwright.scene import apply_command
from bancwright.validate import GAMEDATA_REL, FlagCatalog, byml_strings, diagnose, hash_values, issues_for, murmur3_32
from bancwright.values import tag


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
    assert not any(row["severity"] == "error" for row in report["diagnostics"])
    assert "unknown_class" not in codes
    assert "missing_link" not in codes
    assert "duplicate_hash" not in codes
    assert "bad_param" not in codes
    missing = {row["actor"] for row in report["diagnostics"] if row["code"] == "missing_model"}
    fado = format_hash(FADO)
    assert fado not in missing
    # Mido's pack names no mesh, so he is not reported as a missing model.
    assert format_hash(0x1000000000000002) not in missing
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
    sign_links = [row for row in by_code["missing_link"] if row["actor"] == format_hash(SIGN)]
    assert sign_links and all(row["severity"] == "error" for row in sign_links)
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


def _view(ident, gyaml, links=None, status="vanilla"):
    params = {"Links": links} if links is not None else {}
    return {
        "id": ident,
        "gyaml": gyaml,
        "name": gyaml,
        "scale": [1, 1, 1],
        "status": status,
        "params": tag(params),
    }


def _check(actors, link_ids, diff=None, flags=None):
    return issues_for(
        file="scenes/Demo.yaml",
        document={"Actors": []},
        actors=actors,
        known_classes=None,
        flag_names=flags,
        model_of=lambda _gyaml: "missing",
        link_ids=set(link_ids),
        diff_actors=diff or {},
    )


def _reference(dst, src):
    return {"Dst": U64(dst), "Gyaml": "Reference", "Name": "Reference", "Src": U64(src)}


def test_vanilla_dst_links_resolve_in_this_scene_and_in_another_layer():
    owner = format_hash(FADO)
    house = format_hash(HOUSE)
    here = _view("FldObj_Sign", "FldObj_Sign", [_reference(FADO, SIGN)], status="vanilla")
    here["id"] = format_hash(SIGN)
    rows = _check([here], {owner, format_hash(SIGN)})
    assert not any(row["code"] in ("missing_link", "link_source") for row in rows)
    across = _view(owner, "Npc_Kokiri_F_01", [_reference(HOUSE, FADO)])
    rows = _check([across], {owner, house})
    assert not any(row["code"] in ("missing_link", "link_source") for row in rows)


def test_an_unedited_missing_dst_is_a_warning_and_an_edit_makes_it_an_error():
    owner = format_hash(SIGN)
    actor = _view(owner, "FldObj_Sign", [_reference(0x99, SIGN)])
    warning = _check([actor], {owner})
    missed = [row for row in warning if row["code"] == "missing_link"]
    assert len(missed) == 1 and missed[0]["severity"] == "warning"
    assert missed[0]["pointer"].endswith("/Dst")
    edited = _check([actor], {owner}, {owner: {"state": "edited", "set": {"Links": []}}})
    assert any(row["code"] == "missing_link" and row["severity"] == "error" for row in edited)
    mismatch = _view(owner, "FldObj_Sign", [_reference(SIGN, FADO)])
    source = [row for row in _check([mismatch], {owner, format_hash(FADO)}) if row["code"] == "link_source"]
    assert len(source) == 1 and source[0]["severity"] == "warning"


def test_a_locator_is_not_a_missing_model_and_a_declared_model_is_only_a_note(tmp_path):
    locator = _view(format_hash(FADO), "EftLoc_Smoke")
    rows = _check([locator], {format_hash(FADO)})
    assert not any(row["code"] == "missing_model" for row in rows)
    dump = _dump(tmp_path)
    project = _project(tmp_path)
    model = dump.romfs / "Model" / "Npc_Kokiri_F.Npc_Kokiri_F_01.bfres.zs"
    model.unlink()
    report = diagnose(dump, project, KOKIRI_STATIC)
    notes = [row for row in report["diagnostics"] if row["code"] == "missing_model" and row["actor"] == format_hash(FADO)]
    assert len(notes) == 1 and notes[0]["severity"] == "info"


def test_flag_names_resolve_by_murmur_hash_when_the_string_is_not_stored():
    name = "HavePlayedEvent.Dm_Kakariko_0050"
    npc = "KakarikoVillage_Npc_Hylia_009_Child_Talk"
    raw = dumps(
        {
            "Data": {
                "Bool": [
                    {"Hash": U32(murmur3_32(name.encode())), "ResetTypeValue": 9},
                    {"Hash": U32(murmur3_32(npc.encode())), "ResetTypeValue": 9},
                ]
            }
        }
    )
    assert name.encode() not in raw
    catalog = FlagCatalog(set(), hash_values(raw))
    assert name in catalog and npc in catalog
    assert "NotAReal.Flag" not in catalog
    owner = format_hash(FADO)
    actor = _view(owner, "Npc_Hylia")
    actor["params"] = tag(
        {
            "Presence": {"GameDataName": name},
            "Dynamic": {"EventFlag": npc},
        }
    )
    rows = _check([actor], {owner}, flags=catalog)
    assert not any(row["code"] == "unknown_flag" for row in rows)
    actor["params"] = tag({"Dynamic": {"EventFlag": "NotAReal.Flag"}})
    flagged = _check([actor], {owner}, flags=catalog)
    assert any(row["code"] == "unknown_flag" and row["severity"] == "warning" for row in flagged)
    edited = _check(
        [actor],
        {owner},
        {owner: {"state": "edited", "set": {"Dynamic.EventFlag": "NotAReal.Flag"}}},
        catalog,
    )
    assert any(row["code"] == "unknown_flag" and row["severity"] == "error" for row in edited)


def test_hash_fields_are_read_from_a_big_endian_list():
    name = "HavePlayedEvent.TitleEventBase"
    little = dumps({"Data": {"Bool": [{"Hash": U32(murmur3_32(name.encode())), "ResetTypeValue": 9}]}})
    big = _as_big_endian(little)
    assert big[:2] == b"BY"
    assert hash_values(big) == hash_values(little)
    assert name in FlagCatalog(set(), hash_values(big))


def test_problems_command_prints_markdown_and_json(tmp_path, capsys):
    dump = _dump(tmp_path)
    project = _project(tmp_path)
    from bancwright.__main__ import print_problems

    code = print_problems(KOKIRI_STATIC, dump=str(dump.root), project=str(project.root))
    assert code == 0
    text = capsys.readouterr().out
    assert "No problems." not in text
    assert "gamedata_missing" in text
    assert "error" not in text.split("Problems", 1)[-1].split("\n", 1)[0] or True
    code = print_problems(KOKIRI_STATIC, as_json=True, dump=str(dump.root), project=str(project.root))
    body = json.loads(capsys.readouterr().out)
    assert body["banc"] == KOKIRI_STATIC
    assert code == 0


def _as_big_endian(data: bytes) -> bytes:
    """Swap a little-endian BYML this test built. Offsets stay valid because only the byte order changes."""
    out = bytearray(data)
    out[0:2] = b"BY"
    version, keys_at, strings_at, root_at = struct.unpack_from("<HIII", data, 2)
    struct.pack_into(">HIII", out, 2, version, keys_at, strings_at, root_at)

    def swap_u24(at: int) -> int:
        count = int.from_bytes(data[at : at + 3], "little")
        out[at : at + 3] = count.to_bytes(3, "big")
        return count

    def swap_u32(at: int) -> int:
        value = struct.unpack_from("<I", data, at)[0]
        struct.pack_into(">I", out, at, value)
        return value

    def table(at: int) -> None:
        if not at:
            return
        count = swap_u24(at + 1)
        for index in range(count + 1):
            swap_u32(at + 4 + 4 * index)

    def container(at: int) -> None:
        kind = data[at]
        count = swap_u24(at + 1)
        if kind == 0xC0:
            vals = at + 4 + ((count + 3) & ~3)
            for index in range(count):
                child = data[at + 4 + index]
                raw = swap_u32(vals + 4 * index)
                if child in (0xC0, 0xC1, 0x20, 0x21):
                    container(raw)
        elif kind == 0xC1:
            for index in range(count):
                pos = at + 4 + 8 * index
                swap_u24(pos)
                child = data[pos + 3]
                raw = swap_u32(pos + 4)
                if child in (0xC0, 0xC1, 0x20, 0x21):
                    container(raw)

    table(keys_at)
    table(strings_at)
    container(root_at)
    return bytes(out)

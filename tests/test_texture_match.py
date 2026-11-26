"""Albedo sampler selection and Tex file matching.

The corpus is synthetic. It does not contain a game dump. Counts below are
the same materials the audit script reports when pointed at this fixture
with the fragment ``Model/AuditActor.``.
"""

from __future__ import annotations

from types import SimpleNamespace

from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import build_fixture
from ocarina_forge.formats.bfres import rank_albedo, sample_v10_bfres
from ocarina_forge.formats.bntx import write_rgba
from ocarina_forge.formats.sarc import pack
from ocarina_forge.formats.zsio import compress
from ocarina_forge.models import _PACK_TEX, _TEX_INDEX, _flat_colour_note, _resolve_tex, _stem
from ocarina_forge.textureaudit import audit_models


def test_rank_albedo_keeps_runtime_slots_flat_and_accepts_an_alb_name():
    assert rank_albedo([("tc", "MaterialAlb"), ("ta", "MaterialCmb")]) == []
    assert rank_albedo([("_a0", "Field_Water_Nrm"), ("_n0", "Field_Water_N0")]) == []
    assert rank_albedo([("s0", "Tree_Alb")]) == []
    assert rank_albedo([("tex0", "Slot_Alb"), ("_n0", "Slot_Nrm")]) == [("tex0", "Slot_Alb")]
    assert rank_albedo([("_a0", "Field_Water_Nrm"), ("tex0", "Field_Water_Alb")]) == [("tex0", "Field_Water_Alb")]
    assert rank_albedo([("_a0", "Rock_Alb"), ("_n0", "Rock_Nrm")])[0] == ("_a0", "Rock_Alb")


def test_an_unused_a0_does_not_hide_another_albedo():
    from ocarina_forge.models import _albedo_sources

    material = SimpleNamespace(
        binds=[("_a0", "Gone_Nrm"), ("tex0", "Slot_Alb")],
        albedo=[("_a0", "Gone_Nrm")],
        shader_blocks=[
            {
                "samplerNames": ["_a0", "tex0"],
                "attributeNames": [],
                "samplerIndices": [255, 0],
            }
        ],
    )
    assert _albedo_sources(material) == [("tex0", "Slot_Alb")]


def test_stem_strips_a_case_different_suffix():
    assert _stem("Tex/Case_Alb.BNTX.ZS") == "Case_Alb"
    assert _stem("Work/Foo.BNTX") == "Foo"


def test_flat_note_names_samplers_and_vertex_colours():
    missing = SimpleNamespace(texture="Gone_Alb", texture_error="samplers _a0=Gone_Alb. tried Tex/Gone_Alb.bntx.zs", colors=None)
    note = _flat_colour_note(missing, "")
    assert "Gone_Alb" in note
    assert "_a0" in note
    assert "Tex/Gone_Alb.bntx.zs" in note
    assert note.endswith("This mesh uses a flat colour.")
    bare = SimpleNamespace(texture="", texture_error="", colors=[(1, 1, 1, 1)], material="", binds=[])
    vertex = _flat_colour_note(bare, "")
    assert "vertex colours" in vertex
    assert "names no albedo texture" in vertex
    assert "No albedo sampler was usable and no Tex file matched." in vertex
    named = SimpleNamespace(
        texture="",
        texture_error="Mt_Body: samplers _a0=Npc_Body_Alb. tried Tex/Npc_Body_Alb.bntx.zs, Model/Npc_Goron_GOU.Tex.bfres.zs",
        colors=[(0.45, 0.28, 0.16, 1)],
        material="Mt_Body",
        binds=[("_a0", "Npc_Body_Alb")],
    )
    seen = _flat_colour_note(named, "")
    assert "Mt_Body" in seen
    assert "_a0" in seen
    assert "Npc_Body_Alb" in seen
    assert "Model/Npc_Goron_GOU.Tex.bfres.zs" in seen
    assert "vertex colours" in seen
    assert "names no albedo texture" not in seen


def test_audit_corpus_resolves_sampler_fallback_and_explains_flats(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    _TEX_INDEX.clear()
    _PACK_TEX.clear()
    colour = bytes([20, 140, 40, 255]) * 4

    def tex(name: str) -> bytes:
        return compress(write_rgba(name, 2, 2, colour), 1, dicts, level=3)

    (dump.romfs / "Tex" / "Rock_Alb.bntx.zs").write_bytes(tex("Rock_Alb"))
    nested = dump.romfs / "Tex" / "Common"
    nested.mkdir(parents=True)
    (nested / "Nested_Alb.bntx.zs").write_bytes(tex("Nested_Alb"))
    (dump.romfs / "Tex" / "Case_Alb.BNTX.ZS").write_bytes(tex("Case_Alb"))
    (dump.romfs / "Tex" / "Slot_Alb.bntx.zs").write_bytes(tex("Slot_Alb"))
    (dump.romfs / "Tex" / "Bare_Alb.bntx.zs").write_bytes(tex("Bare_Alb"))
    (dump.romfs / "Banc" / "AuditField").mkdir(parents=True)
    (dump.romfs / "Banc" / "AuditField" / "Banc_Alb.bntx.zs").write_bytes(tex("Banc_Alb"))

    packed = pack({"Packed_Alb.bntx": write_rgba("Packed_Alb", 2, 2, colour)})
    (dump.romfs / "Pack" / "Actor" / "AuditActor.pack.zs").write_bytes(compress(packed, 4, dicts, level=3))
    shared = pack({"Shared_Alb.bntx": write_rgba("Shared_Alb", 2, 2, colour)})
    (dump.romfs / "Pack" / "ResidentCommon.pack.zs").write_bytes(compress(shared, 4, dicts, level=3))

    materials = [
        {"name": "Standard", "samplers": [("_a0", "Rock_Alb")]},
        {"name": "Nested", "samplers": [("_a0", "Nested_Alb")]},
        {"name": "Cased", "samplers": [("_a0", "Case_Alb")]},
        {"name": "Packed", "samplers": [("_a0", "Packed_Alb")]},
        {"name": "Shared", "samplers": [("_a0", "Shared_Alb")]},
        {"name": "BancFile", "samplers": [("_a0", "Banc_Alb")]},
        {"name": "OddSlot", "samplers": [("tex0", "Slot_Alb"), ("_n0", "Slot_Nrm")]},
        {"name": "Suffix", "samplers": [("_a0", "Bare")]},
        {"name": "Runtime", "samplers": [("tc", "MaterialAlb"), ("ta", "MaterialCmb")]},
        {"name": "Missing", "samplers": [("_a0", "Gone_Alb")]},
        {"name": "Normals", "samplers": [("_n0", "Only_Nrm"), ("_rgh0", "Only_Rgh")]},
    ]
    mesh = sample_v10_bfres("AuditActor", materials)
    model_path = dump.romfs / "Model" / "AuditActor.AuditActor.bfres.zs"
    model_path.write_bytes(compress(mesh, 1, dicts, level=3))
    empty = sample_v10_bfres("AuditEmpty", [{"name": "Empty", "samplers": []}])
    (dump.romfs / "Model" / "AuditEmpty.AuditEmpty.bfres.zs").write_bytes(compress(empty, 1, dicts, level=3))
    _TEX_INDEX.clear()
    _PACK_TEX.clear()

    from ocarina_forge.models import describe_actor

    info = describe_actor(dump, "AuditActor")
    assert info["status"] == "mesh"
    rows = {row["material"]: row for row in info["textureLog"]}
    assert rows["Standard"]["via"] == "sampler"
    assert rows["Nested"]["decoded"] is True
    assert rows["Nested"]["found"] == "Tex/Common/Nested_Alb.bntx.zs"
    assert rows["Cased"]["found"] == "Tex/Case_Alb.BNTX.ZS"
    assert rows["Packed"]["found"].startswith("pack://Pack/Actor/AuditActor.pack.zs//")
    assert rows["Shared"]["found"].startswith("pack://Pack/ResidentCommon.pack.zs//")
    assert rows["BancFile"]["found"] == "Banc/AuditField/Banc_Alb.bntx.zs"
    assert rows["OddSlot"]["via"] == "sampler" and rows["OddSlot"]["wanted"] == "Slot_Alb"
    assert rows["Suffix"]["via"] == "tex-fallback"
    assert rows["Runtime"]["flatCause"] == "runtime-array"
    assert rows["Missing"]["flatCause"] == "missing-file"
    assert "_a0=Gone_Alb" in rows["Missing"]["error"]
    assert "Tex/Gone_Alb.bntx.zs" in rows["Missing"]["error"]
    assert rows["Normals"]["flatCause"] == "non-color-samplers"
    assert "flat colour" in info["reason"]
    assert "Gone_Alb" in info["reason"]
    assert "_a0" in info["reason"]

    bare = describe_actor(dump, "AuditEmpty")
    assert bare["textureLog"][0]["flatCause"] == "untextured"
    assert "no texture names" in bare["textureLog"][0]["error"]

    report = audit_models(dump, prefix="Model/Audit")
    assert report["models"] == 2
    assert report["resolvedViaSampler"] == 7
    assert report["resolvedViaTexFallback"] == 1
    assert report["flat"] == 4
    assert report["flatByCause"]["runtime-array"]["count"] == 1
    assert report["flatByCause"]["untextured"]["count"] == 1
    assert report["flatByCause"]["missing-file"]["count"] == 1
    assert report["flatByCause"]["non-color-samplers"]["count"] == 1
    example = report["flatByCause"]["missing-file"]["examples"][0]
    assert example["binds"] == ["_a0=Gone_Alb"]
    assert "rgba" not in json_blob(report)
    assert "\\" + "x" not in json_blob(report)

    _TEX_INDEX.clear()
    path, tried = _resolve_tex(dump, "Nested_Alb", ["AuditActor"])
    assert path == "Tex/Common/Nested_Alb.bntx.zs"
    assert any("Nested_Alb" in item for item in tried)


def test_unlinked_character_shape_uses_companion_textures_and_names_them(tmp_path, monkeypatch):
    """A hero mesh whose shape index misses, with the albedo in a shared Tex bfres.

    Vertex colours are set on the shape. They must not produce the sentence
    that the material names no albedo, and they must not stop the lookup.
    """
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    dump = Dump(build_fixture(tmp_path / "dump"))
    dicts = dump.dicts()
    from ocarina_forge.formats.bfres import parse, sample_bfres
    from ocarina_forge.models import (
        _COMPANION_IMAGES,
        _COMPANION_LIST,
        _PACK_TEX,
        _TEX_INDEX,
        _convert_cached,
        _resolve_materials,
    )

    _TEX_INDEX.clear()
    _PACK_TEX.clear()
    _COMPANION_LIST.clear()
    _COMPANION_IMAGES.clear()
    colour = bytes([120, 72, 36, 255]) * 4
    mesh = sample_v10_bfres(
        "Npc_Goron_GOU_Biggoron",
        [{"name": "Mt_Body", "samplers": [("_a0", "Npc_Body_Alb"), ("_n0", "Npc_Body_Nrm")]}],
        shapes=[{"name": "Body", "material": 9}],
    )
    model_rel = "Model/Npc_Goron_GOU_Biggoron.Npc_Goron_GOU_Biggoron.bfres.zs"
    (dump.romfs / model_rel).write_bytes(compress(mesh, 1, dicts, level=3))
    child = sample_v10_bfres(
        "Npc_Goron_GOU_Child",
        [{"name": "Mt_Child", "samplers": [("_a0", "Npc_Child_Alb")]}],
    )
    child_rel = "Model/Npc_Goron_GOU_Child.Npc_Goron_GOU_Child.bfres.zs"
    (dump.romfs / child_rel).write_bytes(compress(child, 1, dicts, level=3))
    packed = pack({"Npc_Child_Alb.bntx": write_rgba("Npc_Child_Alb", 2, 2, colour)})
    actor = dump.romfs / "Pack" / "Actor"
    actor.mkdir(parents=True, exist_ok=True)
    (actor / "Npc_Goron_GOU.pack.zs").write_bytes(compress(packed, 4, dicts, level=3))

    hint = f"Npc_Goron_Biggoron_Adult {model_rel}"
    parsed = parse(mesh)
    for part in parsed[0].parts:
        part.colors = [(0.45, 0.28, 0.16, 1.0)] * len(part.positions)
    _external, log, _raws = _resolve_materials(dump, parsed, hint, False)
    assert log[0]["decoded"] is False
    note = _flat_colour_note(parsed[0].parts[0], "")
    assert "Mt_Body" in note
    assert "_a0" in note and "Npc_Body_Alb" in note
    assert "Tex/Npc_Body_Alb.bntx.zs" in note
    assert "Model/Npc_Goron_GOU.Tex.bfres.zs" in note
    assert "vertex colours" in note
    assert "names no albedo texture" not in note
    assert note.endswith("This mesh uses a flat colour.")

    companion = sample_bfres(
        "Npc_Goron_GOU",
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [0, 1, 2],
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        colour,
        2,
        2,
        texture_name="Npc_Body_Alb",
    )
    (dump.romfs / "Model" / "Npc_Goron_GOU.Tex.bfres.zs").write_bytes(compress(companion, 1, dicts, level=3))
    _TEX_INDEX.clear()
    _PACK_TEX.clear()
    _COMPANION_LIST.clear()
    _COMPANION_IMAGES.clear()
    colored = parse(mesh)
    for part in colored[0].parts:
        part.colors = [(0.45, 0.28, 0.16, 1.0)] * len(part.positions)
    external, log, _raws = _resolve_materials(dump, colored, hint, False)
    assert log[0]["via"] == "sampler"
    assert log[0]["decoded"] is True
    assert log[0]["wanted"] == "Npc_Body_Alb"
    assert external["Npc_Body_Alb"].rgba
    assert any("Npc_Goron_GOU.Tex.bfres" in path for path in log[0]["tried"])
    assert "names no albedo texture" not in (log[0].get("error") or "")

    _digest, info = _convert_cached(mesh, dump, hint=hint)
    assert info["textures"] >= 1
    assert "names no albedo texture" not in " ".join(info.get("notes") or [])
    assert info["textureLog"][0]["material"] == "Mt_Body"

    child_hint = f"Npc_Goron_GOU_Child {child_rel}"
    child_models = parse(child)
    _external, child_log, _raws = _resolve_materials(dump, child_models, child_hint, False)
    assert child_log[0]["via"] == "sampler"
    assert child_log[0]["wanted"] == "Npc_Child_Alb"
    assert "Npc_Goron_GOU.pack.zs" in child_log[0]["found"]

    report = audit_models(dump, prefix="Model/Npc_Goron")
    assert report["models"] == 2
    assert report["resolvedViaSampler"] == 2
    assert report["flat"] == 0
    assert "rgba" not in json_blob(report)


def json_blob(report: dict) -> str:
    import json

    return json.dumps(report)

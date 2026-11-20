"""Material groups, the flags the viewer still skips, and the header cache."""

import json
import os

from ocarina_forge.formats.bfres import parse, sample_v10_bfres
from ocarina_forge.materialaudit import _flags, audit, render_text, sketch


def test_the_header_sketch_names_attributes_without_decoding_vertices():
    mesh = sample_v10_bfres(
        "Roof",
        [
            {"name": "Mt_Land_HrlCastle_Roof_Slate_A", "samplers": [("_a0", "slate"), ("_a1", "moss"), ("_rah0", "rah")]},
            {"name": "Mt_Glass_Window", "samplers": [("_a0", "Glass_Alb")], "mode": "translucent"},
        ],
    )
    parsed = parse(mesh)
    header = sketch(mesh)
    assert header["version"] == 10
    assert [row["name"] for row in header["models"][0]["materials"]] == [item.name for item in parsed[0].materials]
    slate = header["models"][0]["materials"][0]
    assert slate["vertexAttributes"] == ["_p0", "_u0"]
    assert [item["sampler"] for item in slate["binds"]] == ["_a0", "_a1", "_rah0"]
    glass = header["models"][0]["materials"][1]
    assert glass["alpha"] == "blend"
    assert glass["vertexAttributes"] == ["_p0", "_u0"]


def test_groups_sort_by_count_and_flag_what_the_viewer_skips(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    romfs = tmp_path / "romfs"
    (romfs / "Model").mkdir(parents=True)
    (romfs / "Tex").mkdir()
    (romfs / "Tex" / "slate.bntx.zs").write_bytes(b"slate")
    (romfs / "Tex" / "moss.bntx.zs").write_bytes(b"moss")
    blend = [
        {"name": "Mt_Land_HrlCastle_Roof_Slate_A", "samplers": [("_a0", "slate"), ("_a1", "moss"), ("_rah0", "rah")]},
    ]
    (romfs / "Model" / "Roof.bfres").write_bytes(sample_v10_bfres("Roof", blend))
    (romfs / "Model" / "Brick.bfres").write_bytes(
        sample_v10_bfres("Brick", [{"name": "Wall_Brick_Blend_A", "samplers": blend[0]["samplers"]}])
    )
    (romfs / "Model" / "Glass.bfres").write_bytes(
        sample_v10_bfres("Glass", [{"name": "Mt_Glass_Window", "samplers": [("_a0", "Glass_Alb")], "mode": "translucent"}])
    )
    (romfs / "Model" / "Nope.bfres").write_bytes(b"not a model")
    (romfs / "Model" / "Skip.anim.bfres").write_bytes(b"not a model")

    from ocarina_forge.dump import Dump

    report = audit(Dump(tmp_path), workers=2)
    assert report["models"] == 3
    assert report["materials"] == 3
    assert report["failed"][0]["file"] == "Model/Nope.bfres"
    assert report["groups"][0]["signature"] == "albedo+rah"
    assert report["groups"][0]["count"] == 2
    assert report["groups"][0]["modelCount"] == 2
    assert "Mt_Land_HrlCastle_Roof_Slate_A" in {item["material"] for item in report["groups"][0]["examples"]}
    glass = next(group for group in report["groups"] if group["signature"] == "albedo")
    assert any("glass" in row["value"] and "translucent" in row["value"] for row in glass["splits"]["tags"])
    assert glass["splits"]["attributes"][0]["value"] == "_p0 _u0"
    kinds = {(item["kind"], item["name"]): item["count"] for item in report["unhandled"]}
    assert ("unknown sampler", "_rah0") not in kinds
    assert kinds[("missing texture", "rah")] == 2
    assert kinds[("missing texture", "Glass_Alb")] == 1
    assert kinds[("unparsed assign", "")] == 3
    text = render_text(report)
    assert text.splitlines()[2].startswith("2  2 models  albedo+rah")
    assert "unhandled" in text
    assert not text.lstrip().startswith("{")

    import ocarina_forge.materialaudit as audit_mod

    calls = {"n": 0}
    real = audit_mod.sketch

    def counting(data):
        calls["n"] += 1
        return real(data)

    monkeypatch.setattr(audit_mod, "sketch", counting)
    again = audit(Dump(tmp_path), workers=1)
    assert calls["n"] == 0
    assert again["materials"] == report["materials"]


def test_the_same_sampler_list_is_one_group_and_the_attribute_split_is_named():
    from ocarina_forge.materialaudit import _report

    shared = [
        {"sampler": "_a0", "texture": "slate"},
        {"sampler": "_n0", "texture": "slate_n"},
        {"sampler": "_rgh0", "texture": "slate_r"},
        {"sampler": "bake0", "texture": "CmnTex_BakeLightDefault"},
        {"sampler": "bake1", "texture": "DummyBake"},
    ]
    wide = {
        "name": "Roof_A",
        "binds": shared,
        "vertexAttributes": ["_p0", "_n0", "_t0", "_u0", "_c0", "bake"],
        "alpha": "opaque",
        "doubleSided": False,
    }
    tight = {
        "name": "Roof_B",
        "binds": shared,
        "vertexAttributes": ["_p0", "_u0"],
        "alpha": "opaque",
        "doubleSided": False,
    }
    report = _report(
        [{"file": "Model/Roof.bfres", "models": [{"name": "Roof", "materials": [wide, tight, wide]}]}],
        {"slate", "slate_n", "slate_r"},
    )
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert group["signature"] == "albedo+normal+roughness+bake"
    assert group["count"] == 3
    assert [row["value"] for row in group["splits"]["attributes"]] == ["_p0 _n0 _t0 _u0 _c0 bake", "_p0 _u0"]
    assert len(group["splits"]["samplers"]) == 1
    text = render_text(report)
    assert "splits on attributes 2" in text
    assert "CmnTex_BakeLightDefault" not in text
    kinds = {(item["kind"], item["name"]) for item in report["unhandled"]}
    assert ("missing texture", "CmnTex_BakeLightDefault") not in kinds
    assert ("missing texture", "DummyBake") not in kinds
    assert ("unknown sampler", "bake0") in kinds
    assert ("unknown sampler", "_rgh0") not in kinds


def test_an_unused_sampler_is_not_also_an_unknown_role():
    flags = _flags(
        {
            "binds": [
                {"sampler": "_a0", "texture": "slate"},
                {"sampler": "_n1", "texture": "moss_n"},
            ],
            "assign": {"_a0": "_u0"},
            "shaderBlocks": [
                {
                    "slot": "+0x10",
                    "attributeNames": ["_p0", "_u0"],
                    "samplerNames": ["_a0", "_n1"],
                    "samplerIndices": [0, 255],
                }
            ],
        },
        {"slate", "moss_n"},
    )
    assert ("unused sampler", "_n1") in flags
    assert not any(kind == "unknown sampler" for kind, _name in flags)
    assert not any(kind == "unparsed assign" for kind, _name in flags)
    assert not any(kind == "missing texture" for kind, _name in flags)


def test_the_cli_writes_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    romfs = tmp_path / "romfs"
    (romfs / "Model").mkdir(parents=True)
    (romfs / "Model" / "One.bfres").write_bytes(
        sample_v10_bfres("One", [{"name": "Wall_Outside_A_Blend", "samplers": [("_a0", "stone")]}])
    )
    out = tmp_path / "audit.json"
    from ocarina_forge.__main__ import print_material_audit

    assert print_material_audit(as_json=True, dump=str(tmp_path), out=str(out)) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert saved["materials"] == 1
    assert printed["groups"][0]["signature"] == "albedo"
    assert os.environ["OCARINA_FORGE_CACHE"]

    text_out = tmp_path / "audit.txt"
    assert print_material_audit(dump=str(tmp_path), out=str(text_out)) == 0
    summary = text_out.read_text(encoding="utf-8")
    assert summary.splitlines()[2] == "1  1 models  albedo"
    assert not summary.lstrip().startswith("{")

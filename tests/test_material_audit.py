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
    labels = [group["label"] for group in report["groups"]]
    assert labels[0].startswith("_a0+_a1 blend")
    assert report["groups"][0]["count"] == 2
    assert report["groups"][0]["modelCount"] == 2
    assert "Mt_Land_HrlCastle_Roof_Slate_A" in {item["material"] for item in report["groups"][0]["examples"]}
    glass = next(group for group in report["groups"] if "glass" in group["tags"])
    assert "translucent" in glass["tags"]
    assert glass["attributes"] == ["_p0", "_u0"]
    kinds = {(item["kind"], item["name"]): item["count"] for item in report["unhandled"]}
    assert kinds[("unknown sampler", "_rah0")] == 2
    assert kinds[("missing texture", "rah")] == 2
    assert kinds[("missing texture", "Glass_Alb")] == 1
    assert kinds[("unparsed assign", "")] == 3
    text = render_text(report)
    assert text.index("_a0+_a1 blend") < text.index("glass")
    assert "unhandled" in text

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
    assert printed["groups"][0]["label"].startswith("_a0 only")
    assert os.environ["OCARINA_FORGE_CACHE"]

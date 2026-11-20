"""The moss overlay: which _c0 channel, which sampler, and what lands in the glb."""

import json
import struct

from ocarina_forge.blend import choose_channel, overlay_plan, sampler_indices
from ocarina_forge.formats.bfres import Material, Model, Part, _close_uv_seams, _part_from
from ocarina_forge.formats.bntx import Texture
from ocarina_forge.formats.gltf import Primitive, to_glb
from ocarina_forge.models import _assemble


def test_a_shader_option_names_the_channel():
    channel, reason = choose_channel({"color0_g": "1"}, None)
    assert channel == "g"
    assert "shader option" in reason


def test_the_only_moving_channel_is_the_mask():
    channel, reason = choose_channel({}, [(0.44, 0.44), (0.10, 0.90), (0.0, 0.0), (1.0, 1.0)])
    assert channel == "g"
    assert "only channel" in reason


def test_red_is_the_weight_when_red_and_green_both_move():
    # The castle roof: r ~0.44..0.82, g ~0.70 and also moving, b flat 0, a flat 1.
    channel, reason = choose_channel({}, [(0.10, 0.82), (0.40, 0.90), (0.0, 0.0), (1.0, 1.0)])
    assert channel == "r"
    assert "green is not the mix" in reason


def test_the_byte_map_is_sampler_indices_and_ff_is_unused():
    names = ["_a0", "_n0", "_rah0", "_a1", "_n1", "_rah1"]
    attributes = ["_p0", "_n0", "_t0", "_u0", "_c0", "bake"]
    raw = [0, 1, 2, 3, 6, 5, 0, 1, 2, 3, 0xFF, 5, 4]
    block = {
        "attributeNames": attributes,
        "samplerNames": names,
        "byteMap": [{"value": value} for value in raw],
    }
    indices = sampler_indices(block)
    assert indices["_a1"] == 3
    assert indices["_n1"] == 255

    unused = Material(
        "roof",
        albedo=[("_a0", "slate"), ("_a1", "moss")],
        shader_blocks=[
            {
                "slot": "+0x10",
                "attributeNames": attributes,
                "samplerNames": names,
                "samplerIndices": [0, 1, 2, 255, 5, 4],
            }
        ],
    )
    assert overlay_plan(unused) is None


def test_the_overlay_keeps_its_own_scale_and_the_assigned_uv():
    material = Material(
        "Mt_Land_HrlCastle_Roof_Slate_A",
        albedo=[("_a0", "slate"), ("_a1", "Land_Hrlcastle_Floor_Stone_Moss_A_01_Alb")],
        assign={"_a1": "_u1"},
        params=[
            {"kind": "texsrt", "name": "tex_srt_a0", "scale": [8, 8], "translate": [0, 0]},
            {"kind": "texsrt", "name": "tex_srt_a1", "scale": [2.5, 1.5], "translate": [0.25, 0.5]},
        ],
    )
    plan = overlay_plan(material, [(0.2, 0.8), (0.4, 0.9), (0.0, 0.0), (1.0, 1.0)])
    assert plan["sampler"] == "_a1"
    assert plan["uv"] == "_u1"
    assert plan["scale"] == [2.5, 1.5]
    assert plan["translate"] == [0.25, 0.5]
    assert plan["channel"] == "r"


def test_a_seam_split_copies_the_vertex_color():
    positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    uvs = [(0.02, 0.5), (0.04, 0.5), (0.98, 0.5)]
    colors = [[(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1)]]
    _close_uv_seams(positions, uvs, [0, 1, 2], colors)
    assert len(colors[0]) == 4
    assert colors[0][-1] == (0, 0, 1, 1)


def test_an_extra_uv_does_not_rename_the_shape():
    positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    part = _part_from(
        "pCube14947",
        positions,
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        None,
        -1,
        uv_extra={"_u1": [(0.2, 0.3), (0.4, 0.5), (0.6, 0.7)]},
    )
    assert part.name == "pCube14947"
    assert part.uv_extra["_u1"][1] == (0.4, 0.5)


def _json(blob: bytes) -> dict:
    chunk = struct.unpack_from("<I", blob, 12)[0]
    return json.loads(blob[20 : 20 + chunk])


def test_the_glb_carries_the_mask_and_a_dark_emissive_overlay():
    slate = Texture("slate", 1, 1, bytes([180, 160, 140, 255]), 0)
    moss = Texture("moss", 1, 1, bytes([40, 120, 40, 255]), 0)
    blend = {
        "channel": "r",
        "uv": "_u1",
        "scale": [2.5, 1.5],
        "translate": [0.25, 0.5],
        "sampler": "_a1",
        "texture": "moss",
    }
    part = Part(
        "roof",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        "slate",
        "Mt_Land_HrlCastle_Roof_Slate_A",
        0,
        colors=[(0.2, 0.7, 0, 1), (0.8, 0.7, 0, 1), (0.5, 0.7, 0, 1)],
        uv_extra={"_u1": [(0.1, 0.2), (0.3, 0.4), (0.5, 0.6)]},
        overlay_texture="moss",
        blend=blend,
    )
    model = Model("castle", parts=[part], images={"slate": slate, "moss": moss})
    blob, info = _assemble([model], {})
    assert any("moss blend" in note for note in info["notes"])
    document = _json(blob)
    primitive = document["meshes"][0]["primitives"][0]
    color = document["accessors"][primitive["attributes"]["COLOR_0"]]
    assert color["type"] == "VEC4"
    assert "TEXCOORD_1" in primitive["attributes"]
    material = document["materials"][primitive["material"]]
    assert material["extras"]["blend"]["channel"] == "r"
    assert material["extras"]["blend"]["scale"] == [2.5, 1.5]
    # 0 is the glTF default, so a packer may omit the factor. Either way it does not glow.
    assert material.get("emissiveFactor", [0, 0, 0]) == [0, 0, 0]
    assert "emissiveTexture" in material


def test_a_plain_emissive_map_still_glows():
    png = to_glb(
        [
            Primitive(
                [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                [0, 1, 2],
                emissive_png=b"\x89PNG\r\n\x1a\n",
            )
        ]
    )
    material = _json(png)["materials"][0]
    assert material["emissiveFactor"] == [1, 1, 1]

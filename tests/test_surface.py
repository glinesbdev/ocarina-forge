"""Packed roughness maps: channel order from the pixels, then one ORM image."""

import json
import struct

from ocarina_forge.formats.bfres import Material, Model, Part
from ocarina_forge.formats.bntx import Texture
from ocarina_forge.models import _accept, _assemble, _surface_choice
from ocarina_forge.surface import classify_packed, collect, pack_layer, surface_role


def _image(width, height, paint) -> Texture:
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            red, green, blue = paint(x, y)
            at = (y * width + x) * 4
            rgba[at] = red
            rgba[at + 1] = green
            rgba[at + 2] = blue
            rgba[at + 3] = 255
    return Texture("packed", width, height, bytes(rgba), 0)


def test_rah_pixels_are_roughness_ao_height():
    image = _image(
        8,
        8,
        lambda x, y: (80 if (x + y) % 2 == 0 else 180, 210, int(x * 255 / 7)),
    )
    assert classify_packed(image.rgba, 8, 8, "rah") == {"roughness": "r", "ao": "g", "height": "b"}


def test_rmh_pixels_are_roughness_metal_height():
    def paint(x, y):
        metal = 255 if x < 2 else 0
        return (80 if (x + y) % 2 == 0 else 180, metal, int(x * 255 / 7))

    image = _image(8, 8, paint)
    assert classify_packed(image.rgba, 8, 8, "rmh") == {"roughness": "r", "metal": "g", "height": "b"}


def test_a_flat_pack_keeps_the_name_prior():
    image = _image(8, 8, lambda x, y: (40, 40, 40))
    assert classify_packed(image.rgba, 8, 8, "rmh")["metal"] == "g"
    assert classify_packed(image.rgba, 8, 8, "rah")["ao"] == "g"


def test_a_dedicated_roughness_overrides_the_packed_channel():
    packed = _image(4, 4, lambda x, y: (10, 200, 80))
    rough = _image(4, 4, lambda x, y: (140, 140, 140))
    image, note = pack_layer([("_rah0", "rah", packed), ("_rgh0", "roughness", rough)])
    assert image is not None
    assert image.rgba[1] == 140
    assert image.rgba[0] == 200
    assert "_rgh0" in note and "roughness" in note


def test_a_bc4_channel_is_kept_for_a_surface_map():
    image = Texture("rgh", 1, 1, bytes([40, 0, 0, 255]), 0x1D01, codec="bc4")
    kept, _mode, _cutoff, _note = _accept(image, "opaque", 0.5, keep_data=True)
    assert kept.rgba[0] == 40
    assert _accept(image, "opaque", 0.5)[0] is None


def _json(blob: bytes) -> dict:
    chunk = struct.unpack_from("<I", blob, 12)[0]
    return json.loads(blob[20 : 20 + chunk])


def test_the_glb_stores_orm_as_png_and_shares_it_with_occlusion():
    packed = _image(4, 4, lambda x, y: (80 if (x + y) % 2 == 0 else 180, 210, int(x * 255 / 3)))
    slate = Texture("slate", 1, 1, bytes([180, 160, 140, 255]), 0)
    material = Material("roof", binds=[("_a0", "slate"), ("_rah0", "rah")], albedo=[("_a0", "slate")])
    model = Model("castle", materials=[material], images={"slate": slate, "rah": packed})
    choice = _surface_choice(None, model, material, [], {}, [])
    assert choice["orm"] is not None
    assert "roughness" in choice["note"]
    part = Part(
        "roof",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        "slate",
        material_index=0,
        orm_image=choice["orm"],
        surface_note=choice["note"],
    )
    model.parts = [part]
    blob, info = _assemble([model], {})
    assert any(note.startswith("surface ") for note in info["notes"])
    document = _json(blob)
    body = document["materials"][0]
    pbr = body["pbrMetallicRoughness"]
    # 1 is the glTF default, so the packer may omit the factor. 0 would have been written.
    assert pbr.get("metallicFactor", 1) == 1
    assert pbr.get("roughnessFactor", 1) == 1
    index = pbr["metallicRoughnessTexture"]["index"]
    assert body["occlusionTexture"]["index"] == index
    assert document["images"][index]["mimeType"] == "image/png"


def test_layer_one_maps_are_named_in_the_extras():
    layer0 = _image(2, 2, lambda x, y: (90, 200, 10))
    layer1 = _image(2, 2, lambda x, y: (40, 30, 220))
    normal = Texture("moss_n", 1, 1, bytes([128, 128, 255, 255]), 0)
    part = Part(
        "roof",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [0, 1, 2],
        [(0, 0), (1, 0), (0, 1)],
        "slate",
        overlay_texture="moss",
        blend={"channel": "r", "uv": "_u0", "scale": [1, 1], "translate": [0, 0], "sampler": "_a1"},
        orm_image=layer0,
        orm1_image=layer1,
        normal1_texture="moss_n",
        colors=[(0.2, 0, 0, 1), (0.8, 0, 0, 1), (0.4, 0, 0, 1)],
    )
    model = Model(
        "castle",
        parts=[part],
        images={
            "slate": Texture("slate", 1, 1, bytes([180, 160, 140, 255]), 0),
            "moss": Texture("moss", 1, 1, bytes([40, 120, 40, 255]), 0),
            "moss_n": normal,
        },
    )
    document = _json(_assemble([model], {})[0])
    body = document["materials"][0]
    surface = body["extras"]["surface"]
    base = body["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]
    assert surface["orm1"] != base
    assert surface["normal1"] != surface["orm1"]


def test_an_unused_overlay_normal_is_not_collected():
    material = Material(
        "roof",
        binds=[("_a0", "slate"), ("_n0", "slate_n"), ("_rgh0", "slate_r"), ("_n1", "moss_n"), ("_n2", "third")],
        shader_blocks=[
            {
                "slot": "+0x10",
                "samplerNames": ["_a0", "_n0", "_rgh0", "_n1", "_n2"],
                "samplerIndices": [0, 1, 2, 255, 3],
            }
        ],
    )
    assert collect(material) == [("_rgh0", "slate_r", "roughness", 0)]
    assert surface_role("_n1") == "normal1"
    assert surface_role("_n2") is None
    assert surface_role("bake0") is None

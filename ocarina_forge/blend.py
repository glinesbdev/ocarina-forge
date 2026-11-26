"""Two-layer albedo blend: ``_a1`` over ``_a0``, masked by one ``_c0`` channel.

The retail ``+0x10`` byte map is an attribute index list, then the sampler
index list when that list is packed beside it. ``0xFF`` means the sampler is
not used in this variation. Those bytes name slots, not a color component.
A shader option that says ``color0_r`` (or g, b, a) picks the component.
Otherwise a channel that is the only one moving is the mask. When red and
another channel both move, the overlay weight is red: on the castle roof,
blue stays 0 and alpha stays 1, and green moves with red but is not the mix.
"""

from __future__ import annotations

import re

_COMPONENT = {"r": "r", "g": "g", "b": "b", "a": "a", "red": "r", "green": "g", "blue": "b", "alpha": "a"}
_NAMED = re.compile(
    r"(?:color0|vtxcolor|vtx_color|vertex_color|vertexcolor|_c0|\bc0)[._ ]?(r|g|b|a|red|green|blue|alpha)\b",
    re.I,
)
_UV = re.compile(r"^(?:_?u\d|_?uv\d|texcoord\d|uvset\d)$", re.I)
_WEIGHT_KEY = re.compile(r"blend|weight|mask|vtx|vertex", re.I)


def sampler_indices(block: dict) -> dict[str, int]:
    """Sampler name to variation index. ``255`` means the sampler is unused.

    An explicit ``samplerIndices`` list wins. Otherwise the bytes after the
    attribute names in ``byteMap`` are that list, which is how the roof's
    table is stored.
    """
    names = [str(name) for name in (block.get("samplerNames") or [])]
    explicit = block.get("samplerIndices")
    if explicit is None:
        raw = [int(item.get("value") or 0) for item in (block.get("byteMap") or []) if isinstance(item, dict)]
        raw = raw[len(block.get("attributeNames") or []) :]
    else:
        raw = [int(item) for item in explicit]
    return {name: raw[index] for index, name in enumerate(names) if index < len(raw)}


def span_from_colors(colors) -> list[tuple[float, float]] | None:
    """``(min, max)`` per component from the shape's vertex colours."""
    if not colors:
        return None
    low = [1.0, 1.0, 1.0, 1.0]
    high = [0.0, 0.0, 0.0, 0.0]
    width = 0
    for item in colors:
        if not item:
            continue
        width = max(width, min(4, len(item)))
        for index in range(min(4, len(item))):
            value = float(item[index])
            low[index] = min(low[index], value)
            high[index] = max(high[index], value)
    if width <= 0:
        return None
    return [(low[index], high[index]) for index in range(width)]


def color_span(attributes) -> list[tuple[float, float]] | None:
    """``(min, max)`` per ``_c0`` component, from a shape's attribute report."""
    for item in attributes or []:
        if (item.get("name") or "") not in {"_c0", "c0", "_c"}:
            continue
        low = item.get("min") or []
        high = item.get("max") or []
        width = min(len(low), len(high), 4)
        if width <= 0:
            return None
        return [(float(low[index]), float(high[index])) for index in range(width)]
    return None


def choose_channel(options: dict | None, span: list[tuple[float, float]] | None) -> tuple[str, str]:
    """The ``_c0`` component that masks ``_a1``, and a short reason."""
    named = _channel_from_options(options or {})
    if named:
        return named, f"shader option names _c0.{named}"
    varying = _varying(span)
    if len(varying) == 1:
        return varying[0], f"_c0.{varying[0]} is the only channel that varies"
    if not varying or "r" in varying:
        return "r", "_c0.r is the overlay weight; blue and alpha stay flat on these blends, and green is not the mix"
    return varying[0], f"_c0.{varying[0]} varies and red does not"


def overlay_plan(material, span: list[tuple[float, float]] | None = None) -> dict | None:
    """How to draw ``_a1`` over ``_a0``. None when this material is a single layer.

    A sampler whose variation index is ``0xFF`` is not part of the shader, so
    it is not the overlay. The UV is the assign's channel for ``_a1`` when it
    names one, and ``_u0`` otherwise. Scale and translate come from ``_a1``'s
    own texture SRT, so the moss tile is not the slate's.
    """
    albedo = list(getattr(material, "albedo", None) or [])
    if len(albedo) < 2:
        return None
    slot, texture = albedo[1]
    if not texture:
        return None
    if not _has_c0(material):
        return None
    indices = _primary_indices(material)
    if indices is not None and slot in indices and indices[slot] == 0xFF:
        return None
    channel, reason = choose_channel(getattr(material, "options", None), span)
    uv = _uv_channel(material, slot)
    scale, translate = _srt(material, slot)
    normal_slot, normal = _bound(material, indices, ("_n1",))
    surface_slot, surface = _bound(material, indices, ("_rah1", "_rmh1"))
    plan = {
        "sampler": slot,
        "texture": texture,
        "channel": channel,
        "reason": reason,
        "uv": uv,
        "scale": [round(float(scale[0]), 6), round(float(scale[1]), 6)],
        "translate": [round(float(translate[0]), 6), round(float(translate[1]), 6)],
    }
    if normal:
        plan["normalSampler"] = normal_slot
        plan["normal"] = normal
    if surface:
        plan["surfaceSampler"] = surface_slot
        plan["surface"] = surface
    return plan


_SKIN_COLOR = re.compile(
    r"(const_color|albedo_color|base_color|diffuse_color|material_color|color0|gsys_color)",
    re.I,
)
_SKIN_SKIP = re.compile(r"(spec|emit|emiss|rim|shadow|fog|light)", re.I)
_MASK_SLOT = re.compile(r"(msk|mask|blend)", re.I)
# A rock mask swings from skin to stone. A little movement is vertex AO.
_MASK_WIDE = 0.4


def skin_overlay_plan(material, span: list[tuple[float, float]] | None = None) -> dict | None:
    """Rock albedo over a skin tint, masked by one vertex-colour channel.

    Longshot does not describe this. A character material can store one albedo
    (the rock) plus a const-colour uniform, and the mesh stores ``_c0``. The
    rock shows where that mask is high. The low weight is the uniform, or the
    vertex rgb when the mask is alpha. Two colour textures stay on
    ``overlay_plan``. A gentle vertex colour, the kind used as AO, is not a mask.
    """
    albedo = list(getattr(material, "albedo", None) or [])
    if not albedo or not albedo[0][1]:
        return None
    if len(albedo) >= 2 and albedo[1][1]:
        return None
    if overlay_plan(material, span):
        return None
    slot, texture = albedo[0]
    indices = _primary_indices(material)
    if indices is not None and slot in indices and indices[slot] == 0xFF:
        return None
    if not span or not _varying(span):
        return None
    options = getattr(material, "options", None) or {}
    if not (_wide(span) or _option_requests_blend(options)):
        return None
    # One albedo plus a wide vertex colour is AO on hair, skin, and eyes.
    # The rock layer is the material whose texture name is the rock, or a
    # shader option that asks for the blend. Treating every such albedo as
    # rock painted Biggoron's skin, face, and teeth with the rock map.
    if not _rock_albedo(material, texture) and not _option_requests_blend(options):
        return None
    channel, reason = _skin_channel(material, span)
    color = _skin_color(material)
    if color is None and channel != "a":
        return None
    mask_slot, mask_tex = _mask_bind(material, indices)
    uv = _uv_channel(material, slot)
    scale, translate = _srt(material, slot)
    plan = {
        "sampler": slot or "_a0",
        "texture": texture,
        "channel": channel,
        "reason": reason,
        "uv": uv,
        "scale": [round(float(scale[0]), 6), round(float(scale[1]), 6)],
        "translate": [round(float(translate[0]), 6), round(float(translate[1]), 6)],
        "skin": True,
        "skinVertex": color is None,
        "skinColor": [round(float(item), 4) for item in color] if color else None,
        "maskSampler": mask_slot,
        "maskTexture": mask_tex,
        "shader": getattr(material, "shader", "") or "",
        "archive": getattr(material, "archive", "") or "",
    }
    return plan


def _rock_albedo(material, texture: str) -> bool:
    """True when this albedo is the rock layer, not the skin, hair, or eyes."""
    return "rock" in f"{getattr(material, 'name', '')} {texture}".lower()


def _skin_color(material) -> tuple[float, float, float, float] | None:
    stored = getattr(material, "base_color", None)
    sane = _sane_tint(stored)
    if sane:
        return sane
    for item in getattr(material, "params", None) or []:
        if not isinstance(item, dict) or item.get("kind") != "float":
            continue
        name = str(item.get("name") or "")
        if not _SKIN_COLOR.search(name) or _SKIN_SKIP.search(name):
            continue
        sane = _sane_tint(item.get("value"))
        if sane:
            return sane
    return None


def _sane_tint(values) -> tuple[float, float, float, float] | None:
    if not values or len(values) < 3:
        return None
    try:
        red, green, blue = float(values[0]), float(values[1]), float(values[2])
        alpha = float(values[3]) if len(values) > 3 else 1.0
    except (TypeError, ValueError):
        return None
    if max(red, green, blue) > 1.5:
        red, green, blue = red / 255.0, green / 255.0, blue / 255.0
        if alpha > 1.5:
            alpha /= 255.0
    if min(red, green, blue, alpha) < 0 or max(red, green, blue, alpha) > 1:
        return None
    return (red, green, blue, alpha)


def _wide(span) -> bool:
    return any(high - low >= _MASK_WIDE for low, high in (span or [])[:4])


def _option_requests_blend(options: dict) -> bool:
    if _channel_from_options(options):
        return True
    for key, value in options.items():
        if _WEIGHT_KEY.search(f"{key} {value}"):
            return True
    return False


def _skin_channel(material, span) -> tuple[str, str]:
    named = _channel_from_options(getattr(material, "options", None) or {})
    if named:
        return named, f"shader option names _c0.{named}"
    ranges = [high - low for low, high in (span or [])[:4]]
    names = "rgba"
    if len(ranges) >= 4 and ranges[3] >= _MASK_WIDE and ranges[3] >= max(ranges[:3] or [0]):
        return "a", "_c0.a is the rock weight; rgb tints the skin"
    wide = [index for index, width in enumerate(ranges) if width >= _MASK_WIDE]
    if len(wide) == 1:
        return names[wide[0]], f"_c0.{names[wide[0]]} is the rock weight"
    channel, reason = choose_channel(getattr(material, "options", None), span)
    return channel, reason


def _mask_bind(material, indices: dict[str, int] | None) -> tuple[str, str]:
    for slot, texture in getattr(material, "binds", None) or []:
        if not texture or not _MASK_SLOT.search(str(slot or "")):
            continue
        if indices is not None and indices.get(slot, 0xFF) == 0xFF:
            continue
        return slot, texture
    return "", ""


def _has_c0(material) -> bool:
    """True when the material has a ``_c0`` attribute, or names no attributes.

    A named attribute list without ``_c0`` is a single layer. Materials that
    do not list attributes (the older samples) still blend when ``_a1`` is used.
    """
    saw = False
    for block in getattr(material, "shader_blocks", None) or []:
        names = [str(name) for name in (block.get("attributeNames") or [])]
        if not names:
            continue
        saw = True
        if any(name in {"_c0", "c0", "_c"} for name in names):
            return True
    return not saw


def _primary_indices(material) -> dict[str, int] | None:
    for block in getattr(material, "shader_blocks", None) or []:
        if block.get("slot") not in (None, "+0x10"):
            continue
        indices = sampler_indices(block)
        if indices:
            return indices
    return None


def _bound(material, indices: dict[str, int] | None, slots: tuple[str, ...]) -> tuple[str, str]:
    """The first of ``slots`` whose variation index is not ``0xFF``."""
    for slot, texture in getattr(material, "binds", None) or []:
        if slot not in slots or not texture:
            continue
        if indices is not None and indices.get(slot, 0xFF) == 0xFF:
            continue
        return slot, texture
    return "", ""


def _channel_from_options(options: dict) -> str | None:
    for key, value in options.items():
        for text in (str(key), str(value)):
            match = _NAMED.search(text)
            if match:
                return _COMPONENT[match.group(1).lower()]
        token = str(value or "").strip().lower()
        if token in _COMPONENT and _WEIGHT_KEY.search(str(key)):
            return _COMPONENT[token]
    return None


def _varying(span: list[tuple[float, float]] | None) -> list[str]:
    if not span:
        return []
    names = "rgba"
    found = []
    for index, (low, high) in enumerate(span[:4]):
        if high - low >= 0.02:
            found.append(names[index])
    return found


def _uv_channel(material, slot: str) -> str:
    assign = getattr(material, "assign", None) or {}
    if slot in assign and _UV.fullmatch(str(assign[slot] or "")):
        return str(assign[slot])
    for key, value in assign.items():
        if value == slot and _UV.fullmatch(str(key or "")):
            return str(key)
    return "_u0"


def _srt(material, slot: str) -> tuple[tuple[float, float], tuple[float, float]]:
    token = "".join(char for char in slot.lower() if char.isalnum())
    matches = []
    for item in getattr(material, "params", None) or []:
        if item.get("kind") != "texsrt":
            continue
        name = "".join(char for char in str(item.get("name") or "").lower() if char.isalnum())
        if token and len(token) >= 2 and token in name:
            matches.append(item)
    if len(matches) != 1:
        return (1.0, 1.0), (0.0, 0.0)
    scale = matches[0].get("scale") or [1.0, 1.0]
    translate = matches[0].get("translate") or [0.0, 0.0]
    if len(scale) < 2 or len(translate) < 2:
        return (1.0, 1.0), (0.0, 0.0)
    return (float(scale[0]), float(scale[1])), (float(translate[0]), float(translate[1]))

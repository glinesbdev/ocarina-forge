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
    for block in getattr(material, "shader_blocks", None) or []:
        if block.get("slot") not in (None, "+0x10"):
            continue
        indices = sampler_indices(block)
        if slot in indices and indices[slot] == 0xFF:
            return None
    channel, reason = choose_channel(getattr(material, "options", None), span)
    uv = _uv_channel(material, slot)
    scale, translate = _srt(material, slot)
    return {
        "sampler": slot,
        "texture": texture,
        "channel": channel,
        "reason": reason,
        "uv": uv,
        "scale": [round(float(scale[0]), 6), round(float(scale[1]), 6)],
        "translate": [round(float(translate[0]), 6), round(float(translate[1]), 6)],
    }


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

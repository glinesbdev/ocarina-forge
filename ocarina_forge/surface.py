"""Roughness, metalness, and ambient occlusion for one material layer.

MeshStandardMaterial reads occlusion from R, roughness from G, and metalness
from B of one image. Height has no slot, so it is stored in A and not sampled.

A packed ``_rah`` map is roughness, AO, height. A packed ``_rmh`` map is
roughness, metal, height. Those initials are the prior. The pixels override
them: metal is bimodal, AO has a high mean, and height is the smoothest
channel. A dedicated ``_rgh``, ``_mt``, or ``_ao`` map wins over the packed
channel. ``_n1`` is the overlay normal. ``_n2`` and anything past layer 1
stay out of this pass.
"""

from __future__ import annotations

from ocarina_forge.blend import sampler_indices
from ocarina_forge.formats.bntx import Texture

_ROUGH_FILL = 217  # 0.85, the flat roughness used when a layer has no map
_AO_FILL = 255
_METAL_FILL = 0
_HEIGHT_FILL = 255


def surface_role(sampler: str) -> str | None:
    """``roughness``, ``metal``, ``ao``, ``rah``, ``rmh``, or ``normal1``.

    ``None`` for albedo, ``_n0``, ``_n2``, bake, and anything this pass does
    not bind. A layer digit above 1 is left unknown on purpose.
    """
    token = _token(sampler)
    layer = _layer(token)
    if layer > 1:
        return None
    if token.startswith("rgh") or token.startswith("rough"):
        return "roughness"
    if token.startswith("rah"):
        return "rah"
    if token.startswith("rmh"):
        return "rmh"
    if token.startswith("mt") or token.startswith("metal"):
        return "metal"
    if token.startswith("ao"):
        return "ao"
    if token in {"n1", "nrm1", "normal1"}:
        return "normal1"
    return None


def collect(material) -> list[tuple[str, str, str, int]]:
    """Used surface samplers as ``(sampler, texture, role, layer)``.

    A variation index of ``0xFF`` is not sampled, so it is not collected.
    """
    found = []
    for slot, texture in list(getattr(material, "binds", None) or []):
        role = surface_role(slot)
        if not role or not texture or _unused(material, slot):
            continue
        found.append((str(slot), str(texture), role, _layer(_token(slot))))
    return found


def classify_packed(rgba: bytes, width: int, height: int, kind: str) -> dict[str, str]:
    """Channel letter per role. ``kind`` is ``rah`` or ``rmh``.

    The prior fills any role the pixels do not separate.
    """
    prior = _prior(kind)
    stats = _stats(rgba, width, height)
    if not stats:
        return dict(prior)
    assigned = _assign(stats)
    used = set(assigned.values())
    for role, channel in prior.items():
        if role not in assigned and channel not in used:
            assigned[role] = channel
            used.add(channel)
    return assigned


def pack_layer(entries: list[tuple[str, str, Texture]]) -> tuple[Texture | None, str]:
    """One ORM image for a layer, and a short note of where each channel came from.

    ``entries`` are ``(sampler, role, image)``. The note names the sampler and
    the channel letters that were actually used.
    """
    slots: dict[str, tuple[Texture, int, str]] = {}
    notes = []
    packed = [item for item in entries if item[1] in {"rah", "rmh"}]
    dedicated = [item for item in entries if item[1] in {"roughness", "metal", "ao"}]
    packed.sort(key=lambda item: 0 if item[1] == "rah" else 1)
    for sampler, role, image in packed + dedicated:
        if role in {"rah", "rmh"}:
            order = classify_packed(image.rgba, image.width, image.height, role)
            letters = []
            for packed_role, channel in order.items():
                if packed_role not in slots:
                    slots[packed_role] = (image, _channel_index(channel), sampler)
                letters.append(f"{channel.upper()} {packed_role}")
            if letters:
                notes.append(f"{sampler} " + ", ".join(letters))
            continue
        if role not in {"roughness", "metal", "ao"}:
            continue
        channel = _dedicated_channel(image.rgba, image.width, image.height)
        slots[role] = (image, channel, sampler)
        notes.append(f"{sampler} {_CHANNEL[channel]} {role}")
    if not slots:
        return None, ""
    width, height = _output_size(slots)
    rgba = bytearray(width * height * 4)
    for index in range(width * height):
        rgba[index * 4] = _AO_FILL
        rgba[index * 4 + 1] = _ROUGH_FILL
        rgba[index * 4 + 2] = _METAL_FILL
        rgba[index * 4 + 3] = _HEIGHT_FILL
    _write(rgba, width, height, slots.get("ao"), 0)
    _write(rgba, width, height, slots.get("roughness"), 1)
    _write(rgba, width, height, slots.get("metal"), 2)
    _write(rgba, width, height, slots.get("height"), 3)
    label = "+".join(item[2] for item in slots.values())
    image = Texture(name=f"orm:{label}", width=width, height=height, rgba=bytes(rgba), fmt=0, codec="orm")
    return image, "; ".join(notes)


def _prior(kind: str) -> dict[str, str]:
    if kind == "rmh":
        return {"roughness": "r", "metal": "g", "height": "b"}
    return {"roughness": "r", "ao": "g", "height": "b"}


def _assign(stats: dict[str, dict[str, float]]) -> dict[str, str]:
    channels = [name for name in "rgb" if name in stats]
    assigned: dict[str, str] = {}
    ranked = sorted(channels, key=lambda name: stats[name]["bimodal"], reverse=True)
    if len(ranked) >= 2 and stats[ranked[0]]["bimodal"] >= 0.45 and stats[ranked[0]]["bimodal"] - stats[ranked[1]]["bimodal"] >= 0.12:
        assigned["metal"] = ranked[0]
    remaining = [name for name in channels if name != assigned.get("metal")]
    by_mean = sorted(remaining, key=lambda name: stats[name]["mean"], reverse=True)
    if len(by_mean) >= 2 and stats[by_mean[0]]["mean"] >= 0.55 and stats[by_mean[0]]["mean"] - stats[by_mean[-1]]["mean"] >= 0.12:
        assigned["ao"] = by_mean[0]
    remaining = [name for name in remaining if name != assigned.get("ao")]
    by_smooth = sorted(remaining, key=lambda name: stats[name]["smooth"])
    if len(by_smooth) >= 2 and stats[by_smooth[1]]["smooth"] > 0 and stats[by_smooth[0]]["smooth"] <= stats[by_smooth[1]]["smooth"] * 0.75:
        assigned["height"] = by_smooth[0]
    remaining = [name for name in remaining if name != assigned.get("height")]
    if len(remaining) == 1:
        assigned["roughness"] = remaining[0]
    return assigned


def _stats(rgba: bytes, width: int, height: int) -> dict[str, dict[str, float]]:
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return {}
    step_x = max(1, width // 48)
    step_y = max(1, height // 48)
    samples = []
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            at = (y * width + x) * 4
            samples.append((x, y, rgba[at], rgba[at + 1], rgba[at + 2]))
    if len(samples) < 4:
        return {}
    out = {}
    for channel, name in enumerate("rgb"):
        values = [item[2 + channel] for item in samples]
        count = len(values)
        mean = sum(values) / count
        near = sum(1 for value in values if value <= 20 or value >= 235)
        smooth = 0.0
        smooth_count = 0
        for x, y, *_rest in samples:
            nx = min(width - 1, x + step_x)
            at = (y * width + nx) * 4 + channel
            if at < len(rgba):
                smooth += abs(rgba[(y * width + x) * 4 + channel] - rgba[at])
                smooth_count += 1
        out[name] = {
            "mean": mean / 255,
            "bimodal": near / count,
            "smooth": (smooth / smooth_count) / 255 if smooth_count else 0.0,
        }
    return out


def _dedicated_channel(rgba: bytes, width: int, height: int) -> int:
    stats = _stats(rgba, width, height)
    if not stats:
        return 0
    spread = max(stats[name]["mean"] for name in "rgb") - min(stats[name]["mean"] for name in "rgb")
    if spread < 0.04:
        return 0
    return max(range(3), key=lambda index: _variance(rgba, width, height, index))


def _variance(rgba: bytes, width: int, height: int, channel: int) -> float:
    step_x = max(1, width // 48)
    step_y = max(1, height // 48)
    values = []
    for y in range(0, height, step_y):
        row = y * width
        for x in range(0, width, step_x):
            at = (row + x) * 4 + channel
            if at < len(rgba):
                values.append(rgba[at])
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _output_size(slots: dict) -> tuple[int, int]:
    width = max(image.width for image, _channel, _sampler in slots.values())
    height = max(image.height for image, _channel, _sampler in slots.values())
    return max(1, min(width, 1024)), max(1, min(height, 1024))


def _write(out: bytearray, width: int, height: int, slot, dest: int) -> None:
    if slot is None:
        return
    image, channel, _sampler = slot
    for y in range(height):
        sy = min(image.height - 1, y * image.height // height) if image.height else 0
        for x in range(width):
            sx = min(image.width - 1, x * image.width // width) if image.width else 0
            at = (sy * image.width + sx) * 4 + channel
            if at < len(image.rgba):
                out[(y * width + x) * 4 + dest] = image.rgba[at]


def _unused(material, sampler: str) -> bool:
    for block in getattr(material, "shader_blocks", None) or []:
        if block.get("slot") not in (None, "", "+0x10"):
            continue
        indices = sampler_indices(block)
        if sampler in indices and indices[sampler] == 0xFF:
            return True
    return False


def _layer(token: str) -> int:
    digits = "".join(char for char in token if char.isdigit())
    if not digits:
        return 0
    return int(digits)


def _token(name: str) -> str:
    return "".join(char for char in (name or "").lower() if char.isalnum())


_CHANNEL = "rgb"


def _channel_index(letter: str) -> int:
    return {"r": 0, "g": 1, "b": 2}.get(letter, 0)

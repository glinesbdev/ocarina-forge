"""Actor identity, categories and the view the editor draws.

A banc actor is one entry of the `Actors` array in a `.bcett.byml`. The fields
the placement files actually use (public custom-map notes, 1.1.1):

    Gyaml       actor name, matching Pack/Actor/<Gyaml>.pack.zs
    Hash        u64 instance id, also stored as Phive.Placement.ID
    SRTHash     u32
    Translate   [x, y, z] metres, or a dictionary X/Y/Z
    Rotate      optional [x, y, z] radians, extrinsic XYZ (Rz · Ry · Rx; yaw is Y)
    Scale       optional [x, y, z]
    Name        optional instance name
    Dynamic     per-instance parameters
    Links, Bakeable, Presence, Phive, ...
"""

from __future__ import annotations

from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import U32, U64
from ocarina_forge.values import tag

ROLLING_ROCK_LAND = "Land_Kokiri_RollingRockArea_A_"

HEADER_KEYS = ("Gyaml", "Hash", "SRTHash", "Translate", "Rotate", "Scale", "Name")

# Prefixes are matched in order. They follow the actor-pack prefixes in the
# public romfs listing (Npc, Enemy, Obj, Land, ...).
_PREFIXES = (
    ("npc", ("Npc_", "Shop_")),
    ("enemy", ("Enemy_",)),
    ("animal", ("Animal_",)),
    ("effect", ("Eft",)),
    ("land", ("Land_",)),
    ("area", ("Area_", "Area")),
    ("object", ("Obj_", "FldObj_", "Item_", "Weapon_", "Dm_")),
    ("system", ("StartPos", "Player", "Sound", "AirWall", "Ground")),
)


def category(gyaml: str) -> str:
    name = gyaml or ""
    # FldObj_AirWall and BGM_Silence are regions. The FldObj_ prefix would
    # otherwise call the air wall an ordinary object and draw its cube solid.
    if "AirWall" in name or name.startswith(("BGM", "Bgm")):
        return "system"
    for label, prefixes in _PREFIXES:
        if name.startswith(prefixes):
            return label
    if "Anchor" in name or name.endswith("Actor"):
        return "system"
    return "other"


def is_marker(gyaml: str) -> bool:
    """An actor that is not supposed to have a mesh.

    Effect locators, tags, and air walls stay boxes. Looking them up is not
    a missing model.
    """
    name = gyaml or ""
    if name.startswith(("EftLoc_", "Loc_", "Tag_", "TeraTreeTag_")) or "Locator" in name:
        return True
    return "AirWall" in name


def is_region(gyaml: str) -> bool:
    """A volume that is a region, not a prop that happens to be scaled up.

    These stay wireframes. A land mesh scaled past 8 metres still draws.
    """
    name = gyaml or ""
    if category(name) in ("area", "system"):
        return True
    # The area name also appears on the authored road and rocks.
    region_name = name.removeprefix(ROLLING_ROCK_LAND).lower()
    return any(word in region_name for word in _VOLUME_WORDS)


# Names that are regions, not props. They are drawn as outlines.
_VOLUME_WORDS = ("area", "volume", "fog", "trigger", "sensor", "collision", "collider", "airwall")


def is_volume(gyaml: str, scale: list[float] | None = None) -> bool:
    """Areas, fog, triggers, and other regions are volumes, not solid props.

    Anything whose placeholder box would be 8 metres or more on an axis is
    treated the same way. A solid box that large hides the meshes inside it.
    A real mesh still replaces the outline once it loads.
    """
    name = gyaml or ""
    if is_region(name):
        return True
    axes = scale or [1.0, 1.0, 1.0]
    try:
        reach = max(abs(float(axis)) for axis in list(axes)[:3])
    except (TypeError, ValueError):
        reach = 1.0
    return reach >= 8


def format_hash(value: int) -> str:
    return f"0x{int(value) & 0xFFFFFFFFFFFFFFFF:016x}"


def parse_hash(value) -> int:
    if isinstance(value, bool) or value is None:
        raise BancError("An actor id has to be a hash, written as 0x followed by hex digits.", "actor")
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        try:
            number = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        except ValueError as exc:
            raise BancError(f"{value!r} is not an actor hash.", "actor") from exc
    if number < 0 or number > 0xFFFFFFFFFFFFFFFF:
        raise BancError(f"{value!r} does not fit in a 64-bit actor hash.", "actor")
    return number


def read_vec(actor: dict, key: str, default: list[float]) -> list[float]:
    current = actor.get(key)
    if isinstance(current, dict) and all(axis in current for axis in ("X", "Y", "Z")):
        return [_float(current[axis], default[i]) for i, axis in enumerate("XYZ")]
    if isinstance(current, (list, tuple)) and len(current) >= 3:
        return [_float(current[0], default[0]), _float(current[1], default[1]), _float(current[2], default[2])]
    return list(default)


def _float(value, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return fallback
    return number


def write_vec(actor: dict, key: str, xyz: list[float]) -> None:
    """Write a vector, keeping an existing X/Y/Z dictionary instead of switching to a list."""
    numbers = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
    current = actor.get(key)
    if isinstance(current, dict) and {"X", "Y", "Z"} <= set(current):
        sample = current.get("X", 0.0)
        from ocarina_forge.formats.byml import F64

        cast = (lambda n: F64(n)) if isinstance(sample, F64) else float
        actor[key] = {axis: cast(number) for axis, number in zip("XYZ", numbers)}
        return
    actor[key] = numbers


def actor_id(actor: dict) -> str | None:
    if "Hash" not in actor:
        return None
    try:
        return format_hash(int(actor["Hash"]))
    except (TypeError, ValueError):
        return None


def _actor_layer(gyaml: str) -> str:
    from ocarina_forge.layers import actor_layer

    return actor_layer(gyaml)


def view_actor(actor: dict, status: str) -> dict:
    gyaml = str(actor.get("Gyaml") or "Unnamed")
    params = {key: value for key, value in actor.items() if key not in HEADER_KEYS}
    srt = actor.get("SRTHash")
    scale = read_vec(actor, "Scale", [1.0, 1.0, 1.0])
    return {
        "id": actor_id(actor),
        "gyaml": gyaml,
        "name": actor.get("Name") if isinstance(actor.get("Name"), str) else None,
        "category": category(gyaml),
        "translate": read_vec(actor, "Translate", [0.0, 0.0, 0.0]),
        "rotate": read_vec(actor, "Rotate", [0.0, 0.0, 0.0]),
        "scale": scale,
        "volume": is_volume(gyaml, scale),
        "region": is_region(gyaml),
        "srtHash": f"0x{int(srt) & 0xFFFFFFFF:08x}" if srt is not None else None,
        "status": status,
        "params": tag(params),
        "layer": _actor_layer(gyaml),
    }


def make_actor(gyaml: str, hash_value: int, translate, rotate=None, scale=None, name: str | None = None, params: dict | None = None) -> dict:
    """A new placement. Hash and Phive.Placement.ID match, as the game expects."""
    actor = {
        "Gyaml": gyaml,
        "Hash": U64(hash_value),
        "SRTHash": U32(hash_value & 0xFFFFFFFF),
        "Translate": [float(v) for v in translate],
        "Phive": {"Placement": {"ID": U64(hash_value)}},
    }
    if rotate is not None:
        actor["Rotate"] = [float(v) for v in rotate]
    if scale is not None:
        actor["Scale"] = [float(v) for v in scale]
    if name:
        actor["Name"] = name
    for key, value in (params or {}).items():
        if key in ("Gyaml", "Hash", "SRTHash"):
            continue
        actor[key] = value
    placement = actor.setdefault("Phive", {}).setdefault("Placement", {})
    placement["ID"] = U64(hash_value)
    actor["Hash"] = U64(hash_value)
    return actor

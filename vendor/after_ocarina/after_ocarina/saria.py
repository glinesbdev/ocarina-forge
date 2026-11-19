"""Optional banc clone for a Saria actor that carries a presence flag.

The sitting ocarina Saria does not. She is Npc_Kokiri_Saria_Child_OcarinaHolder,
she has no Presence block, and her sit comes from the actor's musician params.
``src/scenes/m3-saria.yaml`` leaves ``place`` false. Her spawn is the logic
module in ``after_ocarina.logic``. This cloner still runs when ``place`` is true.

Which actor is the one by the house is decided from the placement files:

1. The only Npc_Kokiri_Saria_Child whose presence names HavePlayedEvent.Dm_Kokiri_0060.
2. Otherwise the only one within 40 units of Link's bed (about -46.5, 118.5, 15.4).
3. Otherwise the only Npc_Kokiri_Saria_Child in the listed files.

Anything else is reported and left unchanged, and the rest of the build still
runs. The ocarina idle is whatever animation the copied actor already carries.
"""

from __future__ import annotations

import copy
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.paths import SCENES_PATH, require_tools
from after_ocarina.sources import _mapping, _rel_path

HOUSE = (-46.5, 118.5, 15.4)
OPENING_FLAG = "HavePlayedEvent.Dm_Kokiri_0060"
EPILOGUE_FLAG = "HavePlayedEvent.TitleEventBase"
GYAML = "Npc_Kokiri_Saria_Child"
STATIC_REL = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"
DYNAMIC_REL = "Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs"
_INVERT_KEYS = frozenset({"IsInverted", "IsNegation", "IsNot", "Not"})
_FRESH_HASH = 0xA70E510000000001
_FRESH_SRT = 0xA70E5101


@dataclass(frozen=True)
class Placement:
    place: bool
    files: tuple[str, ...]
    gyaml: str
    flag: str
    opening_flag: str
    near: tuple[float, float, float]
    near_radius: float
    source: str
    logic: str = ""
    schedule: str = ""


@dataclass(frozen=True)
class _Actor:
    file: str
    index: int
    actor: dict
    strings: tuple[str, ...]
    translate: tuple[float, float, float] | None


def load_placement(path: Path | None = None) -> Placement:
    path = path or (SCENES_PATH.parent / "m3-saria.yaml")
    if not path.is_file():
        raise ModError(f"Missing {path}")
    data = _mapping(path)
    unknown = set(data) - {"place", "files", "gyaml", "flag", "opening_flag", "near", "near_radius", "logic", "schedule"}
    if unknown:
        raise ModError(f"{path.name}: unknown keys: {', '.join(sorted(unknown))}")
    place = data.get("place", True)
    if not isinstance(place, bool):
        raise ModError(f"{path.name}: place must be true or false")
    files = data.get("files", [])
    if place and (not isinstance(files, list) or not files):
        raise ModError(f"{path.name}: files must be a non-empty list when place is true")
    if not isinstance(files, list):
        raise ModError(f"{path.name}: files must be a list")
    rels = tuple(_rel_path(item, path, index, "files", suffix=".byml.zs") for index, item in enumerate(files, start=1))
    logic = ""
    if "logic" in data:
        logic = _rel_path(data.get("logic"), path, 1, "logic", suffix=".ainb")
    schedule = ""
    if "schedule" in data:
        schedule = _rel_path(data.get("schedule"), path, 1, "schedule", suffix=".bgyml")
    gyaml = _text(data.get("gyaml", GYAML), path.name, "gyaml")
    flag = _text(data.get("flag", EPILOGUE_FLAG), path.name, "flag")
    opening = _text(data.get("opening_flag", OPENING_FLAG), path.name, "opening_flag")
    near_value = data.get("near", list(HOUSE))
    if not isinstance(near_value, list) or len(near_value) != 3 or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in near_value):
        raise ModError(f"{path.name}: near must be three numbers")
    radius = data.get("near_radius", 40)
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or radius <= 0:
        raise ModError(f"{path.name}: near_radius must be a positive number")
    return Placement(
        place=place,
        files=rels,
        gyaml=gyaml,
        flag=flag,
        opening_flag=opening,
        near=(float(near_value[0]), float(near_value[1]), float(near_value[2])),
        near_radius=float(radius),
        source=path.name,
        logic=logic,
        schedule=schedule,
    )


def place_actors(unpacked: dict[str, bytes], spec: Placement | None = None) -> tuple[dict[str, bytes], str]:
    """Return the placement files that changed, plus a report.

    A file this cloner cannot read is described in the report and skipped.
    The caller keeps building the rest of the mod.
    """
    spec = spec or load_placement()
    if not spec.place:
        return {}, "Saria placement: place is false, so the banc files were left alone."
    byml = _byml()
    loaded: list[tuple[str, object]] = []
    problems: list[str] = []
    for rel, raw in unpacked.items():
        if raw[:2] != b"YB":
            problems.append(f"{rel}: header is {raw[:2]!r}, and byml.py only rewrites little-endian YB")
            continue
        try:
            loaded.append((rel, byml.loads(raw)))
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            problems.append(f"{rel}: {exc}")
    lines = ["Saria placement:"]
    lines.extend(f"  {problem}" for problem in problems)
    if not loaded:
        lines.append("  No placement file could be read, so Saria was not cloned.")
        lines.append(_spec_text(spec))
        return {}, "\n".join(lines)

    candidates = _candidates(loaded, spec)
    lines.append(_actor_report(candidates, spec))
    chosen, why = _choose(candidates, spec)
    if chosen is None:
        lines.append("  Saria was not cloned. Need one actor from the rules above.")
        lines.append(_spec_text(spec))
        return {}, "\n".join(lines)
    presence = chosen.actor.get("Presence")
    retargeted = _retarget(presence, spec.flag)
    if retargeted is None:
        lines.append(
            f"  {chosen.file} actor {chosen.index} ({chosen.actor.get('Name')!r}) "
            "has no presence flag to retarget, so she was not cloned."
        )
        lines.append(f"  Presence: {_short(presence)}")
        lines.append(_spec_text(spec))
        return {}, "\n".join(lines)

    root = next(item for rel, item in loaded if rel == chosen.file)
    actors = root["Actors"]
    clone = copy.deepcopy(chosen.actor)
    used_hash = _ints(root)
    new_hash = _fresh(used_hash, _FRESH_HASH)
    clone["Hash"] = byml.U64(new_hash)
    phive = clone.get("Phive")
    if isinstance(phive, dict):
        placement = phive.get("Placement")
        if isinstance(placement, dict) and "ID" in placement:
            placement["ID"] = byml.U64(new_hash)
    if "SRTHash" in clone:
        clone["SRTHash"] = byml.U32(_fresh(_ints(root), _FRESH_SRT) & 0xFFFFFFFF)
    name = clone.get("Name")
    clone["Name"] = f"{name}_AtO" if isinstance(name, str) and name else "AtO_Saria"
    clone["Presence"] = retargeted
    actors.append(clone)
    outside = _count(root, int(chosen.actor.get("Hash", -1))) - _count(chosen.actor, int(chosen.actor.get("Hash", -1)))
    try:
        written = byml.dumps(root)
    except Exception as exc:
        if isinstance(exc, ModError):
            raise
        lines.append(f"  Could not rewrite {chosen.file}: {exc}")
        lines.append("  Saria was not cloned. The other build steps still run.")
        lines.append(_spec_text(spec))
        return {}, "\n".join(lines)
    lines.append(
        f"  Cloned {chosen.actor.get('Name')!r} in {chosen.file} as {clone['Name']!r} ({why})."
    )
    lines.append(f"  Presence was {_short(presence)}")
    lines.append(f"  Presence is now {_short(retargeted)}")
    dynamic = chosen.actor.get("Dynamic")
    keys = ", ".join(dynamic) if isinstance(dynamic, dict) and dynamic else "(none)"
    lines.append(f"  Dynamic keys copied with her: {keys}")
    if outside > 0:
        lines.append(
            f"  Her old hash also appears {outside} time(s) outside that actor "
            "(AiGroups, FarDeleteGroups, or a similar list). If she does not show, "
            "add the new hash beside the old one. This build does not edit those lists."
        )
    return {chosen.file: written}, "\n".join(lines)


def sample_banc(spec: Placement | None = None) -> dict[str, bytes]:
    """A little-endian forest placement with the opening Saria by the house.

    The meadow actor is a different gyaml and must not be cloned. A second
    forest Saria stands far from the house and is not the opening one.
    """
    spec = spec or load_placement()
    byml = _byml()
    opening = {
        "Gyaml": spec.gyaml,
        "Name": "OpeningSaria",
        "Hash": byml.U64(1),
        "SRTHash": byml.U32(11),
        "Translate": [-46.0, 118.5, 15.0],
        "Rotate": [0.0, 1.2, 0.0],
        "Scale": [1.0, 1.0, 1.0],
        "Dynamic": {"ASName": "Ocarina", "Schedule": "Sit"},
        "Presence": {"GameDataName": spec.opening_flag, "IsInverted": True},
        "Phive": {"Placement": {"ID": byml.U64(1)}},
    }
    far = {
        "Gyaml": spec.gyaml,
        "Name": "FarSaria",
        "Hash": byml.U64(2),
        "Translate": [200.0, 10.0, 200.0],
        "Presence": {"GameDataName": "HaveTalked_Special.Npc_Kokiri_Saria_Child_Talk_Event20", "IsInverted": False},
    }
    other = {
        "Gyaml": "Npc_Kokiri_014",
        "Name": "OtherKid",
        "Hash": byml.U64(3),
        "Translate": [-46.0, 118.5, 16.0],
    }
    meadow = {
        "Gyaml": "Npc_Kokiri_Saria_Child_SacredForestMeadow",
        "Name": "MeadowSaria",
        "Hash": byml.U64(9),
        "Translate": [-46.0, 118.5, 15.0],
        "Presence": {"GameDataName": spec.opening_flag, "IsInverted": True},
    }
    files = {
        spec.files[0]: {"Actors": [opening, far, other]},
    }
    if len(spec.files) > 1:
        files[spec.files[1]] = {"Actors": [meadow]}
    return {rel: byml.dumps(root) for rel, root in files.items()}


def _candidates(loaded: list[tuple[str, object]], spec: Placement) -> list[_Actor]:
    found: list[_Actor] = []
    for rel, root in loaded:
        actors = root.get("Actors") if isinstance(root, dict) else None
        if not isinstance(actors, list):
            continue
        for index, actor in enumerate(actors):
            if not isinstance(actor, dict) or actor.get("Gyaml") != spec.gyaml:
                continue
            found.append(
                _Actor(
                    file=rel,
                    index=index,
                    actor=actor,
                    strings=tuple(_strings(actor.get("Presence"))),
                    translate=_translate(actor.get("Translate")),
                )
            )
    return found


def _choose(candidates: list[_Actor], spec: Placement) -> tuple[_Actor | None, str | None]:
    flagged = [item for item in candidates if spec.opening_flag in item.strings]
    if len(flagged) == 1:
        return flagged[0], f"presence names {spec.opening_flag}"
    if flagged:
        return None, None
    near = [
        item
        for item in candidates
        if item.translate is not None and _distance(item.translate, spec.near) <= spec.near_radius
    ]
    if len(near) == 1:
        return near[0], "the only one near the house"
    if near:
        return None, None
    if len(candidates) == 1:
        return candidates[0], "the only forest Saria"
    return None, None


def _retarget(presence: object, flag: str) -> object | None:
    if isinstance(presence, str):
        if not _is_flag(presence):
            return None
        return flag
    if isinstance(presence, dict):
        cloned = copy.deepcopy(presence)
        if _replace_flags(cloned, flag) == 0:
            return None
        _clear_inversion(cloned)
        return cloned
    if isinstance(presence, list):
        cloned = copy.deepcopy(presence)
        if _replace_flags(cloned, flag) == 0:
            return None
        _clear_inversion(cloned)
        return cloned
    return None


def _replace_flags(node: object, flag: str) -> int:
    count = 0
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str) and _is_flag(value):
                node[key] = flag
                count += 1
            else:
                count += _replace_flags(value, flag)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str) and _is_flag(value):
                node[index] = flag
                count += 1
            else:
                count += _replace_flags(value, flag)
    return count


def _clear_inversion(node: object) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key in _INVERT_KEYS and isinstance(value, bool):
                node[key] = False
            else:
                _clear_inversion(value)
    elif isinstance(node, list):
        for value in node:
            _clear_inversion(value)


def _is_flag(value: str) -> bool:
    return "." in value and " " not in value and "/" not in value and "\\" not in value


def _strings(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(_strings(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_strings(value))
    return found


def _translate(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _ints(node: object) -> set[int]:
    found: set[int] = set()
    if isinstance(node, int) and not isinstance(node, bool):
        found.add(int(node))
    elif isinstance(node, dict):
        for value in node.values():
            found.update(_ints(value))
    elif isinstance(node, list):
        for value in node:
            found.update(_ints(value))
    return found


def _count(node: object, value: int) -> int:
    if isinstance(node, int) and not isinstance(node, bool) and int(node) == value:
        return 1
    if isinstance(node, dict):
        return sum(_count(item, value) for item in node.values())
    if isinstance(node, list):
        return sum(_count(item, value) for item in node)
    return 0


def _fresh(used: set[int], start: int) -> int:
    value = start
    while value in used:
        value += 1
    return value


def _actor_report(candidates: list[_Actor], spec: Placement) -> str:
    if not candidates:
        return f"  No {spec.gyaml} actors."
    lines = [f"  {spec.gyaml} actors:"]
    for item in candidates:
        where = "no Translate" if item.translate is None else ", ".join(f"{part:.2f}" for part in item.translate)
        flags = ", ".join(item.strings) if item.strings else "(no presence strings)"
        lines.append(f"    {item.file} [{item.index}] {item.actor.get('Name')!r} at {where}; presence {flags}")
    return "\n".join(lines)


def _spec_text(spec: Placement) -> str:
    files = ", ".join(spec.files)
    return (
        "  On the dump, check these and paste the actors if she is missing:\n"
        f"    files: {files}\n"
        "    .venv/bin/python tools/byml.py json <romfs>/<file> --dict <romfs>/Pack/ZsDic.pack.zs\n"
        f"    Look for Gyaml {spec.gyaml}. Record Name, Translate, Presence, and Dynamic.\n"
        "    If the opening sit is only inside Dm_Kokiri_0060, this clone cannot play it.\n"
        "    .venv/bin/python tools/evflmod.py dump Npc_Kokiri_Saria_Child\n"
        "    If the hooked entry is not the talk, set entry_point in src/events/m3-saria-talk.yaml."
    )


def _short(value: object) -> str:
    text = repr(value)
    if len(text) > 400:
        return text[:400] + "..."
    return text


def _text(value: object, where: str, key: str) -> str:
    if not isinstance(value, str) or not value or any(ch in value for ch in "\n\r"):
        raise ModError(f"{where}: {key} must be a single line")
    return value


def _byml():
    tools = str(require_tools())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import byml

    return byml

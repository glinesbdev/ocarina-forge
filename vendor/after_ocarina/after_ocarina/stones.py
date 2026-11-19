"""Place one display Spiritual Stone in each child hometown.

The actors are new. ``saria.place_actors`` clones an actor that is already in
the destination banc, and that cloner has not been seen in game, so this
module does not use it. The param shape comes from the vanilla altar stone of
the same Gyaml in ``Market_Child_Static``. ``Links`` and any other hash that
points at a different Market actor are dropped. A pack or model path the dump
does not contain stops the build. Nothing is substituted.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.logic import TITLE_EVENT
from after_ocarina.paths import SCENES_PATH
from after_ocarina.saria import _byml, _fresh
from after_ocarina.sources import _mapping, _rel_path

TEMPLATE_REL = "Banc/Market/Market_Child_Static.bcett.byml.zs"
KOKIRI_REL = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"
GORON_REL = "Banc/GoronCity/GoronCity_Child_Static.bcett.byml.zs"
ZORA_REL = "Banc/ZoraDomain/ZoraDomain_Child_Static.bcett.byml.zs"
_FILE_SUFFIXES = (".pack", ".bfres", ".bphsh", ".bgyml", ".byml")
_HASHES = {
    "Obj_TimeTemple_SpiritStone_A_01": 0xA70E510000000041,
    "Obj_TimeTemple_SpiritStone_A_02": 0xA70E510000000042,
    "Obj_TimeTemple_SpiritStone_A_03": 0xA70E510000000043,
}
_SRT = {
    "Obj_TimeTemple_SpiritStone_A_01": 0xA70E5141,
    "Obj_TimeTemple_SpiritStone_A_02": 0xA70E5142,
    "Obj_TimeTemple_SpiritStone_A_03": 0xA70E5143,
}


@dataclass(frozen=True)
class DisplayStone:
    gyaml: str
    name: str
    banc: str
    translate: tuple[float, float, float]
    y_offset: float
    rotate: tuple[float, float, float] | None
    vanilla_entry: int
    source: str


@dataclass(frozen=True)
class StonePlacement:
    flag: str
    template: str
    stones: tuple[DisplayStone, ...]
    source: str


def load_stones(path: Path | None = None) -> StonePlacement:
    path = path or (SCENES_PATH.parent / "m6-stones.yaml")
    if not path.is_file():
        raise ModError(f"Missing {path}")
    data = _mapping(path)
    unknown = set(data) - {"flag", "template", "stones"}
    if unknown:
        raise ModError(f"{path.name}: unknown keys: {', '.join(sorted(unknown))}")
    if data.get("flag") != TITLE_EVENT:
        raise ModError(f"{path.name}: flag must be {TITLE_EVENT}")
    template = _rel_path(data.get("template"), path, 0, "template", suffix=".byml.zs")
    rows = data.get("stones")
    if not isinstance(rows, list) or not rows:
        raise ModError(f"{path.name}: stones must be a non-empty list")
    stones: list[DisplayStone] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ModError(f"{path.name}: stone {index} must be a mapping")
        extra = set(row) - {"gyaml", "name", "banc", "translate", "y_offset", "rotate", "vanilla_entry"}
        if extra:
            raise ModError(f"{path.name}: stone {index} has unknown keys: {', '.join(sorted(extra))}")
        gyaml = _line(row.get("gyaml"), path.name, index, "gyaml")
        if gyaml in seen:
            raise ModError(f"{path.name}: {gyaml} is listed twice")
        seen.add(gyaml)
        banc = _rel_path(row.get("banc"), path, index, "banc", suffix=".byml.zs")
        if not banc.endswith("_Static.bcett.byml.zs"):
            raise ModError(f"{path.name}: stone {index} banc must be a Static placement")
        translate = _vec(row.get("translate"), path.name, index, "translate")
        rotate = _vec(row.get("rotate"), path.name, index, "rotate") if "rotate" in row else None
        offset = row.get("y_offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)):
            raise ModError(f"{path.name}: stone {index} y_offset must be a number")
        entry = row.get("vanilla_entry")
        if isinstance(entry, bool) or not isinstance(entry, int) or entry <= 0:
            raise ModError(f"{path.name}: stone {index} vanilla_entry must be the dump's size-table entry")
        stones.append(
            DisplayStone(
                gyaml=gyaml,
                name=_line(row.get("name"), path.name, index, "name"),
                banc=banc,
                translate=translate,
                y_offset=float(offset),
                rotate=rotate,
                vanilla_entry=entry,
                source=path.name,
            )
        )
    return StonePlacement(flag=TITLE_EVENT, template=template, stones=tuple(stones), source=path.name)


def place_stones(
    dests: dict[str, bytes],
    template: bytes,
    spec: StonePlacement,
    romfs: Path,
) -> tuple[dict[str, bytes], str]:
    """Append one display stone to each destination Static banc."""
    byml = _byml()
    if template[:2] != b"YB":
        raise ModError(f"{spec.template}: header is {template[:2]!r}. byml.py only rewrites little-endian YB.")
    try:
        market = byml.loads(template)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ModError(f"{spec.template}: {exc}") from None
    actors = market.get("Actors") if isinstance(market, dict) else None
    if not isinstance(actors, list):
        raise ModError(f"{spec.template}: no Actors list")
    source_hashes = _actor_hashes(actors)
    roots: dict[str, dict] = {}
    for rel, raw in dests.items():
        if raw[:2] != b"YB":
            raise ModError(f"{rel}: header is {raw[:2]!r}. byml.py only rewrites little-endian YB.")
        try:
            roots[rel] = byml.loads(raw)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ModError(f"{rel}: {exc}") from None
    notes = ["Display stones:"]
    for stone in spec.stones:
        source, donor_index = _donor(actors, stone.gyaml, spec.template)
        root = roots.get(stone.banc)
        if root is None:
            raise ModError(f"{stone.banc} was not loaded")
        dest_actors = root.get("Actors")
        if not isinstance(dest_actors, list):
            raise ModError(f"{stone.banc}: no Actors list")
        if any(isinstance(actor, dict) and actor.get("Name") == stone.name for actor in dest_actors):
            raise ModError(
                f"{stone.banc}: already has {stone.name}. Build from a vanilla placement."
            )
        placed, dropped = _display_actor(source, stone, source_hashes, byml)
        _require_dependencies(placed, romfs, stone.gyaml)
        dest_actors.append(placed)
        y = stone.translate[1] + stone.y_offset
        dropped_text = ", ".join(dropped) if dropped else "none"
        notes.append(
            f"  {stone.name} ({stone.gyaml}, Market actor {donor_index}) in {stone.banc} at "
            f"({stone.translate[0]}, {y}, {stone.translate[2]}), y_offset {stone.y_offset}. "
            f"Presence {TITLE_EVENT}, not inverted, not lazy. Dropped Market links: {dropped_text}."
        )
    written: dict[str, bytes] = {}
    for rel, root in roots.items():
        try:
            written[rel] = byml.dumps(root)
        except Exception as exc:
            raise ModError(f"{rel}: could not rewrite the placement ({exc})") from None
    return written, "\n".join(notes)


def sample_market_banc() -> bytes:
    """Altar stones out of the order first assumed, plus a link the placement must drop.

    A 1.1.1 dump has ``Obj_TimeTemple_SpiritStone_A_03`` at actor 357. The
    fixture keeps that and puts the other two on the remaining slots, so a
    lookup by the old index fails and a lookup by Gyaml still finds each one.
    """
    byml = _byml()
    actors: list[dict] = []
    for index in range(359):
        actors.append({"Gyaml": "FldObj_Filler", "Name": f"Filler{index}", "Hash": byml.U64(index + 1)})
    door = byml.U64(6160701382657199989)
    actors[354] = {"Gyaml": "Obj_DoorOfTime", "Name": "Door", "Hash": door}
    for index, gyaml, name in (
        (355, "Obj_TimeTemple_SpiritStone_A_02", "AltarGoron"),
        (357, "Obj_TimeTemple_SpiritStone_A_03", "AltarZora"),
        (358, "Obj_TimeTemple_SpiritStone_A_01", "AltarKokiri"),
    ):
        actors[index] = _altar(byml, gyaml, name, byml.U64(10_000 + index), door)
    return byml.dumps({"Actors": actors, "AiGroups": [{"Hashes": [door]}]})


def _donor(actors: list, gyaml: str, template: str) -> tuple[dict, int]:
    matches = [
        index
        for index, actor in enumerate(actors)
        if isinstance(actor, dict) and actor.get("Gyaml") == gyaml
    ]
    if len(matches) != 1:
        where = ", ".join(str(index) for index in matches) if matches else "none"
        raise ModError(
            f"{template}: {gyaml} matches {where}. Need exactly one. "
            "The build will not take a different actor."
        )
    return actors[matches[0]], matches[0]


def sample_town_banc(name: str) -> bytes:
    """A hometown Static file with one unrelated actor. Built for the tests."""
    byml = _byml()
    return byml.dumps(
        {
            "Actors": [
                {
                    "Gyaml": "StartPos",
                    "Name": name,
                    "Hash": byml.U64(42),
                    "Translate": [1.0, 2.0, 3.0],
                }
            ]
        }
    )


def _altar(byml, gyaml: str, name: str, hash_value, door) -> dict:
    return {
        "Gyaml": gyaml,
        "Name": name,
        "Hash": hash_value,
        "SRTHash": byml.U32(20),
        "Translate": [1.0, 2.0, 3.0],
        "Rotate": [0.0, 1.5, 0.0],
        "Scale": [1.0, 1.0, 1.0],
        "Bakeable": False,
        "Dynamic": {
            "Model": f"Model/{gyaml}.bfres",
            "AltarHash": door,
        },
        "Links": [{"Hash": door, "Gyaml": "Obj_DoorOfTime"}],
        "Phive": {"Placement": {"ID": hash_value}},
    }


def _display_actor(source: dict, stone: DisplayStone, source_hashes: set[int], byml) -> tuple[dict, list[str]]:
    actor = copy.deepcopy(source)
    dropped: list[str] = []
    if "Links" in actor:
        dropped.append("Links")
        del actor["Links"]
    old_hash = int(actor.get("Hash", -1))
    others = {item for item in source_hashes if item != old_hash}
    used = set(others)
    new_hash = _fresh(used, _HASHES.get(stone.gyaml, 0xA70E510000000040))
    new_srt = _fresh(used | {new_hash}, _SRT.get(stone.gyaml, 0xA70E5140)) & 0xFFFFFFFF
    actor["Name"] = stone.name
    actor["Hash"] = byml.U64(new_hash)
    actor["SRTHash"] = byml.U32(new_srt)
    x, y, z = stone.translate
    actor["Translate"] = [float(x), float(y + stone.y_offset), float(z)]
    if stone.rotate is not None:
        actor["Rotate"] = [float(part) for part in stone.rotate]
    phive = actor.get("Phive")
    if not isinstance(phive, dict):
        phive = {}
        actor["Phive"] = phive
    placement = phive.get("Placement")
    if not isinstance(placement, dict):
        placement = {}
        phive["Placement"] = placement
    placement["ID"] = byml.U64(new_hash)
    actor["Presence"] = {"GameDataName": TITLE_EVENT, "IsInverted": False, "IsLazy": False}
    _retarget_hashes(actor, old_hash, new_hash, others, dropped)
    return actor, dropped


def _retarget_hashes(node: object, old: int, new: int, others: set[int], dropped: list[str], key: str = "") -> None:
    if isinstance(node, dict):
        for name in list(node):
            if name in {"Hash", "ID", "SRTHash"}:
                continue
            value = node[name]
            if isinstance(value, int) and not isinstance(value, bool) and int(value) in others:
                dropped.append(name)
                del node[name]
            elif isinstance(value, int) and not isinstance(value, bool) and int(value) == old:
                node[name] = type(value)(new) if type(value) is not int else new
            else:
                _retarget_hashes(value, old, new, others, dropped, name)
    elif isinstance(node, list):
        kept = []
        for value in node:
            if isinstance(value, int) and not isinstance(value, bool) and int(value) in others:
                dropped.append(key or "list")
                continue
            if isinstance(value, int) and not isinstance(value, bool) and int(value) == old:
                kept.append(type(value)(new) if type(value) is not int else new)
            else:
                _retarget_hashes(value, old, new, others, dropped, key)
                kept.append(value)
        node[:] = kept


def _require_dependencies(actor: dict, romfs: Path, gyaml: str) -> None:
    pack = f"Pack/Actor/{gyaml}.pack.zs"
    if not (romfs / pack).is_file():
        raise ModError(f"{gyaml} needs {pack}, and this dump does not have it. The build will not guess another actor.")
    for text in _path_strings(actor):
        rel = text[5:] if text.startswith("Work/") else text
        rel = rel.lstrip("/")
        if not rel.endswith(_FILE_SUFFIXES) and not rel.endswith(tuple(item + ".zs" for item in _FILE_SUFFIXES)):
            continue
        candidates = [rel]
        if not rel.endswith(".zs"):
            candidates.append(rel + ".zs")
        if any((romfs / item).is_file() for item in candidates):
            continue
        raise ModError(f"{gyaml} points at {text}, and this dump does not have it. The build will not guess a replacement.")


def _path_strings(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, str) and "/" in node:
        found.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(_path_strings(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_path_strings(value))
    return found


def _actor_hashes(actors: list) -> set[int]:
    found: set[int] = set()
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        value = actor.get("Hash")
        if isinstance(value, int) and not isinstance(value, bool):
            found.add(int(value))
    return found


def _line(value: object, where: str, index: int, key: str) -> str:
    if not isinstance(value, str) or not value or any(ch in value for ch in "\n\r/"):
        raise ModError(f"{where}: stone {index} {key} must be a single name")
    return value


def _vec(value: object, where: str, index: int, key: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3 or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ModError(f"{where}: stone {index} {key} must be three numbers")
    return (float(value[0]), float(value[1]), float(value[2]))

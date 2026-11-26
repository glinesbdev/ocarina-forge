"""Infer which decorations sit on a parent piece.

The banc does not store that relationship. Longshot's modding API (spec
revision 4, ``longshot.d.ts``) agrees: ``content.place`` and
``read.placements`` have no parent, and ``has()`` has no placement-group
feature. ``actors.attach`` is a live bone bind (``has("actors.attach")``),
not a placement. ``Links`` and the banc group lists are not decoration
parents. This module does not use them, and it does not use a shared name
or pack: those miss and false-hit on the measured fixture. See
docs/formats.md.
"""

from __future__ import annotations

import math

from ocarina_forge.actors import actor_id, format_hash, parse_hash, view_actor
from ocarina_forge.errors import BancError
from ocarina_forge.layers import actor_layer

_GROUP_KEYS = ("AiGroups", "FarDeleteGroups", "SimultaneousGroups", "PlgGdTable")

# Metres added on every side of the parent's world box.
GROUP_MARGIN = 1.5
# Half-size of the stand-in box used until a mesh bound arrives.
FALLBACK_HALF = 3.0
# Metres from a pivot for the add/remove lists in the properties panel.
CANDIDATE_RADIUS = 8.0

_NAME_STOP = frozenset({
    "land", "obj", "fldobj", "item", "the", "a", "b", "c", "d", "e",
})


def _vec(value, default):
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return list(default)
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return list(default)


def transform_point(point, translate, rotate, scale) -> tuple[float, float, float]:
    """Apply actor scale, then extrinsic XYZ rotation (Rz · Ry · Rx), then translation."""
    translate = _vec(translate, (0.0, 0.0, 0.0))
    rotate = _vec(rotate, (0.0, 0.0, 0.0))
    scale = _vec(scale, (1.0, 1.0, 1.0))
    x = float(point[0]) * scale[0]
    y = float(point[1]) * scale[1]
    z = float(point[2]) * scale[2]
    cx, sx = math.cos(rotate[0]), math.sin(rotate[0])
    y, z = y * cx - z * sx, y * sx + z * cx
    cy, sy = math.cos(rotate[1]), math.sin(rotate[1])
    x, z = x * cy + z * sy, -x * sy + z * cy
    cz, sz = math.cos(rotate[2]), math.sin(rotate[2])
    x, y = x * cz - y * sz, x * sz + y * cz
    return (x + translate[0], y + translate[1], z + translate[2])


def _local_box(bounds):
    if isinstance(bounds, dict) and bounds.get("min") and bounds.get("max"):
        return _vec(bounds["min"], (0, 0, 0)), _vec(bounds["max"], (0, 0, 0)), True
    half = FALLBACK_HALF
    return [-half, -half, -half], [half, half, half], False


def world_box(actor: dict, bounds=None) -> dict:
    """Axis-aligned world box of a local bound, or the 3 m stand-in."""
    local_min, local_max, authored = _local_box(bounds)
    corners = []
    for x in (local_min[0], local_max[0]):
        for y in (local_min[1], local_max[1]):
            for z in (local_min[2], local_max[2]):
                corners.append(transform_point(
                    (x, y, z), actor.get("translate"), actor.get("rotate"), actor.get("scale"),
                ))
    return {
        "min": [min(corner[axis] for corner in corners) for axis in range(3)],
        "max": [max(corner[axis] for corner in corners) for axis in range(3)],
        "authored": authored,
    }


def expand_box(box: dict, margin: float = GROUP_MARGIN) -> dict:
    return {
        "min": [box["min"][axis] - margin for axis in range(3)],
        "max": [box["max"][axis] + margin for axis in range(3)],
        "authored": box.get("authored", False),
    }


def _contains(box: dict, point) -> bool:
    point = _vec(point, (0.0, 0.0, 0.0))
    return all(box["min"][axis] <= point[axis] <= box["max"][axis] for axis in range(3))


def _volume(box: dict) -> float:
    span = 1.0
    for axis in range(3):
        span *= max(0.0, box["max"][axis] - box["min"][axis])
    return span


def _distance(left, right) -> float:
    a = _vec(left, (0.0, 0.0, 0.0))
    b = _vec(right, (0.0, 0.0, 0.0))
    return math.sqrt(sum((a[axis] - b[axis]) ** 2 for axis in range(3)))


def is_decoration(actor: dict) -> bool:
    if not actor or actor.get("status") in ("removed", "locked", "recipe"):
        return False
    layer = actor.get("layer") or actor_layer(str(actor.get("gyaml") or ""))
    return layer == "decorations"


def is_parent_piece(actor: dict, *, has_bounds: bool = False) -> bool:
    """A Land_, Obj_, or FldObj_ mesh. Regions are not parents.

    A placeholder volume (scale of 8 m or more, before a mesh bound exists)
    is not a parent. Once bounds exist, the smallest box wins, so a field
    does not take a decoration that also sits on a house.
    """
    if not actor or actor.get("status") in ("removed", "locked", "recipe"):
        return False
    if actor.get("region"):
        return False
    if actor.get("volume") and not has_bounds:
        return False
    name = str(actor.get("gyaml") or "")
    if actor_layer(name) != "models":
        return False
    return name.startswith(("Land_", "Obj_", "FldObj_"))


def _tokens(name: str) -> set[str]:
    from ocarina_forge.layers import _tokens as layer_tokens

    return {
        token for token in layer_tokens(name)
        if token not in _NAME_STOP and not token.isdigit()
    }


def shared_name_tokens(left: str, right: str) -> list[str]:
    """Distinctive tokens shared by two Gyaml names. Diagnostic only."""
    return sorted(_tokens(left) & _tokens(right))


def normalize_groups(raw) -> dict:
    """``include`` maps a decoration hash to a parent hash. ``exclude`` drops the auto group."""
    include: dict[str, str] = {}
    exclude: list[str] = []
    if isinstance(raw, dict):
        incoming = raw.get("include") or {}
        if isinstance(incoming, dict):
            for child, parent in incoming.items():
                try:
                    include[format_hash(parse_hash(child))] = format_hash(parse_hash(parent))
                except BancError:
                    continue
        incoming_exclude = raw.get("exclude") or []
        if isinstance(incoming_exclude, list):
            for child in incoming_exclude:
                try:
                    ident = format_hash(parse_hash(child))
                except BancError:
                    continue
                if ident not in include and ident not in exclude:
                    exclude.append(ident)
    exclude.sort()
    return {"include": include, "exclude": exclude}


def groups_or_none(raw) -> dict | None:
    groups = normalize_groups(raw)
    if groups["include"] or groups["exclude"]:
        return groups
    return None


def resolve_groups(actors: list[dict], *, bounds_for=None, include=None, exclude=None, margin: float = GROUP_MARGIN) -> dict:
    """One parent per decoration: an override, else the smallest expanded box that contains it."""
    bounds_for = bounds_for or (lambda _actor: None)
    include = include or {}
    excluded = set(exclude or [])
    rows = [actor for actor in actors or [] if isinstance(actor, dict) and actor.get("id")]
    parents = []
    for actor in rows:
        bounds = bounds_for(actor)
        if is_parent_piece(actor, has_bounds=bool(bounds and bounds.get("min") and bounds.get("max"))):
            parents.append((actor, bounds))
    parent_ids = {actor["id"] for actor, _bounds in parents}
    child_of: dict[str, str] = {}
    reason: dict[str, str] = {}
    for child in rows:
        if not is_decoration(child):
            continue
        if child["id"] in excluded:
            reason[child["id"]] = "excluded"
            continue
        forced = include.get(child["id"])
        if forced and forced in parent_ids and forced != child["id"]:
            child_of[child["id"]] = forced
            reason[child["id"]] = "override"
            continue
        point = child.get("translate")
        best = None
        for parent, bounds in parents:
            world = expand_box(world_box(parent, bounds), margin)
            if not _contains(world, point):
                continue
            volume = _volume(world)
            dist = _distance(point, parent.get("translate"))
            authored = bool(world.get("authored"))
            if best is None or volume < best["volume"] - 1e-6 or (
                abs(volume - best["volume"]) <= 1e-6 and dist < best["distance"]
            ):
                best = {"id": parent["id"], "volume": volume, "distance": dist, "authored": authored}
        if best:
            child_of[child["id"]] = best["id"]
            reason[child["id"]] = "bounds" if best["authored"] else "fallback"
        else:
            reason[child["id"]] = "standalone"
    by_parent: dict[str, list[str]] = {actor["id"]: [] for actor, _bounds in parents}
    for child_id, parent_id in child_of.items():
        by_parent.setdefault(parent_id, []).append(child_id)
    return {"childOf": child_of, "byParent": by_parent, "reason": reason}


def _hashes_in(value, found: set[int]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        # U64 hashes, and any wider int. A small plain int is a count, not an actor.
        if type(value) is not int or value > 0xFFFFFFFF:
            found.add(int(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _hashes_in(item, found)
    elif isinstance(value, list):
        for item in value:
            _hashes_in(item, found)


def _group_entries(document: dict) -> list[tuple[str, set[int]]]:
    entries = []
    for key in _GROUP_KEYS:
        value = document.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            found: set[int] = set()
            _hashes_in(item, found)
            if found:
                entries.append((key, found))
    header = document.get("PhiveHeader")
    if isinstance(header, dict) and header.get("PlacementGroups"):
        found = set()
        _hashes_in(header["PlacementGroups"], found)
        if found:
            entries.append(("PhiveHeader.PlacementGroups", found))
    return entries


def _link_targets(actor: dict) -> list[int]:
    own = actor_id(actor)
    targets = []
    for link in actor.get("Links") or []:
        if not isinstance(link, dict):
            continue
        for key in ("Dst", "Hash"):
            value = link.get(key)
            if isinstance(value, bool) or value is None:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number and (own is None or format_hash(number) != own):
                targets.append(number)
    return targets


def authored_ties(document: dict) -> dict:
    """Count file references. These are not used to build a group."""
    actors = [actor for actor in (document.get("Actors") or []) if isinstance(actor, dict) and actor_id(actor)]
    by_hash = {}
    for actor in actors:
        try:
            by_hash[parse_hash(actor_id(actor))] = actor
        except BancError:
            continue
    parent_hashes = set()
    for number, actor in by_hash.items():
        row = view_actor(actor, "vanilla")
        if is_parent_piece(row, has_bounds=True):
            parent_hashes.add(number)
    entries = _group_entries(document)
    decorations = []
    with_links = 0
    link_to_parent = 0
    in_group_list = 0
    shares_group_with_parent = 0
    for actor in actors:
        row = view_actor(actor, "vanilla")
        if not is_decoration(row):
            continue
        decorations.append(actor)
        try:
            number = parse_hash(actor_id(actor))
        except BancError:
            continue
        targets = _link_targets(actor)
        if targets:
            with_links += 1
            if any(target in parent_hashes for target in targets):
                link_to_parent += 1
        for _label, hashes in entries:
            if number not in hashes:
                continue
            in_group_list += 1
            if hashes & parent_hashes:
                shares_group_with_parent += 1
            break
    return {
        "decorationsWithLinks": with_links,
        "decorationLinksToParent": link_to_parent,
        "decorationsInGroupLists": in_group_list,
        "decorationSharesGroupWithParent": shares_group_with_parent,
        "decorationCount": len(decorations),
    }


def measure(document: dict, bounds_for=None) -> dict:
    """How the proximity rule treats one banc, plus whether name or pack would agree.

    ``bounds_for`` receives a view row and returns ``{"min", "max"}`` or None.
    Missing bounds use the 3 m stand-in. The returned ``nameHeuristicReliable``
    and ``packHeuristicReliable`` flags are false when that cue would group a
    different set than proximity. The editor does not use either cue.
    """
    if not isinstance(document, dict) or not isinstance(document.get("Actors"), list):
        raise BancError("A decoration report needs a placement document with an Actors array.", "bad_byml")
    rows = []
    for actor in document["Actors"]:
        if isinstance(actor, dict) and actor_id(actor):
            rows.append(view_actor(actor, "vanilla"))
    by_id = {row["id"]: row for row in rows}
    resolved = resolve_groups(rows, bounds_for=bounds_for)
    reasons = resolved["reason"]
    grouped_bounds = sum(1 for reason in reasons.values() if reason == "bounds")
    grouped_fallback = sum(1 for reason in reasons.values() if reason == "fallback")
    name_agree = 0
    name_grouped_without = 0
    name_points_elsewhere = 0
    same_pack = 0
    for child_id, reason in reasons.items():
        if reason not in ("bounds", "fallback", "override"):
            child = by_id.get(child_id)
            if not child:
                continue
            elsewhere = [
                parent for parent in rows
                if is_parent_piece(parent, has_bounds=True) and shared_name_tokens(child.get("gyaml") or "", parent.get("gyaml") or "")
            ]
            if elsewhere:
                name_points_elsewhere += 1
            continue
        child = by_id[child_id]
        parent = by_id.get(resolved["childOf"][child_id])
        if not parent:
            continue
        shared = shared_name_tokens(child.get("gyaml") or "", parent.get("gyaml") or "")
        if shared:
            name_agree += 1
        else:
            name_grouped_without += 1
        if (child.get("gyaml") or "") == (parent.get("gyaml") or ""):
            same_pack += 1
        other = [
            candidate for candidate in rows
            if candidate["id"] != parent["id"]
            and is_parent_piece(candidate, has_bounds=True)
            and shared_name_tokens(child.get("gyaml") or "", candidate.get("gyaml") or "")
            and not shared
        ]
        if other:
            name_points_elsewhere += 1
    grouped = grouped_bounds + grouped_fallback + sum(1 for reason in reasons.values() if reason == "override")
    standalone = sum(1 for reason in reasons.values() if reason == "standalone")
    ties = authored_ties(document)
    name_reliable = name_grouped_without == 0 and name_points_elsewhere == 0 and grouped > 0 and name_agree == grouped
    return {
        **ties,
        "parents": sum(1 for row in rows if is_parent_piece(row, has_bounds=True)),
        "grouped": grouped,
        "groupedByBounds": grouped_bounds,
        "groupedByFallback": grouped_fallback,
        "standalone": standalone,
        "excluded": sum(1 for reason in reasons.values() if reason == "excluded"),
        "nameAgreesWithProximity": name_agree,
        "nameGroupedWithoutSharedToken": name_grouped_without,
        "namePointsAtADifferentParent": name_points_elsewhere,
        "samePackAsParent": same_pack,
        "nameHeuristicReliable": name_reliable,
        "packHeuristicReliable": same_pack > 0 and same_pack == grouped and name_grouped_without == 0,
        "usesLinksOrGroups": False,
    }


def render_measure(report: dict) -> str:
    lines = [
        f"Decorations: {report['decorationCount']}. Grouped by proximity: {report['grouped']} "
        f"({report['groupedByBounds']} from mesh bounds, {report['groupedByFallback']} from the 3 m stand-in). "
        f"Standalone: {report['standalone']}.",
        f"Authored Links on decorations: {report['decorationsWithLinks']}. "
        f"Those links name a parent piece: {report['decorationLinksToParent']}.",
        f"Decorations listed in AiGroups, FarDeleteGroups, SimultaneousGroups, PlgGdTable, "
        f"or PhiveHeader.PlacementGroups: {report['decorationsInGroupLists']}. "
        f"Sharing that list with a parent piece: {report['decorationSharesGroupWithParent']}.",
        f"Name token agrees with the proximity parent: {report['nameAgreesWithProximity']}. "
        f"Proximity groups with no shared token: {report['nameGroupedWithoutSharedToken']}. "
        f"A shared token names a different piece: {report['namePointsAtADifferentParent']}. "
        f"Name heuristic reliable: {report['nameHeuristicReliable']}.",
        f"Same Gyaml (same pack) as the proximity parent: {report['samePackAsParent']}. "
        f"Pack heuristic reliable: {report['packHeuristicReliable']}.",
        "Grouping uses the smallest parent box expanded by 1.5 m. Links, group lists, names, and packs are not used.",
    ]
    return "\n".join(lines) + "\n"

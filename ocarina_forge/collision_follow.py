"""Move baked collision with an actor, or mint the SRTHash that may drop the bake.

Longshot's modding API (spec revision 4, 2026-11-19, and the schema behind
``longshot.d.ts``) does not edit a Phive bake. ``has("world.ray")`` is a
collision query and was false on the build recorded in ``docs/formats.md``.
``actors.setPosition`` writes the live actor, not the banc and not
``StaticCompoundBody``. Placement patches change ``Translate``, ``Rotate``,
``Scale``, and, on an add, ``SRTHash``. None of those rewrite a compound shape.

Two export modes, both off unless the project asks:

- ``srt`` (variant B). A moved actor keeps its ``Hash`` and its
  ``Phive.Placement.ID``. Export writes a new ``SRTHash``. No ``.bphsh`` and
  no ``.bphsc`` is shipped. The shape file is untouched. Whether the game then
  instances the class shape at the new pose is the in-game test.
- ``bake``. ``SRTHash`` stays, so a lookup by hash and SRTHash still hits the
  same row. Export copies each tile that contains the actor and moves that
  row's compound instances. The shape bytes are not rewritten.

``off`` is today's export: the mesh moves and the bake is not touched.
"""

from __future__ import annotations

import shutil
import zlib
from dataclasses import dataclass, field

from ocarina_forge.actors import actor_id, format_hash, parse_hash, read_vec
from ocarina_forge.errors import BancError
from ocarina_forge.formats.phive import CompoundLayoutError, compound_actor_spans, patch_compound_transform
from ocarina_forge.scene import _base_map, _close

MODES = ("off", "srt", "bake")

# Child Kokiri sign from the placement patch Bradyn already loads.
# Static, instance KokiriForest_013, Obj_Kokiri_Signboard_A_01.
SIGN_HASH = 0x4A13F8880EB01A43
SIGN_BANC = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"
SIGN_GYAML = "Obj_Kokiri_Signboard_A_01"
SHIFT_X = 4.0


@dataclass
class PoseMove:
    hash: str
    hash_int: int
    gyaml: str
    old: dict
    new: dict
    srt_old: int | None
    srt_new: int | None = None
    tiles: list[str] = field(default_factory=list)


def collision_mode(project, override: str | None = None) -> str:
    """``off``, ``srt``, or ``bake``. A typo is an error, not a silent off."""
    if override:
        mode = str(override).strip().lower()
    else:
        manifest = getattr(project, "manifest", {}) or {}
        raw = manifest.get("collisionFollow", manifest.get("collision_follow", "off"))
        mode = "off" if raw is None else str(raw).strip().lower()
    if mode not in MODES:
        raise BancError(
            f"collisionFollow is {mode!r}. Use off, srt, or bake. "
            "off leaves the bake where it was. srt mints a new SRTHash and does not "
            "write a shape. bake moves the compound instance and keeps the shape bytes.",
            "collision",
        )
    return mode


def mint_srt_hash(actor_hash: int, used: set[int]) -> int:
    """A new u32 for this actor. It is not a shape, and it is not the instance id.

    The value is the CRC of a fixed label plus the actor hash, bumped until it
    is not already an SRTHash in the banc. Zero is skipped.
    """
    seed = zlib.crc32(
        f"ocarina-forge-collision-follow-b:{int(actor_hash) & 0xFFFFFFFFFFFFFFFF:016x}".encode()
    ) & 0xFFFFFFFF
    candidate = seed or 1
    occupied = {int(value) & 0xFFFFFFFF for value in used}
    start = candidate
    while candidate in occupied:
        candidate = (candidate + 1) & 0xFFFFFFFF
        if candidate == 0:
            candidate = 1
        if candidate == start:
            raise BancError("Every SRTHash in this banc is already taken.", "collision")
    return candidate


def _pose(actor, record, key, field, default):
    if record.get(key) is not None:
        return [float(value) for value in record[key]]
    return read_vec(actor, field, default)


def pose_moves(document: dict, diff: dict) -> list[PoseMove]:
    """Actors whose translate, rotate, or scale differs from the banc.

    Adds, hides, and removes are not this path. An added actor already has a
    new SRTHash from ``make_actor``.
    """
    base = _base_map(document)
    moves = []
    for ident, record in (diff.get("actors") or {}).items():
        if not isinstance(record, dict):
            continue
        if record.get("state") in ("hidden", "removed", "added") or "actor" in record:
            continue
        try:
            hashed = format_hash(parse_hash(ident))
        except BancError:
            continue
        actor = base.get(hashed)
        if actor is None:
            continue
        old_t = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        old_r = read_vec(actor, "Rotate", [0.0, 0.0, 0.0])
        old_s = read_vec(actor, "Scale", [1.0, 1.0, 1.0])
        new_t = _pose(actor, record, "translate", "Translate", old_t)
        new_r = _pose(actor, record, "rotate", "Rotate", old_r)
        new_s = _pose(actor, record, "scale", "Scale", old_s)
        if _close(new_t, old_t) and _close(new_r, old_r) and _close(new_s, old_s):
            continue
        srt = actor.get("SRTHash")
        moves.append(PoseMove(
            hash=hashed,
            hash_int=parse_hash(hashed),
            gyaml=str(actor.get("Gyaml") or record.get("gyaml") or "Actor"),
            old={"translate": old_t, "rotate": old_r, "scale": old_s},
            new={"translate": new_t, "rotate": new_r, "scale": new_s},
            srt_old=None if srt is None else int(srt) & 0xFFFFFFFF,
        ))
    return moves


def assign_srts(document: dict, moves: list[PoseMove]) -> None:
    used = set()
    for actor in document.get("Actors") or []:
        if isinstance(actor, dict) and actor.get("SRTHash") is not None:
            used.add(int(actor["SRTHash"]) & 0xFFFFFFFF)
    for move in moves:
        if move.srt_old is not None:
            used.add(move.srt_old)
        minted = mint_srt_hash(move.hash_int, used)
        move.srt_new = minted
        used.add(minted)


def apply_srts(document: dict, moves: list[PoseMove]) -> None:
    """Write minted SRTHash values onto the exported banc. Hash and Placement.ID stay."""
    by_hash = {move.hash: move for move in moves if move.srt_new is not None}
    if not by_hash:
        return
    from ocarina_forge.formats.byml import U32

    for actor in document.get("Actors") or []:
        if not isinstance(actor, dict):
            continue
        ident = actor_id(actor)
        move = by_hash.get(ident)
        if move is None:
            continue
        actor["SRTHash"] = U32(move.srt_new)
        placement = actor.get("Phive")
        if isinstance(placement, dict):
            slot = placement.get("Placement")
            if isinstance(slot, dict) and "ID" in slot and int(slot["ID"]) != int(actor["Hash"]):
                raise BancError(
                    f"{move.gyaml} ({ident}) has Phive.Placement.ID different from Hash. "
                    "SRTHash was not changed.",
                    "collision",
                )


def srt_ops(moves: list[PoseMove]) -> list[dict]:
    ops = []
    for move in moves:
        if move.srt_new is None:
            continue
        # The key is already on a placed actor. ``add`` is only for a pose that
        # had no SRTHash, which grows the banc the same way a new Scale does.
        op = "set" if move.srt_old is not None else "add"
        ops.append({"op": op, "hash": move.hash, "path": "SRTHash", "value": int(move.srt_new)})
    return ops


def scene_compound_names(banc: str) -> list[str]:
    """Directory names under Phive/StaticCompoundBody that may hold this banc.

    The issue names ``<Scene>``. The banc stem, the stem without ``_Static``
    or ``_Dynamic``, and the area folder are the three names tried. The grid
    (Kokiri 4×4 per age, Hyrule Field 256 tiles per age) is not assumed.
    """
    text = banc.replace("\\", "/")
    leaf = text.rsplit("/", 1)[-1]
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if leaf.endswith(suffix):
            leaf = leaf[: -len(suffix)]
            break
    names = [leaf]
    for token in ("_Static", "_Dynamic"):
        if leaf.endswith(token):
            names.append(leaf[: -len(token)])
            break
    parts = text.split("/")
    if len(parts) >= 2 and parts[0] == "Banc" and parts[1] not in names:
        names.append(parts[1])
    return names


def compound_files(dump, banc: str) -> tuple[bool, list[str]]:
    """``(a scene directory exists, romfs-relative .bphsc paths)``."""
    romfs = dump.romfs
    found_dir = False
    paths: list[str] = []
    seen: set[str] = set()
    for name in scene_compound_names(banc):
        folder = romfs / "Phive" / "StaticCompoundBody" / name
        if not folder.is_dir():
            continue
        found_dir = True
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if not (path.name.endswith(".bphsc") or path.name.endswith(".bphsc.zs")):
                continue
            relative = path.relative_to(romfs).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            paths.append(relative)
    return found_dir, paths


def bake_tiles(dump, jobs: list[tuple[str, list[PoseMove]]]) -> list[tuple[str, bytes, int | None]]:
    """Patched tiles for every banc in ``jobs``.

    ``jobs`` is ``(banc, moves)``. Static and Dynamic both name
    ``KokiriForest_Child``, so they share ``X1_Z2``. Each tile is read from
    the dump once, and every move that belongs to a banc listing that tile is
    applied before the tile is returned. A later banc must not start over from
    the dump and drop the earlier banc's instances. A tile that mentions an
    actor and cannot be patched stops the export. The returned bytes are the
    same length as the unpacked source.
    """
    planned: dict[str, list[PoseMove]] = {}
    order: list[str] = []
    for banc, moves in jobs:
        if not moves:
            continue
        found_dir, paths = compound_files(dump, banc)
        if not found_dir:
            continue
        for relative in paths:
            bucket = planned.get(relative)
            if bucket is None:
                bucket = []
                planned[relative] = bucket
                order.append(relative)
            seen = {move.hash_int for move in bucket}
            for move in moves:
                if move.hash_int in seen:
                    continue
                seen.add(move.hash_int)
                bucket.append(move)
    written: list[tuple[str, bytes, int | None]] = []
    for relative in order:
        raw, dict_id = dump.read_file(relative)
        current = raw
        touched = False
        for move in planned[relative]:
            try:
                patched = patch_compound_transform(current, move.hash_int, move.old, move.new)
            except CompoundLayoutError as exc:
                raise BancError(
                    f"{relative}: {exc}. The shape was not rewritten and this bake was not exported.",
                    "collision",
                ) from exc
            if patched is None:
                continue
            if len(patched) != len(raw):
                raise BancError(
                    f"{relative}: the patched bake changed size. The shape must stay the same.",
                    "collision",
                )
            current = patched
            touched = True
            if relative not in move.tiles:
                move.tiles.append(relative)
        if touched:
            written.append((relative, current, dict_id))
    return written


def requested_banc(explicit: str | None, positional: str | None = None) -> str:
    """``--banc`` wins over the positional banc. The Kokiri sign banc is the default."""
    for value in (explicit, positional):
        text = (value or "").strip()
        if text:
            return text
    return SIGN_BANC


def paired_banc(banc: str) -> str | None:
    """The Static banc beside a Dynamic one, and the reverse. Same folder and extension."""
    folder, _, name = banc.replace("\\", "/").rpartition("/")
    stem = name
    ext = ""
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            ext = suffix
            break
    if not ext:
        return None
    for source, dest in (("Static", "Dynamic"), ("Dynamic", "Static")):
        token = f"_{source}"
        if stem.endswith(token):
            other = stem[: -len(token)] + f"_{dest}"
            return f"{folder}/{other}{ext}" if folder else f"{other}{ext}"
    return None


def _open_banc(dump, banc: str):
    try:
        document, _dict_id, _raw = dump.read_byml(banc)
    except BancError as exc:
        if exc.code != "not_found":
            raise
        return None
    return document


def hashes_with_bodies(dump, banc: str) -> set[int]:
    """Actor hashes whose compound row has a body in any tile of this scene.

    ``first == 0xFFFFFFFF`` is a table row with no body. That hash is left out.
    """
    found_dir, paths = compound_files(dump, banc)
    if not found_dir:
        return set()
    found: set[int] = set()
    for relative in paths:
        raw, _dict_id = dump.read_file(relative)
        spans = compound_actor_spans(raw)
        if not spans:
            continue
        for hash_value, (first, last) in spans.items():
            if first == 0xFFFFFFFF or last == 0xFFFFFFFF or last < first:
                continue
            found.add(int(hash_value))
    return found


def _land_rows(document: dict) -> list[dict]:
    rows = []
    for actor in document.get("Actors") or []:
        if not isinstance(actor, dict) or not actor_id(actor):
            continue
        gyaml = str(actor.get("Gyaml") or "")
        if not gyaml.startswith("Land_"):
            continue
        if actor.get("Bakeable") is False:
            continue
        rows.append(actor)
    return rows


def _best_land(origin, placed: list[tuple[str, dict]], has_body: set[int] | None):
    """Nearest eligible Land_. A row with a compound body ranks ahead of one without."""
    ranked = []
    for banc, actor in placed:
        ident = actor_id(actor)
        pos = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        distance = sum((a - b) ** 2 for a, b in zip(pos, origin))
        if has_body is None:
            baked = 0
        else:
            baked = 0 if parse_hash(ident) in has_body else 1
        ranked.append((baked, distance, ident, banc, actor))
    ranked.sort()
    if not ranked:
        return None
    return ranked[0][3], ranked[0][4]


def choose_variant_b(document: dict, only_hash: str | None = None, has_body: set[int] | None = None) -> list[dict]:
    """The sign, plus the nearest Land_ that is not Bakeable false.

    ``only_hash`` keeps a single actor and does not add the Land_.
    """
    actors = [actor for actor in document.get("Actors") or [] if isinstance(actor, dict) and actor_id(actor)]
    if only_hash:
        wanted = format_hash(parse_hash(only_hash))
        match = [actor for actor in actors if actor_id(actor) == wanted]
        if not match:
            raise BancError(f"No actor {wanted} is placed in this scene.", "collision")
        return match

    sign_id = format_hash(SIGN_HASH)
    sign = next((actor for actor in actors if actor_id(actor) == sign_id), None)
    if sign is None:
        raise BancError(
            f"This banc has no {SIGN_GYAML} at {sign_id} ({SIGN_BANC}, KokiriForest_013). "
            "Variant B moves that sign 4 m on +X, and the nearest Land_ that is not Bakeable false. "
            "Pass --id to move one actor in another scene.",
            "collision",
        )
    if str(sign.get("Gyaml") or "") != SIGN_GYAML:
        raise BancError(
            f"{sign_id} is {sign.get('Gyaml')}, not {SIGN_GYAML}. Nothing was written.",
            "collision",
        )
    origin = read_vec(sign, "Translate", [0.0, 0.0, 0.0])
    placed = [("", actor) for actor in _land_rows(document) if actor_id(actor) != sign_id]
    best = _best_land(origin, placed, has_body)
    chosen = [sign]
    if best is not None:
        chosen.append(best[1])
    return chosen


def _actor_line(banc: str, actor: dict, has_body: set[int] | None) -> tuple[str, dict]:
    ident = actor_id(actor)
    old = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
    new = [old[0] + SHIFT_X, old[1], old[2]]
    record = {"state": "edited", "gyaml": str(actor.get("Gyaml") or ""), "translate": new}
    srt = actor.get("SRTHash")
    srt_text = "none" if srt is None else f"0x{int(srt) & 0xFFFFFFFF:08x}"
    baked = "unknown"
    if has_body is not None:
        baked = "body" if parse_hash(ident) in has_body else "no body"
    line = (
        f"{banc}: {actor.get('Gyaml')} {ident}  translate {old} -> {new}  "
        f"SRTHash {srt_text}  Bakeable {actor.get('Bakeable')!r}  compound {baked}"
    )
    return line, record


def replace_scene_tree(project, diffs: list[dict]) -> None:
    """Drop every scene yaml, then write ``diffs``. Export must not ship a previous run."""
    folder = project.root / "scenes"
    if folder.exists():
        shutil.rmtree(folder)
    for diff in diffs:
        project.save_scene_diff(diff)


def prepare_variant_b(dump, project, *, only_hash: str | None = None, banc: str | None = None) -> list[str]:
    """Write this run's scene diffs: +4 m on X, and nothing left from the previous run.

    ``--id`` keeps that one actor. The default run moves the Kokiri sign and
    the Land_ that prefers a compound row with a body, searching the paired
    Dynamic or Static banc as well.
    """
    target = banc or SIGN_BANC
    primary = _open_banc(dump, target)
    if primary is None:
        raise BancError(f"{target} is not in this dump.", "collision")
    other_name = paired_banc(target)
    other = _open_banc(dump, other_name) if other_name else None
    bodies = hashes_with_bodies(dump, target)
    if other_name and other is not None:
        bodies |= hashes_with_bodies(dump, other_name)

    grouped: dict[str, list[dict]] = {}
    if only_hash:
        wanted = format_hash(parse_hash(only_hash))
        home = target if any(actor_id(actor) == wanted for actor in primary.get("Actors") or []) else None
        document = primary
        if home is None and other is not None and any(actor_id(actor) == wanted for actor in other.get("Actors") or []):
            home = other_name
            document = other
        if home is None:
            where = target if other is None else f"{target} or {other_name}"
            raise BancError(
                f"No actor {wanted} is placed in {where}. "
                "Pass --banc for a scene outside that pair.",
                "collision",
            )
        grouped[home] = choose_variant_b(document, only_hash=wanted)
    else:
        sign_id = format_hash(SIGN_HASH)
        if not any(actor_id(actor) == sign_id for actor in primary.get("Actors") or []):
            raise BancError(
                f"This banc has no {SIGN_GYAML} at {sign_id} ({SIGN_BANC}, KokiriForest_013). "
                "The default run moves that sign 4 m on +X, and the Land_ whose compound row has a body. "
                "Pass --id to move one actor. --banc selects the scene.",
                "collision",
            )
        chosen = choose_variant_b(primary, has_body=bodies)
        sign = chosen[0]
        grouped[target] = [sign]
        origin = read_vec(sign, "Translate", [0.0, 0.0, 0.0])
        placed = [(target, actor) for actor in _land_rows(primary) if actor_id(actor) != sign_id]
        if other is not None and other_name:
            placed.extend((other_name, actor) for actor in _land_rows(other))
        best = _best_land(origin, placed, bodies)
        if best is not None:
            land_banc, land = best
            grouped.setdefault(land_banc, []).append(land)

    diffs = []
    lines = []
    for scene_banc, actors in grouped.items():
        payload = {}
        for actor in actors:
            line, record = _actor_line(scene_banc, actor, bodies)
            payload[actor_id(actor)] = record
            lines.append(line)
        diffs.append({"format": 1, "banc": scene_banc, "actors": payload})
    replace_scene_tree(project, diffs)
    land_lines = [line for line in lines if "Land_" in line]
    if not only_hash and not land_lines:
        lines.append(
            "No Land_ actor was added. The sign's baked row is in KokiriForest_Child_X1_Z2.bphsc. "
            "A Land_ with no compound body, such as the fern, is not that test."
        )
    elif not only_hash and not any(line.endswith("compound body") for line in land_lines):
        lines.append(
            "The Land_ that was added has no compound body. It is in the actor table and it is not baked collision."
        )
    return lines

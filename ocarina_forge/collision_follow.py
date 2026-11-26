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

import zlib
from dataclasses import dataclass, field

from ocarina_forge.actors import actor_id, format_hash, parse_hash, read_vec
from ocarina_forge.errors import BancError
from ocarina_forge.formats.phive import CompoundLayoutError, patch_compound_transform
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


def bake_tiles(dump, banc: str, moves: list[PoseMove]) -> list[tuple[str, bytes, int | None]]:
    """Patched tile bytes for every move that this scene's compounds contain.

    A tile that does not mention the actor is left out. A tile that mentions
    the actor and cannot be patched stops the export. The returned bytes are
    the same length as the unpacked source.
    """
    found_dir, paths = compound_files(dump, banc)
    if not found_dir or not moves:
        return []
    written: list[tuple[str, bytes, int | None]] = []
    for relative in paths:
        raw, dict_id = dump.read_file(relative)
        current = raw
        touched = False
        for move in moves:
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
            move.tiles.append(relative)
        if touched:
            written.append((relative, current, dict_id))
    return written


def choose_variant_b(document: dict, only_hash: str | None = None) -> list[dict]:
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
    lands = []
    for actor in actors:
        if actor_id(actor) == sign_id:
            continue
        gyaml = str(actor.get("Gyaml") or "")
        if not gyaml.startswith("Land_"):
            continue
        if actor.get("Bakeable") is False:
            continue
        pos = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        distance = sum((a - b) ** 2 for a, b in zip(pos, origin))
        lands.append((distance, actor_id(actor), actor))
    lands.sort(key=lambda item: (item[0], item[1]))
    chosen = [sign]
    if lands:
        chosen.append(lands[0][2])
    return chosen


def prepare_variant_b(dump, project, *, only_hash: str | None = None, banc: str | None = None) -> list[str]:
    """Write the variant B scene diff: +4 m on X, ready for ``collisionFollow: srt``."""
    target = banc or SIGN_BANC
    document, _dict_id, _raw = dump.read_byml(target)
    chosen = choose_variant_b(document, only_hash=only_hash)
    actors = {}
    lines = []
    for actor in chosen:
        ident = actor_id(actor)
        old = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        new = [old[0] + SHIFT_X, old[1], old[2]]
        actors[ident] = {"state": "edited", "gyaml": str(actor.get("Gyaml") or ""), "translate": new}
        srt = actor.get("SRTHash")
        srt_text = "none" if srt is None else f"0x{int(srt) & 0xFFFFFFFF:08x}"
        bakeable = actor.get("Bakeable")
        lines.append(
            f"{actor.get('Gyaml')} {ident}  translate {old} -> {new}  "
            f"SRTHash {srt_text}  Bakeable {bakeable!r}"
        )
    project.save_scene_diff({"format": 1, "banc": target, "actors": actors})
    if not any(str(actor.get("Gyaml") or "").startswith("Land_") for actor in chosen):
        lines.append(
            "No Land_ actor was added. The sign alone does not answer the baked-static question "
            "unless that sign is itself baked."
        )
    return lines

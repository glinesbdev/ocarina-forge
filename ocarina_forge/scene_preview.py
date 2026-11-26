"""Build and locate the optional, local preview pack for a single map."""

from __future__ import annotations

import array
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ocarina_forge.actors import ROLLING_ROCK_LAND
from ocarina_forge.errors import BancError
from ocarina_forge.formats import terrain as heightmap
from ocarina_forge.models import CacheMiss, _cached_glb, cache_dir, describe_actor, describe_terrain
from ocarina_forge.scene import scene_view, sibling_bancs
from ocarina_forge.world_surface import physical_triangles, rolling_rock_map


TERRAIN = "@terrain"
FISHING_POND_WATER = "Obj_LakeHrl_WaterPlane_A_07"
KOKIRI_CHILD = "Banc/KokiriForest/KokiriForest_Child_"
WORKER = Path(__file__).resolve().parents[1] / "frontend" / "tools" / "scene-preview-worker.mjs"
PREVIEW_VERSION = "scene-preview-v2"
# Hyrule Field has its own terrain pack. Both folders are current.
TERRAIN_VERSION = "scene-terrain-v12"
TERRAIN_FIELD_VERSION = "scene-terrain-v13"


def preview_bank(dump, banc: str, known: set[str] | None = None) -> str:
    """A paired preview contains both banks, so address it through Dynamic."""
    if not banc.endswith(("_Static.bcett.byml.zs", "_Static.bcett.byml")):
        return banc
    if known is None:
        known = {scene["path"] for scene in dump.list_scenes()}
    other = sibling_bancs(banc, known)
    return other[0] if other else banc


def preview_path(dump, banc: str) -> Path:
    key = hashlib.sha256(f"{dump.root}\0{preview_bank(dump, banc)}".encode()).hexdigest()
    return cache_dir() / PREVIEW_VERSION / f"{key}.glb"


_rolling_rock_map = rolling_rock_map


def corrected_terrain_path(dump, banc: str, digest: str) -> Path | None:
    if not digest:
        return None
    scene = re.sub(r"_(?:Static|Dynamic)(?=\.bcett\.byml(?:\.zs)?$)", "", banc)
    key = hashlib.sha256(f"{dump.root}\0{scene}\0{digest}".encode()).hexdigest()
    version = TERRAIN_FIELD_VERSION if scene.startswith("Banc/HyruleField/HyruleField_") else TERRAIN_VERSION
    return cache_dir() / version / f"{key}.glb"


def terrain_fit_pending(dump, banc: str, terrain: dict) -> bool:
    if terrain.get("status") != "mesh" or not (terrain.get("terrain") or {}).get("heightScale"):
        return False
    path = corrected_terrain_path(dump, banc, terrain.get("digest") or "")
    return bool(path and not path.is_file() and not path.with_suffix(".none").is_file())


_VISUAL_SURFACE = re.compile(r"(?:ground|floor|road|path|terrain|rockarea)(?:_|$)", re.IGNORECASE)
_VISUAL_SCENERY = re.compile(
    r"detail|scenery|interior|inside|underground|house|roof|bridge|foliage|tree|water",
    re.IGNORECASE,
)


def _terrain_actor(name: str) -> bool:
    """Floors that are safe to bake into the visible heightmap.

    Islands, cliffs, soils, and hills are sampled at snap time through
    ``world_surface`` instead. Baking them into Hyrule Field subdivided the
    mesh from about 50k to 335k triangles and ran the browser out of memory.
    """
    return bool(name) and name.startswith("Land_") and bool(_VISUAL_SURFACE.search(name)) and not _VISUAL_SCENERY.search(name)



def _scene_actors(dump, banc, known):
    for source in sorted([banc, *sibling_bancs(banc, known)]):
        document, _, _ = dump.read_byml(source)
        for actor in scene_view(document, {"banc": source})["actors"]:
            if actor.get("gyaml") and (not actor.get("region") or actor["gyaml"] in (
                "StartPos", "FieldAndDungeonEntranceEventRequestActor",
            )):
                yield actor


_ENTRANCE_MATCH = 40.0
_ENTRANCE_PAD = 15.0


def _entrance_regions(actors: list[dict]) -> tuple[list[list[float]], list[list[float]]]:
    """Pair a named exit trigger with a nearby start, and keep unpaired triggers.

    The box is the union of 15 m pads around the start and every same-name
    trigger within 40 m. A 20 m limit missed Hyrule Field's Market gate, where
    the start sits about 21.5 m from the trigger. Unpaired triggers still get
    a pad so fine height can be restored when no start is in range.
    """
    triggers: dict[str, list[list[float]]] = {}
    for actor in actors:
        if actor.get("gyaml") == "FieldAndDungeonEntranceEventRequestActor" and actor.get("name"):
            triggers.setdefault(actor["name"], []).append(actor["translate"])
    starts = []
    boxes = []
    used = set()
    for actor in actors:
        if actor.get("gyaml") != "StartPos" or actor.get("name") not in triggers:
            continue
        x, _, z = actor["translate"]
        near = []
        for trigger in triggers[actor["name"]]:
            if (x - trigger[0]) ** 2 + (z - trigger[2]) ** 2 <= _ENTRANCE_MATCH ** 2:
                near.append(trigger)
                used.add((actor["name"], trigger[0], trigger[2]))
        if not near:
            continue
        starts.append(actor["translate"])
        xs = [x] + [item[0] for item in near]
        zs = [z] + [item[2] for item in near]
        boxes.append([min(xs) - _ENTRANCE_PAD, min(zs) - _ENTRANCE_PAD,
                      max(xs) + _ENTRANCE_PAD, max(zs) + _ENTRANCE_PAD])
    for name, items in triggers.items():
        for trigger in items:
            if (name, trigger[0], trigger[2]) in used:
                continue
            x, _, z = trigger
            boxes.append([x - _ENTRANCE_PAD, z - _ENTRANCE_PAD, x + _ENTRANCE_PAD, z + _ENTRANCE_PAD])
    return starts, boxes


def _entrance_starts(actors: list[dict]) -> list[list[float]]:
    """Starts that still have a named trigger within the entrance match radius."""
    starts, _boxes = _entrance_regions(actors)
    return starts


def _entrance_collision(dump, name: str) -> list:
    return [list(item) for item in physical_triangles(dump, name)]


def prepare_scene_terrain(dump, banc: str, terrain: dict, *, actors=None, prepare_missing: bool = True) -> Path | None:
    if not terrain_fit_pending(dump, banc, terrain):
        return None
    path = corrected_terrain_path(dump, banc, terrain["digest"])
    base = _cached_glb(terrain["digest"])
    if base is None:
        return None
    if actors is None:
        actors = _scene_actors(dump, banc, {scene["path"] for scene in dump.list_scenes()})
    actors = list(actors)
    pond = next((actor for actor in actors if actor["gyaml"] == FISHING_POND_WATER), None)
    starts, entrances = _entrance_regions(actors)
    collision = {}
    floors = []
    for actor in actors:
        name = actor["gyaml"]
        verified = _rolling_rock_map(banc) and name.startswith(ROLLING_ROCK_LAND)
        entrance_indices = []
        if entrances and re.fullmatch(r"Land_[A-Za-z0-9_]+", name):
            x, _, z = actor["translate"]
            entrance_indices = [i for i, (left, top, right, bottom) in enumerate(entrances)
                                if left <= x <= right and top <= z <= bottom]
            if entrance_indices and name not in collision:
                collision[name] = _entrance_collision(dump, name)
            if entrance_indices and not collision[name]:
                entrance_indices = []
        terrain_surface = _terrain_actor(name)
        if verified or entrance_indices or terrain_surface:
            floors.append({**actor, "verifiedTerrain": verified, "terrainSurface": terrain_surface,
                           "entranceIndices": entrance_indices})

    models = {}
    missing = False
    names = dict.fromkeys(actor["gyaml"] for actor in floors)
    if pond:
        names[FISHING_POND_WATER] = None
    for name in names:
        try:
            info = describe_actor(dump, name, convert=prepare_missing)
        except CacheMiss:
            missing = True
            continue
        model = _cached_glb(info.get("digest") or "")
        if model:
            models[name] = str(model.resolve())
    if missing:
        return None
    verified = [actor for actor in floors if actor["verifiedTerrain"] and actor["gyaml"] in models]
    detail_bounds = list(entrances)
    if verified:
        positions = [actor["translate"] for actor in verified]
        detail_bounds.append([min(p[0] for p in positions) - 15, min(p[2] for p in positions) - 15,
                              max(p[0] for p in positions) + 15, max(p[2] for p in positions) + 15])
    data = _run_worker({
        "terrain": str(base.resolve()), "cutout": models, "actors": floors,
        "heightTiles": _authored_height_tiles(dump, terrain, detail_bounds) if detail_bounds else [],
        "heightScale": terrain["terrain"]["heightScale"],
        "entrances": entrances,
        "entranceStarts": starts,
        "collision": {name: triangles for name, triangles in collision.items() if triangles},
        "pond": pond if FISHING_POND_WATER in models else None,
    }) if models or entrances else b""
    if data:
        _write_prepared(path, data)
        return path
    _write_prepared(path.with_suffix(".none"), b"")
    return None


def _authored_height_tiles(dump, terrain: dict, detail_bounds: list[list[float]]) -> list[dict]:
    """Original height samples overlapping local refinement areas, finest first."""
    layout = terrain.get("terrain") or {}
    if not layout.get("scale") or not layout.get("heightScale"):
        return []
    tiles = []
    for row in sorted(terrain.get("tiles") or [], key=lambda row: row["edge"]):
        if row["layer"] != "main" or row["status"] != "drawn" or not row.get("path"):
            continue
        bounds = heightmap._world_box(*row["center"], row["edge"], layout["scale"], layout["origin"])
        x0, z0, x1, z1 = bounds
        if not any(x1 >= left and x0 <= right and z1 >= top and z0 <= bottom
                   for left, top, right, bottom in detail_bounds):
            continue
        try:
            blob, _ = dump.read_file(row["path"])
            samples, _, _ = heightmap.read_height_file(heightmap.unpack_hght(blob, dump.dicts()))
        except (BancError, heightmap.TerrainError, OSError):
            continue
        heights = array.array("f", samples)
        if sys.byteorder != "little":
            heights.byteswap()
        tiles.append({"bounds": bounds, "step": row["step"], "grid": base64.b64encode(heights).decode("ascii")})
    return tiles


def _model_priority(name: str, count: int) -> tuple[int, int, str]:
    tokens = name.lower().split("_")
    if ("house" in tokens or "building" in tokens) and any(
        token in tokens for token in ("outside", "exterior")
    ):
        group = 0
    elif name.startswith(ROLLING_ROCK_LAND) and name.rsplit("_", 1)[-1] in {"01", "02", "03", "08", "09", "10"}:
        group = 1
    elif name.startswith("Land_") and any(token in {"bridge", "drawbridge"} or token.endswith("bridge") for token in tokens):
        group = 1
    elif name.startswith("Land_") and not any(token in tokens for token in ("inside", "interior")):
        group = 2
    else:
        group = 3
    return group, -count, name


def build_scene_preview(dump, banc: str, limit: int = 0, *, prepare_missing: bool = False,
                        known: set[str] | None = None, models: dict | None = None) -> tuple[Path, int]:
    """Pack terrain and all drawable actor types, unless an explicit limit is set."""
    if known is None:
        known = {scene["path"] for scene in dump.list_scenes()}
    if banc not in known:
        raise BancError("Choose a scene from the dump to build a preview.", "not_found")
    banc = preview_bank(dump, banc, known)
    counts: dict[str, int] = {}
    actors = list(_scene_actors(dump, banc, known))
    for actor in actors:
        if actor.get("region"):
            continue
        name = actor["gyaml"]
        counts[name] = counts.get(name, 0) + 1

    sources: dict[str, Path] = {}
    try:
        terrain = describe_terrain(dump, banc, convert=prepare_missing)
    except CacheMiss:
        terrain = {}
    path = _cached_glb(terrain.get("digest") or "")
    if path:
        prepare_scene_terrain(dump, banc, terrain, actors=actors, prepare_missing=prepare_missing)
        corrected = corrected_terrain_path(dump, banc, terrain["digest"])
        sources[TERRAIN] = corrected if corrected and corrected.is_file() else path
    for name in sorted(counts, key=lambda name: _model_priority(name, counts[name])):
        if limit and len(sources) - (TERRAIN in sources) >= limit:
            break
        model = (models or {}).get(name)
        if model is None:
            try:
                model = describe_actor(dump, name, convert=prepare_missing)
            except CacheMiss:
                continue
        path = _cached_glb(model.get("digest") or "")
        if path:
            sources[name] = path
    if not sources:
        raise BancError("No drawable cached geometry for this scene preview.", "empty_preview")

    manifest = [{"name": name, "path": str(path.resolve())} for name, path in sources.items()]
    output = preview_path(dump, banc)
    stamp = output.with_suffix(".stamp")
    key = hashlib.sha256(json.dumps([(row["name"], row["path"], sources[row["name"]].stat().st_mtime_ns)
                                    for row in manifest]).encode())
    for filename in (WORKER, WORKER.with_name("model-preview.mjs")):
        key.update(filename.read_bytes())
    fingerprint = key.hexdigest().encode("ascii")
    if not (output.is_file() and stamp.is_file() and stamp.read_bytes() == fingerprint):
        _write_prepared(output, _run_worker(manifest))
        _write_prepared(stamp, fingerprint)
    return output, len(sources) - (TERRAIN in sources)


def build_all_scene_previews(dump, limit: int, *, prepare_missing: bool, share_textures: bool, log=print) -> dict:
    """Prepare each logical scene and each distinct actor model once per run."""
    from ocarina_forge.shared_models import prepare_shared_model
    from ocarina_forge.warm import scene_model_names

    banks = [scene["path"] for scene in dump.list_scenes()]
    known = set(banks)
    scenes = list(dict.fromkeys(preview_bank(dump, bank, known) for bank in banks))
    models = {}
    shared = set()
    images = {}
    result = {"ready": 0, "skipped": 0, "failed": 0}
    for index, banc in enumerate(scenes, start=1):
        log(f"Forge scene {index}/{len(scenes)} {banc}")
        try:
            for bank in (banc, *sibling_bancs(banc, known)):
                for name in scene_model_names(dump, bank):
                    if name in models:
                        continue
                    try:
                        info = describe_actor(dump, name, convert=prepare_missing)
                    except CacheMiss:
                        models[name] = {}
                        continue
                    models[name] = {"digest": info.get("digest")}
                    path = _cached_glb(info.get("digest") or "")
                    if share_textures and path and path.stem not in shared:
                        try:
                            prepare_shared_model(path, images)
                        except (OSError, ValueError) as exc:
                            log(f"  Shared textures for {name} could not be prepared. {exc}")
                        shared.add(path.stem)
            output, count = build_scene_preview(dump, banc, limit, prepare_missing=prepare_missing,
                                                known=known, models=models)
            result["ready"] += 1
            log(f"  Preview ready with {count} model types, {output.stat().st_size:,} bytes")
        except (BancError, OSError, ValueError) as exc:
            key = "skipped" if isinstance(exc, BancError) and exc.code == "empty_preview" else "failed"
            result[key] += 1
            log(f"  Preview {key}. {exc}")
    return {**result, "models": len(models), "shared": len(shared)}


def _run_worker(manifest) -> bytes:
    try:
        result = subprocess.run(
            ["node", str(WORKER)], input=json.dumps(manifest).encode(),
            capture_output=True, check=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        message = getattr(exc, "stderr", None)
        detail = message.decode(errors="replace").splitlines() if message else []
        reason = next((line for line in detail if line.startswith("Error: ")), str(exc))
        raise BancError(f"Could not build the scene preview: {reason}", "preview") from exc
    return result.stdout


def _write_prepared(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temp = Path(output.name)
    try:
        temp.write_bytes(data)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    from ocarina_forge.cache_prune import note_written

    note_written(destination)


def prepare_scene_shared_models(dump, banc: str) -> int:
    """Prepare the already converted models on this map without warming unrelated assets."""
    from ocarina_forge.shared_models import prepare_shared_model
    from ocarina_forge.warm import scene_warm_queue

    seen = set()
    prepared = 0
    images = {}
    for name in scene_warm_queue(dump, banc):
        try:
            model = describe_actor(dump, name, convert=False)
        except CacheMiss:
            continue
        digest = model.get("digest") or ""
        if not digest or digest in seen:
            continue
        seen.add(digest)
        path = _cached_glb(digest)
        if path and prepare_shared_model(path, images):
            prepared += 1
    return prepared

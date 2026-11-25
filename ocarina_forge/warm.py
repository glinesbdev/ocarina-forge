"""Convert a scene's models before the view asks, nearest first, then the neighbors.

The editor sends the names it is about to draw, already sorted nearest to the
camera. Anything in that scene the client did not list is appended, and then
the other scenes in the same folder, then the rest of the dump. Interactive
converts are not started here. The server waits those out before it takes the
next name.
"""

from __future__ import annotations

from ocarina_forge.scene import scene_view, sibling_bancs


def model_names(view: dict) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for actor in view.get("actors") or []:
        name = str(actor.get("gyaml") or actor.get("Gyaml") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def scene_model_names(dump, banc: str) -> list[str]:
    """Actor classes in one scene. A file with no Actors array contributes none.

    Static setup files are in the scene list and are not placement files.
    One of those must not abort the walk.
    """
    if not banc:
        return []
    from ocarina_forge.errors import BancError

    try:
        document, _dict_id, _raw = dump.read_byml(banc)
        return model_names(scene_view(document, {"banc": banc}))
    except BancError:
        return []


def neighbor_bancs(banc: str, scenes: list[str]) -> list[str]:
    """Same folder first, then every other scene. The open scene is skipped."""
    folder = banc.rsplit("/", 1)[0] if "/" in banc else ""
    same: list[str] = []
    rest: list[str] = []
    for path in scenes:
        if not path or path == banc:
            continue
        if folder and (path.startswith(folder + "/") or path == folder):
            same.append(path)
        else:
            rest.append(path)
    return same + rest


def warm_queue(nearest: list[str], scene_names: list[str], neighbors: list[str]) -> list[tuple[str, str]]:
    """Models in the order to convert, then scene markers for the neighbors."""
    ordered: list[str] = []
    seen: set[str] = set()
    for name in list(nearest) + list(scene_names):
        if not name or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    queue: list[tuple[str, str]] = [("model", name) for name in ordered]
    queue.extend(("scene", path) for path in neighbors if path)
    return queue


def queue_warm(dump, target: str, nearest: list[str] | None = None, *, neighbors: bool = True) -> list[tuple[str, str]]:
    """Target models first. Neighbor scenes stay as markers until those finish."""
    scenes = [item["path"] for item in dump.list_scenes()]
    if target in {"all", "--all"}:
        return warm_queue([], [], scenes)
    if target and target not in scenes:
        raise SystemExit(f"No scene {target}. Pass a banc path or --all.")
    extra = neighbor_bancs(target, scenes) if neighbors and target else []
    return warm_queue(list(nearest or []), scene_model_names(dump, target) if target else [], extra)


def scene_warm_queue(dump, target: str, names: list[str] | None = None) -> list[str]:
    """Return a fixed, deduplicated model list for one scene and its Static/Dynamic sibling."""
    scenes = [item["path"] for item in dump.list_scenes()]
    if target not in scenes:
        raise ValueError(f"No scene {target}.")
    scene_names = scene_model_names(dump, target)
    companion_names = [
        name
        for path in sibling_bancs(target, set(scenes))
        for name in scene_model_names(dump, path)
    ]
    ordered = warm_queue(
        list(names or []),
        scene_names + companion_names,
        [],
    )
    return [name for kind, name in ordered if kind == "model"]


def run_warm(dump, target: str, *, log=print, neighbors: bool = True) -> dict:
    """Convert now, in this process. ``target`` is a banc path or ``all``.

    The target's models are converted before any neighbor file is parsed.
    ``neighbors=False`` is the ``--scene-only`` timing walk.
    """
    import sys
    import time

    from ocarina_forge.models import describe_actor

    queue = queue_warm(dump, target, neighbors=neighbors)
    seen = {name for kind, name in queue if kind == "model"}
    started = time.perf_counter()
    misses = 0
    hits = 0
    convert_ms = 0.0
    texture_ms = 0.0
    count = 0
    while queue:
        kind, name = queue.pop(0)
        if kind == "scene":
            fresh = []
            for item in scene_model_names(dump, name):
                if item in seen:
                    continue
                seen.add(item)
                fresh.append(("model", item))
            queue[0:0] = fresh
            continue
        info = describe_actor(dump, name)
        timing = info.get("timing") or {}
        cache = timing.get("cache") or info.get("status")
        if cache == "hit":
            hits += 1
        elif info.get("status") == "mesh":
            misses += 1
            convert_ms += float(timing.get("convertMs") or 0)
            texture_ms += float(timing.get("textureMs") or 0)
        count += 1
        log(f"{name} cache={cache} convert={timing.get('convertMs', 0)}ms texture={timing.get('textureMs', 0)}ms")
        sys.stdout.flush()
    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    summary = {
        "models": count,
        "misses": misses,
        "hits": hits,
        "convertMs": round(convert_ms, 1),
        "textureMs": round(texture_ms, 1),
        "wallMs": wall_ms,
    }
    log(
        f"warm done {summary['models']} models, {summary['misses']} converted, "
        f"{summary['hits']} cached, conv {summary['convertMs']} ms, tex {summary['textureMs']} ms, "
        f"wall {summary['wallMs']} ms"
    )
    sys.stdout.flush()
    return summary

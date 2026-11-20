"""Convert a scene's models before the view asks, nearest first, then the neighbors.

The editor sends the names it is about to draw, already sorted nearest to the
camera. Anything in that scene the client did not list is appended, and then
the other scenes in the same folder, then the rest of the dump. Interactive
converts are not started here. The server waits those out before it takes the
next name.
"""

from __future__ import annotations

from ocarina_forge.scene import scene_view


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
    if not banc:
        return []
    document, _dict_id, _raw = dump.read_byml(banc)
    return model_names(scene_view(document, {}))


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


def run_warm(dump, target: str, *, log=print) -> dict:
    """Convert now, in this process. ``target`` is a banc path or ``all``."""
    from ocarina_forge.models import describe_actor

    scenes = [item["path"] for item in dump.list_scenes()]
    if target in {"all", "--all"}:
        bancs = scenes
    else:
        if target not in scenes:
            raise SystemExit(f"No scene {target}. Pass a banc path or --all.")
        bancs = [target, *neighbor_bancs(target, scenes)]
    names: list[str] = []
    seen: set[str] = set()
    groups = [scene_model_names(dump, banc) for banc in bancs]
    for group in groups:
        for name in group:
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
    misses = 0
    hits = 0
    convert_ms = 0.0
    texture_ms = 0.0
    for index, name in enumerate(names, start=1):
        info = describe_actor(dump, name)
        timing = info.get("timing") or {}
        cache = timing.get("cache") or info.get("status")
        if cache == "hit":
            hits += 1
        elif info.get("status") == "mesh":
            misses += 1
            convert_ms += float(timing.get("convertMs") or 0)
            texture_ms += float(timing.get("textureMs") or 0)
        log(
            f"{index}/{len(names)} {name} {info.get('status')} cache={cache} "
            f"convert={timing.get('convertMs', 0)}ms texture={timing.get('textureMs', 0)}ms"
        )
    summary = {
        "models": len(names),
        "misses": misses,
        "hits": hits,
        "convertMs": round(convert_ms, 1),
        "textureMs": round(texture_ms, 1),
    }
    log(
        f"warm done {summary['models']} models, {summary['misses']} converted, "
        f"{summary['hits']} cached, conv {summary['convertMs']} ms, tex {summary['textureMs']} ms"
    )
    return summary

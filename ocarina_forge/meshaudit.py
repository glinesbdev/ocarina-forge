"""Check which scene models actually made it through the mesh converter."""

from collections import Counter, defaultdict
from pathlib import Path
import re
import struct

from ocarina_forge.actors import is_marker
from ocarina_forge.errors import BancError
from ocarina_forge.formats.gltf import glb_issues, split_glb
from ocarina_forge.models import CacheMiss, _cached_glb, describe_actor
from ocarina_forge.scene import scene_view, sibling_bancs
from ocarina_forge.shared_models import shared_model_path


_UNDECODED = re.compile(r"FSHP flag 0x[\da-f]+", re.IGNORECASE)
_LABELS = {
    "ready": "validated", "no_mesh": "no mesh expected", "uncached": "uncached",
    "missing": "missing file", "unsupported": "unsupported geometry", "partial": "partially decoded",
    "invalid": "invalid glTF", "failed": "conversion failed",
}


def _check_glb(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
        failures = glb_issues(data)
        if failures:
            return failures
        document, _ = split_glb(data)
        accessors = document.get("accessors", [])
        nodes = document.get("nodes", [])
        meshes = document.get("meshes", [])
        scenes = document.get("scenes", [])
        active = list(scenes[document.get("scene", 0)].get("nodes", [])) if scenes else []
        visited, visible = set(), set()
        while active:
            index = active.pop()
            if index in visited:
                continue
            visited.add(index)
            node = nodes[index]
            active.extend(node.get("children", []))
            if "mesh" in node:
                visible.add(node["mesh"])
        has_triangles = False
        for index in sorted(visible):
            for part, primitive in enumerate(meshes[index].get("primitives", [])):
                mode = primitive.get("mode", 4)
                if mode not in (4, 5, 6):
                    continue
                position = primitive.get("attributes", {}).get("POSITION")
                if position is None or accessors[position].get("type") != "VEC3":
                    failures.append(f"Mesh {index} primitive {part} has no 3D positions.")
                    continue
                count = accessors[primitive.get("indices", position)]["count"]
                if count < 3 or (mode == 4 and count % 3):
                    failures.append(f"Mesh {index} primitive {part} has an invalid triangle count.")
                else:
                    has_triangles = True
        if failures:
            return failures
        if not has_triangles:
            return ["The glTF contains no drawable triangles."]
        return []
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, struct.error) as exc:
        return [f"Could not read the glTF: {exc}"]


def _inspect_model(dump, name: str, prepare_missing: bool, checked: dict[Path, list[str]]) -> dict:
    result = {"gyaml": name, "source": "", "triangles": 0}
    try:
        meta = describe_actor(dump, name, convert=prepare_missing)
    except CacheMiss:
        return {**result, "status": "uncached", "reason": "This model has not been converted yet."}
    reason = meta.get("reason") or ""
    result.update(source=meta.get("source") or "", triangles=meta.get("triangles") or 0)
    incomplete = _UNDECODED.search(reason)
    if meta.get("status") != "mesh":
        if incomplete:
            status = "unsupported"
        elif reason == "No mesh.":
            status = "no_mesh"
        elif reason.startswith("No model for "):
            status = "missing"
        else:
            status = "failed"
        return {**result, "status": status, "reason": reason}
    digest = meta.get("digest") or ""
    path = _cached_glb(digest)
    if path is None:
        return {**result, "status": "invalid", "reason": "The converted glTF is missing from the cache."}
    shared = shared_model_path(digest)
    for artifact in [path, shared] if shared.is_file() else [path]:
        if artifact not in checked:
            checked[artifact] = _check_glb(artifact)
        if checked[artifact]:
            detail = "; ".join(checked[artifact][:3])
            return {**result, "status": "invalid", "reason": detail}
    if incomplete:
        return {**result, "status": "partial", "reason": reason}
    return {**result, "status": "ready", "reason": ""}


def audit_scene_meshes(dump, banc: str, *, models=(), prepare_missing=False, view=None) -> dict:
    """Group actor types across a scene and its age-matched companion bank."""
    known = {scene["path"] for scene in dump.list_scenes()}
    if banc not in known:
        raise BancError("Choose a scene from the dump to audit its meshes.", "not_found")
    selected = set(models)
    actors = defaultdict(list)
    for bank in [banc, *sibling_bancs(banc, known)]:
        if view is None:
            document, _, _ = dump.read_byml(bank)
            placed = scene_view(document, {"banc": bank})["actors"]
        else:
            placed = view(bank)["actors"]
        for actor in placed:
            name = actor.get("gyaml") or ""
            if not name or actor.get("region") or is_marker(name) or (selected and name not in selected):
                continue
            actors[name].append({"id": actor["id"], "banc": bank, "name": actor.get("name") or name})
    checked: dict[Path, list[str]] = {}
    rows = [dict(_inspect_model(dump, name, prepare_missing, checked), actors=actors[name]) for name in sorted(actors)]
    return {"banc": banc, "counts": dict(Counter(row["status"] for row in rows)), "models": rows}


def render_text(report: dict) -> str:
    lines = [f"Mesh audit for {report['banc']}"]
    lines.extend(f"{count} {_LABELS[status]}" for status, count in sorted(report["counts"].items()))
    for row in report["models"]:
        if row["status"] in {"ready", "no_mesh"}:
            continue
        count = len(row["actors"])
        placements = "placement" if count == 1 else "placements"
        lines.append(f"{row['gyaml']} ({count} {placements}): {_LABELS[row['status']]}")
        lines.append(f"  {row['reason']}")
        if row["source"]:
            lines.append(f"  {row['source']}")
    return "\n".join(lines)

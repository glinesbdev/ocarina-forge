"""Actors an After the Ocarina build would add, shown before the build runs.

Scene YAML that is not an actor diff can still place something. Display
stones name a gyaml, a banc, and a translate. The viewport draws those as
read-only ghosts. The banc file is unchanged until the project is built.
"""

from __future__ import annotations

from bancwright.actors import category, is_volume
from bancwright.project import load_yaml
from bancwright.values import tag


def recipe_actors(project, banc: str) -> list[dict]:
    """Placements in this project that the build adds to ``banc``."""
    folder = project.root / "scenes"
    if not folder.is_dir() or not banc:
        return []
    found: list[dict] = []
    for path in sorted(folder.rglob("*.yaml")):
        data = load_yaml(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("actors"), dict) and data.get("banc"):
            continue
        relative = path.relative_to(project.root).as_posix()
        for index, row in enumerate(_rows(data)):
            placed = _placement(data, row, banc, relative, index)
            if placed:
                found.append(placed)
    return found


def _rows(data: dict):
    stones = data.get("stones")
    if isinstance(stones, list):
        for row in stones:
            if isinstance(row, dict):
                yield row
    for key, value in data.items():
        if key in ("stones", "actors"):
            continue
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict) and "gyaml" in row and "translate" in row:
                    yield row
    if data.get("place") is not False and "gyaml" in data and "translate" in data:
        yield data


def _placement(data: dict, row: dict, banc: str, relative: str, index: int) -> dict | None:
    target = row.get("banc") or data.get("banc")
    if target != banc:
        return None
    gyaml = row.get("gyaml")
    if not isinstance(gyaml, str) or not gyaml.strip():
        return None
    translate = _xyz(row.get("translate"))
    if translate is None:
        return None
    offset = row.get("y_offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        offset = 0
    translate = [translate[0], translate[1] + float(offset), translate[2]]
    rotate = _xyz(row.get("rotate")) or [0.0, 0.0, 0.0]
    scale = _xyz(row.get("scale")) or [1.0, 1.0, 1.0]
    flag = row.get("flag") if isinstance(row.get("flag"), str) else data.get("flag")
    flag = flag.strip() if isinstance(flag, str) else ""
    name = row.get("name") if isinstance(row.get("name"), str) and row.get("name").strip() else None
    note = "added by build, flag-gated" if flag else "added by build"
    return {
        "id": f"recipe:{relative}:{index}",
        "gyaml": gyaml.strip(),
        "name": name,
        "category": category(gyaml.strip()),
        "translate": translate,
        "rotate": rotate,
        "scale": scale,
        "volume": is_volume(gyaml.strip(), scale),
        "srtHash": None,
        "status": "recipe",
        "params": tag({}),
        "recipe": {"file": relative, "flag": flag, "note": note},
    }


def _xyz(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    numbers = []
    for item in value[:3]:
        if isinstance(item, bool):
            return None
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            return None
    return numbers

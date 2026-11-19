#!/usr/bin/env python3
"""Report how the models in one scene resolve against a dump.

Usage, from the repo root:

    .venv/bin/python scripts/check_models.py <dump> [banc]

<dump> is the Longshot folder that contains romfs/, or romfs/ itself.
With no banc, Kokiri Forest child static is used when that file exists,
otherwise the first scene in the dump.

The script prints one block you can paste back: a summary, every actor
that stayed a box, then every mesh with its source path, triangle count
and texture count. The same conversion the editor uses, including Tex/.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bancwright.dump import Dump  # noqa: E402
from bancwright.errors import BancError  # noqa: E402
from bancwright.models import describe_actor, describe_terrain  # noqa: E402

KOKIRI = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 2 if len(argv) < 2 else 0
    try:
        dump = Dump(Path(argv[1]))
    except BancError as exc:
        print(exc.message)
        return 1
    banc = argv[2] if len(argv) > 2 else _default_banc(dump)
    if not banc:
        print("This dump has no .bcett.byml.zs scenes.")
        return 1
    try:
        document, _dict_id, _raw = dump.read_byml(banc)
    except BancError as exc:
        print(exc.message)
        return 1
    actors = document.get("Actors") if isinstance(document, dict) else None
    if not isinstance(actors, list):
        print(f"{banc} has no Actors list.")
        return 1

    by_gyaml: dict[str, int] = defaultdict(int)
    for actor in actors:
        if isinstance(actor, dict) and isinstance(actor.get("Gyaml"), str):
            by_gyaml[actor["Gyaml"]] += 1

    rows = []
    for gyaml in sorted(by_gyaml):
        info = describe_actor(dump, gyaml)
        rows.append((gyaml, by_gyaml[gyaml], info))
        print(f"  {gyaml} ({by_gyaml[gyaml]})", file=sys.stderr, flush=True)

    terrain = describe_terrain(dump, banc)
    mesh = [row for row in rows if row[2]["status"] == "mesh"]
    boxes = [row for row in rows if row[2]["status"] != "mesh"]
    textured = [row for row in mesh if row[2].get("textures")]
    print(banc)
    print(
        f"instances {sum(by_gyaml.values())}  unique {len(by_gyaml)}  "
        f"mesh {len(mesh)}  textured {len(textured)}  "
        f"untextured {len(mesh) - len(textured)}  box {len(boxes)}"
    )
    _print_terrain(terrain)
    print()
    print("boxes:")
    if not boxes:
        print("  none")
    for gyaml, count, info in boxes:
        print(f"  {gyaml} x{count}  {info.get('reason') or 'box'}")
    print()
    print("meshes:")
    if not mesh:
        print("  none")
    for gyaml, count, info in mesh:
        textures = info.get("textures") or 0
        texture = "1 texture" if textures == 1 else f"{textures} textures"
        print(f"  {gyaml} x{count}  {info.get('source') or ''}  {info.get('triangles') or 0} tris  {texture}")
        if info.get("reason"):
            print(f"    {info['reason']}")
        _print_textures(info.get("textureLog") or [])
    print()
    _print_textures(terrain.get("textureLog") or [], "terrain")
    missed = _missed_names(rows, terrain)
    if missed:
        print()
        _print_tex_sample(dump, missed)
    return 0


def _default_banc(dump: Dump) -> str:
    scenes = dump.list_scenes()
    for scene in scenes:
        if scene["path"] == KOKIRI:
            return KOKIRI
    return scenes[0]["path"] if scenes else ""


def _print_textures(log: list, label: str = "") -> None:
    prefix = f"{label} " if label else "    "
    for entry in log:
        wanted = entry.get("wanted") or "(no name)"
        tried = ", ".join(entry.get("tried") or []) or "nothing"
        found = entry.get("found") or "not found"
        if entry.get("decoded"):
            detail = f"{entry.get('format') or '?'} {entry.get('size') or ''} {entry.get('swizzle') or ''}".strip()
            image = entry.get("image") or ""
            extra = f" image {image}" if image and image != wanted else ""
            count = entry.get("images") or 0
            bundle = f" ({count} in the bntx)" if count > 1 else ""
            print(f"{prefix}texture {wanted}")
            print(f"{prefix}  tried {tried}")
            print(f"{prefix}  found {found}{bundle}{extra}  decoded {detail}")
        else:
            print(f"{prefix}texture {wanted}")
            print(f"{prefix}  tried {tried}")
            print(f"{prefix}  found {found}  {entry.get('error') or 'not decoded'}")
        if entry.get("decoded") and entry.get("error"):
            print(f"{prefix}  note {entry['error']}")


def _missed_names(rows, terrain: dict) -> list[str]:
    names = []
    for _gyaml, _count, info in rows:
        for entry in info.get("textureLog") or []:
            if not entry.get("decoded") and entry.get("wanted"):
                names.append(entry["wanted"])
    for entry in terrain.get("textureLog") or []:
        if not entry.get("decoded") and entry.get("wanted"):
            names.append(entry["wanted"])
    if any(row[2].get("status") == "mesh" and not row[2].get("textures") and not (row[2].get("textureLog")) for row in rows):
        names.append(rows[0][0] if rows else "")
    return [name for name in dict.fromkeys(names) if name]


def _print_tex_sample(dump: Dump, needles: list[str]) -> None:
    folder = dump.romfs / "Tex"
    if not folder.is_dir():
        print("Tex/ is not in this dump.")
        return
    names = sorted(path.name for path in folder.iterdir() if path.is_file())
    print(f"Tex/ has {len(names)} files. Closest names:")
    shown: list[str] = []
    for needle in needles:
        low = needle.lower()
        for name in names:
            if low in name.lower() and name not in shown:
                shown.append(name)
            if len(shown) >= 16:
                break
        if len(shown) >= 16:
            break
    if not shown:
        shown = names[:12]
        print("  (no filename contained the texture name; first files in Tex/)")
    for name in shown:
        print(f"  Tex/{name}")


def _print_terrain(info: dict) -> None:
    status = info.get("status") or "none"
    if status == "mesh":
        textures = info.get("textures") or 0
        texture = "1 texture" if textures == 1 else f"{textures} textures"
        print(f"terrain mesh  {info.get('source') or ''}  {info.get('triangles') or 0} tris  {texture}")
    elif status == "none":
        print(f"terrain none  {info.get('reason') or ''}")
    else:
        print(f"terrain {status}  {info.get('source') or ''}  {info.get('reason') or ''}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except BancError as exc:
        print(exc.message)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)

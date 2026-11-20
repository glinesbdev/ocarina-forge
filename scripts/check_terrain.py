#!/usr/bin/env python3
"""Report which terrain sources a scene drew, and which it skipped.

Usage, from the repo root:

    .venv/bin/python scripts/check_terrain.py <dump> [banc]
    .venv/bin/python scripts/check_terrain.py <dump> [banc] --png out.png

`--png` writes a 1024² top-down picture of the mesh that was emitted, in
terrain-unit space (x to the right, z up, -1..1). Covered ground is bright.
A black pixel is a place the mesh builder did not draw. `zeros` on a tile
line is the count of exact 0 samples after the partial-column repair; on a
retail tile that count should be about 0.

<dump> is the Longshot folder that contains romfs/, or romfs/ itself.
With no banc, Kokiri Forest child static is used when that file exists,
otherwise the first scene in the dump.

The script uses the same conversion as the editor. It prints the TerrainArc
scene, the origin, and one line per tile (drawn or skipped, with the reason).
It then lists ground-like actors whose shapes are FSHP flag 0x10. Those
shapes are not the field: the payload is not an FVTX, so they stay boxes.
Actor LOD, the small-prop distance cull, and the InnerMesh / bake / fade /
water-volume filters do not apply to the heightmap.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bancwright.dump import Dump  # noqa: E402
from bancwright.errors import BancError  # noqa: E402
from bancwright.formats.terrain import write_coverage_png  # noqa: E402
from bancwright.models import cache_dir, describe_terrain, flag10_ground  # noqa: E402

KOKIRI = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 2 if len(argv) < 2 else 0
    png_path = ""
    positionals = []
    index = 1
    while index < len(argv):
        if argv[index] == "--png" and index + 1 < len(argv):
            png_path = argv[index + 1]
            index += 2
            continue
        positionals.append(argv[index])
        index += 1
    if not positionals:
        print(__doc__.strip())
        return 2
    try:
        dump = Dump(Path(positionals[0]))
    except BancError as exc:
        print(exc.message)
        return 1
    banc = positionals[1] if len(positionals) > 1 else _default_banc(dump)
    if not banc:
        print("This dump has no .bcett.byml.zs scenes.")
        return 1
    print(f"scene {banc}")
    try:
        info = describe_terrain(dump, banc)
    except BancError as exc:
        print(exc.message)
        return 1
    terrain = info.get("terrain") or {}
    origin = terrain.get("origin") or [0, 0, 0]
    print(f"status {info.get('status')}")
    print(f"source {info.get('source') or '(none)'}")
    if terrain.get("scene"):
        print(f"terrain scene {terrain['scene']}")
    if terrain.get("param"):
        print(f"param {terrain['param']}")
    if "scale" in terrain:
        print(
            f"origin {origin[0]} {origin[1]} {origin[2]}  "
            f"scale {terrain.get('scale')}  height scale {terrain.get('heightScale')}"
        )
    for extra in terrain.get("subs") or []:
        print(f"sub scene {extra}")
    clip = terrain.get("clip")
    if clip:
        print(f"actor clip x {clip[0]}..{clip[2]}  z {clip[1]}..{clip[3]}")
    print(f"reason {info.get('reason') or ''}")
    tiles = info.get("tiles") or []
    drawn = [row for row in tiles if row.get("status") == "drawn"]
    skipped = [row for row in tiles if row.get("status") != "drawn"]
    print(f"tiles {len(tiles)}  drawn {len(drawn)}  skipped {len(skipped)}  triangles {info.get('triangles') or 0}")
    for row in tiles:
        layers = ",".join(str(item) for item in row.get("layers") or []) or "-"
        where = row.get("path") or "(no file)"
        folder = row.get("directory") or "(no dir)"
        mate = row.get("mate") or "(no mate)"
        mate_note = row.get("mateNote") or "(mate not decoded)"
        histogram = row.get("mateHistogram") or "-"
        step = row.get("step") or "-"
        ao = row.get("ao") or ""
        print(
            f"  {row.get('status')} {row.get('name')}  "
            f"dir {folder}  centre {row.get('center')}  edge {row.get('edge')}  "
            f"triangles {row.get('triangles') or 0}  step {step}  layers {layers}  "
            f"holes {row.get('holes') or 0}  zeros {row.get('zeros') or 0}  dropped {row.get('dropped') or 0}  "
            f"height {row.get('heightMin')}..{row.get('heightMax')}  "
            f"hght {where}  mate {mate}  hist {histogram}  {mate_note}  {ao}  {row.get('reason') or ''}"
        )
    if not tiles and info.get("status") == "mesh" and "terrain proxy" in (info.get("reason") or ""):
        print("no TerrainArc for this scene. The static-map bfres is the untextured stand-in and is not drawn.")
    elif not tiles and info.get("status") != "mesh":
        print("no heightmap tiles were decoded. The green stand-in is not used when a TSCB was named and failed.")
    print("flag 0x10 ground-like shapes (not the field; the payload is not an FVTX):")
    flagged = flag10_ground(dump, banc)
    if not flagged:
        print("  none")
    for row in flagged:
        shapes = ", ".join(row.get("shapes") or []) or "(unnamed)"
        print(f"  skipped {row.get('gyaml')}  {row.get('source') or ''}  {shapes}  {row.get('reason') or ''}")
    print(
        "filters: actor LOD, the small-prop distance cull, and InnerMesh / bake / fade / "
        "water-volume omission do not drop the heightmap. Path strips and moss mounds that "
        "already draw are actor models, not the field. .custom.extm is not decoded. "
        "A .mate line is the material grid; the directory is the folder the .hght came from. "
        "holes is how many samples were non-finite (0 is ground, not a hole). "
        "zeros is how many samples are exact 0 after the partial-column repair; a retail tile should be near 0. "
        "dropped is how many coarse cells were removed because a finer tile emitted a quad there."
    )
    if png_path:
        _write_png(info, png_path)
    return 0


def _write_png(info: dict, png_path: str) -> None:
    digest = info.get("digest") or ""
    if info.get("status") != "mesh" or not digest:
        print("png skipped: this scene did not produce a terrain mesh")
        return
    glb_path = cache_dir() / digest[:2] / f"{digest}.glb"
    if not glb_path.is_file():
        print(f"png skipped: {glb_path} is not in the cache")
        return
    positions, indices = _glb_triangles(glb_path.read_bytes())
    terrain = info.get("terrain") or {}
    origin = terrain.get("origin") or [0.0, 0.0, 0.0]
    scale = float(terrain.get("scale") or 1.0)
    stats = write_coverage_png(
        png_path,
        positions,
        indices,
        (float(origin[0]), float(origin[1]), float(origin[2])),
        scale,
    )
    print(
        f"png {png_path}  covered {stats['covered']}/{stats['pixels']} px  "
        f"unit x,z -1..1  height {stats['heightMin']}..{stats['heightMax']}"
    )


def _glb_triangles(glb: bytes):
    json_len = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + json_len])
    blob = glb[20 + json_len + 8 :]
    positions = []
    indices = []
    for mesh in document.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            attrs = primitive.get("attributes") or {}
            if "POSITION" not in attrs or "indices" not in primitive:
                continue
            base = len(positions)
            positions.extend(_accessor(document, blob, attrs["POSITION"], 3, "f"))
            raw = _accessor(document, blob, primitive["indices"], 1, "index")
            indices.extend(base + int(item[0]) for item in raw)
    return positions, indices


def _accessor(document, blob: bytes, index: int, width: int, kind: str):
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    start = (view.get("byteOffset") or 0) + (accessor.get("byteOffset") or 0)
    count = accessor["count"]
    if kind == "index":
        code = "<H" if accessor.get("componentType") == 5123 else "<I"
        step = 2 if accessor.get("componentType") == 5123 else 4
        return [struct.unpack_from(code, blob, start + item * step) for item in range(count)]
    return [struct.unpack_from(f"<{width}f", blob, start + item * width * 4) for item in range(count)]


def _default_banc(dump: Dump) -> str:
    if (dump.romfs / KOKIRI).is_file():
        return KOKIRI
    try:
        scenes = dump.list_scenes()
    except BancError:
        return ""
    return scenes[0]["path"] if scenes else ""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

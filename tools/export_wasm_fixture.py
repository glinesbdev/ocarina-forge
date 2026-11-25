#!/usr/bin/env python3
"""Export walkable-surface and texture fixtures for the wasm bench.

Reads the real game dump, samples the same Land_* island/cliff/hill meshes
that Snap uses, and writes binary fixtures plus a JSON sidecar with the
Python timings. Dump-derived geometry is not committed; regenerate with:

    python tools/export_wasm_fixture.py --dump E:\\sw2\\oot\\stable\\ray-test
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocarina_forge.dump import Dump
from ocarina_forge.extension import module as native_module
from ocarina_forge.formats import swizzle as swizzle_mod
from ocarina_forge.world_surface import collect_terrain_surfaces, query_bounds

SURFACE_MAGIC = 0x5357464F  # OFWS
TEXTURE_MAGIC = 0x53574654  # TFWS
LAKE_BANC = "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs"
KOKIRI_BANC = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"
# Island tree on Lake Hylia, the Snap check point.
LAKE_CENTER = (-192.39, 506.95)


def _write_surface(path: Path, step: float, triangles: list[float], points: list[float], expected: list[float]) -> None:
    tri_count = len(triangles) // 9
    point_count = len(expected)
    header = struct.pack("<IIdII8s", SURFACE_MAGIC, 1, float(step), tri_count, point_count, b"\x00" * 8)
    body = header + struct.pack(f"<{len(triangles)}d", *triangles)
    body += struct.pack(f"<{len(points)}d", *points)
    body += struct.pack(f"<{len(expected)}d", *expected)
    path.write_bytes(body)


def _unique_triangles(index) -> list[float]:
    seen: set[tuple] = set()
    flat: list[float] = []
    for cell in index.cells.values():
        for triangle, _meta in cell:
            key = tuple(triangle)
            if key in seen:
                continue
            seen.add(key)
            flat.extend(key)
    return flat


def _grid(min_x: float, min_z: float, max_x: float, max_z: float, spacing: float) -> list[tuple[float, float]]:
    points = []
    x = min_x
    while x <= max_x + 1e-9:
        z = min_z
        while z <= max_z + 1e-9:
            points.append((x, z))
            z += spacing
        x += spacing
    return points


def _export_area(dump: Dump, *, name: str, banc: str, points: list[tuple[float, float]], step: float, out_dir: Path) -> dict:
    bounds = query_bounds(points, pad=80.0)
    t0 = time.perf_counter()
    index = collect_terrain_surfaces(dump, banc, bounds=bounds)
    collect_ms = (time.perf_counter() - t0) * 1000
    triangles = _unique_triangles(index)
    unique = len(triangles) // 9
    if index.count != unique:
        print(f"{name}: dropped {index.count - unique} duplicate triangles (index had {index.count})")

    t0 = time.perf_counter()
    rebuilt = type(index)(step=step)
    for i in range(0, len(triangles), 9):
        rebuilt.add(tuple(triangles[i : i + 9]), {})
    index_ms = (time.perf_counter() - t0) * 1000

    expected = []
    t0 = time.perf_counter()
    for x, z in points:
        hit = rebuilt.sample(x, z)
        y = hit["worldY"] if hit else float("nan")
        expected.append(y)
    sample_ms = (time.perf_counter() - t0) * 1000

    hits = sum(1 for y in expected if y == y)
    stem = name
    _write_surface(
        out_dir / f"{stem}.bin",
        step,
        triangles,
        [coord for point in points for coord in point],
        expected,
    )
    meta = {
        "name": name,
        "banc": banc,
        "step": step,
        "triangles": len(triangles) // 9,
        "points": len(points),
        "hits": hits,
        "bounds": list(bounds) if bounds else None,
        "pythonCollectMs": collect_ms,
        "pythonIndexMs": index_ms,
        "pythonSampleMs": sample_ms,
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(
        f"{name}: {meta['triangles']} triangles, {meta['points']} points, "
        f"{hits} hits, collect {collect_ms:.1f} ms, index {index_ms:.1f} ms, sample {sample_ms:.1f} ms"
    )
    return meta


def _pattern(width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            out[i] = x & 0xFF
            out[i + 1] = y & 0xFF
            out[i + 2] = (x ^ y) & 0xFF
            out[i + 3] = 255
    return bytes(out)


def _export_texture(out_dir: Path, width: int = 1024, height: int = 1024) -> dict:
    native = native_module()
    if native is None or not hasattr(native, "encode_bc7") or not hasattr(native, "decode_swizzle"):
        raise SystemExit("The Rust Python extension is not available; cannot encode the texture fixture.")
    rgba = _pattern(width, height)
    block_w, block_h, bytes_per, tile = 4, 4, 16, 0
    log2 = swizzle_mod.default_block_log2(height, block_h)
    linear = native.encode_bc7(width, height, rgba)
    raw = swizzle_mod.swizzle(width, height, block_w, block_h, bytes_per, tile, log2, linear)
    t0 = time.perf_counter()
    decoded = native.decode_swizzle("bc7", width, height, block_w, block_h, bytes_per, tile, log2, raw, b"")
    python_ms = (time.perf_counter() - t0) * 1000
    if not decoded:
        raise SystemExit("native.decode_swizzle returned nothing for the texture fixture.")
    out_rgba, _note, _used = decoded
    codec = b"bc7"
    header = struct.pack(
        "<IIiiiiiiiIII",
        TEXTURE_MAGIC,
        1,
        width,
        height,
        block_w,
        block_h,
        bytes_per,
        tile,
        log2,
        len(codec),
        len(raw),
        len(out_rgba),
    )
    (out_dir / "texture.bin").write_bytes(header + codec + raw + out_rgba)
    meta = {
        "codec": "bc7",
        "width": width,
        "height": height,
        "blockW": block_w,
        "blockH": block_h,
        "bytesPer": bytes_per,
        "tile": tile,
        "log2": log2,
        "rawBytes": len(raw),
        "rgbaBytes": len(out_rgba),
        "pythonDecodeMs": python_ms,
        "source": "synthetic BC7 swizzle through the Rust Python extension",
    }
    (out_dir / "texture.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"texture: {width}x{height} bc7, decode {python_ms:.1f} ms, {len(raw)} swizzled bytes")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", default=r"E:\sw2\oot\stable\ray-test", help="Longshot dump folder (the one that contains romfs/).")
    parser.add_argument("--out", default=str(ROOT / "tools" / "wasm-bench" / "data"), help="Fixture output folder.")
    parser.add_argument("--spacing", type=float, default=2.0, help="Sample grid spacing in metres.")
    parser.add_argument("--step", type=float, default=32.0, help="MeshIndex cell size in metres.")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dump = Dump(args.dump)
    lake_half = 400.0
    cx, cz = LAKE_CENTER
    lake_points = _grid(cx - lake_half, cz - lake_half, cx + lake_half, cz + lake_half, args.spacing)
    kokiri_points = _grid(-180.0, -180.0, 180.0, 180.0, args.spacing)
    _export_area(dump, name="hyrule-field-child", banc=LAKE_BANC, points=lake_points, step=args.step, out_dir=out_dir)
    _export_area(dump, name="kokiri-forest-child", banc=KOKIRI_BANC, points=kokiri_points, step=args.step, out_dir=out_dir)
    _export_texture(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

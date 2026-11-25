"""Count how model materials find an albedo, without keeping texels.

``python -m ocarina_forge.textureaudit <dump>`` prints JSON. Each material is
``sampler`` (the bound texture name resolved), ``tex-fallback`` (a different
Tex file was used), or ``flat``. Flat rows are grouped by cause. Example
rows name the model, the material, the sampler binds, and the paths that
were tried. They do not contain image bytes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ocarina_forge.formats.bfres import FresError
from ocarina_forge.formats.zsio import decompress, is_zstd
from ocarina_forge.models import _resolve_materials, parse

_EXAMPLE_LIMIT = 4


def audit_models(dump, *, prefix: str = "") -> dict:
    """Sweep ``Model/*.bfres`` and ``Model/*.bfres.zs`` under ``dump``."""
    counts = {"sampler": 0, "tex-fallback": 0, "flat": 0}
    causes: dict[str, dict] = {}
    models = 0
    unreadable = []
    for relative in _model_files(dump):
        if prefix and prefix not in relative:
            continue
        models += 1
        try:
            blob, _dict_id = dump.read_file(relative)
            if is_zstd(blob):
                blob, _dict_id = decompress(blob, dump.dicts())
            parsed = parse(blob)
            _external, log, _raws = _resolve_materials(dump, parsed, relative, False)
        except (FresError, OSError, ValueError, EOFError) as exc:
            if len(unreadable) < _EXAMPLE_LIMIT:
                unreadable.append({"model": relative, "error": str(exc)[:200]})
            continue
        colored = _colored_materials(parsed)
        for entry in log:
            kind = _kind(entry)
            if kind == "flat":
                cause = _cause(entry, colored)
                counts["flat"] += 1
                bucket = causes.setdefault(cause, {"count": 0, "examples": []})
                bucket["count"] += 1
                if len(bucket["examples"]) < _EXAMPLE_LIMIT:
                    bucket["examples"].append(_example(relative, entry, cause))
            else:
                counts[kind] += 1
    return {
        "models": models,
        "materials": counts["sampler"] + counts["tex-fallback"] + counts["flat"],
        "resolvedViaSampler": counts["sampler"],
        "resolvedViaTexFallback": counts["tex-fallback"],
        "flat": counts["flat"],
        "flatByCause": causes,
        "unreadable": unreadable,
    }


def _model_files(dump) -> list[str]:
    folder = dump.romfs / "Model"
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if ".anim." in name or ".bfres.mc" in name:
            continue
        if name.endswith(".bfres.zs") or name.endswith(".bfres"):
            found.append(path.relative_to(dump.romfs).as_posix())
    found.sort()
    return found


def _colored_materials(models) -> set[str]:
    names = set()
    for model in models:
        for part in model.parts:
            if part.colors and part.material:
                names.add(part.material)
    return names


def _kind(entry: dict) -> str:
    via = entry.get("via") or ""
    if via in {"sampler", "tex-fallback"} and (entry.get("decoded") or entry.get("textured")):
        return via
    if entry.get("decoded") or entry.get("textured"):
        return "sampler" if entry.get("sampler") else "tex-fallback"
    return "flat"


def _cause(entry: dict, colored: set[str]) -> str:
    stored = entry.get("flatCause") or ""
    binds = entry.get("binds") or []
    if entry.get("material") in colored and not binds and stored in {"", "untextured"}:
        return "vertex-colour"
    if stored:
        return stored
    text = (entry.get("error") or "").lower()
    if "terrain proxy" in text:
        return "terrain-proxy"
    if "water stand-in" in text:
        return "water-stand-in"
    if "vertex colour" in text:
        return "vertex-colour"
    if "only non-color" in text:
        return "non-color-samplers"
    if "no texture names" in text:
        return "untextured"
    if "magenta" in text:
        return "magenta-placeholder"
    if "format" in text or "not a colour" in text:
        return "unsupported-format"
    if "no tex file" in text:
        return "missing-file"
    if binds:
        return "non-color-samplers"
    return "untextured"


def _example(relative: str, entry: dict, cause: str) -> dict:
    binds = [f"{slot or '?'}={tex}" for slot, tex in (entry.get("binds") or []) if slot or tex]
    tried = [str(path) for path in (entry.get("tried") or [])[:8]]
    return {
        "model": relative,
        "material": entry.get("material") or "",
        "cause": cause,
        "sampler": entry.get("sampler") or "",
        "wanted": entry.get("wanted") or "",
        "binds": binds,
        "tried": tried,
        "error": (entry.get("error") or "")[:300],
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__.strip())
        return 0 if args and args[0] in {"-h", "--help"} else 2
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError

    try:
        dump = Dump(Path(args[0]))
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    prefix = args[1] if len(args) > 1 else ""
    report = audit_models(dump, prefix=prefix)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    summary = (
        f"models {report['models']}: "
        f"sampler {report['resolvedViaSampler']}, "
        f"tex-fallback {report['resolvedViaTexFallback']}, "
        f"flat {report['flat']}"
    )
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

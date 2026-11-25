"""Scene lighting references and the Longshot lighting-capture import.

The capture mod writes ``storage.global`` the same way height-check does: a
``lighting`` summary plus one ``lighting:<map>`` object of samples. Sun, fog,
exposure, and the sky stay null until a build actually returns them. This
module does not invent those numbers.

Per-scene JSON is written under the user data directory
(``~/.local/share/ocarina-forge/lighting``, or ``OCARINA_FORGE_LIGHTING``).
It is not a game asset and it is not committed.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

from ocarina_forge.actors import HEADER_KEYS, read_vec
from ocarina_forge.locate import setting

SCHEMA = 1
MOD_ID = "local/lighting-capture"
# What capture.js calls, plus gfx.world for the static longshot/gfx probe.
# Checked against the permission table in ocarina_forge/permissions.py.
CAPTURE_PERMISSIONS = ("state.read", "actors.read", "storage", "gfx.world")
UNAVAILABLE = (
    "sun",
    "ambient",
    "hemisphere",
    "fog",
    "sky",
    "exposure",
    "tonemap",
    "env",
)
# Directory names to stat. A hit is evidence. A miss is not a format claim.
PROBE_DIRS = (
    "Env",
    "Environment",
    "Sky",
    "Lighting",
    "Light",
    "Fog",
    "Effect",
    "Effects",
    "Climate",
    "Weather",
    "World/Lighting",
    "System/Env",
)
NAME_NEEDLES = ("Env", "Sky", "Fog", "Light", "Climate", "Weather", "Palette")
_MAP_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_TONEMAP = {"aces", "linear", "none", "reinhard", "cineon"}


def references_dir() -> Path:
    """Where imported per-scene lighting JSON is stored. Never the repo."""
    env = setting("LIGHTING")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share" / "ocarina-forge" / "lighting"


def capture_root() -> Path:
    """The mod sources shipped with Forge (script only, no game files)."""
    return Path(__file__).resolve().parent / "lighting_capture"


def map_name_for_banc(banc: str) -> str:
    """``Banc/KokiriForest/KokiriForest_Child_Static...`` -> ``KokiriForest_Child``."""
    name = (banc or "").replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    for suffix in ("_Static", "_Dynamic"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def emitter_kind(gyaml: str) -> str | None:
    """Point emitters, locators, and fog volumes Forge already classifies."""
    name = gyaml or ""
    if "Fog" in name:
        return "fog-volume"
    if name.startswith("EftLoc_") or "Locator" in name:
        return "locator"
    if name.startswith("Eft"):
        return "emitter"
    return None


def _finite(value, low: float | None = None, high: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if low is not None and number < low:
        return None
    if high is not None and number > high:
        return None
    return number


def _color(value):
    if isinstance(value, str) and _HEX.match(value):
        return value.lower()
    if isinstance(value, (list, tuple)) and len(value) == 3:
        numbers = [_finite(item, 0.0, 1.0) for item in value]
        if any(item is None for item in numbers):
            return None
        return numbers
    return None


def _vec3(value, limit: float = 1.0e5):
    if isinstance(value, dict):
        raw = [value.get("x"), value.get("y"), value.get("z")]
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        raw = value[:3]
    else:
        return None
    numbers = [_finite(item, -limit, limit) for item in raw]
    if any(item is None for item in numbers):
        return None
    return numbers


def _sun(value):
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    position = _vec3(value.get("position"))
    if color is None or position is None:
        return None
    intensity = value.get("intensity", 1.0)
    strength = _finite(intensity, 0.0, 20.0)
    if strength is None:
        return None
    return {"color": color, "position": position, "intensity": strength}


def _hemisphere(value):
    if not isinstance(value, dict):
        return None
    sky = _color(value.get("sky"))
    ground = _color(value.get("ground"))
    intensity = _finite(value.get("intensity", 1.0), 0.0, 20.0)
    if sky is None or ground is None or intensity is None:
        return None
    return {"sky": sky, "ground": ground, "intensity": intensity}


def _ambient(value):
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    intensity = _finite(value.get("intensity", 1.0), 0.0, 20.0)
    if color is None or intensity is None:
        return None
    return {"color": color, "intensity": intensity}


def _fog(value):
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    if color is None:
        return None
    kind = value.get("kind") or "linear"
    if kind == "exp2":
        density = _finite(value.get("density"), 0.0, 1.0)
        if density is None:
            return None
        return {"kind": "exp2", "color": color, "density": density}
    if kind != "linear":
        return None
    near = _finite(value.get("near"), 0.0, 100000.0)
    far = _finite(value.get("far"), 0.0, 100000.0)
    if near is None or far is None or far <= near:
        return None
    return {"kind": "linear", "color": color, "near": near, "far": far}


def _sky(value):
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    if color is None:
        return None
    return {"color": color}


def _gfx_note(value):
    """Keep a live longshot/gfx dump. Key lists stay intact past the generic cap."""
    if not isinstance(value, dict):
        return None
    note = {}
    for key in ("present", "import", "worldType"):
        item = value.get(key)
        if isinstance(item, bool) or item is None:
            note[key] = item
        elif isinstance(item, str):
            note[key] = item[:80]
    for key in ("moduleKeys", "worldKeys"):
        rows = value.get(key)
        if isinstance(rows, list):
            note[key] = [str(item)[:80] for item in rows[:24] if isinstance(item, (str, int))]
    world = value.get("world")
    if isinstance(world, dict):
        copied = {}
        for index, (key, item) in enumerate(world.items()):
            if index >= 24:
                break
            if isinstance(item, str):
                copied[str(key)[:80]] = item[:80]
            elif isinstance(item, (int, float, bool)) or item is None:
                copied[str(key)[:80]] = item
            else:
                copied[str(key)[:80]] = _shrink(item, 1)
        note["world"] = copied
    return note or None


def _shrink(value, depth: int = 2):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _finite(value)
        return number
    if isinstance(value, str):
        return value[:80]
    if depth <= 0:
        return None
    if isinstance(value, list):
        return [_shrink(item, depth - 1) for item in value[:4]]
    if isinstance(value, dict):
        out = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 12:
                break
            text = str(key)[:80]
            if not text:
                continue
            out[text] = _shrink(item, depth - 1)
        return out
    return None


def _effects(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if not isinstance(kind, str) or not kind:
            continue
        rows.append({
            "type": kind[:80],
            "key": item.get("key") if isinstance(item.get("key"), str) else None,
            "x": _finite(item.get("x"), -1.0e6, 1.0e6),
            "y": _finite(item.get("y"), -1.0e6, 1.0e6),
            "z": _finite(item.get("z"), -1.0e6, 1.0e6),
        })
        if len(rows) >= 24:
            break
    return rows


def normalize_sample(raw) -> dict | None:
    """Keep one capture row. Invalid light values are dropped, not guessed."""
    if not isinstance(raw, dict):
        return None
    x = _finite(raw.get("x"), -1.0e6, 1.0e6)
    y = _finite(raw.get("y"), -1.0e6, 1.0e6)
    z = _finite(raw.get("z"), -1.0e6, 1.0e6)
    if x is None or y is None or z is None:
        return None
    tonemap = raw.get("tonemap")
    if not isinstance(tonemap, str) or tonemap.lower() not in _TONEMAP:
        tonemap = None
    else:
        tonemap = tonemap.lower()
    exposure = raw.get("exposure")
    exposure = None if exposure is None else _finite(exposure, 0.05, 8.0)
    observed = raw.get("observed") if isinstance(raw.get("observed"), dict) else {}
    return {
        "x": x,
        "y": y,
        "z": z,
        "timeOfDay": None if raw.get("timeOfDay") is None else _finite(raw.get("timeOfDay"), -1.0e4, 1.0e4),
        "frame": None if raw.get("frame") is None else _finite(raw.get("frame"), 0.0, 1.0e12),
        "age": raw.get("age") if isinstance(raw.get("age"), str) else None,
        "mode": raw.get("mode") if isinstance(raw.get("mode"), str) else None,
        "sun": _sun(raw.get("sun")),
        "ambient": _ambient(raw.get("ambient")),
        "hemisphere": _hemisphere(raw.get("hemisphere")),
        "fog": _fog(raw.get("fog")),
        "sky": _sky(raw.get("sky")),
        "exposure": exposure,
        "tonemap": tonemap,
        "env": _sky(raw.get("env")),
        "camera": _shrink(raw.get("camera")),
        "effects": _effects(raw.get("effects")),
        "observed": {
            "game": _shrink((observed.get("game") if isinstance(observed, dict) else None) or {}),
            "player": _shrink((observed.get("player") if isinstance(observed, dict) else None) or {}),
        },
    }


def unwrap_storage(raw) -> tuple[dict, str | None]:
    """Accept a storage.global wrapper or the data object itself."""
    if not isinstance(raw, dict):
        raise ValueError("lighting capture is not a JSON object")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        raise ValueError("lighting capture is not a JSON object")
    mod = raw.get("mod") if isinstance(raw.get("mod"), str) else None
    return data, mod


def scenes_from_capture(raw) -> dict[str, dict]:
    """Split a capture into one document per map name."""
    data, mod = unwrap_storage(raw)
    summary = data.get("lighting") if isinstance(data.get("lighting"), dict) else {}
    grouped: dict[str, list] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.startswith("lighting:") or not isinstance(value, dict):
            continue
        rest = key[len("lighting:"):]
        map_name = rest.split(":", 1)[0]
        if not _MAP_NAME.match(map_name):
            continue
        rows = value.get("samples")
        if not isinstance(rows, list):
            continue
        grouped.setdefault(map_name, []).extend(rows)
    documents = {}
    notes = [str(item) for item in (summary.get("notes") or []) if isinstance(item, str)]
    unavailable = [str(item) for item in (summary.get("unavailable") or UNAVAILABLE)]
    probes = summary.get("probes") if isinstance(summary.get("probes"), dict) else {}
    gfx = _gfx_note(summary.get("gfx"))
    for map_name, rows in grouped.items():
        samples = [item for item in (normalize_sample(row) for row in rows) if item]
        document = {
            "schema": SCHEMA,
            "map": map_name,
            "mod": mod or MOD_ID,
            "unavailable": unavailable,
            "probes": {str(key): bool(value) for key, value in list(probes.items())[:16]},
            "notes": notes,
            "samples": samples[:48],
        }
        if gfx:
            document["gfx"] = gfx
        documents[map_name] = document
    if not documents and isinstance(data.get("samples"), list) and isinstance(data.get("map"), str):
        map_name = data["map"]
        if _MAP_NAME.match(map_name):
            samples = [item for item in (normalize_sample(row) for row in data["samples"]) if item]
            documents[map_name] = {
                "schema": SCHEMA,
                "map": map_name,
                "mod": mod or MOD_ID,
                "unavailable": unavailable,
                "probes": {str(key): bool(value) for key, value in list(probes.items())[:16]},
                "notes": notes,
                "samples": samples[:48],
            }
    return documents


STORE_NAME = "local~lighting-capture.json"


def find_store(path: str | Path) -> Path:
    """A capture JSON, or the store file inside a Longshot game folder.

    A Longshot run reported the store at
    ``<game>/dev/user/modstore/global/local~lighting-capture.json``. The ``~``
    is the mod id ``local/lighting-capture`` with the slash replaced. That
    file is the storage key map. It does not contain a ``mod`` field.
    """
    source = Path(path).expanduser()
    if source.is_file():
        return source
    if not source.is_dir():
        raise ValueError(f"{source} is not a capture file or a game folder")
    preferred = [
        source / "dev" / "user" / "modstore" / "global" / STORE_NAME,
        source / "user" / "modstore" / "global" / STORE_NAME,
    ]
    for candidate in preferred:
        if candidate.is_file():
            return candidate
    matches: list[Path] = []
    for candidate in source.rglob(STORE_NAME):
        if candidate.is_file():
            matches.append(candidate)
        if len(matches) >= 8:
            break
    if not matches:
        raise ValueError(
            f"no {STORE_NAME} under {source}. "
            f"Expected dev/user/modstore/global/{STORE_NAME}"
        )
    for candidate in matches:
        parts = set(candidate.parts)
        if "modstore" in parts and "global" in parts:
            return candidate
    return matches[0]


def import_capture(path: str | Path, dest: str | Path | None = None) -> list[Path]:
    """Write one JSON file per map. Returns the paths written."""
    source = find_store(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    documents = scenes_from_capture(raw)
    if not documents:
        raise ValueError(f"{source} has no lighting:<map> samples")
    folder = Path(dest).expanduser() if dest else references_dir()
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for map_name, document in sorted(documents.items()):
        target = folder / f"{map_name}.json"
        target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written.append(target)
    return written


def load_reference(map_name: str, folder: str | Path | None = None) -> dict | None:
    """Read one imported scene file. Missing is None, not an error."""
    if not _MAP_NAME.match(map_name or ""):
        return None
    root = Path(folder).expanduser() if folder else references_dir()
    path = root / f"{map_name}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    samples = [item for item in (normalize_sample(row) for row in (raw.get("samples") or [])) if item]
    return {
        "schema": SCHEMA,
        "map": map_name,
        "mod": raw.get("mod") if isinstance(raw.get("mod"), str) else None,
        "unavailable": [str(item) for item in (raw.get("unavailable") or [])],
        "probes": raw.get("probes") if isinstance(raw.get("probes"), dict) else {},
        "notes": [str(item) for item in (raw.get("notes") or []) if isinstance(item, str)],
        "samples": samples[:48],
        "path": str(path),
        "gfx": raw.get("gfx") if isinstance(raw.get("gfx"), dict) else None,
    }


def collect_emitters(document: dict, banc: str = "") -> list[dict]:
    """Effect, locator, and fog actors already stored on a banc. Key names only."""
    rows = []
    for actor in document.get("Actors") or []:
        if not isinstance(actor, dict):
            continue
        gyaml = str(actor.get("Gyaml") or "")
        kind = emitter_kind(gyaml)
        if kind is None:
            continue
        dynamic = actor.get("Dynamic") if isinstance(actor.get("Dynamic"), dict) else {}
        translate = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        scale = read_vec(actor, "Scale", [1.0, 1.0, 1.0])
        rows.append({
            "banc": banc,
            "gyaml": gyaml,
            "kind": kind,
            "name": actor.get("Name") if isinstance(actor.get("Name"), str) else None,
            "translate": translate,
            "scale": scale,
            "paramKeys": sorted(str(key) for key in list(dynamic.keys())[:40] if str(key) not in HEADER_KEYS),
        })
        if len(rows) >= 400:
            break
    return rows


def param_summary(document) -> dict:
    """Top-level GameBancParam key names. Values are not interpreted."""
    if not isinstance(document, dict):
        return {"keys": [], "parent": None, "terrainSceneName": None}
    keys = []
    for key in document.keys():
        text = str(key)
        if text == "$parent":
            continue
        keys.append(text)
    parent = document.get("$parent")
    scene = document.get("TerrainSceneName")
    return {
        "keys": sorted(keys)[:80],
        "parent": parent if isinstance(parent, str) else None,
        "terrainSceneName": scene if isinstance(scene, str) else None,
    }


def probe_directories(dump) -> dict:
    """Which probed folders exist under romfs. Names are a search list, not a layout."""
    present = []
    absent = []
    romfs = dump.romfs
    for relative in PROBE_DIRS:
        path = romfs / relative
        (present if path.is_dir() else absent).append(relative)
    return {"present": present, "absent": absent, "note": "Directory names are probes. A missing folder is not a claim that the game has no such file."}


def matching_names(dump, folder: str, limit: int = 40) -> list[str]:
    """File names in one folder that contain a lighting-related needle."""
    directory = dump.romfs / folder
    if not directory.is_dir():
        return []
    found = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    for entry in entries:
        if any(needle.lower() in entry.name.lower() for needle in NAME_NEEDLES):
            found.append(f"{folder}/{entry.name}")
        if len(found) >= limit:
            break
    return found


def _open_param(dump, banc: str) -> tuple[str, dict | None]:
    from ocarina_forge.models import _locate_heightmap

    located = _locate_heightmap(dump, banc)
    document = located.get("document") if isinstance(located, dict) else None
    param = located.get("param") if isinstance(located, dict) else ""
    return param or "", document if isinstance(document, dict) else None


def lighting_report(dump, banc: str, *, survey: bool = False) -> dict:
    """Emitters, GameBancParam key names, probes, and a capture if one was imported."""
    from ocarina_forge.errors import BancError
    from ocarina_forge.scene import sibling_bancs

    if not banc or ".." in banc.split("/"):
        raise BancError("That scene path is not in the dump.", "not_found")
    document, _dict_id, _raw = dump.read_byml(banc)
    if not isinstance(document, dict):
        document = {}
    emitters = collect_emitters(document, banc)
    try:
        available = {row["path"] for row in dump.list_scenes()}
    except (OSError, BancError):
        available = set()
    for sibling in sibling_bancs(banc, available):
        try:
            other, _dict_id, _raw = dump.read_byml(sibling)
        except BancError:
            continue
        if isinstance(other, dict):
            emitters.extend(collect_emitters(other, sibling))
    param_path, param_document = _open_param(dump, banc)
    map_name = map_name_for_banc(banc)
    reference = load_reference(map_name)
    expected = references_dir() / f"{map_name}.json"
    area = banc.split("/")[1] if banc.startswith("Banc/") and banc.count("/") >= 2 else ""
    names = matching_names(dump, "Banc/GameBancParam")
    if area:
        names.extend(matching_names(dump, f"Banc/{area}"))
    report = {
        "banc": banc,
        "map": map_name_for_banc(banc),
        "unavailable": list(reference["unavailable"]) if reference else list(UNAVAILABLE),
        "notes": list(reference["notes"]) if reference else [
            "No imported capture for this map. Sun, fog, sky, and exposure are not read from a file format in this checkout.",
        ],
        "probes": (reference or {}).get("probes") or {},
        "samples": (reference or {}).get("samples") or [],
        "referencePath": (reference or {}).get("path") or str(expected),
        "referenceFound": reference is not None,
        "emitters": emitters[:400],
        "param": {"path": param_path, **param_summary(param_document)},
        "directories": probe_directories(dump),
        "nameHits": names[:40],
    }
    if survey:
        report["terrainResources"] = _terrain_types(dump, banc)
    return report


def _terrain_types(dump, banc: str) -> dict:
    """Declared TSCB resource extensions. Payloads other than the ones formats.md already decodes are not read."""
    from ocarina_forge.formats.terrain import TerrainError
    from ocarina_forge.grounddebug import inspect_resources

    try:
        found = inspect_resources(dump, banc, [])
    except (TerrainError, OSError, ValueError) as exc:
        return {"error": str(exc)}
    types = []
    for source in found.get("sources") or []:
        for item in source.get("fileTypes") or []:
            if not isinstance(item, dict):
                continue
            types.append({
                "source": source.get("source"),
                "type": item.get("type"),
                "name": item.get("name"),
                "extension": item.get("extension"),
            })
    return {
        "note": "Extensions come from the TSCB index. bake_ao.extm is decoded for the ground. bake_gi.extm and custom.extm are not.",
        "types": types[:64],
    }


def _entry_ok(text: str, gfx_probe: bool) -> bool:
    """The probe entry is a static import. The default entry is not."""
    static = 'from "longshot/gfx"' in text
    dynamic = "import(" in text
    if gfx_probe:
        return static and not dynamic and "forge-entry: gfx-probe" in text
    return not static


def capture_manifest_text() -> str:
    """``mod.json`` with permissions taken from the Longshot permission table.

    The names are the calls in ``capture.js`` (``state.read``, ``actors.read``,
    ``storage``) plus ``gfx.world``, which the gfx-probe entry imports.
    """
    from ocarina_forge.permissions import assert_player_permissions

    manifest = json.loads((capture_root() / "mod.json").read_text(encoding="utf-8"))
    manifest["permissions"] = assert_player_permissions(list(CAPTURE_PERMISSIONS))
    return json.dumps(manifest, indent=2) + "\n"


def install_mod(game: Path, *, gfx_probe: bool = False) -> Path:
    """Copy the capture mod into ``<game>/mods/local/lighting-capture``.

    The default entry does not static-import ``longshot/gfx``. ``gfx_probe``
    installs the static-import entry for a build that already reports
    ``has("gfx.world")``. The written ``main.js`` is checked before this
    returns, so a probe install cannot leave the dynamic entry in place.
    """
    source = capture_root()
    entry_name = "probe.js" if gfx_probe else "main.js"
    entry_path = source / entry_name
    if not entry_path.is_file():
        raise FileNotFoundError(f"missing capture entry {entry_path}")
    text = entry_path.read_text(encoding="utf-8")
    if not _entry_ok(text, gfx_probe):
        raise RuntimeError(f"{entry_path} is not the {'gfx probe' if gfx_probe else 'default'} entry")
    dest = Path(game).expanduser() / "mods" / "local" / "lighting-capture"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "mod.json").write_text(capture_manifest_text(), encoding="utf-8")
    shutil.copyfile(source / "capture.js", dest / "capture.js")
    main_path = dest / "main.js"
    if main_path.exists():
        main_path.unlink()
    main_path.write_text(text, encoding="utf-8", newline="\n")
    written = main_path.read_text(encoding="utf-8")
    if written != text or not _entry_ok(written, gfx_probe):
        raise RuntimeError(
            f"wrote the wrong entry to {main_path} ({main_path.stat().st_size} bytes)"
        )
    stale = dest / "probe.js"
    if stale.is_file():
        stale.unlink()
    return dest


def render_report(report: dict) -> str:
    lines = [f"lighting {report.get('map') or ''} ({report.get('banc') or ''})".rstrip()]
    samples = report.get("samples") or []
    lines.append(f"capture: {len(samples)} sample(s)" + (f" from {report.get('referencePath')}" if report.get("referencePath") else " (none imported)"))
    if report.get("notes"):
        lines.append("notes: " + " ".join(report["notes"]))
    param = report.get("param") or {}
    keys = ", ".join(param.get("keys") or []) or "(no GameBancParam keys read)"
    lines.append(f"GameBancParam {param.get('path') or '(not found)'}: {keys}")
    directories = report.get("directories") or {}
    lines.append("probed directories present: " + (", ".join(directories.get("present") or []) or "(none)"))
    hits = report.get("nameHits") or []
    lines.append("name hits: " + (", ".join(hits) if hits else "(none)"))
    emitters = report.get("emitters") or []
    lines.append(f"effect actors: {len(emitters)}")
    for row in emitters[:12]:
        keys = ",".join(row.get("paramKeys") or [])
        lines.append(f"  {row.get('kind')} {row.get('gyaml')} {row.get('translate')} params:{keys or '-'}")
    if len(emitters) > 12:
        lines.append(f"  … {len(emitters) - 12} more")
    terrain = report.get("terrainResources")
    if isinstance(terrain, dict):
        if terrain.get("error"):
            lines.append(f"terrain index: {terrain['error']}")
        else:
            lines.append(terrain.get("note") or "terrain index")
            for item in terrain.get("types") or []:
                lines.append(f"  {item.get('extension') or item.get('name')} type {item.get('type')}")
    return "\n".join(lines) + "\n"


def windows_steps(game: Path | None = None, *, gfx_probe: bool = False) -> str:
    """What Bradyn runs on Windows. The game is not started from here."""
    folder = game or Path.home() / "Games" / "Longshot" / "mod-test"
    mod = folder / "mods" / "local" / "lighting-capture"
    data = references_dir()
    return "\n".join([
        "Lighting capture (Windows). Forge cannot start the game.",
        "",
        "1. From the repo, with the venv active. Quit Longshot first.",
        "   python -m ocarina_forge lighting-mod --install --gfx-probe --game <game>",
        "   --gfx-probe static-imports longshot/gfx. The spec (section 7.2) has no",
        "   import(), which is why the dynamic import logged TypeError. The default",
        "   install, without --gfx-probe, still links on a build that lacks the module.",
        f"   Files land in {mod}",
        "   The default game is %USERPROFILE%\\Games\\Longshot\\mod-test.",
        "",
        "2. Start Longshot. Re-enable local/lighting-capture. The manifest now asks",
        "   for state.read, actors.read, storage, and gfx.world. A permission change",
        "   is read at the next start, and the mod stays off until that grant.",
        "",
        "3. Load a map and walk. The mod records a sample when Link moves about 8 m,",
        "   and at most 48 samples per map. It does not change the picture.",
        "   The log is <game>\\logs\\mod-run.txt. A capture line contains FORGE_LIGHTING.",
        "   The gfx line lists export names from the static import. Send that line.",
        "   It is not a light reading. The probe build is "
        + ("the one just described." if gfx_probe else "selected with --gfx-probe."),
        "",
        "4. Quit the game so storage flushes. The store file reported from a",
        "   Longshot run is:",
        r"   <game>\dev\user\modstore\global\local~lighting-capture.json",
        "   The ~ stands in for the slash in the mod id local/lighting-capture.",
        "   The file is the storage key map. It has no \"mod\" field, so a search",
        "   for \"mod\": \"local/lighting-capture\" does not match it. To find it:",
        "   Get-ChildItem -Recurse -Filter local~lighting-capture.json <game>",
        "",
        "5. Import the game folder, or that JSON. One small file is written per map",
        "   name. Nothing is committed. The printed name is the map key",
        "   (KokiriForest_Child for the Child Kokiri bancs):",
        "   python -m ocarina_forge lighting-import <game>",
        f"   Files land in {data} (OCARINA_FORGE_LIGHTING overrides the folder).",
        "",
        "6. The Forge server does not need a restart to pick up the new mod.",
        "   The gfx line in the game log is the dump. Re-import the game folder",
        "   only when that dump should also land in the reference JSON, then reload",
        "   the page. /api/lighting reads the file on each request, and the open",
        "   scene keeps the lighting it already fetched.",
        "   The line under the stage bar names the reference file, the sample count,",
        "   and which fields came from the file. Editor defaults draws the editor",
        "   lights so the two can be compared. The published API does not return",
        "   sun or fog, so those stay on the editor values until a file contains them.",
        "",
        "7. Optional file survey, with your dump (prints key names, does not decode payloads):",
        "   python -m ocarina_forge lighting-survey Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs --dump <dump>",
        "",
    ])

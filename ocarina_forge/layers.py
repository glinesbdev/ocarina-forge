"""Which parts of a scene Forge fetches.

A placement banc is one BYML. Every actor lives in ``Actors``. Terrain
height, actor meshes, textures, and behavior graphs are separate files, so
a load filter can skip those decodes. The filter never rewrites the banc:
save and both exporters still start from the source document.

Actor layers follow the Gyaml, because the banc has no decoration, light,
or water list:

- ``Npc_`` / ``Enemy_`` / ``Animal_`` / ``Shop_`` are models.
- ``Eft`` (including ``EftLoc_``) is effects.
- Water tokens match the model reader (``water``, ``lake``, ``river``, …).
  An ``npc`` token is not water.
- ``AirWall``, ``collision``, ``collider``, and ``navmesh`` are collision
  volumes. They draw as gizmos. Phive on the actor is not a separate file.
- Light tokens (``light``, ``lamp``, ``torch``, ``candle``, ``lantern``,
  ``chandelier``) are lights. ``bake0`` / ``bake1`` are not this layer.
- Dressing tokens (grass, flower, bush, …) are decorations.
- Everything else, including ``Land_`` meshes, areas, and ``StartPos``, is
  models. ``Land_`` is not the TSCB heightmap.

``terrain`` is the TSCB / static-map pipeline, not an actor. ``behavior`` is
the AINB graph plus ``Links`` (vanilla ``Dst``, edited ``Hash``). ``textures``
is a decode flag on mesh layers, not an actor list. Layout loads model
geometry and skips BNTX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ocarina_forge.actors import format_hash, parse_hash
from ocarina_forge.errors import BancError

LAYERS = (
    "models",
    "terrain",
    "water",
    "collision",
    "behavior",
    "effects",
    "decorations",
    "lights",
)

# Same set as ``models._WATER_TOKENS``. A test locks the two together.
WATER_TOKENS = frozenset({
    "water",
    "lake",
    "waterfall",
    "ocean",
    "pond",
    "sea",
    "river",
    "stream",
    "moat",
    "aqua",
    "mizu",
    "brook",
})

_LIGHT_TOKENS = frozenset({"light", "lamp", "torch", "candle", "lantern", "chandelier"})
_DECORATION_TOKENS = frozenset({
    "grass",
    "flower",
    "bush",
    "weed",
    "ivy",
    "fern",
    "mushroom",
    "moss",
    "vine",
    "shrub",
    "plant",
    "clover",
    "herb",
    "petal",
})
_COLLISION_TOKENS = frozenset({"airwall", "collision", "collider", "navmesh"})
_CHARACTER_PREFIXES = ("Npc_", "Enemy_", "Animal_", "Shop_")
_TOKEN = re.compile(r"[a-z0-9]+")

PRESETS = {
    "full": {"layers": LAYERS, "textures": True},
    "visual": {
        "layers": ("models", "terrain", "water", "effects", "decorations", "lights"),
        "textures": True,
    },
    "layout": {
        "layers": ("models", "terrain", "water", "collision", "decorations", "lights"),
        "textures": False,
    },
    "terrain-water": {"layers": ("terrain", "water"), "textures": True},
    "logic": {"layers": ("behavior", "collision"), "textures": False},
}

_PRESET_LABELS = {
    "full": "Full",
    "visual": "Visual only",
    "layout": "Layout",
    "terrain-water": "Terrain + water",
    "logic": "Logic",
}


@dataclass(frozen=True)
class LoadSpec:
    layers: frozenset[str]
    textures: bool
    preset: str

    def as_dict(self) -> dict:
        return {
            "preset": self.preset,
            "textures": self.textures,
            "layers": {layer: layer in self.layers for layer in LAYERS},
            "visible": {layer: True for layer in LAYERS},
        }


def _tokens(name: str) -> set[str]:
    """Split on underscores and camel case. ``WaterPlane`` yields ``water``."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name or "")
    return set(_TOKEN.findall(spaced.lower()))


def actor_layer(gyaml: str) -> str:
    """The load layer for one actor. Terrain and behavior are not Gyaml layers."""
    name = gyaml or ""
    if name.startswith(_CHARACTER_PREFIXES):
        return "models"
    if name.startswith("Eft"):
        return "effects"
    tokens = _tokens(name)
    if "npc" not in tokens and tokens & WATER_TOKENS:
        return "water"
    folded = name.lower()
    if tokens & _COLLISION_TOKENS or any(word in folded for word in _COLLISION_TOKENS):
        return "collision"
    if tokens & _LIGHT_TOKENS:
        return "lights"
    if tokens & _DECORATION_TOKENS:
        return "decorations"
    return "models"


def record_layer(record: dict | None) -> str:
    if not isinstance(record, dict):
        return "models"
    return actor_layer(str(record.get("gyaml") or record.get("actor") or ""))


def preset_spec(name: str) -> LoadSpec | None:
    found = PRESETS.get(name)
    if not found:
        return None
    return LoadSpec(frozenset(found["layers"]), bool(found["textures"]), name)


def _spec_from_layers(layers: set[str], textures: bool | None, preset: str = "") -> LoadSpec:
    if not preset:
        preset = _match_preset(layers, textures)
    if textures is None:
        textures = _default_textures(layers, preset)
    return LoadSpec(frozenset(layer for layer in layers if layer in LAYERS), bool(textures), preset or "custom")


def _default_textures(layers: set[str], preset: str) -> bool:
    known = PRESETS.get(preset)
    if known:
        return bool(known["textures"])
    return bool(layers & {"models", "water", "decorations", "lights"})


def _match_preset(layers: set[str], textures: bool | None) -> str:
    frozen = frozenset(layers)
    for name, found in PRESETS.items():
        if frozenset(found["layers"]) != frozen:
            continue
        if textures is None or bool(found["textures"]) == bool(textures):
            return name
    return "custom"


def parse_textures_flag(value) -> bool | None:
    """``None`` when the caller did not set the flag."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"0", "false", "off", "no"}:
        return False
    if text in {"1", "true", "on", "yes"}:
        return True
    return None


def parse_load_query(text: str, textures=None) -> LoadSpec | None:
    """``?load=`` value. Empty means the caller did not set a filter."""
    raw = (text or "").strip()
    if not raw:
        return None
    forced = parse_textures_flag(textures)
    if "," not in raw:
        preset = preset_spec(raw)
        if preset:
            if forced is None:
                return preset
            return LoadSpec(preset.layers, forced, preset.preset if forced == preset.textures else "custom")
    layers = set()
    for token in raw.split(","):
        name = token.strip().lower()
        if name in LAYERS:
            layers.add(name)
        elif name in PRESETS and "," not in raw:
            return preset_spec(name)
    return _spec_from_layers(layers, forced)


def default_filters() -> dict:
    spec = preset_spec("full")
    assert spec is not None
    body = spec.as_dict()
    body["format"] = 1
    body["visible"]["terrain"] = False
    return body


def normalize_filters(data: dict | None) -> dict:
    """A project file or a PUT body. Unknown keys are dropped."""
    raw = data if isinstance(data, dict) else {}
    preset_name = str(raw.get("preset") or "")
    layers_in = raw.get("layers")
    if isinstance(layers_in, dict):
        layers = {layer for layer in LAYERS if layers_in.get(layer) is True}
        textures = parse_textures_flag(raw.get("textures"))
        spec = _spec_from_layers(layers, True if textures is None else textures, "")
    elif preset_name in PRESETS:
        spec = preset_spec(preset_name)
        assert spec is not None
    else:
        spec = preset_spec("full")
        assert spec is not None
    visible_in = raw.get("visible") if isinstance(raw.get("visible"), dict) else {}
    visible = {layer: visible_in.get(layer) is not False for layer in LAYERS}
    return {
        "format": 1,
        "preset": spec.preset,
        "textures": spec.textures,
        "layers": {layer: layer in spec.layers for layer in LAYERS},
        "visible": visible,
    }


def spec_from_filters(data: dict | None) -> LoadSpec:
    normal = normalize_filters(data)
    layers = frozenset(layer for layer, on in normal["layers"].items() if on)
    return LoadSpec(layers, bool(normal["textures"]), str(normal["preset"]))


def _tagged_dict(node):
    if isinstance(node, list) and len(node) == 2 and node[0] == "dict" and isinstance(node[1], dict):
        return node[1]
    return None


def _tagged_list(node) -> list:
    if isinstance(node, list) and len(node) == 2 and node[0] == "array" and isinstance(node[1], list):
        return node[1]
    return []


def link_ids(row: dict) -> list[str]:
    """Placement hashes a link points at. Vanilla stores ``Dst``. Edits may use ``Hash``."""
    params = _tagged_dict(row.get("params"))
    if not params:
        return []
    found: list[str] = []
    for item in _tagged_list(params.get("Links")):
        fields = _tagged_dict(item)
        if not fields:
            continue
        for key in ("Dst", "Hash"):
            node = fields.get(key)
            if not (isinstance(node, list) and len(node) == 2 and node[0] in {"u64", "s32", "u32", "str"}):
                continue
            try:
                ident = format_hash(parse_hash(node[1]))
            except BancError:
                break
            if ident != format_hash(0):
                found.append(ident)
            break
    return found


def _strip_links(row: dict) -> dict:
    params = row.get("params")
    body = _tagged_dict(params)
    if body is None or "Links" not in body:
        return row
    copy = dict(row)
    kept = dict(body)
    kept.pop("Links", None)
    copy["params"] = [params[0], kept]
    copy.pop("links", None)
    return copy


def filter_scene_view(view: dict, spec: LoadSpec) -> dict:
    """Drop actors the editor should not mount. The diff is the full project diff."""
    rows = [row for row in (view.get("actors") or []) if isinstance(row, dict)]
    counts = {layer: 0 for layer in LAYERS}
    by_id: dict[str, dict] = {}
    for row in rows:
        layer = actor_layer(str(row.get("gyaml") or ""))
        counts[layer] += 1
        ident = row.get("id")
        if ident:
            by_id[str(ident)] = row
    loaded = spec.layers
    chosen: list[tuple[dict, str, bool]] = []
    have: set[str] = set()
    for row in rows:
        layer = actor_layer(str(row.get("gyaml") or ""))
        ident = str(row.get("id") or "")
        logic = False
        if layer in loaded:
            pass
        elif "behavior" in loaded and link_ids(row):
            logic = True
        else:
            continue
        chosen.append((row, layer, logic))
        if ident:
            have.add(ident)
    if "behavior" in loaded:
        for row in rows:
            for target in link_ids(row):
                if target in have or target not in by_id:
                    continue
                other = by_id[target]
                have.add(target)
                chosen.append((other, actor_layer(str(other.get("gyaml") or "")), True))
    actors = []
    for row, layer, logic in chosen:
        copy = dict(row)
        copy["sourceLayer"] = layer
        copy["logicOnly"] = logic
        copy["layer"] = "behavior" if logic else layer
        if logic or layer == "collision":
            copy["fetchModel"] = False
        if "behavior" in loaded:
            copy["links"] = link_ids(row)
        else:
            copy = _strip_links(copy)
        actors.append(copy)
    result = dict(view)
    result["actors"] = actors
    result["diff"] = view.get("diff") or {"actors": {}}
    result["load"] = {
        "layers": [layer for layer in LAYERS if layer in loaded],
        "textures": spec.textures,
        "preset": spec.preset,
        "counts": counts,
        "omitted": [layer for layer in LAYERS if layer not in loaded],
    }
    return result


def filter_attached(view: dict, spec: LoadSpec) -> dict:
    """Filter the host view and each companion. Companion diffs stay complete."""
    filtered = filter_scene_view(view, spec)
    companions = []
    for extra in view.get("companions") or []:
        if not isinstance(extra, dict):
            continue
        other = filter_scene_view(extra, spec)
        companions.append({
            "banc": extra.get("banc"),
            "actors": other["actors"],
            "removed": extra.get("removed") or [],
            "diff": extra.get("diff") or {"actors": {}},
        })
    if companions:
        filtered["companions"] = companions
    elif "companions" in view:
        filtered["companions"] = []
    return filtered


def keep_unloaded_edits(incoming: dict, stored: dict, loaded_layers) -> dict:
    """Edits for a layer that is off stay as they were stored.

    A payload that omits them, or sends a different record, cannot change
    them. The returned records for those actors are the stored objects.
    """
    loaded = set(loaded_layers or [])
    stored = stored if isinstance(stored, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    kept: dict = {}
    for ident, record in incoming.items():
        if not isinstance(record, dict):
            continue
        layer = record_layer(record)
        if layer not in loaded:
            if ident in stored and isinstance(stored[ident], dict):
                kept[ident] = stored[ident]
            continue
        kept[ident] = record
    for ident, record in stored.items():
        if ident in kept or not isinstance(record, dict):
            continue
        if record_layer(record) not in loaded:
            kept[ident] = record
    return kept


def preset_label(name: str) -> str:
    return _PRESET_LABELS.get(name, name)

"""Read-only behavior graph for one actor.

The dump's AINB, schedule, and event flow are the base. Each node, pin, and
edge keeps an id taken from that file (path, index, and name). Drawing the
graph again uses the same ids. A later edit is a typed op — ``add node``,
``connect``, or ``set field`` — stored beside the base, not a replaced file.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import dumps, loads

OPS = ("add node", "connect", "disconnect", "set field")

# Names the game uses, and the label the curated layer shows.
_FRIENDLY = {
    "ActorLogicSimpleActor": "Spawn actor",
    "HoldGameDataGetBool": "Read flag",
    "SetGameDataBool": "Set flag",
    "HoldGameDataGetInt": "Read counter",
    "OperateOR": "Or",
    "OperateAND": "And",
    "OperateNOT": "Not",
    "Filler": "Unused",
    "HoldChallengeCurrentStepCheckOrder": "Quest step",
    "Element_Sequential": "Sequence",
    "Element_Selector": "Pick one",
    "Element_Parallel": "Together",
    "ASPlay": "Play animation",
    "Demo_Talk": "Talk",
    "Demo_WaitFrame": "Wait",
    "Element_S32Selector": "Pick by number",
    "Element_Sequential": "Sequence",
    "Element_Simultaneous": "Together",
    "Element_F32Selector": "Pick by value",
    "Element_StringSelector": "Pick by text",
    "Element_RandomSelector": "Pick at random",
    "Element_BoolSelector": "Pick by flag",
    "Element_Fork": "Fork",
    "Element_Join": "Join",
    "Element_Alert": "Alert",
    "Element_Expression": "Expression",
    "Element_ModuleIF_Input_S32": "Module number in",
    "Element_ModuleIF_Input_F32": "Module value in",
    "Element_ModuleIF_Input_Vec3f": "Module vector in",
    "Element_ModuleIF_Input_String": "Module text in",
    "Element_ModuleIF_Input_Bool": "Module flag in",
    "Element_ModuleIF_Input_Ptr": "Module pointer in",
    "Element_ModuleIF_Output_S32": "Module number out",
    "Element_ModuleIF_Output_F32": "Module value out",
    "Element_ModuleIF_Output_Vec3f": "Module vector out",
    "Element_ModuleIF_Output_String": "Module text out",
    "Element_ModuleIF_Output_Bool": "Module flag out",
    "Element_ModuleIF_Output_Ptr": "Module pointer out",
    "Element_ModuleIF_Child": "Module child",
    "Element_StateEnd": "State end",
    "Element_SplitTiming": "Split timing",
}

# Event-flow entry names the game uses, and the label a person should see.
_ENTRY_NAMES = {
    "onplayertalk": "Talk",
    "ontalk": "Talk",
    "demotalk": "Talk",
    "talk": "Talk",
    "onplayernear": "Player near",
    "onnear": "Player near",
}

_PARAM_LISTS = (
    ("int", "int_inputs", "int_outputs"),
    ("bool", "bool_inputs", "bool_outputs"),
    ("float", "float_inputs", "float_outputs"),
    ("string", "string_inputs", "string_outputs"),
    ("vec3", "vec3f_inputs", "vec3f_outputs"),
    ("ptr", "ptr_inputs", "ptr_outputs"),
)
_PROP_LISTS = (
    "int_properties",
    "bool_properties",
    "float_properties",
    "string_properties",
    "vec3f_properties",
    "ptr_properties",
)
_KIND_ORDER = {"ainb": 0, "schedule": 1, "event": 2}


def mint_id(kind: str, taken: set[str]) -> str:
    """The next id of this kind. The same ``taken`` set returns the same id."""
    prefix = kind or "node"
    number = 1
    while f"{prefix}:{number}" in taken:
        number += 1
    return f"{prefix}:{number}"


def make_op(op: str, **fields) -> dict:
    """One typed graph edit. The id is one the project already minted."""
    if op not in OPS:
        names = ", ".join(OPS)
        raise ValueError(f"A graph edit is one of {names}.")
    body = {"op": op, **fields}
    if op == "add node" and not body.get("id"):
        raise ValueError("add node needs an id minted once.")
    if op == "connect" and not (body.get("id") and body.get("from") and body.get("to")):
        raise ValueError("connect needs an id, from, and to.")
    if op == "disconnect" and not (body.get("from") and body.get("to")):
        raise ValueError("disconnect needs from and to.")
    if op == "set field" and not (body.get("id") and body.get("field")):
        raise ValueError("set field needs an id and a field.")
    return body


def friendly_name(raw: str) -> tuple[str, bool]:
    """Curated label, and whether it came from the known-name list."""
    text = (raw or "").strip()
    if not text:
        return "Undecoded", False
    if text in _FRIENDLY:
        return _FRIENDLY[text], True
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).replace("_", " ")
    return spaced, False


def entry_label(raw: str) -> str:
    """Friendly name for an event-flow entry such as OnPlayerTalk."""
    text = (raw or "").strip()
    folded = re.sub(r"[^a-z0-9]", "", text.lower())
    if folded in _ENTRY_NAMES:
        return _ENTRY_NAMES[folded]
    if "talk" in folded:
        return "Talk"
    if folded.startswith("on") and len(text) > 2:
        return friendly_name(text[2:])[0]
    return friendly_name(text)[0]


def _field(name: str, value, type_name: str, *, editable: bool = True, options: list | None = None) -> dict:
    plain = _plain(value)
    kind = _field_type(name, type_name)
    return {
        "name": name,
        "value": plain,
        "base": plain,
        "type": kind,
        "editable": editable and kind in ("bool", "int", "float", "string", "flag", "enum"),
        "options": list(options or []),
    }


def _field_type(name: str, type_name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if key.endswith("flag") or "gamedata" in key or key in ("databool", "conditionflag"):
        return "flag"
    mapped = {"bool": "bool", "int": "int", "float": "float", "string": "string", "enum": "enum", "flag": "flag"}
    return mapped.get(type_name, "string")


def schedule_builtin_label(name: str) -> str | None:
    """Dummy and game__bsa__ / game__aischedule__ names are references, not missing files."""
    text = (name or "").replace("\\", "/").strip()
    base = text.split("/")[-1]
    folded = re.sub(r"[^a-z0-9]", "", base.lower())
    if not folded:
        return None
    if folded == "dummy" or folded.startswith("dummygamecomponentaischeduleparam"):
        return "Dummy schedule"
    # A .bgyml name is a file in some pack. game__bsa__ActionSeq__AIScheduleMoveTo is a type.
    low = base.lower()
    if low.endswith((".bgyml", ".bgyml.zs", ".gyml", ".gyml.zs")):
        return None
    if "gamebsa" not in folded and "gameaischedule" not in folded:
        return None
    for token, label in (
        ("threatreaction", "Threat reaction"),
        ("moveto", "Move to"),
        ("checktimer", "Check timer"),
        ("checkstate", "Check state"),
        ("specialparam", "Special param"),
    ):
        if token in folded:
            return label
    tail = re.sub(r"(?i)^aischedule", "", base.split("__")[-1])
    return friendly_name(tail or base)[0]


def _catalog_label(node: dict) -> str:
    """The builtin name a palette lists. UserDefined falls through to the node name."""
    type_name = str(node.get("type") or "")
    raw = str(node.get("raw") or "")
    if type_name and type_name not in ("UserDefined", "undecoded") and not type_name.startswith("node "):
        return type_name
    if raw and raw not in ("undecoded",) and not raw.startswith("node ") and " " not in raw:
        return raw
    return ""


def pin_signature(pins: list[dict]) -> str:
    """One line a palette can show: direction, type, and name for each pin."""
    parts = []
    for pin in pins or []:
        direction = "out" if pin.get("dir") == "out" else "in"
        kind = str(pin.get("type") or "flow")
        name = str(pin.get("name") or "")
        parts.append(f"{direction} {kind} {name}".strip())
    return " · ".join(parts)


def _pin_brief(pin: dict) -> dict:
    return {
        "dir": "out" if pin.get("dir") == "out" else "in",
        "type": str(pin.get("type") or "flow"),
        "name": str(pin.get("name") or ""),
    }


CUSTOM_EVENT = {
    "type": "CustomEvent",
    "name": "Custom Event",
    "count": 0,
    "kind": "event",
    "signature": "in flow in · out flow out",
    "pins": [
        {"dir": "in", "type": "flow", "name": "in"},
        {"dir": "out", "type": "flow", "name": "out"},
    ],
    "custom": True,
}


def node_type_catalog(dump) -> list[dict]:
    """Built-in node types from the NPC audit, plus Custom Event.

    The scan is cached on the dump. Opening the palette again does not reread every pack.
    """
    cached = getattr(dump, "_behavior_type_catalog", None)
    if cached is not None:
        return cached
    report = audit_npc_behavior(dump, progress=False)
    rows = [row for row in report["catalog"] if row["type"] not in ("UserDefined", "undecoded") and not str(row["type"]).startswith("node ")]
    if not any(row["type"] == "CustomEvent" for row in rows):
        rows.append(dict(CUSTOM_EVENT))
    dump._behavior_type_catalog = rows
    return rows


def audit_npc_behavior(dump, *, progress: bool = True) -> dict:
    """Count undecoded node types across every Npc_ pack."""
    packs = [name for name in dump.actor_names() if name.startswith("Npc_")]
    undecoded: dict[str, dict] = {}
    common: dict[str, dict] = {}
    errors = []
    node_total = 0
    catalog_buckets: dict[str, dict] = {}
    for index, gyaml in enumerate(packs, start=1):
        try:
            graph = load_actor_graph(dump, gyaml=gyaml)
        except Exception as exc:
            errors.append({"gyaml": gyaml, "error": getattr(exc, "message", None) or str(exc)})
            continue
        for node in graph["nodes"]:
            node_total += 1
            label = node.get("type") or node.get("raw") or "undecoded"
            bucket = common.setdefault(label, {"count": 0, "kind": node.get("kind") or "", "decoded": 0, "pins": None, "name": ""})
            bucket["count"] += 1
            if node.get("decoded"):
                bucket["decoded"] += 1
            if bucket["pins"] is None and node.get("pins"):
                bucket["pins"] = [_pin_brief(pin) for pin in node["pins"]]
            if not bucket["name"]:
                bucket["name"] = node.get("name") or friendly_name(label)[0]
            catalog_label = _catalog_label(node)
            if catalog_label:
                row = catalog_buckets.setdefault(
                    catalog_label, {"count": 0, "kind": node.get("kind") or "", "pins": None, "name": ""}
                )
                row["count"] += 1
                if row["pins"] is None and node.get("pins"):
                    row["pins"] = [_pin_brief(pin) for pin in node["pins"]]
                if not row["name"]:
                    row["name"] = node.get("name") or friendly_name(catalog_label)[0]
            if node.get("decoded"):
                continue
            missed = undecoded.setdefault(
                label, {"count": 0, "kind": node.get("kind") or "", "actors": []}
            )
            missed["count"] += 1
            if gyaml not in missed["actors"] and len(missed["actors"]) < 5:
                missed["actors"].append(gyaml)
        if progress and (index == len(packs) or index % 25 == 0):
            print(f"behavior-audit {index}/{len(packs)} {gyaml}", file=sys.stderr)
    missed_rows = [
        {"type": name, "count": row["count"], "kind": row["kind"], "actors": row["actors"]}
        for name, row in sorted(undecoded.items(), key=lambda pair: (-pair[1]["count"], pair[0]))
    ]
    ranked = sorted(common.items(), key=lambda pair: (-pair[1]["count"], pair[0]))
    common_rows = [
        {
            "type": name,
            "count": row["count"],
            "decoded": row["decoded"],
            "kind": row["kind"],
            "signature": pin_signature(row.get("pins") or []),
        }
        for name, row in ranked
    ]
    catalog = [
        {
            "type": name,
            "name": row["name"] or friendly_name(name)[0],
            "count": row["count"],
            "kind": row["kind"],
            "signature": pin_signature(row.get("pins") or []),
            "pins": list(row.get("pins") or []),
        }
        for name, row in sorted(catalog_buckets.items(), key=lambda pair: (-pair[1]["count"], pair[0]))[:240]
    ]
    return {
        "packs": len(packs),
        "read": len(packs) - len(errors),
        "nodes": node_total,
        "undecoded": sum(row["count"] for row in missed_rows),
        "undecodedTypes": missed_rows[:40],
        "types": common_rows[:40],
        "catalog": catalog,
        "errors": errors,
    }


def render_behavior_audit(report: dict) -> str:
    """Text form of an NPC behavior audit."""
    lines = [
        f"behavior-audit packs {report['packs']} read {report['read']}",
        f"nodes {report['nodes']}",
        f"undecoded {report['undecoded']}",
    ]
    if report["undecodedTypes"]:
        lines.append("undecoded types")
        for row in report["undecodedTypes"]:
            actors = ", ".join(row["actors"])
            lines.append(f"  {row['count']:6}  {row['kind'] or 'node':8}  {row['type']}  {actors}")
    else:
        lines.append("undecoded types: none")
    if report["types"]:
        lines.append("common types")
        for row in report["types"][:15]:
            lines.append(f"  {row['count']:6}  decoded {row['decoded']:6}  {row['type']}")
    for row in report["errors"][:10]:
        lines.append(f"error {row['gyaml']}: {row['error']}")
    return "\n".join(lines) + "\n"


def load_actor_graph(dump, *, gyaml: str = "", banc: str = "", actor_id: str = "") -> dict:
    """The base graph for this actor class, plus any path named on the placement."""
    actor = None
    if banc and actor_id:
        actor = _actor_in(dump, banc, actor_id)
        if actor is None:
            raise BancError(f"No actor {actor_id} in {banc}.", "actor")
        gyaml = str(actor.get("Gyaml") or gyaml)
    gyaml = (gyaml or "").strip()
    if not gyaml:
        raise BancError("Which actor? Pass the actor class, the name of Pack/Actor/<name>.pack.zs.", "actor")
    gyaml = _require_pack(dump, gyaml)
    files, trace = _collect(dump, gyaml, actor)
    graph = _graph(gyaml, files)
    graph["actor"] = actor_id or ""
    graph["banc"] = banc or ""
    graph["trace"] = trace
    if not graph["nodes"]:
        graph["note"] = "This actor's pack has no AI graph, schedule, or event flow."
    return graph


def _graph(gyaml: str, files: list[dict]) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    sources: list[dict] = []
    for item in files:
        source = {
            "id": f"src:{item['path']}",
            "kind": item["kind"],
            "path": item["path"],
            "title": item["title"],
            "status": item["status"],
            "note": item.get("note") or "",
            "sharedFrom": item.get("sharedFrom") or "",
            "sharedWith": list(item.get("sharedWith") or []),
            "warning": item.get("warning") or "",
        }
        sources.append(source)
        built = _read_source(item)
        for node in built["nodes"]:
            node["source"] = source["id"]
            nodes.append(node)
        edges.extend(built["edges"])
        source["count"] = len(built["nodes"])
        source["file"] = Path(item["path"]).name
        source["role"] = _source_role(item["path"], item["kind"], gyaml)
        source["entries"] = [
            {"id": node["id"], "name": node["name"], "raw": node["raw"]}
            for node in built["nodes"]
            if node.get("entry")
        ]
        source["openEntry"] = _pick_entry(source["entries"])
    sources.sort(key=lambda row: (_KIND_ORDER.get(row["kind"], 9), row["path"]))
    order = {row["id"]: index for index, row in enumerate(sources)}
    nodes.sort(key=lambda row: (order.get(row["source"], 9), row.get("index", 0), row["id"]))
    opened = _pick_source(sources, gyaml)
    return {
        "gyaml": gyaml,
        "actor": "",
        "banc": "",
        "readOnly": True,
        "ops": [],
        "opKinds": list(OPS),
        "sources": sources,
        "nodes": nodes,
        "edges": edges,
        "open": opened,
        "undecoded": sum(1 for node in nodes if not node.get("decoded")),
        "note": "",
        "trace": [],
    }


def _source_role(path: str, kind: str, gyaml: str) -> str:
    low = path.lower()
    actor = gyaml.lower()
    if kind == "event":
        return "event"
    if kind == "schedule":
        return "schedule"
    if kind == "ainb" and actor and actor in low and "logic" in low:
        return "root"
    if "utility" in low:
        return "utility"
    if "root" in low:
        return "root"
    return "ai"


def _pick_source(sources: list[dict], gyaml: str) -> str:
    """The actor's own logic module, else its root AI, else its event flow."""
    if not sources:
        return ""
    actor = gyaml.lower()

    def score(source: dict) -> tuple:
        path = source["path"].lower()
        own = bool(actor and actor in path)
        role = source.get("role") or ""
        if source["kind"] == "ainb" and own and "logic" in path:
            rank = 0
        elif role == "root" and own:
            rank = 1
        elif source["kind"] == "event" and own:
            rank = 2
        elif role == "root":
            rank = 3
        elif source["kind"] == "event":
            rank = 4
        else:
            rank = 5
        return (rank, path)

    return min(sources, key=score)["id"]


def _pick_entry(entries: list[dict]) -> str:
    """Root, then Utility, then a talk entry."""
    if not entries:
        return ""

    def score(entry: dict) -> tuple:
        raw = (entry.get("raw") or "").lower()
        name = (entry.get("name") or "").lower()
        if raw == "root":
            rank = 0
        elif "utility" in raw:
            rank = 1
        elif raw == "event" or name == "talk" or "talk" in raw:
            rank = 2
        else:
            rank = 5
        return (rank, raw)

    return min(entries, key=score)["id"]


def _read_source(item: dict) -> dict:
    if item["status"] != "ok" or item.get("data") is None:
        return {"nodes": [_undecoded(item)], "edges": []}
    kind = item["kind"]
    try:
        if kind == "ainb":
            return _ainb(item["path"], item["data"])
        if kind == "schedule":
            if _is_row_schedule(item["data"]):
                return _schedule(item["path"], item["data"])
            shared = _shared_container(item["path"], item["data"])
            if shared is not None:
                return shared
            raise ValueError("not an AIScheduleParam")
        if kind == "event":
            return _event(item["path"], item["data"])
    except Exception as exc:  # a file the library cannot read still has a node
        item = {**item, "status": "undecoded", "note": str(exc)}
        return {"nodes": [_undecoded(item)], "edges": []}
    return {"nodes": [_undecoded(item)], "edges": []}


def _undecoded(item: dict) -> dict:
    raw = Path(item["path"]).name or "undecoded"
    name, known = ("Undecoded", False)
    return _node(
        kind=item["kind"],
        path=item["path"],
        index=0,
        key="undecoded",
        raw=raw,
        name=name,
        known=known,
        decoded=False,
        note=item.get("note") or "This file did not decode.",
    )


def _node(*, kind, path, index, key, raw, name, known, decoded, fields=None, pins=None, note="", type_name="", entry=False) -> dict:
    return {
        "id": f"{kind}:{path}#{index}:{key}",
        "kind": kind,
        "index": index,
        "type": type_name,
        "raw": raw or "undecoded",
        "name": name,
        "known": known,
        "decoded": decoded,
        "entry": entry,
        "fields": fields or [],
        "pins": pins or [],
        "note": note,
    }


def _pin(node_id: str, direction: str, slot: int, name: str, type_name: str) -> dict:
    label = name or direction
    return {
        "id": f"{node_id}:{direction}:{type_name}:{slot}:{label}",
        "name": label,
        "dir": direction,
        "type": type_name,
    }


def _edge(src: str, dst: str, label: str = "") -> dict:
    return {"id": f"{src}>{dst}", "from": src, "to": dst, "label": label}


def _ainb(path: str, data: bytes) -> dict:
    from ainb import AINB

    graph = AINB.from_binary(data, reader_name=path)
    nodes = []
    by_index: dict[int, str] = {}
    pins_out: dict[tuple[int, int, str], str] = {}
    for index, node in enumerate(graph.nodes or []):
        type_name = getattr(getattr(node, "type", None), "name", "") or ""
        raw = str(getattr(node, "name", "") or "")
        if raw:
            name, known = friendly_name(raw)
            decoded = True
        elif type_name in _FRIENDLY:
            raw = type_name
            name, known = _FRIENDLY[type_name], True
            decoded = True
        elif type_name and type_name != "UserDefined":
            raw = type_name
            name, known = friendly_name(type_name)
            decoded = True
        else:
            raw = f"node {index}"
            name, known, decoded = "Undecoded", False, False
            type_name = type_name or "UserDefined"
        ident_key = raw or "undecoded"
        fields = _ainb_fields(node)
        built = _node(
            kind="ainb",
            path=path,
            index=index,
            key=ident_key,
            raw=raw,
            name=name,
            known=known,
            decoded=decoded,
            fields=fields,
            type_name=type_name,
        )
        pins = []
        for slot, plug in enumerate(getattr(node, "child_plugs", []) or []):
            label = str(getattr(plug, "name", "") or "") or "child"
            pins.append(_pin(built["id"], "out", 1000 + slot, label, "flow"))
        for slot, plug in enumerate(getattr(node, "generic_plugs", []) or []):
            label = str(getattr(plug, "name", "") or "") or "in"
            pins.append(_pin(built["id"], "in", 2000 + slot, label, "flow"))
        for type_name, inputs, outputs in _PARAM_LISTS:
            for slot, param in enumerate(getattr(node.params, outputs, []) or []):
                pin = _pin(built["id"], "out", slot, str(getattr(param, "name", "") or ""), type_name)
                pins.append(pin)
                pins_out[(index, slot, type_name)] = pin["id"]
            for slot, param in enumerate(getattr(node.params, inputs, []) or []):
                pins.append(_pin(built["id"], "in", slot, str(getattr(param, "name", "") or ""), type_name))
        built["pins"] = pins
        by_index[index] = built["id"]
        nodes.append(built)
    edges = []
    for index, node in enumerate(graph.nodes or []):
        for type_name, inputs, _outputs in _PARAM_LISTS:
            for slot, param in enumerate(getattr(node.params, inputs, []) or []):
                dst = _pin(by_index[index], "in", slot, str(getattr(param, "name", "") or ""), type_name)["id"]
                for source in _param_sources(param):
                    src_index = int(getattr(source, "src_node_index", -1))
                    src_slot = int(getattr(source, "src_output_index", 0))
                    if src_index < 0 or src_index not in by_index:
                        continue
                    src = pins_out.get((src_index, src_slot, type_name))
                    if src is None:
                        src = _pin(by_index[src_index], "out", src_slot, "out", type_name)["id"]
                    edges.append(_edge(src, dst))
        for slot, plug in enumerate(getattr(node, "child_plugs", []) or []):
            dst_index = int(getattr(plug, "node_index", -1))
            if dst_index not in by_index:
                continue
            label = str(getattr(plug, "name", "") or "") or "child"
            src = _pin(by_index[index], "out", 1000 + slot, label, "flow")["id"]
            dst = _flow_in(nodes[dst_index], by_index[dst_index])
            edges.append(_edge(src, dst, label))
        for slot, plug in enumerate(getattr(node, "generic_plugs", []) or []):
            src_index = int(getattr(plug, "node_index", -1))
            if src_index not in by_index:
                continue
            label = str(getattr(plug, "name", "") or "") or "in"
            src = _flow_out(nodes[src_index], by_index[src_index], label)
            dst = _pin(by_index[index], "in", 2000 + slot, label, "flow")["id"]
            edges.append(_edge(src, dst, label))
    for command in graph.commands or []:
        root = int(getattr(command, "root_node_index", -1))
        label = str(getattr(command, "name", "") or "Root")
        cmd = _node(
            kind="ainb",
            path=path,
            index=10000 + len(nodes),
            key=f"command:{label}",
            raw=label,
            name=f"Command {label}",
            known=True,
            decoded=True,
            type_name="Command",
            entry=True,
        )
        # Keep the command's index stable from the file, not from how many nodes we already drew.
        cmd["index"] = root if root >= 0 else 0
        cmd["id"] = f"ainb:{path}#command:{label}"
        nodes.append(cmd)
        if root in by_index:
            src = _pin(cmd["id"], "out", 0, "root", "flow")
            dst = _pin(by_index[root], "in", 0, "root", "flow")
            cmd["pins"] = [src]
            target = nodes[root]
            if not any(pin["id"] == dst["id"] for pin in target["pins"]):
                target["pins"].insert(0, dst)
            edges.append(_edge(src["id"], dst["id"], "root"))
    return {"nodes": nodes, "edges": edges}


def _ainb_fields(node) -> list[dict]:
    fields = []
    for type_name, inputs, _outputs in _PARAM_LISTS:
        if type_name in ("vec3", "ptr"):
            continue
        for param in getattr(node.params, inputs, []) or []:
            wired = _param_sources(param) and any(int(getattr(src, "src_node_index", -1)) >= 0 for src in _param_sources(param))
            value = getattr(param, "default_value", None)
            if value is None and not wired:
                continue
            fields.append(
                _field(str(getattr(param, "name", "") or type_name), "" if value is None else value, type_name, editable=not wired)
            )
    props = getattr(node, "properties", None)
    if props is not None:
        for list_name in _PROP_LISTS:
            type_name = list_name.split("_", 1)[0]
            if type_name in ("vec3f", "ptr"):
                continue
            mapped = {"int": "int", "bool": "bool", "float": "float", "string": "string"}.get(type_name, "string")
            for prop in getattr(props, list_name, []) or []:
                value = getattr(prop, "default_value", None)
                if value is None:
                    continue
                fields.append(_field(str(getattr(prop, "name", "") or "property"), value, mapped))
    return fields


def _flow_in(node: dict, node_id: str) -> str:
    pin = _pin(node_id, "in", 0, "in", "flow")
    if not any(item["id"] == pin["id"] for item in node["pins"]):
        node["pins"].insert(0, pin)
    return pin["id"]


def _flow_out(node: dict, node_id: str, label: str) -> str:
    pin = _pin(node_id, "out", 0, label or "out", "flow")
    if not any(item["id"] == pin["id"] for item in node["pins"]):
        node["pins"].append(pin)
    return pin["id"]


def _param_sources(param) -> list:
    source = getattr(param, "source", None)
    if source is None:
        return []
    if isinstance(source, list):
        return list(source)
    return [source]


def _schedule(path: str, data: bytes) -> dict:
    document = loads(data)
    if not isinstance(document, dict):
        raise ValueError("A schedule is a dictionary.")
    rows = document.get("ConditionalSchedule")
    if not isinstance(rows, list):
        rows = document.get("ActionSchedule") if isinstance(document.get("ActionSchedule"), list) else []
    nodes = []
    edges = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            nodes.append(
                _node(
                    kind="schedule",
                    path=path,
                    index=index,
                    key="undecoded",
                    raw=f"row {index}",
                    name="Undecoded",
                    known=False,
                    decoded=False,
                    note="This schedule row is not a dictionary.",
                )
            )
            continue
        flag = str(row.get("ConditionFlag") or "Always")
        row_node = _node(
            kind="schedule",
            path=path,
            index=index,
            key=flag,
            raw=flag,
            name=f"When {flag}",
            known=True,
            decoded=True,
            fields=[_field("ConditionFlag", flag, "flag")],
        )
        nodes.append(row_node)
        actions = row.get("ActionSchedule") if isinstance(row.get("ActionSchedule"), list) else []
        for slot, action in enumerate(actions):
            for name in _action_names(action):
                builtin = schedule_builtin_label(name)
                label, known = friendly_name(name)
                child = _node(
                    kind="schedule",
                    path=path,
                    index=index,
                    key=f"as:{slot}:{name}",
                    raw=name,
                    name=builtin or (label if known else f"Play {name}"),
                    known=True,
                    decoded=True,
                    type_name="builtin" if builtin else "Action",
                    fields=[_field("Setting", name, "string")],
                )
                child["id"] = f"schedule:{path}#{index}:as:{slot}:{name}"
                out_pin = _pin(row_node["id"], "out", slot, "then", "flow")
                in_pin = _pin(child["id"], "in", 0, "when", "flow")
                row_node["pins"].append(out_pin)
                child["pins"].append(in_pin)
                nodes.append(child)
                edges.append(_edge(out_pin["id"], in_pin["id"]))
        entries = row.get("EventEntry") if isinstance(row.get("EventEntry"), list) else []
        for slot, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            event_name = str(entry.get("EventName") or entry.get("$type") or "hook")
            hook = _node(
                kind="schedule",
                path=path,
                index=index,
                key=f"hook:{slot}:{event_name}",
                raw=event_name,
                name=f"Talk {event_name}",
                known=True,
                decoded=True,
                fields=[_field("EventName", event_name, "string")],
            )
            hook["id"] = f"schedule:{path}#{index}:hook:{slot}:{event_name}"
            out_pin = _pin(row_node["id"], "out", 20 + slot, "hook", "flow")
            in_pin = _pin(hook["id"], "in", 0, "when", "flow")
            row_node["pins"].append(out_pin)
            hook["pins"].append(in_pin)
            nodes.append(hook)
            edges.append(_edge(out_pin["id"], in_pin["id"], "event"))
    if not nodes:
        raise ValueError("This schedule has no rows.")
    return {"nodes": nodes, "edges": edges}


def _action_names(action) -> list[str]:
    if isinstance(action, str) and action:
        return [action]
    if not isinstance(action, dict):
        return []
    setting = action.get("Setting")
    if isinstance(setting, str) and setting:
        return [setting]
    if isinstance(setting, dict):
        names = []
        for key in ("ArrivalAS", "ResponseWaitAS", "WaitArrivalAS", "AS", "Name"):
            value = setting.get(key)
            if isinstance(value, str) and value:
                names.append(value)
        return names
    return []


def _event(path: str, data: bytes) -> dict:
    from evfl.evfl import EventFlow

    flow = EventFlow()
    flow.read(data)
    chart = flow.flowchart
    if chart is None:
        raise ValueError("This event flow has no flowchart.")
    nodes = []
    by_event: dict[int, str] = {}
    for index, event in enumerate(chart.events or []):
        raw, label, fields, decoded = _event_label(event)
        name, known = (label, True) if decoded else ("Undecoded", False)
        data = getattr(event, "data", None)
        event_type = type(data).__name__.replace("Event", "") if data is not None else ""
        built = _node(
            kind="event",
            path=path,
            index=index,
            key=str(getattr(event, "name", "") or f"event{index}"),
            raw=raw,
            name=name,
            known=known,
            decoded=decoded,
            fields=fields,
            type_name=event_type,
        )
        nodes.append(built)
        by_event[index] = built["id"]
    edges = []
    for index, event in enumerate(chart.events or []):
        data_node = getattr(event, "data", None)
        for slot, (nxt, label) in enumerate(_event_next(data_node)):
            if nxt not in by_event:
                continue
            src = _pin(by_event[index], "out", slot, label or "next", "flow")
            dst = _pin(by_event[nxt], "in", 0, "in", "flow")
            nodes[index]["pins"].append(src)
            if not any(pin["id"] == dst["id"] for pin in nodes[nxt]["pins"]):
                nodes[nxt]["pins"].append(dst)
            edges.append(_edge(src["id"], dst["id"], label))
    for index, entry in enumerate(chart.entry_points or []):
        label = str(getattr(entry, "name", "") or f"entry {index}")
        built = _node(
            kind="event",
            path=path,
            index=index,
            key=f"entry:{label}",
            raw=label,
            name=entry_label(label),
            known=True,
            decoded=True,
            type_name="Entry",
            entry=True,
            fields=[_field("entry", label, "string", editable=False)],
        )
        built["id"] = f"event:{path}#entry:{label}"
        nodes.append(built)
        target = getattr(getattr(entry, "main_event", None), "_idx", None)
        if isinstance(target, int) and target in by_event:
            src = _pin(built["id"], "out", 0, "calls", "flow")
            dst = _pin(by_event[target], "in", 0, "in", "flow")
            built["pins"].append(src)
            if not any(pin["id"] == dst["id"] for pin in next(node for node in nodes if node["id"] == by_event[target])["pins"]):
                next(node for node in nodes if node["id"] == by_event[target])["pins"].append(dst)
            edges.append(_edge(src["id"], dst["id"], "entry"))
    return {"nodes": nodes, "edges": edges}


def _event_label(event) -> tuple[str, str, list[dict], bool]:
    data = getattr(event, "data", None)
    if data is None:
        return (str(getattr(event, "name", "") or "event"), "Undecoded", [], False)
    kind = type(data).__name__.replace("Event", "") or "Action"
    action = _holder(getattr(data, "actor_action", None)) or _holder(getattr(data, "actor_query", None))
    structural = {
        "Switch": "Choose",
        "Fork": "Fork",
        "Join": "Join",
        "Return": "Return",
        "SubFlow": "Sub flow",
        "SubFlowSwitch": "Choose sub flow",
        "Action": "Action",
    }
    raw = action or str(getattr(event, "name", "") or kind)
    if action:
        friendly, known = friendly_name(action)
        if not known:
            friendly = f"Call {action}"
        if kind == "Switch":
            friendly = f"Choose {friendly}"
    else:
        friendly, known = structural.get(kind, kind), True
    fields = [_field("event", str(getattr(event, "name", "") or ""), "string", editable=False)]
    if action:
        fields.append(_field("action", action, "string"))
    params = getattr(getattr(data, "params", None), "data", None)
    if isinstance(params, dict):
        for key, value in params.items():
            if isinstance(value, (list, dict)):
                continue
            fields.append(_field(str(key), value, _py_field_type(value)))
    flow_name = str(getattr(data, "res_flowchart_name", "") or "")
    entry_name = str(getattr(data, "entry_point_name", "") or "")
    if flow_name or entry_name:
        fields.append(_field("flowchart", flow_name, "string"))
        fields.append(_field("entry", entry_name, "string"))
        friendly = f"Sub flow {entry_name or flow_name}"
    return raw, friendly, fields, True


def _py_field_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _event_next(data) -> list[tuple[int, str]]:
    if data is None:
        return []
    found = []
    nxt = getattr(data, "nxt", None)
    if nxt is not None and getattr(nxt, "_idx", 0xFFFF) not in (None, 0xFFFF):
        found.append((int(nxt._idx), "next"))
    join = getattr(data, "join", None)
    if join is not None and getattr(join, "_idx", None) not in (None, 0xFFFF):
        found.append((int(join._idx), "join"))
    for fork in getattr(data, "forks", []) or []:
        if getattr(fork, "_idx", None) not in (None, 0xFFFF):
            found.append((int(fork._idx), "fork"))
    cases = getattr(data, "cases", None) or {}
    if isinstance(cases, dict):
        for key, target in cases.items():
            if getattr(target, "_idx", None) not in (None, 0xFFFF):
                found.append((int(target._idx), str(key)))
    return found


def _holder(value) -> str:
    if value is None:
        return ""
    inner = getattr(value, "v", value)
    if inner is None:
        return ""
    named = getattr(inner, "v", None)
    if isinstance(named, str):
        return named
    if isinstance(inner, str):
        return inner
    return ""


def _require_pack(dump, gyaml: str) -> str:
    """The dump's spelling of this actor, or an error that names the nearest packs."""
    names = dump.actor_names()
    for name in names:
        if name == gyaml or name.lower() == gyaml.lower():
            return name
    suggestions = suggest_actors(gyaml, names)
    listed = ", ".join(suggestions) if suggestions else "none"
    raise BancError(
        f"No actor pack for {gyaml}. Looked for Pack/Actor/{gyaml}.pack.zs. Close matches: {listed}.",
        "actor",
    )


def suggest_actors(name: str, names: list[str], limit: int = 8) -> list[str]:
    """Pack names that share a token or nearly spell this one."""
    import difflib

    folded = name.lower()
    token = folded.split("_")[-1] if "_" in folded else folded
    scored: list[tuple[int, str]] = []
    for item in names:
        low = item.lower()
        score = 0
        if folded and folded in low:
            score += 100
        if token and len(token) >= 4 and token in low:
            score += 50
        ratio = max(
            difflib.SequenceMatcher(None, folded, low).ratio(),
            difflib.SequenceMatcher(None, low, folded).ratio(),
        )
        if ratio >= 0.78:
            score += 40
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    found: list[str] = []
    for item in [pair[1] for pair in scored]:
        if item not in found:
            found.append(item)
        if len(found) >= limit:
            break
    return found


def _collect(dump, gyaml: str, actor: dict | None) -> tuple[list[dict], list[str]]:
    """Pack components, then the files After the Ocarina reads for this actor.

    A schedule is ``Component/AIScheduleParam/<actor>.game__component__AIScheduleParam.bgyml``.
    A talk flow is ``Event/EventFlow/<actor>.bfevfl.zs``, also reached from a
    component's ``FlowchartFileName`` (``Work/Event/EventFlow/<actor>.evfc``)
    or from a schedule ``EventName`` that points at an EventSetting gyml.
    An AI graph is an ``.ainb`` named by an AIInfo ``AIProgram``, or a logic
    module ``Logic/*<actor>*.logic.module.ainb``.
    """
    found: list[dict] = []
    seen: set[str] = set()
    logged: set[str] = set()
    trace: list[str] = []
    queue: list[tuple[str, str, str]] = []
    pack_rel = f"Pack/Actor/{gyaml}.pack.zs"
    if not (dump.romfs / pack_rel).is_file():
        trace.append(f"pack {pack_rel} missing")
    else:
        trace.append(f"pack {pack_rel}")
        _read_pack(dump, pack_rel, found, seen, trace, queue)
    if actor:
        for field, value, role in _refs_in(actor):
            if role:
                queue.append((role, value, f"placement {field or 'param'}"))
                trace.append(f"reference {field or 'param'}={value} from placement role={role}")
    for role, relative in _convention_paths(gyaml):
        queue.append((role, relative, "actor name"))
    _drain(dump, found, seen, logged, trace, queue)
    for folder, suffixes, role in (
        ("Logic", (".ainb",), "ainb"),
        ("AI", (".ainb",), "ainb"),
        ("Event/EventFlow", (".bfevfl", ".bfevfl.zs", ".evfl", ".evfl.zs"), "event"),
        ("Component/AIScheduleParam", (".bgyml", ".bgyml.zs"), "schedule"),
    ):
        hits = _files_named_like(dump, folder, gyaml, suffixes)
        if hits:
            trace.append(f"scan {folder}/ for {gyaml}: {', '.join(hits)}")
        else:
            trace.append(f"scan {folder}/ for {gyaml}: none")
        for relative in hits:
            queue.append((role, relative, f"name match in {folder}/"))
    _drain(dump, found, seen, logged, trace, queue)
    _annotate_shared(dump, gyaml, found)
    return found, trace


def _read_pack(dump, pack_rel: str, found: list[dict], seen: set[str], trace: list[str], queue: list) -> None:
    try:
        payload, _dict_id = dump.read_file(pack_rel)
    except Exception as exc:
        trace.append(f"pack {pack_rel} rejected {exc}")
        return
    try:
        from ocarina_forge.formats.sarc import Sarc

        archive = Sarc(payload)
    except ValueError as exc:
        trace.append(f"pack {pack_rel} rejected not a SARC ({exc})")
        return
    for name in archive.names():
        blob = archive.read(name)
        member = f"{pack_rel}:{name}"
        kind = _sniff(name, blob)
        if kind:
            trace.append(f"component {name} {_magic_label(blob)} opened {kind}")
            _add_file(found, seen, kind, member, blob)
            continue
        if blob[:2] == b"YB":
            try:
                document = loads(blob)
            except ValueError as exc:
                trace.append(f"component {name} byml rejected {exc}")
                continue
            trace.append(f"component {name} {_describe_byml(document)}")
            if _is_schedule(document):
                trace.append(f"component {name} opened schedule")
                _add_file(found, seen, "schedule", member, blob)
                _enqueue_document(blob, "schedule", name, queue, trace)
            for field, value, role in _refs_in(document):
                if not role:
                    continue
                trace.append(f"reference {field}={value} from {name} role={role}")
                queue.append((role, value, name))
            continue
        trace.append(f"component {name} {_magic_label(blob)} skipped not a behavior file")


def _drain(dump, found: list[dict], seen: set[str], logged: set[str], trace: list[str], queue: list) -> None:
    while queue:
        role, text, why = queue.pop(0)
        _try_reference(dump, found, seen, logged, trace, queue, role, text, why)


def _try_reference(dump, found, seen, logged, trace, queue, role: str, text: str, why: str) -> None:
    if _reference_only(text):
        trace.append(f"reference {text} reference only ({why})")
        return
    builtin = schedule_builtin_label(text)
    if builtin:
        trace.append(f"reference {text} known reference {builtin} ({why})")
        return
    candidates = _candidates(text, role)
    if candidates and candidates[0] in seen:
        return
    tried = False
    for relative in candidates:
        if relative in seen or relative in logged:
            tried = True
            continue
        logged.add(relative)
        blob, reason = _open_relative(dump, relative)
        if blob is None:
            trace.append(f"try {relative} rejected {reason} ({why})")
            continue
        kind, reject = _accept(role, relative, blob)
        tried = True
        if kind is None:
            trace.append(f"try {relative} rejected {reject} ({why})")
            if blob[:2] == b"YB":
                _enqueue_document(blob, "", relative, queue, trace)
            continue
        trace.append(f"try {relative} opened {kind} ({why})")
        _add_file(found, seen, kind, relative, blob)
        _enqueue_document(blob, kind, relative, queue, trace)
        return
    shared = _open_shared_schedule(dump, text)
    if shared is not None:
        blob, path, pack = shared
        if path not in seen:
            trace.append(f"try {path} opened schedule shared from {pack} ({why})")
            _add_file(found, seen, "schedule", path, blob)
            _enqueue_document(blob, "schedule", path, queue, trace)
        return
    named = why == "actor name" or why.startswith("name match")
    if not tried and not named and _explicit_file(text):
        trace.append(f"reference {text} missing ({why})")
        hinted = role if role in ("ainb", "schedule", "event") else (_kind_for(text) or "ainb")
        _add_missing(found, seen, hinted, text.replace("\\", "/"), "This path is not in the dump.")


def _enqueue_document(blob: bytes, kind: str, where: str, queue: list, trace: list[str]) -> None:
    if blob[:2] != b"YB":
        return
    try:
        document = loads(blob)
    except ValueError:
        return
    if kind == "schedule" or _is_schedule(document):
        for field, value, role in _refs_in(document):
            if role == "event" or "event" in field.lower():
                queue.append(("event", value, f"{where} {field}"))
                trace.append(f"reference {field}={value} from {where} role=event")
    for field, value, role in _refs_in(document):
        if field.lower() in ("flowchartfilename", "eventflow") and role:
            queue.append((role, value, f"{where} {field}"))


def _convention_paths(gyaml: str) -> list[tuple[str, str]]:
    rows = [
        ("schedule", f"Component/AIScheduleParam/{gyaml}.game__component__AIScheduleParam.bgyml"),
        ("schedule", f"Component/AIScheduleParam/{gyaml}.game__component__AIScheduleParam.bgyml.zs"),
        ("event", f"Event/EventFlow/{gyaml}.bfevfl.zs"),
        ("event", f"Event/EventFlow/{gyaml}.bfevfl"),
        ("event", f"Event/EventFlow/{gyaml}.evfl.zs"),
        ("event", f"Event/EventFlow/{gyaml}.evfl"),
        ("ainb", f"AI/{gyaml}.ainb"),
        ("ainb", f"AI/{gyaml}.work.ainb"),
        ("ainb", f"AI/{gyaml}.root.ainb"),
        ("ainb", f"Logic/{gyaml}.logic.module.ainb"),
        ("ainb", f"Logic/{gyaml}.ainb"),
    ]
    return rows


def _files_named_like(dump, folder: str, gyaml: str, suffixes: tuple[str, ...]) -> list[str]:
    root = dump.romfs / folder
    if not root.is_dir():
        return []
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if not any(lower.endswith(suffix) for suffix in suffixes):
            continue
        if _actor_in_name(path.name, gyaml):
            hits.append(path.relative_to(dump.romfs).as_posix())
    return hits


def _actor_in_name(filename: str, gyaml: str) -> bool:
    low = filename.lower()
    token = gyaml.lower()
    start = 0
    while True:
        at = low.find(token, start)
        if at < 0:
            return False
        after = at + len(token)
        if after >= len(low) or not low[after].isalnum():
            return True
        start = at + 1


def _open_relative(dump, relative: str) -> tuple[bytes | None, str]:
    path = dump.romfs / relative
    if not path.is_file():
        return None, "not in the dump"
    try:
        blob, _dict_id = dump.read_file(relative)
    except Exception as exc:
        return None, str(exc)
    return blob, ""


def _accept(role: str, relative: str, blob: bytes) -> tuple[str | None, str]:
    sniffed = _sniff(relative, blob)
    if role == "ainb":
        if sniffed == "ainb":
            return "ainb", ""
        if blob[:2] == b"YB":
            return None, "BYML, not an AINB"
        return None, f"magic {blob[:4]!r} is not AIB"
    if role == "event":
        if sniffed == "event":
            return "event", ""
        if blob[:2] == b"YB":
            return None, "BYML event setting, not a flowchart"
        return None, f"magic {blob[:4]!r} is not BFEVFL"
    if role == "schedule":
        if sniffed == "schedule":
            return "schedule", ""
        if blob[:2] == b"YB":
            try:
                document = loads(blob)
            except ValueError as exc:
                return None, str(exc)
            if _is_schedule(document):
                return "schedule", ""
        return None, "not an AIScheduleParam"
    if sniffed:
        return sniffed, ""
    if blob[:2] == b"YB":
        try:
            document = loads(blob)
        except ValueError as exc:
            return None, str(exc)
        if _is_schedule(document):
            return "schedule", ""
    return None, "not an AI graph, schedule, or event flow"


def _add_file(found: list[dict], seen: set[str], kind: str, path: str, data: bytes) -> None:
    if path in seen:
        return
    seen.add(path)
    found.append(
        {
            "kind": kind,
            "path": path,
            "title": _title(kind),
            "status": "ok",
            "data": data,
            "note": "",
        }
    )


def _is_row_schedule(data: bytes) -> bool:
    if data[:2] != b"YB":
        return False
    try:
        document = loads(data)
    except ValueError:
        return False
    return _is_schedule(document)


def _shared_container(path: str, data: bytes) -> dict | None:
    """A ConditionContainer (or any other schedule BYML) still counts as the file we found."""
    try:
        document = loads(data)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    fields = []
    for key, value in document.items():
        if isinstance(value, (list, dict)) or value is None:
            continue
        fields.append(_field(str(key), value, _py_field_type(value)))
        if len(fields) >= 8:
            break
    label = Path(path).name
    friendly, known = friendly_name(label)
    return {
        "nodes": [
            _node(
                kind="schedule",
                path=path,
                index=0,
                key=label,
                raw=label,
                name=friendly if known else label,
                known=True,
                decoded=True,
                type_name="shared",
                fields=fields,
            )
        ],
        "edges": [],
    }


def _is_shared_schedule_name(text: str) -> bool:
    low = text.replace("\\", "/").lower()
    return "aischeduleparam" in low or "conditioncontainer" in low


def _schedule_lookup_keys(text: str) -> list[str]:
    base = text.replace("\\", "/").strip().split("/")[-1]
    keys: list[str] = []

    def add(item: str) -> None:
        folded = item.lower()
        if folded and folded not in keys:
            keys.append(folded)

    add(base)
    stem = base
    for suffix in (".bgyml.zs", ".bgyml", ".gyml", ".zs"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    add(stem)
    marker = ".game__"
    at = stem.lower().find(marker)
    if at > 0:
        add(stem[:at])
    return keys


def schedule_index(dump) -> dict:
    """Stem to the pack member that holds it, and the actors whose BYML names that file.

    Built once per dump. A later actor reuses the same index.
    """
    cached = getattr(dump, "_behavior_schedule_index", None)
    if cached is not None:
        return cached
    files: dict[str, list[dict]] = {}
    users: dict[str, list[str]] = {}
    names = dump.actor_names()
    for index, gyaml in enumerate(names, start=1):
        pack_rel = f"Pack/Actor/{gyaml}.pack.zs"
        try:
            archive = _open_pack_archive(dump, pack_rel)
        except Exception:
            continue
        for member in archive.names():
            if _is_shared_schedule_name(member):
                record = {"pack": gyaml, "pack_rel": pack_rel, "member": member}
                for key in _schedule_lookup_keys(member):
                    rows = files.setdefault(key, [])
                    if not any(row["pack"] == gyaml and row["member"] == member for row in rows):
                        rows.append(record)
            blob = _member_bytes(dump, archive, member)
            _note_schedule_users(users, gyaml, blob)
        if index == len(names) or index % 200 == 0:
            print(f"schedule index {index}/{len(names)} {gyaml}", file=sys.stderr)
    for bucket in users.values():
        bucket.sort()
    cached = {"files": files, "users": users}
    dump._behavior_schedule_index = cached
    print(f"schedule index {len(names)} packs, {len(files)} names", file=sys.stderr)
    return cached


def _note_schedule_users(users: dict[str, list[str]], gyaml: str, blob: bytes) -> None:
    if blob[:2] not in (b"YB", b"BY"):
        return
    folded = blob.lower()
    if b"aischeduleparam" not in folded and b"conditioncontainer" not in folded:
        return
    try:
        from ocarina_forge.validate import byml_strings

        strings = byml_strings(blob)
    except Exception:
        return
    for text in strings:
        if not _is_shared_schedule_name(text):
            continue
        for key in _schedule_lookup_keys(text):
            bucket = users.setdefault(key, [])
            if gyaml not in bucket:
                bucket.append(gyaml)


def _open_pack_archive(dump, pack_rel: str):
    from ocarina_forge.formats.sarc import Sarc

    payload, _dict_id = dump.read_file(pack_rel)
    return Sarc(payload)


def _member_bytes(dump, archive, member: str) -> bytes:
    from ocarina_forge.formats.zsio import decompress, is_zstd

    blob = archive.read(member)
    if is_zstd(blob):
        blob, _dict_id = decompress(blob, dump.dicts())
    return blob


def _pick_schedule(index: dict, text: str) -> dict | None:
    keys = _schedule_lookup_keys(text)
    files = index["files"]
    head = keys[-1] if keys else ""
    base = keys[0] if keys else ""
    for key in keys:
        choices = list(files.get(key) or [])
        if not choices:
            continue
        exact = [row for row in choices if row["member"].split("/")[-1].lower() == base]
        pool = exact or choices
        owned = [row for row in pool if row["pack"].lower() == head]
        if owned:
            return owned[0]
        if len(pool) == 1:
            return pool[0]
    return None


def _fallback_schedule_pack(dump, text: str) -> dict | None:
    keys = _schedule_lookup_keys(text)
    head = keys[-1] if len(keys) > 1 else ""
    if not head:
        return None
    pack = next((name for name in dump.actor_names() if name.lower() == head), "")
    if not pack:
        return None
    pack_rel = f"Pack/Actor/{pack}.pack.zs"
    try:
        archive = _open_pack_archive(dump, pack_rel)
    except Exception:
        return None
    base = keys[0]
    exact = [name for name in archive.names() if name.split("/")[-1].lower() == base]
    if not exact:
        exact = [name for name in archive.names() if _is_shared_schedule_name(name)]
        if len(exact) != 1:
            return None
    return {"pack": pack, "pack_rel": pack_rel, "member": exact[0]}


def _open_shared_schedule(dump, text: str) -> tuple[bytes, str, str] | None:
    if not _is_shared_schedule_name(text):
        return None
    index = schedule_index(dump)
    hit = _pick_schedule(index, text) or _fallback_schedule_pack(dump, text)
    if hit is None:
        return None
    try:
        archive = _open_pack_archive(dump, hit["pack_rel"])
        blob = _member_bytes(dump, archive, hit["member"])
    except Exception:
        return None
    if blob[:2] != b"YB":
        return None
    return blob, f"{hit['pack_rel']}:{hit['member']}", hit["pack"]


def _annotate_shared(dump, gyaml: str, found: list[dict]) -> None:
    index = schedule_index(dump)
    for item in found:
        path = item.get("path") or ""
        if ":" not in path:
            continue
        member = path.split(":", 1)[1]
        base = member.split("/")[-1]
        if not _is_shared_schedule_name(base):
            continue
        pack = path.split(":", 1)[0].rsplit("/", 1)[-1].removesuffix(".pack.zs")
        users = []
        for key in _schedule_lookup_keys(base):
            for name in index["users"].get(key) or []:
                if name not in users:
                    users.append(name)
        affected = []
        for name in [pack, *users]:
            if name and name not in affected:
                affected.append(name)
        others = [name for name in affected if name != gyaml]
        if pack != gyaml:
            item["sharedFrom"] = pack
            item["note"] = f"shared from {pack}"
        if others:
            item["sharedWith"] = affected
            item["warning"] = f"Editing it changes {', '.join(affected)}."


def _add_missing(found: list[dict], seen: set[str], kind: str, path: str, note: str) -> None:
    if path in seen:
        return
    seen.add(path)
    found.append({"kind": kind, "path": path, "title": _title(kind), "status": "missing", "data": None, "note": note})


def _title(kind: str) -> str:
    return {"ainb": "AI graph", "schedule": "Schedule", "event": "Event flow"}.get(kind, "Behavior")


def _sniff(name: str, data: bytes) -> str | None:
    lower = name.lower()
    if data[:4] == b"AIB " or lower.endswith(".ainb"):
        return "ainb"
    if data[:8] == b"BFEVFL\x00\x00" or lower.endswith((".bfevfl", ".bfevfl.zs", ".evfl", ".evfl.zs")):
        return "event"
    if "aischedule" in lower:
        return "schedule"
    return None


def _kind_for(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith(".ainb") or ".ainb" in lower:
        return "ainb"
    if lower.endswith((".bfevfl", ".evfl", ".evfc")) or any(token in lower for token in (".bfevfl", ".evfl", ".evfc")):
        return "event"
    if "aischedule" in lower:
        return "schedule"
    return None


_AI_KEYS = {
    "aiprogram",
    "aiprogramuser",
    "residentai",
    "residentprogram",
    "logic",
    "program",
    "aipath",
    "ainame",
    "aifile",
}
_EVENT_KEYS = {
    "flowchartfilename",
    "eventflow",
    "eventname",
    "flowname",
    "eventfile",
    "flowchart",
    "flowfile",
}
_SCHEDULE_KEYS = {"schedule", "aischedule", "actionschedule"}


def _describe_byml(document) -> str:
    if not isinstance(document, dict):
        return type(document).__name__
    kind = str(document.get("$type") or "")
    parent = str(document.get("$parent") or "")
    keys = ",".join(str(key) for key in list(document)[:12])
    bits = ["byml"]
    if kind:
        bits.append(f"$type={kind}")
    if parent:
        bits.append(f"$parent={parent}")
    bits.append(f"keys={keys}")
    return " ".join(bits)


def _magic_label(blob: bytes) -> str:
    if blob[:4] == b"AIB ":
        return "ainb"
    if blob[:8] == b"BFEVFL\x00\x00":
        return "bfevfl"
    if blob[:2] == b"YB":
        return "byml"
    if blob[:4] == b"SARC":
        return "sarc"
    return f"magic {blob[:4]!r}"


def _refs_in(document) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []

    def walk(value, field: str) -> None:
        if isinstance(value, str):
            role = _role_of(field, value)
            if role:
                found.append((field, value, role))
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, str(key))
            return
        if isinstance(value, list):
            for item in value:
                walk(item, field)

    walk(document, "")
    return found


def _role_of(field: str, value: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", field.lower())
    low = value.replace("\\", "/").lower()
    if key in _SCHEDULE_KEYS or "aischedule" in low:
        return "schedule"
    if key in _EVENT_KEYS or any(token in low for token in (".bfevfl", ".evfl", ".evfc", "eventflow/", "eventsetting/")):
        return "event"
    if key in _AI_KEYS or ".ainb" in low or low.startswith("ai/") or low.startswith("logic/") or "/logic/" in low or ".logic.module" in low:
        return "ainb"
    return ""


def _reference_only(text: str) -> bool:
    """Work/AI/Root and Work/BSA paths are editor sources, not files in the romfs."""
    raw = text.replace("\\", "/").strip().lower()
    if "work/ai/root/" in raw or raw.startswith("ai/root/"):
        return True
    if "work/bsa/" in raw or raw.startswith("bsa/"):
        return True
    base = raw.rsplit("/", 1)[-1]
    return base.endswith(".ain") or base.endswith(".ain.zs")


def _explicit_file(text: str) -> bool:
    low = text.replace("\\", "/").lower()
    return any(token in low for token in (".ainb", ".bfevfl", ".evfl", ".evfc", "aischedule"))


def _is_schedule(document) -> bool:
    return isinstance(document, dict) and (
        isinstance(document.get("ConditionalSchedule"), list) or isinstance(document.get("ActionSchedule"), list)
    )


def _schedule_paths(text: str) -> list[str]:
    """Filenames such as Npc_Hylia_Talon_Child.game__component__AIScheduleParam.bgyml live in one folder."""
    raw = text.replace("\\", "/").strip().lstrip("/")
    if raw.startswith("Work/"):
        raw = raw[len("Work/") :]
    base = raw.split("/")[-1]
    low = base.lower()
    if "aischeduleparam" not in low and "aischedule" not in low:
        return []
    names = [f"Component/AIScheduleParam/{base}"]
    if low.endswith(".gyml"):
        names.append(f"Component/AIScheduleParam/{base[:-5]}.bgyml")
        names.append(f"Component/AIScheduleParam/{base[:-5]}.bgyml.zs")
    if not low.endswith(".zs"):
        names.append(f"Component/AIScheduleParam/{base}.zs")
    stem = base
    for suffix in (".bgyml.zs", ".bgyml", ".gyml", ".zs"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    marker = ".game__component__aischeduleparam"
    if stem.lower().endswith(marker):
        stem = stem[: -len(marker)]
    if stem and stem != base:
        names.append(f"Component/AIScheduleParam/{stem}.game__component__AIScheduleParam.bgyml")
        names.append(f"Component/AIScheduleParam/{stem}.game__component__AIScheduleParam.bgyml.zs")
    return names


def _candidates(text: str, role: str) -> list[str]:
    raw = text.replace("\\", "/").strip().lstrip("/")
    if raw.startswith("Work/"):
        raw = raw[len("Work/") :]
    names: list[str] = []

    def add(item: str) -> None:
        if item and item not in names and ".." not in item.split("/"):
            names.append(item)

    looks_like_file = "/" in raw or raw.endswith(
        (".ainb", ".bfevfl", ".evfl", ".evfc", ".bgyml", ".gyml", ".zs")
    )
    if looks_like_file:
        add(raw)
        if raw.endswith(".zs"):
            add(raw[:-3])
        else:
            add(raw + ".zs")
        if raw.endswith(".gyml"):
            add(raw[:-5] + ".bgyml")
            add(raw[:-5] + ".bgyml.zs")
        if raw.endswith(".evfc"):
            add(raw[:-5] + ".bfevfl")
            add(raw[:-5] + ".bfevfl.zs")
        for extra in _schedule_paths(raw):
            add(extra)
        return names
    base = raw.split("/")[-1]
    if base.endswith(".zs"):
        base = base[:-3]
    for suffix in (".gyml", ".bgyml", ".evfc", ".bfevfl", ".evfl", ".ainb"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if ".engine__" in base:
        base = base.split(".engine__", 1)[0]
    if role in ("ainb", ""):
        add(f"AI/{base}.work.ainb")
        add(f"AI/{base}.root.ainb")
        add(f"AI/{base}.ainb")
        add(f"Logic/{base}.logic.module.ainb")
    if role in ("event", ""):
        add(f"Event/EventFlow/{base}.bfevfl.zs")
        add(f"Event/EventFlow/{base}.bfevfl")
        add(f"Event/EventFlow/{base}.evfl.zs")
        add(f"Event/EventFlow/{base}.evfl")
    if role in ("schedule", ""):
        add(f"Component/AIScheduleParam/{base}.game__component__AIScheduleParam.bgyml")
        add(f"Component/AIScheduleParam/{base}.game__component__AIScheduleParam.bgyml.zs")
        for extra in _schedule_paths(raw):
            add(extra)
    return names


def _actor_in(dump, banc: str, actor_id: str):
    from ocarina_forge.actors import actor_id as format_id

    document, _dict_id, _raw = dump.read_byml(banc)
    for actor in document.get("Actors") or []:
        if isinstance(actor, dict) and format_id(actor) == actor_id:
            return actor
    return None


def _plain(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return str(value)


def sample_ai_bytes() -> bytes:
    """A four-node module shaped like an actor AI file. It is not a game file."""
    from ainb import AINB
    from ainb.blackboard import Blackboard
    from ainb.command import Command
    from ainb.node import Node, NodeFlag, NodeType
    from ainb.param import InputParam, OutputParam, ParamSource
    from ainb.param_common import ParamFlag, ParamType
    from ainb.property import Property

    graph = AINB()
    graph.version = 0x407
    graph.filename = "Npc_Kokiri_F_01.root"
    graph.category = "AI"
    graph.blackboard = Blackboard()
    graph.exists_section_0x6c = True
    command = Command()
    command.name = "Root"
    command.guid = "11111111-1111-1111-1111-111111111111"
    command.root_node_index = 1
    graph.commands.append(command)

    def make(name: str) -> Node:
        node = Node(NodeType.UserDefined)
        node.name = name
        node.flags = NodeFlag(1 if name else 0)
        node.guid = "22222222-2222-2222-2222-222222222222"
        return node

    flag = make("HoldGameDataGetBool")
    spawn = make("ActorLogicSimpleActor")
    mystery = make("MysteryNode")
    blank = make("")
    graph.nodes = [flag, spawn, mystery, blank]
    for index, node in enumerate(graph.nodes):
        node.index = index
    output = OutputParam(ParamType.Bool)
    output.name = "Bool"
    output.is_output = True
    flag.params.bool_outputs.append(output)
    data_bool = InputParam(ParamType.String)
    data_bool.name = "DataBool"
    data_bool.default_value = "Kokiri_FadoMet"
    data_bool.source = ParamSource(-1, 0, ParamFlag())
    flag.params.string_inputs.append(data_bool)
    created = InputParam(ParamType.Bool)
    created.name = "Logic_Create"
    created.default_value = False
    created.source = ParamSource(0, 0, ParamFlag())
    spawn.params.bool_inputs.append(created)
    prop = Property(ParamType.String)
    prop.name = "InstanceName"
    prop.default_value = "Fado"
    prop.flags = ParamFlag()
    spawn.properties.string_properties.append(prop)
    return graph.to_binary()


def sample_schedule_bytes() -> bytes:
    """One day row, one night row, and a row that is not a dictionary."""
    return dumps(
        {
            "BaseSchedule": "Npc_Kokiri_F_01",
            "ConditionalSchedule": [
                {
                    "ConditionFlag": "ChildDay",
                    "ActionSchedule": [{"Setting": "Act_Wait"}],
                    "EventEntry": [
                        {
                            "EventName": "Npc_Kokiri_F_01_Talk",
                            "$type": "game__aischedule__OnPlayerTalk",
                        }
                    ],
                },
                {"ConditionFlag": "Night"},
                "undecoded-row",
            ],
        }
    )


def sample_flow_bytes() -> bytes:
    """One entry point that calls a talk action. It is not a game file."""
    from evfl.actor import Actor
    from evfl.common import ActorIdentifier, StringHolder
    from evfl.entry_point import EntryPoint
    from evfl.event import ActionEvent, Event
    from evfl.evfl import EventFlow
    from evfl.flowchart import Flowchart
    from evfl.util import make_index, make_rindex

    flow = EventFlow()
    flow.name = "Npc_Kokiri_F_01"
    chart = Flowchart()
    chart.name = "Npc_Kokiri_F_01"
    actor = Actor()
    actor.identifier = ActorIdentifier("Npc_Kokiri_F_01")
    actor.concurrent_clips = 1
    talk = StringHolder("Demo_Talk")
    actor.actions.append(talk)
    chart.actors.append(actor)
    done = Event()
    done.name = "Talk"
    done.data = ActionEvent()
    done.data.actor = make_rindex(actor)
    done.data.actor_action = make_rindex(talk)
    done.data.nxt = make_index(None)
    chart.events.append(done)
    entry = EntryPoint("OnPlayerTalk")
    entry.main_event = make_index(done)
    chart.entry_points.append(entry)
    flow.flowchart = chart
    buffer = io.BytesIO()
    if not flow.write(buffer):
        raise RuntimeError("evfl refused to write the sample flow")
    return buffer.getvalue()


def sample_broken_ainb() -> bytes:
    """An AINB magic with a body the library cannot read."""
    return b"AIB \xff\xff undecoded"


FADO_AI = "AI/Npc_Kokiri_F_01.root.ainb"
FADO_SCHEDULE = "Component/AIScheduleParam/Npc_Kokiri_F_01.game__component__AIScheduleParam.bgyml"
FADO_FLOW = "Event/EventFlow/Npc_Kokiri_F_01.bfevfl"
FADO_BROKEN = "AI/Npc_Kokiri_F_01.broken.ainb"


def fado_behavior_refs() -> dict[str, str]:
    """Paths the sample Fado pack names. A real pack names the same kinds of files."""
    return {
        "AIProgram": FADO_AI,
        "Schedule": FADO_SCHEDULE,
        "FlowchartFileName": FADO_FLOW,
        "BrokenLogic": FADO_BROKEN,
    }

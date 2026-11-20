"""Read-only behavior graph for one actor.

The dump's AINB, schedule, and event flow are the base. Each node, pin, and
edge keeps an id taken from that file (path, index, and name). Drawing the
graph again uses the same ids. A later edit is a typed op — ``add node``,
``connect``, or ``set field`` — stored beside the base, not a replaced file.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from ocarina_forge.formats.byml import dumps, loads

OPS = ("add node", "connect", "set field")

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
}

_PATH = re.compile(r"[\w./\\-]+\.(?:ainb|bfevfl|evfl|evfc|bgyml)")
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


def load_actor_graph(dump, *, gyaml: str = "", banc: str = "", actor_id: str = "") -> dict:
    """The base graph for this actor class, plus any path named on the placement."""
    actor = None
    note = ""
    if banc and actor_id:
        actor = _actor_in(dump, banc, actor_id)
        if actor is None:
            note = "That actor is not in this scene."
        else:
            gyaml = str(actor.get("Gyaml") or gyaml)
    gyaml = (gyaml or "").strip()
    files = _collect(dump, gyaml, actor)
    graph = _graph(gyaml, files)
    graph["actor"] = actor_id or ""
    graph["banc"] = banc or ""
    if note:
        graph["note"] = note
    elif not graph["nodes"]:
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
        }
        sources.append(source)
        built = _read_source(item)
        for node in built["nodes"]:
            node["source"] = source["id"]
            nodes.append(node)
        edges.extend(built["edges"])
    sources.sort(key=lambda row: (_KIND_ORDER.get(row["kind"], 9), row["path"]))
    order = {row["id"]: index for index, row in enumerate(sources)}
    nodes.sort(key=lambda row: (order.get(row["source"], 9), row.get("index", 0), row["id"]))
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
        "note": "",
    }


def _read_source(item: dict) -> dict:
    if item["status"] != "ok" or item.get("data") is None:
        return {"nodes": [_undecoded(item)], "edges": []}
    kind = item["kind"]
    try:
        if kind == "ainb":
            return _ainb(item["path"], item["data"])
        if kind == "schedule":
            return _schedule(item["path"], item["data"])
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


def _node(*, kind, path, index, key, raw, name, known, decoded, fields=None, pins=None, note="") -> dict:
    return {
        "id": f"{kind}:{path}#{index}:{key}",
        "kind": kind,
        "index": index,
        "raw": raw or "undecoded",
        "name": name,
        "known": known,
        "decoded": decoded,
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
        raw = str(getattr(node, "name", "") or "")
        name, known = friendly_name(raw)
        ident_key = raw or "undecoded"
        fields = _ainb_fields(node)
        built = _node(
            kind="ainb",
            path=path,
            index=index,
            key=ident_key,
            raw=raw or f"node {index}",
            name=name if raw else "Undecoded",
            known=known if raw else False,
            decoded=bool(raw),
            fields=fields,
        )
        pins = []
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
        for param in getattr(node.params, inputs, []) or []:
            if _param_sources(param) and any(int(getattr(src, "src_node_index", -1)) >= 0 for src in _param_sources(param)):
                continue
            value = getattr(param, "default_value", None)
            if value in (None, ""):
                continue
            fields.append({"name": str(getattr(param, "name", "") or type_name), "value": _plain(value)})
    props = getattr(node, "properties", None)
    if props is not None:
        for list_name in _PROP_LISTS:
            for prop in getattr(props, list_name, []) or []:
                value = getattr(prop, "default_value", None)
                if value in (None, ""):
                    continue
                fields.append({"name": str(getattr(prop, "name", "") or "property"), "value": _plain(value)})
    return fields


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
            fields=[{"name": "ConditionFlag", "value": flag}],
        )
        nodes.append(row_node)
        actions = row.get("ActionSchedule") if isinstance(row.get("ActionSchedule"), list) else []
        for slot, action in enumerate(actions):
            for name in _action_names(action):
                label, known = friendly_name(name)
                child = _node(
                    kind="schedule",
                    path=path,
                    index=index,
                    key=f"as:{slot}:{name}",
                    raw=name,
                    name=label if known else f"Play {name}",
                    known=True,
                    decoded=True,
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
                fields=[{"name": "EventName", "value": event_name}],
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
            name=f"Enter {label}",
            known=True,
            decoded=True,
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
    raw = action or str(getattr(event, "name", "") or kind)
    friendly, known = friendly_name(action) if action else (kind, True)
    if action and not known:
        friendly = f"Call {action}"
    fields = [{"name": "event", "value": str(getattr(event, "name", "") or "")}]
    if action:
        fields.append({"name": "action", "value": action})
    flow_name = str(getattr(data, "res_flowchart_name", "") or "")
    entry_name = str(getattr(data, "entry_point_name", "") or "")
    if flow_name or entry_name:
        fields.append({"name": "subflow", "value": f"{flow_name}:{entry_name}".strip(":")})
        friendly = f"Sub flow {entry_name or flow_name}"
    return raw, friendly, fields, True


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


def _collect(dump, gyaml: str, actor: dict | None) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    texts: list[str] = []
    if actor:
        _walk(actor, texts)
    pack_rel = f"Pack/Actor/{gyaml}.pack.zs" if gyaml else ""
    if pack_rel and (dump.romfs / pack_rel).is_file():
        payload, _dict_id = dump.read_file(pack_rel)
        try:
            from ocarina_forge.formats.sarc import Sarc

            archive = Sarc(payload)
        except ValueError:
            archive = None
        if archive is not None:
            for name in archive.names():
                blob = archive.read(name)
                member = f"{pack_rel}:{name}"
                kind = _sniff(name, blob)
                if kind:
                    _add_file(found, seen, kind, member, blob)
                    continue
                if blob[:2] == b"YB":
                    try:
                        document = loads(blob)
                    except ValueError:
                        continue
                    if _is_schedule(document):
                        _add_file(found, seen, "schedule", member, blob)
                    _walk(document, texts)
    for text in texts:
        for relative in _candidates(text):
            path = dump.romfs / relative
            if not path.is_file():
                continue
            try:
                blob, _dict_id = dump.read_file(relative)
            except Exception as exc:
                _add_missing(found, seen, _kind_for(relative), relative, str(exc))
                break
            kind = _sniff(relative, blob) or _kind_for(relative)
            if kind == "schedule" or (kind is None and blob[:2] == b"YB"):
                try:
                    document = loads(blob)
                except ValueError:
                    document = None
                if _is_schedule(document):
                    kind = "schedule"
            if kind:
                _add_file(found, seen, kind, relative, blob)
            break
        else:
            hinted = _kind_for(text)
            if hinted and _looks_like_path(text):
                _add_missing(found, seen, hinted, text.replace("\\", "/"), "This path is not in the dump.")
    if len(found) > 12:
        return found[:12]
    return found


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
    if data[:4] == b"EVFL" or lower.endswith((".bfevfl", ".evfl")):
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


def _looks_like_path(text: str) -> bool:
    return bool(_PATH.search(text)) and "/" in text.replace("\\", "/")


def _is_schedule(document) -> bool:
    return isinstance(document, dict) and (
        isinstance(document.get("ConditionalSchedule"), list) or isinstance(document.get("ActionSchedule"), list)
    )


def _walk(value, into: list[str]) -> None:
    if isinstance(value, str):
        for match in _PATH.findall(value):
            into.append(match)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk(item, into)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, into)


def _candidates(text: str) -> list[str]:
    path = text.replace("\\", "/").lstrip("/")
    if path.startswith("Work/"):
        path = path[len("Work/") :]
    names = [path]
    if path.endswith(".zs"):
        names.append(path[:-3])
    else:
        names.append(path + ".zs")
    if path.endswith(".evfc"):
        stem = path[:-5]
        names.extend((stem + ".bfevfl", stem + ".bfevfl.zs"))
    base = path.split("/")[-1]
    for folder in ("AI/", "Logic/", "Event/EventFlow/", "Component/AIScheduleParam/", "AS/"):
        names.append(folder + base)
        if not base.endswith(".zs"):
            names.append(folder + base + ".zs")
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return unique


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

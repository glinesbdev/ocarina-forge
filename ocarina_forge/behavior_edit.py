"""Apply behavior ops and export the files that can round-trip.

A format is exported only after parse → write with no ops → parse matches the
original bytes or the same node, param, and entry signature. ``set field`` is
written that way. ``add node``, ``connect``, and ``disconnect`` are saved in
the project, and export refuses that file until an AINB writer can round-trip
a graph whose nodes and wires changed.
"""

from __future__ import annotations

import io
from pathlib import Path

from ocarina_forge.behavior import load_actor_graph, mint_id
from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import dumps, loads
from ocarina_forge.formats.zsio import compress, dict_id_for

STRUCTURE_EXPORT = (
    "Added nodes and wires are saved in the project. "
    "Export stays blocked until the AINB writer can round-trip a modified graph."
)
_STRUCTURAL = {"add node", "connect", "disconnect"}


def apply_ops(graph: dict, ops: list[dict]) -> dict:
    """Overlay saved ops. Dump ids stay put. Added nodes keep the ids the project minted."""
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    by_id = {node["id"]: node for node in nodes}
    kept = []
    fields = []
    structure = []
    for op in ops or []:
        kind = op.get("op")
        if kind == "add node":
            _apply_add(graph, op, by_id)
            structure.append({"op": "add node", "id": op.get("id"), "name": op.get("name") or op.get("type") or "Node", "type": op.get("type") or ""})
            kept.append(op)
        elif kind == "connect":
            _apply_connect(edges, by_id, op)
            structure.append({"op": "connect", "id": op.get("id"), "from": op.get("from"), "to": op.get("to")})
            kept.append(op)
        elif kind == "disconnect":
            _apply_disconnect(edges, op)
            structure.append({"op": "disconnect", "id": op.get("id"), "from": op.get("from"), "to": op.get("to")})
            kept.append(op)
        elif kind == "set field":
            result = _apply_field(by_id, op)
            fields.append(result)
            if not result["missing"]:
                kept.append({key: op[key] for key in ("op", "id", "field", "type", "value") if key in op or key == "op"})
    for source in graph.get("sources") or []:
        source["count"] = sum(1 for node in nodes if node.get("source") == source["id"])
    graph["ops"] = kept
    graph["structure"] = structure
    graph["diff"] = [
        {
            "id": op["id"],
            "field": op["field"],
            "type": op.get("type") or "string",
            "value": op.get("value"),
            "missing": bool(op.get("missing")),
        }
        for op in fields
    ]
    return graph


def _apply_add(graph: dict, op: dict, by_id: dict) -> None:
    node_id = str(op.get("id") or "")
    if not node_id or node_id in by_id:
        return
    pins = []
    for pin in op.get("pins") or []:
        if not isinstance(pin, dict) or not pin.get("id"):
            continue
        pins.append(
            {
                "id": str(pin["id"]),
                "dir": "out" if pin.get("dir") == "out" else "in",
                "type": str(pin.get("type") or "flow"),
                "name": str(pin.get("name") or ""),
            }
        )
    node = {
        "id": node_id,
        "kind": str(op.get("kind") or "ainb"),
        "index": 100000 + len(by_id),
        "type": str(op.get("type") or ""),
        "raw": str(op.get("type") or op.get("name") or "node"),
        "name": str(op.get("name") or op.get("type") or "Node"),
        "known": True,
        "decoded": True,
        "entry": False,
        "fields": [],
        "pins": pins,
        "note": "",
        "source": str(op.get("source") or graph.get("open") or ""),
        "added": True,
    }
    graph["nodes"].append(node)
    by_id[node_id] = node


def _apply_connect(edges: list, by_id: dict, op: dict) -> None:
    src = str(op.get("from") or "")
    dst = str(op.get("to") or "")
    if not src or not dst or _has_edge(edges, src, dst):
        return
    if _find_pin(by_id, src) is None or _find_pin(by_id, dst) is None:
        return
    edges.append({"id": str(op.get("id") or f"{src}>{dst}"), "from": src, "to": dst, "added": True})


def _apply_disconnect(edges: list, op: dict) -> None:
    src = str(op.get("from") or "")
    dst = str(op.get("to") or "")
    edges[:] = [edge for edge in edges if not (edge.get("from") == src and edge.get("to") == dst)]


def _apply_field(by_id: dict, op: dict) -> dict:
    node = by_id.get(op.get("id") or "")
    field_name = op.get("field") or ""
    if node is None:
        return {**op, "missing": True}
    found = False
    for field in node.get("fields") or []:
        if field.get("name") != field_name:
            continue
        field["value"] = op.get("value")
        field["edited"] = not _same(field.get("base"), op.get("value"), field.get("type") or "")
        if field["edited"]:
            node["edited"] = True
        found = True
    return {**op, "missing": not found}


def set_field(dump, project, *, gyaml: str, node_id: str, field: str, value, type_name: str = "") -> dict:
    """Store one ``set field`` op, or drop it when the value is the dump's again."""
    graph = load_actor_graph(dump, gyaml=gyaml)
    node = next((item for item in graph["nodes"] if item["id"] == node_id), None)
    if node is None:
        raise BancError(f"No node {node_id} on {gyaml}.", "behavior")
    spec = next((item for item in node.get("fields") or [] if item.get("name") == field), None)
    if spec is None or not spec.get("editable"):
        raise BancError(f"{field} on this node cannot be edited.", "behavior")
    kind = type_name or spec.get("type") or "string"
    try:
        coerced = _coerce(kind, value)
    except (TypeError, ValueError) as exc:
        raise BancError(f"{field} needs a {kind}.", "behavior") from exc
    ops = [
        op
        for op in project.load_behavior_ops(gyaml)
        if not (op.get("op") == "set field" and op.get("id") == node_id and op.get("field") == field)
    ]
    if not _same(spec.get("base"), coerced, kind):
        ops.append({"op": "set field", "id": node_id, "field": field, "type": kind, "value": coerced})
    project.save_behavior_ops(gyaml, ops)
    return apply_ops(graph, ops)


def replace_ops(dump, project, *, gyaml: str, ops: list) -> dict:
    """Replace the saved ops. Undo and redo write the previous list back."""
    clean = [_clean_op(op) for op in ops or []]
    clean = [op for op in clean if op]
    project.save_behavior_ops(gyaml, clean)
    graph = load_actor_graph(dump, gyaml=gyaml)
    return apply_ops(graph, clean)


def add_node(dump, project, *, gyaml: str, source: str, path: str, type_name: str, name: str, pins: list, kind: str = "") -> dict:
    """Mint one node id and store an ``add node`` op."""
    if not path:
        raise BancError("Which file should this node belong to?", "behavior")
    graph = load_actor_graph(dump, gyaml=gyaml)
    ops = list(project.load_behavior_ops(gyaml))
    node_id = mint_id("node", _taken(graph, ops))
    built = []
    for index, pin in enumerate(pins or []):
        if not isinstance(pin, dict):
            continue
        direction = "out" if pin.get("dir") == "out" else "in"
        type_pin = str(pin.get("type") or "flow")
        label = str(pin.get("name") or direction)
        built.append({"id": f"{node_id}:{direction}:{type_pin}:{index}:{label}", "dir": direction, "type": type_pin, "name": label})
    if not built:
        built = [
            {"id": f"{node_id}:in:flow:0:in", "dir": "in", "type": "flow", "name": "in"},
            {"id": f"{node_id}:out:flow:1:out", "dir": "out", "type": "flow", "name": "out"},
        ]
    ops.append(
        {
            "op": "add node",
            "id": node_id,
            "source": source or graph.get("open") or "",
            "path": path,
            "type": type_name,
            "name": name or type_name or "Node",
            "kind": kind or "ainb",
            "pins": built,
        }
    )
    project.save_behavior_ops(gyaml, ops)
    return apply_ops(graph, ops)


def connect_pins(dump, project, *, gyaml: str, path: str, src: str, dst: str) -> dict:
    """Store a ``connect`` op, or drop the ``disconnect`` that had cut this wire."""
    graph = load_actor_graph(dump, gyaml=gyaml)
    ops = list(project.load_behavior_ops(gyaml))
    preview = apply_ops(graph, ops)
    left = _find_pin({node["id"]: node for node in preview["nodes"]}, src)
    right = _find_pin({node["id"]: node for node in preview["nodes"]}, dst)
    if left is None or right is None:
        raise BancError("Those pins are not on this graph.", "behavior")
    if left["dir"] == right["dir"]:
        raise BancError("Connect an output to an input.", "behavior")
    output = left if left["dir"] == "out" else right
    incoming = right if left["dir"] == "out" else left
    if _pin_kind(output.get("type") or "") != _pin_kind(incoming.get("type") or ""):
        raise BancError("Those pins are different types.", "behavior")
    src_id = output["id"]
    dst_id = incoming["id"]
    ops = [op for op in ops if not (op.get("op") == "disconnect" and op.get("from") == src_id and op.get("to") == dst_id)]
    base = load_actor_graph(dump, gyaml=gyaml)
    already = _has_edge(base["edges"], src_id, dst_id) or any(
        op.get("op") == "connect" and op.get("from") == src_id and op.get("to") == dst_id for op in ops
    )
    if not already:
        edge_id = mint_id("edge", _taken(base, ops))
        ops.append({"op": "connect", "id": edge_id, "from": src_id, "to": dst_id, "path": path})
    project.save_behavior_ops(gyaml, ops)
    return apply_ops(load_actor_graph(dump, gyaml=gyaml), ops)


def disconnect_pins(dump, project, *, gyaml: str, path: str, src: str, dst: str) -> dict:
    """Drop a ``connect`` op, or store a ``disconnect`` for a wire from the dump."""
    graph = load_actor_graph(dump, gyaml=gyaml)
    ops = list(project.load_behavior_ops(gyaml))
    linked = [op for op in ops if op.get("op") == "connect" and op.get("from") == src and op.get("to") == dst]
    if linked:
        ops = [op for op in ops if op not in linked]
    elif _has_edge(graph["edges"], src, dst) and not any(
        op.get("op") == "disconnect" and op.get("from") == src and op.get("to") == dst for op in ops
    ):
        edge = next(item for item in graph["edges"] if item.get("from") == src and item.get("to") == dst)
        ops.append({"op": "disconnect", "id": edge.get("id") or f"{src}>{dst}", "from": src, "to": dst, "path": path})
    project.save_behavior_ops(gyaml, ops)
    return apply_ops(graph, ops)


def _clean_op(op) -> dict | None:
    if not isinstance(op, dict):
        return None
    kind = op.get("op")
    if kind == "set field":
        if not (op.get("id") and op.get("field")):
            return None
        return {
            "op": "set field",
            "id": str(op["id"]),
            "field": str(op["field"]),
            "type": str(op.get("type") or "string"),
            "value": op.get("value"),
        }
    if kind == "add node":
        if not op.get("id"):
            return None
        pins = []
        for pin in op.get("pins") or []:
            if isinstance(pin, dict) and pin.get("id"):
                pins.append(
                    {
                        "id": str(pin["id"]),
                        "dir": "out" if pin.get("dir") == "out" else "in",
                        "type": str(pin.get("type") or "flow"),
                        "name": str(pin.get("name") or ""),
                    }
                )
        return {
            "op": "add node",
            "id": str(op["id"]),
            "source": str(op.get("source") or ""),
            "path": str(op.get("path") or ""),
            "type": str(op.get("type") or ""),
            "name": str(op.get("name") or op.get("type") or "Node"),
            "kind": str(op.get("kind") or "ainb"),
            "pins": pins,
        }
    if kind in ("connect", "disconnect"):
        if not (op.get("from") and op.get("to")):
            return None
        return {
            "op": kind,
            "id": str(op.get("id") or f"{op['from']}>{op['to']}"),
            "from": str(op["from"]),
            "to": str(op["to"]),
            "path": str(op.get("path") or ""),
        }
    return None


def _taken(graph: dict, ops: list[dict]) -> set[str]:
    taken: set[str] = set()
    for node in graph.get("nodes") or []:
        taken.add(node["id"])
        for pin in node.get("pins") or []:
            taken.add(pin["id"])
    for edge in graph.get("edges") or []:
        if edge.get("id"):
            taken.add(edge["id"])
    for op in ops:
        if op.get("id"):
            taken.add(str(op["id"]))
        for pin in op.get("pins") or []:
            if isinstance(pin, dict) and pin.get("id"):
                taken.add(str(pin["id"]))
    return taken


def _find_pin(by_id: dict, pin_id: str):
    if not pin_id:
        return None
    for node in by_id.values():
        for pin in node.get("pins") or []:
            if pin.get("id") == pin_id:
                return pin
    return None


def _has_edge(edges: list, src: str, dst: str) -> bool:
    return any(edge.get("from") == src and edge.get("to") == dst for edge in edges)


def _pin_kind(type_name: str) -> str:
    text = (type_name or "").lower()
    if text in ("ptr", "pointer", "actor"):
        return "pointer"
    if text in ("vec3", "vec3f", "vector"):
        return "vec3"
    if text in ("bool", "int", "float", "string"):
        return text
    return "flow"


def _op_path(op: dict) -> str:
    if op.get("path"):
        return str(op["path"])
    if op.get("op") in _STRUCTURAL:
        return _path_of(str(op.get("from") or op.get("id") or ""))
    return _path_of(str(op.get("id") or ""))


def roundtrip_status(kind: str, data: bytes) -> tuple[bool, str]:
    """True when a no-op rewrite matches the bytes or the semantic signature."""
    try:
        written = rewrite(kind, data)
        if written == data or signature(kind, data) == signature(kind, written):
            return True, ""
    except Exception as exc:
        return False, f"{kind} cannot round-trip ({exc}). Editing is saved. This file was not exported."
    return False, f"{kind} cannot round-trip. Editing is saved. This file was not exported."


def export_behaviors(project, dump, *, gyaml: str = "", out_root: Path | None = None) -> list[dict]:
    """Write rebuilt files for saved set-field ops. A format that fails the round-trip is skipped."""
    root = Path(out_root) if out_root is not None else project.root / "build" / "romfs"
    names = [gyaml] if gyaml else project.behavior_actors()
    results = []
    for name in names:
        ops = project.load_behavior_ops(name)
        if not ops:
            continue
        grouped: dict[str, list[dict]] = {}
        for op in ops:
            grouped.setdefault(_op_path(op), []).append(op)
        for path, file_ops in grouped.items():
            if any(op.get("op") in _STRUCTURAL for op in file_ops):
                results.append({"path": path, "exported": False, "message": STRUCTURE_EXPORT})
                continue
            results.append(_export_one(dump, root, path, file_ops))
    return results


def rewrite(kind: str, data: bytes) -> bytes:
    if kind == "ainb":
        from ainb import AINB

        return AINB.from_binary(data, reader_name="roundtrip").to_binary()
    if kind == "event":
        from evfl.evfl import EventFlow

        flow = EventFlow()
        flow.read(data)
        buffer = io.BytesIO()
        if not flow.write(buffer):
            raise ValueError("evfl refused to write the flow")
        return buffer.getvalue()
    if kind == "schedule":
        return dumps(loads(data))
    raise ValueError(f"unknown behavior file {kind}")


def signature(kind: str, data: bytes):
    if kind == "ainb":
        from ainb import AINB

        graph = AINB.from_binary(data, reader_name="signature")
        nodes = []
        for node in graph.nodes or []:
            params = []
            for type_name, inputs, _outputs in (
                ("bool", "bool_inputs", "bool_outputs"),
                ("int", "int_inputs", "int_outputs"),
                ("float", "float_inputs", "float_outputs"),
                ("string", "string_inputs", "string_outputs"),
            ):
                for param in getattr(node.params, inputs, []) or []:
                    params.append((type_name, str(getattr(param, "name", "")), repr(getattr(param, "default_value", None))))
            plugs = tuple(int(getattr(plug, "node_index", -1)) for plug in getattr(node, "child_plugs", []) or [])
            nodes.append((getattr(getattr(node, "type", None), "name", ""), node.name, tuple(params), plugs))
        commands = tuple((command.name, int(command.root_node_index)) for command in graph.commands or [])
        return ("ainb", tuple(nodes), commands)
    if kind == "event":
        from evfl.evfl import EventFlow

        flow = EventFlow()
        flow.read(data)
        chart = flow.flowchart
        events = []
        for event in chart.events or []:
            data_node = event.data
            action = _holder(getattr(data_node, "actor_action", None)) or _holder(getattr(data_node, "actor_query", None))
            params = getattr(getattr(data_node, "params", None), "data", None) or {}
            plain = tuple(sorted((str(key), repr(value)) for key, value in params.items() if not isinstance(value, (list, dict))))
            events.append((event.name, type(data_node).__name__, action, plain))
        entries = tuple(str(getattr(entry, "name", "")) for entry in chart.entry_points or [])
        return ("event", tuple(events), entries)
    if kind == "schedule":
        document = loads(data)
        return ("schedule", _schedule_rows(document))
    raise ValueError(f"unknown behavior file {kind}")


def _export_one(dump, root: Path, path: str, ops: list[dict]) -> dict:
    if ":" in path or not path:
        return {
            "path": path,
            "exported": False,
            "message": "This node is stored inside the actor pack. Export edits AI, event, and schedule files that stand beside the pack.",
        }
    kind = _kind_of(path)
    try:
        data, dict_id = dump.read_file(path)
    except BancError as exc:
        return {"path": path, "exported": False, "message": exc.message}
    ok, reason = roundtrip_status(kind, data)
    if not ok:
        return {"path": path, "exported": False, "message": reason}
    try:
        edited = _apply_file(kind, data, path, ops)
    except Exception as exc:
        return {"path": path, "exported": False, "message": f"Could not apply the edits to {path}: {exc}"}
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.endswith(".zs"):
        packed = compress(edited, dict_id if dict_id is not None else dict_id_for(path), dump.dicts())
        destination.write_bytes(packed)
    else:
        destination.write_bytes(edited)
    return {"path": path, "exported": True, "message": f"Wrote {path}.", "original": len(data), "unpacked": len(edited)}


def _apply_file(kind: str, data: bytes, path: str, ops: list[dict]) -> bytes:
    if kind == "ainb":
        return _apply_ainb(data, path, ops)
    if kind == "event":
        return _apply_event(data, path, ops)
    if kind == "schedule":
        return _apply_schedule(data, path, ops)
    raise ValueError(f"unknown behavior file {kind}")


def _apply_ainb(data: bytes, path: str, ops: list[dict]) -> bytes:
    from ainb import AINB

    graph = AINB.from_binary(data, reader_name=path)
    for op in ops:
        index = _index_of(op["id"])
        if index is None or index >= len(graph.nodes):
            continue
        _set_ainb_field(graph.nodes[index], op["field"], _coerce(op.get("type") or "string", op.get("value")))
    return graph.to_binary()


def _set_ainb_field(node, field: str, value) -> None:
    for type_name, inputs, _outputs in (
        ("bool", "bool_inputs", "bool_outputs"),
        ("int", "int_inputs", "int_outputs"),
        ("float", "float_inputs", "float_outputs"),
        ("string", "string_inputs", "string_outputs"),
    ):
        for param in getattr(node.params, inputs, []) or []:
            if str(getattr(param, "name", "")) == field:
                param.default_value = value
                return
    props = getattr(node, "properties", None)
    if props is None:
        return
    for list_name in (
        "int_properties",
        "bool_properties",
        "float_properties",
        "string_properties",
    ):
        for prop in getattr(props, list_name, []) or []:
            if str(getattr(prop, "name", "")) == field:
                prop.default_value = value
                return


def _apply_event(data: bytes, path: str, ops: list[dict]) -> bytes:
    from evfl.evfl import EventFlow

    flow = EventFlow()
    flow.read(data)
    chart = flow.flowchart
    events = list(chart.events or [])
    for op in ops:
        index = _index_of(op["id"])
        if index is None or index >= len(events):
            continue
        event = events[index]
        value = _coerce(op.get("type") or "string", op.get("value"))
        field = op["field"]
        data_node = event.data
        if field == "action":
            _set_holder(getattr(data_node, "actor_action", None) or getattr(data_node, "actor_query", None), value)
            continue
        if field == "flowchart":
            data_node.res_flowchart_name = str(value)
            continue
        if field == "entry":
            data_node.entry_point_name = str(value)
            continue
        params = getattr(data_node, "params", None)
        if params is not None and isinstance(getattr(params, "data", None), dict) and field in params.data:
            params.data[field] = value
    buffer = io.BytesIO()
    if not flow.write(buffer):
        raise ValueError("evfl refused to write the edited flow")
    return buffer.getvalue()


def _apply_schedule(data: bytes, path: str, ops: list[dict]) -> bytes:
    document = loads(data)
    rows = document.get("ConditionalSchedule")
    if not isinstance(rows, list):
        rows = document.get("ActionSchedule") if isinstance(document.get("ActionSchedule"), list) else []
    for op in ops:
        index = _index_of(op["id"])
        if index is None or index >= len(rows) or not isinstance(rows[index], dict):
            continue
        row = rows[index]
        key = _key_of(op["id"])
        value = _coerce(op.get("type") or "string", op.get("value"))
        field = op["field"]
        if field == "ConditionFlag" or key == str(row.get("ConditionFlag") or "Always"):
            if field == "ConditionFlag":
                row["ConditionFlag"] = value
                continue
        if key.startswith("as:"):
            parts = key.split(":", 2)
            slot = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
            current = parts[2] if len(parts) > 2 else ""
            actions = row.get("ActionSchedule") if isinstance(row.get("ActionSchedule"), list) else []
            if 0 <= slot < len(actions):
                _set_action(actions[slot], field, value, current)
            continue
        if key.startswith("hook:"):
            slot = int(key.split(":")[1])
            entries = row.get("EventEntry") if isinstance(row.get("EventEntry"), list) else []
            if slot < len(entries) and isinstance(entries[slot], dict) and field == "EventName":
                entries[slot]["EventName"] = value
    return dumps(document)


def _set_action(action, field: str, value, current: str = "") -> None:
    if isinstance(action, str):
        return
    if not isinstance(action, dict):
        return
    setting = action.get("Setting")
    if isinstance(setting, str) and field in ("Setting", "AS", "Name"):
        action["Setting"] = value
        return
    if isinstance(setting, dict) and field in setting:
        setting[field] = value
        return
    if isinstance(setting, dict) and field == "Setting":
        for key in ("ArrivalAS", "ResponseWaitAS", "WaitArrivalAS", "AS", "Name"):
            if current and setting.get(key) == current:
                setting[key] = value
                return
        for key in ("ArrivalAS", "ResponseWaitAS", "WaitArrivalAS", "AS", "Name"):
            if key in setting:
                setting[key] = value
                return


def _set_holder(holder, value: str) -> None:
    if holder is None:
        return
    inner = getattr(holder, "v", None)
    if inner is None:
        return
    if hasattr(inner, "v"):
        inner.v = value


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


def _schedule_rows(document) -> tuple:
    if not isinstance(document, dict):
        return tuple()
    rows = document.get("ConditionalSchedule")
    if not isinstance(rows, list):
        rows = document.get("ActionSchedule") if isinstance(document.get("ActionSchedule"), list) else []
    found = []
    for row in rows:
        if not isinstance(row, dict):
            found.append(("bad",))
            continue
        actions = []
        for action in row.get("ActionSchedule") or []:
            if isinstance(action, str):
                actions.append(action)
            elif isinstance(action, dict):
                setting = action.get("Setting")
                actions.append(tuple(sorted(setting.items())) if isinstance(setting, dict) else setting)
        entries = []
        for entry in row.get("EventEntry") or []:
            if isinstance(entry, dict):
                entries.append(str(entry.get("EventName") or ""))
        found.append((str(row.get("ConditionFlag") or ""), tuple(actions), tuple(entries)))
    return tuple(found)


def _path_of(node_id: str) -> str:
    body = node_id.split(":", 1)[1] if ":" in node_id else node_id
    return body.split("#", 1)[0]


def _index_of(node_id: str) -> int | None:
    if "#" not in node_id:
        return None
    rest = node_id.split("#", 1)[1]
    if rest.startswith("command:") or rest.startswith("entry:"):
        return None
    head = rest.split(":", 1)[0]
    return int(head) if head.isdigit() else None


def _key_of(node_id: str) -> str:
    if "#" not in node_id:
        return ""
    rest = node_id.split("#", 1)[1]
    parts = rest.split(":", 1)
    return parts[1] if len(parts) == 2 else ""


def _kind_of(path: str) -> str:
    low = path.lower()
    if ".ainb" in low:
        return "ainb"
    if any(token in low for token in (".bfevfl", ".evfl")):
        return "event"
    return "schedule"


def _coerce(type_name: str, value):
    if type_name == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if type_name == "int":
        return int(value)
    if type_name == "float":
        return float(value)
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _same(left, right, type_name: str) -> bool:
    if type_name == "float":
        try:
            return abs(float(left) - float(right)) < 1e-5
        except (TypeError, ValueError):
            return False
    if type_name == "int":
        try:
            return int(left) == int(right)
        except (TypeError, ValueError):
            return False
    if type_name == "bool":
        return _coerce("bool", left) == _coerce("bool", right)
    return left == right

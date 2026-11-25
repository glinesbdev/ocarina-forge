"""Apply ``set field`` ops to a behavior graph, and export the files that round-trip.

A format is exported only after parse → write with no ops → parse matches the
original bytes or the same node, param, and entry signature. Editing is saved
either way. Connecting and adding nodes are not ops this stage writes.
"""

from __future__ import annotations

import io
from pathlib import Path

from ocarina_forge.behavior import load_actor_graph
from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import dumps, loads
from ocarina_forge.formats.zsio import compress, dict_id_for


def apply_ops(graph: dict, ops: list[dict]) -> dict:
    """Overlay saved field values. Node ids stay the ones the dump minted."""
    by_id = {node["id"]: node for node in graph.get("nodes") or []}
    applied = []
    for op in ops:
        if op.get("op") != "set field":
            continue
        node = by_id.get(op.get("id") or "")
        field_name = op.get("field") or ""
        if node is None:
            applied.append({**op, "missing": True})
            continue
        found = False
        for field in node.get("fields") or []:
            if field.get("name") != field_name:
                continue
            field["value"] = op.get("value")
            field["edited"] = not _same(field.get("base"), op.get("value"), field.get("type") or "")
            if field["edited"]:
                node["edited"] = True
            found = True
        applied.append({**op, "missing": not found})
    graph["ops"] = [op for op in applied if not op.get("missing")]
    graph["diff"] = [
        {
            "id": op["id"],
            "field": op["field"],
            "type": op.get("type") or "string",
            "value": op.get("value"),
            "missing": bool(op.get("missing")),
        }
        for op in applied
    ]
    return graph


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
    clean = []
    for op in ops or []:
        if not isinstance(op, dict) or op.get("op") != "set field":
            continue
        if not (op.get("id") and op.get("field")):
            continue
        clean.append(
            {
                "op": "set field",
                "id": str(op["id"]),
                "field": str(op["field"]),
                "type": str(op.get("type") or "string"),
                "value": op.get("value"),
            }
        )
    project.save_behavior_ops(gyaml, clean)
    graph = load_actor_graph(dump, gyaml=gyaml)
    return apply_ops(graph, clean)


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
            path = _path_of(op["id"])
            grouped.setdefault(path, []).append(op)
        for path, file_ops in grouped.items():
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

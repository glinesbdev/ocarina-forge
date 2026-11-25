"""Delete courtyard actors when the epilogue flag is set.

``Npc_Hylia_Soldier_027_Child`` is banc actor 223. His Presence is not
negated, and his ``Logic_Delete`` is unwired, so he stands on a cleared
save. ``Land_HrlCastle_HyruleCastle_A_Door_Steel_A_1f92`` is the closed
steel gate on the courtyard stairs. It has no Presence. After Door of Time
step 03 its ``Logic_Create`` is true and its ``Logic_Delete`` is unwired,
so the gate stays shut. Both deletes copy the ``Dm_Kokiri_0150`` query
from the vanilla Saria logic module and change only ``GameDataNameBool``.

``Logic_Create`` may already read another node. This patch leaves that
edge alone and refuses a ``Logic_Delete`` that is already wired. The open
gate ``..._d73e`` is the same module: a copy of its ``OperateOR`` makes
``Create = OR(step.False, TitleEventBase)`` and ``Delete`` the OR's False
output, which is ``step.True`` when the flag is false.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.logic import (
    CREATE_PARAM,
    DATA_BOOL,
    DELETE_PARAM,
    GET_BOOL,
    LOGIC_REL as DONOR_REL,
    MULTI_PARAM,
    OR_NAME,
    TITLE_EVENT,
    _bool_multi,
    _copy_header_style,
    _field_values,
    _find_played,
    _flag,
    _header_fields,
    _instance_names,
    _load,
    _named_input,
    _node_base,
    _node_size,
    _only_source,
    _point_plugs_at,
    _set_bool_multi,
    _set_game_data_bool,
    _source,
    preserve_node_headers,
)
from after_ocarina.paths import SCENES_PATH
from after_ocarina.sources import _mapping, _rel_path

GUARD_LOGIC = "Logic/HyruleCastle_Prefab_6d27.logic.module.ainb"
GATE_LOGIC = "Logic/HyruleCastle_Prefab_c03d.logic.module.ainb"
GUARD_INSTANCE = "Npc_Hylia_Soldier_027_Child"
CLOSED_GATE = "Land_HrlCastle_HyruleCastle_A_Door_Steel_A_1f92"
OPEN_GATE = "Land_HrlCastle_HyruleCastle_A_Door_Steel_A_d73e"
_HOLD_GUID = "a70e5100-0000-4000-8000-000000000027"
_OR_GUID = "a70e5100-0000-4000-8000-000000000028"
FALSE_OUTPUT = "False"
TRUE_OUTPUT = "True"


@dataclass(frozen=True)
class ActorDelete:
    logic: str
    instance: str | None
    open: str | None
    source: str
    node: int | None = None
    node_name: str | None = None


@dataclass(frozen=True)
class CastleLogic:
    place: bool
    flag: str
    deletes: tuple[ActorDelete, ...]
    source: str
    door: str | None = None


def load_castle(path: Path | None = None) -> CastleLogic:
    path = path or (SCENES_PATH.parent / "m4b-castle.yaml")
    if not path.is_file():
        raise ModError(f"Missing {path}")
    data = _mapping(path)
    unknown = set(data) - {"place", "flag", "deletes", "door"}
    if unknown:
        raise ModError(f"{path.name}: unknown keys: {', '.join(sorted(unknown))}")
    if data.get("place", False) is not False:
        raise ModError(f"{path.name}: place stays false. The courtyard banc is not edited.")
    if data.get("flag") != TITLE_EVENT:
        raise ModError(f"{path.name}: flag must be {TITLE_EVENT}")
    rows = data.get("deletes")
    if not isinstance(rows, list) or not rows:
        raise ModError(f"{path.name}: deletes must be a non-empty list")
    deletes: list[ActorDelete] = []
    seen_logic: set[str] = set()
    seen_instance: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ModError(f"{path.name}: delete {index} must be a mapping")
        extra = set(row) - {"logic", "instance", "open", "node", "node_name"}
        if extra:
            raise ModError(f"{path.name}: delete {index} has unknown keys: {', '.join(sorted(extra))}")
        logic = _rel_path(row.get("logic"), path, index, "logic", suffix=".ainb")
        instance = row.get("instance")
        node = row.get("node")
        node_name = row.get("node_name")
        if node is None and (not isinstance(instance, str) or not instance):
            raise ModError(f"{path.name}: delete {index} needs an instance name, or a node index and node_name")
        if instance is not None and (not isinstance(instance, str) or not instance):
            raise ModError(f"{path.name}: delete {index} instance must be a string")
        if node is not None and (isinstance(node, bool) or not isinstance(node, int) or node < 0):
            raise ModError(f"{path.name}: delete {index} node must be an index")
        if node is not None and (not isinstance(node_name, str) or not node_name):
            raise ModError(f"{path.name}: delete {index} node_name must name that node")
        if node is None and node_name is not None:
            raise ModError(f"{path.name}: delete {index} node_name needs a node index")
        opened = row.get("open")
        if opened is not None and (not isinstance(opened, str) or not opened):
            raise ModError(f"{path.name}: delete {index} open must be an instance name")
        if opened is not None and opened == instance:
            raise ModError(f"{path.name}: delete {index} open is the same instance as the delete")
        if logic in seen_logic:
            raise ModError(f"{path.name}: {logic} is listed twice")
        for name in (instance, opened):
            if name and name in seen_instance:
                raise ModError(f"{path.name}: {name} is listed twice")
            if name:
                seen_instance.add(name)
        seen_logic.add(logic)
        deletes.append(
            ActorDelete(
                logic=logic,
                instance=instance,
                open=opened,
                source=path.name,
                node=node,
                node_name=node_name,
            )
        )
    door = data.get("door")
    if door is not None:
        door = _rel_path(door, path, 0, "door", suffix=".ainb")
    return CastleLogic(place=False, flag=TITLE_EVENT, deletes=tuple(deletes), source=path.name, door=door)


def load_scene_logics() -> list[CastleLogic]:
    """Every scene yaml that lists deletes. The courtyard file has no door key."""
    found = []
    root = SCENES_PATH.parent
    for path in sorted(root.glob("*.yaml")):
        data = _mapping(path)
        if "deletes" not in data:
            continue
        found.append(load_castle(path))
    if not found:
        raise ModError(f"No scene yaml under {root} lists deletes")
    return found


def apply_delete(
    data: bytes,
    label: str,
    instance: str | None,
    donor: bytes | None = None,
    donor_label: str = DONOR_REL,
    open_instance: str | None = None,
    node_index: int | None = None,
    node_name: str | None = None,
) -> tuple[bytes, str]:
    """Wire one actor's Logic_Delete to TitleEventBase.

    ``donor`` is the vanilla Kokiri Saria logic module when this file has no
    ``HoldGameDataGetBoolSimple`` of its own. ``Logic_Create`` is left as it
    was, including a link to another node. ``open_instance`` also rewires the
    open gate in this same file through a copy of its step ``OperateOR``.
    """
    file = _load(data, label)
    nodes = file.nodes
    _refuse_if_patched(nodes, label, instance)
    if node_index is not None:
        if node_index >= len(nodes):
            raise ModError(f"{label}: node {node_index} is outside the file ({len(nodes)} nodes).")
        actor = nodes[node_index]
        if node_name is not None and actor.name != node_name:
            raise ModError(f"{label}: node {node_index} is {actor.name!r}, expected {node_name!r}.")
        actor_index = node_index
    else:
        if not instance:
            raise ModError(f"{label}: a delete needs an instance name or a node index.")
        actor_index, actor = _find_actor(nodes, label, instance)
    create = _named_input(actor, CREATE_PARAM, label, actor_index)
    create_from = _only_source(create, label, actor_index, CREATE_PARAM)
    create_kept = (create_from.src_node_index, create_from.src_output_index, create.default_value)
    delete = _named_input(actor, DELETE_PARAM, label, actor_index)
    delete_from = _only_source(delete, label, actor_index, DELETE_PARAM)
    if delete_from.src_node_index >= 0:
        raise ModError(
            f"{label}: node {actor_index} {DELETE_PARAM} already reads node {delete_from.src_node_index}. "
            "This patch only wires an unwired Logic_Delete."
        )
    hold, hold_index, template_index, donor_file, origin = _append_flag(file, donor, donor_label)
    _wire_delete(actor, delete, hold_index)

    or_index = None
    open_index = None
    step_index = None
    if open_instance:
        or_index, open_index, step_index = _rewire_open_gate(file, open_instance, hold_index, label)

    original_count = hold_index
    written = file.to_binary()
    written = preserve_node_headers(
        data,
        written,
        version=file.version,
        command_count=len(file.commands),
        node_count=original_count,
    )
    if donor_file is None:
        written = _copy_header_style(
            written,
            version=file.version,
            command_count=len(file.commands),
            src_index=template_index,
            dest_index=hold_index,
        )
    else:
        written = _copy_header_from(
            written,
            dest_version=file.version,
            dest_commands=len(file.commands),
            dest_index=hold_index,
            source=donor,
            source_version=donor_file.version,
            source_commands=len(donor_file.commands),
            source_index=template_index,
        )
    if or_index is not None and step_index is not None:
        written = _copy_header_style(
            written,
            version=file.version,
            command_count=len(file.commands),
            src_index=step_index,
            dest_index=or_index,
        )
    _check_delete(written, label, instance, actor_index, create_kept, node_name)
    if open_instance and or_index is not None and open_index is not None and step_index is not None:
        _check_open(written, label, open_instance, open_index, or_index, step_index, hold_index)
    kept = _create_phrase(*create_kept)
    shown = (_instance_names(actor) or [actor.name])[0]
    note = (
        f"Castle delete: node {actor_index} {shown!r} "
        f"{DELETE_PARAM} now reads {GET_BOOL} node {hold_index} output 0. "
        f"That node comes from {origin}; only {DATA_BOOL} changed to {TITLE_EVENT}. "
        f"{CREATE_PARAM} stays {kept}."
    )
    if open_index is not None and or_index is not None and step_index is not None:
        note += (
            f" Open gate node {open_index} {CREATE_PARAM} reads {OR_NAME} node {or_index} output {TRUE_OUTPUT}, "
            f"and {DELETE_PARAM} reads output {FALSE_OUTPUT}. "
            f"That OR is node {step_index} {FALSE_OUTPUT} OR {TITLE_EVENT}, "
            f"so a false flag leaves {CREATE_PARAM} on node {step_index} output 0 "
            f"and {DELETE_PARAM} on its output 1."
        )
    return written, note


def sample_logic_for(logic: str) -> bytes:
    if logic == GUARD_LOGIC:
        return sample_guard_logic_bytes()
    if logic == GATE_LOGIC:
        return sample_gate_logic_bytes()
    raise ModError(f"No castle logic fixture for {logic}")


def sample_guard_logic_bytes() -> bytes:
    """The castle module's shape: eight nodes and no GetBoolSimple.

    Node 2 is ``ActorLogicNpc`` ``Npc_Hylia_Soldier_027_Child_7a60``.
    ``Logic_Create`` is constant true and ``Logic_Delete`` is unwired.
    Built for the tests. It is not a game file. The flag node comes from
    the Saria donor, not from this file.
    """
    from ainb import AINB
    from ainb.blackboard import Blackboard
    from ainb.command import Command
    from ainb.node import Node, NodeFlag, NodeType
    from ainb.param import InputParam, ParamSource
    from ainb.param_common import ParamFlag, ParamType
    from ainb.property import Property

    file = AINB()
    file.version = 0x407
    file.filename = "HyruleCastle_Prefab_6d27.logic.module"
    file.category = "Logic"
    file.blackboard = Blackboard()
    file.exists_section_0x6c = True
    command = Command()
    command.name = "Root"
    command.guid = "11111111-1111-1111-1111-111111111111"
    command.root_node_index = 2
    file.commands.append(command)

    def make(name: str, *, query: bool = False) -> Node:
        node = Node(NodeType.UserDefined)
        node.name = name
        node.flags = NodeFlag(1 if query else 0)
        node.guid = "22222222-2222-2222-2222-222222222222"
        return node

    names = (
        "ActorLogicEventRequestActor",
        "ActorLogicEventRequestActor",
        "ActorLogicNpc",
        "OperateCompareInt",
        "OperateCompareInt",
        "OperateAND",
        "OperateAND",
        "OperateOR",
    )
    file.nodes = [make(name, query=name.startswith("Actor")) for name in names]
    for index, node in enumerate(file.nodes):
        node.index = index
    guard = file.nodes[2]
    create = InputParam(ParamType.Bool)
    create.name = CREATE_PARAM
    create.default_value = True
    create.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
    delete = InputParam(ParamType.Bool)
    delete.name = DELETE_PARAM
    delete.default_value = False
    delete.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
    guard.params.bool_inputs.extend((create, delete))
    prop = Property(ParamType.String)
    prop.name = "InstanceName"
    prop.default_value = "Npc_Hylia_Soldier_027_Child_7a60"
    prop.flags = ParamFlag().set_uses_default()
    guard.properties.string_properties.append(prop)
    return file.to_binary()


def sample_gate_logic_bytes() -> bytes:
    """The steel-gate module: four nodes, no GetBoolSimple.

    Node 0 is the closed gate. Its create reads the step OR's True output
    and its delete is unwired. Node 3 is the open gate, created on the OR's
    False output and deleted on its True output. Built for the tests.
    """
    from ainb import AINB
    from ainb.blackboard import Blackboard
    from ainb.command import Command
    from ainb.node import GenericPlug, Node, NodeFlag, NodeType
    from ainb.param import InputParam, OutputParam, ParamSource
    from ainb.param_common import ParamFlag, ParamType
    from ainb.property import Property

    file = AINB()
    file.version = 0x407
    file.filename = "HyruleCastle_Prefab_c03d.logic.module"
    file.category = "Logic"
    file.blackboard = Blackboard()
    file.exists_section_0x6c = True
    command = Command()
    command.name = "Root"
    command.guid = "11111111-1111-1111-1111-111111111111"
    command.root_node_index = 0
    file.commands.append(command)

    def make(name: str, *, query: bool = True) -> Node:
        node = Node(NodeType.UserDefined)
        node.name = name
        node.flags = NodeFlag(1 if query else 0)
        node.guid = "33333333-3333-3333-3333-333333333333"
        return node

    closed, step, gate, opened = (
        make("ActorLogicSimpleActor"),
        make("HoldChallengeCurrentStepCheckOrder"),
        make(OR_NAME),
        make("ActorLogicSimpleActor"),
    )
    file.nodes = [closed, step, gate, opened]
    for index, node in enumerate(file.nodes):
        node.index = index

    def boolean_output(name: str) -> OutputParam:
        param = OutputParam(ParamType.Bool)
        param.name = name
        param.is_output = True
        return param

    def link(index: int, output: int) -> ParamSource:
        return ParamSource(index, output, ParamFlag())

    def bool_input(name: str, source: ParamSource, default: bool = False) -> InputParam:
        param = InputParam(ParamType.Bool)
        param.name = name
        param.default_value = default
        param.source = source
        return param

    def instance(node: Node, name: str) -> None:
        prop = Property(ParamType.String)
        prop.name = "InstanceName"
        prop.default_value = name
        prop.flags = ParamFlag().set_uses_default()
        node.properties.string_properties.append(prop)

    def string_property(node: Node, name: str, value: str) -> None:
        prop = Property(ParamType.String)
        prop.name = name
        prop.default_value = value
        prop.flags = ParamFlag().set_uses_default()
        node.properties.string_properties.append(prop)

    for name in ("IsAfter", "IsBefore", "IsEqual"):
        step.params.bool_outputs.append(boolean_output(name))
    string_property(step, "ChallengeName", "Work/Challenge/DoorOfTime.game__challenge__Challenge.gyml")
    string_property(step, "ChallengeStepName", "Step03")
    gate.params.bool_outputs.extend((boolean_output(FALSE_OUTPUT), boolean_output(TRUE_OUTPUT)))
    multi = InputParam(ParamType.Bool)
    multi.name = MULTI_PARAM
    multi.default_value = False
    multi.source = [link(1, 2), link(1, 0)]
    gate.params.bool_inputs.append(multi)
    gate.queries = [1]
    plug = GenericPlug()
    plug.name = MULTI_PARAM
    plug.node_index = 1
    gate.generic_plugs.append(plug)

    closed.params.bool_inputs.extend(
        (
            bool_input(CREATE_PARAM, link(2, 1)),
            bool_input(DELETE_PARAM, ParamSource(-1, 0, ParamFlag().set_uses_default()), False),
        )
    )
    closed.queries = [2]
    instance(closed, CLOSED_GATE)
    create_plug = GenericPlug()
    create_plug.name = CREATE_PARAM
    create_plug.node_index = 2
    closed.generic_plugs.append(create_plug)

    opened.params.bool_inputs.extend((bool_input(CREATE_PARAM, link(2, 0)), bool_input(DELETE_PARAM, link(2, 1))))
    opened.queries = [2]
    instance(opened, OPEN_GATE)
    for name in (CREATE_PARAM, DELETE_PARAM):
        door_plug = GenericPlug()
        door_plug.name = name
        door_plug.node_index = 2
        opened.generic_plugs.append(door_plug)
    return file.to_binary()


def _append_flag(file, donor: bytes | None, donor_label: str):
    nodes = file.nodes
    label = file.filename or donor_label
    local = _find_template(nodes)
    donor_file = None
    if local is not None:
        template_index, template = local
        hold, how = _copy_template(template, label, template_index)
        origin = f"node {template_index} in this file ({how})"
    else:
        if donor is None:
            raise ModError(
                f"{label}: no {GET_BOOL} with a {DATA_BOOL} to clone. "
                f"This module needs a donor from {donor_label}."
            )
        donor_file = _load(donor, donor_label)
        template_index, template = _find_played(donor_file.nodes, donor_label)
        hold, how = _copy_template(template, donor_label, template_index)
        origin = f"{donor_label} node {template_index} ({how})"
    if not hold.params.bool_outputs:
        raise ModError(f"{label}: the {GET_BOOL} template has no bool output to wire into {DELETE_PARAM}.")
    hold.guid = _HOLD_GUID
    hold.index = len(nodes)
    _set_game_data_bool(hold, TITLE_EVENT, label, template_index)
    hold.queries = []
    for group in hold._plugs:
        group.clear()
    hold_index = len(nodes)
    file.nodes.append(hold)
    return hold, hold_index, template_index, donor_file, origin


def _wire_delete(actor, delete, hold_index: int) -> None:
    from ainb.param import ParamSource
    from ainb.param_common import ParamFlag

    delete.source = ParamSource(hold_index, 0, ParamFlag())
    queries = list(actor.queries or [])
    if hold_index not in queries:
        queries.append(hold_index)
    actor.queries = queries
    _set_plug(actor, DELETE_PARAM, hold_index)


def _rewire_open_gate(file, instance: str, hold_index: int, label: str) -> tuple[int, int, int]:
    nodes = file.nodes
    open_index, actor = _find_actor(nodes, label, instance)
    create = _named_input(actor, CREATE_PARAM, label, open_index)
    delete = _named_input(actor, DELETE_PARAM, label, open_index)
    create_from = _only_source(create, label, open_index, CREATE_PARAM)
    delete_from = _only_source(delete, label, open_index, DELETE_PARAM)
    step_index = create_from.src_node_index
    if step_index < 0 or step_index != delete_from.src_node_index:
        raise ModError(
            f"{label}: node {open_index} {CREATE_PARAM} reads node {step_index} "
            f"and {DELETE_PARAM} reads node {delete_from.src_node_index}. "
            "The open gate's create and delete have to read the same step OR."
        )
    step = nodes[step_index]
    if step.name != OR_NAME:
        raise ModError(f"{label}: node {open_index} reads node {step_index} {step.name!r}, expected {OR_NAME}.")
    false_at = _output_index(step, FALSE_OUTPUT, label, step_index)
    true_at = _output_index(step, TRUE_OUTPUT, label, step_index)
    if (false_at, true_at) != (0, 1):
        raise ModError(
            f"{label}: node {step_index} {OR_NAME} outputs put {FALSE_OUTPUT} at {false_at} "
            f"and {TRUE_OUTPUT} at {true_at}. Expected 0 and 1."
        )
    if (create_from.src_output_index, delete_from.src_output_index) != (false_at, true_at):
        raise ModError(
            f"{label}: node {open_index} {CREATE_PARAM} reads output {create_from.src_output_index} "
            f"and {DELETE_PARAM} reads output {delete_from.src_output_index}. "
            f"Expected {FALSE_OUTPUT} then {TRUE_OUTPUT}."
        )
    if not step.flags.is_query():
        raise ModError(f"{label}: node {step_index} {OR_NAME} is not a query, so a copy cannot read it.")
    cloned = copy.deepcopy(step)
    cloned.guid = _OR_GUID
    cloned.index = len(nodes)
    or_index = len(nodes)
    file.nodes.append(cloned)
    bits = _bool_multi(step, label, step_index)[0].flags
    _set_bool_multi(
        cloned,
        [
            _source(step_index, false_at, bits),
            _source(hold_index, 0, bits),
        ],
    )
    cloned.queries = [step_index, hold_index]
    _point_plugs_at(cloned, step_index)
    _retarget_source(create, or_index, true_at)
    _retarget_source(delete, or_index, false_at)
    _set_plug(actor, CREATE_PARAM, or_index)
    _set_plug(actor, DELETE_PARAM, or_index)
    updated: list[int] = []
    for item in actor.queries or []:
        chosen = or_index if item == step_index else item
        if chosen not in updated:
            updated.append(chosen)
    if or_index not in updated:
        updated.append(or_index)
    actor.queries = updated
    return or_index, open_index, step_index


def _create_phrase(node_index: int, output: int, default) -> str:
    if node_index < 0:
        return f"constant {default!r}"
    return f"node {node_index} output {output}"


def _copy_template(template, label: str, index: int) -> tuple[object, str]:
    """Deep-copy a self-contained query. Rebuild it when it points outside itself."""
    reasons = _external_reasons(template)
    if not reasons:
        return copy.deepcopy(template), "deepcopy"
    return _fresh_from(template, label, index, reasons), "fresh: " + ", ".join(reasons)


def _external_reasons(node) -> list[str]:
    reasons = []
    if node.attachments:
        reasons.append("attachments")
    if node.state_info is not None:
        reasons.append("state")
    for param in _inputs(node):
        sources = param.source if isinstance(param.source, list) else [param.source]
        for source in sources:
            if source is None or not hasattr(source, "src_node_index"):
                continue
            if source.src_node_index >= 0:
                reasons.append(f"{param.name} reads node {source.src_node_index}")
            if source.is_blackboard() or getattr(param, "is_blackboard_input", False):
                reasons.append(f"{param.name} reads the blackboard")
    return reasons


def _fresh_from(template, label: str, index: int, reasons: list[str]):
    """A new node with the donor's name, flags, properties, and parameters.

    Links to other nodes and blackboard reads become constant defaults. A
    linked input with no default cannot be rebuilt.
    """
    from ainb.node import Node, NodeFlag

    node = Node(template.type)
    node.name = template.name
    node.flags = NodeFlag(int(template.flags))
    node.properties = copy.deepcopy(template.properties)
    node.params = copy.deepcopy(template.params)
    for param in _inputs(node):
        sources = param.source if isinstance(param.source, list) else [param.source]
        for source in sources:
            if source is None or not hasattr(source, "src_node_index"):
                continue
            linked = source.src_node_index >= 0 or source.is_blackboard() or getattr(param, "is_blackboard_input", False)
            if not linked:
                continue
            if param.default_value is None:
                raise ModError(
                    f"{label}: node {index} {template.name} {param.name} has no default "
                    f"({', '.join(reasons)})."
                )
            source.src_node_index = -1
            source.src_output_index = 0
            from ainb.param_common import ParamFlag

            source.flags = ParamFlag().set_uses_default()
            param.is_blackboard_input = False
    return node


def _inputs(node):
    from ainb.param_common import ParamType

    found = []
    for param_type in ParamType:
        found.extend(node.params.get_inputs(param_type))
    return found


def _copy_header_from(
    written: bytes,
    *,
    dest_version: int,
    dest_commands: int,
    dest_index: int,
    source: bytes,
    source_version: int,
    source_commands: int,
    source_index: int,
) -> bytes:
    """Copy the three header fields ``to_binary`` zeroes from a node in another file."""
    out = bytearray(written)
    dest_pad, dest_unk, dest_tail = _header_fields(dest_version)
    src_pad, src_unk, src_tail = _header_fields(source_version)
    dest_at = _node_base(dest_commands) + dest_index * _node_size(dest_version)
    src_at = _node_base(source_commands) + source_index * _node_size(source_version)
    if dest_at + dest_tail + 2 > len(out) or src_at + src_tail + 2 > len(source):
        raise ModError("logic file is missing a donor node header to copy.")
    out[dest_at + dest_pad] = source[src_at + src_pad]
    out[dest_at + dest_unk : dest_at + dest_unk + 4] = source[src_at + src_unk : src_at + src_unk + 4]
    out[dest_at + dest_tail : dest_at + dest_tail + 2] = source[src_at + src_tail : src_at + src_tail + 2]
    return bytes(out)


def _check_delete(
    data: bytes,
    label: str,
    instance: str | None,
    actor_index: int,
    create_kept: tuple,
    node_name: str | None = None,
) -> None:
    file = _load(data, label)
    actor = file.nodes[actor_index]
    names = _instance_names(actor)
    if instance:
        if not any(name == instance or name.startswith(instance) for name in names):
            raise ModError(f"{label}: node {actor_index} is not {instance} after the rewrite ({names}).")
    elif node_name and actor.name != node_name:
        raise ModError(f"{label}: node {actor_index} is {actor.name!r} after the rewrite, expected {node_name!r}.")
    create = _named_input(actor, CREATE_PARAM, label, actor_index)
    create_from = _only_source(create, label, actor_index, CREATE_PARAM)
    kept = (create_from.src_node_index, create_from.src_output_index, create.default_value)
    if kept != create_kept:
        raise ModError(f"{label}: node {actor_index} {CREATE_PARAM} changed from {create_kept} to {kept}.")
    delete = _named_input(actor, DELETE_PARAM, label, actor_index)
    source = _only_source(delete, label, actor_index, DELETE_PARAM).src_node_index
    if source < 0 or source >= len(file.nodes):
        raise ModError(f"{label}: node {actor_index} {DELETE_PARAM} does not read the new node.")
    if _field_values(file.nodes[source], DATA_BOOL) != [TITLE_EVENT]:
        raise ModError(
            f"{label}: node {source} {DATA_BOOL} is {_field_values(file.nodes[source], DATA_BOOL)!r}."
        )


def _check_open(data: bytes, label: str, instance: str, open_index: int, or_index: int, step_index: int, hold_index: int) -> None:
    file = _load(data, label)
    actor = file.nodes[open_index]
    names = _instance_names(actor)
    if not any(name == instance or name.startswith(instance) for name in names):
        raise ModError(f"{label}: node {open_index} is not {instance} after the rewrite ({names}).")
    create = _only_source(_named_input(actor, CREATE_PARAM, label, open_index), label, open_index, CREATE_PARAM)
    delete = _only_source(_named_input(actor, DELETE_PARAM, label, open_index), label, open_index, DELETE_PARAM)
    if (create.src_node_index, create.src_output_index) != (or_index, 1):
        raise ModError(f"{label}: open {CREATE_PARAM} reads {(create.src_node_index, create.src_output_index)}.")
    if (delete.src_node_index, delete.src_output_index) != (or_index, 0):
        raise ModError(f"{label}: open {DELETE_PARAM} reads {(delete.src_node_index, delete.src_output_index)}.")
    gate = file.nodes[or_index]
    if gate.name != OR_NAME:
        raise ModError(f"{label}: node {or_index} is {gate.name!r}, expected {OR_NAME}.")
    sources = [(item.src_node_index, item.src_output_index) for item in _bool_multi(gate, label, or_index)]
    if sources != [(step_index, 0), (hold_index, 0)]:
        raise ModError(f"{label}: node {or_index} {MULTI_PARAM} is {sources}.")
    if _field_values(file.nodes[hold_index], DATA_BOOL) != [TITLE_EVENT]:
        raise ModError(f"{label}: node {hold_index} is not {TITLE_EVENT}.")
    step = file.nodes[step_index]
    step_sources = [(item.src_node_index, item.src_output_index) for item in _bool_multi(step, label, step_index)]
    if any(node == or_index or node == hold_index for node, _output in step_sources):
        raise ModError(f"{label}: the step {OR_NAME} was rewired ({step_sources}).")
    for step_false in (False, True):
        vanilla_create, vanilla_delete, patched_create, patched_delete = _flag_false_result(step_false)
        if (patched_create, patched_delete) != (vanilla_create, vanilla_delete):
            raise ModError(
                f"{label}: TitleEventBase false does not keep the open gate on the step OR "
                f"(step False={step_false}: vanilla {(vanilla_create, vanilla_delete)}, "
                f"patched {(patched_create, patched_delete)})."
            )


def _flag_false_result(step_false: bool) -> tuple[bool, bool, bool, bool]:
    """Vanilla create/delete, then the patched pair, when TitleEventBase is false.

    The step OR's False and True outputs are treated as a complement. Create
    becomes ``OR(step.False, flag)`` and Delete becomes that OR's False output.
    With the flag false, both equal the vanilla outputs.
    """
    step_true = not step_false
    combined = step_false or False
    return step_false, step_true, combined, not combined


def _refuse_if_patched(nodes, label: str, instance: str | None) -> None:
    titled = [index for index, node in enumerate(nodes) if node.name == GET_BOOL and TITLE_EVENT in _field_values(node, DATA_BOOL)]
    if titled:
        raise ModError(
            f"{label}: already patched. Node {titled[0]} is {GET_BOOL} with {DATA_BOOL} {TITLE_EVENT}. "
            "Build from a vanilla logic module."
        )
    if not instance:
        return
    for index, node in _matching(nodes, instance):
        delete = next((param for param in node.params.bool_inputs if param.name == DELETE_PARAM), None)
        if delete is None or not hasattr(delete.source, "src_node_index"):
            continue
        source = delete.source.src_node_index
        if source >= 0 and 0 <= source < len(nodes) and TITLE_EVENT in _field_values(nodes[source], DATA_BOOL):
            raise ModError(
                f"{label}: already patched. Node {index} {DELETE_PARAM} reads {TITLE_EVENT} on node {source}. "
                "Build from a vanilla logic module."
            )


def _find_actor(nodes, label: str, instance: str):
    found = _matching(nodes, instance)
    if len(found) != 1:
        shown = ", ".join(
            f"{index}:{_instance_names(node) or '-'}" for index, node in enumerate(nodes) if _instance_names(node)
        )
        raise ModError(
            f"{label}: expected one node whose InstanceName is or starts with {instance!r}, "
            f"found {len(found)}. Instances: {shown or 'none'}."
        )
    return found[0]


def _matching(nodes, instance: str) -> list[tuple[int, object]]:
    found = []
    for index, node in enumerate(nodes):
        names = _instance_names(node)
        if any(name == instance or name.startswith(instance) for name in names):
            found.append((index, node))
    return found


def _find_template(nodes):
    found = [
        (index, node)
        for index, node in enumerate(nodes)
        if node.name == GET_BOOL
        and _field_values(node, DATA_BOOL)
        and TITLE_EVENT not in _field_values(node, DATA_BOOL)
        and node.flags.is_query()
        and node.params.bool_outputs
    ]
    return found[0] if found else None


def _set_plug(node, name: str, index: int) -> None:
    from ainb.node import ChildPlug, GenericPlug

    found = [plug for group in node._plugs for plug in group if getattr(plug, "name", None) == name]
    if found:
        for plug in found:
            plug.node_index = index
        return
    create = [plug for group in node._plugs for plug in group if getattr(plug, "name", None) == CREATE_PARAM]
    if create and isinstance(create[0], GenericPlug):
        plug = GenericPlug()
        node.generic_plugs.append(plug)
    else:
        plug = ChildPlug()
        node.child_plugs.append(plug)
    plug.name = name
    plug.node_index = index


def _retarget_source(param, node_index: int, output: int) -> None:
    from ainb.param import ParamSource

    current = param.source[0] if isinstance(param.source, list) else param.source
    flags = current.flags if hasattr(current, "flags") else 0
    new = ParamSource(node_index, output, _flag(flags))
    if isinstance(param.source, list):
        param.source = [new]
    else:
        param.source = new


def _output_index(node, name: str, label: str, index: int) -> int:
    names = [param.name for param in node.params.bool_outputs]
    if name not in names:
        listed = ", ".join(names) if names else "none"
        raise ModError(f"{label}: node {index} {node.name} bool outputs are {listed}, expected {name!r}.")
    return names.index(name)

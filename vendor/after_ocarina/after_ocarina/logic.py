"""Spawn the sitting ocarina Saria when the epilogue flag is set.

The holder is an ``ActorLogicNpc``. Her name is the ``InstanceName`` property
``Npc_Kokiri_Saria_Child_OcarinaHolder_3c76``, not the node name. ``Logic_Create``
reads output 1 of an ``OperateAND``. That AND reads the forest-stone step
(``HoldChallengeCurrentStepCheckOrder``, ``Get_StoneOfForest`` before
``GoToWarpPoint``) and the game-data bool ``Npc_Kokiri_Saria_Child_WarpDone``.
Those strings are properties or input defaults. This patch ORs the AND with
``HoldGameDataGetBoolSimple`` reading ``HavePlayedEvent.TitleEventBase``. That
node is a copy of the vanilla ``GetBoolSimple`` whose ``GameDataNameBool`` is
``HavePlayedEvent.Dm_Kokiri_0150``: the name, the params, and ``GameDataArrayIndex``
stay, and only the game-data string changes. The new ``OperateOR`` is a copy of
a vanilla ``OperateOR``, and its plug list points at the AND. The boot hook
already sets the flag. The standing actor's ``Logic_Delete`` points at that OR.

``ainb`` 0.2.17 loads with ``from_binary``. ``from_dict(as_dict())`` rebuilds
node flags from the named bits and drops the rest, so this module edits the
objects. ``to_binary`` also writes three node-header fields as zero (the
padding byte beside the flags, the discarded unk1 word, and the padding half
after the multi-param count). Those bytes are copied back from the source
for every node that was already in the file, and the two new nodes copy them
from the node they were cloned from.
"""

from __future__ import annotations

import copy
import struct

from after_ocarina.errors import ModError

LOGIC_REL = "Logic/KokiriForest_Child_Level_Npc_Kokiri_Saria_Child_a766.logic.module.ainb"
HOLDER_INDEX = 20
DELETE_INDEX = 14
WARP_INDEX = 21
STEP_INDEX = 22
AND_INDEX = 23
ACTOR_NODE = "ActorLogicNpc"
INSTANCE_PARAM = "InstanceName"
HOLDER_INSTANCE = "Npc_Kokiri_Saria_Child_OcarinaHolder_3c76"
WARP_FLAG = "Npc_Kokiri_Saria_Child_WarpDone"
STEP_NODE = "HoldChallengeCurrentStepCheckOrder"
STONE_STEP = "Get_StoneOfForest"
WARP_STEP = "GoToWarpPoint"
GET_BOOL = "HoldGameDataGetBoolSimple"
DATA_BOOL = "GameDataNameBool"
ARRAY_INDEX = "GameDataArrayIndex"
PLAYED_EVENT = "HavePlayedEvent.Dm_Kokiri_0150"
TITLE_EVENT = "HavePlayedEvent.TitleEventBase"
BOOL_OUTPUT = "Bool"
PLAYED_INDEX = 16
OR_TEMPLATE_INDEX = 6
OR_NAME = "OperateOR"
AND_NAME = "OperateAND"
CREATE_PARAM = "Logic_Create"
DELETE_PARAM = "Logic_Delete"
MULTI_PARAM = "BoolMulti"
_HEADER = 0x74
_COMMAND = 0x18
_HOLD_GUID = "a70e5100-0000-4000-8000-000000000024"
_OR_GUID = "a70e5100-0000-4000-8000-000000000025"


def apply_logic(data: bytes, label: str = LOGIC_REL) -> tuple[bytes, str]:
    """Return the patched module and a one-line report. Raises if the graph is not the one above."""
    file = _load(data, label)
    nodes = file.nodes
    _refuse_if_patched(nodes, label)
    holder_index, holder = _find_holder(nodes, label)
    create = _named_input(holder, CREATE_PARAM, label, holder_index)
    create_from = _only_source(create, label, holder_index, CREATE_PARAM)
    and_index = create_from.src_node_index
    if and_index < 0 or and_index >= len(nodes):
        raise ModError(
            f"{label}: node {holder_index} {CREATE_PARAM} comes from node {and_index}, which is outside the file."
        )
    gate = nodes[and_index]
    if gate.name != AND_NAME:
        raise ModError(
            f"{label}: node {holder_index} {CREATE_PARAM} comes from node {and_index} {gate.name!r}, "
            f"expected {AND_NAME}."
        )
    if not gate.flags.is_query():
        raise ModError(f"{label}: {AND_NAME} node {and_index} is not a query, so a new OR cannot read it.")
    warp_index, warp, warp_source, step_index = _split_and_sources(nodes, gate, and_index, label)
    if not warp.params.bool_outputs:
        raise ModError(f"{label}: node {warp_index} {warp.name!r} has no bool output.")
    if len(warp.params.bool_outputs) <= warp_source.src_output_index:
        raise ModError(
            f"{label}: node {warp_index} {warp.name!r} has no bool output {warp_source.src_output_index}."
        )
    if len(gate.params.bool_outputs) <= create_from.src_output_index:
        raise ModError(
            f"{label}: {AND_NAME} node {and_index} has no bool output {create_from.src_output_index} "
            f"for {CREATE_PARAM} to read."
        )
    delete_index, delete, delete_source = _find_delete(nodes, and_index, label)
    played_index, played = _find_played(nodes, label)
    or_template_index, or_template = _find_or_template(nodes, label)
    if len(or_template.params.bool_outputs) <= create_from.src_output_index:
        names = [param.name for param in or_template.params.bool_outputs] or ["none"]
        raise ModError(
            f"{label}: {OR_NAME} node {or_template_index} has no bool output {create_from.src_output_index} "
            f"({names}), so {CREATE_PARAM} cannot read the copy."
        )

    hold = copy.deepcopy(played)
    hold.guid = _HOLD_GUID
    _set_game_data_bool(hold, TITLE_EVENT, label, played_index)
    hold_index = len(nodes)
    file.nodes.append(hold)
    or_node = copy.deepcopy(or_template)
    or_node.guid = _OR_GUID
    or_node.queries = [and_index, hold_index]
    template = _bool_multi(or_node, label, or_template_index)[0]
    flag = _flag(template.flags)
    _set_bool_multi(
        or_node,
        [
            _source(and_index, create_from.src_output_index, flag),
            _source(hold_index, 0, flag),
        ],
    )
    plug_count = _point_plugs_at(or_node, and_index)
    or_index = len(file.nodes)
    file.nodes.append(or_node)

    create_from.src_node_index = or_index
    holder_queries, holder_plugs = _retarget_links(holder, and_index, or_index)
    delete_source.src_node_index = or_index
    delete_queries, delete_plugs = _retarget_links(delete, and_index, or_index)

    written = file.to_binary()
    written = preserve_node_headers(
        data,
        written,
        version=file.version,
        command_count=len(file.commands),
        node_count=hold_index,
    )
    written = _copy_header_style(
        written,
        version=file.version,
        command_count=len(file.commands),
        src_index=played_index,
        dest_index=hold_index,
    )
    written = _copy_header_style(
        written,
        version=file.version,
        command_count=len(file.commands),
        src_index=or_template_index,
        dest_index=or_index,
    )
    holder_name = _instance_names(holder)[0]
    delete_name = ", ".join(_instance_names(delete)) or delete.name
    note = (
        f"Saria logic: node {holder_index} {ACTOR_NODE} {INSTANCE_PARAM} {holder_name!r} "
        f"{CREATE_PARAM} now comes from {OR_NAME} node {or_index} "
        f"({AND_NAME} node {and_index} output {create_from.src_output_index} OR "
        f"{GET_BOOL} node {hold_index} output 0). "
        f"Node {delete_index} ({delete_name}) {DELETE_PARAM} first source is that OR. "
        f"Added {GET_BOOL} node {hold_index} ({DATA_BOOL} {TITLE_EVENT}), "
        f"cloned from node {played_index} without renaming it. "
        f"{OR_NAME} node {or_index} is cloned from node {or_template_index}; "
        f"its plug list ({plug_count}) points at node {and_index}. "
        f"Step check is node {step_index}. Warp flag is node {warp_index} {warp.name!r}. "
        f"Queries updated on node {holder_index} ({holder_queries}) and node {delete_index} ({delete_queries}); "
        f"plugs retargeted: {holder_plugs} and {delete_plugs}."
    )
    return written, note


def roundtrip_logic(data: bytes, label: str = LOGIC_REL) -> bytes:
    """Load and write without editing the graph, restoring the header bytes ``to_binary`` zeroes."""
    file = _load(data, label)
    return preserve_node_headers(
        data,
        file.to_binary(),
        version=file.version,
        command_count=len(file.commands),
        node_count=len(file.nodes),
    )


def preserve_node_headers(
    original: bytes,
    written: bytes,
    *,
    version: int,
    command_count: int,
    node_count: int,
) -> bytes:
    """Copy the three header fields ``Node._write`` hardcodes to zero back onto existing nodes."""
    out = bytearray(written)
    size = _node_size(version)
    base = _node_base(command_count)
    pad_at, unk_at, tail_at = _header_fields(version)
    for index in range(node_count):
        src = base + index * size
        if src + size > len(original) or src + tail_at + 2 > len(out):
            raise ModError("logic file node headers moved; the writer layout does not match this file")
        out[src + pad_at] = original[src + pad_at]
        out[src + unk_at : src + unk_at + 4] = original[src + unk_at : src + unk_at + 4]
        out[src + tail_at : src + tail_at + 2] = original[src + tail_at : src + tail_at + 2]
    return bytes(out)


def sample_logic_bytes(
    *,
    played_as: str = "input",
    holder_at: int = HOLDER_INDEX,
) -> bytes:
    """A tiny module shaped like the real Kokiri child logic file.

    It is built for the tests. It is not a game file. Actors are ``UserDefined``
    nodes named ``ActorLogicNpc``; the ocarina holder is ``InstanceName``.
    ``Logic_Create`` reads bool output 1 of ``OperateAND``. The warp flag is a
    ``HoldGameDataGetBoolSimple`` ``GameDataNameBool``. Another ``GetBoolSimple``
    reads ``HavePlayedEvent.Dm_Kokiri_0150`` and keeps an Int ``GameDataArrayIndex``
    and a ``Bool`` output. ``played_as`` is ``input`` or ``property``, where that
    string is stored. An existing ``OperateOR`` supplies the plug list the patch
    copies. ``holder_at`` moves the holder off index 20.
    """
    if played_as not in ("property", "input"):
        raise ValueError(played_as)
    from ainb import AINB
    from ainb.blackboard import Blackboard
    from ainb.command import Command
    from ainb.node import ChildPlug, Node, NodeFlag, NodeType
    from ainb.param import InputParam, OutputParam, ParamSource
    from ainb.param_common import ParamFlag, ParamType
    from ainb.property import Property

    file = AINB()
    file.version = 0x407
    file.filename = "KokiriForest_Child_Level_Npc_Kokiri_Saria_Child_a766.logic.module"
    file.category = "Logic"
    file.blackboard = Blackboard()
    file.exists_section_0x6c = True
    command = Command()
    command.name = "Root"
    command.guid = "11111111-1111-1111-1111-111111111111"
    command.root_node_index = HOLDER_INDEX
    file.commands.append(command)

    def make(name: str, *, query: bool = False, flags: int = 0) -> Node:
        node = Node(NodeType.UserDefined)
        node.name = name
        node.flags = NodeFlag((1 if query else 0) | flags)
        node.guid = "22222222-2222-2222-2222-222222222222"
        return node

    names = {
        OR_TEMPLATE_INDEX: (OR_NAME, True, 0),
        DELETE_INDEX: (ACTOR_NODE, True, 0),
        13: ("OtherBool", True, 0),
        15: ("Goto_HyruleCastle_IsCompleted_Exp", True, 0),
        PLAYED_INDEX: (GET_BOOL, True, 0x20),
        HOLDER_INDEX: (ACTOR_NODE, True, 0),
        WARP_INDEX: (GET_BOOL, True, 0),
        STEP_INDEX: (STEP_NODE, True, 0),
        AND_INDEX: (AND_NAME, True, 0),
    }
    nodes: list[Node] = []
    for index in range(AND_INDEX + 1):
        name, query, extra = names.get(index, (f"Filler{index:02d}", False, 0))
        node = make(name, query=query, flags=extra)
        node.index = index
        nodes.append(node)
    file.nodes = nodes

    def boolean_output(name: str = "Result") -> OutputParam:
        param = OutputParam(ParamType.Bool)
        param.name = name
        param.is_output = True
        return param

    def link(index: int, output: int = 1) -> ParamSource:
        return ParamSource(index, output, ParamFlag())

    def multi(name: str, sources: list[ParamSource]) -> InputParam:
        param = InputParam(ParamType.Bool)
        param.name = name
        param.default_value = False
        param.source = sources
        return param

    def string_property(node: Node, name: str, value: str) -> None:
        prop = Property(ParamType.String)
        prop.name = name
        prop.default_value = value
        prop.flags = ParamFlag().set_uses_default()
        node.properties.string_properties.append(prop)

    def integer_input(node: Node, name: str, value: int) -> None:
        param = InputParam(ParamType.Int)
        param.name = name
        param.default_value = value
        param.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
        node.params.int_inputs.append(param)

    def string_input(node: Node, name: str, value: str) -> None:
        param = InputParam(ParamType.String)
        param.name = name
        param.default_value = value
        param.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
        node.params.string_inputs.append(param)

    for index in (13, 15, STEP_INDEX, WARP_INDEX, AND_INDEX, OR_TEMPLATE_INDEX):
        nodes[index].params.bool_outputs.append(boolean_output("Result"))
        nodes[index].params.bool_outputs.append(boolean_output("Passed"))
    nodes[PLAYED_INDEX].params.bool_outputs.append(boolean_output(BOOL_OUTPUT))
    nodes[OR_TEMPLATE_INDEX].params.bool_inputs.append(multi(MULTI_PARAM, [link(13), link(15)]))
    nodes[OR_TEMPLATE_INDEX].queries = [13, 15]
    template_plug = ChildPlug()
    template_plug.name = MULTI_PARAM
    template_plug.node_index = 13
    nodes[OR_TEMPLATE_INDEX].child_plugs.append(template_plug)
    nodes[AND_INDEX].params.bool_inputs.append(multi(MULTI_PARAM, [link(STEP_INDEX), link(WARP_INDEX)]))
    nodes[AND_INDEX].queries = [STEP_INDEX, WARP_INDEX]
    nodes[DELETE_INDEX].params.bool_inputs.append(
        multi(DELETE_PARAM, [link(AND_INDEX), link(15), link(13)])
    )
    nodes[DELETE_INDEX].queries = [AND_INDEX, 15, 13]
    string_property(nodes[DELETE_INDEX], INSTANCE_PARAM, "Npc_Kokiri_Saria_Child")
    create = InputParam(ParamType.Bool)
    create.name = CREATE_PARAM
    create.default_value = False
    create.source = link(AND_INDEX)
    nodes[HOLDER_INDEX].params.bool_inputs.append(create)
    untouched = InputParam(ParamType.Bool)
    untouched.name = DELETE_PARAM
    untouched.default_value = False
    untouched.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
    nodes[HOLDER_INDEX].params.bool_inputs.append(untouched)
    nodes[HOLDER_INDEX].queries = [AND_INDEX]
    string_property(nodes[HOLDER_INDEX], INSTANCE_PARAM, HOLDER_INSTANCE)
    plug = ChildPlug()
    plug.name = CREATE_PARAM
    plug.node_index = AND_INDEX
    nodes[HOLDER_INDEX].child_plugs.append(plug)
    delete_plug = ChildPlug()
    delete_plug.name = DELETE_PARAM
    delete_plug.node_index = AND_INDEX
    nodes[DELETE_INDEX].child_plugs.append(delete_plug)
    string_property(nodes[STEP_INDEX], "CurrentStep", STONE_STEP)
    string_property(nodes[STEP_INDEX], "NextStep", WARP_STEP)
    integer_input(nodes[WARP_INDEX], ARRAY_INDEX, 1)
    string_input(nodes[WARP_INDEX], DATA_BOOL, WARP_FLAG)
    integer_input(nodes[PLAYED_INDEX], ARRAY_INDEX, 0)
    if played_as == "property":
        string_property(nodes[PLAYED_INDEX], DATA_BOOL, PLAYED_EVENT)
    else:
        string_input(nodes[PLAYED_INDEX], DATA_BOOL, PLAYED_EVENT)
    if holder_at != HOLDER_INDEX:
        nodes[HOLDER_INDEX], nodes[holder_at] = nodes[holder_at], nodes[HOLDER_INDEX]
        nodes[HOLDER_INDEX].index = HOLDER_INDEX
        nodes[holder_at].index = holder_at
        command.root_node_index = holder_at
    return file.to_binary()


def _load(data: bytes, label: str):
    if not data.startswith(b"AIB "):
        raise ModError(f"{label}: not an AINB (header {data[:4]!r}).")
    from ainb import AINB

    try:
        return AINB.from_binary(data, reader_name=label)
    except AssertionError:
        raise ModError(
            f"{label}: ainb 0.2.17 refused this file. That library asserts when header offset 0x6c is zero."
        ) from None
    except Exception as exc:
        raise ModError(f"{label}: {exc}") from None


def _refuse_if_patched(nodes, label: str) -> None:
    """Refuse a module this patch already edited.

    Vanilla ``HoldGameDataGetBoolSimple`` reads ``HavePlayedEvent.Dm_Kokiri_0150``.
    Ours is the ``GetBoolSimple`` whose ``GameDataNameBool`` is
    ``HavePlayedEvent.TitleEventBase``, or the ``OperateOR`` that the holder's
    ``Logic_Create`` reads when that OR includes such a node.
    """
    holders = _holders(nodes)
    if len(holders) == 1:
        _holder_index, holder = holders[0]
        source = _create_source_index(holder)
        if source is not None and 0 <= source < len(nodes) and nodes[source].name == OR_NAME:
            for upstream in _multi_sources(nodes[source]):
                if 0 <= upstream < len(nodes) and _is_title_flag(nodes[upstream]):
                    raise ModError(
                        f"{label}: already patched. Node {_holder_index} {CREATE_PARAM} points at {OR_NAME} "
                        f"node {source}, which reads {GET_BOOL} {TITLE_EVENT} on node {upstream}. "
                        "Build from a vanilla dump of this module."
                    )
    titled = [index for index, node in enumerate(nodes) if _is_title_flag(node)]
    if titled:
        raise ModError(
            f"{label}: already patched. Node {titled[0]} is {GET_BOOL} with {DATA_BOOL} {TITLE_EVENT}. "
            "Build from a vanilla dump of this module."
        )


def _is_title_flag(node) -> bool:
    return node.name == GET_BOOL and TITLE_EVENT in _field_values(node, DATA_BOOL)


def _create_source_index(node) -> int | None:
    from ainb.param import ParamSource
    from ainb.param_common import ParamType

    found = [
        param
        for param_type in ParamType
        for param in node.params.get_inputs(param_type)
        if param.name == CREATE_PARAM
    ]
    if len(found) != 1:
        return None
    source = found[0].source
    if isinstance(source, list):
        if len(source) != 1:
            return None
        source = source[0]
    if not isinstance(source, ParamSource):
        return None
    return source.src_node_index


def _multi_sources(node) -> list[int]:
    from ainb.param_common import ParamType

    found = [
        param
        for param_type in ParamType
        for param in node.params.get_inputs(param_type)
        if param.name == MULTI_PARAM and isinstance(param.source, list)
    ]
    if len(found) != 1:
        return []
    return [source.src_node_index for source in found[0].source]


def _string_fields(node) -> list[tuple[str, str]]:
    fields = [
        (prop.name, prop.default_value)
        for prop in node.properties.string_properties
        if isinstance(prop.default_value, str)
    ]
    fields.extend(
        (param.name, param.default_value)
        for param in node.params.string_inputs
        if isinstance(param.default_value, str)
    )
    return fields


def _field_values(node, name: str) -> list[str]:
    return [value for key, value in _string_fields(node) if key == name]


def _has_text(node, text: str) -> bool:
    return any(value == text for _key, value in _string_fields(node))


def _instance_names(node) -> list[str]:
    return _field_values(node, INSTANCE_PARAM)


def _holders(nodes) -> list[tuple[int, object]]:
    return [
        (index, node)
        for index, node in enumerate(nodes)
        if any("OcarinaHolder" in value for value in _instance_names(node))
    ]


def _find_holder(nodes, label: str):
    found = _holders(nodes)
    if len(found) == 1:
        index, node = found[0]
        if node.name != ACTOR_NODE:
            raise ModError(
                f"{label}: node {index} {INSTANCE_PARAM} is the ocarina holder, "
                f"but the node name is {node.name!r}, expected {ACTOR_NODE}."
            )
        return index, node
    actors = [
        f"{index}:{node.name} {INSTANCE_PARAM}={_instance_names(node) or '-'}"
        for index, node in enumerate(nodes)
        if node.name == ACTOR_NODE or _instance_names(node)
    ]
    listed = ", ".join(actors) if actors else "none"
    raise ModError(
        f"{label}: expected one {ACTOR_NODE} whose {INSTANCE_PARAM} contains 'OcarinaHolder', "
        f"found {len(found)}. Actors: {listed}."
    )


def _split_and_sources(nodes, gate, and_index: int, label: str):
    sources = _bool_multi(gate, label, and_index)
    warp = None
    step = None
    extras: list[int] = []
    for source in sources:
        index = source.src_node_index
        if index < 0 or index >= len(nodes):
            extras.append(index)
            continue
        node = nodes[index]
        if _has_text(node, WARP_FLAG):
            warp = (index, node, source)
        elif node.name == STEP_NODE or (_has_text(node, STONE_STEP) and _has_text(node, WARP_STEP)):
            step = index
        else:
            extras.append(index)
    if warp is None or step is None or extras:
        details = []
        for source in sources:
            index = source.src_node_index
            node = nodes[index] if 0 <= index < len(nodes) else None
            shown = f"{index}:{node.name} {_string_fields(node)}" if node is not None else str(index)
            details.append(shown)
        raise ModError(
            f"{label}: {AND_NAME} node {and_index} should read {WARP_FLAG} and "
            f"{STEP_NODE} ({STONE_STEP} before {WARP_STEP}). Sources: {'; '.join(details)}."
        )
    warp_index, warp_node, warp_source = warp
    return warp_index, warp_node, warp_source, step


def _find_delete(nodes, and_index: int, label: str):
    """The standing Saria's delete: a multi whose first source is the AND, or an OR she points at."""
    direct = []
    for index, node in enumerate(nodes):
        if index == and_index:
            continue
        for name in (DELETE_PARAM, MULTI_PARAM):
            param = _bool_input(node, name)
            if param is None or not isinstance(param.source, list) or not param.source:
                continue
            if name == MULTI_PARAM and node.name != OR_NAME:
                continue
            if param.source[0].src_node_index == and_index:
                direct.append((index, node, param.source[0]))
    if len(direct) > 1:
        listed = ", ".join(f"{index}:{node.name}" for index, node, _source in direct)
        raise ModError(
            f"{label}: more than one delete reads {AND_NAME} node {and_index} first ({listed})."
        )
    if len(direct) == 1:
        return direct[0]
    pointed = []
    for index, node in enumerate(nodes):
        param = _bool_input(node, DELETE_PARAM)
        if param is None or isinstance(param.source, list):
            continue
        target = param.source.src_node_index
        if target < 0 or target >= len(nodes) or nodes[target].name != OR_NAME:
            continue
        multi = _bool_input(nodes[target], MULTI_PARAM)
        if multi is None or not isinstance(multi.source, list) or not multi.source:
            continue
        if multi.source[0].src_node_index == and_index:
            pointed.append((target, nodes[target], multi.source[0], index))
    if len(pointed) == 1:
        target, node, source, _actor = pointed[0]
        return target, node, source
    listed = ", ".join(
        f"{index}:{node.name} {INSTANCE_PARAM}={_instance_names(node) or '-'}"
        for index, node in enumerate(nodes)
        if node.name in (ACTOR_NODE, OR_NAME)
    )
    raise ModError(
        f"{label}: no {DELETE_PARAM} or {OR_NAME} starts at {AND_NAME} node {and_index}. "
        f"Actor and OR nodes: {listed or 'none'}."
    )


def _bool_input(node, name: str):
    found = [param for param in node.params.bool_inputs if param.name == name]
    if len(found) != 1:
        return None
    return found[0]


def _set_bool_multi(node, sources: list) -> None:
    param = _bool_input(node, MULTI_PARAM)
    if param is None:
        raise ModError(f"cloned {node.name} has no {MULTI_PARAM} input.")
    param.source = sources


def _named_input(node, name: str, label: str, index: int):
    from ainb.param_common import ParamType

    found = []
    for param_type in ParamType:
        found.extend(param for param in node.params.get_inputs(param_type) if param.name == name)
    if len(found) != 1:
        names = [param.name for param_type in ParamType for param in node.params.get_inputs(param_type)]
        listed = ", ".join(names) if names else "none"
        raise ModError(f"{label}: node {index} {node.name!r} has {len(found)} {name} inputs ({listed}).")
    return found[0]


def _only_source(param, label: str, index: int, name: str):
    from ainb.param import ParamSource

    if isinstance(param.source, list):
        if len(param.source) != 1:
            raise ModError(f"{label}: node {index} {name} has {len(param.source)} sources.")
        return param.source[0]
    if not isinstance(param.source, ParamSource):
        raise ModError(f"{label}: node {index} {name} has no source.")
    return param.source


def _bool_multi(node, label: str, index: int) -> list:
    param = _named_input(node, MULTI_PARAM, label, index)
    if not isinstance(param.source, list) or not param.source:
        raise ModError(f"{label}: node {index} {MULTI_PARAM} is not a multi-source input.")
    return param.source


def _find_played(nodes, label: str):
    """The GetBoolSimple that already reads HavePlayedEvent.Dm_Kokiri_0150."""
    found = [
        (index, node)
        for index, node in enumerate(nodes)
        if node.name == GET_BOOL and PLAYED_EVENT in _field_values(node, DATA_BOOL)
    ]
    if len(found) != 1:
        shown = [
            f"{index}:{node.name} {DATA_BOOL}={_field_values(node, DATA_BOOL) or '-'}"
            for index, node in enumerate(nodes)
            if node.name == GET_BOOL or _field_values(node, DATA_BOOL)
        ]
        raise ModError(
            f"{label}: expected one {GET_BOOL} whose {DATA_BOOL} is {PLAYED_EVENT!r}, "
            f"found {len(found)}. {'; '.join(shown) or 'none'}."
        )
    index, node = found[0]
    if not node.flags.is_query():
        raise ModError(f"{label}: node {index} {GET_BOOL} is not a query, so the new OR cannot read a copy of it.")
    outputs = [param.name for param in node.params.bool_outputs]
    if not outputs or outputs[0] != BOOL_OUTPUT:
        raise ModError(
            f"{label}: node {index} {GET_BOOL} bool outputs are {outputs or ['none']}, "
            f"expected {BOOL_OUTPUT!r} at index 0."
        )
    if not any(param.name == ARRAY_INDEX for param in node.params.int_inputs):
        names = [param.name for param in node.params.int_inputs] or ["none"]
        raise ModError(f"{label}: node {index} {GET_BOOL} has no Int {ARRAY_INDEX} ({names}).")
    return index, node


def _find_or_template(nodes, label: str):
    """A vanilla OperateOR to copy, plugs included. Not a fixed index."""
    named = [(index, node) for index, node in enumerate(nodes) if node.name == OR_NAME]
    usable = [
        (index, node)
        for index, node in named
        if node.flags.is_query() and _bool_input(node, MULTI_PARAM) is not None
    ]
    if not usable:
        listed = ", ".join(f"{index}:{node.name}" for index, node in named) or "none"
        raise ModError(f"{label}: no {OR_NAME} with a {MULTI_PARAM} input to clone ({listed}).")
    with_plugs = [(index, node) for index, node in usable if any(group for group in node._plugs)]
    return (with_plugs or usable)[0]


def _set_game_data_bool(node, value: str, label: str, index: int) -> None:
    found = [item for item in node.properties.string_properties if item.name == DATA_BOOL]
    found.extend(item for item in node.params.string_inputs if item.name == DATA_BOOL)
    if len(found) != 1:
        raise ModError(f"{label}: node {index} {node.name!r} has {len(found)} {DATA_BOOL} fields.")
    found[0].default_value = value


def _point_plugs_at(node, index: int) -> int:
    """Point the copied OR's plug list at its first BoolMulti source."""
    from ainb.node import ChildPlug

    count = 0
    for group in node._plugs:
        for plug in group:
            plug.node_index = index
            count += 1
    if count == 0:
        plug = ChildPlug()
        plug.name = MULTI_PARAM
        plug.node_index = index
        node.child_plugs.append(plug)
        count = 1
    return count


def _retarget_links(node, old: int, new: int) -> tuple[int, int]:
    updated: list[int] = []
    replaced = 0
    for item in node.queries:
        chosen = new if item == old else item
        if item == old:
            replaced += 1
        if chosen not in updated:
            updated.append(chosen)
    if new not in updated:
        updated.append(new)
    node.queries = updated
    plugs = 0
    for group in node._plugs:
        for plug in group:
            if plug.node_index == old:
                plug.node_index = new
                plugs += 1
    return replaced, plugs


def _source(index: int, output: int, flags):
    from ainb.param import ParamSource

    return ParamSource(index, output, _flag(flags))


def _flag(value):
    from ainb.param_common import ParamFlag

    return ParamFlag(int(value))


def _copy_header_style(written: bytes, *, version: int, command_count: int, src_index: int, dest_index: int) -> bytes:
    out = bytearray(written)
    size = _node_size(version)
    base = _node_base(command_count)
    pad_at, unk_at, tail_at = _header_fields(version)
    src = base + src_index * size
    dest = base + dest_index * size
    if dest + tail_at + 2 > len(out) or src + tail_at + 2 > len(out):
        raise ModError("logic file is missing a node header to copy.")
    out[dest + pad_at] = out[src + pad_at]
    out[dest + unk_at : dest + unk_at + 4] = out[src + unk_at : src + unk_at + 4]
    out[dest + tail_at : dest + tail_at + 2] = out[src + tail_at : src + tail_at + 2]
    return bytes(out)


def _node_base(command_count: int) -> int:
    return _HEADER + _COMMAND * command_count


def _node_size(version: int) -> int:
    return 0x3C if version > 0x404 else 0x38


def _header_fields(version: int) -> tuple[int, int, int]:
    if version > 0x404:
        return 7, 0x10, 0x1E
    return 7, 0x0C, 0x1A


def header_bytes(data: bytes, index: int, *, version: int = 0x407, command_count: int = 1) -> tuple[int, int, int]:
    """Padding byte, unk1, and the trailing padding half for one node. Tests use this."""
    base = _node_base(command_count) + index * _node_size(version)
    pad_at, unk_at, tail_at = _header_fields(version)
    pad = data[base + pad_at]
    unk = struct.unpack_from("<I", data, base + unk_at)[0]
    tail = struct.unpack_from("<H", data, base + tail_at)[0]
    return pad, unk, tail

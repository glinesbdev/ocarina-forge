"""Close the Door of Time, and remove the Master Sword, in the epilogue.

``Logic/Market_Common_f252.logic.module.ainb`` is shared with the adult
banc. Node 5 is ``OperateORFixInputTrue``. Its output 1 is what opens the
door and creates the Spiritual Stones. This patch leaves that node alone
and feeds ``AND(output 1, NOT TitleEventBase)`` to those inputs. A false
flag makes the AND equal output 1.

The NOT is a plain ``OperateOR`` with one input, read on its False output.
``OperateORFixInputTrue`` is not that OR: a fixed true input would make the
complement constantly false and close the door on a new game. f252 has no
plain ``OperateOR`` and no ``OperateAND``. Both are cloned from the vanilla
Saria module, the same cross-file donor the courtyard deletes use. The flag
query is cloned from node 6 in f252, which reads
``Market_Child_TimeTempleDoor_Opened`` and which nothing consumes.

``Logic/Market_Child_Prefab_e700`` node 6 is the Master Sword. Its delete
goes through ``castle.apply_delete``. The shared DoorOfTime BoolExps are
not edited.
"""

from __future__ import annotations

from after_ocarina.castle import (
    _copy_header_from,
    _copy_template,
    _flag,
    _set_plug,
)
from after_ocarina.errors import ModError
from after_ocarina.logic import (
    AND_NAME,
    BOOL_OUTPUT,
    CREATE_PARAM,
    DATA_BOOL,
    GET_BOOL,
    LOGIC_REL as DONOR_REL,
    MULTI_PARAM,
    OR_NAME,
    TITLE_EVENT,
    _bool_input,
    _bool_multi,
    _copy_header_style,
    _field_values,
    _find_or_template,
    _load,
    _named_input,
    _only_source,
    _point_plugs_at,
    _set_bool_multi,
    _set_game_data_bool,
    _source,
    preserve_node_headers,
)

DOOR_LOGIC = "Logic/Market_Common_f252.logic.module.ainb"
SWORD_LOGIC = "Logic/Market_Child_Prefab_e700.logic.module.ainb"
FIX_OR = "OperateORFixInputTrue"
DOOR_ACTOR = "ActorLogicGateDoor"
SWORD_ACTOR = "ActorLogicAttentionActor"
OPENED_FLAG = "Market_Child_TimeTempleDoor_Opened"
OPEN_PARAM = "isOpen"
SKIP_PARAM = "IsSkipAnim"
CLOSE_PARAM = "isClose"
SIGNAL_NODE = 5
SIGNAL_OUTPUT = 1
DOOR_INDEX = 3
FLAG_TEMPLATE = 6
# Logic-module nodes, not Market_Child_Static actor indices. All three
# Logic_Create inputs are rewired together. Which node is which stone does not matter.
STONE_INDICES = (0, 1, 2)
AND_RESULT = 1
NOT_OUTPUT = 0
_HOLD_GUID = "a70e5100-0000-4000-8000-000000000030"
_NOT_GUID = "a70e5100-0000-4000-8000-000000000031"
_AND_GUID = "a70e5100-0000-4000-8000-000000000032"


def apply_temple_door(
    data: bytes,
    label: str = DOOR_LOGIC,
    donor: bytes | None = None,
    donor_label: str = DONOR_REL,
) -> tuple[bytes, str]:
    """AND the door-open signal with NOT TitleEventBase. Node 5 is not edited."""
    file = _load(data, label)
    nodes = file.nodes
    _refuse_if_patched(nodes, label)
    _require_signal(nodes, label)
    template = nodes[FLAG_TEMPLATE]
    _require_flag_template(template, label)
    if _consumed(nodes, FLAG_TEMPLATE):
        raise ModError(
            f"{label}: node {FLAG_TEMPLATE} {GET_BOOL} is already read. "
            "It was the unused donor for the flag query."
        )
    readers = _signal_readers(nodes, label)
    _require_readers(readers, label)
    close = _named_input(nodes[DOOR_INDEX], CLOSE_PARAM, label, DOOR_INDEX)
    close_from = _only_source(close, label, DOOR_INDEX, CLOSE_PARAM)
    if close_from.src_node_index >= 0 or close.default_value is not True:
        raise ModError(
            f"{label}: node {DOOR_INDEX} {CLOSE_PARAM} is not constant true "
            f"({close_from.src_node_index}, {close.default_value!r})."
        )
    if donor is None:
        raise ModError(f"{label}: the {AND_NAME} and {OR_NAME} donors were not loaded from {donor_label}.")
    donor_file = _load(donor, donor_label)

    hold, how = _copy_template(template, label, FLAG_TEMPLATE)
    hold.guid = _HOLD_GUID
    hold.index = len(nodes)
    _set_game_data_bool(hold, TITLE_EVENT, label, FLAG_TEMPLATE)
    hold.queries = []
    for group in hold._plugs:
        group.clear()
    hold_index = len(nodes)
    file.nodes.append(hold)

    not_template_index, not_template, not_origin = _plain_or(nodes, donor_file, donor_label)
    not_node = _clone_gate(not_template, _NOT_GUID, [(hold_index, 0)])
    not_index = len(file.nodes)
    not_node.index = not_index
    file.nodes.append(not_node)
    _point_plugs_at(not_node, hold_index)

    and_template_index, and_template, and_origin = _plain_and(nodes, donor_file, donor_label)
    if len(and_template.params.bool_outputs) <= AND_RESULT:
        names = [param.name for param in and_template.params.bool_outputs] or ["none"]
        raise ModError(
            f"{and_origin}: {AND_NAME} node {and_template_index} has no bool output {AND_RESULT} ({names})."
        )
    and_node = _clone_gate(and_template, _AND_GUID, [(SIGNAL_NODE, SIGNAL_OUTPUT), (not_index, NOT_OUTPUT)])
    and_index = len(file.nodes)
    and_node.index = and_index
    file.nodes.append(and_node)
    _point_plugs_at(and_node, SIGNAL_NODE)

    by_node: dict[int, list[str]] = {}
    for index, _node, param in readers:
        _retarget_source(param, and_index, AND_RESULT)
        by_node.setdefault(index, []).append(param.name)
    for index, names in by_node.items():
        _retarget_reader(file.nodes[index], SIGNAL_NODE, and_index, names)

    original_count = hold_index
    written = file.to_binary()
    written = preserve_node_headers(
        data,
        written,
        version=file.version,
        command_count=len(file.commands),
        node_count=original_count,
    )
    written = _copy_header_style(
        written,
        version=file.version,
        command_count=len(file.commands),
        src_index=FLAG_TEMPLATE,
        dest_index=hold_index,
    )
    written = _copy_new_header(
        written, file, not_index, not_template_index, not_origin, donor, donor_file
    )
    written = _copy_new_header(
        written, file, and_index, and_template_index, and_origin, donor, donor_file
    )
    _check_door(written, label, and_index, not_index, hold_index)
    note = (
        f"Temple door: {FIX_OR} node {SIGNAL_NODE} output {SIGNAL_OUTPUT} is unchanged. "
        f"{AND_NAME} node {and_index} is that output AND the False output of {OR_NAME} node {not_index}. "
        f"That OR reads only {GET_BOOL} node {hold_index} ({TITLE_EVENT}), cloned from node {FLAG_TEMPLATE} ({how}). "
        f"The {OR_NAME} comes from {not_origin} node {not_template_index}. "
        f"The {AND_NAME} comes from {and_origin} node {and_template_index}. "
        f"isOpen, IsSkipAnim, and the stones' {CREATE_PARAM} read the AND output {AND_RESULT}. "
        f"{CLOSE_PARAM} stays constant true. A false flag leaves every rewired input equal to node {SIGNAL_NODE} "
        f"output {SIGNAL_OUTPUT}."
    )
    return written, note


def reduced_signal(vanilla: bool, flag: bool) -> bool:
    """What a rewired input becomes. Output 0 of a one-input OperateOR is NOT that input.

    Output 1 of OperateAND is the AND of its inputs. Both match the courtyard
    gate, where False is 0 and True is 1, and the Saria holder, which reads
    OperateAND output 1.
    """
    return vanilla and (not flag)


def sample_door_logic_bytes() -> bytes:
    """Nine nodes shaped like Market_Common_f252. Built for the tests. Not a game file."""
    from ainb import AINB
    from ainb.blackboard import Blackboard
    from ainb.command import Command
    from ainb.node import GenericPlug, Node, NodeFlag, NodeType
    from ainb.param import InputParam, OutputParam, ParamSource
    from ainb.param_common import ParamFlag, ParamType
    from ainb.property import Property

    file = AINB()
    file.version = 0x407
    file.filename = "Market_Common_f252.logic.module"
    file.category = "Logic"
    file.blackboard = Blackboard()
    file.exists_section_0x6c = True
    command = Command()
    command.name = "Root"
    command.guid = "11111111-1111-1111-1111-111111111111"
    command.root_node_index = DOOR_INDEX
    file.commands.append(command)

    def make(name: str, *, query: bool = True) -> Node:
        node = Node(NodeType.UserDefined)
        node.name = name
        node.flags = NodeFlag(1 if query else 0)
        node.guid = "44444444-4444-4444-4444-444444444444"
        return node

    names = (
        "ActorLogicSimpleActor",
        "ActorLogicSimpleActor",
        "ActorLogicSimpleActor",
        DOOR_ACTOR,
        "HoldGameDataGetBool",
        FIX_OR,
        GET_BOOL,
        "Filler",
        "HoldChallengeCurrentStepCheckOrder",
    )
    file.nodes = [make(name, query=name != "Filler") for name in names]
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

    def plug(node: Node, name: str, index: int) -> None:
        item = GenericPlug()
        item.name = name
        item.node_index = index
        node.generic_plugs.append(item)

    def string_input(node: Node, name: str, value: str) -> None:
        param = InputParam(ParamType.String)
        param.name = name
        param.default_value = value
        param.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
        node.params.string_inputs.append(param)

    def integer_input(node: Node, name: str, value: int) -> None:
        param = InputParam(ParamType.Int)
        param.name = name
        param.default_value = value
        param.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
        node.params.int_inputs.append(param)

    def string_property(node: Node, name: str, value: str) -> None:
        prop = Property(ParamType.String)
        prop.name = name
        prop.default_value = value
        prop.flags = ParamFlag().set_uses_default()
        node.properties.string_properties.append(prop)

    signal = link(SIGNAL_NODE, SIGNAL_OUTPUT)
    for index, stone in enumerate(("SpiritualStone_Kokiri", "SpiritualStone_Goron", "SpiritualStone_Zora")):
        node = file.nodes[index]
        node.params.bool_inputs.append(bool_input(CREATE_PARAM, signal))
        node.queries = [SIGNAL_NODE]
        plug(node, CREATE_PARAM, SIGNAL_NODE)
        string_property(node, "InstanceName", stone)
    door = file.nodes[DOOR_INDEX]
    door.params.bool_inputs.extend(
        (
            bool_input(OPEN_PARAM, signal),
            bool_input(SKIP_PARAM, signal),
            bool_input(CLOSE_PARAM, ParamSource(-1, 0, ParamFlag().set_uses_default()), True),
        )
    )
    door.queries = [SIGNAL_NODE]
    plug(door, OPEN_PARAM, SIGNAL_NODE)
    plug(door, SKIP_PARAM, SIGNAL_NODE)
    proxy = file.nodes[4]
    proxy.params.bool_outputs.extend((boolean_output("Unused"), boolean_output("Bool")))
    gate = file.nodes[SIGNAL_NODE]
    gate.params.bool_outputs.extend((boolean_output("False"), boolean_output("True")))
    multi = InputParam(ParamType.Bool)
    multi.name = MULTI_PARAM
    multi.default_value = False
    multi.source = [link(4, 1), link(8, 0), link(8, 2)]
    gate.params.bool_inputs.append(multi)
    gate.queries = [4, 8]
    plug(gate, MULTI_PARAM, 4)
    opened = file.nodes[FLAG_TEMPLATE]
    opened.params.bool_outputs.append(boolean_output(BOOL_OUTPUT))
    string_input(opened, DATA_BOOL, OPENED_FLAG)
    integer_input(opened, "GameDataArrayIndex", 0)
    step = file.nodes[8]
    for name in ("IsAfter", "IsBefore", "IsEqual"):
        step.params.bool_outputs.append(boolean_output(name))
    string_property(step, "ChallengeStepName", "Step06")
    return file.to_binary()


def sample_sword_logic_bytes() -> bytes:
    """Seven nodes. Node 6 is the Master Sword with an unwired Logic_Delete."""
    from ainb import AINB
    from ainb.blackboard import Blackboard
    from ainb.command import Command
    from ainb.node import Node, NodeFlag, NodeType
    from ainb.param import InputParam, ParamSource
    from ainb.param_common import ParamFlag, ParamType
    from ainb.property import Property

    file = AINB()
    file.version = 0x407
    file.filename = "Market_Child_Prefab_e700.logic.module"
    file.category = "Logic"
    file.blackboard = Blackboard()
    file.exists_section_0x6c = True
    command = Command()
    command.name = "Root"
    command.guid = "11111111-1111-1111-1111-111111111111"
    command.root_node_index = 6
    file.commands.append(command)

    def make(name: str) -> Node:
        node = Node(NodeType.UserDefined)
        node.name = name
        node.flags = NodeFlag(1 if name == SWORD_ACTOR else 0)
        node.guid = "55555555-5555-5555-5555-555555555555"
        return node

    file.nodes = [make(f"Filler{index}") for index in range(6)]
    file.nodes.append(make(SWORD_ACTOR))
    for index, node in enumerate(file.nodes):
        node.index = index
    sword = file.nodes[6]
    create = InputParam(ParamType.Bool)
    create.name = CREATE_PARAM
    create.default_value = True
    create.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
    delete = InputParam(ParamType.Bool)
    delete.name = "Logic_Delete"
    delete.default_value = False
    delete.source = ParamSource(-1, 0, ParamFlag().set_uses_default())
    sword.params.bool_inputs.extend((create, delete))
    prop = Property(ParamType.String)
    prop.name = "InstanceName"
    prop.default_value = "Obj_MasterSword_Pedestal"
    prop.flags = ParamFlag().set_uses_default()
    sword.properties.string_properties.append(prop)
    return file.to_binary()


def sample_temple_logic(logic: str) -> bytes:
    if logic == DOOR_LOGIC:
        return sample_door_logic_bytes()
    if logic == SWORD_LOGIC:
        return sample_sword_logic_bytes()
    raise ModError(f"No temple logic fixture for {logic}")


def _refuse_if_patched(nodes, label: str) -> None:
    titled = [index for index, node in enumerate(nodes) if node.name == GET_BOOL and TITLE_EVENT in _field_values(node, DATA_BOOL)]
    if titled:
        raise ModError(
            f"{label}: already patched. Node {titled[0]} is {GET_BOOL} with {DATA_BOOL} {TITLE_EVENT}. "
            "Build from a vanilla logic module."
        )


def _require_signal(nodes, label: str) -> None:
    if len(nodes) <= SIGNAL_NODE or nodes[SIGNAL_NODE].name != FIX_OR:
        shown = nodes[SIGNAL_NODE].name if len(nodes) > SIGNAL_NODE else "missing"
        raise ModError(f"{label}: node {SIGNAL_NODE} is {shown!r}, expected {FIX_OR}.")
    gate = nodes[SIGNAL_NODE]
    if not gate.flags.is_query():
        raise ModError(f"{label}: node {SIGNAL_NODE} {FIX_OR} is not a query.")
    if len(gate.params.bool_outputs) <= SIGNAL_OUTPUT:
        raise ModError(f"{label}: node {SIGNAL_NODE} has no bool output {SIGNAL_OUTPUT}.")
    if len(nodes) <= DOOR_INDEX or nodes[DOOR_INDEX].name != DOOR_ACTOR:
        raise ModError(f"{label}: node {DOOR_INDEX} is not {DOOR_ACTOR}.")


def _require_flag_template(node, label: str) -> None:
    if node.name != GET_BOOL or _field_values(node, DATA_BOOL) != [OPENED_FLAG]:
        raise ModError(
            f"{label}: node {FLAG_TEMPLATE} is {node.name!r} {_field_values(node, DATA_BOOL)!r}, "
            f"expected {GET_BOOL} {OPENED_FLAG}."
        )
    if not node.flags.is_query():
        raise ModError(f"{label}: node {FLAG_TEMPLATE} is not a query.")
    names = [param.name for param in node.params.bool_outputs]
    if not names or names[0] != BOOL_OUTPUT:
        raise ModError(f"{label}: node {FLAG_TEMPLATE} bool outputs are {names or ['none']}, expected {BOOL_OUTPUT!r} at 0.")


def _consumed(nodes, index: int) -> bool:
    for node in nodes:
        for param in node.params.bool_inputs:
            sources = param.source if isinstance(param.source, list) else [param.source]
            if any(getattr(source, "src_node_index", -1) == index for source in sources):
                return True
    return False


def _signal_readers(nodes, label: str, allow_multi: tuple[int, ...] = ()) -> list[tuple[int, object, object]]:
    found = []
    for index, node in enumerate(nodes):
        for param in node.params.bool_inputs:
            if isinstance(param.source, list):
                hits = [
                    source
                    for source in param.source
                    if source.src_node_index == SIGNAL_NODE and source.src_output_index == SIGNAL_OUTPUT
                ]
                if hits and index not in allow_multi:
                    raise ModError(
                        f"{label}: node {index} {param.name} reads node {SIGNAL_NODE} output {SIGNAL_OUTPUT} "
                        "as one of several sources."
                    )
                continue
            source = param.source
            if source.src_node_index == SIGNAL_NODE and source.src_output_index == SIGNAL_OUTPUT:
                found.append((index, node, param))
    return found


def _require_readers(readers, label: str) -> None:
    got = {(index, param.name) for index, _node, param in readers}
    expected = {(index, CREATE_PARAM) for index in STONE_INDICES}
    expected.add((DOOR_INDEX, OPEN_PARAM))
    expected.add((DOOR_INDEX, SKIP_PARAM))
    if got != expected:
        raise ModError(
            f"{label}: node {SIGNAL_NODE} output {SIGNAL_OUTPUT} is read by {sorted(got)}, expected {sorted(expected)}."
        )


def _plain_or(nodes, donor_file, donor_label: str) -> tuple[int, object, str]:
    local = [
        (index, node)
        for index, node in enumerate(nodes)
        if node.name == OR_NAME and node.flags.is_query() and _bool_input(node, MULTI_PARAM) is not None
    ]
    if local:
        index, node = local[0]
        return index, node, "this file"
    index, node = _find_or_template(donor_file.nodes, donor_label)
    return index, node, donor_label


def _plain_and(nodes, donor_file, donor_label: str) -> tuple[int, object, str]:
    def usable(pool, where: str):
        found = []
        for index, node in enumerate(pool):
            if node.name != AND_NAME or not node.flags.is_query():
                continue
            if _bool_input(node, MULTI_PARAM) is None:
                continue
            if len(node.params.bool_outputs) <= AND_RESULT:
                continue
            found.append((index, node, where))
        return found

    local = usable(nodes, "this file")
    if local:
        return local[0]
    donor = usable(donor_file.nodes, donor_label)
    if not donor:
        raise ModError(f"{donor_label}: no {AND_NAME} with {MULTI_PARAM} and bool output {AND_RESULT} to clone.")
    return donor[0]


def _clone_gate(template, guid: str, sources: list[tuple[int, int]]):
    node = _copy_template(template, template.name, 0)[0]
    node.guid = guid
    bits = _bool_multi(template, template.name, 0)[0].flags
    _set_bool_multi(node, [_source(index, output, bits) for index, output in sources])
    node.queries = [index for index, _output in sources]
    return node


def _retarget_source(param, node_index: int, output: int) -> None:
    from ainb.param import ParamSource

    current = param.source
    flags = current.flags if hasattr(current, "flags") else 0
    param.source = ParamSource(node_index, output, _flag(flags))


def _retarget_reader(node, old: int, new: int, names: list[str]) -> None:
    for name in names:
        _set_plug(node, name, new)
    still = False
    for param in node.params.bool_inputs:
        source = param.source
        sources = source if isinstance(source, list) else [source]
        if any(getattr(item, "src_node_index", -1) == old for item in sources):
            still = True
    updated: list[int] = []
    for item in node.queries or []:
        if item == old and not still:
            continue
        if item not in updated:
            updated.append(item)
    if new not in updated:
        updated.append(new)
    node.queries = updated


def _copy_new_header(written, file, dest: int, src_index: int, origin: str, donor: bytes, donor_file):
    if origin == "this file":
        return _copy_header_style(
            written,
            version=file.version,
            command_count=len(file.commands),
            src_index=src_index,
            dest_index=dest,
        )
    return _copy_header_from(
        written,
        dest_version=file.version,
        dest_commands=len(file.commands),
        dest_index=dest,
        source=donor,
        source_version=donor_file.version,
        source_commands=len(donor_file.commands),
        source_index=src_index,
    )


def _check_door(data: bytes, label: str, and_index: int, not_index: int, hold_index: int) -> None:
    file = _load(data, label)
    readers = _signal_readers(file.nodes, label, allow_multi=(and_index,))
    if readers:
        raise ModError(f"{label}: node {SIGNAL_NODE} output {SIGNAL_OUTPUT} is still read after the rewrite.")
    close = _only_source(
        _named_input(file.nodes[DOOR_INDEX], CLOSE_PARAM, label, DOOR_INDEX), label, DOOR_INDEX, CLOSE_PARAM
    )
    if close.src_node_index >= 0:
        raise ModError(f"{label}: {CLOSE_PARAM} was rewired.")
    for index, name in [(DOOR_INDEX, OPEN_PARAM), (DOOR_INDEX, SKIP_PARAM), *[(stone, CREATE_PARAM) for stone in STONE_INDICES]]:
        source = _only_source(_named_input(file.nodes[index], name, label, index), label, index, name)
        if (source.src_node_index, source.src_output_index) != (and_index, AND_RESULT):
            raise ModError(f"{label}: node {index} {name} reads {(source.src_node_index, source.src_output_index)}.")
        got = _eval_reader(file.nodes, index, name, vanilla=True, flag=False)
        if got is not True:
            raise ModError(f"{label}: node {index} {name} is {got} when the flag is false and the door signal is true.")
        got = _eval_reader(file.nodes, index, name, vanilla=False, flag=False)
        if got is not False:
            raise ModError(f"{label}: node {index} {name} is {got} when the flag is false and the door signal is false.")
        if _eval_reader(file.nodes, index, name, vanilla=True, flag=True) is not False:
            raise ModError(f"{label}: node {index} {name} stays open when {TITLE_EVENT} is true.")
    step_sources = [
        (item.src_node_index, item.src_output_index) for item in _bool_multi(file.nodes[SIGNAL_NODE], label, SIGNAL_NODE)
    ]
    if (and_index, AND_RESULT) in step_sources or (hold_index, 0) in step_sources:
        raise ModError(f"{label}: node {SIGNAL_NODE} was rewired ({step_sources}).")


def _eval_reader(nodes, index: int, name: str, *, vanilla: bool, flag: bool) -> bool:
    param = _named_input(nodes[index], name, "temple door", index)
    source = _only_source(param, "temple door", index, name)
    return _eval_output(nodes, source.src_node_index, source.src_output_index, vanilla=vanilla, flag=flag, seen=set())


def _eval_output(nodes, index: int, output: int, *, vanilla: bool, flag: bool, seen: set[tuple[int, int]]) -> bool:
    key = (index, output)
    if key in seen:
        raise ModError(f"temple door: node {index} output {output} loops.")
    seen.add(key)
    node = nodes[index]
    if node.name == FIX_OR and index == SIGNAL_NODE:
        if output != SIGNAL_OUTPUT:
            raise ModError(f"temple door: the reduction read {FIX_OR} output {output}, not {SIGNAL_OUTPUT}.")
        return vanilla
    if node.name == GET_BOOL and TITLE_EVENT in _field_values(node, DATA_BOOL):
        if output != 0:
            raise ModError(f"temple door: {TITLE_EVENT} was read on output {output}.")
        return flag
    if node.name == OR_NAME:
        values = [
            _eval_output(nodes, item.src_node_index, item.src_output_index, vanilla=vanilla, flag=flag, seen=seen)
            for item in _bool_multi(node, "temple door", index)
        ]
        combined = any(values)
        if output == 1:
            return combined
        if output == 0:
            return not combined
        raise ModError(f"temple door: {OR_NAME} output {output} is neither 0 nor 1.")
    if node.name == AND_NAME:
        values = [
            _eval_output(nodes, item.src_node_index, item.src_output_index, vanilla=vanilla, flag=flag, seen=seen)
            for item in _bool_multi(node, "temple door", index)
        ]
        combined = all(values)
        if output == 1:
            return combined
        if output == 0:
            return not combined
        raise ModError(f"temple door: {AND_NAME} output {output} is neither 0 nor 1.")
    raise ModError(f"temple door: cannot reduce node {index} {node.name!r}.")

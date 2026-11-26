"""Decoration groups are inferred. The banc has no parent field."""

from ocarina_forge.actors import format_hash, make_actor, read_vec
from ocarina_forge.decoration_groups import measure, resolve_groups, transform_point
from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import U64
from ocarina_forge.project import Project
from ocarina_forge.scene import apply_command, export_document, scene_view

BANC = "Banc/Test/Test_Static.bcett.byml.zs"
HOUSE = 0x10
GROUND = 0x20
IVY = 0x30
ROOF = 0x31
FIELD = 0x32
FERN = 0x33
MUSHROOM = 0x34
CLIFF = 0x40
NPC = 0x50
TOWN = 0x60
FLOWER = 0x61

HOUSE_BOUNDS = {"min": [-4, -1, -4], "max": [4, 5, 4]}
GROUND_BOUNDS = {"min": [-50, -1, -50], "max": [50, 1, 50]}
CLIFF_BOUNDS = {"min": [-4, -1, -4], "max": [4, 5, 4]}


def _document():
    return {
        "Actors": [
            make_actor("Land_House_A_01", HOUSE, [10, 0, 10]),
            make_actor("Land_Field", GROUND, [0, 0, 0]),
            make_actor("Land_IvyClimbable_A_01", IVY, [12, 3, 10], rotate=(1.5708, -1.29154, 0)),
            make_actor("Obj_Grass_A", ROOF, [10, 0.2, 10]),
            make_actor("Obj_Grass_B", FIELD, [40, 0, 40]),
            make_actor("Obj_Fern_Kokiri", FERN, [0, 0, 0], params={"Links": [{"Dst": U64(NPC), "Src": U64(FERN)}]}),
            make_actor("Obj_Mushroom_A", MUSHROOM, [200, 0, 200]),
            make_actor("Land_Kokiri_Shrine", CLIFF, [80, 0, 80]),
            make_actor("Npc_Kokiri_F_01", NPC, [12, 0, 10]),
        ],
        "AiGroups": [{"Members": [U64(FERN), U64(NPC)]}],
    }


def _bounds(actor):
    return {
        format_hash(HOUSE): HOUSE_BOUNDS,
        format_hash(GROUND): GROUND_BOUNDS,
        format_hash(CLIFF): CLIFF_BOUNDS,
    }.get(actor["id"])


def _rows(document):
    from ocarina_forge.actors import view_actor

    return [view_actor(actor, "vanilla") for actor in document["Actors"]]


def test_ivy_tip_hangs_down():
    tip = transform_point((0, 0, 3), (29.5, 109, -21.75), (1.5708, -1.29154, 0), (1, 1, 1))
    assert abs(tip[0] - 29.5) < 0.02
    assert abs(tip[1] - 106) < 0.02
    assert abs(tip[2] - -21.75) < 0.02


def test_smallest_box_groups_ivy_and_grass_and_ignores_the_name():
    document = _document()
    rows = _rows(document)
    resolved = resolve_groups(rows, bounds_for=_bounds)
    child = resolved["childOf"]
    assert child[format_hash(IVY)] == format_hash(HOUSE)
    assert child[format_hash(ROOF)] == format_hash(HOUSE)
    assert child[format_hash(FIELD)] == format_hash(GROUND)
    assert child[format_hash(FERN)] == format_hash(GROUND)
    assert format_hash(MUSHROOM) not in child
    assert resolved["reason"][format_hash(MUSHROOM)] == "standalone"
    report = measure(document, _bounds)
    assert report["decorationsWithLinks"] == 1
    assert report["decorationLinksToParent"] == 0
    assert report["decorationsInGroupLists"] == 1
    assert report["decorationSharesGroupWithParent"] == 0
    assert report["groupedByBounds"] == 4
    assert report["standalone"] == 1
    assert report["nameGroupedWithoutSharedToken"] >= 1
    assert report["namePointsAtADifferentParent"] >= 1
    assert report["nameHeuristicReliable"] is False
    assert report["samePackAsParent"] == 0
    assert report["packHeuristicReliable"] is False
    assert report["usesLinksOrGroups"] is False


def test_override_and_exclude_and_volume_stand_in():
    document = _document()
    rows = _rows(document)
    forced = resolve_groups(
        rows,
        bounds_for=_bounds,
        include={format_hash(IVY): format_hash(CLIFF)},
        exclude=[format_hash(ROOF)],
    )
    assert forced["childOf"][format_hash(IVY)] == format_hash(CLIFF)
    assert forced["reason"][format_hash(IVY)] == "override"
    assert format_hash(ROOF) not in forced["childOf"]
    assert forced["reason"][format_hash(ROOF)] == "excluded"

    town = {
        "Actors": [
            make_actor("Land_Town", TOWN, [0, 0, 0], scale=(20, 1, 20)),
            make_actor("Obj_Flower_A", FLOWER, [0, 0, 0]),
        ],
    }
    bare = resolve_groups(_rows(town))
    assert format_hash(FLOWER) not in bare["childOf"]
    bounded = resolve_groups(
        _rows(town),
        bounds_for=lambda actor: {"min": [-2, -1, -2], "max": [2, 1, 2]} if actor["id"] == format_hash(TOWN) else None,
    )
    assert bounded["childOf"][format_hash(FLOWER)] == format_hash(TOWN)


def test_grouped_move_exports_transforms_and_not_the_group(tmp_path):
    document = _document()
    empty = {"format": 1, "banc": BANC, "actors": {}}
    diff, focus = apply_command(document, empty, {
        "command": "transform_set",
        "id": format_hash(HOUSE),
        "actors": [
            {"id": format_hash(HOUSE), "translate": [11, 0, 10], "rotate": [0, 0.5, 0]},
            {"id": format_hash(IVY), "translate": [13, 3, 10], "rotate": [1.5708, -0.7, 0]},
        ],
    })
    assert focus == format_hash(HOUSE)
    exported, _warnings = export_document(document, diff)
    assert "decorationGroups" not in exported
    ivy = next(actor for actor in exported["Actors"] if int(actor["Hash"]) == IVY)
    assert read_vec(ivy, "Translate", [0, 0, 0]) == [13, 3, 10]
    assert read_vec(ivy, "Rotate", [0, 0, 0]) == [1.5708, -0.7, 0]
    house = next(actor for actor in exported["Actors"] if int(actor["Hash"]) == HOUSE)
    assert read_vec(house, "Translate", [0, 0, 0]) == [11, 0, 10]

    diff, focus = apply_command(document, diff, {
        "command": "decoration_group",
        "id": format_hash(IVY),
        "parent": format_hash(HOUSE),
    })
    assert focus == format_hash(IVY)
    assert diff["decorationGroups"]["include"][format_hash(IVY)] == format_hash(HOUSE)
    project = Project.create(tmp_path / "mod", name="groups", title="Groups", author="Ada", version="0.1.0")
    project.save_scene_diff(diff)
    loaded = project.load_scene_diff(BANC)
    assert loaded["decorationGroups"]["include"][format_hash(IVY)] == format_hash(HOUSE)
    view = scene_view(document, loaded)
    assert view["diff"]["decorationGroups"]["include"][format_hash(IVY)] == format_hash(HOUSE)
    exported, _warnings = export_document(document, loaded)
    assert "decorationGroups" not in exported

    cleared, _focus = apply_command(document, diff, {"command": "decoration_group", "ids": [format_hash(IVY)], "parent": None})
    assert format_hash(IVY) in cleared["decorationGroups"]["exclude"]
    assert format_hash(IVY) not in cleared["decorationGroups"]["include"]
    project.save_scene_diff({"format": 1, "banc": BANC, "actors": {}, "decorationGroups": cleared["decorationGroups"]})
    assert project.scene_path(BANC).is_file()
    project.save_scene_diff({"format": 1, "banc": BANC, "actors": {}})
    assert not project.scene_path(BANC).is_file()


def test_group_command_rejects_a_missing_actor_and_a_self_group():
    document = _document()
    empty = {"format": 1, "banc": BANC, "actors": {}}
    try:
        apply_command(document, empty, {"command": "decoration_group", "id": format_hash(0x99), "parent": format_hash(HOUSE)})
    except BancError as exc:
        assert "No actor" in exc.message
    else:
        raise AssertionError("missing decoration was accepted")
    try:
        apply_command(document, empty, {"command": "decoration_group", "id": format_hash(IVY), "parent": format_hash(IVY)})
    except BancError as exc:
        assert "itself" in exc.message
    else:
        raise AssertionError("self group was accepted")

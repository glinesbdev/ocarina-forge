"""Exercise the Havok shape layout without relying on a retail file."""

import json
import math
import struct

import pytest

from ocarina_forge.actors import format_hash
from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import U64, loads
from ocarina_forge.formats.phive import (
    CompoundLayoutError,
    compound_actor_spans,
    patch_compound_transform,
    read_mesh_shape,
)
from ocarina_forge.formats.zsio import compress, decompress


def _chunk(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _shape(version=b"20250200", primitive=b"\0\1\2\3"):
    data = bytearray(256)
    struct.pack_into("<3f", data, 0x50, 0.5, 0.5, 0.5)
    struct.pack_into("<iI", data, 0x88, 160 - 0x88, 1)
    struct.pack_into("<iI", data, 168, 224 - 168, 1)
    struct.pack_into("<iI", data, 176, 228 - 176, 24)
    struct.pack_into("<3i", data, 192, -2, 0, 4)
    data[224:228] = primitive
    struct.pack_into("<12H", data, 228, 2, 0, 0, 2, 0, 4, 6, 0, 4, 6, 0, 0)
    strings = b"hknpMeshShape\0hknpMeshShape::GeometrySection\0hknpMeshShape::GeometrySection::Primitive\0hkUint8\0"
    types = _chunk(b"TST1", strings) + _chunk(b"TNA1", bytes([5, 0, 0, 1, 0, 2, 0, 3, 0]))
    items = b"".join(struct.pack("<III", kind, offset, count)
                     for kind, offset, count in ((1, 0, 1), (2, 160, 1), (3, 224, 1), (4, 228, 24)))
    tag = _chunk(b"TAG0", _chunk(b"SDKV", version) + _chunk(b"DATA", data)
                 + _chunk(b"TYPE", types) + _chunk(b"INDX", _chunk(b"ITEM", items)))
    wrapper = bytearray(48)
    wrapper[:6] = b"Phive\0"
    struct.pack_into("<I", wrapper, 12, len(wrapper))
    struct.pack_into("<I", wrapper, 28, len(tag) + 8)
    return bytes(wrapper) + tag + bytes(8)


def test_shape_applies_section_origin_and_splits_quads():
    assert read_mesh_shape(_shape()) == [
        [0, 0, 2, 0, 0, 4, 2, 0, 4],
        [0, 0, 2, 2, 0, 4, 2, 0, 2],
    ]
    assert len(read_mesh_shape(_shape(primitive=b"\0\1\2\2"))) == 1


def test_shape_ignores_unsupported_or_invalid_collision():
    assert read_mesh_shape(_shape(version=b"20230100")) == []
    assert read_mesh_shape(_shape()[:-9]) == []
    assert read_mesh_shape(_shape(primitive=b"\0\1\2\xff")) == []


_MARKER = b"SHAPE-BYTES-DO-NOT-TOUCH!!"
_PAD = b"\xAB\xAB\xAB\xAB"
_TAIL = b"\xCD" * 16
_HASH = 0x1000000000000007


def _tag(data: bytes) -> bytes:
    strings = b"hknpCompoundShape\0"
    types = _chunk(b"TST1", strings) + _chunk(b"TNA1", bytes([2, 0, 0]))
    items = struct.pack("<III", 0, 0, 0) + struct.pack("<III", 1, 0, 1)
    inner = (
        _chunk(b"SDKV", b"20250200")
        + _chunk(b"DATA", data)
        + _chunk(b"TYPE", types)
        + _chunk(b"INDX", _chunk(b"ITEM", items))
    )
    return _chunk(b"TAG0", inner)


def _instance_data(position, scale, quat=(0.0, 0.0, 0.0, 1.0), shape=0x10) -> bytearray:
    data = bytearray(0xC0 + len(_MARKER))
    struct.pack_into("<I", data, 0x38, 0x80 - 0x38)
    struct.pack_into("<I", data, 0x40, 1)
    inst = 0x80
    struct.pack_into("<4f", data, inst, *quat)
    struct.pack_into("<3f", data, inst + 0x10, *position)
    data[inst + 0x1C:inst + 0x20] = _PAD
    struct.pack_into("<3f", data, inst + 0x20, *scale)
    data[inst + 0x2C:inst + 0x30] = _PAD
    struct.pack_into("<Q", data, inst + 0x30, shape)
    data[0xC0:0xC0 + len(_MARKER)] = _MARKER
    return data


def _compound(datas, *, actors=None, bodies=None) -> tuple[bytes, list[int]]:
    """One synthetic .bphsc and the file offset of each compound's first instance."""
    if actors is None:
        actors = [(_HASH, 0, 0, _TAIL)]
    if bodies is None:
        bodies = [0]
    actor_at = 0x40
    actor_bytes = bytearray()
    for hash_value, first, last, tail in actors:
        actor_bytes += struct.pack("<IIQ", first, last, hash_value) + tail
    body_at = actor_at + len(actor_bytes)
    body_bytes = bytearray()
    for index in bodies:
        body = bytearray(0x14)
        struct.pack_into("<H", body, 0x0A, index)
        body_bytes += body
    body_end = body_at + len(body_bytes)
    pad = (16 - (body_end % 16)) % 16
    header = bytearray(actor_at)
    header[:6] = b"Phive\0"
    struct.pack_into("<5I", header, 0x10, actor_at, actor_at + len(actor_bytes), 0, body_at, body_end)
    tags = []
    instance_at = []
    cursor = len(header) + len(actor_bytes) + len(body_bytes) + pad
    for data in datas:
        tag = _tag(bytes(data))
        data_at = cursor + 8 + len(_chunk(b"SDKV", b"20250200")) + 8
        if len(data) >= 0xC0:
            instance_at.append(data_at + 0x80)
        tags.append(tag)
        cursor += len(tag)
    raw = bytes(header) + bytes(actor_bytes) + bytes(body_bytes) + bytes(pad) + b"".join(tags)
    return raw, instance_at


def _pose(translate, rotate=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    return {"translate": list(translate), "rotate": list(rotate), "scale": list(scale)}


def _floats(raw, offset, count):
    return struct.unpack_from(f"<{count}f", raw, offset)


def test_compound_move_keeps_the_shape_and_the_actor_record():
    raw, (inst,) = _compound([_instance_data((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))])
    patched = patch_compound_transform(raw, _HASH, _pose((0, 0, 0)), _pose((4, 0, 0)))
    assert patched is not None
    assert len(patched) == len(raw)
    assert patched[0x40:0x60] == raw[0x40:0x60]
    assert _floats(patched, inst + 0x10, 3) == pytest.approx((4.0, 0.0, 0.0))
    assert _floats(patched, inst, 4) == pytest.approx((0.0, 0.0, 0.0, 1.0))
    assert _floats(patched, inst + 0x20, 3) == pytest.approx((1.0, 1.0, 1.0))
    assert patched[inst + 0x1C:inst + 0x20] == _PAD
    assert patched[inst + 0x2C:inst + 0x30] == _PAD
    assert patched[inst + 0x30:inst + 0x38] == raw[inst + 0x30:inst + 0x38]
    assert _MARKER in patched
    assert patched[patched.index(_MARKER):patched.index(_MARKER) + len(_MARKER)] == _MARKER
    changed = {index for index, (before, after) in enumerate(zip(raw, patched)) if before != after}
    allowed = set(range(inst, inst + 16)) | set(range(inst + 0x10, inst + 0x1C)) | set(range(inst + 0x20, inst + 0x2C))
    assert changed <= allowed


def test_compound_offset_instance_follows_the_actor():
    raw, (inst,) = _compound([_instance_data((1.0, 0.0, 0.0), (1.0, 1.0, 1.0))])
    moved = patch_compound_transform(raw, _HASH, _pose((0, 0, 0)), _pose((4, 0, 0)))
    assert _floats(moved, inst + 0x10, 3) == pytest.approx((5.0, 0.0, 0.0))

    yaw = math.pi / 2
    turned = patch_compound_transform(raw, _HASH, _pose((0, 0, 0)), _pose((0, 0, 0), rotate=(0.0, yaw, 0.0)))
    assert _floats(turned, inst + 0x10, 3) == pytest.approx((0.0, 0.0, -1.0))
    quat = _floats(turned, inst, 4)
    expect = (0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2))
    assert abs(sum(a * b for a, b in zip(quat, expect))) == pytest.approx(1.0)


def test_compound_scale_stays_when_only_the_actor_moves():
    raw, (inst,) = _compound([_instance_data((0.0, 0.0, 0.0), (2.0, 2.0, 2.0))])
    patched = patch_compound_transform(raw, _HASH, _pose((0, 0, 0)), _pose((4, 0, 0)))
    assert _floats(patched, inst + 0x10, 3) == pytest.approx((4.0, 0.0, 0.0))
    assert _floats(patched, inst + 0x20, 3) == pytest.approx((2.0, 2.0, 2.0))
    assert patched[inst + 0x30:inst + 0x38] == raw[inst + 0x30:inst + 0x38]


def test_compound_misses_return_none_and_bad_files_fail_closed():
    raw, _inst = _compound([_instance_data((0, 0, 0), (1, 1, 1))])
    assert patch_compound_transform(raw, _HASH + 1, _pose((0, 0, 0)), _pose((4, 0, 0))) is None
    empty, _none = _compound(
        [bytearray(0x48)],
        actors=[(_HASH, 0xFFFFFFFF, 0xFFFFFFFF, _TAIL)],
        bodies=[],
    )
    assert patch_compound_transform(empty, _HASH, _pose((0, 0, 0)), _pose((1, 0, 0))) is None
    assert compound_actor_spans(empty)[_HASH] == (0xFFFFFFFF, 0xFFFFFFFF)
    with pytest.raises(CompoundLayoutError):
        patch_compound_transform(b"Nope\0\0" + bytes(32), _HASH, _pose((0, 0, 0)), _pose((1, 0, 0)))
    broken = bytearray(raw)
    struct.pack_into("<I", broken, 0x14, 0xFFFFFFF0)
    with pytest.raises(CompoundLayoutError):
        patch_compound_transform(bytes(broken), _HASH, _pose((0, 0, 0)), _pose((1, 0, 0)))
    dup, _inst = _compound(
        [_instance_data((0, 0, 0), (1, 1, 1))],
        actors=[(_HASH, 0, 0, _TAIL), (_HASH, 0, 0, _TAIL)],
    )
    with pytest.raises(CompoundLayoutError):
        patch_compound_transform(dup, _HASH, _pose((0, 0, 0)), _pose((1, 0, 0)))
    negative, _inst = _compound([_instance_data((0, 0, 0), (-1.0, 1.0, 1.0))])
    with pytest.raises(CompoundLayoutError):
        patch_compound_transform(negative, _HASH, _pose((0, 0, 0)), _pose((1, 0, 0)))


def test_two_compounds_pick_one_instance_and_leave_the_other():
    near = _instance_data((0.0, 0.0, 0.0), (1, 1, 1))
    far = _instance_data((20.0, 0.0, 0.0), (1, 1, 1))
    raw, (near_at, far_at) = _compound([near, far])
    patched = patch_compound_transform(raw, _HASH, _pose((0, 0, 0)), _pose((4, 0, 0)))
    assert _floats(patched, near_at + 0x10, 3) == pytest.approx((4.0, 0.0, 0.0))
    assert _floats(patched, far_at + 0x10, 3) == pytest.approx((20.0, 0.0, 0.0))

    both_far, _offsets = _compound([
        _instance_data((10.0, 0.0, 0.0), (1, 1, 1)),
        _instance_data((30.0, 0.0, 0.0), (1, 1, 1)),
    ])
    with pytest.raises(CompoundLayoutError):
        patch_compound_transform(both_far, _HASH, _pose((0, 0, 0)), _pose((4, 0, 0)))

    only, offsets = _compound([
        _instance_data((0.0, 0.0, 0.0), (1, 1, 1)),
        bytearray(0x48),
    ])
    patched = patch_compound_transform(only, _HASH, _pose((0, 0, 0)), _pose((4, 0, 0)))
    assert len(offsets) == 1
    assert _floats(patched, offsets[0] + 0x10, 3) == pytest.approx((4.0, 0.0, 0.0))


def test_srt_mint_is_stable_and_avoids_zero_and_taken_values():
    from ocarina_forge.collision_follow import SIGN_HASH, mint_srt_hash

    first = mint_srt_hash(SIGN_HASH, set())
    assert first == mint_srt_hash(SIGN_HASH, set())
    assert first != 0
    assert first != (SIGN_HASH & 0xFFFFFFFF)
    nxt = mint_srt_hash(SIGN_HASH, {first})
    assert nxt == ((first + 1) & 0xFFFFFFFF or 1)
    assert nxt != first


def test_variant_b_skips_bakeable_false_and_keeps_a_requested_actor():
    from ocarina_forge.collision_follow import SIGN_GYAML, SIGN_HASH, choose_variant_b

    def actor(gyaml, hash_value, translate, bakeable=None):
        row = {"Gyaml": gyaml, "Hash": U64(hash_value), "Translate": list(translate)}
        if bakeable is not None:
            row["Bakeable"] = bakeable
        return row

    sign = actor(SIGN_GYAML, SIGN_HASH, (0, 0, 0))
    document = {"Actors": [
        sign,
        actor("Land_Blocked", 0x10, (1, 0, 0), False),
        actor("Land_Far", 0x20, (5, 0, 0), True),
        actor("Land_Near", 0x30, (2, 0, 0), True),
    ]}
    chosen = choose_variant_b(document)
    assert [item["Gyaml"] for item in chosen] == [SIGN_GYAML, "Land_Near"]
    missing = {"Actors": [
        sign,
        actor("Land_Open", 0x40, (0.5, 0, 0)),
        actor("Land_Near", 0x30, (2, 0, 0), True),
    ]}
    assert choose_variant_b(missing)[1]["Gyaml"] == "Land_Open"
    assert choose_variant_b(document, only_hash="0x30")[0]["Gyaml"] == "Land_Near"
    with pytest.raises(BancError):
        choose_variant_b({"Actors": [actor("FldObj_Sign", 0x99, (0, 0, 0))]})


def test_collision_follow_export_changes_the_record_or_the_transform(tmp_path):
    from ocarina_forge.collision_follow import prepare_variant_b, scene_compound_names
    from ocarina_forge.dump import Dump
    from ocarina_forge.export import build
    from ocarina_forge.fixtures import GROUND, KOKIRI_STATIC, build_fixture
    from ocarina_forge.formats.restbl import load_table, save_table
    from ocarina_forge.modexport import export_mod
    from ocarina_forge.project import Project

    assert scene_compound_names(KOKIRI_STATIC) == [
        "KokiriForest_Child_Static",
        "KokiriForest_Child",
        "KokiriForest",
    ]
    dump_root = build_fixture(tmp_path / "dump")
    raw, (inst,) = _compound([_instance_data((0.0, -0.2, 0.0), (20.0, 1.0, 20.0))])
    relative = "Phive/StaticCompoundBody/KokiriForest_Child/X0_Z0.bphsc.zs"
    romfs = dump_root / "romfs"
    destination = romfs / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(compress(raw, 1, Dump(dump_root).dicts(), level=1))
    (destination.parent / "skip.bphsc").symlink_to(destination.name)
    table_rel = "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"
    table = load_table((romfs / table_rel).read_bytes())
    table.raise_to(relative, 8192)
    (romfs / table_rel).write_bytes(save_table(table, level=1))

    dump = Dump(dump_root)
    project = Project.create(tmp_path / "mod", name="collision-follow-test", title="Collision follow", author="Ada", version="0.1.0")
    with pytest.raises(BancError) as missing:
        prepare_variant_b(dump, project)
    assert "Obj_Kokiri_Signboard_A_01" in missing.value.message
    lines = prepare_variant_b(dump, project, only_hash=format_hash(GROUND))
    assert any("Land_Kokiri_Ground" in line for line in lines)

    off = export_mod(project, dump, collision_follow="off", level=1)
    assert not any(name.endswith((".bphsc", ".bphsc.zs", ".bphsh", ".bphsh.zs")) for name in off["files"])
    folder = project.root / "build" / "mod"
    patch = json.loads((folder / "patches" / "KokiriForest_Child_Static.json").read_text(encoding="utf-8"))
    assert [op["path"] for op in patch["placements"]] == ["Translate"]
    assert patch["placements"][0]["hash"] == format_hash(GROUND)

    srt = export_mod(project, dump, collision_follow="srt", level=1)
    assert not any("bphsc" in name or "bphsh" in name for name in srt["files"])
    patch = json.loads((folder / "patches" / "KokiriForest_Child_Static.json").read_text(encoding="utf-8"))
    by_path = {op["path"]: op for op in patch["placements"]}
    assert set(by_path) == {"Translate", "SRTHash"}
    assert by_path["SRTHash"]["op"] == "set"
    assert by_path["SRTHash"]["hash"] == format_hash(GROUND)
    assert isinstance(by_path["SRTHash"]["value"], int)
    assert by_path["SRTHash"]["value"] not in (0, GROUND & 0xFFFFFFFF)
    assert any("does not move the baked row" in line["message"] for line in srt["log"])

    built = build(project, dump, collision_follow="srt", level=1)
    payload, _dict = decompress((project.root / "build" / "romfs" / KOKIRI_STATIC).read_bytes(), dump.dicts())
    actor = next(row for row in loads(payload)["Actors"] if int(row["Hash"]) == GROUND)
    assert int(actor["SRTHash"]) == by_path["SRTHash"]["value"]
    assert int(actor["Hash"]) == GROUND
    assert int(actor["Phive"]["Placement"]["ID"]) == GROUND
    assert not any("bphsc" in name for name in built["files"])

    baked = export_mod(project, dump, collision_follow="bake", level=1)
    assert f"romfs/{relative}" in baked["files"]
    assert any(name.endswith(".rsizetable.zs") for name in baked["files"])
    assert not any(name.endswith(".bphsh") or name.endswith(".bphsh.zs") for name in baked["files"])
    patch = json.loads((folder / "patches" / "KokiriForest_Child_Static.json").read_text(encoding="utf-8"))
    assert [op["path"] for op in patch["placements"]] == ["Translate"]
    patched, dict_id = decompress((folder / "romfs" / relative).read_bytes(), dump.dicts())
    assert dict_id == 1
    assert len(patched) == len(raw)
    assert _floats(patched, inst + 0x10, 3) == pytest.approx((4.0, -0.2, 0.0))
    assert _floats(patched, inst + 0x20, 3) == pytest.approx((20.0, 1.0, 20.0))
    assert patched[inst + 0x30:inst + 0x38] == raw[inst + 0x30:inst + 0x38]
    assert patched[0x40:0x60] == raw[0x40:0x60]
    marker_at = patched.index(_MARKER)
    assert patched[marker_at:marker_at + len(_MARKER)] == raw[raw.index(_MARKER):raw.index(_MARKER) + len(_MARKER)]
    updated = load_table((folder / "romfs" / table_rel).read_bytes())
    assert updated.get(relative) == 9216
    assert "skip.bphsc" not in " ".join(baked["files"])


def test_a_compound_body_outranks_a_nearer_land_with_no_body():
    from ocarina_forge.collision_follow import SIGN_GYAML, SIGN_HASH, _best_land, choose_variant_b

    sign = {"Gyaml": SIGN_GYAML, "Hash": U64(SIGN_HASH), "Translate": [0.0, 0.0, 0.0]}
    near = {"Gyaml": "Land_Kokiri_Zenmai_A_02", "Hash": U64(0x30), "Translate": [1.0, 0.0, 0.0], "Bakeable": True}
    far = {"Gyaml": "Land_Far", "Hash": U64(0x20), "Translate": [8.0, 0.0, 0.0], "Bakeable": True}
    document = {"Actors": [sign, near, far]}
    assert choose_variant_b(document, has_body={0x20})[1]["Gyaml"] == "Land_Far"
    banc, actor = _best_land(
        [0.0, 0.0, 0.0],
        [("Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs", near),
         ("Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs", far)],
        {0x20},
    )
    assert banc.endswith("_Dynamic.bcett.byml.zs")
    assert actor["Gyaml"] == "Land_Far"


def test_prepare_replaces_scenes_and_finds_an_id_on_the_paired_banc(tmp_path):
    from ocarina_forge.collision_follow import prepare_variant_b, requested_banc
    from ocarina_forge.dump import Dump
    from ocarina_forge.fixtures import HOUSE, KOKIRI_STATIC, build_fixture
    from ocarina_forge.project import Project

    assert requested_banc("Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs", KOKIRI_STATIC).endswith("_Dynamic.bcett.byml.zs")
    assert requested_banc(None, KOKIRI_STATIC) == KOKIRI_STATIC
    dump = Dump(build_fixture(tmp_path / "dump"))
    project = Project.create(tmp_path / "mod", name="collision-follow-test", title="Collision follow", author="Ada", version="0.1.0")
    stale = project.root / "scenes" / "HyruleField" / "Old.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("format: 1\nbanc: Banc/HyruleField/Old.bcett.byml.zs\nactors: {}\n", encoding="utf-8")
    lines = prepare_variant_b(dump, project, only_hash=format_hash(HOUSE), banc=KOKIRI_STATIC)
    yamls = list((project.root / "scenes").rglob("*.yaml"))
    assert [path.name for path in yamls] == ["KokiriForest_Child_Dynamic.yaml"]
    assert any("Land_Kokiri_House_01" in line for line in lines)
    assert not stale.exists()


def test_static_and_dynamic_edits_merge_into_one_tile(tmp_path):
    from ocarina_forge.collision_follow import PoseMove, bake_tiles
    from ocarina_forge.fixtures import KOKIRI_DYNAMIC, KOKIRI_STATIC

    hash_a = 0x111
    hash_b = 0x222
    data = bytearray(0x100)
    struct.pack_into("<I", data, 0x38, 0x80 - 0x38)
    struct.pack_into("<I", data, 0x40, 2)
    struct.pack_into("<4f", data, 0x80, 0, 0, 0, 1)
    struct.pack_into("<3f", data, 0x90, 0, 0, 0)
    struct.pack_into("<3f", data, 0xA0, 1, 1, 1)
    struct.pack_into("<Q", data, 0xB0, 1)
    struct.pack_into("<4f", data, 0xC0, 0, 0, 0, 1)
    struct.pack_into("<3f", data, 0xD0, 8, 0, 0)
    struct.pack_into("<3f", data, 0xE0, 1, 1, 1)
    struct.pack_into("<Q", data, 0xF0, 1)
    raw, (inst_a,) = _compound(
        [data],
        actors=[(hash_a, 0, 0, _TAIL), (hash_b, 1, 1, _TAIL)],
        bodies=[0, 1],
    )
    inst_b = inst_a + 0x40
    relative = "Phive/StaticCompoundBody/KokiriForest_Child/X1_Z2.bphsc"
    romfs = tmp_path / "romfs"
    destination = romfs / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(raw)

    class Dump:
        def __init__(self, root):
            self.romfs = root

        def read_file(self, path):
            return (self.romfs / path).read_bytes(), None

    def move(hash_value, old, new):
        return PoseMove(
            hash=format_hash(hash_value),
            hash_int=hash_value,
            gyaml="Land",
            old=_pose(old),
            new=_pose(new),
            srt_old=1,
        )

    baked = bake_tiles(Dump(romfs), [
        (KOKIRI_STATIC, [move(hash_a, (0, 0, 0), (4, 0, 0))]),
        (KOKIRI_DYNAMIC, [move(hash_b, (8, 0, 0), (12, 0, 0))]),
    ])
    assert [item[0] for item in baked] == [relative]
    patched = baked[0][1]
    assert len(patched) == len(raw)
    assert _floats(patched, inst_a + 0x10, 3) == pytest.approx((4.0, 0.0, 0.0))
    assert _floats(patched, inst_b + 0x10, 3) == pytest.approx((12.0, 0.0, 0.0))
    assert patched[inst_a + 0x30:inst_a + 0x38] == raw[inst_a + 0x30:inst_a + 0x38]
    assert patched[inst_b + 0x30:inst_b + 0x38] == raw[inst_b + 0x30:inst_b + 0x38]
    assert patched[0x40:0x80] == raw[0x40:0x80]

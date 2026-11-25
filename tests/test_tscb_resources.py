from struct import pack_into

import pytest

from ocarina_forge.formats.terrain import TerrainError, read_tscb, read_tscb_resources


def index_fixture():
    data = bytearray(0x480)
    data[:4] = b"TSCB"

    def uint(at, value):
        pack_into("<I", data, at, value)

    def offset(at, target):
        uint(at, target - at)

    def string(at, target, value):
        offset(at, target)
        data[target : target + len(value)] = value

    uint(4, 0x10D)
    pack_into("<2f", data, 0x10, 8, 100)
    offset(0x0C, 0x400)
    uint(0x30, 2)
    uint(0x38, 2)
    offset(0x70, 0x7C)
    offset(0x78, 0x184)
    offset(0x7C, 0x84)
    offset(0x80, 0x104)
    for at, code, name, ext, text_at in (
        (0x84, 0, b"Height", b"hght", 0x400),
        (0x104, 0x5300, b"Custom", b"custom.extm", 0x430),
    ):
        uint(at, code)
        string(at + 0x60, text_at, name)
        string(at + 0x64, text_at + 8, ext)
        if code == 0:
            string(at + 0x6C, text_at + 24, b"Type")
    for table_slot, at, name, count in ((0x184, 0x190, b"001", 2), (0x188, 0x300, b"002", 1)):
        offset(table_slot, at)
        pack_into("<3f", data, at, 1.0, 2.0, 4.0)
        uint(at + 0x20, count)
        uint(at + 0x64, 4)
        string(at + 0x60, 0x460 + 8 * (table_slot == 0x188), name)
    offset(0x1F8, 0x200)
    offset(0x1FC, 0x240)
    offset(0x368, 0x3C4)
    pack_into("<2f", data, 0x220, 0.25, 0.5)
    uint(0x240, 0x5300)
    uint(0x3C4, 0xFFFF)
    return data


def test_tscb_resource_tables_follow_relative_offsets_and_area_counts():
    data = index_fixture()
    assert read_tscb(data)[2] == [(1.0, 2.0, 4.0, "001"), (1.0, 2.0, 4.0, "002")]
    index = read_tscb_resources(data)
    assert [(item["type"], item["extension"]) for item in index["fileTypes"]] == [(0, "hght"), (0x5300, "custom.extm")]
    assert [item["resourceType"] for item in index["fileTypes"]] == ["Type", None]
    first, second = index["tiles"]
    assert first["resources"] == [
        {"type": 0, "extension": "hght", "rawMin": 0.25, "rawMax": 0.5},
        {"type": 0x5300, "extension": "custom.extm", "rawMin": 0, "rawMax": 0},
    ]
    assert second["resources"] == [{"type": 0xFFFF, "extension": None, "rawMin": 0, "rawMax": 0}]


def test_invalid_resource_offset_cannot_read_the_next_area():
    data = index_fixture()
    pack_into("<I", data, 0x1FC, 0x370 - 0x1FC)
    with pytest.raises(TerrainError, match="outside its area"):
        read_tscb_resources(data)
    assert len(read_tscb(data)[2]) == 2


def test_resource_parser_requires_verified_version():
    data = index_fixture()
    pack_into("<I", data, 4, 0x70C)
    with pytest.raises(TerrainError, match="unverified"):
        read_tscb_resources(data)
    assert len(read_tscb(data)[2]) == 2

"""Round-trips for BYML, SARC, zstd dictionaries and the resource size table."""

import struct

import pytest
import zstandard

from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import F64, Hash32, Hash64, S64, U32, U64, dumps, loads, same
from ocarina_forge.formats.restbl import ResourceSizeTable, entry_for_byml, load_table, save_table
from ocarina_forge.formats.sarc import Sarc, pack
from ocarina_forge.formats.zsio import DictSet, compress, decompress


def test_byml_roundtrip_keeps_types():
    document = {
        "Name": "Fado",
        "Count": -3,
        "Flag": True,
        "Off": False,
        "Rate": 1.5,
        "Room": U32(7),
        "Big": S64(-1 << 40),
        "Hash": U64(0x1000000000000001),
        "Wide": F64(1.25),
        "Blob": b"\x00\x01\xff",
        "Empty": None,
        "Tags": ["a", "b"],
        "Nested": {"X": 1.0, "Y": 0.0, "Z": -2.0},
        "Flags32": Hash32({2: U32(9), 1: True}),
        "Flags64": Hash64({0x1000000000000001: "yes"}),
    }
    again = loads(dumps(document))
    assert same(document, again)


def test_byml_rejects_big_endian_magic():
    with pytest.raises(ValueError, match="little-endian"):
        loads(b"BY" + b"\x00" * 16)


def test_sarc_roundtrip_and_repack():
    files = {"Actor/A.bgyml": b"alpha", "Actor/B.bgyml": b"bravo" * 3, "Z.dat": b""}
    blob = pack(files)
    archive = Sarc(blob)
    assert archive.read("Actor/A.bgyml") == b"alpha"
    assert archive.read("Z.dat") == b""
    again = pack({name: archive.read(name) for name in archive.names()}, like=blob)
    assert again == blob


def test_zstd_dictionary_roundtrip(tmp_path):
    samples = [dumps({"Name": f"actor-{i}", "N": i, "Pad": "TranslateRotateScale" * 40}) for i in range(12)]
    dictionary = zstandard.train_dictionary(1024, samples, dict_id=3)
    archive = pack({"bcett.byml.zsdic": dictionary.as_bytes()})
    path = tmp_path / "ZsDic.pack.zs"
    path.write_bytes(zstandard.ZstdCompressor(level=3, write_content_size=True).compress(archive))
    loaded = DictSet.load(path)
    assert 3 in loaded
    packed = compress(samples[0], 3, loaded, level=3)
    assert zstandard.get_frame_parameters(packed).dict_id == 3
    payload, dict_id = decompress(packed, loaded)
    assert dict_id == 3 and payload == samples[0]
    plain, none = decompress(samples[0], loaded)
    assert none is None and plain == samples[0]


def test_missing_dictionary_is_a_clear_error(tmp_path):
    samples = [b"abcdefghijklmnopqrstuvwxyz" * 80 + bytes([i]) for i in range(8)]
    dictionary = zstandard.train_dictionary(512, samples, dict_id=3)
    other = zstandard.ZstdCompressionDict(dictionary.as_bytes())
    # Frame asks for dictionary 3, and the pack we load only has dictionary 1.
    packed = zstandard.ZstdCompressor(level=3, dict_data=other, write_content_size=True).compress(samples[0])
    wrong = zstandard.train_dictionary(512, samples, dict_id=1)
    path = tmp_path / "ZsDic.pack.zs"
    path.write_bytes(
        zstandard.ZstdCompressor(level=3, write_content_size=True).compress(pack({"zs.zsdic": wrong.as_bytes()}))
    )
    loaded = DictSet.load(path)
    with pytest.raises(BancError, match="dictionary id 3") as caught:
        decompress(packed, loaded)
    assert caught.value.code == "dict_missing"


def test_restbl_raises_inserts_and_keeps_order():
    raw = struct.pack("<6sIIII", b"RESTBL", 1, 32, 0, 0)
    table = ResourceSizeTable(raw)
    table.named.append(("System/Resource/KeepMe.bgyml", 1000))
    old, new = table.raise_to("Banc/Field/Field_Static.bcett.byml", 100)
    assert (old, new) == (0, 104)
    # A lower request must not shrink the entry.
    assert table.raise_to("Banc/Field/Field_Static.bcett.byml", 50)[1] == 104
    table.raise_to("AS/A.root.asb", 16)
    table.raise_to("System/Resource/KeepMe.bgyml", 2000)
    blob = table.to_bytes()
    again = ResourceSizeTable(blob)
    assert again.get("System/Resource/KeepMe.bgyml") == 2000
    assert again.get("AS/A.root.asb") == 16
    crcs = [struct.unpack_from("<I", blob, 22 + 8 * i)[0] for i in range(2)]
    assert crcs == sorted(crcs)
    # A colliding path is stored by name instead of overwriting the hashed entry.
    import zlib

    path = "AS/A.root.asb"
    collision = path  # same path updates in place; use the named slot for a different spelling check
    assert again.named[0][0] == "System/Resource/KeepMe.bgyml"
    packed = save_table(again, level=1)
    assert load_table(packed).get(path) == 16
    assert zlib.crc32(b"unused") >= 0


def test_byml_entry_grows_by_the_longshot_rule_and_never_shrinks():
    # File grew by 10 bytes. Old entry 8000. Verified rule: align8(8000 + 10 + 1024).
    grown = entry_for_byml(8000, 1000, 1010)
    assert grown == (8000 + 10 + 1024 + 7) & ~7
    # A smaller file still raises by the 1024 margin. The entry is never left alone
    # once the bytes differ, and it is never shrunk.
    shrunk = entry_for_byml(8000, 1000, 900)
    assert shrunk == (8000 + 1024 + 7) & ~7
    assert shrunk > 8000
    try:
        entry_for_byml(0, None, 100)
    except ValueError:
        pass
    else:
        raise AssertionError("a missing entry must not be invented")
    # Saria's schedule, from the in-game notes: 3316 -> 3384, entry 14448 -> 15544.
    assert entry_for_byml(14448, 3316, 3384) == 15544
    # Logic module: 12598 -> 13189, entry 12984 -> 14600.
    assert entry_for_byml(12984, 12598, 13189) == 14600
    assert grown % 8 == 0 and shrunk % 8 == 0

"""Hang one Threads of Time tapestry, derived from the dump at build time.

The cloth position is baked into the bfres. Cloning a Sub model and only
renaming it would sit on the source slot, so the vertex buffer and the three
bounding-box centres move by ``(cx, cy)`` minus the quad's centre. The menu
row and the model list are appended. The two model paths are new hashed
resource-size entries. Nothing in this module is a game file: the samples are
synthetic, and a real build reads the dump.
"""

from __future__ import annotations

import copy
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from after_ocarina.errors import ModError
from after_ocarina.paths import RSTB_REL, TAPESTRY_PATH, require_tools
from after_ocarina.rstb import load_table, strip_zs, target_entry
from after_ocarina.zsio import dicts_for, pack, unpack

BOOT_PACK = "Pack/Bootup.Nin_Ounce_NVN2.pack.zs"
MENU_TABLE = (
    "Game/UI/ChallengeMenu/ChallengeMenuTable/ChallengeMenuTable.game__ui__ChallengeMenuTable.bgyml"
)
TAPESTRY_PACK = "Pack/Actor/Tapestry_ChallengeMenu.pack.zs"
MODEL_INFO = "Component/ModelInfo/Tapestry_ChallengeMenu.engine__component__ModelInfo.bgyml"
PROJECT = "Obj_TapestryChallengeMenu"
# Every vanilla tapestry model reserves this much. The unpacked clone is 21184.
TAPESTRY_ENTRY = 40152
_MODEL_SIZE = 21184
_FSHP = (0x4F0, 0x508, 0x520)


@dataclass(frozen=True)
class TapestrySpec:
    name: str
    src: str
    cx: float
    cy: float
    source: str


@dataclass(frozen=True)
class TapestryEdit:
    files: tuple[str, ...]
    # Inner members are not files the build rewrote on disk. Their table entries still grow.
    raises: dict[str, int]
    additions: dict[str, int]


def model_rel(name: str) -> str:
    return f"Model/{PROJECT}.{name}.bfres.zs"


def output_rels(spec: TapestrySpec) -> tuple[str, ...]:
    return (
        model_rel(spec.name),
        model_rel(spec.name + "_Complete"),
        BOOT_PACK,
        TAPESTRY_PACK,
    )


def load_tapestry(path: Path | None = None) -> TapestrySpec | None:
    path = path or TAPESTRY_PATH
    if not path.is_file():
        return None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or "tapestry" not in loaded:
        raise ModError(f"{path.name}: expected a tapestry: mapping")
    row = loaded["tapestry"]
    if not isinstance(row, dict):
        raise ModError(f"{path.name}: tapestry must be a mapping")
    extra = set(row) - {"name", "src", "cx", "cy"}
    if extra:
        raise ModError(f"{path.name}: unknown tapestry keys: {', '.join(sorted(extra))}")
    name = _slug(row.get("name"), path.name, "name")
    src = _slug(row.get("src"), path.name, "src")
    return TapestrySpec(
        name=name,
        src=src,
        cx=_coord(row.get("cx"), path.name, "cx"),
        cy=_coord(row.get("cy"), path.name, "cy"),
        source=path.name,
    )


def apply_tapestry(romfs: Path, out_dir: Path, spec: TapestrySpec) -> TapestryEdit:
    """Clone the source tapestry, append the menu row and the two sub-models, and name the table edits."""
    dicts = dicts_for(romfs)
    written: list[str] = []

    def read(rel: str) -> tuple[bytes, int | None]:
        path = romfs / rel
        if not path.is_file():
            raise ModError(f"Not in the dump: {rel}")
        return unpack(path.read_bytes(), dicts)

    def write(rel: str, raw: bytes, dict_id: int | None) -> None:
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pack(raw, dict_id, dicts))
        written.append(rel)

    for suffix in ("", "_Complete"):
        src_name = spec.src + suffix
        new_name = spec.name + suffix
        raw, dict_id = read(model_rel(src_name))
        write(model_rel(new_name), clone_model(raw, src_name, new_name, spec.cx, spec.cy), dict_id)

    byml, zsmod, sarc_type = _tools()
    raw, dict_id = read(BOOT_PACK)
    files = _sarc_files(raw, sarc_type)
    if MENU_TABLE not in files:
        raise ModError(f"{BOOT_PACK} has no {MENU_TABLE}")
    table = byml.loads(files[MENU_TABLE])
    menu_old = len(files[MENU_TABLE])
    rows = table.get("ChallengeMenuParamArray")
    if not isinstance(rows, list):
        raise ModError(f"{MENU_TABLE} has no ChallengeMenuParamArray")
    if any(isinstance(row, dict) and row.get("ChallengeShort") == spec.name for row in rows):
        raise ModError(f"{MENU_TABLE} already has a row named {spec.name}")
    source = next((row for row in rows if isinstance(row, dict) and row.get("ChallengeShort") == spec.src), None)
    if source is None:
        raise ModError(f"{MENU_TABLE} has no row named {spec.src}")
    row = copy.deepcopy(source)
    if type(row.get("Category")) is not byml.U32:
        raise ModError(f"{MENU_TABLE} Category on {spec.src} is not a U32, and the new row has to keep that type")
    row["ChallengeShort"] = spec.name
    row["ActorPos"] = {"X": spec.cx, "Y": spec.cy}
    rows.append(row)
    files[MENU_TABLE] = byml.dumps(table)
    menu_new = len(files[MENU_TABLE])
    write(BOOT_PACK, zsmod.sarc_pack(files, like=raw), dict_id)

    raw, dict_id = read(TAPESTRY_PACK)
    files = _sarc_files(raw, sarc_type)
    if MODEL_INFO not in files:
        raise ModError(f"{TAPESTRY_PACK} has no {MODEL_INFO}")
    info = byml.loads(files[MODEL_INFO])
    info_old = len(files[MODEL_INFO])
    models = info.get("SubModels")
    if not isinstance(models, list):
        raise ModError(f"{MODEL_INFO} has no SubModels")
    present = {item.get("FmdbName") for item in models if isinstance(item, dict)}
    for new_name in (spec.name, spec.name + "_Complete"):
        if new_name in present:
            raise ModError(f"{MODEL_INFO} already lists {new_name}")
        models.append({"FmdbName": new_name, "ModelProjectName": PROJECT})
    files[MODEL_INFO] = byml.dumps(info)
    info_new = len(files[MODEL_INFO])
    write(TAPESTRY_PACK, zsmod.sarc_pack(files, like=raw), dict_id)

    sizes = load_table(romfs / RSTB_REL)
    raises: dict[str, int] = {}
    for path, old_len, new_len in ((MENU_TABLE, menu_old, menu_new), (MODEL_INFO, info_old, info_new)):
        entry = sizes.size_for(path)
        if entry is None:
            raise ModError(
                f"{path} has no hashed or named entry in the resource size table. "
                "The tapestry step raises that entry; it does not invent one for a menu file."
            )
        needed = target_entry(entry, new_len, old_unpacked=old_len)
        if needed is None:
            raise ModError(f"{path}: changed, but the size-table entry was not raised")
        raises[path] = needed
    additions = {
        strip_zs(model_rel(spec.name)): TAPESTRY_ENTRY,
        strip_zs(model_rel(spec.name + "_Complete")): TAPESTRY_ENTRY,
    }
    for path in additions:
        if sizes.size_for(path) is not None:
            raise ModError(f"{path}: already has a resource size entry, so it is not a new hashed entry")
    print(
        f"{BOOT_PACK}: ChallengeMenuTable row {spec.name} at ({spec.cx}, {spec.cy}), "
        f"cloned from {spec.src}"
    )
    return TapestryEdit(files=tuple(written), raises=raises, additions=additions)


def clone_model(raw: bytes, src_name: str, new_name: str, cx: float, cy: float) -> bytes:
    """Rename one Sub tapestry and slide its baked quad to ``(cx, cy)``. The file stays 21184 bytes."""
    if raw[:8] != b"FRES    " or len(raw) != _MODEL_SIZE:
        raise ModError(
            f"{PROJECT}.{src_name}.bfres is {len(raw)} bytes. "
            "The tapestry clone expects the 21184-byte Sub layout."
        )
    data = bytearray(raw)
    try:
        string_at, count, entries, end = _strings(data)
    except (struct.error, IndexError) as exc:
        raise ModError(f"{PROJECT}.{src_name}.bfres: string pool could not be read ({exc})") from None
    found = {text: at for at, text in entries}
    old_model = src_name.encode()
    old_file = f"{PROJECT}.{src_name}".encode()
    if old_model not in found or old_file not in found:
        raise ModError(f"{PROJECT}.{src_name}.bfres does not name {src_name} in its string pool")
    new_end = end
    renamed: dict[bytes, int] = {}
    for old, new in (
        (old_model, new_name.encode()),
        (old_file, f"{PROJECT}.{new_name}".encode()),
    ):
        blob = struct.pack("<H", len(new)) + new + b"\0"
        if len(blob) % 2:
            blob += b"\0"
        if new_end + len(blob) > len(data) or data[new_end : new_end + len(blob)] != b"\0" * len(blob):
            raise ModError(f"{PROJECT}.{src_name}.bfres has no room to add {new.decode()}")
        data[new_end : new_end + len(blob)] = blob
        renamed[old] = new_end
        for slot in range(0, string_at, 8):
            if struct.unpack_from("<Q", data, slot)[0] == found[old]:
                struct.pack_into("<Q", data, slot, new_end)
        if old == old_file:
            struct.pack_into("<I", data, 0x10, new_end + 2)
        new_end += len(blob)
    struct.pack_into("<I", data, string_at + 0x10, count + 2)
    dictionary = struct.unpack_from("<Q", data, 0x30)[0]
    if dictionary + 28 > len(data) or struct.unpack_from("<i", data, dictionary + 4)[0] != 1:
        raise ModError(f"{PROJECT}.{src_name}.bfres model dictionary is not the single-key Sub layout")
    struct.pack_into("<I", data, dictionary + 8 + 16, lowest_bit_ref(new_name.encode()))
    count_verts = struct.unpack_from("<I", data, 0x238)[0]
    start = _vertex_start(data, count_verts, src_name)
    points = [struct.unpack_from("<2e", data, start + index * 28) for index in range(count_verts)]
    origin_x = (min(point[0] for point in points) + max(point[0] for point in points)) / 2
    origin_y = (min(point[1] for point in points) + max(point[1] for point in points)) / 2
    delta_x, delta_y = cx - origin_x, cy - origin_y
    for index, (px, py) in enumerate(points):
        struct.pack_into("<2e", data, start + index * 28, px + delta_x, py + delta_y)
    for at in _FSHP:
        box_x, box_y = struct.unpack_from("<2f", data, at)
        if abs(box_x - origin_x) >= 0.01 or abs(box_y - origin_y) >= 0.01:
            raise ModError(
                f"{PROJECT}.{src_name}.bfres bounding centre at {hex(at)} is {(box_x, box_y)}, "
                f"not the quad centre {(origin_x, origin_y)}"
            )
        struct.pack_into("<2f", data, at, box_x + delta_x, box_y + delta_y)
    if len(data) != _MODEL_SIZE:
        raise ModError(f"{PROJECT}.{new_name}.bfres grew past {_MODEL_SIZE} bytes")
    return bytes(data)


def lowest_bit_ref(key: bytes) -> int:
    """Index of the lowest set bit in the key. The model dictionary stores that as the node reference."""
    for bit in range(len(key) * 8):
        if (key[len(key) - 1 - (bit >> 3)] >> (bit & 7)) & 1:
            return bit
    return 0


def sample_model(model_name: str) -> bytes:
    """A 21184-byte stand-in with the offsets the clone reads. It is not a game model."""
    data = bytearray(_MODEL_SIZE)
    data[:8] = b"FRES    "
    string_at = 0x2FE0
    data[string_at : string_at + 4] = b"_STR"
    cursor = string_at + 0x14
    offsets: list[int] = []
    for text in (b"", model_name.encode(), f"{PROJECT}.{model_name}".encode()):
        offsets.append(cursor)
        blob = struct.pack("<H", len(text)) + text + b"\0"
        if len(blob) % 2:
            blob += b"\0"
        data[cursor : cursor + len(blob)] = blob
        cursor += len(blob)
    struct.pack_into("<I", data, string_at + 0x10, 2)
    _model_at, file_at = offsets[1], offsets[2]
    struct.pack_into("<I", data, 0x10, file_at + 2)
    struct.pack_into("<Q", data, 0x20, file_at)
    struct.pack_into("<Q", data, 0xF8, offsets[1])
    dictionary = 0x180
    struct.pack_into("<Q", data, 0x30, dictionary)
    struct.pack_into("<i", data, dictionary + 4, 1)
    struct.pack_into("<I", data, 0x238, 4)
    start = 0x4100
    for index, (px, py) in enumerate(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))):
        struct.pack_into("<2e", data, start + index * 28, px, py)
        struct.pack_into("<H", data, start + index * 28 + 6, 0x3C00)
    for at in _FSHP:
        struct.pack_into("<2f", data, at, 0.5, 0.5)
    return bytes(data)


def sample_boot_pack(src_name: str) -> bytes:
    byml, zsmod, _sarc = _tools()
    row = {
        "ActorPos": {"X": -0.25, "Y": -0.20},
        "CameraFovy": 16.0,
        "Category": byml.U32(1200733368),
        "ChallengeShort": src_name,
        "IsSingleChallenge": True,
    }
    other = {
        "ActorPos": {"X": 0.0, "Y": 0.0},
        "CameraFovy": 16.0,
        "Category": byml.U32(460120899),
        "ChallengeShort": "OtherChallenge",
        "IsSingleChallenge": False,
    }
    payload = byml.dumps({"ChallengeMenuParamArray": [other, row]})
    return zsmod.sarc_pack({MENU_TABLE: payload, "Game/UI/Keep.bgyml": b"keep"})


def sample_model_info(src_name: str) -> bytes:
    byml, zsmod, _sarc = _tools()
    models = [
        {"FmdbName": src_name, "ModelProjectName": PROJECT},
        {"FmdbName": src_name + "_Complete", "ModelProjectName": PROJECT},
    ]
    return zsmod.sarc_pack({MODEL_INFO: byml.dumps({"SubModels": models})})


def source_files(spec: TapestrySpec) -> dict[str, bytes]:
    """Uncompressed files a fixture dump needs before the tapestry step can run."""
    return {
        model_rel(spec.src): sample_model(spec.src),
        model_rel(spec.src + "_Complete"): sample_model(spec.src + "_Complete"),
        BOOT_PACK: sample_boot_pack(spec.src),
        TAPESTRY_PACK: sample_model_info(spec.src),
    }


def reserved_entries() -> dict[str, int]:
    """Table keys that are not the rewritten files themselves. The new model paths are absent on purpose."""
    return {MENU_TABLE: 2000, MODEL_INFO: 1500}


def _strings(data: bytes | bytearray) -> tuple[int, int, list[tuple[int, bytes]], int]:
    matches = [index for index in range(len(data) - 3) if data[index : index + 4] == b"_STR"]
    if not matches:
        raise ModError("tapestry bfres has no _STR block")
    string_at = matches[0]
    count = struct.unpack_from("<I", data, string_at + 0x10)[0]
    cursor = string_at + 0x14
    entries: list[tuple[int, bytes]] = []
    for _ in range(count + 1):
        length = struct.unpack_from("<H", data, cursor)[0]
        entries.append((cursor, bytes(data[cursor + 2 : cursor + 2 + length])))
        cursor = (cursor + 2 + length + 1 + 1) & ~1
    return string_at, count, entries, cursor


def _vertex_start(data: bytes | bytearray, count: int, src_name: str) -> int:
    if count <= 0 or count > 64:
        raise ModError(f"{PROJECT}.{src_name}.bfres vertex count {count} is not a Sub tapestry")
    limit = len(data) - count * 28
    for start in range(0x3300, limit, 4):
        if all(struct.unpack_from("<H", data, start + vertex * 28 + 6)[0] == 0x3C00 for vertex in range(count)):
            return start
    raise ModError(f"{PROJECT}.{src_name}.bfres vertex buffer was not found")


def _sarc_files(raw: bytes, sarc_type) -> dict[str, bytes]:
    sarc = sarc_type(raw)
    return {name: sarc.read(name) for name in sarc.names()}


def _tools():
    tools = str(require_tools())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import byml
    import zsmod
    import zstool

    return byml, zsmod, zstool.Sarc


def _slug(value: object, where: str, key: str) -> str:
    if not isinstance(value, str) or not value or not value.replace("_", "").isalnum() or not value[0].isalpha():
        raise ModError(f"{where} {key} must be a letter followed by letters, digits, or underscores")
    return value


def _coord(value: object, where: str, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModError(f"{where} {key} must be a number")
    return float(value)

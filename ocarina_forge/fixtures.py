"""Build a tiny synthetic dump. Nothing in it comes from the game.

The sample exists so the editor, the tests and a machine without a dump can
still open a scene. Dictionary bytes are trained on these files, not copied
from a ZsDic pack.
"""

from __future__ import annotations

import struct
from pathlib import Path

import zstandard

from ocarina_forge.formats.bfres import sample_bfres
from ocarina_forge.formats.bntx import write_rgba
from ocarina_forge.formats.byml import Hash32, U32, U64, dumps
from ocarina_forge.formats.restbl import ResourceSizeTable, save_table
from ocarina_forge.formats.sarc import pack
from ocarina_forge.formats.zsio import DictSet, compress

# Stable instance ids. The sample project and the import example use these.
FADO = 0x1000000000000001
MIDO = 0x1000000000000002
BABA = 0x1000000000000003
TREE = 0x1000000000000004
START = 0x1000000000000005
HEART = 0x1000000000000006
GROUND = 0x1000000000000007
FIREFLY = 0x1000000000000008
CUCCO = 0x1000000000000009
SIGN = 0x100000000000000A
HOUSE = 0x100000000000000B
POT = 0x100000000000000C
AREA = 0x100000000000000D
TREE2 = 0x100000000000000E
STAL = 0x1000000000000020
GUARD = 0x1000000000000021
ROCK = 0x1000000000000022
BOSS_FLOOR = 0x1000000000000030
BOSS_TREE = 0x1000000000000031
BOSS_GANON = 0x1000000000000032
BOSS_CEILING = 0x1000000000000033

KOKIRI_STATIC = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs"
KOKIRI_DYNAMIC = "Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs"
FIELD_STATIC = "Banc/HyruleField/HyruleField_Adult_Static.bcett.byml.zs"
BOSS_STATIC = "Banc/BossRush/BossRush_InternalRoom_Static.bcett.byml.zs"

CATALOG = (
    "Npc_Kokiri_F_01",
    "Npc_Kokiri_M_01",
    "Npc_Zora_01",
    "Npc_Hyrule_Guard",
    "Enemy_DekuBaba",
    "Enemy_Stalchild",
    "Enemy_Lizalfos",
    "Obj_Tree",
    "Obj_Pot",
    "Obj_Rock",
    "Obj_TBox_A_02",
    "Item_RecoveryHeart",
    "Land_Kokiri_Ground",
    "Land_Kokiri_House_01",
    "Eft_Firefly",
    "Animal_Cucco",
    "FldObj_Sign",
    "StartPos",
    "Area_Kokiri_Town",
    "Weapon_Sword_Kokiri",
    "Shop_Kokiri",
    "Obj_BossRoom_Floor",
    "Obj_Ganon",
    "Obj_BossRoom_Ceiling",
)


def _actor(gyaml, hash_value, translate, rotate=None, scale=None, name=None, **extra):
    actor = {
        "Gyaml": gyaml,
        "Hash": U64(hash_value),
        "SRTHash": U32(hash_value & 0xFFFFFFFF),
        "Translate": [float(v) for v in translate],
        "Phive": {"Placement": {"ID": U64(hash_value)}},
    }
    if rotate is not None:
        actor["Rotate"] = [float(v) for v in rotate]
    if scale is not None:
        actor["Scale"] = [float(v) for v in scale]
    if name:
        actor["Name"] = name
    actor.update(extra)
    return actor


def _document(area: str, actors: list[dict], aux: bool = False) -> dict:
    document = {
        "FilePath": f"Work/Banc/Scene/{area}/{area}.bcett.json",
        "Actors": actors,
        "AiGroups": [],
        "FarDeleteGroups": [],
        "SimultaneousGroups": [],
        "PlgGdTable": [],
        "PhiveHeader": {"PlacementGroups": []},
    }
    if aux:
        document["AuxFlags"] = Hash32({0x10: U32(1), 0x20: U32(2)})
    return document


def kokiri_static() -> dict:
    fado = _actor(
        "Npc_Kokiri_F_01",
        FADO,
        (2, 0, 3),
        rotate=(0, 0.4, 0),
        name="Fado",
        Bakeable=False,
        Presence="Default",
        Dynamic={
            "TalkCount": 0,
            "IsEnable": True,
            "DefaultDungeonRoomIndex": U32(1),
            "Drop__DropActor": "Item_RecoveryHeart",
        },
        Links=[],
    )
    sign = _actor(
        "FldObj_Sign",
        SIGN,
        (5, 0, 4),
        Links=[{"Gyaml": "Npc_Kokiri_F_01", "Hash": U64(FADO), "Ref": "talk"}],
    )
    # The sign stores Translate as X/Y/Z, which some files do. The others use a list.
    sign["Translate"] = {"X": 5.0, "Y": 0.0, "Z": 4.0}
    return _document(
        "KokiriForest",
        [
            fado,
            _actor("Npc_Kokiri_M_01", MIDO, (-4, 0, 1), name="Mido", Presence="Default"),
            _actor("Enemy_DekuBaba", BABA, (8, 0, -2), rotate=(0, 1.0, 0)),
            _actor("Obj_Tree", TREE, (0, 0, -6), scale=(1.5, 1.5, 1.5)),
            _actor("Obj_Tree", TREE2, (6, 0, -8), name="Twin"),
            _actor("Area_Kokiri_Town", AREA, (0, 2, 0), scale=(30, 8, 30), name="Town"),
            _actor("StartPos", START, (0, 1, 8)),
            _actor("Item_RecoveryHeart", HEART, (3, 0.5, 5)),
            _actor("Land_Kokiri_Ground", GROUND, (0, -0.2, 0), scale=(20, 1, 20), Bakeable=True),
            _actor("Eft_Firefly", FIREFLY, (1, 2, 1)),
            _actor("Animal_Cucco", CUCCO, (-2, 0, 6), name="Cucco"),
            sign,
        ],
        aux=True,
    )


def kokiri_dynamic() -> dict:
    return _document(
        "KokiriForest",
        [
            _actor("Land_Kokiri_House_01", HOUSE, (-8, 0, 4), scale=(2, 2, 2), Bakeable=True),
            _actor("Obj_Pot", POT, (6, 0, 2)),
        ],
    )


def boss_room_static() -> dict:
    """A room with no heightmap. The floor is a model at y=2.5, not the origin."""
    return _document(
        "BossRush_InternalRoom",
        [
            _actor("Obj_BossRoom_Floor", BOSS_FLOOR, (0, 2.5, 0), name="Room floor"),
            _actor("Obj_BossRoom_Ceiling", BOSS_CEILING, (0, 95.0245, 0), name="Room ceiling"),
            _actor("Obj_Tree", BOSS_TREE, (0, 6, 0), name="Room tree"),
            _actor("Obj_Ganon", BOSS_GANON, (6, 20, -4), name="Ganon"),
        ],
    )


def field_static() -> dict:
    return _document(
        "HyruleField",
        [
            _actor("Enemy_Stalchild", STAL, (10, 0, 10)),
            _actor("Npc_Hyrule_Guard", GUARD, (0, 0, 0), rotate=(0, 0.2, 0), name="Guard"),
            _actor("Obj_Rock", ROCK, (4, 0, -3), scale=(2, 1, 2)),
        ],
    )


def _train(samples: list[bytes]) -> dict[int, bytes]:
    blob = {1: "zs.zsdic", 2: "bkprb.zsdic", 3: "bcett.byml.zsdic", 4: "pack.zsdic"}
    out = {}
    for dict_id in blob:
        trained = zstandard.train_dictionary(2048, samples, dict_id=dict_id)
        out[dict_id] = trained.as_bytes()
    return out


def _empty_table() -> ResourceSizeTable:
    raw = struct.pack("<6sIIII", b"RESTBL", 1, 160, 0, 0)
    return ResourceSizeTable(raw)


def build_fixture(root: Path) -> Path:
    """Write a dump root (the folder that contains romfs/) and return it."""
    root = Path(root)
    romfs = root / "romfs"
    if romfs.exists():
        # Rebuild from scratch so a partial fixture cannot linger.
        import shutil

        shutil.rmtree(root)
    romfs.mkdir(parents=True)

    scenes = {
        KOKIRI_STATIC: dumps(kokiri_static()),
        KOKIRI_DYNAMIC: dumps(kokiri_dynamic()),
        FIELD_STATIC: dumps(field_static()),
        BOSS_STATIC: dumps(boss_room_static()),
    }
    actor_params = {}
    for name in CATALOG:
        body = {"Name": name, "Synthetic": True}
        if name == "Npc_Kokiri_F_01":
            from ocarina_forge.behavior import fado_behavior_refs

            body.update(fado_behavior_refs())
        actor_params[name] = dumps(body)
    dictionaries = _train(list(scenes.values()) + list(actor_params.values()))
    dict_bytes = {dict_id: dictionaries[dict_id] for dict_id in dictionaries}
    zsdic = pack({("zs.zsdic" if i == 1 else "bkprb.zsdic" if i == 2 else "bcett.byml.zsdic" if i == 3 else "pack.zsdic"): raw for i, raw in dict_bytes.items()})
    (romfs / "Pack").mkdir(parents=True)
    (romfs / "Pack" / "ZsDic.pack.zs").write_bytes(
        zstandard.ZstdCompressor(level=3, write_content_size=True).compress(zsdic)
    )
    loaded = DictSet.load(romfs / "Pack" / "ZsDic.pack.zs")

    table = _empty_table()
    for relative, payload in scenes.items():
        packed = compress(payload, 3, loaded, level=3)
        destination = romfs / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(packed)
        table.raise_to(relative, (len(payload) + 512 + 7) & ~7)

    folder = romfs / "Pack" / "Actor"
    folder.mkdir(parents=True, exist_ok=True)
    model_info = {
        # Root FmdbName / ModelProjectName, the usual actor shape.
        "Npc_Kokiri_F_01": _model_info("Npc_Kokiri_F", "Npc_Kokiri_F_01"),
        # SubModels only, which is how some packs list every mesh.
        "Obj_Tree": _model_info("Obj_Tree", "Obj_Tree", sub=True),
        "Obj_BossRoom_Floor": _model_info("Obj_BossRoom_Floor", "Obj_BossRoom_Floor", sub=True),
        "Obj_BossRoom_Ceiling": _model_info("Obj_BossRoom_Ceiling", "Obj_BossRoom_Ceiling", sub=True),
        "Obj_Ganon": _model_info("Obj_Ganon", "Obj_Ganon", sub=True),
        "Enemy_DekuBaba": _model_info("Enemy_DekuBaba", "Enemy_DekuBaba"),
    }
    for name, payload in actor_params.items():
        files = {f"Actor/{name}.engine__actor__ActorParam.bgyml": payload}
        if name in model_info:
            files[f"Component/ModelInfo/{name}.engine__component__ModelInfo.bgyml"] = model_info[name]
        archive = pack(files)
        packed = compress(archive, 4, loaded, level=3)
        (folder / f"{name}.pack.zs").write_bytes(packed)

    model_dir = romfs / "Model"
    model_dir.mkdir(parents=True, exist_ok=True)
    tex_dir = romfs / "Tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    # Real dumps name meshes Model/<Project>.<Fmdb>.bfres.zs and keep the
    # base color in Tex/<name>.bntx.zs. The bfres itself has no embedded BNTX.
    _write_model(model_dir, tex_dir, loaded, "Npc_Kokiri_F.Npc_Kokiri_F_01", "Npc_Kokiri_F_01_Alb", _fado_model(), _fado_rgba())
    _write_model(model_dir, tex_dir, loaded, "Obj_Tree.Obj_Tree", "Obj_Tree_Alb", _tree_model(), _tree_rgba())
    _write_model(
        model_dir,
        tex_dir,
        loaded,
        "KokiriForest_01.KokiriForest_01",
        "KokiriForest_01_Alb",
        _terrain_model(),
        _terrain_rgba(),
    )
    _write_model(
        model_dir,
        tex_dir,
        loaded,
        "Obj_BossRoom_Floor.Obj_BossRoom_Floor",
        "Obj_BossRoom_Floor_Alb",
        _boss_floor_model(),
        _boss_floor_rgba(),
    )
    _write_model(
        model_dir,
        tex_dir,
        loaded,
        "Obj_BossRoom_Ceiling.Obj_BossRoom_Ceiling",
        "Obj_BossRoom_Ceiling_Alb",
        _boss_ceiling_model(),
        _boss_ceiling_rgba(),
    )
    _write_model(
        model_dir,
        tex_dir,
        loaded,
        "Obj_Ganon.Obj_Ganon",
        "Obj_Ganon_Alb",
        _ganon_model(),
        _ganon_rgba(),
    )
    from ocarina_forge.behavior import (
        FADO_AI,
        FADO_BROKEN,
        FADO_FLOW,
        FADO_SCHEDULE,
        sample_ai_bytes,
        sample_broken_ainb,
        sample_flow_bytes,
        sample_schedule_bytes,
    )

    for relative, payload in (
        (FADO_AI, sample_ai_bytes()),
        (FADO_SCHEDULE, sample_schedule_bytes()),
        (FADO_FLOW, sample_flow_bytes()),
        (FADO_BROKEN, sample_broken_ainb()),
    ):
        destination = romfs / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    # A truncated header at the path ModelInfo names, so the viewer can show a box and a reason.
    (model_dir / "Enemy_DekuBaba.Enemy_DekuBaba.bfres.zs").write_bytes(
        compress(b"FRES    " + bytes(48), 1, loaded, level=3)
    )

    # A named entry and an unrelated hashed entry, so export has to keep both.
    table.named.append(("System/Resource/KeepMe.bgyml", 4096))
    table.raise_to("AS/Untouched.root.asb", 2048)
    table_path = romfs / "System" / "Resource" / "ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_bytes(save_table(table, level=3))

    (root / "mods").mkdir(exist_ok=True)
    (root / "FIXTURE.txt").write_text(
        "Synthetic Ocarina Forge fixture. Not a game dump. Safe to commit; nothing here is from Nintendo.\n",
        encoding="utf-8",
    )
    return root


def _model_info(project: str, fmdb: str, *, sub: bool = False) -> bytes:
    pair = {"FmdbName": fmdb, "ModelProjectName": project}
    return dumps({"SubModels": [pair]} if sub else pair)


def _write_model(model_dir, tex_dir, dicts, stem: str, texture: str, mesh: bytes, rgba: tuple[bytes, int, int]) -> None:
    pixels, width, height = rgba
    (model_dir / f"{stem}.bfres.zs").write_bytes(compress(mesh, 1, dicts, level=3))
    image = write_rgba(texture, width, height, pixels, swizzled=True)
    (tex_dir / f"{texture}.bntx.zs").write_bytes(compress(image, 1, dicts, level=3))


def _checker(width: int, height: int, a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bytes:
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out.extend(a if ((x // 4) + (y // 4)) % 2 == 0 else b)
    return bytes(out)


def _fado_rgba():
    return _checker(16, 16, (224, 176, 90, 255), (92, 64, 28, 255)), 16, 16


def _fado_model() -> bytes:
    rgba, width, height = _fado_rgba()
    positions = [(0.0, 1.4, 0.0), (-0.45, 0.0, -0.45), (0.45, 0.0, -0.45), (0.0, 0.0, 0.55)]
    uvs = [(0.5, 1.0), (0.0, 0.0), (1.0, 0.0), (0.5, 0.15)]
    indices = [0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 3, 2]
    return sample_bfres(
        "Npc_Kokiri_F_01",
        positions,
        indices,
        uvs,
        rgba,
        width,
        height,
        texture_name="Npc_Kokiri_F_01_Alb",
        embed_texture=False,
    )


def _tree_rgba():
    return _checker(16, 16, (46, 110, 52, 255), (28, 72, 34, 255)), 16, 16


def _tree_model() -> bytes:
    rgba, width, height = _tree_rgba()
    positions = [(0.0, 3.2, 0.0), (-0.9, 0.4, -0.9), (0.9, 0.4, -0.9), (0.0, 0.4, 1.0), (0.0, 0.0, 0.0)]
    uvs = [(0.5, 1.0), (0.0, 0.3), (1.0, 0.3), (0.5, 0.0), (0.5, 0.2)]
    indices = [0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 4, 2, 2, 4, 3, 3, 4, 1]
    return sample_bfres(
        "Obj_Tree",
        positions,
        indices,
        uvs,
        rgba,
        width,
        height,
        texture_name="Obj_Tree_Alb",
        embed_texture=False,
    )


def _terrain_rgba():
    return bytes([78, 112, 58, 255, 96, 128, 64, 255, 70, 104, 50, 255, 88, 120, 60, 255]), 2, 2


def _boss_floor_rgba():
    return bytes([140, 108, 72, 255, 168, 132, 90, 255, 120, 92, 60, 255, 156, 120, 80, 255]), 2, 2


def _boss_floor_model() -> bytes:
    rgba, width, height = _boss_floor_rgba()
    positions = [(-10.0, 0.0, -10.0), (10.0, 0.0, -10.0), (10.0, 0.0, 10.0), (-10.0, 0.0, 10.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    indices = [0, 2, 1, 0, 3, 2]
    return sample_bfres(
        "Obj_BossRoom_Floor",
        positions,
        indices,
        uvs,
        rgba,
        width,
        height,
        texture_name="Obj_BossRoom_Floor_Alb",
        embed_texture=False,
    )


def _boss_ceiling_rgba():
    return bytes([90, 78, 70, 255, 110, 96, 86, 255, 70, 62, 56, 255, 100, 88, 78, 255]), 2, 2


def _boss_ceiling_model() -> bytes:
    rgba, width, height = _boss_ceiling_rgba()
    positions = [(-40.0, 0.0, -40.0), (40.0, 0.0, -40.0), (40.0, 0.0, 40.0), (-40.0, 0.0, 40.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    indices = [0, 2, 1, 0, 3, 2]
    return sample_bfres(
        "Obj_BossRoom_Ceiling",
        positions,
        indices,
        uvs,
        rgba,
        width,
        height,
        texture_name="Obj_BossRoom_Ceiling_Alb",
        embed_texture=False,
    )


def _ganon_rgba():
    return bytes([92, 24, 36, 255, 140, 48, 56, 255, 60, 16, 24, 255, 120, 36, 44, 255]), 2, 2


def _ganon_model() -> bytes:
    """A tall body. The feet sit just under the actor origin so a self-hit is below the pivot."""
    rgba, width, height = _ganon_rgba()
    positions = [
        (-1.2, -0.2, -1.2), (1.2, -0.2, -1.2), (1.2, -0.2, 1.2), (-1.2, -0.2, 1.2),
        (-1.2, 18.0, -1.2), (1.2, 18.0, -1.2), (1.2, 18.0, 1.2), (-1.2, 18.0, 1.2),
    ]
    uvs = [(0.0, 0.0)] * 8
    indices = [
        0, 2, 1, 0, 3, 2,
        4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4,
        1, 2, 6, 1, 6, 5,
        2, 3, 7, 2, 7, 6,
        3, 0, 4, 3, 4, 7,
    ]
    return sample_bfres(
        "Obj_Ganon",
        positions,
        indices,
        uvs,
        rgba,
        width,
        height,
        texture_name="Obj_Ganon_Alb",
        embed_texture=False,
    )


def _terrain_model() -> bytes:
    rgba, width, height = _terrain_rgba()
    positions = [(-40.0, -0.05, -40.0), (40.0, -0.05, -40.0), (40.0, -0.05, 40.0), (-40.0, -0.05, 40.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    indices = [0, 2, 1, 0, 3, 2]
    return sample_bfres(
        "KokiriForest_01",
        positions,
        indices,
        uvs,
        rgba,
        width,
        height,
        texture_name="KokiriForest_01_Alb",
        embed_texture=False,
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "fixtures" / "dump"
    build_fixture(root)
    print(f"wrote {root}")


if __name__ == "__main__":
    main()

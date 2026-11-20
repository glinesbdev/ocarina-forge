"""Repository locations and the game version this mod is built against."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_REPO = ROOT / "third_party" / "oot-remake-modding"
TOOLS = TOOLS_REPO / "tools"

# Commit of Kabosune/oot-remake-modding recorded by the submodule gitlink.
PINNED_TOOLS = "0b81628d6ac7a0653fb7e1d1125aad061ce3cfb2"

TITLE_ID = "0400486024DEC000"
UPDATE_VERSION = "v131072"
UPDATE_LABEL = "1.1.1"
BUILD_ID = "e0f3ccfa"
MAIN_BUILD_ID = "E0F3CCFAF50065AE4EA01EB5AEB13B1A45DA3348"

RSTB_REL = "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"
ZSDIC_REL = "Pack/ZsDic.pack.zs"

TOOL_FILES = (
    "msbtmod.py",
    "rstb_set.py",
    "zstool.py",
    "zsmod.py",
    "evflags.py",
    "asbtool.py",
    "byml.py",
)

SRC = ROOT / "src"
TEXT_DIR = SRC / "text"
EVENTS_DIR = SRC / "events"
RSDB_DIR = SRC / "rsdb"
GAMEDATA_DIR = SRC / "gamedata"
EVENTS_PATH = EVENTS_DIR / "edits.yaml"
SCENES_PATH = SRC / "scenes" / "reroutes.yaml"
TAPESTRY_PATH = SRC / "ui" / "m8-tapestry.yaml"
GAMEDATA_REL = "GameData/GameDataList.Product.110.byml.zs"
MOD_TOML = SRC / "mod.toml"
BUILD_DIR = ROOT / "build" / "romfs"


def configure(src: Path) -> None:
    """Point the source directories at a project. ROOT and the tools stay put.

    ``tables/`` is accepted as the RSDB directory when ``rsdb/`` is absent.
    Callers that imported these names must rebind them; see ocarina_forge.engine.ato.
    """
    global SRC, TEXT_DIR, EVENTS_DIR, RSDB_DIR, GAMEDATA_DIR
    global EVENTS_PATH, SCENES_PATH, TAPESTRY_PATH, MOD_TOML
    SRC = Path(src)
    TEXT_DIR = SRC / "text"
    EVENTS_DIR = SRC / "events"
    if (SRC / "rsdb").is_dir() or not (SRC / "tables").is_dir():
        RSDB_DIR = SRC / "rsdb"
    else:
        RSDB_DIR = SRC / "tables"
    GAMEDATA_DIR = SRC / "gamedata"
    EVENTS_PATH = EVENTS_DIR / "edits.yaml"
    SCENES_PATH = SRC / "scenes" / "reroutes.yaml"
    TAPESTRY_PATH = SRC / "ui" / "m8-tapestry.yaml"
    MOD_TOML = SRC / "mod.toml"


def require_tools() -> Path:
    """Return the tools directory, or raise ModError if the submodule is missing."""
    from after_ocarina.errors import ModError

    missing = [name for name in TOOL_FILES if not (TOOLS / name).is_file()]
    if missing:
        raise ModError(
            "The modding tools submodule is missing "
            f"({', '.join(missing)}). From the repo root run: python scripts/setup.py"
        )
    return TOOLS

"""Run the vendored After the Ocarina pipeline against a Bancwright project.

The project directory is the pipeline's ``src``: ``text/``, ``events/``,
``scenes/``, ``rsdb/`` (or ``tables/``), ``gamedata/``, ``ui/``, ``mod.toml``.
The package and the community tools stay under ``vendor/after_ocarina``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def vendor_root() -> Path:
    return Path(__file__).resolve().parents[2] / "vendor" / "after_ocarina"


def ensure_imported():
    root = str(vendor_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    import after_ocarina.paths as paths

    return paths


def bind_project(src: Path) -> None:
    """Point every module that captured a source path at this project."""
    paths = ensure_imported()
    paths.configure(src)
    import after_ocarina.boolexp as boolexp
    import after_ocarina.castle as castle
    import after_ocarina.config as config
    import after_ocarina.gamedata as gamedata
    import after_ocarina.rsdb as rsdb
    import after_ocarina.saria as saria
    import after_ocarina.sources as sources
    import after_ocarina.stones as stones
    import after_ocarina.tapestry as tapestry

    rsdb.RSDB_DIR = paths.RSDB_DIR
    sources.EVENTS_DIR = paths.EVENTS_DIR
    sources.EVENTS_PATH = paths.EVENTS_PATH
    sources.SCENES_PATH = paths.SCENES_PATH
    sources.TEXT_DIR = paths.TEXT_DIR
    stones.SCENES_PATH = paths.SCENES_PATH
    castle.SCENES_PATH = paths.SCENES_PATH
    boolexp.GAMEDATA_DIR = paths.GAMEDATA_DIR
    gamedata.GAMEDATA_DIR = paths.GAMEDATA_DIR
    saria.SCENES_PATH = paths.SCENES_PATH
    config.MOD_TOML = paths.MOD_TOML
    tapestry.TAPESTRY_PATH = paths.TAPESTRY_PATH
    try:
        import after_ocarina.selftest as selftest
    except ImportError:
        return
    selftest.TEXT_DIR = paths.TEXT_DIR
    selftest.EVENTS_PATH = paths.EVENTS_PATH
    selftest.SCENES_PATH = paths.SCENES_PATH


def build_project(
    project_root: Path,
    romfs: Path,
    out_dir: Path,
    *,
    mod_id: str,
    mod_name: str,
    install: bool = False,
    mods_root: Path | None = None,
):
    """Build one project. Returns the pipeline's BuildResult.

    Raises the pipeline's ModError when the dump or a source file is wrong.
    """
    bind_project(project_root)
    import after_ocarina.paths as paths
    from after_ocarina.pipeline import build_mod

    return build_mod(
        romfs,
        mod_id=mod_id,
        mod_name=mod_name,
        text_dir=paths.TEXT_DIR,
        events_path=paths.EVENTS_PATH,
        scenes_path=paths.SCENES_PATH,
        out_dir=out_dir,
        events_dir=paths.EVENTS_DIR,
        mods_root=mods_root,
        install=install,
    )

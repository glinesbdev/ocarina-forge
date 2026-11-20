"""A read-only view of the user's own romfs.

Ocarina Forge never writes into the dump. Mods are built beside it, under mods/.
"""

from __future__ import annotations

from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import loads
from ocarina_forge.formats.zsio import DictSet, decompress

RSTB_REL = "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"


def resolve_dump(path: Path) -> tuple[Path, Path]:
    """Return (dump root, romfs directory).

    The dump root is the Longshot folder that contains `romfs/` and `mods/`.
    Pointing directly at `romfs/` is accepted too.
    """
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        raise BancError(f"Cannot read {path}: {exc}", "dump_missing") from exc
    if not candidate.exists():
        raise BancError(
            f"Nothing at {candidate}. Point Ocarina Forge at your Longshot folder, "
            "the one that contains romfs/ (on this machine that is usually "
            "~/Games/Longshot/rc/v3.0.0).",
            "dump_missing",
        )
    if (candidate / "romfs").is_dir():
        return candidate, candidate / "romfs"
    if candidate.name == "romfs" and candidate.is_dir():
        return candidate.parent, candidate
    if (candidate / "Banc").is_dir():
        return candidate.parent, candidate
    raise BancError(
        f"{candidate} has no romfs directory. Ocarina Forge needs the folder that contains "
        "romfs/Banc/<Area>/*.bcett.byml.zs.",
        "dump_invalid",
    )


class Dump:
    def __init__(self, path: Path):
        self.root, self.romfs = resolve_dump(path)
        self._dicts: DictSet | None = None

    @property
    def synthetic(self) -> bool:
        return (self.root / "FIXTURE.txt").is_file()

    def dicts(self) -> DictSet:
        if self._dicts is None:
            self._dicts = DictSet.load(self.romfs / "Pack" / "ZsDic.pack.zs")
        return self._dicts

    def list_scenes(self) -> list[dict]:
        banc = self.romfs / "Banc"
        if not banc.is_dir():
            raise BancError(
                f"{self.romfs} has no Banc folder. Expected romfs/Banc/<Area>/<Scene>.bcett.byml.zs "
                "from your own dump of the 1.1.1 romfs.",
                "dump_invalid",
            )
        scenes = []
        for file in sorted(banc.rglob("*.bcett.byml.zs")):
            relative = file.relative_to(self.romfs).as_posix()
            parts = relative.split("/")
            area = parts[1] if len(parts) >= 3 else "(root)"
            scenes.append(
                {
                    "path": relative,
                    "area": area,
                    "name": file.name.removesuffix(".bcett.byml.zs"),
                }
            )
        return scenes

    def actor_names(self) -> list[str]:
        folder = self.romfs / "Pack" / "Actor"
        if not folder.is_dir():
            return []
        return sorted(file.name.removesuffix(".pack.zs") for file in folder.glob("*.pack.zs"))

    def read_file(self, relative: str) -> tuple[bytes, int | None]:
        path = self.romfs / relative
        if not path.is_file():
            raise BancError(
                f"{relative} is not in this dump ({self.romfs}).",
                "not_found",
            )
        return decompress(path.read_bytes(), self.dicts())

    def read_byml(self, relative: str):
        payload, dict_id = self.read_file(relative)
        try:
            return loads(payload), dict_id, payload
        except ValueError as exc:
            raise BancError(f"{relative} is not a BYML file Ocarina Forge can read: {exc}", "bad_byml") from exc

    def rstb_relative(self) -> str | None:
        preferred = self.romfs / RSTB_REL
        if preferred.is_file():
            return RSTB_REL
        folder = self.romfs / "System" / "Resource"
        if not folder.is_dir():
            return None
        found = sorted(folder.glob("*.rsizetable.zs"))
        if not found:
            return None
        return found[0].relative_to(self.romfs).as_posix()

"""Fail when a tracked file looks like a game dump, key, or extracted asset."""

from __future__ import annotations

import subprocess
from pathlib import Path

from after_ocarina.paths import ROOT, TITLE_ID

# Suffixes of dumps, archives, patches and extracted assets. Checked against
# the final suffix and against the whole filename, so `file.sarc.zs` matches.
SUFFIXES = (
    ".nca",
    ".nso",
    ".nsp",
    ".xci",
    ".nsz",
    ".xcz",
    ".ncz",
    ".nup",
    ".npdm",
    ".tik",
    ".cert",
    ".keys",
    ".ips",
    ".pchtxt",
    ".sav",
    ".zs",
    ".sarc",
    ".byml",
    ".bgyml",
    ".msbt",
    ".bfevfl",
    ".bfres",
    ".bntx",
    ".bars",
    ".bwav",
    ".rsizetable",
    ".bfsha",
    ".ainb",
    ".asb",
    ".bphsh",
    ".bphsc",
    ".bphnm",
    ".bkres",
    ".bkprb",
    ".pack",
    ".webm",
    ".bcul",
    ".tscb",
    ".tmb",
    ".bin",
)

NAMES = {
    "prod.keys",
    "title.keys",
    "keys.txt",
    "image.bin",
    "main",
    "main.elf",
    "main.npdm",
}

# Directory names that, anywhere in a tracked path, mean a dump was added.
DUMP_PARTS = {"romfs", "exefs", "dump", "dumps"}

MAGICS = (
    (b"\x28\xb5\x2f\xfd", "zstd frame"),
    (b"SARC", "SARC archive"),
    (b"RESTBL", "resource size table"),
    (b"MsgStdBn", "MSBT text"),
    (b"BFEVFL", "event flow"),
    (b"FRES", "BFRES model"),
    (b"ASB ", "animation graph"),
    (b"NSO0", "NSO executable"),
)


def game_file_reason(rel: str, head: bytes = b"") -> str | None:
    """Return why ``rel`` must not be committed, or None when it is fine."""
    path = Path(rel)
    name = path.name.lower()
    if name in NAMES or TITLE_ID.lower() in name:
        return f"name {path.name}"
    if DUMP_PARTS.intersection(part.lower() for part in path.parts):
        return "path is under a dump directory"
    if any(name.endswith(suffix) for suffix in SUFFIXES):
        return f"suffix of {path.name}"
    for magic, label in MAGICS:
        if head.startswith(magic):
            return label
    return None


def tracked_files(repo: Path) -> list[str]:
    """Paths tracked by ``repo`` and, when present, by the tools submodule."""
    listed = _git_files(repo)
    submodule = repo / "third_party" / "oot-remake-modding"
    if (submodule / ".git").exists() and _is_submodule(repo, "third_party/oot-remake-modding"):
        listed.extend(f"third_party/oot-remake-modding/{rel}" for rel in _git_files(submodule))
    return listed


def find_game_files(repo: Path | None = None) -> list[tuple[str, str]]:
    repo = repo or ROOT
    found = []
    seen = set()
    for rel in _candidates(repo):
        if rel in seen:
            continue
        seen.add(rel)
        path = repo / rel
        if not path.is_file():
            continue
        reason = game_file_reason(rel, path.read_bytes()[:16])
        if reason:
            found.append((rel, reason))
    return found


def _candidates(repo: Path) -> list[str]:
    listed = tracked_files(repo)
    output = subprocess.check_output(
        ["git", "ls-files", "-o", "--exclude-standard", "-z"],
        cwd=repo,
    )
    listed.extend(item.decode() for item in output.split(b"\0") if item)
    return listed


def _git_files(repo: Path) -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo)
    return [item.decode() for item in output.split(b"\0") if item]


def _is_submodule(repo: Path, rel: str) -> bool:
    output = subprocess.check_output(["git", "ls-files", "-s", "--", rel], cwd=repo, text=True)
    return output.startswith("160000")

"""The After the Ocarina project, the verified size-table rule, and the fixture build."""

from pathlib import Path

import pytest

from bancwright.catalog import catalog
from bancwright.engine.ato import bind_project, vendor_root
from bancwright.importers.ato_project import import_ato_project
from bancwright.project import Project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "after-the-ocarina"


def test_example_project_lists_bradyns_sources():
    project = Project(EXAMPLE)
    assert project.manifest["format"] == 2
    assert project.manifest["engine"] == "after-ocarina"
    assert project.manifest["name"] == "after-ocarina"
    summary = catalog(project)
    labels = {item["label"] for item in summary["text"]}
    assert "Dm_Kokiri_0060_0002" in labels
    titles = {item["file"] for item in summary["events"]}
    assert "m2a-boot-hook.yaml" in titles
    assert "m8-navi-rumors.yaml" in titles
    quests = {item["name"] for item in summary["quests"]}
    assert "EscapeFromCrumblingGanonCastle" in quests
    assert "Defeat_Ganon" in quests
    assert any(item["name"] == "EscapeFromCrumblingGanonCastle" for item in summary["ui"])
    assert summary["recipeBancs"]
    # Scene recipes are not actor diffs, so the viewport does not pretend they are.
    assert project.modified_scenes() == []
    # No game-derived bytes snuck into the project.
    for path in EXAMPLE.rglob("*"):
        if path.is_file():
            assert path.suffix.lower() in {".yaml", ".yml", ".toml"}, path


def test_importer_copies_yaml_only(tmp_path):
    ato = tmp_path / "ato"
    src = ato / "src"
    (src / "text").mkdir(parents=True)
    (src / "events").mkdir()
    (src / "scenes").mkdir()
    (src / "mod.toml").write_text('id = "demo-mod"\nname = "Demo"\n', encoding="utf-8")
    (src / "text" / "one.yaml").write_text("edits: []\n", encoding="utf-8")
    (src / "events" / "edits.yaml").write_text("flows: []\n", encoding="utf-8")
    (src / "scenes" / "reroutes.yaml").write_text("reroutes: []\n", encoding="utf-8")
    (src / "stolen.bfres").write_bytes(b"FRES")
    dest = tmp_path / "project"
    with pytest.raises(Exception):
        # A non-text file inside a copied folder is refused. The loose file above is outside those folders.
        (src / "text" / "model.bfres").write_bytes(b"nope")
        import_ato_project(ato, dest)
    (src / "text" / "model.bfres").unlink()
    result = import_ato_project(ato, dest)
    assert (dest / "text" / "one.yaml").is_file()
    assert (dest / "manifest.yaml").is_file()
    assert result["manifest"]["name"] == "demo-mod"
    assert not (dest / "stolen.bfres").exists()


def test_compare_script_prefers_the_checkout_venv(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("compare_romfs", ROOT / "scripts" / "compare_romfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ato = tmp_path / "ato"
    python = ato / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)
    assert module.ato_python(ato) == python
    bare = tmp_path / "bare"
    bare.mkdir()
    assert module.ato_python(bare) == Path(module.sys.executable)
    err = capsys.readouterr().err
    assert "requirements.txt" in err
    assert "evfl" in err


def test_size_rule_matches_the_vendored_pipeline():
    import sys

    root = str(vendor_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from after_ocarina.rstb import rewrite_hashed, target_entry

    from bancwright.formats.restbl import entry_for_byml

    assert entry_for_byml(14448, 3316, 3384) == target_entry(14448, 3384, old_unpacked=3316) == 15544
    assert entry_for_byml(12984, 12598, 13189) == 14600
    # A shrink still raises by the margin, and rewrite_hashed refuses to go backwards.
    table = _tiny_table({"System/Resource/Keep.bgyml": 1000})
    raised = rewrite_hashed(table, {"System/Resource/Keep.bgyml": 2048}, {})
    assert _hashed_size(raised, "System/Resource/Keep.bgyml") == 2048
    with pytest.raises(Exception):
        rewrite_hashed(raised, {"System/Resource/Keep.bgyml": 8}, {})
    added = rewrite_hashed(table, {}, {"Model/New.bfres": 40152})
    assert _hashed_size(added, "Model/New.bfres") == 40152
    with pytest.raises(Exception):
        rewrite_hashed(added, {}, {"Model/New.bfres": 40152})


def test_fixture_build_matches_the_vendored_self_test():
    """The pipeline's own invented dump. This is the check that does not need a game dump."""
    bind_project(EXAMPLE)
    from after_ocarina.selftest import run_self_test

    run_self_test()


def _tiny_table(entries: dict[str, int]) -> bytes:
    import struct
    import zlib

    name_size = 32
    hashed = []
    for path, size in entries.items():
        hashed.append((zlib.crc32(path.encode()) & 0xFFFFFFFF, size))
    hashed.sort()
    header = bytearray(22)
    header[:6] = b"RESTBL"
    struct.pack_into("<I", header, 6, 1)
    struct.pack_into("<I", header, 10, name_size)
    struct.pack_into("<I", header, 14, len(hashed))
    struct.pack_into("<I", header, 18, 0)
    body = b"".join(struct.pack("<II", crc, size) for crc, size in hashed)
    return bytes(header) + body


def _hashed_size(data: bytes, path: str) -> int:
    import struct
    import zlib

    crc = zlib.crc32(path.encode()) & 0xFFFFFFFF
    n_hashed = struct.unpack_from("<I", data, 14)[0]
    for index in range(n_hashed):
        got, size = struct.unpack_from("<II", data, 22 + 8 * index)
        if got == crc:
            return size
    raise AssertionError(path)

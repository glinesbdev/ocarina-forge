import json
from pathlib import Path

from fastapi.testclient import TestClient

from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import FADO, KOKIRI_STATIC, build_fixture
from ocarina_forge.modexport import export_mod
from ocarina_forge.project import Project
from ocarina_forge.server import app


def _mod(root: Path, *, op: str = "set") -> Path:
    source = root / "mods" / "local" / "static-move"
    (source / "src").mkdir(parents=True)
    (source / "patches").mkdir()
    (source / "romfs").mkdir()
    manifest = {
        "id": "local/static-move",
        "name": "Static Move",
        "version": "0.1.0",
        "runtime": "js",
        "entry": "src/main.ts",
        "permissions": ["content.read"],
        "requires": ["content.read.placements"],
        "multiplayer": "all",
        "customField": {"keep": True},
    }
    (source / "mod.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "src" / "main.ts").write_text("export const running = true;\n", encoding="utf-8")
    (source / "romfs" / "asset.bin").write_bytes(b"runtime asset")
    (source / "patches" / "kokiri.json").write_text(
        json.dumps({
            "target": KOKIRI_STATIC,
            "description": "Move the sign and a mod-added placement.",
            "placements": [
                {"op": op, "hash": f"0x{FADO:016x}", "path": "Translate", "value": [6, 0, 3]},
                {"op": "set", "hash": "0xdeadbeefdeadbeef", "path": "Rotate", "value": [0, 0.5, 0]},
            ],
        }),
        encoding="utf-8",
    )
    return source


def test_local_mod_api_imports_patches_and_preserves_runtime(tmp_path, monkeypatch):
    game = tmp_path / "game"
    projects = tmp_path / "projects"
    source = _mod(game)
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("OCARINA_FORGE_GAME", str(game))
    monkeypatch.setenv("OCARINA_FORGE_PROJECTS", str(projects))
    client = TestClient(app)

    listed = client.get("/api/local-mods")
    assert listed.status_code == 200
    item = listed.json()["mods"][0]
    assert item["id"] == "local/static-move"
    assert item["editable"] is True
    assert item["action"] == "import"

    opened = client.post("/api/local-mods/open", json={"id": item["id"]})
    assert opened.status_code == 200, opened.text
    project = Project(opened.json()["root"])
    diff = project.load_scene_diff(KOKIRI_STATIC)
    assert diff["actors"][f"0x{FADO:016x}"]["translate"] == [6.0, 0.0, 3.0]
    unknown = diff["actors"]["0xdeadbeefdeadbeef"]
    assert unknown["rotate"] == [0.0, 0.5, 0.0]
    assert (project.root / ".longshot-source" / "src" / "main.ts").read_text(encoding="utf-8") == (
        source / "src" / "main.ts"
    ).read_text(encoding="utf-8")

    dump = Dump(build_fixture(tmp_path / "dump"))
    result = export_mod(project, dump, install=True, game=game)
    installed = Path(result["installed"])
    exported_manifest = json.loads((installed / "mod.json").read_text(encoding="utf-8"))
    assert exported_manifest["entry"] == "src/main.ts"
    assert exported_manifest["runtime"] == "js"
    assert exported_manifest["permissions"] == ["content.read"]
    assert exported_manifest["requires"] == ["content.read.placements"]
    assert exported_manifest["multiplayer"] == "all"
    assert exported_manifest["customField"] == {"keep": True}
    assert (installed / "src" / "main.ts").is_file()
    assert (installed / "romfs" / "asset.bin").read_bytes() == b"runtime asset"
    patch = json.loads(next((installed / "patches").glob("*.json")).read_text(encoding="utf-8"))
    keys = {(op["hash"], op["path"]) for op in patch["placements"]}
    assert (f"0x{FADO:016x}", "Translate") in keys
    assert ("0xdeadbeefdeadbeef", "Rotate") in keys


def test_unsupported_local_patch_is_marked_not_editable(tmp_path, monkeypatch):
    game = tmp_path / "game"
    projects = tmp_path / "projects"
    _mod(game, op="remove")
    monkeypatch.setenv("OCARINA_FORGE_GAME", str(game))
    monkeypatch.setenv("OCARINA_FORGE_PROJECTS", str(projects))
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))

    client = TestClient(app)
    item = client.get("/api/local-mods").json()["mods"][0]
    assert item["editable"] is False
    assert "set operations only" in item["reason"]
    opened = client.post("/api/local-mods/open", json={"id": item["id"]})
    assert opened.status_code == 400
    assert "set operations only" in opened.json()["message"]

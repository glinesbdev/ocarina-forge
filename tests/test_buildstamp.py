"""Build stamps and editor cache headers."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ocarina_forge.buildstamp import read_build
from ocarina_forge.editor_static import cache_control_for
from ocarina_forge.local_api import editor_client
from ocarina_forge.server import app


def test_cache_control_revalidates_html_and_pins_hashed_assets():
    assert cache_control_for("") == "no-cache"
    assert cache_control_for("index.html") == "no-cache"
    assert cache_control_for("assets/index-Abcdef12.js") == "public, max-age=31536000, immutable"
    assert cache_control_for("assets/index-Abcdef12.js.map") == "public, max-age=31536000, immutable"
    assert cache_control_for("assets/plain.js") == "no-cache"
    assert cache_control_for("basis/basis_transcoder.wasm") == "no-cache"


def test_editor_responses_set_those_cache_headers(tmp_path):
    from ocarina_forge.editor_static import EditorStaticFiles

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "basis").mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Forge</title>", encoding="utf-8")
    (dist / "assets" / "index-Abcdef12.js").write_text("export {}", encoding="utf-8")
    (dist / "assets" / "plain.js").write_text("export {}", encoding="utf-8")
    (dist / "basis" / "basis_transcoder.wasm").write_bytes(b"\0asm")
    editor = FastAPI()
    editor.mount("/", EditorStaticFiles(directory=dist, html=True), name="frontend")
    client = TestClient(editor)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/index.html").headers["cache-control"] == "no-cache"
    hashed = client.get("/assets/index-Abcdef12.js")
    assert hashed.status_code == 200
    assert hashed.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/assets/plain.js").headers["cache-control"] == "no-cache"
    assert client.get("/basis/basis_transcoder.wasm").headers["cache-control"] == "no-cache"


def test_read_build_uses_git_or_unknown(tmp_path):
    missing = read_build(tmp_path)
    assert missing == {"revision": "unknown", "committedAt": 0}
    found = read_build()
    assert found["revision"] != "unknown"
    assert len(found["revision"]) == 40
    assert found["committedAt"] > 0


def test_health_includes_the_server_build():
    body = editor_client(app).get("/api/health").json()
    assert body["ok"] is True
    assert body["version"]
    assert len(body["build"]["revision"]) == 40
    assert body["build"]["committedAt"] > 0

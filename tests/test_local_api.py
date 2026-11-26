"""The local API rejects foreign origins, a non-loopback Host, and path escapes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ocarina_forge.dump import DUMP_PATH_MESSAGE, Dump, lexical_dump_relative
from ocarina_forge.errors import BancError
from ocarina_forge.fixtures import KOKIRI_STATIC, build_fixture
from ocarina_forge.local_api import LAUNCH_TOKEN, editor_client
from ocarina_forge.project import Project


def _client(app):
    return editor_client(app, headers={"Origin": "http://127.0.0.1:47231"})


def test_cross_origin_write_is_rejected():
    from ocarina_forge.server import app

    client = _client(app)
    rejected = client.post(
        "/api/project",
        json={"name": "not-created"},
        headers={"Origin": "https://evil.example"},
    )
    assert rejected.status_code == 403
    assert rejected.headers.get("access-control-allow-origin") in (None, "")
    assert "*" not in rejected.headers.get("access-control-allow-origin", "")

    preflight = client.options(
        "/api/project",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 403
    assert "access-control-allow-origin" not in preflight.headers


def test_same_origin_write_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    from ocarina_forge.server import app

    client = _client(app)
    saved = client.put("/api/config", json={"workers": 1})
    assert saved.status_code == 200, saved.text
    assert saved.headers["access-control-allow-origin"] == "http://127.0.0.1:47231"
    assert saved.json()["workers"]["count"] == 1

    bare = TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-Ocarina-Forge-Token": LAUNCH_TOKEN},
    )
    referred = bare.put(
        "/api/config",
        json={"workers": 1},
        headers={"Referer": "http://localhost:47231/"},
    )
    assert referred.status_code == 200, referred.text

    missing_token = TestClient(app, base_url="http://127.0.0.1")
    denied = missing_token.put(
        "/api/config",
        json={"workers": 1},
        headers={"Origin": "http://127.0.0.1:47231"},
    )
    assert denied.status_code == 403


def test_non_loopback_host_is_rejected():
    from ocarina_forge.server import app

    client = TestClient(app, base_url="http://127.0.0.1")
    rejected = client.get("/api/health", headers={"Host": "evil.example"})
    assert rejected.status_code == 403
    assert "localhost" in rejected.json()["message"]

    loopback = client.get("/api/health", headers={"Host": "127.0.0.1:47232"})
    assert loopback.status_code == 200
    assert loopback.json()["token"]


def test_scene_paths_cannot_escape_the_dump(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    from ocarina_forge.server import app

    dump = Dump(build_fixture(tmp_path / "dump"))
    canary = tmp_path / "secret.txt"
    canary.write_text("CANARY-SECRET-VALUE", encoding="utf-8")
    client = _client(app)
    saved = client.put("/api/config", json={"dump": str(dump.root), "project": ""})
    assert saved.status_code == 200, saved.text

    real_read = Path.read_bytes

    def guarded(self):
        try:
            current = self.resolve()
        except OSError:
            current = None
        if current == canary.resolve():
            raise AssertionError(f"read outside the dump: {self}")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    attempts = [
        str(canary),
        "/etc/hostname",
        "..\\..\\..\\Windows\\system32\\drivers\\etc\\hosts",
        "C:\\Windows\\system32\\drivers\\etc\\hosts",
        "Banc/../../etc/passwd",
    ]
    for banc in attempts:
        response = client.get("/api/scene", params={"banc": banc})
        assert response.status_code == 404, banc
        assert "CANARY-SECRET-VALUE" not in response.text
        assert "is not a BYML" not in response.text
        assert response.json()["message"] == DUMP_PATH_MESSAGE

    opened = client.get("/api/scene", params={"banc": KOKIRI_STATIC})
    assert opened.status_code == 200, opened.text
    assert opened.json()["banc"] == KOKIRI_STATIC

    link = dump.romfs / "escape"
    try:
        link.symlink_to(canary)
    except OSError:
        return
    escaped = client.get("/api/scene", params={"banc": "escape"})
    assert escaped.status_code == 404
    assert "CANARY-SECRET-VALUE" not in escaped.text
    assert "is not a BYML" not in escaped.text


def test_scene_diff_cannot_leave_the_project(tmp_path):
    project = Project.create(tmp_path / "mod", name="notes", title="Notes", author="Ada", version="0.1.0")
    with pytest.raises(BancError) as raised:
        project.scene_path("../../outside")
    assert raised.value.code == "not_found"
    assert not (tmp_path / "outside.yaml").exists()
    assert lexical_dump_relative(KOKIRI_STATIC).endswith(".bcett.byml.zs")

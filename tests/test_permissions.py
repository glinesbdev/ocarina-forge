"""Permission table, manifest rules, script scan, and mods.json grants."""

import json
from pathlib import Path

import pytest

from ocarina_forge.errors import BancError
from ocarina_forge.lighting import CAPTURE_PERMISSIONS, capture_manifest_text, capture_root
from ocarina_forge.modexport import _manifest_json
from ocarina_forge.permissions import (
    FIXED_IDS,
    SOURCE,
    catalog,
    compare_scan,
    grant_in_mods_json,
    project_declaration,
    save_project_permissions,
    scan_sources,
    scan_tree,
    validate_declaration,
)
from ocarina_forge.project import Project


PERMISSION_TYPE = {
    "state.read", "storage", "state.write", "actors.read", "actors.write", "actors.spawn",
    "actors.remove", "items.read", "input.read", "input.write", "camera.write", "content.add",
    "content.read", "items.grant", "audio", "audio.game", "ui.hud", "ui.window", "ui.settings",
    "gfx.world", "gfx.shader", "net:*", "net.lan", "mods.inspect", "mods.control", "romfs.read",
    "memory.read", "memory.write", "hooks.unsafe", "ipc", "discord.status", "gfx.textures",
    "content.patch", "session", "save.flags", "romfs.provide", "exefs",
}


def test_the_table_matches_the_permission_type_at_563ca4e5():
    assert SOURCE["commitShort"] == "563ca4e5"
    assert SOURCE["file"].endswith("permissions.md")
    assert FIXED_IDS == PERMISSION_TYPE
    rows = [item for group in catalog()["groups"] for item in group["permissions"]]
    assert {row["id"] for row in rows} == PERMISSION_TYPE
    assert {row["risk"] for row in rows} <= {"low", "medium", "high", "unsafe"}
    system = {row["id"] for row in rows if row["systemOnly"]}
    assert system == {"mods.inspect", "mods.control", "ipc"}
    assert catalog()["net"]["player"] == "Can talk to <origin>"


def test_manifest_rules_from_the_modding_api():
    overlap = validate_declaration({
        "permissions": ["state.read", "state.read"],
        "optionalPermissions": ["state.read", "storage"],
    })
    assert any("twice" in item for item in overlap["errors"])
    assert any("already in permissions" in item for item in overlap["errors"])

    local = validate_declaration({"permissions": ["items.grant"], "multiplayer": "local"})
    assert any("cannot be one player's own choice" in item for item in local["errors"])
    host = validate_declaration({"permissions": ["actors.remove", "content.add"], "multiplayer": "host"})
    assert host["errors"] == []

    implied = validate_declaration({"net": ["https://tracker.example.org", "*"]})
    assert any("net:https://tracker.example.org" in item for item in implied["errors"])
    assert any("net:*" in item for item in implied["errors"])
    optional_net = validate_declaration({
        "net": ["wss://ap.example.org:38281"],
        "optionalPermissions": ["net:wss://ap.example.org:38281"],
    })
    assert optional_net["errors"] == []

    bad_origin = validate_declaration({"net": ["http://example.org/path"]})
    assert any("not \"*\"" in item for item in bad_origin["errors"])

    player = validate_declaration({"permissions": ["mods.inspect", "ipc"]})
    assert any("system mod" in item for item in player["errors"])
    system = validate_declaration({"permissions": ["mods.inspect"], "system": True})
    assert system["errors"] == []

    leaked = validate_declaration({"permissions": ["romfs.read", "net:*", "session"]})
    assert any("way off this PC" in item for item in leaked["errors"])
    unknown = validate_declaration({"permissions": ["teleport"]})
    assert any("not a permission" in item for item in unknown["errors"])
    content = validate_declaration({}, content="src/content.ts", runtime="js")
    assert any("content.add" in item for item in content["errors"])
    patched = validate_declaration({"permissions": ["content.patch"]}, content="src/content.ts", runtime="js")
    assert patched["errors"] == []


def test_scan_maps_calls_and_flags_dynamic_import():
    sources = {
        "main.js": """
            import { state, flags, frame } from "longshot/game";
            import { list, spawn } from "longshot/actors";
            import log from "longshot/log";
            import { helper } from "./helper.js";
            export function run() {
              frame.on(() => state());
              flags.set("x", 1);
              list();
              spawn({});
              log.info("hi");
              helper();
              return <Hud>hi</Hud>;
            }
        """,
        "helper.ts": """
            import { Hud } from "longshot/ui";
            import { pause } from "longshot/game";
            export function helper() {
              pause();
              return Hud({});
            }
            const mod = await import("longshot/gfx");
        """,
    }
    # The JSX in main.js names Hud without importing it, so only helper's Hud counts.
    scan = scan_sources(sources, entry="main.js")
    assert scan["required"] == ["state.read", "state.write", "actors.read", "actors.spawn", "camera.write", "ui.hud"]
    assert scan["dynamicImports"] == [{"file": "helper.ts", "line": 8, "specifier": "longshot/gfx"}]
    declared = {"permissions": ["state.read"], "optionalPermissions": [], "net": []}
    compared = compare_scan(declared, scan)
    assert "state.write" in compared["missing"]
    assert "state.read" not in compared["missing"]
    assert compared["unused"] == []


def test_namespace_calls_and_net_origins():
    scan = scan_sources({
        "main.js": """
            import * as actors from "longshot/actors";
            import net from "longshot/net";
            actors.despawn(actor);
            actors.remove(key);
            await net.connect("wss://ap.example.org:38281/room");
            await net.fetch("https://API.Example.org/v1");
        """,
    }, entry="main.js")
    assert scan["required"] == ["actors.remove", "actors.spawn", "net:wss://ap.example.org:38281", "net:https://api.example.org"]


def test_lighting_capture_scripts_match_its_manifest():
    root = capture_root()
    scan = scan_tree(root, "main.js")
    assert scan["required"] == ["actors.read", "storage", "state.read"]
    assert any(item["specifier"] == "longshot/gfx" for item in scan["dynamicImports"])
    text = (root / "mod.json").read_text(encoding="utf-8")
    manifest = json.loads(text)
    assert manifest["permissions"] == list(CAPTURE_PERMISSIONS)
    assert json.loads(capture_manifest_text())["permissions"] == list(CAPTURE_PERMISSIONS)
    probe = scan_tree(root, "probe.js")
    assert probe["required"] == ["actors.read", "storage", "state.read"]
    assert any("longshot/gfx" in item["message"] for item in probe["hints"])
    compared = compare_scan({"permissions": list(CAPTURE_PERMISSIONS), "optionalPermissions": [], "net": []}, scan)
    assert compared["missing"] == []
    assert "gfx.world" in compared["unused"]


def test_project_permissions_round_trip_into_mod_json(tmp_path):
    project = Project.create(tmp_path / "mod", name="kokiri-sign", title="Kokiri sign", author="Ada", version="0.1.0")
    view = save_project_permissions(project, {
        "permissions": ["state.read", "items.grant"],
        "optionalPermissions": ["storage"],
        "net": ["https://tracker.example.org"],
        "multiplayer": "all",
    })
    assert view["declaration"]["permissions"] == ["state.read", "items.grant"]
    assert view["declaration"]["net"] == ["https://tracker.example.org"]
    # A net entry implies net:<origin>. That permission is still missing until it is in a list.
    saved = project_declaration(project)
    assert "net:https://tracker.example.org" in " ".join(saved["errors"])
    save_project_permissions(project, {
        "permissions": ["state.read", "items.grant"],
        "optionalPermissions": ["storage", "net:https://tracker.example.org"],
        "net": ["https://tracker.example.org"],
        "multiplayer": "all",
    })
    saved = project_declaration(project)
    assert saved["errors"] == []
    body = _manifest_json(project)
    assert body["permissions"] == ["state.read", "items.grant"]
    assert body["optionalPermissions"] == ["storage", "net:https://tracker.example.org"]
    assert body["net"] == ["https://tracker.example.org"]
    assert body["multiplayer"] == "all"
    assert "description" not in body


def test_mods_json_grant_backs_up_and_refuses_a_random_folder(tmp_path):
    game = tmp_path / "game"
    (game / "mods").mkdir(parents=True)
    (game / "dev").mkdir()
    existing = game / "dev" / "mods.json"
    original = json.dumps({
        "keep": 1,
        "mods": [
            {"id": "local/other", "enabled": False, "version": "1.0.0"},
            {"id": "local/kokiri-sign", "enabled": True, "note": "keep me"},
        ],
        "cheats": {"inf": True},
    })
    existing.write_text(original, encoding="utf-8")
    result = grant_in_mods_json(game, "local/kokiri-sign", ["state.read", "storage"])
    assert Path(result["backup"]).read_text(encoding="utf-8") == original
    written = json.loads(existing.read_text(encoding="utf-8"))
    assert written["keep"] == 1
    assert written["cheats"] == {"inf": True}
    assert written["mods"][0]["id"] == "local/other"
    assert written["mods"][1]["granted"] == ["state.read", "storage"]
    assert written["mods"][1]["note"] == "keep me"
    assert written["mods"][1]["enabled"] is True

    fresh = tmp_path / "other-game"
    fresh.mkdir()
    (fresh / "longshot_options.ini").write_text("x=1\n", encoding="utf-8")
    created = grant_in_mods_json(fresh, "local/new-mod", ["actors.read"])
    assert created["created"] is True
    assert created["backup"] == ""
    assert json.loads(Path(created["path"]).read_text(encoding="utf-8"))["mods"] == [
        {"id": "local/new-mod", "enabled": True, "granted": ["actors.read"]},
    ]

    profile = tmp_path / "profile.json"
    profile.write_text('{"v": 1, "mods": [{"id": "local/new-mod", "on": false}]}', encoding="utf-8")
    granted = grant_in_mods_json(profile, "local/new-mod", ["audio"])
    entry = json.loads(profile.read_text(encoding="utf-8"))["mods"][0]
    assert entry["on"] is False
    assert entry["granted"] == ["audio"]
    assert "enabled" not in entry

    with pytest.raises(BancError):
        grant_in_mods_json(tmp_path / "notes", "local/new-mod", ["audio"])
    assert not (tmp_path / "notes").exists()

    broken = tmp_path / "broken"
    (broken / "mods").mkdir(parents=True)
    (broken / "mods.json").write_text('{"mods": [{"id": "a", "id": "b"}]}', encoding="utf-8")
    with pytest.raises(BancError):
        grant_in_mods_json(broken, "local/new-mod", ["audio"])
    assert (broken / "mods.json").read_text(encoding="utf-8").startswith('{"mods"')
    assert not (broken / "mods.json.bak").exists()

"""Longshot mod permissions.

Data table generated from upstream Longshot ``assets/modding/docs/permissions.md``
(that file is generated from the API schema) and the ``Permission`` type in
``assets/modding/packages/api/longshot.d.ts``. Upstream commit
``563ca4e55b1306dd93ad57377020c463dd524c7f`` (``563ca4e5``) on ``mod-api``.
Refresh this table when that file changes. Manifest rules are from
``assets/modding/docs/manifest.md`` at the same commit. ``mods.json`` grants
are the mod entry's ``"granted"`` array (spec 12.4); the list file is the one
described in ``docs/dev/reference/mods.md`` and ``docs/dev/reference/profiles.md``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from ocarina_forge.errors import BancError

SOURCE = {
    "file": "assets/modding/docs/permissions.md",
    "types": "assets/modding/packages/api/longshot.d.ts",
    "manifest": "assets/modding/docs/manifest.md",
    "commit": "563ca4e55b1306dd93ad57377020c463dd524c7f",
    "commitShort": "563ca4e5",
    "branch": "mod-api",
}

RESTART_NOTE = (
    "Changing permissions may need the mod turned off and on again, or the game restarted, "
    "before the new mod.json is read."
)

# id, what the player is told, risk, system-mod only, group, what in the API needs it.
_ROWS: tuple[tuple[str, str, str, bool, str, str], ...] = (
    ("state.read", "Can see what happens in your game", "low", False, "game",
     "longshot/game state, after, flags.get, flags.has, update.on, mapLoading.on, mapLoaded.on, saveWriting.on, flagChanged.on, paused.on; longshot/player state; longshot/camera state; longshot/world ray, destinations, states, state"),
    ("state.write", "Can change your game and what gets saved", "medium", False, "game",
     "longshot/game flags.set, textShown; longshot/player set, setPosition, setAge; longshot/rules remove and rewards, skips, actorLife, prices, heals, falls, rupees, bottles, damageTaken, damageDealt; longshot/world travel, setState"),
    ("camera.write", "Can move the camera, pause the game and hide its HUD", "low", False, "game",
     "longshot/game pause; longshot/camera override, follow, and CameraOverride methods"),
    ("storage", "Can keep its own data on this PC", "low", False, "storage",
     "longshot/storage save.slot, flags, saves, Store global, profile, and save, save.loaded"),
    ("save.flags", "Can change what your game saves and how", "high", False, "storage",
     "A content module's saved flags. Nothing in the script API calls this yet."),
    ("actors.read", "Can see the characters and enemies around you", "low", False, "actors",
     "longshot/actors list, info, alive, attached, and removed, spawned, died, damaged, lifted, thrown, dropped; longshot/behaviour"),
    ("actors.write", "Can change the characters, enemies and things around you: their health, where they are, which way they face, how they move, whether they are seen, what they hold on to and what their behaviour is told", "high", False, "actors",
     "longshot/actors damage, setLife, setPosition, setRotation, setVelocity, setVisible, light, attach, detach, answer"),
    ("actors.spawn", "Can make characters, enemies and things appear in your game, and take away the ones it made", "high", False, "actors",
     "longshot/actors spawn, remove"),
    ("actors.remove", "Can take away characters, enemies and things that are in your game", "high", False, "actors",
     "longshot/actors despawn"),
    ("items.read", "Can see the items you find and the places you take them from", "low", False, "items",
     "longshot/items held, checked.on, granted.on"),
    ("items.grant", "Can give you items, and keeps a list of what it gave", "medium", False, "items",
     "longshot/items grant, pending, remove"),
    ("input.read", "Can see the buttons you press on your controller", "low", False, "input",
     "longshot/input action, pad.state, pointer.capture"),
    ("input.write", "Can press buttons for you, and change what your controller does", "medium", False, "input",
     "longshot/input press, rumble, pad.intercept"),
    ("content.add", "Can add new items, characters and places to your game", "medium", False, "content",
     "longshot/content item, actor, model, modelOf, place, change, changeActor, text, flag, tree, messages, sound"),
    ("content.read", "Can read the names and data of the game's actors and texts", "low", False, "content",
     "longshot/content read.row, read.text, read.actorName, read.mapName, read.component, read.placements"),
    ("content.patch", "Can change the game's data in checked ways", "low", False, "content",
     "longshot/content patch"),
    ("audio", "Can play its own sounds", "low", False, "audio",
     "longshot/audio load, play, playAt, playOn, and Sound and Voice handles"),
    ("audio.game", "Can change how loud the game's own music, sounds and voices are", "low", False, "audio",
     "longshot/audio setGroupVolume, duck"),
    ("ui.hud", "Can draw on your screen over the game", "low", False, "ui",
     "longshot/ui Hud and World"),
    ("ui.window", "Can open its own windows", "low", False, "ui",
     "longshot/ui Window; longshot/capture thumbnail, load, saved, remove"),
    ("ui.settings", "Can show its own page in the Mods tab", "low", False, "ui",
     "longshot/ui SettingsPage and DebugPage"),
    ("gfx.world", "Can draw its own shapes and labels in the game's world", "low", False, "graphics",
     "longshot/gfx model, world.line, world.box, world.circle, world.billboard, world.model, and WorldShape methods"),
    ("gfx.shader", "Can run its own graphics effects on your GPU", "medium", False, "graphics",
     "longshot/gfx effect, and Effect.set, setEnabled"),
    ("gfx.textures", "Can change how the game looks with its own pictures", "low", False, "graphics",
     "A textures folder. No script call asks for this."),
    ("net:*", "Can talk to any server on the internet", "high", False, "network",
     "longshot/net, when the manifest's net list is *"),
    ("net.lan", "Can talk to devices on your home network", "high", False, "network",
     "longshot/net to addresses on the home network"),
    ("romfs.read", "Can read the game's own files on this PC (never with the network)", "high", False, "files",
     "longshot/romfs read, size"),
    ("romfs.provide", "Can make game files while the game runs", "high", False, "files",
     "Asked and granted like any other permission. Nothing in the script API calls this yet."),
    ("memory.read", "Can read all of the running game's memory. Full access to the running game: only for mods you trust", "unsafe", False, "memory",
     "longshot/memory read, regions"),
    ("memory.write", "Can change the running game's memory directly, which can run any code on your PC, as you, with full access to your files: only for mods whose source you trust", "unsafe", False, "memory",
     "longshot/memory write"),
    ("hooks.unsafe", "Hooks the game's own code where Longshot never named a place. Full access to the running game: only for mods you trust", "unsafe", False, "hooks",
     "longshot/hooks listen, sites, setValue"),
    ("session", "Can send messages to the players in your room", "medium", False, "session",
     "Asked and granted like any other permission. Nothing in the script API calls this yet."),
    ("discord.status", "Can change your Discord status, which your Discord friends see", "medium", False, "discord",
     "Longshot's Discord status. No script call asks for this."),
    ("exefs", "Changes the game's own code. Full access to the running game: only for mods you trust", "unsafe", False, "code",
     "The mod's own exefs patches. No script call asks for this."),
    ("mods.inspect", "Can see how your other mods run: their time, memory, faults and log", "medium", True, "system",
     "longshot/mods inspect, logs"),
    ("mods.control", "Can restart your other mods and switch them on and off", "high", True, "system",
     "longshot/mods restart, setEnabled, reload"),
    ("ipc", "Can talk to other programs on this PC through the local sockets or pipes it names", "unsafe", True, "system",
     "longshot/ipc"),
)

_GROUPS = (
    ("game", "Game"),
    ("storage", "Storage"),
    ("actors", "Actors"),
    ("items", "Items"),
    ("input", "Input"),
    ("content", "Content"),
    ("audio", "Audio"),
    ("ui", "Interface"),
    ("graphics", "Graphics"),
    ("network", "Network"),
    ("files", "Game files"),
    ("memory", "Memory"),
    ("hooks", "Hooks"),
    ("session", "Session"),
    ("discord", "Discord"),
    ("code", "Game code"),
    ("system", "System mods"),
)

# Exact calls. None needs no permission. "net" is longshot/net connect or fetch.
_CALLS: dict[tuple[str, str], str | None] = {}


def _call(module: str, path: str, permission: str | None) -> None:
    _CALLS[(module, path)] = permission


for _path, _permission in (
    ("state", "state.read"), ("after", "state.read"), ("flags.get", "state.read"), ("flags.has", "state.read"),
    ("flags.set", "state.write"), ("textShown", "state.write"), ("pause", "camera.write"),
    ("update.on", "state.read"), ("mapLoading.on", "state.read"), ("mapLoaded.on", "state.read"),
    ("saveWriting.on", "state.read"), ("flagChanged.on", "state.read"), ("paused.on", "state.read"),
    ("frame.on", None),
):
    _call("longshot/game", _path, _permission)
for _path, _permission in (("state", "state.read"), ("set", "state.write"), ("setPosition", "state.write"), ("setAge", "state.write")):
    _call("longshot/player", _path, _permission)
for _path, _permission in (("state", "state.read"), ("override", "camera.write"), ("follow", "camera.write")):
    _call("longshot/camera", _path, _permission)
for _path, _permission in (
    ("ray", "state.read"), ("destinations", "state.read"), ("states", "state.read"), ("state", "state.read"),
    ("travel", "state.write"), ("setState", "state.write"),
):
    _call("longshot/world", _path, _permission)
for _path in (
    "remove", "rewards.add", "skips.add", "actorLife.add", "prices.add", "heals.add", "falls.add",
    "rupees.add", "bottles.add", "damageTaken.add", "damageDealt.add",
):
    _call("longshot/rules", _path, "state.write")
for _path, _permission in (
    ("list", "actors.read"), ("info", "actors.read"), ("alive", "actors.read"), ("attached", "actors.read"),
    ("removed.on", "actors.read"), ("spawned.on", "actors.read"), ("died.on", "actors.read"),
    ("damaged.on", "actors.read"), ("lifted.on", "actors.read"), ("thrown.on", "actors.read"),
    ("dropped.on", "actors.read"),
    ("damage", "actors.write"), ("setLife", "actors.write"), ("setPosition", "actors.write"),
    ("setRotation", "actors.write"), ("setVelocity", "actors.write"), ("setVisible", "actors.write"),
    ("light", "actors.write"), ("attach", "actors.write"), ("detach", "actors.write"), ("answer", "actors.write"),
    ("spawn", "actors.spawn"), ("remove", "actors.spawn"), ("despawn", "actors.remove"),
):
    _call("longshot/actors", _path, _permission)
for _path, _permission in (
    ("held", "items.read"), ("checked.on", "items.read"), ("granted.on", "items.read"),
    ("grant", "items.grant"), ("pending", "items.grant"), ("remove", "items.grant"),
):
    _call("longshot/items", _path, _permission)
for _path, _permission in (
    ("action", "input.read"), ("pad.state", "input.read"), ("pointer.capture", "input.read"),
    ("press", "input.write"), ("rumble", "input.write"), ("pad.intercept", "input.write"),
):
    _call("longshot/input", _path, _permission)
for _path in (
    "item", "actor", "model", "modelOf", "place", "change", "changeActor", "text", "flag", "tree", "messages", "sound",
):
    _call("longshot/content", _path, "content.add")
for _path in ("read.row", "read.text", "read.actorName", "read.mapName", "read.component", "read.placements"):
    _call("longshot/content", _path, "content.read")
_call("longshot/content", "patch", "content.patch")
for _path in ("load", "play", "playAt", "playOn"):
    _call("longshot/audio", _path, "audio")
for _path in ("setGroupVolume", "duck"):
    _call("longshot/audio", _path, "audio.game")
for _path, _permission in (
    ("Hud", "ui.hud"), ("World", "ui.hud"), ("Window", "ui.window"),
    ("SettingsPage", "ui.settings"), ("DebugPage", "ui.settings"),
):
    _call("longshot/ui", _path, _permission)
for _path in ("thumbnail", "load", "saved", "remove"):
    _call("longshot/capture", _path, "ui.window")
for _path in ("model", "world.line", "world.box", "world.circle", "world.billboard", "world.model"):
    _call("longshot/gfx", _path, "gfx.world")
_call("longshot/gfx", "effect", "gfx.shader")
_call("longshot/net", "connect", "net")
_call("longshot/net", "fetch", "net")
for _path in ("read", "size"):
    _call("longshot/romfs", _path, "romfs.read")
for _path in ("read", "regions"):
    _call("longshot/memory", _path, "memory.read")
_call("longshot/memory", "write", "memory.write")
for _path in ("listen", "sites", "setValue"):
    _call("longshot/hooks", _path, "hooks.unsafe")
for _path in ("inspect", "logs"):
    _call("longshot/mods", _path, "mods.inspect")
for _path in ("restart", "setEnabled", "reload"):
    _call("longshot/mods", _path, "mods.control")
for _path in (
    "running", "caller", "callerHas", "checkPermission", "load", "guard", "event", "emit", "registry",
    "command", "claimant", "claims",
):
    _call("longshot/mods", _path, None)
_call("longshot", "has", None)

# Any call on the module. None means the module's calls need no permission.
_ANY_CALL: dict[str, str | None] = {
    "longshot/storage": "storage",
    "longshot/behaviour": "actors.read",
    "longshot/ipc": "ipc",
    "longshot/log": None,
    "longshot/settings": None,
    "longshot/permissions": None,
    "longshot/dev": None,
    "longshot/time": None,
    "longshot/files": None,
    "longshot/lifecycle": None,
    "longshot/ui": None,
    "longshot/mods": None,
    "longshot": None,
}

_BY_ID = {row[0]: row for row in _ROWS}
FIXED_IDS = frozenset(_BY_ID)
SYSTEM_ONLY = frozenset(row[0] for row in _ROWS if row[3])
UNSAFE = frozenset(row[0] for row in _ROWS if row[2] == "unsafe")
RAW_DATA = frozenset({"romfs.read", "memory.read"})
OFF_PC = frozenset({"net:*", "net.lan", "session", "ipc", "discord.status"})
ROOM_SHARED = frozenset({"state.write", "items.grant", "content.add", "actors.spawn", "actors.remove"})
_HOST = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*|(?:\d{1,3}\.){3}\d{1,3}"
_ORIGIN = re.compile(rf"^(wss|https)://({_HOST})(?::(\d{{1,5}}))?$")
_SCRIPT = {".js", ".mjs", ".ts", ".tsx", ".mts"}
_ALT = {".js": (".js", ".ts", ".tsx"), ".mjs": (".mjs", ".mts"), ".ts": (".ts",), ".tsx": (".tsx",), ".mts": (".mts",)}


def catalog() -> dict:
    """The permission table the editor shows, grouped by module."""
    grouped: dict[str, list[dict]] = {key: [] for key, _label in _GROUPS}
    for ident, player, risk, system_only, group, features in _ROWS:
        grouped[group].append({
            "id": ident,
            "player": player,
            "risk": risk,
            "systemOnly": system_only,
            "features": features,
        })
    return {
        "source": SOURCE,
        "restartNote": RESTART_NOTE,
        "net": {
            "id": "net:<origin>",
            "player": "Can talk to <origin>",
            "risk": "medium",
            "features": "longshot/net connect and fetch. Each net entry is this permission.",
        },
        "groups": [{"id": key, "label": label, "permissions": grouped[key]} for key, label in _GROUPS if grouped[key]],
        "noPermission": [
            "longshot has",
            "longshot/log",
            "longshot/settings get, set, changed",
            "longshot/permissions has, request, changed",
            "longshot/dev",
            "longshot/time",
            "longshot/files",
            "longshot/game frame.on",
            "longshot/lifecycle stopping",
            "every handle's release",
        ],
    }


def permission_row(ident: str) -> dict | None:
    row = _BY_ID.get(ident)
    if row is None:
        return None
    ident, player, risk, system_only, group, features = row
    return {
        "id": ident,
        "player": player,
        "risk": risk,
        "systemOnly": system_only,
        "group": group,
        "features": features,
    }


def is_known_permission(ident: str) -> bool:
    if ident in FIXED_IDS:
        return True
    if ident.startswith("net:") and ident not in {"net:*", "net:"}:
        return origin_text(ident[4:]) is not None
    return False


def origin_text(value: str) -> str | None:
    """A ``net`` entry (``wss://`` or ``https://`` host, optional port, or ``*``)."""
    text = (value or "").strip()
    if text == "*":
        return "*"
    match = _ORIGIN.fullmatch(text)
    if not match:
        return None
    port = match.group(3)
    if port is not None:
        number = int(port)
        if number < 1 or number > 65535:
            return None
    return text


def permission_for_origin(origin: str) -> str:
    if origin == "*":
        return "net:*"
    return f"net:{origin}"


def _text_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _strings(value, field: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"{field} has to be a list of strings.")
        return []
    return [item.strip() for item in value]


def validate_declaration(declaration: dict, *, content: str = "", runtime: str = "", ipc: list | None = None) -> dict:
    """Manifest rules from ``manifest.md``. ``errors`` are what the game would refuse."""
    errors: list[str] = []
    warnings: list[str] = []
    required = _strings(declaration.get("permissions"), "permissions", errors)
    optional = _strings(declaration.get("optionalPermissions"), "optionalPermissions", errors)
    net = _strings(declaration.get("net"), "net", errors)
    multiplayer = str(declaration.get("multiplayer") or "local").strip() or "local"
    system = bool(declaration.get("system"))
    if multiplayer not in {"local", "all", "host"}:
        errors.append('multiplayer has to be "local", "all", or "host".')

    def _check_names(names: list[str], field: str) -> list[str]:
        seen: set[str] = set()
        kept: list[str] = []
        for name in names:
            if not name:
                errors.append(f'{field} has an empty name.')
                continue
            if name in seen:
                errors.append(f'{field} lists "{name}" twice.')
                continue
            seen.add(name)
            if not is_known_permission(name):
                errors.append(f'{field}: "{name}" is not a permission.')
                continue
            if name in SYSTEM_ONLY and not system:
                errors.append(f'{name} is only for a Longshot system mod. A player mod that names it is refused.')
            kept.append(name)
        return kept

    required = _check_names(required, "permissions")
    optional = _check_names(optional, "optionalPermissions")
    overlap = [name for name in optional if name in set(required)]
    for name in overlap:
        errors.append(f'optionalPermissions also lists "{name}", which is already in permissions.')

    origins: list[str] = []
    for entry in net:
        origin = origin_text(entry)
        if origin is None:
            errors.append(
                f'net: "{entry}" is not "*" or a lower-case wss:// or https:// host with an optional port and no path.'
            )
            continue
        if origin in origins:
            errors.append(f'net lists "{entry}" twice.')
            continue
        origins.append(origin)
        implied = permission_for_origin(origin)
        if implied not in required and implied not in optional:
            errors.append(
                f'net "{origin}" needs {implied} in permissions, or in optionalPermissions when the mod can start without it.'
            )

    declared_origins = []
    for name in [*required, *optional]:
        if name == "net:*":
            declared_origins.append("*")
        elif name.startswith("net:") and name != "net:":
            origin = origin_text(name[4:])
            if origin and origin != "*":
                declared_origins.append(origin)
    unique_origins = list(dict.fromkeys([*origins, *declared_origins]))
    if len(unique_origins) > 64:
        errors.append("A mod can name at most 64 network origins, counting net and the net: permissions together.")
    for origin in origins:
        if origin != "*" and origin.endswith(":443"):
            bare = origin[:-4]
            if bare in unique_origins:
                warnings.append(f"{origin} is the same origin as {bare}. The game grants each string as it is written.")

    asked = set(required) | set(optional)
    if not system and ipc:
        errors.append("ipc endpoints are only for a Longshot system mod. A player mod that names them is refused.")
    blocked = sorted(asked & ROOM_SHARED)
    if multiplayer == "local" and blocked:
        errors.append(
            "multiplayer is local, and the mod asks for "
            + ", ".join(blocked)
            + '. A mod that changes the shared game cannot be one player\'s own choice. Say "all" or "host".'
        )
    raw = sorted(asked & RAW_DATA)
    off = sorted(name for name in asked if name in OFF_PC or name.startswith("net:"))
    if net and not off:
        off = ["net"]
    if raw and (off or net):
        via = ", ".join(off) if off else "net"
        errors.append(
            f"{', '.join(raw)} cannot be held together with a way off this PC ({via})."
        )
    content_name = (content or "").strip()
    if content_name:
        if not (runtime or "").strip():
            errors.append("content is only read when the manifest also sets runtime.")
        if "content.add" not in required and "content.patch" not in required:
            errors.append(
                "content needs content.add in permissions, or content.patch when that module is typed patches only."
            )
    return {
        "permissions": required,
        "optionalPermissions": optional,
        "net": origins,
        "multiplayer": multiplayer,
        "system": system,
        "errors": errors,
        "warnings": warnings,
    }


def assert_player_permissions(names: list[str]) -> list[str]:
    """Raise when a lighting-capture style manifest names something the table does not."""
    result = validate_declaration({"permissions": list(names), "multiplayer": "local"})
    if result["errors"]:
        raise BancError(" ".join(result["errors"]), "permissions")
    return result["permissions"]


def _lex(source: str) -> list[tuple[str, str, int]]:
    tokens: list[tuple[str, str, int]] = []
    i = 0
    n = len(source)
    line = 1
    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                if source[i] == "\n":
                    line += 1
                i += 1
            i = min(n, i + 2)
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            start = line
            buf: list[str] = []
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    if i + 1 < n:
                        buf.append(source[i + 1])
                    if i + 1 < n and source[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if quote == "`" and source[i] == "$" and i + 1 < n and source[i + 1] == "{":
                    buf.append("${")
                    i += 2
                    depth = 1
                    while i < n and depth:
                        if source[i] == "\n":
                            line += 1
                        if source[i] == "{":
                            depth += 1
                        elif source[i] == "}":
                            depth -= 1
                        i += 1
                    buf.append("}")
                    continue
                if source[i] == "\n":
                    line += 1
                buf.append(source[i])
                i += 1
            i = min(n, i + 1)
            tokens.append(("string", "".join(buf), start))
            continue
        if ch.isalpha() or ch in "_$":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            tokens.append(("ident", source[i:j], line))
            i = j
            continue
        if ch in "(){}[].,;<>*=":
            tokens.append((ch, ch, line))
            i += 1
            continue
        i += 1
    return tokens


def _clause_bindings(tokens: list[tuple[str, str, int]]) -> list[tuple[str, str, str]]:
    bindings: list[tuple[str, str, str]] = []
    i = 0
    while i < len(tokens):
        kind, value, _line = tokens[i]
        if kind == "ident" and value == "type" and i + 1 < len(tokens) and tokens[i + 1][0] == "ident":
            i += 2
            continue
        if kind == "*" and i + 2 < len(tokens) and tokens[i + 1][1] == "as" and tokens[i + 2][0] == "ident":
            bindings.append((tokens[i + 2][1], "", "namespace"))
            i += 3
            continue
        if kind == "{":
            i += 1
            while i < len(tokens) and tokens[i][0] != "}":
                if tokens[i][1] == "type":
                    i += 1
                    continue
                if tokens[i][0] == "ident":
                    imported = tokens[i][1]
                    local = imported
                    i += 1
                    if i + 1 < len(tokens) and tokens[i][1] == "as" and tokens[i + 1][0] == "ident":
                        local = tokens[i + 1][1]
                        i += 2
                    if imported not in {"type", "as", "from"}:
                        bindings.append((local, imported, "named"))
                    continue
                i += 1
            if i < len(tokens) and tokens[i][0] == "}":
                i += 1
            continue
        if kind == "ident" and value not in {"as", "from", "type"}:
            bindings.append((value, "", "default"))
        i += 1
    return bindings


def _scan_tokens(filename: str, tokens: list[tuple[str, str, int]]) -> dict:
    bindings: dict[str, tuple[str, str, str]] = {}
    imports: list[dict] = []
    relatives: list[str] = []
    calls: list[dict] = []
    dynamic: list[dict] = []
    i = 0
    while i < len(tokens):
        kind, value, line = tokens[i]
        previous = tokens[i - 1][0] if i else ""
        if kind == "ident" and value == "import" and previous != ".":
            if i + 1 < len(tokens) and tokens[i + 1][0] == "(":
                specifier = ""
                if i + 2 < len(tokens) and tokens[i + 2][0] == "string":
                    specifier = tokens[i + 2][1]
                dynamic.append({"file": filename, "line": line, "specifier": specifier})
                i += 2
                continue
            j = i + 1
            clause: list[tuple[str, str, int]] = []
            specifier = ""
            while j < len(tokens):
                if tokens[j][0] == "ident" and tokens[j][1] == "from" and j + 1 < len(tokens) and tokens[j + 1][0] == "string":
                    specifier = tokens[j + 1][1]
                    j += 2
                    break
                if tokens[j][0] == "string" and not clause:
                    specifier = tokens[j][1]
                    j += 1
                    break
                if tokens[j][0] == ";":
                    break
                clause.append(tokens[j])
                j += 1
            if specifier.startswith("longshot/") or specifier == "longshot":
                for local, imported, style in _clause_bindings(clause):
                    bindings[local] = (specifier, style, imported)
                imports.append({"file": filename, "line": line, "module": specifier})
            elif specifier.startswith("."):
                relatives.append(specifier)
            i = j
            continue
        if kind == "ident" and value == "export" and previous != ".":
            j = i + 1
            specifier = ""
            window = tokens[i + 1:i + 48]
            blocked = {item[1] for item in window if item[0] == "ident"} & {"function", "class", "const", "let", "var", "async"}
            if not blocked:
                while j < len(tokens) and j < i + 48 and tokens[j][0] != ";":
                    if tokens[j][1] == "from" and j + 1 < len(tokens) and tokens[j + 1][0] == "string":
                        specifier = tokens[j + 1][1]
                        j += 2
                        break
                    j += 1
            if specifier.startswith("longshot/") or specifier == "longshot":
                imports.append({"file": filename, "line": line, "module": specifier})
                i = j
                continue
            if specifier.startswith("."):
                relatives.append(specifier)
                i = j
                continue
        if kind == "<" and i + 1 < len(tokens) and tokens[i + 1][0] == "ident" and previous in {"(", "{", "}", "=", ",", "ident"}:
            name = tokens[i + 1][1]
            bound = bindings.get(name)
            if bound and previous == "ident" and tokens[i - 1][1] not in {"return", "throw", "yield"}:
                bound = None
            if bound:
                module, style, imported = bound
                path = imported if style == "named" else name
                calls.append({"file": filename, "line": line, "module": module, "path": path, "argument": ""})
            i += 2
            continue
        if kind == "ident" and value in bindings and previous != ".":
            module, style, imported = bindings[value]
            j = i + 1
            parts: list[str] = [imported] if style == "named" else []
            while j + 1 < len(tokens) and tokens[j][0] == "." and tokens[j + 1][0] == "ident":
                parts.append(tokens[j + 1][1])
                j += 2
            if j < len(tokens) and tokens[j][0] == "(":
                argument = tokens[j + 1][1] if j + 1 < len(tokens) and tokens[j + 1][0] == "string" else ""
                calls.append({
                    "file": filename,
                    "line": line,
                    "module": module,
                    "path": ".".join(part for part in parts if part),
                    "argument": argument,
                })
            i = j
            continue
        i += 1
    return {
        "imports": imports,
        "relatives": relatives,
        "calls": calls,
        "dynamicImports": dynamic,
    }


def _resolve(root: Path, importer: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    target = (importer.parent / specifier)
    try:
        target.relative_to(root)
    except ValueError:
        return None
    suffix = target.suffix
    names = _ALT.get(suffix, (suffix,)) if suffix else ()
    for alt in names:
        candidate = target.with_suffix(alt)
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        return candidate
    return None


def scan_tree(root: Path, entry: str = "", content: str = "") -> dict:
    """Follow ``entry`` and ``content`` and their static relative imports."""
    root = Path(root)
    seeds = [name for name in (entry, content) if isinstance(name, str) and name.strip()]
    files: list[Path] = []
    seen: set[Path] = set()
    queue: list[Path] = []
    for name in seeds:
        path = (root / name).resolve() if not Path(name).is_absolute() else Path(name)
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        queue.append(path)
    scanned: list[dict] = []
    missing_files: list[str] = []
    while queue and len(seen) < 64:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        if path.is_symlink() or not path.is_file() or path.suffix not in _SCRIPT:
            if path not in files:
                missing_files.append(path.name)
            continue
        if path.stat().st_size > 1_000_000:
            missing_files.append(f"{path.name} is over 1 MB and was not scanned")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            missing_files.append(f"{path.name} could not be read ({exc})")
            continue
        relative = path.relative_to(root).as_posix()
        found = _scan_tokens(relative, _lex(text))
        scanned.append(found)
        for specifier in found["relatives"]:
            nxt = _resolve(root, path, specifier)
            if nxt is not None and nxt not in seen:
                queue.append(nxt)
    return _summarize(scanned, missing_files, bool(seeds))


def scan_sources(sources: dict[str, str], *, entry: str, content: str = "") -> dict:
    """Scan an in-memory mod. ``sources`` maps a relative path to its text."""
    seeds = [name for name in (entry, content) if name]
    seen: set[str] = set()
    queue = list(seeds)
    scanned: list[dict] = []
    missing: list[str] = []
    while queue and len(seen) < 64:
        name = queue.pop(0).replace("\\", "/")
        if name in seen:
            continue
        seen.add(name)
        text = sources.get(name)
        if text is None:
            missing.append(name)
            continue
        found = _scan_tokens(name, _lex(text))
        scanned.append(found)
        importer = Path(name)
        for specifier in found["relatives"]:
            if not specifier.startswith("."):
                continue
            target = Path(os.path.normpath(str(importer.parent / specifier)))
            if str(target).startswith(".."):
                continue
            suffix = target.suffix
            for alt in _ALT.get(suffix, (suffix,)):
                candidate = target.with_suffix(alt).as_posix()
                if candidate in sources:
                    queue.append(candidate)
                    break
    return _summarize(scanned, missing, bool(seeds))


def _origin_in_argument(argument: str) -> str | None:
    match = re.match(r"^(wss|https)://([^/?#\s]+)", argument.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    host = match.group(2).lower()
    return origin_text(f"{match.group(1).lower()}://{host}")


def _summarize(scanned: list[dict], missing_files: list[str], had_entry: bool) -> dict:
    imports: list[dict] = []
    calls: list[dict] = []
    dynamic: list[dict] = []
    for found in scanned:
        imports.extend(found["imports"])
        calls.extend(found["calls"])
        dynamic.extend(found["dynamicImports"])
    required: list[str] = []
    hints: list[dict] = []
    used_modules: set[str] = set()
    for call in calls:
        module = call["module"]
        path = call["path"]
        used_modules.add(module)
        if (module, path) in _CALLS:
            permission = _CALLS[(module, path)]
        elif module in _ANY_CALL:
            permission = _ANY_CALL[module]
        elif module == "longshot" or module.startswith("longshot/"):
            hints.append({
                "file": call["file"],
                "line": call["line"],
                "module": module,
                "path": path,
                "message": f"{module} {path}() is not in the permission table. Check it before shipping the mod.",
            })
            continue
        else:
            continue
        if permission is None:
            continue
        if permission == "net":
            origin = _origin_in_argument(call["argument"])
            if origin:
                name = permission_for_origin(origin)
                if name not in required:
                    required.append(name)
            else:
                hints.append({
                    "file": call["file"],
                    "line": call["line"],
                    "module": module,
                    "path": path,
                    "message": "longshot/net needs a net:<origin> permission for each server, or net:*. Add the origin in the net field.",
                })
            continue
        if permission not in required:
            required.append(permission)
    called = {(call["module"], call["path"]) for call in calls}
    for item in imports:
        if item["module"] in used_modules:
            continue
        if item["module"] in _ANY_CALL and _ANY_CALL[item["module"]]:
            hints.append({
                "file": item["file"],
                "line": item["line"],
                "module": item["module"],
                "path": "",
                "message": (
                    f"{item['module']} is imported and no call was seen. "
                    f"Its calls need {_ANY_CALL[item['module']]}."
                ),
            })
            continue
        permissions = sorted({value for (module, _path), value in _CALLS.items() if module == item["module"] and value and value != "net"})
        if permissions:
            hints.append({
                "file": item["file"],
                "line": item["line"],
                "module": item["module"],
                "path": "",
                "message": (
                    f"{item['module']} is imported and no mapped call was seen. "
                    f"Calls in that module need {', '.join(permissions)}."
                ),
            })
    return {
        "scanned": had_entry,
        "required": required,
        "imports": imports,
        "calls": calls,
        "dynamicImports": dynamic,
        "hints": hints,
        "missingFiles": missing_files,
        "called": sorted(f"{module} {path}" for module, path in called if path),
    }


def compare_scan(declaration: dict, scan: dict) -> dict:
    asked = set(declaration.get("permissions") or []) | set(declaration.get("optionalPermissions") or [])
    net = set(declaration.get("net") or [])
    for origin in net:
        asked.add(permission_for_origin(origin))
    missing = [name for name in scan.get("required") or [] if name not in asked]
    used = set(scan.get("required") or [])
    unused = [name for name in list(declaration.get("permissions") or []) + list(declaration.get("optionalPermissions") or []) if name not in used and not name.startswith("net:")]
    # A net permission is used when the scan asked for that origin, or when a net call had no literal.
    net_hint = any("longshot/net" in item.get("message", "") for item in scan.get("hints") or [])
    if net_hint:
        unused = [name for name in unused if name not in {"net:*", "net.lan"}]
    return {"missing": missing, "unused": unused}


def project_declaration(project) -> dict:
    """Effective lists: the project manifest wins, then an imported mod.json."""
    manifest = project.manifest
    original: dict = {}
    source = project.root / ".longshot-source" / "mod.json"
    if source.is_file() and not source.is_symlink():
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            original = loaded

    def pick(key, default):
        if key in manifest:
            return manifest.get(key)
        if key in original:
            return original.get(key)
        return default

    content = pick("content", "") or ""
    runtime = pick("runtime", "") or ""
    entry = pick("entry", "") or ""
    declaration = {
        "permissions": pick("permissions", []) or [],
        "optionalPermissions": pick("optionalPermissions", []) or [],
        "net": pick("net", []) or [],
        "multiplayer": pick("multiplayer", "local") or "local",
        "system": bool(manifest.get("system")),
    }
    checked = validate_declaration(declaration, content=str(content), runtime=str(runtime), ipc=original.get("ipc") if isinstance(original.get("ipc"), list) else None)
    root = source.parent if source.is_file() else project.root
    if not (root / "mod.json").is_file() and not str(entry):
        scan = _summarize([], [], False)
    else:
        scan = scan_tree(root, str(entry), str(content) if content else "")
    compared = compare_scan(
        {
            "permissions": _text_list(declaration.get("permissions")),
            "optionalPermissions": _text_list(declaration.get("optionalPermissions")),
            "net": _text_list(declaration.get("net")),
        },
        scan,
    )
    return {
        "modId": f"local/{manifest.get('name', '')}",
        "declaration": {
            "permissions": _text_list(declaration.get("permissions")),
            "optionalPermissions": _text_list(declaration.get("optionalPermissions")),
            "net": _text_list(declaration.get("net")),
            "multiplayer": str(declaration.get("multiplayer") or "local"),
            "system": bool(declaration.get("system")),
        },
        "stored": {key: key in manifest for key in ("permissions", "optionalPermissions", "net", "multiplayer")},
        "entry": str(entry or ""),
        "content": str(content or ""),
        "runtime": str(runtime or ""),
        "errors": checked["errors"],
        "warnings": checked["warnings"],
        "scan": {
            "scanned": scan["scanned"],
            "required": scan["required"],
            "missing": compared["missing"],
            "unused": compared["unused"],
            "dynamicImports": scan["dynamicImports"],
            "hints": scan["hints"],
            "missingFiles": scan["missingFiles"],
        },
        "restartNote": RESTART_NOTE,
    }


def save_project_permissions(project, body: dict) -> dict:
    raw = project.root / "manifest.yaml"
    data = {}
    if raw.is_file():
        from ocarina_forge.project import load_yaml

        loaded = load_yaml(raw.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    incoming = {
        "permissions": _text_list(body.get("permissions")),
        "optionalPermissions": _text_list(body.get("optionalPermissions")),
        "net": _text_list(body.get("net")),
        "multiplayer": body.get("multiplayer") or "local",
        "system": bool(body.get("system")),
    }
    multiplayer = str(incoming["multiplayer"] or "local").strip() or "local"
    data["permissions"] = incoming["permissions"]
    data["optionalPermissions"] = incoming["optionalPermissions"]
    data["net"] = incoming["net"]
    if multiplayer != "local":
        data["multiplayer"] = multiplayer
    else:
        data.pop("multiplayer", None)
    if incoming["system"]:
        data["system"] = True
    else:
        data.pop("system", None)
    project.save_manifest_data(data)
    return project_declaration(project)


def overlay_manifest(body: dict, project) -> dict:
    """Project permission fields replace the imported manifest's when they were saved."""
    manifest = project.manifest
    if "permissions" in manifest:
        values = manifest.get("permissions") or []
        if values:
            body["permissions"] = list(values)
        else:
            body.pop("permissions", None)
    if "optionalPermissions" in manifest:
        values = manifest.get("optionalPermissions") or []
        if values:
            body["optionalPermissions"] = list(values)
        else:
            body.pop("optionalPermissions", None)
    if "net" in manifest:
        values = manifest.get("net") or []
        if values:
            body["net"] = list(values)
        else:
            body.pop("net", None)
    if "multiplayer" in manifest:
        value = str(manifest.get("multiplayer") or "")
        if value and value != "local":
            body["multiplayer"] = value
        else:
            body.pop("multiplayer", None)
    return body


def permission_warnings(body: dict) -> list[str]:
    system = str(body.get("id") or "").startswith("system/")
    return validate_declaration(
        {
            "permissions": body.get("permissions") or [],
            "optionalPermissions": body.get("optionalPermissions") or [],
            "net": body.get("net") or [],
            "multiplayer": body.get("multiplayer") or "local",
            "system": system,
        },
        content=str(body.get("content") or ""),
        runtime=str(body.get("runtime") or ""),
        ipc=body.get("ipc") if isinstance(body.get("ipc"), list) else None,
    )["errors"]


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        duplicated = sorted({key for key in keys if keys.count(key) > 1})
        raise ValueError("duplicate key: " + ", ".join(duplicated))
    return dict(pairs)


def resolve_mods_list(folder: Path) -> tuple[Path, bool]:
    """The game's mod list inside ``folder``. The second value is true when it will be created.

    An existing ``dev/mods.json`` is the dev profile ``longshot mod allow`` writes. Otherwise
    ``mods.json`` beside ``longshot_options.ini``, then ``user/mods.json``. A ``profile.json``
    the user pointed at is that profile's list. Nothing outside ``folder`` is considered.
    """
    folder = Path(folder).expanduser()
    if folder.is_symlink():
        raise BancError("The game path is a link. Point at the folder itself.", "permissions")
    if folder.is_file():
        if folder.name not in {"mods.json", "profile.json"}:
            raise BancError("Point at a game folder, or at its mods.json.", "permissions")
        return folder.resolve(), False
    if not folder.is_dir():
        raise BancError("That game folder was not found.", "permissions")
    folder = folder.resolve()
    existing = [
        folder / "dev" / "mods.json",
        folder / "mods.json",
        folder / "user" / "mods.json",
        folder / "profile.json",
    ]
    for path in existing:
        if path.is_symlink():
            raise BancError(f"{path.name} is a link. Forge will not follow it.", "permissions")
        if path.is_file():
            return path, False
    looks_like_game = (folder / "mods").is_dir() or (folder / "longshot_options.ini").is_file() or (folder / "dev").is_dir()
    if not looks_like_game:
        raise BancError(
            "That folder has no mods.json. Point at a game folder (one with mods/, dev/, or longshot_options.ini) or at the mods.json file.",
            "permissions",
        )
    if (folder / "dev").is_dir():
        return folder / "dev" / "mods.json", True
    return folder / "mods.json", True


def grant_in_mods_json(folder: Path, mod_id: str, permissions: list[str]) -> dict:
    """Pre-grant ``permissions`` on ``mod_id`` in the game folder's mod list.

    The previous file is copied to ``<name>.bak`` first. A folder that is not a game
    folder is left untouched.
    """
    if not isinstance(mod_id, str) or "/" not in mod_id:
        raise BancError("The mod id is missing.", "permissions")
    checked = validate_declaration({"permissions": list(permissions), "multiplayer": "all" if set(permissions) & ROOM_SHARED else "local"})
    if checked["errors"]:
        raise BancError(" ".join(checked["errors"]), "permissions")
    granted = checked["permissions"]
    path, creating = resolve_mods_list(folder)
    backup = ""
    if creating:
        document = {"mods": []}
    else:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BancError(f"Could not read {path}: {exc}", "permissions") from exc
        if path.stat().st_size >= 1 << 20:
            raise BancError(f"{path} is 1 MB or larger, so it was left as it is.", "permissions")
        try:
            document = json.loads(text, object_pairs_hook=_pairs)
        except (json.JSONDecodeError, ValueError) as exc:
            raise BancError(f"{path.name} is not a mod list Forge can update ({exc}). It was not changed.", "permissions") from exc
        if not isinstance(document, dict):
            raise BancError(f"{path.name} is not a JSON object. It was not changed.", "permissions")
    mods = document.get("mods")
    if mods is None:
        mods = []
        document["mods"] = mods
    if not isinstance(mods, list) or any(not isinstance(item, dict) for item in mods):
        raise BancError(f'{path.name} has a "mods" value that is not a list of objects. It was not changed.', "permissions")
    if not creating:
        backup_path = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup_path)
        backup = str(backup_path)
    switch = "on" if path.name == "profile.json" else "enabled"
    target = next((item for item in mods if item.get("id") == mod_id), None)
    if target is None:
        target = {"id": mod_id, switch: True}
        mods.append(target)
    target["granted"] = granted
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    warnings = []
    unsafe = [name for name in granted if name in UNSAFE or (permission_row(name) or {}).get("risk") == "unsafe"]
    if unsafe:
        warnings.append("This writes unsafe permissions the game would normally ask for itself: " + ", ".join(unsafe) + ".")
    return {
        "path": str(path),
        "backup": backup,
        "created": creating,
        "modId": mod_id,
        "granted": granted,
        "warnings": warnings,
        "restartNote": RESTART_NOTE,
    }

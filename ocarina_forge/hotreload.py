"""Copy a built mod into a Longshot folder when the project is saved.

This is off until a mod folder is chosen. A burst of edits waits, then one
build is copied. Each file is replaced with os.replace, so a reader never
sees a half-written banc. Files this tool wrote last time, and that the new
build no longer contains, are removed. Anything else in the folder stays.

Longshot reads placement when the map loads. Leaving the map and entering it
again is what shows the change. A folder whose name starts with "_" or "-"
is ignored by Longshot, and this refuses to write one.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.export import build
from ocarina_forge.project import atomic_write, check_mod_name

MANIFEST = ".ocarina-forge-hot.json"


def destination_for(path: str, *, dump_root: Path | None = None, project_root: Path | None = None) -> Path:
    text = (path or "").strip()
    if not text:
        raise BancError("Choose the Longshot mod folder to write into.", "hot_reload")
    folder = Path(text).expanduser().resolve()
    check_mod_name(folder.name)
    if dump_root:
        romfs = (Path(dump_root) / "romfs").resolve()
        if _inside(folder, romfs) or _inside(romfs, folder):
            raise BancError("Hot reload writes a mod folder. It does not write into the dump.", "hot_reload")
    if project_root:
        built = (Path(project_root) / "build").resolve()
        if _inside(folder, built) or _inside(built, folder):
            raise BancError("Pick the Longshot mods folder, not the project's build folder.", "hot_reload")
    return folder


def publish(project, dump, destination: str) -> dict:
    """Build the project and copy that romfs into `destination`."""
    folder = destination_for(
        destination,
        dump_root=getattr(dump, "root", None),
        project_root=getattr(project, "root", None),
    )
    result = build(project, dump, install=False, make_zip=False)
    source = Path(result["romfs"])
    written: list[str] = []
    if source.is_dir():
        for file in sorted(path for path in source.rglob("*") if path.is_file()):
            relative = file.relative_to(source).as_posix()
            _atomic_bytes(folder / "romfs" / relative, file.read_bytes())
            written.append(relative)
    removed = _drop_stale(folder, written)
    atomic_write(folder / MANIFEST, json.dumps({"files": written}, indent=2) + "\n")
    warning = ""
    for line in result.get("log") or []:
        message = str(line.get("message") or "")
        if line.get("level") == "warn" and "does not merge" in message:
            warning = message
    return {"files": written, "removed": removed, "warning": warning, "folder": str(folder)}


def describe(result: dict) -> str:
    """One status line. Seeing the change in game means re-entering the map."""
    count = len(result.get("files") or [])
    removed = int(result.get("removed") or 0)
    folder = result.get("folder") or "the mod folder"
    if count:
        message = (
            f"Hot reload: wrote {count} file{'s' if count != 1 else ''} to {folder}. "
            "Leave the map and enter it again to see the change."
        )
    elif removed:
        message = (
            f"Hot reload: removed {removed} file{'s' if removed != 1 else ''} from {folder}. "
            "Leave the map and enter it again, and it will use the dump."
        )
    else:
        message = "Hot reload: nothing to write. The map still uses the dump until a scene change is saved."
    if result.get("warning"):
        message = f"{message} {result['warning']}"
    return message


class Publisher:
    """One debounced publish. A burst of saves becomes a single build."""

    def __init__(self, delay: float = 0.6):
        self.delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._running = False
        self._again = False
        self.state = {"status": "off", "message": "Hot reload is off.", "files": 0}

    def mark(self, **kwargs) -> None:
        with self._lock:
            self.state.update(kwargs)

    def public(self, settings: dict) -> dict:
        with self._lock:
            state = dict(self.state)
        return {
            "enabled": bool(settings.get("enabled")),
            "path": str(settings.get("path") or ""),
            **state,
        }

    def schedule(self, callback) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self.state["status"] = "pending"
            self.state["message"] = "Hot reload: saving…"
            timer = threading.Timer(self.delay, self._fire, args=(callback,))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _fire(self, callback) -> None:
        with self._lock:
            if self._running:
                self._again = True
                return
            self._running = True
        try:
            callback()
        finally:
            with self._lock:
                self._running = False
                again = self._again
                self._again = False
            if again:
                self._fire(callback)


publisher = Publisher()


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _manifest_file(folder: Path) -> Path:
    """The new manifest. The previous ``.bancwright-hot.json`` is still read."""
    current = folder / MANIFEST
    legacy = folder / ".bancwright-hot.json"
    if current.is_file() or not legacy.is_file():
        return current
    return legacy


def _drop_stale(folder: Path, written: list[str]) -> int:
    manifest = _manifest_file(folder)
    previous: list[str] = []
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            previous = [str(item) for item in (data.get("files") or [])]
        except (OSError, json.JSONDecodeError, TypeError):
            previous = []
    keep = set(written)
    removed = 0
    for relative in previous:
        if relative in keep or not _safe_relative(relative):
            continue
        target = folder / "romfs" / relative
        if target.is_file():
            target.unlink()
            removed += 1
    _prune_empty(folder / "romfs")
    return removed


def _safe_relative(relative: str) -> bool:
    if not relative or relative.startswith(("/", "\\")):
        return False
    return ".." not in Path(relative).parts


def _prune_empty(root: Path) -> None:
    if not root.is_dir():
        return
    for child in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        try:
            child.rmdir()
        except OSError:
            pass


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True

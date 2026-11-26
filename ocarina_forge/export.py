"""Build a project into a romfs folder, optionally install it and zip it.

Changed placement files are re-encoded with the dictionary the dump used
(id 3 for .bcett.byml.zs) and the resource size table entry is raised.
The dump itself is only read. Load filters are ignored: every actor comes
from the source banc plus the project diff, including layers the editor
did not fetch.
"""

from __future__ import annotations

import shutil
import struct
import zipfile
from pathlib import Path

from ocarina_forge.domains import note_pending
from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import dumps, same
from ocarina_forge.formats.restbl import entry_for_byml, load_table, save_table
from ocarina_forge.formats.zsio import compress, dict_id_for
from ocarina_forge.scene import export_document


class Log:
    def __init__(self):
        self.lines: list[dict] = []

    def info(self, message: str) -> None:
        self.lines.append({"level": "info", "message": message})

    def warn(self, message: str) -> None:
        self.lines.append({"level": "warn", "message": message})

    def error(self, message: str) -> None:
        self.lines.append({"level": "error", "message": message})


def build(project, dump, *, install: bool = False, make_zip: bool = False, level: int = 19,
          collision_follow: str | None = None) -> dict:
    if project.manifest.get("engine") == "after-ocarina":
        return _build_after_ocarina(project, dump, install=install, make_zip=make_zip)
    from ocarina_forge.collision_follow import (
        apply_srts,
        assign_srts,
        bake_tiles,
        collision_mode,
        compound_files,
        pose_moves,
    )

    log = Log()
    mode = collision_mode(project, collision_follow)
    out_root = project.root / "build" / "romfs"
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)

    bancs = project.modified_scenes()
    log.info(f"Project {project.manifest['name']} ({project.manifest.get('title')}), target {project.manifest['target']['version']}.")
    if project.manifest["target"].get("version") not in ("1.1.1",):
        log.warn(
            f"This project targets {project.manifest['target'].get('version')}, and the size-table rules "
            "were checked against 1.1.1. Confirm the entry before shipping the mod."
        )

    table = None
    rstb_rel = None
    editing_behavior = bool(project.behavior_actors())
    if bancs or editing_behavior:
        rstb_rel = dump.rstb_relative()
        if bancs and not rstb_rel:
            raise BancError(
                "This dump has no resource size table at System/Resource/*.rsizetable.zs. "
                "A changed placement file needs a bigger entry or the game can sit on the load forever. "
                "Nothing was installed.",
                "rstb",
            )
        if not rstb_rel:
            log.warn("This dump has no resource size table. Behavior files will still be written. A missing entry is not invented.")
        else:
            try:
                table = load_table((dump.romfs / rstb_rel).read_bytes())
            except BancError:
                raise
            except Exception as exc:
                raise BancError(f"Could not read the resource size table {rstb_rel}: {exc}", "rstb") from exc
            log.info(f"Size table: {rstb_rel}.")
    if not bancs:
        log.info("No scene diffs in this project.")

    written: list[str] = []
    table_raised = False
    for banc in bancs:
        diff = project.load_scene_diff(banc)
        try:
            document, dict_id, original = dump.read_byml(banc)
        except BancError as exc:
            raise BancError(f"{banc}: {exc.message}", exc.code) from exc
        moves = pose_moves(document, diff) if mode != "off" else []
        if mode == "srt" and moves:
            assign_srts(document, moves)
        try:
            exported, warnings = export_document(document, diff)
        except BancError as exc:
            raise BancError(f"{banc}: {exc.message}", exc.code) from exc
        if mode == "srt" and moves:
            apply_srts(exported, moves)
            for move in moves:
                log.info(
                    f"{banc}: {move.gyaml} ({move.hash}) collisionFollow srt. "
                    f"SRTHash 0x{0 if move.srt_old is None else move.srt_old:08x} -> 0x{move.srt_new:08x}. "
                    "Hash and Phive.Placement.ID stay. No .bphsh and no .bphsc is written."
                )
        elif mode == "bake" and moves:
            found_dir, _paths = compound_files(dump, banc)
            if not found_dir:
                log.warn(
                    f"{banc}: collisionFollow bake found no Phive/StaticCompoundBody directory "
                    "for this scene. No tile was written. The class .bphsh was not rewritten."
                )
            else:
                for relative, compound, compound_dict in bake_tiles(dump, banc, moves):
                    _write_compound(out_root, dump, table, relative, compound, compound_dict, log, level)
                    table_raised = True
                for move in moves:
                    if move.tiles:
                        log.info(
                            f"{banc}: {move.gyaml} ({move.hash}) collisionFollow bake. "
                            f"Instance transform updated in {', '.join(move.tiles)}. "
                            "SRTHash stays. The actor record and the shape bytes were not changed."
                        )
                    else:
                        log.warn(
                            f"{banc}: {move.gyaml} ({move.hash}) is not in a static-compound tile. "
                            "The class .bphsh was not rewritten."
                        )
        for warning in warnings:
            log.warn(f"{banc}: {warning}")
        if same(exported, document):
            log.info(f"{banc}: the edits cancel out, so the file was not written.")
            continue
        try:
            payload = dumps(exported)
        except (TypeError, ValueError, struct.error) as exc:
            raise BancError(f"{banc}: could not re-encode the placement file: {exc}", "bad_byml") from exc
        if dict_id is None:
            dict_id = dict_id_for(banc)
            log.warn(f"{banc}: the dump copy was not zstd. Writing it with dictionary {dict_id}.")
        packed = compress(payload, dict_id, dump.dicts(), level=level)
        destination = out_root / banc
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(packed)
        old_entry = table.get(banc) or 0
        if not old_entry:
            raise BancError(
                f"{banc} has no hashed or named entry in the resource size table. "
                "A changed file is raised in place. A brand-new file is only added when a patch asks for one. "
                "Nothing was installed.",
                "rstb",
            )
        new_entry = entry_for_byml(old_entry, len(original), len(payload))
        before, after = table.raise_to(banc, new_entry)
        table_raised = True
        log.info(
            f"{banc}: {len(original)} -> {len(payload)} bytes unpacked, dictionary {dict_id}, "
            f"size table {before} -> {after}."
        )
        written.append(banc)

    note_pending(project, log)
    from ocarina_forge.behavior_edit import export_behaviors

    for result in export_behaviors(project, dump, out_root=out_root):
        if not result.get("exported"):
            log.error(f"{result.get('path') or 'behavior'}: {result['message']}")
            continue
        log.info(result["message"])
        written.append(result["path"])
        if table is None or not result.get("path"):
            continue
        old_entry = table.get(result["path"]) or 0
        if not old_entry:
            log.warn(f"{result['path']} has no size-table entry. The file was written. A missing entry is not invented.")
            continue
        new_entry = entry_for_byml(old_entry, result.get("original"), result.get("unpacked") or 0)
        before, after = table.raise_to(result["path"], new_entry)
        if after != before:
            table_raised = True
        log.info(f"{result['path']}: size table {before} -> {after}.")

    if table is not None and table_raised and rstb_rel:
        packed_table = save_table(table, level=level)
        table_path = out_root / rstb_rel
        table_path.parent.mkdir(parents=True, exist_ok=True)
        table_path.write_bytes(packed_table)
        log.info(f"Wrote {rstb_rel} ({len(packed_table)} bytes compressed). Entries are only ever raised.")
    elif table is not None:
        log.info("No placement file changed, so the size table was not written.")

    installed = None
    if install:
        if not written:
            log.warn("Nothing new was built, so the mods folder was left alone.")
        else:
            folder = dump.root / "mods" / project.manifest["name"]
            romfs = folder / "romfs"
            if romfs.exists():
                shutil.rmtree(romfs)
                log.info(f"Replaced the previous romfs in {folder}.")
            shutil.copytree(out_root, romfs)
            (folder / "BUILD.txt").write_text(_text(log), encoding="utf-8")
            installed = str(folder)
            log.info(
                f"Installed to {folder}. Longshot loads mods/{project.manifest['name']}/romfs. "
                "A folder starting with '_' or '-' would be ignored; this name is fine."
            )

    zip_path = None
    if make_zip:
        files = [path for path in out_root.rglob("*") if path.is_file()]
        if not files:
            log.warn("Nothing was built, so no zip was written.")
        else:
            name = project.manifest["name"]
            zip_path = project.root / "build" / f"{name}.zip"
            if zip_path.exists():
                zip_path.unlink()
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file in files:
                    archive.write(file, (Path(name) / "romfs" / file.relative_to(out_root)).as_posix())
            log.info(f"Wrote {zip_path}. Unzip it so {name}/ sits in the Longshot mods folder.")
            zip_path = str(zip_path)

    log.info(f"Build folder: {out_root}.")
    (project.root / "build").mkdir(exist_ok=True)
    (project.root / "build" / "log.txt").write_text(_text(log), encoding="utf-8")
    return {
        "ok": True,
        "log": log.lines,
        "romfs": str(out_root),
        "installed": installed,
        "zip": zip_path,
        "files": written,
    }


def _build_after_ocarina(project, dump, *, install: bool, make_zip: bool) -> dict:
    """The After the Ocarina pipeline owns this project. Scene diffs are not merged in."""
    import io
    import contextlib

    from ocarina_forge.engine.ato import build_project

    log = Log()
    out_root = project.root / "build" / "romfs"
    if project.modified_scenes():
        log.warn(
            "This project also has scene actor diffs. The After the Ocarina build does not merge them. "
            "Those diffs stay in the editor."
        )
    log.info(
        f"Project {project.manifest['name']} ({project.manifest.get('title')}), "
        "engine after-ocarina. Text, events, gamedata, logic, schedule and the tapestry "
        "are applied by that pipeline."
    )
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            result = build_project(
                project.root,
                dump.romfs,
                out_root,
                mod_id=project.manifest["name"],
                mod_name=project.manifest.get("title") or project.manifest["name"],
                install=install,
                mods_root=(dump.root / "mods") if install else None,
            )
    except Exception as exc:
        message = getattr(exc, "message", None) or str(exc)
        raise BancError(message, "build") from exc
    for line in buffer.getvalue().splitlines():
        if line.strip():
            log.info(line)
    written = list(result.files)
    if result.resource_size_table:
        from after_ocarina.paths import RSTB_REL

        written.append(RSTB_REL)
    installed = str(result.installed) if result.installed is not None else None
    if installed:
        log.info(f"Installed to {installed}. Longshot loads mods/{project.manifest['name']}/romfs.")
    zip_path = None
    if make_zip and out_root.is_dir():
        name = project.manifest["name"]
        zip_path = project.root / "build" / f"{name}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(path for path in out_root.rglob("*") if path.is_file()):
                archive.write(file, (Path(name) / "romfs" / file.relative_to(out_root)).as_posix())
        log.info(f"Wrote {zip_path}.")
        zip_path = str(zip_path)
    log.info(f"Build folder: {out_root}.")
    (project.root / "build").mkdir(exist_ok=True)
    (project.root / "build" / "log.txt").write_text(_text(log), encoding="utf-8")
    return {
        "ok": True,
        "log": log.lines,
        "romfs": str(out_root),
        "installed": installed,
        "zip": zip_path,
        "files": written,
    }


def _write_compound(out_root, dump, table, relative, payload, dict_id, log, level) -> None:
    """Write one patched tile. The unpacked length matches the dump. The entry grows by 1024."""
    if table is None:
        raise BancError(
            "A patched static compound needs the resource size table, and this dump has none. "
            "Nothing else from this bake was installed.",
            "rstb",
        )
    old_entry = table.get(relative) or 0
    if not old_entry:
        raise BancError(
            f"{relative} has no hashed or named entry in the resource size table. "
            "A changed bake is raised in place. A missing entry is not invented.",
            "rstb",
        )
    new_entry = entry_for_byml(old_entry, len(payload), len(payload))
    before, after = table.raise_to(relative, new_entry)
    destination = out_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(compress(payload, dict_id, dump.dicts(), level=level))
    log.info(
        f"{relative}: instance transform patched, unpacked size still {len(payload)}. "
        f"Size table {before} -> {after}. Shape bytes were not written."
    )


def _text(log: Log) -> str:
    return "\n".join(line["message"] for line in log.lines) + "\n"

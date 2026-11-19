"""Build a project into a romfs folder, optionally install it and zip it.

Changed placement files are re-encoded with the dictionary the dump used
(id 3 for .bcett.byml.zs) and the resource size table entry is raised.
The dump itself is only read.
"""

from __future__ import annotations

import shutil
import struct
import zipfile
from pathlib import Path

from bancwright.domains import note_pending
from bancwright.errors import BancError
from bancwright.formats.byml import dumps, same
from bancwright.formats.restbl import entry_for_byml, load_table, save_table
from bancwright.formats.zsio import compress, dict_id_for
from bancwright.scene import export_document


class Log:
    def __init__(self):
        self.lines: list[dict] = []

    def info(self, message: str) -> None:
        self.lines.append({"level": "info", "message": message})

    def warn(self, message: str) -> None:
        self.lines.append({"level": "warn", "message": message})

    def error(self, message: str) -> None:
        self.lines.append({"level": "error", "message": message})


def build(project, dump, *, install: bool = False, make_zip: bool = False, level: int = 19) -> dict:
    log = Log()
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
    if bancs:
        rstb_rel = dump.rstb_relative()
        if not rstb_rel:
            raise BancError(
                "This dump has no resource size table at System/Resource/*.rsizetable.zs. "
                "A changed placement file needs a bigger entry or the game can sit on the load forever. "
                "Nothing was installed.",
                "rstb",
            )
        try:
            table = load_table((dump.romfs / rstb_rel).read_bytes())
        except BancError:
            raise
        except Exception as exc:
            raise BancError(f"Could not read the resource size table {rstb_rel}: {exc}", "rstb") from exc
        log.info(f"Size table: {rstb_rel}.")
    else:
        log.info("No scene diffs in this project.")

    written: list[str] = []
    for banc in bancs:
        diff = project.load_scene_diff(banc)
        try:
            document, dict_id, original = dump.read_byml(banc)
        except BancError as exc:
            raise BancError(f"{banc}: {exc.message}", exc.code) from exc
        try:
            exported, warnings = export_document(document, diff)
        except BancError as exc:
            raise BancError(f"{banc}: {exc.message}", exc.code) from exc
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
        new_entry = entry_for_byml(old_entry, len(original), len(payload))
        before, after = table.raise_to(banc, new_entry)
        log.info(
            f"{banc}: {len(original)} -> {len(payload)} bytes unpacked, dictionary {dict_id}, "
            f"size table {before} -> {after}."
        )
        written.append(banc)

    if table is not None and written and rstb_rel:
        packed_table = save_table(table, level=level)
        table_path = out_root / rstb_rel
        table_path.parent.mkdir(parents=True, exist_ok=True)
        table_path.write_bytes(packed_table)
        log.info(f"Wrote {rstb_rel} ({len(packed_table)} bytes compressed). Entries are only ever raised.")
    elif table is not None:
        log.info("No placement file changed, so the size table was not written.")

    note_pending(project, log)

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


def _text(log: Log) -> str:
    return "\n".join(line["message"] for line in log.lines) + "\n"

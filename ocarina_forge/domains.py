"""Domains a project can patch.

Scenes are built in this version. Dialog, event flows and RSDB tables have a
place in the project (see docs/project-format.md) and are deliberately not
written into the romfs yet. A later editor should implement `export` for its
domain and call it from `ocarina_forge.export.build`, next to the scene loop.
"""

from __future__ import annotations

PENDING = ("text", "events", "tables")

_LABELS = {
    "text": "Dialog",
    "events": "Event flows",
    "tables": "RSDB tables",
}


def note_pending(project, log) -> None:
    log.info("Dialog, event flows and RSDB tables are stored in the project. This version exports scenes only.")
    for domain in PENDING:
        count = len(project.domain_files(domain))
        if count:
            log.warn(
                f"{_LABELS[domain]}: {count} patch file(s) stay in the project and were not written into the romfs."
            )

"""Read-only summary of a project for the project panel.

Scene actor diffs stay in the viewport. Text, events, tables, gamedata and
UI recipes are listed here. The build still applies the advanced ones.
"""

from __future__ import annotations

from ocarina_forge.project import load_yaml


def _load(path) -> dict:
    data = load_yaml(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _bancs(value, found: list[str]) -> None:
    if isinstance(value, str) and value.startswith("Banc/") and value.endswith(".bcett.byml.zs"):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _bancs(item, found)
    elif isinstance(value, list):
        for item in value:
            _bancs(item, found)


def catalog(project) -> dict:
    root = project.root
    text = []
    text_dir = root / "text"
    for path in (sorted(text_dir.glob("*.yaml")) if text_dir.is_dir() else []):
        data = _load(path)
        for edit in data.get("edits") or []:
            if not isinstance(edit, dict):
                continue
            body = str(edit.get("text") or "").replace("\n", " ")
            text.append(
                {
                    "file": path.name,
                    "label": edit.get("label") or "",
                    "pack": edit.get("pack") or "",
                    "msbt": edit.get("msbt") or "",
                    "preview": body[:96],
                }
            )

    events = []
    quests: list[dict] = []
    seen_quests: set[str] = set()

    def quest(name: str, where: str, kind: str) -> None:
        if not name or name in seen_quests:
            return
        seen_quests.add(name)
        quests.append({"name": name, "where": where, "kind": kind})

    if (root / "events").is_dir():
        for path in sorted((root / "events").glob("*.yaml")):
            data = _load(path)
            patches = data.get("patches") or []
            flows = data.get("flows")
            kind = "list"
            title = path.stem
            challenges = []
            if isinstance(patches, list) and patches:
                kind = "flow"
                for patch in patches:
                    if not isinstance(patch, dict):
                        continue
                    rumor = patch.get("rumor") or {}
                    if isinstance(rumor, dict) and rumor.get("challenge"):
                        kind = "rumor"
                        challenges.append(str(rumor["challenge"]))
                        quest(str(rumor["challenge"]), f"events/{path.name}", "rumor")
                    for step in patch.get("steps") or []:
                        if isinstance(step, dict):
                            params = step.get("params") or {}
                            if isinstance(params, dict) and params.get("ChallengeName"):
                                quest(str(params["ChallengeName"]), f"events/{path.name}", "boot")
            elif isinstance(flows, list):
                kind = "pending" if not flows else "flow"
                title = "Pending flows" if not flows else path.stem
            events.append(
                {
                    "file": path.name,
                    "title": title,
                    "kind": kind,
                    "patches": len(patches) if isinstance(patches, list) else 0,
                    "challenges": challenges,
                }
            )

    tables = []
    for folder_name in ("rsdb", "tables"):
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            data = _load(path)
            rows = []
            for edit in data.get("edits") or []:
                if isinstance(edit, dict) and edit.get("row"):
                    rows.append(str(edit["row"]))
                    short = str(edit["row"]).rsplit("/", 1)[-1]
                    if "Challenge" in short or "Escape" in short or "Defeat" in short:
                        quest(short.split(".", 1)[0], f"{folder_name}/{path.name}", "table")
            tables.append({"file": f"{folder_name}/{path.name}", "rows": rows})

    gamedata = []
    if (root / "gamedata").is_dir():
        for path in sorted((root / "gamedata").glob("*.yaml")):
            data = _load(path)
            names = []
            for edit in data.get("edits") or data.get("expressions") or []:
                if isinstance(edit, dict):
                    names.append(str(edit.get("name") or edit.get("flag") or path.stem))
            if not names and data:
                names.append(path.stem)
            gamedata.append({"file": path.name, "names": names})

    ui = []
    if (root / "ui").is_dir():
        for path in sorted((root / "ui").glob("*.yaml")):
            data = _load(path)
            spec = data.get("tapestry") if isinstance(data.get("tapestry"), dict) else {}
            if spec.get("name"):
                quest(str(spec["name"]), f"ui/{path.name}", "tapestry")
            ui.append(
                {
                    "file": path.name,
                    "name": spec.get("name") or path.stem,
                    "src": spec.get("src") or "",
                }
            )

    scenes = []
    recipe_bancs: list[str] = []
    if (root / "scenes").is_dir():
        for path in sorted((root / "scenes").rglob("*.yaml")):
            data = _load(path)
            if data.get("actors") is not None and data.get("banc"):
                continue
            bancs: list[str] = []
            _bancs(data, bancs)
            recipe_bancs.extend(bancs)
            kind = "recipe"
            if "reroutes" in data and not data.get("stones") and not data.get("deletes"):
                kind = "pending" if not data.get("reroutes") else "recipe"
            summary = path.stem
            if data.get("place") is False:
                summary = f"{path.stem}: logic, the banc is not shipped"
            elif data.get("stones"):
                summary = f"{path.stem}: {len(data['stones'])} display stones"
            elif isinstance(data.get("reroutes"), list) and not data["reroutes"]:
                summary = f"{path.stem}: no reroutes yet"
            scenes.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "kind": kind,
                    "summary": summary,
                    "bancs": bancs,
                }
            )

    return {
        "engine": (project.manifest.get("engine") or "scenes"),
        "text": text,
        "events": events,
        "quests": quests,
        "tables": tables,
        "gamedata": gamedata,
        "ui": ui,
        "scenes": scenes,
        "recipeBancs": sorted(set(recipe_bancs)),
    }

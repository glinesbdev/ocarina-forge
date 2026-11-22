# Ocarina Forge

Ocarina Forge is a local scene editor for Longshot banc mods of the 2026 Nintendo Switch 2 remake of *The Legend of Zelda: Ocarina of Time* (version 1.1.1).

On November 22, 2026 the viewer freeze was lifted. Forge is the editor. An in-game editor on Origin `glinesbdev/longshot` (branch `editor-mod`) could move a placement and reload the area, and editing live memory still did not feel like editing the game. Every edit is a visual front end for a Longshot mod: typed patches under `patches/<banc>.json`, built with `longshot mod` check, pack, and dev. The running game is a preview. Forge asks it to reload the mod and to teleport Link. The picture in the editor stays Forge's Three.js renderer. The plan, including what carries over from `editor-mod`, what is dropped, and the Rust port of the slow Python paths (profile first, then PyO3), is [docs/design/mod-editor.md](docs/design/mod-editor.md). The milestone list is [docs/roadmap.md](docs/roadmap.md).

The editor browses every banc scene in a dump, moves and edits actors in a 3D view, and exports a mod folder Longshot will load. Actor meshes come from `Model/<Project>.<Fmdb>.bfres.zs` and the albedo sampler from `Tex/`, loaded at runtime (bind pose, no animation) and cached under `~/.cache/ocarina-forge/models`. A second open reuses that cache without converting again. A missing texture borrows a sibling albedo or a rock, vine, or cloth colour. Numbered terra materials use one layer of the `MaterialAlb` array. The field itself is the TerrainArc heightmap, with those same layers when the tile carries an index, and Ground starts on for it. Water is a single faint sheet (the viewport toggle starts on), and alpha-tested foliage is a cutout. Areas and other volumes are outlines, and the untextured `KokiriForest_01` stand-in stays hidden until Ground is turned on. The scene stays usable while the nearest models load first. It also builds an After the Ocarina project: text, events, gamedata, logic, schedules, and the tapestry are advanced patches, and a recipe that places an actor (the Spiritual Stones) can be moved like any actor, with the new transform written back to the recipe YAML. The build still produces those files. Saving that same edit as a Modding API mod folder, and checking it in a running game, is milestone 1 in [docs/design/mod-editor.md](docs/design/mod-editor.md). Dialog, event graphs, and a desktop package are later milestones there. They are not in this build.

Ocarina Forge reads a dump you already own. It does not ship, and this repository must not contain, game files, models, textures, or Nintendo text.

## Run it on Linux

Python 3.11 or newer, and Node 20 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd frontend
npm install
cd ..

./scripts/dev.sh
```

Then open http://127.0.0.1:47231.

A project lives in `~/.local/share/ocarina-forge/projects/<name>/` unless `OCARINA_FORGE_PROJECTS` says otherwise. The mod id is `local/<name>`. Saving a Longshot mod installs it under the test game, `~/Games/Longshot/mod-test` unless `OCARINA_FORGE_GAME` is set.

```bash
python -m ocarina_forge project create kokiri-sign --title "Kokiri sign" --author Bradyn
python -m ocarina_forge edit KokiriForest_Child --gyaml Obj_Kokiri_Signboard_A_01 --id 0x4a13f8880eb01a43 --translate -9.75,111.6,10.24 --dump ~/Games/Longshot/rc/v3.0.0
python -m ocarina_forge mod-export --install --dump ~/Games/Longshot/rc/v3.0.0
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0 --model Land_Kokiri_House_Inside_E_04 --cprofile /tmp/house-inside-e04.prof
```

`edit` needs `--id`. That class is placed more than once in Kokiri child, and a match of several actors writes nothing and exits non-zero. The sign above is Static `KokiriForest_013`, hash `0x4a13f8880eb01a43`. `mod-export` writes `build/mod` only when an actor still differs from the dump, and `--install` copies that folder to `<game>/mods/local/kokiri-sign/`. `profile` times one model in a private cache, so a cold `cache=miss` does not mean the key changed. A scene walk stays one model at a time unless you pass `--jobs`, and `--limit N` stops each scene early. It does not measure the browser.

The API listens on 127.0.0.1:47232. Vite proxies `/api` to it. `scripts/dev.sh` starts both and stops the API when you quit.

The first screen asks for the dump folder, the one that contains `romfs`. On a typical Longshot install that is:

```text
~/Games/Longshot/rc/v3.0.0
```

Mods are written to `~/Games/Longshot/rc/v3.0.0/mods/<project name>/romfs`. Longshot ignores a mod folder whose name starts with `_` or `-`, and Ocarina Forge refuses those names.

If you do not have a dump on this machine, choose **Use the bundled sample dump**. It builds a tiny synthetic Kokiri Forest (no Nintendo data) and opens the sample project.

The dump path and the open project are stored in `./ocarina-forge.local.yaml` (gitignored), or in `~/.config/ocarina-forge/config.yaml` if that file already exists. A `bancwright.local.yaml` or `~/.config/bancwright/config.yaml` from the previous name is still read, and the home config is renamed on first use. `OCARINA_FORGE_CONFIG`, `OCARINA_FORGE_DUMP`, and `OCARINA_FORGE_PROJECT` override them. `BANCWRIGHT_CONFIG`, `BANCWRIGHT_DUMP`, and `BANCWRIGHT_PROJECT` still work for this release. Converted models are written to `$OCARINA_FORGE_CACHE`, or `$XDG_CACHE_HOME/ocarina-forge/models`, or `~/.cache/ocarina-forge/models`. If `~/.cache/bancwright` is still there and the new folder is not, it is renamed across. `$BANCWRIGHT_CACHE` is still honored. That cache stays outside the repo. `python -m bancwright` prints a deprecation note and runs the same commands as `python -m ocarina_forge`.

## What you can do

- Browse `romfs/Banc/<Area>/*.bcett.byml.zs`, grouped by area.
- See each actor as a labeled box at its translate, rotate, and scale. Color is the actor category (NPC, enemy, object, land, effect, animal, system).
- Fly the camera: hover the view to focus it (a click is not required, and a text field or an open dialog keeps focus). Right-drag looks, WASD moves, Q and E go down and up, Shift is faster. Alt+left-drag orbits the point under the cursor (or the selection). Middle-drag pans. Scroll zooms. **Shortcuts**, or `?`, lists every binding. The same list is [docs/controls.md](docs/controls.md).
- Select an actor by clicking it or its row. Move and rotate with the gizmo, or type metres and degrees. Y is up. Yaw is the Y rotation. The file stores radians.
- Edit the other BYML parameters as typed fields. Add an actor from the names in `Pack/Actor`. Duplicate, hide, delete, or revert.
- Undo and redo cover the edits you make in this session. The project files are the lasting copy.
- **Build mod** re-encodes changed placement files with the dump’s zstd dictionary and raises the resource size table. It can install the result under `mods/<name>` and write a zip. The build log is shown in the window. The dump’s own files are not modified.

Edits are diffs in the project folder. See [docs/project-format.md](docs/project-format.md).

## Tests

```bash
source .venv/bin/activate
python3 -m pytest
```

Tests build synthetic BYML and zstd fixtures in a temporary directory. They do not need a game dump. A checklist for the real dump is in [docs/testing-on-real-dump.md](docs/testing-on-real-dump.md).

## Layout

| Path | Role |
| --- | --- |
| `ocarina_forge/` | Python library and FastAPI server. Parsers, validation, and exporters. This is the maintained data side. |
| `ocarina_forge/formats/` | BYML, SARC, zstd dictionaries, RESTBL, BFRES, and BNTX. |
| `ocarina_forge/domains.py` | Where a dialog, event, or table exporter plugs into the build. |
| `frontend/` | Vite + Three.js editor. Real meshes replace boxes when a model converts; the gizmo stays on the same pivot. New renderer work follows [docs/design/mod-editor.md](docs/design/mod-editor.md). |
| `docs/design/mod-editor.md` | How an edit becomes a Longshot mod, and the milestone plan. |
| `examples/sample-project/` | A small project in the format. |
| `examples/ato-scenes/` | An After the Ocarina style scene YAML, used to prove the simple scene importer. |
| `examples/after-the-ocarina/` | The imported After the Ocarina project (Bradyn's YAML only). |
| `vendor/after_ocarina/` | Bradyn's build library, plus the MIT community tools the pipeline shells out to. |
| `docs/formats.md` | The container rules this version implements, reconciled with `game-formats.md`. |
| `docs/textures.md` | Switch 2 GOB order for 16-byte blocks, and what `tex-audit` prints. |
| `docs/equivalence.md` | How to diff this build against After the Ocarina's own `scripts/build.py`. |

Import a simple scene YAML from the project menu, or open **After the Ocarina** to load the full project. The project panel lists its text, events, and quests. Scene recipes are marked in the scene list.

```bash
python scripts/import_ato.py ~/Work/after-the-ocarina examples/after-the-ocarina
python scripts/compare_romfs.py --ato ~/Work/after-the-ocarina
```

## Not in this version

This build still does not write a Modding API mod folder, and it does not talk to `longshot mod run`. Skeletal animation, a dialog editor, desktop packaging, blueprint phase B (connecting and adding nodes), baked lighting, and an asset browser are later milestones, not this slice. See [docs/design/mod-editor.md](docs/design/mod-editor.md) and [docs/roadmap.md](docs/roadmap.md). Panels dock, and a field on an existing behavior node can be edited. Models that cannot be resolved stay labeled boxes; see [docs/models.md](docs/models.md).

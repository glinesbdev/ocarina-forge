# Bancwright

Bancwright is a local workbench for placing actors in the 2026 Nintendo Switch 2 remake of *The Legend of Zelda: Ocarina of Time* (version 1.1.1), as played through the Longshot PC recompilation.

This version lets you browse every banc scene in a dump, move and edit actors in a 3D view, and export a mod folder Longshot will load. Actor meshes come from `Model/<Project>.<Fmdb>.bfres.zs` and base colors from `Tex/`, loaded at runtime (bind pose, no animation) and cached under `~/.cache/bancwright/models`. Areas and other volumes are outlines, not solid boxes. It also builds an After the Ocarina project: text, events, gamedata, logic, schedules, and the tapestry are advanced patches, and a recipe that places an actor (the Spiritual Stones) can be moved like any actor, with the new transform written back to the recipe YAML. The build still produces those files. Dialog editing, event-flow graphs, and a desktop package are later stages.

Bancwright reads a dump you already own. It does not ship, and this repository must not contain, game files, models, textures, or Nintendo text.

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

The API listens on 127.0.0.1:47232. Vite proxies `/api` to it. `scripts/dev.sh` starts both and stops the API when you quit.

The first screen asks for the dump folder, the one that contains `romfs`. On a typical Longshot install that is:

```text
~/Games/Longshot/rc/v3.0.0
```

Mods are written to `~/Games/Longshot/rc/v3.0.0/mods/<project name>/romfs`. Longshot ignores a mod folder whose name starts with `_` or `-`, and Bancwright refuses those names.

If you do not have a dump on this machine, choose **Use the bundled sample dump**. It builds a tiny synthetic Kokiri Forest (no Nintendo data) and opens the sample project.

The dump path and the open project are stored in `./bancwright.local.yaml` (gitignored), or in `~/.config/bancwright/config.yaml` if that file already exists. `BANCWRIGHT_CONFIG`, `BANCWRIGHT_DUMP`, and `BANCWRIGHT_PROJECT` override them. Converted models are written to `$BANCWRIGHT_CACHE`, or `$XDG_CACHE_HOME/bancwright/models`, or `~/.cache/bancwright/models`. That cache stays outside the repo.

## What you can do

- Browse `romfs/Banc/<Area>/*.bcett.byml.zs`, grouped by area.
- See each actor as a labeled box at its translate, rotate, and scale. Color is the actor category (NPC, enemy, object, land, effect, animal, system).
- Orbit (left drag, right drag pans, scroll zooms) or fly (WASD, Q/E, right-drag looks, Shift).
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
| `bancwright/` | Python library and FastAPI server. This is the piece that can later replace a mod’s build step. |
| `bancwright/formats/` | BYML, SARC, zstd dictionaries, RESTBL. |
| `bancwright/domains.py` | Where a future dialog, event, or table exporter plugs into the build. |
| `frontend/` | Vite + Three.js editor. Real meshes replace boxes when a model converts; the gizmo stays on the same pivot. |
| `examples/sample-project/` | A small project in the format. |
| `examples/ato-scenes/` | An After the Ocarina style scene YAML, used to prove the simple scene importer. |
| `examples/after-the-ocarina/` | The imported After the Ocarina project (Bradyn's YAML only). |
| `vendor/after_ocarina/` | Bradyn's build library, plus the MIT community tools the pipeline shells out to. |
| `docs/formats.md` | The container rules this version implements, reconciled with `game-formats.md`. |
| `docs/equivalence.md` | How to diff this build against After the Ocarina's own `scripts/build.py`. |

Import a simple scene YAML from the project menu, or open **After the Ocarina** to load the full project. The project panel lists its text, events, and quests. Scene recipes are marked in the scene list.

```bash
python scripts/import_ato.py ~/Work/after-the-ocarina examples/after-the-ocarina
python scripts/compare_romfs.py --ato ~/Work/after-the-ocarina
```

## Not in this version

Skeletal animation, a dialog editor, an event-flow graph, and desktop packaging. Those advanced patches build, and they are not edited on screen yet. Models that cannot be resolved stay labeled boxes; see [docs/models.md](docs/models.md).

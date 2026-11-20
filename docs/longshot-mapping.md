# Longshot mapping

Ocarina Forge keeps its own project folder. [The Longshot Modding API spec](https://git.magicbean.dev/MagicBeanDev/Longshot/wiki/Modding-API-Spec), revision 4 (as built 2026-11-19), is the package a later exporter writes. It is not the on-disk format. Conforming is an exporter change. The YAML in [project-format.md](project-format.md) stays the source.

This note does not change that YAML. It records where each edit lands in the spec, and the gaps that an exporter cannot paper over by replacing a whole game file.

## Exporters stay outside the project

Three exporters, one folder of source:

| Exporter | What it writes | Game files |
| --- | --- | --- |
| Today's rebuilt folder | `build/`, and the hot-reload copy under `mods/<name>/romfs` | Produced on the player's PC from their dump. Not committed. |
| A future `.lsmod` | A zip with `mod.json` at the root (§5.2, §5.5) and `patches/**/*.json` beside `romfs` (§6.1.1) | Patches and files the author wrote (§6.2). |
| Share-safe | The same package, refused when a file was derived from the game | Same rule as `longshot-mod lint`: no cloned BFRES, no decoded BNTX, no copied event or AI graph (§6.6). |

The project never stores the merged romfs. A whole-file replacement, when the host still needs one, is export output.

## Where each edit goes

| Ocarina Forge edit | Project today | Spec | Gap |
| --- | --- | --- | --- |
| Actor placement: move, rotate, scale, hide, remove | `scenes/<Area>/<Scene>.yaml`, actors keyed by the banc `Hash` | §6.1 map placements: add, move, remove, and change, folded in load order by `Hash` | §6.1.1 does not key placements by `Hash` yet. Typed patches reach a file by path. The exporter turns one hash record into those ops. It does not write the banc. |
| Params | `set` on an edited actor (dotted path, `$u32` and the other type tags). `params` on an added actor | §6.1 parameter patches: `set`, `add`, `remove`, plus `type` when the value's type changes. A component file such as an enemy param is the same patch on a pack member (§6.1.1) | Placement fields are part of the placement op, not a second file. Dotted paths become the spec's JSON-Pointer paths (`~0`, `~1`, `-`). The type tag becomes `type`. |
| Links | Stored on the actor when edited. Export still warns and leaves a dangling `Dst` rather than rewriting `Links` | §6.1 placement change by that actor's `Hash` | No separate link section. A link edit is a `set` or `remove` on that actor. Group lists stay out of the diff until the exporter has an op for them. |
| Text | `text/*.yaml` entries by label. Format 1 stores them and does not write them. The After the Ocarina pipeline applies them | §6.1 text: entries by label, per language, missing languages filled from `defaultLanguage`. §2.1 translation | §6.1.1 does not key text by label yet. The label is the key the exporter will use. A whole MSBT is not the edit. |
| Flags | A flag name on an actor is checked against `GameData/GameDataList.Product.110.byml.zs`. `gamedata/*.yaml` appends BoolExp rows and a reset type | §6.1 flag list: entries by hash. Adding one is `save.flags` (§9.6), which changes the save layout | Game-data edits are limited to adding entries. Rewriting an existing flag, or replacing the list, is outside the spec. `save.flags` is not built. The `110` in the file name is a version-numbered target: the project stores the flag, and the exporter binds the path from the dump. |
| Events | `events/*.yaml`: flow patches, the boot hook, the rumor builder | §2.1 new dialogue: text, flags, and the event service. The game's event flows are opaque files (§6.1) | §6.5: event graphs do not merge. Last layer wins. The project stores typed ops (below), not a replaced flow. |
| AI | AINB logic in the pipeline YAML. The Behavior tab draws the graph and does not edit it yet | §6.1 opaque files: AI graphs, last layer wins | §6.5: AI graphs do not merge. Same rule as events. |
| Schedules | `AIScheduleParam` recipes in the pipeline | §6.1 data tables, row ops by the table's key, when the file is a table. An opaque schedule graph is §6.1's last-layer file | §6.1.1 does not key rows yet. A schedule edit is a row op, not a replaced param file. A graph falls under §6.5. |
| New actors | `state: added`, `actor` is a `Pack/Actor` gyaml, hash is a new 64-bit id with bit 62 set | §6.1 placement add by `Hash`. §6.5: a new actor can only use a class the game already has | No mod-scoped actor ids. §6.2's id is `owner/name:kind`, and the host maps it to a game id. Our hash is the game id. It stays stable. An exporter may also emit the scoped id later. It does not renumber the hash. |
| New text files | A new label in `text/`, or a file the pipeline adds | §6.1 text by label | A new label is an entry. A new file copied from the dump is game-derived and share-safe export refuses it. |

## Gaps, in one place

- Opaque event and AI graphs (§6.5). The host will not merge them. The project still must not store a replaced file.
- Game-data edits are limited to adding entries (§6.1 flag list, §9.6). Existing rows are not a patch target.
- Version-numbered targets. `GameDataList.Product.110` and any path with a build number in it are dump paths. The project names the entry. The exporter chooses the file.
- No mod-scoped actor ids (§6.2). Placements keep the banc `Hash`.

§6.4 model swap (an opaque file plus textures by name) is a later exporter. The glTF cache is not a mod file. §6.3 texture packs are authored images named for the game's textures, replaced in the renderer, not a copied BNTX.

## Behavior editor

The editor in [roadmap.md](roadmap.md) draws AINB, event flows, and schedule graphs. The file it writes follows these rules.

- A graph edit is a typed op: `add node`, `connect`, or `set field`.
- Every node, pin, and edge has a stable id. The project mints it once. Drawing the graph again does not renumber it.
- The dump's graph is the base. The project stores the ops. It never stores a whole-file replacement.
- Today's exporter may compile those ops into one opaque file, because §6.1 serves event flows and AI as whole files and §6.5 does not merge them. That file belongs in `build/`.
- When the host can merge graphs, a second exporter emits the same ops. The project does not change shape.
- Share-safe export refuses the base graph and any other bytes taken from the dump.

## Validation

`ocarina_forge.validate.diagnose` is the check. It returns a dict. It does not print.

```text
{
  "banc": "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs",
  "diagnostics": [
    {
      "file": "scenes/KokiriForest/KokiriForest_Child_Static.yaml",
      "field": "/actors/0x…/Dynamic/IsEnable",
      "pointer": "/actors/0x…/Dynamic/IsEnable",
      "message": "…",
      "code": "bad_param",
      "severity": "error",
      "actor": "0x…"
    }
  ]
}
```

`field` and `pointer` are the same JSON pointer. `python -m ocarina_forge problems <banc> --json` prints that object and exits 1 when any row is an error. That is the same job as `longshot-mod check --json`: a library result a tool can read, not a line in a log. The Problems list and the actor's Properties panel read this list.

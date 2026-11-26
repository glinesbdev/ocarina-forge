# Collision follow

Moving an actor in Forge moves the mesh. The Kokiri sign also has a baked compound row, and that row stays at the old spot unless `collisionFollow` is `bake`.

The scene diff is not in git. Each run deletes `scenes/` and writes only the bancs for this run, with the dump's coordinates plus 4 m on +X. That folder is gitignored. Do not commit it, and do not commit a `.bphsc` or a `.bphsh`.

## What the sign did in game

`Obj_Kokiri_Signboard_A_01` has an actor-side ControllerSet that follows `Translate`, and a baked row in `KokiriForest_Child_X1_Z2.bphsc`.

- `off`: collision at the new spot, and an invisible box at the old spot. The box is the vanilla bake row.
- `bake`: the old spot is clear. Bake mode is what moves that row. The shape stays the same.

A `.bphsc` actor-table record with `first == last == 0xFFFFFFFF` has no body. `Land_Kokiri_Zenmai_A_02` (a fern) is one. Being in the actor table is not baked collision. The 16-byte tail of a record starts with `SRTHash`.

## House facade

`Land_Kokiri_House_Outside_B_02` (`0xd471946eaeb8e44f`) is only the front facade, not the whole house. This command replaces `scenes/` and exports that actor alone. It looks on Kokiri Child Static, then the paired Dynamic banc. `--banc` overrides both.

```bash
python -m ocarina_forge collision-follow-test --dump ~/Games/Longshot/rc/v3.0.0 --install --collision-follow bake --id 0xd471946eaeb8e44f
```

Hyblitze traced the actor-pack path and the physics ray. Zirem wrote the spawn request. That ray queries collision. It does not describe this bake. The layout Forge patches, and what each `collisionFollow` mode writes, is in `docs/formats.md`. Their code is not copied.

## What the default command moves

- `Obj_Kokiri_Signboard_A_01`, Static, instance `KokiriForest_013`, hash `0x4a13f8880eb01a43`, banc `Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs`. Translate moves 4 m on +X from whatever the dump has. The destination is not a hardcoded coordinate.
- One `Land_` whose `Bakeable` is not `false`. A missing `Bakeable` counts. An explicit `false` is skipped. A compound row with a body ranks ahead of a nearer row with no body, and the paired Dynamic banc is included. The log prints gyaml, hash, old position, new position, `SRTHash`, `Bakeable`, and `compound body` or `compound no body`.

`--id 0x...` moves only that actor. `--banc` selects the scene. Without `--banc`, the actor is taken from the Kokiri Child Static banc, or from the paired Dynamic banc when the hash is only there.

## Steps

1. Quit Longshot.
2. From the repo, with the virtualenv active:

   ```bash
   python -m ocarina_forge collision-follow-test --dump ~/Games/Longshot/rc/v3.0.0 --install --collision-follow bake
   ```

   `--dump` is the folder that contains `romfs/`. `--game` overrides the install. The default game folder is `~/Games/Longshot/mod-test`, or the configured game. The manifest still says `srt`. Pass `--collision-follow bake` for the mode that cleared the sign's old box.

3. The command replaces `examples/collision-follow-test/scenes/` and installs `mods/local/collision-follow-test/`. An earlier scene yaml is not exported.
4. The patch JSON has `Translate` for each moved actor. `bake` also writes the `.bphsc` tiles that contain those actors. Do not commit those tiles. `srt` writes an `SRTHash` set and no `.bphsc`.
5. Enable `local/collision-follow-test`. Restart the process once (`longshot mod stop`, then `longshot mod run`, or `restart --manifest`). The Kokiri sign's `Translate` array can be a shared container, and the size table is read at process start.
6. Load a child file and enter Kokiri Forest the same way you do for the sign milestone.
7. The sign should stand 4 m toward +X of the old position printed in the log. With `bake`, the old spot is clear. With `off`, an invisible box stays there.

## Control

```bash
python -m ocarina_forge collision-follow-test --dump ~/Games/Longshot/rc/v3.0.0 --install --collision-follow off
```

That install replaces `local/collision-follow-test`. `off` leaves the vanilla bake row at the old spot. Do not judge a run while another mode's files are still the ones enabled.

## If the SRTHash set looks ignored

`--romfs` also writes a whole-banc mod through Build mod, into the dump's `mods/collision-follow-test/romfs`. Disable `local/collision-follow-test` before you judge that run. Do not enable both.

```bash
python -m ocarina_forge collision-follow-test --dump ~/Games/Longshot/rc/v3.0.0 --install --romfs
```

## What this does not answer

An actor with `Bakeable: false` is not the bake question. The log prints `Bakeable`. Neither `srt` nor `bake` rewrites a class `.bphsh`. Whether an unbaked actor's collision follows the placement is still open.

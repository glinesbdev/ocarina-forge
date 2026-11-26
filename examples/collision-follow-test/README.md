# Collision follow, variant B

This project is the SRTHash experiment from issue #10. Moving an actor in Forge moves the mesh and leaves the baked collision where it was. Variant B keeps `Hash` and `Phive.Placement.ID`, writes a new `SRTHash`, and ships no shape file. A new hash value is not a new shape.

The scene diff is not in git. The command below reads your dump and writes `scenes/` with the dump's coordinates plus 4 m on +X. That folder is gitignored. Do not commit it, and do not commit a `.bphsc` or a `.bphsh`.

Hyblitze traced the actor-pack path and the physics ray. Zirem wrote the spawn request. That ray queries collision. It does not describe this bake. The layout Forge patches, and what each `collisionFollow` mode writes, is in `docs/formats.md`. Their code is not copied.

The game was not launched to produce this example. The result below is still yours to record.

## What the command moves

- `Obj_Kokiri_Signboard_A_01`, Static, instance `KokiriForest_013`, hash `0x4a13f8880eb01a43`, banc `Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs`. Translate moves 4 m on +X from whatever the dump has. The destination is not a hardcoded coordinate.
- The nearest `Land_` actor whose `Bakeable` is not `false`. A missing `Bakeable` counts. An explicit `false` is skipped. The log prints its gyaml, hash, old position, new position, old `SRTHash`, and `Bakeable`.

`--id 0x...` moves only that actor, in this banc unless you pass another banc path as the argument.

## Steps

1. Quit Longshot.
2. From the repo, with the virtualenv active:

   ```bash
   python -m ocarina_forge collision-follow-test --dump ~/Games/Longshot/rc/v3.0.0 --install
   ```

   `--dump` is the folder that contains `romfs/`. `--game` overrides the install. The default game folder is `~/Games/Longshot/mod-test`, or the configured game.

3. The command writes `examples/collision-follow-test/scenes/` and installs `mods/local/collision-follow-test/`.
4. Open the patch JSON. Each moved actor has a `Translate` set and an `SRTHash` set. The mod has no `.bphsh` and no `.bphsc`.
5. Enable `local/collision-follow-test`. Restart the process once (`longshot mod stop`, then `longshot mod run`, or `restart --manifest`). The Kokiri sign's `Translate` array can be a shared container, and the size table is read at process start.
6. Load a child file and enter Kokiri Forest the same way you do for the sign milestone.
7. The sign should stand 4 m toward +X of the old position printed in the log. Walk through the old spot. Walk into the sign at the new spot.
8. Do the same for the `Land_` the log names. Use the printed old and new coordinates.

Write down which of these happened, for the sign and for the `Land_`:

- Collision at the new pose. The SRTHash re-mint is enough.
- The mesh moved and collision stayed at the old pose. The re-mint is not enough. The next try is `--collision-follow bake`, which writes `.bphsc` tiles. Do not commit those files.
- No collision at either place.
- The actor is missing.

## Control

Disable `local/collision-follow-test` first. Then:

```bash
python -m ocarina_forge collision-follow-test --dump ~/Games/Longshot/rc/v3.0.0 --install --collision-follow off
```

That is today's bug: the mesh moves and the bake stays. Do not leave the `srt` mod and this one enabled together. They are the same mod id, so the second install replaces the first. Disable it before you judge a later run.

## If the SRTHash set looks ignored

`--romfs` also writes a whole-banc mod through Build mod, into the dump's `mods/collision-follow-test/romfs`. Disable `local/collision-follow-test` before you judge that run. Do not enable both.

```bash
python -m ocarina_forge collision-follow-test --dump ~/Games/Longshot/rc/v3.0.0 --install --romfs
```

## What this does not answer

An actor with `Bakeable: false` is not the bake question. The log prints `Bakeable`. Neither `srt` nor `bake` rewrites a class `.bphsh`. Whether an unbaked actor's collision follows the placement is still open.

# Vendored After the Ocarina build

`after_ocarina/` is Bradyn's build library from `glinesbdev/after-the-ocarina`, branch `m8-navi-rumors`, commit `ad60e27d6342c8b602a077afa290ad646d7c3941`.

The archive attached to the import was the parent commit `72c844a`. This environment could not clone the Origin repository, so the `ad60e27` delta was applied on top of that archive: tapestry, Zora starter bind, Mido's Show before Complete, `Defeat_Ganon` Complete on the boot hook, and `rewrite_hashed`.

`third_party/oot-remake-modding/tools/` is the MIT community tools the pipeline shells out to (`msbtmod.py`, `rstb_set.py`, `zstool.py`, `zsmod.py`, `evflags.py`, `asbtool.py`, `byml.py`), pinned by After the Ocarina at `0b81628d`. The license text is `bancwright/formats/LICENSE-oot-remake-modding.txt`. No game files are in this tree.

Bancwright calls this through `bancwright.engine.ato`. `paths.configure` points `src/` at a project such as `examples/after-the-ocarina`. The package root stays here so the tools path does not move.

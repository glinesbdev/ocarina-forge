# Asset library: moving an actor from one banc to another

Research for [#16](https://github.com/glinesbdev/ocarina-forge/issues/16). It also covers [#10](https://github.com/glinesbdev/ocarina-forge/issues/10) (baked collision left behind when an actor moves) and [#11](https://github.com/glinesbdev/ocarina-forge/issues/11) (decorations that do not follow a parent). This is a docs spike. No drag-and-drop was built, and nothing here was loaded in a running game.

**Verdict: partly feasible.** A placement can be copied into another banc and exported as a new actor, because the class, mesh, textures, and behavior files are global. The copy does not bring scene collision, unlinked decorations, group membership, or rail and flag context with it. Those are the blockers, not the model.

## How a placement is stored

A scene file is `Banc/<Area>/<Scene>.bcett.byml.zs` (zstd dictionary 3). One age has two files, `_Static` and `_Dynamic`. The editor writes the banc the actor belongs to and can draw the sibling as a companion. Kokiri houses are Dynamic; the NPCs that stand on them are Static (`docs/formats.md`, `docs/models.md`).

Each entry in `Actors` is one instance. The header fields the editor treats as identity and pose are `Gyaml`, `Hash`, `SRTHash`, `Translate`, `Rotate`, `Scale`, and `Name` (`ocarina_forge/actors.py`). Everything else is parameters: `Dynamic`, `Links`, `Presence`, `Bakeable`, `Phive`, and any other key the file happens to have.

`Gyaml` is the class name. It matches `Pack/Actor/<Gyaml>.pack.zs` (dictionary 4). The pack is a SARC. Forge does not unpack it to list classes; `Dump.actor_names` reads the filenames (`ocarina_forge/dump.py`). The public 1.1.1 file list counts 8,373 actor packs and 247 banc files ([Kabosune, romfs.md](https://github.com/Kabosune/oot-remake-modding/blob/main/docs/romfs.md)). Those counts are from that listing, not from a dump in this repo.

The same engine family is Nintendo's ModuleSystem, the Tears of the Kingdom engine, on a newer console build ([Kabosune, engine.md](https://github.com/Kabosune/oot-remake-modding/blob/main/docs/engine.md), citing oatmealdome). TotK notes are useful and are marked as such below. They are not an OoT 1.1.1 specification.

## 1. Can an actor move, and what does it depend on?

### What already works in this repo

Adding a class to the open banc is implemented. The `add` command stores `state: added` and a gyaml (`ocarina_forge/scene.py`). `make_actor` writes a new `Hash` with bit 62 set, the same value as `Phive.Placement.ID`, and `SRTHash` equal to the low 32 bits of that hash (`ocarina_forge/actors.py`). **Build mod** (`POST /api/export`, `ocarina_forge/export.py`) re-encodes the whole banc, including added actors, and raises the resource-size entry with the existing formula. The dump's actor pack, model, and texture are not copied. The game already has them.

**Save Longshot mod** (`POST /api/mod-export`) does not write that add. `placement_ops` logs "Adding a placement is a later milestone, so it was not written" and emits no op (`ocarina_forge/modexport.py`). A library drop is visible in the editor and in the rebuilt-banc export. It is absent from the typed-patch export until that gap is closed.

`duplicate` is the wrong primitive for a cross-banc copy. It copies every non-header key, including `Links` and `Phive`, then `make_actor` overwrites `Hash` and `Phive.Placement.ID` only. `Links` still point at the source hashes. `SRTHash` is freshly minted because it is a header field and is not part of that parameter bag.

The closest in-repo cross-banc copy is After the Ocarina's display stones (`vendor/after_ocarina/after_ocarina/stones.py`). A Market altar actor is cloned into a hometown Static banc. The clone gets a new `Hash`, a new `SRTHash`, and `Phive.Placement.ID` set to the new hash. `Links` are deleted. Any other integer that equals a Market actor's hash is deleted. `Presence` is replaced with a title-event flag. The build stops if `Pack/Actor/<Gyaml>.pack.zs` is missing, or if a path string on the actor (`.pack`, `.bfres`, `.bphsh`, `.bgyml`, `.byml`, with or without `.zs`) is not in the dump. The module comment says the other cloner, Saria's, "has not been seen in game", and this spike did not run the stone placement in a game either. Saria's cloner also says that if the old hash appears in `AiGroups`, `FarDeleteGroups`, or a similar list, the clone may not show, and the build does not edit those lists (`saria.py`).

So: copying the placement record is feasible, and the global class files do not need to be copied. Whether the dropped actor stands, collides, and runs its logic in a different area is not proven here.

### Dependency list

| Dependency | Where it lives | Copied with the placement? |
| --- | --- | --- |
| Class definition | `Pack/Actor/<Gyaml>.pack.zs`, global | No. The gyaml string is enough if that pack exists. |
| Model | `Model/<Project>.<Fmdb>.bfres.zs`, named by `Component/ModelInfo` inside the pack (`docs/models.md`) | No. |
| Textures | `Tex/<name>.bntx.zs`, plus the shared `Tex/MaterialAlb.bntx.zs` array | No. |
| Actor-local collision | `Phive/Shape/Dcc/<Gyaml>__Physical.Nin_Ounce_NVN2.bphsh` (optional `.zs`), read by `physical_triangles` | No. The shape is in actor space. The placement transform instances it. |
| Baked scene collision | `Phive/StaticCompoundBody/<Scene>/…bphsc`. `collisionFollow: bake` patches the instance transform in the same scene. See [formats.md](../formats.md). | No. It is in source-scene world space. A move can retarget that scene's tile. A copy into another area must not ship the source tile. See §4. |
| AI graph | `.ainb` in the pack, or `AI/` and `Logic/` (`ocarina_forge/behavior.py`) | No. |
| Schedule | `Component/AIScheduleParam/…bgyml` in this pack or another pack. A schedule in another pack is labeled shared. | No. |
| Talk / event flow | `Event/EventFlow/<actor>.bfevfl.zs`, or a name in `FlowchartFileName` / a schedule `EventName` | No. The file is global. The flow can still test scene flags. |
| Instance params | `Dynamic` on the actor | Yes. Copy the dictionary with the actor. |
| Links | `Links[]` with `Dst` or `Hash`, and optional `Src` (`ocarina_forge/validate.py`) | Only if the target actor is copied too, and the hashes are rewritten. |
| Groups | `AiGroups`, `FarDeleteGroups`, `SimultaneousGroups`, `PlgGdTable`, `PhiveHeader.PlacementGroups` | Not today. Export leaves the destination lists unchanged (`ocarina_forge/scene.py`). |
| Presence / flags | `Presence`, and any parameter whose key looks like a flag or `GameDataName` | The string is copied. The flag is global (`GameData/GameDataList.Product.110.byml.zs`) but its meaning is often this scene's quest. |
| Child actors | Separate `Actors` entries. No parent field was found in this repo. | Only by a second search. See §4. |
| Rails | Not read by Forge. TotK mods strip a per-actor `Rails` key. Unverified here. | Do not invent a rewrite. Detect the key if a dump has it. |
| Terrain, climate, culling, lightmaps | `TerrainArc`, `Banc/GameBancParam`, and (public listing only) `PreComputedCulling/*.bcul.zs` and `Bake/*.bkprb.zs` | No. The destination scene keeps its own. |

Public OoT notes describe the pack as an `ActorParam` (`Actor/<name>.engine__actor__ActorParam.bgyml`) whose refs point at `ModelInfo`, `Physics`, and the other components, with `$parent` inheritance ([engine.md](https://github.com/Kabosune/oot-remake-modding/blob/main/docs/engine.md)). A texture tool for this game follows `ActorParam` → `ModelInfo` for the mesh, and `PhysicsParam` → `ControllerSetParam` → `ShapeParam` → a `.phsh` for collision ([oot-texture-tool](https://github.com/ToxicToucan/oot-texture-tool)). Forge's mesh path matches the ModelInfo step (`docs/models.md`). Forge's collision path does **not** follow ShapeParam. It tries one filename built from the gyaml. If the pack's shape file has another name, `physical_triangles` returns nothing and the fitter falls back to visual triangles (`ocarina_forge/world_surface.py`). How often the convention name matches retail is unverified.

`RSDB/ActorInfo` is a global table in the public listing. Forge does not read it when placing an actor. A TotK tutorial found that a static prop needed larger `CalcRadius` / `LoadRadius` / `DisplayRadius` before collision worked outdoors, and that a shrine accepted the same object without that change ([GameBanana, custom structure](https://gamebanana.com/tuts/19424)). That is TotK, edited in the actor pack, not in the banc. It is unverified for OoT 1.1.1. A library drop should not rewrite `ActorInfo`. If an outdoor copy is invisible or has no collision while a small-dungeon copy works, that table is the first place to look.

## 2. Global files versus per-banc files

**Global, referenced by name, not copied on export**

- `Pack/Actor/<Gyaml>.pack.zs` and any other pack a schedule name points at.
- `Model/…bfres.zs` and `Tex/…bntx.zs`.
- `Phive/Shape/Dcc/…bphsh` when the convention path exists.
- `AI/`, `Logic/`, `Sequence/`, `Event/EventFlow/` (public layout and `behavior.py`).
- `GameData/GameDataList.Product.110.byml.zs`. Validation checks flag strings and murmur3 hashes against it (`ocarina_forge/validate.py`). Copying a placement does not add a flag. A flag the class already uses is already in the list.
- `RSDB/*`, including `ActorInfo` and `GameBancInfo`. The public tuning notes say `GameBancInfo` lists the scenes ([tuning.md](https://github.com/Kabosune/oot-remake-modding/blob/main/docs/tuning.md)). Forge does not write it.

**Per banc (or per area), and part of the copy**

- The actor dictionary itself, with a new `Hash`, a new `SRTHash`, and `Phive.Placement.ID` equal to the new hash.
- `Translate` / `Rotate` / `Scale`, rewritten to the drop point. Decorations keep their offset from the parent.
- `Dynamic` and the rest of `Phive` (anything other than `Placement.ID`).
- `Bakeable`, unchanged. It records whether this instance was authored as baked. It does not move the bake.
- `Name`, copied, and disambiguated if the destination already uses it. The stone builder refuses a duplicate `Name`.
- `Presence`, copied and then shown as a warning. Replacing it, the way the stones replace Market presence with a title flag, is a per-drop choice, not the default.

**Per scene, not copied, and not safe to copy as files**

- The destination's other actors, its group lists, and its `PhiveHeader`.
- `Phive/StaticCompoundBody/<Scene>/`. Issue #10 names the files `…_X<i>_Z<j>….bphsc`, compressed, with a 4×4 grid per age in Kokiri and 256 tiles per age in Hyrule Field. Those counts are not re-measured here. The actor record, the body index, and the instance transform that `collisionFollow: bake` writes are in [formats.md](../formats.md). The tail starts with `SRTHash`. The patcher does not write it.
- `TerrainArc` and `GameBancParam`. The dropped actor stands on the destination heightmap.
- Baked lighting and precomputed culling, from the public folder list. `docs/models.md` already says material `bake0` on these models binds a shared placeholder, not a per-scene lightmap that Forge samples. A copied actor will not pick up a new light bake. Whether old culling hides a new prop is unverified.

**What export must register**

For the rebuilt banc (`export.py`): the new actor in `Actors`, and a raised size-table row for that banc. The row must already exist. Export refuses a banc with no row. It does not add a row for a new pack, because no pack is written.

For the Longshot patch export: an add op that does not exist yet. `docs/longshot-mapping.md` already maps `state: added` to a placement add by `Hash`, and says a new actor can only use a class the game already has. That matches this design. The exporter still has to grow the banc and the size table the same way `export.py` does when the patch is applied.

No mod should ship a second copy of `Pack/Actor`, `Model`, or `Tex` for a stock gyaml. Share-safe export already refuses cloned BFRES and decoded BNTX (`docs/longshot-mapping.md`).

## 3. What does not move cleanly, and how the UI should say so

Treat these as badges on the library card and as a checklist on drop. The drop can still proceed. The badge says what will be wrong.

| Badge | When | Default action |
| --- | --- | --- |
| `scene-bound` | `Gyaml` is `StartPos`, `Player`, `PlayerCamera`, an `Area` / anchor / airwall / entrance (`FieldAndDungeonEntranceEventRequestActor` is one Forge already special-cases), or the category is `system` or `area` | Allow it, off by default. These are map infrastructure, not props. |
| `linked` | `Links[].Dst` or `Links[].Hash` is a non-zero hash that is not in the copied set, or `Src` is not this actor | Offer "include linked actors" or "drop links". Dropping matches the stone builder. Leaving them matches today's `duplicate`, and the destination will not resolve them. Validation already reports `missing_link` for a hash that is not in this scene or another layer of the same folder. |
| `grouped` | The source hash appears in `AiGroups`, `FarDeleteGroups`, `SimultaneousGroups`, `PlgGdTable`, or `PhiveHeader.PlacementGroups` | Do not pull the whole group. A group can be the town. Warn that the group was not copied, which is the failure Saria's cloner already documents. |
| `flag-gated` | `Presence`, or any flag-like key (`validate._is_flag_key`: key ends in `flag` or `gamedataname`, plus the known flag keys), names a GameData flag | Copy the string. Warn that the actor is hidden until that flag matches, including `IsInverted`. Quest flags such as `HavePlayedEvent.…` stay tied to the source story. |
| `age` | The gyaml contains `_Child` or `_Adult` and the destination banc's age token differs | Warn. The pack still exists. Behavior that reads `Sequence_IsChildhood` (public engine notes) may no-op. Unverified per class. |
| `split-layer` | A linked actor, or a proximity decoration, lives in the companion banc | Say which file it will be written to. Kokiri houses and NPCs are this case. Copying one layer leaves the other behind. |
| `baked-collision` | `Bakeable` is true, or the source is `Land_` (issue #10 says about 83% of `Land_` placements are baked; not re-counted here) | Warn that in-game collision at the new transform is unproven until the #10 experiment. |
| `path-bound` | The actor or the banc root has a key named `Rails` or `ActorToRailLinks` | TotK randomizers delete a per-actor `Rails` key when they change an enemy ([TotkRandomizer](https://github.com/MelonSpeedruns/TotkRandomizer/blob/main/Form1.cs)). Forge has no rail reader. If the key is absent, do not show the badge. If it is present, refuse a silent copy and list the key. |
| `shared-behavior` | The schedule graph is tagged `sharedFrom` another pack | Informational. The behavior file is global and does not need copying. |
| `missing-class` | No `Pack/Actor/<Gyaml>.pack.zs` | Block the drop. `behavior._require_pack` already errors in this case. |

Hashes inside `Dynamic` that equal some other actor's `Hash` in the source area are the same kind of edge as `Links`, even when they are not under `Links`. The stone builder deletes every such integer that points at an actor left behind. The library should list them before it deletes them.

Root keys Forge does not know should pass through on a same-banc edit, because `export_document` deep-copies the document and replaces `Actors`. A cross-banc import of one actor does not see those keys unless the indexer records them. The first index build should collect the set of root key names and actor key names so a rail array is discovered from the dump instead of from a guessed schema.

## 4. The graph #10 and #11 need

One dragged actor is a root. The drop copies a closure around that root. Three edge kinds are enough. They should stay separate in the UI, because only the first kind is authoritative.

### Explicit edges (real file references)

From the source actor, and then from each actor added to the set:

- `Links[].Dst` and `Links[].Hash` (skip 0).
- `Links[].Src` when it names a different actor.
- Any other u64 on the actor, except its own `Hash` and `Phive.Placement.ID`, that equals an actor hash in the same area folder. The folder rule is the one `validate._folder_hashes` already uses: same directory, so Static, Dynamic, and any other layer that shares the folder.
- The reverse: actors whose links or stray hashes point at the root.

Stop at a fixed depth and show the set. Do not follow group lists into membership. Record the group as a `grouped` badge instead.

On write, mint one new hash per copied actor (bit 62), set `SRTHash` to the low 32 bits, set `Phive.Placement.ID` to the new hash, and replace copied hashes in `Links` and in the rest of the parameter tree. Drop edges that point outside the set and keep them on the warning list. This is `make_actor` plus the stone builder's retarget, applied to a set rather than to one actor.

### Spatial edges (decorations, #11)

Issue #11 is right that decorations have no link and no group. Nothing in `actors.py`, `scene.py`, or `validate.py` names a parent, a child actor, or a decoration. A search of this repo finds no `Rails` field either.

The closure for a drag is the same heuristic the issue proposes for a move:

- Candidate children are placements in the same banc (and the companion, if the user includes that layer) whose category is object, effect, or a small `Land_` / `FldObj_` prop, and that have no explicit edge of their own.
- Keep those whose position lies inside the parent's world bounds expanded by a margin. The margin is a guess until someone measures a few houses. Start with the parent's rendered bounds plus a metre and a half, and show the set so it can be edited. Issue #11 already requires the grouping to be visible and overridable.
- Volumes, `StartPos`, NPCs, and enemies are not auto-children. They get explicit edges only.
- Standalone props stay draggable on their own, which is an acceptance criterion on #11.

Relative `Translate` is `drop - parent_old`, applied in the parent's local frame if the drop also rotates. Each child is its own added actor, with its own new hash. Undo removes the whole set.

Proximity will attach the wrong pot. That is why the badge exists, not a reason to skip the closure. A house dropped without this set is the bug #11 describes, moved from "gizmo" to "library".

### Collision edges (#10)

There are two collision nodes, and they are not interchangeable.

1. **Class shape, global.** `Phive/Shape/Dcc/<Gyaml>__Physical….bphsh`, actor-local triangles. `formats/phive.py` reads Havok SDK `20250200` `hknpMeshShape` only. The placement's `Translate`, `Rotate`, and `Scale` are what put that shape in the world. Copying the actor does not copy this file. Every instance of the class shares it. This is the collision a new, unbaked instance can use if the game instances shapes itself.

2. **Scene bake, per tile.** The running game, for a baked static, uses the compound under `Phive/StaticCompoundBody/<Scene>/` instead of the class shape. Issue #10 says moving the mesh in Forge leaves that bake at the old transform. The published reader in [oot-texture-tool](https://github.com/ToxicToucan/oot-texture-tool) (`phive_compound.py`, GPL-3.0) is the layout `formats/phive.py` reimplements. The program is not copied. Breath of the Wild's decomp looks up a compound actor by `HashId` **and** `SRTHash`, then a shape range (`getActorIdx` in [physStaticCompoundInfo.cpp](https://codeviewer.zeldamods.org/uking/uking/src/KingSystem/Physics/StaticCompound/physStaticCompoundInfo.cpp.html)). That is BOTW, an older SDK, and those offsets are not used. The EPD actor notes, which say to treat names as unofficial, give a PreActor a static-compound handle keyed by Phive `PlacementID` ([Engine::actor](https://epd.zeldamods.org/wiki/Engine::actor)). Together those say the bake is an index of placed instances, not a property of the gyaml. On this game the `0x20` actor record stores `SRTHash` at `+0x10`. `first == last == 0xFFFFFFFF` is a row with no body.

The #10 experiment was run on the Kokiri sign. Minting a new `SRTHash` does not drop the baked row. `collisionFollow: bake` moves that row, and the old spot is clear. A library **copy** is still open: the destination compound has no record for the new hash. Two unverified outcomes for that copy:

- The game instances the class shape at the new pose when the compound has no row. Collision works with no `.bphsc` edit. TotK's editor does not rely on this for `Bakeable` outdoor actors; it writes a new compound, and the tutorial says the prop has no collision until that file exists ([GameBanana](https://gamebanana.com/tuts/19424)). A TotK chest tutorial also says `Bakeable: true`, a unique `Hash`, the same value as `Phive.Placement.ID`, and a unique `SRTHash` ([chest tutorial](https://gamebanana.com/tuts/17511)).
- The game only collides statics that are in the destination compound. The mesh appears and the collision does not. `collisionFollow: bake` moves an instance that is already in the source scene. It does not invent a row in another area.

Until one of those is tried in game, the graph should still **record** a collision edge: `{gyaml, shape path or "convention miss", bakeable, source banc}`. A library copy should not emit the source `.bphsc`. Shipping the source tile would plant the source scene's world-space triangles into the wrong level.

`Bakeable: false` on the stone altar is the in-repo example of an authored unbaked prop. A library test should drop one `Bakeable: false` object and one `Bakeable: true` `Land_` object, into the same area and into a different area. That is the acceptance test for "collision came along".

## 5. Indexing every banc without converting every model

The data to search is placements, not packs. `/api/actors` already lists pack filenames and filters them in Python, capped at 400 (`ocarina_forge/server.py`). That is the class picker. It has no scene, no pose, and no thumbnail. The library is a second index.

**Build**

1. `Dump.list_scenes` is a directory walk of `Banc/**/*.bcett.byml.zs`. Run it once per dump.
2. Decompress and parse each banc the way `Dump.read_byml` already does (dictionary set cached on the dump, file bytes cached by path, size, and mtime, 512 MiB cap).
3. For each actor with a `Hash`, write a compact row: banc, companion banc if `sibling_bancs` finds one, gyaml, category (`actors.category`), hash, name, translate, `Bakeable` if present, whether `Links` is non-empty, whether `Presence` is present, and the actor's parameter key names. Do not store the whole `Dynamic` tree in the search index. Load that from the banc at drop time.
4. While streaming, union the root key names. That is how rails show up without a schema.
5. Persist under the existing user cache (`ocarina_forge.models.cache_dir`, `$OCARINA_FORGE_CACHE` or `~/.cache/ocarina-forge`). A sibling folder `library/` next to the model glbs keeps one cache root. Key the file by dump root plus a digest of `(relative path, size, mtime_ns)` for every banc, the same identity `read_file` uses. A changed banc rebuilds only its rows.
6. Do this off the request that opens the library, in the process pool the model converter already uses. The open scene does not wait on Hyrule Field.

247 files is the public count. Parsing them once is the expensive step, and it is BYML, not BFRES. Do not call `describe_actor` during the walk.

**Search**

- An inverted map of gyaml → row ids, plus prefix search on gyaml, `Name`, and area. Category filters reuse the prefix function, so the UI chips match the outliner.
- Page the results. The 400-name cap on `/api/actors` is too small for a library and too large to render as one list.
- A row is "in this scene" when its banc, or its companion, is the open file. Offer that as a filter so the user can still grab a Kokiri prop while standing in Kokiri.

**Thumbnails, reusing the model cache**

The glb cache is content-addressed: `cache_dir() / <digest[:2]> / <digest>.glb`, with the `_CACHE_VERSION` prefix in `ocarina_forge/models.py` (`models-54` at the time of this note) plus a hash of the converter sources. Bumping that prefix misses old glbs. The same gyaml always hits the same glb. Albedo images are already decoded to JPEG or PNG under that cache. `scene-preview-v2` is a whole-map glb. It is the wrong artifact for a card. `shared_models.py` calls `Image.thumbnail` to shrink shared textures, not to portrait an actor.

Use two levels:

1. **Immediate.** If `describe_actor(..., convert=False)` is a cache hit, the card can show the cached albedo (the first image the glb's sidecar names). A miss shows the category color and the gyaml. No convert on the index pass.
2. **On demand.** The first time a gyaml is shown in the visible page, enqueue the same convert the viewport uses (`GET /api/models/<Gyaml>`). When the glb exists, a later slice can render a small poster. Until that renderer exists, the albedo is the thumbnail. Unique gyamls on the page are the only converts. There are thousands of packs (8,373 in the public list) and far fewer that a search page shows.

Marker classes (`is_marker`: locators, tags, air walls) get no thumbnail request. They stay boxes in the viewport for the same reason.

Cache the poster beside the glb, keyed by the same digest, so a converter bump that misses the glb also misses the poster.

## 6. Phased plan

Scope is what has to be written, not a schedule. Each phase is independently testable. Later phases do not block browsing.

### Phase A — one clean copy

A command next to `add` and `duplicate`: source banc, source hash, destination banc, destination translate.

- Load the source actor, run it through `make_actor` with the destination pose, and store `state: added` on the destination diff. Preserve `Dynamic`, `Bakeable`, `Name`, and `Phive` except `Placement.ID`.
- Strip `Links` on this phase and record what was stripped. That is the stone-builder rule and it cannot dangle.
- Refuse a missing pack.
- Tests on the synthetic Kokiri and Field fixtures in `ocarina_forge/fixtures.py`: a tree copied from Kokiri Static into Field Static has a new hash, matching `Phive.Placement.ID`, a new `SRTHash`, the field translate, and no Kokiri link. **Build mod** writes the field banc. **Save Longshot mod** still skips it, and the test asserts that warning so the gap stays visible.
- In-game check, not runnable here: the rebuilt mod in Longshot, one `Obj_` with `Bakeable` false, same age, no links. Confirm the mesh. Then the same object with `Bakeable` true.

Risk: `duplicate`'s parameter copy is easy to reuse and will keep source links. The new command should not call `duplicate`.

### Phase B — library index and panel

The cache in §5, a search API that is not capped at 400, and a panel that lists rows (gyaml, area, category, badges from the compact fields). Drop calls Phase A at the cursor. Ground snap already exists; the drop uses the same hit point the gizmo snap uses.

Risk: the first index of a full dump is a few hundred zstd BYMLs. Stream them and cache by mtime so a second open does not repeat it. Do not hold every parsed document.

### Phase C — badges

The table in §3, computed at drop time from the full actor, not only from the compact row. `grouped` requires scanning the source group lists for one hash. `flag-gated` reuses the flag walker. `linked` reuses `_link_target`. `path-bound` is "this key exists", which Phase B's key census makes cheap.

Risk: flag names that are not really gates. Show the key and the string, and let the user drop anyway.

### Phase D — explicit closure

The link and stray-hash closure from §4, with include-or-drop, hash remapping, and one undo step for the set. Companion-banc members are written to the companion diff. The editor already keeps a diff per banc.

Risk: a stray u64 that is not an actor hash (a timer, a random seed) gets treated as a link. Only treat a value as an edge when it equals a real actor hash in the source folder, which is the stone builder's `others` set.

### Phase E — decoration closure (#11)

Proximity set, visible and editable, relative transforms, still one undo step. Use it both for a library drop and for a gizmo move, so #11 and #16 share one grouping.

Risk: wrong attachments. The override list has to round-trip in the project diff, or the next drop recomputes a different set. Store the chosen child hashes (the source hashes) on the drop operation, not a new implicit format in the banc.

### Phase F — collision experiment, then a patcher only if it fails (#10)

1. Move a baked `Land_` inside one scene by editing `Translate` and minting a new `SRTHash`. Load the mod. If collision is at the new pose, Forge's move path can mint `SRTHash` the way `make_actor` already does for adds. Today a move keeps `SRTHash` because the editor locks it (`scene.py` rejects edits to `Hash`, `SRTHash`, and `Placement.ID`).
2. Drop that same actor into another area with Phase A, without a `.bphsc`. If collision works, the library is done on this axis. If the mesh is solid only when `Bakeable` is false, document that and offer "place unbaked" as an explicit toggle that writes `Bakeable: false`. That toggle is a behavior change and needs the in-game result first.
3. If neither works, `collisionFollow: bake` is the fallback already in the exporter. It was written from the published reader, not from the BOTW struct. It was checked against the synthetic file in `tests/test_phive.py`. It was not checked against a retail tile, and it has not been run in game. A wrong compound is world collision, so a tile that does not match the layout fails the export.

Risk of phase F3: a wrong compound is world collision. Ship it only behind a test that round-trips one actor's hash to the same shape range and fails closed otherwise.

### Phase G — Longshot add op

Teach `placement_ops` to emit an add for `state: added`, including the parameter tree and the remapped links, and to raise the size table the way `export.py` does. `docs/longshot-mapping.md` already describes the spec side. Until this lands, the library's in-game path is **Build mod**, not **Save Longshot mod**.

## What this spike did not do

- No retail dump was opened. Counts, tile grids, and the 83% bake figure are from the issue or from public listings.
- The actor-table tail starts with `SRTHash` (u32 at record `+0x10`). `first == last == 0xFFFFFFFF` is a row with no body. Both are from the Kokiri in-game check in [formats.md](../formats.md). A hash in the table is not baked collision.
- No actor was loaded in game. The stone copy and the Saria cloner are the existing evidence, and the Saria cloner says it has not been seen in game.
- Rail, child-actor, and `ActorInfo` radius behavior are unverified for 1.1.1. The UI should detect keys it finds, not assume TotK's `Rails` key exists.

## Sources

- This repo: `ocarina_forge/actors.py`, `scene.py`, `export.py`, `modexport.py`, `validate.py`, `behavior.py`, `world_surface.py`, `formats/phive.py`, `dump.py`, `server.py`, `models.py`; `docs/formats.md`, `docs/models.md`, `docs/project-format.md`, `docs/longshot-mapping.md`; `vendor/after_ocarina/after_ocarina/stones.py`, `saria.py`.
- Issues [#10](https://github.com/glinesbdev/ocarina-forge/issues/10), [#11](https://github.com/glinesbdev/ocarina-forge/issues/11), [#16](https://github.com/glinesbdev/ocarina-forge/issues/16).
- [oot-remake-modding romfs.md](https://github.com/Kabosune/oot-remake-modding/blob/main/docs/romfs.md), [engine.md](https://github.com/Kabosune/oot-remake-modding/blob/main/docs/engine.md), [tuning.md](https://github.com/Kabosune/oot-remake-modding/blob/main/docs/tuning.md). The engine page says TotK knowledge carries over only after it is checked on this build.
- [oot-texture-tool](https://github.com/ToxicToucan/oot-texture-tool) `phive_compound.py` (GPL-3.0). The published layout of this game's static compound. Reimplemented, not copied. The level-index notes beside it are still prose.
- BOTW decomp, older engine: [physStaticCompoundInfo.h](https://codeviewer.zeldamods.org/uking/uking/src/KingSystem/Physics/StaticCompound/physStaticCompoundInfo.h.html), [physStaticCompoundInfo.cpp](https://codeviewer.zeldamods.org/uking/uking/src/KingSystem/Physics/StaticCompound/physStaticCompoundInfo.cpp.html).
- [EPD Engine::actor](https://epd.zeldamods.org/wiki/Engine::actor). Names unofficial unless the page says otherwise.
- TotK, not OoT: [custom structure tutorial](https://gamebanana.com/tuts/19424), [chest placement tutorial](https://gamebanana.com/tuts/17511), [event-flow file route](https://gamebanana.com/tuts/19875), [TotkRandomizer `Rails` strip](https://github.com/MelonSpeedruns/TotkRandomizer/blob/main/Form1.cs).

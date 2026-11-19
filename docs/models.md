# Actor models

The 3D view loads a mesh from the dump you point Bancwright at. Converted glTF is cached on your machine, outside this repo. The sample dump has a few synthetic meshes so the path can be tested with no Nintendo files. A real dump is still required for real actors; the checklist is [testing-on-real-dump.md](testing-on-real-dump.md).

## What gets drawn

For each actor name (`Gyaml`):

1. Open `Pack/Actor/<Gyaml>.pack.zs` (zstd dictionary 4) and read every `Component/ModelInfo/*.engine__component__ModelInfo.bgyml`. Each one, including entries under `SubModels`, has `ModelProjectName` and `FmdbName`.
2. Try `Model/<Project>.<Fmdb>.bfres.zs` (dictionary 1), then the uncompressed `.bfres`. When `FmdbName` is missing, the project name is used for both: `Model/<Project>.<Project>.bfres.zs`.
3. If that exact file is missing, try the other `Model/<Project>.*.bfres.zs` files, skipping `*.anim.bfres.zs`. When the exact file is on disk and its shapes are FSHP flag `0x10` (no decodable vertex buffer), the actor stays a box. Another fmdb in the same project is not drawn in its place. `Land_Kokiri_House_Outside_C_00` used to pick up `Land_Kokiri_House_Inside_C_03`.
4. Then `Model/<Gyaml>.<Gyaml>.bfres.zs`.
5. Older layouts still work: a `.bfres` stored inside the pack, a `Model/<name>.bfres` string in the pack, and `Model/<Gyaml>.bfres.zs`.

The first file that converts is the one shown. The same glTF is reused for every instance of that actor. Three downloads run at a time. Until one arrives, the actor is the labeled box. A static map is requested after the actors, and it is not part of picking, so a few hundred thousand triangles do not sit on the click path.

A scene draws a static map, not pickable, from the first file that exists:

- `Model/<Area>_NN.<Area>_NN.bfres.zs` (Kokiri Forest child is `Model/KokiriForest_01.KokiriForest_01.bfres.zs`)
- `Model/<Scene>.<Scene>.bfres.zs`
- the older single names `Model/<Scene>.bfres.zs`, `Model/<Area>.bfres.zs`, `Model/<Area>_Static.bfres.zs`
- the same stems under `Terrain/` and `Map/`

No map file means no extra object. It does not add a box.

## What is decoded

Meshes and one base color. Vertices stay in bind pose. A shape with a skin count of zero is moved by its single bone. A skinned shape is already stored in model space, so it is drawn as stored. Animation is not read.

The base color is the albedo sampler. On a version-10 material that names its samplers, that is `_a0`, then `_a1` or `_a2` if `_a0` is missing. A normal, roughness, bake, or runtime slot (`_n0`, `_rgh0`, `bake0`, `tc`, `ta`, `MaterialAlb`, `MaterialCmb`) is not used as the color, and the string pool is not a fallback once any sampler is named. If the sampler names are missing (the version-9 samples), a texture name that looks like an albedo (`_Alb`, `Albedo`, `Diffuse`) wins over `_Nrm`, and a file with no material texture can still pick one albedo-like name out of the string pool. Real textures are not inside the bfres. The viewer looks up `Tex/<TextureName>.bntx.zs` (dictionary 1), then the same stem with or without an `_Alb` suffix, then an image of that name inside a `.bntx` that holds several images. It does not substitute a different image from that file. Lookup is case-insensitive. An embedded BNTX is still used when the bfres has one.

`Model/KokiriForest_01.KokiriForest_01.bfres.zs` in the retail dump is a dummy: one material, no textures, positions only. The real ground is `TerrainArc/` (not read yet). That mesh is drawn flat-shaded, with normals computed from the faces and an earth-green tint, and the reason says `terrain proxy`. It is not looked up in `Tex/`. A static map that does name an albedo still uses that texture.

Tile mode and block compression (BC1–BC7, ASTC, RGBA8) are decoded on the server to PNG and packed into a glTF binary. The block height is the low 3 bits of BRTI+0x34. If that value’s padded surface does not match the mip-table size, the size picks the height. ASTC ids `0x37`–`0x3A` are 10×8, 10×10, 12×10, and 12×12. A BC1 block with color0 ≤ color1 treats index 3 as transparent, which is the sky on the FarView cards. An all-zero ASTC block on a last GOB row that is not a multiple of 8 is replaced with the block above. A failed decode, an unsupported format, or a solid magenta image is not bound: that part is a flat colour (the material’s colour parameter, or a neutral grey) and it does not reuse another part’s texture. Magenta texels in an otherwise real image are punched out and the part is drawn as cutout.

Version-10 alpha comes from the render-state string array. The u16 at FMAT `+0xA0` is 0 or 1 and is not that array’s length; the reader walks the 31 slots at the `+0x40` pointer and stops on a null or a non-string. Index 11 `mask` (or index 15 `true`) is glTF `MASK`, `translucent` is `BLEND`, and index 10 `both` is double-sided while `front` is single-sided. Version-9 files still use the named `gsys_*` render info. The view turns on alpha-to-coverage for a mask. An opaque shape that shares its positions with a masked twin (FarView and FarView_Shaking) is dropped, so the opaque copy does not paint the punched sky black. `scripts/check_models.py` prints one block per material and says `textured` or `flat` for that material, then the shape names. A flat material also prints `flat fallback`. The summary counts shapes that received an image and shapes that used the flat colour. It does not call an actor textured because one of its materials was. The block still lists the sampler names, the texture each one references, which one was used as the albedo, the `Tex/` path, whether it decoded (format, size, swizzle, block height), the alpha mode, and the render-state word. A missing `Tex/` file (`Cloth_Kokiri_Interior_A_Alb`, `Tree_Kokiri_GraphicVine_A_01_Alb`, `Rock_Kokiri_FieldProp_J_Alb`) and a bake-only or water-only material stay flat.

## Build-time actors

An After the Ocarina recipe that places an actor (the `stones:` list, or any other scene YAML row with a gyaml, a translate, and a banc) is drawn on that map before you build. It keeps the violet tint and the label “added by build, flag-gated” when the file names a flag. It is editable: the gizmo and the properties panel write `translate`, `rotate`, `scale`, and `params` back into that YAML (comments kept), and the build uses those values. Y in the file is the gizmo Y minus `y_offset`, so the offset is not applied twice. Hide and Delete ask, then remove the list entry. Duplicate adds a copy one metre along X. Undo, the Changes tab, and Revert use a snapshot taken the first time that file is edited (`.bancwright/recipe-baseline.json`). Saria’s file leaves `place: false` and does not add a ghost.

Areas, fog, triggers, sensors, collision, and anything else that is not a real mesh and is 8 metres or larger on an axis are drawn as an edge outline with a very faint fill. The fill does not write depth, so it cannot hide the meshes inside it. The outliner has a Volumes chip that hides every outline at once, plus a chip per category. The stage-bar Show / Lock / Hide for Areas and System is unchanged.

The cache key is a hash of the source bytes plus a version prefix (`models-6`). Older glTF in that folder is left unused. Files live in `$BANCWRIGHT_CACHE`, or `$XDG_CACHE_HOME/bancwright/models`, or `~/.cache/bancwright/models`.

## Companion bancs

Opening a scene also loads its sibling: `_Static` with `_Dynamic`, same folder and the same age. The scene name in the bar gains “with Dynamic” or “with Static”. Those actors are drawn slightly dimmed, on by default. **Companion banc** hides them. **Pick companions** (on by default) lets you select them; off, they stay visible and clicks pass through. Selecting one switches the banc that Move, Rotate, and the properties panel write. The file you opened stays the host. Undo puts both diffs back.

Kokiri Forest child is the case this is for. The houses are in `KokiriForest_Child_Dynamic` (42 of 43). The NPCs are in Static, with their feet near y = 117, which is the floor of those houses. Static alone looks like the characters are floating.

`GET /api/models/<Gyaml>` returns `status` (`mesh` or `box`), `reason`, `source`, `triangles`, and `textures`. `GET /api/models/<Gyaml>/model.glb` is the file. Terrain uses `/api/terrain?banc=` and `/api/terrain/model.glb`.

## What stays a box

The properties panel shows the reason.

- No pack, and no `Model/<Project>.<Fmdb>.bfres.zs` or `Model/<Gyaml>.<Gyaml>.bfres.zs`. The properties panel lists the paths it tried.
- The pack is not a SARC, or the bfres is truncated or not a Switch `FRES` file.
- `.bfres.mc` (mesh codec). This viewer does not decode that stream.
- FSHP flag `0x10` (flags word `0x12`). The vertex buffer is not an FVTX and the mesh count is 0. The box uses the shape’s center and extent. The reason is `geometry format not decoded (FSHP flag 0x10)`. The layout after the GPU buffer is not documented as a meshlet or `.bfres.mc` block, so it is not decoded.
- The GPU buffer is an external section past the end of the file (the HasExternalGPU flag).
- A vertex format this reader does not know.
- The glTF request failed in the browser. The box comes back and the reason is the load error.

## Picking

Areas, system actors (`StartPos`, `Player`, `Sound`, `AirWall`, `Ground`, names with `Anchor`, names ending in `Actor`), names containing `Trigger`, and land actors scaled to 8 metres or more on any axis are volumes. They are drawn as a faint box with edges. A ray prefers the closest solid (a real mesh, or an ordinary box). A volume is selected when the click is on its edge, or when the ray hits nothing else. Alt-click, or another click on the same stack, cycles. **Areas** and **System** can be shown, locked (visible, not pickable), or hidden. A real mesh on a volume actor is picked like any other mesh.

## Sources

The reader in `bancwright/formats/bfres.py` and `bntx.py` is original. Field order and the Switch vertex-format ids follow [KillzXGaming/BfresLibrary](https://github.com/KillzXGaming/BfresLibrary) (MIT), checked against the public Wexos BFRES and BNTX notes. Block compression uses [texture2ddecoder](https://pypi.org/project/texture2ddecoder/) (MIT). The Tegra block-linear swizzle in `bancwright/formats/swizzle.py` is an original implementation of the GOB layout in the Tegra X1 TRM. Switch Toolbox and AboodXD’s BNTX tools were not copied; their swizzle code is GPL.

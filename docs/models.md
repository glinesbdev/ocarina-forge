# Actor models

The 3D view loads a mesh from the dump you point Bancwright at. Converted glTF is cached on your machine, outside this repo. The sample dump has a few synthetic meshes so the path can be tested with no Nintendo files. A real dump is still required for real actors; the checklist is [testing-on-real-dump.md](testing-on-real-dump.md).

## What gets drawn

For each actor name (`Gyaml`):

1. Open `Pack/Actor/<Gyaml>.pack.zs` (zstd dictionary 4) and read every `Component/ModelInfo/*.engine__component__ModelInfo.bgyml`. Each one, including entries under `SubModels`, has `ModelProjectName` and `FmdbName`.
2. Try `Model/<Project>.<Fmdb>.bfres.zs` (dictionary 1), then the uncompressed `.bfres`. When `FmdbName` is missing, the project name is used for both: `Model/<Project>.<Project>.bfres.zs`.
3. If that file is missing, try the other `Model/<Project>.*.bfres.zs` files, skipping `*.anim.bfres.zs`.
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

The base color is the albedo sampler, `_a0` when the material has one, even if that sampler is not first. A normal, specular, or blend slot (`_n0`, `_s0`, `_b0`) is not used as the color. If the sampler names are missing, a texture name that looks like an albedo (`_Alb`, `Albedo`, `Diffuse`) wins over `_Nrm`. Real textures are not inside the bfres. The viewer looks up `Tex/<TextureName>.bntx.zs` (dictionary 1), then the same stem with or without an `_Alb` suffix, then an image of that name inside a `.bntx` that holds several images. It does not substitute a different image from that file. Lookup is case-insensitive. An embedded BNTX is still used when the bfres has one. The static map, if it has no albedo name, also tries a `Tex/` file whose name contains the area and `Alb`, and uses that as the one ground colour.

Tile mode and block compression (BC1–BC7, ASTC, RGBA8) are decoded on the server to PNG and packed into a glTF binary. A failed decode, an unsupported format, or a solid magenta image is not bound: that part is a flat colour (the material’s colour parameter, or a neutral grey) and it does not reuse another part’s texture. Magenta texels in an otherwise real image are punched out and the part is drawn as cutout. Alpha test in the material (`gsys_alpha_test_enable`, render state `mask`) becomes glTF `MASK` with the material’s cutoff, and the view turns on alpha-to-coverage. A translucent render state becomes `BLEND`. `gsys_render_state_display_face` of front or back is single-sided; both sides stay double-sided, which is also the default when the file does not say. `scripts/check_models.py` prints one block per material: the sampler names, the texture each one references, which one was used as the albedo, the `Tex/` path, whether it decoded (format, size, swizzle), and the alpha mode.

## Build-time actors

An After the Ocarina recipe that places an actor (the `stones:` list, or any other scene YAML row with a gyaml, a translate, and a banc) is drawn on that map before you build. It keeps the violet tint and the label “added by build, flag-gated” when the file names a flag. It is editable: the gizmo and the properties panel write `translate`, `rotate`, `scale`, and `params` back into that YAML (comments kept), and the build uses those values. Y in the file is the gizmo Y minus `y_offset`, so the offset is not applied twice. Hide and Delete ask, then remove the list entry. Duplicate adds a copy one metre along X. Undo, the Changes tab, and Revert use a snapshot taken the first time that file is edited (`.bancwright/recipe-baseline.json`). Saria’s file leaves `place: false` and does not add a ghost.

Areas, fog, triggers, sensors, collision, and anything else that is not a real mesh and is 8 metres or larger on an axis are drawn as an edge outline with a very faint fill. The fill does not write depth, so it cannot hide the meshes inside it. The outliner has a Volumes chip that hides every outline at once, plus a chip per category. The stage-bar Show / Lock / Hide for Areas and System is unchanged.

The cache key is a hash of the source bytes plus a version prefix. Files live in `$BANCWRIGHT_CACHE`, or `$XDG_CACHE_HOME/bancwright/models`, or `~/.cache/bancwright/models`.

`GET /api/models/<Gyaml>` returns `status` (`mesh` or `box`), `reason`, `source`, `triangles`, and `textures`. `GET /api/models/<Gyaml>/model.glb` is the file. Terrain uses `/api/terrain?banc=` and `/api/terrain/model.glb`.

## What stays a box

The properties panel shows the reason.

- No pack, and no `Model/<Project>.<Fmdb>.bfres.zs` or `Model/<Gyaml>.<Gyaml>.bfres.zs`. The properties panel lists the paths it tried.
- The pack is not a SARC, or the bfres is truncated or not a Switch `FRES` file.
- `.bfres.mc` (mesh codec). This viewer does not decode that stream.
- The GPU buffer is an external section past the end of the file (the HasExternalGPU flag).
- A vertex format this reader does not know.
- The glTF request failed in the browser. The box comes back and the reason is the load error.

## Picking

Areas, system actors (`StartPos`, `Player`, `Sound`, `AirWall`, `Ground`, names with `Anchor`, names ending in `Actor`), names containing `Trigger`, and land actors scaled to 8 metres or more on any axis are volumes. They are drawn as a faint box with edges. A ray prefers the closest solid (a real mesh, or an ordinary box). A volume is selected when the click is on its edge, or when the ray hits nothing else. Alt-click, or another click on the same stack, cycles. **Areas** and **System** can be shown, locked (visible, not pickable), or hidden. A real mesh on a volume actor is picked like any other mesh.

## Sources

The reader in `bancwright/formats/bfres.py` and `bntx.py` is original. Field order and the Switch vertex-format ids follow [KillzXGaming/BfresLibrary](https://github.com/KillzXGaming/BfresLibrary) (MIT), checked against the public Wexos BFRES and BNTX notes. Block compression uses [texture2ddecoder](https://pypi.org/project/texture2ddecoder/) (MIT). The Tegra block-linear swizzle in `bancwright/formats/swizzle.py` is an original implementation of the GOB layout in the Tegra X1 TRM. Switch Toolbox and AboodXD’s BNTX tools were not copied; their swizzle code is GPL.

# Actor models

The 3D view loads a mesh from the dump you point Bancwright at. Converted glTF is cached on your machine, outside this repo. The sample dump has a few synthetic meshes so the path can be tested with no Nintendo files. A real dump is still required for real actors; the checklist is [testing-on-real-dump.md](testing-on-real-dump.md).

## What gets drawn

For each actor name (`Gyaml`):

1. Open `Pack/Actor/<Gyaml>.pack.zs` (zstd dictionary 4) and look for a `.bfres` in the SARC. A file whose name contains the actor name is preferred.
2. Otherwise read strings in that pack. `ModelProjectName` and a `Model/<name>.bfres` path become `Model/<name>.bfres.zs` (dictionary 1), then the uncompressed `.bfres`.
3. Otherwise try `Model/<Gyaml>.bfres.zs`.

The first file that converts is the one shown. The same glTF is reused for every instance of that actor. Three downloads run at a time. Until one arrives, the actor is the labeled box.

A scene can also draw a static map, not pickable, from the first file that exists:

- `Model/<Scene>.bfres.zs`
- `Model/<Area>.bfres.zs`
- `Model/<Area>_Static.bfres.zs` and `Model/<Scene>_Static.bfres.zs`
- the same stems under `Terrain/` and `Map/`

No map file means no extra object. It does not add a box.

## What is decoded

Meshes and one base color. Vertices stay in bind pose. A shape with a skin count of zero is moved by its single bone. A skinned shape is already stored in model space, so it is drawn as stored. Animation is not read.

Textures are the embedded BNTX. Tile mode and block compression (BC1–BC7, ASTC, RGBA8) are decoded on the server to PNG and packed into a glTF binary. A mesh whose texture cannot be decoded is still drawn, untextured, and the properties panel says so.

The cache key is a hash of the source bytes plus a version prefix. Files live in `$BANCWRIGHT_CACHE`, or `$XDG_CACHE_HOME/bancwright/models`, or `~/.cache/bancwright/models`.

`GET /api/models/<Gyaml>` returns `status` (`mesh` or `box`), `reason`, `source`, `triangles`, and `textures`. `GET /api/models/<Gyaml>/model.glb` is the file. Terrain uses `/api/terrain?banc=` and `/api/terrain/model.glb`.

## What stays a box

The properties panel shows the reason.

- No pack and no `Model/<Gyaml>.bfres.zs`.
- The pack is not a SARC, or the bfres is truncated or not a Switch `FRES` file.
- `.bfres.mc` (mesh codec). This viewer does not decode that stream.
- The GPU buffer is an external section past the end of the file (the HasExternalGPU flag).
- A vertex format this reader does not know.
- The glTF request failed in the browser. The box comes back and the reason is the load error.

## Picking

Areas, system actors (`StartPos`, `Player`, `Sound`, `AirWall`, `Ground`, names with `Anchor`, names ending in `Actor`), names containing `Trigger`, and land actors scaled to 8 metres or more on any axis are volumes. They are drawn as a faint box with edges. A ray prefers the closest solid (a real mesh, or an ordinary box). A volume is selected when the click is on its edge, or when the ray hits nothing else. Alt-click, or another click on the same stack, cycles. **Areas** and **System** can be shown, locked (visible, not pickable), or hidden. A real mesh on a volume actor is picked like any other mesh.

## Sources

The reader in `bancwright/formats/bfres.py` and `bntx.py` is original. Field order and the Switch vertex-format ids follow [KillzXGaming/BfresLibrary](https://github.com/KillzXGaming/BfresLibrary) (MIT), checked against the public Wexos BFRES and BNTX notes. Block compression uses [texture2ddecoder](https://pypi.org/project/texture2ddecoder/) (MIT). The Tegra block-linear swizzle in `bancwright/formats/swizzle.py` is an original implementation of the GOB layout in the Tegra X1 TRM. Switch Toolbox and AboodXD’s BNTX tools were not copied; their swizzle code is GPL.

# Notices for the Godot spike

The readers in `Forge.Import` are a port of this repo's Python (`ocarina_forge/formats/`, `ocarina_forge/models.py`, `ocarina_forge/actors.py`). They are not a copy of another project's source.

People and libraries this work depends on or follows:

- **Hyblitze** and **Zirem** — the field, actor, and format notes this repo already documents and this port follows.
- **NupExplorer** (lunixxx37) — the published meshlet bit layout. The decoder is a port of `ocarina_forge/formats/meshlet.py`, which is an original implementation of those notes. NupExplorer's source is not copied. https://github.com/lunixxx37/NupExplorer
- **KaboWabo / Kabosune** — terrain and texture notes already recorded in this repo's format docs.
- **BfresLibrary** (KillzXGaming / Syroot) — the vertex-format id list (big-endian ushorts such as `_F32_3` = `0x0518`), the public Switch FSKA header for major 9 and 10 (flags, absolute offsets, curve element counts), and the FSKL matrix-list order (smooth entries first, the u16 at `+0x3A`, then rigid entries, the u16 at `+0x3C`). This spike does not link or copy that library. The version-10 model header walk is the one in this repo's Python. The FSKA reader follows that published header and the documented curve evaluation (value, out-slope, in-slope); it was checked with a hand-built clip, not a retail file. https://github.com/KillzXGaming/BfresLibrary
- **Switch Toolbox** (KillzXGaming) — reference for how a displayed mesh indexes that combined matrix list, and for a rigid vertex storing an index into the rigid half. No code from it is included. https://github.com/KillzXGaming/Switch-Toolbox
- **NuxsGit** — reference only. No code from it is included.
- **ZstdSharp.Port 0.8.5** (Oleg Stepanischev) — MIT. Used to decompress zstd frames and dictionary packs. https://github.com/oleg-st/ZstdSharp
- **zstd** (Meta / Facebook) — the format ZstdSharp ports. BSD license, https://github.com/facebook/zstd
- **Godot Engine 4.7.2** — MIT. https://godotengine.org
- **AssetRipper.TextureDecoder 2.6.3** (ds5678) — MIT. Used for ASTC footprints outside the Godot CPU image decoder. https://github.com/AssetRipper/AssetRipper.TextureDecoder

No Nintendo titles, characters, or game files are in this folder. Import caches, `.godot/` output, and a project produced by `ocarina-forge-godot new` stay on the machine that owns the dump. The generator does not copy the dump into the repository.

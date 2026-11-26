# Textures

BNTX images are decoded in `ocarina_forge/formats/bntx.py`. The block-linear address is `ocarina_forge/formats/swizzle.py`. This note is the Switch 2 GOB order that the castle brick walls needed.

## What stayed scrambled

`textureLayout` (BRTI `+0x34`) is the block height, and ASTC 8×5 is 8 pixels wide and 5 tall. That part was already right. `Land_HrlCastle_Wall_Brick_A_Lighter_Alb` is 1024×1024 ASTC 8×5, block height 1, mip 0 of 425984 bytes. `Land_HrlCastle_Wall_Brick_Blend_A_Alb` is ASTC 6×6, block height 1, mip 0 of 484352 bytes. The PNGs were still broken: brick seams ran in zigzags, and rectangles of tiles sat 8–16 pixels off. The mean colour was fine.

The same mip-0 blocks, decoded with ARM's reference astcenc and with texture2ddecoder, showed the same seams. The colour decoder is not the fault. The bytes are in the wrong GOB slots.

## How the order was found

A GOB is 64 bytes by 8 rows, 512 bytes. Above the 16 bytes of one element, the Tegra X1 order is y0 at 16, x0 at 32, y1 at 64, y2 at 128, x1 at 256. A search over that bit order, scored by the mean absolute difference of samples four pixels apart (the same edge score as the decoder's noise check), found one arrangement that lays both castle albedos down as straight bricks and plaster:

| Texture | Footprint | Blocks | Tegra score | Swapped score |
| --- | --- | --- | --- | --- |
| `Land_HrlCastle_Wall_Brick_A_Lighter_Alb` | 8×5 | 128×208 | 12.10 | 11.23 |
| `Land_HrlCastle_Wall_Brick_Blend_A_Alb` | 6×6 | 172×176, pitched | 10.27 | 9.11 |

The gap is about a point because a brick wall is full of real edges. It is enough to rank the two orders, and the picture changes from zigzag seams to clean courses. The decoder's "much smoother" retry wants a drop of ten points and a score under half, so it does not fire on these. The order is chosen from the element size, not from the score.

## Bit 5 is y1, bit 6 is x0

The order that won swaps bit 5 and bit 6. y0 stays at 16, y2 stays at 128, and x1 stays at 256. y1 moves from 64 to 32. x0 moves from 32 to 64. `((y % 8) // 2) % 2` is the 32 term. `((xx % 32) // 16)` is the 64 term. `xx` is the byte column, `x * bytes_per`.

Tegra X1, inside one GOB:

```text
(x%64/32)*256 + (y%8/2)*64 + (x%32/16)*32 + (y%2)*16 + x%16
```

Switch 2, the 16-byte pieces of that same GOB:

```text
(x%64/32)*256 + (y%8/4)*128 + (x%32/16)*64 + (y%4/2)*32 + (y%2)*16 + x%16
```

Block height stays the `textureLayout` value. Both castle albedos are log2 0, so the GOB stack is one GOB tall. `swizzle` and `deswizzle` use the same address, so a round trip and an export write the order the reader used.

A block at element `(0, 2)` is byte 32 in this order and byte 64 in the Tegra order. A block at `(1, 0)` is byte 64 here and byte 32 on Tegra. When both of those bits are set, `(1, 2)`, both orders land on byte 96.

## Which formats

16-byte elements use the Switch 2 order:

- ASTC, every footprint, including 8×5, 6×6, and 10×5
- BC2, BC3, BC5, BC6, BC7

8-byte elements stay on Tegra X1: BC1 and BC4. 4-byte elements stay on Tegra X1: RGBA8 and the height samples. Those already decode cleanly. The two formulas put a 16-byte slot in different places whenever the y1 bit and the 16-byte x bit disagree, so the swap would move BC1 blocks and scramble the stone that already reads correctly.

The header does not select the order. The castle albedos share this BRTI prefix at `+0x10`:

```text
09 02 00 00 00 00 0b/0a 00 01 00 00 00 06 3x 00 00
```

| Offset | Field | On those albedos |
| --- | --- | --- |
| `+0x10` | flags byte | `0x09` |
| `+0x11` | dimension | `0x02` (2D) |
| `+0x12` | tile mode | 0, block linear |
| `+0x14` | swizzle u16 | `0x0000` |
| `+0x16` | mip count | `0x0A` or `0x0B` |
| `+0x18` | following u32 | 1 |
| `+0x1C` | format | `0x3x06` (sRGB ASTC) |
| `+0x34` | `textureLayout` | `0x00000000`, block height log2 0 |
| `+0x38` | `sizeRange` | `0x000100C9` |

The swizzle field is 0, so it is not a pattern id. The flags byte is `0x09` on these files and is not read as a switch. `tex-debug` prints both next to `gob switch2` or `gob tegra`.

## What tex-audit reports

`python -m ocarina_forge tex-audit` still lists every texture whose mip-0 size implies a different block height than the usual one. It also decodes mip 0 of each image twice, once in each GOB order, and prints both edge scores. The lower score is `smoother`. A tie is an equal score: a flat colour, a linear tile, or an image under 32 pixels on a side, where the score is 0 either way.

The viewer ignores that ranking. A 16-byte element is Switch 2. A line marked `disagree` is a texture whose smoother order is not that rule. That is the check for an 8-byte image that wants the swap, or a 16-byte image that wants Tegra, including one whose flags byte is not `0x09`. The summary counts `switch2`, `tegra`, `tie`, `unscored`, and `disagree`.

## Cache

The glb prefix is `models-62`. Decoded BNTX images are keyed with `bntx-6`, so an old `bntx/*.bwtx` is not reused. A `bntx-6` file also stores the linear blocks. Startup pruning removes entries whose recorded version is not current; entries with no version stay until the size cap evicts them. Albedo in a packed glb is a KTX2 of those blocks (BC1, BC3, or BC7; ASTC is re-encoded to BC7) plus a JPEG or PNG fallback. The editor's geometry glb leaves those images out. They are files in the shared texture store, named by `GET /api/models/<Gyaml>/textures`, and the manifest is cached under `texman/`. See [performance.md](performance.md) and [formats.md](formats.md).

## Check on a dump

From the repo, with the venv active and the dump that contains `romfs`:

```bash
.venv/bin/python -m ocarina_forge tex-debug Land_HrlCastle_Wall_Brick_A_Lighter_Alb --png /tmp/lighter.png --dump ~/Games/Longshot/rc/v3.0.0
.venv/bin/python -m ocarina_forge tex-debug Land_HrlCastle_Wall_Brick_Blend_A_Alb --png /tmp/blend.png --dump ~/Games/Longshot/rc/v3.0.0
.venv/bin/python -m ocarina_forge tex-audit --dump ~/Games/Longshot/rc/v3.0.0
```

`Lighter_Alb` should say `1024x1024`, footprint `8x5`, block height 1, `gob switch2`, flags `0x09`, swizzle `0x0000`. `Blend_A_Alb` should say footprint `6x6` and the same gob order. The PNGs should be straight bricks and plaster, not zigzag seams. In `tex-audit`, those two names should show `smoother switch2` with a lower switch2 score. A BC1 or BC4 name should say `gob tegra`. A `disagree` line is the one to paste back.

## WebGPU compressed uploads

On WebGPU, packed OCFG/KTX2 maps upload as BC7 or BC1 when the adapter has `texture-compression-bc`. Caps come from `renderer.backend.hasFeature`, not from the canvas context's WebGL extensions. three r170's missing BC7 `getFormat` case is patched at init and by a Vite plugin. See `docs/rendering-performance.md`.

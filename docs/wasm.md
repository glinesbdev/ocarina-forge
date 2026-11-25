# WebAssembly prototype

This branch tests whether two hot Forge paths should run in the browser as
WebAssembly, including a SharedArrayBuffer worker pool.

It does not replace the Python server. The work lives in a separate
`forge-wasm` worktree so the `forge-height-check` dev server on ports
47231/47232 can keep running.

## What moved

1. **Walkable-surface sampling** (`MeshIndex`, `triangle_height`,
   `compose_height`). This is the Snap / height-diff path from
   `ocarina_forge/world_surface.py`. The heightmap plus Land_* island,
   cliff, and hill meshes are sampled under a grid of XZ points. The same
   Rust lives in `native/src/surface.rs` and is exposed to the page
   through `native/src/wasm.rs`.
2. **Texture deswizzle + decode** (`decode_swizzle`). This already compiled
   for `wasm32-unknown-unknown`. The bench runs it on a 1024×1024 synthetic
   BC7 surface that uses the same swizzle path as a real BNTX mip.

The current JS and Python copies stay in place. The bench compares them.
Every wasm height and every decoded texel matched the Python baseline
exactly (`max |error| = 0`).

Threading is a JavaScript worker pool, not `wasm-bindgen-rayon`. Each
worker instantiates the wasm module, claims a slice of the point list
with `Atomics.add` on a SharedArrayBuffer, and writes heights back into
that buffer. That needs `crossOriginIsolated`, not a nightly
`build-std` toolchain.

## Headers

`frontend/vite.config.js` and `tools/wasm-bench/vite.config.js` send:

- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Embedder-Policy: credentialless`
- `Cross-Origin-Resource-Policy: same-origin`

`credentialless` is enough for `crossOriginIsolated === true` in Chromium
and is gentler than `require-corp` on future cross-origin assets. The
basis transcoder middleware also sends `CORP: same-origin`. `/api` stays
same-origin through the Vite proxy, so model and texture fetches do not
need a second origin.

Do not start the main UI from this worktree on 47231 while the
height-check server is still bound there. Use the bench on **47241**.
The header change in the main Vite config was not live-verified against
the running height-check server for that reason. The bench page confirmed
`crossOriginIsolated === true` and SharedArrayBuffer was available.

## Results

Measured Wednesday, 25 November 2026, 12:49 AM America/New_York, on this
Windows machine, in headless Chrome 158, 24 logical cores. Fixtures came
from `E:\sw2\oot\stable\ray-test`: a 2 m XZ grid over Lake Hylia
(400 m half-extent around the island tree at −192.39, 506.95) and over
Kokiri Forest from −180 to 180. Times are the median of three runs after
the wasm module has loaded. Python times are from the export script on
the same machine, through the existing Rust Python extension where that
path already uses it. JS heap (Chrome `performance.memory`) went from
about 1 MB at start to 80 MB at the end. The gitignored fixtures are
64.4 MB, 26.9 MB, and 5.2 MB on disk.

### Hyrule Field (Child) / Lake Hylia

841,138 Land_* triangles, 160,801 sample points, 45,871 hits.

| Path | Time | Throughput | Notes |
| --- | ---: | ---: | --- |
| Python index build | 2,196 ms | — | MeshIndex in CPython |
| JS index build | 73 ms | — | |
| wasm index build | 74 ms | — | |
| Python sample | 82,239 ms | 1,955 points/s | current server path |
| JS sample | 2,420 ms | 66,436 points/s | 34× vs Python |
| wasm single-thread sample | 1,817 ms | 88,490 points/s | 45× vs Python, 1.3× vs JS |
| wasm 8-thread SAB sample | 289 ms | 555,876 points/s | 284× vs Python, 6.3× vs wasm ST |

### Kokiri Forest (Child)

362,527 triangles, 32,761 points, 9,348 hits.

| Path | Time | Throughput | Notes |
| --- | ---: | ---: | --- |
| Python index build | 659 ms | — | |
| JS index build | 29 ms | — | |
| wasm index build | 24 ms | — | |
| Python sample | 38,932 ms | 841 points/s | current server path |
| JS sample | 1,336 ms | 24,521 points/s | 29× vs Python |
| wasm single-thread sample | 998 ms | 32,839 points/s | 39× vs Python, 1.3× vs JS |
| wasm 8-thread SAB sample | 307 ms | 106,797 points/s | 127× vs Python, 3.3× vs wasm ST |

Kokiri has fewer points, so the eight workers have less work to split
and the speedup over single-thread wasm is smaller.

### Texture decode

Synthetic 1024×1024 BC7, swizzled with the same Tegra/Switch path as a
BNTX mip. The Python number is already the Rust extension through PyO3.

| Path | Time |
| --- | ---: |
| Python `decode_swizzle` | 17.01 ms |
| wasm `decodeSwizzle` | 11.17 ms |

## Does SharedArrayBuffer threading help?

Yes, on the surface sampler. Eight workers were about 6.3× faster than
single-thread wasm on the Lake Hylia grid and 3.3× faster on Kokiri.
That is the difference between a 1.8 s query and a 0.3 s query for
160 k points. A live Snap drag only needs one point at a time, so the
win there is mainly “run it in the tab, no `/api/ground` round trip,”
with the worker pool ready for height-diff grids and any bulk query.
Texture decode is already a short single-thread job; a worker pool
would only matter for a stack of large mips.

Index build is similar in JS and wasm (the triangles still have to be
copied into the module). Both are far ahead of CPython.

## Blockers

- **wasm-pack 0.13.1 and Rust 1.99.** `wasm-pack build --out-dir` passes
  `--out-dir` to `cargo build`, which 1.99 rejects. The bench script
  uses `cargo build --target wasm32-unknown-unknown` plus
  `wasm-bindgen-cli` 0.2.130 instead. Stable Rust is enough. Nightly
  and `-Z build-std` were not required.
- **three.js / WebGPU.** Not exercised here. The sampler is CPU-side
  and does not touch a GPU buffer. A later viewer hook would still use
  the existing snap path, then write the returned Y onto the actor.
- **Main UI headers.** COOP/COEP are in `frontend/vite.config.js` for
  when this is integrated, but they were confirmed only on the bench
  server. Restarting the height-check UI is what would prove that the
  basis transcoder and `/api` still load under those headers.
- Headless Chrome logged one 404 during page load (most likely the
  missing favicon). It did not affect the numbers.

## How to run the bench

From `C:\Users\gline\src\forge-wasm`, with the height-check server left
alone:

```bat
scripts\wasm-bench.bat
```

That script exports fixtures from `E:\sw2\oot\stable\ray-test` if they
are missing, builds the wasm module, and opens headless Chrome against
`http://127.0.0.1:47241`. To watch it in a window instead:

```bat
cd C:\Users\gline\src\forge-wasm
cargo build --manifest-path native\Cargo.toml --release --target wasm32-unknown-unknown --no-default-features --features wasm
wasm-bindgen native\target\wasm32-unknown-unknown\release\ocarina_forge_native.wasm --out-dir tools\wasm-bench\pkg --target web
cd tools\wasm-bench
npm install
npx vite --port 47241 --strictPort --host 127.0.0.1
```

Then open `http://127.0.0.1:47241`. The `.venv` folder in this worktree
is a junction to `C:\Users\gline\src\ocarina-forge\.venv`. Do not
`pip install`, `maturin develop`, or otherwise write through that
junction; use `rmdir` if you need to remove it.

If the fixtures are missing:

```bat
.venv\Scripts\python.exe tools\export_wasm_fixture.py --dump E:\sw2\oot\stable\ray-test
```

Dump-derived geometry is written to `tools/wasm-bench/data/` and is
gitignored.

## Recommended next step

Hook `WasmMeshIndex` into the viewer Snap path in this worktree: send
the scene's Land_* triangles once (a compact packed buffer, not a GLB
full of island meshes), build the index in a worker, and sample locally
as the user drags. That keeps the visible terrain light — the thing
that ran the tab out of memory — and removes the `/api/ground` round
trip. The eight-worker pool is there when a height-diff grid needs it.

Client-side texture decode is already as fast as the server Rust path,
so it is a smaller win unless the server starts shipping swizzled mips
instead of decoded or Basis textures.

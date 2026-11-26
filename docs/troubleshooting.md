# Troubleshooting the renderer

Forge tries WebGPU first. The viewport status label says which backend actually started, and why a fallback happened. The short git revision on that label is the page you are looking at. `GET /api/health` returns the API process's `build.revision` and `build.committedAt`.

## What the status label means

| Line starts with | What is running |
| --- | --- |
| `WebGPU` | `WebGPURenderer` on a real WebGPU device |
| `WebGPU (WebGL2 fallback: …)` | three stayed on `WebGPURenderer`, but its backend is WebGL2. The reason is the part in parentheses |
| `WebGL (WebGPU unavailable: …)` | WebGPU was the choice, and a classic `WebGLRenderer` is drawing. The reason is the part in parentheses |
| `WebGL` | This session asked for WebGL (`?renderer=webgl` or `?webgpu=0`), or the page is a build from before this label |

`?renderer=webgpu-strict` shows the reason and does not open the WebGL view. `?renderer=webgl` forces WebGL and stays a plain `WebGL` label.

The first time a fallback happens, a dismissible notice shows the same reason and links here. Dismissing it stores that reason in `localStorage` under `ocarinaforge.webgpuNotice`. A different reason shows the notice again. The status label keeps the reason either way. Details are also on the console as `WebGPU unavailable` or `WebGPU available`, including adapter info and a few device limits when the browser provided them.

## Plain "WebGL" that went away after a restart

A Brave window with `brave://flags/#enable-unsafe-webgpu` already enabled showed plain `WebGL`, with or without `?webgpu=1`. Restarting the Forge server made WebGPU start. The adapter was fine. The page was stale.

That label is what `backendLabel` returns for anything that is not a `WebGPURenderer`. Two code paths construct a classic `WebGLRenderer` and used to stamp the status string as the literal `WebGL`:

1. **The WebGL module graph.** `three.module.js` exports `WebGLRenderer`. `three.webgpu.js` does not. `createBackendRenderer` used that export as the signal to construct `WebGLRenderer`. A cached module, an old `frontend/dist`, a `webgl`-mode bundle (`npm run build:webgl`), or a Vite prebundle of `three` (`frontend/node_modules/.vite/deps/three.js`, which is `three.module.js`) still has `WebGLRenderer`. Boot then treated startup as a success, so the line never became `WebGPU (WebGL2 fallback)`.
2. **three r170 swallows the WebGPU error.** `WebGPURenderer` sets `getFallback`. If `requestAdapter()` returns null or `requestDevice()` throws, `Renderer.init` logs `WebGPU is not available, running under WebGL2 backend` and swaps in `WebGLBackend`. Forge rejected that renderer and booted the classic WebGL graph, which labeled itself `WebGL`. The toast said "Using WebGL2" and did not say why. `texture-compression-bc` is not required to start (a missing BC7 feature decodes to RGBA later). A device lost after a good init does not swap in classic WebGL: the line stays `WebGPU`, and a toast says the device was lost.

`python -m ocarina_forge --static frontend/dist` serves that folder with Starlette, which sends `ETag` and `Last-Modified` and used to send no `Cache-Control`. Browsers then heuristically cache `index.html` and keep the previous bundle. Restarting the API does not rebuild `dist`. `npm run build:webgl` is the WebGL-only bundle (`import.meta.env.MODE === "webgl"`); it never calls `navigator.gpu` until this page's label says `this bundle was built in webgl mode`.

`build.bat` and `./build.sh` serve the built editor with Vite preview on port 47231 and the API on 47232. Preview sends `Cache-Control: no-cache` on the page. A tab left open across a checkout change can keep the previous module graph until the preview restarts and the browser reloads. `three` is excluded from prebundling so a stale `deps/three.js` is not the WebGL build.

What to do:

1. Hard-reload (Ctrl+Shift+R). Compare the revision on the status label with `curl -s http://127.0.0.1:47232/api/health`.
2. If a banner says the server is older than the page, restart `build.bat` or `./build.sh` so the API and the editor both come back. If it says the page is older, the browser or `frontend/dist` is stale.
3. The builder rebuilds `frontend/dist` when editor sources changed. `index.html` is `Cache-Control: no-cache`. Fingerprinted files under `assets/` are `public, max-age=31536000, immutable`. A WebGL-only `build:webgl` stays on WebGL on purpose, and the status label says so.

## Brave Shields and the WebGPU flag

The flag and Shields are separate. Brave can expose `navigator.gpu` and still return a null adapter, or fail `requestDevice`, while fingerprinting protection is on.

1. Open `brave://flags/#enable-unsafe-webgpu` and set it to Enabled. Relaunch Brave.
2. Shields for `http://127.0.0.1:47231`: lower **Fingerprinting blocking**, or turn Shields off for that site. A blocked adapter shows up as `WebGL (WebGPU unavailable: adapter is null)` or `requestDevice failed: …`.
3. Open `brave://gpu` (Chrome: `chrome://gpu`). WebGPU should say Hardware accelerated. Disabled, Blocked, or Software only matches a fallback.

## Linux

Chrome and Brave on Linux use Vulkan for WebGPU. When `chrome://gpu` or `brave://gpu` shows WebGPU as unavailable:

- Launch with `--enable-unsafe-webgpu`.
- If Vulkan itself is disabled on that page, add `--enable-features=Vulkan` or `--use-angle=vulkan`.
- Some Brave builds also have `brave://flags/#enable-vulkan`.
- The same GPU page lists driver problems (an old Mesa, a missing NVIDIA Vulkan ICD). Those block the adapter before Forge runs.

## Models stay untextured, or old Python processes stay in Task Manager

Two separate things showed up together on a machine that had been left overnight.

**Load textures is off.** The project's `load-filters.yaml` can say `textures: false` (the Layout and Logic presets do). Geometry still loads. Images do not, and the view can look stuck. The editor shows a banner when a mesh layer is on and that filter is off. Turn it on with **Load textures** in the Actors panel, or with the button on the banner. The **Textures** checkbox on the stage bar only shows or hides images that are already loaded. The first time you check it while the filter is off, it turns the filter on. With the stage bar off and downloads finished, the status line says `textures hidden`. Profile `tex` counts maps on the drawn materials. `gpu N tex` counts texture objects, including the four internal textures on each batch, and is not a byte size. Open the page with `?texdebug=1` (or set `localStorage.ocarinaforge.texdebug` to `"1"` and toggle **Textures**) and the console prints one `forge texdebug` line: `meshMaps` against `partMaps`, `litOnly` when the image is only in `forgeLit`, `missingUv`, `foreign` when the texture came from a second three copy, and `uploaded` when the GPU image exists. `requested`, `fetched`, `decoded`, and `assigned` count the actor texture chain, and `error=` is the first failure. A dropped download or a skipped manifest also logs one `Texture pipeline:` warning. Selecting an actor prints `forge texdebug actor` (the Details panel shows the same block): the albedo sampler, the Tex stem, the format and size, and the uv span on the drawn geometry. A `Texture pipeline: decode error` whose path is `<hash>.ktx2.<unique>.tmp` is a Windows sharing violation on the cache rename. The request no longer fails for that. The bytes are served from the existing file when it matches, or from memory when the rename has not landed, and the texture route reads them and closes the file. The actor texture request sends `textures=1` when Load textures is on. A layer list that matches Layout would otherwise be read as textures off, which is why terrain can show grass while actors stay flat.

**Converter workers left behind.** On Windows, killing the API without ending its process tree used to leave `python` pool workers and their `node meshopt-worker.mjs` children running. A current API puts those processes in a job that ends with the API. After `taskkill /F /PID <api pid>`, Task Manager should show no extra `python` workers and no `node` running `meshopt-worker.mjs`. A normal quit and a change to the converter core count reap the pool as well. On startup the API logs a leftover Forge worker whose parent is already gone, and it does not stop it. Other Python processes are not part of that log. Close a leftover yourself if it is still using memory. The same lifetime rules are in the README under Converter processes.

## Check from the console

```javascript
navigator.gpu
const adapter = await navigator.gpu.requestAdapter()
adapter && (adapter.info || await adapter.requestAdapterInfo())
const device = await adapter.requestDevice()
```

`navigator.gpu` being `undefined` means the API is hidden (flag off, or this browser has no WebGPU). `requestAdapter()` returning `null` is the Shields / no-adapter case. A throw from `requestDevice()` is the message Forge puts on the status label. The console object from Forge adds `code`, `adapterInfo`, and `limits` (`maxTextureDimension2D`, `maxBufferSize`, `maxStorageBufferBindingSize` when the adapter has them).

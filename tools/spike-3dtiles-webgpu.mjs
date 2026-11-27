/**
 * SPIKE. Not a product path. Loads a two-level glTF tileset with
 * 3d-tiles-renderer 0.5.4 and three 0.187.0 WebGPURenderer in headless Chrome.
 * Synthetic geometry only. Does not register BatchedTilesPlugin until after the
 * core result is recorded. Run: node tools/spike-3dtiles-webgpu.mjs
 */
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const root = await modules();
const work = await mkdtemp(join(tmpdir(), "forge-tiles-webgpu-"));
const ktx = await writeKtx(join(work, "green.ktx2"));
await writeTiles(work, ktx);

const requests = [];
const server = createServer(async (req, res) => {
  try {
    const path = decodeURIComponent((req.url || "/").split("?")[0]);
    requests.push(path);
    if (process.env.SPIKE_LOG) console.error("REQ", path);
    if (path === "/" || path === "/index.html") {
      res.writeHead(200, { "content-type": "text/html" });
      res.end(page());
      return;
    }
    const file = path.startsWith("/nm/")
      ? join(root, path.slice(4))
      : join(work, path.replace(/^\/+/, ""));
    const allowed = file.startsWith(root) || file.startsWith(work);
    if (!allowed || path.includes("..")) {
      res.writeHead(404);
      res.end();
      return;
    }
    const body = await readFile(file);
    const type = file.endsWith(".js") || file.endsWith(".mjs") ? "text/javascript"
      : file.endsWith(".wasm") ? "application/wasm"
      : file.endsWith(".json") ? "application/json"
      : "application/octet-stream";
    res.writeHead(200, { "content-type": type });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;

const attempts = [
  ["--enable-unsafe-webgpu", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--enable-features=Vulkan"],
  ["--enable-unsafe-webgpu", "--use-vulkan=swiftshader", "--disable-vulkan-surface", "--enable-unsafe-swiftshader"],
  ["--enable-unsafe-webgpu", "--use-webgpu-adapter=swiftshader", "--enable-unsafe-swiftshader"],
];
let result = null;
let lastError = null;
for (const flags of attempts) {
  try {
    requests.length = 0;
    result = await runChrome(port, flags);
    if (result && result.adapter) {
      result.requests = requests.filter((item) => !item.startsWith("/nm/") && item !== "/favicon.ico");
      break;
    }
  } catch (error) {
    lastError = String(error && error.stack || error);
  }
}
server.closeAllConnections();
await new Promise((resolve) => server.close(resolve));
await rm(work, { recursive: true, force: true });
if (!result) {
  console.log(JSON.stringify({ spike: "3dtiles-webgpu", error: lastError }, null, 2));
  process.exit(1);
}
result.spike = "3dtiles-webgpu";
result.three = "0.187.0";
result.library = "3d-tiles-renderer@0.5.4";
console.log(JSON.stringify(result, null, 2));

async function runChrome(pagePort, flags) {
  const debug = 9300 + Math.floor(Math.random() * 500);
  const profile = await mkdtemp(join(tmpdir(), "forge-tiles-chrome-"));
  const chrome = spawn(process.env.CHROME || "google-chrome", [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu-sandbox",
    "--user-data-dir=" + profile,
    `--remote-debugging-port=${debug}`,
    ...flags,
    `http://127.0.0.1:${pagePort}/`,
  ], { stdio: "ignore" });
  try {
    let socketUrl = "";
    for (let i = 0; i < 40 && !socketUrl; i += 1) {
      try {
        const list = await (await fetch(`http://127.0.0.1:${debug}/json/list`)).json();
        const tab = list.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
        if (tab) socketUrl = tab.webSocketDebuggerUrl;
      } catch {
        // Chrome is still starting.
      }
      if (!socketUrl) await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!socketUrl) throw new Error("headless Chrome did not expose a page");
    return await readResult(socketUrl);
  } finally {
    chrome.kill("SIGKILL");
    await rm(profile, { recursive: true, force: true });
  }
}

function readResult(socketUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(socketUrl);
    let id = 0;
    const pending = new Map();
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && pending.has(message.id)) {
        pending.get(message.id)(message);
        pending.delete(message.id);
      }
    });
    ws.addEventListener("error", () => reject(new Error("chrome socket failed")));
    const send = (method, params = {}) => new Promise((done) => {
      const next = ++id;
      pending.set(next, done);
      ws.send(JSON.stringify({ id: next, method, params }));
    });
    ws.addEventListener("open", async () => {
      try {
        await send("Runtime.enable");
        let payload = null;
        for (let i = 0; i < 160 && !payload; i += 1) {
          const reply = await send("Runtime.evaluate", {
            expression: "window.__tiles",
            returnByValue: true,
          });
          payload = reply.result && reply.result.result && reply.result.result.value;
          if (!payload) await new Promise((done) => setTimeout(done, 250));
        }
        ws.close();
        if (!payload) reject(new Error("the page never finished"));
        else resolve(payload);
      } catch (error) {
        ws.close();
        reject(error);
      }
    });
  });
}

function page() {
  return `<!doctype html>
<meta charset="utf-8">
<canvas id="c" width="160" height="90"></canvas>
<script type="importmap">
{ "imports": {
  "three": "/nm/three/build/three.module.js",
  "three/webgpu": "/nm/three/build/three.webgpu.js",
  "three/tsl": "/nm/three/build/three.tsl.js",
  "three/addons/": "/nm/three/examples/jsm/"
} }
</script>
<script type="module">
const errors = [];
const original = console.error;
console.error = (...args) => { errors.push(args.map(String).join(" ")); original(...args); };
window.addEventListener("error", (event) => errors.push(String(event.message)));
window.addEventListener("unhandledrejection", (event) => errors.push(String(event.reason)));
const out = { errors, flags: ${JSON.stringify("pending")} };
try {
  const THREE = await import("three");
  const WEBGPU = await import("three/webgpu");
  const { TilesRenderer } = await import("/nm/3d-tiles-renderer/build/index.js");
  const { GLTFExtensionsPlugin, BatchedTilesPlugin } = await import("/nm/3d-tiles-renderer/build/index.plugins.js");
  const { KTX2Loader } = await import("three/addons/loaders/KTX2Loader.js");
  const { MeshoptDecoder } = await import("three/addons/libs/meshopt_decoder.module.js");
  const canvas = document.getElementById("c");
  if (!navigator.gpu) throw new Error("navigator.gpu is missing");
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter) throw new Error("requestAdapter returned null");
  out.adapter = {
    backend: adapter.info?.backend || "",
    architecture: adapter.info?.architecture || "",
    vendor: adapter.info?.vendor || "",
    sameCore: THREE.Mesh === WEBGPU.Mesh,
  };
  const renderer = new WEBGPU.WebGPURenderer({ canvas, antialias: false });
  await renderer.init();
  const device = renderer.backend.device;
  const bufferProbe = [];
  const vertexUsage = GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST;
  const geometryUsage = GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST | GPUBufferUsage.COPY_SRC;
  const indexUsage = GPUBufferUsage.INDEX | GPUBufferUsage.COPY_DST | GPUBufferUsage.COPY_SRC;
  for (const [size, usage, label] of [[32, vertexUsage, "vertex"], [32, geometryUsage, "geometry"], [12, indexUsage, "index"]]) {
    try {
      const buffer = device.createBuffer({ size, usage, mappedAtCreation: true });
      buffer.unmap();
      bufferProbe.push(label + " " + size + ":ok");
    } catch (error) {
      bufferProbe.push(label + " " + size + ":" + (error && error.message || error));
    }
  }
  out.bufferProbe = bufferProbe;
  // Some tile buffers fail mappedAtCreation on this SwiftShader after the first uploads.
  // Keep a real GPUBuffer (a Proxy fails setIndexBuffer's brand check) and copy with writeBuffer.
  const bufferFailures = [];
  out.bufferFailures = bufferFailures;
  const staged = new WeakMap();
  const originalGetMappedRange = GPUBuffer.prototype.getMappedRange;
  const originalUnmap = GPUBuffer.prototype.unmap;
  GPUBuffer.prototype.getMappedRange = function(offset, size) {
    const pending = staged.get(this);
    if (!pending) return originalGetMappedRange.call(this, offset, size);
    return pending.bytes;
  };
  GPUBuffer.prototype.unmap = function() {
    const pending = staged.get(this);
    if (!pending) return originalUnmap.call(this);
    staged.delete(this);
    pending.device.queue.writeBuffer(this, 0, pending.bytes);
  };
  const originalCreateBuffer = GPUDevice.prototype.createBuffer;
  GPUDevice.prototype.createBuffer = function(descriptor) {
    if (!descriptor?.mappedAtCreation) return originalCreateBuffer.call(this, descriptor);
    try {
      return originalCreateBuffer.call(this, descriptor);
    } catch (error) {
      bufferFailures.push({ size: descriptor.size, usage: descriptor.usage, message: String(error && error.message || error) });
      const real = originalCreateBuffer.call(this, {
        ...descriptor,
        usage: descriptor.usage | GPUBufferUsage.COPY_DST,
        mappedAtCreation: false,
      });
      staged.set(real, { bytes: new ArrayBuffer(descriptor.size), device: this });
      return real;
    }
  };
  const probeMesh = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ color: 0xff2040 }),
  );
  probeMesh.frustumCulled = false;
  const probeScene = new THREE.Scene();
  probeScene.add(probeMesh);
  const probeCamera = new THREE.PerspectiveCamera(50, 160 / 90, 0.1, 10);
  probeCamera.position.set(0, 0, 3);
  probeCamera.lookAt(0, 0, 0);
  try {
    if (renderer.renderAsync) await renderer.renderAsync(probeScene, probeCamera);
    else renderer.render(probeScene, probeCamera);
    out.probe = { drawCalls: renderer.info.render.drawCalls, triangles: renderer.info.render.triangles };
  } catch (error) {
    out.probe = { error: String(error && error.message || error) };
  }
  out.renderer = {
    isWebGPURenderer: renderer.isWebGPURenderer === true,
    backend: renderer.backend?.constructor?.name || "",
    isWebGPUBackend: renderer.backend?.isWebGPUBackend === true,
    bc: renderer.hasFeature("texture-compression-bc"),
    astc: renderer.hasFeature("texture-compression-astc"),
  };
  const gpuErrors = [];
  renderer.backend.device?.addEventListener?.("uncapturederror", (event) => {
    gpuErrors.push(String(event.error?.message || event.error || event));
  });
  await MeshoptDecoder.ready;
  const ktx = new KTX2Loader().detectSupport(renderer);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 160 / 90, 0.1, 400);
  scene.add(new THREE.AmbientLight(0xffffff, 1));
  const parent = new THREE.Group();
  parent.rotation.x = -Math.PI / 2;
  scene.add(parent);
  const tiles = new TilesRenderer("/tiles/tileset.json");
  tiles.errorTarget = 16;
  tiles.registerPlugin(new GLTFExtensionsPlugin({
    rtc: false,
    metadata: true,
    ktxLoader: ktx,
    meshoptDecoder: MeshoptDecoder,
  }));
  const found = [];
  const resident = new Set();
  tiles.addEventListener("load-model", ({ scene: model, url }) => {
    resident.add(url);
    model.traverse((child) => {
      if (!child.isMesh) return;
      const geometry = child.geometry;
      const names = geometry ? Object.keys(geometry.attributes) : [];
      const feature = geometry?.getAttribute?.("_feature_id_0");
      const material = child.material;
      const map = material?.map || null;
      child.frustumCulled = false;
      found.push({
        url,
        name: material?.name || "",
        type: material?.type || "",
        extras: material?.userData?.forge || "",
        map: Boolean(map),
        compressed: Boolean(map?.isCompressedTexture),
        mapFormat: map?.format ?? "",
        attributes: names,
        feature: feature ? feature.getX(0) : null,
        meshFeatures: Boolean(child.userData?.meshFeatures),
      });
    });
  });
  tiles.addEventListener("dispose-model", ({ url }) => resident.delete(url));
  parent.add(tiles.group);
  tiles.setCamera(camera);
  const resolutionFromRenderer = tiles.setResolutionFromRenderer(camera, renderer);
  async function view(z) {
    camera.position.set(0, 0, z);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();
    let renderError = "";
    for (let i = 0; i < 16; i += 1) {
      tiles.update();
      try {
        if (renderer.renderAsync) await renderer.renderAsync(scene, camera);
        else renderer.render(scene, camera);
        renderError = "";
      } catch (error) {
        renderError = String(error && error.message || error);
      }
      await new Promise((resolve) => setTimeout(resolve, 40));
      if (!renderError && tiles.stats.downloading === 0 && tiles.stats.parsing === 0 && resident.size > 0 && i > 1) break;
    }
    const placed = [];
    tiles.group.traverse((child) => {
      if (!child.isMesh) return;
      child.updateWorldMatrix(true, false);
      const e = child.matrixWorld.elements;
      placed.push({ name: child.material?.name || "", x: Math.round(e[12] * 100) / 100, y: Math.round(e[13] * 100) / 100, z: Math.round(e[14] * 100) / 100 });
    });
    return {
      z,
      resident: [...resident],
      placed,
      stats: { visible: tiles.stats.visible, used: tiles.stats.used, loaded: tiles.stats.loaded, failed: tiles.stats.failed, refused: tiles.stats.refused, downloading: tiles.stats.downloading },
      cachedBytes: tiles.lruCache.cachedBytes,
      drawCalls: renderer.info.render.drawCalls,
      triangles: renderer.info.render.triangles,
      renderError,
    };
  }
  const far = await view(120);
  const near = await view(2.5);
  out.tiles = { resolutionFromRenderer, meshes: found, far, near };
  camera.position.set(0, 0, 2.5);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  try {
    if (renderer.renderAsync) await renderer.renderAsync(scene, camera);
    else renderer.render(scene, camera);
    out.tiles.secondPass = { drawCalls: renderer.info.render.drawCalls, triangles: renderer.info.render.triangles };
  } catch (error) {
    out.tiles.secondPass = { error: String(error && error.message || error) };
  }
  const solo = new THREE.Scene();
  const soloCamera = new THREE.PerspectiveCamera(50, 160 / 90, 0.1, 10);
  soloCamera.position.set(0, 0, 3);
  soloCamera.lookAt(0, 0, 0);
  let soloMesh = null;
  tiles.group.traverse((child) => { if (child.isMesh) soloMesh = child; });
  if (soloMesh) {
    soloMesh.frustumCulled = false;
    solo.add(soloMesh);
    try {
      if (renderer.renderAsync) await renderer.renderAsync(solo, soloCamera);
      else renderer.render(solo, soloCamera);
      out.tiles.solo = {
        name: soloMesh.material?.name || "",
        drawCalls: renderer.info.render.drawCalls,
        triangles: renderer.info.render.triangles,
        vertices: soloMesh.geometry?.getAttribute("position")?.count || 0,
      };
    } catch (error) {
      out.tiles.solo = { error: String(error && error.message || error) };
    }
  } else {
    out.tiles.solo = { error: "no mesh in the tiles group" };
  }
  let pixels = { red: 0, green: 0, blue: 0, readError: "" };
  try {
    const target = new THREE.RenderTarget(160, 90);
    renderer.setRenderTarget(target);
    if (renderer.renderAsync) await renderer.renderAsync(solo, soloCamera);
    else renderer.render(solo, soloCamera);
    const buffer = await renderer.readRenderTargetPixelsAsync(target, 0, 0, 160, 90);
    renderer.setRenderTarget(null);
    for (let i = 0; i < buffer.length; i += 4) {
      if (buffer[i + 1] > 140 && buffer[i] < 80 && buffer[i + 2] < 80) pixels.green += 1;
      if (buffer[i + 2] > 140 && buffer[i + 1] < 120) pixels.blue += 1;
      if (buffer[i] > 160 && buffer[i + 1] < 80 && buffer[i + 2] < 80) pixels.red += 1;
    }
  } catch (error) {
    pixels.readError = String(error && error.message || error);
    try { renderer.setRenderTarget(null); } catch { /* device already lost */ }
  }
  out.tiles.pixels = pixels;
  let featureHelper = "not-called";
  let meshesSeen = 0;
  (soloMesh ? solo : tiles.group).traverse((child) => {
    if (!child.isMesh) return;
    meshesSeen += 1;
    if (featureHelper !== "not-called" || !child.userData?.meshFeatures) return;
    try {
      featureHelper = JSON.stringify(child.userData.meshFeatures.getFeatures(0, { x: 1, y: 0, z: 0 }));
    } catch (error) {
      featureHelper = String(error && error.message || error);
    }
  });
  out.tiles.featureHelper = featureHelper;
  out.tiles.meshesSeen = meshesSeen;
  const bytesBeforeLru = tiles.lruCache.cachedBytes;
  tiles.lruCache.maxBytesSize = 1;
  tiles.lruCache.minBytesSize = 0;
  tiles.lruCache.maxSize = 1;
  tiles.lruCache.minSize = 0;
  camera.position.set(0, 0, -80);
  camera.lookAt(0, 0, -81);
  camera.updateMatrixWorld();
  try {
    for (let i = 0; i < 4; i += 1) tiles.update();
    await new Promise((resolve) => setTimeout(resolve, 50));
  } catch (error) {
    out.tiles.lruError = String(error && error.message || error);
  }
  let batched = "not-constructed";
  try {
    const plugin = new BatchedTilesPlugin({ renderer });
    batched = plugin.name;
  } catch (error) {
    batched = String(error && error.message || error);
  }
  out.tiles.lru = { before: bytesBeforeLru, after: tiles.lruCache.cachedBytes, resident: [...resident] };
  out.tiles.batched = batched;
  out.gpuErrors = gpuErrors.slice(0, 8);
} catch (error) {
  out.fatal = String(error && error.stack || error);
}
window.__tiles = out;
</script>`;
}

async function writeTiles(dir, ktxBytes) {
  const { Document, NodeIO } = await import(pathToFileURL(join(root, "@gltf-transform/core/dist/index.modern.js")).href);
  const { EXTMeshoptCompression, KHRMaterialsUnlit, KHRTextureBasisu } = await import(pathToFileURL(join(root, "@gltf-transform/extensions/dist/index.modern.js")).href);
  const { meshopt } = await import(pathToFileURL(join(root, "@gltf-transform/functions/dist/functions.modern.js")).href);
  const encoderMod = await import(pathToFileURL(join(root, "meshoptimizer/meshopt_encoder.js")).href);
  await encoderMod.MeshoptEncoder.ready;
  const io = new NodeIO()
    .registerExtensions([EXTMeshoptCompression, KHRMaterialsUnlit, KHRTextureBasisu])
    .registerDependencies({ "meshopt.encoder": encoderMod.MeshoptEncoder });

  async function tile(name, color, actorId, textureBytes) {
    const doc = new Document();
    const buffer = doc.createBuffer();
    const scene = doc.createScene();
    doc.getRoot().setDefaultScene(scene);
    const unlit = doc.createExtension(KHRMaterialsUnlit).createUnlit();
    const material = doc.createMaterial(name)
      .setBaseColorFactor([...color, 1])
      .setExtension("KHR_materials_unlit", unlit)
      .setExtras({ forge: name })
      .setAlphaMode("OPAQUE");
    if (textureBytes) {
      doc.createExtension(KHRTextureBasisu).setRequired(true);
      const texture = doc.createTexture("green")
        .setMimeType("image/ktx2")
        .setImage(textureBytes);
      material.setBaseColorTexture(texture);
    }
    const positions = new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]);
    const prim = doc.createPrimitive()
      .setMaterial(material)
      .setAttribute("POSITION", doc.createAccessor().setType("VEC3").setArray(positions).setBuffer(buffer))
      .setAttribute("_FEATURE_ID_0", doc.createAccessor().setType("SCALAR").setArray(new Float32Array([actorId, actorId, actorId, actorId])).setBuffer(buffer))
      .setIndices(doc.createAccessor().setType("SCALAR").setArray(new Uint16Array([0, 1, 2, 0, 2, 3])).setBuffer(buffer));
    scene.addChild(doc.createNode(name).setMesh(doc.createMesh().addPrimitive(prim)));
    await doc.transform(meshopt({ encoder: encoderMod.MeshoptEncoder, level: "medium" }));
    await writeFile(join(folder, `${name}.glb`), patchFeatures(Buffer.from(await io.writeBinary(doc)), actorId));
  }
  const folder = join(dir, "tiles");
  await mkdir(folder, { recursive: true });
  // Coarse tile is unlit red. The replacement is white multiplied by a green BC7, so a green pixel means the KTX2 was sampled.
  await tile("plain", [0.9, 0.05, 0.05], 42, null);
  if (ktxBytes) await tile("ktx", [1, 1, 1], 7, ktxBytes);
  else await tile("ktx", [0.1, 0.2, 0.95], 7, null);
  const box = [0, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 2];
  const tileset = {
    asset: { version: "1.1", gltfUpAxis: "y" },
    geometricError: 1000,
    root: {
      boundingVolume: { box },
      refine: "ADD",
      geometricError: 200,
      children: [
        {
          boundingVolume: { box },
          refine: "REPLACE",
          geometricError: 8,
          content: { uri: "plain.glb" },
          children: [
            { boundingVolume: { box }, geometricError: 0, content: { uri: "ktx.glb" } },
          ],
        },
      ],
    },
  };
  await writeFile(join(folder, "tileset.json"), JSON.stringify(tileset));
}

function patchFeatures(glb, actorId) {
  const view = new DataView(glb.buffer, glb.byteOffset, glb.byteLength);
  const jsonLength = view.getUint32(12, true);
  const json = JSON.parse(new TextDecoder().decode(glb.subarray(20, 20 + jsonLength)));
  json.extensionsUsed = [...new Set([...(json.extensionsUsed || []), "EXT_mesh_features"])];
  for (const mesh of json.meshes || []) {
    for (const prim of mesh.primitives || []) {
      if (prim.attributes?._FEATURE_ID_0 === undefined) continue;
      prim.extensions = {
        ...(prim.extensions || {}),
        EXT_mesh_features: { featureIds: [{ featureCount: Math.max(actorId + 1, 1), attribute: 0 }] },
      };
    }
  }
  let text = Buffer.from(JSON.stringify(json));
  while (text.length % 4) text = Buffer.concat([text, Buffer.from(" ")]);
  const binLength = view.getUint32(20 + jsonLength, true);
  const bin = glb.subarray(28 + jsonLength, 28 + jsonLength + binLength);
  const chunks = Buffer.concat([
    Buffer.from(Uint8Array.of(text.length & 255, (text.length >> 8) & 255, (text.length >> 16) & 255, (text.length >> 24) & 255)),
    Buffer.from("JSON"),
    text,
    glb.subarray(20 + jsonLength, 28 + jsonLength),
    bin,
  ]);
  const total = 12 + chunks.length;
  const header = Buffer.alloc(12);
  header.write("glTF", 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(total, 8);
  return Buffer.concat([header, chunks]);
}

async function writeKtx(file) {
  const script = `
import sys
sys.path.insert(0, ${JSON.stringify("/agent/repos/ocarina-forge")})
from ocarina_forge.formats.gpu import encode_bc7, ktx2, VK_BC7_SRGB
rgba = bytes([16, 220, 48, 255]) * 16
open(sys.argv[1], "wb").write(ktx2(VK_BC7_SRGB, 4, 4, [encode_bc7(4, 4, rgba)]))
`;
  const run = spawnSync("python3", ["-c", script, file], { encoding: "utf8" });
  if (run.status !== 0) return null;
  return readFile(file);
}

async function modules() {
  const home = join(tmpdir(), "spike-tiles");
  const dir = join(home, "node_modules");
  const probe = spawnSync(process.execPath, ["-e", "import('three')"], { cwd: home, encoding: "utf8" });
  if (probe.status === 0) return dir;
  const installDir = await mkdtemp(join(tmpdir(), "spike-tiles-"));
  const install = spawnSync("npm", ["install", "--silent", "three@0.187.0", "3d-tiles-renderer@0.5.4", "@gltf-transform/core@4.2.1", "@gltf-transform/extensions@4.2.1", "@gltf-transform/functions@4.2.1", "meshoptimizer@1.3.0"], {
    cwd: installDir,
    encoding: "utf8",
  });
  if (install.status !== 0) throw new Error(install.stderr || "npm install failed");
  return join(installDir, "node_modules");
}

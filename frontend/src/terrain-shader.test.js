import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
const threeModule = readFileSync(new URL("../node_modules/three/build/three.module.js", import.meta.url));

function extractTemplate(name) {
  const marker = `const ${name} = \``;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${name} is missing`);
  const body = start + marker.length;
  const end = source.indexOf("`;", body);
  assert.notEqual(end, -1, `${name} is not closed`);
  return source.slice(body, end);
}

function extractReplace(include) {
  const marker = `"${include}",\n          \``;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, include);
  const body = start + marker.length;
  const end = source.indexOf("`", body);
  assert.notEqual(end, -1, include);
  return source.slice(body, end);
}

const FUNCS = extractTemplate("TERRAIN_FUNCS");
const MAP = extractTemplate("TERRAIN_MAP");

test("the map replacement has no function definitions", () => {
  assert.match(FUNCS, /vec2 terrainCell\s*\(/);
  assert.match(FUNCS, /vec4 terrainSample\s*\(/);
  assert.match(FUNCS, /vec4 terrainTri\s*\(/);
  assert.doesNotMatch(MAP, /terrainCell\s*\(/);
  assert.doesNotMatch(
    MAP,
    /\b(?:void|float|int|uint|bool|vec[234]|mat[234]|sampler2D)\s+[A-Za-z_]\w*\s*\([^;]*\)\s*\{/
  );
  assert.match(MAP, /if \(terrainLayerMissing < 0\.5 && terrainSub < 0\.5 && vTerrainMark\.y > 0\.5\) discard;/);
  const inject = source.indexOf('.replace("void main() {", `${TERRAIN_FUNCS}\\nvoid main() {`)');
  const maps = source.indexOf('.replace("#include <map_fragment>", TERRAIN_MAP)');
  const decls = source.lastIndexOf("#include <uv_pars_fragment>", inject);
  assert.ok(decls !== -1 && decls < inject && inject < maps, "helpers are injected after the uniforms and before the map chunk");
});

test("a gizmo drag cannot throw away the atlas or move every copy", () => {
  assert.match(source, /applyActorMatrix\(entry\.slots, entry\.key, actorMatrix\)/);
  assert.doesNotMatch(source, /slot\.mesh\.instanceMatrix\.needsUpdate = true/);
  assert.match(source, /matrixWorldAutoUpdate = false/);
  assert.match(source, /shader\.fragmentShader\.includes\("terrainCell\("\)/);
  assert.match(source, /customProgramCacheKey = \(\) => `terrain\|\$\{columns\}\|\$\{repeat\}`/);
  assert.match(source, /terrainRoot\.renderOrder = -1/);
  assert.match(source, /terrainRoot\.frustumCulled = false/);
  assert.match(source, /boundsGap\(/);
  assert.match(source, /transformSphere\(/);
});

test("sub terrain starts off and the field is front-side", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const box = html.match(/<input id="show-sub"[^>]*>/);
  assert.ok(box, "missing the Sub terrain checkbox");
  assert.equal(box[0].includes("checked"), false);
  assert.match(main, /showSub:\s*false/);
  assert.match(source, /let showSub = false/);
  assert.match(source, /material\.side = THREE\.FrontSide/);
});

test("the terrain shader compiles", async () => {
  const page = `<!doctype html>
<meta charset="utf-8">
<script type="importmap">{"imports":{"three":"/three.module.js"}}</script>
<script type="module">
import * as THREE from "three";
const TERRAIN_FUNCS = ${JSON.stringify(FUNCS)};
const TERRAIN_MAP = ${JSON.stringify(MAP)};
const UV_PARS_VERT = ${JSON.stringify(extractReplace("#include <uv_pars_vertex>"))};
const UV_VERT = ${JSON.stringify(extractReplace("#include <uv_vertex>"))};
const UV_PARS_FRAG = ${JSON.stringify(extractReplace("#include <uv_pars_fragment>"))};
const errors = [];
window.__shader = null;
try {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false });
  const gl = renderer.getContext();
  const compile = gl.compileShader.bind(gl);
  gl.compileShader = function (shader) {
    compile(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) errors.push(gl.getShaderInfoLog(shader) || "shader compile failed");
  };
  const link = gl.linkProgram.bind(gl);
  gl.linkProgram = function (program) {
    link(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) errors.push("link: " + (gl.getProgramInfoLog(program) || "link failed"));
  };
  const texture = new THREE.DataTexture(new Uint8Array([180, 140, 90, 255]), 1, 1);
  texture.needsUpdate = true;
  const material = new THREE.MeshStandardMaterial({ map: texture, roughness: 0.85, metalness: 0 });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.terrainCols = { value: 4 };
    shader.uniforms.terrainRepeat = { value: 8 };
    shader.uniforms.terrainDebug = { value: 0 };
    shader.uniforms.terrainSub = { value: 1 };
    shader.uniforms.terrainLayerMissing = { value: 0 };
    shader.vertexShader = shader.vertexShader
      .replace("#include <uv_pars_vertex>", UV_PARS_VERT)
      .replace("#include <uv_vertex>", UV_VERT);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <uv_pars_fragment>", UV_PARS_FRAG)
      .replace("void main() {", TERRAIN_FUNCS + "\\nvoid main() {")
      .replace("#include <map_fragment>", TERRAIN_MAP);
  };
  const scene = new THREE.Scene();
  scene.add(new THREE.Mesh(new THREE.PlaneGeometry(1, 1), material));
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 20);
  camera.position.set(0, 0.4, 2);
  renderer.render(scene, camera);
  window.__shader = { errors };
} catch (error) {
  window.__shader = { errors: [String(error && error.stack || error)] };
}
</script>`;

  const server = createServer((req, res) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    if (url.pathname === "/three.module.js") {
      res.writeHead(200, { "content-type": "text/javascript" });
      res.end(threeModule);
      return;
    }
    res.writeHead(200, { "content-type": "text/html" });
    res.end(page);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const debug = 9341;
  const chrome = spawn("google-chrome", [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    `--remote-debugging-port=${debug}`,
    `--user-data-dir=/tmp/terrain-shader-${process.pid}`,
    `http://127.0.0.1:${port}/`,
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
    assert.ok(socketUrl, "headless Chrome did not expose a page");
    const result = await readShader(socketUrl);
    assert.deepEqual(result.errors, [], result.errors.join("\n"));
  } finally {
    chrome.kill("SIGKILL");
    await new Promise((resolve) => server.close(resolve));
  }
});

function readShader(socketUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(socketUrl);
    let id = 0;
    const pending = new Map();
    const exceptions = [];
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && pending.has(message.id)) {
        pending.get(message.id)(message);
        pending.delete(message.id);
      }
      if (message.method === "Runtime.exceptionThrown") {
        exceptions.push(message.params.exceptionDetails.text || "exception");
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
        for (let i = 0; i < 40; i += 1) {
          const reply = await send("Runtime.evaluate", {
            expression: "window.__shader",
            returnByValue: true,
          });
          payload = reply.result && reply.result.result && reply.result.result.value;
          if (payload) break;
          await new Promise((done) => setTimeout(done, 250));
        }
        ws.close();
        if (!payload) reject(new Error(exceptions.join("\n") || "the page never compiled a shader"));
        else resolve({ errors: [...(payload.errors || []), ...exceptions] });
      } catch (error) {
        ws.close();
        reject(error);
      }
    });
  });
}

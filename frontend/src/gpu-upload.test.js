import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(root, "..");
const outDir = path.join(root, "tools", "_gpu_quad");
const chrome = process.env.CHROME || "google-chrome";

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
    server.on("error", reject);
  });
}

const WRITER = `
from pathlib import Path
import sys
from ocarina_forge.formats.gltf import Primitive, to_glb
from ocarina_forge.formats.gpu import VK_BC1, encode_bc1, gpu_image, ktx2, pack_texture, split_texture

out = Path(sys.argv[1])
rgba = bytes([180, 40, 30, 255]) * (8 * 8)
image = type("I", (), {})()
image.width = 8
image.height = 8
image.rgba = rgba
image.codec = "bc1"
image.blocks = encode_bc1(8, 8, rgba)
positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
chain = gpu_image(image, "opaque")
(out / "chain.glb").write_bytes(to_glb([Primitive(positions, [0, 1, 2], uvs, png=chain, name="Quad")]))
_ktx, fallback = split_texture(chain)
one = pack_texture(ktx2(VK_BC1, 8, 8, image.blocks), fallback)
(out / "one.glb").write_bytes(to_glb([Primitive(positions, [0, 1, 2], uvs, png=one, name="Quad")]))
print("wrote", out)
`;

async function waitFor(url) {
  const started = Date.now();
  while (Date.now() - started < 10000) {
    try {
      const response = await fetch(url);
      if (response.ok) return response.json();
    } catch {
      // Chrome is still binding the port.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Chrome did not open ${url}`);
}

function cdp(ws) {
  let id = 0;
  return function send(method, params) {
    const messageId = ++id;
    ws.send(JSON.stringify({ id: messageId, method, params }));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${method} timed out`)), 8000);
      function onMessage(event) {
        const body = JSON.parse(event.data);
        if (body.id !== messageId) return;
        clearTimeout(timer);
        ws.removeEventListener("message", onMessage);
        resolve(body.result);
      }
      ws.addEventListener("message", onMessage);
    });
  };
}

async function render(filePort, file, query) {
  const debugPort = await freePort();
  const browser = spawn(chrome, [
    "--headless=new",
    "--disable-gpu-sandbox",
    "--no-first-run",
    "--use-gl=angle",
    "--use-angle=swiftshader-webgl",
    "--enable-unsafe-swiftshader",
    `--user-data-dir=/tmp/ocarina-forge-gpu-quad-${debugPort}`,
    `--remote-debugging-port=${debugPort}`,
    "about:blank",
  ], { stdio: "ignore" });
  try {
    const version = await waitFor(`http://127.0.0.1:${debugPort}/json/version`);
    const pageUrl = `http://127.0.0.1:${filePort}/tools/gpu-quad.html?file=/tools/_gpu_quad/${file}&${query}`;
    const browserSocket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      browserSocket.addEventListener("open", resolve);
      browserSocket.addEventListener("error", reject);
    });
    const browserSend = cdp(browserSocket);
    const created = await browserSend("Target.createTarget", { url: pageUrl });
    const targets = await waitFor(`http://127.0.0.1:${debugPort}/json/list`);
    const page = targets.find((item) => item.id === created.targetId) || targets.find((item) => item.type === "page");
    browserSocket.close();
    const socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve);
      socket.addEventListener("error", reject);
    });
    const notes = [];
    socket.addEventListener("message", (event) => {
      const body = JSON.parse(event.data);
      if (body.method === "Runtime.exceptionThrown") {
        const details = body.params.exceptionDetails || {};
        const exception = details.exception || {};
        notes.push(exception.description || details.text || JSON.stringify(details).slice(0, 500));
      }
    });
    const send = cdp(socket);
    await send("Runtime.enable");
    let text = "";
    let href = "";
    const started = Date.now();
    while (Date.now() - started < 8000) {
      const result = await send("Runtime.evaluate", {
        expression: "({ href: location.href, text: document.getElementById('out') ? document.getElementById('out').textContent : '' })",
        returnByValue: true,
      });
      const value = result && result.result && result.result.value;
      text = (value && value.text) || "";
      href = (value && value.href) || "";
      if (text && text !== "pending") break;
      if (notes.length) break;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    socket.close();
    if (!text || text === "pending") return `pending href=${href} notes=${notes.join(" | ")}`;
    return text;
  } finally {
    browser.kill();
  }
}

function parse(text) {
  const match = String(text).match(/pixel (\d+),(\d+),(\d+) compressed (\d) filter (\d+) levels (\d+) s3tc (\d) bptc (\d)/);
  if (!match) return null;
  return {
    rgb: [Number(match[1]), Number(match[2]), Number(match[3])],
    compressed: match[4] === "1",
    filter: Number(match[5]),
    levels: Number(match[6]),
    s3tc: match[7] === "1",
    bptc: match[8] === "1",
  };
}

function near(pixel, color) {
  return pixel.every((channel, index) => Math.abs(channel - color[index]) <= 16);
}

const chromeProbe = spawnSync(chrome, ["--version"], { encoding: "utf8" });

test("a compressed quad matches the bitmap fallback through GLTFLoader", { timeout: 90000, skip: chromeProbe.status === 0 ? false : "Chrome is not installed" }, async () => {
  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });
  const python = path.join(repo, ".venv", "bin", "python");
  const made = await run(python, ["-c", WRITER, outDir], { cwd: repo, env: { ...process.env, OCARINA_FORGE_WORKERS: "1" } });
  assert.equal(made.code, 0, made.stderr);
  const port = await freePort();
  const server = spawn(python, ["-m", "http.server", String(port), "--bind", "127.0.0.1"], { cwd: root, stdio: "ignore" });
  try {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const chainText = await render(port, "chain.glb", "repair=1");
    const chain = parse(chainText);
    const bitmap = parse(await render(port, "chain.glb", "bitmap=1&repair=1"));
    const broken = parse(await render(port, "one.glb", "repair=0"));
    const saved = parse(await render(port, "one.glb", "repair=1"));
    assert.ok(chain, `chain render did not report a pixel: ${chainText}`);
    assert.ok(bitmap, "bitmap render did not report a pixel");
    assert.ok(near(bitmap.rgb, [180, 40, 30]), `bitmap pixel ${bitmap.rgb}`);
    if (chain.bptc || chain.s3tc) {
      assert.equal(chain.compressed, true);
      assert.ok(chain.levels >= 4, `expected a full chain, got ${chain.levels}`);
      assert.equal(chain.filter, 1008);
      assert.ok(near(chain.rgb, bitmap.rgb), `compressed ${chain.rgb} bitmap ${bitmap.rgb}`);
    }
    if (broken && (broken.bptc || broken.s3tc)) {
      assert.equal(broken.compressed, true);
      assert.equal(broken.levels, 1);
      // GLTFLoader overwrites the onLoad filter with the sampler, 9987 → 1008.
      assert.equal(broken.filter, 1008);
      // Chrome on a real GPU samples that incomplete texture as black. ANGLE
      // SwiftShader on this machine still returns the base level, so the color
      // is not required to be black here.
    }
    if (saved && saved.compressed) {
      assert.equal(saved.filter, 1006);
      assert.ok(near(saved.rgb, bitmap.rgb), `repaired one-mip ${saved.rgb} bitmap ${bitmap.rgb}`);
    }
  } finally {
    server.kill();
    await rm(outDir, { recursive: true, force: true });
  }
});

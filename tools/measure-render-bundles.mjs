/**
 * Headless WebGPU timing for spatial render bundles.
 *
 * Serves tools/render-bundle-bench.html and three r187, then opens it in
 * Chrome. The page is a synthetic grid of BatchedMesh cells, not a game
 * scene. Prints JSON and writes tools/measure-render-bundles.json.
 *
 *   node tools/measure-render-bundles.mjs
 *
 * Optional env: CHROME_PATH, BUNDLE_BENCH_OUT, BUNDLE_BENCH_PORT.
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = path.dirname(fileURLToPath(import.meta.url));
const repo = path.dirname(root);
const require = createRequire(path.join(root, "wasm-bench", "package.json"));
const puppeteer = require("puppeteer-core");

const chrome = process.env.CHROME_PATH || "/opt/google/chrome/chrome";
const out = process.env.BUNDLE_BENCH_OUT || path.join(root, "measure-render-bundles.json");
const port = Number(process.env.BUNDLE_BENCH_PORT || 47331);
const threeBuild = path.join(repo, "frontend", "node_modules", "three", "build");
const pageFile = path.join(root, "render-bundle-bench.html");

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
};

function startServer() {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", "http://127.0.0.1");
      let file = pageFile;
      if (url.pathname.startsWith("/three/")) {
        const name = path.normalize(url.pathname.slice("/three/".length));
        if (name.startsWith("..") || path.isAbsolute(name)) {
          response.writeHead(403);
          response.end();
          return;
        }
        file = path.join(threeBuild, name);
      } else if (url.pathname === "/favicon.ico") {
        response.writeHead(204);
        response.end();
        return;
      } else if (url.pathname !== "/" && url.pathname !== "/bench.html") {
        response.writeHead(404);
        response.end();
        return;
      }
      const body = await readFile(file);
      const ext = path.extname(file);
      response.writeHead(200, { "content-type": types[ext] || "application/octet-stream" });
      response.end(body);
    } catch (error) {
      response.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
      response.end(String(error && error.stack ? error.stack : error));
    }
  });
  return new Promise((resolve) => {
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

const flagSets = [
  [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--enable-unsafe-webgpu",
    "--enable-features=Vulkan",
    "--use-angle=swiftshader",
    "--use-vulkan=swiftshader",
    "--disable-gpu-sandbox",
    "--enable-precise-memory-info",
    "--use-webgpu-adapter=swiftshader",
  ],
  [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--enable-unsafe-webgpu",
    "--use-angle=swiftshader",
    "--disable-gpu-sandbox",
    "--enable-precise-memory-info",
  ],
];

async function runOnce(flags) {
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: true,
    args: flags,
    dumpio: false,
  });
  try {
    const page = await browser.newPage();
    const logs = [];
    page.on("console", (message) => logs.push(`${message.type()}: ${message.text()}`));
    page.on("pageerror", (error) => logs.push(`pageerror: ${error}`));
    await page.goto(`http://127.0.0.1:${port}/bench.html`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForFunction("window.__bundleBench", { timeout: 180000 });
    const result = await page.evaluate(() => window.__bundleBench);
    return { result, logs: logs.slice(-30) };
  } finally {
    await browser.close();
  }
}

const server = await startServer();
let captured = null;
const attempts = [];
try {
  for (const flags of flagSets) {
    try {
      const attempt = await runOnce(flags);
      attempts.push({ flags, ok: Boolean(attempt.result && attempt.result.ok), reason: attempt.result && attempt.result.reason, logs: attempt.logs });
      captured = attempt.result;
      if (attempt.result && attempt.result.ok) break;
    } catch (error) {
      attempts.push({ flags, ok: false, reason: String(error && error.stack ? error.stack : error) });
    }
  }
} finally {
  server.close();
}

const payload = {
  measuredAt: new Date().toISOString(),
  chrome,
  attempts: attempts.map((attempt) => ({
    ok: attempt.ok,
    reason: attempt.reason || null,
    flags: attempt.flags,
    logs: attempt.logs || [],
  })),
  result: captured,
};
const { writeFile } = await import("node:fs/promises");
await writeFile(out, `${JSON.stringify(payload, null, 2)}\n`);
console.log(JSON.stringify(payload, null, 2));
if (!captured || captured.ok !== true) process.exitCode = 1;

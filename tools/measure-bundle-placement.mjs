/**
 * Headless pixel compare: the same BatchedMesh with bundles off and on.
 *   node tools/measure-bundle-placement.mjs
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
const port = Number(process.env.BUNDLE_BENCH_PORT || 47332);
const threeBuild = path.join(repo, "frontend", "node_modules", "three", "build");

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    let file = path.join(root, "bundle-placement.html");
    if (url.pathname.startsWith("/three/")) {
      const name = path.normalize(url.pathname.slice("/three/".length));
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
    response.writeHead(200, { "content-type": path.extname(file) === ".html" ? "text/html" : "text/javascript" });
    response.end(body);
  } catch (error) {
    response.writeHead(500);
    response.end(String(error && error.stack ? error.stack : error));
  }
});
await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
const browser = await puppeteer.launch({
  executablePath: chrome,
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--enable-unsafe-webgpu",
    "--use-angle=swiftshader",
    "--use-vulkan=swiftshader",
    "--disable-gpu-sandbox",
    "--use-webgpu-adapter=swiftshader",
  ],
});
try {
  const page = await browser.newPage();
  page.on("pageerror", (error) => console.error("pageerror", error));
  page.on("console", (message) => {
    if (message.type() === "error") console.error("console", message.text());
  });
  await page.goto(`http://127.0.0.1:${port}/bench.html`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction("window.__bundlePlacement", { timeout: 120000 });
  const result = await page.evaluate(() => window.__bundlePlacement);
  console.log(JSON.stringify(result, null, 2));
  if (!result || result.ok !== true) process.exitCode = 1;
} finally {
  await browser.close();
  server.close();
}

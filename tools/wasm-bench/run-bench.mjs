import { spawn } from "node:child_process";
import { writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const root = path.dirname(fileURLToPath(import.meta.url));
const url = process.env.FORGE_WASM_BENCH_URL || "http://127.0.0.1:47241/";
const out = process.env.FORGE_WASM_BENCH_OUT || path.join(root, "data", "bench-results.json");
const chrome = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const viteCmd = process.platform === "win32" ? "npx.cmd" : "npx";

function stop(child) {
  if (!child || child.exitCode != null || child.pid == null) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore", windowsHide: true });
  } else {
    child.kill("SIGTERM");
  }
}

function waitForServer(child) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timeout = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error("Vite did not start on 47241."));
      }
    }, 30000);
    let text = "";
    const onData = (chunk) => {
      text += chunk.toString();
      if (!settled && (text.includes("127.0.0.1:47241") || text.includes("Local:"))) {
        settled = true;
        clearTimeout(timeout);
        resolve();
      }
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("exit", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error(`Vite exited early with code ${code}. ${text}`));
    });
  });
}

const vite = spawn(viteCmd, ["vite", "--port", "47241", "--strictPort", "--host", "127.0.0.1"], {
  cwd: root,
  stdio: ["ignore", "pipe", "pipe"],
  windowsHide: true,
});

try {
  await waitForServer(vite);
  if (!existsSync(chrome)) throw new Error(`Chrome was not found at ${chrome}. Set CHROME_PATH.`);
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: true,
    args: ["--disable-gpu", "--no-first-run"],
  });
  const page = await browser.newPage();
  page.on("console", (msg) => console.log("[page]", msg.type(), msg.text()));
  page.on("pageerror", (error) => console.error("[pageerror]", error));
  await page.goto(url, { waitUntil: "networkidle0", timeout: 180000 });
  const data = await page.waitForFunction(() => window.__BENCH_DONE, { timeout: 600000 });
  if (!data) throw new Error("The bench page did not finish.");
  const results = await page.evaluate(() => window.__BENCH_RESULTS);
  await browser.close();
  await writeFile(out, JSON.stringify(results, null, 2));
  console.log(`Wrote ${out}`);
  console.log(JSON.stringify(results.rows, null, 2));
  if (results.errors && results.errors.length) {
    process.exitCode = 1;
  }
} finally {
  stop(vite);
}

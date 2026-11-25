/**
 * Headless Chrome measurements for Field-scale memory, draws, and frame time
 * on the already-running forge-wasm dev server (47241), leaving 47231/47232 alone.
 *
 *   node tools/measure-field-memory.mjs
 *
 * Optional env: FORGE_WASM_URL, CHROME_PATH, FORGE_WASM_MEASURE_OUT, FORGE_WASM_LABEL,
 * FORGE_WASM_SCREENSHOT, FORGE_WASM_LOAD_MS (default 720000).
 * FORGE_WASM_MAX_WS_MB (default 10240) kills the Chrome tree at that working set.
 * FORGE_WASM_MIN_FREE_MB (default 4096) also stops if system free RAM falls this low.
 * FORGE_WASM_MIN_FREE_START_MB (default 10240) refuses to launch if free RAM is below it.
 * Only talks to 47241/47242. Does not start or stop 47231/47232.
 */
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";

const root = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(root, "wasm-bench", "package.json"));
const puppeteer = require("puppeteer-core");

const url = process.env.FORGE_WASM_URL || "http://127.0.0.1:47241/";
const chrome = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const label = process.env.FORGE_WASM_LABEL || "run";
const out = process.env.FORGE_WASM_MEASURE_OUT || path.join(root, `measure-field-memory-${label}.json`);
const shot = process.env.FORGE_WASM_SCREENSHOT || path.join(root, `measure-field-memory-${label}.png`);
const loadMs = Number(process.env.FORGE_WASM_LOAD_MS || 720000);
const maxWsMB = Number(process.env.FORGE_WASM_MAX_WS_MB || 10240);
const minFreeMB = Number(process.env.FORGE_WASM_MIN_FREE_MB || 4096);
const minFreeStartMB = Number(process.env.FORGE_WASM_MIN_FREE_START_MB || 10240);
const POINTS = [
  { name: "island tree", x: -192.39, z: 506.95, want: 131.353 },
  { name: "field", x: 0, z: 0, want: 147.441 },
];

function systemAndChromeMemory(pid) {
  const processId = Number(pid) || 0;
  const script = `
    $os = Get-CimInstance Win32_OperatingSystem
    $freeMB = [math]::Round($os.FreePhysicalMemory / 1KB, 1)
    $totalMB = [math]::Round($os.TotalVisibleMemorySize / 1KB, 1)
    $ws = 0; $pb = 0; $count = 0
    if (${processId} -gt 0) {
      $q = [System.Collections.Generic.Queue[int]]::new(); $q.Enqueue(${processId})
      $seen = @{}
      $ids = @()
      while ($q.Count) {
        $id = $q.Dequeue(); if ($seen.ContainsKey($id)) { continue }; $seen[$id] = $true
        $ids += $id
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" -ErrorAction SilentlyContinue | ForEach-Object { $q.Enqueue([int]$_.ProcessId) }
      }
      $procs = Get-Process -Id $ids -ErrorAction SilentlyContinue
      if ($procs) {
        $count = @($procs).Count
        $ws = [math]::Round((($procs | Measure-Object WorkingSet64 -Sum).Sum) / 1MB, 1)
        $pb = [math]::Round((($procs | Measure-Object PrivateMemorySize64 -Sum).Sum) / 1MB, 1)
      }
    }
    @{ freeMB = $freeMB; totalMB = $totalMB; pid = ${processId}; processes = $count; workingSetMB = $ws; privateMB = $pb } | ConvertTo-Json -Compress
  `;
  try {
    const text = execFileSync("powershell.exe", ["-NoProfile", "-Command", script], { encoding: "utf8" });
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function chromeMemoryMB(pid) {
  const mem = systemAndChromeMemory(pid);
  if (!mem) return null;
  return { pid: mem.pid, processes: mem.processes, workingSetMB: mem.workingSetMB, privateMB: mem.privateMB, freeMB: mem.freeMB, totalMB: mem.totalMB };
}

function budgetBreach(mem) {
  if (!mem) return null;
  if (maxWsMB && mem.workingSetMB >= maxWsMB) {
    return `Chrome working set ${mem.workingSetMB} MB reached the ${maxWsMB} MB cap`;
  }
  if (minFreeMB && mem.freeMB != null && mem.freeMB < minFreeMB) {
    return `System free RAM ${mem.freeMB} MB fell below the ${minFreeMB} MB floor`;
  }
  return null;
}

async function heap(page) {
  const js = await page.evaluate(() => {
    const mem = performance.memory;
    return mem
      ? { usedMB: mem.usedJSHeapSize / 1048576, totalMB: mem.totalJSHeapSize / 1048576, limitMB: mem.jsHeapSizeLimit / 1048576 }
      : null;
  });
  const metrics = await page.metrics();
  return {
    js,
    metricsMB: {
      jsHeap: metrics.JSHeapUsedSize / 1048576,
      jsHeapTotal: metrics.JSHeapTotalSize / 1048576,
      nodes: metrics.Nodes,
    },
  };
}

async function snapshot(page, browser, tag) {
  const stats = await page.evaluate(() => {
    const text = document.querySelector(".view-status")?.textContent || "";
    const prepare = document.getElementById("scene-prepare-text")?.textContent || "";
    const title = document.getElementById("scene-prepare-title")?.textContent || "";
    const scene = document.getElementById("scene-name")?.textContent?.trim() || "";
    let report = null;
    try {
      report = typeof globalThis.__forgeMemory === "function" ? globalThis.__forgeMemory() : null;
    } catch (error) {
      report = { error: String(error) };
    }
    return { scene, title, prepare, stats: text, report };
  });
  return {
    label: tag,
    at: new Date().toISOString(),
    ...stats,
    heap: await heap(page),
    chrome: chromeMemoryMB(browser.process()?.pid),
  };
}

async function waitForWorkspace(page) {
  await page.waitForSelector("#workspace:not([hidden])", { timeout: 180000 });
  await page.waitForFunction(
    () => {
      const name = document.getElementById("scene-name");
      return name && name.textContent && name.textContent.trim().length > 0;
    },
    { timeout: 180000 },
  );
}

async function currentScene(page) {
  return page.evaluate(() => document.getElementById("scene-name")?.textContent?.trim() || "");
}

async function clickScene(page, pattern) {
  return page.evaluate((want) => {
    const rx = new RegExp(want, "i");
    const buttons = [...document.querySelectorAll("#scene-list button.scene-item")];
    const childFirst = buttons.filter((button) => /child/i.test(button.textContent || ""));
    const pool = [...childFirst, ...buttons];
    const match = pool.find((button) => rx.test(button.textContent || ""));
    if (!match) return null;
    match.click();
    return match.textContent.trim();
  }, pattern);
}

function parseLoadState() {
  const stats = document.querySelector(".view-status")?.textContent || "";
  const prepare = document.getElementById("scene-prepare-text")?.textContent || "";
  const title = document.getElementById("scene-prepare-title")?.textContent || "";
  const live = document.getElementById("viewport")?.classList.contains("is-live");
  const hidden = document.getElementById("scene-prepare")?.hidden !== false;
  const numberAfter = (label) => {
    const match = stats.match(new RegExp("(\\d+) " + label));
    return match ? Number(match[1]) : 0;
  };
  const models = /(\d+) of (\d+) scene models/.exec(prepare);
  return {
    live,
    hidden,
    title,
    prepare,
    stats,
    queued: numberAfter("queued"),
    loading: numberAfter("loading"),
    background: numberAfter("background remaining"),
    backgroundLoading: numberAfter("background loading"),
    loaded: numberAfter("loaded"),
    modelsDone: models ? Number(models[1]) : null,
    modelsTotal: models ? Number(models[2]) : null,
  };
}

async function waitForBackgroundIdle(page, timeout, browser) {
  const deadline = Date.now() + timeout;
  let last = "";
  let stable = 0;
  let previous = "";
  while (Date.now() < deadline) {
    const state = await page.evaluate(parseLoadState);
    last = `${state.title} | ${state.prepare} | ${state.stats} | q=${state.queued} load=${state.loading} bg=${state.background} loaded=${state.loaded}`;
    if (browser) {
      const mem = chromeMemoryMB(browser.process()?.pid);
      const reason = budgetBreach(mem);
      if (reason) {
        console.warn("memory cap", reason, JSON.stringify(mem));
        return { ...state, last, capped: true, capReason: reason, capMemory: mem };
      }
    }
    const modelsDone = state.modelsTotal && state.modelsDone === state.modelsTotal;
    const foregroundIdle = state.queued === 0 && state.loading === 0 && !state.backgroundLoading;
    const queuesClear = foregroundIdle && state.background === 0;
    const residentIdle = foregroundIdle && (state.hidden || /scene ready/i.test(state.title)
      || /visible detail is ready/i.test(state.prepare) || /all scene models are loaded/i.test(state.prepare)
      || (state.background > 0 && state.loaded > 0));
    const done = state.live && state.loaded > 0 && (queuesClear || residentIdle) && (modelsDone || residentIdle || state.hidden);
    if (done) {
      if (previous === last) stable += 1;
      else stable = 0;
      previous = last;
      if (stable >= 2) return { ...state, last };
    } else {
      stable = 0;
      previous = last;
    }
    console.log("load", last);
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  return { timedOut: true, last };
}

async function measureMovingFrame(page) {
  const canvas = await page.$("#viewport canvas, .view-status, #viewport");
  const box = await page.$("#viewport");
  if (box) {
    const size = await box.boundingBox();
    if (size) {
      const x = size.x + size.width / 2;
      const y = size.y + size.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down({ button: "right" });
      for (let i = 0; i < 12; i += 1) {
        await page.mouse.move(x + i * 8, y);
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      await page.mouse.up({ button: "right" });
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  }
  return page.evaluate(parseLoadState);
}

async function samplePoints(page) {
  return page.evaluate((points) => {
    const hook = globalThis.__forgeGround;
    if (!hook || !hook.ready()) return { ready: false, meta: hook && hook.meta(), rows: [] };
    const rows = [];
    for (const point of points) {
      const times = [];
      let y = null;
      for (let i = 0; i < 50; i += 1) {
        const t0 = performance.now();
        y = hook.sample(point.x, point.z, "");
        times.push(performance.now() - t0);
      }
      times.sort((a, b) => a - b);
      rows.push({
        ...point,
        y,
        error: y == null ? null : y - point.want,
        medianUs: times[Math.floor(times.length / 2)] * 1000,
      });
    }
    return { ready: hook.ready(), meta: hook.meta(), rows };
  }, POINTS);
}

const bootMem = systemAndChromeMemory(0);
console.log("boot memory", JSON.stringify(bootMem));
if (!bootMem || bootMem.freeMB < minFreeStartMB) {
  const message = `Refusing to launch Chrome: free RAM ${bootMem && bootMem.freeMB} MB is below the ${minFreeStartMB} MB start floor.`;
  console.error(message);
  await writeFile(out, JSON.stringify({ url, label, error: message, bootMem, started: new Date().toISOString() }, null, 2));
  process.exit(2);
}

const browser = await puppeteer.launch({
  executablePath: chrome,
  headless: true,
  protocolTimeout: 900000,
  args: [
    "--no-first-run",
    "--enable-precise-memory-info",
    "--js-flags=--expose-gc",
    "--window-size=1440,900",
    "--use-angle=d3d11",
  ],
});

const page = await browser.newPage();
page.setDefaultTimeout(360000);
const logs = [];
page.on("console", (msg) => {
  const text = `[${msg.type()}] ${msg.text()}`;
  if (/GPU texture|error|Unsupported texture|memory|heap|WebGPU/i.test(text)) logs.push(text);
  console.log("[page]", text);
});
page.on("pageerror", (error) => {
  logs.push(`[pageerror] ${error.message}`);
  console.error("[pageerror]", error.message);
});
page.on("error", (error) => {
  logs.push(`[crash] ${error.message}`);
  console.error("[crash]", error.message);
});

const result = { url, label, started: new Date().toISOString(), logs, snapshots: [], samples: null, kokiri: null };
try {
  const started = Date.now();
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 180000 });
  await waitForWorkspace(page);
  let opened = await currentScene(page);
  console.log("opened", opened);
  if (!/hyrule field/i.test(opened) || !/child/i.test(opened)) {
    const clicked = await clickScene(page, "hyrule field.*child|child.*hyrule field");
    console.log("clicked field child", clicked);
    if (clicked) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      opened = await currentScene(page);
    }
  }
  result.initialScene = opened;

  const midTimer = setInterval(async () => {
    try {
      const snap = await snapshot(page, browser, "progress");
      result.snapshots.push(snap);
      console.log("progress", JSON.stringify({
        scene: snap.scene,
        prepare: snap.prepare,
        stats: snap.stats,
        heap: snap.heap && snap.heap.js,
        chrome: snap.chrome,
        textures: snap.report && snap.report.textures,
      }));
    } catch (error) {
      console.warn("progress snapshot failed", error);
    }
  }, 60000);

  const load = await waitForBackgroundIdle(page, loadMs, browser);
  clearInterval(midTimer);
  result.load = load;
  result.loadMs = Date.now() - started;
  if (load && load.capped) {
    result.capped = true;
    result.capReason = load.capReason;
    try {
      result.moving = await measureMovingFrame(page);
    } catch (error) {
      result.movingError = String(error);
    }
    const ready = await snapshot(page, browser, "field-capped");
    result.snapshots.push(ready);
    try {
      result.samples = await samplePoints(page);
      await page.screenshot({ path: shot, type: "png" });
      result.screenshot = shot;
    } catch (error) {
      result.capSampleError = String(error);
    }
    result.finished = new Date().toISOString();
    await writeFile(out, JSON.stringify(result, null, 2));
    console.log("capped", load.capReason);
    console.log("wrote", out);
    await browser.close();
    process.exit(2);
  }
  await page.evaluate(() => {
    if (typeof gc === "function") gc();
  });
  result.moving = await measureMovingFrame(page);
  const ready = await snapshot(page, browser, "field-ready");
  result.snapshots.push(ready);
  result.samples = await samplePoints(page);
  await page.screenshot({ path: shot, type: "png" });
  result.screenshot = shot;
  console.log("field-ready", JSON.stringify(ready, null, 2));
  console.log("samples", JSON.stringify(result.samples, null, 2));

  const kokiri = await clickScene(page, "kokiri.*child|child.*kokiri");
  console.log("clicked kokiri", kokiri);
  if (kokiri) {
    const kokiriLoad = await waitForBackgroundIdle(page, 180000, browser);
    await measureMovingFrame(page);
    result.kokiri = { scene: await currentScene(page), load: kokiriLoad, ...(await snapshot(page, browser, "kokiri-ready")) };
    console.log("kokiri", JSON.stringify({ scene: result.kokiri.scene, stats: result.kokiri.stats, heap: result.kokiri.heap, chrome: result.kokiri.chrome }));
  }

  result.finished = new Date().toISOString();
  await writeFile(out, JSON.stringify(result, null, 2));
  console.log("wrote", out);
} catch (error) {
  result.error = String(error && error.stack ? error.stack : error);
  try {
    result.crashSnapshot = await snapshot(page, browser, "crash");
  } catch {
    /* page may already be gone */
  }
  await writeFile(out, JSON.stringify(result, null, 2));
  console.error("failed", result.error);
  throw error;
} finally {
  await browser.close();
}

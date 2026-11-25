import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import net from "node:net";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(frontend, "..");
const chromeBin = process.env.CHROME || "google-chrome";
const python = path.join(repo, ".venv", "bin", "python");

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

function waitForOk(url, timeoutMs) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const step = async () => {
      try {
        const response = await fetch(url);
        if (response.ok || response.status === 404) {
          resolve(response.status);
          return;
        }
      } catch {
        // Still booting.
      }
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`Timed out waiting for ${url}`));
        return;
      }
      setTimeout(step, 200);
    };
    step();
  });
}

function cdp(ws) {
  let id = 0;
  return function send(method, params, timeoutMs = 20000) {
    const messageId = ++id;
    ws.send(JSON.stringify({ id: messageId, method, params }));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${method} timed out`)), timeoutMs);
      function onMessage(event) {
        const body = JSON.parse(event.data);
        if (body.id !== messageId) return;
        clearTimeout(timer);
        ws.removeEventListener("message", onMessage);
        if (body.error) reject(new Error(JSON.stringify(body.error)));
        else resolve(body.result);
      }
      ws.addEventListener("message", onMessage);
    });
  };
}

const PAGE = `
async function pixel(point) {
  const canvas = document.querySelector("canvas");
  if (!canvas || !point) return null;
  const copy = document.createElement("canvas");
  copy.width = canvas.width;
  copy.height = canvas.height;
  const ctx = copy.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(canvas, 0, 0, copy.width, copy.height);
  const x = Math.round(point.x);
  const y = Math.round(point.y);
  if (x < 0 || y < 0 || x >= copy.width || y >= copy.height) return { x, y, rgb: null };
  const data = ctx.getImageData(x, y, 1, 1).data;
  return { x, y, rgb: [data[0], data[1], data[2]] };
}
function dark(rgb) {
  if (!rgb) return false;
  return rgb[1] > rgb[0] + 8 && rgb[1] < 140 && rgb[0] < 90;
}
if (!document.querySelector('[data-actor-id$="0x1000000000000004"]')) {
  if (!globalThis.__forgeOpenedKokiri) {
    const scene = [...document.querySelectorAll(".scene-item")].find((el) => /Kokiri Forest Child/i.test(el.textContent || ""));
    if (scene) {
      globalThis.__forgeOpenedKokiri = true;
      scene.click();
    }
  }
  return "tree row missing";
}
const row = document.querySelector('[data-actor-id$="0x1000000000000004"]');
if (!row) return "tree row missing";
row.click();
const snap = document.getElementById("snap-surface");
snap.checked = true;
snap.dispatchEvent(new Event("change", { bubbles: true }));
if (typeof __forgeSnapDrag !== "function") return "drag hook missing";
let lifted = null;
for (let i = 0; i < 40; i += 1) {
  lifted = __forgeSnapDrag("Y", 1.6);
  const read = lifted && lifted.reads && lifted.reads.find((slot) => slot.shown);
  if (read && Math.abs(read.gpuY - lifted.pivotY) < 0.02 && Math.abs(lifted.pivotY - 1.6) < 0.02) break;
  await new Promise((resolve) => setTimeout(resolve, 250));
}
await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
const liftedPixel = await pixel(lifted && lifted.base);
let dropped = null;
for (let i = 0; i < 20; i += 1) {
  dropped = __forgeSnapDrag("X", 1.6);
  const read = dropped && dropped.reads && dropped.reads.find((slot) => slot.shown);
  if (read && dropped.pivotY < 0.4 && Math.abs(read.gpuY - dropped.pivotY) < 0.02) break;
  await new Promise((resolve) => setTimeout(resolve, 250));
}
await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
const droppedPixel = await pixel(dropped && dropped.base);
const stalePixel = await pixel(lifted && lifted.base);
return { lifted, liftedPixel, dropped, droppedPixel, stalePixel, liftedDark: dark(liftedPixel && liftedPixel.rgb), droppedDark: dark(droppedPixel && droppedPixel.rgb), staleDark: dark(stalePixel && stalePixel.rgb) };
`;

const chromeProbe = spawnSync(chromeBin, ["--version"], { encoding: "utf8" });

test("dragging a Kokiri tree through the viewport keeps the batched mesh on the gizmo", {
  timeout: 120000,
  skip: chromeProbe.status === 0 ? false : "Chrome is not installed",
}, async () => {
  const apiPort = await freePort();
  const vitePort = await freePort();
  const debugPort = await freePort();
  const api = spawn(python, ["-m", "ocarina_forge", "--host", "127.0.0.1", "--port", String(apiPort)], {
    cwd: repo,
    stdio: "ignore",
  });
  const vite = spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(vitePort), "--strictPort"], {
    cwd: frontend,
    env: { ...process.env, OCARINA_FORGE_PORT: String(apiPort) },
    stdio: "ignore",
  });
  const browser = spawn(chromeBin, [
    "--headless=new",
    "--disable-gpu-sandbox",
    "--no-first-run",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    `--user-data-dir=/tmp/ocarina-forge-snap-${debugPort}`,
    `--remote-debugging-port=${debugPort}`,
    "about:blank",
  ], { stdio: "ignore" });
  try {
    await waitForOk(`http://127.0.0.1:${apiPort}/api/status`, 30000);
    const sample = await fetch(`http://127.0.0.1:${apiPort}/api/setup/sample`, { method: "POST" });
    assert.equal(sample.ok, true, await sample.text());
    await waitForOk(`http://127.0.0.1:${vitePort}/`, 30000);
    const version = await (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const browserSocket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      browserSocket.addEventListener("open", resolve);
      browserSocket.addEventListener("error", reject);
    });
    const browserSend = cdp(browserSocket);
    const created = await browserSend("Target.createTarget", { url: `http://127.0.0.1:${vitePort}/?renderer=webgl` });
    const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
    const page = targets.find((item) => item.id === created.targetId);
    browserSocket.close();
    const socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve);
      socket.addEventListener("error", reject);
    });
    const send = cdp(socket);
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", {
      width: 1400,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    });
    let report = null;
    const started = Date.now();
    while (Date.now() - started < 45000) {
      const evaluated = await send("Runtime.evaluate", {
        expression: `(async () => { ${PAGE} })()`,
        awaitPromise: true,
        returnByValue: true,
      }, 70000);
      if (evaluated.exceptionDetails) {
        const text = evaluated.exceptionDetails.text || "";
        if (/tree row missing|drag hook missing/.test(text) || (evaluated.exceptionDetails.exception && /missing/.test(evaluated.exceptionDetails.exception.description || ""))) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          continue;
        }
      }
      const value = evaluated.result && evaluated.result.value;
      if (value === "tree row missing" || value === "drag hook missing") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        continue;
      }
      report = value;
      break;
    }
    async function pose() {
      const evaluated = await send("Runtime.evaluate", {
        expression: "__forgePose()",
        returnByValue: true,
      });
      return evaluated.result && evaluated.result.value;
    }
    async function handle(axis) {
      const evaluated = await send("Runtime.evaluate", {
        expression: `__forgeHandleClient(${JSON.stringify(axis)})`,
        returnByValue: true,
      });
      return evaluated.result && evaluated.result.value;
    }
    async function drag(from, dx, dy) {
      const steps = 8;
      await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: from.x, y: from.y });
      await send("Input.dispatchMouseEvent", {
        type: "mousePressed", x: from.x, y: from.y, button: "left", clickCount: 1,
      });
      for (let i = 1; i <= steps; i += 1) {
        await send("Input.dispatchMouseEvent", {
          type: "mouseMoved",
          x: from.x + (dx * i) / steps,
          y: from.y + (dy * i) / steps,
          button: "left",
          buttons: 1,
        });
      }
      await send("Input.dispatchMouseEvent", {
        type: "mouseReleased",
        x: from.x + dx,
        y: from.y + dy,
        button: "left",
        clickCount: 1,
      });
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    const beforeHandles = await pose();
    const yHandle = await handle("Y");
    await drag(yHandle, 0, -90);
    const liftedHandle = await pose();
    const xHandle = await handle("X");
    await drag(xHandle, 80, 0);
    const snappedHandle = await pose();
    const bossEvaluated = await send("Runtime.evaluate", {
      expression: `(async () => {
        const button = [...document.querySelectorAll(".scene-item")].find((el) => /Boss Rush Internal Room/i.test(el.textContent || ""));
        if (!button) return "boss scene missing";
        button.click();
        for (let i = 0; i < 40; i += 1) {
          const row = document.querySelector('[data-actor-id$="0x1000000000000031"]');
          if (row && typeof __forgeSnapDrag === "function") {
            row.click();
            const snap = document.getElementById("snap-surface");
            snap.checked = true;
            snap.dispatchEvent(new Event("change", { bubbles: true }));
            let pose = null;
            for (let j = 0; j < 40; j += 1) {
              pose = __forgeSnapDrag("X", 6);
              const read = pose && pose.reads && (pose.reads.find((slot) => slot.shown) || pose.reads[0]);
              if (read && pose.pivotY > 2 && pose.pivotY < 3.2 && Math.abs(read.gpuY - pose.pivotY) < 0.05) return pose;
              await new Promise((resolve) => setTimeout(resolve, 250));
            }
            return pose;
          }
          await new Promise((resolve) => setTimeout(resolve, 250));
        }
        return "boss tree missing";
      })()`,
      awaitPromise: true,
      returnByValue: true,
    }, 70000);
    const boss = bossEvaluated.result && bossEvaluated.result.value;
    socket.close();
    report.handleDrag = { before: beforeHandles, yHandle, lifted: liftedHandle, xHandle, snapped: snappedHandle };
    assert.ok(report && report.lifted, `the editor did not report a drag: ${JSON.stringify(report)}`);
    assert.ok(Math.abs(report.lifted.pivotY - 1.6) < 0.02, `Y lift moved the pivot: ${JSON.stringify(report.lifted)}`);
    const liftedRead = report.lifted.reads.find((slot) => slot.shown);
    assert.ok(liftedRead && Math.abs(liftedRead.gpuY - report.lifted.pivotY) < 0.02, JSON.stringify(report.lifted));
    assert.ok(report.dropped.pivotY < 0.4, `sideways snap did not reach the ground: ${JSON.stringify(report.dropped)}`);
    const droppedRead = report.dropped.reads.find((slot) => slot.shown);
    assert.ok(droppedRead && Math.abs(droppedRead.gpuY - report.dropped.pivotY) < 0.02, JSON.stringify(report.dropped));
    assert.ok(Math.abs(report.dropped.pivotY - report.lifted.pivotY) > 0.5, "the snap did not move Y");
    const shownY = (reading) => reading && reading.reads && reading.reads.find((slot) => slot.shown);
    const liftedSlot = shownY(liftedHandle);
    const snappedSlot = shownY(snappedHandle);
    assert.ok(
      liftedHandle && beforeHandles && liftedHandle.pivotY > beforeHandles.pivotY + 0.4,
      `Y handle drag did not lift the pivot: ${JSON.stringify(report.handleDrag)}`,
    );
    assert.ok(liftedSlot && Math.abs(liftedSlot.gpuY - liftedHandle.pivotY) < 0.02, JSON.stringify(liftedHandle));
    assert.ok(
      snappedHandle && snappedHandle.pivotY < liftedHandle.pivotY - 0.4,
      `X handle drag did not snap the pivot down: ${JSON.stringify(snappedHandle)}`,
    );
    assert.ok(snappedSlot && Math.abs(snappedSlot.gpuY - snappedHandle.pivotY) < 0.02, JSON.stringify(snappedHandle));
    assert.ok(boss && boss.pivotY > 2 && boss.pivotY < 3.2, `Boss Rush room did not snap onto its floor: ${JSON.stringify(boss)}`);
    const bossRead = boss.reads && (boss.reads.find((slot) => slot.shown) || boss.reads[0]);
    assert.ok(bossRead && Math.abs(bossRead.gpuY - boss.pivotY) < 0.05, JSON.stringify(boss));
  } finally {
    browser.kill();
    vite.kill();
    api.kill();
  }
});

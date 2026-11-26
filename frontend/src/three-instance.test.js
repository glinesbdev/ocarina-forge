import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("three/webgpu and three/tsl share one Node constructor", async () => {
  const webgpu = await import("three/webgpu");
  const tsl = await import("three/tsl");
  assert.equal(webgpu.Node, tsl.Node);
  assert.equal(typeof webgpu.Node, "function");
  // REVISION is the same on two copies. The id counter is not: the next node
  // from either import must take the next id of this constructor.
  const fromWebgpu = new webgpu.Node();
  const fromTsl = new tsl.Node();
  assert.equal(fromTsl.id, fromWebgpu.id + 1);
  assert.equal(webgpu.REVISION, "187");
});

test("the production bundle has one Node class and a native pick pack", (t) => {
  const assets = path.join(frontendRoot, "dist", "assets");
  if (!existsSync(assets)) {
    // `npm run build` writes this. A source-only run still has the constructor test.
    t.skip("frontend/dist is produced by npm run build");
    return;
  }
  const files = readdirSync(assets).filter((name) => name.endsWith(".js"));
  const nodeClasses = [];
  const tslChunks = new Set();
  const pickChunks = [];
  for (const name of files) {
    const source = readFileSync(path.join(assets, name), "utf8");
    if (source.includes("isNode=!0") || source.includes("isNode = !0") || source.includes("isNode=true")) {
      nodeClasses.push(name);
    }
    const tslImport = source.match(/from"\.\/(three\.tsl-[^"]+\.js)"/);
    if (tslImport) tslChunks.add(tslImport[1]);
    if (name.startsWith("gpu-pick-node-")) pickChunks.push({ name, source });
  }
  assert.deepEqual(nodeClasses, nodeClasses.filter((name) => name.startsWith("three.tsl-")));
  assert.equal(nodeClasses.length, 1, `Node class found in ${nodeClasses.join(", ")}`);
  assert.equal(tslChunks.size, 1, `three/tsl chunks: ${[...tslChunks].join(", ")}`);
  assert.ok(pickChunks.length >= 1);
  for (const chunk of pickChunks) {
    assert.match(chunk.source, /fn pack24\(value: u32\) -> vec4<f32>/);
    assert.match(chunk.source, /\(value \/ 256u\) & 255u/);
    assert.match(chunk.source, /\(value \/ 65536u\) & 255u/);
    assert.doesNotMatch(chunk.source, /cameraProjectionMatrix|cameraViewMatrix|\.bitAnd\(|\.shiftRight\(/);
    assert.match(chunk.source, /from"\.\/three\.tsl-/);
    assert.match(chunk.source, /(?:import|from)"\.\/three\.webgpu-/);
  }
});

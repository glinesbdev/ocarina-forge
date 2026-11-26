import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  collectThreeImportProblems,
  mark,
  oneThreePlugin,
  problemsInSource,
  rawDependency,
  sameThreeBuild,
  tslBuild,
  unmark,
} from "../one-three.js";

test("the webgpu module graph aliases every three entry to one build", () => {
  const webgpu = "/modules/three/build/three.webgpu.js";
  assert.equal(sameThreeBuild("/modules/three/build/three.module.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/modules/three/build/three.webgpu.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/node_modules/.vite/deps/three.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/node_modules/.vite/deps/three_tsl.js", webgpu), tslBuild(webgpu));
  assert.equal(sameThreeBuild("/modules/three/build/three.tsl.js", webgpu), null);
  assert.equal(sameThreeBuild("C:\\modules\\three\\build\\three.tsl.js", webgpu), null);
  assert.equal(sameThreeBuild("C:\\modules\\three\\build\\three.module.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/src/viewport.js", webgpu), null);
  assert.equal(mark("/src/main.js"), "/src/main.js?three=webgpu");
  assert.equal(mark("/src/main.js?x=1"), "/src/main.js?x=1&three=webgpu");
  assert.equal(unmark("/src/main.js?three=webgpu"), "/src/main.js");
  assert.equal(unmark("/src/main.js?x=1&three=webgpu"), "/src/main.js?x=1");
  const webgpuFile = fileURLToPath(new URL("../node_modules/three/build/three.webgpu.js", import.meta.url));
  assert.equal(
    rawDependency("three/addons/controls/OrbitControls.js", webgpuFile),
    path.resolve(path.dirname(webgpuFile), "../examples/jsm/controls/OrbitControls.js"),
  );
  assert.equal(
    rawDependency("three-mesh-bvh", webgpuFile),
    path.resolve(path.dirname(webgpuFile), "../../three-mesh-bvh/src/index.js"),
  );
  assert.equal(rawDependency("./actors.js", webgpuFile), null);
});

test("the WebGPU plugin leaves Vite virtual modules to Vite", async () => {
  const plugin = oneThreePlugin("/modules/three/build/three.webgpu.js");
  const resolved = await plugin.resolveId.call(
    { resolve: async () => ({ id: "\0vite/preload-helper.js" }) },
    "\0vite/preload-helper.js",
    "/src/viewport.js?three=webgpu",
  );
  assert.equal(resolved, null);
  assert.equal(plugin.load("\0vite/preload-helper.js?three=webgpu"), null);
});

test("WebGPU aliases Three.js before Vite can prebundle it in a custom cache", async () => {
  const webgpu = "/modules/three/build/three.webgpu.js";
  const plugin = oneThreePlugin(webgpu);
  const context = { resolve: () => assert.fail("Three.js must resolve directly to the WebGPU build") };
  for (const name of ["three", "three/webgpu"]) {
    assert.equal(await plugin.resolveId.call(context, name, "/src/viewport.js?three=webgpu"), webgpu);
  }
  assert.equal(await plugin.resolveId.call(context, "three", "/src/viewport.js"), null);
});

test("three/tsl uses one id for the query graph and the relative import", async () => {
  // Windows realpath can change drive-letter and directory case. path.join of
  // the unresolved string is then a second module, with a second Node class.
  const webgpu = "C:/Proj/node_modules/three/build/three.webgpu.js";
  const real = "C:/proj/node_modules/three/build/three.tsl.js";
  const plugin = oneThreePlugin(webgpu);
  let calls = 0;
  const context = {
    async resolve(source, importer, options) {
      calls += 1;
      assert.equal(source, "./three.tsl.js");
      assert.equal(importer, webgpu);
      assert.equal(options.skipSelf, true);
      return { id: real };
    },
  };
  const marked = "C:/Proj/src/terrain-node.js?three=webgpu";
  const backslash = "C:\\Proj\\src\\terrain-node.js?three=webgpu";
  assert.equal(await plugin.resolveId.call(context, "three/tsl", marked), real);
  assert.equal(await plugin.resolveId.call(context, "three/tsl", "C:/Proj/src/terrain-node.js"), real);
  assert.equal(await plugin.resolveId.call(context, "three/tsl?three=webgpu", backslash), real);
  assert.equal(
    await plugin.resolveId.call(context, ".\\three.tsl.js", "C:\\Proj\\node_modules\\three\\build\\three.webgpu.js"),
    real,
  );
  assert.equal(calls, 1);
  assert.equal(tslBuild("C:\\Proj\\node_modules\\three\\build\\three.webgpu.js"), "C:/Proj/node_modules/three/build/three.tsl.js");
  assert.equal(tslBuild(webgpu), "C:/Proj/node_modules/three/build/three.tsl.js");
  const fallback = oneThreePlugin(webgpu);
  assert.equal(
    await fallback.resolveId.call({ resolve: async () => null }, "three/tsl", "/src/viewport.js?three=webgpu"),
    tslBuild(webgpu),
  );
});

test("a Windows resolve that returns three.webgpu.js is not used for three/tsl", async () => {
  // The `?three=webgpu` path asked Vite for `./three.tsl.js` and, on Windows,
  // got three.webgpu.js back. That file does not export `reference`.
  const webgpu = "C:\\Users\\gline\\src\\ocarina-forge-gh\\frontend\\node_modules\\three\\build\\three.webgpu.js";
  const plugin = oneThreePlugin(webgpu);
  const context = {
    async resolve() {
      return { id: webgpu };
    },
  };
  const importer = "C:\\Users\\gline\\src\\ocarina-forge-gh\\frontend\\src\\terrain-node.js?three=webgpu";
  const expected = "C:/Users/gline/src/ocarina-forge-gh/frontend/node_modules/three/build/three.tsl.js";
  assert.equal(await plugin.resolveId.call(context, "three/tsl", importer), expected);
  assert.equal(await plugin.resolveId.call(context, "three/tsl", importer.replace("?three=webgpu", "")), expected);
  assert.equal(await plugin.resolveId.call(context, "./three.tsl.js", webgpu), expected);
  const reentered = oneThreePlugin(webgpu);
  const loop = {
    async resolve(source, importerId) {
      return reentered.resolveId.call(loop, source, importerId);
    },
  };
  assert.equal(await reentered.resolveId.call(loop, "three/tsl", importer), expected);
});

test("src imports are names the three entry exports", () => {
  const webgpuFile = fileURLToPath(new URL("../node_modules/three/build/three.webgpu.js", import.meta.url));
  const srcDir = fileURLToPath(new URL("./", import.meta.url));
  assert.deepEqual(collectThreeImportProblems(srcDir, webgpuFile), []);
  assert.deepEqual(collectThreeImportProblems(srcDir, webgpuFile, { includeTests: true }), []);
  const missing = problemsInSource('import { reference } from "three/webgpu";\n', "sample.js", webgpuFile);
  assert.equal(missing.length, 1);
  assert.match(missing[0], /reference/);
  assert.match(missing[0], /three\.webgpu\.js/);
  const unimported = problemsInSource(
    'import { Fn } from "three/tsl";\nmaterialReference("color", "color", material);\n',
    "blend.js",
    webgpuFile,
  );
  assert.ok(unimported.some((problem) => problem.includes("materialReference")));
  const plugin = oneThreePlugin(webgpuFile);
  return plugin.buildStart.call({
    error(message) {
      assert.fail(message);
    },
  });
});

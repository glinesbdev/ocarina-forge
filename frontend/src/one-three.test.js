import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { mark, oneThreePlugin, rawDependency, sameThreeBuild, tslBuild, unmark } from "../one-three.js";

test("the webgpu module graph aliases every three entry to one build", () => {
  const webgpu = "/modules/three/build/three.webgpu.js";
  assert.equal(sameThreeBuild("/modules/three/build/three.module.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/modules/three/build/three.webgpu.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/node_modules/.vite/deps/three.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/node_modules/.vite/deps/three_tsl.js", webgpu), tslBuild(webgpu));
  assert.equal(sameThreeBuild("/modules/three/build/three.tsl.js", webgpu), null);
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
  assert.equal(
    await plugin.resolveId.call(context, "three/tsl", "/src/viewport.js?three=webgpu"),
    tslBuild(webgpu),
  );
  assert.equal(await plugin.resolveId.call(context, "three", "/src/viewport.js"), null);
});

import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { mark, rawDependency, sameThreeBuild, unmark } from "../one-three.js";

test("the webgpu module graph aliases every three entry to one build", () => {
  const webgpu = "/modules/three/build/three.webgpu.js";
  assert.equal(sameThreeBuild("/modules/three/build/three.module.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/modules/three/build/three.webgpu.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/node_modules/.vite/deps/three.js", webgpu), webgpu);
  assert.equal(sameThreeBuild("/node_modules/.vite/deps/three_tsl.js", webgpu), webgpu);
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

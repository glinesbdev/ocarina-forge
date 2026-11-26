import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { pickMask } from "./gpu-pick.js";
import { resetTexturePipelineError, texturePipelineError } from "./texture-debug.js";
import { applyTextureManifest, clearTextureMaps, formatTextureReport, materialKey, presentTextures, restoreShownMaps } from "./texture-apply.js";
import { createTextureQueue, orderTextureJobs, simulateTextureWave, textureSlotLimit } from "./texture-stream.js";

function color(rgb) {
  return {
    rgb: rgb.slice(),
    clone() {
      return color(this.rgb);
    },
    copy(other) {
      this.rgb = other.rgb.slice();
    },
    setRGB(r, g, b) {
      this.rgb = [r, g, b];
    },
  };
}

function material() {
  return {
    map: null,
    normalMap: null,
    roughnessMap: null,
    metalnessMap: null,
    aoMap: null,
    emissiveMap: null,
    color: color([0.6, 0.5, 0.4]),
    alphaTest: 0,
    transparent: false,
    roughness: 0.9,
    metalness: 0,
    needsUpdate: false,
    userData: {},
  };
}

function texture(url) {
  return { url, isTexture: true, disposed: false, dispose() { this.disposed = true; } };
}

test("a failed image request warns once and leaves the material untextured", async () => {
  resetTexturePipelineError();
  const warns = [];
  const original = console.warn;
  console.warn = (message) => warns.push(String(message));
  try {
    const mesh = material();
    const report = await applyTextureManifest(
      [{ materialKey: "Body:0", material: mesh }],
      { materials: { "Body:0": { map: "/api/model-textures/albedo.ktx2" } } },
      async () => {
        throw new Error("texture download failed (404)");
      },
    );
    assert.equal(mesh.map, null);
    assert.equal(report[0].status, "not-applied");
    assert.equal(texturePipelineError(), "texture download failed (404)");
    assert.deepEqual(warns, ["Texture pipeline: texture download failed (404)"]);
  } finally {
    console.warn = original;
    resetTexturePipelineError();
  }
});

test("a water stand-in records the ripple and does not keep the cyan multiply", async () => {
  const mesh = material();
  mesh.transparent = true;
  await applyTextureManifest(
    [{ materialKey: "Sheet:0", material: mesh }],
    {
      materials: {
        "Sheet:0": {
          map: "/api/model-textures/ripple.ktx2",
          standIn: "water",
          texture: "ocarina-forge-water",
          sampler: "_a0",
          format: "generated 16x16",
          color: [1, 1, 1, 0.62],
          alpha: "blend",
        },
      },
    },
    async () => texture("/api/model-textures/ripple.ktx2"),
  );
  assert.equal(mesh.userData.waterStandIn, true);
  assert.equal(mesh.userData.forgeAlbedoName, "ocarina-forge-water");
  assert.equal(mesh.userData.forgeAlbedoSampler, "_a0");
  assert.equal(mesh.userData.forgeAlbedoFormat, "generated 16x16");
  assert.deepEqual(mesh.color.rgb, [1, 1, 1]);
});

test("a blend manifest records the overlay when the geometry file had no extras", async () => {
  const mesh = material();
  await applyTextureManifest(
    [{ materialKey: "Ground:0", material: mesh }],
    {
      materials: {
        "Ground:0": {
          map: "/api/model-textures/base.ktx2",
          moss: "/api/model-textures/grass.ktx2",
          color: [1, 1, 1, 1],
          blend: { channel: "r", uv: "_u0", scale: [4, 4], translate: [0, 0], sampler: "_a1", texture: "Island_Grass_Alb" },
        },
      },
    },
    async (url) => texture(url),
  );
  assert.equal(mesh.map.url, "/api/model-textures/base.ktx2");
  assert.equal(mesh.userData.blendMap.url, "/api/model-textures/grass.ktx2");
  assert.equal(mesh.userData.blend.texture, "Island_Grass_Alb");
  assert.equal(mesh.userData.blend.channel, "r");
  assert.deepEqual(mesh.userData.blend.scale, [4, 4]);
});

test("a UNORM BC7 albedo keeps the linear colour space", async () => {
  const mesh = material();
  await applyTextureManifest(
    [{ materialKey: "Body:0", material: mesh }],
    { materials: { "Body:0": { map: "/api/model-textures/albedo.ktx2", color: [1, 1, 1, 1] } } },
    async (url) => ({ ...texture(url), colorSpace: "srgb-linear", userData: { forgeTransfer: "linear" } }),
  );
  assert.equal(mesh.map.colorSpace, "srgb-linear");
  assert.equal(mesh.map.userData.forgeTransfer, "linear");
});

test("a manifest keys the glTF node without its lod prefix", () => {
  assert.equal(materialKey("lod0:Body:0"), "Body:0");
  assert.equal(materialKey("lod1:Body:0"), "Body:0");
  assert.equal(materialKey("lod0Body0"), "lod0Body0");
  assert.equal(materialKey({ name: "lod0Body0", materialKey: "Body:0" }), "Body:0");
});

test("textures on from the first load assign maps on a sanitized glTF name", async () => {
  const mesh = material();
  const parts = [{
    name: "lod0Body0",
    materialKey: "Body:0",
    material: mesh,
    geometry: { id: "geom" },
  }];
  const geometry = parts[0].geometry;
  await applyTextureManifest(parts, {
    materials: {
      "Body:0": {
        map: "/api/model-textures/albedo.ktx2",
        orm: "/api/model-textures/orm.ktx2",
        color: [1, 1, 1, 1],
      },
    },
  }, async (url) => texture(url));
  assert.equal(parts[0].geometry, geometry);
  assert.equal(mesh.map.isTexture, true);
  assert.equal(mesh.map.url, "/api/model-textures/albedo.ktx2");
  assert.equal(mesh.map.colorSpace, "srgb");
  assert.equal(mesh.map.needsUpdate, true);
  assert.equal(mesh.roughnessMap, mesh.metalnessMap);
  assert.equal(mesh.roughnessMap.colorSpace, "srgb-linear");
  assert.equal(mesh.needsUpdate, true);
});

test("a missing file, a decode error, and a missed mesh are named", async () => {
  const mesh = material();
  const parts = [
    { name: "lod0Hair0", materialKey: "Hair:0", material: mesh },
    { name: "lod0Teeth0", materialKey: "Teeth:0", material: material() },
    { name: "lod0Gone0", materialKey: "Gone:0", material: material() },
    { name: "lod0Other0", materialKey: "Other:0", material: material() },
  ];
  const report = await applyTextureManifest(parts, {
    materials: {
      "Hair:0": { map: "/api/model-textures/hair.ktx2", status: "ready", found: "Tex/Hair.bntx.zs" },
      "Teeth:0": { map: null, status: "decode", error: "decode error: Tex/Teeth.bntx.zs is not a BNTX", found: "Tex/Teeth.bntx.zs" },
      "Gone:0": { map: null, status: "missing", error: "file not found. tried Tex/Gone.bntx.zs", found: "" },
    },
  }, async () => null);
  const note = formatTextureReport(report);
  assert.match(note, /Hair:0: not applied\. The image request failed\./);
  assert.match(note, /Teeth:0: decode error: Tex\/Teeth\.bntx\.zs is not a BNTX/);
  assert.match(note, /Gone:0: file not found\. tried Tex\/Gone\.bntx\.zs/);
  assert.match(note, /Other:0: not applied\. The texture response has no entry for this mesh\./);
  assert.equal(mesh.map, null);
});

test("a value that is not a three.js texture is left off the material", async () => {
  const mesh = material();
  const parts = [{ name: "lod0:Body:0", material: mesh }];
  await applyTextureManifest(parts, {
    materials: { "Body:0": { map: "/api/model-textures/albedo.ktx2", orm: "/api/model-textures/orm.ktx2" } },
  }, async (url) => ({ url }));
  assert.equal(mesh.map, null);
  assert.equal(mesh.roughnessMap, null);
  assert.equal(mesh.needsUpdate, true);
});

test("applying textures does not rebuild geometry and clearing drops them", async () => {
  const mesh = material();
  const parts = [{ name: "lod0:Body:0", material: mesh, geometry: { id: "same" } }];
  const geometry = parts[0].geometry;
  const loads = [];
  await applyTextureManifest(parts, {
    materials: {
      "Body:0": {
        map: "/api/model-textures/albedo.ktx2",
        orm: "/api/model-textures/orm.ktx2",
        alpha: "mask",
        cutoff: 0.4,
        color: [1, 1, 1, 1],
      },
    },
  }, async (url) => {
    loads.push(url);
    return texture(url);
  });
  assert.deepEqual(loads, ["/api/model-textures/albedo.ktx2", "/api/model-textures/orm.ktx2"]);
  assert.equal(parts[0].geometry, geometry);
  assert.equal(mesh.map.url, "/api/model-textures/albedo.ktx2");
  assert.equal(mesh.roughnessMap, mesh.metalnessMap);
  assert.equal(mesh.alphaTest, 0.4);
  assert.deepEqual(mesh.color.rgb, [1, 1, 1]);
  loads.length = 0;
  await applyTextureManifest(parts, {
    materials: { "Body:0": { map: "/api/model-textures/albedo.ktx2" } },
  }, async (url) => {
    loads.push(url);
    return { url };
  });
  assert.deepEqual(loads, []);
  const released = [];
  clearTextureMaps(parts, (texture) => released.push(texture.url));
  assert.deepEqual(released.sort(), ["/api/model-textures/albedo.ktx2", "/api/model-textures/orm.ktx2"]);
  assert.equal(mesh.map, null);
  assert.equal(mesh.roughnessMap, null);
  assert.equal(mesh.alphaTest, 0);
  assert.equal(mesh.roughness, 0.9);
  assert.deepEqual(mesh.color.rgb, [0.6, 0.5, 0.4]);
  assert.equal(parts[0].geometry, geometry);
});

test("a shared material is cloned once per shape key", async () => {
  const shared = material();
  const beard = material();
  const parts = [
    { name: "lod0Body_low0", materialKey: "Body_low:5", material: shared },
    { name: "lod1Body_low0", materialKey: "Body_low:5", material: shared },
    { name: "lod0Face0", materialKey: "Eyelid_Lower_low:2", material: shared },
    { name: "lod0Rock0", materialKey: "RockSmall_Body:4", material: shared },
    { name: "lod0Beard0", materialKey: "BeardA_01_low:3", material: beard },
  ];
  const report = await applyTextureManifest(parts, {
    materials: {
      "Body_low:5": { map: "/api/model-textures/skin.ktx2", color: [1, 1, 1, 1], status: "ready", found: "CmnTex_Chara_Mt_Skin" },
      "Eyelid_Lower_low:2": { map: "/api/model-textures/face.ktx2", color: [1, 1, 1, 1], status: "ready", found: "CmnTex_Chara_Mt_Face" },
      "RockSmall_Body:4": { map: "/api/model-textures/rock.ktx2", color: [1, 1, 1, 1], status: "ready", found: "CmnTex_Chara_Mt_Rock" },
      "BeardA_01_low:3": { map: "/api/model-textures/hair.ktx2", color: [1, 1, 1, 1], status: "ready", found: "Mt_Hair" },
    },
  }, async (url) => texture(url));
  assert.equal(parts[0].material, parts[1].material);
  assert.notEqual(parts[0].material, parts[2].material);
  assert.notEqual(parts[0].material, parts[3].material);
  assert.equal(parts[0].material.map.url, "/api/model-textures/skin.ktx2");
  assert.equal(parts[1].material.map.url, "/api/model-textures/skin.ktx2");
  assert.equal(parts[2].material.map.url, "/api/model-textures/face.ktx2");
  assert.equal(parts[3].material.map.url, "/api/model-textures/rock.ktx2");
  assert.equal(parts[4].material, beard);
  assert.equal(beard.map.url, "/api/model-textures/hair.ktx2");
  const note = formatTextureReport(report);
  assert.match(note, /Body_low:5: applied CmnTex_Chara_Mt_Skin/);
  assert.match(note, /RockSmall_Body:4: applied CmnTex_Chara_Mt_Rock/);
  assert.doesNotMatch(note, /Body_low:5: applied CmnTex_Chara_Mt_Rock/);
});

test("a skin tint is named and a tiling albedo repeats", async () => {
  const mesh = material();
  const parts = [{ name: "lod0Body_low0", materialKey: "Body_low:5", material: mesh }];
  const report = await applyTextureManifest(parts, {
    materials: {
      "Body_low:5": {
        map: "/api/model-textures/skin.ktx2",
        color: [0.72, 0.48, 0.28, 1],
        tint: [0.72, 0.48, 0.28],
        uv: "_u1",
        tile: "2.00×2.00",
        status: "ready",
        found: "Pack/Actor/CmnTex_Chara.pack.zs",
      },
    },
  }, async (url) => texture(url));
  assert.deepEqual(mesh.color.rgb, [0.72, 0.48, 0.28]);
  assert.equal(mesh.map.wrapS, 1000);
  assert.equal(mesh.map.wrapT, 1000);
  const note = formatTextureReport(report);
  assert.match(note, /Body_low:5: applied Pack\/Actor\/CmnTex_Chara\.pack\.zs/);
  assert.match(note, /tint 0\.72 0\.48 0\.28/);
  assert.match(note, /uv _u1/);
  assert.match(note, /repeats 2\.00×2\.00/);
});

test("the texture toggle does not ask the model loader to run", () => {
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const geometry = main.slice(main.indexOf("function geometryRequest"), main.indexOf("viewport = await createViewport"));
  const change = main.slice(main.indexOf("async function applyLoadChange"), main.indexOf("async function openScene"));
  const shown = viewport.slice(viewport.indexOf("function setTextureShown"), viewport.indexOf("function setTextureLoad"));
  assert.match(geometry, /flat: "1"/);
  assert.doesNotMatch(geometry, /textures/);
  assert.match(main, /fetchTextures:/);
  assert.match(main, /textures: load\.textures \? "1" : "0"/);
  assert.match(main, /id="show-textures"|show-textures/);
  assert.match(main, /setTextureShown/);
  assert.match(main, /Load textures/);
  assert.doesNotMatch(change, /invalidateModels/);
  assert.doesNotMatch(change, /forceSceneLoad/);
  assert.match(viewport, /planAssetFetches/);
  assert.match(viewport, /materialKey: identity\.family/);
  assert.match(viewport, /if \(textureAllow && template\) enqueueTexture/);
  assert.match(viewport, /function setTextureShown/);
  assert.match(viewport, /function setTextureLoad/);
  assert.doesNotMatch(shown, /fetchTextures|enqueueTexture|paintActorTextures/);
  assert.doesNotMatch(viewport, /function setTextures/);
  assert.doesNotMatch(viewport, /function invalidateModels/);
  assert.doesNotMatch(viewport, /formatStats/);
});

test("show, hide, and show again swaps resident maps and does not fetch", async () => {
  const mesh = material();
  const batchMate = mesh;
  const parts = [
    { name: "lod0Body0", materialKey: "Body:0", material: mesh, geometry: { id: "geom" } },
    { name: "batch", materialKey: "Body:0", material: batchMate, geometry: { id: "geom" } },
  ];
  let fetches = 0;
  let disposed = 0;
  await applyTextureManifest(parts, {
    materials: {
      "Body:0": { map: "/api/model-textures/albedo.ktx2", alpha: "mask", cutoff: 0.4, color: [1, 1, 1, 1] },
    },
  }, async (url) => {
    fetches += 1;
    return { url, isTexture: true, dispose() { disposed += 1; } };
  });
  const before = fetches;
  const started = performance.now();
  assert.equal(presentTextures(parts, false), 1);
  assert.equal(presentTextures(parts, true), 1);
  assert.equal(presentTextures(parts, false), 1);
  const elapsed = performance.now() - started;
  assert.equal(fetches, before);
  assert.equal(disposed, 0);
  assert.ok(elapsed < 30);
  assert.equal(mesh.map, null);
  assert.equal(mesh.alphaTest, 0);
  assert.equal(mesh.userData.forgeLit.map.isTexture, true);
  assert.equal(pickMask(mesh).map, null);
  presentTextures(parts, true);
  assert.equal(mesh.map.isTexture, true);
  assert.equal(mesh.map.url, "/api/model-textures/albedo.ktx2");
  assert.equal(pickMask(mesh).map, mesh.map);
  assert.equal(batchMate.map, mesh.map);
});

test("restoreShownMaps puts a saved map back on a material the stage bar is showing", () => {
  const held = { isTexture: true };
  const material = { map: null, needsUpdate: false, userData: { forgeLit: { map: held } } };
  assert.equal(restoreShownMaps([{ material }], false), 0);
  assert.equal(material.map, null);
  assert.equal(restoreShownMaps([{ material }, { material }], true), 1);
  assert.equal(material.map, held);
  assert.equal(material.needsUpdate, true);
  assert.equal(restoreShownMaps([{ material }], true), 0);
});

test("a show toggle during the stream reveals what is resident and leaves the rest counted", async () => {
  const near = material();
  const far = material();
  const nearParts = [{ name: "lod0Near0", materialKey: "Near:0", material: near }];
  const farParts = [{ name: "lod0Far0", materialKey: "Far:0", material: far }];
  const requests = [];
  const queue = createTextureQueue();
  queue.setAllow(true);
  queue.note({ gyaml: "Far", url: "/api/models/Far/model.glb?flat=1", distance: 400, visible: false });
  queue.note({ gyaml: "Near", url: "/api/models/Near/model.glb?flat=1", distance: 2, visible: true });
  const [first] = queue.take(1);
  assert.equal(first.gyaml, "Near");
  await applyTextureManifest(nearParts, {
    materials: { "Near:0": { map: "/api/model-textures/near.ktx2", color: [1, 1, 1, 1] } },
  }, async (url) => {
    requests.push(url);
    return { url, isTexture: true, dispose() {} };
  });
  queue.finish(first.gyaml);
  presentTextures(nearParts, false);
  const progress = queue.progress();
  assert.deepEqual(progress, { done: 1, total: 2 });
  const before = requests.length;
  const started = performance.now();
  presentTextures(nearParts, true);
  assert.ok(performance.now() - started < 30);
  assert.equal(requests.length, before);
  assert.equal(near.map.url, "/api/model-textures/near.ktx2");
  assert.equal(far.map, null);
  assert.equal(requests.some((url) => url.includes("model.glb")), false);
});

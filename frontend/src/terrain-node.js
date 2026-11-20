/**
 * TSL port of the GLSL terrain atlas. WebGPU ignores onBeforeCompile, so the
 * node graph has to do the same work: atlas cell, triplanar blend, the sub
 * sheet discard, and the tile-color debug. WebGL keeps the GLSL path.
 */
import {
  Fn,
  abs,
  attribute,
  clamp,
  cos,
  float,
  floor,
  fract,
  materialReference,
  max,
  mix,
  mod,
  modelWorldMatrix,
  normalLocal,
  positionWorld,
  pow,
  reference,
  step,
  texture,
  vec2,
  vec3,
  vec4,
} from "three/tsl";

export function attachTerrainColor(material) {
  if (!material || material.colorNode || !material.map) return material;
  const columns = reference("value", "float", material.userData.terrainColsUniform);
  const repeat = reference("value", "float", material.userData.terrainRepeatUniform);
  const debug = reference("value", "float", material.userData.terrainDebugUniform);
  const sub = reference("value", "float", material.userData.terrainSubUniform);
  const missing = reference("value", "float", material.userData.terrainLayerUniform);
  const atlas = texture(material.userData.terrainMap || material.map);
  const layer = attribute("uv1", "vec2");
  const shade = attribute("uv2", "vec2");
  const mark = attribute("uv3", "vec2");
  const world = positionWorld;
  const normal = normalLocal.transformDirection(modelWorldMatrix).normalize();

  const terrainCell = Fn(([worldUv, slot]) => {
    const columnsSafe = max(columns, float(1));
    const slotFloor = floor(slot.add(0.5));
    const column = mod(slotFloor, columnsSafe);
    const row = floor(slotFloor.div(columnsSafe));
    const inside = fract(worldUv).mul(0.96).add(0.02);
    return vec2(column, row).add(inside).div(columnsSafe);
  });

  const terrainSample = Fn(([worldUv, slot]) => atlas.uv(terrainCell(worldUv, slot)));

  const terrainTri = Fn(([slot]) => {
    const blend = pow(max(abs(normal), vec3(0.0001)), vec3(4));
    const weight = blend.div(blend.x.add(blend.y).add(blend.z));
    const alongX = terrainSample(world.zy.div(repeat), slot);
    const alongY = terrainSample(world.xz.div(repeat), slot);
    const alongZ = terrainSample(world.xy.div(repeat), slot);
    return alongX.mul(weight.x).add(alongY.mul(weight.y)).add(alongZ.mul(weight.z));
  });

  material.colorNode = Fn(() => {
    mark.y.greaterThan(0.5).and(missing.lessThan(0.5)).and(sub.lessThan(0.5)).discard();
    const sampled = mix(terrainTri(layer.x), terrainTri(layer.y), clamp(shade.x, 0, 1));
    const stripe = step(0.5, fract(world.x.add(world.z).mul(0.15)));
    const warn = mix(vec3(0.75, 0.2, 0.55), vec3(0.25, 0.12, 0.18), stripe);
    const hue = mark.x.mul(6.2831853);
    const ink = clamp(
      vec3(
        float(0.55).add(cos(hue).mul(0.45)),
        float(0.55).add(cos(hue.add(2.094395)).mul(0.45)),
        float(0.55).add(cos(hue.add(4.18879)).mul(0.45)),
      ),
      vec3(0),
      vec3(1),
    );
    const marked = mix(ink, mix(ink, vec3(1), 0.45), step(0.5, mark.y));
    const debugColor = mix(vec4(marked, 1), vec4(warn, 1), step(0.5, missing));
    const sampledOrDebug = mix(sampled, debugColor, step(0.5, debug));
    const tint = materialReference("color", "color", material);
    return vec4(sampledOrDebug.rgb.mul(tint), sampledOrDebug.a);
  })();
  material.userData.terrainNode = true;
  return material;
}

/**
 * Scene lighting resolved for the viewport.
 *
 * Captured numbers come from the lighting-capture mod. Fields it could not
 * read stay null, and the editor default fills those in. The default is not
 * a measurement from the game.
 */

export const LIGHT_KEYS = ["sun", "hemisphere", "ambient", "exposure", "tonemap", "fog", "sky", "env"];

export const EDITOR_LIGHTING = {
  sun: { color: "#fff2d6", intensity: 2.6, position: [18, 36, 14] },
  hemisphere: { sky: "#d5e0d4", ground: "#4a4034", intensity: 0.45 },
  ambient: { color: "#8ea090", intensity: 0.08 },
  exposure: 1.05,
  tonemap: "aces",
  fog: null,
  sky: null,
  env: null,
};

const TONEMAPS = new Set(["aces", "linear", "none", "reinhard", "cineon"]);

export function isEffectGizmo(actor) {
  const name = (actor && actor.gyaml) || "";
  if (!name) return false;
  if (name.includes("Fog")) return true;
  if (name.startsWith("EftLoc_") || name.includes("Locator")) return true;
  return name.startsWith("Eft") || actor.category === "effect";
}

export function tonemapName(value) {
  const name = String(value || "").toLowerCase();
  return TONEMAPS.has(name) ? name : "aces";
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function distance2(sample, focus) {
  if (!focus || !sample) return Infinity;
  const x = finite(sample.x);
  const z = finite(sample.z);
  if (x === null || z === null) return Infinity;
  const dx = x - Number(focus.x || 0);
  const dz = z - Number(focus.z || 0);
  return dx * dx + dz * dz;
}

export function nearestSample(samples, focus) {
  const rows = Array.isArray(samples) ? samples.filter((row) => row && typeof row === "object") : [];
  if (!rows.length) return null;
  if (!focus) return rows[0];
  let best = rows[0];
  let bestDistance = distance2(best, focus);
  for (const row of rows.slice(1)) {
    const distance = distance2(row, focus);
    if (distance < bestDistance) {
      best = row;
      bestDistance = distance;
    }
  }
  return best;
}

function sampleHasLight(sample) {
  if (!sample) return false;
  return LIGHT_KEYS.some((key) => sample[key] != null);
}

export function lightingFields(sample) {
  const fromCapture = [];
  const editorDefault = [];
  for (const key of LIGHT_KEYS) {
    if (sample && sample[key] != null) fromCapture.push(key);
    else editorDefault.push(key);
  }
  return { fromCapture, editorDefault };
}

/**
 * Merge one capture sample over the editor default.
 * `focus` is `{x, z}` in metres (the orbit target). A sample with no sun,
 * fog, or other light field does not replace the default.
 * `options.editorDefaults` draws the editor lights even when a reference exists.
 */
export function resolveLighting(payload, focus, options = {}) {
  const samples = (payload && payload.samples) || [];
  const sample = nearestSample(samples, focus);
  const lighting = {
    sun: { ...EDITOR_LIGHTING.sun, position: EDITOR_LIGHTING.sun.position.slice() },
    hemisphere: { ...EDITOR_LIGHTING.hemisphere },
    ambient: { ...EDITOR_LIGHTING.ambient },
    exposure: EDITOR_LIGHTING.exposure,
    tonemap: EDITOR_LIGHTING.tonemap,
    fog: null,
    sky: null,
    env: null,
  };
  const ignore = Boolean(options.editorDefaults);
  let applied = false;
  if (sample && !ignore) {
    for (const key of LIGHT_KEYS) {
      if (sample[key] != null) {
        lighting[key] = sample[key];
        applied = true;
      }
    }
  }
  const source = ignore ? "editor-default" : applied ? "capture" : sample ? "capture-partial" : "editor-default";
  return {
    lighting,
    source,
    sample: applied ? sample : null,
    timeOfDay: sample && finite(sample.timeOfDay),
    sampleCount: samples.length,
    note: lightingNote(source, samples.length, payload, { editorDefaults: ignore }),
  };
}

export function lightingNote(source, count, payload, options = {}) {
  const map = payload && payload.map ? String(payload.map) : "";
  const path = payload && payload.referencePath ? String(payload.referencePath) : "";
  const found = Boolean(payload && payload.referenceFound) || count > 0;
  const fields = lightingFields(found ? nearestSample((payload && payload.samples) || [], null) : null);
  const where = [map, count ? `${count} sample${count === 1 ? "" : "s"}` : "0 samples", path].filter(Boolean).join(" · ");
  const fromFile = fields.fromCapture.length ? fields.fromCapture.join(", ") : "none";
  const defaults = fields.editorDefault.join(", ");
  if (options.editorDefaults && found) {
    return `Showing editor defaults. Reference on disk: ${where}. From the file: ${fromFile}. Editor default: ${defaults}.`;
  }
  if (!found) {
    const expected = path ? ` Expected ${path}.` : "";
    return `No reference file${map ? ` for ${map}` : ""}. Editor defaults.${expected}`;
  }
  if (source === "capture") {
    return `Using reference ${where}. From the file: ${fromFile}. Editor default: ${defaults || "none"}.`;
  }
  return `Using reference ${where}. From the file: ${fromFile}. Editor default: ${defaults}.`;
}

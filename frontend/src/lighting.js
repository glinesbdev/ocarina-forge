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

/**
 * Merge one capture sample over the editor default.
 * `focus` is `{x, z}` in metres (the orbit target). A sample with no sun,
 * fog, or other light field does not replace the default.
 */
export function resolveLighting(payload, focus) {
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
  let applied = false;
  if (sample) {
    for (const key of LIGHT_KEYS) {
      if (sample[key] != null) {
        lighting[key] = sample[key];
        applied = true;
      }
    }
  }
  const source = applied ? "capture" : sample ? "capture-partial" : "editor-default";
  return {
    lighting,
    source,
    sample: applied ? sample : null,
    timeOfDay: sample && finite(sample.timeOfDay),
    sampleCount: samples.length,
    note: lightingNote(source, samples.length, payload),
  };
}

export function lightingNote(source, count, payload) {
  if (source === "capture") return `Captured lighting (${count} sample${count === 1 ? "" : "s"}).`;
  if (source === "capture-partial") {
    return `Capture has ${count} sample${count === 1 ? "" : "s"} (time of day and effects). Sun, fog, and exposure were not in the game API.`;
  }
  const notes = payload && payload.notes;
  if (Array.isArray(notes) && typeof notes[0] === "string" && notes[0]) return notes[0];
  const unavailable = payload && payload.unavailable;
  if (Array.isArray(unavailable) && unavailable.length) {
    return "Editor lighting. This scene has no captured sun or fog.";
  }
  return "Editor lighting. No capture for this scene.";
}

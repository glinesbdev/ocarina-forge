/** Local walkable-surface sampling for Snap, backed by a wasm GroundSurface. */

let wasmReady = null;
let wasmMod = null;
let surface = null;
let loadToken = 0;
let lastMeta = null;

function syncDevHook() {
  if (import.meta.env && import.meta.env.DEV) {
    globalThis.__forgeGround = {
      ready: groundSurfaceReady,
      meta: groundSurfaceMeta,
      sample: sampleGroundSurface,
      dispose: disposeGroundSurface,
    };
  }
}


export function parseActorHash(value) {
  if (value == null || value === "") return { lo: 0, hi: 0, value: 0n };
  const text = String(value).trim();
  let number;
  try {
    number = text.toLowerCase().startsWith("0x") ? BigInt(text) : BigInt(text);
  } catch {
    return { lo: 0, hi: 0, value: 0n };
  }
  const masked = number & 0xffffffffffffffffn;
  return {
    lo: Number(masked & 0xffffffffn),
    hi: Number((masked >> 32n) & 0xffffffffn),
    value: masked,
  };
}

export async function ensureGroundWasm() {
  if (!wasmReady) {
    const moduleUrl = new URL("../wasm/ocarina_forge_native.js", import.meta.url);
    const wasmUrl = new URL("../wasm/ocarina_forge_native_bg.wasm", import.meta.url);
    wasmReady = import(moduleUrl)
      .then((mod) => {
        wasmMod = mod;
        return mod.default({ module_or_path: wasmUrl });
      })
      .catch((error) => {
        wasmReady = null;
        wasmMod = null;
        throw error;
      });
  }
  return wasmReady;
}

async function readSurfaceBuffer(response) {
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  if (bytes.length >= 4 && bytes[0] === 0x4f && bytes[1] === 0x46 && bytes[2] === 0x47 && bytes[3] === 0x53) {
    return bytes;
  }
  if (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b && typeof DecompressionStream === "function") {
    const stream = new Response(buffer).body.pipeThrough(new DecompressionStream("gzip"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  throw new Error("The ground surface pack was not a recognised OFGS buffer.");
}

export function disposeGroundSurface() {
  loadToken += 1;
  if (surface && typeof surface.free === "function") {
    try {
      surface.free();
    } catch {
      /* already freed */
    }
  }
  surface = null;
  lastMeta = null;
  syncDevHook();
}

export function groundSurfaceReady() {
  return Boolean(surface);
}

export function groundSurfaceMeta() {
  syncDevHook();
  return lastMeta;
}

export function sampleGroundSurface(x, z, excludeId) {
  if (!surface) return null;
  const hash = parseActorHash(excludeId);
  const y = surface.sample(x, z, hash.lo, hash.hi);
  return Number.isFinite(y) ? y : null;
}

export async function loadGroundSurface(url) {
  const token = ++loadToken;
  await ensureGroundWasm();
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`The ground surface pack could not be loaded (${response.status}).`);
  }
  const bytes = await readSurfaceBuffer(response);
  if (token !== loadToken) return null;
  const next = new wasmMod.WasmGroundSurface(bytes);
  if (token !== loadToken) {
    next.free?.();
    return null;
  }
  if (surface && typeof surface.free === "function") {
    try {
      surface.free();
    } catch {
      /* replaced */
    }
  }
  surface = next;
  lastMeta = {
    url,
    triangles: next.triangleCount,
    vertices: next.vertexCount,
    tiles: next.tileCount,
    memoryBytes: next.memoryBytes(),
    packBytes: bytes.byteLength,
    heap: performance.memory
      ? { usedMB: performance.memory.usedJSHeapSize / 1048576, totalMB: performance.memory.totalJSHeapSize / 1048576 }
      : null,
  };
  syncDevHook();
  return lastMeta;
}

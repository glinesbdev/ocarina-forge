/**
 * Counts WebGPU pipeline creations and material.version changes. `needsUpdate`
 * has no getter; assigning it increments `version`. A quiet frame after the
 * load is the number to trust. Later pipeline or version changes are logged
 * a few times, not on every frame.
 */
function wrapCount(object, name, state, field) {
  if (!object || typeof object[name] !== "function" || object[name].__forgeCount) return;
  const original = object[name].bind(object);
  const wrapped = (...args) => {
    state[field] += 1;
    return original(...args);
  };
  wrapped.__forgeCount = true;
  object[name] = wrapped;
}

export function createGpuProfile(renderer) {
  const device = renderer && renderer.backend && renderer.backend.device;
  const state = {
    made: 0,
    frames: 0,
    quiet: 0,
    logged: 0,
    versions: new WeakMap(),
    device: device || null,
    armed: false,
  };
  if (!device) return state;
  if (typeof device.createRenderPipeline === "function") {
    const original = device.createRenderPipeline.bind(device);
    device.createRenderPipeline = (descriptor) => {
      state.made += 1;
      return original(descriptor);
    };
  }
  if (typeof device.createRenderPipelineAsync === "function") {
    const original = device.createRenderPipelineAsync.bind(device);
    device.createRenderPipelineAsync = (descriptor) => {
      state.made += 1;
      return original(descriptor);
    };
  }
  return state;
}

/**
 * Count writeTexture and createBindGroup. Called when Profile or the Field
 * benchmark turns on. Until then those methods are the originals.
 */
export function armFrameCounters(state) {
  if (!state || state.armed) return state;
  state.armed = true;
  state.writeTex = 0;
  state.binds = 0;
  state.last = { pipelines: 0, versions: 0, writeTex: 0, binds: 0 };
  const device = state.device;
  if (!device) return state;
  wrapCount(device.queue, "writeTexture", state, "writeTex");
  wrapCount(device, "createBindGroup", state, "binds");
  return state;
}

export function sampleGpuFrame(state, { calls = 0, materials = [], loading = false } = {}) {
  if (!state) return "";
  state.frames += 1;
  const made = state.made;
  state.made = 0;
  let churn = 0;
  for (const material of materials) {
    if (!material) continue;
    const version = material.version || 0;
    const seen = state.versions.get(material);
    if (seen === undefined) state.versions.set(material, version);
    else if (seen !== version) {
      churn += 1;
      state.versions.set(material, version);
    }
  }
  if (state.armed) {
    state.last = {
      pipelines: made,
      versions: churn,
      writeTex: state.writeTex || 0,
      binds: state.binds || 0,
    };
    state.writeTex = 0;
    state.binds = 0;
  }
  if (loading) {
    state.quiet = 0;
    return "";
  }
  const interesting = made > 0 || churn > 0 || calls > 1;
  if (!interesting) {
    state.quiet += 1;
    if (state.quiet === 2 && state.logged < 1) {
      state.logged += 1;
      const line = `WebGPU profile: render calls ${calls}, pipelines this frame 0, material versions stable`;
      console.info(line);
      return line;
    }
    return "";
  }
  if (state.logged >= 6) return "";
  state.logged += 1;
  const line = `WebGPU profile: render calls ${calls}, pipelines this frame ${made}, material version changes ${churn}`;
  console.info(line);
  return line;
}

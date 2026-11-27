/**
 * Resolve `sharp` to the stub before `@gltf-transform/functions` evaluates it.
 */
const stub = new URL("./sharp-stub.mjs", import.meta.url).href;

export function resolve(specifier, context, nextResolve) {
  if (specifier === "sharp") {
    return { shortCircuit: true, url: stub };
  }
  return nextResolve(specifier, context);
}

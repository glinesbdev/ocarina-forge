/**
 * Tile bake entry. Registers the sharp stub, then runs the job.
 *
 *   node frontend/tools/bake-scene-tiles.mjs --selftest
 *   node frontend/tools/bake-scene-tiles.mjs job.json
 */
const moduleApi = await import("node:module");
if (typeof moduleApi.registerHooks === "function") {
  const hook = await import("./optional-sharp-hook.mjs");
  moduleApi.registerHooks({ resolve: hook.resolve });
} else {
  const registered = moduleApi.register("./optional-sharp-hook.mjs", import.meta.url);
  if (registered && typeof registered.then === "function") await registered;
}

const { runCli } = await import("../src/bake-scene-tiles.mjs");
await runCli();

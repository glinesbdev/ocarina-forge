# Validate a Forge scene against Longshot

Use the same game build, map variant, and save conditions for both views. Forge can inspect authored BANC placements, its generated ground, and the models it draws. Longshot can sample live actor transforms and capture the game's rendered frame. Comparing those records helps distinguish a bad placement from an actor that moves after spawning or scenery that draws through a separate system.

## Sample live actors

Run a Longshot game test from a **separate runtime and a copied test save**. Keep the runtime logs and captures local. The example uses the repository's `market-entrance` save and teleports to the Lake Hylia fishing pond in `HyruleField_Child`. Follow Longshot's game-test setup for the checkout that built your runtime.

Create a temporary mod folder outside the Forge repository with the following `mod.json`.

```json
{
  "id": "local/forge-scene-probe",
  "name": "Forge Scene Probe",
  "version": "0.1.0",
  "api": "0.1",
  "runtime": "js",
  "entry": "main.js",
  "game": { "builds": ["*"] },
  "permissions": ["state.read", "actors.read"]
}
```

Put `export {};` in `main.js`. Put the probe in `test/scene.test.js`.

```js
import log from "longshot/log";

const files = [
  "Animal_Fish_Bass",
  "Animal_Fish_Loach",
  "Obj_LakeHrl_WaterPlane_A_07",
];
const limit = 64;

export default {
  "Sample Lake Hylia fishing pond": {
    game: true,
    timeout: 12000,
    async run(game) {
      await game.start();
      await game.teleport("HyruleField_Child", { x: -92, y: 138, z: 511 });
      await game.frames(120);

      for (const sample of ["settled", "later"]) {
        const { frame, map, age, timeOfDay } = game.state();
        const link = game.player()?.position;
        log.info("FORGE_RUNTIME_STATE " + JSON.stringify({
          sample, frame, map, age, timeOfDay, link,
        }));

        for (const file of files) {
          const actors = game.actors({ file, limit });
          log.info("FORGE_RUNTIME_COUNT " + JSON.stringify({
            sample, frame, file, count: actors.length, limit,
          }));
          for (const { key, position, rotation, scale, velocity } of actors) {
            log.info("FORGE_RUNTIME_ACTOR " + JSON.stringify({
              sample, frame, file, key, position, rotation, scale, velocity,
            }));
          }
        }

        if (sample === "settled") await game.frames(90);
      }
    },
  },
};
```

From the Longshot checkout, point the test runner at the mod folder and the separate runtime. Add `--window` to watch it. The game exits when the test finishes.

```powershell
python -I scripts/dev.py game-tests "C:\path\to\forge-scene-probe" --runtime "C:\path\to\test-runtime" --save market-entrance
```

Longshot collects the test result and game log in its `runs/game-tests` directory. Search the game log for `FORGE_RUNTIME_`. The count records make empty queries visible. A count at the limit can be truncated. Each actor gets its own log line to avoid truncating a whole school of fish.

## Compare the sample to Forge

1. In Forge, open the same age and map with the Static and Dynamic BANCs together. Select the matching actor and open **Problems → Audit meshes → Mesh audit JSON**. Record the authored position, the display position if it differs, the source BANC and hash, and the resolved model and LOD.
2. Match the actor recipe by `file`. Runtime `type` may identify a category shared by several files. Positions are world-space metres with Y up. Use the runtime `key` to follow an actor between frames of the same run. Compare unique stationary actors by file and nearby position. For moving groups or repeated files, compare the distribution and mark ambiguous instance matches. A runtime key has no established mapping to a BANC hash.
3. Treat a mismatch according to what was measured. A stable transform offset merits checking placement, model-local transforms and the selected LOD. Changing positions may reflect movement after spawn. An empty registry query can mean the actor is inactive, out of range, or drawn through a different path. Check the native frame and Forge's model resolution before calling it missing.

At the fishing pond, Longshot reported the water-plane actor pivot at Y ≈ 132.643 m, which agreed with Forge's measured pond surface there. Sampled bass appeared below that level even though the authored spawns were above it. That supports Forge's submerged fish preview while preserving their authored spawn positions. It does not establish an individual match for every fish or the shape of the entire water surface. See [Forge's terrain and fish preview notes](formats.md#terrainarc).

## Cross-check authored placements

Longshot's `content.read.placements(map, { actor, param })` reads the effective Static and Dynamic BANCs through the game's mod layers. It returns the source file, placement ID, layer, actor recipe, position, and placement parameters. Use it once per map or with an actor filter, then compare to Forge by **source BANC and placement ID**. An unmodified comparison needs both tools pointed at equivalent base data. To validate a built mod, compare Forge's intended placements against a runtime with the same mod layers enabled. This complements the live actor probe and helps diagnose missing or mispositioned objects before spawn behavior enters the comparison. See [Longshot's content reference](https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/mod-api/docs/modding/content.md#reading-the-games-data).

For a visual cross-check, `gfx.world` can mark a world coordinate or follow a live actor. It can show a spawn marker beside a moving fish in the same capture. `gfx.effect` adds a mod's fullscreen shader pass. The modding docs reviewed here give no placement reader for the game's native particle emitters. See [Longshot's world drawing reference](https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/mod-api/docs/modding/world.md) and [screen effects reference](https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/mod-api/docs/modding/gfx.md).

## Compare terrain and water

1. **Record the authored inputs.** From the Forge environment configured for the same dump, use `--resources-only` to list the TSCB records covering an X/Z point. Run the same command without that flag to inspect ground heights. Sample both sides of the mismatch, including a known-good reference point. Keep the main terrain and sub terrain separate in the report.

   ```powershell
   $banc = "Banc/HyruleField/HyruleField_Child_Dynamic.bcett.byml.zs"
   python -m ocarina_forge ground-debug $banc --resources-only --at=-72,510 --json
   python -m ocarina_forge ground-debug $banc --at=-72,510 --json
   ```

2. **Capture the native view.** On a test run, hold a camera pose with `longshot/camera` using `override()`, `setPosition()` and `setTarget()`. Log `camera.state()` after the override takes effect, hold the pose long enough for capture, and release it in a `finally` block. Match eye, target, field of view, and aspect ratio in Forge. Use the control bridge's `shot` command for the native frame. If the frame alone cannot distinguish ground from water or actor scenery, capture one known Vulkan present with `RX_VK_DUMP=<present>` and `RX_VK_DUMP_DIR` in a local run folder. `RX_VK_PASS_TRACE=1` can help identify the pass. Keep Link near the target so the game's normal scene streaming can load it.
3. **Isolate the source of the difference.** In Forge, capture the same view with **Ground**, **Sub terrain**, and **Water** toggled independently. Compare the visible shore, floor, or entrance against the native frame and the X/Z samples. A water actor pivot is one transform. The drawn surface requires geometry or render evidence. Swim volumes, collision floors, and render surfaces need their own evidence.
4. **Measure native collision or water queries separately.** The installed build used for the pond comparison had no public implementation of `world.ray`. Upstream PR #2422 describes `world.ray` and `world.rayAll` with hit kinds and layer filters. The author reports that 14 Lake Hylia rays hit water with the default filter and reached the lakebed with `water: false`. On a build that reports `has("world.ray")` and supports the documented filters, cast down at each X/Z point with water enabled and again with it disabled. Record both hits, their kinds and height, and compare them to the matched frame and Forge's sampled heights. This measures native collision surfaces. Swim volumes, visual geometry, and the TSCB tile decoder still need separate evidence. See [Longshot's world query implementation](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/2422).

These are on-demand diagnostics and add no work to Forge boot. The installed OoT TSCB survey found no `.water.extm` resources. Forge currently draws water from BFRES actor models, and the meaning of `.custom.extm` remains open. Keep a mismatch open until the runtime evidence identifies which source or layer differs. See [TerrainArc formats](formats.md#terrainarc) and [matched draw diagnostics](performance.md).

Longshot references in a sibling checkout include the [game-test runner](../../longshot/docs/dev/game-tests.md), [actor API](../../longshot/docs/modding/reference.md#longshotactors), [camera API](../../longshot/docs/modding/reference.md#longshotcamera), [control bridge](../../longshot/docs/dev/reference/control-bridge.md#shot), and [Vulkan debug switches](../../longshot/docs/dev/developing.md#debug).

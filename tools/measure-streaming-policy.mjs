/** Reprint the synthetic Field orbit used to compare the residency policy. Does not open a scene. */
import { simulateFieldOrbit } from "../frontend/src/resident-budget.js";

const orbit = simulateFieldOrbit();
const gb = (bytes) => `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
const line = (label, row) => {
  console.log(
    `${label}\tevicted ${row.evictions}\treloaded ${row.reloads}\trapid ${row.rapidReloads}\t`
    + `updates ${row.recomputes}\tpeak ${gb(row.peakBytes)}\tmeshes ${row.peakCount}`,
  );
};

console.log(`models ${orbit.modelCount}  steps ${orbit.steps}  (${orbit.stepDegrees}°)`);
line("before", orbit.before);
line("after ", orbit.after);

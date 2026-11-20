import { rendererChoice, stripLegacyGpu } from "./backend.js";
import { migrateStorage } from "./storage.js";

try {
  migrateStorage(localStorage);
  localStorage.removeItem("ocarinaforge.webgpu");
  localStorage.removeItem("bancwright.webgpu");
} catch {
  /* private mode has no storage */
}

const search = globalThis.location ? globalThis.location.search : "";
const cleaned = stripLegacyGpu(search);
if (cleaned !== null && globalThis.location && cleaned !== search) {
  globalThis.location.replace(`${globalThis.location.pathname}${cleaned}${globalThis.location.hash || ""}`);
} else if (rendererChoice(search) === "webgpu") {
  await import("./main.js?three=webgpu");
} else {
  await import("./main.js");
}

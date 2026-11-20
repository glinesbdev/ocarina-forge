import { rendererChoice } from "./backend.js";

const choice = rendererChoice(globalThis.location ? globalThis.location.search : "");
if (choice === "webgpu") await import("./main.js?three=webgpu");
else await import("./main.js");

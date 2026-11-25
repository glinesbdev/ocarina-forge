import { createReadStream, existsSync, cpSync } from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";
import { oneThreePlugin } from "./one-three.js";

const basisDir = path.resolve("node_modules/three/examples/jsm/libs/basis");

// COOP/COEP so the page is crossOriginIsolated and can use SharedArrayBuffer
// with a wasm worker pool. /api is same-origin through the Vite proxy.
// credentialless is enough for isolation and is gentler on any future
// cross-origin texture or model URLs than require-corp.
const isolationHeaders = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "credentialless",
  "Cross-Origin-Resource-Policy": "same-origin",
};

function basisTranscoder() {
  const serve = (req, res, next) => {
    const name = (req.url || "/").split("?")[0].replace(/^\//, "");
    if (!name || name.includes("..")) return next();
    const file = path.join(basisDir, name);
    if (!file.startsWith(basisDir) || !existsSync(file)) return next();
    res.setHeader("Content-Type", name.endsWith(".wasm") ? "application/wasm" : "text/javascript");
    res.setHeader("Cross-Origin-Resource-Policy", "same-origin");
    createReadStream(file).pipe(res);
  };
  return {
    name: "basis-transcoder",
    configureServer(server) {
      server.middlewares.use("/basis", serve);
    },
    configurePreviewServer(server) {
      server.middlewares.use("/basis", serve);
    },
    closeBundle() {
      if (existsSync(basisDir)) cpSync(basisDir, path.resolve("dist/basis"), { recursive: true });
    },
  };
}

const threeWebgpu = path.resolve("node_modules/three/build/three.webgpu.js").replaceAll("\\", "/");

const apiPort = process.env.BANCWRIGHT_PORT || "47232";

export default defineConfig(({ mode }) => ({
  plugins: [
    ...(mode === "webgl"
      ? [{
          name: "webgl-external-gpu-nodes",
          enforce: "pre",
          resolveId(source) {
            if (source === "three/webgpu" || source === "three/tsl") return { id: source, external: true };
          },
        }]
      : [oneThreePlugin(threeWebgpu)]),
    basisTranscoder(),
  ],
  build: {
    target: "es2022",
  },
  server: {
    host: "0.0.0.0",
    port: 47231,
    strictPort: true,
    headers: isolationHeaders,
    proxy: {
      "/api": `http://127.0.0.1:${apiPort}`,
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 47231,
    strictPort: true,
    headers: isolationHeaders,
    proxy: {
      "/api": `http://127.0.0.1:${apiPort}`,
    },
  },
}));

import { createReadStream, existsSync, cpSync } from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";
import { oneThreePlugin } from "./one-three.js";

const basisDir = path.resolve("node_modules/three/examples/jsm/libs/basis");

function basisTranscoder() {
  const serve = (req, res, next) => {
    const name = (req.url || "/").split("?")[0].replace(/^\//, "");
    if (!name || name.includes("..")) return next();
    const file = path.join(basisDir, name);
    if (!file.startsWith(basisDir) || !existsSync(file)) return next();
    res.setHeader("Content-Type", name.endsWith(".wasm") ? "application/wasm" : "text/javascript");
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
    proxy: {
      "/api": "http://127.0.0.1:47232",
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 47231,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:47232",
    },
  },
}));

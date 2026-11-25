import { defineConfig } from "vite";

// The bench must be crossOriginIsolated so SharedArrayBuffer works.
// credentialless is enough for isolation and does not require CORP on every
// subresource. The pack, fixtures, and wasm all load from this origin.
const isolationHeaders = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "credentialless",
  "Cross-Origin-Resource-Policy": "same-origin",
};

export default defineConfig({
  root: ".",
  server: {
    host: "127.0.0.1",
    port: 47241,
    strictPort: true,
    headers: isolationHeaders,
  },
  preview: {
    host: "127.0.0.1",
    port: 47241,
    strictPort: true,
    headers: isolationHeaders,
  },
  build: {
    target: "es2022",
  },
});

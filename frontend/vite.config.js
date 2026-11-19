import { defineConfig } from "vite";

export default defineConfig({
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
});

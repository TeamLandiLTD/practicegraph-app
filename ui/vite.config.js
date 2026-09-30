import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

// The window compares this with the engine's version: an engine left
// running from an older install serves the new page with old code.
const APP_VERSION = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")).version;

// Built assets are served by `practicegraph ui serve` from <root>/webui —
// fully self-contained, relative paths, no CDN, no external requests.
export default defineConfig({
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(APP_VERSION) },
  base: "./",
  test: {
    environment: "jsdom",
    setupFiles: [],
  },
  build: {
    outDir: "../webui",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.js"],
  },
});

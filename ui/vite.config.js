import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets are served by `practicegraph ui serve` from <root>/webui —
// fully self-contained, relative paths, no CDN, no external requests.
export default defineConfig({
  plugins: [react()],
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

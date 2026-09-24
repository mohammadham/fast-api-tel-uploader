import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Output lands in ../static so FastAPI serves the built panel exactly like the
// old runtime version: /static/assets/* + index.html at the static root.
export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  build: {
    outDir: "../static",
    emptyOutDir: false, // keep vendor/ + old runtime files; we clean precisely
    assetsDir: "assets",
  },
});

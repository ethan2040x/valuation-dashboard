import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

const isSingleFile = process.env.BUILD_SINGLEFILE === "1";

export default defineConfig({
  base: isSingleFile ? "./" : "/",
  plugins: isSingleFile ? [react(), viteSingleFile()] : [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    target: "es2020",
    sourcemap: false,
    cssCodeSplit: !isSingleFile,
    assetsInlineLimit: isSingleFile ? 100_000_000 : 4096,
  },
});

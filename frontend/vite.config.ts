import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

const isSingleFile = process.env.BUILD_SINGLEFILE === "1";

export default defineConfig({
  base: "./",
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
    rollupOptions: isSingleFile
      ? undefined
      : {
          output: {
            manualChunks: (id) => {
              if (id.includes("node_modules/echarts") || id.includes("node_modules/zrender")) {
                return "echarts";
              }
              if (
                id.includes("node_modules/react/") ||
                id.includes("node_modules/react-dom/") ||
                id.includes("node_modules/scheduler/")
              ) {
                return "vendor";
              }
              return undefined;
            },
          },
        },
  },
});

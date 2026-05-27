import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // When running `npm run dev` locally, proxy API calls to avoid CORS
      "/anomaly-api": {
        target: "http://localhost:8002",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/anomaly-api/, ""),
      },
      "/log-api": {
        target: "http://localhost:8004",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/log-api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API + SSE to the FastAPI backend so the browser talks to a single
// origin (no CORS) and Server-Sent Events stream through untouched.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        // Critical for SSE: don't buffer the streamed response.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache";
          });
        },
      },
    },
  },
});

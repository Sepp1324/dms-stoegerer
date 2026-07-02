import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-Server proxyt /api an das Django-Backend, damit im Browser keine
// CORS-Sonderfälle auftreten und die SPA relative Pfade nutzen kann.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});

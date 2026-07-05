import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Dev-Server proxyt /api an das Django-Backend, damit im Browser keine
// CORS-Sonderfälle auftreten und die SPA relative Pfade nutzen kann.
export default defineConfig({
  plugins: [
    react(),
    // Installierbare PWA (STOAA-514): App-Shell-Precache, KEIN Offline-Sync.
    // Manifest + Service Worker werden generiert; Icons liegen unter
    // public/icons/ (erzeugt via scripts/generate-pwa-icons.mjs).
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/apple-touch-icon.png"],
      manifest: {
        name: "DMS – Dokumentenverwaltung",
        short_name: "DMS",
        description: "Dokumente erfassen und verwalten – mobil per Kamera.",
        lang: "de",
        theme_color: "#0f172a",
        background_color: "#0f172a",
        display: "standalone",
        start_url: "/",
        scope: "/",
        icons: [
          { src: "icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: "icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
          {
            src: "icons/icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        // Nur App-Shell precachen (JS/CSS/HTML/Icons). Bewusst KEIN
        // Runtime-Caching der /api-Aufrufe – kein Offline-Sync (Nicht-Ziel).
        globPatterns: ["**/*.{js,css,html,ico,png,svg,webmanifest}"],
        navigateFallbackDenylist: [/^\/api/, /^\/share\//],
      },
    }),
  ],
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

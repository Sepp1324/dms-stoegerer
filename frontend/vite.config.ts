/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Dev-Server proxyt /api an das Django-Backend, damit im Browser keine
// CORS-Sonderfälle auftreten und die SPA relative Pfade nutzen kann.
export default defineConfig({
  plugins: [
    react(),
    // Installierbare PWA (STOAA-514): Asset-Precache, KEIN Offline-Sync.
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
        // Nur statische Assets precachen (JS/CSS/Icons). Bewusst KEIN
        // Runtime-Caching der /api-Aufrufe – kein Offline-Sync (Nicht-Ziel).
        //
        // index.html gehört NICHT in den Precache, und Navigationen beantwortet
        // der Service Worker nicht (navigateFallback: null): Workbox speichert
        // eine precachte Antwort samt Headern und liefert sie bei jeder
        // Navigation wieder aus. Die Security-Header aus nginx.conf (CSP!) wären
        // damit im Browser eingefroren, bis sich der Inhalt von index.html
        // ändert – eine reine nginx.conf-Änderung käme nie beim Nutzer an
        // (so passiert bei connect-src blob: für die PDF-Vorschau). Seiten
        // kommen deshalb immer vom Server; ohne Netz startet die App nicht,
        // was ohne Offline-Sync ohnehin gilt.
        globPatterns: ["**/*.{js,css,ico,png,svg,webmanifest}"],
        navigateFallback: null,
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
  // Vitest (#9): jsdom-Umgebung + jest-dom-Matcher (setupFiles). Ohne Globals –
  // Tests importieren describe/it/expect explizit aus "vitest".
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});

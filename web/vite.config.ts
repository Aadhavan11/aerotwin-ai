import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The app can live at the site root (development) or under a path such as /hacklanta/ (VITE_BASE=/hacklanta/ npm run build).
// The browser always calls <base>api/*; Vite forwards that to the FastAPI server (no CORS, no CDN, works offline).
const base = process.env.VITE_BASE ?? "/";
const apiPrefix = `${base}api`.replace(/\/{2,}/g, "/");
const proxy = {
  [apiPrefix]: {
    target: process.env.API_URL ?? "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (p: string) => p.slice(apiPrefix.length),
  },
};

export default defineConfig({
  base,
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy, allowedHosts: ["shamitbasu.com", ".shamitbasu.com", "localhost", "127.0.0.1"] },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The browser calls /api/*; Vite forwards to the FastAPI server (no CORS, no CDN, works offline).
const proxy = { "/api": { target: process.env.API_URL ?? "http://127.0.0.1:8000", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api/, "") } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
});

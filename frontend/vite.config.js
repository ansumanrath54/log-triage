import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, forward /api calls to the FastAPI server so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.API_TARGET || "http://localhost:8000" },
  },
});

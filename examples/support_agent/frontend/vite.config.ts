import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/chat": "http://localhost:8000",
      "/triage": "http://localhost:8000",
      "/history": "http://localhost:8000",
      "/analytics": "http://localhost:8000",
      "/tickets": "http://localhost:8000",
      "/__yomai__": "http://localhost:8000",
    },
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI runs on :5173 and forwards /api calls to Flask on :5001
// (not 5000: macOS uses that port for AirPlay Receiver).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:5001" },
  },
});

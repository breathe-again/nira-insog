import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: {
      // Polling helps file-watching work reliably inside Docker on macOS.
      usePolling: true,
      interval: 300,
    },
  },
});

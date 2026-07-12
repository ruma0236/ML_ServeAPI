import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const apiProxyTarget = process.env.VITE_CONTROL_PANEL_API_PROXY_TARGET || "http://127.0.0.1:8000";
const allowedHosts = (process.env.VITE_CONTROL_PANEL_ALLOWED_HOSTS || "ruma.tail35433c.ts.net")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      react: fileURLToPath(new URL("./node_modules/react", import.meta.url)),
      "react-dom": fileURLToPath(new URL("./node_modules/react-dom", import.meta.url))
    }
  },
  server: {
    host: "127.0.0.1",
    allowedHosts,
    port: 5174,
    strictPort: true,
    proxy: {
      "/control-panel": {
        target: apiProxyTarget,
        changeOrigin: true
      }
    }
  },
  preview: {
    host: "127.0.0.1",
    allowedHosts,
    port: 4173,
    strictPort: true
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [fileURLToPath(new URL("./src/test/setup.ts", import.meta.url))]
  }
});

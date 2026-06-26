import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";

const target = process.env.VITE_WEATHER_API ?? process.env.VITE_BFF_ORIGIN ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT) || 5174,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
      },
    },
  },
});

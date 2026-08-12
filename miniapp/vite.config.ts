import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Telegram Mini App: base "./" чтобы собранная папка dist работала из
// любого пути (хостинг статики), production build.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    host: true,
    port: 5173,
  },
});

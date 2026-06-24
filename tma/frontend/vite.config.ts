import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Конфигурация сборки фронтенда TMA.
// Базовый URL API задаётся переменной окружения VITE_API_BASE_URL (см. .env.example)
// и читается в коде через import.meta.env — здесь хардкода адресов нет.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // доступ извне (удобно для проверки через туннель/телефон)
    port: 5173,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});

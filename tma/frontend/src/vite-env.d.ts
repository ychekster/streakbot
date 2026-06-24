/// <reference types="vite/client" />

// Типизация переменных окружения, доступных через import.meta.env.
interface ImportMetaEnv {
  /** Базовый URL API-сервера TMA (tma/backend). */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

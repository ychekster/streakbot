/**
 * HTTP-клиент к API TMA.
 *
 * Каждый запрос несёт заголовок `Authorization: tma <initData>` — бэкенд по нему
 * проверяет подпись Telegram. Ошибки сети и API приводятся к единому типу
 * `ApiRequestError`, чтобы UI показывал понятный текст, не разбирая разные форматы.
 */

import { getInitData } from "../telegram/webapp";

// Базовый URL API (пустая строка => тот же источник, что и фронтенд).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

const AUTH_SCHEME = "tma";

/** Машинно-читаемые коды ошибок, которые UI может различать. */
export type ApiErrorCode =
  | "network_error"
  | "invalid_init_data"
  | "missing_init_data"
  | "user_not_found"
  | "task_not_found"
  | "validation_error"
  | "internal_error"
  | "http_error";

/** Единая ошибка запроса к API. */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;

  constructor(status: number, code: ApiErrorCode, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

// Форма тела ошибки от бэкенда: { "error": { "code", "message" } }.
interface ApiErrorBody {
  error?: { code?: string; message?: string };
}

async function parseJsonSafe(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/**
 * Выполнить запрос к API и вернуть распарсенный JSON-ответ типа T.
 * Бросает `ApiRequestError` при сетевой ошибке или ответе с не-2xx статусом.
 */
export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `${AUTH_SCHEME} ${getInitData()}`,
    ...(options.headers as Record<string, string> | undefined),
  };

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch {
    // fetch падает только при сетевой недоступности — отдельный понятный код.
    throw new ApiRequestError(0, "network_error", "Нет связи с сервером");
  }

  const body = await parseJsonSafe(response);

  if (!response.ok) {
    const errorBody = (body as ApiErrorBody | null)?.error;
    const code = (errorBody?.code as ApiErrorCode) ?? "http_error";
    const message = errorBody?.message ?? "Не удалось выполнить запрос";
    throw new ApiRequestError(response.status, code, message);
  }

  return body as T;
}

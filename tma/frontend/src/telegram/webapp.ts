/**
 * Тонкая типизированная обёртка над window.Telegram.WebApp.
 *
 * SDK подключается скриптом в index.html. Здесь — только то, что реально нужно
 * приложению: получить initData для авторизации, развернуть на весь экран,
 * подкрасить фон под дизайн и дать тактильный отклик. Все обращения к SDK
 * защищены проверками на наличие — приложение не падает, если открыто вне Telegram.
 */

type HapticStyle = "light" | "medium" | "heavy" | "rigid" | "soft";
type HapticNotification = "error" | "success" | "warning";

interface TelegramHapticFeedback {
  impactOccurred(style: HapticStyle): void;
  notificationOccurred(type: HapticNotification): void;
}

interface TelegramWebApp {
  initData: string;
  ready(): void;
  expand(): void;
  setBackgroundColor(color: string): void;
  setHeaderColor(color: string): void;
  HapticFeedback?: TelegramHapticFeedback;
}

interface TelegramNamespace {
  WebApp?: TelegramWebApp;
}

declare global {
  interface Window {
    Telegram?: TelegramNamespace;
  }
}

function getWebApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp;
}

/** Доступно ли приложение внутри Telegram (есть ли SDK и непустая initData). */
export function isTelegramAvailable(): boolean {
  const webApp = getWebApp();
  return Boolean(webApp && webApp.initData);
}

/** Сырая строка initData для заголовка авторизации (пустая строка вне Telegram). */
export function getInitData(): string {
  return getWebApp()?.initData ?? "";
}

/**
 * Инициализация при запуске: сообщить готовность, развернуть на весь экран,
 * подкрасить фон/шапку под цвет фона приложения (бесшовный вид без верхнего зазора).
 */
export function initTelegram(backgroundColor: string): void {
  const webApp = getWebApp();
  if (!webApp) {
    return;
  }
  webApp.ready();
  webApp.expand();
  // Цвета шапки/фона задаём из дизайн-токена, переданного приложением.
  try {
    webApp.setBackgroundColor(backgroundColor);
    webApp.setHeaderColor(backgroundColor);
  } catch {
    // Старые клиенты могут не поддерживать выбор произвольного цвета — не критично.
  }
}

/** Тактильный отклик на успешное/неуспешное действие (если поддерживается клиентом). */
export function hapticNotification(type: HapticNotification): void {
  getWebApp()?.HapticFeedback?.notificationOccurred(type);
}

/** Лёгкий тактильный отклик на нажатие (если поддерживается клиентом). */
export function hapticImpact(style: HapticStyle = "light"): void {
  getWebApp()?.HapticFeedback?.impactOccurred(style);
}

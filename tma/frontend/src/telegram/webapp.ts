/**
 * Тонкая типизированная обёртка над window.Telegram.WebApp.
 *
 * SDK подключается скриптом в index.html. Здесь — только то, что реально нужно
 * приложению: получить initData для авторизации, раскрыть на весь экран (включая
 * полноэкранный режим на iPhone), прокинуть отступы безопасных зон в CSS и дать
 * тактильный отклик. Все обращения к SDK защищены проверками на наличие —
 * приложение не падает, если открыто вне Telegram или в старом клиенте.
 */

type HapticStyle = "light" | "medium" | "heavy" | "rigid" | "soft";
type HapticNotification = "error" | "success" | "warning";

interface TelegramHapticFeedback {
  impactOccurred(style: HapticStyle): void;
  notificationOccurred(type: HapticNotification): void;
}

/** Отступы безопасной зоны (вырез устройства или панель управления Telegram). */
interface SafeAreaInset {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

interface TelegramWebApp {
  initData: string;
  platform: string;
  ready(): void;
  expand(): void;
  setBackgroundColor(color: string): void;
  setHeaderColor(color: string): void;
  // Полноэкранный режим и связанные методы — Bot API 8.0+ (могут отсутствовать).
  requestFullscreen?(): void;
  disableVerticalSwipes?(): void;
  // Отступы безопасных зон — Bot API 8.0+.
  safeAreaInset?: SafeAreaInset;
  contentSafeAreaInset?: SafeAreaInset;
  onEvent?(eventType: string, handler: () => void): void;
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
 * Прокинуть отступы безопасных зон Telegram в CSS-переменные:
 *  --app-safe-area-* — вырез устройства (чёлка, home indicator);
 *  --app-content-safe-area-* — панель управления Telegram в полноэкранном режиме.
 * В фуллскрине эти значения появляются асинхронно, поэтому функция вызывается
 * повторно по событиям изменения зон.
 */
function applySafeAreaInsets(webApp: TelegramWebApp): void {
  const root = document.documentElement;
  const setInset = (name: string, value: number | undefined): void => {
    if (typeof value === "number") {
      root.style.setProperty(name, `${value}px`);
    }
  };
  const safe = webApp.safeAreaInset;
  const content = webApp.contentSafeAreaInset;
  setInset("--app-safe-area-top", safe?.top);
  setInset("--app-safe-area-bottom", safe?.bottom);
  setInset("--app-content-safe-area-top", content?.top);
  setInset("--app-content-safe-area-bottom", content?.bottom);
}

/**
 * Инициализация при запуске: сообщить готовность, раскрыть на весь экран,
 * подкрасить фон/шапку и прокинуть отступы безопасных зон.
 *
 * На iPhone `expand()` оставляет зазор сверху (приложение открывается «шторкой»),
 * поэтому дополнительно включаем полноэкранный режим (Bot API 8.0). На других
 * платформах поведение не меняем — там приложение и так раскрывается корректно.
 */
export function initTelegram(backgroundColor: string): void {
  const webApp = getWebApp();
  if (!webApp) {
    return;
  }
  webApp.ready();
  webApp.expand();
  try {
    webApp.setBackgroundColor(backgroundColor);
    webApp.setHeaderColor(backgroundColor);
  } catch {
    // Старые клиенты могут не поддерживать выбор цвета — не критично.
  }

  if (webApp.platform === "ios" && typeof webApp.requestFullscreen === "function") {
    try {
      webApp.requestFullscreen();
      // В фуллскрине вертикальный свайп не должен случайно сворачивать приложение.
      webApp.disableVerticalSwipes?.();
    } catch {
      // requestFullscreen может бросить на неподдерживаемом клиенте — игнорируем.
    }
  }

  // Применяем отступы безопасных зон сейчас и пересчитываем по событиям (фуллскрин
  // меняет их асинхронно — после перехода значения станут известны).
  applySafeAreaInsets(webApp);
  const refresh = (): void => applySafeAreaInsets(webApp);
  webApp.onEvent?.("safeAreaChanged", refresh);
  webApp.onEvent?.("contentSafeAreaChanged", refresh);
  webApp.onEvent?.("fullscreenChanged", refresh);
}

/** Тактильный отклик на успешное/неуспешное действие (если поддерживается клиентом). */
export function hapticNotification(type: HapticNotification): void {
  getWebApp()?.HapticFeedback?.notificationOccurred(type);
}

/** Лёгкий тактильный отклик на нажатие (если поддерживается клиентом). */
export function hapticImpact(style: HapticStyle = "light"): void {
  getWebApp()?.HapticFeedback?.impactOccurred(style);
}

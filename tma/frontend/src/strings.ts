/**
 * Вся пользовательская копия интерфейса в одном месте (как TEXTS у бота).
 * Ни одной строки-сообщения прямо в компонентах.
 */
export const STRINGS = {
  screenTitle: "Привычки",

  emptyEmoji: "🌱",
  emptyTitle: "Пока нет привычек",
  emptyDescription: "Добавь первую привычку в боте командой /add.",

  errorEmoji: "⚠️",
  errorTitle: "Что-то пошло не так",
  errorRetry: "Повторить",

  outsideEmoji: "📱",
  outsideTitle: "Откройте в Telegram",
  outsideDescription:
    "Это мини-приложение работает внутри Telegram — откройте его кнопкой в боте StreakBot.",
} as const;

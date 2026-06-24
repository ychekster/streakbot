/**
 * Экран «Привычки».
 *
 * При запуске разворачивает мини-приложение на весь экран и подкрашивает фон под
 * дизайн. Загружает привычки через initData-авторизацию и показывает: скелетон при
 * загрузке, понятное сообщение при ошибке/пустом списке/запуске вне Telegram, либо
 * список карточек с годовой сеткой и кнопками отметки (оптимистичное обновление).
 */

import { useEffect } from "react";

import { HabitCard } from "./components/HabitCard";
import { Skeleton } from "./components/Skeleton";
import { StatusMessage } from "./components/StatusMessage";
import { useHabits } from "./hooks/useHabits";
import { useToggle } from "./hooks/useToggle";
import { STRINGS } from "./strings";
import { initTelegram, isTelegramAvailable } from "./telegram/webapp";
import styles from "./App.module.css";

// Цвет фона берём из дизайн-токена (CSS-переменной), а не хардкодим в коде.
function readBackgroundColor(): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue("--color-background")
    .trim();
  return value || "#f2f2f7";
}

export function App() {
  const { habits, status, errorMessage, setHabits, reload } = useHabits();
  const toggle = useToggle(setHabits);
  const telegramAvailable = isTelegramAvailable();

  // Разворачиваем приложение и красим фон один раз при монтировании.
  useEffect(() => {
    initTelegram(readBackgroundColor());
  }, []);

  return (
    <main className={styles.screen}>
      <h1 className={styles.title}>{STRINGS.screenTitle}</h1>
      {renderContent()}
    </main>
  );

  function renderContent() {
    // Открыто вне Telegram — авторизоваться нечем, объясняем пользователю.
    if (!telegramAvailable) {
      return (
        <StatusMessage
          emoji={STRINGS.outsideEmoji}
          title={STRINGS.outsideTitle}
          description={STRINGS.outsideDescription}
        />
      );
    }

    if (status === "loading") {
      return <Skeleton />;
    }

    if (status === "error") {
      return (
        <StatusMessage
          emoji={STRINGS.errorEmoji}
          title={STRINGS.errorTitle}
          description={errorMessage ?? undefined}
          actionLabel={STRINGS.errorRetry}
          onAction={reload}
        />
      );
    }

    if (habits.length === 0) {
      return (
        <StatusMessage
          emoji={STRINGS.emptyEmoji}
          title={STRINGS.emptyTitle}
          description={STRINGS.emptyDescription}
        />
      );
    }

    return (
      <div className={styles.list}>
        {habits.map((habit) => (
          <HabitCard key={habit.id} habit={habit} onToggle={toggle} />
        ))}
      </div>
    );
  }
}

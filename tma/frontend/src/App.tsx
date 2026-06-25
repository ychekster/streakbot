/**
 * Экран «Привычки».
 *
 * При запуске разворачивает мини-приложение на весь экран и подкрашивает фон под
 * дизайн. Загружает привычки через initData-авторизацию и показывает: скелетон при
 * загрузке, понятное сообщение при ошибке/пустом списке/запуске вне Telegram, либо
 * список карточек с годовой сеткой и кнопками отметки (оптимистичное обновление).
 */

import { useEffect } from "react";

import { CollapsingHeader } from "./components/CollapsingHeader";
import { HabitsSection } from "./components/HabitsSection";
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
      <CollapsingHeader title={STRINGS.screenTitle} />
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

    // Две секции: запланированные на сегодня (интерактивные) и остальные (только просмотр).
    const scheduledHabits = habits.filter((habit) => habit.scheduled_today);
    const otherHabits = habits.filter((habit) => !habit.scheduled_today);
    return (
      <>
        {scheduledHabits.length > 0 ? (
          <HabitsSection habits={scheduledHabits} interactive onToggle={toggle} />
        ) : null}
        {otherHabits.length > 0 ? (
          <HabitsSection
            habits={otherHabits}
            interactive={false}
            subheading={STRINGS.otherSubheading}
          />
        ) : null}
      </>
    );
  }
}

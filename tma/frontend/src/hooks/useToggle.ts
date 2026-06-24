/**
 * Оптимистичное переключение отметки привычки за сегодня.
 *
 * По нажатию состояние меняется немедленно (done_today и последняя ячейка года),
 * затем уходит запрос. При успехе привычка заменяется ответом сервера (источник
 * истины), при ошибке — откат к прежнему состоянию и тактильный сигнал об ошибке.
 * Параллельные нажатия по одной привычке игнорируются, пока запрос в полёте.
 */

import { useCallback, useRef } from "react";

import { toggleHabit } from "../api/habits";
import type { Habit } from "../types/habit";
import { hapticImpact, hapticNotification } from "../telegram/webapp";

/** Применить оптимистичное переключение к одной привычке в списке. */
function withToggledHabit(habits: Habit[], taskId: number): Habit[] {
  return habits.map((habit) => {
    if (habit.id !== taskId) {
      return habit;
    }
    const nextDone = !habit.done_today;
    // Последняя ячейка истории — сегодня; синхронизируем её с отметкой.
    const nextHistory = [...habit.history];
    if (nextHistory.length > 0) {
      nextHistory[nextHistory.length - 1] = nextDone;
    }
    return { ...habit, done_today: nextDone, history: nextHistory };
  });
}

/** Заменить привычку в списке на авторитетную версию с сервера. */
function withServerHabit(habits: Habit[], updated: Habit): Habit[] {
  return habits.map((habit) => (habit.id === updated.id ? updated : habit));
}

export function useToggle(
  setHabits: React.Dispatch<React.SetStateAction<Habit[]>>,
) {
  // id привычек, по которым сейчас выполняется запрос (защита от дабл-тапа).
  const inFlight = useRef<Set<number>>(new Set());

  return useCallback(
    async (taskId: number) => {
      if (inFlight.current.has(taskId)) {
        return;
      }
      inFlight.current.add(taskId);
      hapticImpact("light");

      // Запоминаем снимок для отката и применяем оптимистичное изменение.
      let snapshot: Habit[] = [];
      setHabits((current) => {
        snapshot = current;
        return withToggledHabit(current, taskId);
      });

      try {
        const updated = await toggleHabit(taskId);
        setHabits((current) => withServerHabit(current, updated));
      } catch {
        // Откат к состоянию до нажатия и сигнал об ошибке.
        setHabits(snapshot);
        hapticNotification("error");
      } finally {
        inFlight.current.delete(taskId);
      }
    },
    [setHabits],
  );
}

/**
 * Загрузка и хранение списка привычек.
 *
 * Управляет статусом экрана (загрузка / ошибка / готово), хранит данные и даёт
 * перезагрузку. Сам сетап отметки вынесен в useToggle, которому передаётся
 * setHabits — так загрузка и мутации разделены.
 */

import { useCallback, useEffect, useState } from "react";

import { fetchHabits } from "../api/habits";
import { ApiRequestError } from "../api/client";
import type { Habit } from "../types/habit";

export type HabitsStatus = "loading" | "ready" | "error";

interface UseHabitsResult {
  habits: Habit[];
  status: HabitsStatus;
  /** Текст ошибки для показа пользователю (только при status === "error"). */
  errorMessage: string | null;
  /** Прямой доступ к стейту для оптимистичных обновлений (используется useToggle). */
  setHabits: React.Dispatch<React.SetStateAction<Habit[]>>;
  /** Перезагрузить список (например, по кнопке «Повторить»). */
  reload: () => void;
}

export function useHabits(): UseHabitsResult {
  const [habits, setHabits] = useState<Habit[]>([]);
  const [status, setStatus] = useState<HabitsStatus>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setStatus("loading");
    setErrorMessage(null);
    try {
      const loaded = await fetchHabits();
      setHabits(loaded);
      setStatus("ready");
    } catch (error) {
      const message =
        error instanceof ApiRequestError
          ? error.message
          : "Не удалось загрузить привычки";
      setErrorMessage(message);
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { habits, status, errorMessage, setHabits, reload: () => void load() };
}

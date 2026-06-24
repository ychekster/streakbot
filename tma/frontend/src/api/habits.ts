/** Запросы к API привычек поверх общего HTTP-клиента. */

import type { Habit, HabitsResponse, ToggleResponse } from "../types/habit";
import { apiRequest } from "./client";

/** Загрузить список привычек пользователя с историей выполнения за год. */
export async function fetchHabits(): Promise<Habit[]> {
  const data = await apiRequest<HabitsResponse>("/tasks", { method: "GET" });
  return data.habits;
}

/** Переключить отметку выполнения задачи за сегодня; вернуть обновлённую привычку. */
export async function toggleHabit(taskId: number): Promise<Habit> {
  const data = await apiRequest<ToggleResponse>(`/tasks/${taskId}/toggle`, {
    method: "POST",
  });
  return data.habit;
}

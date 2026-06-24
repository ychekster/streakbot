/** Типы данных привычек — зеркалят схемы ответа API (tma/backend/schemas.py). */

export interface Habit {
  /** Идентификатор задачи. */
  id: number;
  /** Название привычки. */
  name: string;
  /** Отмечена ли задача выполненной сегодня. */
  done_today: boolean;
  /**
   * Выполнение за последние YEAR_GRID_DAYS дней (старое → сегодня).
   * true — день выполнен, false — пропущен/нет данных.
   * Индекс 0 — самый старый день, последний элемент — сегодня.
   */
  history: boolean[];
}

/** Ответ GET /tasks. */
export interface HabitsResponse {
  habits: Habit[];
}

/** Ответ POST /tasks/{task_id}/toggle. */
export interface ToggleResponse {
  habit: Habit;
}

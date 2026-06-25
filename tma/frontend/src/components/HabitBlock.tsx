/** Блок одной привычки внутри общей карточки: строка «название + кнопка отметки» и сетка точек. */

import type { Habit } from "../types/habit";
import { CheckButton } from "./CheckButton";
import { YearGrid } from "./YearGrid";
import styles from "./HabitBlock.module.css";

interface HabitBlockProps {
  habit: Habit;
  /** Можно ли отмечать выполнение. Для незапланированных на сегодня — только просмотр. */
  interactive: boolean;
  onToggle?: (taskId: number) => void;
}

export function HabitBlock({ habit, interactive, onToggle }: HabitBlockProps) {
  return (
    <article className={styles.block}>
      <div className={styles.header}>
        <h3 className={styles.name}>{habit.name}</h3>
        <CheckButton
          done={habit.done_today}
          habitName={habit.name}
          disabled={!interactive}
          onToggle={() => onToggle?.(habit.id)}
        />
      </div>
      <YearGrid history={habit.history} />
    </article>
  );
}

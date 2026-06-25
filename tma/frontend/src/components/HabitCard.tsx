/** Карточка привычки: строка «название + кнопка отметки» и сетка выполнения. */

import type { Habit } from "../types/habit";
import { CheckButton } from "./CheckButton";
import { YearGrid } from "./YearGrid";
import styles from "./HabitCard.module.css";

interface HabitCardProps {
  habit: Habit;
  onToggle: (taskId: number) => void;
}

export function HabitCard({ habit, onToggle }: HabitCardProps) {
  return (
    <article className={styles.card}>
      <div className={styles.header}>
        <h2 className={styles.name}>{habit.name}</h2>
        <CheckButton
          done={habit.done_today}
          habitName={habit.name}
          onToggle={() => onToggle(habit.id)}
        />
      </div>
      <YearGrid history={habit.history} />
    </article>
  );
}

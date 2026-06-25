/** Секция привычек: необязательный приглушённый подзаголовок и общая карточка с блоками. */

import type { Habit } from "../types/habit";
import { HabitBlock } from "./HabitBlock";
import styles from "./HabitsSection.module.css";

interface HabitsSectionProps {
  habits: Habit[];
  /** Интерактивная секция (можно отмечать) или только просмотр прогресса. */
  interactive: boolean;
  /** Приглушённый подзаголовок над карточкой (например, у секции «не на сегодня»). */
  subheading?: string;
  onToggle?: (taskId: number) => void;
}

export function HabitsSection({
  habits,
  interactive,
  subheading,
  onToggle,
}: HabitsSectionProps) {
  return (
    <section className={styles.section}>
      {subheading ? <h2 className={styles.subheading}>{subheading}</h2> : null}
      <div className={styles.card}>
        {habits.map((habit) => (
          <HabitBlock
            key={habit.id}
            habit={habit}
            interactive={interactive}
            onToggle={onToggle}
          />
        ))}
      </div>
    </section>
  );
}

/** Круглая кнопка отметки выполнения за сегодня (пустой кружок ↔ заполненный с галочкой). */

import styles from "./CheckButton.module.css";

interface CheckButtonProps {
  done: boolean;
  /** Доступная подпись (название привычки) — для скринридеров. */
  habitName: string;
  onToggle: () => void;
  /** Неактивная кнопка: показывает статус, но не реагирует на нажатие (только просмотр). */
  disabled?: boolean;
}

export function CheckButton({
  done,
  habitName,
  onToggle,
  disabled = false,
}: CheckButtonProps) {
  return (
    <button
      type="button"
      className={`${styles.button} ${done ? styles.done : ""} ${
        disabled ? styles.disabled : ""
      }`}
      onClick={disabled ? undefined : onToggle}
      disabled={disabled}
      aria-pressed={done}
      aria-label={
        disabled
          ? `${habitName}: ${done ? "выполнено" : "сегодня не запланировано"}`
          : done
            ? `Снять отметку: ${habitName}`
            : `Отметить выполнено: ${habitName}`
      }
    >
      {/* Галочка появляется только в выполненном состоянии. */}
      <svg
        className={styles.check}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M5 12.5l4.5 4.5L19 7.5"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}

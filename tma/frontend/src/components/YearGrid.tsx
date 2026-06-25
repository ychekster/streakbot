/**
 * Сетка выполнения: 7 рядов по 26 кружков (182 дня).
 *
 * Раскладка построчная: индекс 0 истории — левый верхний угол (самый старый день),
 * последний — правый нижний (сегодня). Закрашенный кружок — день выполнен,
 * полупрозрачный — пропущен/нет данных. Кружки масштабируются под ширину карточки.
 */

import styles from "./YearGrid.module.css";

interface YearGridProps {
  /** История выполнения (старое → сегодня), длиной GRID_DAYS. */
  history: boolean[];
}

export function YearGrid({ history }: YearGridProps) {
  return (
    <div
      className={styles.grid}
      role="img"
      aria-label="Годовая история выполнения"
    >
      {history.map((done, index) => (
        <span
          key={index}
          className={`${styles.cell} ${done ? styles.filled : styles.empty}`}
        />
      ))}
    </div>
  );
}

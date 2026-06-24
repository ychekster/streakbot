/** Скелетон-заглушка списка привычек на время первичной загрузки. */

import { SKELETON_HABIT_COUNT } from "../constants";
import styles from "./Skeleton.module.css";

export function Skeleton() {
  return (
    <div className={styles.list} aria-hidden="true">
      {Array.from({ length: SKELETON_HABIT_COUNT }).map((_, index) => (
        <div key={index} className={styles.card}>
          <div className={styles.header}>
            <span className={`${styles.shimmer} ${styles.name}`} />
            <span className={`${styles.shimmer} ${styles.check}`} />
          </div>
          <span className={`${styles.shimmer} ${styles.grid}`} />
        </div>
      ))}
    </div>
  );
}

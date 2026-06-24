/**
 * Центрированное сообщение-состояние: пустой список, ошибка загрузки или запуск
 * вне Telegram. Необязательная кнопка действия (например, «Повторить»).
 */

import styles from "./StatusMessage.module.css";

interface StatusMessageProps {
  emoji: string;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function StatusMessage({
  emoji,
  title,
  description,
  actionLabel,
  onAction,
}: StatusMessageProps) {
  return (
    <div className={styles.container}>
      <span className={styles.emoji} aria-hidden="true">
        {emoji}
      </span>
      <p className={styles.title}>{title}</p>
      {description ? <p className={styles.description}>{description}</p> : null}
      {actionLabel && onAction ? (
        <button type="button" className={styles.action} onClick={onAction}>
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

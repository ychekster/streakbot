# Переход с SQLite на PostgreSQL

Благодаря SQLAlchemy и async-драйверам смена СУБД не требует изменений в коде —
только конфигурации и зависимостей.

## Шаги

1. **Изменить `DATABASE_URL`** в `.env`:
   ```
   DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/streakbot
   ```

2. **Добавить драйвер** в `requirements.txt` и установить:
   ```
   asyncpg>=0.29.0
   ```
   ```bash
   pip install -r requirements.txt
   ```

3. **Создать базу данных** в PostgreSQL:
   ```sql
   CREATE DATABASE streakbot;
   ```

4. **Применить миграции**:
   ```bash
   alembic upgrade head
   ```

5. **Запустить бота**:
   ```bash
   python -m bot.main
   ```

Больше ничего менять не нужно — SQLAlchemy абстрагирует диалект СУБД, а весь
доступ к данным идёт через `Repository`.

## Замечания

- Тип `BigInteger` для `telegram_id` корректно отображается и в SQLite, и в
  PostgreSQL.
- Enum'ы хранятся как `VARCHAR` (`native_enum=False`), что переносимо между
  диалектами.
- Для продакшена с несколькими воркерами стоит заменить `MemoryStorage` (FSM)
  на персистентное хранилище, например `RedisStorage`, и вынести APScheduler
  jobstore в БД/Redis, чтобы задания планировщика переживали рестарт без
  пересоздания.

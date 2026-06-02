# StreakBot

Telegram-бот для отслеживания привычек через стрики — серии непрерывного
выполнения задач. Создаёт расписание, ежедневно напоминает и считает стрик 🔥

## Технологический стек

| Компонент | Библиотека |
|---|---|
| Telegram API | `aiogram` 3.x (async, FSM) |
| ORM | `SQLAlchemy` 2.0 (async) |
| БД | `aiosqlite` (SQLite) → легко на PostgreSQL |
| Миграции | `alembic` |
| Планировщик | `APScheduler` (AsyncIOScheduler) |
| Конфиг | `python-dotenv` + `pydantic-settings` |
| Часовые пояса | `pytz` + `timezonefinder` + `geopy` |
| Логи | `loguru` |

## Установка и запуск

```bash
git clone <repo-url>
cd StreakBot

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env          # Windows: copy .env.example .env
# Заполнить .env: указать BOT_TOKEN от @BotFather

alembic upgrade head          # опционально: создать схему через миграции
python -m bot.main
```

> При запуске `python -m bot.main` таблицы создаются автоматически
> (`create_all`), поэтому шаг `alembic upgrade head` не обязателен для локального
> старта на SQLite. Alembic нужен для версионирования схемы и продакшена.

## Структура проекта

```
StreakBot/
├── .env.example            # Шаблон переменных окружения
├── requirements.txt
├── README.md
├── docs/
│   ├── ARCHITECTURE.md     # Архитектура и обоснование решений
│   └── SCALING.md          # Переход на PostgreSQL
├── alembic/                # Миграции схемы БД
├── alembic.ini
└── bot/
    ├── main.py             # Точка входа: bot, dispatcher, scheduler
    ├── config.py           # Pydantic-конфиг из .env
    ├── constants.py        # Все тексты (TEXTS) и константы
    ├── handlers/
    │   ├── cancel.py       # /cancel — универсальная отмена
    │   ├── start.py        # /start и /help — главное меню
    │   ├── onboarding.py   # FSM регистрации (OnboardingStates)
    │   ├── add_task.py     # /add — FSM добавления задачи
    │   ├── today.py        # /today и /done — списки с инлайн-навигацией и карточки задач
    │   ├── delete_task.py  # /delete — удаление с пагинацией
    │   ├── stats.py        # /stats — статистика и стрики
    │   └── settings.py     # /settings — изменение настроек
    ├── keyboards/
    │   └── builders.py     # Все клавиатуры
    ├── database/
    │   ├── base.py         # DeclarativeBase, engine, фабрика сессий
    │   ├── models.py       # User, Task, TaskLog
    │   └── repository.py   # CRUD (Repository pattern)
    ├── services/
    │   ├── scheduler.py    # APScheduler-задачи
    │   ├── streak.py       # Подсчёт стриков
    │   └── geo.py          # Часовой пояс по городу
    ├── middlewares/
    │   ├── activity.py     # Учёт времени последней активности
    │   ├── database.py     # DI сессии и репозитория
    │   └── registration.py # Проверка регистрации
    └── utils/
        └── validators.py   # Валидация ввода, экранирование MarkdownV2
```

## Переменные окружения

| Переменная | Назначение | Обязательна |
|---|---|---|
| `BOT_TOKEN` | Токен бота от @BotFather | да |
| `DATABASE_URL` | Строка подключения к БД (async-драйвер) | нет (есть дефолт SQLite) |
| `LOG_LEVEL` | Уровень логирования (`INFO` по умолчанию) | нет |
| `LOG_FILE` | Путь к файлу логов (`logs/bot.log`) | нет |

## Команды бота

| Команда | Действие |
|---|---|
| `/start` | Регистрация / главное меню |
| `/today` | Невыполненные задачи на сегодня (инлайн-список → карточка → ✅ Выполнено) |
| `/done` | Выполненные сегодня задачи (карточка → Отменить выполнение) |
| `/add` | Добавить задачу |
| `/delete` | Удалить задачу |
| `/stats` | Статистика и стрики |
| `/settings` | Настройки уведомлений и пояса |
| `/help` | Список команд |
| `/cancel` | Отменить текущее действие |

## Архитектурные решения (кратко)

- **Стрик вычисляется динамически** из `TaskLog` — без хранения и
  рассинхронизации.
- **Repository pattern** — весь доступ к БД через один слой.
- **DI через aiogram** — сессия, репозиторий, конфиг и планировщик
  пробрасываются в хендлеры.
- **MarkdownV2** с обязательным экранированием динамических вставок.
- **Восстановление jobs** планировщика при старте по данным из БД.
- **Утренний дайджест интерактивный**: задачи на сегодня + блок просроченных
  (вчерашних невыполненных) с возможностью отметить их до 12:00 — всё через
  редактирование одного сообщения.
- **Дайджесты деликатны**: откладываются, если пользователь активен (<5 мин),
  и сбрасывают состояние/клавиатуру для чистого чата.

Подробнее — в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Масштабирование

Переход с SQLite на PostgreSQL описан в [docs/SCALING.md](docs/SCALING.md):
по сути — поменять `DATABASE_URL`, добавить `asyncpg` и применить миграции.

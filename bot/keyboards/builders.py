"""Все клавиатуры бота (reply и inline) в одном месте.

Хендлеры импортируют готовые клавиатуры отсюда и не собирают их вручную.
Callback-data завязана на короткие префиксы, разбираемые в соответствующих
роутерах.
"""

from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.constants import (
    BTN_ADD_MORE,
    BTN_ADD_TASK,
    BTN_BACK,
    BTN_DELETE,
    BTN_DONE,
    BTN_DONE_MARK,
    BTN_FREQ_DAILY,
    BTN_FREQ_ONETIME,
    BTN_FREQ_SPECIFIC,
    BTN_MARK,
    BTN_NO,
    BTN_REMINDER_NO,
    BTN_REMINDER_YES,
    BTN_SETTINGS_EVENING,
    BTN_SETTINGS_MORNING,
    BTN_SETTINGS_TIMEZONE,
    BTN_SKIP,
    BTN_SKIP_MARK,
    BTN_START,
    BTN_YES,
    DELETE_PAGE_SIZE,
    EVENING_TIME_PRESETS,
    MONTHS_SHORT,
    MORNING_TIME_PRESETS,
    TIMEZONE_PRESETS,
    WEEKDAYS,
)

# Длина обрезки названий задач в кнопках списка удаления.
_BUTTON_NAME_LIMIT = 22

# Готовый объект «убрать reply-клавиатуру».
REMOVE_KB = ReplyKeyboardRemove()


# --------------------------------------------------------------------------- #
#  Reply-клавиатуры онбординга и настроек
# --------------------------------------------------------------------------- #

def start_kb() -> ReplyKeyboardMarkup:
    """Кнопка «🚀 Поехали» на приветственном экране."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_START)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _time_presets_kb(presets: tuple[str, ...]) -> ReplyKeyboardMarkup:
    """Ряд кнопок-пресетов времени."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=value) for value in presets]],
        resize_keyboard=True,
    )


def morning_time_kb() -> ReplyKeyboardMarkup:
    """Пресеты утреннего времени."""
    return _time_presets_kb(MORNING_TIME_PRESETS)


def evening_time_kb() -> ReplyKeyboardMarkup:
    """Пресеты вечернего времени."""
    return _time_presets_kb(EVENING_TIME_PRESETS)


def timezone_kb() -> ReplyKeyboardMarkup:
    """Пресеты часовых поясов (по два в ряд, последний — один)."""
    labels = [label for label, _ in TIMEZONE_PRESETS]
    rows: list[list[KeyboardButton]] = []
    for i in range(0, len(labels), 2):
        rows.append([KeyboardButton(text=label) for label in labels[i : i + 2]])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def confirm_city_kb() -> ReplyKeyboardMarkup:
    """Подтверждение найденного города: Да / Нет в одну строку."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_YES), KeyboardButton(text=BTN_NO)]],
        resize_keyboard=True,
    )


def onboarding_done_kb() -> ReplyKeyboardMarkup:
    """Финал онбординга: добавить задачу или пропустить."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ADD_TASK), KeyboardButton(text=BTN_SKIP)]],
        resize_keyboard=True,
    )


def settings_kb() -> ReplyKeyboardMarkup:
    """Reply-клавиатура карточки настроек."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SETTINGS_MORNING)],
            [KeyboardButton(text=BTN_SETTINGS_EVENING)],
            [KeyboardButton(text=BTN_SETTINGS_TIMEZONE)],
        ],
        resize_keyboard=True,
    )


# --------------------------------------------------------------------------- #
#  Inline-клавиатуры добавления задачи
# --------------------------------------------------------------------------- #

def frequency_kb() -> InlineKeyboardMarkup:
    """Выбор частоты выполнения задачи."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_FREQ_DAILY, callback_data="freq:daily")],
            [InlineKeyboardButton(text=BTN_FREQ_SPECIFIC, callback_data="freq:specific")],
            [InlineKeyboardButton(text=BTN_FREQ_ONETIME, callback_data="freq:onetime")],
        ]
    )


def days_kb(selected: set[str]) -> InlineKeyboardMarkup:
    """Выбор дней недели с галочками + кнопка «Готово»."""
    buttons: list[InlineKeyboardButton] = []
    for code, short, _ in WEEKDAYS:
        mark = "✅ " if code in selected else ""
        buttons.append(
            InlineKeyboardButton(text=f"{mark}{short}", callback_data=f"day:{code}")
        )
    # Раскладка: 4 + 3 + ряд с «Готово».
    rows = [buttons[:4], buttons[4:], [
        InlineKeyboardButton(text=BTN_DONE, callback_data="days_done")
    ]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def year_kb(years: list[int]) -> InlineKeyboardMarkup:
    """Выбор года (текущий + 2 следующих) одним рядом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(y), callback_data=f"year:{y}") for y in years]
        ]
    )


def month_kb(year: int, min_month: int) -> InlineKeyboardMarkup:
    """Выбор месяца сеткой 4×3; месяцы раньше min_month не отображаются."""
    buttons = [
        InlineKeyboardButton(text=MONTHS_SHORT[m], callback_data=f"month:{m}")
        for m in range(min_month, 13)
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_kb(year: int, month: int, min_day: int) -> InlineKeyboardMarkup:
    """Выбор дня сеткой по 7; дни раньше min_day не отображаются."""
    days_in_month = calendar.monthrange(year, month)[1]
    buttons = [
        InlineKeyboardButton(text=str(d), callback_data=f"dateday:{d}")
        for d in range(min_day, days_in_month + 1)
    ]
    rows = [buttons[i : i + 7] for i in range(0, len(buttons), 7)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def has_reminder_kb() -> InlineKeyboardMarkup:
    """Спросить, есть ли фиксированное время напоминания."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_REMINDER_YES, callback_data="rem:yes"),
                InlineKeyboardButton(text=BTN_REMINDER_NO, callback_data="rem:no"),
            ]
        ]
    )


def task_confirm_kb() -> InlineKeyboardMarkup:
    """После добавления задачи: добавить ещё или завершить."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_ADD_MORE, callback_data="add_more"),
                InlineKeyboardButton(text=BTN_DONE, callback_data="add_done"),
            ]
        ]
    )


# --------------------------------------------------------------------------- #
#  Inline-клавиатуры карточки задачи (/taskN)
# --------------------------------------------------------------------------- #

def task_mark_kb(task_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Отметить выполнение» на карточке задачи."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_MARK, callback_data=f"mark:{task_id}")]
        ]
    )


def task_done_skip_kb(task_id: int) -> InlineKeyboardMarkup:
    """Выбор результата: Выполнено / Не выполнено."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_DONE_MARK, callback_data=f"done:{task_id}"),
                InlineKeyboardButton(text=BTN_SKIP_MARK, callback_data=f"skip:{task_id}"),
            ]
        ]
    )


# --------------------------------------------------------------------------- #
#  Inline-клавиатуры удаления и статистики (с пагинацией)
# --------------------------------------------------------------------------- #

def _truncate(name: str) -> str:
    """Обрезать длинное название для кнопки, добавив многоточие."""
    if len(name) <= _BUTTON_NAME_LIMIT:
        return name
    return name[: _BUTTON_NAME_LIMIT - 1].rstrip() + "…"


def _pagination_row(page: int, total_pages: int, prefix: str) -> list[InlineKeyboardButton]:
    """Собрать навигационный ряд: ◀️ «Страница X из N» ▶️.

    На первой странице нет «◀️», на последней — «▶️».
    """
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:{page - 1}"))
    row.append(
        InlineKeyboardButton(
            text=f"Страница {page + 1} из {total_pages}", callback_data="noop"
        )
    )
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:{page + 1}"))
    return row


def delete_list_kb(
    tasks: list[tuple[int, str]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Список задач для удаления: до 6 кнопок (3×2) + навигация.

    `tasks` — пары (id, name) только для текущей страницы.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(tasks), 2):
        chunk = tasks[i : i + 2]
        rows.append(
            [
                InlineKeyboardButton(
                    text=_truncate(name), callback_data=f"task_select:{task_id}"
                )
                for task_id, name in chunk
            ]
        )
    if total_pages > 1:
        rows.append(_pagination_row(page, total_pages, "task_page"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm_kb(task_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления: Удалить / Назад."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_DELETE, callback_data=f"task_delete_confirm:{task_id}"
                ),
                InlineKeyboardButton(text=BTN_BACK, callback_data="task_delete_cancel"),
            ]
        ]
    )


def stats_nav_kb(page: int, total_pages: int) -> InlineKeyboardMarkup | None:
    """Навигация по страницам статистики (None, если страница одна)."""
    if total_pages <= 1:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[_pagination_row(page, total_pages, "stats_page")]
    )


def current_years() -> list[int]:
    """Список годов для one_time: текущий + 2 следующих."""
    this_year = date.today().year
    return [this_year, this_year + 1, this_year + 2]

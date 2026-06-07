"""Все клавиатуры бота (reply и inline) в одном месте.

Хендлеры импортируют готовые клавиатуры отсюда и не собирают их вручную.
Callback-data завязана на короткие префиксы, разбираемые в соответствующих
роутерах.
"""

from __future__ import annotations

from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.constants import (
    BTN_ADD_TASK,
    BTN_ARROW_NEXT,
    BTN_ARROW_PREV,
    BTN_CONFIRM,
    BTN_DELETE,
    BTN_DONE,
    BTN_EDIT_FREQ,
    BTN_EDIT_NAME,
    BTN_EDIT_REMINDER,
    BTN_FREQ_DAILY,
    BTN_FREQ_SPECIFIC,
    BTN_MARK_OVERDUE,
    BTN_MARK_TODAY,
    BTN_NAV_NEXT,
    BTN_NAV_PREV,
    BTN_NO,
    BTN_REM_CHANGE,
    BTN_REM_REMOVE,
    BTN_REMINDER_NO,
    BTN_REMINDER_YES,
    BTN_RETURN_TASK,
    BTN_SAVE,
    BTN_SETTINGS_EVENING,
    BTN_SETTINGS_MORNING,
    BTN_SETTINGS_TIMEZONE,
    BTN_SKIP,
    BTN_START,
    BTN_TASK_DONE,
    BTN_TASKS_ALL,
    BTN_TASKS_TODAY,
    BTN_TODAY_BACK,
    BTN_YES,
    DELETE_PAGE_SIZE,
    EVENING_TIME_PRESETS,
    MORNING_TIME_PRESETS,
    OVERDUE_NO_PAGE_MAX,
    OVERDUE_PAGE_SIZE,
    TIMEZONE_PRESETS,
    TODAY_NO_PAGE_MAX,
    TODAY_PAGE_SIZE,
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
#
#  Кнопка «‹ Назад» (отдельной строкой внизу) есть на каждом шаге, кроме первого
#  после ввода названия (выбор частоты). Нажатие редактирует сообщение и
#  возвращает к предыдущему шагу — см. хендлеры add_back_* в handlers/add_task.py.
# --------------------------------------------------------------------------- #

def frequency_kb() -> InlineKeyboardMarkup:
    """Выбор частоты выполнения задачи (первый шаг после названия — без «Назад»)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_FREQ_DAILY, callback_data="freq:daily")],
            [InlineKeyboardButton(text=BTN_FREQ_SPECIFIC, callback_data="freq:specific")],
        ]
    )


def days_kb(selected: set[str]) -> InlineKeyboardMarkup:
    """Выбор дней недели с галочками + «Готово» + «‹ Назад» (к выбору частоты)."""
    buttons: list[InlineKeyboardButton] = []
    for code, short, _ in WEEKDAYS:
        mark = "✅ " if code in selected else ""
        buttons.append(
            InlineKeyboardButton(text=f"{mark}{short}", callback_data=f"day:{code}")
        )
    # Раскладка: 4 + 3 + ряд «Готово» + ряд «‹ Назад».
    rows = [
        buttons[:4],
        buttons[4:],
        [InlineKeyboardButton(text=BTN_DONE, callback_data="days_done")],
        [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="add_back_freq")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def has_reminder_kb() -> InlineKeyboardMarkup:
    """Вопрос про фиксированное время напоминания + «‹ Назад» (к предыдущему шагу)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_REMINDER_YES, callback_data="rem:yes"),
                InlineKeyboardButton(text=BTN_REMINDER_NO, callback_data="rem:no"),
            ],
            [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="add_back_from_reminder")],
        ]
    )


def reminder_time_back_kb() -> InlineKeyboardMarkup:
    """Экран ввода времени напоминания: только «‹ Назад» (к вопросу о напоминании)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="add_back_to_reminder")]
        ]
    )


# --------------------------------------------------------------------------- #
#  Inline-клавиатуры удаления (с пагинацией)
# --------------------------------------------------------------------------- #

def _truncate(name: str) -> str:
    """Обрезать длинное название для кнопки, добавив многоточие."""
    if len(name) <= _BUTTON_NAME_LIMIT:
        return name
    return name[: _BUTTON_NAME_LIMIT - 1].rstrip() + "…"


def delete_list_kb(
    tasks: list[tuple[int, str]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Список задач для удаления: до 6 кнопок (3×2) + навигация 2 кнопками.

    `tasks` — пары (id, name) только для текущей страницы. Навигация — как в
    статистике: «‹ Назад» и «Далее ›» всегда (когда страниц больше одной),
    alert на краях обрабатывает хендлер.
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
        rows.append(
            [
                InlineKeyboardButton(
                    text=BTN_NAV_PREV, callback_data=f"task_page:{page - 1}"
                ),
                InlineKeyboardButton(
                    text=BTN_NAV_NEXT, callback_data=f"task_page:{page + 1}"
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm_kb(task_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления: «🗑 Удалить», затем «‹ Назад» отдельной строкой."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_DELETE, callback_data=f"task_delete_confirm:{task_id}"
                )
            ],
            [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="task_delete_cancel")],
        ]
    )


# --------------------------------------------------------------------------- #
#  Inline-клавиатуры просмотра задач (/tasks)
#
#  Всё взаимодействие — в рамках одного сообщения (редактирование). Контекст
#  (раздел "a"/"t" и страница) кодируется прямо в callback-data, поэтому навигация
#  переживает сброс FSM. Иерархия: меню → список раздела → карточка задачи →
#  подтверждение удаления.
# --------------------------------------------------------------------------- #

def tasks_menu_kb() -> InlineKeyboardMarkup:
    """Меню /tasks: «Все задачи» (раздел "a") и «Задачи на сегодня» (раздел "t")."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_TASKS_ALL, callback_data="tk_list:a:0")],
            [InlineKeyboardButton(text=BTN_TASKS_TODAY, callback_data="tk_list:t:0")],
        ]
    )


def tasks_list_kb(
    items: list[tuple[int, str]],
    section: str,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Список задач раздела: кнопки задач (по 2 в ряд) + стрелки + «‹ Назад» (к меню).

    `items` — пары (id, name) текущей страницы (до 5). Кнопки идут по 2 в ряд
    (последняя нечётная — одна), ниже — стрелки «‹»/«›» (если страниц больше
    одной; alert на краях обрабатывает хендлер), внизу — «‹ Назад» к меню /tasks.
    """
    rows: list[list[InlineKeyboardButton]] = []
    buttons = [
        InlineKeyboardButton(
            text=_truncate(name), callback_data=f"tk_card:{section}:{page}:{task_id}"
        )
        for task_id, name in items
    ]
    rows.extend(buttons[i : i + 2] for i in range(0, len(buttons), 2))
    if total_pages > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=BTN_ARROW_PREV, callback_data=f"tk_list:{section}:{page - 1}"
                ),
                InlineKeyboardButton(
                    text=BTN_ARROW_NEXT, callback_data=f"tk_list:{section}:{page + 1}"
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="tasks_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_card_kb(
    section: str,
    page: int,
    task_id: int,
    is_today: bool,
    is_done: bool,
) -> InlineKeyboardMarkup:
    """Клавиатура карточки задачи.

    «Выполнено» с галочкой (☑️/⬜) и цветом кнопки (зелёная — выполнено, синяя —
    нет) — только если задача запланирована на сегодня (`is_today`); нажатие
    переключает статус. Ниже — удаление; в последнем ряду «‹ Назад» к списку
    (на ту же страницу раздела).
    """
    rows: list[list[InlineKeyboardButton]] = []
    if is_today:
        # Статус задачи показываем и галочкой в тексте, и цветом кнопки.
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_task_check_mark(is_done)} {BTN_TASK_DONE}",
                    callback_data=f"tk_done:{section}:{page}:{task_id}",
                    style=_task_check_style(is_done),
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=BTN_DELETE, callback_data=f"tk_del:{section}:{page}:{task_id}")]
    )
    rows.append(
        [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data=f"tk_list:{section}:{page}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_delete_confirm_kb(section: str, page: int, task_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления из карточки: «Подтвердить» (`tk_dok`) и «‹ Назад» (к карточке)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_CONFIRM, callback_data=f"tk_dok:{section}:{page}:{task_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=BTN_TODAY_BACK, callback_data=f"tk_card:{section}:{page}:{task_id}"
                )
            ],
        ]
    )


# --------------------------------------------------------------------------- #
#  Inline-клавиатуры редактирования задачи (/edit)
#
#  Список выбора задачи идентичен /delete (callback-data edit_select / edit_page).
#  Карточка задачи редактируется в рамках одного сообщения; «‹ Назад» ведёт к
#  предыдущему состоянию этого сообщения (callback edit_to_card / edit_freq_back).
# --------------------------------------------------------------------------- #

def edit_list_kb(
    tasks: list[tuple[int, str]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Список задач для редактирования: до 6 кнопок (3×2) + навигация 2 кнопками.

    Полностью повторяет раскладку `delete_list_kb`, но с callback-data
    `edit_select:{id}` / `edit_page:{page}`.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(tasks), 2):
        chunk = tasks[i : i + 2]
        rows.append(
            [
                InlineKeyboardButton(
                    text=_truncate(name), callback_data=f"edit_select:{task_id}"
                )
                for task_id, name in chunk
            ]
        )
    if total_pages > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=BTN_NAV_PREV, callback_data=f"edit_page:{page - 1}"
                ),
                InlineKeyboardButton(
                    text=BTN_NAV_NEXT, callback_data=f"edit_page:{page + 1}"
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_card_kb() -> InlineKeyboardMarkup:
    """Карточка задачи: «Название», «Частота», «Напоминание» и «‹ Назад» (к списку)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_EDIT_NAME, callback_data="edit_field:name")],
            [InlineKeyboardButton(text=BTN_EDIT_FREQ, callback_data="edit_field:freq")],
            [InlineKeyboardButton(text=BTN_EDIT_REMINDER, callback_data="edit_field:rem")],
            [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="edit_back_list")],
        ]
    )


def edit_return_kb() -> InlineKeyboardMarkup:
    """Одна кнопка «‹ Вернуться к задаче» (на сообщении об успешном изменении)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_RETURN_TASK, callback_data="edit_to_card")]
        ]
    )


def edit_freq_kb() -> InlineKeyboardMarkup:
    """Выбор частоты при редактировании: «Каждый день», «В конкретные дни», «‹ Назад»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_FREQ_DAILY, callback_data="edit_freq_set:daily")],
            [InlineKeyboardButton(text=BTN_FREQ_SPECIFIC, callback_data="edit_freq_set:specific")],
            [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="edit_to_card")],
        ]
    )


def edit_days_kb(selected: set[str]) -> InlineKeyboardMarkup:
    """Выбор дней недели при редактировании (раскладка как в /add) + «Готово» + «‹ Назад»."""
    buttons: list[InlineKeyboardButton] = []
    for code, short, _ in WEEKDAYS:
        mark = "✅ " if code in selected else ""
        buttons.append(
            InlineKeyboardButton(text=f"{mark}{short}", callback_data=f"eday:{code}")
        )
    rows = [
        buttons[:4],
        buttons[4:],
        [InlineKeyboardButton(text=BTN_DONE, callback_data="edays_done")],
        [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="edit_freq_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_reminder_menu_kb() -> InlineKeyboardMarkup:
    """Меню существующего напоминания: «Изменить»/«Убрать» и «‹ Назад» отдельной строкой."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_REM_CHANGE, callback_data="edit_rem_change"),
                InlineKeyboardButton(text=BTN_REM_REMOVE, callback_data="edit_rem_remove"),
            ],
            [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data="edit_to_card")],
        ]
    )


# --------------------------------------------------------------------------- #
#  Inline-клавиатуры интерактивной отметки задач (утренний/вечерний дайджест)
#
#  Под дайджестом — inline-кнопки отметки. По нажатию текущее сообщение
#  редактируется — всё в рамках одного сообщения, новых сообщений не отправляется.
#  Отметка сегодняшних задач у обоих дайджестов — флоу `dm_*` (галочки с
#  автосохранением, как /today; см. `digest_today_mark_kb`). Отметка вчерашних
#  просроченных (только утро) — флоу `md_*` с выбором и подтверждением
#  (`task_select_kb`/`select_confirm_kb` через обёртки `overdue_*`).
# --------------------------------------------------------------------------- #

# Цвета кнопок через поле InlineKeyboardButton.style (Bot API 9.4). На старых
# клиентах без поддержки 9.4 цвет дефолтный (механика при этом не страдает).
_STYLE_BLUE = "primary"    # синяя
_STYLE_GREEN = "success"   # зелёная
_STYLE_RED = "danger"      # красная

# Статус задачи на кнопке-галочке кодируется ДВУМЯ независимыми способами сразу:
#  1) эмодзи-галочка в тексте кнопки — ☑️ выполнено / ⬜ не выполнено (видна на
#     любом клиенте);
#  2) цвет кнопки — зелёная (выполнено) / синяя (не выполнено).
_CHECK_ON = "☑️"             # галочка — задача выполнена
_CHECK_OFF = "⬜"             # пустой чек-бокс — задача не выполнена
_STYLE_TODO = _STYLE_BLUE    # синяя кнопка — задача не выполнена
_STYLE_DONE = _STYLE_GREEN   # зелёная кнопка — задача выполнена


def _task_check_mark(is_done: bool) -> str:
    """Эмодзи-галочка кнопки задачи: ☑️, если выполнена, иначе ⬜."""
    return _CHECK_ON if is_done else _CHECK_OFF


def _task_check_style(is_done: bool) -> str:
    """Цвет кнопки задачи-галочки: зелёная, если выполнена, иначе синяя."""
    return _STYLE_DONE if is_done else _STYLE_TODO


def shown_check_state(
    markup: InlineKeyboardMarkup | None, callback_data: str
) -> bool | None:
    """Отображаемое СЕЙЧАС состояние кнопки-галочки по её callback_data.

    Возвращает True (☑️ выполнено) / False (⬜ не выполнено), читая текущую клавиатуру
    сообщения — то есть то, что в момент нажатия видит пользователь. Нужна, чтобы
    отметку применять относительно показанного состояния, а не статуса в БД: тогда
    «устаревшая» кнопка (статус задачи успели изменить из другого места — например,
    через напоминание или /today) при нажатии просто синхронизируется с актуальным
    значением, без конфликта и ошибки. None — если кнопки с такой callback_data в
    клавиатуре нет (определить показанное состояние не удалось).
    """
    if markup is None:
        return None
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data == callback_data:
                return button.text.startswith(_CHECK_ON)
    return None


def evening_digest_kb(origin: str) -> InlineKeyboardMarkup:
    """Клавиатура вечернего дайджеста: синяя кнопка «Отметить выполненные».

    Открывает /today-вид отметки сегодняшних задач с автосохранением прямо в этом
    сообщении (флоу `dm_mark:{origin}` — редактирование дайджеста + «‹ Назад»);
    `origin` несёт версию дайджеста для возврата по «‹ Назад» (для вечернего — "e").
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=BTN_MARK_TODAY, callback_data=f"dm_mark:{origin}", style=_STYLE_BLUE
            )
        ]]
    )


def morning_digest_kb(
    has_today_tasks: bool,
    has_overdue: bool,
) -> InlineKeyboardMarkup | None:
    """Клавиатура утреннего дайджеста.

    Сверху — синяя кнопка «Отметить выполненные» (`today_open`), если задачи на
    сегодня есть: по нажатию бот присылает НОВОЕ отдельное сообщение, идентичное
    ответу на команду /today, не трогая само сообщение дайджеста. Ниже — красная
    кнопка «Отметить вчерашние задачи» (`md_mark`), если есть просроченные. Если нет
    ни тех, ни других — клавиатуры нет.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if has_today_tasks:
        rows.append(
            [InlineKeyboardButton(
                text=BTN_MARK_TODAY, callback_data="today_open", style=_STYLE_BLUE
            )]
        )
    if has_overdue:
        rows.append(
            [InlineKeyboardButton(text=BTN_MARK_OVERDUE, callback_data="md_mark", style=_STYLE_RED)]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def today_mark_kb(
    items: list[tuple[int, str]],
    selected: set[int],
    page: int,
    *,
    toggle_cb: str = "today_toggle",
    page_cb: str = "today_page",
    back_cb: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура отметки сегодняшних задач галочками (механика автосохранения).

    Статус кодируется и галочкой в тексте (☑️/⬜), и цветом кнопки (синяя — не
    выполнена, зелёная — выполнена). Промежуточных кнопок нет — отметка применяется
    сразу по нажатию. До 8 задач — ряды по 2 без пагинации (до 4 рядов); больше —
    6 на странице (3 ряда) плюс ряд пагинации «‹ Назад» / «Далее ›» (alert на краях
    обрабатывает хендлер). Текущая страница кодируется прямо в callback-data кнопок
    (`{toggle_cb}:{page}:{id}`), поэтому флоу полностью без FSM и переживает рестарт
    бота.

    `toggle_cb`/`page_cb` задают пространство callback-data: по умолчанию — команда
    /today (`today_toggle`/`today_page`); утренний дайджест передаёт сюда префиксы
    с origin (`dm_toggle:{origin}` и т.п.). Если задан `back_cb`, последним рядом
    добавляется красная кнопка «‹ Назад» (в дайджесте — возврат к нему), что даёт
    максимум 5 рядов вместо 4.
    """
    nav_row: list[InlineKeyboardButton] = []
    if len(items) <= TODAY_NO_PAGE_MAX:
        page = 0
        page_items = items
    else:
        total_pages = (len(items) + TODAY_PAGE_SIZE - 1) // TODAY_PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        start = page * TODAY_PAGE_SIZE
        page_items = items[start : start + TODAY_PAGE_SIZE]
        nav_row = [
            InlineKeyboardButton(
                text=BTN_NAV_PREV, callback_data=f"{page_cb}:{page - 1}"
            ),
            InlineKeyboardButton(
                text=BTN_NAV_NEXT, callback_data=f"{page_cb}:{page + 1}"
            ),
        ]

    buttons = [
        InlineKeyboardButton(
            text=f"{_task_check_mark(task_id in selected)} {_truncate(name)}",
            callback_data=f"{toggle_cb}:{page}:{task_id}",
            style=_task_check_style(task_id in selected),
        )
        for task_id, name in page_items
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if nav_row:
        rows.append(nav_row)
    if back_cb is not None:
        rows.append(
            [InlineKeyboardButton(text=BTN_TODAY_BACK, callback_data=back_cb, style=_STYLE_RED)]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def digest_today_mark_kb(
    items: list[tuple[int, str]],
    selected: set[int],
    page: int,
    origin: str,
) -> InlineKeyboardMarkup:
    """Отметка сегодняшних задач из дайджеста с правкой того же сообщения: как /today
    + красная «Назад».

    Та же механика галочек с автосохранением, что и в /today, но callback-data в
    пространстве "dm" с `origin` — версией дайджеста, из которой вошли ("e" — вечерний
    дайджест; "d"/"f" — обычный/финальный вид утреннего, остаются для старых
    сообщений: утренний дайджест теперь открывает /today отдельным сообщением, см.
    `morning_digest_kb`). Красная кнопка «‹ Назад» (`dm_back:{origin}`) возвращает
    РОВНО к этой версии. Максимум 5 рядов.
    """
    return today_mark_kb(
        items, selected, page,
        toggle_cb=f"dm_toggle:{origin}",
        page_cb=f"dm_page:{origin}",
        back_cb=f"dm_back:{origin}",
    )


def task_select_kb(
    items: list[tuple[int, str]],
    selected: set[int],
    page: int,
    prefix: str,
    *,
    done_text: str = BTN_DONE,
    done_style: str | None = None,
    back_style: str | None = None,
) -> InlineKeyboardMarkup:
    """Выбор задач кнопками с галочкой и цветом + кнопка-завершение + «‹ Назад».

    Выбор кодируется и галочкой в тексте (☑️/⬜), и цветом кнопки (синяя — не
    отмечена, зелёная — отмечена). До 6 задач — ряды по 2 (без пагинации). Больше —
    4 на странице (2 ряда) и ряд пагинации «‹ Назад» / «Далее ›» (alert на краях).
    Затем ряд кнопки-завершения и отдельной строкой «‹ Назад» (к дайджесту).
    Используется флоу отметки сегодняшних (`tm`, вечер) и вчерашних просроченных
    (`md`, утро) задач. Текст и цвет кнопки-завершения и цвет «‹ Назад»
    параметризуются (`done_text`/`done_style`/`back_style`): для просроченных —
    «Сохранить» и красные цвета (см. `overdue_select_kb`).
    """
    nav_row: list[InlineKeyboardButton] = []
    if len(items) <= OVERDUE_NO_PAGE_MAX:
        page_items = items
    else:
        total_pages = (len(items) + OVERDUE_PAGE_SIZE - 1) // OVERDUE_PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        start = page * OVERDUE_PAGE_SIZE
        page_items = items[start : start + OVERDUE_PAGE_SIZE]
        nav_row = [
            InlineKeyboardButton(
                text=BTN_NAV_PREV, callback_data=f"{prefix}_page:{page - 1}"
            ),
            InlineKeyboardButton(
                text=BTN_NAV_NEXT, callback_data=f"{prefix}_page:{page + 1}"
            ),
        ]

    buttons = [
        InlineKeyboardButton(
            text=f"{_task_check_mark(task_id in selected)} {_truncate(name)}",
            callback_data=f"{prefix}_toggle:{task_id}",
            style=_task_check_style(task_id in selected),
        )
        for task_id, name in page_items
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if nav_row:
        rows.append(nav_row)
    rows.append(
        [InlineKeyboardButton(text=done_text, callback_data=f"{prefix}_done", style=done_style)]
    )
    rows.append(
        [InlineKeyboardButton(
            text=BTN_TODAY_BACK, callback_data=f"{prefix}_back_digest", style=back_style
        )]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def overdue_select_kb(
    items: list[tuple[int, str]],
    selected: set[int],
    page: int,
) -> InlineKeyboardMarkup:
    """Выбор просроченных задач (утро): галочки + «Сохранить» (красная) + «‹ Назад» (красная)."""
    return task_select_kb(
        items, selected, page, "md",
        done_text=BTN_SAVE, done_style=_STYLE_RED, back_style=_STYLE_RED,
    )


def select_confirm_kb(
    prefix: str,
    *,
    confirm_style: str | None = None,
    back_style: str | None = None,
) -> InlineKeyboardMarkup:
    """Экран подтверждения: «Подтвердить», затем «‹ Назад» отдельной строкой.

    Цвета кнопок параметризуются (`confirm_style`/`back_style`): для просроченных —
    зелёная «Подтвердить» и красная «‹ Назад» (см. `overdue_confirm_kb`).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=BTN_CONFIRM, callback_data=f"{prefix}_confirm", style=confirm_style
            )],
            [InlineKeyboardButton(
                text=BTN_TODAY_BACK, callback_data=f"{prefix}_back_select", style=back_style
            )],
        ]
    )


def overdue_confirm_kb() -> InlineKeyboardMarkup:
    """Подтверждение просроченных (утро): «Подтвердить» (зелёная) + «‹ Назад» (красная)."""
    return select_confirm_kb("md", confirm_style=_STYLE_GREEN, back_style=_STYLE_RED)


def overdue_expired_kb() -> InlineKeyboardMarkup:
    """Экран «время вышло» (утренний дайджест): единственная синяя кнопка «Подтвердить»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=BTN_CONFIRM, callback_data="md_expired_ok", style=_STYLE_BLUE
            )]
        ]
    )


def reminder_kb(
    task_id: int, target_date: date, name: str, is_done: bool
) -> InlineKeyboardMarkup:
    """Кнопка-галочка задачи на сообщении-напоминании (стандартная механика отметки).

    Как в /today и дайджестах: синяя кнопка с ⬜ и названием задачи, по нажатию —
    ☑️ и зелёный цвет (выполнено); повторное нажатие возвращает обратно. Статус
    кодируется и галочкой в тексте, и цветом (`_task_check_mark`/`_task_check_style`).
    Кнопка не пропадает, дата отметки — в callback-data (`rem_done:{id}:{дата}`),
    поэтому отметка работает и после рестарта.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{_task_check_mark(is_done)} {_truncate(name)}",
                    callback_data=f"rem_done:{task_id}:{target_date.isoformat()}",
                    style=_task_check_style(is_done),
                )
            ]
        ]
    )

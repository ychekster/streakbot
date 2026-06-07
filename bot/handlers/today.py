"""Интерактивные дайджесты и напоминания.

Здесь живёт вся логика утреннего и вечернего дайджестов (сборка текста и
интерактивная отметка задач), напоминание о задаче с кнопкой-галочкой отметки
(стандартная механика, как у /today), а также команда `/today` — список задач на
сегодня сразу с галочками (отметка применяется по нажатию). Отдельной команды
`/done` нет.

Отметка сегодняшних задач у дайджестов отличается:
- **Утренний**: синяя кнопка «Отметить выполненные» (`today_open`) присылает НОВОЕ
  отдельное сообщение, идентичное ответу на команду /today (галочки с
  автосохранением). Само сообщение дайджеста при этом не трогается, и от имени
  пользователя в чат ничего не отправляется.
- **Вечерний**: синяя кнопка «Отметить выполненные» редактирует то же сообщение в
  вид /today прямо на месте — флоу `dm_*` (галочки с автосохранением + красная
  «‹ Назад»). Без FSM, без «Готово» и подтверждения. Шаги несут origin — версию
  дайджеста, из которой вошли ("e" — вечерний); «‹ Назад» (`dm_back:{origin}`)
  возвращает к ней. Флоу `dm_*` обслуживает также старые сообщения утреннего
  дайджеста (origin "d"/"f"), отправленные до перехода на `today_open`.

Утренний дайджест дополнительно несёт красную кнопку отметки вчерашних
просроченных задач (`md_*`): экран выбора → «Сохранить» → подтверждение →
«Подтвердить»; после подтверждения дайджест показывается без блока просроченных,
поэтому кнопка просроченных больше не появляется. Логика определения просроченных
(`_tasks_for_date_not_done`) при этом не меняется.

Защита от «устаревших» кнопок: многошаговый флоу `md_*` выставляет FSM-состояние
`MarkingStates.active`, требуемое всеми его шагами (если команда сбросила FSM,
нажатия на старом сообщении ничего не делают). Флоу `dm_*` без FSM: он идемпотентен
(toggle переключает статус в БД, страница и origin — в callback-data), поэтому
защита состоянием ему не нужна, и кнопки работают даже после рестарта.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytz
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from loguru import logger

from bot.constants import OVERDUE_PAGE_SIZE, TEXTS, TODAY_PAGE_SIZE
from bot.database.models import Task, TaskLog, TaskStatus, User
from bot.database.repository import Repository
from bot.keyboards.builders import (
    REMOVE_KB,
    digest_today_mark_kb,
    evening_digest_kb,
    morning_digest_kb,
    overdue_confirm_kb,
    overdue_expired_kb,
    overdue_select_kb,
    reminder_kb,
    today_mark_kb,
)
from bot.utils.validators import escape_md

router = Router(name="today")

# Версия дайджеста, из которой вошли в отметку сегодняшних задач правкой того же
# сообщения (флоу `dm_*`). Несётся в callback-data, чтобы «‹ Назад» вернул РОВНО к ней:
#   "e" — вечерний дайджест (актуальный потребитель флоу `dm_*`);
#   "d" — обычный утренний дайджест (с возможным блоком/кнопкой просроченных);
#   "f" — финальный вид утреннего дайджеста без просроченных.
# "d"/"f" остаются только для старых сообщений утреннего дайджеста: теперь его кнопка
# «Отметить выполненные» (`today_open`) присылает /today отдельным сообщением.
_DM_DIGEST = "d"
_DM_FINAL = "f"
_DM_EVENING = "e"


class MarkingStates(StatesGroup):
    """Активный флоу отметки вчерашних просроченных задач (`md_*`).

    Состояние выставляется при входе (`md_mark`, который остаётся без фильтра
    состояния — чтобы кнопка под дайджестом срабатывала и после рестарта, когда FSM
    пуст) и требуется всеми последующими шагами. Это защищает от срабатывания
    «устаревших» кнопок на старом сообщении после того, как любая команда сбросила
    FSM, и гарантирует, что выбор (`md_selected`) ещё на месте.
    """

    active = State()


def _user_tz(user: User) -> pytz.BaseTzInfo:
    """Таймзона пользователя (UTC как фолбэк)."""
    try:
        return pytz.timezone(user.timezone) if user.timezone else pytz.utc
    except Exception:  # noqa: BLE001
        return pytz.utc


async def _today_tasks(
    repo: Repository,
    user: User,
    today,
) -> tuple[list[tuple[Task, TaskLog]], list[tuple[Task, TaskLog]]]:
    """Вернуть (активные, отмеченные) задачи на сегодня, создав pending-логи.

    Активные — со статусом pending, отмеченные — done/skipped.
    """
    tasks = await repo.get_tasks_due_on(user.telegram_id, today)
    active: list[tuple[Task, TaskLog]] = []
    marked: list[tuple[Task, TaskLog]] = []
    for task in tasks:
        log = await repo.get_or_create_log(task.id, user.telegram_id, today)
        if log.status == TaskStatus.pending:
            active.append((task, log))
        else:
            marked.append((task, log))
    return active, marked


def _today_block(ordered: list[Task], done_ids: set[int]) -> str:
    """Блок задач на сегодня (стиль /today, без нумерации).

    Сначала невыполненные задачи (обычным текстом), затем выполненные
    (зачёркнутые); между непустыми группами — пустая строка, пустые группы
    опускаются. Заголовок и подсказку добавляет вызывающая функция.
    """
    not_done = [f"• {escape_md(t.name)}" for t in ordered if t.id not in done_ids]
    done = [f"• ~{escape_md(t.name)}~" for t in ordered if t.id in done_ids]
    blocks: list[str] = []
    if not_done:
        blocks.append("\n".join(not_done))
    if done:
        blocks.append("\n".join(done))
    return "\n\n".join(blocks)


def _digest_tasks_text(header_key: str, ordered: list[Task], done_ids: set[int]) -> str:
    """Текст дайджеста-списка сегодняшних задач: жирный заголовок + блок задач
    (буллеты «• », выполненные зачёркнуты) + жирная подсказка нажать кнопку. Если
    задач нет — только заголовок. Общий для финального вида утреннего дайджеста и для
    вечернего дайджеста — различаются только заголовком (`header_key`).
    """
    header = f"*{escape_md(TEXTS[header_key])}*"
    if not ordered:
        return header
    prompt = f"*{escape_md(TEXTS['digest_today_mark_prompt'])}*"
    return f"{header}\n\n{_today_block(ordered, done_ids)}\n\n{prompt}"


async def _tasks_for_date_not_done(
    repo: Repository,
    user: User,
    target_date,
) -> list[Task]:
    """Активные задачи с логом за указанную дату и статусом не `done`.

    Включает все типы задач. Удалённые (is_active=False) задачи исключаются.
    Используется для просроченных (вчера) в утреннем дайджесте.
    """
    logs = await repo.get_logs_for_date(user.telegram_id, target_date)
    tasks: list[Task] = []
    for log in logs:
        if log.status == TaskStatus.done:
            continue
        task = await repo.get_task(log.task_id)
        if task is not None and task.is_active:
            tasks.append(task)
    tasks.sort(key=lambda t: t.id)
    return tasks


async def _overdue_tasks(repo: Repository, user: User, today) -> list[Task]:
    """Просроченные = вчерашние невыполненные задачи (любого типа)."""
    return await _tasks_for_date_not_done(repo, user, today - timedelta(days=1))


async def _today_marking_tasks(
    repo: Repository,
    user: User,
    today,
) -> tuple[list[Task], set[int]]:
    """Сегодняшние задачи для отметки (по логам за сегодня).

    Возвращает (упорядоченный список задач, множество id выполненных). Порядок:
    сначала невыполненные, затем выполненные; внутри групп — по id. Логи здесь
    НЕ создаются — берутся уже существующие (их создаёт сборка дайджеста через
    `_today_tasks`). Удалённые (is_active=False) задачи исключаются.
    """
    logs = await repo.get_logs_for_date(user.telegram_id, today)
    not_done: list[Task] = []
    done: list[Task] = []
    for log in logs:
        task = await repo.get_task(log.task_id)
        if task is None or not task.is_active:
            continue
        (done if log.status == TaskStatus.done else not_done).append(task)
    not_done.sort(key=lambda t: t.id)
    done.sort(key=lambda t: t.id)
    return not_done + done, {task.id for task in done}


async def build_evening_digest(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Вечерний дайджест для планировщика: список сегодняшних задач + inline-кнопка.

    Структура текста — как у финального вида утреннего дайджеста (жирный заголовок
    «Итоги дня» + блок задач на сегодня + подсказка), без блока просроченных;
    различается только заголовок. Кнопка «Отметить выполненные» открывает /today-вид
    отметки с автосохранением (флоу `dm_*`, origin "e" — «‹ Назад» вернёт сюда). Если
    на сегодня задач нет — заглушка без кнопки. Возвращает (текст, inline-клавиатуру).
    """
    today = datetime.now(_user_tz(user)).date()
    ordered, done_ids = await _today_ordered(repo, user, today)  # создаёт логи + читает
    if not ordered:
        return escape_md(TEXTS["digest_evening_no_tasks"]), None
    text = _digest_tasks_text("digest_evening_header", ordered, done_ids)
    keyboard = evening_digest_kb(_DM_EVENING)
    return text, keyboard


async def _morning_digest_text(repo: Repository, user: User) -> str:
    """Текст утреннего дайджеста: вступление + блок задач на сегодня + (если есть)
    блок просроченных с подсказкой про дедлайн 12:00.

    Заголовки блоков — жирные; невыполненные задачи обычным текстом, выполненные —
    зачёркнутыми. Сначала создаются pending-логи сегодняшних задач.
    """
    today = datetime.now(_user_tz(user)).date()
    await _today_tasks(repo, user, today)  # создаём pending-логи сегодняшних задач
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    overdue = await _overdue_tasks(repo, user, today)

    if not ordered and not overdue:
        return escape_md(TEXTS["digest_morning_no_tasks"])

    parts = [escape_md(TEXTS["digest_morning_intro"])]
    if ordered:
        header = f"*{escape_md(TEXTS['digest_morning_tasks_header'])}*"
        parts.append(f"{header}\n{_today_block(ordered, done_ids)}")
    if overdue:
        oheader = f"*{escape_md(TEXTS['digest_overdue_header'])}*"
        olist = "\n".join(f"• {escape_md(task.name)}" for task in overdue)
        parts.append(f"{oheader}\n{olist}")
        parts.append(escape_md(TEXTS["digest_overdue_footer"]))
    return "\n\n".join(parts)


async def build_morning_digest(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Утренний дайджест для планировщика: текст + inline-кнопки отметки.

    Если есть просроченные задачи — обычный вид: вступление + блок задач на сегодня
    + блок просроченных, синяя «Отметить выполненные» (если задачи на сегодня есть) и
    красная «Отметить вчерашние задачи». Если просроченных нет — сразу финальный вид
    (тот же формат, что показывается после прохождения флоу отметки просроченных):
    заголовок + задачи на сегодня + синяя кнопка, без блока и кнопки просроченных
    (`_morning_final_view`). Синяя кнопка «Отметить выполненные» (`today_open`)
    присылает НОВОЕ отдельное сообщение, идентичное /today, не трогая сам дайджест.
    Возвращает (текст, inline-клавиатуру).
    """
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    if not overdue:
        # Просроченных нет — показываем сразу финальный вид.
        return await _morning_final_view(repo, user)
    text = await _morning_digest_text(repo, user)
    ordered, _ = await _today_marking_tasks(repo, user, today)
    keyboard = morning_digest_kb(bool(ordered), True)
    return text, keyboard


# --------------------------------------------------------------------------- #
#  Интерактивный утренний дайджест: отметка вчерашних (просроченных) задач
#
#  Вход — inline-кнопка «Отметить вчерашние задачи» (`md_mark`, без фильтра
#  состояния) под дайджестом: редактирует текущее сообщение в экран выбора и
#  выставляет `MarkingStates.active`. Дальнейшие шаги (`md_*`) требуют этого
#  состояния. Выбор (галочки) хранится в FSM-данных (md_selected, md_page);
#  просроченные задачи каждый раз пересчитываются из БД. «Подтвердить» активно
#  до 12:00 по времени пользователя.
# --------------------------------------------------------------------------- #

def _overdue_select_text(overdue: list[Task]) -> str:
    """Экран выбора просроченных: жирный заголовок-название + список + жирная
    подсказка (без нумерации, в стиле /today)."""
    title = f"*{escape_md(TEXTS['overdue_select_title'])}*"
    prompt = f"*{escape_md(TEXTS['overdue_select_header'])}*"
    tasks = "\n".join(f"• {escape_md(task.name)}" for task in overdue)
    return f"{title}\n\n{tasks}\n\n{prompt}"


def _overdue_confirm_text(overdue: list[Task], selected: set[int]) -> str:
    """Экран подтверждения просроченных: заголовок + жирные блоки «Засчитаются» /
    «Сгорят» (каждый — если непустой) + предупреждение."""
    marked = [task for task in overdue if task.id in selected]
    unmarked = [task for task in overdue if task.id not in selected]
    parts = [escape_md(TEXTS["overdue_confirm_header"])]
    if marked:
        header = f"*{escape_md(TEXTS['overdue_confirm_marked'])}*"
        parts.append(header + "\n" + "\n".join(f"• {escape_md(t.name)}" for t in marked))
    if unmarked:
        header = f"*{escape_md(TEXTS['overdue_confirm_unmarked'])}*"
        parts.append(header + "\n" + "\n".join(f"• {escape_md(t.name)}" for t in unmarked))
    parts.append(escape_md(TEXTS["overdue_confirm_warning"]))
    return "\n\n".join(parts)


def _morning_final_text(ordered: list[Task], done_ids: set[int]) -> str:
    """Финальный вид утреннего дайджеста после подтверждения просроченных: без блока
    просроченных. Жирный заголовок + блок сегодняшних задач + жирная подсказка
    (та же структура, что и у вечернего дайджеста — см. `_digest_tasks_text`)."""
    return _digest_tasks_text("digest_morning_final_header", ordered, done_ids)


async def _morning_final_view(
    repo: Repository, user: User
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст и клавиатура финального вида: без просроченных, остаётся только кнопка
    отметки сегодняшних задач (если они есть).

    Если задач на сегодня нет — короткая заглушка без клавиатуры (как и у обычного
    утреннего дайджеста в пустой день).
    """
    today = datetime.now(_user_tz(user)).date()
    await _today_tasks(repo, user, today)  # гарантируем pending-логи сегодняшних задач
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    if not ordered:
        return escape_md(TEXTS["digest_morning_no_tasks"]), None
    text = _morning_final_text(ordered, done_ids)
    # Без блока и кнопки просроченных: остаётся только синяя «Отметить выполненные»
    # (`today_open`) — она присылает /today отдельным сообщением, не трогая дайджест.
    keyboard = morning_digest_kb(bool(ordered), False)
    return text, keyboard


@router.callback_query(F.data == "md_mark")
async def md_mark(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Inline-кнопка «Отметить вчерашние задачи» — открыть экран выбора (без фильтра состояния)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    if not overdue:
        # Просроченных уже нет — возвращаем дайджест в исходный вид (с кнопками).
        await callback.answer(TEXTS["overdue_none"], show_alert=True)
        text, keyboard = await build_morning_digest(repo, user)
        await callback.message.edit_text(text, reply_markup=keyboard)
        return
    await state.set_state(MarkingStates.active)
    await state.update_data(md_selected=[], md_page=0)
    await callback.message.edit_text(
        _overdue_select_text(overdue),
        reply_markup=overdue_select_kb([(t.id, t.name) for t in overdue], set(), 0),
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data.startswith("md_toggle:"))
async def md_toggle(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Переключить галочку на задаче (меняем только клавиатуру)."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    overdue_ids = {task.id for task in overdue}
    data = await state.get_data()
    selected = set(data.get("md_selected", [])) & overdue_ids
    page = data.get("md_page", 0)
    if task_id in overdue_ids:
        selected.symmetric_difference_update({task_id})
    await state.update_data(md_selected=list(selected))
    await callback.message.edit_reply_markup(
        reply_markup=overdue_select_kb(
            [(t.id, t.name) for t in overdue], selected, page
        )
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data.startswith("md_page:"))
async def md_page(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Пагинация списка просроченных задач (alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    total_pages = max(1, (len(overdue) + OVERDUE_PAGE_SIZE - 1) // OVERDUE_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("md_selected", []))
    await state.update_data(md_page=page)
    await callback.message.edit_reply_markup(
        reply_markup=overdue_select_kb(
            [(t.id, t.name) for t in overdue], selected, page
        )
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "md_done")
async def md_done(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Готово» — показать экран подтверждения."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("md_selected", [])) & {task.id for task in overdue}
    await callback.message.edit_text(
        _overdue_confirm_text(overdue, selected), reply_markup=overdue_confirm_kb()
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "md_back_digest")
async def md_back_digest(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Назад» (с выбора просроченных) — вернуть исходный утренний дайджест."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text, keyboard = await build_morning_digest(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.clear()
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "md_back_select")
async def md_back_select(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Назад» (с подтверждения) — вернуть экран выбора (галочки сохраняются)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("md_selected", [])) & {task.id for task in overdue}
    page = data.get("md_page", 0)
    await callback.message.edit_text(
        _overdue_select_text(overdue),
        reply_markup=overdue_select_kb([(t.id, t.name) for t in overdue], selected, page),
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "md_confirm")
async def md_confirm(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Подтвердить»: до 12:00 — зафиксировать отметки, иначе экран «время вышло»."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    tz = _user_tz(user)
    now = datetime.now(tz)
    today = now.date()
    deadline = tz.localize(datetime.combine(today, time(12, 0)))
    if now >= deadline:
        # Дедлайн прошёл — показываем экран «время вышло»; состояние остаётся
        # активным, чтобы сработала кнопка `md_expired_ok`.
        await callback.message.edit_text(
            escape_md(TEXTS["overdue_expired"]), reply_markup=overdue_expired_kb()
        )
        await callback.answer()
        return

    overdue = await _overdue_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("md_selected", []))
    yesterday = today - timedelta(days=1)
    done_count = 0
    for task in overdue:
        log = await repo.get_or_create_log(task.id, user.telegram_id, yesterday)
        if task.id in selected:
            await repo.set_log_status(log, TaskStatus.done)
            done_count += 1
        else:
            await repo.set_log_status(log, TaskStatus.missed)
    logger.info(
        "User {} confirmed overdue marking ({}/{} done)",
        user.telegram_id, done_count, len(overdue),
    )
    text, keyboard = await _morning_final_view(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.clear()
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "md_expired_ok")
async def md_expired_ok(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Подтверждение на экране «время вышло» — показать финальный вид без изменений."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text, keyboard = await _morning_final_view(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.clear()
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Команда /today: список задач на сегодня сразу с отметкой галочками
#
#  Сообщение: жирный заголовок «Задачи на сегодня», блок невыполненных (обычный
#  текст), блок выполненных (зачёркнутый), жирный абзац-подсказка и клавиатура с
#  галочками. Промежуточных шагов нет: нажатие на галочку сразу переключает статус
#  задачи в БД (done ↔ pending) и на месте перестраивает текст и клавиатуру.
#  Флоу без FSM — источник истины статусов это БД, а текущая страница приходит в
#  callback-data, поэтому кнопки работают и после рестарта бота.
# --------------------------------------------------------------------------- #

def _today_view_text(ordered: list[Task], done_ids: set[int]) -> str:
    """Текст вида отметки сегодняшних задач (команда /today и тот же вид в утреннем
    дайджесте): жирный заголовок «Задачи на сегодня», блок задач (невыполненные
    обычным текстом, выполненные — зачёркнутые) и жирная подсказка.
    """
    header = f"*{escape_md(TEXTS['today_header'])}*"
    footer = f"*{escape_md(TEXTS['today_prompt'])}*"
    return f"{header}\n\n{_today_block(ordered, done_ids)}\n\n{footer}"


async def _today_ordered(
    repo: Repository,
    user: User,
    today,
) -> tuple[list[Task], set[int]]:
    """Сегодняшние задачи (упорядоченные) и id выполненных, гарантируя логи.

    Сначала создаёт недостающие pending-логи (как сборка дайджеста), затем читает
    актуальные статусы. Порядок: сначала невыполненные, затем выполненные.
    """
    await _today_tasks(repo, user, today)  # создаём pending-логи сегодняшних задач
    return await _today_marking_tasks(repo, user, today)


async def _toggle_today_log(
    repo: Repository, user: User, today, task_id: int
) -> bool:
    """Переключить статус сегодняшнего лога задачи (done ↔ pending) с автосохранением.

    Возвращает False, если задачи нет среди сегодняшних (удалена или больше не на
    сегодня) — тогда ничего не меняем. Общая механика для команды /today и для вида
    отметки сегодняшних задач в утреннем дайджесте (dm_*).
    """
    ordered, _ = await _today_ordered(repo, user, today)
    if task_id not in {task.id for task in ordered}:
        return False
    log = await repo.get_or_create_log(task_id, user.telegram_id, today)
    new_status = (
        TaskStatus.pending if log.status == TaskStatus.done else TaskStatus.done
    )
    await repo.set_log_status(log, new_status)
    logger.info(
        "User {} toggled today task {} (now {})",
        user.telegram_id, task_id, new_status.name,
    )
    return True


async def _send_today_view(target: Message, repo: Repository, user: User, today) -> None:
    """Отправить НОВОЕ сообщение со списком задач на сегодня и галочками — вид /today.

    Общая отправка для команды /today и для кнопки утреннего дайджеста «Отметить
    выполненные» (`today_open`), которая шлёт идентичное /today сообщение, не трогая
    сам дайджест. `target.answer` создаёт новое сообщение (а не редактирует). Если на
    сегодня задач нет — короткая заглушка (как у /today).
    """
    ordered, done_ids = await _today_ordered(repo, user, today)
    if not ordered:
        await target.answer(escape_md(TEXTS["tasks_today_empty"]), reply_markup=REMOVE_KB)
        return
    await target.answer(
        _today_view_text(ordered, done_ids),
        reply_markup=today_mark_kb([(t.id, t.name) for t in ordered], done_ids, 0),
    )


@router.message(Command("today"))
async def cmd_today(message: Message, state: FSMContext, repo: Repository) -> None:
    """/today — список задач на сегодня сразу с галочками (отметка по нажатию)."""
    await state.clear()
    user = await repo.get_user(message.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    await _send_today_view(message, repo, user, today)


@router.callback_query(F.data == "today_open")
async def today_open(callback: CallbackQuery, repo: Repository) -> None:
    """Кнопка утреннего дайджеста «Отметить выполненные».

    Присылает НОВОЕ отдельное сообщение, идентичное ответу на команду /today (список
    задач с галочками-автосохранением). Само сообщение дайджеста НЕ изменяется, и от
    имени пользователя в чат ничего не отправляется. Дальнейшие нажатия галочек на
    новом сообщении обрабатывают штатные хендлеры /today (`today_toggle`/`today_page`).
    """
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    await _send_today_view(callback.message, repo, user, today)
    await callback.answer()


@router.callback_query(F.data.startswith("today_toggle:"))
async def today_toggle(callback: CallbackQuery, repo: Repository) -> None:
    """Нажатие на галочку /today: сразу переключить статус задачи в БД и перестроить вид.

    Без FSM: статус берётся из БД и тут же меняется (done ↔ pending), страница
    приходит в callback-data. Поэтому кнопки работают и после рестарта бота.
    """
    if callback.message is None:
        await callback.answer()
        return
    # callback-data: "today_toggle:{page}:{task_id}".
    _, page_raw, task_id_raw = callback.data.split(":")
    page, task_id = int(page_raw), int(task_id_raw)
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    if not await _toggle_today_log(repo, user, today, task_id):
        # Задача исчезла (удалена) или больше не на сегодня — ничего не меняем.
        await callback.answer()
        return
    # Пересчитываем порядок и галочки после изменения и перерисовываем сообщение.
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    await callback.message.edit_text(
        _today_view_text(ordered, done_ids),
        reply_markup=today_mark_kb([(t.id, t.name) for t in ordered], done_ids, page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("today_page:"))
async def today_page(callback: CallbackQuery, repo: Repository) -> None:
    """Пагинация клавиатуры /today (текст показывает все задачи; alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    total_pages = max(1, (len(ordered) + TODAY_PAGE_SIZE - 1) // TODAY_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    await callback.message.edit_reply_markup(
        reply_markup=today_mark_kb([(t.id, t.name) for t in ordered], done_ids, page)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Вечерний дайджест: отметка сегодняшних задач правкой того же сообщения (флоу dm_*)
#
#  Кнопка «Отметить выполненные» (`dm_mark:{origin}`) редактирует дайджест в тот же
#  вид, что и /today: галочки с автосохранением (нажатие сразу пишет статус в БД),
#  плюс последним рядом красная кнопка «‹ Назад» (`dm_back:{origin}`). origin несёт
#  версию дайджеста, из которой вошли: "e" — вечерний. «‹ Назад» возвращает РОВНО к
#  ней. Без FSM (как /today): страница и origin в callback-data, источник истины
#  статусов — БД, поэтому кнопки работают и после рестарта.
#
#  Утренний дайджест на этот флоу больше НЕ заходит — его кнопка «Отметить
#  выполненные» (`today_open`) присылает /today отдельным сообщением. Origin "d"/"f"
#  (обычный/финальный вид утреннего) остаётся поддержан здесь только для старых
#  сообщений утреннего дайджеста, отправленных до перехода на `today_open`.
# --------------------------------------------------------------------------- #

def _dm_origin(value: str | None) -> str:
    """Нормализовать origin dm-флоу: 'f' (финальный вид) / 'e' (вечерний) / 'd' (обычный)."""
    return value if value in (_DM_FINAL, _DM_EVENING) else _DM_DIGEST


async def _dm_return_view(
    repo: Repository, user: User, origin: str
) -> tuple[str, InlineKeyboardMarkup | None]:
    """(Текст, клавиатура) той версии дайджеста, из которой вошли в отметку задач.

    origin='f' — финальный вид утреннего без просроченных, 'e' — вечерний дайджест,
    'd' — обычный утренний. Используется кнопкой «‹ Назад» и фолбэком при отсутствии
    задач, чтобы сообщение восстанавливалось ровно в исходную версию.
    """
    if origin == _DM_FINAL:
        return await _morning_final_view(repo, user)
    if origin == _DM_EVENING:
        return await build_evening_digest(repo, user)
    return await build_morning_digest(repo, user)


@router.callback_query(F.data.startswith("dm_mark:"))
async def dm_mark(callback: CallbackQuery, repo: Repository) -> None:
    """Кнопка дайджеста «Отметить выполненные» — открыть /today-вид отметки сегодняшних задач."""
    if callback.message is None:
        await callback.answer()
        return
    # callback-data: "dm_mark:{origin}".
    origin = _dm_origin(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, done_ids = await _today_ordered(repo, user, today)
    if not ordered:
        # Задач на сегодня нет (например, устаревшая кнопка) — вернуть ту же версию.
        await callback.answer(TEXTS["tasks_today_empty"], show_alert=True)
        text, keyboard = await _dm_return_view(repo, user, origin)
        await callback.message.edit_text(text, reply_markup=keyboard)
        return
    await callback.message.edit_text(
        _today_view_text(ordered, done_ids),
        reply_markup=digest_today_mark_kb(
            [(t.id, t.name) for t in ordered], done_ids, 0, origin
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dm_toggle:"))
async def dm_toggle(callback: CallbackQuery, repo: Repository) -> None:
    """Галочка в дайджесте: сразу переключить статус задачи в БД и перестроить вид."""
    if callback.message is None:
        await callback.answer()
        return
    # callback-data: "dm_toggle:{origin}:{page}:{task_id}".
    parts = callback.data.split(":")
    if len(parts) != 4:  # устаревший формат (без origin) у старых сообщений — игнорируем
        await callback.answer()
        return
    _, origin_raw, page_raw, task_id_raw = parts
    origin = _dm_origin(origin_raw)
    page, task_id = int(page_raw), int(task_id_raw)
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    if not await _toggle_today_log(repo, user, today, task_id):
        # Задача исчезла (удалена) или больше не на сегодня — ничего не меняем.
        await callback.answer()
        return
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    await callback.message.edit_text(
        _today_view_text(ordered, done_ids),
        reply_markup=digest_today_mark_kb(
            [(t.id, t.name) for t in ordered], done_ids, page, origin
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dm_page:"))
async def dm_page(callback: CallbackQuery, repo: Repository) -> None:
    """Пагинация вида отметки сегодняшних задач в дайджесте (alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    # callback-data: "dm_page:{origin}:{page}".
    parts = callback.data.split(":")
    if len(parts) != 3:  # устаревший формат (без origin) у старых сообщений — игнорируем
        await callback.answer()
        return
    _, origin_raw, page_raw = parts
    origin = _dm_origin(origin_raw)
    page = int(page_raw)
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    total_pages = max(1, (len(ordered) + TODAY_PAGE_SIZE - 1) // TODAY_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    await callback.message.edit_reply_markup(
        reply_markup=digest_today_mark_kb(
            [(t.id, t.name) for t in ordered], done_ids, page, origin
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dm_back:"))
async def dm_back(callback: CallbackQuery, repo: Repository) -> None:
    """«‹ Назад» из вида отметки — вернуть РОВНО ту версию дайджеста, из которой вошли."""
    if callback.message is None:
        await callback.answer()
        return
    # callback-data: "dm_back:{origin}".
    origin = _dm_origin(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    text, keyboard = await _dm_return_view(repo, user, origin)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Напоминание о задаче: текст + кнопка-галочка отметки (стандартная механика)
#
#  Сообщение: жирный заголовок «Напоминание» + строка «Пора выполнить задачу …».
#  Кнопка под ним — стандартная галочка отметки (как /today): синяя ⬜ с названием
#  задачи, по нажатию — зелёная ☑️; повторное нажатие возвращает обратно. Кнопка не
#  пропадает, текст сообщения от её состояния не зависит. Если задача уже отмечена
#  выполненной за дату напоминания — кнопка приходит сразу зелёной с галочкой. Без
#  FSM: статус — из БД, дата — в callback-data, поэтому кнопка работает и после
#  рестарта бота.
# --------------------------------------------------------------------------- #

def _reminder_text(name: str) -> str:
    """Текст напоминания: жирный заголовок «Напоминание» + строка с названием задачи."""
    title = f"*{escape_md(TEXTS['reminder_title'])}*"
    body = escape_md(TEXTS["reminder_body"].format(name=name))
    return f"{title}\n\n{body}"


async def build_reminder(
    repo: Repository, task: Task, target_date: date
) -> tuple[str, InlineKeyboardMarkup]:
    """Собрать напоминание о задаче для планировщика: текст + кнопка-галочка.

    Кнопка приходит сразу зелёной с галочкой, если задача уже отмечена выполненной
    за `target_date` (иначе синяя с пустым квадратом). Текст от состояния кнопки не
    зависит. Возвращает (текст, inline-клавиатуру).
    """
    log = await repo.get_log(task.id, target_date)
    is_done = log is not None and log.status == TaskStatus.done
    return _reminder_text(task.name), reminder_kb(task.id, target_date, task.name, is_done)


@router.callback_query(F.data.startswith("rem_done:"))
async def reminder_toggle(callback: CallbackQuery, repo: Repository) -> None:
    """Нажатие на кнопку-галочку напоминания: переключить статус задачи в БД (как /today).

    Стандартная механика отметки: нажатие переключает статус задачи за дату
    напоминания (done ↔ pending) и перерисовывает только кнопку (синяя ⬜ ↔ зелёная
    ☑️). Текст сообщения не меняется, кнопка не пропадает. Если задача удалена —
    отметить нельзя, сообщаем об этом alert'ом, кнопку оставляем как есть.
    """
    if callback.message is None:
        await callback.answer()
        return
    # callback-data: "rem_done:{task_id}:{дата}".
    parts = callback.data.split(":")
    task_id = int(parts[1])
    target_date = date.fromisoformat(parts[2])
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["reminder_gone"], show_alert=True)
        return
    user = await repo.get_user(callback.from_user.id)
    log = await repo.get_or_create_log(task_id, user.telegram_id, target_date)
    new_status = (
        TaskStatus.pending if log.status == TaskStatus.done else TaskStatus.done
    )
    await repo.set_log_status(log, new_status)
    is_done = new_status == TaskStatus.done
    logger.info(
        "User {} toggled task {} (now {}) via reminder",
        user.telegram_id, task_id, new_status.name,
    )
    await callback.message.edit_reply_markup(
        reply_markup=reminder_kb(task_id, target_date, task.name, is_done)
    )
    await callback.answer()

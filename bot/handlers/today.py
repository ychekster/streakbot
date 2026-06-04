"""Интерактивные дайджесты и напоминания.

Здесь живёт вся логика утреннего и вечернего дайджестов (сборка текста и
интерактивная отметка задач), а также обработчик кнопки «Выполнена» на
напоминании. Команд `/today` и `/done` больше нет.

Оба дайджеста несут inline-кнопку отметки сегодняшних задач (`tm_*` — общий
для утреннего и вечернего дайджеста): по нажатию текущее сообщение редактируется
в экран выбора, где невыполненные задачи идут без галочки, а уже выполненные — с
галочкой; пользователь может как отмечать, так и снимать отметки. «Готово» →
экран подтверждения → «Подтвердить» фиксирует новый статус всех задач (выбранные
→ выполнено, остальные → не выполнено). Кнопка после подтверждения остаётся; её
текст становится «Отменить выполнение», если все задачи выполнены.

Утренний дайджест дополнительно несёт кнопку отметки вчерашних просроченных
задач (`md_*`) — она располагается ниже кнопки сегодняшних задач.

Защита от «устаревших» кнопок: вход в флоу отметки (`tm_mark`/`md_mark`) остаётся
без фильтра состояния — чтобы кнопка под дайджестом работала и после рестарта, —
но выставляет FSM-состояние `MarkingStates.active`, которое требуется всеми
последующими шагами. Поэтому если между шагами любая команда сбросила FSM
(`state.clear()`), нажатия на старом сообщении просто ничего не делают, а не
приводят к рассинхронизации (например, к подстановке вечернего дайджеста вместо
утреннего) или к показу неактуальных названий. Источник флоу и выбор хранятся
в FSM-данных (`tm_origin`/`tm_selected`/`md_selected` …) и согласованы с
состоянием: пока состояние `active`, эти данные гарантированно на месте.
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

from bot.constants import BTN_SAVE, OVERDUE_PAGE_SIZE, TEXTS
from bot.database.models import Task, TaskLog, TaskStatus, User
from bot.database.repository import Repository
from bot.keyboards.builders import (
    REMOVE_KB,
    check_open_kb,
    evening_digest_kb,
    morning_digest_kb,
    overdue_expired_kb,
    select_confirm_kb,
    task_select_kb,
)
from bot.utils.validators import escape_md

router = Router(name="today")

# Источники флоу отметки сегодняшних задач (origin в callback/FSM): кнопка под
# утренним или вечерним дайджестом — возврат ведёт к соответствующему дайджесту.
_ORIGIN_MORNING = "morning"
_ORIGIN_EVENING = "evening"


class MarkingStates(StatesGroup):
    """Активный флоу интерактивной отметки (today `tm_*` или overdue `md_*`).

    Состояние выставляется при входе (`tm_mark`/`md_mark`, которые остаются без
    фильтра состояния — чтобы кнопка под дайджестом срабатывала и после рестарта,
    когда FSM пуст) и требуется всеми последующими шагами. Это защищает от
    срабатывания «устаревших» кнопок на старом сообщении после того, как любая
    команда сбросила FSM, и гарантирует, что `tm_origin` и выбор ещё на месте.
    """

    active = State()


class CheckStates(StatesGroup):
    """Активен режим отметки задач из /check (галочки + «Сохранить»).

    Как и `MarkingStates`, вход (`check_open`) — без фильтра состояния (кнопка
    «Отметить» живёт на сообщении /check и должна работать и после рестарта), а
    шаги (`check_toggle`/`check_page`/`check_done`) требуют это состояние.
    Состояние отдельное от `MarkingStates`, чтобы флоу /check и флоу дайджестов
    не перехватывали кнопки друг друга. Выбор хранится в FSM (`check_selected`).
    """

    marking = State()


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


def _render_task_lines(
    active: list[tuple[Task, TaskLog]],
    marked: list[tuple[Task, TaskLog]],
) -> list[str]:
    """Строки списка задач: активные, пустая строка, отмеченные (зачёркнуты).

    Нумерация сквозная.
    """
    lines: list[str] = []
    number = 1
    for task, _ in active:
        lines.append(f"{number}\\. {escape_md(task.name)}")
        number += 1
    if marked:
        if active:
            lines.append("")  # пропуск строки между блоками
        for task, _ in marked:
            lines.append(f"~{number}\\. {escape_md(task.name)}~")  # зачёркнуто
            number += 1
    return lines


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


async def _evening_digest_text(repo: Repository, user: User, today) -> str:
    """Текст вечернего итога: выполненные против оставшихся за сегодня.

    Удалённые (is_active=False) задачи в итоге не показываются — даже если за
    сегодня по ним остался лог.
    """
    logs = await repo.get_logs_for_date(user.telegram_id, today)
    if not logs:
        return escape_md(TEXTS["digest_evening_no_tasks"])

    done, remaining = [], []
    for log in logs:
        task = await repo.get_task(log.task_id)
        if task is None or not task.is_active:
            continue
        (done if log.status == TaskStatus.done else remaining).append(task.name)

    if not remaining:
        header = escape_md(TEXTS["digest_evening_header"])
        return f"{header}\n\n{escape_md(TEXTS['digest_evening_all_done'])}"

    parts = [escape_md(TEXTS["digest_evening_header"]), ""]
    if done:
        parts.append(escape_md(TEXTS["digest_evening_done"]))
        parts.extend(f"• {escape_md(name)}" for name in done)
        parts.append("")
    parts.append(escape_md(TEXTS["digest_evening_pending"]))
    parts.extend(f"• {escape_md(name)}" for name in remaining)
    return "\n".join(parts)


async def build_evening_digest(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Вечерний дайджест для планировщика: текст итогов + inline-кнопка отметки.

    Кнопка отметки сегодняшних задач показывается, если на сегодня есть задачи
    (любого статуса); её текст — «Отменить выполнение», если все они выполнены.
    Возвращает (текст, inline-клавиатуру).
    """
    today = datetime.now(_user_tz(user)).date()
    text = await _evening_digest_text(repo, user, today)
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    has_tasks = bool(ordered)
    all_done = has_tasks and len(done_ids) == len(ordered)
    keyboard = evening_digest_kb(has_tasks, all_done)
    return text, keyboard


async def _morning_digest_text(repo: Repository, user: User) -> str:
    """Текст утреннего дайджеста: блок задач на сегодня + блок просроченных."""
    today = datetime.now(_user_tz(user)).date()
    active, marked = await _today_tasks(repo, user, today)
    overdue = await _overdue_tasks(repo, user, today)

    if not active and not marked and not overdue:
        return escape_md(TEXTS["digest_morning_no_tasks"])

    blocks: list[str] = []
    if active or marked:
        lines = [escape_md(TEXTS["digest_morning_header"]), ""]
        lines.extend(_render_task_lines(active, marked))
        blocks.append("\n".join(lines))
    else:
        blocks.append(escape_md(TEXTS["digest_morning_greeting"]))

    if overdue:
        olines = [escape_md(TEXTS["digest_overdue_header"])]
        olines.extend(f"• {escape_md(task.name)}" for task in overdue)
        blocks.append("\n".join(olines))

    return "\n\n".join(blocks)


async def build_morning_digest(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Утренний дайджест для планировщика: текст + inline-кнопки отметки.

    Сначала строится текст (он же создаёт логи сегодняшних задач), затем
    клавиатура: кнопка отметки сегодняшних задач (если задачи на сегодня есть) и
    ниже — кнопка отметки вчерашних просроченных (если просроченные есть).
    Возвращает (текст, inline-клавиатуру).
    """
    today = datetime.now(_user_tz(user)).date()
    text = await _morning_digest_text(repo, user)
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    overdue = await _overdue_tasks(repo, user, today)
    has_today = bool(ordered)
    all_today_done = has_today and len(done_ids) == len(ordered)
    keyboard = morning_digest_kb(has_today, all_today_done, bool(overdue))
    return text, keyboard


async def _digest_text_for(repo: Repository, user: User, origin: str) -> str:
    """Текст дайджеста по источнику флоу отметки (утро/вечер)."""
    if origin == _ORIGIN_MORNING:
        return await _morning_digest_text(repo, user)
    today = datetime.now(_user_tz(user)).date()
    return await _evening_digest_text(repo, user, today)


async def _build_digest(
    repo: Repository,
    user: User,
    origin: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Собрать дайджест (куда вернуться) по источнику флоу отметки."""
    if origin == _ORIGIN_MORNING:
        return await build_morning_digest(repo, user)
    return await build_evening_digest(repo, user)


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
    """Экран выбора: заголовок + нумерованный список просроченных задач."""
    lines = [escape_md(TEXTS["overdue_select_header"]), ""]
    lines.extend(
        f"{i + 1}\\. {escape_md(task.name)}" for i, task in enumerate(overdue)
    )
    return "\n".join(lines)


def _overdue_confirm_text(overdue: list[Task], selected: set[int]) -> str:
    """Экран подтверждения: отмеченные / неотмеченные + предупреждение."""
    marked = [task for task in overdue if task.id in selected]
    unmarked = [task for task in overdue if task.id not in selected]
    parts = [escape_md(TEXTS["overdue_confirm_header"]), ""]
    if marked:
        parts.append(escape_md(TEXTS["overdue_confirm_marked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in marked)
        parts.append("")
    if unmarked:
        parts.append(escape_md(TEXTS["overdue_confirm_unmarked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in unmarked)
        parts.append("")
    if not marked:
        parts.append(escape_md(TEXTS["overdue_confirm_none_marked"]))
        parts.append("")
    parts.append(escape_md(TEXTS["overdue_confirm_warning"]))
    return "\n".join(parts)


async def _morning_final_text(repo: Repository, user: User) -> str:
    """Финальный вид: утренний дайджест без просроченных + строка про /help."""
    today = datetime.now(_user_tz(user)).date()
    active, marked = await _today_tasks(repo, user, today)
    if active or marked:
        lines = [escape_md(TEXTS["digest_morning_header"]), ""]
        lines.extend(_render_task_lines(active, marked))
        block = "\n".join(lines)
    else:
        block = escape_md(TEXTS["digest_morning_greeting"])
    return f"{block}\n\n{escape_md(TEXTS['digest_morning_help'])}"


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
        reply_markup=task_select_kb([(t.id, t.name) for t in overdue], set(), 0, "md"),
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
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in overdue], selected, page, "md"
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
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in overdue], selected, page, "md"
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
        _overdue_confirm_text(overdue, selected), reply_markup=select_confirm_kb("md")
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
        reply_markup=task_select_kb([(t.id, t.name) for t in overdue], selected, page, "md"),
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
    text = await _morning_final_text(repo, user)
    await callback.message.edit_text(text, reply_markup=None)
    await state.clear()
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "md_expired_ok")
async def md_expired_ok(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Подтверждение на экране «время вышло» — показать финальный вид без изменений."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text = await _morning_final_text(repo, user)
    await callback.message.edit_text(text, reply_markup=None)
    await state.clear()
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Интерактивная отметка сегодняшних задач (общий флоу: утро, вечер и /tasks)
#
#  Вход — inline-кнопка (callback `tm_mark:{origin}`, без фильтра состояния;
#  origin — "morning", "evening" или "tasks"). Кнопка редактирует сообщение в
#  экран выбора и выставляет `MarkingStates.active`; дальнейшие шаги (`tm_*`)
#  требуют этого состояния. Экран выбора показывает ВСЕ сегодняшние задачи:
#  невыполненные без галочки, выполненные — с галочкой; галочки можно как
#  ставить, так и снимать. «Готово» → подтверждение → «Подтвердить» фиксирует
#  новый статус всех задач (выбранные → done, остальные → pending) и возвращает
#  к исходному экрану. Источник флоу хранится в FSM (`tm_origin`) — это
#  соответствующий (утренний или вечерний) дайджест.
# --------------------------------------------------------------------------- #

def _resolve_origin(value: str | None) -> str:
    """Нормализовать источник флоу: morning / (по умолчанию) evening."""
    return _ORIGIN_MORNING if value == _ORIGIN_MORNING else _ORIGIN_EVENING


def _today_confirm_text(tasks: list[Task], selected: set[int]) -> str:
    """Экран подтверждения отметки сегодняшних задач (без предупреждения о сгорании).

    Выбранные станут выполненными, остальные — невыполненными.
    """
    marked = [task for task in tasks if task.id in selected]
    unmarked = [task for task in tasks if task.id not in selected]
    parts = [escape_md(TEXTS["evening_confirm_header"]), ""]
    if marked:
        parts.append(escape_md(TEXTS["evening_confirm_marked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in marked)
        parts.append("")
    if unmarked:
        parts.append(escape_md(TEXTS["evening_confirm_unmarked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in unmarked)
        parts.append("")
    if not marked:
        parts.append(escape_md(TEXTS["evening_confirm_none_marked"]))
    return "\n".join(parts).rstrip()


async def _apply_today_marks(
    repo: Repository,
    user: User,
    tasks: list[Task],
    selected: set[int],
    today,
) -> int:
    """Зафиксировать статусы сегодняшних задач: выбранные → done, остальные → pending.

    Статус меняется только при фактическом отличии (чтобы не плодить лишние
    отметки времени). Возвращает число задач, ставших выполненными.
    """
    done_count = 0
    for task in tasks:
        log = await repo.get_or_create_log(task.id, user.telegram_id, today)
        new_status = TaskStatus.done if task.id in selected else TaskStatus.pending
        if log.status != new_status:
            await repo.set_log_status(log, new_status)
        if new_status == TaskStatus.done:
            done_count += 1
    return done_count


async def _show_today_select(
    callback: CallbackQuery,
    repo: Repository,
    user: User,
    origin: str,
    ordered: list[Task],
    selected: set[int],
    page: int,
) -> None:
    """Отрисовать экран выбора сегодняшних задач (текст дайджеста + подсказка)."""
    digest_text = await _digest_text_for(repo, user, origin)
    text = f"{digest_text}\n\n{escape_md(TEXTS['evening_select_prompt'])}"
    await callback.message.edit_text(
        text,
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in ordered], selected, page, "tm"
        ),
    )


@router.callback_query(F.data.startswith("tm_mark"))
async def tm_mark(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Inline-кнопка отметки сегодняшних задач (без фильтра состояния) — открыть экран выбора."""
    if callback.message is None:
        await callback.answer()
        return
    # callback-data: "tm_mark:{origin}".
    parts = callback.data.split(":", 1)
    origin = _resolve_origin(parts[1] if len(parts) > 1 else None)
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    # Текст дайджеста для утра создаёт логи сегодняшних задач — считаем его первым.
    digest_text = await _digest_text_for(repo, user, origin)
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    if not ordered:
        # Задач на сегодня нет — показываем alert и возвращаемся к дайджесту.
        await callback.answer(TEXTS["evening_nothing"], show_alert=True)
        text, keyboard = await _build_digest(repo, user, origin)
        await callback.message.edit_text(text, reply_markup=keyboard)
        return
    # Стартовые галочки = уже выполненные задачи (их можно снять).
    await state.set_state(MarkingStates.active)
    await state.update_data(tm_selected=list(done_ids), tm_page=0, tm_origin=origin)
    text = f"{digest_text}\n\n{escape_md(TEXTS['evening_select_prompt'])}"
    await callback.message.edit_text(
        text,
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in ordered], done_ids, 0, "tm"
        ),
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data.startswith("tm_toggle:"))
async def tm_toggle(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Переключить галочку на сегодняшней задаче (меняем только клавиатуру)."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    valid_ids = {task.id for task in ordered}
    data = await state.get_data()
    selected = set(data.get("tm_selected", [])) & valid_ids
    page = data.get("tm_page", 0)
    if task_id in valid_ids:
        selected.symmetric_difference_update({task_id})
    await state.update_data(tm_selected=list(selected))
    await callback.message.edit_reply_markup(
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in ordered], selected, page, "tm"
        )
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data.startswith("tm_page:"))
async def tm_page(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Пагинация списка сегодняшних задач (alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    total_pages = max(1, (len(ordered) + OVERDUE_PAGE_SIZE - 1) // OVERDUE_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("tm_selected", []))
    await state.update_data(tm_page=page)
    await callback.message.edit_reply_markup(
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in ordered], selected, page, "tm"
        )
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "tm_done")
async def tm_done(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Готово» — показать экран подтверждения нового статуса всех задач."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("tm_selected", [])) & {task.id for task in ordered}
    await callback.message.edit_text(
        _today_confirm_text(ordered, selected), reply_markup=select_confirm_kb("tm")
    )
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "tm_back_digest")
async def tm_back_digest(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Назад» (с выбора) — вернуть исходный экран (того же источника) с кнопками."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    data = await state.get_data()
    origin = _resolve_origin(data.get("tm_origin"))
    text, keyboard = await _build_digest(repo, user, origin)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.clear()
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "tm_back_select")
async def tm_back_select(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Назад» (с подтверждения) — вернуть экран выбора (галочки сохраняются)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("tm_selected", [])) & {task.id for task in ordered}
    page = data.get("tm_page", 0)
    origin = _resolve_origin(data.get("tm_origin"))
    await _show_today_select(callback, repo, user, origin, ordered, selected, page)
    await callback.answer()


@router.callback_query(MarkingStates.active, F.data == "tm_confirm")
async def tm_confirm(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Подтвердить» — зафиксировать статусы и вернуть исходный экран (кнопка остаётся)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("tm_selected", [])) & {task.id for task in ordered}
    origin = _resolve_origin(data.get("tm_origin"))
    done_count = await _apply_today_marks(repo, user, ordered, selected, today)
    logger.info(
        "User {} confirmed today marking via {} ({}/{} done)",
        user.telegram_id, origin, done_count, len(ordered),
    )
    text, keyboard = await _build_digest(repo, user, origin)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.clear()
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Команда /check: список задач на сегодня с отметкой галочками
#
#  Сообщение: жирный заголовок «Задачи на сегодня», блок невыполненных (обычный
#  текст), блок выполненных (зачёркнутый), жирный абзац-подсказка + кнопка
#  «Отметить». «Отметить» меняет только клавиатуру на галочки (+ «Сохранить»
#  вместо «Готово»); смена галочки в реальном времени перемещает задачу между
#  блоками в тексте. «Сохранить» фиксирует статусы в БД и возвращает список
#  (кнопка «Отметить» остаётся). Робастность — как у tm/md: вход без фильтра
#  состояния, шаги под `CheckStates.marking`; выбор — в FSM (`check_selected`,
#  `check_page`).
# --------------------------------------------------------------------------- #

def _check_view_text(ordered: list[Task], selected: set[int], prompt: str) -> str:
    """Текст /check по разбиению `selected`.

    Верхний блок — задачи не из `selected` (обычный текст), нижний — из `selected`
    (зачёркнутый); между непустыми блоками — пустая строка, пустые блоки
    опускаются. Заголовок и подсказка — жирные.
    """
    not_done = [escape_md(t.name) for t in ordered if t.id not in selected]
    done = [f"~{escape_md(t.name)}~" for t in ordered if t.id in selected]
    blocks: list[str] = []
    if not_done:
        blocks.append("\n".join(not_done))
    if done:
        blocks.append("\n".join(done))
    body = "\n\n".join(blocks)
    header = f"*{escape_md(TEXTS['check_header'])}*"
    footer = f"*{escape_md(prompt)}*"
    return f"{header}\n\n{body}\n\n{footer}"


def _check_mark_kb(
    ordered: list[Task], selected: set[int], page: int
) -> InlineKeyboardMarkup:
    """Клавиатура отметки /check: галочки (формат дайджеста) + «Сохранить», без «‹ Назад»."""
    return task_select_kb(
        [(t.id, t.name) for t in ordered],
        selected,
        page,
        "check",
        done_text=BTN_SAVE,
        with_back=False,
    )


async def _check_list_view(
    repo: Repository,
    user: User,
    prompt: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Вид покоя /check: два блока по статусам из БД + подсказка + кнопка «Отметить».

    Если на сегодня задач нет — текст-заглушка и клавиатура None. Сегодняшние логи
    при необходимости создаются (как при сборке дайджеста).
    """
    today = datetime.now(_user_tz(user)).date()
    await _today_tasks(repo, user, today)  # создаём pending-логи сегодняшних задач
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    if not ordered:
        return escape_md(TEXTS["tasks_today_empty"]), None
    return _check_view_text(ordered, done_ids, prompt), check_open_kb()


@router.message(Command("check"))
async def cmd_check(message: Message, state: FSMContext, repo: Repository) -> None:
    """/check — прислать список задач на сегодня с кнопкой «Отметить»."""
    await state.clear()
    user = await repo.get_user(message.from_user.id)
    text, keyboard = await _check_list_view(repo, user, TEXTS["check_prompt"])
    await message.answer(text, reply_markup=keyboard or REMOVE_KB)


@router.callback_query(F.data == "check_open")
async def check_open(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Отметить» — заменить клавиатуру на галочки (текст сообщения не трогаем)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    await _today_tasks(repo, user, today)
    ordered, done_ids = await _today_marking_tasks(repo, user, today)
    if not ordered:
        # Задач на сегодня не осталось — показываем заглушку без кнопки.
        await callback.answer(TEXTS["tasks_today_empty"], show_alert=True)
        await callback.message.edit_text(
            escape_md(TEXTS["tasks_today_empty"]), reply_markup=None
        )
        return
    # Стартовые галочки = уже выполненные задачи.
    await state.set_state(CheckStates.marking)
    await state.update_data(check_selected=list(done_ids), check_page=0)
    await callback.message.edit_reply_markup(reply_markup=_check_mark_kb(ordered, done_ids, 0))
    await callback.answer()


@router.callback_query(CheckStates.marking, F.data.startswith("check_toggle:"))
async def check_toggle(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Смена галочки: задача в реальном времени перемещается между блоками в тексте."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    valid_ids = {task.id for task in ordered}
    if task_id not in valid_ids:
        # Задача исчезла (удалена) — ничего не меняем.
        await callback.answer()
        return
    data = await state.get_data()
    selected = set(data.get("check_selected", [])) & valid_ids
    page = data.get("check_page", 0)
    selected.symmetric_difference_update({task_id})
    await state.update_data(check_selected=list(selected))
    # Обновляем и текст (блоки по выбору), и клавиатуру (галочки).
    await callback.message.edit_text(
        _check_view_text(ordered, selected, TEXTS["check_prompt"]),
        reply_markup=_check_mark_kb(ordered, selected, page),
    )
    await callback.answer()


@router.callback_query(CheckStates.marking, F.data.startswith("check_page:"))
async def check_page(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Пагинация клавиатуры отметки (текст показывает все задачи; alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    total_pages = max(1, (len(ordered) + OVERDUE_PAGE_SIZE - 1) // OVERDUE_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("check_selected", []))
    await state.update_data(check_page=page)
    await callback.message.edit_reply_markup(reply_markup=_check_mark_kb(ordered, selected, page))
    await callback.answer()


@router.callback_query(CheckStates.marking, F.data == "check_done")
async def check_save(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Сохранить» — зафиксировать статусы и вернуть список с кнопкой «Отметить»."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    ordered, _ = await _today_marking_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("check_selected", [])) & {task.id for task in ordered}
    done_count = await _apply_today_marks(repo, user, ordered, selected, today)
    await state.clear()
    logger.info(
        "User {} saved /check marking ({}/{} done)",
        user.telegram_id, done_count, len(ordered),
    )
    text, keyboard = await _check_list_view(repo, user, TEXTS["check_prompt_saved"])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Кнопка «Выполнена» на напоминании о задаче
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("rem_done:"))
async def reminder_done(callback: CallbackQuery, repo: Repository) -> None:
    """Отметить задачу выполненной из напоминания (активно до 12:00 следующего дня)."""
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    task_id = int(parts[1])
    target_date = date.fromisoformat(parts[2])
    user = await repo.get_user(callback.from_user.id)
    tz = _user_tz(user)
    now = datetime.now(tz)
    deadline = tz.localize(datetime.combine(target_date + timedelta(days=1), time(12, 0)))
    if now >= deadline:
        await callback.message.edit_text(escape_md(TEXTS["reminder_expired"]))
        await callback.answer()
        return

    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.message.edit_text(escape_md(TEXTS["reminder_gone"]))
        await callback.answer()
        return

    log = await repo.get_or_create_log(task_id, user.telegram_id, target_date)
    if log.status == TaskStatus.done:
        await callback.message.edit_text(
            escape_md(TEXTS["reminder_already_done"].format(name=task.name))
        )
    else:
        await repo.set_log_status(log, TaskStatus.done)
        await callback.message.edit_text(
            escape_md(TEXTS["reminder_marked_done"].format(name=task.name))
        )
        logger.info("User {} marked task {} done via reminder", user.telegram_id, task_id)
    await callback.answer()

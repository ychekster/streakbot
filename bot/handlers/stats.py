"""/stats — статистика и стрики по всем активным задачам (с пагинацией)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.constants import STATS_PAGE_SIZE, TEXTS
from bot.database.models import FrequencyType, Task
from bot.database.repository import Repository
from bot.keyboards.builders import REMOVE_KB, stats_nav_kb
from bot.services.streak import get_current_streak, get_max_streak
from bot.utils.validators import escape_md

router = Router(name="stats")


async def _stats_tasks(repo: Repository, user_id: int) -> list[Task]:
    """Активные задачи для статистики — без одноразовых (они не участвуют в стриках)."""
    return [
        task
        for task in await repo.get_active_tasks(user_id)
        if task.frequency_type != FrequencyType.one_time
    ]


async def _render_stats(
    repo: Repository,
    tasks: list[Task],
    page: int,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Собрать текст страницы статистики и навигационную клавиатуру."""
    total_pages = max(1, (len(tasks) + STATS_PAGE_SIZE - 1) // STATS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_tasks = tasks[page * STATS_PAGE_SIZE : (page + 1) * STATS_PAGE_SIZE]

    lines = [escape_md(TEXTS["stats_header"]), ""]
    for task in page_tasks:
        current = await get_current_streak(repo, task.id)
        best = await get_max_streak(repo, task.id)
        lines.append(escape_md(task.name))
        lines.append(f"🔥 {escape_md(f'Текущий стрик: {current} дней')}")
        lines.append(f"🏆 {escape_md(f'Лучший стрик: {best} дней')}")
        lines.append("")
    text = "\n".join(lines).rstrip()
    return text, stats_nav_kb(page, total_pages)


@router.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать статистику."""
    await state.clear()
    tasks = await _stats_tasks(repo, message.from_user.id)
    if not tasks:
        await message.answer(escape_md(TEXTS["stats_no_tasks"]), reply_markup=REMOVE_KB)
        return
    text, keyboard = await _render_stats(repo, tasks, 0)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("stats_page:"))
async def stats_paginate(callback: CallbackQuery, repo: Repository) -> None:
    """Перелистнуть страницу статистики."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    tasks = await _stats_tasks(repo, callback.from_user.id)
    if not tasks:
        await callback.message.edit_text(escape_md(TEXTS["stats_no_tasks"]))
        await callback.answer()
        return
    text, keyboard = await _render_stats(repo, tasks, page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

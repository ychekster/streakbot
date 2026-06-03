"""/tasks — единое меню просмотра задач.

Команда присылает сообщение с двумя кнопками: «Все задачи» и «Задачи на сегодня».
Всё дальнейшее взаимодействие идёт в рамках этого одного сообщения (редактирование):

- «Все задачи» — отображение и логика идентичны /stats (переиспользуются
  `stats_tasks` и `render_stats_page`), со стрелочной пагинацией «‹»/«›» и
  кнопкой «‹ Назад» к меню.
- «Задачи на сегодня» — переиспользует общий флоу отметки сегодняшних задач из
  `handlers/today.py` (callback `tm_mark:tasks`, origin="tasks"); его «‹ Назад» и
  подтверждение возвращают к этому меню.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.constants import STATS_PAGE_SIZE, TEXTS
from bot.database.repository import Repository
from bot.handlers.stats import render_stats_page, stats_tasks
from bot.keyboards.builders import tasks_all_kb, tasks_menu_kb
from bot.utils.validators import escape_md

router = Router(name="tasks")


async def _all_tasks_view(
    repo: Repository,
    user_id: int,
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    """Собрать экран «Все задачи»: текст как в /stats + клавиатура /tasks.

    При отсутствии задач — сообщение `stats_no_tasks` с одной кнопкой «‹ Назад».
    """
    tasks = await stats_tasks(repo, user_id)
    if not tasks:
        return escape_md(TEXTS["stats_no_tasks"]), tasks_all_kb(0, 1)
    text, page, total_pages = await render_stats_page(repo, tasks, page)
    return text, tasks_all_kb(page, total_pages)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, state: FSMContext) -> None:
    """Показать меню выбора: «Все задачи» / «Задачи на сегодня»."""
    await state.clear()
    await message.answer(
        escape_md(TEXTS["tasks_menu_prompt"]), reply_markup=tasks_menu_kb()
    )


@router.callback_query(F.data == "tasks_menu")
async def tasks_back_to_menu(callback: CallbackQuery) -> None:
    """«‹ Назад» — вернуть к меню с двумя кнопками (редактируя сообщение)."""
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text(
        escape_md(TEXTS["tasks_menu_prompt"]), reply_markup=tasks_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "tasks_all")
async def tasks_all(callback: CallbackQuery, repo: Repository) -> None:
    """«Все задачи» — показать первую страницу статистики в этом же сообщении."""
    if callback.message is None:
        await callback.answer()
        return
    text, keyboard = await _all_tasks_view(repo, callback.from_user.id, 0)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("tasks_all_page:"))
async def tasks_all_paginate(callback: CallbackQuery, repo: Repository) -> None:
    """Листание страниц раздела «Все задачи» (alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    tasks = await stats_tasks(repo, callback.from_user.id)
    if not tasks:
        await callback.message.edit_text(
            escape_md(TEXTS["stats_no_tasks"]), reply_markup=tasks_all_kb(0, 1)
        )
        await callback.answer()
        return
    # Стрелки «‹»/«›» есть всегда (когда страниц больше одной) — на краях alert.
    total_pages = max(1, (len(tasks) + STATS_PAGE_SIZE - 1) // STATS_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    text, page, total_pages = await render_stats_page(repo, tasks, page)
    await callback.message.edit_text(text, reply_markup=tasks_all_kb(page, total_pages))
    await callback.answer()

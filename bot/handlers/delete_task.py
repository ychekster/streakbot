"""/delete — удаление задачи (мягкое) с пагинацией и подтверждением."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.constants import DELETE_PAGE_SIZE, TEXTS
from bot.database.models import Task
from bot.database.repository import Repository
from bot.keyboards.builders import REMOVE_KB, delete_confirm_kb, delete_list_kb
from bot.services.scheduler import SchedulerService
from bot.utils.validators import escape_md

router = Router(name="delete_task")


class DeleteTaskStates(StatesGroup):
    """Состояния FSM удаления задачи."""

    select = State()   # выбор задачи из списка с пагинацией
    confirm = State()  # подтверждение удаления


def _paginate(tasks: list[Task], page: int) -> tuple[list[tuple[int, str]], int]:
    """Вернуть ((id, name) текущей страницы, всего страниц)."""
    total_pages = max(1, (len(tasks) + DELETE_PAGE_SIZE - 1) // DELETE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = tasks[page * DELETE_PAGE_SIZE : (page + 1) * DELETE_PAGE_SIZE]
    return [(task.id, task.name) for task in chunk], total_pages


@router.message(Command("delete"))
async def cmd_delete(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать список задач для удаления."""
    await state.clear()
    tasks = await repo.get_active_tasks(message.from_user.id)
    if not tasks:
        await message.answer(escape_md(TEXTS["no_tasks_yet"]), reply_markup=REMOVE_KB)
        return
    await state.set_state(DeleteTaskStates.select)
    await state.update_data(page=0)
    page_tasks, total_pages = _paginate(tasks, 0)
    await message.answer(
        escape_md(TEXTS["delete_select"]),
        reply_markup=delete_list_kb(page_tasks, 0, total_pages),
    )


@router.callback_query(DeleteTaskStates.select, F.data.startswith("task_page:"))
async def delete_paginate(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Перелистнуть страницу списка задач."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    tasks = await repo.get_active_tasks(callback.from_user.id)
    if not tasks:
        await callback.message.edit_text(escape_md(TEXTS["no_tasks_yet"]))
        await state.clear()
        await callback.answer()
        return
    page_tasks, total_pages = _paginate(tasks, page)
    await state.update_data(page=page)
    await callback.message.edit_reply_markup(
        reply_markup=delete_list_kb(page_tasks, page, total_pages)
    )
    await callback.answer()


@router.callback_query(DeleteTaskStates.select, F.data.startswith("task_select:"))
async def delete_select(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Выбрать задачу — показать подтверждение удаления."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    await state.set_state(DeleteTaskStates.confirm)
    await callback.message.edit_text(
        escape_md(TEXTS["delete_confirm"].format(name=task.name)),
        reply_markup=delete_confirm_kb(task_id),
    )
    await callback.answer()


@router.callback_query(DeleteTaskStates.confirm, F.data.startswith("task_delete_confirm:"))
async def delete_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Подтвердить удаление: мягко удалить задачу и снять напоминание."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is not None:
        had_reminder = task.reminder_time is not None
        await repo.soft_delete_task(task)
        if had_reminder:
            scheduler.remove_task_reminder_job(task_id)
        logger.info("User {} deleted task {}", callback.from_user.id, task_id)

    # Показать обновлённый список или сообщить, что задач не осталось.
    tasks = await repo.get_active_tasks(callback.from_user.id)
    if not tasks:
        await state.clear()
        await callback.message.edit_text(escape_md(TEXTS["no_tasks_yet"]))
    else:
        await state.set_state(DeleteTaskStates.select)
        await state.update_data(page=0)
        page_tasks, total_pages = _paginate(tasks, 0)
        await callback.message.edit_text(
            escape_md(TEXTS["delete_select"]),
            reply_markup=delete_list_kb(page_tasks, 0, total_pages),
        )
    await callback.answer(TEXTS["delete_done"])


@router.callback_query(DeleteTaskStates.confirm, F.data == "task_delete_cancel")
async def delete_cancel(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Назад — вернуться к списку задач."""
    if callback.message is None:
        await callback.answer()
        return
    tasks = await repo.get_active_tasks(callback.from_user.id)
    if not tasks:
        await state.clear()
        await callback.message.edit_text(escape_md(TEXTS["no_tasks_yet"]))
        await callback.answer()
        return
    data = await state.get_data()
    page = data.get("page", 0)
    page_tasks, total_pages = _paginate(tasks, page)
    await state.set_state(DeleteTaskStates.select)
    await callback.message.edit_text(
        escape_md(TEXTS["delete_select"]),
        reply_markup=delete_list_kb(page_tasks, page, total_pages),
    )
    await callback.answer()

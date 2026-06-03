"""Универсальная команда /cancel.

Регистрируется первым роутером, поэтому перехватывает /cancel при любом
FSM-состоянии. Полностью обнуляет состояние: сбрасывает FSM и убирает
reply-клавиатуру — как будто никакого активного действия не было.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from bot.constants import TEXTS
from bot.keyboards.builders import REMOVE_KB
from bot.utils.validators import escape_md

router = Router(name="cancel")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отменить текущее действие: очистить FSM-состояние и убрать reply-клавиатуру."""
    await state.clear()
    await message.answer(escape_md(TEXTS["action_cancelled"]), reply_markup=REMOVE_KB)
    logger.info("User {} cancelled current action", message.from_user.id)

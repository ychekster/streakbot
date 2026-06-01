"""Универсальная команда /cancel.

Регистрируется первым роутером, поэтому перехватывает /cancel при любом
FSM-состоянии. Сбрасывает состояние и сообщает об отмене. Reply-клавиатуру
намеренно не трогаем — так клавиатура настроек остаётся на месте, как требует
сценарий /settings.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from bot.constants import TEXTS
from bot.utils.validators import escape_md

router = Router(name="cancel")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отменить текущее действие и очистить FSM-состояние."""
    await state.clear()
    await message.answer(escape_md(TEXTS["action_cancelled"]))
    logger.info("User {} cancelled current action", message.from_user.id)

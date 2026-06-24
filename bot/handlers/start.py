"""Команды /start и /help — вход в онбординг и главное меню.

`begin_onboarding` вынесена отдельно, т.к. её переиспользует middleware при
восстановлении потерянного состояния регистрации.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from bot.config import Config
from bot.constants import TEXTS
from bot.database.models import User
from bot.database.repository import Repository
from bot.handlers.onboarding import OnboardingStates
from bot.keyboards.builders import REMOVE_KB, main_menu_kb, start_kb
from bot.utils.validators import escape_md

router = Router(name="start")


async def begin_onboarding(
    message: Message,
    state: FSMContext,
    repo: Repository,
    user: User,
) -> None:
    """Показать приветствие (Шаг 0) и перевести FSM в ожидание старта."""
    await state.set_state(OnboardingStates.waiting_start)
    await message.answer(escape_md(TEXTS["welcome"]), reply_markup=start_kb())


async def _show_main_menu(message: Message, config: Config) -> None:
    """Показать главное меню с кнопкой Mini App, если задан TMA_URL.

    Если URL мини-приложения не настроен — меню как раньше, с `REMOVE_KB`
    (убирает возможную reply-клавиатуру предыдущих шагов).
    """
    await message.answer(
        escape_md(TEXTS["main_menu"]),
        reply_markup=main_menu_kb(config.tma_url) or REMOVE_KB,
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, repo: Repository, config: Config
) -> None:
    """Обработать /start для всех трёх случаев: новый, недозарегистрированный, готовый."""
    tg = message.from_user
    user, created = await repo.get_or_create_user(tg.id, tg.username, tg.first_name)

    if created:
        # Новый пользователь — начинаем онбординг.
        logger.info("New user {} registered (onboarding started)", tg.id)
        await begin_onboarding(message, state, repo, user)
    elif not user.is_registered:
        # Незавершённый онбординг — сбрасываем прогресс и начинаем заново.
        await repo.update_profile(user, tg.username, tg.first_name)
        await repo.reset_onboarding(user)
        await begin_onboarding(message, state, repo, user)
    else:
        # Зарегистрированный пользователь — главное меню.
        await repo.update_profile(user, tg.username, tg.first_name)
        await state.clear()
        await _show_main_menu(message, config)


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext, config: Config) -> None:
    """/help для зарегистрированного пользователя — главное меню.

    Незарегистрированных сюда не пропустит middleware.
    """
    await state.clear()
    await _show_main_menu(message, config)

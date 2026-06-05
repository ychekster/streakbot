"""/stats — статистика и стрики по всем активным задачам в виде альбома картинок.

Команда собирает по каждой активной задаче (без одноразовых — они не участвуют в
стриках) название, текущий и рекордный стрик и сетку последних 30 дней, рендерит
PNG-картинки поверх готового шаблона (`services/stats_image.py`) — по две задачи
на картинку — и отправляет их альбомом.

`stats_tasks` вынесена как переиспользуемая: тот же набор задач (активные, без
одноразовых) показывает раздел «Все задачи» команды /tasks.
"""

from __future__ import annotations

from datetime import date, datetime

import pytz
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message
from loguru import logger

from bot.constants import TEXTS
from bot.database.models import FrequencyType, Task, User
from bot.database.repository import Repository
from bot.keyboards.builders import REMOVE_KB
from bot.services.stats_image import TaskStatsCard, render_stats_album
from bot.services.streak import get_current_streak, get_last_30_days, get_max_streak
from bot.utils.validators import escape_md

router = Router(name="stats")

# Telegram ограничивает медиагруппу 2–10 элементами: при большем числе картинок
# они отправляются несколькими альбомами, а одиночная картинка — отдельным фото.
_ALBUM_MAX = 10


async def stats_tasks(repo: Repository, user_id: int) -> list[Task]:
    """Активные задачи для статистики — без одноразовых (они не участвуют в стриках)."""
    return [
        task
        for task in await repo.get_active_tasks(user_id)
        if task.frequency_type != FrequencyType.one_time
    ]


def _user_today(user: User | None) -> date:
    """Сегодняшняя дата в часовом поясе пользователя (UTC как фолбэк)."""
    try:
        tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.utc
    except Exception:  # noqa: BLE001 — некорректная таймзона не должна ронять команду
        tz = pytz.utc
    return datetime.now(tz).date()


async def _build_cards(
    repo: Repository, tasks: list[Task], today: date
) -> list[TaskStatsCard]:
    """Собрать динамические данные (название, стрики, сетка 30 дней) по каждой задаче."""
    cards: list[TaskStatsCard] = []
    for task in tasks:
        cards.append(
            TaskStatsCard(
                name=task.name,
                current_streak=await get_current_streak(repo, task.id),
                max_streak=await get_max_streak(repo, task.id),
                last_30_days=await get_last_30_days(repo, task.id, today),
            )
        )
    return cards


async def _send_album(message: Message, images: list[bytes]) -> None:
    """Отправить картинки статистики: одиночную — фото, несколько — альбомами по 10.

    Медиагруппа Telegram принимает 2–10 элементов, поэтому картинки бьются на
    альбомы по `_ALBUM_MAX`; «хвост» из одной картинки уходит отдельным фото.
    """
    for start in range(0, len(images), _ALBUM_MAX):
        chunk = images[start : start + _ALBUM_MAX]
        if len(chunk) == 1:
            await message.answer_photo(
                BufferedInputFile(chunk[0], filename=f"stats_{start + 1}.png")
            )
            continue
        media = [
            InputMediaPhoto(
                media=BufferedInputFile(image, filename=f"stats_{start + offset + 1}.png")
            )
            for offset, image in enumerate(chunk)
        ]
        await message.answer_media_group(media=media)


@router.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать статистику альбомом PNG-картинок (по две задачи на картинку)."""
    await state.clear()
    tasks = await stats_tasks(repo, message.from_user.id)
    if not tasks:
        await message.answer(escape_md(TEXTS["stats_no_tasks"]), reply_markup=REMOVE_KB)
        return
    user = await repo.get_user(message.from_user.id)
    cards = await _build_cards(repo, tasks, _user_today(user))
    images = await render_stats_album(cards)
    await _send_album(message, images)
    logger.info(
        "User {} requested /stats: {} task(s), {} image(s)",
        message.from_user.id, len(tasks), len(images),
    )

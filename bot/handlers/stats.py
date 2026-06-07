"""/stats — статистика и стрики по всем активным задачам в виде альбома картинок.

Команда собирает по каждой активной задаче название, текущий и рекордный стрик и
сетку последних 30 дней, рендерит PNG-картинки поверх готового шаблона
(`services/stats_image.py`) — по две задачи на картинку — и отправляет их альбомом.

UX ожидания: пока картинки генерируются И выгружаются, пользователь видит
непрерывный индикатор — сообщение-«загрузку» («Готовлю статистику…») и статус
«отправляет файлы» (`upload_photo`). Статус в Telegram живёт лишь ~5 секунд,
поэтому он шлётся в цикле фоновой задачей (`_uploading_indicator`), пока идёт вся
работа. Сообщение-«загрузка» снимается только сразу ПОСЛЕ появления альбома, чтобы
между его удалением и фотографиями не возникало «пустоты». Выгрузка PNG идёт с
увеличенным таймаутом (`_SEND_TIMEOUT`), а ошибки генерации и отправки
обрабатываются прямо в хендлере — пользователь получает ровно одно нейтральное
сообщение, а альбом отправляется один раз.

`stats_tasks` вынесена как переиспользуемая: тот же набор задач (все активные)
показывает раздел «Все задачи» команды /tasks.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

import pytz
from aiogram import Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message
from loguru import logger

from bot.constants import TEXTS
from bot.database.models import Task, User
from bot.database.repository import Repository
from bot.keyboards.builders import REMOVE_KB
from bot.services.stats_image import TaskStatsCard, render_stats_album
from bot.services.streak import get_current_streak, get_last_30_days, get_max_streak
from bot.utils.validators import escape_md

router = Router(name="stats")

# Telegram ограничивает медиагруппу 2–10 элементами: при большем числе картинок
# они отправляются несколькими альбомами, а одиночная картинка — отдельным фото.
_ALBUM_MAX = 10

# Увеличенный таймаут именно для выгрузки картинок (секунды). PNG-альбом тяжёлый, и
# при дефолтных 60 с медленная сеть давала TelegramNetworkError («Request timeout»):
# отсюда и сообщения «что-то пошло не так», и дубли (после таймаута пользователь
# повторял /stats, а Telegram при этом успевал доставить альбом). Запас по таймауту
# убирает первопричину; обычные текстовые ответы шлются с дефолтным таймаутом.
_SEND_TIMEOUT = 120

# Период повтора статуса «отправляет файлы». Статус upload_photo живёт в Telegram
# около 5 секунд, поэтому шлём его чаще (с запасом), чтобы индикатор не пропадал на
# долгой генерации/выгрузке.
_CHAT_ACTION_INTERVAL = 4.0


async def stats_tasks(repo: Repository, user_id: int) -> list[Task]:
    """Активные задачи для статистики и раздела «Все задачи» (/tasks)."""
    return await repo.get_active_tasks(user_id)


def _user_tz(user: User | None) -> pytz.BaseTzInfo:
    """Часовой пояс пользователя (UTC как фолбэк)."""
    try:
        return pytz.timezone(user.timezone) if user and user.timezone else pytz.utc
    except Exception:  # noqa: BLE001 — некорректная таймзона не должна ронять команду
        return pytz.utc


async def _build_cards(
    repo: Repository, tasks: list[Task], tz: pytz.BaseTzInfo
) -> list[TaskStatsCard]:
    """Собрать динамические данные (название, стрики, сетка 30 дней) по каждой задаче.

    Текущий стрик считается с учётом окна восстановления (по поясу `tz`), сетка —
    относительно сегодняшней даты в этом же поясе.
    """
    today = datetime.now(tz).date()
    cards: list[TaskStatsCard] = []
    for task in tasks:
        cards.append(
            TaskStatsCard(
                name=task.name,
                current_streak=await get_current_streak(repo, task.id, tz),
                max_streak=await get_max_streak(repo, task.id),
                last_30_days=await get_last_30_days(repo, task.id, today),
            )
        )
    return cards


async def _delete_message(message: Message) -> None:
    """Удалить сообщение, проглатывая возможную ошибку (могло быть уже удалено)."""
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001 — удаление «загрузки» не критично
        logger.warning("Could not delete /stats loading message: {}", exc)


async def _keep_uploading(message: Message) -> None:
    """Циклически слать статус «отправляет файлы» (upload_photo), пока не отменят.

    Статус в Telegram живёт ~5 секунд, поэтому повторяем его каждые
    `_CHAT_ACTION_INTERVAL` секунд — так индикатор виден непрерывно всё время
    генерации и выгрузки. Сбой отправки статуса не критичен (логируем и продолжаем);
    корутина завершается по отмене (CancelledError из `asyncio.sleep`).
    """
    while True:
        try:
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        except Exception as exc:  # noqa: BLE001 — индикатор не критичен
            logger.warning("Could not send upload_photo chat action: {}", exc)
        await asyncio.sleep(_CHAT_ACTION_INTERVAL)


@asynccontextmanager
async def _uploading_indicator(message: Message) -> AsyncIterator[None]:
    """Контекст: держит непрерывный статус «отправляет файлы» на время блока.

    Запускает фоновую задачу `_keep_uploading` и гарантированно останавливает её на
    выходе (в т.ч. при ошибке), дожидаясь фактического завершения.
    """
    task = asyncio.create_task(_keep_uploading(message))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _send_album(message: Message, images: list[bytes]) -> None:
    """Отправить картинки статистики: одиночную — фото, несколько — альбомами по 10.

    Медиагруппа Telegram принимает 2–10 элементов, поэтому картинки бьются на
    альбомы по `_ALBUM_MAX`; «хвост» из одной картинки уходит отдельным фото.
    Выгрузка идёт через `bot.send_*` с увеличенным `request_timeout`, чтобы тяжёлый
    PNG-альбом успевал загрузиться и не падал по таймауту.
    """
    bot = message.bot
    chat_id = message.chat.id
    for start in range(0, len(images), _ALBUM_MAX):
        chunk = images[start : start + _ALBUM_MAX]
        if len(chunk) == 1:
            await bot.send_photo(
                chat_id,
                BufferedInputFile(chunk[0], filename=f"stats_{start + 1}.png"),
                request_timeout=_SEND_TIMEOUT,
            )
            continue
        media = [
            InputMediaPhoto(
                media=BufferedInputFile(image, filename=f"stats_{start + offset + 1}.png")
            )
            for offset, image in enumerate(chunk)
        ]
        await bot.send_media_group(chat_id, media, request_timeout=_SEND_TIMEOUT)


@router.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать статистику альбомом PNG-картинок (по две задачи на картинку).

    Порядок: показываем «загрузку» и непрерывный статус «отправляет файлы» → пока
    индикатор активен, генерируем картинки и выгружаем альбом → как только альбом в
    чате, снимаем «загрузку» (пауза между удалением и фото минимальна). Ошибки
    генерации и отправки логируются и показывают одно нейтральное сообщение; альбом
    отправляется один раз.
    """
    await state.clear()
    tasks = await stats_tasks(repo, message.from_user.id)
    if not tasks:
        await message.answer(escape_md(TEXTS["stats_no_tasks"]), reply_markup=REMOVE_KB)
        return
    user = await repo.get_user(message.from_user.id)
    # «Загрузка» (текст) показывается сразу; ниже — непрерывный статус «отправляет
    # файлы» на всё время генерации и выгрузки.
    loading = await message.answer(escape_md(TEXTS["stats_loading"]))
    try:
        async with _uploading_indicator(message):
            cards = await _build_cards(repo, tasks, _user_tz(user))
            images = await render_stats_album(cards)
            await _send_album(message, images)
    except Exception:  # noqa: BLE001 — показываем пользователю одно нейтральное сообщение
        logger.exception(
            "Failed to build/send /stats album for user {}", message.from_user.id
        )
        await _delete_message(loading)
        await message.answer(escape_md(TEXTS["internal_error"]))
        return

    # Альбом уже в чате — снимаем «загрузку» сразу после него (минимальная пауза).
    await _delete_message(loading)
    logger.info(
        "User {} requested /stats: {} task(s), {} image(s)",
        message.from_user.id, len(tasks), len(images),
    )

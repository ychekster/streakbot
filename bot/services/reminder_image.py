"""Генерация PNG-баннера для напоминания о задаче.

Поверх готового шаблона `assets/reminder_template.png` (1254×1254) накладываются
динамические данные ОДНОЙ задачи: название, текущий и рекордный стрик и сетка
последних 30 дней — той же инфраструктурой Pillow, что и `/stats` и утренний баннер.

Чтобы не дублировать логику, переиспользуются помощники `services/stats_image`:
структура данных карточки (`TaskStatsCard`), загрузка шрифта Inter Bold, обрезка
названия по ширине и цвета (название — чёрное, число/выполненный день — #226FE3,
пропуск — #EBF1FA). Отличаются только координаты/размеры под этот шаблон.

Рендеринг Pillow синхронный и CPU-bound, поэтому публичная корутина
`render_reminder_banner` выполняет работу в отдельном потоке (`asyncio.to_thread`).
"""

from __future__ import annotations

import asyncio
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from bot.constants import STATS_GRID_DAYS
from bot.services.stats_image import (
    _COLOR_ACCENT,
    _COLOR_BLACK,
    _COLOR_CELL_EMPTY,
    TaskStatsCard,
    _load_font,
    _truncate_to_width,
)

# Шаблон лежит рядом с остальными (bot/assets), шрифт и цвета — из stats_image.
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_TEMPLATE_PATH = _ASSETS_DIR / "reminder_template.png"

# --------------------------------------------------------------------------- #
#  Координаты и размеры (по спецификации шаблона 1254×1254)
# --------------------------------------------------------------------------- #

# Название задачи: левый верхний угол; при ширине > _NAME_MAX_WIDTH обрезается
# многоточием в ОДНУ строку (без переноса).
_NAME_X = 108
_NAME_Y = 315
_NAME_FONT_SIZE = 90
_NAME_MAX_WIDTH = 940

# Числа стрика: рисуются по центру своих блоков (anchor "mm").
_NUMBER_FONT_SIZE = 130
_CURRENT_CENTER = (64, 486)   # текущий стрик
_RECORD_CENTER = (327, 486)   # рекордный стрик

# Сетка последних 30 дней: 6 колонок × 5 рядов = 30 ячеек 72×72 с зазорами 16 px,
# первый квадрат — в левом верхнем углу блока сетки. Цвета и логика — как в /stats.
_GRID_X = 609
_GRID_Y = 465
_GRID_COLS = 6
_CELL_SIZE = 72
_CELL_GAP = 16
_CELL_RADIUS = 16


@lru_cache(maxsize=1)
def _load_template() -> Image.Image:
    """Загрузить шаблон один раз (кешируется), в RGB. Перед рисованием берётся `.copy()`.

    Перевод в RGB (как в `stats_image`) уменьшает размер итогового PNG; альфа-канал
    шаблона непрозрачен, поэтому вид не меняется.
    """
    return Image.open(_TEMPLATE_PATH).convert("RGB")


def _draw_grid(draw: ImageDraw.ImageDraw, values) -> None:
    """Нарисовать сетку 30 дней: 6 колонок × 5 рядов, слева направо и сверху вниз.

    Логика цветов идентична баннеру статистики: ячейка с index синяя (#226FE3,
    выполнено), если `values[index]` истинно, иначе светлая (#EBF1FA).
    """
    for index in range(STATS_GRID_DAYS):
        row, col = divmod(index, _GRID_COLS)
        x0 = _GRID_X + col * (_CELL_SIZE + _CELL_GAP)
        y0 = _GRID_Y + row * (_CELL_SIZE + _CELL_GAP)
        done = index < len(values) and bool(values[index])
        color = _COLOR_ACCENT if done else _COLOR_CELL_EMPTY
        draw.rounded_rectangle(
            (x0, y0, x0 + _CELL_SIZE, y0 + _CELL_SIZE), radius=_CELL_RADIUS, fill=color
        )


def _render(card: TaskStatsCard) -> bytes:
    """Наложить данные задачи на копию шаблона и вернуть PNG-байты (синхронно)."""
    image = _load_template().copy()
    draw = ImageDraw.Draw(image)
    name_font = _load_font(_NAME_FONT_SIZE)
    number_font = _load_font(_NUMBER_FONT_SIZE)

    # Название: одной строкой, при превышении ширины — многоточие (без переноса).
    name = _truncate_to_width(card.name, name_font, _NAME_MAX_WIDTH)
    draw.text((_NAME_X, _NAME_Y), name, font=name_font, fill=_COLOR_BLACK)

    # Числа стрика: по центру своих блоков (anchor "mm" — центр по обеим осям).
    draw.text(
        _CURRENT_CENTER, str(card.current_streak),
        font=number_font, fill=_COLOR_ACCENT, anchor="mm",
    )
    draw.text(
        _RECORD_CENTER, str(card.max_streak),
        font=number_font, fill=_COLOR_ACCENT, anchor="mm",
    )

    # Сетка последних 30 дней.
    _draw_grid(draw, card.last_30_days)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


async def render_reminder_banner(card: TaskStatsCard) -> bytes:
    """Сгенерировать PNG-баннер напоминания для одной задачи.

    Тяжёлый Pillow-рендеринг выполняется в отдельном потоке, чтобы не блокировать
    event loop (как `render_stats_album` в `services/stats_image.py`).
    """
    return await asyncio.to_thread(_render, card)

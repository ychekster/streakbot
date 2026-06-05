"""Генерация PNG-картинок статистики поверх готового шаблона.

Дизайн (фон, логотип, название StreakBot, подпись «Статистика», белые карточки,
подписи «текущий стрик» / «рекорд» / «последние 30 дней» и разделительные линии)
заранее нарисован в PNG-шаблоне `assets/stats_template.png` (1254×1254). Код НЕ
перерисовывает дизайн: он открывает копию шаблона и накладывает поверх только
динамические данные — название задачи, числа стрика и сетку последних 30 дней.

На одной картинке умещаются ДВЕ задачи (верхний и нижний блок). Если задач
больше двух — генерируется несколько картинок (отправляются альбомом); при
нечётном числе задач нижний блок последней картинки остаётся пустым.

Рендеринг Pillow синхронный и CPU-bound, поэтому публичная корутина
`render_stats_album` выполняет работу в отдельном потоке (`asyncio.to_thread`) —
так же, как геокодинг в `services/geo.py`, чтобы не блокировать event loop.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
#  Пути к ассетам (шаблон и шрифт лежат в bot/assets, рядом с пакетом)
# --------------------------------------------------------------------------- #

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_TEMPLATE_PATH = _ASSETS_DIR / "stats_template.png"
_FONT_PATH = _ASSETS_DIR / "Inter_28pt-Bold.ttf"

# --------------------------------------------------------------------------- #
#  Координаты и стили (по спецификации шаблона 1254×1254)
#
#  Все величины относятся к ПЕРВОЙ задаче (верхний блок). Вторая задача рисуется
#  тем же кодом со сдвигом по вертикали на _BLOCK_OFFSET — координаты второго
#  блока ровно на столько ниже.
# --------------------------------------------------------------------------- #

# На одной картинке — две задачи; вертикальный сдвиг второго блока от первого.
_CARDS_PER_IMAGE = 2
_BLOCK_OFFSET = 495

# Размеры шрифтов.
_NAME_FONT_SIZE = 52
_NUMBER_FONT_SIZE = 110

# Цвета (RGB).
_COLOR_BLACK = (0, 0, 0)            # название задачи
_COLOR_ACCENT = (34, 111, 227)      # #226FE3 — числа и выполненный день
_COLOR_CELL_EMPTY = (235, 241, 250)  # #EBF1FA — пропущенный/невыполненный день

# Название задачи (верхний блок).
_NAME_X = 120
_NAME_Y = 300
# Ширина текстового блока названия ограничена 450 px: текст переносится по словам
# (до двух строк, без уменьшения шрифта); если слово не влезает в строку — уходит
# на следующую, а если не помещается и в две строки — вторая строка обрезается
# многоточием. Координаты x/y, размер и жирность шрифта при этом не меняются.
_NAME_MAX_WIDTH = 450
_NAME_MAX_LINES = 2
# Межстрочный интервал ≈ line-height 1: шаг между строками равен размеру шрифта.
_NAME_LINE_HEIGHT = _NAME_FONT_SIZE

# Числа стрика (верхний блок): текущий стрик и рекорд на одной высоте.
_CURRENT_X = 120
_RECORD_X = 393
_NUMBER_Y = 454

# Сетка последних 30 дней (верхний блок): 6 колонок × 5 рядов = 30 ячеек,
# первая ячейка — ровно в левом верхнем углу блока сетки.
_GRID_X = 644
_GRID_Y = 295
_GRID_COLS = 6
_GRID_ROWS = 5
_GRID_CELLS = _GRID_COLS * _GRID_ROWS  # 30
_CELL_W = 56
_CELL_H = 50
_CELL_GAP_X = 17
_CELL_GAP_Y = 17
_CELL_RADIUS = 12

# Однотонный прямоугольник, закрывающий пустой нижний блок при нечётном числе
# задач (рисуется поверх шаблона на последней картинке, если на ней одна задача).
_COVER_X = 97
_COVER_Y = 770
_COVER_W = 1060
_COVER_H = 400
_COLOR_COVER = (254, 253, 253)  # #FEFDFD


@dataclass(frozen=True)
class TaskStatsCard:
    """Динамические данные одной задачи для отрисовки на картинке статистики.

    `last_30_days` — ровно 30 значений в хронологическом порядке (старое → сегодня);
    True означает выполненный день (синяя ячейка), False — пропущенный (светлая).
    """

    name: str
    current_streak: int
    max_streak: int
    last_30_days: Sequence[bool]


@lru_cache(maxsize=1)
def _load_template() -> Image.Image:
    """Загрузить шаблон один раз (кешируется), в RGB. Перед рисованием берётся `.copy()`.

    Альфа-канал шаблона полностью непрозрачен, поэтому переход в RGB не меняет вид,
    но заметно уменьшает размер итогового PNG — это ускоряет выгрузку альбома и
    снижает риск таймаута при отправке.
    """
    return Image.open(_TEMPLATE_PATH).convert("RGB")


@lru_cache(maxsize=None)
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Загрузить шрифт Inter Bold нужного размера (кешируется по размеру)."""
    return ImageFont.truetype(str(_FONT_PATH), size)


def _truncate_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Обрезать строку многоточием, чтобы её ширина не превышала max_width.

    Если строка и так помещается — возвращается без изменений. Иначе с конца
    отрезаются символы, пока строка вместе с многоточием не уложится в ширину.
    """
    if font.getlength(text) <= max_width:
        return text
    ellipsis = "…"
    truncated = text
    while truncated and font.getlength(truncated + ellipsis) > max_width:
        truncated = truncated[:-1].rstrip()
    return f"{truncated}{ellipsis}" if truncated else ellipsis


def _wrap_words(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Жадно перенести текст по словам так, чтобы каждая строка влезала в max_width.

    Слово, которое само шире строки, не разрывается (займёт строку целиком и при
    необходимости будет обрезано позже). Число строк не ограничено.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else f"{current} {word}"
        if not current or font.getlength(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _fit_name_lines(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int
) -> list[str]:
    """Разбить название максимум на max_lines строк, не уменьшая шрифт.

    Сначала жадный перенос по словам. Если строк получилось больше лимита, весь
    «хвост» сворачивается в последнюю разрешённую строку, и она обрезается
    многоточием. Каждая строка дополнительно подрезается по ширине на случай
    слишком длинного одиночного слова.
    """
    lines = _wrap_words(text, font, max_width)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        lines = head + [tail]
    return [_truncate_to_width(line, font, max_width) for line in lines]


def _draw_grid(draw: ImageDraw.ImageDraw, values: Sequence[bool], y_offset: int) -> None:
    """Нарисовать сетку 30 дней: 6 колонок × 5 рядов, слева направо и сверху вниз.

    Ячейка с index синяя (выполнено), если values[index] истинно, иначе светлая.
    """
    for index in range(_GRID_CELLS):
        row, col = divmod(index, _GRID_COLS)
        x0 = _GRID_X + col * (_CELL_W + _CELL_GAP_X)
        y0 = _GRID_Y + y_offset + row * (_CELL_H + _CELL_GAP_Y)
        done = index < len(values) and bool(values[index])
        color = _COLOR_ACCENT if done else _COLOR_CELL_EMPTY
        draw.rounded_rectangle(
            (x0, y0, x0 + _CELL_W, y0 + _CELL_H), radius=_CELL_RADIUS, fill=color
        )


def _draw_card(draw: ImageDraw.ImageDraw, card: TaskStatsCard, y_offset: int) -> None:
    """Наложить данные одной задачи на шаблон со сдвигом по вертикали y_offset.

    y_offset = 0 для верхнего блока и _BLOCK_OFFSET для нижнего.
    """
    name_font = _load_font(_NAME_FONT_SIZE)
    number_font = _load_font(_NUMBER_FONT_SIZE)

    # Название задачи: до двух строк (перенос по словам, без уменьшения шрифта),
    # вторая строка при нехватке обрезается многоточием. X/Y первой строки
    # неизменны, вторая строка ниже на _NAME_LINE_HEIGHT (≈ line-height 1).
    name_lines = _fit_name_lines(card.name, name_font, _NAME_MAX_WIDTH, _NAME_MAX_LINES)
    for line_index, line in enumerate(name_lines):
        line_y = _NAME_Y + y_offset + line_index * _NAME_LINE_HEIGHT
        draw.text((_NAME_X, line_y), line, font=name_font, fill=_COLOR_BLACK)

    # Числа стрика: текущий и рекорд.
    draw.text(
        (_CURRENT_X, _NUMBER_Y + y_offset),
        str(card.current_streak),
        font=number_font,
        fill=_COLOR_ACCENT,
    )
    draw.text(
        (_RECORD_X, _NUMBER_Y + y_offset),
        str(card.max_streak),
        font=number_font,
        fill=_COLOR_ACCENT,
    )

    # Сетка последних 30 дней.
    _draw_grid(draw, card.last_30_days, y_offset)


def _render_image(cards: Sequence[TaskStatsCard]) -> bytes:
    """Нарисовать одну картинку (до двух задач) поверх копии шаблона; вернуть PNG-байты.

    Если на картинке только одна задача (нечётное число задач), пустой нижний блок
    закрывается однотонным прямоугольником поверх шаблона. PNG сохраняется с
    оптимизацией, чтобы уменьшить размер и ускорить выгрузку альбома.
    """
    image = _load_template().copy()
    draw = ImageDraw.Draw(image)
    for position, card in enumerate(cards[:_CARDS_PER_IMAGE]):
        _draw_card(draw, card, y_offset=_BLOCK_OFFSET * position)
    if len(cards) < _CARDS_PER_IMAGE:
        # Нечётная последняя задача — закрываем пустой нижний блок заглушкой.
        draw.rectangle(
            (_COVER_X, _COVER_Y, _COVER_X + _COVER_W, _COVER_Y + _COVER_H),
            fill=_COLOR_COVER,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _render_all(cards: Sequence[TaskStatsCard]) -> list[bytes]:
    """Сгенерировать список картинок: по две задачи на каждую (синхронно)."""
    return [
        _render_image(cards[start : start + _CARDS_PER_IMAGE])
        for start in range(0, len(cards), _CARDS_PER_IMAGE)
    ]


async def render_stats_album(cards: Sequence[TaskStatsCard]) -> list[bytes]:
    """Сгенерировать альбом PNG-картинок статистики (по две задачи на картинку).

    Возвращает список PNG-байтов (по картинке на каждые две задачи). Тяжёлый
    Pillow-рендеринг выполняется в отдельном потоке, чтобы не блокировать event
    loop (как геокодинг в `services/geo.py`).
    """
    if not cards:
        return []
    return await asyncio.to_thread(_render_all, list(cards))

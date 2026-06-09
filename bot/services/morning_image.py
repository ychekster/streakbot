"""Генерация PNG-баннера стриков для утреннего дайджеста.

Поверх готового шаблона `assets/morning_template.png` (1254×1254) накладывается до
10 ячеек со стриками задач: 🔥 + текущий стрик + название задачи. Дизайн (фон,
заголовок, пустые ячейки) уже нарисован в шаблоне — код добавляет только
динамические данные, как и `services/stats_image.py`.

Сетка ячеек: 2 столбца × 5 рядов, заполняется слева направо и сверху вниз. Если
задач меньше 10 — лишние ячейки остаются пустыми (как в шаблоне); если больше 10 —
берутся первые 10.

Рендеринг Pillow синхронный и CPU-bound, поэтому публичная корутина
`render_morning_banner` выполняет работу в отдельном потоке (`asyncio.to_thread`) —
так же, как генерация картинок в `services/stats_image.py`, чтобы не блокировать
event loop.
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
#  Пути к ассетам (рядом с пакетом, вместе с ассетами /stats)
# --------------------------------------------------------------------------- #

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_TEMPLATE_PATH = _ASSETS_DIR / "morning_template.png"
_FIRE_PATH = _ASSETS_DIR / "fire.png"          # эмодзи 🔥 как переносимый PNG-ассет
_FONT_PATH = _ASSETS_DIR / "Inter_28pt-Bold.ttf"

# --------------------------------------------------------------------------- #
#  Геометрия сетки ячеек (по спецификации шаблона 1254×1254)
# --------------------------------------------------------------------------- #

# 10 ячеек: 2 столбца × 5 рядов.
MORNING_BANNER_MAX_CELLS = 10
_GRID_COLS = 2
_CELL_W = 517
_CELL_H = 202
_CELL_GAP_X = 13   # расстояние между левой и правой ячейкой в ряду
_CELL_GAP_Y = 16   # расстояние между рядами
_GRID_X0 = 102     # x первой ячейки (ряд 1, левая)
_GRID_Y0 = 151     # y первой ячейки

# --------------------------------------------------------------------------- #
#  Контент внутри ячейки
#
#  Блок «эмодзи+число / название» центрирован в ячейке по вертикали и горизонтали.
#  Сверху ряд «эмодзи (100px) + число», ниже (через _ROW_NAME_GAP) — название (40px).
#  Высота блока _CONTENT_H складывается из эмодзи + зазора + названия: эмодзи — самый
#  высокий элемент верхнего ряда, поэтому задаёт его высоту. Эмодзи и число выровнены
#  по нижней линии (низ эмодзи = базовая линия числа), название — строкой ниже, по
#  центру ячейки.
# --------------------------------------------------------------------------- #

_EMOJI_SIZE = 100               # высота эмодзи 🔥
_NUMBER_FONT_SIZE = 120
_NAME_FONT_SIZE = 40
_EMOJI_NUMBER_GAP = 12          # горизонтальный зазор между эмодзи и числом
_ROW_NAME_GAP = 10              # вертикальный зазор между рядом «эмодзи+число» и названием
_COLOR_WHITE = (255, 255, 255)  # цвет числа и названия
# Высота блока контента — для вертикального центрирования в ячейке (эмодзи задаёт
# высоту верхнего ряда, ниже — зазор и строка названия).
_CONTENT_H = _EMOJI_SIZE + _ROW_NAME_GAP + _NAME_FONT_SIZE
# Запас слева/справа в ячейке, чтобы длинное название не упиралось в края.
_NAME_PADDING = 24


@dataclass(frozen=True)
class MorningStreakCell:
    """Данные одной ячейки баннера: название задачи и её текущий стрик."""

    name: str
    current_streak: int


@lru_cache(maxsize=1)
def _load_template() -> Image.Image:
    """Загрузить шаблон один раз (кешируется), в RGB. Перед рисованием берётся `.copy()`.

    Перевод в RGB уменьшает размер итогового PNG (как в `stats_image`); эмодзи с
    альфа-каналом корректно накладывается поверх через маску при вставке.
    """
    return Image.open(_TEMPLATE_PATH).convert("RGB")


@lru_cache(maxsize=1)
def _load_fire() -> Image.Image:
    """Загрузить 🔥 (RGBA) один раз и отмасштабировать по высоте до _EMOJI_SIZE.

    Эмодзи хранится отдельным PNG-ассетом (а не рисуется emoji-шрифтом), поэтому
    результат одинаков на любой платформе и не зависит от системных шрифтов.
    """
    fire = Image.open(_FIRE_PATH).convert("RGBA")
    scale = _EMOJI_SIZE / fire.height
    width = max(1, round(fire.width * scale))
    return fire.resize((width, _EMOJI_SIZE), Image.LANCZOS)


@lru_cache(maxsize=None)
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Загрузить шрифт Inter Bold нужного размера (кешируется по размеру)."""
    return ImageFont.truetype(str(_FONT_PATH), size)


def _truncate_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Обрезать название многоточием, чтобы его ширина не превышала max_width.

    Если строка и так помещается — возвращается без изменений (как в `stats_image`).
    """
    if font.getlength(text) <= max_width:
        return text
    ellipsis = "…"
    truncated = text
    while truncated and font.getlength(truncated + ellipsis) > max_width:
        truncated = truncated[:-1].rstrip()
    return f"{truncated}{ellipsis}" if truncated else ellipsis


def _cell_origin(index: int) -> tuple[int, int]:
    """Левый верхний угол ячейки по её индексу (слева направо, сверху вниз)."""
    row, col = divmod(index, _GRID_COLS)
    x = _GRID_X0 + col * (_CELL_W + _CELL_GAP_X)
    y = _GRID_Y0 + row * (_CELL_H + _CELL_GAP_Y)
    return x, y


def _draw_cell(
    image: Image.Image, draw: ImageDraw.ImageDraw, cell: MorningStreakCell, index: int
) -> None:
    """Нарисовать содержимое одной ячейки: 🔥 + число (текущий стрик) + название.

    Блок контента высотой _CONTENT_H центрируется в ячейке. Эмодзи и число выровнены
    по нижней линии (`base_y`): низ эмодзи и базовая линия числа совпадают. Название —
    строкой ниже, по центру ячейки; слишком длинное обрезается многоточием.
    """
    cell_x, cell_y = _cell_origin(index)
    number_font = _load_font(_NUMBER_FONT_SIZE)
    name_font = _load_font(_NAME_FONT_SIZE)
    fire = _load_fire()

    # Вертикаль: блок _CONTENT_H центрируем в ячейке; эмодзи занимает верхние
    # _EMOJI_SIZE px, значит общая нижняя линия эмодзи и числа — на _EMOJI_SIZE ниже
    # верха блока, а название — ещё на _ROW_NAME_GAP ниже.
    block_top = cell_y + (_CELL_H - _CONTENT_H) // 2
    base_y = block_top + _EMOJI_SIZE

    # Горизонталь верхнего ряда: [эмодзи][зазор][число] центрируем в ячейке.
    number = str(cell.current_streak)
    number_w = round(number_font.getlength(number))
    top_row_w = fire.width + _EMOJI_NUMBER_GAP + number_w
    top_x = cell_x + (_CELL_W - top_row_w) // 2

    # Эмодзи 🔥: низ на base_y; вставляем с учётом альфа-канала (маска — сам эмодзи).
    image.paste(fire, (top_x, base_y - fire.height), fire)
    # Число: сразу за эмодзи, базовая линия (низ) на base_y — выравнивание по низу.
    draw.text(
        (top_x + fire.width + _EMOJI_NUMBER_GAP, base_y),
        number,
        font=number_font,
        fill=_COLOR_WHITE,
        anchor="ls",  # left-baseline
    )
    # Название: ниже верхнего ряда на _ROW_NAME_GAP, по центру ячейки.
    name = _truncate_to_width(cell.name, name_font, _CELL_W - 2 * _NAME_PADDING)
    draw.text(
        (cell_x + _CELL_W // 2, base_y + _ROW_NAME_GAP),
        name,
        font=name_font,
        fill=_COLOR_WHITE,
        anchor="ma",  # middle-ascender (по центру по горизонтали, верх строки)
    )


def _render(cells: Sequence[MorningStreakCell]) -> bytes:
    """Наложить ячейки на копию шаблона и вернуть PNG-байты (синхронно).

    Рисуются только первые `MORNING_BANNER_MAX_CELLS` ячеек; остальные слоты
    остаются пустыми (как в шаблоне). PNG сохраняется с оптимизацией.
    """
    image = _load_template().copy()
    draw = ImageDraw.Draw(image)
    for index, cell in enumerate(cells[:MORNING_BANNER_MAX_CELLS]):
        _draw_cell(image, draw, cell, index)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


async def render_morning_banner(cells: Sequence[MorningStreakCell]) -> bytes:
    """Сгенерировать PNG-баннер стриков (до 10 ячеек).

    Тяжёлый Pillow-рендеринг выполняется в отдельном потоке, чтобы не блокировать
    event loop (как `render_stats_album` в `services/stats_image.py`).
    """
    return await asyncio.to_thread(_render, list(cells))

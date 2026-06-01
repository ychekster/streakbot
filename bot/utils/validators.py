"""Валидация пользовательского ввода и экранирование текста под MarkdownV2.

Здесь сосредоточены все проверки форматов времени, UTC-смещений и диапазонов,
а также утилита `escape_md`, через которую проходит любая динамическая вставка
в сообщения бота.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timezone, timedelta

import pytz

from bot.constants import EVENING_RANGE, MORNING_RANGE, WEEKDAYS

# Карты для форматирования дней недели.
_DAY_SHORT: dict[str, str] = {code: short for code, short, _ in WEEKDAYS}
_DAY_ORDER: tuple[str, ...] = tuple(code for code, _, _ in WEEKDAYS)

# Спецсимволы MarkdownV2, требующие экранирования.
_MD_SPECIAL = set(r"_*[]()~`>#+-=|{}.!")

# Время: "9:00", "09:00", "9.00", "0900", "9-00" и т.п.
_TIME_RE = re.compile(r"^(\d{1,2})\s*[:.\- ]\s*(\d{2})$")
_TIME_DIGITS_RE = re.compile(r"^(\d{3,4})$")

# UTC-смещение: "UTC+3", "GMT-5", "+5:30", "utc +7" и т.п.
_UTC_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::(\d{2}))?$", re.IGNORECASE)

# Полудробные/четвертные пояса, которые нельзя выразить через Etc/GMT.
# Ключ — "знакЧасы:Минуты", значение — реальная IANA-зона с таким смещением.
_FRACTIONAL_TZ: dict[str, str] = {
    "+3:30": "Asia/Tehran",
    "+4:30": "Asia/Kabul",
    "+5:30": "Asia/Kolkata",
    "+5:45": "Asia/Kathmandu",
    "+6:30": "Asia/Yangon",
    "+9:30": "Australia/Darwin",
    "+10:30": "Australia/Adelaide",
    "+12:45": "Pacific/Chatham",
    "-3:30": "America/St_Johns",
    "-9:30": "Pacific/Marquesas",
}


def escape_md(text: object) -> str:
    """Экранировать строку для безопасной отправки с parse_mode=MarkdownV2."""
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in str(text))


def format_days_short(codes: object) -> str:
    """Собрать строку дней недели из кодов: 'ПН, СР, ПТ'.

    Принимает список кодов или строку 'mon,wed,fri'. Порядок — канонический.
    """
    if isinstance(codes, str):
        items = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        items = list(codes)
    chosen = set(items)
    return ", ".join(_DAY_SHORT[code] for code in _DAY_ORDER if code in chosen)


def parse_time(text: str) -> time | None:
    """Распарсить время из распространённых форматов.

    Принимает: '09:00', '9:00', '0900', '9.00', '09.00', '9-00' и аналоги.
    Возвращает datetime.time или None при ошибке.
    """
    raw = text.strip()

    match = _TIME_RE.match(raw)
    if match:
        hours, minutes = int(match.group(1)), int(match.group(2))
    else:
        digits = _TIME_DIGITS_RE.match(raw)
        if not digits:
            return None
        value = digits.group(1)
        if len(value) == 3:  # "900" -> 9:00
            hours, minutes = int(value[0]), int(value[1:])
        else:  # "0900" -> 09:00
            hours, minutes = int(value[:2]), int(value[2:])

    # Полночь можно ввести как 24:00 — нормализуем к 00:00.
    if hours == 24 and minutes == 0:
        hours = 0
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return time(hour=hours, minute=minutes)


def is_morning_time_valid(t: time) -> bool:
    """Проверить диапазон утреннего времени: 04:00 — 12:00 включительно."""
    start, end = MORNING_RANGE
    if t.hour == end:  # ровно 12:00
        return t.minute == 0
    return start <= t.hour < end


def is_evening_time_valid(t: time) -> bool:
    """Проверить диапазон вечернего времени: 16:00 — 00:00 включительно."""
    start = EVENING_RANGE[0]
    if t == time(0, 0):  # полночь — верхняя граница диапазона
        return True
    return start <= t.hour <= 23


def looks_like_utc(text: str) -> bool:
    """Похоже ли это на ввод часового пояса в формате UTC±Число."""
    return bool(_UTC_RE.match(text.strip()))


def parse_utc_offset(text: str) -> str | None:
    """Распарсить UTC-смещение в строку IANA-таймзоны.

    Принимает: 'UTC+3', 'UTC-5', 'UTC+5:30' и аналоги. Проверяет диапазон
    от UTC-12 до UTC+14. Возвращает строку таймзоны ('Etc/GMT-3', 'Asia/Kolkata',
    'UTC') или None, если формат не распознан, смещение вне диапазона либо
    дробное смещение не имеет представимой зоны.
    """
    match = _UTC_RE.match(text.strip())
    if not match:
        return None

    sign, hours_str, minutes_str = match.group(1), match.group(2), match.group(3)
    hours = int(hours_str)
    minutes = int(minutes_str) if minutes_str else 0
    if minutes not in (0, 30, 45):
        return None

    # Знаковое смещение в часах для проверки диапазона.
    signed_hours = hours + minutes / 60
    if sign == "-":
        signed_hours = -signed_hours
    if not (-12 <= signed_hours <= 14):
        return None

    if minutes == 0:
        if hours == 0:
            return "UTC"
        # В именах Etc/GMT знак инвертирован: Etc/GMT-3 == UTC+3.
        etc_sign = "-" if sign == "+" else "+"
        return f"Etc/GMT{etc_sign}{hours}"

    key = f"{sign}{hours}:{minutes:02d}"
    return _FRACTIONAL_TZ.get(key)


def utc_label(tz_string: str) -> str:
    """Вернуть подпись текущего UTC-смещения зоны, например 'UTC+3' или 'UTC+5:30'."""
    try:
        offset = datetime.now(pytz.timezone(tz_string)).utcoffset() or timedelta(0)
    except Exception:  # noqa: BLE001 — неизвестная зона не должна ронять отображение
        return tz_string
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}"


def format_timezone_display(tz_string: str) -> str:
    """Человекочитаемое представление часового пояса для карточки настроек.

    Для зон Etc/GMT и UTC — просто 'UTC±N'. Для именованных зон — 'Город (UTC±N)'.
    """
    label = utc_label(tz_string)
    if tz_string == "UTC" or tz_string.startswith("Etc/"):
        return label
    city = tz_string.split("/")[-1].replace("_", " ")
    return f"{city} ({label})"

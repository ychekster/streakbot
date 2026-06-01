"""Геокодинг: определение часового пояса по названию города.

Используем geopy (Nominatim) для получения координат и timezonefinder для
поиска IANA-таймзоны. Обе библиотеки синхронные и/или CPU-bound, поэтому
вызовы выполняются в отдельном потоке через asyncio.to_thread.

Любая ошибка (недоступность сервиса, таймаут) перехватывается и логируется —
наружу возвращается (None, None), пользователь не видит исключений.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from bot.utils.validators import utc_label

# Ленивая инициализация тяжёлых объектов (создаются один раз при первом вызове).
_geolocator = None
_tz_finder = None


def _get_geolocator():
    """Вернуть singleton-геокодер Nominatim."""
    global _geolocator
    if _geolocator is None:
        from geopy.geocoders import Nominatim

        _geolocator = Nominatim(user_agent="streakbot", timeout=8)
    return _geolocator


def _get_tz_finder():
    """Вернуть singleton TimezoneFinder (загрузка данных — разовая)."""
    global _tz_finder
    if _tz_finder is None:
        from timezonefinder import TimezoneFinder

        _tz_finder = TimezoneFinder()
    return _tz_finder


def _lookup_sync(city: str) -> tuple[str | None, str | None]:
    """Синхронный поиск таймзоны по городу (выполняется в отдельном потоке)."""
    location = _get_geolocator().geocode(city, language="ru")
    if location is None:
        return None, None

    tz_name = _get_tz_finder().timezone_at(
        lat=location.latitude, lng=location.longitude
    )
    if tz_name is None:
        return None, None

    # Короткое имя города: первый компонент адреса от Nominatim.
    address = getattr(location, "address", "") or city
    city_name = address.split(",")[0].strip() or city
    display = f"{city_name} ({utc_label(tz_name)})"
    return tz_name, display


async def find_timezone(city: str) -> tuple[str | None, str | None]:
    """Найти таймзону по названию города.

    Возвращает (timezone_string, display_name) или (None, None), если город
    не найден либо геокодер недоступен.
        timezone_string: например 'Asia/Almaty'
        display_name:    например 'Алматы (UTC+5)'
    """
    query = city.strip()
    if not query:
        return None, None
    try:
        return await asyncio.to_thread(_lookup_sync, query)
    except Exception as exc:  # noqa: BLE001 — внешняя зависимость, ловим всё
        logger.warning("Geocoder error for city '{}': {}", query, exc)
        return None, None

"""Нормализация значений для поиска и оценки.

Модуль не знает про Django и pipeline. Чистые функции:
  - parse_number: "89 990" → 89990.0
  - parse_quantity: "16 ГБ" → (16.0, "GB")
  - parse_date: "15.03.2025" → date(2025, 3, 15)
  - normalize_text: нижний регистр, ё→е, схлопывание пробелов
  - normalize_key: "ОЗУ" → "ram", "SSD" → "storage"

Всё детерминированно, тестируется без модели.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


# --- Разбор чисел -----------------------------------------------------------

_NUMBER_RE = re.compile(r"[\d][\d\s.,]*")


def parse_number(value: Any) -> float | None:
    """Парсит число из строки.

    Примеры:
        "89 990"      → 89990.0
        "1 299,50"    → 1299.5
        "1.299,50"    → 1299.5
        "1,500"       → 1500.0   (тысячи, потому что после запятой 3 цифры)
        "0,5"         → 0.5
        "0,500"       → 0.5      (0 перед запятой — десятичная)
        "1.500"       → 1500.0   (то же правило для точки)
        "1,299.50"    → 1299.5
        "abc"         → None
        ""            → None

    Возвращает float или None, если число не найдено.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value)
    # Заменяем неразрывные пробелы (U+00A0, U+202F) на обычные
    s = s.replace("\u00a0", " ").replace("\u202f", " ")
    s = " ".join(s.split())

    m = _NUMBER_RE.search(s)
    if not m:
        return None

    raw = m.group().strip()
    # Убираем пробелы (они разделяют тысячи)
    t = raw.replace(" ", "")
    # Убираем конечные точки и запятые
    t = t.rstrip(".,")

    if not t:
        return None

    # Определяем разделители
    seps = [i for i, c in enumerate(t) if c in ",."]

    if not seps:
        try:
            return float(t)
        except ValueError:
            return None

    # Если один разделитель и после него 3 цифры —
    # это разделитель тысяч (например, "1,500" = 1500).
    # Исключение: "0,500" и ",500" — это десятичная.
    if len(seps) == 1:
        idx = seps[0]
        head = t[:idx]
        tail = t[idx + 1:]
        if len(tail) == 3 and head not in ("0", ""):
            # Это тысячи
            try:
                return float(head + tail)
            except ValueError:
                return None

    # Иначе — десятичная дробь.
    # Разделитель — последний из встреченных.
    last_sep = seps[-1]
    head = t[:last_sep]
    tail = t[last_sep + 1:]

    # Убираем все разделители из head (они были тысячами)
    head = re.sub(r"[,.]", "", head)

    if not head:
        head = "0"

    try:
        return float(head + "." + tail)
    except ValueError:
        return None


# --- Разбор величин с единицами ---------------------------------------------

MEMORY_UNITS = {
    "гб": ("GB", 1.0),
    "gb": ("GB", 1.0),
    "gbyte": ("GB", 1.0),
    "гигабайт": ("GB", 1.0),
    "тб": ("GB", 1000.0),
    "tb": ("GB", 1000.0),
    "tbyte": ("GB", 1000.0),
    "терабайт": ("GB", 1000.0),
    "мб": ("GB", 0.001),
    "mb": ("GB", 0.001),
    "mbyte": ("GB", 0.001),
    "мегабайт": ("GB", 0.001),
}

MONEY_UNITS = {
    "руб": "RUB",
    "р": "RUB",
    "₽": "RUB",
    "rub": "RUB",
    "ruble": "RUB",
    "рубль": "RUB",
    "рублей": "RUB",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
}


def parse_quantity(
    value: Any,
    kind: str = "number",
) -> tuple[float | None, str]:
    """Парсит число с единицей.

    kind:
        'memory' — ГБ/ТБ/МБ, база = ГБ.
        'money'  — рубли/доллары, возвращает код валюты.
        'number' — просто число, без единицы.

    Возвращает (число, единица).
    """
    if value is None:
        return None, ""

    s = str(value)
    s = s.replace("\u00a0", " ").replace("\u202f", " ")

    number = parse_number(s)
    if number is None:
        return None, ""

    if kind == "number":
        return number, ""

    # Убираем число из строки, чтобы найти единицу
    remainder = _NUMBER_RE.sub(" ", s).lower()
    remainder = remainder.replace(".", "").replace(",", "")
    tokens = re.findall(r"[a-zа-яё$€₽]+", remainder, flags=re.IGNORECASE)

    if not tokens:
        return number, ""

    # Ищем единицу в любом токене
    if kind == "memory":
        for token in tokens:
            if token in MEMORY_UNITS:
                code, multiplier = MEMORY_UNITS[token]
                return number * multiplier, code
        return number, ""

    if kind == "money":
        for token in tokens:
            if token in MONEY_UNITS:
                return number, MONEY_UNITS[token]
        return number, ""

    return number, ""


# --- Разбор дат -------------------------------------------------------------

_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def parse_date(value: Any) -> date | None:
    """Парсит дату.

    Поддерживает:
        "15.03.2025"
        "15/03/2025"
        "2025-03-15"
        "15 марта 2025"
        "15.03.25"
    """
    if value is None:
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    s = str(value).strip().lower()
    if not s:
        return None

    # Формат YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # Формат DD.MM.YYYY или DD/MM/YYYY или DD.MM.YY
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{2,4})$", s)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000 if year < 50 else 1900
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # Формат "15 марта 2025"
    m = re.match(r"^(\d{1,2})\s+([а-яё]+)\s+(\d{4})$", s)
    if m:
        day = int(m.group(1))
        month_name = m.group(2)
        year = int(m.group(3))
        month = _RU_MONTHS.get(month_name)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                return None

    return None


# --- Текст ------------------------------------------------------------------

def normalize_text(value: Any) -> str:
    """Нормализует текст для сравнения.

    - нижний регистр,
    - ё → е,
    - схлопывание пробелов,
    - удаление знаков препинания по краям.
    """
    if value is None:
        return ""
    s = str(value).lower()
    s = s.replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" \t\n.,;:!?\"'()[]{}")
    return s


def normalize_query(value: Any) -> str:
    """Нормализует поисковый запрос — то же, что normalize_text."""
    return normalize_text(value)


# --- Ключи ------------------------------------------------------------------

KEY_SYNONYMS = {
    # RAM
    "оперативная память": "ram",
    "озу": "ram",
    "ram": "ram",
    "память": "ram",
    # Storage
    "накопитель": "storage",
    "жёсткий диск": "storage",
    "жесткий диск": "storage",
    "ssd": "storage",
    "hdd": "storage",
    "диск": "storage",
    # Цена
    "цена": "price",
    "стоимость": "price",
    "price": "price",
    # Производитель
    "производитель": "manufacturer",
    "бренд": "manufacturer",
    "manufacturer": "manufacturer",
    "vendor": "manufacturer",
}


def normalize_key(key: Any) -> str:
    """Приводит ключ характеристики к каноническому виду.

    "ОЗУ" → "ram", "SSD" → "storage", "Наименование" → "наименование".
    """
    if key is None:
        return ""
    s = normalize_text(key)
    if s in KEY_SYNONYMS:
        return KEY_SYNONYMS[s]
    # Если не нашли синоним — возвращаем slug
    slug = re.sub(r"[^a-zа-я0-9]+", "_", s).strip("_")
    return slug
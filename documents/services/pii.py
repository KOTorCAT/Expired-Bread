"""Детекция и маскирование типовых персональных данных (PII).

Проект заявляет работу только с обезличенными документами. Этот модуль —
механизм: находит типовые PII (телефон, email, ИНН, СНИЛС, паспорт) и умеет
заменять их заглушкой, чтобы текст с PII не уходил в модель и не хранился
в поисковом индексе.
"""

from __future__ import annotations

import re

PHONE = re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
INN = re.compile(r"\b\d{10}\b|\b\d{12}\b")
SNILS = re.compile(r"\b\d{3}[\s\-]\d{3}[\s\-]\d{3}[\s\-]\d{2}\b")
PASSPORT = re.compile(r"\b\d{4}[\s\-]?\d{6}\b")

# Порядок не важен для результата (всё заменяется на заглушку),
# но важен для читаемости кода: от специфичного к общему.
PATTERNS = (
    ("phone", PHONE),
    ("email", EMAIL),
    ("snils", SNILS),
    ("passport", PASSPORT),
    ("inn", INN),
)

MASK = "***"


def mask_pii(text: str) -> str:
    """Заменяет найденные PII заглушкой. Без PII возвращает текст как есть."""
    if not text:
        return text
    for _, pattern in PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def contains_pii(text: str) -> bool:
    """True, если в тексте есть хотя бы один типовой PII-паттерн."""
    if not text:
        return False
    return any(pattern.search(text) for _, pattern in PATTERNS)

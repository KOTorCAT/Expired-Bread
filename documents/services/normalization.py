"""Нормализация текста для поиска."""

from __future__ import annotations


def build_search_text(
    title: str = "",
    description: str = "",
    original_filename: str = "",
    extracted_text: str = "",
) -> str:
    """Собирает единый нормализованный текст для поиска."""
    parts = [
        title or "",
        description or "",
        original_filename or "",
        extracted_text or "",
    ]
    return "\n".join(p.lower() for p in parts if p)


def normalize_query(query: str) -> str:
    """Приводит пользовательский запрос к нижнему регистру."""
    return (query or "").lower().strip()
"""Поиск документов по метаданным и содержимому.

Модуль ничего не знает о Django-views и шаблонах — только QuerySet.
"""

from __future__ import annotations

from functools import reduce
from operator import or_
from typing import Any

from django.db.models import Q, QuerySet

from documents.models import Document
from documents.services.normalization import normalize_query


# Белый список допустимых значений.
ALLOWED_STATUSES = {choice.value for choice in Document.Status}
ALLOWED_DOCUMENT_TYPES = {
    "resume",
    "price_list",
    "report",
    "contract",
    "letter",
    "other",
}


def search_documents(
    query: str = "",
    content_terms: list[str] | None = None,
    status: str = "",
    document_type: str = "",
    only_ready: bool = True,
) -> QuerySet[Document]:
    """Ищет документы по нормализованному тексту.

    Все запросы приводятся к нижнему регистру, поиск идёт по полю search_text,
    в котором текст тоже уже в нижнем регистре.

    query — общий поиск.
    content_terms — список слов; документ подходит, если встречается
        хотя бы одно (логика ИЛИ).
    status и document_type — точное совпадение.
    only_ready=True — только подтверждённые документы.
    """
    qs = Document.objects.all()

    if only_ready:
        qs = qs.filter(status=Document.Status.READY)

    if query:
        qs = qs.filter(search_text__contains=normalize_query(query))

    if content_terms:
        term_qs = [
            Q(search_text__contains=normalize_query(term))
            for term in content_terms
            if term
        ]
        if term_qs:
            qs = qs.filter(reduce(or_, term_qs))

    if status in ALLOWED_STATUSES:
        qs = qs.filter(status=status)

    if document_type in ALLOWED_DOCUMENT_TYPES:
        qs = qs.filter(document_type=document_type)

    return qs


def parse_search_query(query: str) -> dict[str, Any]:
    """Заглушка на будущее — здесь позже можно вызывать LLM.

    Сейчас возвращает словарь, который сразу идёт в search_documents.
    """
    return {"query": (query or "").strip()}
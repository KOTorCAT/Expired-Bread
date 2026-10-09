"""Поиск документов по метаданным.

Модуль ничего не знает о Django-views и шаблонах — только QuerySet.
"""

from __future__ import annotations
from typing import Any
from django.db.models import Q, QuerySet
from documents.models import Document
from functools import reduce
from operator import or_

# Белый список допустимых значений.
# Защищает от случайных GET-параметров вроде ?type=что_угодно.
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
    """Ищет документы по текстовому запросу и фильтрам.

    query — общий поиск по title, description, original_filename, extracted_text.
    content_terms — список слов, каждое из которых должно встречаться
        хотя бы в одном из текстовых полей (логика ИЛИ).
    status и document_type — точное совпадение.
    only_ready=True — по умолчанию показываем только подтверждённые.
    """
    qs = Document.objects.all()

    if only_ready:
        qs = qs.filter(status=Document.Status.READY)

    if query:
        qs = qs.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(original_filename__icontains=query)
            | Q(extracted_text__icontains=query)
        )

    if content_terms:
        term_q = Q()
        for term in content_terms:
            if not term:
                continue
            term_q |= (
                Q(title__icontains=term)
                | Q(description__icontains=term)
                | Q(extracted_text__icontains=term)
            )
        if term_q:
            qs = qs.filter(term_q)

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
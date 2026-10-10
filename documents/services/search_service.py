"""Поиск документов по метаданным, содержимому и полям.

Модуль ничего не знает о Django-views и шаблонах — только QuerySet.
Фильтры по полям работают через DocumentField (плоская таблица).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from operator import or_
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet

from documents.models import Document, DocumentField
from documents.services.normalization import normalize_query, parse_quantity
from documents.schemas import all_types


# --- Фильтры по полям --------------------------------------------------------

ALLOWED_OPS = {"eq", "contains", "lt", "lte", "gt", "gte"}


@dataclass(frozen=True)
class FieldFilter:
    """Один фильтр по полю.

    key — канонический ключ ('price', 'ram', 'manufacturer').
    op — оператор из ALLOWED_OPS.
    value — значение. Для money/memory — уже нормализованное число.
    raw_value — как ввёл пользователь (для отладки).
    """
    key: str
    op: str
    value: Any
    raw_value: str = ""


def parse_filters(get_params) -> list[FieldFilter]:
    """Разбирает GET-параметры вида ?f=price:lt:100000&f=ram:gte:16 ГБ.

    Возвращает список FieldFilter. Невалидные фильтры пропускаются.
    """
    raw_filters = get_params.getlist("f")
    result: list[FieldFilter] = []

    for raw in raw_filters:
        raw = (raw or "").strip()
        if not raw:
            continue

        parts = raw.split(":", 2)
        if len(parts) != 3:
            continue

        key, op, value_str = parts
        key = key.strip()
        op = op.strip().lower()
        value_str = value_str.strip()

        if not key or not value_str:
            continue
        if op not in ALLOWED_OPS:
            continue

        # Нормализуем значение
        value: Any = value_str

        # Для операторов сравнения пробуем распарсить как число
        if op in ("lt", "lte", "gt", "gte"):
            num, unit = parse_quantity(value_str, kind="money")
            if num is None:
                num, unit = parse_quantity(value_str, kind="memory")
            if num is None:
                num, unit = parse_quantity(value_str, kind="number")
            if num is not None:
                value = num

        result.append(FieldFilter(
            key=key,
            op=op,
            value=value,
            raw_value=value_str,
        ))

    return result


# --- Поиск ------------------------------------------------------------------

def search_documents(
    query: str = "",
    content_terms: list[str] | None = None,
    status: str = "",
    document_type: str = "",
    filters: list[FieldFilter] | None = None,
    only_ready: bool = True,
) -> QuerySet[Document]:
    """Ищет документы по тексту и фильтрам по полям.

    query — общий поиск по search_text.
    content_terms — список слов (логика ИЛИ).
    status и document_type — точное совпадение.
    filters — список FieldFilter.
    only_ready=True — только ready.
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

    if status in {choice.value for choice in Document.Status}:
        qs = qs.filter(status=status)

    if document_type in all_types():
        qs = qs.filter(document_type=document_type)

    # Фильтры по полям — через Exists, чтобы не дублировать документы
    if filters:
        for f in filters:
            subq = DocumentField.objects.filter(
                document=OuterRef("pk"),
                key=f.key,
            )
            subq = _apply_op(subq, f)
            qs = qs.filter(Exists(subq))

    return qs


def _apply_op(subq: QuerySet, f: FieldFilter) -> QuerySet:
    """Применяет оператор к подзапросу DocumentField."""
    if f.op == "eq":
        if isinstance(f.value, (int, float)):
            return subq.filter(value_num=f.value)
        return subq.filter(value_text=f.value)

    if f.op == "contains":
        return subq.filter(value_text__contains=normalize_query(f.value))

    # lt, lte, gt, gte
    if not isinstance(f.value, (int, float)):
        # Для числовых операторов значение должно быть числом
        return subq.none()

    return subq.filter(**{f"value_num__{f.op}": f.value})


# --- Заглушка на будущее -----------------------------------------------------

def parse_search_query(query: str) -> dict[str, Any]:
    """Заглушка: LLM-разбор вопроса в фильтры. Сейчас не используется."""
    return {"query": (query or "").strip()}
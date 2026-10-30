"""Поиск документов по метаданным, содержимому и полям.

Модуль ничего не знает о Django-views и шаблонах — только QuerySet.
Фильтры по полям работают через DocumentField (плоская таблица).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import reduce
from operator import or_
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet

from documents.models import Document, DocumentField
from documents.services.normalization import (
    kind_for_key,
    normalize_query,
    parse_date,
    parse_quantity,
)

# --- Фильтры по полям --------------------------------------------------------

ALLOWED_OPS = {"eq", "contains", "lt", "lte", "gt", "gte"}


@dataclass(frozen=True)
class FieldFilter:
    """Один фильтр по полю.

    key — канонический ключ ('price', 'ram', 'manufacturer').
    op — оператор из ALLOWED_OPS.
    value — значение. Для money/memory/date — уже нормализованное.
    raw_value — как ввёл пользователь (для отладки).
    """
    key: str
    op: str
    value: Any
    raw_value: str = ""


def _normalize_filter_value(op: str, value_str: str, key: str = "") -> Any:
    """Преобразует строку значения фильтра в нормализованный вид.

    Для операторов сравнения пробует:
      1) дату → ordinal (float)
      2) money → число
      3) memory → число в ГБ
      4) число
    Для 'eq' — только дату (ordinal), иначе оставляет строку.
    """
    if op in ("lt", "lte", "gt", "gte"):
        d = parse_date(value_str)
        if d is not None:
            return float(d.toordinal())

        # Единицу определяет ключ: «1 ТБ» для накопителя — это 1000 ГБ,
        # а для цены единицы нет. Раньше первым пробовались деньги, и
        # любое число проходило как сумма: «1 ТБ» превращалось в «1».
        kind = kind_for_key(key)
        if kind in ("memory", "money", "number"):
            num, _ = parse_quantity(value_str, kind=kind)
            if num is not None:
                return num

        # Ключ неизвестен — пробуем по порядку, начиная с денег
        for fallback in ("money", "memory", "number"):
            num, _ = parse_quantity(value_str, kind=fallback)
            if num is not None:
                return num
        return value_str

    if op == "eq":
        d = parse_date(value_str)
        if d is not None:
            return float(d.toordinal())
        return value_str

    # contains
    return value_str


def parse_filters(get_params) -> list[FieldFilter]:
    """Разбирает GET-параметры ?f=key:op:value.

    Возвращает список FieldFilter. Невалидные фильтры пропускаются.
    """
    raw_filters = get_params.getlist("f")
    result: list[FieldFilter] = []

    for raw in raw_filters:
        f = parse_filter_string(raw)
        if f is not None:
            result.append(f)

    return result


def parse_filter_string(raw: str) -> FieldFilter | None:
    """Парсит одну строку 'key:op:value' в FieldFilter.

    Возвращает None, если строка не похожа на фильтр.
    """
    if not raw:
        return None
    raw = raw.strip()
    if ":" not in raw:
        return None

    parts = raw.split(":", 2)
    if len(parts) != 3:
        return None

    key, op, value_str = parts
    key = key.strip()
    op = op.strip().lower()
    value_str = value_str.strip()

    if not key or not value_str:
        return None
    if op not in ALLOWED_OPS:
        return None
    # Ключ: буквы, цифры, подчёркивания
    if not re.fullmatch(r"[a-zA-Z0-9_]+", key):
        return None

    value = _normalize_filter_value(op, value_str, key)

    return FieldFilter(
        key=key,
        op=op,
        value=value,
        raw_value=value_str,
    )


def parse_filter_expression(raw: str) -> list[FieldFilter]:
    """Парсит строку, в которой может быть несколько фильтров.

    Разделители: '&', ',', ' и ' (регистронезависимо).
    Пример: "price:lt:100000 & ram:gte:16"
    """
    if not raw:
        return []

    parts = re.split(r"\s*(?:&|,|\s+и\s+)\s*", raw, flags=re.IGNORECASE)

    result: list[FieldFilter] = []
    for part in parts:
        f = parse_filter_string(part)
        if f is not None:
            result.append(f)
    return result


def filters_from_rows(params) -> list[FieldFilter]:
    """Разбирает конструктор фильтров: ff=поле, fo=условие, fv=значение.

    Так пользователю не нужно знать синтаксис «ключ:оператор:значение».
    Пустые строки и неизвестные поля просто пропускаются.
    """
    keys = params.getlist("ff")
    ops = params.getlist("fo")
    values = params.getlist("fv")

    result: list[FieldFilter] = []

    for key, op, value in zip(keys, ops, values):
        key = (key or "").strip()
        op = (op or "").strip().lower()
        value = (value or "").strip()

        if not key or not value or op not in ALLOWED_OPS:
            continue
        if not re.fullmatch(r"[a-zA-Z0-9_]+", key):
            continue

        result.append(
            FieldFilter(
                key=key,
                op=op,
                value=_normalize_filter_value(op, value, key),
                raw_value=value,
            )
        )

    return result


# --- Поиск ------------------------------------------------------------------

def search_documents(
    user: Any = None,
    query: str = "",
    content_terms: list[str] | None = None,
    status: str = "",
    document_type: str = "",
    filters: list[FieldFilter] | None = None,
    only_ready: bool = True,
) -> QuerySet[Document]:
    """Ищет документы по тексту и фильтрам по полям."""
    qs = Document.objects.all()
    if user is not None:
        qs = qs.for_user(user)

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

    if document_type:
        qs = qs.filter(document_type=document_type)

    # Фильтры по полям — через Exists, чтобы не дублировать документы
    if filters:
        for f in filters:
            subq = DocumentField.objects.filter(
                document=OuterRef("pk"),
                key__in=KEY_ALIASES.get(f.key, (f.key,)),
            )
            subq = _apply_op(subq, f)
            qs = qs.filter(Exists(subq))

    return qs


# Ключи, которые для пользователя означают одно и то же.
# В прайс-листе поставщик лежит в vendor, в спецификации — в
# manufacturer, но человек ищет одно лицо.
KEY_ALIASES = {
    "vendor": ("vendor", "manufacturer"),
    "manufacturer": ("manufacturer", "vendor"),
}


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
        return subq.none()

    return subq.filter(**{f"value_num__{f.op}": f.value})


# --- Заглушка на будущее -----------------------------------------------------

def parse_search_query(query: str) -> dict[str, Any]:
    """Заглушка: LLM-разбор вопроса в фильтры. Сейчас не используется."""
    return {"query": (query or "").strip()}
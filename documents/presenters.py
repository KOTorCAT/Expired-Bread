"""Представление данных документа для интерфейса.

Пользователю не нужно видеть JSON. Этот модуль превращает словарь
извлечённых данных в подписи, значения и таблицы — и обратно,
из полей формы в словарь.

Здесь нет ни Django, ни шаблонов: только структуры данных, поэтому
всё тестируется отдельно.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

from documents.schemas import get_schema

# Подписи, общие для всех типов.
COMMON_LABELS = {
    "title": "Название",
    "description": "Описание",
    "organization": "Организация",
    "date": "Дата документа",
    "items": "Позиции",
    "characteristics": "Характеристики",
    "sections": "Разделы",
    "requirements": "Требования",
}

# Подписи полей по типам документов.
FIELD_LABELS: dict[str, dict[str, str]] = {
    "price_list": {
        "vendor": "Поставщик",
        "currency": "Валюта",
        "name": "Наименование",
        "sku": "Артикул",
        "price": "Цена",
        "unit": "Единица",
    },
    "receipt": {
        "seller": "Продавец",
        "seller_inn": "ИНН",
        "total": "Итог",
        "payment_method": "Способ оплаты",
        "vat": "НДС",
        "cashier": "Кассир",
        "fiscal_number": "Фискальный номер",
        "name": "Наименование",
        "quantity": "Количество",
        "price": "Цена",
        "total_sum": "Сумма",
    },
    "specification": {
        "product_name": "Изделие",
        "manufacturer": "Производитель",
        "category": "Категория",
        "price": "Цена",
        "ram": "Оперативная память",
        "storage": "Накопитель",
    },
    "technical_document": {
        "document_code": "Обозначение",
        "version": "Версия",
        "title": "Заголовок",
        "content": "Содержание",
    },
    "resume": {
        "candidate_name": "Кандидат",
        "age": "Возраст",
        "city": "Город",
        "email": "Почта",
        "phone": "Телефон",
        "position": "Должность",
        "skills": "Навыки",
        "languages": "Языки",
        "experience": "Опыт работы",
        "education": "Образование",
        "company": "Компания",
        "period": "Период",
        "institution": "Учебное заведение",
        "degree": "Специальность",
    },
}


def label_for(doc_type: str, key: str) -> str:
    """Человеческая подпись поля вместо технического ключа."""
    labels = FIELD_LABELS.get(doc_type, {})
    if key in labels:
        return labels[key]
    if key in COMMON_LABELS:
        return COMMON_LABELS[key]
    return key.replace("_", " ").capitalize()


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# Название и описание правятся полями формы документа, дата — полем
# «Дата документа», поэтому в общем списке они не дублируются.
SKIP = frozenset({"title", "description", "date"})


# --- Просмотр ---------------------------------------------------------------

def build_view(doc_type: str, data: Any) -> dict[str, Any]:
    """Готовит данные для показа: строки «подпись — значение» и таблицы."""
    scalars: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []

    if not isinstance(data, dict):
        return {"scalars": scalars, "tables": tables}

    for key, value in data.items():
        if key in SKIP or _is_empty(value):
            continue

        if isinstance(value, list) and isinstance(value[0], dict):
            tables.append(_table_for_list(doc_type, key, value))
        elif isinstance(value, dict):
            tables.append(_table_for_dict(doc_type, key, value))
        elif isinstance(value, list):
            scalars.append({
                "key": key,
                "label": label_for(doc_type, key),
                "value": ", ".join(str(item) for item in value if not _is_empty(item)),
            })
        else:
            scalars.append({
                "key": key,
                "label": label_for(doc_type, key),
                "value": value,
            })

    return {"scalars": scalars, "tables": tables}


def _columns_from_items(items: list[dict]) -> list[str]:
    columns: list[str] = []
    for item in items:
        for key in item:
            if key not in columns:
                columns.append(key)
    return columns


def _table_for_list(doc_type: str, key: str, items: list[dict]) -> dict[str, Any]:
    columns = _columns_from_items(items)
    return {
        "key": key,
        "label": label_for(doc_type, key),
        "kind": "list",
        "columns": [{"key": col, "label": label_for(doc_type, col)} for col in columns],
        "rows": [[item.get(col, "") for col in columns] for item in items],
    }


def _table_for_dict(doc_type: str, key: str, mapping: dict) -> dict[str, Any]:
    return {
        "key": key,
        "label": label_for(doc_type, key),
        "kind": "dict",
        "columns": [
            {"key": "key", "label": "Характеристика"},
            {"key": "value", "label": "Значение"},
        ],
        "rows": [[name, value] for name, value in mapping.items()],
    }


# --- Редактирование ---------------------------------------------------------

def _model_fields_of_list(annotation: Any) -> list[str] | None:
    """Ключи вложенной модели, если поле — список объектов."""
    if get_origin(annotation) is not list:
        return None
    args = get_args(annotation)
    if not args:
        return None
    inner = args[0]
    fields = getattr(inner, "model_fields", None)
    return list(fields) if fields else None


def _is_dict(annotation: Any) -> bool:
    return get_origin(annotation) is dict


def _is_text_list(annotation: Any) -> bool:
    if get_origin(annotation) is not list:
        return False
    args = get_args(annotation)
    return bool(args) and args[0] is str


def build_editor(doc_type: str, data: Any) -> dict[str, Any]:
    """Строит поля редактора по схеме типа, а не по тому, что пришло.

    Так в форме видны и пустые поля: пользователь может заполнить
    то, что модель не нашла.
    """
    schema = get_schema(doc_type)
    values = data if isinstance(data, dict) else {}

    scalars: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []

    for name, field in schema.model_fields.items():
        if name in SKIP:
            continue
        annotation = field.annotation
        value = values.get(name)

        item_fields = _model_fields_of_list(annotation)
        if item_fields:
            rows = []
            for index, item in enumerate(value or []):
                if not isinstance(item, dict):
                    continue
                rows.append({
                    "index": index,
                    "cells": [
                        {"key": col, "value": item.get(col, "")}
                        for col in item_fields
                    ],
                })
            tables.append({
                "key": name,
                "label": label_for(doc_type, name),
                "kind": "list",
                "columns": [
                    {"key": col, "label": label_for(doc_type, col)}
                    for col in item_fields
                ],
                "rows": rows,
            })
            continue

        if _is_dict(annotation):
            pairs = value if isinstance(value, dict) else {}
            tables.append({
                "key": name,
                "label": label_for(doc_type, name),
                "kind": "dict",
                "columns": [
                    {"key": "key", "label": "Характеристика"},
                    {"key": "value", "label": "Значение"},
                ],
                "rows": [
                    {"index": index, "cells": [
                        {"key": "key", "value": pair_key},
                        {"key": "value", "value": pair_value},
                    ]}
                    for index, (pair_key, pair_value) in enumerate(pairs.items())
                ],
            })
            continue

        if _is_text_list(annotation):
            shown = ", ".join(str(item) for item in (value or []))
            scalars.append({
                "key": name,
                "label": label_for(doc_type, name),
                "value": shown,
                "text_list": True,
                "multiline": False,
            })
            continue

        scalars.append({
            "key": name,
            "label": label_for(doc_type, name),
            "value": "" if value is None else value,
            "text_list": False,
            "multiline": name == "description",
        })

    return {"scalars": scalars, "tables": tables}


def parse_editor(post, doc_type: str) -> dict[str, Any]:
    """Собирает словарь данных из полей формы.

    Никакого JSON: имена полей — `f_<ключ>` для простых значений,
    `<коллекция>__<номер>__<поле>` для строк таблиц.
    """
    schema = get_schema(doc_type)
    result: dict[str, Any] = {}

    for name, field in schema.model_fields.items():
        annotation = field.annotation

        item_fields = _model_fields_of_list(annotation)
        if item_fields:
            result[name] = _parse_rows(post, name, item_fields)
            continue

        if _is_dict(annotation):
            pairs = _parse_rows(post, name, ["key", "value"])
            result[name] = {
                row["key"]: row["value"]
                for row in pairs
                if row.get("key") and row.get("value")
            }
            continue

        raw = (post.get(f"f_{name}") or "").strip()

        if _is_text_list(annotation):
            result[name] = [part.strip() for part in raw.split(",") if part.strip()]
        else:
            result[name] = raw

    return result


# --- Фильтры поиска ---------------------------------------------------------

# Поля, по которым пользователю понятно фильтровать.
FILTER_FIELDS = (
    {"key": "price", "label": "Цена", "kind": "money"},
    {"key": "total", "label": "Сумма", "kind": "money"},
    {"key": "ram", "label": "Оперативная память", "kind": "memory"},
    {"key": "storage", "label": "Накопитель", "kind": "memory"},
    {"key": "name", "label": "Наименование", "kind": "text"},
    {"key": "manufacturer", "label": "Производитель", "kind": "text"},
    {"key": "seller", "label": "Продавец", "kind": "text"},
    {"key": "vendor", "label": "Поставщик", "kind": "text"},
    {"key": "date", "label": "Дата документа", "kind": "date"},
)

# Условия — обычными словами, без операторов.
OPERATORS = (
    {"key": "contains", "label": "содержит"},
    {"key": "eq", "label": "равно"},
    {"key": "lt", "label": "меньше"},
    {"key": "lte", "label": "не больше"},
    {"key": "gt", "label": "больше"},
    {"key": "gte", "label": "не меньше"},
)

DEFAULT_FILTER_ROWS = 3


def describe_filter(field_filter) -> str:
    """«Цена меньше 100000» вместо «price lt 100000»."""
    label = next(
        (item["label"] for item in FILTER_FIELDS if item["key"] == field_filter.key),
        label_for("", field_filter.key),
    )
    op = next(
        (item["label"] for item in OPERATORS if item["key"] == field_filter.op),
        field_filter.op,
    )
    return f"{label} {op} {field_filter.raw_value or field_filter.value}"


def build_filter_rows(filters, minimum: int = DEFAULT_FILTER_ROWS) -> list[dict]:
    """Строки конструктора: выбранные условия плюс пустые до минимума."""
    rows = [
        {"field": item.key, "op": item.op, "value": item.raw_value}
        for item in filters
    ]
    while len(rows) < minimum:
        rows.append({"field": "", "op": "contains", "value": ""})
    return rows


def _parse_rows(post, name: str, columns: list[str]) -> list[dict[str, str]]:
    """Читает строки таблицы, не полагаясь на непрерывность номеров."""
    prefix = f"{name}__"
    indices: set[int] = set()

    for key in post.keys():
        if not key.startswith(prefix):
            continue
        head = key[len(prefix):].split("__", 1)[0]
        if head.isdigit():
            indices.add(int(head))

    rows: list[dict[str, str]] = []
    for index in sorted(indices):
        row = {
            col: (post.get(f"{name}__{index}__{col}") or "").strip()
            for col in columns
        }
        if any(row.values()):
            rows.append(row)

    return rows

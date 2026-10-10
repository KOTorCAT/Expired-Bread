"""Реестр типов документов.

Единый источник правды: формы, поиск, JSON Schema и промпты
берут типы отсюда.
"""

from documents.schemas.base import BaseDocumentData
from documents.schemas.price_list import PriceListData
from documents.schemas.specification import SpecificationData
from documents.schemas.technical_document import TechnicalDocumentData


REGISTRY = {
    "price_list": PriceListData,
    "specification": SpecificationData,
    "technical_document": TechnicalDocumentData,
    "other": BaseDocumentData,
}


# Человекочитаемые названия типов (для форм и шаблонов)
TYPE_LABELS = {
    "price_list": "Прайс-лист",
    "specification": "Спецификация",
    "technical_document": "Техническая документация",
    "other": "Другое",
}


def get_schema(doc_type: str):
    """Возвращает Pydantic-класс схемы для типа документа."""
    return REGISTRY.get(doc_type, BaseDocumentData)


def all_types() -> list[str]:
    """Список всех доступных типов."""
    return list(REGISTRY)


def get_label(doc_type: str) -> str:
    """Человекочитаемое название типа."""
    return TYPE_LABELS.get(doc_type, doc_type)
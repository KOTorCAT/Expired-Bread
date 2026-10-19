"""Реестр типов документов.

Единый источник правды: формы, поиск, JSON Schema и промпты
берут типы отсюда.
"""

from documents.schemas.base import BaseDocumentData
from documents.schemas.price_list import PriceListData
from documents.schemas.receipt import ReceiptData
from documents.schemas.resume import ResumeData
from documents.schemas.specification import SpecificationData
from documents.schemas.technical_document import TechnicalDocumentData


REGISTRY = {
    "price_list": PriceListData,
    "receipt": ReceiptData,
    "specification": SpecificationData,
    "technical_document": TechnicalDocumentData,
    "resume": ResumeData,
    "other": BaseDocumentData,
}


# Человекочитаемые названия типов (для форм и шаблонов)
TYPE_LABELS = {
    "price_list": "Прайс-лист",
    "receipt": "Чек",
    "specification": "Спецификация",
    "technical_document": "Техническая документация",
    "resume": "Резюме",
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
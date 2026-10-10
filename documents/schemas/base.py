"""Базовые поля, общие для всех типов документов."""

from pydantic import BaseModel, Field


class BaseDocumentData(BaseModel):
    """Минимальный набор полей, которые есть у любого документа.

    Для полей, которые почти всегда есть, используется пустая строка
    по умолчанию вместо None. Это заставляет GigaChat заполнять их,
    а не ставить null.
    """

    title: str = Field(description="Краткое название документа")
    description: str = Field(
        default="",
        description="1–2 предложения о содержании документа",
    )
    organization: str = Field(
        default="",
        description="Организация, выпустившая документ",
    )
    date: str = Field(
        default="",
        description="Дата документа как в тексте",
    )
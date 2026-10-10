"""Базовые поля, общие для всех типов документов."""

from pydantic import BaseModel, Field


class BaseDocumentData(BaseModel):
    """Минимальный набор полей, которые есть у любого документа."""

    title: str = Field(description="Краткое название документа")
    description: str = Field(
        default="",
        description="1–2 предложения о содержании документа",
    )
    organization: str | None = Field(
        default=None,
        description="Организация, выпустившая документ",
    )
    date: str | None = Field(
        default=None,
        description="Дата документа как в тексте",
    )
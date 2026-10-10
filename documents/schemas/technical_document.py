"""Схема технической документации."""

from pydantic import BaseModel, Field

from documents.schemas.base import BaseDocumentData


class TechnicalSection(BaseModel):
    """Раздел технической документации."""

    title: str = Field(description="Заголовок раздела")
    content: str = Field(default="", description="Содержимое раздела")


class TechnicalDocumentData(BaseDocumentData):
    """Техническая документация: текст со структурой."""

    document_code: str | None = Field(
        default=None,
        description="Обозначение документа (например, ГОСТ или ТУ)",
    )
    version: str | None = Field(default=None, description="Версия документа")
    sections: list[TechnicalSection] = Field(
        default_factory=list,
        description="Разделы документа",
    )
    requirements: list[str] = Field(
        default_factory=list,
        description="Список требований",
    )
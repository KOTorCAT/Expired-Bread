"""Схема спецификации."""

from pydantic import BaseModel, Field

from documents.schemas.base import BaseDocumentData


class SpecificationData(BaseDocumentData):
    """Спецификация: описание одного изделия с характеристиками."""

    product_name: str = Field(description="Название изделия")
    manufacturer: str | None = Field(default=None, description="Производитель")
    category: str | None = Field(default=None, description="Категория товара")
    price: str | None = Field(
        default=None,
        description="Цена как в документе, с валютой",
    )
    ram: str | None = Field(
        default=None,
        description="Оперативная память (например, '16 ГБ')",
    )
    storage: str | None = Field(
        default=None,
        description="Накопитель (например, '512 ГБ SSD')",
    )
    characteristics: dict[str, str] = Field(
        default_factory=dict,
        description="Остальные характеристики: ключ как в документе",
    )
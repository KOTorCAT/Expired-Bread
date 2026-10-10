"""Схема спецификации."""

from pydantic import BaseModel, Field

from documents.schemas.base import BaseDocumentData


class SpecificationData(BaseDocumentData):
    """Спецификация: описание одного изделия с характеристиками."""

    product_name: str = Field(description="Название изделия")
    price: str = Field(
        description="Цена как в документе, с валютой",
    )
    ram: str = Field(
        description="Оперативная память (например, '16 ГБ')",
    )
    storage: str = Field(
        description="Накопитель (например, '512 ГБ SSD')",
    )
    manufacturer: str = Field(default="", description="Производитель")
    category: str = Field(default="", description="Категория товара")
    characteristics: dict[str, str] = Field(
        default_factory=dict,
        description="Остальные характеристики: ключ как в документе",
    )
    
"""Схема прайс-листа."""

from pydantic import BaseModel, Field

from documents.schemas.base import BaseDocumentData


class PriceItem(BaseModel):
    """Одна позиция прайс-листа."""

    name: str = Field(description="Название товара или услуги")
    price: str = Field(
        description="Цена как в документе, с валютой",
    )
    sku: str = Field(default="", description="Артикул")
    unit: str = Field(
        default="",
        description="Единица измерения (шт, кг, л)",
    )


class PriceListData(BaseDocumentData):
    """Прайс-лист: список позиций с ценами."""

    vendor: str = Field(default="", description="Поставщик")
    currency: str = Field(default="", description="Валюта")
    items: list[PriceItem] = Field(
        default_factory=list,
        description="Список позиций",
    )
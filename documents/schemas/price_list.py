"""Схема прайс-листа."""

from pydantic import BaseModel, Field

from documents.schemas.base import BaseDocumentData


class PriceItem(BaseModel):
    """Одна позиция прайс-листа."""

    name: str = Field(description="Название товара или услуги")
    sku: str | None = Field(default=None, description="Артикул")
    price: str | None = Field(
        default=None,
        description="Цена как в документе, с валютой",
    )
    unit: str | None = Field(
        default=None,
        description="Единица измерения (шт, кг, л)",
    )


class PriceListData(BaseDocumentData):
    """Прайс-лист: список позиций с ценами."""

    vendor: str | None = Field(default=None, description="Поставщик")
    currency: str | None = Field(default=None, description="Валюта")
    items: list[PriceItem] = Field(
        default_factory=list,
        description="Список позиций",
    )
"""Схема кассового чека."""

from pydantic import BaseModel, Field

from documents.schemas.base import BaseDocumentData


class ReceiptItem(BaseModel):
    """Одна позиция чека."""

    name: str = Field(description="Наименование позиции как в чеке")
    quantity: str = Field(default="", description="Количество как в чеке")
    price: str = Field(default="", description="Цена за единицу как в чеке")
    total: str = Field(default="", description="Сумма по позиции как в чеке")


class ReceiptData(BaseDocumentData):
    """Кассовый чек: продавец, итог и позиции."""

    seller: str = Field(default="", description="Продавец или организация")
    seller_inn: str = Field(default="", description="ИНН продавца")
    total: str = Field(default="", description="Итоговая сумма как в чеке")
    payment_method: str = Field(
        default="", description="Способ оплаты (наличные, карта)"
    )
    vat: str = Field(default="", description="НДС как в чеке")
    cashier: str = Field(default="", description="Кассир")
    fiscal_number: str = Field(
        default="", description="Номер фискального документа или ФН"
    )
    items: list[ReceiptItem] = Field(
        default_factory=list, description="Позиции чека"
    )

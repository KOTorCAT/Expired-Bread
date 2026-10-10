"""Индексация полей документа.

Превращает Document.data в плоские строки DocumentField.
Идемпотентно: повторный вызов пересобирает поля с нуля.
"""

from __future__ import annotations

import logging

from django.db import transaction

from documents.models import Document, DocumentField
from documents.services.normalization import build_fields

logger = logging.getLogger(__name__)


@transaction.atomic
def index_document(document: Document) -> int:
    """Пересобирает DocumentField для документа.

    Возвращает количество созданных полей.
    """
    DocumentField.objects.filter(document=document).delete()

    if not document.data:
        return 0

    values = build_fields(document.document_type, document.data)
    if not values:
        return 0

    DocumentField.objects.bulk_create(
        DocumentField(
            document=document,
            key=v.key,
            item_index=v.item_index,
            value_text=v.value_text,
            value_num=v.value_num,
            unit=v.unit,
            raw=v.raw,
        )
        for v in values
    )

    logger.info("Документ %s: проиндексировано %d полей", document.id, len(values))
    return len(values)
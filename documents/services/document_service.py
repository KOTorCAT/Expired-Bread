"""Оркестрация обработки документа: extractors + llm + модель Document.

Это единственное место, где связываются все три слоя.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from documents.models import Document
from documents.services.extractors import ExtractionError, extract_text
from documents.services.llm import (
    LLMError,
    analyze_document,
    analyze_document_vision,
)

logger = logging.getLogger(__name__)


def process_document(document_id: int) -> Document:
    """Полный цикл обработки документа.

    Шаги:
    1. Помечаем документ как processing.
    2. Пытаемся извлечь текст через extractors.
    3. Если текста нет (скан) — идём через Vision.
    4. Отправляем в GigaChat, получаем структурированный JSON.
    5. Сохраняем результат, ставим статус review.
    6. При любой ошибке — статус failed и текст ошибки в error_message.
    """
    document = Document.objects.get(pk=document_id)

    document.status = Document.Status.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "updated_at"])

    try:
        data = _analyze(document)
    except (ExtractionError, LLMError) as exc:
        logger.warning("Обработка документа %s провалилась: %s", document.id, exc)
        document.status = Document.Status.FAILED
        document.error_message = str(exc)
        document.save(update_fields=["status", "error_message", "updated_at"])
        return document
    except Exception as exc:
        logger.exception("Неожиданная ошибка при обработке документа %s", document.id)
        document.status = Document.Status.FAILED
        document.error_message = f"Неожиданная ошибка: {exc}"
        document.save(update_fields=["status", "error_message", "updated_at"])
        return document

    _apply_llm_result(document, data)

    document.status = Document.Status.REVIEW
    document.save(update_fields=[
        "title", "description", "document_type", "data",
        "status", "updated_at",
    ])
    return document


def confirm_document(document_id: int, cleaned_data: dict) -> Document:
    """Сохраняет подтверждённые пользователем данные."""
    document = Document.objects.get(pk=document_id)

    document.title = cleaned_data.get("title", document.title)
    document.document_type = cleaned_data.get("document_type", document.document_type)
    document.description = cleaned_data.get("description", document.description)
    document.status = Document.Status.READY
    document.save(update_fields=[
        "title", "document_type", "description", "status", "updated_at",
    ])
    return document


# --- Внутренние функции -----------------------------------------------------

def _analyze(document: Document) -> dict:
    """Пытается получить данные через текст, при неудаче — через Vision."""
    file_path = document.file.path

    try:
        text = extract_text(file_path)
        logger.info("Документ %s: извлечено %d символов текста", document.id, len(text))
        # Сохраняем текст в модель — пригодится для поиска по содержимому
        document.extracted_text = text
        document.save(update_fields=["extracted_text", "updated_at"])
        return analyze_document(text)
    except ExtractionError as exc:
        logger.info(
            "Документ %s: текст извлечь не удалось (%s), пробуем Vision",
            document.id, exc,
        )
        return analyze_document_vision(file_path)


def _apply_llm_result(document: Document, data: dict) -> None:
    """Раскладывает ответ LLM по полям модели.

    Обязательные поля идут в title / document_type / description,
    всё остальное — в data.
    """
    document.title = data.get("title", "")[:255]
    document.document_type = data.get("document_type", "")[:64]
    document.description = data.get("description", "")

    # В data кладём всё, что вернула модель, включая дополнительные поля.
    # Обязательные дублируем — они пригодятся при повторной обработке.
    document.data = data
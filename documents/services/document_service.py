"""Оркестрация обработки документа.

Связывает:
  - extractors (извлечение текста),
  - pipeline (классификация, извлечение, валидация),
  - модель Document (сохранение результата).

Тонкий слой: вся логика — в pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from documents.models import Document
from documents.services.extractors import ExtractionError, extract_text
from documents.services.llm import LLMError
from documents.services.normalization import build_search_text
from documents.services.pipeline import (
    PipelineConfig,
    PipelineResult,
    run_pipeline,
)

logger = logging.getLogger(__name__)


def process_document(
    document_id: int,
    config: PipelineConfig | None = None,
) -> Document:
    """Полный цикл обработки документа.

    Шаги:
    1. Помечаем документ как processing.
    2. Извлекаем текст через extractors.
    3. Запускаем pipeline (classify → extract → validate).
    4. Сохраняем data, data_raw, processing_meta, search_text.
    5. Статус review. При ошибке — failed с текстом.
    """
    document = Document.objects.get(pk=document_id)

    document.status = Document.Status.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "updated_at"])

    try:
        result = _run_for_document(document, config)
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

    _apply_pipeline_result(document, result)

    document.status = Document.Status.REVIEW
    document.save(update_fields=[
        "title", "description", "document_type",
        "data", "data_raw", "processing_meta",
        "search_text", "status", "updated_at",
    ])
    return document


def confirm_document(document_id: int, cleaned_data: dict) -> Document:
    """Сохраняет подтверждённые пользователем данные.

    Пересчитывает search_text, потому что пользователь мог поправить
    title, description или тип.
    """
    document = Document.objects.get(pk=document_id)

    document.title = cleaned_data.get("title", document.title)
    document.document_type = cleaned_data.get("document_type", document.document_type)
    document.description = cleaned_data.get("description", document.description)
    document.status = Document.Status.READY

    document.search_text = build_search_text(
        title=document.title,
        description=document.description,
        original_filename=document.original_filename,
        extracted_text=document.extracted_text,
    )
    document.save(update_fields=[
        "title", "document_type", "description", "status",
        "search_text", "updated_at",
    ])
    return document


# --- Внутренние функции -----------------------------------------------------

def _run_for_document(
    document: Document,
    config: PipelineConfig | None,
) -> PipelineResult:
    """Извлекает текст и запускает pipeline.

    Если текста нет — pipeline идёт через Vision (если use_vision=True).
    """
    config = config or PipelineConfig()

    file_path = document.file.path
    text = ""

    try:
        text = extract_text(file_path)
        logger.info("Документ %s: извлечено %d символов", document.id, len(text))
        document.extracted_text = text
        document.save(update_fields=["extracted_text", "updated_at"])
    except ExtractionError as exc:
        logger.info(
            "Документ %s: текст не извлечён (%s), пробуем Vision",
            document.id, exc,
        )
        # Не падаем — pipeline пойдёт через Vision, если use_vision=True
        if not config.use_vision:
            raise

    # Кэш в eval/cache, если включён
    if config.use_cache and not config.cache_dir:
        config.cache_dir = str(Path(settings.BASE_DIR) / "eval" / "cache")

    return run_pipeline(text=text, file_path=file_path, config=config)


def _apply_pipeline_result(
    document: Document,
    result: PipelineResult,
) -> None:
    """Раскладывает результат pipeline по полям модели."""
    data = result.data

    document.title = (data.get("title") or "")[:255]
    document.document_type = (result.doc_type or "")[:64]
    document.description = data.get("description") or ""

    document.data = data
    document.data_raw = result.data_raw
    document.processing_meta = result.meta

    document.search_text = build_search_text(
        title=document.title,
        description=document.description,
        original_filename=document.original_filename,
        extracted_text=document.extracted_text,
    )
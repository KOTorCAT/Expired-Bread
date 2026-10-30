"""Оркестрация обработки документа.

Связывает:
  - extractors (извлечение текста),
  - pipeline (классификация, извлечение, валидация),
  - field_index (индексация полей),
  - модель Document (сохранение результата).

Тонкий слой: вся логика — в pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from documents.models import Document
from documents.services.extractors import ExtractionError, extract_text
from documents.services.field_index import index_document
from documents.services.llm import LLMError
from documents.services.normalization import build_search_text, parse_date
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
    4. Сохраняем data, data_raw, processing_meta, search_text, document_date.
    5. Индексируем поля в DocumentField.
    6. Статус review. При ошибке — failed с текстом.
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
        "search_text", "document_date", "status", "updated_at",
    ])

    # Индексация полей в DocumentField
    try:
        index_document(document)
    except Exception as exc:
        logger.warning("Не удалось проиндексировать поля: %s", exc)

    return document


def confirm_document(
    document_id: int,
    cleaned_data: dict,
    user=None,
) -> Document:
    """Сохраняет подтверждённые пользователем данные.

    cleaned_data может содержать:
      - простые поля: title, document_type, description, document_date;
      - полностью отредактированный словарь данных в ключе 'data'
        (его уже проверила форма по схеме типа).

    Пересчитывает search_text и переиндексирует DocumentField,
    потому что пользователь мог поправить данные, тип или дату.
    Дополнительно считает, сколько полей пришлось исправить вручную,
    и складывает список в processing_meta['corrected_fields'].
    """
    document = _fetch_document(document_id, user=user)

    if "data" in cleaned_data and cleaned_data["data"] is not None:
        document.data = cleaned_data["data"]

    document.title = (cleaned_data.get("title", document.title) or "")[:255]
    document.document_type = (
        cleaned_data.get("document_type", document.document_type) or ""
    )[:64]
    document.description = cleaned_data.get("description", document.description) or ""
    document.status = Document.Status.READY
    document.reviewed_at = timezone.now()

    # Дата документа: если пользователь задал — берём его значение,
    # иначе оставляем то, что извлёк AI
    if "document_date" in cleaned_data:
        document.document_date = cleaned_data["document_date"]

    document.search_text = build_search_text(
        title=document.title,
        description=document.description,
        original_filename=document.original_filename,
        extracted_text=document.extracted_text,
    )

    # Метрика исправлений: сколько полей пользователь поправил вручную
    meta = dict(document.processing_meta or {})
    corrected = _diff_paths(document.data_raw, document.data)
    if corrected:
        meta["corrected_fields"] = corrected
    else:
        meta.pop("corrected_fields", None)
    document.processing_meta = meta

    document.save(update_fields=[
        "title", "document_type", "description",
        "document_date", "status", "data", "processing_meta",
        "reviewed_at", "search_text", "updated_at",
    ])

    # Переиндексация после правок пользователя
    try:
        index_document(document)
    except Exception as exc:
        logger.warning("Не удалось переиндексировать поля: %s", exc)

    return document


def delete_document(document: Document) -> None:
    """Удаляет документ вместе с файлом на диске.

    DocumentField удаляются каскадом. Ошибка удаления файла не мешает
    удалить запись: иначе документ остался бы висеть в списке.
    """
    try:
        if document.file:
            document.file.delete(save=False)
    except Exception as exc:  # noqa: BLE001 — файл мог быть уже удалён
        logger.warning("Не удалось удалить файл документа %s: %s", document.id, exc)

    document.delete()


# --- Внутренние функции -----------------------------------------------------

def _fetch_document(document_id: int, user=None) -> Document:
    """Достаёт документ, не давая выйти за пределы своих (этап 8)."""
    queryset = Document.objects.all()
    if user is not None:
        queryset = queryset.for_user(user)
    return queryset.get(pk=document_id)


def _diff_paths(raw: Any, data: Any) -> list[str]:
    """Список путей, которые отличаются между сырым ответом и правкой."""
    if not isinstance(raw, dict) or not isinstance(data, dict):
        return []

    def walk(node: Any, prefix: str = "") -> dict[str, Any]:
        result: dict[str, Any] = {}
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{prefix}.{key}" if prefix else str(key)
                result.update(walk(value, child))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                result.update(walk(value, f"{prefix}[{index}]"))
        else:
            result[prefix] = node
        return result

    left = walk(raw)
    right = walk(data)

    return [
        path
        for path in sorted(set(left) | set(right))
        if left.get(path) != right.get(path)
    ]


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
    # Не затираем описание пустотой, если модель его не вернула.
    description = (data.get("description") or "").strip()
    if description:
        document.description = description

    document.data = data
    document.data_raw = result.data_raw
    document.processing_meta = result.meta

    # Дата документа — из data["date"] через parse_date.
    # Если дата не распознана, оставляем None.
    date_value = data.get("date")
    if date_value:
        parsed = parse_date(date_value)
        document.document_date = parsed

    document.search_text = build_search_text(
        title=document.title,
        description=document.description,
        original_filename=document.original_filename,
        extracted_text=document.extracted_text,
    )

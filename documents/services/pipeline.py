"""Конвейер обработки документа.

Единственная точка, где связываются:
  - llm.json_chat (транспорт),
  - prompts (версии промптов),
  - schemas (Pydantic-модели типов),
  - normalization (нормализация значений — заглушка на этом этапе).

Не знает ни про Django-модели, ни про HTTP.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from documents.schemas import all_types, get_schema
from documents.services.llm import (
    LLMError,
    clean_keys,
    delete_file,
    get_default_model,
    json_chat,
    upload_file,
)
from documents.services.prompts import (
    get_classify_prompt,
    get_extract_prompt,
    get_repair_prompt,
)

logger = logging.getLogger(__name__)


# --- Схема для классификации ------------------------------------------------

CLASSIFY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "description": (
                "Тип документа: price_list, specification, "
                "technical_document или other"
            ),
        },
    },
    "required": ["document_type"],
    "additionalProperties": False,
}


# --- Конфигурация конвейера -------------------------------------------------

@dataclass
class PipelineConfig:
    """Параметры запуска конвейера."""

    model: str = ""                       # пусто → GIGACHAT_MODEL из окружения
    classify_model: str = ""              # пусто → та же модель (Lite — опционально)
    prompt_version: str = "v1"
    doc_type_hint: str = ""               # тип из формы; если задан, классификации нет
    classify_chars: int = 1500            # сколько символов видит классификатор
    max_chars: int = 12000                # порог обрезки длинного текста
    use_cache: bool = False               # кэш ответов модели
    cache_dir: str = ""                   # путь к папке кэша (если use_cache)
    use_vision: bool = True               # использовать Vision, если текста нет


# --- Результат конвейера ----------------------------------------------------

@dataclass
class PipelineResult:
    """Результат работы конвейера."""

    doc_type: str
    data: dict[str, Any]                # проверенные данные по схеме типа
    data_raw: dict[str, Any]            # сырой ответ модели до очистки
    fields: list = field(default_factory=list)  # нормализованные поля (этап 3–4)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


# --- Утилита: slim_schema ---------------------------------------------------

def slim_schema(node: Any) -> Any:
    """Убирает из JSON Schema служебные `title` (метаинформация Pydantic).

    Модели `title` в описаниях полей не нужны — они тратят токены.
    Настоящее поле `title` (в `properties`) остаётся, потому что там
    значение — объект, а не строка.
    """
    if isinstance(node, dict):
        return {
            k: slim_schema(v)
            for k, v in node.items()
            if not (k == "title" and isinstance(v, str))
        }
    if isinstance(node, list):
        return [slim_schema(v) for v in node]
    return node


# --- Кэш --------------------------------------------------------------------

def _cache_key(
    model: str,
    prompt_version: str,
    schema_version: str,
    text: str,
) -> str:
    payload = f"{model}|{prompt_version}|{schema_version}|{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(cache_dir: Path, key: str) -> dict[str, Any] | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_set(cache_dir: Path, key: str, value: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    try:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Не удалось записать в кэш: %s", exc)


# --- Основные шаги конвейера ------------------------------------------------

def classify(text: str, config: PipelineConfig) -> tuple[str, dict[str, Any]]:
    """Определяет тип документа.

    Если в config задан doc_type_hint — классификация не вызывается.
    Возвращает (doc_type, usage).
    """
    if config.doc_type_hint and config.doc_type_hint in all_types():
        return config.doc_type_hint, {"from_hint": True}

    sample = text[:config.classify_chars]
    model = config.classify_model or config.model or get_default_model()

    parsed, usage = json_chat(
        system=get_classify_prompt(config.prompt_version),
        user=sample,
        json_schema=CLASSIFY_JSON_SCHEMA,
        model=model,
        max_tokens=20,
    )

    doc_type = parsed.get("document_type", "other")
    if doc_type not in all_types():
        logger.warning("Модель вернула неизвестный тип: %s → other", doc_type)
        doc_type = "other"

    return doc_type, usage


def extract(
    text: str,
    schema: type[BaseModel],
    config: PipelineConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Извлекает данные по схеме. Возвращает (raw_dict, usage)."""
    json_schema = slim_schema(schema.model_json_schema())

    parsed, usage = json_chat(
        system=get_extract_prompt(config.prompt_version),
        user=text,
        json_schema=json_schema,
        model=config.model,
    )

    return parsed, usage


def validate_with_repair(
    raw: dict[str, Any],
    schema: type[BaseModel],
    text: str,
    config: PipelineConfig,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Валидирует raw по схеме. При ошибке — один повтор.

    Возвращает (data, warnings, usage_from_repair).
    """
    warnings: list[str] = []

    error_text = ""

    try:
        cleaned = clean_keys(raw)
        model = schema.model_validate(cleaned)
        return model.model_dump(), warnings, {}
    except ValidationError as exc:
        error_text = str(exc)
        warnings.append(f"validation_error: {exc.error_count()} полей")
        logger.info("Валидация не прошла, пробуем ремонт: %s", exc)

    # Один повтор с текстом ошибки
    repair_user = (
        f"Текст документа:\n{text}\n\n"
        f"Предыдущий ответ:\n{json.dumps(raw, ensure_ascii=False)}\n\n"
        f"Верни корректный JSON по схеме."
    )

    json_schema = slim_schema(schema.model_json_schema())

    try:
        repaired, usage = json_chat(
            system=get_repair_prompt(error_text, config.prompt_version),
            user=repair_user,
            json_schema=json_schema,
            model=config.model,
        )
        cleaned = clean_keys(repaired)
        model = schema.model_validate(cleaned)
        warnings.append("repaired")
        return model.model_dump(), warnings, usage
    except (LLMError, ValidationError) as exc2:
        raise LLMError(f"Валидация не прошла после ремонта: {exc2}") from exc2


def run_pipeline(
    text: str,
    file_path: str | None = None,
    config: PipelineConfig | None = None,
) -> PipelineResult:
    """Полный цикл: классификация → извлечение → валидация.

    Если текста нет или он короче 30 символов, и есть file_path —
    используется Vision-путь (если config.use_vision=True).
    """
    config = config or PipelineConfig()

    if not text or len(text.strip()) < 30:
        if file_path and config.use_vision:
            return _run_vision_pipeline(file_path, config)
        raise LLMError("Текст слишком короткий, а Vision отключён")

    # Обрезка длинного текста (простая версия; разбиение — позже)
    if len(text) > config.max_chars:
        text = text[: config.max_chars]
        logger.info("Текст обрезан до %d символов", config.max_chars)

    # Шаг 1: классификация
    doc_type, classify_usage = classify(text, config)

    # Шаг 2: извлечение по схеме
    schema = get_schema(doc_type)

    # Кэш по хэшу (текст + схема + версия промпта)
    cache_dir: Path | None = None
    cache_key: str | None = None
    if config.use_cache and config.cache_dir:
        cache_dir = Path(config.cache_dir)
        cache_key = _cache_key(
            model=config.model or get_default_model(),
            prompt_version=config.prompt_version,
            schema_version=doc_type,
            text=text,
        )
        cached = _cache_get(cache_dir, cache_key)
        if cached:
            logger.info("Использован кэш для %s", cache_key[:12])
            return PipelineResult(
                doc_type=cached.get("doc_type", doc_type),
                data=cached.get("data", {}),
                data_raw=cached.get("data_raw", {}),
                warnings=cached.get("warnings", ["from_cache"]),
                meta={"from_cache": True},
            )

    data_raw, extract_usage = extract(text, schema, config)

    # Шаг 3: валидация с ремонтом
    data, warnings, repair_usage = validate_with_repair(
        data_raw, schema, text, config,
    )

    # Собираем meta
    meta = {
        "model": config.model or get_default_model(),
        "classify_model": config.classify_model or config.model or get_default_model(),
        "prompt_version": config.prompt_version,
        "doc_type_hint": config.doc_type_hint,
        "usage": {
            "classify": classify_usage,
            "extract": extract_usage,
            "repair": repair_usage,
        },
        "warnings": warnings,
    }

    # Кэшируем результат
    if cache_dir is not None and cache_key is not None:
        _cache_set(cache_dir, cache_key, {
            "doc_type": doc_type,
            "data": data,
            "data_raw": data_raw,
            "warnings": warnings,
            "meta": meta,
        })

    return PipelineResult(
        doc_type=doc_type,
        data=data,
        data_raw=data_raw,
        warnings=warnings,
        meta=meta,
    )


def _run_vision_pipeline(
    file_path: str,
    config: PipelineConfig,
) -> PipelineResult:
    """Альтернативный путь через Vision, если текст не извлёкся."""
    logger.info("Vision-путь для файла: %s", file_path)

    file_id = upload_file(file_path)

    try:
        # В Vision-режиме классификацию пропускаем: без текста модель
        # сама плохо разберётся в типе. Берём doc_type_hint или other.
        doc_type = config.doc_type_hint or "other"
        if doc_type not in all_types():
            doc_type = "other"

        schema = get_schema(doc_type)
        json_schema = slim_schema(schema.model_json_schema())

        parsed, usage = json_chat(
            system=get_extract_prompt(config.prompt_version),
            user="Проанализируй приложенный документ и извлеки данные по схеме.",
            json_schema=json_schema,
            model=config.model,
            attachments=[file_id],
        )

        data, warnings, repair_usage = validate_with_repair(
            parsed, schema, "", config,
        )

        return PipelineResult(
            doc_type=doc_type,
            data=data,
            data_raw=parsed,
            warnings=warnings + ["vision"],
            meta={
                "model": config.model or get_default_model(),
                "prompt_version": config.prompt_version,
                "usage": {"extract": usage, "repair": repair_usage},
                "vision": True,
            },
        )
    finally:
        delete_file(file_id)
"""Загрузка датасета и прогон конвейера для оценки качества.

Модуль работает без БД и без веб-слоя: берёт документы из eval/dataset,
извлекает текст, запускает run_pipeline и сравнивает результат с эталоном.

Разделение dev/test берётся из поля `_meta.split` файла эталона: его
пишет генератор датасета, его же читает оценка. Единственное
определение разбиения — в documents/evaluation/splits.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from documents.evaluation.matching import compare_maps, flatten
from documents.evaluation.metrics import Metrics
from documents.evaluation.splits import SPLITS, resolve_split
from documents.services.extractors import ExtractionError, extract_text
from documents.services.pipeline import PipelineConfig, run_pipeline

logger = logging.getLogger(__name__)

# Части датасета. Само разбиение задаётся в файле эталона, см. splits.py.
SUPPORTED_SPLITS = ("all",) + SPLITS

# Грубая оценка токенов на символ текста (для --dry-run).
CHARS_PER_TOKEN = 4

# Накладные токены на один запрос (системный промпт и схема).
PROMPT_OVERHEAD_TOKENS = 600

# Расширения исходных документов в порядке предпочтения.
SOURCE_SUFFIXES = (".docx", ".pdf")

TRUTH_SUFFIX = ".truth.json"


@dataclass(frozen=True)
class DatasetItem:
    """Один документ датасета: исходник и эталон."""

    doc_id: str
    doc_type: str
    source_path: Path
    truth: dict[str, Any]
    split: str = ""


# --- Загрузка ---------------------------------------------------------------

def _find_source(truth_path: Path) -> Path | None:
    """Ищет исходный файл рядом с *.truth.json."""
    stem = truth_path.name[: -len(TRUTH_SUFFIX)]
    for suffix in SOURCE_SUFFIXES:
        candidate = truth_path.with_name(stem + suffix)
        if candidate.exists():
            return candidate
    return None


def _read_payload(truth_path: Path) -> dict[str, Any] | None:
    """Читает файл эталона целиком, вместе с `_meta`."""
    try:
        payload = json.loads(truth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Эталон %s не прочитан: %s", truth_path, exc)
        return None

    return payload if isinstance(payload, dict) else None


def _truth_data(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Допускает две формы файла: {"data": {...}} и просто {...}."""
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else None


def load_dataset(
    dataset_dir: str | Path,
    split: str = "all",
    doc_types: Sequence[str] | None = None,
) -> list[DatasetItem]:
    """Собирает датасет из каталога вида eval/dataset/<тип>/<имя>.truth.json."""
    if split not in SUPPORTED_SPLITS:
        raise ValueError(
            f"Неизвестный split: {split}. Допустимые: {', '.join(SUPPORTED_SPLITS)}"
        )

    root = Path(dataset_dir)
    if not root.exists():
        raise FileNotFoundError(f"Каталог датасета не найден: {root}")

    allowed = set(doc_types) if doc_types else None
    items: list[DatasetItem] = []

    for truth_path in sorted(root.glob(f"*/*{TRUTH_SUFFIX}")):
        doc_type = truth_path.parent.name
        if allowed is not None and doc_type not in allowed:
            continue

        stem = truth_path.name[: -len(TRUTH_SUFFIX)]
        doc_id = f"{doc_type}/{stem}"

        source = _find_source(truth_path)
        if source is None:
            logger.warning("Для %s нет ни .docx, ни .pdf — пропускаю", truth_path)
            continue

        payload = _read_payload(truth_path)
        if payload is None:
            logger.warning("Эталон %s пуст или невалиден — пропускаю", truth_path)
            continue

        truth = _truth_data(payload)
        if truth is None:
            logger.warning("В эталоне %s нет данных — пропускаю", truth_path)
            continue

        declared = resolve_split(payload)

        # Документ без _meta.split не попадает ни в dev, ни в test:
        # угадывать его часть — ровно та ошибка, которую мы убрали.
        if split != "all" and declared != split:
            if declared is None:
                logger.warning(
                    "В %s нет _meta.split — в выборку %s не попадает",
                    truth_path,
                    split,
                )
            continue

        items.append(
            DatasetItem(
                doc_id=doc_id,
                doc_type=doc_type,
                source_path=source,
                truth=truth,
                split=declared or "",
            )
        )

    return items


def sample_items(
    items: Sequence[DatasetItem],
    size: int,
    *,
    stratify_by_type: bool = True,
) -> list[DatasetItem]:
    """Стратифицированная выборка: поровну по типам документов."""
    if size <= 0 or size >= len(items):
        return list(items)

    if not stratify_by_type:
        return list(items[:size])

    by_type: dict[str, list[DatasetItem]] = {}
    for item in items:
        by_type.setdefault(item.doc_type, []).append(item)

    types = sorted(by_type)
    per_type = max(1, size // max(1, len(types)))

    picked: list[DatasetItem] = []
    for doc_type in types:
        picked.extend(by_type[doc_type][:per_type])

    return picked[:size] if len(picked) > size else picked


# --- Прогон -----------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Грубая оценка токенов для --dry-run."""
    return len(text) // CHARS_PER_TOKEN + PROMPT_OVERHEAD_TOKENS


def run_evaluation(
    items: Sequence[DatasetItem],
    config: PipelineConfig | None = None,
    *,
    repeat: int = 1,
    dry_run: bool = False,
    max_tokens: int = 0,
    use_truth_type: bool = False,
    extractor: Callable[[str | Path], str] | None = None,
    pipeline_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Прогоняет конвейер по датасету и возвращает сводку метрик.

    Параметры:
        repeat — сколько раз обработать каждый документ (модель
                 недетерминирована, нужен разброс).
        dry_run — только оценить расход токенов, модель не вызывать.
        max_tokens — предохранитель: остановиться при превышении бюджета.
        use_truth_type — подставлять тип из эталона вместо классификации
                 (сравнение «тип из формы» против режима «Авто»).
    """
    extract = extractor or extract_text
    run = pipeline_fn or run_pipeline
    base_config = config or PipelineConfig()

    metrics = Metrics()
    estimated = 0
    stopped_early = False
    processed = 0

    for item in items:
        if max_tokens and estimated >= max_tokens:
            stopped_early = True
            logger.warning(
                "Достигнут бюджет %d токенов, прогон остановлен", max_tokens
            )
            break

        try:
            text = extract(item.source_path)
        except ExtractionError as exc:
            metrics.add_failure(item.doc_type, exc)
            continue
        except Exception as exc:  # файл битый — не роняем весь прогон
            metrics.add_failure(item.doc_type, exc)
            continue

        if dry_run:
            estimated += estimate_tokens(text) * max(1, repeat)
            processed += 1
            continue

        item_config = (
            replace(base_config, doc_type_hint=item.doc_type)
            if use_truth_type
            else base_config
        )

        for _ in range(max(1, repeat)):
            started = time.perf_counter()
            try:
                result = run(text=text, config=item_config)
            except Exception as exc:
                metrics.add_failure(item.doc_type, exc)
                continue

            elapsed = time.perf_counter() - started

            # Обе стороны разворачиваем по виду полей эталонного типа:
            # так сравнение не зависит от того, угадала ли модель тип.
            expected = flatten(item.doc_type, item.truth)
            actual = flatten(item.doc_type, result.data)
            matches = compare_maps(expected, actual)

            metrics.add_document(
                item.doc_type,
                matches,
                expected_type=item.doc_type,
                actual_type=result.doc_type,
                elapsed=elapsed,
                usage=result.meta.get("usage"),
            )

            prompt, completion = _tokens(result.meta.get("usage"))
            estimated += prompt + completion if prompt or completion else estimate_tokens(text)

        processed += 1

    return {
        "summary": metrics.to_dict(),
        "dry_run": dry_run,
        "items": len(items),
        "processed": processed,
        "repeat": repeat,
        "estimated_tokens": estimated,
        "stopped_early": stopped_early,
        "use_truth_type": use_truth_type,
        "config": _config_to_dict(base_config),
    }


def _tokens(usage: Any) -> tuple[int, int]:
    """Достаёт prompt/completion токены из usage одного документа."""
    from documents.evaluation.metrics import _sum_usage

    return _sum_usage(usage)


def _config_to_dict(config: PipelineConfig) -> dict[str, Any]:
    return {
        "model": config.model or "(из окружения)",
        "classify_model": config.classify_model or "(та же)",
        "prompt_version": config.prompt_version,
        "doc_type_hint": config.doc_type_hint or "(авто)",
        "max_chars": config.max_chars,
        "use_cache": config.use_cache,
        "use_vision": config.use_vision,
    }

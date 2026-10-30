"""Загрузка датасета и прогон конвейера для оценки качества.

Модуль работает без БД и без веб-слоя: берёт документы из eval/dataset,
извлекает текст, запускает run_pipeline и сравнивает результат с эталоном.

Разделение dev/test берётся из поля `_meta.split` файла эталона: его
пишет генератор датасета, его же читает оценка. Единственное
определение разбиения — в documents/evaluation/splits.py.

У одного эталона может быть два исходника: DOCX и PDF. Формат выбирается
параметром `formats`; метрики по форматам считаются отдельно, потому что
PDF проходит через другой экстрактор и качество там другое.

Прогон можно прервать и продолжить: результат каждого документа пишется
в progress.jsonl сразу, а `resume=True` перечитывает файл и пропускает
уже обработанное. Без этого сбой на пятом часу обнулял бы всю ночь.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from documents.evaluation.matching import FieldMatch, compare_maps, flatten
from documents.evaluation.metrics import Metrics
from documents.evaluation.splits import SPLITS, resolve_split
from documents.services.extractors import ExtractionError, extract_text
from documents.services.pipeline import PipelineConfig, run_pipeline

logger = logging.getLogger(__name__)

# Части датасета. Само разбиение задаётся в файле эталона, см. splits.py.
SUPPORTED_SPLITS = ("all",) + SPLITS

# Форматы исходников. Порядок важен: он же порядок обхода при `formats`.
SUPPORTED_FORMATS = ("docx", "pdf")

# Грубая оценка токенов на символ текста (для --dry-run).
CHARS_PER_TOKEN = 4

# Накладные токены на один запрос (системный промпт и схема).
PROMPT_OVERHEAD_TOKENS = 600

TRUTH_SUFFIX = ".truth.json"

# Имя файла с построчным прогрессом прогона.
PROGRESS_FILENAME = "progress.jsonl"


@dataclass(frozen=True)
class DatasetItem:
    """Один документ датасета: исходник и эталон."""

    doc_id: str
    doc_type: str
    source_path: Path
    truth: dict[str, Any]
    split: str = ""
    source_format: str = ""


# --- Загрузка ---------------------------------------------------------------

def _find_source(truth_path: Path, source_format: str) -> Path | None:
    """Ищет исходник конкретного формата рядом с *.truth.json."""
    stem = truth_path.name[: -len(TRUTH_SUFFIX)]
    candidate = truth_path.with_name(f"{stem}.{source_format}")
    return candidate if candidate.exists() else None


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
    formats: Sequence[str] = SUPPORTED_FORMATS,
) -> list[DatasetItem]:
    """Собирает датасет из каталога вида eval/dataset/<тип>/<имя>.truth.json.

    На каждый существующий исходник создаётся отдельный элемент, поэтому
    при formats=("docx", "pdf") один эталон даёт два элемента с разным
    `source_format`.
    """
    if split not in SUPPORTED_SPLITS:
        raise ValueError(
            f"Неизвестный split: {split}. Допустимые: {', '.join(SUPPORTED_SPLITS)}"
        )

    unknown = [name for name in formats if name not in SUPPORTED_FORMATS]
    if unknown:
        raise ValueError(
            f"Неизвестный формат: {', '.join(unknown)}. "
            f"Допустимые: {', '.join(SUPPORTED_FORMATS)}"
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

        found_any = False
        for source_format in formats:
            source = _find_source(truth_path, source_format)
            if source is None:
                continue

            found_any = True
            items.append(
                DatasetItem(
                    doc_id=doc_id,
                    doc_type=doc_type,
                    source_path=source,
                    truth=truth,
                    split=declared or "",
                    source_format=source_format,
                )
            )

        if not found_any:
            logger.warning(
                "Для %s нет исходников в форматах %s — пропускаю",
                truth_path,
                ", ".join(formats),
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


# --- Прогресс прогона -------------------------------------------------------

def progress_key(item: DatasetItem, repeat_index: int) -> str:
    """Ключ одного прогона: документ, формат и номер повтора."""
    return f"{item.doc_id}|{item.source_format}|{repeat_index}"


def _match_to_dict(match: FieldMatch) -> dict[str, str]:
    return {
        "path": match.path,
        "kind": match.kind,
        "expected": match.expected,
        "actual": match.actual,
        "status": match.status,
        "category": match.category,
    }


def _match_from_dict(payload: dict[str, Any]) -> FieldMatch:
    return FieldMatch(
        path=payload.get("path", ""),
        kind=payload.get("kind", "text"),
        expected=payload.get("expected", ""),
        actual=payload.get("actual", ""),
        status=payload.get("status", "wrong"),
        category=payload.get("category", ""),
    )


def load_progress(path: str | Path | None) -> dict[str, dict[str, Any]]:
    """Читает progress.jsonl и возвращает записи по ключам прогонов.

    Битые строки пропускаются: файл мог обрезаться при аварийном
    завершении, и из-за одной строки терять весь прогресс нельзя.
    """
    if not path:
        return {}

    target = Path(path)
    if not target.exists():
        return {}

    records: dict[str, dict[str, Any]] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Строка прогресса не разобрана, пропускаю")
            continue

        key = record.get("key")
        if key:
            records[key] = record

    return records


def append_progress(path: str | Path, record: dict[str, Any]) -> None:
    """Дописывает одну запись прогресса и сразу сбрасывает на диск."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def _replay(metrics: Metrics, record: dict[str, Any]) -> None:
    """Возвращает в метрики уже посчитанный результат из прогресса."""
    matches = [_match_from_dict(item) for item in record.get("matches", [])]
    metrics.add_document(
        record.get("doc_type", ""),
        matches,
        expected_type=record.get("expected_type", ""),
        actual_type=record.get("actual_type", ""),
        elapsed=record.get("elapsed", 0.0),
        usage=record.get("usage"),
        source_format=record.get("format", ""),
    )


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
    progress_path: str | Path | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Прогоняет конвейер по датасету и возвращает сводку метрик.

    Параметры:
        repeat — сколько раз обработать каждый документ (модель
                 недетерминирована, нужен разброс).
        dry_run — только оценить расход токенов, модель не вызывать.
        max_tokens — предохранитель: остановиться при превышении бюджета.
        use_truth_type — подставлять тип из эталона вместо классификации
                 (сравнение «тип из формы» против режима «Авто»).
        progress_path — файл, куда построчно пишется результат каждого
                 документа. Единственный способ пережить обрыв прогона.
        resume — прочитать progress_path и пропустить уже сделанное.
    """
    extract = extractor or extract_text
    run = pipeline_fn or run_pipeline
    base_config = config or PipelineConfig()

    metrics = Metrics()
    estimated = 0
    stopped_early = False
    processed = 0
    resumed = 0

    done = load_progress(progress_path) if (resume and progress_path) else {}

    for item in items:
        if max_tokens and estimated >= max_tokens:
            stopped_early = True
            logger.warning(
                "Достигнут бюджет %d токенов, прогон остановлен", max_tokens
            )
            break

        item_config = (
            replace(base_config, doc_type_hint=item.doc_type)
            if use_truth_type
            else base_config
        )

        pending = [
            index for index in range(max(1, repeat))
            if progress_key(item, index) not in done
        ]

        if not pending:
            # Всё по этому документу уже сделано ранее
            for index in range(max(1, repeat)):
                _replay(metrics, done[progress_key(item, index)])
                resumed += 1
            processed += 1
            continue

        try:
            text = extract(item.source_path)
        except ExtractionError as exc:
            metrics.add_failure(item.doc_type, exc, source_format=item.source_format)
            continue
        except Exception as exc:  # файл битый — не роняем весь прогон
            metrics.add_failure(item.doc_type, exc, source_format=item.source_format)
            continue

        if dry_run:
            estimated += estimate_tokens(text) * max(1, repeat)
            processed += 1
            continue

        for repeat_index in range(max(1, repeat)):
            key = progress_key(item, repeat_index)

            if key in done:
                _replay(metrics, done[key])
                resumed += 1
                continue

            started = time.perf_counter()
            try:
                result = run(text=text, config=item_config)
            except Exception as exc:
                metrics.add_failure(
                    item.doc_type, exc, source_format=item.source_format
                )
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
                source_format=item.source_format,
            )

            if progress_path:
                append_progress(
                    progress_path,
                    {
                        "key": key,
                        "doc_id": item.doc_id,
                        "doc_type": item.doc_type,
                        "format": item.source_format,
                        "repeat_index": repeat_index,
                        "expected_type": item.doc_type,
                        "actual_type": result.doc_type,
                        "elapsed": round(elapsed, 3),
                        "usage": result.meta.get("usage"),
                        "matches": [_match_to_dict(m) for m in matches],
                    },
                )

            prompt, completion = _tokens(result.meta.get("usage"))
            estimated += prompt + completion if prompt or completion else estimate_tokens(text)

        processed += 1

    return {
        "summary": metrics.to_dict(),
        "dry_run": dry_run,
        "items": len(items),
        "processed": processed,
        "resumed": resumed,
        "repeat": repeat,
        "estimated_tokens": estimated,
        "stopped_early": stopped_early,
        "use_truth_type": use_truth_type,
        "formats": sorted({item.source_format for item in items}),
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

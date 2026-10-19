"""Оценка качества извлечения данных (этап 7).

Модуль не входит в веб-запросы и не требует БД: он берёт датасет
(документ + эталон), прогоняет конвейер и считает метрики.

    matching.py — правила сравнения значений с эталоном
    metrics.py  — накопление TP/FP/FN и расчёт precision/recall/F1
    runner.py   — загрузка датасета и прогон конвейера
    report.py   — отчёты (JSON, CSV, Markdown)
"""

from documents.evaluation.matching import (
    FUZZY_THRESHOLD,
    FieldMatch,
    compare_data,
    compare_maps,
    flatten,
)
from documents.evaluation.metrics import Metrics
from documents.evaluation.runner import DatasetItem, load_dataset, run_evaluation

__all__ = [
    "FUZZY_THRESHOLD",
    "DatasetItem",
    "FieldMatch",
    "Metrics",
    "compare_data",
    "compare_maps",
    "flatten",
    "load_dataset",
    "run_evaluation",
]

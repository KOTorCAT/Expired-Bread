"""Отчёты по результатам оценки качества.

Пишет четыре файла в каталог результатов:

  summary.json — полная сводка (машиночитаемая).
  per_field.csv — метрики по каждому полю (для Excel).
  errors.csv    — каждая ошибка отдельной строкой (главный файл для разбора).
  report.md     — человекочитаемый отчёт для ВКР.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def write_reports(result: dict[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """Записывает отчёты и возвращает пути к созданным файлам."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    summary = result["summary"]
    paths: dict[str, Path] = {}

    summary_path = directory / "summary.json"
    summary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["summary"] = summary_path

    fields_path = directory / "per_field.csv"
    with fields_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            ["field", "tp", "fp", "fn", "support", "precision", "recall", "f1",
             "format_errors", "fuzzy"]
        )
        for name, counter in summary["fields"]["per_field"].items():
            writer.writerow(
                [
                    name,
                    counter["tp"], counter["fp"], counter["fn"],
                    counter["support"],
                    _fmt(counter["precision"]),
                    _fmt(counter["recall"]),
                    _fmt(counter["f1"]),
                    counter["format_errors"],
                    counter["fuzzy"],
                ]
            )
    paths["per_field"] = fields_path

    errors_path = directory / "errors.csv"
    with errors_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            ["doc_type", "format", "field", "kind", "expected", "actual",
             "status", "category"]
        )
        for error in summary["errors"]:
            writer.writerow(
                [
                    error["doc_type"],
                    error.get("format", ""),
                    error["path"],
                    error["kind"],
                    error["expected"],
                    error["actual"],
                    error["status"],
                    error["category"],
                ]
            )
    paths["errors"] = errors_path

    report_path = directory / "report.md"
    report_path.write_text(render_markdown(result), encoding="utf-8")
    paths["report"] = report_path

    return paths


def render_markdown(result: dict[str, Any]) -> str:
    """Собирает отчёт в Markdown."""
    summary = result["summary"]
    fields = summary["fields"]
    micro = fields["micro"]
    classification = summary["classification"]
    tokens = summary["tokens"]
    timing = summary["timing"]

    lines: list[str] = []
    add = lines.append

    add("# Оценка качества извлечения данных")
    add("")
    add(f"- Документов обработано: **{summary['documents']}** "
        f"(ошибок обработки: {summary['failed']})")
    add(f"- Повторов на документ: {result.get('repeat', 1)}")
    add(f"- Всего прогонов: {summary['runs']}")

    if result.get("dry_run"):
        add(f"- **Пробный прогон (dry-run)**: оценка расхода "
            f"≈ {result.get('estimated_tokens', 0)} токенов")
    if result.get("stopped_early"):
        add("- ⚠️ Прогон остановлен по лимиту токенов")
    add("")

    add("## Конфигурация")
    add("")
    add("| Параметр | Значение |")
    add("| --- | --- |")
    for key, value in (result.get("config") or {}).items():
        add(f"| {key} | {value} |")
    if result.get("use_truth_type"):
        add("| тип документа | из эталона (классификация не вызывалась) |")
    add("")

    add("## Метрики по полям")
    add("")
    add("| Метрика | Значение |")
    add("| --- | --- |")
    add(f"| Precision (micro) | {_fmt(micro['precision'])} |")
    add(f"| Recall (micro) | {_fmt(micro['recall'])} |")
    add(f"| F1 (micro) | {_fmt(micro['f1'])} |")
    add(f"| F1 (macro по полям) | {_fmt(fields['macro_f1'])} |")
    add(f"| Ошибок нормализации | {micro['format_errors']} |")
    add(f"| Мягких совпадений | {micro['fuzzy']} |")
    if classification["total"]:
        add(f"| Точность классификации | {_fmt(classification['accuracy'])} "
            f"({classification['correct']}/{classification['total']}) |")
    add("")

    add("## По полям")
    add("")
    add("| Поле | TP | FP | FN | Precision | Recall | F1 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for name, counter in fields["per_field"].items():
        add(
            f"| `{name}` | {counter['tp']} | {counter['fp']} | {counter['fn']} "
            f"| {_fmt(counter['precision'])} | {_fmt(counter['recall'])} "
            f"| {_fmt(counter['f1'])} |"
        )
    add("")

    per_type = summary.get("per_doc_type") or {}
    if per_type:
        add("## По типам документов")
        add("")
        add("| Тип | Precision | Recall | F1 |")
        add("| --- | --- | --- | --- |")
        for doc_type, block in per_type.items():
            m = block["micro"]
            add(
                f"| `{doc_type}` | {_fmt(m['precision'])} | {_fmt(m['recall'])} "
                f"| {_fmt(m['f1'])} |"
            )
        add("")

    per_format = summary.get("per_format") or {}
    if len(per_format) > 1:
        add("## По форматам файлов")
        add("")
        add("| Формат | Документов | Precision | Recall | F1 |")
        add("| --- | --- | --- | --- | --- |")
        for source_format, block in per_format.items():
            m = block["micro"]
            add(
                f"| `{source_format}` | {block['documents']} "
                f"| {_fmt(m['precision'])} | {_fmt(m['recall'])} "
                f"| {_fmt(m['f1'])} |"
            )
        add("")

    if classification["confusion"]:
        add("## Матрица ошибок классификации")
        add("")
        add("| Эталон \\ Ответ | " + " | ".join(
            sorted({k for row in classification["confusion"].values() for k in row})
        ) + " |")
        columns = sorted({k for row in classification["confusion"].values() for k in row})
        add("| --- | " + " | ".join("---" for _ in columns) + " |")
        for expected, row in sorted(classification["confusion"].items()):
            cells = " | ".join(str(row.get(col, 0)) for col in columns)
            add(f"| `{expected}` | {cells} |")
        add("")

    add("## Типичные ошибки")
    add("")
    categories: dict[str, int] = {}
    for error in summary["errors"]:
        key = error["category"] or error["status"]
        categories[key] = categories.get(key, 0) + 1

    if categories:
        add("| Категория | Количество |")
        add("| --- | --- |")
        for category, count in sorted(categories.items(), key=lambda kv: -kv[1]):
            add(f"| {category} | {count} |")
        add("")
        add("Первые 15 ошибок (полный список — в `errors.csv`):")
        add("")
        add("| Документ | Поле | Эталон | Ответ | Категория |")
        add("| --- | --- | --- | --- | --- |")
        for error in summary["errors"][:15]:
            add(
                f"| `{error['doc_type']}` | `{error['path']}` "
                f"| {_truncate(error['expected'])} | {_truncate(error['actual'])} "
                f"| {error['category'] or error['status']} |"
            )
        add("")
    else:
        add("Ошибок не найдено.")
        add("")

    if summary["failures"]:
        add("## Ошибки обработки")
        add("")
        for failure in summary["failures"][:10]:
            add(f"- `{failure['doc_type']}`: {failure['error']}")
        add("")

    add("## Стоимость и время")
    add("")
    add(f"- Токенов: {tokens['total']} "
        f"({tokens['prompt']} на вход, {tokens['completion']} на выход), "
        f"≈ {tokens['per_document']} на документ")
    add(f"- Время: {timing['total_s']} с, "
        f"≈ {timing['mean_s_per_document']} с на документ")
    add("")

    return "\n".join(lines)


def _truncate(value: Any, limit: int = 40) -> str:
    text = str(value or "—").replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"

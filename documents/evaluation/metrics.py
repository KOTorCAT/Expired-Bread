"""Накопление и расчёт метрик качества.

Метрики считаются по полям: для каждого поля известны TP, FP и FN.

  TP — поле извлечено и совпало с эталоном.
  FP — поле извлечено, но значения нет в эталоне или оно неверно.
  FN — поле есть в эталоне, но не извлечено (или извлечено неверно).

Неверное значение даёт одновременно FP и FN — это стандартный подход,
он не позволяет «спрятать» ошибку, посчитав её только с одной стороны.

Сводные метрики:
  micro — суммируются TP/FP/FN по всем полям сразу (частые поля весят
          больше, это «точность по всем значениям»).
  macro — среднее F1 по полям (все поля равноправны, даже редкие).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from documents.evaluation.matching import FieldMatch


def field_name(path: str) -> str:
    """'items[2].price' → 'items.price'; 'requirements[0]' → 'requirements'."""
    name = path
    while "[" in name:
        start = name.index("[")
        end = name.index("]", start) + 1
        name = name[:start] + name[end:]
    return name


def _div(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@dataclass
class Counter:
    """Счётчики одного поля."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    format_errors: int = 0
    fuzzy: int = 0

    def add(self, match: FieldMatch) -> None:
        status = match.status
        if status in ("tp", "tp_fuzzy"):
            self.tp += 1
            if status == "tp_fuzzy":
                self.fuzzy += 1
        elif status == "fp":
            self.fp += 1
        elif status == "fn":
            self.fn += 1
        elif status == "format":
            self.fp += 1
            self.fn += 1
            self.format_errors += 1
        else:  # 'wrong' и всё неизвестное считаем ошибкой значения
            self.fp += 1
            self.fn += 1

    def merge(self, other: "Counter") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.format_errors += other.format_errors
        self.fuzzy += other.fuzzy

    @property
    def support(self) -> int:
        """Сколько раз поле встречалось в эталоне."""
        return self.tp + self.fn

    @property
    def precision(self) -> float:
        return _div(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return _div(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return round(2 * p * r / (p + r), 4) if (p + r) else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "support": self.support,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "format_errors": self.format_errors,
            "fuzzy": self.fuzzy,
        }


def _sum_usage(usage: Any) -> tuple[int, int]:
    """Суммирует prompt/completion токены из вложенного usage-словаря."""
    prompt = 0
    completion = 0

    def walk(node: Any) -> None:
        nonlocal prompt, completion
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    lowered = str(key).lower()
                    if "prompt" in lowered:
                        prompt += int(value)
                    elif "completion" in lowered:
                        completion += int(value)
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(usage)
    return prompt, completion


class Metrics:
    """Собирает результаты прогона и выдаёт сводку."""

    def __init__(self) -> None:
        self.per_field: dict[str, Counter] = {}
        self.per_type_field: dict[tuple[str, str], Counter] = {}
        self.per_type: dict[str, Counter] = {}
        self.per_format: dict[str, Counter] = {}
        self.format_documents: dict[str, int] = {}
        self.format_failures: dict[str, int] = {}
        self.errors: list[dict[str, Any]] = []
        self.classification_total = 0
        self.classification_correct = 0
        self.confusion: dict[str, dict[str, int]] = {}
        self.documents = 0
        self.runs = 0
        self.failed = 0
        self.failures: list[dict[str, str]] = []
        self.total_seconds = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    # --- Наполнение ---------------------------------------------------------

    def add_document(
        self,
        doc_type: str,
        matches: Iterable[FieldMatch],
        *,
        expected_type: str = "",
        actual_type: str = "",
        elapsed: float = 0.0,
        usage: Any = None,
        source_format: str = "",
    ) -> None:
        fmt = source_format or "unknown"
        self.format_documents[fmt] = self.format_documents.get(fmt, 0) + 1
        format_counter = self.per_format.setdefault(fmt, Counter())

        self.documents += 1
        self.runs += 1
        self.total_seconds += elapsed

        prompt, completion = _sum_usage(usage)
        self.prompt_tokens += prompt
        self.completion_tokens += completion

        if expected_type:
            self.classification_total += 1
            actual = actual_type or "other"
            if actual == expected_type:
                self.classification_correct += 1
            row = self.confusion.setdefault(expected_type, {})
            row[actual] = row.get(actual, 0) + 1

        for match in matches:
            name = field_name(match.path)

            counter = self.per_field.setdefault(name, Counter())
            counter.add(match)

            type_counter = self.per_type_field.setdefault(
                (doc_type, name), Counter()
            )
            type_counter.add(match)

            doc_counter = self.per_type.setdefault(doc_type, Counter())
            doc_counter.add(match)

            format_counter.add(match)

            if not match.is_true_positive:
                self.errors.append(
                    {
                        "doc_type": doc_type,
                        "format": fmt,
                        "path": match.path,
                        "kind": match.kind,
                        "expected": match.expected,
                        "actual": match.actual,
                        "status": match.status,
                        "category": match.category,
                    }
                )

    def add_failure(
        self,
        doc_type: str,
        error: BaseException | str,
        source_format: str = "",
    ) -> None:
        self.runs += 1
        self.failed += 1
        fmt = source_format or "unknown"
        self.format_failures[fmt] = self.format_failures.get(fmt, 0) + 1
        message = str(error) if not isinstance(error, str) else error
        self.failures.append({"doc_type": doc_type, "error": message[:500]})

    # --- Сводка -------------------------------------------------------------

    @property
    def micro(self) -> Counter:
        total = Counter()
        for counter in self.per_field.values():
            total.merge(counter)
        return total

    def to_dict(self) -> dict[str, Any]:
        micro = self.micro

        per_field = {
            name: counter.to_dict()
            for name, counter in sorted(self.per_field.items())
        }

        macro_f1 = (
            round(sum(c.f1 for c in self.per_field.values()) / len(self.per_field), 4)
            if self.per_field
            else 0.0
        )

        per_doc_type: dict[str, Any] = {}
        for doc_type, counter in sorted(self.per_type.items()):
            per_doc_type[doc_type] = {
                "micro": counter.to_dict(),
                "fields": {
                    name: c.to_dict()
                    for (t, name), c in sorted(self.per_type_field.items())
                    if t == doc_type
                },
            }

        total_tokens = self.prompt_tokens + self.completion_tokens

        return {
            "documents": self.documents,
            "runs": self.runs,
            "failed": self.failed,
            "failures": self.failures,
            "classification": {
                "total": self.classification_total,
                "correct": self.classification_correct,
                "accuracy": _div(self.classification_correct, self.classification_total),
                "confusion": self.confusion,
            },
            "fields": {
                "micro": micro.to_dict(),
                "macro_f1": macro_f1,
                "per_field": per_field,
            },
            "per_doc_type": per_doc_type,
            "per_format": {
                fmt: {
                    "documents": self.format_documents.get(fmt, 0),
                    "failed": self.format_failures.get(fmt, 0),
                    "micro": counter.to_dict(),
                }
                for fmt, counter in sorted(self.per_format.items())
            },
            "timing": {
                "total_s": round(self.total_seconds, 3),
                "mean_s_per_document": _div(
                    self.total_seconds, self.documents
                ),
            },
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": total_tokens,
                "per_document": round(_div(total_tokens, self.documents), 1),
            },
            "errors": self.errors,
        }

"""Сравнение предсказанных данных с эталоном.

Модуль не знает ни про Django, ни про GigaChat: на входе два словаря
(предсказание и эталон), на выходе список FieldMatch. Поэтому правила
сравнения тестируются без модели, без сети и без БД.

Правила сравнения (после нормализации обеих сторон):

  text            normalize_text равны. Если не равны, но difflib даёт
                  не меньше FUZZY_THRESHOLD — считаем верным и помечаем
                  статусом 'tp_fuzzy' (мягкое совпадение).
  money, memory,  числа равны с точностью FLOAT_TOLERANCE and совпадают
  number, date    единицы. Если единица указана только с одной стороны,
                  расхождением это не считаем: «89990» и «89 990 руб.»
                  описывают одну цену.
  иначе           статус 'wrong', категория 'wrong_value'.

Отдельная категория 'format': модель извлекла значение верно (текст
совпал), но нормализатор не смог его разобрать в число. Это не ошибка
модели, а ошибка правил нормализации — их и надо править.

Функция flatten даёт пути вида 'items[2].price', поэтому отчёт об
ошибках указывает конкретную позицию списка, а не просто ключ.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from documents.services.normalization import (
    FIELD_KINDS,
    normalize_text,
    parse_date,
    parse_number,
    parse_quantity,
)

# Порог мягкого совпадения текста.
FUZZY_THRESHOLD = 0.90

# Допуск при сравнении чисел.
FLOAT_TOLERANCE = 1e-6

# Виды полей, которые сравниваются как числа, а не как строки.
NUMERIC_KINDS = frozenset({"money", "memory", "number", "date"})


@dataclass(frozen=True)
class NormalizedValue:
    """Одно значение, приведённое к сравнимому виду."""

    text: str
    num: float | None
    unit: str
    raw: str


@dataclass(frozen=True)
class FieldMatch:
    """Результат сравнения одного поля."""

    path: str
    kind: str
    expected: str
    actual: str
    status: str          # 'tp' | 'tp_fuzzy' | 'fp' | 'fn' | 'wrong' | 'format'
    category: str = ""   # 'missing' | 'wrong_value' | 'hallucination' | 'format'

    @property
    def is_true_positive(self) -> bool:
        return self.status in ("tp", "tp_fuzzy")


def field_kind(doc_type: str, key: str) -> str:
    """Вид поля по реестру типов. Неизвестные поля — текст."""
    kinds = FIELD_KINDS.get(doc_type) or FIELD_KINDS.get("other", {})
    return kinds.get(key, "text")


def _kind_key(path: str) -> str:
    """Из пути 'items[0].price' достаёт 'price', из 'requirements[2]' — 'requirements'."""
    if "." in path:
        return path.rsplit(".", 1)[-1]
    return path.split("[", 1)[0]


def normalize_value(kind: str, value: Any) -> NormalizedValue:
    """Приводит значение к сравнимому виду в зависимости от вида поля."""
    raw = "" if value is None else str(value)
    text = normalize_text(value)
    num: float | None = None
    unit = ""

    if kind == "money":
        num, unit = parse_quantity(value, kind="money")
    elif kind == "memory":
        num, unit = parse_quantity(value, kind="memory")
    elif kind == "number":
        num = parse_number(value)
    elif kind == "date":
        parsed = parse_date(value)
        if parsed is not None:
            num = float(parsed.toordinal())
            text = parsed.isoformat()

    return NormalizedValue(text=text, num=num, unit=unit, raw=raw)


def flatten(doc_type: str, data: Any) -> dict[str, tuple[str, NormalizedValue]]:
    """Разворачивает data в плоский словарь {путь: (вид, значение)}.

    Скаляры → 'price'. Элементы списков → 'items[0].price'.
    Ключи словарей → 'characteristics.ram'.

    Пустые значения (None, '', пустые списки) пропускаются: в эталоне
    отсутствие поля — это норма, а не значение.
    """
    result: dict[str, tuple[str, NormalizedValue]] = {}
    if not isinstance(data, dict):
        return result

    for key, value in data.items():
        _flatten_node(result, doc_type, str(key), value)

    return result


def _flatten_node(
    out: dict[str, tuple[str, NormalizedValue]],
    doc_type: str,
    path: str,
    value: Any,
) -> None:
    if value is None:
        return

    if isinstance(value, dict):
        for sub_key, sub_value in value.items():
            _flatten_node(out, doc_type, f"{path}.{sub_key}", sub_value)
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _flatten_node(out, doc_type, f"{path}[{index}]", item)
        return

    if isinstance(value, str) and not value.strip():
        return

    kind = field_kind(doc_type, _kind_key(path))
    out[path] = (kind, normalize_value(kind, value))


def _match(
    path: str,
    kind: str,
    expected: NormalizedValue,
    actual: NormalizedValue,
    status: str,
    category: str = "",
) -> FieldMatch:
    return FieldMatch(
        path=path,
        kind=kind,
        expected=expected.raw,
        actual=actual.raw,
        status=status,
        category=category,
    )


def _compare_numeric(
    path: str,
    kind: str,
    expected: NormalizedValue,
    actual: NormalizedValue,
) -> FieldMatch:
    exp_num, act_num = expected.num, actual.num

    if exp_num is not None and act_num is not None:
        same_number = abs(exp_num - act_num) <= FLOAT_TOLERANCE
        # Единица, указанная только с одной стороны, — не расхождение.
        same_unit = not expected.unit or not actual.unit or expected.unit == actual.unit
        if same_number and same_unit:
            return _match(path, kind, expected, actual, "tp")
        return _match(path, kind, expected, actual, "wrong", "wrong_value")

    # Хотя бы с одной стороны число не разобралось.
    if expected.text and expected.text == actual.text:
        return _match(path, kind, expected, actual, "format", "format")

    return _match(path, kind, expected, actual, "wrong", "wrong_value")


def _compare_text(
    path: str,
    kind: str,
    expected: NormalizedValue,
    actual: NormalizedValue,
) -> FieldMatch:
    if expected.text == actual.text:
        return _match(path, kind, expected, actual, "tp")

    ratio = SequenceMatcher(None, expected.text, actual.text).ratio()
    if ratio >= FUZZY_THRESHOLD:
        return _match(path, kind, expected, actual, "tp_fuzzy")

    return _match(path, kind, expected, actual, "wrong", "wrong_value")


def _compare_one(
    path: str,
    kind: str,
    expected: NormalizedValue,
    actual: NormalizedValue,
) -> FieldMatch:
    if kind in NUMERIC_KINDS:
        return _compare_numeric(path, kind, expected, actual)
    return _compare_text(path, kind, expected, actual)


def compare_maps(
    expected: dict[str, tuple[str, NormalizedValue]],
    actual: dict[str, tuple[str, NormalizedValue]],
) -> list[FieldMatch]:
    """Сравнивает два результата flatten.

    Поле только в эталоне → 'fn' (категория missing).
    Поле только в ответе модели → 'fp' (категория hallucination).
    Поле в обоих → сравнение по виду поля.
    """
    matches: list[FieldMatch] = []

    for path in sorted(set(expected) | set(actual)):
        exp = expected.get(path)
        act = actual.get(path)

        if exp is None:
            kind, value = act  # type: ignore[misc]
            matches.append(
                FieldMatch(
                    path=path, kind=kind, expected="", actual=value.raw,
                    status="fp", category="hallucination",
                )
            )
            continue

        if act is None:
            kind, value = exp
            matches.append(
                FieldMatch(
                    path=path, kind=kind, expected=value.raw, actual="",
                    status="fn", category="missing",
                )
            )
            continue

        kind = exp[0]
        matches.append(_compare_one(path, kind, exp[1], act[1]))

    return matches


def compare_data(doc_type: str, expected: Any, actual: Any) -> list[FieldMatch]:
    """Сравнивает два словаря данных по правилам типа документа."""
    return compare_maps(
        flatten(doc_type, expected),
        flatten(doc_type, actual),
    )

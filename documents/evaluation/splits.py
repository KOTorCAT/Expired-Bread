"""Единственное определение разбиения dev/test.

Раньше разбиение задавалось дважды и по-разному:

  * генератор датасета писал в эталон `_meta.split` по номеру документа
    (первые 70 % каждого типа — dev);
  * раннер оценки определял часть по хешу имени и `_meta` вообще
    не читал, потому что при загрузке эталона метаданные отбрасывались.

Из-за этого `evaluate --split test` мог взять документы, помеченные
генератором как dev, и наоборот. Методологическая гарантия «test не
трогали при настройке промптов» фактически не выполнялась.

Теперь источник истины один — поле `_meta.split` в файле эталона:

  * генератор пишет его через `assign_split`;
  * раннер читает его через `resolve_split`;
  * `validate_dataset` проверяет, что оно есть у каждого документа.

Документ без `_meta.split` не попадает ни в dev, ни в test: молча
угадывать его часть — это ровно та ошибка, которую мы убираем.
"""

from __future__ import annotations

from typing import Any

DEV = "dev"
TEST = "test"
SPLITS = (DEV, TEST)

# Доля dev-документов при генерации. Вынесена сюда, чтобы генератор
# и проверки не заводили собственных констант.
DEV_RATIO = 0.7


def assign_split(index: int, count: int, ratio: float = DEV_RATIO) -> str:
    """Часть датасета по номеру документа внутри типа (нумерация с 1).

    Первые `int(count * ratio)` документов типа идут в dev, остальные
    в test. Разбиение по номеру, а не по хешу, сохраняет одинаковую
    долю dev у каждого типа: при 25 документах и 0,7 это 17 и 8.
    """
    if count <= 0:
        raise ValueError("count должен быть положительным")
    if not 1 <= index <= count:
        raise ValueError(f"index вне диапазона 1..{count}: {index}")
    if not 0.0 < ratio < 1.0:
        raise ValueError(f"ratio должен быть между 0 и 1: {ratio}")

    return DEV if index <= int(count * ratio) else TEST


def declared_split(payload: Any) -> str | None:
    """Достаёт `_meta.split` из содержимого файла эталона.

    Возвращает None, если метаданных нет или значение неизвестно.
    """
    if not isinstance(payload, dict):
        return None

    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        return None

    value = meta.get("split")
    return value if value in SPLITS else None


def resolve_split(payload: Any) -> str | None:
    """Часть датасета для документа или None, если она не записана."""
    return declared_split(payload)

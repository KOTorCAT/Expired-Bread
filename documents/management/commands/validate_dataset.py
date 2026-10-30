"""Проверка датасета: эталон должен соответствовать исходнику.

Зачем: если в эталоне записано значение, которого в тексте документа нет,
модель физически не может его извлечь. Она получает 'fn' (missing),
метрики падают, и мы начинаем исправлять несуществующую ошибку модели
вместо того, чтобы исправить датасет.

Команда проверяет:

  1. у каждого эталона есть исходник (.docx или .pdf);
  2. в каждом эталоне записано `_meta.split` (dev или test);
  3. текст исходника извлекается;
  4. каждое оцениваемое значение из эталона встречается в тексте.

Числа ищутся по цифрам без учёта пробелов между разрядами, даты —
в формате дд.мм.гггг или как записаны. Текстовые значения ищутся
по нормализованной подстроке.

Примеры:

    python manage.py validate_dataset
    python manage.py validate_dataset --verbose
    python manage.py validate_dataset --types price_list --limit 5
    python manage.py validate_dataset --allow-misses --out eval/dataset-report.json
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from documents.evaluation.matching import NUMERIC_KINDS, flatten
from documents.evaluation.runner import TRUTH_SUFFIX
from documents.evaluation.splits import SPLITS, resolve_split
from documents.services.extractors import ExtractionError, extract_text
from documents.services.normalization import normalize_text
from documents.services.pipeline import PipelineConfig

# Виды полей, для которых сравниваем числа, а не строки.
_NUMERIC_TEXT_SPLIT = re.compile(r"[.,]")

# Расширения исходников в порядке предпочтения.
SOURCE_SUFFIXES = (".docx", ".pdf")


def field_name(path: str) -> str:
    """Из 'items[0].price' достаёт 'price', из 'requirements[2]' — 'requirements'."""
    if "." in path:
        return path.rsplit(".", 1)[-1]
    return path.split("[", 1)[0]


def find_source(truth_path: Path) -> Path | None:
    """Ищет исходный файл рядом с эталоном."""
    stem = truth_path.name[: -len(TRUTH_SUFFIX)]
    for suffix in SOURCE_SUFFIXES:
        candidate = truth_path.with_name(stem + suffix)
        if candidate.exists():
            return candidate
    return None


def number_pattern(number: float) -> str:
    """Регулярное выражение для числа с произвольными пробелами в разрядах.

    89 990, 89990 и 89 990,00 должны находиться одинаково.
    """
    if number < 0:
        raise ValueError("отрицательные числа в датасете не встречаются")

    whole = f"{number:.6f}".rstrip("0").rstrip(".")
    integer, _, fraction = whole.partition(".")

    body = r"[\s\u00a0]*".join(re.escape(digit) for digit in integer)
    if fraction:
        body += r"[.,]" + r"[\s\u00a0]*".join(re.escape(digit) for digit in fraction)
    return body


def value_in_text(kind: str, value, text: str) -> bool:
    """Встречается ли значение в тексте документа.

    `value` — NormalizedValue из matching.flatten.
    """
    # Сначала — как записано в эталоне: так срабатывают значения,
    # которые нормализатор пересчитывает в другую единицу
    # («1 ТБ» превращается в 1024 ГБ, и число уже не совпадёт).
    if value.text and value.text in text:
        return True

    if kind in NUMERIC_KINDS and value.num is not None:
        if kind == "date":
            try:
                day = date.fromordinal(int(value.num))
            except (ValueError, OverflowError):
                return bool(value.text) and value.text in text
            for candidate in (f"{day:%d.%m.%Y}", f"{day:%d.%m.%y}", f"{day:%Y-%m-%d}"):
                if candidate in text:
                    return True
            return bool(value.text) and value.text in text

        return re.search(number_pattern(value.num), text) is not None

    return False


class Command(BaseCommand):
    help = "Проверяет, что эталоны датасета соответствуют текстам документов"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset",
            default=str(Path(settings.BASE_DIR) / "eval" / "dataset"),
            help="Каталог датасета (по умолчанию eval/dataset)",
        )
        parser.add_argument(
            "--types",
            nargs="+",
            default=None,
            help="Проверить только указанные типы документов",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Проверить не больше N документов на тип (0 — все)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Показывать документы без расхождений",
        )
        parser.add_argument(
            "--allow-misses",
            action="store_true",
            help="Не возвращать код ошибки при расхождениях",
        )
        parser.add_argument(
            "--out",
            default="",
            help="Сохранить подробный отчёт в JSON",
        )

    def handle(self, *args, **options):
        root = Path(options["dataset"])
        if not root.exists():
            self.stderr.write(self.style.ERROR(f"Каталог не найден: {root}"))
            return

        wanted_types = set(options["types"]) if options["types"] else None
        limit = options["limit"]

        truth_paths = sorted(root.glob(f"*/*{TRUTH_SUFFIX}"))
        if wanted_types:
            truth_paths = [p for p in truth_paths if p.parent.name in wanted_types]

        self.stdout.write(f"Датасет: {root}")
        self.stdout.write(f"Эталонов найдено: {len(truth_paths)}")
        self.stdout.write("")

        per_type_counter: Counter[str] = Counter()
        bad_field_counter: Counter[str] = Counter()

        checked = 0
        clean = 0
        hard_docs = 0
        long_docs = 0
        lengths: list[int] = []
        broken: list[dict] = []
        no_split: list[str] = []
        no_source: list[str] = []
        failed_extract: list[dict] = []

        current_type = ""

        for truth_path in truth_paths:
            doc_type = truth_path.parent.name

            if limit and per_type_counter[doc_type] >= limit:
                continue
            per_type_counter[doc_type] += 1

            if doc_type != current_type:
                current_type = doc_type
                self.stdout.write(f"[{doc_type}]")

            doc_id = f"{doc_type}/{truth_path.name[: -len(TRUTH_SUFFIX)]}"

            payload = self._read_payload(truth_path)
            if payload is None:
                broken.append({"doc_id": doc_id, "reason": "эталон не читается"})
                self.stdout.write(f"  {doc_id}  ЭТАЛОН НЕ ЧИТАЕТСЯ")
                continue

            checked += 1

            meta = payload.get("_meta") or {}
            if meta.get("hard"):
                hard_docs += 1
            if meta.get("long"):
                long_docs += 1

            if resolve_split(payload) is None:
                no_split.append(doc_id)

            source = find_source(truth_path)
            if source is None:
                no_source.append(doc_id)
                self.stdout.write(f"  {doc_id}  НЕТ ИСХОДНИКА")
                continue

            try:
                text = normalize_text(extract_text(source))
                lengths.append(len(text))
            except ExtractionError as exc:
                failed_extract.append({"doc_id": doc_id, "reason": str(exc)})
                self.stdout.write(f"  {doc_id}  ТЕКСТ НЕ ИЗВЛЕЧЁН: {exc}")
                continue

            data = payload.get("data", payload)
            if not isinstance(data, dict):
                broken.append({"doc_id": doc_id, "reason": "нет данных"})
                continue

            misses = []
            for path, (kind, value) in sorted(flatten(doc_type, data).items()):
                if value_in_text(kind, value, text):
                    continue
                misses.append({"path": path, "value": value.raw})
                bad_field_counter[field_name(path)] += 1

            if not misses:
                clean += 1
                if options["verbose"]:
                    self.stdout.write(f"  {doc_id}  ok")
                continue

            broken.append({"doc_id": doc_id, "misses": misses})
            self.stdout.write(f"  {doc_id}  расхождений: {len(misses)}")
            for miss in misses[:6]:
                self.stdout.write(f"      {miss['path']}: «{miss['value']}» — нет в тексте")
            if len(misses) > 6:
                self.stdout.write(f"      ... и ещё {len(misses) - 6}")

        self._print_summary(
            checked=checked,
            clean=clean,
            broken=broken,
            no_split=no_split,
            no_source=no_source,
            failed_extract=failed_extract,
            bad_fields=bad_field_counter,
            hard=hard_docs,
            long=long_docs,
            lengths=lengths,
        )

        if options["out"]:
            self._write_json(
                Path(options["out"]),
                broken=broken,
                no_split=no_split,
                no_source=no_source,
                failed_extract=failed_extract,
                bad_fields=bad_field_counter,
            )

        problems = len(broken) + len(no_split) + len(no_source) + len(failed_extract)
        if problems and not options["allow_misses"]:
            raise SystemExit(1)

    # --- вспомогательное ----------------------------------------------------

    def _read_payload(self, truth_path: Path) -> dict | None:
        try:
            payload = json.loads(truth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _print_summary(
        self,
        *,
        checked: int,
        clean: int,
        broken: list[dict],
        no_split: list[str],
        no_source: list[str],
        failed_extract: list[dict],
        bad_fields: Counter,
        hard: int = 0,
        long: int = 0,
        lengths: list[int] | None = None,
    ) -> None:
        self.stdout.write("")
        self.stdout.write("Итог:")
        self.stdout.write(f"  проверено документов: {checked}")
        self.stdout.write(f"  без расхождений: {clean}")
        self.stdout.write(f"  с расхождениями: {len(broken)}")
        self.stdout.write(f"  без исходника: {len(no_source)}")
        self.stdout.write(f"  текст не извлечён: {len(failed_extract)}")
        self.stdout.write(f"  без поля _meta.split: {len(no_split)}")

        if checked:
            self.stdout.write(
                f"  сложных документов: {hard} ({hard * 100 // checked} %)"
            )
            self.stdout.write(
                f"  длинных документов: {long} ({long * 100 // checked} %)"
            )

        if lengths:
            ordered = sorted(lengths)
            middle = ordered[len(ordered) // 2]
            self.stdout.write(
                f"  длина текста: мин {ordered[0]}, медиана {middle}, "
                f"макс {ordered[-1]} символов"
            )

            limit = PipelineConfig().max_chars
            overflow = sum(1 for value in ordered if value > limit)
            if overflow:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {overflow} документов длиннее лимита "
                        f"конвейера ({limit}) — текст будет усечён"
                    )
                )
            else:
                self.stdout.write(
                    f"  все документы короче лимита конвейера ({limit})"
                )

        if bad_fields:
            self.stdout.write("")
            self.stdout.write("Значения, которых нет в тексте (по полям):")
            for name, count in bad_fields.most_common(10):
                self.stdout.write(f"  {name}: {count}")

        if no_split:
            self.stdout.write("")
            self.stdout.write(
                "Документы без _meta.split не попадут ни в dev, ни в test:"
            )
            for doc_id in no_split[:10]:
                self.stdout.write(f"  {doc_id}")

    def _write_json(
        self,
        target: Path,
        *,
        broken: list[dict],
        no_split: list[str],
        no_source: list[str],
        failed_extract: list[dict],
        bad_fields: Counter,
    ) -> None:
        payload = {
            "mismatched": broken,
            "without_split": no_split,
            "without_source": no_source,
            "extraction_failed": failed_extract,
            "fields": dict(bad_fields.most_common()),
            "known_splits": list(SPLITS),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Отчёт сохранён: {target}"))

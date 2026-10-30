"""Генерация датасета документов для оценки качества.

Создаёт документы двух-трёх типов в форматах DOCX и PDF,
плюс эталонные truth.json для каждого.

Пример:
    python manage.py make_dataset --per-type 25 --seed 42

Результат:
    eval/dataset/
        price_list/
            pl_001.docx
            pl_001.pdf
            pl_001.truth.json
            ...
        specification/
            sp_001.docx
            ...
        technical_document/
            td_001.docx
            ...
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documents.evaluation.splits import DEV_RATIO, assign_split
from documents.management.commands._dataset_generators import (
    generate_price_list,
    generate_specification,
    generate_technical_document,
)


# Префиксы для имён файлов
TYPE_PREFIXES = {
    "price_list": "pl",
    "specification": "sp",
    "technical_document": "td",
}

# Генераторы по типу
GENERATORS = {
    "price_list": generate_price_list,
    "specification": generate_specification,
    "technical_document": generate_technical_document,
}


class Command(BaseCommand):
    help = "Генерирует датасет документов для оценки качества"

    def add_arguments(self, parser):
        parser.add_argument(
            "--per-type",
            type=int,
            default=25,
            help="Сколько документов на каждый тип (по умолчанию 25)",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Seed для воспроизводимости (по умолчанию 42)",
        )
        parser.add_argument(
            "--split",
            type=float,
            default=DEV_RATIO,
            help="Доля dev-документов (по умолчанию 0.7)",
        )
        parser.add_argument(
            "--out",
            type=str,
            default="eval/dataset",
            help="Папка для датасета (по умолчанию eval/dataset)",
        )
        parser.add_argument(
            "--types",
            nargs="+",
            default=list(GENERATORS.keys()),
            help="Типы документов для генерации",
        )

    def handle(self, *args, **options):
        per_type = options["per_type"]
        base_seed = options["seed"]
        split = options["split"]
        out_dir = Path(settings.BASE_DIR) / options["out"]
        types = options["types"]

        # Проверка типов
        for t in types:
            if t not in GENERATORS:
                raise CommandError(
                    f"Неизвестный тип: {t}. Доступны: {list(GENERATORS.keys())}"
                )

        self.stdout.write(
            f"Генерация датасета: {per_type} документов на тип, seed={base_seed}"
        )
        self.stdout.write(f"Типы: {', '.join(types)}")
        self.stdout.write(f"Dev/test split: {split:.0%} / {1 - split:.0%}")
        self.stdout.write(f"Папка: {out_dir}")
        self.stdout.write("")

        total = 0
        for doc_type in types:
            self._generate_type(
                doc_type=doc_type,
                count=per_type,
                base_seed=base_seed,
                split=split,
                out_dir=out_dir,
            )
            total += per_type

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Готово. Создано документов: {total}")
        )

    def _generate_type(
        self,
        doc_type: str,
        count: int,
        base_seed: int,
        split: float,
        out_dir: Path,
    ) -> None:
        """Генерирует документы одного типа."""
        prefix = TYPE_PREFIXES[doc_type]
        type_dir = out_dir / doc_type
        type_dir.mkdir(parents=True, exist_ok=True)

        generator = GENERATORS[doc_type]

        # Разбиение по номеру документа — ровно то же, что запишется
        # в эталон через assign_split.
        dev_count = sum(
            1 for index in range(1, count + 1) if assign_split(index, count, split) == "dev"
        )

        self.stdout.write(f"[{doc_type}]")
        self.stdout.write(f"  dev: {dev_count}, test: {count - dev_count}")

        for i in range(1, count + 1):
            # Seed на каждый документ свой, но воспроизводимый
            doc_seed = base_seed * 1000 + i
            split_name = assign_split(i, count, split)

            try:
                truth, docx_bytes, pdf_bytes = generator(doc_seed)
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"  [{i}] ошибка генерации: {exc}")
                )
                continue

            # Имена файлов
            base_name = f"{prefix}_{i:03d}"
            docx_path = type_dir / f"{base_name}.docx"
            pdf_path = type_dir / f"{base_name}.pdf"
            truth_path = type_dir / f"{base_name}.truth.json"

            # Сохраняем
            docx_path.write_bytes(docx_bytes)
            pdf_path.write_bytes(pdf_bytes)

            # В truth добавляем метаданные для оценки
            truth_with_meta = {
                "_meta": {
                    "doc_type": doc_type,
                    "split": split_name,
                    "seed": doc_seed,
                    "file_base": base_name,
                },
                "data": truth,
            }
            truth_path.write_text(
                json.dumps(truth_with_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            size_kb_docx = len(docx_bytes) / 1024
            size_kb_pdf = len(pdf_bytes) / 1024
            self.stdout.write(
                f"  [{i:3d}] {base_name} ({split_name}): "
                f"docx={size_kb_docx:.1f} КБ, pdf={size_kb_pdf:.1f} КБ"
            )
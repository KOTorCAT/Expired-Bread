"""Валидация датасета: проверка truth.json по Pydantic-схемам.

Проверяет, что каждая запись в truth.json может быть провалидирована
схемой соответствующего типа. Если схема изменилась — покажет, какие
документы перестали проходить.

Пример:
    python manage.py validate_dataset --dataset eval/dataset
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from pydantic import ValidationError

from documents.schemas import get_schema


class Command(BaseCommand):
    help = "Проверяет, что truth.json соответствует схемам типов"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset",
            type=str,
            default="eval/dataset",
            help="Папка с датасетом (по умолчанию eval/dataset)",
        )

    def handle(self, *args, **options):
        dataset_dir = Path(settings.BASE_DIR) / options["dataset"]

        if not dataset_dir.exists():
            self.stdout.write(
                self.style.ERROR(f"Папка не найдена: {dataset_dir}")
            )
            return

        truth_files = sorted(dataset_dir.rglob("*.truth.json"))
        if not truth_files:
            self.stdout.write(
                self.style.WARNING("Не найдено ни одного truth.json")
            )
            return

        self.stdout.write(f"Найдено truth-файлов: {len(truth_files)}")
        self.stdout.write("")

        total = 0
        ok_count = 0
        error_count = 0

        for truth_path in truth_files:
            total += 1

            try:
                payload = json.loads(truth_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self.stdout.write(
                    self.style.ERROR(f"  {truth_path.name}: невалидный JSON — {exc}")
                )
                error_count += 1
                continue

            meta = payload.get("_meta", {})
            data = payload.get("data", {})
            doc_type = meta.get("doc_type")

            if not doc_type:
                self.stdout.write(
                    self.style.ERROR(f"  {truth_path.name}: нет _meta.doc_type")
                )
                error_count += 1
                continue

            schema = get_schema(doc_type)

            try:
                schema.model_validate(data)
                ok_count += 1
            except ValidationError as exc:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  {truth_path.name} ({doc_type}):")
                )
                for err in exc.errors():
                    loc = " -> ".join(str(x) for x in err["loc"])
                    self.stdout.write(f"      {loc}: {err['msg']}")

        self.stdout.write("")
        if error_count == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Готово. Проверено: {total}, все валидны"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Готово. Проверено: {total}, "
                    f"ok: {ok_count}, с ошибками: {error_count}"
                )
            )
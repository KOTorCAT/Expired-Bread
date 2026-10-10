"""Пересборка DocumentField для всех ready-документов.

Запускай после изменений в normalization.py или схеме FIELD_KINDS.

Пример:
    python manage.py rebuild_fields
    python manage.py rebuild_fields --doc 5
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services.field_index import index_document


class Command(BaseCommand):
    help = "Пересобирает DocumentField для документов"

    def add_arguments(self, parser):
        parser.add_argument(
            "--doc",
            type=int,
            default=None,
            help="ID конкретного документа (по умолчанию все ready)",
        )

    def handle(self, *args, **options):
        doc_id = options["doc"]

        if doc_id:
            docs = Document.objects.filter(pk=doc_id)
        else:
            docs = Document.objects.filter(status=Document.Status.READY)

        total = docs.count()
        self.stdout.write(f"К обработке: {total}")

        updated = 0
        errors = 0

        for doc in docs:
            try:
                count = index_document(doc)
                updated += 1
                self.stdout.write(f"  [{doc.id}] {doc.original_filename}: {count} полей")
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(f"  [{doc.id}] ошибка: {exc}"))

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Готово. Обновлено: {updated}, ошибок: {errors}")
        )
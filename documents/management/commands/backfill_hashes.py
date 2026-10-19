"""Считает SHA-256 для уже загруженных файлов.

Нужно один раз после появления поля content_hash: у старых документов
хеш пустой, поэтому защита от повторной загрузки их не видит.
"""

import hashlib

from django.core.management.base import BaseCommand

from documents.models import Document


class Command(BaseCommand):
    help = "Заполняет content_hash у документов, где он пуст"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Пересчитать у всех")

    def handle(self, *args, **options):
        queryset = Document.objects.exclude(file="")
        if not options["all"]:
            queryset = queryset.filter(content_hash="")

        updated = 0
        for document in queryset.iterator():
            try:
                with document.file.open("rb") as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
            except OSError as exc:
                self.stdout.write(self.style.WARNING(f"{document.pk}: {exc}"))
                continue

            document.content_hash = digest
            document.save(update_fields=["content_hash"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Обработано документов: {updated}"))

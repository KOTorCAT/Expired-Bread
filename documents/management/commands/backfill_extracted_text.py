"""Разовая команда: заполнить extracted_text и search_text."""

from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services.extractors import ExtractionError, extract_text
from documents.services.normalization import build_search_text


class Command(BaseCommand):
    help = "Доганяет extracted_text и search_text для существующих документов"

    def handle(self, *args, **options):
        docs = Document.objects.all()
        total = docs.count()
        self.stdout.write(f"Проверяю документов: {total}")

        updated = 0
        errors = 0

        for doc in docs:
            fields_to_update = []

            if not doc.extracted_text:
                try:
                    text = extract_text(doc.file.path)
                    doc.extracted_text = text
                    fields_to_update.append("extracted_text")
                except ExtractionError as exc:
                    errors += 1
                    self.stdout.write(
                        self.style.WARNING(f"  [{doc.id}] текст не извлечён: {exc}")
                    )

            new_search = build_search_text(
                title=doc.title,
                description=doc.description,
                original_filename=doc.original_filename,
                extracted_text=doc.extracted_text,
            )
            if new_search != doc.search_text:
                doc.search_text = new_search
                fields_to_update.append("search_text")

            if fields_to_update:
                fields_to_update.append("updated_at")
                doc.save(update_fields=fields_to_update)
                updated += 1
                self.stdout.write(
                    f"  [{doc.id}] {doc.original_filename}: "
                    f"обновлено ({', '.join(fields_to_update)})"
                )

        self.stdout.write(
            self.style.SUCCESS(f"Готово. Обновлено: {updated}, ошибок: {errors}")
        )
"""Маскирует персональные данные в хранимых документах."""
from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services.pii import contains_pii, mask_pii


class Command(BaseCommand):
    help = "Заменяет PII (телефон, email, ИНН, СНИЛС, паспорт) заглушкой в документах"

    def add_arguments(self, parser):
        parser.add_argument("--doc", type=int, help="id документа")
        parser.add_argument("--all", action="store_true", help="все документы")
        parser.add_argument("--dry-run", action="store_true", help="только показать")

    def handle(self, *args, **options):
        queryset = Document.objects.all()
        if options["doc"]:
            queryset = queryset.filter(pk=options["doc"])
        elif not options["all"]:
            self.stderr.write("Укажите --doc ID или --all")
            return

        changed = 0
        for document in queryset:
            fields = {
                "extracted_text": document.extracted_text,
                "description": document.description,
                "search_text": document.search_text,
            }
            hits = sum(1 for value in fields.values() if contains_pii(value))
            if not hits:
                continue

            changed += 1
            self.stdout.write(
                f"#{document.pk} {document.original_filename}: PII в {hits} поле(ях)"
            )
            if not options["dry_run"]:
                document.extracted_text = mask_pii(document.extracted_text)
                document.description = mask_pii(document.description)
                document.search_text = mask_pii(document.search_text)
                document.save(
                    update_fields=[
                        "extracted_text",
                        "description",
                        "search_text",
                        "updated_at",
                    ]
                )

        self.stdout.write(f"Затронуто документов: {changed}")

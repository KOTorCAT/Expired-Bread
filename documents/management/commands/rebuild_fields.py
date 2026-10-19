"""Пересборка нормализованных полей DocumentField из Document.data.

Нужна после изменения правил нормализации: сами данные в Document.data
не меняются, а поиск по полям должен увидеть новые значения.

    python manage.py rebuild_fields              # все ready-документы
    python manage.py rebuild_fields --all        # вообще все
    python manage.py rebuild_fields --doc 42     # один документ
"""

from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services.field_index import index_document


class Command(BaseCommand):
    help = "Пересобирает DocumentField из Document.data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--doc",
            type=int,
            default=0,
            help="ID одного документа (по умолчанию — все подходящие)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Включить документы не в статусе ready",
        )

    def handle(self, *args, **options):
        queryset = Document.objects.all()

        if options["doc"]:
            queryset = queryset.filter(pk=options["doc"])
        elif not options["all"]:
            queryset = queryset.filter(status=Document.Status.READY)

        documents = list(queryset)
        if not documents:
            self.stdout.write(self.style.WARNING("Документов для индексации нет."))
            return

        total_fields = 0
        for document in documents:
            try:
                total_fields += index_document(document)
            except Exception as exc:  # noqa: BLE001 — не роняем весь пересчёт
                self.stdout.write(
                    self.style.ERROR(f"Документ {document.pk}: ошибка — {exc}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Обработано документов: {len(documents)}, "
                f"полей записано: {total_fields}"
            )
        )

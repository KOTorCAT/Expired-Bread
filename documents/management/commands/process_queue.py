"""Дообрабатывает документы, оставшиеся в очереди после перезапуска.

Обычно обработка стартует сразу из view в фоновом потоке. Если сервер
перезапустили посреди работы, часть документов остаётся в статусе
`uploaded` (не взяты) или `processing` (зависли). Эта команда запускает
их заново — синхронно, как воркер.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from documents.models import Document
from documents.services.background import STUCK_AFTER_MINUTES
from documents.services.document_service import process_document


class Command(BaseCommand):
    help = "Обрабатывает документы, оставшиеся в очереди после перезапуска"

    def handle(self, *args, **options):
        stale = Document.objects.filter(
            status=Document.Status.PROCESSING,
            updated_at__lte=timezone.now() - timedelta(minutes=STUCK_AFTER_MINUTES),
        )
        queued = Document.objects.filter(status=Document.Status.UPLOADED)

        total = stale.count() + queued.count()
        for document in list(stale) + list(queued):
            claimed = Document.objects.filter(
                pk=document.pk,
                status__in=(Document.Status.UPLOADED, Document.Status.PROCESSING),
            ).update(
                status=Document.Status.PROCESSING,
                updated_at=timezone.now(),
            )
            if not claimed:
                continue

            try:
                process_document(document.pk)
            except Exception as exc:  # noqa: BLE001 — воркер не должен падать
                Document.objects.filter(pk=document.pk).update(
                    status=Document.Status.FAILED,
                    error_message=str(exc)[:500],
                    updated_at=timezone.now(),
                )
                self.stderr.write(f"#{document.pk}: {exc}")
            else:
                self.stdout.write(f"#{document.pk}: обработан")

        self.stdout.write(f"Взято в работу: {total}")

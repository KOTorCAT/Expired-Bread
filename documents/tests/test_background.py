"""Тесты фоновой обработки (без реальных потоков и без модели)."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from documents.models import Document
from documents.services import background


class FakeThread:
    """Поток, который выполняется сразу и синхронно.

    Так тесты не зависят от планировщика и не «плавают».
    """

    instances = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        FakeThread.instances.append(self)

    def start(self) -> None:
        if self.target is not None:
            self.target(*self.args)


class StartProcessingTestCase(TestCase):
    def setUp(self):
        FakeThread.instances = []

    def _document(self, **kwargs) -> Document:
        defaults = {"original_filename": "doc.docx"}
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_marks_document_processing(self):
        document = self._document()
        with patch.object(background, "Thread", FakeThread), patch.object(
            background, "_process_in_thread"
        ):
            self.assertTrue(background.start_processing(document.pk))

        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.PROCESSING)

    def test_starts_thread_with_document_id(self):
        document = self._document()
        with patch.object(background, "Thread", FakeThread), patch.object(
            background, "_process_in_thread"
        ) as runner:
            background.start_processing(document.pk)

        self.assertEqual(len(FakeThread.instances), 1)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0], document.pk)

    def test_thread_is_daemon(self):
        document = self._document()
        with patch.object(background, "Thread", FakeThread), patch.object(
            background, "_process_in_thread"
        ):
            background.start_processing(document.pk)

        self.assertTrue(FakeThread.instances[0].daemon)

    def test_second_call_is_ignored_while_processing(self):
        document = self._document(status=Document.Status.PROCESSING)
        with patch.object(background, "Thread", FakeThread), patch.object(
            background, "_process_in_thread"
        ) as runner:
            self.assertFalse(background.start_processing(document.pk))

        runner.assert_not_called()

    def test_failed_document_can_be_restarted(self):
        document = self._document(status=Document.Status.FAILED, error_message="старое")
        with patch.object(background, "Thread", FakeThread), patch.object(
            background, "_process_in_thread"
        ):
            self.assertTrue(background.start_processing(document.pk))

        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.PROCESSING)
        self.assertEqual(document.error_message, "")

    def test_ready_document_can_be_reprocessed(self):
        document = self._document(status=Document.Status.READY)
        with patch.object(background, "Thread", FakeThread), patch.object(
            background, "_process_in_thread"
        ):
            self.assertTrue(background.start_processing(document.pk))


class ProcessInThreadTestCase(TestCase):
    def test_calls_process_document(self):
        with patch("documents.services.document_service.process_document") as process, patch.object(
            background, "close_old_connections"
        ), patch.object(background, "connection"):
            background._process_in_thread(42, config=None)

        process.assert_called_once()
        self.assertEqual(process.call_args.args[0], 42)

    def test_error_marks_document_failed(self):
        document = Document.objects.create(
            original_filename="doc.docx", status=Document.Status.PROCESSING
        )

        with patch(
            "documents.services.document_service.process_document",
            side_effect=RuntimeError("сломалось"),
        ), patch.object(background, "close_old_connections"), patch.object(
            background, "connection"
        ):
            background._process_in_thread(document.pk, config=None)

        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.FAILED)
        self.assertIn("Неожиданная ошибка", document.error_message)


class RecoverStuckTestCase(TestCase):
    def _stuck(self, minutes: int) -> Document:
        document = Document.objects.create(
            original_filename="doc.docx", status=Document.Status.PROCESSING
        )
        # update() не трогает auto_now, поэтому время ставим явно
        Document.objects.filter(pk=document.pk).update(
            updated_at=timezone.now() - timedelta(minutes=minutes)
        )
        document.refresh_from_db()
        return document

    def test_old_processing_becomes_failed(self):
        document = self._stuck(background.STUCK_AFTER_MINUTES + 1)

        self.assertTrue(background.recover_stuck(document))
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.FAILED)
        self.assertIn("превышено время ожидания", document.error_message)

    def test_recent_processing_is_untouched(self):
        document = self._stuck(1)

        self.assertFalse(background.recover_stuck(document))
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.PROCESSING)

    def test_other_statuses_are_untouched(self):
        document = Document.objects.create(
            original_filename="doc.docx", status=Document.Status.READY
        )
        self.assertFalse(background.recover_stuck(document))

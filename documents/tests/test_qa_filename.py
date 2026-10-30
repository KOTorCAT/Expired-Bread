"""Контекст Q&A содержит имя файла — чтобы работал вопрос «как называется файл»."""
from django.test import TestCase

from documents.models import Document
from documents.services.qa_service import _build_context


class QaFilenameContextTestCase(TestCase):
    def test_context_contains_original_filename(self):
        document = Document.objects.create(
            original_filename="аываываываываыв.docx",
            title="Заметка о Гае Ричи",
            status=Document.Status.READY,
            extracted_text="Фильмы Гая Ричи: Lock, Stock and Two Smoking Barrels.",
        )

        context = _build_context([document], "как называется файл про гая ричи")

        self.assertIn("Имя файла:", context)
        self.assertIn("аываываываываыв.docx", context)

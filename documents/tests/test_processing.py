import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from docx import Document as DocxDocument

from documents.models import Document
from documents.services.document_service import process_document


class ProcessDocumentTestCase(TestCase):
    def setUp(self):
        # Создаём временную папку и делаем её MEDIA_ROOT на время теста
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

        self.override = override_settings(MEDIA_ROOT=self.tmp)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _create_document(self) -> Document:
        # Кладём настоящий DOCX прямо в MEDIA_ROOT, чтобы document.file.path
        # указывал на существующий файл и не выходил за пределы MEDIA_ROOT.
        path = self.tmp / "sample.docx"
        docx = DocxDocument()
        docx.add_paragraph("Это тестовый документ.")
        docx.save(path)

        return Document.objects.create(
            file="sample.docx",
            original_filename="sample.docx",
        )

    @patch("documents.services.document_service.analyze_document")
    def test_process_document_success(self, mock_analyze):
        mock_analyze.return_value = {
            "document_type": "report",
            "title": "Тестовый отчёт",
            "description": "Описание",
            "extra": "полезное поле",
        }
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertEqual(result.status, Document.Status.REVIEW)
        self.assertEqual(result.title, "Тестовый отчёт")
        self.assertEqual(result.document_type, "report")
        self.assertEqual(result.data["extra"], "полезное поле")

    @patch("documents.services.document_service.analyze_document")
    def test_process_document_llm_error(self, mock_analyze):
        from documents.services.llm import LLMError
        mock_analyze.side_effect = LLMError("Модель недоступна")
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertEqual(result.status, Document.Status.FAILED)
        self.assertIn("Модель недоступна", result.error_message)

    @patch("documents.services.document_service.analyze_document_vision")
    @patch("documents.services.document_service.extract_text")
    def test_process_document_falls_back_to_vision(self, mock_extract, mock_vision):
        from documents.services.extractors import ExtractionError
        mock_extract.side_effect = ExtractionError("Скан без текста")
        mock_vision.return_value = {
            "document_type": "other",
            "title": "Скан",
            "description": "",
        }
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertEqual(result.status, Document.Status.REVIEW)
        mock_vision.assert_called_once()
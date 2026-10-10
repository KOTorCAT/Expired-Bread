import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from docx import Document as DocxDocument

from documents.models import Document
from documents.services.document_service import process_document
from documents.services.pipeline import PipelineResult


class ProcessDocumentTestCase(TestCase):
    """Тесты process_document. run_pipeline мокается."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

        self.override = override_settings(MEDIA_ROOT=self.tmp)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _create_document(self) -> Document:
        path = self.tmp / "sample.docx"
        docx = DocxDocument()
        docx.add_paragraph("Это тестовый документ.")
        docx.save(path)

        return Document.objects.create(
            file="sample.docx",
            original_filename="sample.docx",
        )

    def _make_result(
        self,
        doc_type: str = "price_list",
        data: dict | None = None,
        warnings: list | None = None,
    ) -> PipelineResult:
        return PipelineResult(
            doc_type=doc_type,
            data=data or {
                "title": "Тестовый прайс",
                "description": "Описание",
                "items": [{"name": "Товар", "price": "100 руб."}],
            },
            data_raw={"title": "Тестовый прайс"},
            warnings=warnings or [],
            meta={"model": "GigaChat-2-Max", "usage": {"extract": {"prompt_tokens": 100}}},
        )

    @patch("documents.services.document_service.run_pipeline")
    def test_process_document_success(self, mock_run_pipeline):
        mock_run_pipeline.return_value = self._make_result()
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertEqual(result.status, Document.Status.REVIEW)
        self.assertEqual(result.title, "Тестовый прайс")
        self.assertEqual(result.document_type, "price_list")
        self.assertEqual(result.description, "Описание")
        self.assertGreater(len(result.search_text), 0)
        self.assertEqual(result.processing_meta.get("model"), "GigaChat-2-Max")

    @patch("documents.services.document_service.run_pipeline")
    def test_process_document_saves_data_and_raw(self, mock_run_pipeline):
        mock_run_pipeline.return_value = self._make_result()
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertIn("title", result.data)
        self.assertIn("title", result.data_raw)
        self.assertEqual(result.processing_meta.get("model"), "GigaChat-2-Max")

    @patch("documents.services.document_service.run_pipeline")
    def test_process_document_llm_error(self, mock_run_pipeline):
        from documents.services.llm import LLMError
        mock_run_pipeline.side_effect = LLMError("Модель недоступна")
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertEqual(result.status, Document.Status.FAILED)
        self.assertIn("Модель недоступна", result.error_message)

    @patch("documents.services.document_service.run_pipeline")
    def test_process_document_extraction_error(self, mock_run_pipeline):
        from documents.services.extractors import ExtractionError
        mock_run_pipeline.side_effect = ExtractionError("Скан без текста")
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertEqual(result.status, Document.Status.FAILED)
        self.assertIn("Скан без текста", result.error_message)

    @patch("documents.services.document_service.run_pipeline")
    def test_process_document_fills_search_text(self, mock_run_pipeline):
        mock_run_pipeline.return_value = self._make_result(
            data={
                "title": "Особый прайс",
                "description": "Уникальное описание",
                "items": [],
            },
        )
        doc = self._create_document()

        result = process_document(doc.id)

        self.assertIn("особый прайс", result.search_text.lower())
        self.assertIn("уникальное описание", result.search_text.lower())

    @patch("documents.services.document_service.run_pipeline")
    def test_process_document_calls_pipeline_with_config(self, mock_run_pipeline):
        from documents.services.pipeline import PipelineConfig

        mock_run_pipeline.return_value = self._make_result()
        doc = self._create_document()

        config = PipelineConfig(doc_type_hint="price_list", use_cache=False)
        process_document(doc.id, config=config)

        mock_run_pipeline.assert_called_once()
        call_kwargs = mock_run_pipeline.call_args.kwargs
        self.assertEqual(call_kwargs["config"].doc_type_hint, "price_list")


class ConfirmDocumentTestCase(TestCase):
    """Тесты confirm_document — пересчёт search_text."""

    def test_confirm_document_updates_fields(self):
        doc = Document.objects.create(
            original_filename="x.pdf",
            title="Старое",
            description="Старое описание",
            document_type="other",
            status=Document.Status.REVIEW,
            extracted_text="какой-то текст",
        )

        from documents.services.document_service import confirm_document

        result = confirm_document(doc.id, {
            "title": "Новое название",
            "document_type": "price_list",
            "description": "Новое описание",
        })

        self.assertEqual(result.title, "Новое название")
        self.assertEqual(result.document_type, "price_list")
        self.assertEqual(result.description, "Новое описание")
        self.assertEqual(result.status, Document.Status.READY)
        self.assertIn("новое название", result.search_text.lower())
from unittest.mock import patch

from django.test import TestCase

from documents.models import Document
from documents.services.normalization import build_search_text
from documents.services.qa_service import ask


class QAServiceTestCase(TestCase):
    def setUp(self):
        self.doc = Document.objects.create(
            original_filename="resume.pdf",
            title="Резюме Смирнова",
            description="Backend-разработчик",
            document_type="resume",
            status=Document.Status.READY,
            extracted_text=(
                "Навыки: Python, Django, PostgreSQL. "
                "Опыт работы: 3 года коммерческой разработки."
            ),
        )
        # search_text — производное поле, заполняем вручную для тестов,
        # где документ создан напрямую, а не через process_document
        self.doc.search_text = build_search_text(
            title=self.doc.title,
            description=self.doc.description,
            original_filename=self.doc.original_filename,
            extracted_text=self.doc.extracted_text,
        )
        self.doc.save(update_fields=["search_text"])

    @patch("documents.services.qa_service.answer_question")
    def test_ask_found(self, mock_answer):
        mock_answer.return_value = {
            "found": True,
            "answer": "В резюме указаны Python, Django, PostgreSQL.",
        }
        result = ask("какие технологии Python указаны в резюме?")
        self.assertTrue(result["found"])
        self.assertIn("Python", result["answer"])
        self.assertEqual(result["sources"], [self.doc])
        mock_answer.assert_called_once()

    @patch("documents.services.qa_service.answer_question")
    def test_ask_not_found_by_llm(self, mock_answer):
        mock_answer.return_value = {
            "found": False,
            "answer": "В документах нет такой информации.",
        }
        result = ask("есть ли у него сертификаты Kubernetes?")
        self.assertFalse(result["found"])
        self.assertEqual(result["sources"], [])

    @patch("documents.services.qa_service.answer_question")
    def test_ask_no_documents(self, mock_answer):
        result = ask("абракадабра зюйдвест ксилофон")
        self.assertFalse(result["found"])
        self.assertEqual(result["sources"], [])
        mock_answer.assert_not_called()

    def test_ask_empty_question(self):
        result = ask("")
        self.assertFalse(result["found"])

    def test_ask_only_failed_documents_not_searched(self):
        Document.objects.all().update(status=Document.Status.REVIEW)
        result = ask("Python")
        self.assertFalse(result["found"])
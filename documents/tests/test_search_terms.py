"""Тесты основы слова в Q&A: падежи не должны ломать поиск."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from documents.models import Document
from documents.services.normalization import build_search_text
from documents.services.qa_service import _question_terms, _stem, ask


class StemTestCase(SimpleTestCase):
    def test_inflected_word_is_stemmed(self):
        self.assertEqual(_question_terms("что есть в чеке"), ["чек"])

    def test_plain_word_stays(self):
        self.assertIn("лук", _question_terms("сколько стоит лук"))

    def test_stem_keeps_at_least_three_letters(self):
        self.assertEqual(_stem("чеке"), "чек")
        self.assertEqual(_stem("цене"), "цен")

    def test_stem_does_not_shorten_short_words(self):
        self.assertEqual(_stem("ее"), "ее")
        self.assertEqual(_stem("лук"), "лук")

    def test_stop_words_still_dropped(self):
        self.assertEqual(_question_terms("что где когда"), [])


class InflectedQuestionTestCase(TestCase):
    def setUp(self):
        self.document = Document.objects.create(
            original_filename="receipt.docx",
            title="Кассовый чек",
            document_type="receipt",
            status=Document.Status.READY,
            extracted_text="Лук репчатый 1 кг 79.90",
        )
        self.document.search_text = build_search_text(
            title=self.document.title,
            description="",
            original_filename=self.document.original_filename,
            extracted_text=self.document.extracted_text,
        )
        self.document.save(update_fields=["search_text"])

    @patch("documents.services.qa_service.answer_question")
    def test_question_in_prepositional_case_finds_receipt(self, mock_answer):
        mock_answer.return_value = {"found": True, "answer": "Чек на 79.90"}

        result = ask("что есть в чеке")

        self.assertTrue(result["found"])
        mock_answer.assert_called_once()

    @patch("documents.services.qa_service.answer_question")
    def test_unrelated_question_finds_nothing(self, mock_answer):
        result = ask("абракадабра зюйдвест")

        self.assertFalse(result["found"])
        mock_answer.assert_not_called()

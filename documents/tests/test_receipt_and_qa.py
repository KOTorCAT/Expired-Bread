"""Тесты типа «чек», даты со временем и значимых слов в Q&A."""

from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from documents.models import Document
from documents.schemas import REGISTRY, all_types, get_label, get_schema
from documents.services.normalization import FIELD_KINDS, build_search_text, parse_date
from documents.services.qa_service import (
    MIN_TERM_LENGTH,
    STOP_WORDS,
    _question_terms,
    ask,
)


class ReceiptSchemaTestCase(SimpleTestCase):
    def test_receipt_is_registered(self):
        self.assertIn("receipt", REGISTRY)
        self.assertIn("receipt", all_types())
        self.assertEqual(get_label("receipt"), "Чек")

    def test_minimal_receipt_is_valid(self):
        data = get_schema("receipt").model_validate({"title": "Чек № 42"})
        self.assertEqual(data.title, "Чек № 42")
        self.assertEqual(data.items, [])

    def test_receipt_with_items(self):
        data = get_schema("receipt").model_validate({
            "title": "Чек",
            "seller": "ООО Лук",
            "total": "1 250,00 руб.",
            "items": [
                {"name": "Лук репчатый", "quantity": "1", "price": "79.90",
                 "total": "79.90"},
            ],
        })
        self.assertEqual(len(data.items), 1)
        self.assertEqual(data.items[0].name, "Лук репчатый")

    def test_field_kinds_are_typed(self):
        kinds = FIELD_KINDS["receipt"]
        self.assertEqual(kinds["total"], "money")
        self.assertEqual(kinds["vat"], "money")
        self.assertEqual(kinds["date"], "date")
        self.assertEqual(kinds["quantity"], "number")


class ParseDateWithTimeTestCase(SimpleTestCase):
    def test_receipt_datetime(self):
        self.assertEqual(parse_date("19.10.2026 18:07"), date(2026, 10, 19))

    def test_iso_with_space_time(self):
        self.assertEqual(parse_date("2026-10-19 18:07:45"), date(2026, 10, 19))

    def test_iso_with_t_separator(self):
        self.assertEqual(parse_date("2026-10-19T18:07:00Z"), date(2026, 10, 19))

    def test_plain_date_is_unchanged(self):
        self.assertEqual(parse_date("19.10.2026"), date(2026, 10, 19))

    def test_russian_month_is_unchanged(self):
        self.assertEqual(parse_date("15 марта 2025"), date(2025, 3, 15))


class QuestionTermsTestCase(SimpleTestCase):
    def test_short_meaningful_word_is_kept(self):
        self.assertIn("лук", _question_terms("сколько стоит лук"))

    def test_stop_words_are_dropped(self):
        terms = _question_terms("что где когда")
        for word in ("что", "где", "когда"):
            self.assertNotIn(word, terms)

    def test_single_letters_are_dropped(self):
        self.assertEqual(_question_terms("а и в"), [])

    def test_constants_are_sane(self):
        self.assertEqual(MIN_TERM_LENGTH, 3)
        self.assertIn("что", STOP_WORDS)


class ReceiptQuestionTestCase(TestCase):
    def setUp(self):
        self.doc = Document.objects.create(
            original_filename="receipt.pdf",
            title="Чек № 42",
            document_type="receipt",
            status=Document.Status.READY,
            extracted_text="Лук репчатый 1 кг 79.90",
        )
        self.doc.search_text = build_search_text(
            title=self.doc.title,
            description="",
            original_filename=self.doc.original_filename,
            extracted_text=self.doc.extracted_text,
        )
        self.doc.save(update_fields=["search_text"])

    @patch("documents.services.qa_service.answer_question")
    def test_short_word_question_finds_receipt(self, mock_answer):
        mock_answer.return_value = {"found": True, "answer": "79,90"}

        result = ask("сколько стоит лук")

        self.assertTrue(result["found"])
        mock_answer.assert_called_once()

    @patch("documents.services.qa_service.answer_question")
    def test_irrelevant_question_finds_nothing(self, mock_answer):
        result = ask("абракадабра зюйдвест")

        self.assertFalse(result["found"])
        mock_answer.assert_not_called()

"""Сложные и длинные документы датасета."""

from django.test import SimpleTestCase

from documents.management.commands._dataset_generators import (
    generate_price_list,
    generate_specification,
    generate_technical_document,
)


class PriceListVariantsTestCase(SimpleTestCase):
    def test_usual_document_has_units(self):
        truth, _, _ = generate_price_list(42001)

        self.assertTrue(truth["items"])
        for item in truth["items"]:
            self.assertIn("unit", item)
            self.assertIn("sku", item)

    def test_hard_document_has_no_units(self):
        truth, _, _ = generate_price_list(42001, hard=True)

        for item in truth["items"]:
            self.assertNotIn("unit", item)
            self.assertIn("sku", item)

    def test_long_document_has_many_items(self):
        truth, _, _ = generate_price_list(42001, long=True)

        self.assertGreaterEqual(len(truth["items"]), 45)

    def test_hard_and_long_do_not_crash(self):
        truth, docx_bytes, pdf_bytes = generate_price_list(
            42001, hard=True, long=True
        )

        self.assertTrue(truth["items"])
        self.assertTrue(docx_bytes)
        self.assertTrue(pdf_bytes)


class SpecificationVariantsTestCase(SimpleTestCase):
    def test_usual_document_has_storage(self):
        truth, _, _ = generate_specification(42001)

        self.assertIn("storage", truth)
        self.assertIn("Цвет", truth["characteristics"])

    def test_hard_document_drops_missing_fields(self):
        truth, _, _ = generate_specification(42001, hard=True)

        # Этих полей в документе нет, значит и в эталоне быть не должно:
        # иначе модель обязана «угадать» отсутствующее значение.
        self.assertNotIn("storage", truth)
        self.assertNotIn("Цвет", truth["characteristics"])

    def test_long_document_has_many_characteristics(self):
        truth, _, _ = generate_specification(42001, long=True)

        self.assertGreaterEqual(len(truth["characteristics"]), 60)

    def test_long_document_is_longer(self):
        usual, _, _ = generate_specification(42001)
        long_doc, _, _ = generate_specification(42001, long=True)

        self.assertGreater(len(long_doc["characteristics"]), len(usual["characteristics"]))


class TechnicalDocumentVariantsTestCase(SimpleTestCase):
    def test_usual_document_has_requirements(self):
        truth, _, _ = generate_technical_document(42001)

        self.assertTrue(truth["requirements"])

    def test_hard_document_has_no_requirements(self):
        truth, _, _ = generate_technical_document(42001, hard=True)

        self.assertEqual(truth["requirements"], [])

    def test_long_document_has_long_sections(self):
        usual, _, _ = generate_technical_document(42001)
        long_doc, _, _ = generate_technical_document(42001, long=True)

        usual_length = sum(len(s["content"]) for s in usual["sections"])
        long_length = sum(len(s["content"]) for s in long_doc["sections"])

        self.assertGreater(long_length, usual_length * 3)


class DeterminismTestCase(SimpleTestCase):
    def test_same_seed_gives_same_document(self):
        first_truth, first_docx, _ = generate_price_list(42001, hard=True)
        second_truth, second_docx, _ = generate_price_list(42001, hard=True)

        self.assertEqual(first_truth, second_truth)
        self.assertEqual(first_docx, second_docx)

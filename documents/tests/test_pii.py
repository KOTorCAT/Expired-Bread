"""Тесты маскирования персональных данных."""
from django.test import SimpleTestCase

from documents.services.pii import contains_pii, mask_pii


class PiiMaskingTestCase(SimpleTestCase):
    def test_phone_is_masked(self):
        masked = mask_pii("Звоните +7 900 123-45-67")
        self.assertIn("***", masked)
        self.assertNotIn("900", masked)

    def test_email_is_masked(self):
        self.assertEqual(mask_pii("почта: ivan@example.com"), "почта: ***")

    def test_inn_is_masked(self):
        self.assertEqual(mask_pii("ИНН 7701234567"), "ИНН ***")

    def test_plain_text_is_untouched(self):
        text = "Лук репчатый 1 кг 79.90"
        self.assertEqual(mask_pii(text), text)

    def test_contains_pii(self):
        self.assertTrue(contains_pii("телефон 8-900-123-45-67"))
        self.assertFalse(contains_pii("Лук репчатый 1 кг 79.90"))

"""Тесты правил сравнения с эталоном (этап 7).

Правила проверяются без модели и без БД: на входе два словаря.
"""

from django.test import SimpleTestCase

from documents.evaluation.matching import (
    compare_data,
    compare_maps,
    field_kind,
    flatten,
    normalize_value,
)


class FlattenTestCase(SimpleTestCase):
    def test_scalars_and_lists_get_paths(self):
        data = {
            "title": "Прайс",
            "items": [
                {"name": "Товар A", "price": "100 руб."},
                {"name": "Товар B", "price": "200 руб."},
            ],
        }
        flat = flatten("price_list", data)
        self.assertIn("title", flat)
        self.assertIn("items[0].name", flat)
        self.assertIn("items[1].price", flat)

    def test_empty_values_are_ignored(self):
        flat = flatten("specification", {"title": "", "price": None, "ram": "16 ГБ"})
        self.assertNotIn("title", flat)
        self.assertNotIn("price", flat)
        self.assertIn("ram", flat)

    def test_nested_dict_is_flattened(self):
        flat = flatten("specification", {"characteristics": {"ОЗУ": "16 ГБ"}})
        self.assertIn("characteristics.ОЗУ", flat)

    def test_non_dict_returns_empty(self):
        self.assertEqual(flatten("price_list", None), {})   # type: ignore[arg-type]

    def test_kind_is_taken_from_registry(self):
        self.assertEqual(field_kind("specification", "price"), "money")
        self.assertEqual(field_kind("specification", "ram"), "memory")
        self.assertEqual(field_kind("specification", "title"), "text")
        # Неизвестное поле — текст
        self.assertEqual(field_kind("specification", "нет_такого"), "text")


class NormalizeValueTestCase(SimpleTestCase):
    def test_money(self):
        value = normalize_value("money", "89 990 руб.")
        self.assertEqual(value.num, 89990.0)
        self.assertEqual(value.unit, "RUB")

    def test_memory(self):
        value = normalize_value("memory", "1 ТБ")
        self.assertEqual(value.num, 1000.0)
        self.assertEqual(value.unit, "GB")

    def test_date(self):
        value = normalize_value("date", "15.03.2025")
        self.assertEqual(value.text, "2025-03-15")
        self.assertIsNotNone(value.num)

    def test_garbage_has_no_number(self):
        value = normalize_value("money", "по запросу")
        self.assertIsNone(value.num)


class CompareTestCase(SimpleTestCase):
    def _status(self, doc_type, expected, actual):
        matches = compare_data(doc_type, expected, actual)
        return [m for m in matches]

    def test_exact_text_match(self):
        matches = self._status(
            "specification",
            {"title": "Ноутбук"},
            {"title": "Ноутбук"},
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].status, "tp")

    def test_fuzzy_text_match_inside_threshold(self):
        matches = self._status(
            "specification",
            {"title": "Прайс-лист"},
            {"title": "Прайс лист"},
        )
        self.assertEqual(matches[0].status, "tp_fuzzy")

    def test_text_mismatch_is_wrong(self):
        matches = self._status(
            "specification",
            {"title": "Ноутбук ASUS"},
            {"title": "Холодильник Bosch"},
        )
        self.assertEqual(matches[0].status, "wrong")
        self.assertEqual(matches[0].category, "wrong_value")

    def test_money_without_currency_on_one_side_matches(self):
        matches = self._status(
            "specification",
            {"price": "89 990 руб."},
            {"price": "89990"},
        )
        self.assertEqual(matches[0].status, "tp")

    def test_money_different_value_is_wrong(self):
        matches = self._status(
            "specification",
            {"price": "89 990 руб."},
            {"price": "99 990 руб."},
        )
        self.assertEqual(matches[0].status, "wrong")

    def test_memory_units_are_normalized(self):
        matches = self._status(
            "specification",
            {"ram": "16 ГБ"},
            {"ram": "16GB"},
        )
        self.assertEqual(matches[0].status, "tp")

    def test_memory_terabyte_convention(self):
        """1 ТБ = 1000 ГБ — договорённость из normalization.py."""
        matches = self._status(
            "specification",
            {"storage": "1 ТБ"},
            {"storage": "1000 ГБ"},
        )
        self.assertEqual(matches[0].status, "tp")

    def test_date_formats_are_equivalent(self):
        matches = self._status(
            "specification",
            {"date": "15.03.2025"},
            {"date": "2025-03-15"},
        )
        self.assertEqual(matches[0].status, "tp")

    def test_missing_field_is_false_negative(self):
        matches = self._status(
            "specification",
            {"price": "100 руб."},
            {"title": "Без цены"},
        )
        by_path = {m.path: m for m in matches}
        self.assertEqual(by_path["price"].status, "fn")
        self.assertEqual(by_path["price"].category, "missing")

    def test_extra_field_is_hallucination(self):
        matches = self._status(
            "specification",
            {"title": "Документ"},
            {"title": "Документ", "price": "100 руб."},
        )
        by_path = {m.path: m for m in matches}
        self.assertEqual(by_path["price"].status, "fp")
        self.assertEqual(by_path["price"].category, "hallucination")

    def test_unparsable_but_equal_text_is_format_error(self):
        matches = self._status(
            "specification",
            {"price": "по запросу"},
            {"price": "по запросу"},
        )
        self.assertEqual(matches[0].status, "format")
        self.assertEqual(matches[0].category, "format")

    def test_list_positions_are_compared_separately(self):
        matches = self._status(
            "price_list",
            {
                "items": [
                    {"name": "A", "price": "100"},
                    {"name": "B", "price": "200"},
                ],
            },
            {
                "items": [
                    {"name": "A", "price": "100"},
                    {"name": "B", "price": "999"},
                ],
            },
        )
        by_path = {m.path: m for m in matches}
        self.assertEqual(by_path["items[0].price"].status, "tp")
        self.assertEqual(by_path["items[1].price"].status, "wrong")

    def test_compare_maps_accepts_flattened_input(self):
        expected = flatten("specification", {"price": "100 руб."})
        actual = flatten("specification", {"price": "100"})
        matches = compare_maps(expected, actual)
        self.assertEqual(matches[0].status, "tp")

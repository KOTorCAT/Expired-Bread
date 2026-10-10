from datetime import date

from django.test import SimpleTestCase

from documents.services.normalization import (
    FieldValue,
    build_fields,
    normalize_key,
    normalize_query,
    normalize_text,
    parse_date,
    parse_number,
    parse_quantity,
)


class ParseNumberTestCase(SimpleTestCase):
    """Табличный тест на 30+ форматов."""

    CASES = [
        # (вход, ожидание)
        ("89990", 89990.0),
        ("89 990", 89990.0),
        ("89 990,50", 89990.5),
        ("89 990.50", 89990.5),
        ("1 299,50", 1299.5),
        ("1.299,50", 1299.5),
        ("1,299.50", 1299.5),
        ("1,500", 1500.0),         # три цифры после запятой = тысячи
        ("1.500", 1500.0),
        ("0,5", 0.5),              # 0 перед запятой = десятичная
        ("0,500", 0.5),
        ("0.5", 0.5),
        ("0.500", 0.5),
        (",500", 500.0),           # без head, три цифры = тысячи
        ("16", 16.0),
        ("16.5", 16.5),
        ("16,5", 16.5),
        ("1000000", 1000000.0),
        ("1 000 000", 1000000.0),
        ("999", 999.0),
        ("0", 0.0),
        # Мусор
        ("abc", None),
        ("", None),
        ("   ", None),
        ("не число", None),
        (None, None),
    ]

    def test_parse_number_table(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                result = parse_number(raw)
                if expected is None:
                    self.assertIsNone(result)
                else:
                    self.assertIsNotNone(result, f"parse_number({raw!r}) вернул None")
                    self.assertAlmostEqual(result, expected, places=4)


class ParseQuantityTestCase(SimpleTestCase):
    """Разбор величин с единицами."""

    def test_memory(self):
        cases = [
            ("16 ГБ", 16.0, "GB"),
            ("16ГБ", 16.0, "GB"),
            ("16GB", 16.0, "GB"),
            ("16 Gb", 16.0, "GB"),
            ("32 ГБ", 32.0, "GB"),
            ("1 ТБ", 1000.0, "GB"),
            ("1TB", 1000.0, "GB"),
            ("512 ГБ", 512.0, "GB"),
            ("512 МБ", 0.512, "GB"),
            ("8 ГБ", 8.0, "GB"),
        ]
        for raw, expected_num, expected_unit in cases:
            with self.subTest(raw=raw):
                num, unit = parse_quantity(raw, "memory")
                self.assertAlmostEqual(num, expected_num, places=4)
                self.assertEqual(unit, expected_unit)

    def test_money(self):
        cases = [
            ("89 990 руб.", 89990.0, "RUB"),
            ("89 990 ₽", 89990.0, "RUB"),
            ("89990 рублей", 89990.0, "RUB"),
            ("100 $", 100.0, "USD"),
            ("100 USD", 100.0, "USD"),
            ("200 €", 200.0, "EUR"),
            ("200 EUR", 200.0, "EUR"),
        ]
        for raw, expected_num, expected_unit in cases:
            with self.subTest(raw=raw):
                num, unit = parse_quantity(raw, "money")
                self.assertAlmostEqual(num, expected_num, places=4)
                self.assertEqual(unit, expected_unit)

    def test_number(self):
        num, unit = parse_quantity("123", "number")
        self.assertEqual(num, 123.0)
        self.assertEqual(unit, "")

    def test_garbage(self):
        num, unit = parse_quantity("не число", "money")
        self.assertIsNone(num)
        self.assertEqual(unit, "")


class ParseDateTestCase(SimpleTestCase):
    def test_formats(self):
        cases = [
            ("15.03.2025", date(2025, 3, 15)),
            ("15/03/2025", date(2025, 3, 15)),
            ("2025-03-15", date(2025, 3, 15)),
            ("15 марта 2025", date(2025, 3, 15)),
            ("15.03.25", date(2025, 3, 15)),
            ("01.01.2000", date(2000, 1, 1)),
            ("31.12.2024", date(2024, 12, 31)),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_date(raw), expected)

    def test_garbage(self):
        for raw in ["abc", "", None, "32.13.2025"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_date(raw))


class NormalizeTextTestCase(SimpleTestCase):
    def test_basic(self):
        cases = [
            ("Резюме Ёж", "резюме еж"),
            ("  Много    пробелов  ", "много пробелов"),
            ("Цена: 100 руб.", "цена: 100 руб"),
            ("ПРИВЕТ", "привет"),
            ("", ""),
            (None, ""),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_text(raw), expected)

    def test_normalize_query_alias(self):
        self.assertEqual(
            normalize_query("Резюме Ёж"),
            normalize_text("Резюме Ёж"),
        )


class NormalizeKeyTestCase(SimpleTestCase):
    def test_synonyms(self):
        cases = [
            ("ОЗУ", "ram"),
            ("RAM", "ram"),
            ("Оперативная память", "ram"),
            ("SSD", "storage"),
            ("Накопитель", "storage"),
            ("Цена", "price"),
            ("Стоимость", "price"),
            ("Производитель", "manufacturer"),
            ("Vendor", "manufacturer"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_key(raw), expected)

    def test_slug_for_unknown(self):
        self.assertEqual(normalize_key("Что-то странное"), "что_то_странное")
        self.assertEqual(normalize_key("Процессор"), "процессор")


class BuildFieldsTestCase(SimpleTestCase):
    def test_price_list(self):
        data = {
            "title": "Прайс-лист",
            "date": "15.03.2025",
            "items": [
                {"name": "Ноутбук", "price": "89 990 руб.", "unit": "шт"},
                {"name": "Мышь", "price": "1 299,50 руб.", "unit": "шт"},
            ],
        }
        fields = build_fields("price_list", data)

        # Найдём price для индекса 0
        price0 = next(f for f in fields if f.key == "price" and f.item_index == 0)
        self.assertAlmostEqual(price0.value_num, 89990.0)
        self.assertEqual(price0.unit, "RUB")

        # И для индекса 1
        price1 = next(f for f in fields if f.key == "price" and f.item_index == 1)
        self.assertAlmostEqual(price1.value_num, 1299.5)
        self.assertEqual(price1.unit, "RUB")

        # title — item_index=None
        title = next(f for f in fields if f.key == "title")
        self.assertIsNone(title.item_index)
        self.assertEqual(title.value_text, "прайс-лист")

    def test_specification(self):
        data = {
            "title": "Спецификация",
            "product_name": "Acer Aspire 5",
            "ram": "16GB",
            "storage": "1 ТБ",
            "price": "74990 ₽",
        }
        fields = build_fields("specification", data)

        ram = next(f for f in fields if f.key == "ram")
        self.assertAlmostEqual(ram.value_num, 16.0)
        self.assertEqual(ram.unit, "GB")

        storage = next(f for f in fields if f.key == "storage")
        self.assertAlmostEqual(storage.value_num, 1000.0)
        self.assertEqual(storage.unit, "GB")

        price = next(f for f in fields if f.key == "price")
        self.assertAlmostEqual(price.value_num, 74990.0)
        self.assertEqual(price.unit, "RUB")

    def test_characteristics_dict(self):
        data = {
            "title": "Спецификация",
            "characteristics": {
                "ОЗУ": "16 ГБ",
                "Процессор": "Intel Core i5",
            },
        }
        fields = build_fields("specification", data)

        # "ОЗУ" → "ram"
        ram = next(f for f in fields if f.key == "ram")
        self.assertEqual(ram.value_text, "16 гб")

        # "Процессор" → "процессор"
        proc = next(f for f in fields if f.key == "процессор")
        self.assertEqual(proc.value_text, "intel core i5")

    def test_empty_data(self):
        self.assertEqual(build_fields("price_list", {}), [])

    def test_none_data(self):
        self.assertEqual(build_fields("price_list", None), [])

    def test_text_fields_have_no_num(self):
        data = {
            "title": "Прайс-лист",
            "vendor": "ООО Ромашка",
        }
        fields = build_fields("price_list", data)
        title = next(f for f in fields if f.key == "title")
        self.assertIsNone(title.value_num)
        self.assertEqual(title.unit, "")

    def test_date_converted(self):
        data = {"title": "Документ", "date": "15.03.2025"}
        fields = build_fields("price_list", data)
        d = next(f for f in fields if f.key == "date")
        self.assertEqual(d.value_text, "2025-03-15")
        self.assertIsNotNone(d.value_num)
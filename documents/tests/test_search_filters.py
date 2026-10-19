"""Тесты понятного конструктора фильтров."""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from documents.models import Document
from documents.presenters import build_filter_rows, describe_filter
from documents.services.field_index import index_document
from documents.services.normalization import build_search_text
from documents.services.search_service import FieldFilter, filters_from_rows

SPEC_DATA = {
    "title": "Спецификация",
    "description": "",
    "organization": "",
    "date": "15.03.2025",
    "product_name": "Ноутбук",
    "manufacturer": "ASUS",
    "category": "",
    "price": "54 990 руб.",
    "ram": "16 ГБ",
    "storage": "512 ГБ",
    "characteristics": {},
}


class FiltersFromRowsTestCase(SimpleTestCase):
    def test_builds_filter_and_normalizes_value(self):
        params = MultiValueDict({"ff": ["price"], "fo": ["lt"], "fv": ["100000"]})

        filters = filters_from_rows(params)

        self.assertEqual(len(filters), 1)
        self.assertEqual(filters[0].key, "price")
        self.assertEqual(filters[0].op, "lt")
        self.assertEqual(filters[0].value, 100000.0)

    def test_memory_value_is_normalized(self):
        params = MultiValueDict({"ff": ["ram"], "fo": ["gte"], "fv": ["16 ГБ"]})

        self.assertEqual(filters_from_rows(params)[0].value, 16.0)

    def test_empty_rows_are_skipped(self):
        params = MultiValueDict({
            "ff": ["", "price"], "fo": ["contains", "lt"], "fv": ["", "100"],
        })

        filters = filters_from_rows(params)

        self.assertEqual([item.key for item in filters], ["price"])

    def test_unknown_field_is_skipped(self):
        params = MultiValueDict({"ff": ["нет_такого"], "fo": ["eq"], "fv": ["x"]})

        self.assertEqual(filters_from_rows(params), [])

    def test_unknown_operator_is_skipped(self):
        params = MultiValueDict({"ff": ["price"], "fo": ["взломать"], "fv": ["1"]})

        self.assertEqual(filters_from_rows(params), [])


class DescribeFilterTestCase(SimpleTestCase):
    def test_words_instead_of_operators(self):
        item = FieldFilter(key="price", op="lt", value=100000.0, raw_value="100000")

        self.assertEqual(describe_filter(item), "Цена меньше 100000")

    def test_memory_field(self):
        item = FieldFilter(key="ram", op="gte", value=16.0, raw_value="16 ГБ")

        self.assertEqual(describe_filter(item), "Оперативная память не меньше 16 ГБ")

    def test_rows_are_padded_to_three(self):
        rows = build_filter_rows([])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["field"], "")

    def test_existing_filters_come_first(self):
        rows = build_filter_rows([
            FieldFilter(key="price", op="lt", value=1.0, raw_value="1"),
        ])

        self.assertEqual(rows[0]["field"], "price")
        self.assertEqual(len(rows), 3)


class SearchPageTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="pass12345")
        self.cheap = self._document("Дешёвый", "10 000 руб.", "8 ГБ")
        self.expensive = self._document("Дорогой", "90 000 руб.", "32 ГБ")
        self.client.force_login(self.user)

    def _document(self, title: str, price: str, ram: str) -> Document:
        document = Document.objects.create(
            user=self.user,
            original_filename=f"{title}.docx",
            document_type="specification",
            title=title,
            data=dict(SPEC_DATA, title=title, price=price, ram=ram),
            status=Document.Status.READY,
        )
        document.search_text = build_search_text(
            title=title,
            description="",
            original_filename=document.original_filename,
            extracted_text="",
        )
        document.save(update_fields=["search_text"])
        index_document(document)
        return document

    def test_price_filter_returns_only_cheap(self):
        response = self.client.get(reverse("documents:search"), {
            "ff": ["price"], "fo": ["lt"], "fv": ["50000"],
        })
        titles = [item.title for item in response.context["documents"]]

        self.assertEqual(titles, ["Дешёвый"])

    def test_two_conditions_are_applied(self):
        response = self.client.get(reverse("documents:search"), {
            "ff": ["price", "ram"], "fo": ["gt", "gte"], "fv": ["50000", "16"],
        })
        titles = [item.title for item in response.context["documents"]]

        self.assertEqual(titles, ["Дорогой"])

    def test_applied_filters_are_described_in_words(self):
        response = self.client.get(reverse("documents:search"), {
            "ff": ["price"], "fo": ["lt"], "fv": ["50000"],
        })

        self.assertIn("Цена меньше 50000", response.context["chips"])

    def test_old_syntax_still_works(self):
        response = self.client.get(reverse("documents:search"), {"f": "price:lt:50000"})
        titles = [item.title for item in response.context["documents"]]

        self.assertEqual(titles, ["Дешёвый"])

    def test_page_has_no_technical_hint_by_default(self):
        response = self.client.get(reverse("documents:search"))
        html = response.content.decode()

        self.assertIn('name="ff"', html)
        self.assertIn("не важно", html)

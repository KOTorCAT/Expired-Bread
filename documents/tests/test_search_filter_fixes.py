"""Этап 3: фильтры по единицам, псевдонимы ключей, версии промпта."""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from documents.models import Document
from documents.services.field_index import index_document
from documents.services.normalization import build_fields, build_search_text, kind_for_key
from documents.services.prompts import EXTRACT, EXTRACT_VERSION, get_extract_prompt
from documents.services.search_service import (
    _normalize_filter_value,
    parse_filter_string,
    search_documents,
)

SPEC = {
    "title": "Спецификация",
    "description": "",
    "organization": "",
    "date": "15.03.2025",
    "product_name": "Ноутбук",
    "manufacturer": "ASUS",
    "category": "Ноутбуки",
    "price": "54 990 руб.",
    "ram": "16 ГБ",
    "storage": "1 ТБ",
    "characteristics": {},
}

PRICE_LIST = {
    "title": "Прайс-лист",
    "description": "",
    "organization": "",
    "date": "15.03.2025",
    "vendor": "АО «Волга-Пром»",
    "currency": "RUB",
    "items": [{"name": "Ноутбук", "sku": "ART-9452", "price": "74 990 руб.", "unit": "шт"}],
}

TECH = {
    "title": "Руководство",
    "description": "",
    "organization": "",
    "date": "15.03.2025",
    "document_code": "ГОСТ 2.105-2019",
    "version": "1.0",
    "sections": [],
    "requirements": [],
}


class KindByKeyTestCase(SimpleTestCase):
    def test_memory_keys(self):
        self.assertEqual(kind_for_key("storage"), "memory")
        self.assertEqual(kind_for_key("ram"), "memory")

    def test_money_key(self):
        self.assertEqual(kind_for_key("price"), "money")

    def test_unknown_key_is_text(self):
        self.assertEqual(kind_for_key("нет_такого"), "text")


class FilterUnitsTestCase(SimpleTestCase):
    def test_terabyte_becomes_gigabytes(self):
        self.assertEqual(_normalize_filter_value("gte", "1 ТБ", "storage"), 1000.0)

    def test_gigabytes_stay_gigabytes(self):
        self.assertEqual(_normalize_filter_value("gte", "1000 ГБ", "storage"), 1000.0)

    def test_price_keeps_number(self):
        self.assertEqual(_normalize_filter_value("lt", "100000", "price"), 100000.0)

    def test_megabytes_are_smaller(self):
        self.assertLess(_normalize_filter_value("gte", "512 МБ", "ram"), 1.0)


class CharacteristicsAreNumericTestCase(SimpleTestCase):
    def test_ozu_inside_characteristics_becomes_memory(self):
        fields = build_fields(
            "specification",
            dict(SPEC, characteristics={"ОЗУ": "32 ГБ", "Процессор": "Intel Core i7"}),
        )
        by_key = {field.key: field for field in fields}

        self.assertIn("ram", by_key)
        self.assertEqual(by_key["ram"].value_num, 32.0)
        self.assertEqual(by_key["ram"].unit, "GB")

    def test_text_characteristic_stays_text(self):
        fields = build_fields(
            "specification", dict(SPEC, characteristics={"Процессор": "Intel Core i7"})
        )
        cpu = next(field for field in fields if "intel" in (field.value_text or ""))

        self.assertIsNone(cpu.value_num)


class SearchByUnitsTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="pass12345")
        self.terabyte = self._document("Терабайт", dict(SPEC, title="Терабайт"))
        self.small = self._document(
            "Полтерабайта", dict(SPEC, title="Полтерабайта", storage="512 ГБ")
        )
        self.client.force_login(self.user)

    def _document(self, title: str, data: dict) -> Document:
        document = Document.objects.create(
            user=self.user,
            original_filename=f"{title}.docx",
            document_type="specification",
            title=title,
            data=data,
            status=Document.Status.READY,
        )
        document.search_text = build_search_text(
            title=title, description="", original_filename="x.docx", extracted_text=""
        )
        document.save(update_fields=["search_text"])
        index_document(document)
        return document

    def test_one_terabyte_finds_thousand_gigabytes(self):
        filters = [parse_filter_string("storage:gte:1 ТБ")]

        titles = [item.title for item in search_documents(user=self.user, filters=filters)]

        self.assertIn("Терабайт", titles)

    def test_one_terabyte_does_not_find_512_gb(self):
        filters = [parse_filter_string("storage:gte:1 ТБ")]

        titles = [item.title for item in search_documents(user=self.user, filters=filters)]

        self.assertNotIn("Полтерабайта", titles)

    def test_filter_by_characteristic_value(self):
        document = self._document(
            "С характеристикой",
            dict(SPEC, title="С характеристикой", characteristics={"ОЗУ": "64 ГБ"}),
        )

        filters = [parse_filter_string("ram:gte:32")]
        titles = [item.title for item in search_documents(user=self.user, filters=filters)]

        self.assertIn(document.title, titles)


class VendorAliasTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="pass12345")

        self.price = self._document("Прайс", "price_list", PRICE_LIST)
        self.spec = self._document(
            "Спецификация",
            "specification",
            dict(SPEC, title="Спецификация", manufacturer="АСУС"),
        )
        self.client.force_login(self.user)

    def _document(self, title: str, doc_type: str, data: dict) -> Document:
        document = Document.objects.create(
            user=self.user,
            original_filename=f"{title}.docx",
            document_type=doc_type,
            title=title,
            data=data,
            status=Document.Status.READY,
        )
        document.search_text = build_search_text(
            title=title, description="", original_filename="x.docx", extracted_text=""
        )
        document.save(update_fields=["search_text"])
        index_document(document)
        return document

    def test_vendor_filter_finds_specification(self):
        filters = [parse_filter_string("vendor:contains:асус")]

        titles = [item.title for item in search_documents(user=self.user, filters=filters)]

        self.assertEqual(titles, ["Спецификация"])

    def test_manufacturer_filter_finds_price_list(self):
        filters = [parse_filter_string("manufacturer:contains:волга")]

        titles = [item.title for item in search_documents(user=self.user, filters=filters)]

        self.assertEqual(titles, ["Прайс"])

    def test_vendor_filter_still_finds_price_list(self):
        filters = [parse_filter_string("vendor:contains:волга")]

        titles = [item.title for item in search_documents(user=self.user, filters=filters)]

        self.assertEqual(titles, ["Прайс"])


class PromptVersionTestCase(SimpleTestCase):
    def test_both_versions_exist(self):
        self.assertIn("v1", EXTRACT)
        self.assertIn("v2", EXTRACT)

    def test_versions_differ(self):
        self.assertNotEqual(get_extract_prompt("v1"), get_extract_prompt("v2"))

    def test_v2_allows_service_characters(self):
        prompt = get_extract_prompt("v2")

        self.assertIn("ART-9452", prompt)
        self.assertIn("USB-C", prompt)

    def test_default_version_is_v2(self):
        self.assertEqual(EXTRACT_VERSION, "v2")
        self.assertEqual(get_extract_prompt(), EXTRACT["v2"])

    def test_v1_is_still_selectable(self):
        self.assertEqual(get_extract_prompt("v1"), EXTRACT["v1"])

    def test_unknown_version_raises(self):
        with self.assertRaises(KeyError):
            get_extract_prompt("v9")

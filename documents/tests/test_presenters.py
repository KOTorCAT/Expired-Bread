"""Тесты представления данных: интерфейс без JSON."""

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from documents.models import Document
from documents.presenters import (
    build_editor,
    build_view,
    label_for,
    parse_editor,
)
from documents.schemas import get_schema


class BuildViewTestCase(SimpleTestCase):
    def test_scalars_have_human_labels(self):
        view = build_view("receipt", {"seller": "ООО Лук", "total": "120 руб."})
        by_label = {row["label"]: row["value"] for row in view["scalars"]}

        self.assertEqual(by_label["Продавец"], "ООО Лук")
        self.assertEqual(by_label["Итог"], "120 руб.")

    def test_empty_values_are_skipped(self):
        view = build_view("receipt", {"seller": "", "total": "1"})

        self.assertEqual([row["key"] for row in view["scalars"]], ["total"])

    def test_items_become_a_table(self):
        view = build_view("receipt", {
            "items": [{"name": "Лук", "price": "79"}],
        })

        self.assertEqual(len(view["tables"]), 1)
        table = view["tables"][0]
        self.assertEqual(
            [column["label"] for column in table["columns"]],
            ["Наименование", "Цена"],
        )
        self.assertEqual(table["rows"], [["Лук", "79"]])

    def test_characteristics_become_a_table(self):
        view = build_view("specification", {"characteristics": {"ОЗУ": "16 ГБ"}})

        self.assertEqual(view["tables"][0]["rows"], [["ОЗУ", "16 ГБ"]])

    def test_title_and_description_are_not_duplicated(self):
        view = build_view("receipt", {"title": "Чек", "description": "Описание"})

        self.assertEqual(view["scalars"], [])

    def test_non_dict_data_is_safe(self):
        self.assertEqual(build_view("receipt", None), {"scalars": [], "tables": []})

    def test_unknown_key_gets_readable_label(self):
        self.assertEqual(label_for("receipt", "fiscal_number"), "Фискальный номер")
        self.assertEqual(label_for("receipt", "some_new_field"), "Some new field")


class BuildEditorTestCase(SimpleTestCase):
    def test_fields_come_from_schema_not_data(self):
        editor = build_editor("receipt", {})
        keys = [field["key"] for field in editor["scalars"]]

        self.assertIn("seller", keys)
        self.assertIn("fiscal_number", keys)

    def test_items_columns_match_model(self):
        editor = build_editor("receipt", {})
        table = next(t for t in editor["tables"] if t["key"] == "items")

        self.assertEqual(
            [column["key"] for column in table["columns"]],
            ["name", "quantity", "price", "total"],
        )
        self.assertEqual(table["rows"], [])

    def test_existing_rows_keep_values(self):
        editor = build_editor("receipt", {"items": [{"name": "Лук", "price": "79"}]})
        table = next(t for t in editor["tables"] if t["key"] == "items")
        cells = {cell["key"]: cell["value"] for cell in table["rows"][0]["cells"]}

        self.assertEqual(table["rows"][0]["index"], 0)
        self.assertEqual(cells["name"], "Лук")

    def test_text_list_is_comma_joined(self):
        editor = build_editor("technical_document", {"requirements": ["a", "b"]})
        field = next(f for f in editor["scalars"] if f["key"] == "requirements")

        self.assertTrue(field["text_list"])
        self.assertEqual(field["value"], "a, b")

    def test_title_is_edited_by_main_form(self):
        editor = build_editor("receipt", {"title": "Чек"})
        self.assertNotIn("title", [field["key"] for field in editor["scalars"]])


class ParseEditorTestCase(SimpleTestCase):
    def test_scalars_and_rows(self):
        post = {
            "f_seller": "ООО Лук",
            "f_total": "120",
            "items__0__name": "Лук",
            "items__0__price": "79",
            "items__1__name": "Морковь",
            "items__1__price": "39",
        }

        data = parse_editor(post, "receipt")

        self.assertEqual(data["seller"], "ООО Лук")
        self.assertEqual([row["name"] for row in data["items"]], ["Лук", "Морковь"])

    def test_gaps_in_row_numbers_are_kept(self):
        post = {"items__0__name": "A", "items__2__name": "C"}

        data = parse_editor(post, "receipt")

        self.assertEqual([row["name"] for row in data["items"]], ["A", "C"])

    def test_empty_rows_are_dropped(self):
        post = {"items__0__name": "", "items__0__price": ""}

        self.assertEqual(parse_editor(post, "receipt")["items"], [])

    def test_dictionary_roundtrip(self):
        post = {
            "characteristics__0__key": "ОЗУ",
            "characteristics__0__value": "16 ГБ",
        }

        data = parse_editor(post, "specification")

        self.assertEqual(data["characteristics"], {"ОЗУ": "16 ГБ"})

    def test_text_list_from_csv(self):
        post = {"f_requirements": "a, b , c"}

        data = parse_editor(post, "technical_document")

        self.assertEqual(data["requirements"], ["a", "b", "c"])

    def test_parsed_payload_passes_schema(self):
        data = parse_editor({"f_seller": "ООО"}, "receipt")
        data["title"] = "Чек"

        get_schema("receipt").model_validate(data)


RECEIPT_DATA = {
    "title": "Чек № 42",
    "description": "",
    "organization": "",
    "date": "19.10.2026 18:07",
    "seller": "ООО Лук",
    "seller_inn": "7801234567",
    "total": "120",
    "payment_method": "карта",
    "vat": "",
    "cashier": "",
    "fiscal_number": "12345",
    "items": [{"name": "Лук репчатый", "quantity": "1", "price": "79", "total": "79"}],
}


class PagesWithoutJsonTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.user = get_user_model().objects.create_user("owner", password="pass12345")
        self.document = Document.objects.create(
            user=self.user,
            original_filename="receipt.pdf",
            document_type="receipt",
            title="Чек № 42",
            data=dict(RECEIPT_DATA),
            status=Document.Status.READY,
        )
        self.client.force_login(self.user)

    def test_detail_page_shows_fields_instead_of_json(self):
        response = self.client.get(
            reverse("documents:detail", args=[self.document.pk])
        )
        html = response.content.decode()

        self.assertNotIn('<pre class="json">', html)
        self.assertIn("Продавец", html)
        self.assertIn("ООО Лук", html)
        self.assertIn("Лук репчатый", html)

    def test_review_page_has_no_json_editor(self):
        response = self.client.get(
            reverse("documents:review", args=[self.document.pk])
        )
        html = response.content.decode()

        self.assertNotIn("json-editor", html)
        self.assertNotIn("data_json", html)
        self.assertIn('name="f_seller"', html)
        self.assertIn('name="items__0__name"', html)

    def test_review_page_has_add_row_template(self):
        response = self.client.get(
            reverse("documents:review", args=[self.document.pk])
        )
        html = response.content.decode()

        self.assertIn("data-row-template", html)
        self.assertIn("__NEW__", html)

    def test_review_post_saves_edited_value(self):
        response = self.client.post(
            reverse("documents:review", args=[self.document.pk]),
            {
                "title": "Чек № 42",
                "document_type": "receipt",
                "description": "",
                "document_date": "2026-10-19",
                "f_seller": "ООО Лук",
                "f_total": "999",
                "items__0__name": "Лук репчатый",
                "items__0__price": "79",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.data["total"], "999")
        self.assertEqual(self.document.status, Document.Status.READY)

    def test_review_post_fills_all_schema_fields(self):
        """Редактор отправляет все поля схемы, даже пустые.

        Поэтому в данных не остаётся «дыр»: поиск и отчёт видят
        одинаковый набор ключей.
        """
        self.client.post(
            reverse("documents:review", args=[self.document.pk]),
            {
                "title": "Чек № 42",
                "document_type": "receipt",
                "description": "",
                "document_date": "2026-10-19",
                "f_total": "999",
            },
        )

        self.document.refresh_from_db()
        for key in get_schema("receipt").model_fields:
            self.assertIn(key, self.document.data)

    def test_type_change_reloads_fields_without_touching_data(self):
        """Смена типа не должна записывать данные старой схемы в новую."""
        response = self.client.post(
            reverse("documents:review", args=[self.document.pk]),
            {
                "title": "Чек № 42",
                "document_type": "specification",
                "description": "",
                "document_date": "",
                "f_total": "999",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.document_type, "specification")
        self.assertEqual(self.document.data["total"], "120")

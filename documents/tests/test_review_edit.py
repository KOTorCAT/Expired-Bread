"""Тесты этапа 6: сохранение правок и удаление документа.

Правка полей проверяется в test_presenters (редактор строится по схеме
и не использует JSON), здесь — запись результата и удаление.
"""

import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from documents.models import Document, DocumentField
from documents.services.document_service import confirm_document, delete_document
from documents.services.field_index import index_document
from documents.services.normalization import build_search_text
from documents.services.search_service import (
    parse_filter_string,
    search_documents,
)

SPEC_DATA = {
    "title": "Спецификация на ноутбук",
    "description": "Характеристики и цена",
    "organization": "ООО Тест",
    "date": "02.04.2025",
    "product_name": "Ноутбук ASUS",
    "manufacturer": "ASUS",
    "category": "Ноутбуки",
    "price": "100 руб.",
    "ram": "16 ГБ",
    "storage": "512 ГБ",
    "characteristics": {},
}


class ConfirmEditTestCase(TestCase):
    def _ready_document(self) -> Document:
        document = Document.objects.create(
            original_filename="spec.docx",
            document_type="specification",
            title="Спецификация на ноутбук",
            data=dict(SPEC_DATA),
            data_raw=dict(SPEC_DATA),
            status=Document.Status.READY,
        )
        document.search_text = build_search_text(
            title=document.title,
            description=document.description,
            original_filename=document.original_filename,
            extracted_text="",
        )
        document.save(update_fields=["search_text"])
        index_document(document)
        return document

    def test_edit_changes_indexed_field(self):
        document = self._ready_document()
        expensive = parse_filter_string("price:gt:150")

        self.assertEqual(search_documents(filters=[expensive]).count(), 0)

        confirm_document(document.pk, {
            "title": document.title,
            "document_type": "specification",
            "description": document.description,
            "data": dict(SPEC_DATA, price="200 руб."),
        })

        self.assertEqual(search_documents(filters=[expensive]).count(), 1)

    def test_edit_updates_document_data(self):
        document = self._ready_document()
        confirm_document(document.pk, {
            "title": "Новое название",
            "document_type": "specification",
            "description": "Новое описание",
            "data": dict(SPEC_DATA, price="300 руб."),
        })
        document.refresh_from_db()
        self.assertEqual(document.data["price"], "300 руб.")
        self.assertEqual(document.title, "Новое название")
        self.assertEqual(document.status, Document.Status.READY)
        self.assertIsNotNone(document.reviewed_at)

    def test_manual_fixes_are_recorded(self):
        document = self._ready_document()
        confirm_document(document.pk, {
            "title": document.title,
            "document_type": "specification",
            "description": document.description,
            "data": dict(SPEC_DATA, price="200 руб."),
        })
        document.refresh_from_db()
        self.assertIn("price", document.processing_meta.get("corrected_fields", []))

    def test_no_corrections_when_nothing_changed(self):
        document = self._ready_document()
        confirm_document(document.pk, {
            "title": document.title,
            "document_type": "specification",
            "description": document.description,
            "data": dict(SPEC_DATA),
        })
        document.refresh_from_db()
        self.assertNotIn("corrected_fields", document.processing_meta)

    def test_edit_replaces_old_indexed_fields(self):
        document = self._ready_document()
        confirm_document(document.pk, {
            "title": document.title,
            "document_type": "specification",
            "description": document.description,
            "data": dict(SPEC_DATA, ram="32 ГБ"),
        })
        values = list(
            document.fields.filter(key="ram").values_list("value_num", flat=True)
        )
        self.assertEqual(values, [32.0])
        self.assertNotIn(16.0, values)


class DeleteDocumentTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.override = override_settings(MEDIA_ROOT=self.root)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _document_with_file(self) -> Document:
        document = Document.objects.create(
            original_filename="x.docx",
            document_type="other",
            status=Document.Status.READY,
        )
        document.file.save("x.docx", ContentFile(b"data"), save=True)
        index_document(document)
        return document

    def test_file_and_record_are_deleted(self):
        document = self._document_with_file()
        path = Path(document.file.path)
        self.assertTrue(path.exists())

        document_id = document.pk
        delete_document(document)

        self.assertFalse(Document.objects.filter(pk=document_id).exists())
        self.assertFalse(path.exists())

    def test_indexed_fields_are_deleted_cascade(self):
        document = self._document_with_file()
        confirm_document(document.pk, {
            "title": "Док",
            "document_type": "specification",
            "description": "",
            "data": dict(SPEC_DATA),
        })
        document.refresh_from_db()
        self.assertTrue(DocumentField.objects.filter(document=document).exists())

        delete_document(document)
        self.assertFalse(DocumentField.objects.filter(document_id=document.pk).exists())

    def test_missing_file_does_not_block_deletion(self):
        document = self._document_with_file()
        Path(document.file.path).unlink()

        document_id = document.pk
        delete_document(document)

        self.assertFalse(Document.objects.filter(pk=document_id).exists())

from django.test import TestCase

from documents.models import Document, DocumentField
from documents.services.field_index import index_document


class IndexDocumentTestCase(TestCase):
    def _make_doc(self, **kwargs) -> Document:
        defaults = {
            "original_filename": "test.pdf",
            "document_type": "price_list",
            "status": Document.Status.READY,
            "data": {
                "title": "Прайс-лист",
                "date": "15.03.2025",
                "items": [
                    {"name": "Ноутбук", "price": "89 990 руб.", "unit": "шт"},
                    {"name": "Мышь", "price": "1 299,50 руб.", "unit": "шт"},
                ],
            },
        }
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_index_creates_fields(self):
        doc = self._make_doc()
        count = index_document(doc)

        self.assertGreater(count, 0)
        self.assertEqual(DocumentField.objects.filter(document=doc).count(), count)

    def test_index_is_idempotent(self):
        doc = self._make_doc()
        first = index_document(doc)
        second = index_document(doc)

        self.assertEqual(first, second)
        self.assertEqual(DocumentField.objects.filter(document=doc).count(), first)

    def test_item_index_for_list(self):
        doc = self._make_doc()
        index_document(doc)

        prices = DocumentField.objects.filter(
            document=doc, key="price",
        ).order_by("item_index")
        self.assertEqual(prices.count(), 2)
        self.assertEqual(prices[0].item_index, 0)
        self.assertEqual(prices[1].item_index, 1)

        self.assertAlmostEqual(prices[0].value_num, 89990.0)
        self.assertAlmostEqual(prices[1].value_num, 1299.5)
        self.assertEqual(prices[0].unit, "RUB")

    def test_scalars_have_no_item_index(self):
        doc = self._make_doc()
        index_document(doc)

        title = DocumentField.objects.get(document=doc, key="title")
        self.assertIsNone(title.item_index)

    def test_empty_data_gives_no_fields(self):
        doc = self._make_doc(data={})
        count = index_document(doc)
        self.assertEqual(count, 0)
        self.assertEqual(DocumentField.objects.filter(document=doc).count(), 0)

    def test_reindex_after_data_change(self):
        doc = self._make_doc()
        index_document(doc)

        # Меняем data
        doc.data = {
            "title": "Новый прайс",
            "items": [
                {"name": "Клавиатура", "price": "5000 руб."},
            ],
        }
        doc.save(update_fields=["data"])
        index_document(doc)

        # Старое поле "Ноутбук" исчезло
        self.assertFalse(
            DocumentField.objects.filter(
                document=doc, key="name", value_text__contains="ноутбук",
            ).exists()
        )
        # Новое "Клавиатура" появилось
        self.assertTrue(
            DocumentField.objects.filter(
                document=doc, key="name", value_text__contains="клавиатура",
            ).exists()
        )

    def test_deleting_document_cascades(self):
        doc = self._make_doc()
        index_document(doc)
        self.assertGreater(DocumentField.objects.filter(document=doc).count(), 0)

        doc.delete()
        self.assertEqual(DocumentField.objects.filter(document_id=doc.id).count(), 0)

    def test_specification_fields(self):
        doc = self._make_doc(
            document_type="specification",
            data={
                "title": "Спецификация Acer",
                "product_name": "Acer Aspire 5",
                "ram": "16GB",
                "storage": "1 ТБ",
                "price": "74990 ₽",
            },
        )
        index_document(doc)

        ram = DocumentField.objects.get(document=doc, key="ram")
        self.assertAlmostEqual(ram.value_num, 16.0)
        self.assertEqual(ram.unit, "GB")

        storage = DocumentField.objects.get(document=doc, key="storage")
        self.assertAlmostEqual(storage.value_num, 1000.0)
        self.assertEqual(storage.unit, "GB")

        price = DocumentField.objects.get(document=doc, key="price")
        self.assertAlmostEqual(price.value_num, 74990.0)
        self.assertEqual(price.unit, "RUB")
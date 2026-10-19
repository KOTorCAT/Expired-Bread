"""Совпавшие поля в результатах поиска."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from documents.models import Document
from documents.services.field_index import index_document
from documents.services.normalization import build_search_text

SPEC_DATA = {
    "title": "Спецификация",
    "description": "",
    "organization": "",
    "date": "15.03.2025",
    "product_name": "Ноутбук",
    "manufacturer": "ASUS",
    "category": "",
    "price": "10 000 руб.",
    "ram": "8 ГБ",
    "storage": "512 ГБ",
    "characteristics": {},
}


class MatchLabelsTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="pass12345")
        self.document = Document.objects.create(
            user=self.user,
            original_filename="spec.docx",
            document_type="specification",
            title="Ноутбук",
            data=dict(SPEC_DATA),
            status=Document.Status.READY,
        )
        self.document.search_text = build_search_text(
            title="Ноутбук", description="", original_filename="spec.docx", extracted_text="",
        )
        self.document.save(update_fields=["search_text"])
        index_document(self.document)
        self.client.force_login(self.user)

    def test_matched_field_is_described(self):
        response = self.client.get(reverse("documents:search"), {
            "ff": ["price"], "fo": ["lt"], "fv": ["50000"],
        })

        documents = list(response.context["documents"])
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].match_labels, ["Цена: 10 000 руб."])

    def test_several_fields_are_listed(self):
        response = self.client.get(reverse("documents:search"), {
            "ff": ["price", "ram"], "fo": ["lt", "gte"], "fv": ["50000", "8"],
        })

        labels = response.context["documents"][0].match_labels
        self.assertEqual(len(labels), 2)

    def test_without_filters_there_are_no_labels(self):
        response = self.client.get(reverse("documents:search"), {"q": "ноутбук"})

        self.assertFalse(getattr(response.context["documents"][0], "match_labels", []))

    def test_labels_are_rendered_in_card(self):
        response = self.client.get(reverse("documents:search"), {
            "ff": ["price"], "fo": ["lt"], "fv": ["50000"],
        })

        self.assertContains(response, "Цена: 10 000 руб.")

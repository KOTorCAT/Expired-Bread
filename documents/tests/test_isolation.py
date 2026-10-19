"""Тесты этапа 8: изоляция документов между пользователями.

Это самые важные тесты в проекте: они проверяют, что чужой документ
недоступен ни на одной странице, ни в поиске, ни в Q&A.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from documents.models import Document
from documents.services.field_index import index_document
from documents.services.normalization import build_search_text

SPEC_DATA = {
    "title": "Спецификация",
    "description": "Описание",
    "organization": "",
    "date": "",
    "product_name": "Ноутбук",
    "manufacturer": "ASUS",
    "category": "",
    "price": "100 руб.",
    "ram": "16 ГБ",
    "storage": "512 ГБ",
    "characteristics": {},
}


class IsolationTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)

        User = get_user_model()
        self.alice = User.objects.create_user("alice", password="pass12345")
        self.bob = User.objects.create_user("bob", password="pass12345")

        self.alice_doc = self._make_document(
            self.alice, "Документ Алисы", "уникальное содержимое алисы"
        )
        self.bob_doc = self._make_document(
            self.bob, "Документ Боба", "уникальное содержимое боба"
        )

    def _make_document(self, user, title: str, text: str) -> Document:
        document = Document.objects.create(
            user=user,
            original_filename=f"{user.username}.docx",
            document_type="specification",
            title=title,
            data=dict(SPEC_DATA, title=title),
            status=Document.Status.READY,
            extracted_text=text,
        )
        document.file.save(
            f"{user.username}.docx", ContentFile(b"data"), save=True
        )
        document.search_text = build_search_text(
            title=document.title,
            description=document.description,
            original_filename=document.original_filename,
            extracted_text=document.extracted_text,
        )
        document.save(update_fields=["search_text"])
        index_document(document)
        return document

    # --- Аноним -------------------------------------------------------------

    def test_anonymous_is_redirected(self):
        urls = [
            reverse("documents:index"),
            reverse("documents:list"),
            reverse("documents:upload"),
            reverse("documents:search"),
            reverse("documents:ask"),
            reverse("documents:detail", args=[self.alice_doc.pk]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, msg=url)
            self.assertIn("/accounts/login/", response["Location"], msg=url)

    # --- Чужой документ по прямому id ---------------------------------------

    def test_other_user_gets_404_on_get_views(self):
        self.client.force_login(self.bob)
        for name in ("detail", "review", "download"):
            url = reverse(f"documents:{name}", args=[self.alice_doc.pk])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404, msg=name)

    def test_other_user_gets_404_on_post_views(self):
        self.client.force_login(self.bob)
        for name in ("reprocess", "delete"):
            url = reverse(f"documents:{name}", args=[self.alice_doc.pk])
            response = self.client.post(url)
            self.assertEqual(response.status_code, 404, msg=name)

        # И документ действительно не удалён
        self.assertTrue(
            Document.objects.filter(pk=self.alice_doc.pk).exists()
        )

    # --- Списки и поиск -----------------------------------------------------

    def test_list_shows_only_own_documents(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse("documents:list"))
        titles = [d.title for d in response.context["documents"]]
        self.assertIn("Документ Боба", titles)
        self.assertNotIn("Документ Алисы", titles)

    def test_search_returns_only_own_documents(self):
        self.client.force_login(self.bob)
        response = self.client.get(
            reverse("documents:search"), {"q": "уникальное"}
        )
        titles = [d.title for d in response.context["documents"]]
        self.assertIn("Документ Боба", titles)
        self.assertNotIn("Документ Алисы", titles)

    def test_field_filter_does_not_leak_other_documents(self):
        self.client.force_login(self.bob)
        response = self.client.get(
            reverse("documents:search"), {"f": "price:lt:100000"}
        )
        titles = [d.title for d in response.context["documents"]]
        self.assertNotIn("Документ Алисы", titles)

    # --- Q&A ----------------------------------------------------------------

    @patch("documents.services.qa_service.answer_question")
    def test_qa_does_not_see_other_documents(self, mock_answer):
        mock_answer.return_value = {"found": True, "answer": "утечка"}
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("documents:ask"), {"question": "алисы"}
        )

        self.assertFalse(response.context["result"]["found"])
        mock_answer.assert_not_called()

    @patch("documents.services.qa_service.answer_question")
    def test_qa_uses_own_documents(self, mock_answer):
        mock_answer.return_value = {"found": True, "answer": "ответ"}

        from documents.services.qa_service import ask

        result = ask("содержимое боба", user=self.bob)
        self.assertTrue(result["found"])
        self.assertEqual([d.pk for d in result["sources"]], [self.bob_doc.pk])

    # --- Файлы --------------------------------------------------------------

    def test_media_is_not_served_directly(self):
        url = f"/media/{self.alice_doc.file.name}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_owner_can_download_own_file(self):
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("documents:download", args=[self.alice_doc.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])

    # --- Просмотр своего ---------------------------------------------------

    def test_owner_sees_own_document(self):
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("documents:detail", args=[self.alice_doc.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Документ Алисы")

    def test_owner_can_delete_own_document(self):
        self.client.force_login(self.alice)
        document_id = self.alice_doc.pk
        response = self.client.post(
            reverse("documents:delete", args=[document_id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Document.objects.filter(pk=document_id).exists())

    # --- Регистрация --------------------------------------------------------

    def test_registration_page_is_open(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)

    def test_registration_creates_user_and_logs_in(self):
        response = self.client.post(reverse("register"), {
            "username": "newuser",
            "email": "",
            "password1": "very-secret-pass-42",
            "password2": "very-secret-pass-42",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            get_user_model().objects.filter(username="newuser").exists()
        )

    def test_registration_accepts_demo_style_password(self):
        """Пароли вида demo12345 разрешены: проект демонстрационный.

        Для этого в настройках отключены CommonPasswordValidator
        и UserAttributeSimilarityValidator. Тест фиксирует договорённость,
        чтобы её не сломали случайно.
        """
        response = self.client.post(reverse("register"), {
            "username": "demo2",
            "email": "",
            "password1": "demo12345",
            "password2": "demo12345",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            get_user_model().objects.filter(username="demo2").exists()
        )
        self.assertTrue(
            self.client.login(username="demo2", password="demo12345")
        )

    def test_registration_still_rejects_short_password(self):
        response = self.client.post(reverse("register"), {
            "username": "shorty",
            "email": "",
            "password1": "abc",
            "password2": "abc",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            get_user_model().objects.filter(username="shorty").exists()
        )


class ForUserQuerySetTestCase(TestCase):
    def test_for_user_filters_by_owner(self):
        User = get_user_model()
        alice = User.objects.create_user("a1", password="pass12345")
        bob = User.objects.create_user("b1", password="pass12345")
        Document.objects.create(user=alice, original_filename="a.docx")
        Document.objects.create(user=bob, original_filename="b.docx")

        self.assertEqual(Document.objects.for_user(alice).count(), 1)
        self.assertEqual(Document.objects.for_user(bob).count(), 1)

    def test_for_anonymous_returns_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        Document.objects.create(original_filename="anon.docx")
        self.assertEqual(
            Document.objects.for_user(AnonymousUser()).count(), 0
        )

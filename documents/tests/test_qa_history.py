"""История вопросов Q&A в рамках визита."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from documents import views
from documents.models import Document
from documents.services.normalization import build_search_text


class QAHistoryTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="pass12345")

        # Без документа ask() не находит кандидатов и не доходит до модели
        self.document = Document.objects.create(
            user=self.user,
            original_filename="receipt.docx",
            title="Кассовый чек",
            document_type="receipt",
            status=Document.Status.READY,
            extracted_text="Лук репчатый 1 кг 79.90",
        )
        self.document.search_text = build_search_text(
            title=self.document.title,
            description="",
            original_filename=self.document.original_filename,
            extracted_text=self.document.extracted_text,
        )
        self.document.save(update_fields=["search_text"])

        self.client.force_login(self.user)

    def _ask(self, question: str = "сколько стоит лук"):
        with patch("documents.services.qa_service.answer_question") as mock:
            mock.return_value = {"found": True, "answer": "79,90"}
            return self.client.post(reverse("documents:ask"), {"question": question})

    def test_question_is_saved_in_session(self):
        self._ask()

        history = self.client.session.get(views.QA_HISTORY_KEY)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["question"], "сколько стоит лук")
        self.assertTrue(history[0]["found"])

    def test_post_redirects_to_avoid_repeat(self):
        self.assertEqual(self._ask().status_code, 302)

    def test_history_is_rendered(self):
        self._ask()

        response = self.client.get(reverse("documents:ask"))

        self.assertContains(response, "сколько стоит лук")
        self.assertContains(response, "79,90")

    def test_newest_question_comes_first(self):
        self._ask("первый вопрос")
        self._ask("второй вопрос")

        history = self.client.session.get(views.QA_HISTORY_KEY)
        self.assertEqual(history[0]["question"], "второй вопрос")

    def test_history_is_limited(self):
        for index in range(12):
            self._ask(f"вопрос номер {index}")

        history = self.client.session.get(views.QA_HISTORY_KEY)
        self.assertEqual(len(history), views.QA_HISTORY_LIMIT)

    def test_clear_removes_history(self):
        self._ask()

        self.client.get(reverse("documents:ask") + "?clear=1")

        self.assertEqual(self.client.session.get(views.QA_HISTORY_KEY, []), [])

    def test_empty_question_is_ignored(self):
        self._ask("   ")

        self.assertEqual(self.client.session.get(views.QA_HISTORY_KEY, []), [])

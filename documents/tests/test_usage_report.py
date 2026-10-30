"""Отчёт о расходе токенов по документам."""

import io
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from documents.models import Document


class UsageReportTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="pass12345")

    def _document(self, usage, title: str = "Документ") -> Document:
        return Document.objects.create(
            user=self.user,
            original_filename="doc.docx",
            title=title,
            document_type="other",
            status=Document.Status.READY,
            processing_meta={"usage": usage} if usage is not None else {},
        )

    def _run(self, **kwargs) -> str:
        out = io.StringIO()
        call_command("usage_report", stdout=out, **kwargs)
        return out.getvalue()

    def test_tokens_are_summed(self):
        self._document({"extract": {"prompt_tokens": 100, "completion_tokens": 40}})
        self._document({"extract": {"prompt_tokens": 50, "completion_tokens": 10}})

        output = self._run()

        self.assertIn("вход 150", output)
        self.assertIn("выход 50", output)
        self.assertIn("всего 200", output)

    def test_nested_usage_is_summed(self):
        self._document({
            "classify": {"prompt_tokens": 30, "completion_tokens": 5},
            "extract": {"prompt_tokens": 200, "completion_tokens": 60},
            "repair": {},
        })

        output = self._run()

        self.assertIn("вход 230", output)
        self.assertIn("выход 65", output)

    def test_documents_without_usage_are_counted(self):
        self._document(None)

        output = self._run()

        self.assertIn("Без данных о расходе: 1", output)

    @override_settings(
        GIGACHAT_PRICE_PER_1M_PROMPT=1000.0,
        GIGACHAT_PRICE_PER_1M_COMPLETION=2000.0,
    )
    def test_cost_is_computed_when_prices_are_set(self):
        self._document({
            "extract": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
        })

        output = self._run()

        # 1 млн входа × 1000 ₽ + 0,5 млн выхода × 2000 ₽
        self.assertIn("2000.00", output)

    def test_missing_price_is_stated_honestly(self):
        self._document({"extract": {"prompt_tokens": 10}})

        output = self._run()

        self.assertIn("цена", output.lower())
        self.assertIn("не считается", output)

    def test_json_report_is_written(self):
        self._document({"extract": {"prompt_tokens": 10, "completion_tokens": 5}})

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "usage.json"
            self._run(json=str(target))

            self.assertTrue(target.exists())
            payload = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(payload["prompt_tokens"], 10)
        self.assertEqual(payload["completion_tokens"], 5)
        self.assertEqual(payload["documents_with_usage"], 1)

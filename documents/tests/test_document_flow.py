"""Тесты страниц документа: дата в форме, статус обработки, показ файла."""

import io
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from docx import Document as DocxDocument

from documents.forms import DocumentReviewForm
from documents.models import Document

SPEC_DATA = {
    "title": "Спецификация",
    "description": "",
    "organization": "",
    "date": "15.03.2025",
    "product_name": "Ноутбук",
    "manufacturer": "ASUS",
    "category": "",
    "price": "100 руб.",
    "ram": "16 ГБ",
    "storage": "512 ГБ",
    "characteristics": {},
}


def _docx_bytes(text: str = "Тестовый документ") -> bytes:
    document = DocxDocument()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class BaseTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)

        User = get_user_model()
        self.owner = User.objects.create_user("owner", password="pass12345")
        self.stranger = User.objects.create_user("stranger", password="pass12345")

    def _document(self, *, with_file: bool = False, **kwargs) -> Document:
        defaults = {
            "user": self.owner,
            "original_filename": "doc.docx",
            "document_type": "specification",
            "title": "Спецификация",
            "data": dict(SPEC_DATA),
        }
        defaults.update(kwargs)
        document = Document.objects.create(**defaults)
        if with_file:
            document.file.save("doc.docx", ContentFile(_docx_bytes()), save=True)
        return document


class DateFieldTestCase(BaseTestCase):
    """Дата документа не должна пропадать при открытии проверки."""

    def test_form_renders_iso_value_for_date_input(self):
        document = self._document(document_date=date(2025, 3, 15))

        form = DocumentReviewForm(instance=document)
        html = str(form["document_date"])

        # Именно этот формат понимает <input type="date">
        self.assertIn('value="2025-03-15"', html)
        # Локализованная запись в поле даты недопустима — браузер её отвергнет
        self.assertNotIn("15.03.2025", html)

    def test_date_is_restored_from_data_when_lost(self):
        """Если поле в базе пустое, дата берётся из извлечённых данных.

        Именно этот случай ломал проверку: дата терялась при сохранении
        формы, и дальше документ оставался без даты навсегда.
        """
        document = self._document(document_date=None)

        form = DocumentReviewForm(instance=document)

        self.assertIn('value="2025-03-15"', str(form["document_date"]))

    def test_form_is_empty_when_no_date_anywhere(self):
        document = self._document(document_date=None, data={"title": "Без даты"})

        form = DocumentReviewForm(instance=document)

        self.assertNotIn('value="20', str(form["document_date"]))

    def test_confirmed_date_is_saved(self):
        from documents.services.document_service import confirm_document

        document = self._document(status=Document.Status.REVIEW, document_date=None)

        confirm_document(document.pk, {
            "title": "Спецификация",
            "document_type": "specification",
            "description": "",
            "document_date": date(2025, 4, 2),
            "data": dict(SPEC_DATA),
        })

        document.refresh_from_db()
        self.assertEqual(document.document_date, date(2025, 4, 2))

    def test_date_survives_round_trip_through_form(self):
        """Открыли проверку и сохранили без правок — дата на месте."""
        from documents.services.document_service import confirm_document

        document = self._document(
            status=Document.Status.REVIEW, document_date=date(2025, 3, 15)
        )

        form = DocumentReviewForm(data={
            "title": "Спецификация",
            "document_type": "specification",
            "description": "",
            "document_date": "2025-03-15",
            "data_json": "",
        }, instance=document)

        self.assertTrue(form.is_valid(), form.errors)
        confirm_document(document.pk, form.cleaned_data)

        document.refresh_from_db()
        self.assertEqual(document.document_date, date(2025, 3, 15))


class StatusEndpointTestCase(BaseTestCase):
    def test_owner_gets_json(self):
        document = self._document(status=Document.Status.PROCESSING)
        self.client.force_login(self.owner)

        response = self.client.get(reverse("documents:status", args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], Document.Status.PROCESSING)
        self.assertEqual(payload["status_label"], "Обрабатывается")
        self.assertFalse(payload["finished"])

    def test_finished_status_is_reported(self):
        document = self._document(status=Document.Status.READY)
        self.client.force_login(self.owner)

        payload = self.client.get(
            reverse("documents:status", args=[document.pk])
        ).json()

        self.assertTrue(payload["finished"])
        self.assertTrue(payload["has_data"])

    def test_stranger_gets_404(self):
        document = self._document()
        self.client.force_login(self.stranger)

        response = self.client.get(reverse("documents:status", args=[document.pk]))
        self.assertEqual(response.status_code, 404)

    def test_stuck_processing_is_recovered_on_status_request(self):
        document = self._document(status=Document.Status.PROCESSING)
        Document.objects.filter(pk=document.pk).update(
            updated_at=timezone.now() - timedelta(minutes=30)
        )
        self.client.force_login(self.owner)

        payload = self.client.get(
            reverse("documents:status", args=[document.pk])
        ).json()

        self.assertEqual(payload["status"], Document.Status.FAILED)
        self.assertIn("превышено время", payload["error"])


class PreviewTestCase(BaseTestCase):
    def test_owner_gets_inline_file(self):
        document = self._document(with_file=True)
        self.client.force_login(self.owner)

        response = self.client.get(reverse("documents:preview", args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    def test_stranger_gets_404(self):
        document = self._document(with_file=True)
        self.client.force_login(self.stranger)

        response = self.client.get(reverse("documents:preview", args=[document.pk]))
        self.assertEqual(response.status_code, 404)

    def test_document_without_file_gets_404(self):
        document = self._document(with_file=False)
        self.client.force_login(self.owner)

        response = self.client.get(reverse("documents:preview", args=[document.pk]))
        self.assertEqual(response.status_code, 404)


class UploadStartsBackgroundTestCase(BaseTestCase):
    def test_upload_redirects_without_waiting_for_model(self):
        self.client.force_login(self.owner)
        upload = SimpleUploadedFile(
            "new.docx",
            _docx_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        with patch("documents.views.start_processing") as start:
            response = self.client.post(
                reverse("documents:upload"),
                {"file": upload, "doc_type_hint": "specification"},
            )

        self.assertEqual(response.status_code, 302)
        start.assert_called_once()
        document = Document.objects.get(user=self.owner)
        self.assertEqual(document.status, Document.Status.UPLOADED)

    def test_reprocess_starts_background_and_warns_if_busy(self):
        document = self._document(status=Document.Status.FAILED)
        self.client.force_login(self.owner)

        with patch("documents.views.start_processing", return_value=True):
            response = self.client.post(
                reverse("documents:reprocess", args=[document.pk])
            )
        self.assertEqual(response.status_code, 302)

        with patch("documents.views.start_processing", return_value=False):
            response = self.client.post(
                reverse("documents:reprocess", args=[document.pk]),
                follow=True,
            )
        self.assertContains(response, "уже обрабатывается")

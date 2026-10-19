"""Защита от повторной загрузки одного и того же файла."""

import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document as DocxDocument

from documents.models import Document


def _docx(text: str) -> bytes:
    document = DocxDocument()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class DuplicateUploadTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)

        User = get_user_model()
        self.user = User.objects.create_user("owner", password="pass12345")
        self.client.force_login(self.user)

    def _upload(self, name: str = "doc.docx", data: bytes | None = None, force: bool = False):
        payload = {
            "file": SimpleUploadedFile(name, data or _docx("Один и тот же текст")),
            "doc_type_hint": "other",
        }
        if force:
            payload["force"] = "1"
        with patch("documents.views.start_processing"):
            return self.client.post(reverse("documents:upload"), payload)

    def test_first_upload_stores_hash(self):
        self._upload()

        document = Document.objects.get()
        self.assertEqual(len(document.content_hash), 64)

    def test_same_file_is_not_saved_twice(self):
        self._upload()
        response = self._upload()

        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(response.status_code, 302)

    def test_duplicate_redirects_to_existing_document(self):
        self._upload()
        existing = Document.objects.get()
        response = self._upload()

        self.assertEqual(
            response["Location"], reverse("documents:detail", args=[existing.pk])
        )

    def test_force_allows_duplicate(self):
        self._upload()
        self._upload(force=True)

        self.assertEqual(Document.objects.count(), 2)

    def test_different_content_is_allowed(self):
        self._upload()
        self._upload(name="other.docx", data=_docx("Совсем другой текст"))

        self.assertEqual(Document.objects.count(), 2)

    def test_same_file_of_another_user_is_allowed(self):
        self._upload()
        other = get_user_model().objects.create_user("second", password="pass12345")
        self.client.force_login(other)
        self._upload()

        self.assertEqual(Document.objects.filter(user=other).count(), 1)
        self.assertEqual(Document.objects.filter(user=self.user).count(), 1)


class BackfillHashesTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_fills_empty_hashes(self):
        from django.core.files.base import ContentFile

        document = Document.objects.create(original_filename="old.docx")
        document.file.save("old.docx", ContentFile(_docx("старый")), save=True)
        self.assertEqual(document.content_hash, "")

        call_command("backfill_hashes")

        document.refresh_from_db()
        self.assertEqual(len(document.content_hash), 64)

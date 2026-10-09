import tempfile
from pathlib import Path

import pymupdf as fitz
from django.test import SimpleTestCase
from docx import Document as DocxDocument

from documents.services.extractors import (
    ExtractionError,
    extract_docx_text,
    extract_pdf_text,
    extract_text,
)


class ExtractorsTestCase(SimpleTestCase):
    """SimpleTestCase — потому что БД тут не нужна."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

    def _make_pdf(self, name: str, text: str) -> Path:
        path = self.tmp / name
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(path)
        doc.close()
        return path

    def _make_docx(self, name: str, paragraphs: list[str]) -> Path:
        path = self.tmp / name
        doc = DocxDocument()
        for p in paragraphs:
            doc.add_paragraph(p)
        doc.save(path)
        return path

    def test_extract_pdf_text(self):
        path = self._make_pdf("sample.pdf", "Hello PDF")
        self.assertIn("Hello PDF", extract_pdf_text(path))

    def test_extract_docx_text(self):
        path = self._make_docx("sample.docx", ["Первый абзац", "Второй абзац"])
        text = extract_docx_text(path)
        self.assertIn("Первый абзац", text)
        self.assertIn("Второй абзац", text)

    def test_extract_text_dispatches_by_extension(self):
        path = self._make_pdf("a.pdf", "Dispatch")
        self.assertIn("Dispatch", extract_text(path))

    def test_unsupported_extension(self):
        path = self.tmp / "note.txt"
        path.write_text("hello")
        with self.assertRaises(ExtractionError):
            extract_text(path)

    def test_missing_file(self):
        with self.assertRaises(ExtractionError):
            extract_text(self.tmp / "nope.pdf")

    def test_empty_pdf_raises(self):
        """PDF без текстового слоя — это ошибка, а не пустой результат."""
        path = self.tmp / "empty.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(path)
        doc.close()
        with self.assertRaises(ExtractionError):
            extract_pdf_text(path)